"""Calibrated relevance gate for knowledge-base results. Pure (no I/O), shared by the
web pipeline (src/agents/kb_lookup.py) and the MCP server's search tool.

`best` is the best cosine similarity between the question's embedding and its top
passages (text-embedding-3-small). The fused hybrid score can't be used: it is relative to
the other results, not a calibrated similarity. LOW/HIGH come from evals/experiments/kb_gate_experiment.py (2026-11-02, 44
answerable + 24 unanswerable questions, tuned on one split, checked on a held-out one):
at HIGH, 0 false "relevant" on both splits; below LOW, only unanswerable questions; the
band between held both kinds (about a quarter of questions), so it needs a judgement.
Re-run the experiment after changing the embedding model or the corpus (T2 also gates
the thresholds nightly).
"""
LOW, HIGH = 0.45, 0.60


def decide(best: float) -> str:
    """'relevant' (>= HIGH), 'none' (< LOW) or 'grade' (unsure band: needs a judgement)."""
    if best >= HIGH:
        return "relevant"
    if best < LOW:
        return "none"
    return "grade"


def apply_kb(agents: list[str], kb_relevant: bool | None) -> list[str]:
    """Knowledge-base-first adjustment of the router's choice (src/graph/pipeline.py:_dispatch).

    relevant → the RAG agent always joins (the KB has something on this question);
    not relevant → the RAG agent is dropped (it would only say "nothing found" and cost
    an LLM call), with web search as the fallback if nothing else is left.
    None (no lookup ran) → unchanged."""
    if kb_relevant is None:
        return agents
    if kb_relevant:
        return agents if "rag_agent" in agents else agents + ["rag_agent"]
    rest = [a for a in agents if a != "rag_agent"]
    return rest or ["search_agent"]
