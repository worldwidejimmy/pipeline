"""Centralised configuration — single source of truth for all env vars."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Config:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    tmdb_bearer_token: str = os.getenv("TMDB_BEARER_TOKEN", "")
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/w500"

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "cineai_docs")

    # Retrieval
    top_k: int = 6
    chunk_size: int = 800
    chunk_overlap: int = 100


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
