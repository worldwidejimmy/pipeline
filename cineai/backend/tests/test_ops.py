"""T0: the public Evals & ops data (src/ops.py): log parsing and the allow-list."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import ops  # noqa: E402

LOG = """════════ nightly RAG update @ 20261028T080001Z ════════
  [1/2] ✓ 'Clayface' (2026) ?★
  [2/2] ✓ 'Ray Gunn' (None) ?★
✓ Done — 2 saved, 0 skipped
  151 chunks to embed and insert
✓ Done — 151 chunks added, 122220 total in Milvus
════════ done @ 20261028T080021Z ════════
"""


def test_parse_ingest_log():
    r = ops.parse_ingest_log(LOG)
    assert r == {"reviews": ["Clayface (2026)", "Ray Gunn"], "chunks": 151, "total_rows": 122220, "ok": True}


def test_failed_run_is_not_ok():
    assert ops.parse_ingest_log(LOG.replace("════════ done @", "Traceback (most recent call last):"))["ok"] is False


def test_jobs_are_public_safe():
    text = repr(ops.JOBS).lower()
    for leak in ("/home", "dropbox", ".env", "ssh", "@", "ovh"):
        assert leak not in text
    assert {j["id"] for j in ops.JOBS} >= {"evals", "ingest", "backup", "devops", "seo"}


# ── Per-app Claude usage & cache-read ratio (the Console only groups by workspace) ──
def test_usage_by_day_splits_live_and_eval_and_computes_cache_ratio():
    rows = [
        {"ts": "2026-11-03T01:00:00+00:00", "source": "live", "model": "m", "calls": 7,
         "in": 10_000, "cache_read": 4_000, "cache_write": 0, "out": 1_000},
        {"ts": "2026-11-03T02:00:00+00:00", "source": "live", "model": "m", "calls": 6,
         "in": 10_000, "cache_read": 6_000, "cache_write": 500, "out": 900, "abnormal": ["refusal"]},
        {"ts": "2026-11-03T04:20:00+00:00", "source": "eval", "model": "m", "calls": 5,
         "in": 5_000, "cache_read": 0, "cache_write": 0, "out": 100},
        {"ts": "2026-11-02T09:00:00+00:00", "model": "m", "calls": 1, "in": 0, "out": 0},
    ]
    out = ops.usage_by_day(rows)
    live = next(a for a in out if a["day"] == "2026-11-03" and a["source"] == "live")
    assert live["answers"] == 2 and live["calls"] == 13 and live["cache_read_ratio"] == 0.5
    assert live["abnormal"] == 1 and live["models"] == ["m"]
    assert next(a for a in out if a["source"] == "eval")["cache_read_ratio"] == 0.0
    assert next(a for a in out if a["day"] == "2026-11-02")["cache_read_ratio"] is None   # no input
    assert [a["day"] for a in ops.usage_by_day(rows, days=1)] == ["2026-11-03", "2026-11-03"]


def test_record_usage_has_no_question_or_ip(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "USAGE_LOG", tmp_path / "u.jsonl")
    ops.record_usage(model="m", calls=2, input_tokens=10, cache_read=5, cache_write=0,
                     output_tokens=3, abnormal=[])
    row = json.loads((tmp_path / "u.jsonl").read_text())
    assert set(row) == {"ts", "model", "source", "calls", "in", "cache_read", "cache_write", "out"}
