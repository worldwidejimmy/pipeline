"""An upstream API failure degrades one source to a fixed note instead of failing the answer."""
import asyncio

import httpx
import pytest

from src.agents.resilient import is_transient, resilient, unavailable_note


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://musicbrainz.org/ws/2/artist?query=x")
    return httpx.HTTPStatusError(f"Server error '{code}'", request=req, response=httpx.Response(code, request=req))


def _run(node, state=None):
    return asyncio.run(node(state or {"question": "Tell me about The Cars"}))


def test_upstream_503_becomes_unavailable_note():
    async def node(state):
        raise _status_error(503)
    out = _run(resilient(node, result_key="music_result", source="MusicBrainz"))
    assert out["music_result"] == unavailable_note("MusicBrainz")
    assert "503" not in out["music_result"] and "http" not in out["music_result"]   # no raw error text in the prompt
    assert out["_upstream_error"] == {"source": "MusicBrainz", "status": 503, "transient": True}


def test_timeout_and_connection_errors_are_transient():
    async def node(state):
        raise httpx.ConnectTimeout("timed out")
    out = _run(resilient(node, result_key="tmdb_result", source="TMDB"))
    assert out["_upstream_error"] == {"source": "TMDB", "status": None, "transient": True}


def test_client_error_degrades_but_is_not_transient():
    async def node(state):
        raise _status_error(401)
    out = _run(resilient(node, result_key="tmdb_result", source="TMDB"))
    assert out["tmdb_result"] == unavailable_note("TMDB")
    assert out["_upstream_error"]["transient"] is False      # the eval still flags this one


def test_success_passes_through_and_bugs_still_raise():
    async def ok(state):
        return {"music_result": "fine"}
    assert _run(resilient(ok, result_key="music_result", source="MusicBrainz")) == {"music_result": "fine"}

    async def bug(state):
        raise KeyError("detail")
    with pytest.raises(KeyError):
        _run(resilient(bug, result_key="music_result", source="MusicBrainz"))


def test_is_transient():
    assert is_transient(None) and is_transient(429) and is_transient(500) and is_transient(503)
    assert not is_transient(400) and not is_transient(401) and not is_transient(404)
