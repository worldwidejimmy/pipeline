"""
Milvus retriever tool — wraps langchain-milvus so agents can call it as a
standard LangChain tool.

Two public surfaces:
  1. `get_vectorstore()` — returns a MilvusVectorStore for use during ingest
  2. `build_retriever_tool()` — returns a LangChain Tool for agents to call
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from langchain_core.tools import Tool
from langchain_milvus import Milvus

from src.config import get_config


@lru_cache(maxsize=1)
def get_vectorstore(collection_name: Optional[str] = None) -> Milvus:
    """
    Return a (cached) Milvus vector store connected to the configured instance.
    The collection is created automatically on first write if it doesn't exist.
    """
    cfg = get_config()
    name = collection_name or cfg.milvus_collection
    embeddings = cfg.get_embeddings()

    return Milvus(
        embedding_function=embeddings,
        collection_name=name,
        connection_args={
            "host": cfg.milvus_host,
            "port": cfg.milvus_port,
        },
        # Stores original text alongside the vector so we can return it
        text_field="text",
        auto_id=True,
    )


def similarity_search(query: str, k: Optional[int] = None) -> str:
    """
    Run a similarity search against Milvus and return a formatted string of
    the top-k matching document chunks.

    This is the raw function that the LangChain Tool wraps.
    """
    cfg = get_config()
    top_k = k or cfg.rag_top_k
    store = get_vectorstore()

    docs = store.similarity_search(query, k=top_k)

    if not docs:
        return "No relevant documents found in the knowledge base."

    results = []
    for i, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source", "unknown")
        results.append(f"[{i}] (source: {source})\n{doc.page_content.strip()}")

    return "\n\n---\n\n".join(results)


def build_retriever_tool() -> Tool:
    """
    Return a LangChain Tool that agents can call by name.
    The tool description tells the LLM when and how to use it.
    """
    return Tool(
        name="rag_search",
        func=similarity_search,
        description=(
            "Search the internal knowledge base (your own documents) for relevant "
            "information. Use this tool when the question is likely answered by "
            "documents you have already ingested — e.g. notes, reports, internal "
            "wikis. Input should be a natural-language search query."
        ),
    )
