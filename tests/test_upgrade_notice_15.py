import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import HttpsClient, fresh_modules
from test_upgrade_from_140 import EXPECTED, _place, _same_places

E = {"REMOTE_ADDR": "127.0.0.1"}
PW = EXPECTED["password"]

def _start(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import about
    import datakey
    import db as dbmod
    key = datakey.unwrap_password(PW)
    con = dbmod.connect(key=key)
    try:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_version', ?)", (about.version(),))
        con.commit()
        email, secret = con.execute("SELECT email, totp_secret FROM users ORDER BY id LIMIT 1").fetchone()
    finally:
        con.close()
    import app as A
    from vault import backup as B
    _same_places(monkeypatch, A, tmp_path)
    taken = []
    monkeypatch.setattr(B, "sealed_backup", lambda *a, **kw: taken.append(kw.get("kind")))
    A.app.test_client_class = HttpsClient
    return A, A.app.test_client(), email, secret, taken

def test_the_first_today_after_a_real_upgrade_says_it_was_updated(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, email, secret, taken = _start(tmp_path, monkeypatch)
    import about
    import auth
    import datakey
    assert not datakey.is_unlocked()
    c.post("/login", data=dict(email=email, password=PW), environ_base=E)
    r = c.post("/login/2fa", data=dict(code=auth.totp(secret)), environ_base=E)
    assert r.status_code in (302, 303)
    assert datakey.is_unlocked()
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert f"Updated to Lumen Aureum {about.version()}." in page
    note = page.split('id="update-note"', 1)[1].split("</section>", 1)[0]
    if A.SCHEMA_VERSION > 8:
        assert taken == ["upgrade"] and "from before the update" in note
    assert "Moved on under the new stage rules" in note
    assert "Updated to Lumen Aureum" in c.get("/", environ_base=E).get_data(as_text=True)

def test_a_restart_with_nothing_changed_says_nothing(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, email, secret, _ = _start(tmp_path, monkeypatch)
    import auth
    c.post("/login", data=dict(email=email, password=PW), environ_base=E)
    c.post("/login/2fa", data=dict(code=auth.totp(secret)), environ_base=E)
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    A2, c2, email, secret, _ = _start(tmp_path, monkeypatch)
    c2.post("/login", data=dict(email=email, password=PW), environ_base=E)
    import time
    import auth as auth2
    r = c2.post("/login/2fa", data=dict(code=auth2.totp(secret, at=time.time() + 30)), environ_base=E)
    assert r.status_code in (302, 303)
    today = c2.get("/", environ_base=E)
    assert today.status_code == 200 and 'id="today-head"' in today.get_data(as_text=True)
    assert "Updated to Lumen Aureum" not in today.get_data(as_text=True)
