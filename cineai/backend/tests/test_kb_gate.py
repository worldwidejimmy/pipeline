"""T0: the knowledge-base-first gate (src/kb_gate.py). Pure: no network, no model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.kb_gate import HIGH, LOW, apply_kb, decide  # noqa: E402

EVALS = Path(__file__).resolve().parents[2] / "evals"


@pytest.mark.parametrize("best, want", [
    (0.80, "relevant"), (HIGH, "relevant"), (HIGH - 0.001, "grade"),
    (0.50, "grade"), (LOW, "grade"), (LOW - 0.001, "none"), (0.0, "none"),
])
def test_decide_bands(best, want):
    assert decide(best) == want


def test_thresholds_are_ordered():
    assert 0 < LOW < HIGH < 1


@pytest.mark.parametrize("agents, relevant, want", [
    (["tmdb_agent"], True, ["tmdb_agent", "rag_agent"]),          # KB had something: RAG joins
    (["tmdb_agent", "rag_agent"], True, ["tmdb_agent", "rag_agent"]),
    (["tmdb_agent", "rag_agent"], False, ["tmdb_agent"]),         # nothing useful: skip its LLM call
    (["rag_agent"], False, ["search_agent"]),                     # never leave nothing to answer
    (["music_agent"], False, ["music_agent"]),
    (["tmdb_agent"], None, ["tmdb_agent"]),                       # no lookup ran: router's choice
])
def test_apply_kb(agents, relevant, want):
    assert apply_kb(agents, relevant) == want


def test_calibration_set_is_labelled_and_balanced():
    rows = [json.loads(l) for l in (EVALS / "datasets" / "kb_gate.jsonl").read_text().splitlines() if l.strip()]
    assert {r["label"] for r in rows} == {"relevant", "none"}
    assert len({r["id"] for r in rows}) == len(rows)
    assert sum(r.get("holdout", False) for r in rows) >= len(rows) // 4      # a real held-out split
