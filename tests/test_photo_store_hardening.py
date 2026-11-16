import io
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, unlock_new_install

REC = 1 << 20

@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    unlock_new_install(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Harlow Family')")
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (1, 'portrait', 'shot')").lastrowid
    con.commit()
    con.close()
    return A, sid, tmp_path

def _store(tmp_path, name):
    from vault import backends as B
    d = tmp_path / name
    d.mkdir()
    B.LocalBackend.init_root(str(d))
    return B.LocalBackend(str(d))

def test_a_resume_after_a_database_restore_starts_over_under_a_fresh_key(install, tmp_path):
    A, sid, _ = install
    import db
    import photostore
    con = A.connect()
    data = os.urandom(3 * REC + 7)
    r = photostore.start(con, sid, "raw", "a.NEF", len(data))
    photostore.append(con, r, 0, io.BytesIO(data[:REC]), REC)
    con.close()
    snap = tmp_path / "mid.db"
    db.snapshot(snap)
    con = A.connect()
    r = con.execute("SELECT * FROM photo_files").fetchone()
    photostore.append(con, r, REC, io.BytesIO(data[REC:2 * REC]), REC)
    first_key = r["fkey"]
    con.close()
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        (tmp_path / ("crm.db" + s_)).unlink(missing_ok=True)
    con = A.connect()
    r = con.execute("SELECT * FROM photo_files").fetchone()
    assert r["received"] == REC and r["fkey"] == first_key
    with pytest.raises(photostore.UploadError, match="resume at 0"):
        photostore.append(con, r, REC, io.BytesIO(data[REC:2 * REC]), REC)
    r = con.execute("SELECT * FROM photo_files").fetchone()
    assert r["received"] == 0 and r["fkey"] != first_key and photostore.path_of(r["id"]).stat().st_size == 0
    assert photostore.append(con, r, 0, io.BytesIO(data), len(data)) == len(data)
    assert photostore.plain_bytes(con.execute("SELECT * FROM photo_files").fetchone()) == data
    con.close()

def test_the_status_check_restarts_a_file_longer_than_its_record(install):
    A, sid, _ = install
    import photostore
    con = A.connect()
    data = os.urandom(2 * REC + 1)
    r = photostore.start(con, sid, "raw", "b.NEF", len(data))
    photostore.append(con, r, 0, io.BytesIO(data[:REC]), REC)
    with open(photostore.path_of(r["id"]), "ab") as f:
        f.write(b"\0" * 100)
    r2 = photostore.start(con, sid, "raw", "b.NEF", len(data))
    assert r2["received"] == 0 and r2["fkey"] != r["fkey"]
    con.close()

def test_a_record_length_larger_than_a_record_is_refused_before_reading(install):
    A, sid, _ = install
    import photostore
    con = A.connect()
    data = os.urandom(1000)
    r = photostore.start(con, sid, "raw", "c.NEF", len(data))
    photostore.append(con, r, 0, io.BytesIO(data), len(data))
    r = con.execute("SELECT * FROM photo_files").fetchone()
    p = photostore.path_of(r["id"])
    raw = bytearray(p.read_bytes())
    raw[4:8] = (0xFFFFFFF0).to_bytes(4, "big")
    p.write_bytes(bytes(raw))
    for fn in (lambda: photostore.plain_bytes(r), lambda: photostore.read_file_plain(p, r["id"], r["fkey"], io.BytesIO()),
               lambda: photostore.rekey(r, str(p) + ".x")):
        with pytest.raises(ValueError, match="wrong length"):
            fn()
    con.close()

class _Bitrot:

    def __init__(self, inner):
        self.inner, self.kind = inner, inner.kind

    def put_stream(self, name, reader, **kw):
        data = bytearray(reader.read())
        if name.endswith(".lap"):
            data[-1] ^= 1
        return self.inner.put_stream(name, io.BytesIO(bytes(data)), **kw)

    def __getattr__(self, k):
        return getattr(self.inner, k)

def test_sealing_and_mirroring_compare_what_landed_by_hash(install, tmp_path):
    A, sid, _ = install
    import photostore
    from vault import keys as VK
    con = A.connect()
    data = os.urandom(5000)
    r = photostore.start(con, sid, "raw", "d.NEF", len(data))
    photostore.append(con, r, 0, io.BytesIO(data), len(data))
    r = con.execute("SELECT * FROM photo_files").fetchone()
    bad = _Bitrot(_store(tmp_path, "vault"))
    with pytest.raises(IOError, match="did not verify"):
        photostore.seal_to_vault(con, [r], [bad], VK.master_pub(con))
    r = con.execute("SELECT * FROM photo_files").fetchone()
    assert r["state"] == "done" and r["fkey"] and photostore.path_of(r["id"]).exists()
    with pytest.raises(IOError, match="did not verify"):
        photostore.mirror_pass(con, _Bitrot(_store(tmp_path, "mirror")))
    assert con.execute("SELECT mirrored FROM photo_files").fetchone()[0] is None
    con.close()

def test_a_rotation_fetches_back_a_photo_only_on_the_backup_location(install, tmp_path, monkeypatch):
    A, sid, _ = install
    import datakey
    import photostore
    import rotate
    from vault import web as VW
    store = _store(tmp_path, "vault")
    cfg = VW.config()
    cfg["primary"] = {"type": "local", "path": store.root}
    VW.write_config(cfg)
    con = A.connect()
    data = os.urandom(REC + 3)
    r = photostore.start(con, sid, "raw", "e.NEF", len(data))
    photostore.append(con, r, 0, io.BytesIO(data), len(data))
    assert photostore.mirror_pass(con, store) == (1, 0, 0)
    old = con.execute("SELECT * FROM photo_files").fetchone()
    con.close()
    photostore.path_of(old["id"]).unlink()
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)
    rotate.rotate(datakey.dk(), TEST_PASSWORD)
    fresh_modules()
    import datakey as K2
    import db
    import photostore as P2
    con = db.connect(key=K2.unwrap_password(TEST_PASSWORD))
    new = con.execute("SELECT * FROM photo_files").fetchone()
    assert new["fkey"] != old["fkey"] and P2.plain_bytes(new) == data
    with pytest.raises(Exception):
        P2.plain_bytes(old)
    con.close()
