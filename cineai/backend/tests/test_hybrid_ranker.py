"""T0: the hybrid-search fusion step (milvus_retriever._ranker). No Milvus, no network."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load_retriever():
    """Import milvus_retriever.py under a private name with pymilvus/langchain stubbed
    (other tests stub the whole module under its real name)."""
    pm = types.ModuleType("pymilvus")
    pm.AnnSearchRequest = pm.MilvusClient = object
    pm.RRFRanker = lambda k: ("rrf", k)
    pm.WeightedRanker = lambda *w: ("weighted", w)
    lc = types.ModuleType("langchain_openai")
    lc.OpenAIEmbeddings = object
    saved = {n: sys.modules.get(n) for n in ("pymilvus", "langchain_openai")}
    sys.modules.update(pymilvus=pm, langchain_openai=lc)
    try:
        spec = importlib.util.spec_from_file_location(
            "_retriever_under_test", BACKEND / "src" / "tools" / "milvus_retriever.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


R = _load_retriever()


def _cfg(**kw):
    base = {"hybrid_ranker": "weighted", "hybrid_weights": "0.7,0.3", "hybrid_rrf_k": 60}
    return SimpleNamespace(**{**base, **kw})


def test_default_is_weighted_dense_first():
    assert R._ranker(_cfg()) == ("weighted", (0.7, 0.3))     # order = reqs [dense, sparse]


def test_rrf_still_selectable():
    assert R._ranker(_cfg(hybrid_ranker="rrf", hybrid_rrf_k=40)) == ("rrf", 40)


def test_config_default_matches():
    from src.config import Config
    c = Config()
    assert (c.hybrid_ranker, c.hybrid_weights) == ("weighted", "0.7,0.3")
