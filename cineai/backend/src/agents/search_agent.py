"""
Search Agent — performs live web search and generates a grounded answer
from current entertainment news and reviews.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from src.llm import get_chat, static_part, system_blocks, text_of

from src.config import get_config
from src.tools.web_search import web_search
from src.guardrails import UNTRUSTED_RULE, wrap_untrusted

_SYSTEM = """You are an entertainment journalist with access to current movie and TV news.

Answer the user's question using ONLY the web search results provided below.
Cite sources by referencing the numbered result (e.g., [1], [2]).
Focus on recent, factual information. Do not invent details.

Search Results:
{results}
""" + UNTRUSTED_RULE
_SYSTEM_STATIC = static_part(_SYSTEM, "Search Results:", UNTRUSTED_RULE)   # cached prefix (llm.system_blocks)


def _get_llm():
    return get_chat(temperature=0.1, max_tokens=800, streaming=True)


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
        system_blocks(_SYSTEM_STATIC, "Search Results:\n" + wrap_untrusted("web_search", formatted[:5000])),
        HumanMessage(content=question),
    ])

    return {
        "search_result": text_of(response.content),
        "_search_results": search_data["results"],
    }
