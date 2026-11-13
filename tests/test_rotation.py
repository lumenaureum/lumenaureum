"""Rotating the data key: refused while the app runs; afterwards the old key opens nothing, the password and the master
key both open the new one, and the data is intact. A rotation interrupted before the swap blocks start until finished."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, unlock_new_install  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Harlow Family')")
    con.commit(); con.close()
    return A, secret


def test_rotation(install, monkeypatch):
    A, secret = install
    import datakey
    import rotate
    old = datakey.dk()
    with pytest.raises(rotate.AppRunning):
        rotate.rotate(old, TEST_PASSWORD)                    # refused while the app runs
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)   # the test process is the app
    rotate.rotate(old, TEST_PASSWORD)
    fresh_modules()
    import datakey as K2
    import db
    with pytest.raises(Exception):
        db.connect(key=old).execute("SELECT COUNT(*) FROM households").fetchone()
    new = K2.unwrap_password(TEST_PASSWORD)
    assert new and new != old and K2.unwrap_master(secret) == new
    assert db.connect(key=new).execute("SELECT name FROM households").fetchone()[0] == "Harlow Family"


def test_interrupted_rotation_blocks_start_until_finished(install):
    A, secret = install
    import datakey
    import rotate
    import secrets
    rotate.stage(datakey.dk(), secrets.token_bytes(32), TEST_PASSWORD)    # crash before the swap
    fresh_modules()
    with pytest.raises(SystemExit):
        import app  # noqa: F401
    fresh_modules()
    import rotate as R
    R.finish()
    fresh_modules()
    import app as A2  # noqa: F401
    import datakey as K2
    assert K2.unwrap_password(TEST_PASSWORD)


def test_rotation_rekeys_uploaded_photos(install, monkeypatch):
    """After a rotation every uploaded photo (finished or not) has a fresh key and a rewritten file: the old keys open
    nothing, the new ones open the same photo, and an unfinished upload still resumes."""
    import io
    A, secret = install
    import datakey
    import photostore
    import rotate
    con = A.connect()
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (1, 'portrait', 'shot')").lastrowid
    con.commit()
    full = b"F" * ((1 << 20) + 9)
    r1 = photostore.start(con, sid, "raw", "full.NEF", len(full))
    photostore.append(con, r1, 0, io.BytesIO(full), len(full))
    part = b"P" * ((2 << 20) + 3)
    r2 = photostore.start(con, sid, "raw", "part.NEF", len(part))
    photostore.append(con, r2, 0, io.BytesIO(part[:1 << 20]), 1 << 20)
    old_rows = {r["id"]: r for r in con.execute("SELECT * FROM photo_files").fetchall()}
    con.close()
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)   # the test process is the app
    rotate.rotate(datakey.dk(), TEST_PASSWORD)
    fresh_modules()
    import datakey as K2
    import db
    import photostore as P2
    con = db.connect(key=K2.unwrap_password(TEST_PASSWORD))
    new_rows = {r["id"]: r for r in con.execute("SELECT * FROM photo_files").fetchall()}
    for fid, old in old_rows.items():
        assert new_rows[fid]["fkey"] != old["fkey"] and new_rows[fid]["mirrored"] is None
        if old["state"] == "done":
            with pytest.raises(Exception):
                P2.plain_bytes(old)                                     # the old key opens nothing now
            assert P2.plain_bytes(new_rows[fid]) == full
    r2n = new_rows[r2["id"]]
    assert P2.append(con, r2n, 1 << 20, io.BytesIO(part[1 << 20:]), len(part) - (1 << 20)) == len(part)
    assert P2.plain_bytes(con.execute("SELECT * FROM photo_files WHERE id=?", (r2["id"],)).fetchone()) == part
    con.close()
