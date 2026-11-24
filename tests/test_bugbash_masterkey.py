import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_167_155 import E, shown_key, start

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
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

def ask(c):

    import app as A
    con = A.connect()
    con.execute("UPDATE users SET totp_last=0")
    con.commit(); con.close()
    assert start(c).location.endswith("/security/new-master-key")
    new, _ = shown_key(c)
    r = c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
    assert setting(A, "master_pending"), "the request should be waiting"
    return new, r

def set_tz(c, tz):
    r = c.post("/settings/timezone", data=dict(timezone=tz), environ_base=E)
    assert r.status_code == 302

def test_changing_the_time_zone_cuts_the_48_hour_wait_to_a_day(env):

    A, c, _ = env
    import datakey
    set_tz(c, "Pacific/Pago_Pago")
    real_asked = datetime.now(timezone.utc)
    new, _ = ask(c)
    set_tz(c, "Pacific/Kiritimati")
    assert A.TZ.key == "Pacific/Kiritimati"

    later = real_asked + timedelta(hours=24)
    A.app.config["now"] = lambda: later.astimezone(A.TZ).replace(tzinfo=None)
    took = A.app.config["master_tick"](force=True)
    assert not took, "the new master key took effect only 24 real hours after it was asked for (48 promised)"
    assert datakey.unwrap_master(c.master_secret)

def test_removing_the_notice_server_silences_the_always_on_new_master_key_notice(env, monkeypatch):

    A, c, _ = env
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((body.decode(), dict(headers))))
    for k, v in (("ntfy_server", "https://ntfy.example.net"), ("ntfy_topic", "studio-k3j9x2q7"), ("ntfy_token", "")):
        put(A, k, v)
    with A.app.app_context():
        assert A.ntfy_settings()

    r = c.post("/security/notice", data=dict(act="remove"), environ_base=E)
    assert r.status_code == 302
    ask(c)

    def cfg():
        with A.app.app_context():
            return A.ntfy_settings()
    N.step(lambda: True, A.connect, cfg)
    N.step(lambda: True, A.connect, cfg)
    told = [b for b, h in sent if h.get("Title") == N.NEWMASTER_TITLE]
    assert told, "the owner's phone was never told a new master key was asked for"

def test_replacing_the_notice_server_twice_still_tells_the_first_one(env, monkeypatch):

    A, c, _ = env
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((url, dict(headers))))
    for k, v in (("ntfy_server", "https://ntfy.example.net"), ("ntfy_topic", "studio-k3j9x2q7"), ("ntfy_token", "")):
        put(A, k, v)
    for topic in ("theirs-one-8k2j", "theirs-two-8k2j"):
        r = c.post("/security/notice", data=dict(act="save", server="https://ntfy.example.org", topic=topic),
                   environ_base=E)
        assert r.status_code == 302
    ask(c)

    def cfg():
        with A.app.app_context():
            return A.ntfy_settings()
    N.step(lambda: True, A.connect, cfg)
    told = [u for u, h in sent if h.get("Title") == N.NEWMASTER_TITLE]
    assert any("studio-k3j9x2q7" in u for u in told), told
    assert any("theirs-two-8k2j" in u for u in told), told

def test_the_kept_server_hears_of_a_new_master_key_for_30_days_only(env, monkeypatch):

    A, c, _ = env
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((url, dict(headers))))
    clock = [1_800_000_000.0]
    monkeypatch.setattr(N, "_epoch", lambda: clock[0])
    for k, v in (("ntfy_server", "https://ntfy.example.net"), ("ntfy_topic", "studio-k3j9x2q7"), ("ntfy_token", "")):
        put(A, k, v)
    assert c.post("/security/notice", data=dict(act="remove"), environ_base=E).status_code == 302
    assert N.newmaster_also(A.connect)["topic"] == "studio-k3j9x2q7"
    clock[0] += 29 * 86400
    assert N.newmaster_also(A.connect)["topic"] == "studio-k3j9x2q7", "still kept on day 29"
    clock[0] += 2 * 86400
    assert N.newmaster_also(A.connect) is None, "day 31: no longer kept"
    ask(c)

    def cfg():
        with A.app.app_context():
            return A.ntfy_settings()
    N.step(lambda: True, A.connect, cfg)
    assert not [u for u, h in sent if h.get("Title") == N.NEWMASTER_TITLE], sent

    for k, v in (("ntfy_server", "https://ntfy.example.org"), ("ntfy_topic", "later-k3j9x2q7"), ("ntfy_token", "")):
        put(A, k, v)
    assert c.post("/security/notice", data=dict(act="remove"), environ_base=E).status_code == 302
    assert N.newmaster_also(A.connect)["topic"] == "later-k3j9x2q7"
