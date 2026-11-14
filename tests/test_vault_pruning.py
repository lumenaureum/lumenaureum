import json
import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, unlock_new_install

def ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

@pytest.fixture
def env(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    unlock_new_install(A)
    from vault import backup as BK, seal as S, web as W
    return A, BK, S, W, tmp_path, store

def stored(store, name):
    return any(p.is_file() and name.split("/")[-1] in p.name for p in store.rglob("*"))

def test_orphaned_session_keys_go_after_thirty_days(env):
    A, BK, S, W, tmp, store = env
    con = A.connect()
    archives = {}
    for sid in (1, 2, 3):
        f = tmp / f"photos{sid}"; f.mkdir(); (f / "a.NEF").write_bytes(os.urandom(2000))
        archives[sid] = S.seal_session(con, sid, [str(f)], W.store_backends())
    con.execute("UPDATE vault_keys SET orphaned=? WHERE session_id=1", (ago(31),))
    con.execute("UPDATE vault_keys SET orphaned=? WHERE session_id=2", (ago(29),))
    con.commit()
    assert all(stored(store, n) for n in archives.values())
    BK.prune(con, W.store_backends())
    keys = {r[0] for r in con.execute("SELECT session_id FROM vault_keys")}
    assert keys == {2, 3}
    live = {r[0] for r in con.execute("SELECT session_id FROM vault_objects WHERE kind='session' AND deleted IS NULL")}
    assert live == {2, 3}
    assert con.execute("SELECT deleted FROM vault_objects WHERE session_id=1").fetchone()[0]
    con.close()
    assert not stored(store, archives[1])
    assert stored(store, archives[2]) and stored(store, archives[3])

def test_an_orphaned_key_with_a_copy_elsewhere_is_kept(env):
    A, BK, S, W, tmp, store = env
    con = A.connect()
    f = tmp / "photos"; f.mkdir(); (f / "a.NEF").write_bytes(os.urandom(2000))
    S.seal_session(con, 1, [str(f)], W.store_backends())
    con.execute("UPDATE vault_objects SET backend='sftp' WHERE session_id=1")
    con.execute("UPDATE vault_keys SET orphaned=? WHERE session_id=1", (ago(45),))
    con.commit()
    BK.prune(con, W.store_backends())
    assert con.execute("SELECT 1 FROM vault_keys WHERE session_id=1").fetchone()
    con.close()

def test_reset_snapshots_are_dropped_after_thirty_days(env):
    A, BK, S, W, tmp, store = env
    old = BK.sealed_backup(str(A.DB_PATH), None, kind="reset")[0]
    new = BK.sealed_backup(str(A.DB_PATH), None, kind="reset")[0]
    con = A.connect()
    con.execute("UPDATE vault_objects SET created=? WHERE name=?", (ago(31), old))
    con.execute("UPDATE vault_objects SET created=? WHERE name=?", (ago(29), new))
    con.commit()
    assert stored(store, old) and stored(store, new)
    BK.prune(con, W.store_backends())
    gone = con.execute("SELECT deleted FROM vault_objects WHERE name=?", (old,)).fetchone()[0]
    kept = con.execute("SELECT deleted FROM vault_objects WHERE name=?", (new,)).fetchone()[0]
    con.close()
    assert gone and not kept
    assert not stored(store, old) and stored(store, new)
