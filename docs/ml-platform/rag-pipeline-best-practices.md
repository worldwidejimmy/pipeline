# RAG Pipeline Best Practices — ML Platform Engineering Guide

**Owner:** ML Platform Team  
**Last Updated:** 2025-09-30  
**Applies To:** All RAG implementations at Meridian Data Corp

---

## Overview

This guide covers production best practices for Retrieval-Augmented Generation (RAG) pipelines. It is intended for engineers building or operating RAG systems. Follow these guidelines to ensure retrieval quality, cost efficiency, and operational reliability.

---

## 1. Chunking Strategy

Chunking is one of the highest-leverage decisions in a RAG pipeline. Poor chunking causes retrieval to miss relevant context or return irrelevant noise.

### 1.1 Recommended Chunk Sizes

| Use Case | Chunk Size | Overlap |
|---|---|---|
| General documentation | 800–1000 chars | 100–150 chars |
| Legal / compliance docs | 500–700 chars | 150 chars (high overlap for sentence continuity) |
| Code snippets | 200–400 chars | 50 chars |
| Conversational transcripts | 400–600 chars | 100 chars |

Rule of thumb: Chunk size should be smaller than the context you want the LLM to reason over, but large enough to contain a complete semantic unit (paragraph, section).

### 1.2 Separator Hierarchy

Use semantic separators before character-based ones:

```python
RecursiveCharacterTextSplitter(
    separators=["\n\n", "\n", ". ", " ", ""],
    chunk_size=800,
    chunk_overlap=100,
)
```

Never use a fixed-size splitter (e.g., `CharacterTextSplitter`) for prose documents — it will split sentences mid-thought.

### 1.3 Parent-Child Chunking

For large documents where a retrieved chunk lacks context, use parent-child chunking:
- **Child chunks (retrieval units):** 200–400 chars — small enough for precise retrieval.
- **Parent chunks (context units):** 1500–2000 chars — sent to the LLM for full context.

Store the parent chunk ID in child chunk metadata. At retrieval time, fetch the parent using the child's metadata before assembling the LLM prompt.

This pattern is implemented in LangChain via `ParentDocumentRetriever`.

---

## 2. Embedding Model Selection

### 2.1 Standard Models

| Model | Dimensions | Cost | Best For |
|---|---|---|---|
| `text-embedding-3-small` | 1536 | $0.02/1M tokens | General use — default choice |
| `text-embedding-3-large` | 3072 | $0.13/1M tokens | High-precision retrieval |
| `nomic-embed-text` (local) | 768 | Free (self-hosted) | Dev/test or cost-sensitive prod |
| `mxbai-embed-large` (local) | 1024 | Free (self-hosted) | Best open-source quality |

**Default:** Use `text-embedding-3-small` for all new pipelines unless a use case justifies higher precision.

### 2.2 Embedding Consistency Rule

**Critical:** Use the same embedding model for ingestion and query time. Mismatched embeddings produce meaningless similarity scores and cause retrieval failure. Embedding model selection should be stored in collection metadata.

### 2.3 Dimensionality and Index Size

Doubling embedding dimensions roughly doubles index storage and query time. For billion-scale collections, prefer smaller dimensions and validate with RAGAS recall metrics before committing to a high-dimension model.

---

## 3. Retrieval Quality

### 3.1 Hybrid Search (Recommended for Production)

Pure dense (vector) search fails on queries containing exact terms: product codes, error codes, names, IDs. Pure sparse (BM25) search fails on semantic queries without exact keyword overlap.

**Production default: Hybrid search = BM25 sparse + dense vector.**

Milvus 2.4+ supports sparse vectors natively. Implement hybrid search using:

```python
from pymilvus import AnnSearchRequest, RRFRanker, Collection

# Two search requests: dense + sparse
dense_req = AnnSearchRequest(query_dense_embedding, "dense_vector", params, limit=20)
sparse_req = AnnSearchRequest(query_sparse_embedding, "sparse_vector", params, limit=20)

# Reciprocal Rank Fusion reranking
results = collection.hybrid_search(
    [dense_req, sparse_req],
    rerank=RRFRanker(),
    limit=10,
)
```

### 3.2 Reranking

Initial retrieval returns top-k candidates by approximate similarity. A reranker (cross-encoder) reorders these with much higher accuracy at the cost of additional latency.

**When to add reranking:**
- RAGAS context precision < 0.7 on your eval set
- Top-k results contain clearly irrelevant documents
- Queries are complex or multi-part

**Recommended rerankers:**
- Cohere Rerank (managed API — low ops overhead)
- `cross-encoder/ms-marco-MiniLM-L-6-v2` (self-hosted, lightweight)
- BGE Reranker (strong open-source option)

**Expected latency addition:** 50–150ms for top-20 candidates. Measure p95 impact before enabling in production.

### 3.3 Query Rewriting

When users ask ambiguous or conversational queries, rewrite them before retrieval:

```python
REWRITE_PROMPT = """Given the conversation history and the user's latest question, 
rewrite the question as a standalone, detailed query for document retrieval.
Return only the rewritten query.

History: {history}
Latest question: {question}
Rewritten query:"""
```

This is particularly important in multi-turn chat interfaces where context is implicit.

### 3.4 Top-k Selection

Default top-k: **5** for focused retrieval. Increase to **10–15** when:
- Documents are short (< 300 chars per chunk)
- The query requires synthesizing multiple sources
- Using a reranker (retrieve more, rerank, trim to 5)

Never pass more than 15 chunks to an LLM context without reranking — irrelevant content degrades answer quality.

---

## 4. Evaluation (RAGAS Framework)

All RAG pipelines must pass a baseline RAGAS evaluation before production promotion.

### 4.1 Required Metrics

| Metric | Definition | Minimum Threshold |
|---|---|---|
| **Faithfulness** | Are all claims in the answer grounded in retrieved context? | ≥ 0.75 |
| **Answer Relevancy** | Does the answer address the question asked? | ≥ 0.80 |
| **Context Precision** | Are retrieved chunks relevant to the question? | ≥ 0.70 |
| **Context Recall** | Do retrieved chunks contain all information needed to answer? | ≥ 0.65 |

### 4.2 Evaluation Dataset

Maintain a versioned eval dataset in `ml-platform/eval-datasets/<pipeline-name>/`:
- Minimum 50 Q&A pairs, ideally 200+
- Cover both simple lookups and multi-hop reasoning questions
- Include adversarial examples (questions with no answer in the corpus)
- Update the dataset when the corpus changes significantly

### 4.3 Running RAGAS

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from datasets import Dataset

eval_data = Dataset.from_dict({
    "question": questions,
    "answer": generated_answers,
    "contexts": retrieved_contexts,
    "ground_truth": ground_truth_answers,
})

results = evaluate(eval_data, metrics=[
    faithfulness, answer_relevancy, context_precision, context_recall
])
print(results)
```

Track scores in MLflow: `mlflow.log_metrics(results)`.

---

## 5. Cost Management

### 5.1 Semantic Caching

Implement semantic caching to avoid redundant LLM calls for similar queries:

```python
from langchain.cache import RedisSemanticCache
import langchain

langchain.llm_cache = RedisSemanticCache(
    redis_url="redis://redis.internal:6379",
    embedding=get_embeddings(),
    score_threshold=0.95,  # Cosine similarity threshold for cache hit
)
```

Expected cache hit rate in enterprise documentation Q&A: 15–40% depending on query diversity.

### 5.2 Model Tiering

Not every query requires a frontier model:

| Query Type | Recommended Model | Cost |
|---|---|---|
| Simple factual lookup | `gpt-4o-mini` or `claude-haiku-3-5` | Low |
| Multi-document synthesis | `gpt-4o` or `claude-sonnet-4-5` | Medium |
| Complex reasoning / analysis | `claude-opus-4` or `gpt-4o` | High |

Route queries to the appropriate model tier using a lightweight classifier. A 5-word routing prompt with a mini model costs < $0.001 per query.

### 5.3 Token Budgeting

Set `max_tokens` explicitly on all LLM calls. For RAG answers:
- **Summary answers:** 300–500 tokens
- **Detailed answers:** 800–1200 tokens
- **Never uncapped** — uncontrolled generation can cost 10x expected.

Log `prompt_tokens + completion_tokens` per request to `ml_inference_token_count_total` Prometheus metric.

---

## 6. Prompt Engineering for RAG

### 6.1 System Prompt Template

```
You are a helpful assistant with access to internal documentation.
Answer the user's question using ONLY the information in the provided context.
If the context does not contain enough information to answer, say:
"I don't have enough information in the available documentation to answer this."
Do NOT use prior knowledge or make up information.
Cite your sources by referencing the document name or section.

Context:
{context}
```

### 6.2 Citation Format

Require the model to cite sources:

```
Answer the question based on the context below. 
At the end of your answer, list the source documents you used in the format:
Sources: [doc1.md, section 3], [doc2.pdf, page 4]
```

Citations allow users to verify answers and improve trust in the system.

### 6.3 Injection Defense

Always validate and sanitize user queries before passing to the LLM:

```python
INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) instructions",
    r"you are now",
    r"disregard your (system |)prompt",
    r"<\|.*?\|>",  # Token injection attempts
]

def is_safe_query(query: str) -> bool:
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return False
    return True
```

---

## 7. Observability

### 7.1 Required Metrics

Every RAG pipeline must expose these Prometheus metrics:

```
# Request counts by routing decision
rag_requests_total{routing="rag|search|both"}

# Retrieval latency
rag_retrieval_latency_seconds{quantile="0.5|0.95|0.99"}

# LLM generation latency  
rag_generation_latency_seconds{quantile="0.5|0.95|0.99"}

# Token usage
rag_prompt_tokens_total
rag_completion_tokens_total

# Cache hit rate
rag_cache_hits_total
rag_cache_misses_total

# Retrieved chunk count
rag_retrieved_chunks_count (histogram)
```

### 7.2 LangSmith Tracing

Enable LangSmith tracing in production for detailed trace analysis:

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<key>
LANGCHAIN_PROJECT=meridian-rag-prod
```

Review traces weekly. Focus on: routing decisions, retrieved chunk quality, LLM response faithfulness.

---

## 8. Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Using different embedding models for ingest vs query | Near-zero retrieval quality | Standardize model; store in collection metadata |
| top_k too high without reranker | Irrelevant context degrades answer | Use top_k=5–10; add reranker for precision |
| No chunk overlap | Retrieval misses context split across chunk boundary | Use 10–15% overlap |
| Uncapped LLM output | Cost spikes; slow responses | Always set max_tokens |
| No eval dataset | No way to detect regression | Maintain versioned eval set; run CI on eval |
| Dense-only search | Fails on exact term queries (error codes, names, IDs) | Use hybrid search in production |
| Hallucinations not monitored | Users receive wrong answers without knowing | Monitor faithfulness score; add citation requirements |
