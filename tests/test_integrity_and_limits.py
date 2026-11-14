import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client, unlock_new_install

E = {"REMOTE_ADDR": "127.0.0.1"}

def test_altered_backup_is_refused(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    from vault import backup as BK, seal as S
    old = BK.sealed_backup(str(A.DB_PATH), None)[0]
    new = BK.sealed_backup(str(A.DB_PATH), None)[0]
    con = A.connect()
    exp_new = BK.recorded_sha(con, new)
    con.close()
    assert exp_new
    files = {p.name: p for p in store.rglob("*") if p.is_file()}
    newf = next(p for n, p in files.items() if new.split("/")[-1] in n)
    oldf = next(p for n, p in files.items() if old.split("/")[-1] in n)
    newf.write_bytes(oldf.read_bytes())
    with pytest.raises(S.Mismatch):
        BK.restore(new, secret, str(tmp_path / "r1"), expected_sha=exp_new)
    assert not (tmp_path / "r1").exists()

def test_deleting_a_refunded_session_keeps_its_payments(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (1, 'portrait', '2026-03-02', 'shot')")
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 120, '2026-02-01')")
    con.commit(); con.close()
    c.post("/session/1/delete", environ_base=E)
    con = A.connect()
    assert con.execute("SELECT 1 FROM sessions WHERE id=1").fetchone()
    con.close()
    c.post("/session/1/money", data={"what": "payment", "kind": "refund", "amount": "120"}, environ_base=E)
    c.post("/session/1/delete", environ_base=E)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM sessions WHERE id=1").fetchone()
    assert con.execute("SELECT amount FROM books WHERE received='2026-02-01'").fetchone()[0] == 120
    con.close()

def test_password_checks_never_queue(monkeypatch):
    fresh_modules()
    import auth
    import datakey
    gate = threading.Event()
    monkeypatch.setattr(datakey, "unwrap_password", lambda pw: gate.wait(5) and None)
    ts = [threading.Thread(target=auth.unwrap_password_limited, args=("x",)) for _ in range(2)]
    for t in ts:
        t.start()
    import time
    time.sleep(0.2)
    with pytest.raises(auth.Busy):
        auth.unwrap_password_limited("x")
    gate.set()
    for t in ts:
        t.join()

def test_key_files_are_forced_to_disk(tmp_path, monkeypatch):
    fresh_modules()
    import datakey
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    datakey.write_sealed(tmp_path / "f.bin", b"hello", "aad", key=b"\0" * 32)
    assert len(synced) >= 2

def test_damaged_or_hostile_backups_are_refused(tmp_path, monkeypatch):

    import io
    import tarfile
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    from vault import backup as BK, seal as S, web as W
    good = BK.sealed_backup(str(A.DB_PATH), None)[0]
    path = next(p for p in store.rglob("*") if p.is_file() and good.split("/")[-1] in p.name)
    data = path.read_bytes()
    for i, bad in enumerate((data[:-40] + bytes([data[-40] ^ 1]) + data[-39:], data[: len(data) // 2])):
        path.write_bytes(bad)
        with pytest.raises(Exception):
            BK.restore(good, secret, str(tmp_path / f"bad{i}"))
        assert not (tmp_path / f"bad{i}").exists()
    path.write_bytes(data)
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tf:
        ti = tarfile.TarInfo("../escaped.txt"); body = b"x"; ti.size = len(body)
        tf.addfile(ti, io.BytesIO(body))
    con = A.connect()
    try:
        name = S.seal_file(con, str(evil), W.store_backends(), kind="manual", prefix="db")
    finally:
        con.close()
    with pytest.raises(ValueError):
        BK.restore(name, secret, str(tmp_path / "evil-out"))
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "evil-out").exists()

def test_unrecorded_backup_files_are_adopted(tmp_path, monkeypatch):

    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    unlock_new_install(A)
    from vault import backup as BK
    stray = store / "db" / ("0" * 32 + ".age")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"half an upload")
    BK.sealed_backup(str(A.DB_PATH), None)
    assert stray.exists()
    import sqlite3
    con = A.connect()
    try:
        kind, sha = con.execute("SELECT kind, sha256 FROM vault_objects WHERE name=?", ("db/" + "0" * 32 + ".age",)).fetchone()
        assert kind == "found"
        import hashlib
        assert sha == hashlib.sha256(b"half an upload").hexdigest()
    finally:
        con.close()
