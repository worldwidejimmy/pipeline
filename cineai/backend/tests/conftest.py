"""Shared fixtures for backend unit tests.

usage.py keeps all state in module globals and reads its tunables from the
environment at import time, so every test gets a `usage` fixture that resets
the globals, pins the tunables to known values, and redirects the blacklist
file into tmp_path (never the real backend/data/).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ -> import src.*

import src.usage as usage_module  # noqa: E402


class _Headers(dict):
    """Case-insensitive lookup, matching Starlette's Headers behaviour."""

    def __init__(self, d):
        super().__init__({k.lower(): v for k, v in d.items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeRequest:
    """Duck-typed stand-in for fastapi.Request — usage.py only touches
    .headers, .query_params and .client.host."""

    class _Client:
        def __init__(self, host):
            self.host = host

    def __init__(self, headers=None, query=None, peer="10.0.0.1"):
        self.headers = _Headers(headers or {})
        self.query_params = query or {}
        self.client = self._Client(peer) if peer else None


@pytest.fixture
def usage(tmp_path, monkeypatch):
    m = usage_module
    # Known tunables regardless of the host environment
    monkeypatch.setattr(m, "PREVIEW_PASSWORD", "test-password")
    monkeypatch.setattr(m, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(m, "FREE_LIMIT", 3)
    monkeypatch.setattr(m, "WINDOW_SECONDS", 3600)
    monkeypatch.setattr(m, "DAILY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(m, "DAILY_TOKEN_HARD_CAP", 0)
    monkeypatch.setattr(m, "GLOBAL_DAILY_CALL_CAP", 0)
    monkeypatch.setattr(m, "AUTH_MAX_FAILS", 3)
    monkeypatch.setattr(m, "AUTH_LOCKOUT_SECONDS", 900)
    # Never touch the real backend/data/
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "_BLACKLIST_FILE", tmp_path / "ip_blacklist.json")
    # Fresh in-memory state
    monkeypatch.setattr(m, "_ip_hits", {})
    monkeypatch.setattr(m, "_token_day", "")
    monkeypatch.setattr(m, "_tokens_used", 0)
    monkeypatch.setattr(m, "_ip_stats", {})
    monkeypatch.setattr(m, "_stats_day", "")
    monkeypatch.setattr(m, "_global_calls_day", "")
    monkeypatch.setattr(m, "_global_calls", 0)
    monkeypatch.setattr(m, "_blacklist", set())
    monkeypatch.setattr(m, "_auth_fails", {})
    return m


@pytest.fixture
def anon():
    """Anonymous request from a fixed IP (via Cloudflare header)."""
    def make(ip="203.0.113.7"):
        return FakeRequest(headers={"CF-Connecting-IP": ip})
    return make


@pytest.fixture
def unlimited():
    """Signed-in request carrying the valid access token."""
    def make(ip="203.0.113.9"):
        return FakeRequest(headers={"CF-Connecting-IP": ip, "X-Access-Token": "test-token"})
    return make
