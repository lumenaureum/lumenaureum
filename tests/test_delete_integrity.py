import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "g-abcdef123456AAAAAAAAAAA"

@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def _upload(c, sid, name, data, kind="raw"):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name},
               environ_base=E).get_json()
    at, piece = st["received"], st["piece"]
    while at < len(data):
        r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}", data=data[at:at + piece],
                   headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
        assert r.status_code == 200, r.get_data(as_text=True)
        at = r.get_json()["received"]

def _live_gallery(A, c, tmp_path, host_path=None):
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host_path or host)}
    VW.write_config(cfg)
    (host / GID).mkdir()
    (host / GID / "index.html").write_text("x")
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id) VALUES (1, 'family', 'delivered', '2026-01-01', ?)", (GID,))
    con.execute("INSERT INTO galleries (session_id, key, upload_state) VALUES (1, 'k', 'up')")
    con.commit()
    con.close()
    _upload(c, 1, "a.nef", b"x" * 5000)
    return host

def _one(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def test_deleting_a_session_with_a_live_gallery_and_photos_deletes_all_of_it(install, tmp_path):
    A, c = install
    import photostore
    host = _live_gallery(A, c, tmp_path)
    fid = _one(A, "SELECT id FROM photo_files WHERE session_id=1")[0]["id"]
    assert photostore.path_of(fid).exists()
    r = c.post("/session/1/delete", environ_base=E)
    assert r.status_code == 302
    assert not _one(A, "SELECT id FROM sessions WHERE id=1")
    assert not _one(A, "SELECT id FROM photo_files WHERE session_id=1")
    assert not photostore.path_of(fid).exists() and not (host / GID).exists()
    assert _one(A, "SELECT 1 FROM photo_deleted WHERE id=?", (fid,))
    entries = json.loads(A.DELETIONS_JSON.read_text())
    assert [(d["kind"], d["id"]) for d in entries] == [("session", 1)]
    assert any(a["action"].startswith("Deleted the") for a in _one(A, "SELECT action FROM audit WHERE session_id=1"))

def test_deleting_a_session_while_the_gallery_host_is_down_keeps_everything(install, tmp_path):
    A, c = install
    import photostore
    _live_gallery(A, c, tmp_path, host_path=tmp_path / "gone")
    fid = _one(A, "SELECT id FROM photo_files WHERE session_id=1")[0]["id"]
    c.post("/session/1/delete", environ_base=E)
    assert _one(A, "SELECT id FROM sessions WHERE id=1")
    g = _one(A, "SELECT upload_state, removed_at FROM galleries WHERE session_id=1")[0]
    assert g["upload_state"] == "up" and g["removed_at"] is None
    assert photostore.path_of(fid).exists()
    assert not A.DELETIONS_JSON.exists() or json.loads(A.DELETIONS_JSON.read_text()) == []
    import jobs
    assert jobs.JOBS["session-delete:1"]["state"] == "error"

def test_a_failed_record_delete_signs_no_deletion_and_a_retry_signs_one(install, tmp_path, monkeypatch):
    A, c = install
    _live_gallery(A, c, tmp_path)
    real = A._delete_session_rows
    monkeypatch.setattr(A, "_delete_session_rows", lambda sid: (_ for _ in ()).throw(RuntimeError("disk")))
    c.post("/session/1/delete", environ_base=E)
    assert _one(A, "SELECT id FROM sessions WHERE id=1")
    assert not A.DELETIONS_JSON.exists() or json.loads(A.DELETIONS_JSON.read_text()) == []
    monkeypatch.setattr(A, "_delete_session_rows", real)
    c.post("/session/1/delete", environ_base=E)
    assert not _one(A, "SELECT id FROM sessions WHERE id=1")
    assert len(json.loads(A.DELETIONS_JSON.read_text())) == 1
    with A.app.test_request_context():
        A._record_deletion(1, "x", kind="session")
        n = len(json.loads(A.DELETIONS_JSON.read_text()))
        created = json.loads(A.DELETIONS_JSON.read_text())[0]["created"]
        A._record_deletion(1, created, kind="session")
        assert len(json.loads(A.DELETIONS_JSON.read_text())) == n == 2

def test_a_photo_record_rolled_back_keeps_its_file(install, tmp_path):
    A, c = install
    import photostore
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (1, 'family', 'delivered')")
    con.commit()
    con.close()
    _upload(c, 1, "a.nef", b"y" * 3000)
    con = A.connect()
    row = con.execute("SELECT * FROM photo_files").fetchone()
    pending = photostore.delete(con, row, commit=False)
    assert photostore.path_of(row["id"]).exists()
    con.rollback()
    assert con.execute("SELECT 1 FROM photo_files WHERE id=?", (row["id"],)).fetchone()
    assert photostore.path_of(row["id"]).exists()
    photostore.delete(con, row)
    assert not photostore.path_of(row["id"]).exists() and not con.execute("SELECT 1 FROM photo_files").fetchone()
    con.close()
    assert pending == [row["id"]]

def test_a_destroyed_archive_is_on_record_even_if_the_rest_rolls_back(install, tmp_path, monkeypatch):
    A, c = install
    from vault import backends as VB, keys as VK, seal as VS
    store = tmp_path / "vault"
    store.mkdir()
    (store / ".vault-root").write_text("")
    bk = VB.LocalBackend(str(store))
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (1, 'family', 'delivered')")
    con.commit()
    import io
    bk.put_stream("s/1.tar.age", io.BytesIO(b"sealed"))
    VS.record(con, 1, "raw", "s/1.tar.age", [(bk, 6, "0" * 64)])
    con.commit()
    monkeypatch.setattr(VK, "shred", lambda *_a: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        VS.destroy_session(con, 1, [bk])
    con.rollback()
    assert bk.stat("s/1.tar.age") is None
    assert con.execute("SELECT deleted FROM vault_objects WHERE session_id=1").fetchone()[0]
    con.close()

def test_reapplying_a_client_deletion_after_a_restore_keeps_their_money(install, tmp_path):
    A, c = install
    import db
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Harlow')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'family', 'delivered')", (hid,)).lastrowid
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (?, 'deposit', 250, '2026-03-01')", (sid,))
    con.commit()
    con.close()
    snap = tmp_path / "before.db"
    db.snapshot(snap)
    c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    assert [r["amount"] for r in _one(A, "SELECT amount FROM books")] == [250]
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)
    assert not _one(A, "SELECT 1 FROM books")
    assert A.reapply_deletions() == 1
    assert not _one(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    assert [(r["amount"], r["received"]) for r in _one(A, "SELECT amount, received FROM books")] == [(250, "2026-03-01")]
