"""Unit tests for src/usage.py — the module that guards paid Claude spend.

Covers: client IP resolution, the unlimited-access token, the per-IP rolling
window, the site-wide daily call cap, the daily token accounting + hard cap,
the blacklist (incl. file persistence), auth lockout, and the admin snapshot.
"""
import json
import time

from conftest import FakeRequest


# ── client_ip ─────────────────────────────────────────────────────────────────

def test_client_ip_prefers_cloudflare_header(usage):
    r = FakeRequest(headers={"CF-Connecting-IP": " 198.51.100.4 ",
                             "X-Forwarded-For": "10.9.9.9, 10.0.0.1"})
    assert usage.client_ip(r) == "198.51.100.4"


def test_client_ip_falls_back_to_first_xff_hop(usage):
    r = FakeRequest(headers={"X-Forwarded-For": "198.51.100.8, 172.19.0.1"})
    assert usage.client_ip(r) == "198.51.100.8"


def test_client_ip_falls_back_to_socket_peer(usage):
    assert usage.client_ip(FakeRequest(peer="192.0.2.5")) == "192.0.2.5"


def test_client_ip_unknown_when_no_peer(usage):
    assert usage.client_ip(FakeRequest(peer=None)) == "unknown"


# ── unlimited-access token ────────────────────────────────────────────────────

def test_valid_token_is_unlimited(usage, unlimited):
    assert usage.is_unlimited(unlimited()) is True


def test_wrong_token_is_not_unlimited(usage):
    r = FakeRequest(headers={"X-Access-Token": "wrong"})
    assert usage.is_unlimited(r) is False


def test_token_accepted_via_query_param(usage):
    r = FakeRequest(query={"_t": "test-token"})
    assert usage.is_unlimited(r) is True


def test_no_password_configured_means_nobody_is_unlimited(usage, monkeypatch):
    # Guard against the empty-password derivation accidentally unlocking access
    monkeypatch.setattr(usage, "PREVIEW_PASSWORD", "")
    r = FakeRequest(headers={"X-Access-Token": usage.ACCESS_TOKEN})
    assert usage.is_unlimited(r) is False


# ── per-IP rolling window ─────────────────────────────────────────────────────

def test_consume_decrements_remaining(usage, anon):
    r = anon()
    out = usage.consume(r)
    assert out["allowed"] is True and out["remaining"] == 2
    out = usage.consume(r)
    assert out["allowed"] is True and out["remaining"] == 1


def test_consume_blocks_at_limit_with_reset_eta(usage, anon):
    r = anon()
    for _ in range(usage.FREE_LIMIT):
        assert usage.consume(r)["allowed"] is True
    out = usage.consume(r)
    assert out["allowed"] is False
    assert out["remaining"] == 0
    assert 0 < out["reset_in"] <= usage.WINDOW_SECONDS


def test_window_expiry_restores_quota(usage, anon):
    r = anon()
    for _ in range(usage.FREE_LIMIT):
        usage.consume(r)
    assert usage.consume(r)["allowed"] is False
    # Age all recorded hits past the window boundary
    ip = usage.client_ip(r)
    usage._ip_hits[ip] = [t - (usage.WINDOW_SECONDS + 1) for t in usage._ip_hits[ip]]
    assert usage.consume(r)["allowed"] is True


def test_limits_are_per_ip(usage, anon):
    for _ in range(usage.FREE_LIMIT):
        usage.consume(anon("203.0.113.1"))
    assert usage.consume(anon("203.0.113.1"))["allowed"] is False
    assert usage.consume(anon("203.0.113.2"))["allowed"] is True


def test_remaining_does_not_consume(usage, anon):
    r = anon()
    for _ in range(5):
        q = usage.remaining(r)
    assert q["remaining"] == usage.FREE_LIMIT
    assert usage.consume(r)["allowed"] is True


def test_unlimited_caller_bypasses_window(usage, unlimited):
    r = unlimited()
    for _ in range(usage.FREE_LIMIT * 3):
        out = usage.consume(r)
        assert out["allowed"] is True and out["unlimited"] is True


# ── site-wide daily call cap ──────────────────────────────────────────────────

def test_global_cap_blocks_across_different_ips(usage, anon, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_CALL_CAP", 2)
    assert usage.consume(anon("203.0.113.1"))["allowed"] is True
    assert usage.consume(anon("203.0.113.2"))["allowed"] is True
    out = usage.consume(anon("203.0.113.3"))
    assert out["allowed"] is False
    assert out.get("global_cap") is True


def test_global_cap_not_spent_by_per_ip_denials(usage, anon, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_CALL_CAP", 5)
    r = anon()
    for _ in range(usage.FREE_LIMIT + 4):   # 3 allowed, 4 denied by the IP window
        usage.consume(r)
    assert usage.global_calls_today() == usage.FREE_LIMIT


def test_unlimited_bypasses_global_cap(usage, anon, unlimited, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_CALL_CAP", 1)
    assert usage.consume(anon())["allowed"] is True
    assert usage.consume(anon("203.0.113.99"))["allowed"] is False
    assert usage.consume(unlimited())["allowed"] is True


def test_global_cap_resets_on_new_day(usage, anon, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_CALL_CAP", 1)
    assert usage.consume(anon())["allowed"] is True
    assert usage.consume(anon("203.0.113.99"))["allowed"] is False
    usage._global_calls_day = "1999-01-01"   # simulate UTC date rollover
    usage._ip_hits.clear()
    assert usage.consume(anon("203.0.113.99"))["allowed"] is True
    assert usage.global_calls_today() == 1


# ── daily token accounting + hard cap ─────────────────────────────────────────

def test_add_tokens_accumulates(usage):
    usage.add_tokens(100, 50)
    usage.add_tokens(10, 5)
    assert usage.tokens_used_today() == 165


def test_add_tokens_ignores_zero_and_none(usage):
    usage.add_tokens(0, 0)
    usage.add_tokens(None, None)
    assert usage.tokens_used_today() == 0


def test_tokens_reset_on_new_day(usage):
    usage.add_tokens(500, 0)
    usage._token_day = "1999-01-01"
    assert usage.tokens_used_today() == 0
    usage.add_tokens(10, 0)
    assert usage.tokens_used_today() == 10


def test_hard_cap_disabled_by_default(usage):
    usage.add_tokens(10**9, 0)
    assert usage.over_hard_cap() is False


def test_hard_cap_trips_at_ceiling(usage, monkeypatch):
    monkeypatch.setattr(usage, "DAILY_TOKEN_HARD_CAP", 1000)
    usage.add_tokens(999, 0)
    assert usage.over_hard_cap() is False
    usage.add_tokens(1, 0)
    assert usage.over_hard_cap() is True


def test_per_ip_token_attribution(usage):
    usage.add_tokens(100, 20, ip="203.0.113.7")
    usage.add_tokens(5, 5, ip="203.0.113.7")
    assert usage._ip_stats["203.0.113.7"]["tokens"] == 130


# ── blacklist ─────────────────────────────────────────────────────────────────

def test_blacklisted_ip_is_always_denied(usage, anon):
    usage.blacklist_add("203.0.113.7")
    out = usage.consume(anon("203.0.113.7"))
    assert out["allowed"] is False and out["blocked"] is True


def test_blacklist_remove_restores_access(usage, anon):
    usage.blacklist_add("203.0.113.7")
    usage.blacklist_remove("203.0.113.7")
    assert usage.consume(anon("203.0.113.7"))["allowed"] is True


def test_blacklist_persists_to_file_and_reloads(usage):
    usage.blacklist_add("203.0.113.66")
    assert json.loads(usage._BLACKLIST_FILE.read_text()) == ["203.0.113.66"]
    usage._blacklist = set()          # simulate a fresh process
    usage._load_blacklist()
    assert usage.is_blacklisted("203.0.113.66") is True


# ── auth lockout ──────────────────────────────────────────────────────────────

def test_lockout_after_max_fails(usage):
    ip = "203.0.113.7"
    for _ in range(usage.AUTH_MAX_FAILS - 1):
        usage.record_auth_fail(ip)
    assert usage.auth_locked(ip) is False
    usage.record_auth_fail(ip)
    assert usage.auth_locked(ip) is True


def test_successful_auth_resets_fails(usage):
    ip = "203.0.113.7"
    for _ in range(usage.AUTH_MAX_FAILS):
        usage.record_auth_fail(ip)
    usage.reset_auth_fails(ip)
    assert usage.auth_locked(ip) is False


def test_old_fails_expire(usage):
    ip = "203.0.113.7"
    for _ in range(usage.AUTH_MAX_FAILS):
        usage.record_auth_fail(ip)
    usage._auth_fails[ip] = [t - (usage.AUTH_LOCKOUT_SECONDS + 1)
                             for t in usage._auth_fails[ip]]
    assert usage.auth_locked(ip) is False


# ── snapshots ─────────────────────────────────────────────────────────────────

def test_admin_snapshot_rows_and_flags(usage, anon):
    usage.consume(anon("203.0.113.1"))
    usage.consume(anon("203.0.113.1"))
    usage.consume(anon("203.0.113.2"))
    usage.add_tokens(100, 0, ip="203.0.113.1")
    usage.blacklist_add("203.0.113.2")
    snap = usage.admin_snapshot()
    assert snap["total_tokens"] == 100
    assert snap["calls_today"] == 3
    rows = {r["ip"]: r for r in snap["ips"]}
    assert rows["203.0.113.1"]["requests"] == 2
    assert rows["203.0.113.1"]["tokens"] == 100
    assert rows["203.0.113.2"]["blacklisted"] is True
    assert snap["ips"][0]["ip"] == "203.0.113.1"   # sorted by requests desc


def test_usage_snapshot_anonymous_shape(usage, anon):
    r = anon()
    usage.consume(r)
    snap = usage.snapshot(r)
    assert snap["unlimited"] is False
    assert snap["free_remaining"] == usage.FREE_LIMIT - 1
    assert snap["free_used"] == 1
    assert snap["calls_today"] == 1


def test_usage_snapshot_unlimited_shape(usage, unlimited):
    snap = usage.snapshot(unlimited())
    assert snap["unlimited"] is True
    assert snap["free_remaining"] == usage.FREE_LIMIT
