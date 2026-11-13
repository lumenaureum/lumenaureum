"""Sealing: folders -> tar -> age -> backend, streamed in one pass (no plaintext or ciphertext staged on disk).
Opening needs a private identity, which only comes from the master secret typed in at the time."""
import os
import secrets
import tarfile
import threading
from datetime import datetime

import pyrage

from . import keys


def new_name(prefix):
    return f"{prefix}/{secrets.token_hex(16)}.age"


def _pipe_thread(fn):
    """Run fn(writer) in a thread; return the read end as a binary file object and a thread whose errors are kept."""
    r, w = os.pipe()
    rf, wf = os.fdopen(r, "rb", buffering=1024 * 1024), os.fdopen(w, "wb", buffering=1024 * 1024)
    box = {}

    def run():
        try:
            fn(wf)
        except BaseException as e:  # surfaced by the caller after the stream ends
            box["err"] = e
        finally:
            try:
                wf.close()
            except OSError:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return rf, t, box


def tar_reader(paths, members=()):
    """A tar of folders, plus members given as (name in the archive, size, open()) - an uploaded photo decrypted as it
    streams, so no readable copy is written on the way into the vault."""
    def write(wf):
        with tarfile.open(fileobj=wf, mode="w|") as tf:
            for p in paths:
                tf.add(p, arcname=os.path.basename(p.rstrip("/")))
            for name, size, opener in members:
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = size, 0o600, int(datetime.now().timestamp())
                tf.addfile(ti, opener())
    return _pipe_thread(write)


def encrypt_reader(reader, recipients):
    return _pipe_thread(lambda wf: pyrage.encrypt_io(reader, wf, recipients))


def put_sealed(backends, name, make_reader, recipients):
    """Stream make_reader() encrypted to `recipients` into the first backend, then copy the ciphertext to the rest and
    check every copy has the same hash. Returns [(backend, size, sha256)]."""
    src, t1, b1 = make_reader()
    enc, t2, b2 = encrypt_reader(src, recipients)
    failed = None
    try:
        size, sha = backends[0].put_stream(name, enc)
    except BaseException as e:
        failed = e
    finally:
        for f in (enc, src):     # closing both ends unblocks the tar/encrypt threads, so nothing leaks holding RAW files
            try:
                f.close()
            except Exception:
                pass
    t1.join(timeout=60); t2.join(timeout=60)
    errs = [b["err"] for b in (b1, b2) if "err" in b]
    if failed or errs:
        backends[0].delete(name)
        raise failed or errs[0]
    if backends[0].stat(name) != size:                  # bytes that LANDED, not just bytes sent
        backends[0].delete(name)
        raise IOError(f"{backends[0].kind}: stored size does not match")
    done = [(backends[0], size, sha)]
    for bk in backends[1:]:
        rd, t3, b3 = _pipe_thread(lambda wf: backends[0].get_stream(name, wf))
        try:
            s2, h2 = bk.put_stream(name, rd)
        except BaseException:
            s2, h2 = None, None
        finally:
            rd.close()
        t3.join(timeout=60)
        if "err" in b3 or (s2, h2) != (size, sha) or bk.stat(name) != size:
            for b_ in [bk] + [d[0] for d in done]:     # all or nothing: no unrecorded orphans
                try:
                    b_.delete(name)
                except Exception:
                    pass
            raise IOError(f"copy to {bk.kind} did not verify")
        done.append((bk, s2, h2))
    return done


def record(con, session_id, kind, name, copies):
    now = datetime.now().isoformat(timespec="seconds")
    for bk, size, sha in copies:
        con.execute("INSERT INTO vault_objects (session_id, kind, name, backend, size, sha256, created) VALUES (?,?,?,?,?,?,?)",
                    (session_id, kind, name, bk.kind, size, sha, now))
    con.commit()


def seal_session(con, sid, folders, backends, members=()):
    """Seal a session's folders (and uploaded photos, as members) under the session key. The caller deletes what
    was sealed only after this returns."""
    folders = [f for f in folders if f and os.path.isdir(f)]
    members = list(members)
    if not folders and not members:
        raise ValueError("nothing to seal")
    pub = keys.session_pub(con, sid)
    name = new_name("s")
    copies = put_sealed(backends, name, lambda: tar_reader(folders, members), [keys.recipient(pub)])
    record(con, sid, "session", name, copies)
    write_key_beside(con, sid, name, backends)
    return name


def write_key_beside(con, sid, name, backends):
    """Beside each archive, the session's key (and what the archive is), sealed to the master key: if the database is
    ever restored from a backup older than the archive, the master key alone still opens it (vault.cli orphans).
    Recorded with the session, so deleting the session's archives deletes this too."""
    import io
    import json
    mp = keys.master_pub(con)
    w = con.execute("SELECT wrapped FROM vault_keys WHERE session_id=?", (sid,)).fetchone()
    if not mp or not w:
        return
    try:
        lab = con.execute("""SELECT h.name, s.type, s.shoot_date FROM sessions s JOIN households h ON h.id=s.household_id
                             WHERE s.id=?""", (sid,)).fetchone()
        label = " - ".join(str(v) for v in lab if v) if lab else f"session {sid}"
    except Exception:  # noqa: BLE001 - the vault works without the app's tables
        label = f"session {sid}"
    body = json.dumps(dict(v=1, archive=name, session=sid, label=label, wrapped=w[0],
                           made=datetime.now().isoformat(timespec="seconds"))).encode()
    copies = put_sealed(backends, name + ".key", lambda: (io.BytesIO(body), _noop(), {}), [keys.recipient(mp)])
    record(con, sid, "session-key", name + ".key", copies)


def orphans(con, master_secret, backend):
    """Archives in the vault that this database doesn't know (a restore to an older backup), with what they are."""
    import io
    import json
    ident = keys.parse_secret(master_secret)
    known = {r[0] for r in con.execute("SELECT name FROM vault_objects")} if con is not None else set()
    out = []
    for kname in backend.list("s/"):
        if not kname.endswith(".age.key") or kname[:-4] in known:
            continue
        buf = io.BytesIO()
        backend.get_stream(kname, buf)
        meta = json.loads(pyrage.decrypt(buf.getvalue(), [ident]))
        out.append(meta)
    return out


def open_orphan(archive, master_secret, backend, dest_dir):
    """Open an archive the database doesn't know, with the master key and the key stored beside it."""
    import base64
    import io
    import json
    ident = keys.parse_secret(master_secret)
    buf = io.BytesIO()
    backend.get_stream(archive + ".key", buf)
    meta = json.loads(pyrage.decrypt(buf.getvalue(), [ident]))
    from pyrage import x25519
    sess = x25519.Identity.from_str(pyrage.decrypt(base64.b64decode(meta["wrapped"]), [ident]).decode())
    os.makedirs(dest_dir, exist_ok=True)
    enc, t1, b1 = _pipe_thread(lambda wf: backend.get_stream(archive, wf))
    dec, t2, b2 = _pipe_thread(lambda wf: pyrage.decrypt_io(enc, wf, [sess]))
    try:
        with tarfile.open(fileobj=dec, mode="r|") as tf:
            tf.extractall(dest_dir, filter="data")
    finally:
        dec.close(); enc.close()
    t1.join(timeout=60); t2.join(timeout=60)
    for b in (b1, b2):
        if "err" in b:
            raise b["err"]
    return meta


def seal_file(con, path, backends, kind="db", prefix="db"):
    """Seal one file (a database backup, a faces tar) under the master key."""
    mp = keys.master_pub(con)
    if not mp:
        raise RuntimeError("No master key yet")
    name = new_name(prefix)
    src = open(path, "rb")
    try:
        copies = put_sealed(backends, name, lambda: (src, _noop(), {}), [keys.recipient(mp)])
    finally:
        src.close()
    record(con, None, kind, name, copies)
    return name


def _noop():
    t = threading.Thread(target=lambda: None)
    t.start()
    return t


def open_session(con, sid, master_secret, backend, dest_dir):
    """Reprint path: master secret -> session key -> decrypt the newest archive and unpack into dest_dir."""
    ident = keys.session_identity(con, sid, master_secret)
    rows = con.execute("""SELECT DISTINCT name FROM vault_objects WHERE session_id=? AND kind='session' AND deleted IS NULL
                          AND backend=? ORDER BY id""", (sid, backend.kind)).fetchall()
    if not rows:
        raise KeyError("no sealed archive for this session on that backend")
    os.makedirs(dest_dir, exist_ok=True)
    for (name,) in rows:                       # every archive of the session, oldest first (a split seal is whole again)
        enc, t1, b1 = _pipe_thread(lambda wf, n=name: backend.get_stream(n, wf))
        dec, t2, b2 = _pipe_thread(lambda wf: pyrage.decrypt_io(enc, wf, [ident]))
        try:
            with tarfile.open(fileobj=dec, mode="r|") as tf:
                tf.extractall(dest_dir, filter="data")
        finally:
            dec.close(); enc.close()
        t1.join(timeout=60); t2.join(timeout=60)
        for b in (b1, b2):
            if "err" in b:
                raise b["err"]
    return dest_dir


class Mismatch(ValueError):
    pass


def open_file(name, master_secret, backend, dest_path, expected_sha=None):
    """Restore path for a master-sealed file (database backup). With expected_sha (recorded when the backup was made),
    the stored bytes are checked first: a backup that was swapped, rolled back or altered is refused before it is opened."""
    import hashlib
    ident = keys.parse_secret(master_secret)
    sealed = dest_path + ".sealed"
    try:
        h = hashlib.sha256()
        with open(sealed, "wb") as raw:
            class _Tee:
                def write(self, b):
                    h.update(b)
                    return raw.write(b)
            backend.get_stream(name, _Tee())
        if expected_sha and h.hexdigest() != expected_sha:
            raise Mismatch("this backup is not the one that was made (its fingerprint differs); it was not opened")
        with open(sealed, "rb") as src, open(dest_path, "wb") as out:
            pyrage.decrypt_io(src, out, [ident])
    except BaseException:
        if os.path.exists(dest_path):
            os.remove(dest_path)          # no half-written restore left behind
        raise
    finally:
        if os.path.exists(sealed):
            os.remove(sealed)
    return dest_path


def destroy_session(con, sid, backends):
    """Delete every archive of the session on every backend, then shred its key (the key is what makes it final)."""
    by = {b.kind: b for b in backends}
    now = datetime.now().isoformat(timespec="seconds")
    rows = con.execute("SELECT id, name, backend FROM vault_objects WHERE session_id=? AND deleted IS NULL", (sid,)).fetchall()
    missing = {k for _, _, k in rows if k not in by}
    if missing:
        raise IOError(f"archive copies on {', '.join(sorted(missing))} are not configured here; nothing was deleted")
    for oid, name, kind in rows:
        by[kind].delete_verified(name)
        con.execute("UPDATE vault_objects SET deleted=? WHERE id=?", (now, oid))
    keys.shred(con, sid)
    con.commit()
