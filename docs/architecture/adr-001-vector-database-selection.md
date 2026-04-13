# ADR-001: Vector Database Selection for ML Platform

**Status:** Accepted  
**Date:** 2025-07-22  
**Author:** Platform Engineering  
**Deciders:** Priya Nair, Marcus Chen, Sara Okonkwo  

---

## Context

The ML Platform team requires a vector database to support three initial use cases:

1. **Semantic search over engineering documentation** (~500K documents, growing 5% monthly)
2. **Real-time recommendation embeddings** for the content discovery pipeline (~200M vectors)
3. **RAG retrieval** for the internal AI assistant (query latency SLA: < 200ms p95)

We evaluated four candidates: **Milvus**, **Pinecone**, **Qdrant**, and **Weaviate**.

Evaluation criteria (weighted):
- Query latency at scale (30%)
- Operational complexity (20%)
- Cost at projected volume (20%)
- Hybrid search support (15%)
- Ecosystem / LangChain integration (15%)

---

## Options Evaluated

### Option A: Milvus (Standalone / Distributed)

Milvus is an open-source, purpose-built vector database originally developed at Zilliz. It uses a disaggregated storage-compute architecture backed by etcd (metadata) and MinIO or S3 (segment storage).

**Strengths:**
- Handles billion-scale vectors in distributed mode.
- Rich index variety: HNSW, IVF_FLAT, IVF_SQ8, DiskANN (for on-disk large-scale), SCANN.
- Native sparse vector support (v2.4+) enabling hybrid BM25 + dense search in a single query.
- Strong LangChain integration (`langchain-milvus`).
- Self-hosted — no per-query or per-vector pricing.
- Active open-source community; CNCF sandbox project.
- Attu web UI for collection management and query inspection.

**Weaknesses:**
- Operational complexity: requires etcd + MinIO + query/index/data nodes in distributed mode.
- Standalone mode is single-node — not suitable for our 200M vector use case without migration to distributed.
- Steeper learning curve than managed services.

**Cost:** Infrastructure only (compute + storage). Estimated $1,200/month on GCP for our projected volume.

---

### Option B: Pinecone (Managed SaaS)

Pinecone is a fully managed, serverless vector database offered as a cloud service.

**Strengths:**
- Zero operational overhead — fully managed.
- Excellent developer experience and documentation.
- Fast time-to-value.

**Weaknesses:**
- Pricing at our projected scale: ~$4,800/month (based on 200M vectors, standard pod type).
- No self-hosted option — data leaves our infrastructure.
- Limited index type control compared to Milvus.
- No native sparse/hybrid search at time of evaluation (planned).
- Vendor lock-in risk.

**Cost:** ~$4,800/month projected. 4x more expensive than self-hosted Milvus.

---

### Option C: Qdrant

Qdrant is an open-source vector database written in Rust, offering both self-hosted and managed cloud options.

**Strengths:**
- Very fast single-node performance (Rust implementation).
- Good LangChain integration.
- Payload filtering is highly expressive.
- Hybrid search support (sparse + dense) via `sparse_vectors` field.

**Weaknesses:**
- Distributed mode (sharding/replication) is newer and less battle-tested than Milvus at >100M scale.
- Smaller community and ecosystem than Milvus.
- Managed cloud pricing comparable to Pinecone at scale.

**Cost:** Self-hosted similar to Milvus. Managed cloud: ~$3,200/month projected.

---

### Option D: Weaviate

Weaviate is an open-source vector database with a built-in GraphQL API and native multi-tenancy.

**Strengths:**
- Strong multi-tenancy model — good fit for SaaS platforms.
- Built-in hybrid search (BM25 + vector, called "Hybrid Search").
- GraphQL API is unique and expressive.
- Managed cloud option available.

**Weaknesses:**
- GraphQL API adds complexity for teams already using REST/gRPC.
- Memory-heavy — requires significant RAM for large datasets.
- Schema changes require migration — less flexible than Milvus for evolving data models.
- LangChain integration is functional but less mature than Milvus.

**Cost:** Self-hosted: ~$1,400/month. Managed: ~$3,600/month.

---

## Decision

**Selected: Milvus (Standalone for Phase 1, Distributed for Phase 2)**

Milvus best balances query performance at scale, hybrid search capability, cost, and long-term control over data and infrastructure. The operational complexity is acceptable given the Platform Engineering team's existing Kubernetes expertise.

### Phase 1 (Current)
Deploy Milvus standalone via Docker Compose for the RAG and documentation search use cases. This handles up to ~50M vectors comfortably on a single node.

### Phase 2 (Q1 2026)
Migrate to Milvus distributed on Kubernetes (Helm chart: `milvus/milvus`) for the 200M vector recommendation use case. Use S3-compatible storage (GCS with interoperability mode) for segment persistence.

---

## Consequences

- Platform Engineering owns Milvus operational burden.
- All new embedding workloads default to Milvus. Exceptions require ADR review.
- Embedding model standardized on `text-embedding-3-small` (1536 dims) for cost efficiency, with `text-embedding-3-large` (3072 dims) available for high-stakes retrieval.
- The team will implement hybrid search (BM25 + dense) in Phase 2 using Milvus sparse vector support.
- Annual review scheduled for July 2026 to re-evaluate as the managed vector DB market matures.

---

## References

- Milvus benchmark: https://milvus.io/docs/benchmark.md
- Internal performance test results: Confluence > ML Platform > Vector DB Evaluation 2025
- Cost model spreadsheet: Google Drive > Platform Eng > Vendor Evaluations > VectorDB-2025
