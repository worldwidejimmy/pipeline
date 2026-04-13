"""
Supervisor Agent — the brain of the pipeline.

It has two responsibilities:
  1. ROUTING  : decide whether a question should go to the RAG agent, the
                web-search agent, or both.
  2. SYNTHESIS: once sub-agents have run, combine their results into a final,
                coherent answer for the user.

LangGraph calls this node at two distinct points in the graph:
  • Before any sub-agent runs  → returns {"route": "rag" | "search" | "both"}
  • After sub-agents have run  → returns {"final_answer": "..."}

The routing decision is made by asking the LLM to output a single word so it
is easy to parse and highly reliable.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import get_config

# ── Routing ───────────────────────────────────────────────────────────────────

ROUTE_SYSTEM_PROMPT = """\
You are a routing assistant. Given a user question, decide which tool to use:

  rag    → the question is best answered from internal documents / notes
  search → the question needs up-to-date information from the web
  both   → the question benefits from both internal documents AND live web info

Respond with exactly one word: rag, search, or both.
No explanations, no punctuation.
"""


def route(state: dict) -> dict:
    """
    LangGraph node — routing step.

    Expects state keys:
      - "query"  (str)

    Adds to state:
      - "route"  (str): "rag" | "search" | "both"
    """
    cfg = get_config()
    query: str = state["query"]

    llm = cfg.get_llm()
    messages = [
        SystemMessage(content=ROUTE_SYSTEM_PROMPT),
        HumanMessage(content=query),
    ]

    response = llm.invoke(messages)
    decision = (
        response.content if hasattr(response, "content") else str(response)
    ).strip().lower()

    # Defensively normalise unexpected output
    if decision not in {"rag", "search", "both"}:
        decision = "both"

    return {**state, "route": decision}


# ── Synthesis ─────────────────────────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = """\
You are a research assistant. You have been given the outputs of one or more \
specialised sub-agents. Combine the most relevant information into a single \
clear, well-structured answer. Remove duplicates and contradictions. \
Cite sources where available. If sub-agent results are empty or unhelpful, \
say so rather than fabricating information.
"""


def synthesise(state: dict) -> dict:
    """
    LangGraph node — synthesis step (runs after sub-agents).

    Expects state keys:
      - "query"          (str)
      - "rag_result"     (str, optional)
      - "search_result"  (str, optional)

    Adds to state:
      - "final_answer"  (str)
    """
    cfg = get_config()
    query: str = state["query"]

    parts: list[str] = []
    if state.get("rag_result"):
        parts.append(f"[Internal knowledge base]\n{state['rag_result']}")
    if state.get("search_result"):
        parts.append(f"[Web search]\n{state['search_result']}")

    if not parts:
        return {**state, "final_answer": "No information was retrieved."}

    combined_context = "\n\n===\n\n".join(parts)

    llm = cfg.get_llm()
    messages = [
        SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Sub-agent outputs:\n\n{combined_context}\n\n"
                f"---\n\nOriginal question: {query}\n\nFinal answer:"
            )
        ),
    ]

    response = llm.invoke(messages)
    answer: str = response.content if hasattr(response, "content") else str(response)

    return {**state, "final_answer": answer}
