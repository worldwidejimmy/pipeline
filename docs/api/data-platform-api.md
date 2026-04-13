# Internal Data Platform API Reference

**Version:** v2.3  
**Base URL (prod):** `https://api.data.meridian.internal/v2`  
**Base URL (staging):** `https://api.data.staging.meridian.internal/v2`  
**Auth:** Bearer token (JWT) — obtain from the internal IAM service at `https://iam.meridian.internal`  
**Owner:** Data Platform Team  
**Last Updated:** 2025-10-01

---

## Authentication

All requests must include an `Authorization` header:

```
Authorization: Bearer <jwt-token>
```

Tokens expire after 4 hours. Refresh via `POST /auth/token/refresh`.

Service accounts (for machine-to-machine) use mTLS in addition to JWT. Certificates issued by the internal PKI — contact the Security team for provisioning.

---

## Rate Limits

| Tier | Requests/minute | Burst |
|---|---|---|
| Standard (default) | 600 | 100 |
| Elevated (approved teams) | 3,000 | 500 |
| Batch (async only) | Unlimited | N/A |

Rate limit headers returned on every response:
```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 543
X-RateLimit-Reset: 1730000060
```

When rate-limited, the API returns `429 Too Many Requests` with a `Retry-After` header.

---

## Endpoints

### Feature Store

#### GET /features/{entity_type}/{entity_id}

Retrieve the latest feature vector for a given entity.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `entity_type` | string | Yes | `user`, `content`, `artist`, `playlist` |
| `entity_id` | string | Yes | Entity UUID |
| `feature_group` | string | No | Filter to specific feature group (default: all) |
| `as_of` | ISO8601 datetime | No | Point-in-time lookup (default: latest) |

**Example Request:**
```bash
curl -H "Authorization: Bearer <token>" \
  "https://api.data.meridian.internal/v2/features/user/usr_4a8f2b91?feature_group=engagement"
```

**Example Response:**
```json
{
  "entity_id": "usr_4a8f2b91",
  "entity_type": "user",
  "feature_group": "engagement",
  "computed_at": "2025-10-01T14:32:00Z",
  "features": {
    "session_count_7d": 23,
    "avg_session_duration_s": 1842,
    "skip_rate_30d": 0.12,
    "completion_rate_30d": 0.74,
    "genre_affinity_vector": [0.82, 0.13, 0.05, ...],
    "last_active_hours_ago": 2.4
  }
}
```

**Error Codes:**

| Code | Meaning |
|---|---|
| 404 | Entity not found |
| 400 | Invalid entity_type |
| 422 | Invalid as_of timestamp |

---

#### POST /features/batch

Retrieve feature vectors for multiple entities in a single request.

**Request Body:**
```json
{
  "requests": [
    {"entity_type": "user", "entity_id": "usr_4a8f2b91"},
    {"entity_type": "content", "entity_id": "cnt_88a3c512"}
  ],
  "feature_groups": ["engagement", "content_affinity"]
}
```

**Limits:** Max 500 entities per batch request.

---

### Vector Search

#### POST /vectors/search

Perform semantic similarity search over a specified collection.

**Request Body:**
```json
{
  "collection": "engineering_docs",
  "query_text": "How do I configure Kafka consumer group rebalancing?",
  "top_k": 10,
  "filters": {
    "source_type": "runbook",
    "updated_after": "2025-01-01"
  },
  "search_type": "hybrid"
}
```

**Parameters:**

| Field | Type | Required | Description |
|---|---|---|---|
| `collection` | string | Yes | Target vector collection name |
| `query_text` | string | Yes* | Natural language query (auto-embedded) |
| `query_vector` | float[] | Yes* | Pre-computed embedding vector (alternative to query_text) |
| `top_k` | integer | No | Number of results (default: 5, max: 100) |
| `filters` | object | No | Metadata filters (AND logic) |
| `search_type` | string | No | `dense` (default), `sparse`, or `hybrid` |
| `rerank` | boolean | No | Apply cross-encoder reranking (default: false) |

*Provide either `query_text` or `query_vector`, not both.

**Example Response:**
```json
{
  "results": [
    {
      "id": "doc_chunk_00124",
      "score": 0.921,
      "text": "To configure consumer group rebalancing in Kafka, set the following...",
      "metadata": {
        "source": "runbooks/kafka-consumer-guide.md",
        "section": "Rebalancing Configuration",
        "updated_at": "2025-09-15"
      }
    }
  ],
  "total_found": 847,
  "search_type_used": "hybrid",
  "latency_ms": 34
}
```

---

#### POST /vectors/ingest

Ingest documents into a vector collection.

**Request Body:**
```json
{
  "collection": "engineering_docs",
  "documents": [
    {
      "text": "Document content here...",
      "metadata": {
        "source": "runbooks/new-guide.md",
        "author": "Dev Patel",
        "created_at": "2025-10-01"
      }
    }
  ],
  "chunking": {
    "chunk_size": 800,
    "chunk_overlap": 100
  }
}
```

**Limits:** Max 1,000 documents per request. For bulk ingestion, use the async batch endpoint.

---

### ML Inference

#### POST /inference/generate

Invoke an LLM for text generation. Routes to the appropriate model tier based on the `model` parameter.

**Request Body:**
```json
{
  "model": "claude-sonnet-4-5",
  "messages": [
    {"role": "system", "content": "You are a helpful engineering assistant."},
    {"role": "user", "content": "Explain Kafka consumer group rebalancing."}
  ],
  "max_tokens": 1024,
  "temperature": 0.1,
  "stream": false
}
```

**Supported Models:**

| Model ID | Tier | Notes |
|---|---|---|
| `claude-haiku-3-5` | Managed | Fast, cost-efficient |
| `claude-sonnet-4-5` | Managed | Default for most uses |
| `claude-opus-4` | Managed | Complex reasoning |
| `gpt-4o-mini` | Managed | Alternative to Haiku |
| `gpt-4o` | Managed | Alternative to Sonnet |
| `llama3-70b` | Self-hosted | On-prem, data-sensitive workloads |
| `mistral-7b` | Self-hosted | High throughput, low cost |

**Streaming:** Set `"stream": true` to receive a Server-Sent Events (SSE) stream. Compatible with LangChain's `ChatOpenAI(streaming=True)` when pointed at this endpoint.

---

#### POST /inference/embed

Generate embedding vectors for text.

**Request Body:**
```json
{
  "model": "text-embedding-3-small",
  "input": ["First text to embed", "Second text to embed"],
  "dimensions": 1536
}
```

**Supported Embedding Models:**

| Model | Dimensions | Notes |
|---|---|---|
| `text-embedding-3-small` | 1536 | Default |
| `text-embedding-3-large` | 3072 | High precision |
| `nomic-embed-text` | 768 | Self-hosted, free |

---

### Pipeline Orchestration

#### POST /pipelines/{pipeline_id}/runs

Trigger a pipeline run.

**Path Parameters:** `pipeline_id` — the pipeline name as defined in Airflow (e.g., `ml_feature_refresh`, `rag_ingest_weekly`)

**Request Body:**
```json
{
  "conf": {
    "target_date": "2025-10-01",
    "force_recompute": false
  },
  "note": "Manual trigger for Q3 refresh"
}
```

**Response:**
```json
{
  "run_id": "run_20251001_143200_abc123",
  "pipeline_id": "ml_feature_refresh",
  "status": "queued",
  "triggered_at": "2025-10-01T14:32:00Z",
  "triggered_by": "usr_4a8f2b91"
}
```

#### GET /pipelines/{pipeline_id}/runs/{run_id}

Poll the status of a pipeline run.

**Status values:** `queued`, `running`, `success`, `failed`, `cancelled`

---

## Error Format

All errors return consistent JSON:

```json
{
  "error": {
    "code": "ENTITY_NOT_FOUND",
    "message": "No features found for entity usr_xxxxx",
    "request_id": "req_7f3a2b1c",
    "docs_url": "https://docs.meridian.internal/data-platform/errors#ENTITY_NOT_FOUND"
  }
}
```

---

## SDK

A Python SDK is available internally:

```bash
pip install meridian-data-sdk --index-url https://pypi.internal.meridian.com
```

```python
from meridian.data import DataPlatformClient

client = DataPlatformClient(
    base_url="https://api.data.meridian.internal/v2",
    token=os.environ["MERIDIAN_API_TOKEN"]
)

# Feature lookup
features = client.features.get("user", "usr_4a8f2b91", feature_group="engagement")

# Vector search
results = client.vectors.search(
    collection="engineering_docs",
    query="Kafka consumer rebalancing",
    top_k=10,
    search_type="hybrid"
)

# LLM generation
response = client.inference.generate(
    model="claude-sonnet-4-5",
    messages=[{"role": "user", "content": "Explain RAG pipelines."}],
    max_tokens=800,
)
```

---

## Changelog

| Version | Date | Changes |
|---|---|---|
| v2.3 | 2025-10-01 | Added hybrid search type; added rerank parameter to /vectors/search |
| v2.2 | 2025-08-15 | Added streaming support to /inference/generate |
| v2.1 | 2025-06-01 | Added batch feature lookup endpoint |
| v2.0 | 2025-03-15 | Breaking: migrated auth from API keys to JWT; added rate limiting |
| v1.x | 2024 | Legacy — EOL 2026-01-01 |
