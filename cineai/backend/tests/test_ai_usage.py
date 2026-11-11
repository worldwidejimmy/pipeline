"""T0: the per-call AI token log (src/ai_usage.py). No network."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import ai_usage  # noqa: E402

KEYS = {"ts", "provider", "model", "role", "in", "out", "cache_read", "cache_write_5m", "cache_write_1h"}


def test_non_streamed_uses_raw_api_usage():
    msg = SimpleNamespace(
        response_metadata={"model": "claude-haiku-5-5", "usage": {
            "input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 4502,
            "cache_creation": {"ephemeral_5m_input_tokens": 4502, "ephemeral_1h_input_tokens": 0}}},
        usage_metadata={"input_tokens": 4512})
    assert ai_usage.from_message(msg) == {"inp": 10, "out": 4, "cache_read": 0,
                                          "cache_write_5m": 4502, "cache_write_1h": 0}


def test_streamed_subtracts_cache_from_input():
    # Streamed replies only carry usage_metadata, whose input_tokens includes the cache.
    msg = SimpleNamespace(response_metadata={"model_name": "claude-haiku-5-5"}, usage_metadata={
        "input_tokens": 4512, "output_tokens": 4,
        "input_token_details": {"cache_read": 4402, "cache_creation": 100}})
    assert ai_usage.from_message(msg) == {"inp": 10, "out": 4, "cache_read": 4402,
                                          "cache_write_5m": 100, "cache_write_1h": 0}


def test_no_usage_logs_nothing():
    assert ai_usage.from_message(SimpleNamespace(response_metadata={}, usage_metadata=None)) is None


def test_sdk_usage_without_ttl_split_counts_as_5m():
    u = SimpleNamespace(input_tokens=5, output_tokens=7, cache_read_input_tokens=1,
                        cache_creation_input_tokens=30, cache_creation=None)
    assert ai_usage.from_anthropic_usage(u)["cache_write_5m"] == 30


def test_record_appends_one_line_with_only_token_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_usage, "DIR", tmp_path)
    monkeypatch.setenv("USAGE_SOURCE", "eval")
    ai_usage.record(model="claude-haiku-5-5", inp=1, out=2)
    ai_usage.record(model="claude-sonnet-5-5", inp=3, out=4, role="judge")
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1 and len(files[0].name) == len("2026-11.jsonl")
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [r["role"] for r in rows] == ["eval", "judge"]
    assert all(set(r) == KEYS and r["provider"] == "anthropic" and r["ts"].endswith("Z") for r in rows)


def test_callback_logs_each_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_usage, "DIR", tmp_path)
    monkeypatch.delenv("USAGE_SOURCE", raising=False)
    msg = SimpleNamespace(response_metadata={"model_name": "m"},
                          usage_metadata={"input_tokens": 9, "output_tokens": 1})
    ai_usage.CALLBACK.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=msg)]]))
    row = json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert (row["role"], row["model"], row["in"]) == ("search", "m", 9)


def test_record_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(ai_usage, "DIR", blocker / "sub")      # mkdir fails
    ai_usage.record(model="m", inp=1, out=1)                    # must not raise
