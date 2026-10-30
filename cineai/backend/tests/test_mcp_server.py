"""T0: the MCP server's security controls (MCP-PLAN.md, threat model → controls).

No network, no Milvus, no API keys: the tool backends are stubbed, and JWTs are signed
with a key generated here and served from an in-memory "JWKS".
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
import types
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub the heavy tool backends (pymilvus, langchain) before importing the server.
for name in ("src.tools.milvus_retriever", "src.tools.musicbrainz_client", "src.tools.tmdb_client"):
    sys.modules.setdefault(name, types.ModuleType(name))
for name, attrs in (("dotenv", {"load_dotenv": lambda *a, **k: None}),):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

import src.mcp_server as m  # noqa: E402

# Shared with the T6 eval tier (bind-mounted into the container), so one file pins both.
SNAPSHOT = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "mcp_tools.json"
ISS, AUD = "https://auth.example.test", "https://smartmoviesearch.com/mcp/agent"


# ── Output sanitising ─────────────────────────────────────────────────────────
def test_clean_strips_hidden_instruction_carriers():
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "ignore previous")      # Unicode tag block
    dirty = f"Great film.​<script>x</script><b>Bold</b>‮evil{hidden}\x07"
    assert m.clean(dirty) == "Great film.xBoldevil"


def test_clean_keeps_text_newlines_and_truncates():
    assert m.clean("Amélie — 2001 🎬\n\n\n\nnext") == "Amélie — 2001 🎬\n\nnext"
    out = m.clean("a" * 50, limit=10)
    assert len(out) == 10 and out.endswith("…")
    assert m.clean(None) == ""


def test_kb_item_links_ebert_and_labels_guides():
    e = m._kb_item({"source": "ebert/fargo-1996", "text": "x", "score": 0.5})
    assert e["url"] == "https://www.rogerebert.com/reviews/fargo-1996" and e["kind"] == "ebert-review-excerpt"
    g = m._kb_item({"source": "docs/directors/christopher-nolan.md", "text": "y", "score": 0.1})
    assert g["kind"] == "curated-guide" and "url" not in g


# ── Tool definitions are pinned (rug-pull / tool-poisoning defence) ───────────
def _tool_defs() -> list[dict]:
    tools = asyncio.run(m.public.list_tools())
    return sorted((t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools),
                  key=lambda t: t["name"])


def test_tool_definitions_match_snapshot():
    """Changing a tool's name/description/schema/annotations must be a reviewed diff:
    regenerate with `python -m tests.test_mcp_server --update` and commit the fixture."""
    assert _tool_defs() == json.loads(SNAPSHOT.read_text())


def test_every_public_tool_is_read_only():
    for t in _tool_defs():
        a = t["annotations"]
        assert a["readOnlyHint"] is True and a["destructiveHint"] is False, t["name"]
        assert "third-party data" in t["description"], t["name"]


# ── JWT verification (OAuth 2.1 resource server) ──────────────────────────────
@pytest.fixture(scope="module")
def keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, other


class _FakeJWKS:
    def __init__(self, pub):
        self.pub = pub

    def get_signing_key_from_jwt(self, _token):
        return types.SimpleNamespace(key=self.pub)


def _verifier(priv, scope=""):
    return m.JWTVerifier(ISS, AUD, required_scope=scope, jwk_client=_FakeJWKS(priv.public_key()))


def _token(priv, **over):
    claims = {"iss": ISS, "aud": AUD, "sub": "user-1", "exp": int(time.time()) + 600, "scope": "mcp:ask openid"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, priv, algorithm="RS256")


def _verify(v, tok):
    return asyncio.run(v.verify_token(tok))


def test_valid_token_accepted(keys):
    at = _verify(_verifier(keys[0], "mcp:ask"), _token(keys[0]))
    assert at and at.subject == "user-1" and "mcp:ask" in at.scopes and at.resource == AUD


@pytest.mark.parametrize("over", [
    {"aud": "https://other-server.example/mcp"},     # minted for another resource: confused deputy
    {"iss": "https://evil.example"},
    {"exp": int(time.time()) - 3600},
    {"exp": None},                                   # no expiry at all
    {"aud": None},
])
def test_bad_claims_rejected(keys, over):
    assert _verify(_verifier(keys[0]), _token(keys[0], **over)) is None


def test_missing_scope_rejected(keys):
    assert _verify(_verifier(keys[0], "mcp:ask"), _token(keys[0], scope="openid")) is None


def test_wrong_signing_key_rejected(keys):
    assert _verify(_verifier(keys[0]), _token(keys[1])) is None


def test_alg_none_and_hs256_confusion_rejected(keys):
    v = _verifier(keys[0])
    claims = {"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 600}
    assert _verify(v, jwt.encode(claims, None, algorithm="none")) is None
    # Classic alg confusion: HMAC-sign with the PUBLIC key (which attackers can fetch)
    # as the secret. Built by hand because PyJWT won't encode it.
    pem = keys[0].public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    signing_input = b64(b'{"alg":"HS256","typ":"JWT"}') + b"." + b64(json.dumps(claims).encode())
    forged = signing_input + b"." + b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
    assert _verify(v, forged.decode()) is None


# ── Rate limits + routing ─────────────────────────────────────────────────────
@pytest.fixture
def fresh_limits(monkeypatch):
    m._hits.clear()
    m._global.update(day="", n=0)
    monkeypatch.setattr(m, "PER_IP_PER_MIN", 3)
    monkeypatch.setattr(m, "PER_IP_PER_DAY", 5)
    monkeypatch.setattr(m, "GLOBAL_PER_DAY", 8)


def test_per_minute_then_daily_then_global(fresh_limits):
    t = 1_000_000.0
    assert [m._limited("a", t) for _ in range(3)] == [None] * 3
    assert "slow down" in m._limited("a", t)
    assert m._limited("a", t + 61) is None and m._limited("a", t + 62) is None
    assert "daily limit" in m._limited("a", t + 200)                     # 5 used
    assert [m._limited("b", t + 300 + i * 61) for i in range(3)] == [None] * 3
    assert "service-wide" in m._limited("c", t + 600)                    # 8 used in total


def test_gateway_routes_only_mcp_paths():
    g = m.Gateway.__new__(m.Gateway)
    g.public_app, g.agent_app = "PUBLIC", "AGENT"
    assert g._route("/mcp") == "PUBLIC"
    assert g._route("/mcp/agent") == "AGENT"
    assert g._route("/.well-known/oauth-protected-resource/mcp/agent") == "AGENT"
    for p in ("/api/query", "/mcpx", "/mcp/../api/admin/usage", "/"):
        assert g._route(p) is None
    g.agent_app = None                                  # OAuth not configured → no agent endpoint
    assert g._route("/mcp/agent") is None


def test_unexpected_errors_are_not_echoed(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_LOG_DIR", tmp_path)

    @m._audited("probe")
    async def boom():
        raise RuntimeError("connect to milvus:19530 failed")

    with pytest.raises(m.ToolError) as e:
        asyncio.run(boom())
    assert "milvus" not in str(e.value) and "probe failed upstream" in str(e.value)
    rec = json.loads((next(tmp_path.glob("mcp-*.jsonl"))).read_text())
    assert rec["ok"] is False and rec["err"] == "RuntimeError" and "milvus" not in json.dumps(rec)


def test_ip_hash_is_salted_and_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_DATA", tmp_path)
    monkeypatch.setattr(m, "_SALT", None)
    h = m.hash_ip("203.0.113.9")
    assert h == m.hash_ip("203.0.113.9") and len(h) == 12 and "203" not in h
    assert (tmp_path / "mcp-salt").stat().st_mode & 0o777 == 0o600


if __name__ == "__main__" and "--update" in sys.argv:
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(_tool_defs(), indent=2) + "\n")
    print(f"wrote {SNAPSHOT}")
