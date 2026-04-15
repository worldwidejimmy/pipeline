"""
Synthesiser — merges outputs from all active agents into a single,
coherent answer. Maintains conversation history in state.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config

_SYSTEM = """You are a senior film and TV expert synthesising information from multiple sources.

Combine the agent outputs below into a single, well-structured answer for the user.

Instructions:
- Merge complementary information; do not repeat the same facts
- Resolve any contradictions by noting the discrepancy
- Maintain a consistent, engaging tone
- Use markdown: **bold** for film/show titles, bullet points for lists, headers for sections
- If only one agent ran, clean up and present that output directly
- Do NOT invent information not present in the agent outputs
- Do NOT add facts, ratings, awards, cast members, or any details not explicitly in the data
- PRESERVE all markdown links (TMDB, MusicBrainz) from agent outputs — do not remove or alter URLs

CRITICAL — if the agent outputs indicate no data was found (e.g. "couldn't find",
"no results", "not in the database", "knowledge base does not contain"), do NOT
fill in the gaps from memory. Instead tell the user clearly what wasn't found and
suggest they try a different query (check spelling, try a related title or actor name).
{history_note}

Agent Outputs:
{agent_outputs}
"""

_HISTORY_NOTE = """
The user has asked follow-up questions before. Keep your answer focused on what's new —
don't repeat information already covered in the conversation history unless the user asks.
"""


def _get_llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.2,
        api_key=cfg.groq_api_key,
        max_tokens=1200,
        streaming=True,
    )


async def synthesise_node(state: dict) -> dict:
    """LangGraph node: merge all agent results → final answer, update history."""
    question = state["question"]
    history  = state.get("history") or []

    sections: list[str] = []
    if state.get("tmdb_result"):
        sections.append(f"### TMDB Agent\n{state['tmdb_result']}")
    if state.get("music_result"):
        sections.append(f"### Music Agent (MusicBrainz)\n{state['music_result']}")
    if state.get("rag_result"):
        sections.append(f"### RAG Knowledge Base\n{state['rag_result']}")
    if state.get("search_result"):
        sections.append(f"### Web Search\n{state['search_result']}")

    if not sections:
        answer = "I couldn't find information on that. Please try rephrasing your question."
        new_history = history + [{"q": question, "a": answer}]
        return {"answer": answer, "history": new_history[-10:]}

    history_note = _HISTORY_NOTE if history else ""
    combined = "\n\n".join(sections)
    llm = _get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM.format(
            agent_outputs=combined,
            history_note=history_note,
        )),
        HumanMessage(content=f"Question: {question}"),
    ])

    answer = response.content
    new_history = history + [{"q": question, "a": answer}]
    return {"answer": answer, "history": new_history[-10:]}
