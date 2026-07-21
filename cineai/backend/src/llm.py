"""
Central LLM factory — Anthropic Claude via langchain_anthropic.

The whole app talks to Claude through this one helper, so the model tier is a
single server-wide setting (DEFAULT_MODEL_TIER = haiku | sonnet | opus). Change
it in backend/.env and recreate the backend container to switch every agent.

Pricing (per 1M tokens, in/out): haiku $1/$5 · sonnet $3/$15 · opus $5/$25.
"""
from __future__ import annotations

import json
import re

from langchain_anthropic import ChatAnthropic

from src.config import get_config

# Tier → current model ID (verified against the Claude model catalog).
MODELS = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
}


def model_id() -> str:
    cfg = get_config()
    return MODELS.get(cfg.model_tier.lower(), MODELS["haiku"])


def get_chat(*, temperature: float = 0.1, max_tokens: int = 1024,
             streaming: bool = False) -> ChatAnthropic:
    """Build a ChatAnthropic for the configured tier.

    Opus 4.8 (and 4.7) reject sampling params with a 400, so temperature is
    omitted for the opus tier; haiku/sonnet keep it.
    """
    cfg = get_config()
    mid = model_id()
    kwargs = dict(
        model=mid,
        api_key=cfg.anthropic_api_key,
        max_tokens=max_tokens,
        streaming=streaming,
        stream_usage=True,   # populate usage_metadata on streamed responses
    )
    if not mid.startswith("claude-opus"):
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


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
