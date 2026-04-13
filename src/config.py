"""
Central configuration loaded from environment variables / .env file.
All other modules import from here — never call os.getenv directly.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def get_config() -> "Config":
    return Config()


class Config:
    # LLM
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")

    # Embeddings
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "openai")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    # Milvus
    milvus_host: str = os.getenv("MILVUS_HOST", "localhost")
    milvus_port: int = int(os.getenv("MILVUS_PORT", "19530"))
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "research_docs")

    # Search
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    search_max_results: int = int(os.getenv("SEARCH_MAX_RESULTS", "5"))

    # Pipeline behaviour
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    supervisor_temperature: float = float(os.getenv("SUPERVISOR_TEMPERATURE", "0.2"))

    def get_llm(self):
        """Return an LLM instance based on LLM_PROVIDER."""
        if self.llm_provider == "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=self.ollama_model,
                base_url=self.ollama_base_url,
                temperature=self.supervisor_temperature,
            )
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=self.openai_model,
            api_key=self.openai_api_key,
            temperature=self.supervisor_temperature,
        )

    def get_embeddings(self):
        """Return an Embeddings instance based on EMBEDDING_PROVIDER."""
        if self.embedding_provider == "ollama":
            from langchain_ollama import OllamaEmbeddings
            return OllamaEmbeddings(
                model=self.embedding_model,
                base_url=self.ollama_base_url,
            )
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(
            model=self.embedding_model,
            api_key=self.openai_api_key,
        )
