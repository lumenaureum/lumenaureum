import io
import json
import os
import ssl
import sys
import threading
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client
from test_decided_edge_cases import env, one, run

E = {"REMOTE_ADDR": "127.0.0.1"}

def test_mark_opened_on_today_asks_before_it_closes_the_gallery(env):
    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_built)
              VALUES (1, 1, 'portrait', 'delivered', '2025-06-01', '2025-06-10')""")
    form = {"act": "mark", "field": "first_visit", "on": "2025-06-12", "back": "/"}
    r = c.post("/session/1/quick", data=form, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "This closes the gallery now. Save anyway?" in page
    assert 'name="confirm_close" value="1"' in page and 'name="on" value="2025-06-12"' in page
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] is None
    c.post("/session/1/quick", data=dict(form, confirm_close="1"), environ_base=E)
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] == "2025-06-12"
    run(A, "UPDATE sessions SET first_visit=NULL WHERE id=1")
    r = c.post("/session/1/quick", data={"act": "mark", "field": "first_visit", "back": "/"}, environ_base=E)
    assert r.status_code == 302 and one(A, "SELECT first_visit FROM sessions WHERE id=1")[0]

@pytest.mark.parametrize("url", ["/client/1/export.json", "/money/accounts.zip", "/settings/export.json"])
def test_downloads_that_write_refuse_another_site(env, url):
    A, c = env
    n0 = one(A, "SELECT COUNT(*) FROM audit")[0]
    r = c.get(url, headers={"Sec-Fetch-Site": "cross-site"}, environ_base=E)
    assert r.status_code == 403 and one(A, "SELECT COUNT(*) FROM audit")[0] == n0
    assert c.get(url, headers={"Sec-Fetch-Site": "same-origin"}, environ_base=E).status_code == 200
    assert c.get(url, headers={"Sec-Fetch-Site": "none"}, environ_base=E).status_code == 200

def test_writing_downloads_are_all_listed(env):
    A, c = env
    assert {"client_export", "photo_download", "money_accounts", "settings_export", "letter_qr"} <= A.WRITING_GETS
    assert "letter_qr" in A.app.view_functions

def test_a_settings_file_during_setup_waits_before_it_moves_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A, setup_done=False)
    from vault import web as VW
    before = VW.config().get("primary")
    target = tmp_path / "someone-elses-share"
    body = json.dumps({"lumen_aureum_settings": 1, "settings": {"business_name": "North Light"},
                       "storage": {"primary": {"type": "local", "path": str(target)}}}).encode()
    c.post("/settings/import", data={"back": "/setup/business", "file": (io.BytesIO(body), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    assert VW.config().get("primary") == before
    page = c.get("/setup/business", environ_base=E).get_data(as_text=True)
    assert "This file sets where your backups go" in page and str(target) in page
    c.post("/settings/import/gallery", data={"use": "1", "back": "/setup/business"}, environ_base=E)
    assert VW.config()["primary"]["path"] == str(target)

def test_a_settings_file_backup_place_can_be_declined(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A, setup_done=False)
    from vault import web as VW
    before = VW.config().get("primary")
    body = json.dumps({"lumen_aureum_settings": 1, "storage": {"primary": {"type": "local", "path": str(tmp_path / "x")}}}).encode()
    c.post("/settings/import", data={"back": "/setup/business", "file": (io.BytesIO(body), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    c.post("/settings/import/gallery", data={"use": "0", "back": "/setup/business"}, environ_base=E)
    assert VW.config().get("primary") == before
    assert "where your backups go" not in c.get("/setup/business", environ_base=E).get_data(as_text=True)

def test_the_master_key_is_typed_hidden_on_the_reset_page():
    html = (Path(ROOT) / "templates" / "auth_master_unlock.html").read_text()
    tag = next(l for l in html.splitlines() if 'name="secret"' in l)
    assert 'type="password"' in tag and 'autocomplete="off"' in tag

def test_the_listener_takes_tls_1_2_at_the_least():
    import gunicorn_tls
    import serve
    ctx = gunicorn_tls.ssl_context(None, lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    args = serve.tls_args("c.pem", "k.pem")
    assert args[args.index("--config") + 1] == "python:gunicorn_tls"

@pytest.fixture
def ring(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    signed_in_client(A)
    import datakey
    return datakey, tmp_path

def test_keyring_writes_never_share_a_temporary_file(ring):
    datakey, tmp = ring
    key = datakey.dk()
    errs = []

    def one_write(i):
        try:
            for _ in range(10):
                datakey.replace_master(datakey.master_pub(), key=key)
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=one_write, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and datakey.unwrap_password(TEST_PASSWORD) == key
    assert not [p for p in os.listdir(datakey.KEYRING.parent) if p.endswith(".tmp")]
