"""Every page the app serves, opened on a studio full of demo clients (every preset type, every stage): none fails.
GET routes with an id are opened for each matching row; documents are opened as pages and as PDFs."""
import os
import re
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}
SKIP = {"/logout", "/healthz", "/settings/restart"}          # side effects, not pages
IDS = {"sid": "sessions", "hid": "households", "eid": "events", "did": "documents", "vid": "quote_versions"}


def test_every_page_opens(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    con = A.connect()
    ids = {k: [r[0] for r in con.execute(f"SELECT id FROM {t} ORDER BY id")] for k, t in IDS.items()}
    con.close()
    assert ids["sid"] and ids["hid"]
    failures, opened = [], 0
    for rule in A.app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule.startswith("/static") or rule.rule in SKIP:
            continue
        args = re.findall(r"<(?:int:)?(\w+)>", rule.rule)
        if any(a not in IDS for a in args):
            continue                                         # path/key routes have their own tests
        combos = [{}] if not args else [{args[0]: i} for i in ids[args[0]][:40]] if len(args) == 1 else []
        for kw in combos:
            url = rule.rule
            for k, v in kw.items():
                url = re.sub(rf"<(?:int:)?{k}>", str(v), url)
            r = c.get(url, environ_base=E)
            opened += 1
            if r.status_code >= 500:
                failures.append(f"{url} -> {r.status_code}")
    assert opened > 200 and not failures, failures[:20]
