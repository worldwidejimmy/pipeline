"""T0: answer feedback (src/feedback.py): ids, validation, redaction, rate limit, summary."""
from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name, attrs in (("dotenv", {"load_dotenv": lambda *a, **k: None}),):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

import src.feedback as fb  # noqa: E402

_AT = "@"   # assembled at runtime: the public-repo guard blocks email-shaped literals


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_DIR", tmp_path)
    monkeypatch.setattr(fb.tracing, "ENABLED", False)
    fb._recent.clear()
    fb._votes_by_ip.clear()
    return tmp_path


def _answer(**kw) -> str:
    rid = str(uuid.uuid4())
    fb.remember(rid, question=kw.get("q", "Who directed Inception?"), answer=kw.get("a", "Christopher Nolan."),
                route="tmdb", model="claude-test", traced=False)
    return rid


def test_vote_is_recorded_with_the_servers_own_question_and_answer(store):
    rid = _answer()
    assert fb.submit(rid, -1, "wrong year", "iphash") == {"ok": True}
    (row,) = fb.load()
    assert row["question"] == "Who directed Inception?" and row["answer_head"] == "Christopher Nolan."
    assert row["rating"] == -1 and row["comment"] == "wrong year" and row["ip"] == "iphash"


def test_unknown_or_malformed_ids_are_refused(store):
    for bad in ("not-a-uuid", str(uuid.uuid4())):
        with pytest.raises(fb.FeedbackError):
            fb.submit(bad, 1, "", "iphash")


def test_rating_must_be_plus_or_minus_one(store):
    with pytest.raises(fb.FeedbackError):
        fb.submit(_answer(), 5, "", "iphash")


def test_pii_is_redacted_in_question_and_comment(store):
    rid = _answer(q=f"I'm jane{_AT}example.com, who directed Heat?")
    fb.submit(rid, -1, f"call me on {'-'.join(['415', '555', '0134'])}", "iphash")
    row = fb.load()[0]
    assert _AT not in row["question"] and "<email>" in row["question"] and "<number>" in row["comment"]


def test_revote_replaces_and_summary_counts_latest(store):
    rid = _answer()
    fb.submit(rid, -1, "", "iphash")
    fb.submit(rid, 1, "", "iphash")
    fb.submit(_answer(), -1, "missed the director", "other")
    s = fb.summary()
    assert (s["up"], s["down"]) == (1, 1) and s["recent_down"][0]["comment"] == "missed the director"
    assert s["by_route"] == {"tmdb": {"up": 1, "down": 1}}


def test_per_ip_rate_limit(store, monkeypatch):
    monkeypatch.setattr(fb, "_PER_IP_PER_HOUR", 2)
    fb.submit(_answer(), 1, "", "spammer")
    fb.submit(_answer(), 1, "", "spammer")
    with pytest.raises(fb.FeedbackError, match="too many"):
        fb.submit(_answer(), 1, "", "spammer")
