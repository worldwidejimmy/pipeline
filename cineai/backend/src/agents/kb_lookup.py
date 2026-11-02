"""Knowledge-base-first lookup with a calibrated relevance gate.

Every question searches the curated knowledge base (director/genre/decade guides, music
pages, ~24k Roger Ebert reviews) before routing. Retrieval always returns its nearest
passages, so the question is whether they're actually useful. The gate:

  best dense similarity >= HIGH  → relevant, no LLM call
  best dense similarity <  LOW   → nothing relevant, no LLM call
  in between                     → one small Haiku grader call decides, and picks the
                                   passages worth using

Thresholds and their calibration: src/kb_gate.py.

Effect on the graph (src/graph/pipeline.py:_dispatch): relevant → the RAG agent joins
the fan-out even if the router didn't pick it, and reuses these passages (no second
search); nothing relevant → the RAG agent is skipped (saving its LLM call).
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from src.guardrails import UNTRUSTED_RULE, wrap_untrusted
from src.llm import extract
from src.kb_gate import HIGH, LOW, decide
from src.tools.milvus_retriever import retrieve

_GRADER_SYSTEM = """You judge whether knowledge-base passages help answer a question about movies, TV or music.

A passage is useful only if BOTH hold:
1. It is about the specific film, show, person, artist or topic the question asks about (or is a
   guide whose subject covers it). A review of a DIFFERENT film is never useful, even if it discusses
   the same kind of thing (its own score, its own cast, the same genre).
2. It contains information, analysis or opinion that bears on what is asked. Right subject but no
   bearing (the question asks for someone's height or tour dates; the passage is a film review) is
   not useful.
When unsure, it is not useful. Judge only; never answer the question.
""" + UNTRUSTED_RULE


class Grade(BaseModel):
    useful: list[int] = Field(description="Numbers of the passages that help answer the question; empty if none")


async def _grade(question: str, chunks: list[dict]) -> list[int]:
    numbered = "\n\n".join(f"[{i + 1}] ({c['source']})\n{c['text'][:700]}" for i, c in enumerate(chunks))
    out = await extract(Grade, _GRADER_SYSTEM, f"Question: {question}",
                        dynamic="Passages:\n" + wrap_untrusted("knowledge_base", numbered), max_tokens=60)
    if out is None:
        return []                                       # fail closed: don't claim relevance
    return sorted({n - 1 for n in out.get("useful", []) if 1 <= n <= len(chunks)})


async def kb_lookup_node(state: dict) -> dict:
    t0 = time.time()
    question = state["question"]                        # standalone (see understand.py)
    r = await retrieve(question)
    chunks, best = r["chunks"], r["dense_max"]
    verdict = decide(best) if chunks else "none"
    by = "score"
    if verdict == "grade":
        keep = await _grade(question, chunks)
        verdict, by = ("relevant" if keep else "none"), "grader"
        useful = [chunks[i] for i in keep]
    elif verdict == "relevant":
        # Passages that are themselves in or near the relevant range; at least the top one.
        useful = [c for c in chunks if c["similarity"] >= LOW] or chunks[:1]
    else:
        useful = []
    return {"kb": {
        "relevant": verdict == "relevant",
        "by": by,                                       # "score" (thresholds) or "grader" (LLM)
        "best_score": best,
        "low": LOW, "high": HIGH,
        "chunks": useful,
        # Only the passages judged useful reach the RAG agent's prompt.
        "context": "\n\n---\n\n".join(f"[Source: {c['source']}]\n{c['text']}" for c in useful),
        "latency_ms": int((time.time() - t0) * 1000),
    }}
