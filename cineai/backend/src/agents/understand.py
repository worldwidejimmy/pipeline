"""Understand node: turns a follow-up into a standalone question (query rewriting).

The source agents (TMDB, MusicBrainz, web search, the knowledge base) each see one
question, not the conversation. So "Who composed its score?" after a question about
Inception searched TMDB for "its score" and found nothing. With history present, one
small structured call rewrites the message into a self-contained question ("Who
composed the score for Inception (2010)?"). The agents get the rewrite; the final
answer still sees the user's own words and the history.

No history (a first question) → no LLM call, the question passes through unchanged.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.guardrails import UNTRUSTED_RULE, wrap_untrusted
from src.llm import extract

_SYSTEM = """You rewrite the user's latest message in a movie/TV/music chat as one standalone question.

Resolve pronouns and references ("it", "its score", "the director", "that band", "the second one")
using the conversation, so the question makes sense with no history: name the title (with its year
when known), person or artist. Keep the user's intent and wording otherwise. If the message is
already standalone, or is a new topic, return it unchanged. Never answer the question.
""" + UNTRUSTED_RULE


class Standalone(BaseModel):
    standalone: str = Field(description="The latest message rewritten as a self-contained question")


def history_block(history: list[dict], turns: int = 3) -> str:
    """Last few turns, oldest first, answers trimmed: enough to resolve references."""
    lines = [f"User: {h['q']}\nAssistant: {h['a'][:400]}" for h in history[-turns:]]
    return "Conversation so far:\n" + wrap_untrusted("conversation", "\n\n".join(lines))


async def understand_node(state: dict) -> dict:
    question = state["question"]
    history = state.get("history") or []
    if not history:
        return {"user_question": question}
    out = await extract(Standalone, _SYSTEM, f"Latest message: {question}",
                        dynamic=history_block(history), max_tokens=120)
    standalone = ((out or {}).get("standalone") or "").strip()
    if not standalone or len(standalone) > 400:
        return {"user_question": question}               # fail open: the original question
    return {"user_question": question, "question": standalone}
