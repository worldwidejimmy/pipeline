"""healthcheck.py: URL resolution and the never-silently-drop fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # cineai/
import healthcheck as hc  # noqa: E402


def _conf(tmp_path, monkeypatch, text):
    f = tmp_path / "healthchecks.env"
    f.write_text(text)
    monkeypatch.setattr(hc, "CONF", f)
    monkeypatch.delenv("HC_PING_KEY", raising=False)


def test_per_check_url_wins(tmp_path, monkeypatch):
    _conf(tmp_path, monkeypatch, "# c\nHC_PING_KEY=k\nsms_devops=https://hc-ping.com/uuid-1\n")
    assert hc._url("sms_devops") == "https://hc-ping.com/uuid-1"


def test_ping_key_fallback(tmp_path, monkeypatch):
    _conf(tmp_path, monkeypatch, "HC_PING_KEY='k'\n")
    assert hc._url("sms_seo") == "https://hc-ping.com/k/sms_seo"


def test_unconfigured_returns_false_without_network(tmp_path, monkeypatch):
    _conf(tmp_path, monkeypatch, "")
    monkeypatch.setattr(hc.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert hc.ping("sms_devops", ok=True) is False      # → caller emails instead


def test_fail_suffix(tmp_path, monkeypatch):
    _conf(tmp_path, monkeypatch, "sms_devops=https://hc-ping.com/uuid-1/\n")
    seen = []

    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(hc.urllib.request, "urlopen", lambda req, timeout: seen.append(req.full_url) or R())
    assert hc.ping("sms_devops", ok=False, body="1 issue(s): disk 91%") is True
    assert seen == ["https://hc-ping.com/uuid-1/fail"]
