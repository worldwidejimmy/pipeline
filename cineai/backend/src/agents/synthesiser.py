"""
Synthesiser — merges outputs from all active agents into a single,
coherent answer. This node's LLM tokens stream as the final visible answer.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config

_SYSTEM = """You are a senior film expert synthesising information from multiple sources.

Combine the agent outputs below into a single, well-structured answer for the user.

Instructions:
- Merge complementary information; do not repeat the same facts
- Resolve any contradictions by noting the discrepancy
- Maintain a consistent, engaging tone
- Use markdown formatting: headers, bullet points, bold for movie titles
- If only one agent ran, just clean up and present that output
- Do not invent information not present in the agent outputs

Agent Outputs:
{agent_outputs}
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
    """LangGraph node: merge all agent results into final answer."""
    question = state["question"]

    sections: list[str] = []
    if state.get("tmdb_result"):
        sections.append(f"### TMDB Agent Output\n{state['tmdb_result']}")
    if state.get("rag_result"):
        sections.append(f"### RAG Knowledge Base Output\n{state['rag_result']}")
    if state.get("search_result"):
        sections.append(f"### Web Search Output\n{state['search_result']}")

    if not sections:
        return {"answer": "No agent produced a result. Please try a different question."}

    # Single source — no synthesis needed, just return as-is
    if len(sections) == 1:
        return {"answer": sections[0].split("\n", 1)[-1].strip()}

    combined = "\n\n".join(sections)
    llm = _get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM.format(agent_outputs=combined)),
        HumanMessage(content=f"Question: {question}"),
    ])

    return {"answer": response.content}
