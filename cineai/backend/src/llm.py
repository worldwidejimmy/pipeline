"""
Central LLM factory — Anthropic Claude via langchain_anthropic.

The whole app talks to Claude through this one helper, so the model is a single
server-wide setting:

  DEFAULT_MODEL_TIER = haiku | sonnet | opus   (cost tier; default haiku)
  MODEL_PIN          = <model id>              (optional — overrides auto-resolution)

By default the tier resolves to the NEWEST model in that family, discovered from the
Models API (GET /v1/models) at startup and re-checked every 24h. So when Anthropic
ships a new Haiku the app moves to it without a code change — and the nightly evals
(which record the model id on every result) are what notice if that move hurt
quality. If the move regresses, set MODEL_PIN and recreate the backend.

Request params are derived from the model's advertised CAPABILITIES, not from its id:
newer generations reject sampling params and think by default, both of which break
naive calls (a 16-token router reply that the model spends on thinking).
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from langchain_anthropic import ChatAnthropic

from src.config import get_config

# Family prefix per cost tier — resolution picks the newest model whose id starts with it.
FAMILIES = {"haiku": "claude-haiku-", "sonnet": "claude-sonnet-", "opus": "claude-opus-"}

# Last-known-good per tier: used when the Models API is unreachable at resolve time.
FALLBACK = {
    "haiku":  "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus":   "claude-opus-5-5",
}
# Kept for older imports/tests — same thing.
MODELS = FALLBACK

_TTL_S = 24 * 3600
_lock = threading.Lock()
_state: dict[str, Any] = {}     # model, source, tier, resolved_at, newest_in_tier, caps


def _tier() -> str:
    t = get_config().model_tier.lower()
    return t if t in FAMILIES else "haiku"


def _list_models() -> list[dict]:
    import anthropic
    client = anthropic.Anthropic(api_key=get_config().anthropic_api_key, timeout=5.0, max_retries=1)
    return [m.to_dict() for m in client.models.list(limit=100)]


def pick_newest(models: list[dict], tier: str) -> dict | None:
    """Newest model in a tier's family, by created_at. Pure — unit-tested."""
    fam = [m for m in models if str(m.get("id", "")).startswith(FAMILIES[tier])]
    return max(fam, key=lambda m: str(m.get("created_at", "")), default=None)


def resolve(force: bool = False) -> dict:
    """Resolve (and cache) the model for the configured tier. Never raises."""
    with _lock:
        if not force and _state and time.time() - _state["resolved_at"] < _TTL_S:
            return dict(_state)
        cfg, tier = get_config(), _tier()
        pin = (cfg.model_pin or "").strip()
        st: dict[str, Any] = {"tier": tier, "resolved_at": time.time(), "caps": None,
                              "newest_in_tier": None, "error": None}
        try:
            models = _list_models()
            newest = pick_newest(models, tier)
            st["newest_in_tier"] = newest["id"] if newest else None
            chosen = next((m for m in models if m["id"] == pin), None) if pin else newest
            st["model"] = pin or (newest["id"] if newest else FALLBACK[tier])
            st["source"] = "pinned" if pin else ("auto" if newest else "fallback")
            st["caps"] = (chosen or {}).get("capabilities")
        except Exception as exc:                      # offline/bad key → last-known-good
            st["model"] = pin or FALLBACK[tier]
            st["source"] = "pinned" if pin else "fallback"
            st["error"] = str(exc)[:160]
        if st["model"] != _state.get("model") and _state:
            print(f"[llm] model changed: {_state.get('model')} -> {st['model']} ({st['source']})")
        _state.clear()
        _state.update(st)
        return dict(_state)


def model_id() -> str:
    return resolve()["model"]


def request_params(caps: dict | None, max_tokens: int) -> dict:
    """Thinking/sampling params from advertised capabilities. Pure — unit-tested.

    Every call in this app is a short single-shot generation, so thinking is turned
    OFF wherever the model allows it ('disabled', else 'between_tools', which is
    'off' for a call with no tools). Models that cannot stop thinking get effort=low
    and extra max_tokens headroom, since thinking tokens count against max_tokens.

    Sampling (temperature) is only sent to the legacy surface — models that still
    accept budgeted 'enabled' thinking. The newer generation 400s on non-default
    sampling and the capabilities object has no flag for it, so this is the proxy.
    Unknown caps (Models API down) → send nothing optional; that's valid everywhere.
    """
    types = ((caps or {}).get("thinking") or {}).get("types") or {}
    ok = lambda t: bool((types.get(t) or {}).get("supported"))   # noqa: E731
    out: dict[str, Any] = {"max_tokens": max_tokens, "sampling": False}
    if not types:
        return out
    if ok("enabled"):                        # legacy surface: no thinking unless asked
        out["sampling"] = True
    elif ok("disabled"):
        out["thinking"] = {"type": "disabled"}
    elif ok("between_tools"):
        out["thinking"] = {"type": "between_tools"}
    else:                                     # always-thinking model
        out["effort"] = "low"
        out["max_tokens"] = max_tokens + 2048
    return out


def get_chat(*, temperature: float = 0.1, max_tokens: int = 1024,
             streaming: bool = False) -> ChatAnthropic:
    """Build a ChatAnthropic for the configured tier's resolved model."""
    cfg = get_config()
    st = resolve()
    p = request_params(st["caps"], max_tokens)
    kwargs: dict[str, Any] = dict(
        model=st["model"],
        api_key=cfg.anthropic_api_key,
        max_tokens=p["max_tokens"],
        streaming=streaming,
        stream_usage=True,   # populate usage_metadata on streamed responses
    )
    if p["sampling"]:
        kwargs["temperature"] = temperature
    if "thinking" in p:
        kwargs["thinking"] = p["thinking"]
    if "effort" in p:
        kwargs["effort"] = p["effort"]
    return ChatAnthropic(**kwargs)


def system_blocks(static: str, dynamic: str = "") -> Any:
    """A SystemMessage as [fixed instructions, cache-marked] + [this request's data].

    Prompt caching, measured 2026-10-30: Haiku 5.5 caches a prefix from ~512-720 tokens
    up (379 didn't, 721 did); 5-minute TTL; a write costs 1.25x input, a read 0.1x. So
    it pays when a prefix is reused twice within 5 minutes. That's true for the nightly
    evals (most of our spend; 92% of runs have another within 5 min) and for bursts of
    visitors. Shorter prefixes are silently not cached (no error, no write cost).
    The instructions must come FIRST and be byte-identical across calls, so nothing
    per-request (data, history, dates) may go in `static`.
    """
    from langchain_core.messages import SystemMessage
    blocks: list[dict] = [{"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}]
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return SystemMessage(content=blocks)


def static_part(template: str, marker: str, *suffix: str) -> str:
    """The fixed instructions of a prompt template: everything before `marker` (where the
    per-request data starts), with {{escapes}} resolved, plus any fixed `suffix` text."""
    return template[:template.index(marker)].format() + "".join(suffix)


async def extract(schema: Any, system: str, user: str, *, dynamic: str = "", max_tokens: int = 256) -> dict | None:
    """Structured extraction with Claude's native structured outputs (JSON schema).

    The reply is constrained to `schema` (a Pydantic model), so the model can't answer
    in prose instead. Prose instead of JSON is the failure that made "Who directed
    Inception?" come back "couldn't find" ~40% of the time (found via T7 traces,
    2026-10-30). Returns a plain dict, or None if the call or validation fails (callers
    keep their own fallback).
    """
    from langchain_core.messages import HumanMessage
    try:
        llm = get_chat(temperature=0.0, max_tokens=max_tokens).with_structured_output(schema, method="json_schema")
        out = await llm.ainvoke([system_blocks(system, dynamic), HumanMessage(content=user)])
        return out.model_dump() if hasattr(out, "model_dump") else dict(out)
    except Exception as exc:
        print(f"[llm.extract] {getattr(schema, '__name__', 'schema')} failed: {type(exc).__name__}: {str(exc)[:160]}")
        return None


def text_of(content: Any) -> str:
    """Text of a message/chunk .content — a str, or a list of blocks when the model
    returned thinking blocks alongside text. Thinking is never surfaced."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                       else (b if isinstance(b, str) else "") for b in content)
    return ""


def parse_llm_json(text: str) -> dict:
    """Parse a JSON object out of an LLM reply.

    Claude often wraps "JSON only" answers in ```json fences or adds a short
    preamble; a bare json.loads then raises and callers silently fall back to
    degraded behaviour. Strip fences, then fall back to the first {...} block.
    Raises ValueError if no JSON object can be found.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            return json.loads(brace.group(0))
        raise ValueError(f"no JSON object in LLM reply: {text[:120]!r}")
