"""A database reset needs RESET typed, deletes nothing when the sealed copy beforehand fails, and keeps settings when it runs."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('The Harlows')")
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('business_name', 'Bright Studio')")
    con.commit(); con.close()
    return A, c


def households(A):
    con = A.connect()
    try:
        return con.execute("SELECT COUNT(*) FROM households").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("typed", ["", "reset", "RESET please", "yes"])
def test_reset_without_typing_reset_is_refused(env, typed):
    A, c = env
    r = c.post("/settings/reset", data=dict(confirm=typed), environ_base=E)
    assert r.status_code == 302 and "reset=nope" in r.headers["Location"]
    assert households(A) == 1


def test_failed_backup_before_reset_deletes_nothing(env, monkeypatch):
    A, c = env
    from vault import backup as BK

    def boom(*a, **kw):
        raise IOError("the vault is unreachable")
    monkeypatch.setattr(BK, "sealed_backup", boom)
    try:
        r = c.post("/settings/reset", data=dict(confirm="RESET"), environ_base=E)
        assert r.status_code >= 500 or "reset=done" not in r.headers.get("Location", "")
    except IOError:
        pass
    assert households(A) == 1


def test_reset_wipes_clients_and_keeps_settings(env):
    A, c = env
    r = c.post("/settings/reset", data=dict(confirm=" RESET "), environ_base=E)
    assert r.status_code == 302 and "tab=system" in r.headers["Location"]          # the job panel reports it
    assert households(A) == 0
    con = A.connect()
    try:
        assert con.execute("SELECT value FROM settings WHERE key='business_name'").fetchone()[0] == "Bright Studio"
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM vault_objects WHERE kind='reset'").fetchone()[0] >= 1
    finally:
        con.close()
