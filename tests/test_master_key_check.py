import logging
import os
import sys
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
PROMPT = 'id="master-key-check"'

def _install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    assert c.master_secret
    return A, c

def _set(A, sql, args=()):
    con = A.connect()
    con.execute(sql, args)
    con.commit()
    con.close()

def _setting(A, k):
    con = A.connect()
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()

def _prompted(c):
    r = c.get("/", environ_base=E)
    assert r.status_code == 200
    return PROMPT in r.get_data(as_text=True)

def _days_ago(A, n):
    return (A.today() - timedelta(days=n)).isoformat()

def _wrong_key():
    from vault import keys as K
    return K.generate_master()[0]

def test_asked_a_week_after_the_key_was_made(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    _set(A, "UPDATE users SET created=?", (_days_ago(A, 2),))
    assert not _prompted(c)
    _set(A, "UPDATE users SET created=?", (_days_ago(A, 7),))
    assert _prompted(c)

def test_asked_again_90_days_after_the_last_check(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    _set(A, "UPDATE users SET created=?", (_days_ago(A, 400),))
    _set(A, "INSERT INTO settings (key, value) VALUES ('master_checked', ?)", (_days_ago(A, 30),))
    assert not _prompted(c)
    _set(A, "UPDATE settings SET value=? WHERE key='master_checked'", (_days_ago(A, 90),))
    assert _prompted(c)

def test_not_now_puts_it_off_and_it_comes_back(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    assert _prompted(c)
    r = c.post("/security/master-key/later", environ_base=E)
    assert r.status_code == 302
    assert not _prompted(c)
    _set(A, "UPDATE settings SET value=? WHERE key='master_check_later'", (A.today().isoformat(),))
    assert _prompted(c)

def test_the_right_key_clears_it(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    assert _prompted(c)
    r = c.post("/security/master-key", data={"secret": c.master_secret}, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "That's your master key. Keep it where it is." in page
    assert r.headers["Cache-Control"] == "no-store"
    assert _setting(A, "master_checked") == A.today().isoformat()
    assert not _prompted(c)
    con = A.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM audit WHERE action='Checked the master key'").fetchone()[0] == 1
    finally:
        con.close()

def test_a_wrong_key_does_not(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    for bad in (_wrong_key(), "not a key at all", ""):
        r = c.post("/security/master-key", data={"secret": bad}, environ_base=E)
        assert r.status_code == 200
        assert "That isn't the master key this install uses." in r.get_data(as_text=True)
    assert _setting(A, "master_checked") is None
    assert _prompted(c)
    con = A.connect()
    try:
        assert not con.execute("SELECT COUNT(*) FROM audit WHERE action LIKE '%master key%'").fetchone()[0]
    finally:
        con.close()

def test_wrong_tries_are_limited_like_sign_in(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    import auth
    for _ in range(auth.MAX_FAILS):
        c.post("/security/master-key", data={"secret": _wrong_key()}, environ_base=E)
    r = c.post("/security/master-key", data={"secret": c.master_secret}, environ_base=E)
    assert "Too many wrong keys from here. Try again in 15 minutes." in r.get_data(as_text=True)
    assert _setting(A, "master_checked") is None

def test_wrong_keys_never_lock_sign_in(tmp_path, monkeypatch):

    A, c = _install(tmp_path, monkeypatch)
    import auth
    for _ in range(auth.MAX_FAILS):
        c.post("/security/master-key", data={"secret": _wrong_key()}, environ_base=E)
    for _ in range(2):
        c.post("/vault/check", data={"secret": "AGE-SECRET-KEY-1" + "Q" * 58}, environ_base=E)
    assert not A.app.config["auth_throttle"].ip_blocked("127.0.0.1")
    r = A.app.test_client().post("/login", data=dict(email="owner@example.com", password="not it"), environ_base=E)
    page = r.get_data(as_text=True)
    assert "Wrong email or password." in page and "Too many" not in page

def test_the_key_is_never_kept(tmp_path, monkeypatch, caplog, capsys):
    A, c = _install(tmp_path, monkeypatch)
    caplog.set_level(logging.DEBUG)
    secret, wrong = c.master_secret, _wrong_key()
    for s in (wrong, secret):
        r = c.post("/security/master-key", data={"secret": s}, environ_base=E)
        assert s not in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        assert secret not in repr(dict(sess)) and wrong not in repr(dict(sess))
    con = A.connect()
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        dump = "\n".join(repr(tuple(row)) for t in tables for row in con.execute(f'SELECT * FROM "{t}"'))
    finally:
        con.close()
    assert "master_checked" in dump and "Checked the master key" in dump
    for s in (secret, wrong):
        for piece in (s, s[16:], s[-12:], s.lower()):
            assert piece not in dump
    out = capsys.readouterr()
    logs = caplog.text + out.out + out.err
    assert secret not in logs and wrong not in logs and secret[-12:] not in logs
