"""
Milvus hybrid search (BM25 sparse + dense embeddings) for RAG retrieval.

Uses Milvus 2.5 native full-text search with RRF rank fusion.
Falls back to dense-only search on collections with the old schema.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker
from langchain_openai import OpenAIEmbeddings

from src.config import get_config


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
    combines BM25 sparse retrieval with dense embedding search via RRF rank
    fusion. This fixes exact-title misses that dense-only search suffers from.

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
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=k,
        )
        # BM25: pass raw text — Milvus tokenises it against the stored corpus
        sparse_req = AnnSearchRequest(
            data=[query],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=k,
        )
        raw = client.hybrid_search(
            collection_name=cfg.milvus_collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=cfg.hybrid_rrf_k),
            limit=k,
            output_fields=["text", "source"],
        )
        hits = raw[0]
    else:
        # Dense-only fallback for collections ingested before the hybrid migration
        search_type = "dense"
        raw = client.search(
            collection_name=cfg.milvus_collection,
            data=[dense_vec],
            anns_field="dense_vector",
            param={"metric_type": "IP"},
            limit=k,
            output_fields=["text", "source"],
        )
        hits = raw[0]

    latency_ms = int((time.time() - t0) * 1000)

    chunks = [
        {
            "text": hit["entity"]["text"],
            "score": float(hit["distance"]),
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
    }
