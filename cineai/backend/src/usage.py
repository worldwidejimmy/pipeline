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
import json
import os
import pathlib
import threading
import time
from datetime import datetime, timezone

from fastapi import Request

# ── Auth token (unchanged derivation — keeps existing sessions valid) ─────────
PREVIEW_PASSWORD = os.environ.get("PREVIEW_PASSWORD", "")
ACCESS_TOKEN = hashlib.sha256(f"sms-gate:{PREVIEW_PASSWORD}".encode()).hexdigest()

# ── Tunables (all overridable via env) ────────────────────────────────────────
FREE_LIMIT = int(os.environ.get("FREE_REQUESTS_PER_WINDOW", "10"))
WINDOW_SECONDS = int(os.environ.get("FREE_WINDOW_SECONDS", "3600"))   # rolling 1h
# 0 = no cap; the meter just shows cumulative tokens used today by everyone.
DAILY_TOKEN_BUDGET = int(os.environ.get("DAILY_TOKEN_BUDGET", "0"))
# Hard kill-switch: when today's total reaches this, anonymous LLM calls are paused
# (signed-in/admin still works). 0 = disabled. Protects against runaway paid spend.
DAILY_TOKEN_HARD_CAP = int(os.environ.get("DAILY_TOKEN_HARD_CAP", "0"))
# Global ceiling on anonymous searches per day across the whole site (research/demo
# project). Each /api/query or /api/compare counts as 1; signed-in/admin bypasses.
# 0 = disabled.
GLOBAL_DAILY_CALL_CAP = int(os.environ.get("GLOBAL_DAILY_CALL_CAP", "30"))

AUTH_MAX_FAILS = int(os.environ.get("AUTH_MAX_FAILS", "5"))
AUTH_LOCKOUT_SECONDS = int(os.environ.get("AUTH_LOCKOUT_SECONDS", "900"))  # 15 min

_DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
_BLACKLIST_FILE = _DATA_DIR / "ip_blacklist.json"

_lock = threading.Lock()
# ip → list of request timestamps within the current rolling window
_ip_hits: dict[str, list[float]] = {}
# daily token accumulator, reset on UTC date rollover
_token_day: str = ""
_tokens_used: int = 0
# per-IP daily stats for the admin screen: ip → {requests, tokens, first_seen, last_seen}
_ip_stats: dict[str, dict] = {}
_stats_day: str = ""
# global anonymous-search counter for the daily site-wide cap (reset on UTC rollover)
_global_calls_day: str = ""
_global_calls: int = 0
# blacklisted IPs (file-backed so it survives restarts)
_blacklist: set[str] = set()
# ip → recent failed /api/auth attempt timestamps (lockout)
_auth_fails: dict[str, list[float]] = {}


def _load_blacklist() -> None:
    global _blacklist
    try:
        if _BLACKLIST_FILE.exists():
            _blacklist = set(json.loads(_BLACKLIST_FILE.read_text()))
    except Exception:
        _blacklist = set()


def _save_blacklist() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _BLACKLIST_FILE.write_text(json.dumps(sorted(_blacklist)))
    except Exception:
        pass


_load_blacklist()


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


def _roll_stats_day_locked() -> None:
    global _stats_day, _ip_stats
    today = _today()
    if today != _stats_day:
        _stats_day = today
        _ip_stats = {}


def _ip_stat_locked(ip: str) -> dict:
    s = _ip_stats.get(ip)
    if s is None:
        now = time.time()
        s = {"requests": 0, "tokens": 0, "first_seen": now, "last_seen": now}
        _ip_stats[ip] = s
    return s


def add_tokens(prompt_tokens: int, completion_tokens: int, ip: str | None = None) -> None:
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
        if ip:
            _roll_stats_day_locked()
            _ip_stat_locked(ip)["tokens"] += n


def tokens_used_today() -> int:
    with _lock:
        if _today() != _token_day:
            return 0
        return _tokens_used


def over_hard_cap() -> bool:
    """True when today's total token spend has hit the kill-switch ceiling."""
    return DAILY_TOKEN_HARD_CAP > 0 and tokens_used_today() >= DAILY_TOKEN_HARD_CAP


def _roll_global_day_locked() -> None:
    global _global_calls_day, _global_calls
    today = _today()
    if today != _global_calls_day:
        _global_calls_day = today
        _global_calls = 0


def global_calls_today() -> int:
    with _lock:
        if _today() != _global_calls_day:
            return 0
        return _global_calls


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
    """Try to spend one credit. Returns {allowed, remaining, reset_in, unlimited, blocked}.
    Blacklisted IPs are always denied; unlimited callers always pass."""
    ip = client_ip(request)
    if is_blacklisted(ip):
        return {"allowed": False, "blocked": True, "unlimited": False,
                "limit": FREE_LIMIT, "remaining": 0, "reset_in": 0}
    if is_unlimited(request):
        with _lock:
            _roll_stats_day_locked()
            s = _ip_stat_locked(ip)
            s["requests"] += 1
            s["last_seen"] = time.time()
        return {"allowed": True, "unlimited": True, "limit": FREE_LIMIT,
                "remaining": FREE_LIMIT, "reset_in": 0}
    global _global_calls
    now = time.time()
    with _lock:
        hits = _prune(ip, now)
        if len(hits) >= FREE_LIMIT:
            reset_in = int(WINDOW_SECONDS - (now - min(hits)))
            return {"allowed": False, "unlimited": False, "limit": FREE_LIMIT,
                    "remaining": 0, "reset_in": max(0, reset_in)}
        # Site-wide daily ceiling (checked before spending the per-IP credit)
        _roll_global_day_locked()
        if GLOBAL_DAILY_CALL_CAP > 0 and _global_calls >= GLOBAL_DAILY_CALL_CAP:
            return {"allowed": False, "global_cap": True, "unlimited": False,
                    "limit": FREE_LIMIT, "remaining": 0, "reset_in": 0}
        _global_calls += 1
        hits.append(now)
        _ip_hits[ip] = hits
        reset_in = int(WINDOW_SECONDS - (now - min(hits)))
        _roll_stats_day_locked()
        s = _ip_stat_locked(ip)
        s["requests"] += 1
        s["last_seen"] = now
    return {"allowed": True, "unlimited": False, "limit": FREE_LIMIT,
            "remaining": max(0, FREE_LIMIT - len(hits)), "reset_in": max(0, reset_in)}


# ── Blacklist ─────────────────────────────────────────────────────────────────
def is_blacklisted(ip: str) -> bool:
    return ip in _blacklist


def blacklist_add(ip: str) -> None:
    with _lock:
        _blacklist.add(ip)
        _save_blacklist()


def blacklist_remove(ip: str) -> None:
    with _lock:
        _blacklist.discard(ip)
        _save_blacklist()


# ── Auth lockout (brute-force protection on /api/auth) ────────────────────────
def auth_locked(ip: str) -> bool:
    now = time.time()
    with _lock:
        fails = [t for t in _auth_fails.get(ip, []) if now - t < AUTH_LOCKOUT_SECONDS]
        _auth_fails[ip] = fails
        return len(fails) >= AUTH_MAX_FAILS


def record_auth_fail(ip: str) -> None:
    with _lock:
        _auth_fails.setdefault(ip, []).append(time.time())


def reset_auth_fails(ip: str) -> None:
    with _lock:
        _auth_fails.pop(ip, None)


# ── Admin snapshot (per-IP usage table) ───────────────────────────────────────
def admin_snapshot() -> dict:
    with _lock:
        _roll_stats_day_locked()
        rows = [
            {"ip": ip, "requests": s["requests"], "tokens": s["tokens"],
             "last_seen": int(s["last_seen"]), "blacklisted": ip in _blacklist}
            for ip, s in _ip_stats.items()
        ]
        blacklist = sorted(_blacklist)
    rows.sort(key=lambda r: (r["requests"], r["tokens"]), reverse=True)
    return {
        "day":          _stats_day or _today(),
        "total_tokens": tokens_used_today(),
        "calls_today":  global_calls_today(),
        "call_cap":     GLOBAL_DAILY_CALL_CAP,
        "free_limit":   FREE_LIMIT,
        "window_seconds": WINDOW_SECONDS,
        "ips":          rows,
        "blacklist":    blacklist,
    }


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
        "calls_today":        global_calls_today(),
        "call_cap":           GLOBAL_DAILY_CALL_CAP,
    }
