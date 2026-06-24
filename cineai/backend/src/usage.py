"""
Open-access usage control: per-IP rate limiting + daily token accounting.

The site is public. Anonymous visitors get a small free quota per IP; signing in
with the preview password (same token mechanism as before) lifts the limit.

Request chain in production is:
    Client → Cloudflare → host nginx → frontend-container nginx → backend
The frontend nginx overwrites X-Real-IP with the docker gateway address, so the
only trustworthy client identifiers reaching us are Cloudflare's CF-Connecting-IP
header and the first hop of X-Forwarded-For.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import Request

# ── Auth token (unchanged derivation — keeps existing sessions valid) ─────────
PREVIEW_PASSWORD = os.environ.get("PREVIEW_PASSWORD", "")
ACCESS_TOKEN = hashlib.sha256(f"sms-gate:{PREVIEW_PASSWORD}".encode()).hexdigest()

# ── Tunables (all overridable via env) ────────────────────────────────────────
FREE_LIMIT = int(os.environ.get("FREE_REQUESTS_PER_WINDOW", "3"))
WINDOW_SECONDS = int(os.environ.get("FREE_WINDOW_SECONDS", "3600"))   # rolling 1h
DAILY_TOKEN_BUDGET = int(os.environ.get("GROQ_DAILY_TOKEN_BUDGET", "100000"))

_lock = threading.Lock()
# ip → list of request timestamps within the current rolling window
_ip_hits: dict[str, list[float]] = {}
# daily token accumulator, reset on UTC date rollover
_token_day: str = ""
_tokens_used: int = 0


# ── Client identity ───────────────────────────────────────────────────────────
def client_ip(request: Request) -> str:
    """Best-effort true client IP. Prefer Cloudflare's header (survives both proxy
    hops); fall back to the first X-Forwarded-For entry, then the socket peer."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def access_token(request: Request) -> str | None:
    return request.headers.get("X-Access-Token") or request.query_params.get("_t")


def is_unlimited(request: Request) -> bool:
    """True when the caller presents the valid unlimited-access token.
    If no password is configured, the token is the sha256 of the empty string;
    we still require an exact match so anonymous callers stay rate-limited."""
    return bool(PREVIEW_PASSWORD) and access_token(request) == ACCESS_TOKEN


# ── Daily token accounting ────────────────────────────────────────────────────
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def add_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    global _token_day, _tokens_used
    n = (prompt_tokens or 0) + (completion_tokens or 0)
    if n <= 0:
        return
    with _lock:
        today = _today()
        if today != _token_day:
            _token_day = today
            _tokens_used = 0
        _tokens_used += n


def tokens_used_today() -> int:
    with _lock:
        if _today() != _token_day:
            return 0
        return _tokens_used


# ── Per-IP rate limiting (rolling window) ─────────────────────────────────────
def _prune(ip: str, now: float) -> list[float]:
    hits = [t for t in _ip_hits.get(ip, []) if now - t < WINDOW_SECONDS]
    if hits:
        _ip_hits[ip] = hits
    else:
        _ip_hits.pop(ip, None)
    return hits


def remaining(request: Request) -> dict:
    """Inspect quota WITHOUT consuming a credit (for /api/usage)."""
    if is_unlimited(request):
        return {"unlimited": True, "limit": FREE_LIMIT, "remaining": FREE_LIMIT,
                "used": 0, "reset_in": 0}
    now = time.time()
    ip = client_ip(request)
    with _lock:
        hits = _prune(ip, now)
    used = len(hits)
    reset_in = int(WINDOW_SECONDS - (now - min(hits))) if hits else 0
    return {"unlimited": False, "limit": FREE_LIMIT,
            "remaining": max(0, FREE_LIMIT - used), "used": used,
            "reset_in": max(0, reset_in)}


def consume(request: Request) -> dict:
    """Try to spend one credit. Returns {allowed, remaining, reset_in, unlimited}.
    Unlimited callers always pass and never accrue hits."""
    if is_unlimited(request):
        return {"allowed": True, "unlimited": True, "limit": FREE_LIMIT,
                "remaining": FREE_LIMIT, "reset_in": 0}
    now = time.time()
    ip = client_ip(request)
    with _lock:
        hits = _prune(ip, now)
        if len(hits) >= FREE_LIMIT:
            reset_in = int(WINDOW_SECONDS - (now - min(hits)))
            return {"allowed": False, "unlimited": False, "limit": FREE_LIMIT,
                    "remaining": 0, "reset_in": max(0, reset_in)}
        hits.append(now)
        _ip_hits[ip] = hits
        reset_in = int(WINDOW_SECONDS - (now - min(hits)))
    return {"allowed": True, "unlimited": False, "limit": FREE_LIMIT,
            "remaining": max(0, FREE_LIMIT - len(hits)), "reset_in": max(0, reset_in)}


def snapshot(request: Request) -> dict:
    """Full usage payload for the /api/usage endpoint."""
    q = remaining(request)
    return {
        "unlimited":          q["unlimited"],
        "free_limit":         FREE_LIMIT,
        "free_remaining":     q["remaining"],
        "free_used":          q["used"] if "used" in q else FREE_LIMIT - q["remaining"],
        "window_seconds":     WINDOW_SECONDS,
        "reset_in":           q["reset_in"],
        "tokens_used_today":  tokens_used_today(),
        "token_budget":       DAILY_TOKEN_BUDGET,
    }
