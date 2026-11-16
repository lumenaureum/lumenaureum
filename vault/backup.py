import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime

from . import keys as K, seal as S, web as W

KEEP = 30
MANUAL_KEEP = 10

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datakey
import db as D

import threading
_ONE_AT_A_TIME = threading.Lock()
EXTRAS = []
BUNDLE_FILES = ("crm.db", "keyring.json", "settings.json", "storage.json", "deletions.json", "doc-numbers.json")

def sealed_backup(db_path, faces_dir=None, kind="db", keep=KEEP):

    with _ONE_AT_A_TIME:
        return _sealed_backup(db_path, faces_dir, kind, keep)

def _sealed_backup(db_path, faces_dir, kind, keep):
    con = D.connect(db_path)
    try:
        K.init(con)
        if not K.master_pub(con):
            raise RuntimeError("no master key")
        bks = W.store_backends()
        tmpd = tempfile.mkdtemp(prefix="crm-seal-")
        try:
            snap = os.path.join(tmpd, "crm.db")
            D.snapshot(snap, src_path=db_path)
            bundle = os.path.join(tmpd, "backup.tar")
            with tarfile.open(bundle, "w") as tf:
                tf.add(snap, arcname="crm.db")
                tf.add(str(datakey.KEYRING), arcname="keyring.json")
                for fn in EXTRAS:
                    for name_, data_ in fn() or []:
                        if name_ in BUNDLE_FILES[2:] and data_:
                            p_ = os.path.join(tmpd, name_)
                            with open(p_, "wb") as fh_:
                                fh_.write(data_)
                            tf.add(p_, arcname=name_)
            names = [S.seal_file(con, bundle, bks, kind=kind, prefix="db")]
            if faces_dir and os.path.isdir(faces_dir):
                ft = os.path.join(tmpd, "faces.tar")
                with tarfile.open(ft, "w") as tf:
                    tf.add(faces_dir, arcname="faces")
                names.append(S.seal_file(con, ft, bks, kind={"db": "faces", "manual": "manual-faces", "upgrade": "upgrade-faces"}.get(kind, kind), prefix="db"))
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        prune(con, bks, keep)
        from . import sync as SY
        try:
            SY.run(con, bks[0])
            err = ""
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
        con.execute("INSERT INTO settings (key, value) VALUES ('backup_second_error', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (err,))
        con.commit()
        return names
    finally:
        con.close()

def prune(con, bks, keep=KEEP):

    by = {b.kind: b for b in bks}
    now = datetime.now()
    for kind, n_keep in (("db", keep), ("faces", keep), ("manual", MANUAL_KEEP), ("manual-faces", MANUAL_KEEP),
                         ("upgrade", 3), ("upgrade-faces", 3)):
        names = [r[0] for r in con.execute("""SELECT name FROM vault_objects WHERE kind=? AND deleted IS NULL
                                              GROUP BY name ORDER BY MAX(id) DESC""", (kind,))]
        for name in names[n_keep:]:
            _drop(con, by, name, now)
    for (sid_, orphaned) in con.execute("SELECT session_id, orphaned FROM vault_keys WHERE orphaned IS NOT NULL").fetchall():
        if (now - datetime.fromisoformat(orphaned[:19])).days > 30:
            try:
                S.destroy_session(con, sid_, bks)
            except IOError:
                pass
    for name, created in con.execute("""SELECT name, MIN(created) FROM vault_objects
                                        WHERE kind IN ('reset','manual','manual-faces','upgrade','upgrade-faces','found') AND deleted IS NULL
                                        GROUP BY name""").fetchall():
        if (now - datetime.fromisoformat(created)).days > 30:
            _drop(con, by, name, now)
    _sweep_unlisted(con, bks)
    con.commit()

def _sweep_unlisted(con, bks):

    known = {r[0] for r in con.execute("SELECT DISTINCT name FROM vault_objects")}
    now = datetime.now().isoformat(timespec="seconds")
    for b in bks:
        try:
            names = b.list("db/")
        except Exception:
            continue
        for n in names:
            if n not in known:
                try:
                    size = b.stat(n)
                    h_ = hashlib.sha256()

                    class _W:
                        def write(self, d):
                            h_.update(d)
                            return len(d)
                    b.get_stream(n, _W())
                    sha = h_.hexdigest()
                except Exception:
                    continue
                con.execute("INSERT INTO vault_objects (session_id, kind, name, backend, size, sha256, created) "
                            "VALUES (NULL, 'found', ?, ?, ?, ?, ?)", (n, b.kind, size, sha, now))

def _drop(con, by, name, now):
    rows = con.execute("SELECT id, backend FROM vault_objects WHERE name=? AND deleted IS NULL", (name,)).fetchall()
    if any(b not in by for _, b in rows):
        return
    for oid, backend in rows:
        by[backend].delete_verified(name)
        con.execute("UPDATE vault_objects SET deleted=? WHERE id=?", (now.isoformat(timespec="seconds"), oid))

def recorded_sha(con, name, backend_kind=None):

    try:
        r = con.execute("SELECT sha256 FROM vault_objects WHERE name=? AND deleted IS NULL" + (" AND backend=?" if backend_kind else "")
                        + " ORDER BY id DESC LIMIT 1", (name, backend_kind) if backend_kind else (name,)).fetchone()
    except Exception:
        return None
    return r[0] if r and r[0] else None

def restore(name, master_secret, dest_dir, backend=None, settings_only=False, expected_sha=None):

    if os.path.exists(dest_dir):
        raise FileExistsError(dest_dir)
    bk = backend or W.store_backends()[0]
    os.makedirs(dest_dir, mode=0o700)
    raw = os.path.join(dest_dir, "sealed.out")
    try:
        S.open_file(name, master_secret, bk, raw, expected_sha=expected_sha)
        if not tarfile.is_tarfile(raw):
            raise ValueError("this is not a Lumen Aureum backup")
        with tarfile.open(raw) as tf:
            for m in tf.getmembers():
                if m.name not in BUNDLE_FILES or not m.isfile():
                    raise ValueError(f"unexpected entry in backup: {m.name}")
            tf.extractall(dest_dir, filter="data")
        os.remove(raw)
        key = datakey.unwrap_master(master_secret, ring_path=os.path.join(dest_dir, "keyring.json"))
        con = D.connect(os.path.join(dest_dir, "crm.db"), key=key)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("the restored database failed its integrity check")
        finally:
            con.close()
        for f_ in ("deletions.json", "doc-numbers.json"):
            src_ = os.path.join(dest_dir, f_)
            if os.path.exists(src_):
                os.replace(src_, os.path.join(dest_dir, f_[:-5] + ".restored.json"))
        if settings_only:
            if not os.path.exists(os.path.join(dest_dir, "settings.json")):
                raise ValueError("this backup has no settings file (it was made before settings were included)")
            for f_ in os.listdir(dest_dir):
                if f_ != "settings.json":
                    os.remove(os.path.join(dest_dir, f_))
        return dest_dir
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
