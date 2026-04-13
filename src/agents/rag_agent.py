"""
RAG Agent — retrieves relevant document chunks from Milvus and uses the LLM
to synthesise a grounded answer.

Role in the pipeline
────────────────────
The supervisor routes here when the question is best answered from the internal
knowledge base.  This agent:
  1. Calls the Milvus retriever tool with the user's query
  2. Passes the retrieved chunks + query to the LLM as context
  3. Returns a structured answer string back into the LangGraph state
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import get_config
from src.tools.milvus_retriever import similarity_search

RAG_SYSTEM_PROMPT = """\
You are a research assistant that answers questions strictly from the provided \
document excerpts. If the excerpts do not contain enough information to answer \
the question, say so clearly — do not hallucinate.

Always cite which excerpt (by number) you drew each fact from.
"""


def run_rag_agent(state: dict) -> dict:
    """
    LangGraph node function.

    Expects state keys:
      - "query"  (str) : the user's original question

    Adds to state:
      - "rag_result"  (str) : the grounded answer from the RAG pipeline
    """
    cfg = get_config()
    query: str = state["query"]

    context = similarity_search(query, k=cfg.rag_top_k)

    llm = cfg.get_llm()
    messages = [
        SystemMessage(content=RAG_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Document excerpts:\n\n{context}\n\n"
                f"---\n\nQuestion: {query}\n\nAnswer:"
            )
        ),
    ]

    response = llm.invoke(messages)
    answer: str = response.content if hasattr(response, "content") else str(response)

    return {**state, "rag_result": answer}
