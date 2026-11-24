import contextlib
import io
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client
from test_167_155 import E, replace

ALERT = "Your master key was replaced. Rotate the data key"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c, tmp_path

def rotate_due(A):
    con = A.connect()
    try:
        r = con.execute("SELECT value FROM settings WHERE key='rotate_due'").fetchone()
    finally:
        con.close()
    return r[0] if r else None

def page(c, url):
    import html
    return html.unescape(c.get(url, environ_base=E).get_data(as_text=True))

def test_no_alert_before_any_replacement(env):
    A, c, _ = env
    assert rotate_due(A) is None
    assert ALERT not in page(c, "/alerts")
    assert 'id="rotate-due"' not in page(c, "/security")

@pytest.mark.parametrize("with_old", [False, True])
def test_a_replacement_raises_the_rotation_alert_until_a_rotation_clears_it(env, monkeypatch, with_old):
    A, c, _ = env
    import datakey
    old_master = c.master_secret
    old_dk = datakey.dk()
    new, r = replace(c, old_master if with_old else "")
    assert r.location.endswith("/security#master-key" if with_old else "/security#master-pending")
    assert rotate_due(A)

    assert datakey.dk() == old_dk
    sec = page(c, "/security")
    assert 'id="rotate-due"' in sec and "python -m vault.cli rotate" in sec and "Stop Lumen Aureum" in sec
    assert "rotate the data key" in sec
    al = page(c, "/alerts")
    assert ALERT in al and "/security#rotate-due" in al
    with A.app.test_request_context():
        import flask
        flask.g.pop("alerts", None)
        assert any(a_["text"].startswith(ALERT) and a_["level"] == "fix" for a_ in A.system_alerts())
    import rotate
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", contextlib.nullcontext)
    rotate.rotate(datakey.dk(), TEST_PASSWORD)
    fresh_modules()
    import datakey as K2
    import db
    new_dk = K2.unwrap_password(TEST_PASSWORD)
    assert new_dk != old_dk and K2.unwrap_master(new) == new_dk
    con = db.connect(key=new_dk)
    try:
        assert con.execute("SELECT value FROM settings WHERE key='rotate_due'").fetchone() is None
    finally:
        con.close()

def test_an_interrupted_rotation_still_clears_it_when_finished(env):
    A, c, _ = env
    import datakey
    import secrets
    replace(c, "")
    assert rotate_due(A)
    new_dk = secrets.token_bytes(32)
    import rotate
    rotate.stage(datakey.dk(), new_dk, TEST_PASSWORD)
    rotate.finish()
    fresh_modules()
    import db
    con = db.connect(key=new_dk)
    try:
        assert con.execute("SELECT value FROM settings WHERE key='rotate_due'").fetchone() is None
    finally:
        con.close()

def test_the_command_line_new_master_says_to_rotate_and_raises_the_alert(env, monkeypatch):
    A, c, _ = env
    from vault import cli
    old_master = c.master_secret
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": old_master)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["new-master"]) == 0
    text = out.getvalue()
    assert "rotate the data key" in text and "python -m vault.cli rotate" in text and "Stop Lumen Aureum" in text
    assert rotate_due(A)

FALSE = ["only in reverse", "to you as much as to anyone", "stays closed for good",
         "outranks the password and the two-factor code alike", "it may have been seen)? Type it here"]

def test_the_docs_and_the_page_no_longer_overclaim(env):
    A, c, _ = env
    sec_md = open(os.path.join(ROOT, "docs", "security.md")).read()
    tpl = open(os.path.join(ROOT, "templates", "auth_security.html")).read()
    shown = page(c, "/security")
    for words in FALSE:
        assert words not in sec_md, words
        assert words not in tpl, words
        assert words not in shown, words
    flat = re.sub(r"\s+", " ", sec_md)
    assert "After replacing the master key, rotate the data key." in flat
    assert "a replaced key stops opening anything new only once the data key is rotated" in flat
    assert "or a master key you have since replaced" in flat
    ops = re.sub(r"\s+", " ", open(os.path.join(ROOT, "docs", "operations.md")).read())
    assert "rotate the data key again" in ops
    assert "rotate the data key afterwards too" in shown
