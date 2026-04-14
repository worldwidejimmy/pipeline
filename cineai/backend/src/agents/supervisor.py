"""
Supervisor agent — classifies the user's question and decides which
downstream agents to invoke.

Routing outputs (single token):
  tmdb        → real-time movie/TV data only
  rag         → knowledge-base search only
  search      → live web search only
  tmdb+rag    → real-time data + knowledge base
  tmdb+search → real-time data + web search
  rag+search  → knowledge base + web search
  all         → all three agents
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config

_SYSTEM = """You are a routing agent for a movie and TV intelligence system.

Classify the user's question into exactly one of these routing decisions:
  tmdb        – needs real-time movie/TV database data (ratings, cast, plot, trending)
  rag         – needs deep analysis from stored knowledge base (film theory, history, reviews)
  search      – needs current news or very recent events (this week's box office, just released)
  tmdb+rag    – needs both real-time data AND deep analysis/context
  tmdb+search – needs real-time data AND current web news
  rag+search  – needs stored knowledge AND current web news
  all         – needs all three sources

Rules:
- Questions about specific movie details, ratings, cast → tmdb
- Questions about film theory, director style, historical context → rag
- Questions about box office this week, upcoming releases, recent news → search
- Questions combining "what is X about" + "how does it compare historically" → tmdb+rag
- Questions about trending + current news → tmdb+search
- General "what should I watch" with context → all

Reply with ONLY the routing decision word(s). No explanation. No punctuation.
Examples: "tmdb" or "tmdb+rag" or "all"
"""


def _get_llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0,
        api_key=cfg.groq_api_key,
        max_tokens=16,
    )


async def supervisor_route_node(state: dict) -> dict:
    """LangGraph node: classify question → routing decision."""
    question = state["question"]
    llm = _get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=question),
    ])

    raw = response.content.strip().lower()

    # Normalise to known values
    valid = {"tmdb", "rag", "search", "tmdb+rag", "tmdb+search", "rag+search", "all"}
    routing = raw if raw in valid else "tmdb+rag"

    return {"routing": routing}
