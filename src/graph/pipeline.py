"""
LangGraph StateGraph — the wiring that connects all agents into a pipeline.

Graph topology
──────────────
                     ┌─────────────┐
         ┌──rag──────► rag_agent    ├──────────────┐
         │           └─────────────┘               │
  START──► supervisor                               ▼
         │           ┌─────────────┐         synthesise ──► END
         ├──search───► search_agent├──────────────►│
         │           └─────────────┘               │
         └──both─────► rag_agent                    │
                     ► search_agent ────────────────┘

State schema (TypedDict)
────────────────────────
  query          str               User question (set at entry)
  route          str               "rag" | "search" | "both"
  rag_result     str | None        Output from the RAG agent
  search_result  str | None        Output from the search agent
  final_answer   str | None        Synthesised final answer
"""
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.agents.rag_agent import run_rag_agent
from src.agents.search_agent import run_search_agent
from src.agents.supervisor import route, synthesise


# ── State schema ──────────────────────────────────────────────────────────────

class PipelineState(TypedDict, total=False):
    query: str
    route: str
    rag_result: Optional[str]
    search_result: Optional[str]
    final_answer: Optional[str]


# ── Routing edge function ─────────────────────────────────────────────────────

def dispatch(state: PipelineState) -> list[str]:
    """
    Conditional edge: decides which node(s) to run next based on the route
    value set by the supervisor.

    LangGraph supports returning a list of node names to fan out in parallel.
    """
    decision = state.get("route", "both")
    if decision == "rag":
        return ["rag_agent"]
    if decision == "search":
        return ["search_agent"]
    # "both" — run in parallel
    return ["rag_agent", "search_agent"]


# ── Graph construction ────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    """Build and compile the full research pipeline graph."""
    graph = StateGraph(PipelineState)

    # Nodes
    graph.add_node("supervisor_route", route)
    graph.add_node("rag_agent", run_rag_agent)
    graph.add_node("search_agent", run_search_agent)
    graph.add_node("synthesise", synthesise)

    # Edges
    graph.add_edge(START, "supervisor_route")

    # Conditional fan-out from the routing supervisor
    graph.add_conditional_edges(
        "supervisor_route",
        dispatch,
        {
            "rag_agent": "rag_agent",
            "search_agent": "search_agent",
        },
    )

    # Both sub-agents converge on the synthesis node
    graph.add_edge("rag_agent", "synthesise")
    graph.add_edge("search_agent", "synthesise")

    graph.add_edge("synthesise", END)

    return graph.compile()


# Module-level compiled graph — import this in scripts and notebooks
pipeline = build_pipeline()


def run(query: str) -> str:
    """
    Convenience wrapper: run the full pipeline for a single query string.

    Returns the final synthesised answer.
    """
    final_state = pipeline.invoke({"query": query})
    return final_state.get("final_answer", "No answer produced.")
