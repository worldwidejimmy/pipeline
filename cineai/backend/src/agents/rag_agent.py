"""
RAG Agent — retrieves relevant chunks from the Milvus knowledge base
and generates a grounded answer.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools.milvus_retriever import retrieve

_SYSTEM = """You are a film expert with deep knowledge of cinema history, theory, and criticism.

Answer the user's question using ONLY the context documents provided below.
Cite sources by referencing the document name at the end of relevant sentences.
If the documents don't contain sufficient information, clearly state that.
Do NOT hallucinate or invent information not present in the context.

Context:
{context}
"""


def _get_llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.1,
        api_key=cfg.groq_api_key,
        max_tokens=900,
        streaming=True,
    )


async def rag_agent_node(state: dict) -> dict:
    """LangGraph node: embed query → retrieve chunks → generate grounded answer."""
    question = state["question"]

    retrieval = await retrieve(question)
    context = retrieval["context"]

    if not context.strip():
        return {
            "rag_result": "The knowledge base does not contain relevant information for this query.",
            "_rag_chunks": [],
        }

    llm = _get_llm()
    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM.format(context=context)),
        HumanMessage(content=question),
    ])

    return {
        "rag_result": response.content,
        "_rag_chunks": retrieval["chunks"],
    }
