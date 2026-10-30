"""T0: tracing privacy + routing (src/tracing.py). No network, no LangSmith account."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name, attrs in (("dotenv", {"load_dotenv": lambda *a, **k: None}),
                    ("langchain_anthropic", {"ChatAnthropic": object})):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

import src.tracing as tracing  # noqa: E402


# Fake PII/secrets are ASSEMBLED at runtime: the public-repo guard (rightly) blocks
# commits containing anything shaped like a real key, email address or phone number.
_AT, _DASH = "@", "-"
_EMAIL = "jane.doe+films" + _AT + "example.co.uk"
_PHONE_US = _DASH.join(["415", "555", "0134"])
_PHONE_UK = " ".join(["+44", "20", "7946", "0958"])
_CARD = " ".join(["4111"] * 4)
_ANT_KEY = _DASH.join(["sk", "ant", "api03", "AbCdEfGhIjKlMnOpQrSt"])
_LS_KEY = "_".join(["lsv2", "pt", "0123456789abcdef0123"])


@pytest.mark.parametrize("raw, want", [
    (f"mail me at {_EMAIL} please", "mail me at <email> please"),
    (f"call {_PHONE_US} or {_PHONE_UK}", "call <number> or <number>"),
    (f"card {_CARD}", "card <number>"),
    ("from 203.0.113.42 and 2001:db8:85a3:0:0:8a2e:370:7334", "from <ip> and <ip>"),
    (f"key {_ANT_KEY} and {_LS_KEY}", "key <secret> and <secret>"),
])
def test_redacts_personal_and_secret_strings(raw, want):
    assert tracing.redact(raw) == want


@pytest.mark.parametrize("keep", [
    "2001: A Space Odyssey (1968), 149 min, rated 8.3",
    "Inception is TMDB id 27205; released 2010-07-16",
    "MusicBrainz 092b603f-eb4c-4958-b10e-02420de5885b",
    "Ocean's 11 vs Ocean's Eleven, budget $85,000,000",
    "the 1999 film grossed 463517383 worldwide",          # 9 digits: below the phone threshold
])
def test_leaves_movie_facts_alone(keep):
    assert tracing.redact(keep) == keep


def test_implicit_tracing_switched_off():
    import os
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false" and os.environ["LANGSMITH_TRACING"] == "false"


def test_disabled_means_no_callbacks(monkeypatch):
    monkeypatch.setattr(tracing, "ENABLED", False)
    assert tracing.run_config("web") == ({}, None)
    assert tracing.run_config("eval", eval_tier="t4") == ({}, None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(tracing, "ENABLED", True)
    monkeypatch.setattr(tracing, "_client", lambda: "CLIENT")
    tr = types.ModuleType("langchain_core.tracers")
    tr.LangChainTracer = lambda **kw: ("tracer", kw)
    monkeypatch.setitem(sys.modules, "langchain_core.tracers", tr)
    llm = types.ModuleType("src.llm")
    llm.model_id = lambda: "claude-test"
    monkeypatch.setitem(sys.modules, "src.llm", llm)


def test_eval_runs_go_to_the_eval_project(enabled):
    cfg, rid = tracing.run_config("eval", eval_tier="t4", metadata={"case": "cars"})
    (_, kw), = cfg["callbacks"]
    assert kw["project_name"] == tracing.EVAL_PROJECT and rid
    assert cfg["run_name"] == "eval:t4" and "eval:t4" in cfg["tags"]
    assert cfg["metadata"] == {"source": "eval", "model": "claude-test", "eval_tier": "t4", "case": "cars"}


def test_web_runs_are_sampled_evals_are_not(enabled):
    assert tracing.run_config("web", sample=0.0) == ({}, None)
    cfg, _ = tracing.run_config("web", sample=1.0)
    assert cfg["callbacks"][0][1]["project_name"] == tracing.PROJECT and "ip" not in str(cfg["metadata"]).lower()
    assert tracing.run_config("eval", eval_tier="t3", sample=0.0)[1] is not None
