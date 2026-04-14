"""
LangGraph pipeline for CineAI.

Flow:
  START → supervisor_route → conditional dispatch →
    [tmdb_agent] [rag_agent] [search_agent] (subset or all, fan-out) →
  synthesise → END

State keys:
  question   – raw user question
  routing    – supervisor decision: "tmdb" | "rag" | "search" | "tmdb+rag" |
               "tmdb+search" | "rag+search" | "all"
  tmdb_result    – formatted output from TMDB agent
  rag_result     – formatted output from RAG agent
  search_result  – formatted output from web search agent
  answer         – final synthesised answer
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.config import get_config
from src.agents.supervisor import supervisor_route_node
from src.agents.tmdb_agent import tmdb_agent_node
from src.agents.rag_agent import rag_agent_node
from src.agents.search_agent import search_agent_node
from src.agents.synthesiser import synthesise_node


# ── State ─────────────────────────────────────────────────────────────────────

class CineState(TypedDict, total=False):
    question: str
    routing: str
    tmdb_result: str
    rag_result: str
    search_result: str
    answer: str


# ── Routing logic ─────────────────────────────────────────────────────────────

def _dispatch(state: CineState) -> list[str]:
    """Return the list of agent nodes to activate based on routing decision."""
    routing = (state.get("routing") or "tmdb").lower()

    mapping: dict[str, list[str]] = {
        "tmdb":         ["tmdb_agent"],
        "rag":          ["rag_agent"],
        "search":       ["search_agent"],
        "tmdb+rag":     ["tmdb_agent", "rag_agent"],
        "tmdb+search":  ["tmdb_agent", "search_agent"],
        "rag+search":   ["rag_agent", "search_agent"],
        "all":          ["tmdb_agent", "rag_agent", "search_agent"],
    }
    return mapping.get(routing, ["tmdb_agent"])


# ── Build graph ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def build_pipeline():
    """Compile and return the LangGraph pipeline (cached singleton)."""
    g = StateGraph(CineState)

    g.add_node("supervisor_route", supervisor_route_node)
    g.add_node("tmdb_agent",       tmdb_agent_node)
    g.add_node("rag_agent",        rag_agent_node)
    g.add_node("search_agent",     search_agent_node)
    g.add_node("synthesise",       synthesise_node)

    g.add_edge(START, "supervisor_route")

    # Fan-out: supervisor → one or more agents (parallel when multiple)
    g.add_conditional_edges(
        "supervisor_route",
        _dispatch,
        {
            "tmdb_agent":   "tmdb_agent",
            "rag_agent":    "rag_agent",
            "search_agent": "search_agent",
        },
    )

    # All agents converge to synthesiser
    for node in ("tmdb_agent", "rag_agent", "search_agent"):
        g.add_edge(node, "synthesise")

    g.add_edge("synthesise", END)

    return g.compile()
