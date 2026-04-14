"""Milvus vector store wrapper for RAG retrieval."""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from langchain_milvus import Milvus
from langchain_openai import OpenAIEmbeddings

from src.config import get_config


@lru_cache(maxsize=1)
def _get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=get_config().openai_api_key,
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> Milvus:
    cfg = get_config()
    return Milvus(
        embedding_function=_get_embeddings(),
        collection_name=cfg.milvus_collection,
        connection_args={"uri": cfg.milvus_uri},
        auto_id=True,
    )


async def retrieve(query: str, top_k: int | None = None) -> dict[str, Any]:
    """
    Semantic similarity search over the movie knowledge base.
    Returns chunks with scores and source metadata.
    """
    cfg = get_config()
    k = top_k or cfg.top_k

    t0 = time.time()
    store = get_vectorstore()
    docs_with_scores = store.similarity_search_with_score(query, k=k)
    latency_ms = int((time.time() - t0) * 1000)

    chunks = [
        {
            "text": doc.page_content,
            "score": float(score),
            "source": doc.metadata.get("source", "unknown"),
        }
        for doc, score in docs_with_scores
    ]

    context = "\n\n---\n\n".join(
        f"[Source: {c['source']}]\n{c['text']}" for c in chunks
    )

    return {
        "chunks": chunks,
        "context": context,
        "latency_ms": latency_ms,
        "top_k": k,
    }
