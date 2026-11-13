"""Creating the master key shows the secret once, checks its last six characters, refuses a second key, and expires."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]+")


def _no_master_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("DELETE FROM settings WHERE key='vault_master_pub'")
    con.commit(); con.close()
    from vault import web as W
    W.PENDING.clear()
    return A, c, W


def _master_pub(A):
    from vault import keys as K
    con = A.connect()
    try:
        return K.master_pub(con)
    finally:
        con.close()


def _start(c):
    r = c.post("/vault/new", environ_base=E)
    assert r.status_code == 302 and "/vault/new/" in r.headers["Location"]
    return r.headers["Location"].rsplit("/", 1)[1]


def test_ceremony_shows_secret_once_and_checks_the_tail(tmp_path, monkeypatch):
    A, c, W = _no_master_key(tmp_path, monkeypatch)
    tok = _start(c)
    first = c.get(f"/vault/new/{tok}", environ_base=E)
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
    m = SECRET.search(first.get_data(as_text=True))
    assert m
    secret = m.group(0)
    assert "secret" not in W.PENDING[tok] and W.PENDING[tok]["tail"] == secret[-6:].upper()
    assert secret not in c.get(f"/vault/new/{tok}", environ_base=E).get_data(as_text=True)   # a reload never shows it
    bad = "AAAAAA" if secret[-6:] != "AAAAAA" else "BBBBBB"
    wrong = c.post(f"/vault/confirm/{tok}", data={"tail": bad}, environ_base=E)
    assert "err=1" in wrong.headers["Location"] and _master_pub(A) is None
    ok = c.post(f"/vault/confirm/{tok}", data={"tail": secret[-6:].lower()}, environ_base=E)
    assert ok.status_code == 302 and tok not in W.PENDING
    from vault import keys as K
    pub = _master_pub(A)
    assert pub
    import pyrage
    assert pyrage.decrypt(pyrage.encrypt(b"x", [K.recipient(pub)]), [K.parse_secret(secret)]) == b"x"


def test_a_second_master_key_is_refused(tmp_path, monkeypatch):
    A, c, W = _no_master_key(tmp_path, monkeypatch)
    from vault import keys as K
    con = A.connect(); K.set_master_pub(con, K.generate_master()[1]); con.close()
    assert c.post("/vault/new", environ_base=E).status_code == 409
    assert not W.PENDING


def test_an_expired_ceremony_leaves_nothing(tmp_path, monkeypatch):
    A, c, W = _no_master_key(tmp_path, monkeypatch)
    tok = _start(c)
    W.PENDING[tok]["at"] -= W.TTL + 1
    r = c.get(f"/vault/new/{tok}", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/vault") and tok not in W.PENDING
    tok = _start(c)
    secret = SECRET.search(c.get(f"/vault/new/{tok}", environ_base=E).get_data(as_text=True)).group(0)
    W.PENDING[tok]["at"] -= W.TTL + 1
    r = c.post(f"/vault/confirm/{tok}", data={"tail": secret[-6:]}, environ_base=E)
    assert r.headers["Location"].endswith("/vault") and tok not in W.PENDING and _master_pub(A) is None


def test_confirm_before_the_key_was_shown_is_refused(tmp_path, monkeypatch):
    A, c, W = _no_master_key(tmp_path, monkeypatch)
    tok = _start(c)
    tail = W.PENDING[tok]["secret"][-6:]
    r = c.post(f"/vault/confirm/{tok}", data={"tail": tail}, environ_base=E)
    assert r.headers["Location"].endswith("/vault") and _master_pub(A) is None
    r = c.post("/vault/confirm/not-a-token", data={"tail": tail}, environ_base=E)
    assert r.headers["Location"].endswith("/vault")


def test_a_new_ceremony_drops_the_abandoned_one(tmp_path, monkeypatch):
    A, c, W = _no_master_key(tmp_path, monkeypatch)
    first = _start(c)
    second = _start(c)
    assert first not in W.PENDING and second in W.PENDING
