"""Rotating the data key (python -m vault.cli rotate, with the app stopped): for a password or keyring that may have
leaked. Everything is re-encrypted under a new key into staged files first (database, faces, uploaded photos, storage settings, keyring); the
database copy is opened and checked; only then are they swapped in, keyring last. A marker file records that a swap is under way: if the run
is interrupted, running the command again finishes it, and the app refuses to start until it has.
Older backups keep their own keyring, so the master key still opens them."""
import json
import os
import secrets
from pathlib import Path

import datakey
import db as D
from db import ident as db_ident
from vault import web as VW

MARK = D.DB_PATH.parent / "rotate.pending"
RUNNING = D.DB_PATH.parent / ".running"


def hold_running():
    """The app holds a shared lock on this file for as long as it runs."""
    import fcntl
    RUNNING.parent.mkdir(parents=True, exist_ok=True)
    f = open(RUNNING, "a")
    fcntl.flock(f, fcntl.LOCK_SH)
    return f


def app_running():
    import fcntl
    if not RUNNING.exists():
        return False
    with open(RUNNING, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False
FACES = D.DB_PATH.parent / "faces"
STAGED = ".rot"


def pending():
    return MARK.exists()


def _counts(con):
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: con.execute("SELECT COUNT(*) FROM " + db_ident(t)).fetchone()[0] for t in tables}


def stage(old, new, password):
    db_new = Path(str(D.DB_PATH) + STAGED)
    D.remove_with_sidecars(db_new)
    con = D.connect(key=old)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = _counts(con)
        con.execute("ATTACH DATABASE ? AS rot KEY ?", (str(db_new), D.key_literal(datakey.db_key_hex(new))))
        con.execute("SELECT sqlcipher_export('rot')")
        D.set_user_version(con, "rot", con.execute("PRAGMA user_version").fetchone()[0])
        con.execute("DETACH DATABASE rot")
    finally:
        con.close()
    chk = D.connect(db_new, key=new)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or _counts(chk) != before:
            raise RuntimeError("the re-encrypted database did not check out; nothing was changed")
        chk.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        chk.execute("PRAGMA journal_mode = DELETE")
    finally:
        chk.close()
    for f in (FACES.glob("*.jpg") if FACES.is_dir() else []):
        raw = f.read_bytes()        # a face still plain from an interrupted upgrade is encrypted now as well
        data = datakey.open_bytes(raw, f"face:{f.stem}", label="faces", key=old) if raw.startswith(datakey.FILE_MAGIC) else raw
        datakey.write_sealed(Path(str(f) + STAGED), data, f"face:{f.stem}", label="faces", key=new)
    for name_, aad_ in (("deletions.enc", "deletions"), ("doc-numbers.enc", "numbers")):     # side files outside the database
        side = D.DB_PATH.parent / name_
        if side.exists():
            plain = datakey.open_bytes(side.read_bytes(), aad_, label=aad_, key=old)
            datakey.write_sealed(Path(str(side) + STAGED), plain, aad_, label=aad_, key=new)
    if VW.CFG_PATH.exists():
        raw = VW.CFG_PATH.read_bytes()
        plain = datakey.open_bytes(raw, "storage", label="storage", key=old) if raw.startswith(datakey.FILE_MAGIC) else raw
        datakey.write_sealed(Path(str(VW.CFG_PATH) + STAGED), plain, "storage", label="storage", key=new)
    _stage_photos(db_new, new)
    datakey.new_ring_for(new, password, datakey.master_pub(), path=Path(str(datakey.KEYRING) + STAGED))
    MARK.write_text("1")


def _stage_photos(db_new, new):
    """Every uploaded photo gets a fresh key and is rewritten beside itself (staged); the new keys go into the staged
    database, and each photo's mirror copy is marked for refreshing. Swapped in by finish()."""
    import photostore
    con = D.connect(db_new, key=new)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='photo_files'").fetchone():
            return
        photostore.ensure_trail(con)
        for r in con.execute("SELECT * FROM photo_files").fetchall():
            src = photostore.path_of(r["id"])
            if not src.exists() or r["records"] == 0:
                continue
            fkey, ct_len = photostore.rekey(r, Path(str(src) + STAGED))
            # the trail gains a tag made with the key being retired: a database restored from before this rotation
            # holds that key, so relink-photos can check the new key record really came from here
            trail = json.dumps(photostore.key_trail(r) + [photostore.trail_tag(r["fkey"], r["id"])])
            con.execute("UPDATE photo_files SET fkey=?, ct_len=?, mirrored=NULL, key_trail=? WHERE id=?",
                        (fkey, ct_len, trail, r["id"]))
            kp = photostore.key_path(r["id"])
            if kp.exists():                          # the sealed key beside it follows (staged, swapped in by finish)
                lab = con.execute("""SELECT h.name, s.type, s.shoot_date FROM sessions s JOIN households h ON h.id=s.household_id
                                     WHERE s.id=?""", (r["session_id"],)).fetchone()
                photostore.write_key(dict(dict(r), fkey=fkey, key_trail=trail), datakey.master_pub(),
                                     " - ".join(str(v) for v in lab if v) if lab else "", path=Path(str(kp) + STAGED))
        con.commit()
    finally:
        con.close()


def finish():
    """Swap every staged file in; the keyring goes last. Safe to run again after an interruption."""
    db_new = Path(str(D.DB_PATH) + STAGED)
    if db_new.exists():
        D.remove_with_sidecars(D.DB_PATH)
        os.replace(db_new, D.DB_PATH)
    for f in (FACES.glob("*.jpg" + STAGED) if FACES.is_dir() else []):
        os.replace(f, str(f)[:-len(STAGED)])
    import photostore
    for pat in ("*/*.lap" + STAGED, "*/*.key" + STAGED):
        for f in (photostore.ROOT.glob(pat) if photostore.ROOT.is_dir() else []):
            os.replace(f, str(f)[:-len(STAGED)])
    for name_ in ("deletions.enc", "doc-numbers.enc"):
        side_new = Path(str(D.DB_PATH.parent / name_) + STAGED)
        if side_new.exists():
            os.replace(side_new, D.DB_PATH.parent / name_)
    cfg_new = Path(str(VW.CFG_PATH) + STAGED)
    if cfg_new.exists():
        os.replace(cfg_new, VW.CFG_PATH)
    ring_new = Path(str(datakey.KEYRING) + STAGED)
    if ring_new.exists():
        os.replace(ring_new, datakey.KEYRING)
    MARK.unlink(missing_ok=True)


class AppRunning(Exception):
    pass


def hold_exclusive():
    """The app's start lock, held exclusively for the whole rotation: the app can't start (and write) halfway through."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        RUNNING.parent.mkdir(parents=True, exist_ok=True)
        f = open(RUNNING, "a")
        try:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AppRunning("Lumen Aureum is starting. Stop it first.") from None
            yield
        finally:
            f.close()
    return held()


def rotate(old, password):
    if app_running():
        raise AppRunning("Lumen Aureum is running. Stop it first: rotating under a running app would lose its writes.")
    with hold_exclusive():
        if pending():
            finish()
            return
        new = secrets.token_bytes(32)
        stage(old, new, password)
        finish()
        datakey.lock()
