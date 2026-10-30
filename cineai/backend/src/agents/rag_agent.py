"""
RAG Agent — retrieves relevant chunks from the Milvus knowledge base
and generates a grounded answer.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from src.llm import get_chat, static_part, system_blocks, text_of

from src.config import get_config
from src.tools.milvus_retriever import retrieve
from src.guardrails import UNTRUSTED_RULE, wrap_untrusted

_SYSTEM = """You are a film expert with deep knowledge of cinema history, theory, and criticism.

Answer the user's question using ONLY the context documents provided below.
Cite sources by referencing the document name at the end of relevant sentences.
If the documents don't contain sufficient information, clearly state that.
Do NOT hallucinate or invent information not present in the context.

Context:
{context}
""" + UNTRUSTED_RULE
_SYSTEM_STATIC = static_part(_SYSTEM, "Context:", UNTRUSTED_RULE)   # cached prefix (llm.system_blocks)


def _get_llm():
    return get_chat(temperature=0.1, max_tokens=900, streaming=True)


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
        system_blocks(_SYSTEM_STATIC, "Context:\n" + wrap_untrusted("knowledge_base", context)),
        HumanMessage(content=question),
    ])

    return {
        "rag_result": text_of(response.content),
        "_rag_chunks": retrieval["chunks"],
    }
