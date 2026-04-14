"""
Supervisor agent — classifies the user's question and decides which
downstream agents to invoke. Uses conversation history for follow-up
questions so routing stays coherent across turns.
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
- Questions about a specific movie or TV show title → tmdb  (even if just "Tell me about [title]")
- Questions about a specific actor, director, or person in film → tmdb
- Questions about movie plot, cast, ratings, runtime, release year → tmdb
- Questions about trending, popular, or recently released films → tmdb
- Questions about film theory, director style, cinematography, historical context → rag
- Questions about box office this week, upcoming releases, recent news → search
- Questions combining "what is X about" + "how does it compare historically" → tmdb+rag
- Questions about trending + current news → tmdb+search
- General recommendations with context → all
- Follow-up questions that reference previous answers → use same or broader routing
- When in doubt between tmdb and rag, prefer tmdb

{history_block}
Reply with ONLY the routing decision word(s). No explanation. No punctuation.
Examples: "tmdb" or "tmdb+rag" or "all"
"""

_HISTORY_BLOCK = """Conversation so far (most recent first):
{turns}

The user's new question may be a follow-up to the above.
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
    history  = state.get("history") or []

    # Build history context for the prompt
    if history:
        recent = history[-3:]  # last 3 turns
        turns_text = "\n".join(
            f"Q: {h['q']}\nA: {h['a'][:150]}…" for h in reversed(recent)
        )
        history_block = _HISTORY_BLOCK.format(turns=turns_text)
    else:
        history_block = ""

    system = _SYSTEM.format(history_block=history_block)
    llm = _get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=question),
    ])

    raw = response.content.strip().lower()
    valid = {"tmdb", "rag", "search", "tmdb+rag", "tmdb+search", "rag+search", "all"}
    routing = raw if raw in valid else "tmdb+rag"

    return {"routing": routing}
