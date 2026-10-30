"""LangSmith tracing, made deliberate.

Before this module, tracing was on implicitly (LANGCHAIN_TRACING_V2=true): every LLM
call became its own trace, eval runs were mixed in with real visitors, and whatever
a visitor typed went to a third party verbatim. Now:

- **Explicit tracers only.** Env auto-tracing is switched off at import. Each request
  that should be traced gets a `LangChainTracer` in its RunnableConfig, so one
  question = one trace tree (graph → nodes → model calls), named after its source
  (web / compare / mcp / eval).
- **Visitors and evals are separated.** Eval runs go to `<project>-evals`, tagged with
  their tier, so production dashboards show only real traffic.
- **Redaction before upload.** `redact()` runs over every string in inputs, outputs and
  metadata via the client's anonymizer: emails, phone/card-like numbers, IP addresses
  and API-key-looking strings. Client IPs are never put in traces at all.
- **Sampling** for production (`TRACE_SAMPLE_RATE`, default 1.0 at this volume). Evals
  are always traced.
- **Run ids** are returned to the caller, so user feedback (👍/👎) can attach to the
  exact trace it is about.

Must be imported before anything that makes an LLM call (main.py imports it first).
"""
from __future__ import annotations

import os
import random
import re
import uuid
from typing import Any

try:                                    # load .env before the reads below (as src.config does)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                     # stripped-down test envs
    pass

_KEY = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or ""
_WANTED = (os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2") or "").lower() == "true"
ENABLED = bool(_KEY) and _WANTED
PROJECT = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "smartmoviesearch"
EVAL_PROJECT = f"{PROJECT}-evals"
SAMPLE_RATE = float(os.getenv("TRACE_SAMPLE_RATE", "1.0"))

# Implicit tracing off: only the explicit tracers below send anything. (LangSmith reads
# these lazily, at the first traced call, so this must run before any LLM call.)
for _k in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
    os.environ[_k] = "false"
if _KEY:
    os.environ.setdefault("LANGSMITH_API_KEY", _KEY)

# ── Redaction ─────────────────────────────────────────────────────────────────
# Order matters: secrets and emails before the generic number rule.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:sk|lsv2|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"), "<secret>"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+\b"), "<email>"),
    (re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){5,7}[0-9A-Fa-f]{1,4}\b"), "<ip>"),
    # 10+ digits, optionally split by single spaces/dots/dashes: phone and card numbers.
    # Years, runtimes and TMDB ids are all far shorter.
    (re.compile(r"(?<![\w-])\+?\d(?:[ .\-]?\d){9,18}(?![\w-])"), "<number>"),
]


def redact(text: str) -> str:
    for rx, repl in _RULES:
        text = rx.sub(repl, text)
    return text


# ── Client + per-request config ───────────────────────────────────────────────
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from langsmith import Client
        from langsmith.anonymizer import create_anonymizer
        _CLIENT = Client(api_key=_KEY, anonymizer=create_anonymizer(lambda s, _path: redact(s)))
    return _CLIENT


def run_config(source: str, *, eval_tier: str | None = None, tags: tuple[str, ...] = (),
               metadata: dict[str, Any] | None = None, sample: float | None = None) -> tuple[dict, str | None]:
    """RunnableConfig additions that trace ONE request, plus its run id (None = not traced).

    `source`: web | compare | mcp | eval. `eval_tier` routes the trace to the eval project.
    Merge the returned dict into the config you pass to `ainvoke`/`astream_events`.
    """
    rate = SAMPLE_RATE if sample is None else sample
    if not ENABLED or (eval_tier is None and random.random() >= rate):
        return {}, None
    from langchain_core.tracers import LangChainTracer

    from src.llm import model_id
    run_id = uuid.uuid4()
    project = EVAL_PROJECT if eval_tier else PROJECT
    all_tags = [source, *([f"eval:{eval_tier}"] if eval_tier else []), *tags]
    md = {"source": source, "model": model_id(), **({"eval_tier": eval_tier} if eval_tier else {}), **(metadata or {})}
    return {
        "callbacks": [LangChainTracer(project_name=project, client=_client(), tags=all_tags)],
        "run_id": run_id,
        "run_name": f"{source}:{eval_tier}" if eval_tier else source,
        "tags": all_tags,
        "metadata": md,
    }, str(run_id)


def status() -> dict:
    """For /admin: is tracing on, where to, how much, and is redaction active."""
    return {"enabled": ENABLED, "project": PROJECT, "eval_project": EVAL_PROJECT,
            "sample_rate": SAMPLE_RATE, "redaction": [repl for _, repl in _RULES]}
