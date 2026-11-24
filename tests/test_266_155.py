import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules

OPS = (ROOT / "docs" / "operations.md").read_text()
WATCH = re.search(r"^## Watching it from outside\n(.*?)(?=^## )", OPS, re.S | re.M).group(1)

def test_section_covers_monitor_keyword_and_ntfy():
    for words in ("Uptime Kuma", "HTTP(s) - Keyword", '`"locked":false`', "ntfy", "different machine"):
        assert words in WATCH, words
    assert "security.md#notices-while-locked" in WATCH and "where-it-runs.md#reaching-the-app" in WATCH

def test_the_keyword_matches_healthz(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    for v in ("CRM_TRUSTED_PROXIES", "CRM_ALLOW_ONLY"):
        monkeypatch.delenv(v, raising=False)
    fresh_modules()
    try:
        import app as A
        body = A.app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "192.168.1.20"}).get_data(as_text=True)
        assert body.strip() == '{"locked":true,"ok":true}'
        assert body.strip() in WATCH
        import datakey
        monkeypatch.setattr(datakey, "is_unlocked", lambda: True)
        body = A.app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "192.168.1.20"}).get_data(as_text=True)
        assert '"locked":false' in body and body.strip() in WATCH
    finally:
        fresh_modules()

def test_plain_style():
    assert "—" not in WATCH
