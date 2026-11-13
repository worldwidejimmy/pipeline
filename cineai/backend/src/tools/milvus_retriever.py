"""
Milvus hybrid search (BM25 sparse + dense embeddings) for RAG retrieval.

Uses Milvus 2.5 native full-text search, fused with the dense results by a weighted
score merge (or RRF; `HYBRID_RANKER`).
Falls back to dense-only search on collections with the old schema.
"""
from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from typing import Any

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker, WeightedRanker
from langchain_openai import OpenAIEmbeddings

from src.config import get_config


def _ranker(cfg):
    """The fusion step. "weighted": each list's scores are normalised (Milvus arctan) and added
    with weights (dense, BM25), so a passage only the dense side finds can still win on
    similarity. "rrf": Σ 1/(k + rank), which rewards appearing on both lists regardless of score."""
    if cfg.hybrid_ranker == "rrf":
        return RRFRanker(k=cfg.hybrid_rrf_k)
    dense_w, bm25_w = (float(x) for x in cfg.hybrid_weights.split(","))
    return WeightedRanker(dense_w, bm25_w)     # order matches reqs=[dense, sparse]


@lru_cache(maxsize=1)
def _get_embeddings() -> OpenAIEmbeddings:
    cfg = get_config()
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=cfg.openai_api_key,
    )


@lru_cache(maxsize=1)
def _get_client() -> MilvusClient:
    cfg = get_config()
    return MilvusClient(uri=cfg.milvus_uri)


def _has_hybrid_schema(collection_name: str) -> bool:
    """Return True if the collection has a sparse_vector field (hybrid schema)."""
    try:
        client = _get_client()
        info = client.describe_collection(collection_name)
        field_names = {f["name"] for f in info.get("fields", [])}
        return "sparse_vector" in field_names
    except Exception:
        return False


async def retrieve(query: str, top_k: int | None = None) -> dict[str, Any]:
    """
    Hybrid similarity search over the movie knowledge base.

    When the collection has the hybrid schema (sparse_vector + dense_vector),
    combines BM25 sparse retrieval with dense embedding search (fusion: `_ranker`). This fixes exact-title misses that dense-only search suffers from.

    Falls back to pure dense search on old-schema collections.
    """
    cfg = get_config()
    k = top_k or cfg.top_k

    t0 = time.time()
    client = _get_client()

    # Dense embedding of the query
    dense_vec: list[float] = await _get_embeddings().aembed_query(query)

    if _has_hybrid_schema(cfg.milvus_collection):
        search_type = "hybrid"
        dense_req = AnnSearchRequest(
            data=[dense_vec],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": cfg.dense_nprobe}},
            limit=k,
        )
        # BM25: pass raw text — Milvus tokenises it against the stored corpus
        sparse_req = AnnSearchRequest(
            data=[query],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=k,
        )
        # Milvus calls are blocking I/O: off the event loop, so concurrent requests keep streaming.
        raw = await asyncio.to_thread(
            client.hybrid_search,
            collection_name=cfg.milvus_collection,
            reqs=[dense_req, sparse_req],
            ranker=_ranker(cfg),
            limit=k,
            output_fields=["text", "source", "dense_vector"],
        )
        hits = raw[0]
    else:
        # Dense-only fallback for collections ingested before the hybrid migration
        search_type = "dense"
        raw = await asyncio.to_thread(
            client.search,
            collection_name=cfg.milvus_collection,
            data=[dense_vec],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": cfg.dense_nprobe}},
            limit=k,
            output_fields=["text", "source", "dense_vector"],
        )
        hits = raw[0]

    # Relevance signal for the knowledge-base gate (src/agents/kb_lookup.py). The fused
    # score is relative to the other results (RRF's is rank-only), so it can't say whether anything is relevant; the
    # dense similarity can. Vectors are unit length, so the inner product is the cosine.
    # `dense_max` is the best similarity among the dense top-k, the exact signal the
    # threshold was calibrated on (evals/experiments/kb_gate_experiment.py).
    dense_top = await asyncio.to_thread(
        client.search, cfg.milvus_collection, data=[dense_vec], anns_field="dense_vector",
        search_params={"metric_type": "IP", "params": {"nprobe": cfg.dense_nprobe}},
        limit=k, output_fields=["source"])
    dense_max = max((h["distance"] for h in dense_top[0]), default=0.0)

    latency_ms = int((time.time() - t0) * 1000)

    def _sim(hit: dict) -> float:
        vec = hit["entity"].get("dense_vector")
        return round(sum(a * b for a, b in zip(vec, dense_vec)), 4) if vec else 0.0

    chunks = [
        {
            "text": hit["entity"]["text"],
            "score": float(hit["distance"]),          # fused rank score (ordering only)
            "similarity": _sim(hit),                  # cosine to the query (meaningful)
            "source": hit["entity"].get("source", "unknown"),
            "search_type": search_type,
        }
        for hit in hits
    ]

    context = "\n\n---\n\n".join(
        f"[Source: {c['source']}]\n{c['text']}" for c in chunks
    )

    return {
        "chunks": chunks,
        "context": context,
        "latency_ms": latency_ms,
        "top_k": k,
        "search_type": search_type,
        "dense_max": round(float(dense_max), 4),
    }
