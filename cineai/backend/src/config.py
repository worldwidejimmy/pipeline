"""Centralised configuration — single source of truth for all env vars."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── LLM (Anthropic Claude) ───────────────────────────────────────────────
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Server-wide model tier: haiku | sonnet | opus (see src/llm.py)
    model_tier: str = os.getenv("DEFAULT_MODEL_TIER", "haiku")
    # Optional exact model id; overrides "newest in tier" auto-resolution.
    model_pin: str = os.getenv("MODEL_PIN", "")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    tmdb_bearer_token: str = os.getenv("TMDB_BEARER_TOKEN", "")
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/w500"

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "cineai_docs")

    # Retrieval
    top_k: int = int(os.getenv("TOP_K", "6"))
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Hybrid search
    embedding_dim: int = 1536          # text-embedding-3-small output dim
    # How the BM25 and dense lists are merged: "weighted" (default) adds the two normalised
    # scores, dense 0.7 / BM25 0.3; "rrf" fuses by rank only. Weighted won on T2 (MRR 0.77 →
    # 0.88, holdout 0.73 → 0.77, 2026-11-11): RRF let Ebert reviews that rank on both lists bury a
    # guide that only the dense side finds (music-in-film).
    hybrid_ranker: str = os.getenv("HYBRID_RANKER", "weighted")
    hybrid_weights: str = os.getenv("HYBRID_WEIGHTS", "0.7,0.3")   # dense, BM25
    hybrid_rrf_k: int = int(os.getenv("HYBRID_RRF_K", "60"))  # RRF rank fusion k (ranker "rrf")


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
