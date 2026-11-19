import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import HttpsClient, TEST_PASSWORD, fresh_modules, signed_in_client, unlock_new_install

E = {"REMOTE_ADDR": "127.0.0.1"}
SERVER, TOPIC, TOKEN = "https://ntfy.example.net", "studio-k7Qx2", "tk_secret_token_123"
CLIENT_NAME = "Zebediah Quillfeather"
MESSAGE = re.compile(r"^Your studio restarted and has been locked since (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2}:\d{2} [ap]m\. "
                     r"Backups and gallery closings are waiting until you sign in\.$")
REMOVED_SAY = ("Unattended unlock was removed in Lumen Aureum 1.5: the studio now always starts locked. The old key "
               "file is no longer used and can be deleted from its separate drive. Also take the <code>CRM_UNLOCK_KEY_FILE</code> "
               "line and the <code>/unlock</code> volume line out of compose.yaml (on TrueNAS, out of the app's Edit screen).")

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    return A, signed_in_client(A), tmp_path

def restart():

    fresh_modules()
    import app as A
    A.app.test_client_class = HttpsClient
    return A

def sign_in(A):

    unlock_new_install(A)
    return signed_in_client(A)

def login_page(A):
    return A.app.test_client().get("/login", environ_base=E).get_data(as_text=True)

@pytest.fixture
def sent(monkeypatch):

    out = []

    def fake_post(url, body, headers):
        out.append((url, body.decode(), dict(headers)))
    import locknotice
    monkeypatch.setattr(locknotice, "_post", fake_post)
    return out

def set_up_notice(c, token=TOKEN):
    return c.post("/security/notice", data=dict(act="save", server=SERVER, topic=TOPIC, token=token), environ_base=E)

def patch_post(monkeypatch, out):
    import locknotice
    monkeypatch.setattr(locknotice, "_post", lambda url, body, headers: out.append((url, body.decode(), dict(headers))))

def test_unattended_unlock_is_removed(env):
    A, c, _ = env
    import auth
    import datakey
    import settings_index
    for gone in ("enable_auto", "disable_auto", "try_auto_unlock", "auto_file", "auto_enabled", "check_auto_place"):
        assert not hasattr(datakey, gone), gone
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert "Unattended unlock" not in page and "CRM_UNLOCK_KEY_FILE" not in page
    r = c.post("/security", data=dict(current=TEST_PASSWORD, act="auto_on"), environ_base=E)
    assert r.status_code == 200 and "auto" not in json.loads(datakey.KEYRING.read_text())
    assert not [e for e in settings_index.ENTRIES if "nattended" in e[0]]
    for f in ("app.py", "auth.py", "datakey.py", "compose.yaml", "templates/auth_security.html"):
        text = open(os.path.join(ROOT, f)).read()
        assert "os.environ.get(\"CRM_UNLOCK_KEY_FILE\"" not in text and "CRM_UNLOCK_KEY_FILE:" not in text, f
    assert "auto_on" not in open(auth.__file__).read()

def _turn_on_like_14(datakey, key, key_file):

    from pyrage import x25519
    import pyrage
    ident = x25519.Identity.generate()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(str(ident) + "\n")
    ring = json.loads(datakey.KEYRING.read_text())
    ring["auto"] = base64.b64encode(pyrage.encrypt(key, [ident.to_public()])).decode()
    ring["auto_pub"] = str(ident.to_public())
    datakey._write(ring, key)

def test_upgrade_from_14x_with_unattended_unlock_on(env, monkeypatch):
    A, c, tmp = env
    import datakey
    key = datakey.dk()
    key_file = tmp / "other-drive" / "unlock.key"
    _turn_on_like_14(datakey, key, key_file)
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(key_file))

    A = restart()
    import datakey
    assert not datakey.is_unlocked()
    assert A.app.test_client().get("/healthz", environ_base=E).get_json()["locked"] is True
    page = login_page(A)
    assert REMOVED_SAY in page
    assert REMOVED_SAY in login_page(A)

    sign_in(A)
    ring = json.loads(datakey.KEYRING.read_text())
    assert "auto" not in ring and "auto_pub" not in ring and ring["auto_removed"] == "1.5.0"
    assert datakey.unwrap_password(TEST_PASSWORD) == key
    assert REMOVED_SAY not in login_page(A)

    A = restart()
    assert REMOVED_SAY not in login_page(A)
    assert "Locked after a restart" in login_page(A)
    sign_in(A)
    assert json.loads(sys.modules["datakey"].KEYRING.read_text())["auto_removed"] == "1.5.0"

def test_upgrade_with_only_the_variable_set_says_it_once_too(env, monkeypatch):
    A, c, tmp = env
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(tmp / "elsewhere" / "unlock.key"))
    A = restart()
    assert REMOVED_SAY in login_page(A)
    sign_in(A)
    A = restart()
    assert REMOVED_SAY not in login_page(A)

def test_an_install_that_never_had_it_is_not_told(env):
    A, c, _ = env
    import datakey
    before = datakey.KEYRING.read_text()
    A = restart()
    assert REMOVED_SAY not in login_page(A)
    sign_in(A)
    assert sys.modules["datakey"].KEYRING.read_text() == before

def test_off_by_default_and_set_up_in_security(env, sent):
    A, c, tmp = env
    import locknotice
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert 'id="notice"' in page and "Send a test notice" in page and "Studio locked after a restart" not in page
    assert not locknotice.config_path().exists()
    assert "ntfy.json" in page

    r = c.post("/security/notice", data=dict(act="save", server="http://ntfy.example.net", topic=TOPIC), environ_base=E)
    assert r.status_code == 302 and not locknotice.config_path().exists()
    r = c.post("/security/notice", data=dict(act="save", server=SERVER, topic="has a space"), environ_base=E)
    assert not locknotice.config_path().exists()

    r = set_up_notice(c)
    assert r.status_code == 302 and r.location.endswith("/security#notice")
    cfg = json.loads(locknotice.config_path().read_text())
    assert cfg["server"] == SERVER and cfg["topic"] == TOPIC and cfg["token"] == TOKEN
    assert oct(os.stat(locknotice.config_path()).st_mode)[-3:] == "600"
    assert locknotice.config_path().parent == tmp
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert re.search(r'<input type="checkbox" checked disabled>Studio locked after a restart', page)
    assert "Send a test notice" in page and TOKEN not in page and "Leave blank to keep it" in page

    set_up_notice(c, token="")
    assert json.loads(locknotice.config_path().read_text())["token"] == TOKEN

    c.post("/security/notice", data=dict(act="remove"), environ_base=E)
    assert not locknotice.config_path().exists()
    assert "Studio locked after a restart" not in c.get("/security", environ_base=E).get_data(as_text=True)
    assert not sent

def test_the_test_button_sends_with_the_token(env, sent, monkeypatch):
    A, c, _ = env
    import locknotice
    set_up_notice(c)
    c.post("/security/notice", data=dict(act="test"), environ_base=E)
    assert len(sent) == 1
    url, body, headers = sent[0]
    assert url == f"{SERVER}/{TOPIC}" and body == locknotice.TEST_TEXT
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Test notice sent" in c.get("/security", environ_base=E).get_data(as_text=True)

    def refuse(*a):
        raise OSError("the server answered 403")
    monkeypatch.setattr(locknotice, "_post", refuse)
    c.post("/security/notice", data=dict(act="test"), environ_base=E)
    assert "The test notice couldn&#39;t be sent: the server refused it" in c.get("/security", environ_base=E).get_data(as_text=True)

def test_no_token_no_authorization_header(env, sent):
    A, c, _ = env
    set_up_notice(c, token="")
    c.post("/security/notice", data=dict(act="test"), environ_base=E)
    assert "Authorization" not in sent[0][2]

def test_only_https_ever_leaves(env):
    import locknotice
    with pytest.raises(ValueError):
        locknotice._post("http://ntfy.example.net/x", b"x", {})
    assert locknotice.send(dict(server="http://ntfy.example.net", topic="x"), "x")

def _client_and_notice(A, c):
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES (?)", (CLIENT_NAME,))
    con.commit()
    con.close()
    set_up_notice(c)

def test_a_locked_start_sends_one_content_free_notice(env, monkeypatch):
    A, c, _ = env
    _client_and_notice(A, c)
    out = []
    A = restart()
    import datakey
    import locknotice
    patch_post(monkeypatch, out)
    assert not datakey.is_unlocked()
    assert locknotice.tick(datakey.is_unlocked) == "sent"
    assert len(out) == 1
    url, body, headers = out[0]
    assert url == f"{SERVER}/{TOPIC}" and MESSAGE.match(body), body
    assert headers["Authorization"] == f"Bearer {TOKEN}" and headers["Title"] == "Studio locked"

    assert CLIENT_NAME not in body and "Zebediah" not in body
    assert not re.search(r"https?:|www\.|\.(com|net|org|local)\b|://", body)
    assert not {h.lower() for h in headers} & {"click", "actions", "attach", "icon", "x-click", "x-actions"}
    assert not re.search(r"\d+ (client|session|gallery|galleries|backup)", body)
    assert locknotice.tick(datakey.is_unlocked) is None

def test_at_most_once_a_day_and_a_restart_loop_never_floods(env, monkeypatch):
    A, c, _ = env
    _client_and_notice(A, c)
    out = []
    A = restart()
    import datakey
    import locknotice
    patch_post(monkeypatch, out)
    t0 = datetime.now(timezone.utc)
    assert locknotice.tick(datakey.is_unlocked, now=t0) == "sent"
    for h in (1, 6, 23):
        assert locknotice.tick(datakey.is_unlocked, now=t0 + timedelta(hours=h)) is None
    assert locknotice.tick(datakey.is_unlocked, now=t0 + timedelta(hours=24, minutes=1)) == "sent"
    assert len(out) == 2
    since = locknotice.locked_since()
    A = restart()
    import datakey
    import locknotice
    patch_post(monkeypatch, out)
    assert locknotice.locked_since() == since
    assert locknotice.tick(datakey.is_unlocked, now=t0 + timedelta(hours=25)) is None
    assert len(out) == 2

def test_a_failed_send_is_logged_and_not_retried_in_a_loop(env, monkeypatch, caplog):
    A, c, _ = env
    _client_and_notice(A, c)
    A = restart()
    import datakey
    import locknotice
    tries = []

    def down(*a):
        tries.append(1)
        raise OSError("connection refused")
    monkeypatch.setattr(locknotice, "_post", down)
    import logging
    log = logging.getLogger("test-locked-notice")
    t0 = datetime.now(timezone.utc)
    with caplog.at_level(logging.WARNING):
        assert locknotice.tick(datakey.is_unlocked, log, now=t0) == "failed"
    assert "could not be sent" in caplog.text and "connection refused" in caplog.text
    for m in (1, 2, 30, 59):
        assert locknotice.tick(datakey.is_unlocked, log, now=t0 + timedelta(minutes=m)) is None
    assert len(tries) == 1
    out = []
    patch_post(monkeypatch, out)
    assert locknotice.tick(datakey.is_unlocked, log, now=t0 + timedelta(minutes=61)) == "sent" and len(out) == 1

def test_nothing_is_sent_when_not_set_up_or_unlocked(env, monkeypatch):
    A, c, _ = env
    out = []
    A = restart()
    import datakey
    import locknotice
    patch_post(monkeypatch, out)
    assert locknotice.tick(datakey.is_unlocked) is None
    assert not out
    assert locknotice.tick(lambda: True) is None

def test_the_time_reads_like_a_person_would_say_it():
    import locknotice
    at = datetime(2026, 11, 14, 7, 10, tzinfo=timezone.utc)
    assert locknotice.when(at, "America/New_York") == "Sat 2:10 am"
    assert locknotice.when(datetime(2026, 11, 14, 19, 5, tzinfo=timezone.utc), "UTC") == "Sat 7:05 pm"
    assert MESSAGE.match(locknotice.locked_text(at, "America/New_York"))

def _locked_hours_ago(hours):
    import locknotice
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    locknotice._save(locknotice.state_path(), dict(locked_since=since.isoformat(timespec="seconds")))
    return since

def test_today_says_once_what_waited_and_suggests_the_notice_once(env):
    A, c, _ = env
    A = restart()
    since = _locked_hours_ago(7)
    c = sign_in(A)
    import locknotice
    page = c.get("/", environ_base=E).get_data(as_text=True)
    m = re.search(r'id="locked-waited">([^<]*)<', page)
    assert m and m.group(1) == (f"Locked from {locknotice.when(since, A.TZ.key)} until you signed in. "
                                "Backups and gallery closings that waited are catching up now.")
    assert 'id="ntfy-suggest"' in page and "locked-studio notice" in page
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert 'id="locked-waited"' not in page and 'id="ntfy-suggest"' not in page
    assert locknotice.locked_since() is None

    A = restart()
    _locked_hours_ago(2)
    c = sign_in(A)
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert 'id="locked-waited"' in page and 'id="ntfy-suggest"' not in page

def test_a_quick_sign_in_has_nothing_to_say_and_no_suggestion_once_set_up(env, sent):
    A, c, _ = env
    set_up_notice(c)
    A = restart()
    c = sign_in(A)
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert 'id="locked-waited"' not in page and 'id="ntfy-suggest"' not in page

def test_the_plain_copy_follows_the_encrypted_settings_at_unlock(env, sent):
    A, c, _ = env
    import locknotice
    set_up_notice(c)
    locknotice.clear_config()
    A = restart()
    sign_in(A)
    import locknotice
    cfg = locknotice.read_config()
    assert cfg and cfg["topic"] == TOPIC and cfg["tz"] == A.TZ.key
