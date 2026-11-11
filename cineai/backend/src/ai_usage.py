"""
Per-call Claude token log for the server-wide AI usage dashboards and digests
(spec: server-management AI-USAGE.md).

After every Claude call that returns usage, ONE JSON line is appended to
`<AI_USAGE_DIR>/YYYY-MM.jsonl` (UTC month):

    {"ts": "…Z", "provider": "anthropic", "model": "…", "role": "search|eval|judge",
     "in": …, "out": …, "cache_read": …, "cache_write_5m": …, "cache_write_1h": …}

Tokens only: never the question, the answer or anything about the visitor. `in` uses
Anthropic's meaning (uncached input only; cache reads and writes are their own fields), so
the price file can cost each field at its own rate. Each line is a single O_APPEND write of
well under PIPE_BUF bytes, so concurrent writers (the backend and an eval run in the same
container) never interleave, and the file is never rewritten.

`role`: "search" for site traffic, "eval" when USAGE_SOURCE=eval (the nightly evals and
the model-promotion run set it), "judge" for the T3 judge (passed explicitly).

Logging must never break a call: every error is swallowed.

In Docker the directory is the bind mount `cineai/data/ai-usage` → `/app/ai-usage`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:            # the light unit-test venv has no langchain
    BaseCallbackHandler = object  # type: ignore[assignment,misc]

DIR = Path(os.environ.get("AI_USAGE_DIR")
           or Path(__file__).resolve().parents[2] / "data" / "ai-usage")


def _role() -> str:
    return "eval" if os.environ.get("USAGE_SOURCE") == "eval" else "search"


def record(*, model: str, inp: int, out: int, cache_read: int = 0, cache_write_5m: int = 0,
           cache_write_1h: int = 0, role: str | None = None) -> None:
    try:
        now = datetime.now(timezone.utc)
        row = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "provider": "anthropic",
               "model": str(model or "unknown")[:80], "role": role or _role(),
               "in": int(inp), "out": int(out), "cache_read": int(cache_read),
               "cache_write_5m": int(cache_write_5m), "cache_write_1h": int(cache_write_1h)}
        DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(DIR / f"{now:%Y-%m}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
        finally:
            os.close(fd)
    except Exception:
        pass


def _get(obj: Any, key: str, default: Any = 0) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def from_anthropic_usage(usage: Any) -> dict | None:
    """Fields from an Anthropic API `usage` (SDK object or dict)."""
    if not usage:
        return None
    cc = _get(usage, "cache_creation", None) or {}
    w5, w1 = int(_get(cc, "ephemeral_5m_input_tokens") or 0), int(_get(cc, "ephemeral_1h_input_tokens") or 0)
    total_w = int(_get(usage, "cache_creation_input_tokens") or 0)
    if not cc and total_w:
        w5 = total_w                     # no TTL split reported: our cache_control is the 5m default
    return {"inp": int(_get(usage, "input_tokens") or 0), "out": int(_get(usage, "output_tokens") or 0),
            "cache_read": int(_get(usage, "cache_read_input_tokens") or 0),
            "cache_write_5m": w5, "cache_write_1h": w1}


def from_message(msg: Any) -> dict | None:
    """Fields from a LangChain AIMessage. A non-streamed reply carries the raw API usage in
    response_metadata; a streamed one only has usage_metadata, whose input_tokens INCLUDES
    cache reads and writes (subtracted here), and whose cache_creation is either the total
    (streamed) or the remainder after the ephemeral_* split (non-streamed)."""
    meta = getattr(msg, "response_metadata", None) or {}
    raw = from_anthropic_usage(meta.get("usage"))
    if raw:
        return raw
    um = getattr(msg, "usage_metadata", None)
    if not um:
        return None
    d = um.get("input_token_details") or {}
    cr = int(d.get("cache_read") or 0)
    w1 = int(d.get("ephemeral_1h_input_tokens") or 0)
    w5 = int(d.get("ephemeral_5m_input_tokens") or 0) + int(d.get("cache_creation") or 0)
    return {"inp": max(int(um.get("input_tokens") or 0) - cr - w5 - w1, 0),
            "out": int(um.get("output_tokens") or 0),
            "cache_read": cr, "cache_write_5m": w5, "cache_write_1h": w1}


def _model_of(msg: Any) -> str:
    meta = getattr(msg, "response_metadata", None) or {}
    return meta.get("model") or meta.get("model_name") or "unknown"


class UsageCallback(BaseCallbackHandler):
    """Attached to every ChatAnthropic built by llm.get_chat(), so each call logs once."""

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            for gens in response.generations:
                for g in gens:
                    msg = getattr(g, "message", None)
                    fields = from_message(msg) if msg is not None else None
                    if fields:
                        record(model=_model_of(msg), **fields)
        except Exception:
            pass


CALLBACK = UsageCallback()
