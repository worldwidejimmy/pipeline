"""
Search Agent — performs live web search and generates a grounded answer
from current entertainment news and reviews.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools.web_search import web_search

_SYSTEM = """You are an entertainment journalist with access to current movie and TV news.

Answer the user's question using ONLY the web search results provided below.
Cite sources by referencing the numbered result (e.g., [1], [2]).
Focus on recent, factual information. Do not invent details.

Search Results:
{results}
"""


def _get_llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.1,
        api_key=cfg.groq_api_key,
        max_tokens=800,
        streaming=True,
    )


async def search_agent_node(state: dict) -> dict:
    """LangGraph node: web search → generate grounded answer."""
    question = state["question"]

    search_data = await web_search(
        query=f"movie TV {question}",
        max_results=5,
    )

    formatted = search_data["formatted"]

    if not formatted.strip() or "unavailable" in formatted:
        return {
            "search_result": "Live web search is not available. Configure TAVILY_API_KEY to enable it.",
            "_search_results": [],
        }

    llm = _get_llm()
    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM.format(results=formatted[:5000])),
        HumanMessage(content=question),
    ])

    return {
        "search_result": response.content,
        "_search_results": search_data["results"],
    }
