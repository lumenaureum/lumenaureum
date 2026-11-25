import html
import json
import os
import sys
import time
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client
from test_167_155 import E, TOTP, code, shown_key, start

OWNER = "owner@example.com"
CANCEL = "Cancel the new master key"

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

def setting(A, k):
    con = A.connect()
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    finally:
        con.close()
    return r[0] if r else None

def put(A, k, v):
    con = A.connect()
    try:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
        con.commit()
    finally:
        con.close()

def page(c, url):
    return html.unescape(c.get(url, environ_base=E).get_data(as_text=True))

def ask(c):

    fresh_code()
    assert start(c).location.endswith("/security/new-master-key")
    new, _ = shown_key(c)
    return new, c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)

def fresh_code():

    import app as A
    con = A.connect()
    con.execute("UPDATE users SET totp_last=0")
    con.commit(); con.close()

def move_due(A, delta):

    v = json.loads(setting(A, "master_pending"))
    v["due"] = (A.app.config["now"]() + delta).isoformat(timespec="seconds")
    put(A, "master_pending", json.dumps(v))

def tick(A):
    return A.app.config["master_tick"](force=True)

def audit(A):
    con = A.connect()
    try:
        return " | ".join(r[0] for r in con.execute("SELECT action FROM audit"))
    finally:
        con.close()

def other_device(A):
    import auth
    c2 = A.app.test_client()
    with c2.session_transaction() as s:
        s["uid"] = 1
        s["sid"] = auth.start_session(1)
    return c2

def sign_in(A, when):
    import auth
    con = A.connect()
    con.execute("UPDATE users SET totp_last=0")
    con.commit(); con.close()
    c = A.app.test_client()
    r = c.post("/login", data=dict(email=OWNER, password=TEST_PASSWORD), environ_base=E)
    assert "/login/2fa" in r.headers["Location"]
    r = c.post("/login/2fa", data=dict(code=auth.totp(TOTP, when)), environ_base=E)
    assert r.status_code == 302 and "/login" not in r.headers["Location"]
    return c

def test_the_lost_path_waits_and_keeps_only_the_public_key(env):
    A, c, _ = env
    import datakey
    from vault import keys as K
    old, dk = c.master_secret, datakey.dk()
    con = A.connect(); old_pub = K.master_pub(con); con.close()
    c2 = other_device(A)
    t0 = A.app.config["now"]()
    new, r = ask(c)
    assert r.location.endswith("/security#master-pending")
    p = json.loads(setting(A, "master_pending"))
    assert set(p) == {"pub", "asked", "due"} and p["pub"].startswith("age1")
    import auth
    due = auth.studio_time(p["due"])
    assert timedelta(hours=47, minutes=59) <= due - t0 <= timedelta(hours=48, minutes=1)

    con = A.connect()
    try:
        everything = json.dumps([list(r_) for r_ in con.execute("SELECT key, value FROM settings")])
        everything += json.dumps([list(r_) for r_ in con.execute("SELECT action, note FROM audit")])
    finally:
        con.close()
    assert "AGE-SECRET-KEY" not in everything and new not in everything and new[-6:] not in everything
    import auth
    assert not auth.NEWKEY

    assert datakey.unwrap_master(old) == dk and datakey.master_pub() == old_pub
    with pytest.raises(ValueError):
        datakey.unwrap_master(new)
    con = A.connect(); assert K.master_pub(con) == old_pub; con.close()

    assert setting(A, "rotate_due") is None
    assert c2.get("/security", environ_base=E).status_code == 200
    assert "Asked for a new master key in place of a lost one" in audit(A)
    assert not tick(A)

def test_the_wait_shows_on_security_the_bell_and_today_each_with_cancel(env):
    A, c, _ = env
    ask(c)
    import auth
    with A.app.app_context():
        when = auth.master_pending(A.connect())["when"]
    sec = page(c, "/security")
    assert 'id="master-pending"' in sec and when in sec and CANCEL in sec
    al = page(c, "/alerts")
    assert f"A new master key takes effect on {when}" in al and CANCEL in al and "/security/master-key/cancel" in al
    today = page(c, "/")
    assert 'id="master-pending"' in today and when in today and CANCEL in today

def test_any_signed_in_device_cancels_it_and_then_it_never_applies(env):
    A, c, _ = env
    import datakey
    old = c.master_secret
    new, _ = ask(c)
    c2 = other_device(A)
    r = c2.post("/security/master-key/cancel", data=dict(back="/alerts"), environ_base=E)
    assert r.location.endswith("/alerts")
    assert setting(A, "master_pending") is None
    assert "Canceled the new master key" in audit(A)
    assert CANCEL not in page(c, "/alerts") and 'id="master-pending"' not in page(c, "/security")
    assert not tick(A)
    assert datakey.unwrap_master(old)
    with pytest.raises(ValueError):
        datakey.unwrap_master(new)
    r = c2.post("/security/master-key/cancel", data=dict(back="https://elsewhere.example/"), environ_base=E)
    assert r.location.endswith("/security#master-key")
    assert setting(A, "rotate_due") is None

def test_a_second_request_while_one_waits_is_refused_before_a_code_is_used(env):
    A, c, _ = env
    from werkzeug.security import generate_password_hash
    ask(c)
    first = setting(A, "master_pending")
    con = A.connect()
    con.execute("UPDATE users SET recovery=? WHERE id=1", (json.dumps([generate_password_hash("abcd-ef01-2345")]),))
    con.commit(); con.close()
    r = start(c, code_="abcd-ef01-2345")
    assert r.status_code == 200 and "already waiting to take effect" in html.unescape(r.get_data(as_text=True))
    con = A.connect()
    assert len(json.loads(con.execute("SELECT recovery FROM users WHERE id=1").fetchone()[0])) == 1
    con.close()
    assert setting(A, "master_pending") == first

def test_after_48_hours_it_takes_effect_and_only_then_asks_for_a_rotation(env):
    A, c, _ = env
    import datakey
    from vault import keys as K
    old, dk = c.master_secret, datakey.dk()
    c2 = other_device(A)
    new, _ = ask(c)
    move_due(A, timedelta(minutes=5))
    assert not tick(A) and setting(A, "rotate_due") is None
    move_due(A, -timedelta(minutes=1))
    assert tick(A)
    assert datakey.unwrap_master(new) == dk
    with pytest.raises(ValueError):
        datakey.unwrap_master(old)
    con = A.connect(); assert K.master_pub(con) == datakey.master_pub(); con.close()
    assert setting(A, "master_pending") is None
    assert setting(A, "rotate_due") and setting(A, "master_replaced_on")
    assert "Replaced a lost master key, asked for on" in audit(A)

    assert "/login" in c.get("/security", environ_base=E).headers.get("Location", "")
    assert "/login" in c2.get("/security", environ_base=E).headers.get("Location", "")
    assert "every device signed out" in audit(A)
    assert not tick(A)

def test_it_takes_effect_from_a_signed_in_page_load_too(env):
    A, c, _ = env
    import auth
    ask(c)
    move_due(A, -timedelta(minutes=1))
    auth.MASTER_TICK_AT[0] = 0.0
    assert "/login" in c.get("/security", environ_base=E).headers.get("Location", "")
    assert setting(A, "master_pending") is None and setting(A, "master_replaced_on")
    assert auth.MASTER_WAIT == timedelta(hours=48)

def test_every_sign_in_says_so_while_it_waits_and_for_30_days_after(env):
    A, c, _ = env
    import auth
    ask(c)
    t = time.time()
    c3 = sign_in(A, t)
    assert "A new master key takes effect on" in page(c3, "/security")
    move_due(A, -timedelta(minutes=1))
    c4 = sign_in(A, t + 30)
    day = setting(A, "master_replaced_on")
    assert day
    from datetime import date
    d = date.fromisoformat(day)
    said = page(c4, "/security")
    assert f"Master key replaced on {d:%B} {d.day}, {d.year}." in said and "rotate the data key" in said
    con = A.connect()
    try:
        assert "Master key replaced on" in auth.master_banner(con, d + timedelta(days=30))
        assert auth.master_banner(con, d + timedelta(days=31)) == ""
    finally:
        con.close()

def test_a_restart_during_the_wait_keeps_it_waiting(env):
    A, c, tmp = env
    import datakey
    old = c.master_secret
    new, _ = ask(c)
    first = setting(A, "master_pending")
    datakey.lock()
    fresh_modules()
    import app as A2
    import datakey as D2
    D2.set_unlocked(D2.unwrap_password(TEST_PASSWORD))
    A2.on_unlock()
    assert setting(A2, "master_pending") == first
    assert D2.unwrap_master(old)
    move_due(A2, -timedelta(minutes=1))
    assert A2.app.config["master_tick"](force=True)
    assert D2.unwrap_master(new) == D2.dk()

def test_a_wait_found_over_a_week_late_is_dropped_not_applied(env):
    A, c, _ = env
    import datakey
    old = c.master_secret
    ask(c)
    move_due(A, -timedelta(days=8))
    assert not tick(A)
    assert setting(A, "master_pending") is None and setting(A, "master_replaced_on") is None
    assert datakey.unwrap_master(old)
    assert "was not applied" in audit(A)

def test_the_old_key_path_stays_immediate_and_supersedes_a_waiting_one(env):
    A, c, _ = env
    import datakey
    old = c.master_secret
    ask(c)
    from test_167_155 import replace
    fresh_code()
    new2, r = replace(c, old)
    assert r.location.endswith("/security#master-key")
    assert datakey.unwrap_master(new2) == datakey.dk()
    assert setting(A, "master_pending") is None
    assert "the new key that was waiting is canceled" in audit(A)
    assert setting(A, "master_replaced_on") is None

def test_the_command_line_new_master_supersedes_a_waiting_one(env, monkeypatch):
    A, c, _ = env
    import contextlib
    import io
    from vault import cli
    old = c.master_secret
    ask(c)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": old)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["new-master"]) == 0
    assert "waiting to take effect in the app is canceled" in out.getvalue()
    assert setting(A, "master_pending") is None

def _ntfy(A):
    for k, v in (("ntfy_server", "https://ntfy.example.net"), ("ntfy_topic", "studio-k3j9x2q7"), ("ntfy_token", "")):
        put(A, k, v)
    with A.app.app_context():
        return A.ntfy_settings()

def test_the_phone_is_told_once_in_fixed_words(env, monkeypatch):
    A, c, _ = env
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((body.decode(), dict(headers))))
    N.step(lambda: True, A.connect, lambda: None)
    ask(c)
    N.step(lambda: True, A.connect, lambda: None)
    assert not [s for s in sent if s[1].get("Title") == N.NEWMASTER_TITLE]
    cfg = _ntfy(A)
    N.step(lambda: True, A.connect, lambda: cfg)
    N.step(lambda: True, A.connect, lambda: cfg)
    told = [b for b, h in sent if h.get("Title") == "Studio master key"]
    assert told == [N.NEWMASTER_TEXT]
    body = told[0]
    assert not any(ch.isdigit() for ch in body.replace("48", "")) and "@" not in body and "http" not in body
    assert "—" not in body
    c.post("/security/master-key/cancel", environ_base=E)
    N.step(lambda: True, A.connect, lambda: cfg)
    ask(c)
    N.step(lambda: True, A.connect, lambda: cfg)
    assert len([b for b, h in sent if h.get("Title") == "Studio master key"]) == 2

def test_the_sign_in_change_notice_is_on_by_default_once_a_server_is_set_up(env, monkeypatch):
    A, c, _ = env
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((body.decode(), dict(headers))))
    c.post("/security", data=dict(act="codes", current=TEST_PASSWORD), environ_base=E)
    assert setting(A, N.KEY_CRED_AT) is None
    cfg = _ntfy(A)
    assert 'checked>Your password, master key, or recovery codes changed' in page(c, "/settings?tab=notifications")
    assert N.cred_tick(A.connect, cfg) is None and not sent
    c.post("/security", data=dict(act="codes", current=TEST_PASSWORD), environ_base=E)
    assert N.cred_tick(A.connect, cfg) == "sent"

    c.post("/security/notice", data=dict(act="cred"), environ_base=E)
    assert setting(A, N.KEY_CRED_ON) == "0"
    c.post("/security", data=dict(act="codes", current=TEST_PASSWORD), environ_base=E)
    assert N.cred_tick(A.connect, cfg) is None and len(sent) == 1

    put(A, N.KEY_CRED_ON, "1")
    con = A.connect(); assert N.status_on(con, "cred"); con.close()
    con = A.connect(); con.execute("DELETE FROM settings WHERE key=?", (N.KEY_CRED_ON,)); con.commit()
    assert N.status_on(con, "cred"); con.close()

    c.post("/security/notice", data=dict(act="remove"), environ_base=E)
    assert setting(A, N.KEY_CRED_AT) is None

def test_the_docs_say_how_the_wait_works(env):
    text = open(os.path.join(ROOT, "docs", "security.md"), encoding="utf-8").read()
    assert "48 hours" in text and "Cancel the new master key" in text and "30 days" in text
    assert "Studio master key" in text and "on until you untick it" in text
    assert "—" not in text

def test_when_it_takes_effect_every_known_browser_is_forgotten_and_no_fresh_one_is_given(env):

    A, c, _ = env
    import auth
    import knownbrowser
    ask(c)
    tok = knownbrowser.issue()
    assert knownbrowser.known(tok)
    move_due(A, -timedelta(minutes=1))
    auth.MASTER_TICK_AT[0] = 0.0
    r = c.get("/security", environ_base=E)
    assert "/login" in r.headers.get("Location", "")
    assert not knownbrowser.known(tok) and not knownbrowser.path().exists()
    assert not any(h.startswith(knownbrowser.COOKIE + "=") for h in r.headers.getlist("Set-Cookie"))
    assert not auth.SESSIONS

def test_the_copy_says_every_device_will_be_signed_out(env):
    A, c, _ = env
    assert "every device is signed out, this one too" in page(c, "/security")
    fresh_code()
    start(c)
    new, body = shown_key(c)
    assert "every device is signed out, this one too" in html.unescape(body)
    c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
    assert "every device is signed out, this one too" in page(c, "/security")
    doc = open(os.path.join(ROOT, "docs", "security.md")).read()
    assert "every device is signed out, the one that asked for it too" in doc
    assert "always on, with no box to untick" in doc

def test_the_phone_notice_for_a_new_master_key_is_listed_always_on(env):
    A, c, _ = env
    _ntfy(A)
    body = page(c, "/settings?tab=notifications")
    assert "A new master key was asked for without the old one" in body
