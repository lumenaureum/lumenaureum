import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def _client(A, name):
    con = A.connect()
    hid = con.execute("INSERT INTO households (id, name) VALUES (?, ?)", (A_next(A), name)).lastrowid
    con.commit()
    con.close()
    return hid

def A_next(A):
    with A.app.test_request_context():
        n = A.next_id("households")
        A.db().commit()
    return n

def _here(A, hid):
    con = A.connect()
    try:
        return bool(con.execute("SELECT 1 FROM households WHERE id=?", (hid,)).fetchone())
    finally:
        con.close()

def test_a_restore_merges_its_deletions_and_marks_with_the_live_ones(install, tmp_path):
    A, c = install
    import db
    early, late = _client(A, "Early"), _client(A, "Late")
    c.post(f"/client/{early}/delete", data={"confirm": "DELETE"}, environ_base=E)
    snap = tmp_path / "mid.db"
    db.snapshot(snap)
    backup_deletions = A.DELETIONS_JSON.read_text()
    backup_marks = json.loads(A.NUMBERS_JSON.read_text())
    c.post(f"/client/{late}/delete", data={"confirm": "DELETE"}, environ_base=E)
    live_marks = json.loads(A.NUMBERS_JSON.read_text())
    A.DELETIONS_JSON.write_text(json.dumps([d for d in json.loads(A.DELETIONS_JSON.read_text()) if d["id"] == late]))
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)
    A.DELETIONS_RESTORED.write_text(backup_deletions)
    A.NUMBERS_RESTORED.write_text(json.dumps(dict(backup_marks, Q=999)))
    A._unlocked_once.clear()
    A.on_unlock()
    ids = sorted(d["id"] for d in json.loads(A.DELETIONS_JSON.read_text()))
    assert ids == sorted([early, late])
    marks = json.loads(A.NUMBERS_JSON.read_text())
    assert marks["Q"] == 999 and marks["H"] >= live_marks["H"]
    assert not A.DELETIONS_RESTORED.exists() and not A.NUMBERS_RESTORED.exists()
    assert not _here(A, early) and not _here(A, late)

def test_restore_names_the_side_files_for_the_merge(install, tmp_path):
    A, c = install
    from vault import backup as BK, web as VW
    store = tmp_path / "vault"
    store.mkdir()
    from vault import backends as B
    B.LocalBackend.init_root(str(store))
    cfg = VW.config()
    cfg["primary"] = {"type": "local", "path": str(store)}
    VW.write_config(cfg)
    hid = _client(A, "Gone")
    c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    names = BK.sealed_backup(str(A.DB_PATH))
    dest = tmp_path / "restored"
    BK.restore(names[0], c.master_secret, str(dest))
    have = set(os.listdir(dest))
    assert {"deletions.restored.json", "doc-numbers.restored.json"} <= have
    assert "deletions.json" not in have and "doc-numbers.json" not in have

def test_an_unverified_deletion_never_moves_an_id_mark(install):
    A, c = install
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=999_999_998, created="2025-01-01 00:00:00", mac="0" * 64),
                                            dict(kind="session", id=999_999_997, created="2025-01-01 00:00:00", mac="0" * 64)]))
    A.DELETIONS_ASIDE.write_text(json.dumps([dict(id=888_888_888, created="2025-01-01 00:00:00")]))
    with A.app.test_request_context():
        A._seed_id_marks()
        A.db().commit()
        assert A.id_mark("households") < 1000 and A.id_mark("sessions") < 1000

def test_number_marks_take_turns(install, monkeypatch):
    A, c = install
    real = A._number_marks
    gate = threading.Barrier(2, timeout=2)

    def slow():
        m = real()
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return m
    monkeypatch.setattr(A, "_number_marks", slow)
    got = []

    def one():
        with A.app.app_context():
            got.append(A.next_quote_number())
    ts = [threading.Thread(target=one) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(got)) == 2
