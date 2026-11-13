"""Sealing is refused for a folder another session shares, a purge deletes a sealed archive and its key, and a reprint copy is removed once its days run out."""
import json
import os
import sys
from datetime import date, timedelta

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
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")          # the opt-in folder flow
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    assert c.master_secret
    shoot = (date.today() - timedelta(days=10)).isoformat()
    raw = tmp_path / "photos" / "raw" / shoot[:4] / f"{shoot}_lane"
    raw.mkdir(parents=True)
    (raw / "a.nef").write_bytes(b"raw bytes")
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    for _ in range(2):
        con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', ?)", (shoot,))
    con.commit(); con.close()
    return A, c, tmp_path, raw, store


def pick(c, sid, raw):
    c.post(f"/session/{sid}/raw", data={"raw_dir": str(raw) + "/"}, environ_base=E)


def archives(A, sid):
    con = A.connect()
    rows = con.execute("SELECT name, deleted FROM vault_objects WHERE session_id=? AND kind='session'", (sid,)).fetchall()
    con.close()
    return rows


def sealed_at(A, sid):
    con = A.connect()
    r = con.execute("SELECT sealed_at FROM retention WHERE session_id=?", (sid,)).fetchone()
    con.close()
    return r[0] if r else None


def test_seal_is_refused_for_a_folder_shared_with_another_session(env):
    A, c, tmp, raw, store = env
    pick(c, 1, raw)
    pick(c, 2, raw)
    c.post("/session/1/seal", environ_base=E)
    assert (raw / "a.nef").exists()
    assert sealed_at(A, 1) is None and not archives(A, 1)


def test_seal_then_purge_deletes_the_archive_and_its_key(env):
    A, c, tmp, raw, store = env
    pick(c, 1, raw)
    c.post("/session/1/seal", environ_base=E)
    assert not raw.exists() and sealed_at(A, 1) == date.today().isoformat()
    names = [n for n, d in archives(A, 1) if d is None]
    assert names
    assert (store / names[0]).is_file()
    with A.app.app_context():
        A.app.config["retention_purge_now"](1)
    assert all(d for _, d in archives(A, 1))
    assert not (store / names[0]).exists()
    con = A.connect()
    assert not con.execute("SELECT 1 FROM vault_keys WHERE session_id=1").fetchone()
    assert con.execute("SELECT purged_at FROM retention WHERE session_id=1").fetchone()[0] == date.today().isoformat()
    con.close()


def test_reprint_copy_is_removed_after_its_days(env):
    A, c, tmp, raw, store = env
    pick(c, 1, raw)
    c.post("/session/1/seal", environ_base=E)
    assert not raw.exists()
    c.post("/session/1/unseal", data={"master": c.master_secret}, environ_base=E)
    con = A.connect()
    copy = con.execute("SELECT unsealed_dir FROM retention WHERE session_id=1").fetchone()[0]
    con.close()
    assert copy and any(p.name == "a.nef" for p in (tmp / "photos" / "unsealed").rglob("*"))
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert os.path.isdir(copy)                                           # still within its days
    con = A.connect()
    con.execute("UPDATE retention SET unsealed_at=? WHERE session_id=1", ((date.today() - timedelta(days=8)).isoformat(),))
    con.commit(); con.close()
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not os.path.exists(copy)
    con = A.connect()
    assert tuple(con.execute("SELECT unsealed_dir, unsealed_at FROM retention WHERE session_id=1").fetchone()) == ("", None)
    con.close()


def test_uploaded_photos_seal_into_the_vault_and_open_with_the_master_key(env):
    """Uploaded RAWs and exports go into the session's vault archive (decrypted only as they stream into it), the
    upload copies are removed once the archive verifies, and the master key opens them again for a reprint."""
    A, c, tmp, raw, store = env
    import photostore
    con = A.connect()
    for kind, name, body in (("raw", "a.NEF", b"R" * 3000), ("export", "a.jpg", b"J" * 2000)):
        row = photostore.start(con, 2, kind, name, len(body))
        photostore.append(con, row, 0, __import__("io").BytesIO(body), len(body))
    con.close()
    r = c.post("/session/2/seal", data={"back": "/"}, environ_base=E)
    assert r.status_code == 302 and sealed_at(A, 2)
    con = A.connect()                                # one format: the same encrypted files, now in the vault
    assert con.execute("SELECT COUNT(*) FROM vault_objects WHERE session_id=2 AND kind='photo'").fetchone()[0] == 2
    assert {r_["state"] for r_ in photostore.files(con, 2)} == {"sealed"}
    assert not [r_ for r_ in photostore.files(con, 2) if r_["fkey"]]          # the app no longer holds their keys
    con.close()
    assert not list((tmp / "photos" / "sealed").glob("*/*.lap"))
    c.post("/session/2/unseal", data={"master": "AGE-SECRET-KEY-1WRONG", "back": "/"}, environ_base=E)   # refused at once
    con = A.connect()
    assert not con.execute("SELECT unsealed_dir FROM retention WHERE session_id=2 AND unsealed_dir != ''").fetchone()
    con.close()
    c.post("/session/2/unseal", data={"master": c.master_secret, "back": "/"}, environ_base=E)
    con = A.connect()
    d = con.execute("SELECT unsealed_dir FROM retention WHERE session_id=2").fetchone()[0]
    con.close()
    from pathlib import Path
    assert (Path(d) / "raw" / "a.NEF").read_bytes() == b"R" * 3000 and (Path(d) / "export" / "a.jpg").read_bytes() == b"J" * 2000


def test_archives_and_photos_open_after_restoring_an_older_database(env):
    """E53: a restore to a backup older than an archive (or an upload) loses the database's record of its key. The key
    sealed beside it opens it with the master key alone."""
    import io
    import json
    import pyrage
    A, c, tmp, raw, store = env
    import photostore
    from vault import keys as VK, seal as VS, backends as VB
    con = A.connect()
    mp = VK.master_pub(con)
    row = photostore.start(con, 2, "raw", "late.NEF", 5000, master_pub=mp, label="Lane - family")
    photostore.append(con, row, 0, io.BytesIO(b"L" * 5000), 5000)
    con.close()
    pick(c, 1, raw)
    c.post("/session/1/seal", data={"back": "/"}, environ_base=E)
    name = [a for a in archives(A, 1) if not a[1]][0][0]
    con = A.connect()                                                   # the older database: no record of either
    con.execute("DELETE FROM vault_objects"); con.execute("DELETE FROM vault_keys"); con.execute("DELETE FROM photo_files")
    con.commit()
    backend = VB.LocalBackend(str(store))
    found = VS.orphans(con, c.master_secret, backend)
    con.close()
    assert [m["archive"] for m in found] == [name] and "Lane" in found[0]["label"]
    out = tmp / "reopened"
    VS.open_orphan(name, c.master_secret, backend, str(out))
    assert any(out.rglob("a.nef"))
    m = json.loads(pyrage.decrypt(photostore.key_path(row["id"]).read_bytes(), [VK.parse_secret(c.master_secret)]))
    buf = io.BytesIO()
    photostore.read_file_plain(photostore.path_of(row["id"]), m["id"], m["fkey"], buf)
    assert buf.getvalue() == b"L" * 5000 and m["name"] == "late.NEF"


def test_sealed_photos_reprint_one_file_and_purge_from_the_vault(env):
    import io
    A, c, tmp, raw, store = env
    import photostore
    con = A.connect()
    for name in ("one.NEF", "two.NEF"):
        row = photostore.start(con, 2, "raw", name, 3000)
        photostore.append(con, row, 0, io.BytesIO((name.encode() * 500)[:3000]), 3000)
    con.close()
    c.post("/session/2/seal", data={"back": "/"}, environ_base=E)
    c.post("/session/2/unseal", data={"master": c.master_secret, "only": "two", "back": "/"}, environ_base=E)
    con = A.connect()
    d = con.execute("SELECT unsealed_dir FROM retention WHERE session_id=2").fetchone()[0]
    con.close()
    from pathlib import Path
    assert [p.name for p in Path(d).rglob("*.NEF")] == ["two.NEF"]          # just the one asked for
    with A.app.app_context():
        A.app.config["retention_purge_now"](2)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM vault_objects WHERE session_id=2 AND deleted IS NULL").fetchone()
    assert not photostore.files(con, 2)
    con.close()
    assert not list(store.rglob("*.lap"))
