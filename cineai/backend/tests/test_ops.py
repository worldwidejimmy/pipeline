"""T0: the public Evals & ops data (src/ops.py): log parsing and the allow-list."""
from __future__ import annotations

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
