"""T0: model auto-resolution ("newest model of the configured cost tier") and the
capability → request-params mapping, replayed against a REAL captured /v1/models
catalog (evals/fixtures/model_catalog.json). No network, no LLM.

Why this matters: the newer generation rejects `temperature` (400) and thinks by
default — a 16-token router reply spent entirely on thinking returns nothing. A
silent auto-upgrade must never send params the new model refuses.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for name, attrs in (("dotenv", {"load_dotenv": lambda *a, **k: None}),
                    ("langchain_anthropic", {"ChatAnthropic": object})):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

import src.llm as llm  # noqa: E402

CATALOG = json.loads((Path(__file__).resolve().parents[2] / "evals/fixtures/model_catalog.json")
                     .read_text())["models"]
CAPS = {m["id"]: m.get("capabilities") for m in CATALOG}


@pytest.mark.parametrize("tier,expect", [("haiku", "claude-haiku-5-5"),
                                         ("sonnet", "claude-sonnet-5-5"),
                                         ("opus", "claude-opus-5-5")])
def test_newest_in_tier(tier, expect):
    assert llm.pick_newest(CATALOG, tier)["id"] == expect


def test_tier_never_crosses_family():
    # fable is newer than opus-5 but is not an opus — must not be picked for opus.
    assert not llm.pick_newest(CATALOG, "opus")["id"].startswith("claude-fable")
    assert llm.pick_newest([], "haiku") is None


def test_haiku_5_5_thinking_off_no_sampling():
    p = llm.request_params(CAPS["claude-haiku-5-5"], 16)
    assert p == {"max_tokens": 16, "sampling": False, "thinking": {"type": "disabled"}}


def test_sonnet_5_5_uses_between_tools():
    p = llm.request_params(CAPS["claude-sonnet-5-5"], 900)
    assert p["thinking"] == {"type": "between_tools"} and not p["sampling"]


def test_always_thinking_model_gets_headroom():
    p = llm.request_params(CAPS["claude-opus-5-5"], 16)
    assert p["effort"] == "low" and p["max_tokens"] > 1000 and "thinking" not in p


@pytest.mark.parametrize("mid", ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"])
def test_legacy_models_keep_temperature(mid):
    p = llm.request_params(CAPS[mid], 900)
    assert p["sampling"] and "thinking" not in p


def test_opus_4_8_no_sampling():
    p = llm.request_params(CAPS["claude-opus-4-8"], 900)
    assert not p["sampling"] and p["thinking"] == {"type": "disabled"}


def test_unknown_caps_sends_nothing_optional():
    assert llm.request_params(None, 900) == {"max_tokens": 900, "sampling": False}


def _resolve_with(monkeypatch, *, tier="haiku", pin="", models=None, boom=False, approved_file=None):
    monkeypatch.setattr(llm, "APPROVED_FILE", approved_file or Path("/nonexistent/approved_model.json"))
    monkeypatch.delenv("MODEL_CANDIDATE", raising=False)
    cfg = types.SimpleNamespace(model_tier=tier, model_pin=pin, anthropic_api_key="x")
    monkeypatch.setattr(llm, "get_config", lambda: cfg)
    def fake_list():
        if boom:
            raise RuntimeError("network down")
        return models if models is not None else CATALOG
    monkeypatch.setattr(llm, "_list_models", fake_list)
    llm._state.clear()
    return llm.resolve(force=True)


def test_resolve_auto(monkeypatch):
    st = _resolve_with(monkeypatch)
    assert st["model"] == "claude-haiku-5-5" and st["source"] == "auto"
    assert st["caps"]["thinking"]["types"]["disabled"]["supported"]


def test_resolve_pin_wins_but_reports_newest(monkeypatch):
    st = _resolve_with(monkeypatch, pin="claude-haiku-4-5-20251001")
    assert st["model"] == "claude-haiku-4-5-20251001" and st["source"] == "pinned"
    assert st["newest_in_tier"] == "claude-haiku-5-5"      # admin page shows "newer available"


def test_resolve_offline_falls_back(monkeypatch):
    st = _resolve_with(monkeypatch, tier="sonnet", boom=True)
    assert st["model"] == llm.FALLBACK["sonnet"] and st["source"] == "fallback"
    assert st["caps"] is None and "network down" in st["error"]


def test_bad_tier_means_haiku(monkeypatch):
    assert _resolve_with(monkeypatch, tier="gpt")["model"] == "claude-haiku-5-5"


@pytest.mark.parametrize("content,expect", [
    ("plain", "plain"),
    ([{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "tmdb"}], "tmdb"),
    ([{"type": "text", "text": "a"}, "b"], "ab"),
    (None, ""),
])
def test_text_of(content, expect):
    assert llm.text_of(content) == expect


# ── The promotion gate: the approved model stays live until a candidate passes ──
OLD_HAIKU = next(m["id"] for m in CATALOG if m["id"].startswith("claude-haiku-4"))


def test_choose_order():
    assert llm.choose(CATALOG, "haiku", pin="p") == ("p", "pinned")
    assert llm.choose(CATALOG, "haiku", candidate="c", approved=OLD_HAIKU) == ("c", "candidate")
    assert llm.choose(CATALOG, "haiku", approved=OLD_HAIKU) == (OLD_HAIKU, "approved")
    assert llm.choose(CATALOG, "haiku") == ("claude-haiku-5-5", "auto")            # bootstrap
    assert llm.choose(CATALOG, "haiku", approved="claude-haiku-retired") == \
        ("claude-haiku-5-5", "auto (approved model retired)")
    assert llm.choose([], "haiku", approved=OLD_HAIKU) == (OLD_HAIKU, "fallback")


def test_newer_model_is_not_live_until_approved(monkeypatch, tmp_path):
    f = tmp_path / "approved_model.json"
    f.write_text(json.dumps({"tier": "haiku", "model": OLD_HAIKU}))
    st = _resolve_with(monkeypatch, approved_file=f)
    assert st["model"] == OLD_HAIKU and st["source"] == "approved"
    assert st["newest_in_tier"] == "claude-haiku-5-5"          # the candidate, shown on /admin


def test_approval_of_another_tier_is_ignored(monkeypatch, tmp_path):
    f = tmp_path / "approved_model.json"
    f.write_text(json.dumps({"tier": "sonnet", "model": "claude-sonnet-5-5"}))
    assert _resolve_with(monkeypatch, approved_file=f)["model"] == "claude-haiku-5-5"


def test_new_approval_is_picked_up_without_restart(monkeypatch, tmp_path):
    import os
    f = tmp_path / "approved_model.json"
    f.write_text(json.dumps({"tier": "haiku", "model": OLD_HAIKU}))
    assert _resolve_with(monkeypatch, approved_file=f)["model"] == OLD_HAIKU
    f.write_text(json.dumps({"tier": "haiku", "model": "claude-haiku-5-5"}))
    os.utime(f, (1, 2_000_000_000))                       # a new mtime invalidates the 24h cache
    assert llm.resolve()["model"] == "claude-haiku-5-5"


def test_candidate_env_is_for_the_eval_process(monkeypatch, tmp_path):
    f = tmp_path / "approved_model.json"
    f.write_text(json.dumps({"tier": "haiku", "model": OLD_HAIKU}))
    _resolve_with(monkeypatch, approved_file=f)
    monkeypatch.setenv("MODEL_CANDIDATE", "claude-haiku-5-5")
    st = llm.resolve(force=True)
    assert st["model"] == "claude-haiku-5-5" and st["source"] == "candidate"
    assert st["caps"]                                      # params come from the candidate's caps
