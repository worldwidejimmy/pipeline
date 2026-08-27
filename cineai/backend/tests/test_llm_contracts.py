"""T0 parse/contract evals (see EVALS-PLAN.md, section T0).

Zero-LLM-cost unit tests on the layers between the model and the app. These
replay REAL captured-style Claude outputs against `parse_llm_json` and the
supervisor's route-string normalization, so the worst historical bug in this
codebase — Claude wraps a "JSON only" reply in ```json fences, bare
json.loads() throws, a silent except-fallback searches TMDB for the whole
question verbatim, and "Inception" goes unanswered for WEEKS — becomes a
permanent regression test instead of a live-incident memory.

Fixtures live in evals/fixtures/llm_outputs.jsonl: {"id", "kind", "raw",
"expect"[, "expect_error"]} per line, kind is "intent_json" or "route".

Route normalization is NOT imported from src.agents.supervisor: that module
imports `langchain_core` and `src.config` at module scope (see the top-level
`from langchain_core.messages import ...` and `from src.config import
get_config` in supervisor.py), and this test venv only has fastapi+pytest
(no langchain — see `make test-unit` in the Makefile). Importing supervisor.py
here would either ImportError or drag in network/env config loading. Instead
the exact regex + valid-set logic is replicated below (supervisor.py lines
~124-132), and `test_route_logic_matches_supervisor_source` guards against the
two drifting apart.
"""
from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ -> import src.*

# src/llm.py does `from langchain_anthropic import ChatAnthropic`, and
# src/config.py (which it also imports) does `from dotenv import load_dotenv`.
# Neither package is in this test venv (make test-unit only installs
# fastapi+pytest — see the Makefile) or needed by parse_llm_json, which is
# pure stdlib json/re with no LLM or config dependency. Stub the two modules
# only if they're genuinely missing, so importing src.llm doesn't require
# standing up a real Anthropic client just to exercise a string parser. This
# exercises the REAL parse_llm_json from src/llm.py, not a copy.
try:
    import dotenv  # noqa: F401
except ImportError:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = fake_dotenv

try:
    import langchain_anthropic  # noqa: F401
except ImportError:
    fake_langchain_anthropic = types.ModuleType("langchain_anthropic")
    fake_langchain_anthropic.ChatAnthropic = object
    sys.modules["langchain_anthropic"] = fake_langchain_anthropic

from src.llm import parse_llm_json  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "llm_outputs.jsonl"
SUPERVISOR_SRC = Path(__file__).resolve().parents[1] / "src" / "agents" / "supervisor.py"

# --- replicated from src/agents/supervisor.py:supervisor_route_node, lines ~124-132 ---
# Keep byte-for-byte in sync with the source; test_route_logic_matches_supervisor_source
# below fails loudly if the valid-set literal in supervisor.py ever changes without this
# copy being updated.
_VALID_ROUTES = {
    "tmdb", "rag", "search", "music", "tmdb+rag", "tmdb+search", "tmdb+music",
    "music+search", "rag+search", "all",
}


def normalize_route(raw_llm_reply: str) -> str:
    """Mirrors supervisor.py's post-LLM route normalization exactly."""
    raw = raw_llm_reply.strip().lower()
    routing = raw if raw in _VALID_ROUTES else None
    if routing is None:
        tokens = re.findall(r"[a-z]+(?:\+[a-z]+)?", raw)
        routing = next(
            (t for t in sorted(tokens, key=len, reverse=True) if t in _VALID_ROUTES),
            "tmdb+rag",
        )
    return routing


def _load_fixtures():
    cases = []
    with FIXTURES.open() as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


_CASES = _load_fixtures()
_INTENT_CASES = [c for c in _CASES if c["kind"] == "intent_json"]
_ROUTE_CASES = [c for c in _CASES if c["kind"] == "route"]


def test_fixtures_load_and_cover_both_kinds():
    assert 15 <= len(_CASES) <= 20, f"expected 15-20 fixtures, got {len(_CASES)}"
    assert _INTENT_CASES, "no intent_json fixtures found"
    assert _ROUTE_CASES, "no route fixtures found"
    ids = [c["id"] for c in _CASES]
    assert len(ids) == len(set(ids)), "duplicate fixture ids"


@pytest.mark.parametrize("case", _INTENT_CASES, ids=[c["id"] for c in _INTENT_CASES])
def test_parse_llm_json_fixture(case):
    if case.get("expect_error"):
        with pytest.raises(ValueError):
            parse_llm_json(case["raw"])
    else:
        assert parse_llm_json(case["raw"]) == case["expect"]


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=[c["id"] for c in _ROUTE_CASES])
def test_route_normalization_fixture(case):
    assert normalize_route(case["raw"]) == case["expect"]


def test_route_logic_matches_supervisor_source():
    """Sync-guard: fail loudly if supervisor.py's valid-set or fallback regex
    changes without the copy in this test file being updated to match."""
    src = SUPERVISOR_SRC.read_text()

    # The exact literal set assignment, e.g.:
    #   valid = {"tmdb", "rag", "search", "music", "tmdb+rag", "tmdb+search", "tmdb+music",
    #            "music+search", "rag+search", "all"}
    m = re.search(r"valid\s*=\s*\{(.*?)\}", src, re.S)
    assert m, "could not find `valid = {...}` in supervisor.py — did the route logic move?"
    source_valid = set(re.findall(r'"([a-z+]+)"', m.group(1)))
    assert source_valid == _VALID_ROUTES, (
        "supervisor.py's valid-route set has drifted from the copy in "
        "test_llm_contracts.py — update _VALID_ROUTES above to match.\n"
        f"supervisor.py: {sorted(source_valid)}\n"
        f"this test:     {sorted(_VALID_ROUTES)}"
    )

    # The fallback regex pattern itself.
    assert r'r"[a-z]+(?:\+[a-z]+)?"' in src, (
        "supervisor.py's token-extraction regex has changed — update "
        "normalize_route() in test_llm_contracts.py to match the new pattern."
    )

    # The default fallback route when nothing valid is found.
    assert '"tmdb+rag")' in src, (
        "supervisor.py's default fallback route is no longer \"tmdb+rag\" — "
        "update normalize_route() in test_llm_contracts.py to match."
    )
