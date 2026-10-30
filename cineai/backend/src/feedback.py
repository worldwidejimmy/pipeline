"""👍/👎 answer feedback: the user-signal half of the eval flywheel.

Flow: `_stream_pipeline` gives every answer an id (also its LangSmith run id when
traced) and calls `remember()` with the question and the final answer. The browser
votes by id only (`POST /api/feedback`), so a client can't inject arbitrary
"question/answer" text into the record: the server looks up what it actually
answered. Each vote is
  - appended to data/feedback/feedback-YYYY-MM.jsonl (gitignored), redacted with
    the same PII rules as tracing, client IP as a salted hash only;
  - attached to the LangSmith trace as `user_rating` (when the run was traced);
  - visible on /admin. 👎 items feed evals/feedback_triage.py, which drafts
    candidate eval cases for HUMAN review. Visitor text never goes into the public
    datasets automatically.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from src import tracing
from src.tracing import redact

_DIR = Path(__file__).resolve().parent.parent / "data" / "feedback"
_RECENT_MAX, _RECENT_TTL = 1000, 24 * 3600         # answers you can still vote on
_PER_IP_PER_HOUR = 30
MAX_COMMENT = 500

_recent: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_votes_by_ip: dict[str, deque] = defaultdict(deque)


class FeedbackError(ValueError):
    """Message is safe to return to the client."""


def remember(run_id: str, *, question: str, answer: str, route: str | None, model: str | None,
             traced: bool) -> None:
    """Called once per finished answer. Redacted on the way in; nothing raw is kept."""
    with _lock:
        _recent[run_id] = {"q": redact(question[:500]), "a": redact(answer[:1500]), "route": route,
                           "model": model, "traced": traced, "t": time.time()}
        _recent.move_to_end(run_id)
        while len(_recent) > _RECENT_MAX:
            _recent.popitem(last=False)


def _rate_ok(ip_hash: str, now: float) -> bool:
    q = _votes_by_ip[ip_hash]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= _PER_IP_PER_HOUR:
        return False
    q.append(now)
    return True


def submit(run_id: str, rating: int, comment: str, ip_hash: str) -> dict:
    try:
        uuid.UUID(run_id)
    except (ValueError, TypeError):
        raise FeedbackError("unknown answer")
    if rating not in (1, -1):
        raise FeedbackError("rating must be 1 or -1")
    now = time.time()
    with _lock:
        run = _recent.get(run_id)
        if not run or now - run["t"] > _RECENT_TTL:
            raise FeedbackError("this answer can no longer be rated")
    if not _rate_ok(ip_hash, now):
        raise FeedbackError("too many votes; try later")
    comment = redact((comment or "").strip()[:MAX_COMMENT])
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
           "rating": rating, "comment": comment, "question": run["q"], "answer_head": run["a"][:600],
           "route": run["route"], "model": run["model"], "ip": ip_hash}
    _DIR.mkdir(parents=True, exist_ok=True)
    with (_DIR / f"feedback-{rec['ts'][:7]}.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    if run["traced"] and tracing.ENABLED:
        # Off the request path: LangSmith latency or outages never affect the vote.
        threading.Thread(target=_to_langsmith, args=(run_id, rating, comment), daemon=True).start()
    return {"ok": True}


def _to_langsmith(run_id: str, rating: int, comment: str) -> None:
    try:
        tracing._client().create_feedback(run_id, key="user_rating", score=1 if rating > 0 else 0,
                                          comment=comment or None)
    except Exception as exc:
        print(f"[feedback] LangSmith feedback failed: {type(exc).__name__}")


def load(days: int = 30) -> list[dict]:
    """Latest vote per answer from the last `days` days (a re-vote replaces the earlier one)."""
    cutoff = time.time() - days * 86400
    latest: dict[str, dict] = {}
    for f in sorted(_DIR.glob("feedback-*.jsonl"))[-3:]:
        for ln in f.read_text().splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if datetime.fromisoformat(r["ts"]).timestamp() >= cutoff:
                latest[r["run_id"]] = r
    return sorted(latest.values(), key=lambda r: r["ts"], reverse=True)


def summary(days: int = 30) -> dict:
    """For /admin: totals, per-route split, and the recent 👎 with what was asked."""
    rows = load(days)
    by_route: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        by_route[r.get("route") or "?"][0 if r["rating"] > 0 else 1] += 1
    return {"days": days, "up": sum(r["rating"] > 0 for r in rows), "down": sum(r["rating"] < 0 for r in rows),
            "by_route": {k: {"up": v[0], "down": v[1]} for k, v in sorted(by_route.items())},
            "recent_down": [{k: r[k] for k in ("ts", "question", "comment", "answer_head", "route", "model")}
                            for r in rows if r["rating"] < 0][:25]}
