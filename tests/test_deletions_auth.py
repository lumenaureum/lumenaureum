import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

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
    hid = con.execute("INSERT INTO households (name) VALUES (?)", (name,)).lastrowid
    con.commit()
    created = con.execute("SELECT created FROM households WHERE id=?", (hid,)).fetchone()[0]
    con.close()
    return hid, created

def _here(A, hid):
    con = A.connect()
    try:
        return bool(con.execute("SELECT 1 FROM households WHERE id=?", (hid,)).fetchone())
    finally:
        con.close()

def _audit(A):
    con = A.connect()
    try:
        return [(r[0], r[1]) for r in con.execute("SELECT household_id, action FROM audit")]
    finally:
        con.close()

def _restore(A, snap):
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)

def test_genuine_deletion_is_signed_and_survives_a_restore(install, tmp_path):
    A, c = install
    import db
    hid, _ = _client(A, "Harlow Family")
    snap = tmp_path / "before.db"
    db.snapshot(snap)
    c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    items = json.loads(A.DELETIONS_JSON.read_text())
    assert len(items) == 1 and items[0]["id"] == hid and len(items[0]["mac"]) == 64
    _restore(A, snap)
    assert _here(A, hid)
    assert A.reapply_deletions() == 1 and not _here(A, hid)

def test_restore_of_a_backup_from_before_the_key_was_kept(install, tmp_path):

    A, c = install
    import db
    hid, _ = _client(A, "Harlow Family")
    snap = tmp_path / "before.db"
    db.snapshot(snap)
    con = db.connect(snap)
    con.execute("DROP TABLE side_keys"); con.commit(); con.close()
    c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    _restore(A, snap)
    assert A.reapply_deletions() == 1 and not _here(A, hid)

def test_deletions_survive_a_rotation_then_a_restore_from_before_it(install, tmp_path, monkeypatch):

    A, c = install
    import datakey
    import db
    import rotate
    hid, created = _client(A, "Harlow Family")
    old_key = datakey.dk()
    snap, ring = tmp_path / "before.db", tmp_path / "before-keyring.json"
    db.snapshot(snap)
    shutil.copyfile(datakey.KEYRING, ring)
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)
    rotate.rotate(old_key, TEST_PASSWORD)
    new_key = datakey.unwrap_password(TEST_PASSWORD)
    assert new_key != old_key
    datakey.set_unlocked(new_key)
    with A.app.test_request_context():
        A._delete_client_rows(hid, created, [])
        A.db().commit()
    assert not _here(A, hid)
    _restore(A, snap)
    shutil.copyfile(ring, datakey.KEYRING)
    datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD))
    assert datakey.dk() == old_key and _here(A, hid)
    assert A.reapply_deletions() == 1 and not _here(A, hid)

def test_forged_entry_deletes_nothing_and_is_reported(install):
    A, c = install
    hid, created = _client(A, "Harlow Family")
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=hid, created=created, at="2026-01-01 00:00:00", mac="0" * 64)]))
    assert A.reapply_deletions() == 0
    assert _here(A, hid)
    assert any("did not verify" in a for _, a in _audit(A))
    assert json.loads(A.DELETIONS_JSON.read_text()) == []
    assert json.loads(A.DELETIONS_ASIDE.read_text())[0]["id"] == hid
    assert A.reapply_deletions() == 0 and _here(A, hid)
    assert sum("did not verify" in a for _, a in _audit(A)) == 1

def test_a_mac_from_another_install_does_not_verify(install):
    A, c = install
    import hashlib
    import hmac
    hid, created = _client(A, "Harlow Family")
    d_ = dict(id=hid, created=created)
    mac = hmac.new(b"k" * 32, ("lumen-aureum deletion|" + json.dumps([hid, created])).encode(), hashlib.sha256).hexdigest()
    A.DELETIONS_JSON.write_text(json.dumps([dict(d_, mac=mac)]))
    assert A.reapply_deletions() == 0 and _here(A, hid)

def test_unsigned_entries_from_1_0_0(install):

    A, c = install
    hid, created = _client(A, "Harlow Family")
    gone = dict(id=hid + 100, created="2025-01-01 10:00:00", at="2025-06-01 10:00:00")
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=hid, created=created, at="2026-01-01 00:00:00"), gone]))
    assert A.reapply_deletions() == 0
    assert _here(A, hid)
    assert any(h == hid and "Not deleted" in a for h, a in _audit(A))
    live = json.loads(A.DELETIONS_JSON.read_text())
    assert [d_["id"] for d_ in live] == [gone["id"]] and "mac" in live[0]
    assert [d_["id"] for d_ in json.loads(A.DELETIONS_ASIDE.read_text())] == [hid]

def test_new_deletion_keeps_unverified_entries_unapplied(install):
    A, c = install
    keep, kc = _client(A, "Keep Family")
    drop, _ = _client(A, "Drop Family")
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=keep, created=kc, mac="f" * 64)]))
    c.post(f"/client/{drop}/delete", data={"confirm": "DELETE"}, environ_base=E)
    assert not _here(A, drop) and _here(A, keep)
    assert A.reapply_deletions() == 0 and _here(A, keep)

def _photo(A, c):
    import datakey
    import photostore
    con = A.connect()
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'shot')",
                      (_client(A, "Harlow Family")[0],)).lastrowid
    con.commit()
    body = b"F" * ((1 << 20) + 9)
    r = photostore.start(con, sid, "raw", "full.NEF", len(body), master_pub=datakey.master_pub())
    photostore.append(con, r, 0, io.BytesIO(body), len(body))
    row = con.execute("SELECT * FROM photo_files WHERE id=?", (r["id"],)).fetchone()
    con.close()
    return row, body

def _relink(c, monkeypatch):
    from vault import cli
    out = io.StringIO()
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": c.master_secret)
    with redirect_stdout(out):
        assert cli.main(["relink-photos"]) == 0
    return out.getvalue()

def test_relink_takes_a_key_from_a_rotation_after_the_backup(install, tmp_path, monkeypatch):
    A, c = install
    import datakey
    import db
    import photostore
    import rotate
    row, body = _photo(A, c)
    old_key = datakey.dk()
    snap, ring = tmp_path / "before.db", tmp_path / "before-keyring.json"
    db.snapshot(snap)
    shutil.copyfile(datakey.KEYRING, ring)
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)
    for _ in range(2):
        rotate.rotate(datakey.unwrap_password(TEST_PASSWORD), TEST_PASSWORD)
    _restore(A, snap)
    shutil.copyfile(ring, datakey.KEYRING)
    datakey.set_unlocked(old_key)
    with pytest.raises(Exception):
        photostore.plain_bytes(row)
    out = _relink(c, monkeypatch)
    assert "1 photo key(s) restored" in out
    con = A.connect()
    assert photostore.plain_bytes(con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()) == body
    con.close()

def test_relink_refuses_a_forged_photo_and_key_pair(install, monkeypatch):

    A, c = install
    import datakey
    import photostore
    row, body = _photo(A, c)
    lap = photostore.path_of(row["id"])
    tmp = lap.with_name(lap.name + ".forged")
    fkey, ct_len = photostore.rekey(row, tmp)
    os.replace(tmp, lap)
    photostore.write_key(dict(dict(row), fkey=fkey, key_trail=json.dumps(["0" * 64])), datakey.master_pub())
    out = _relink(c, monkeypatch)
    assert "0 photo key(s) restored" in out and "did not trace back" in out
    con = A.connect()
    assert con.execute("SELECT fkey FROM photo_files WHERE id=?", (row["id"],)).fetchone()[0] == row["fkey"]
    con.close()
