"""
LangGraph pipeline for CineAI.

Flow:
  START → understand (rewrite a follow-up as a standalone question; no LLM on a first question)
        → kb_lookup (knowledge base first, calibrated relevance gate)
        → supervisor_route → conditional dispatch →
    [tmdb_agent] [rag_agent] [search_agent] [music_agent] (subset, fan-out) →
  synthesise → END

State keys:
  question      – the question the agents work on (standalone rewrite of a follow-up)
  user_question – the user's own words (the answer and the history use these)
  kb            – kb_lookup result: relevant?, passages, scores
  history       – list of previous {q, a} turns (last 10 kept)
  routing       – supervisor decision: tmdb|rag|search|tmdb+rag|etc.
  tmdb_result   – formatted output from TMDB agent
  rag_result    – formatted output from RAG agent
  search_result – formatted output from web search agent
  answer        – final synthesised answer
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from src.agents.supervisor import supervisor_route_node
from src.agents.understand import understand_node
from src.agents.kb_lookup import kb_lookup_node
from src.kb_gate import apply_kb
from src.agents.tmdb_agent import tmdb_agent_node
from src.agents.rag_agent import rag_agent_node
from src.agents.search_agent import search_agent_node
from src.agents.music_agent import music_agent_node
from src.agents.synthesiser import synthesise_node


# ── State ─────────────────────────────────────────────────────────────────────

class CineState(TypedDict, total=False):
    question: str
    user_question: str
    kb: dict
    history: list[dict]        # [{"q": "...", "a": "..."}] — last 10 turns
    routing: str
    routing_by: str            # "keyword" (deterministic rule) or "model" (LLM classifier)
    tmdb_result: str
    rag_result: str
    search_result: str
    music_result: str
    answer: str


# ── Routing logic ─────────────────────────────────────────────────────────────

def _dispatch(state: CineState) -> list[str]:
    """Return agent nodes to activate based on routing decision."""
    routing = (state.get("routing") or "tmdb").lower()
    mapping: dict[str, list[str]] = {
        "tmdb":          ["tmdb_agent"],
        "rag":           ["rag_agent"],
        "search":        ["search_agent"],
        "music":         ["music_agent"],
        "tmdb+rag":      ["tmdb_agent", "rag_agent"],
        "tmdb+search":   ["tmdb_agent", "search_agent"],
        "tmdb+music":    ["tmdb_agent", "music_agent"],
        "music+search":  ["music_agent", "search_agent"],
        "rag+search":    ["rag_agent", "search_agent"],
        "all":           ["tmdb_agent", "rag_agent", "search_agent", "music_agent"],
    }
    agents = list(mapping.get(routing, ["tmdb_agent"]))
    return apply_kb(agents, (state.get("kb") or {}).get("relevant"))



# ── Singletons ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_memory() -> MemorySaver:
    return MemorySaver()


@lru_cache(maxsize=1)
def build_pipeline():
    """Compile and return the LangGraph pipeline with memory checkpointer."""
    g = StateGraph(CineState)

    g.add_node("understand",       understand_node)
    g.add_node("kb_lookup",        kb_lookup_node)
    g.add_node("supervisor_route", supervisor_route_node)
    g.add_node("tmdb_agent",       tmdb_agent_node)
    g.add_node("rag_agent",        rag_agent_node)
    g.add_node("search_agent",     search_agent_node)
    g.add_node("music_agent",      music_agent_node)
    g.add_node("synthesise",       synthesise_node)

    g.add_edge(START, "understand")
    g.add_edge("understand", "kb_lookup")
    g.add_edge("kb_lookup", "supervisor_route")

    g.add_conditional_edges(
        "supervisor_route",
        _dispatch,
        {
            "tmdb_agent":   "tmdb_agent",
            "rag_agent":    "rag_agent",
            "search_agent": "search_agent",
            "music_agent":  "music_agent",
        },
    )

    for node in ("tmdb_agent", "rag_agent", "search_agent", "music_agent"):
        g.add_edge(node, "synthesise")

    g.add_edge("synthesise", END)

    return g.compile(checkpointer=_get_memory())
