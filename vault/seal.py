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

    r, w = os.pipe()
    rf, wf = os.fdopen(r, "rb", buffering=1024 * 1024), os.fdopen(w, "wb", buffering=1024 * 1024)
    box = {}

    def run():
        try:
            fn(wf)
        except BaseException as e:
            box["err"] = e
        finally:
            try:
                wf.close()
            except OSError:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return rf, t, box

def add_tree(tf, path, arcname, skip_bad=False):

    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise ValueError(f"{arcname} is not a folder; nothing was sealed")

    skipped = []

    def refuse(rel, why):
        if skip_bad:
            skipped.append(rel)
            return
        raise ValueError(f"{arcname}/{rel} {why}, so the folder can't be sealed as it is (nothing was removed). "
                         "Put the file itself there, or remove it, then seal again.")
    tf.add(real, arcname=arcname, recursive=False)
    def unreadable(e):
        raise e
    for top, dirs, files in os.walk(real, onerror=unreadable, followlinks=False):
        rel_top = os.path.relpath(top, real)
        for d in sorted(dirs):
            if os.path.islink(os.path.join(top, d)):
                refuse(os.path.normpath(os.path.join(rel_top, d)), "is a link to a folder")
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(top, d)))
        for d in dirs:
            tf.add(os.path.join(top, d), arcname=os.path.normpath(os.path.join(arcname, rel_top, d)), recursive=False)
        for f in sorted(files):
            p = os.path.join(top, f)
            rel = os.path.normpath(os.path.join(rel_top, f))
            target = os.path.realpath(p)
            if not os.path.isfile(target):
                refuse(rel, "is not a file" if not os.path.islink(p) else
                       "is a link to something that isn't a file" if os.path.exists(target) else "is a link that leads nowhere")
                continue
            tf.add(target, arcname=os.path.join(arcname, rel), recursive=False)
    return skipped

def tar_reader(paths, members=()):

    def write(wf):
        with tarfile.open(fileobj=wf, mode="w|") as tf:
            for p in paths:
                add_tree(tf, p, os.path.basename(p.rstrip("/")))
            for name, size, opener in members:
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = size, 0o600, int(datetime.now().timestamp())
                tf.addfile(ti, opener())
    return _pipe_thread(write)

def encrypt_reader(reader, recipients):
    return _pipe_thread(lambda wf: pyrage.encrypt_io(reader, wf, recipients))

def put_sealed(backends, name, make_reader, recipients):

    src, t1, b1 = make_reader()
    enc, t2, b2 = encrypt_reader(src, recipients)
    failed = None
    try:
        size, sha = backends[0].put_stream(name, enc)
    except BaseException as e:
        failed = e
    finally:
        for f in (enc, src):
            try:
                f.close()
            except Exception:
                pass
    t1.join(timeout=60); t2.join(timeout=60)
    errs = [b["err"] for b in (b1, b2) if "err" in b]
    if failed or errs:
        backends[0].delete(name)
        raise failed or errs[0]
    if backends[0].stat(name) != size:
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
            for b_ in [bk] + [d[0] for d in done]:
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
    except Exception:
        label = f"session {sid}"
    body = json.dumps(dict(v=1, archive=name, session=sid, label=label, wrapped=w[0],
                           made=datetime.now().isoformat(timespec="seconds"))).encode()
    copies = put_sealed(backends, name + ".key", lambda: (io.BytesIO(body), _noop(), {}), [keys.recipient(mp)])
    record(con, sid, "session-key", name + ".key", copies)

def orphans(con, master_secret, backend):

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

    ident = keys.session_identity(con, sid, master_secret)
    rows = con.execute("""SELECT DISTINCT name FROM vault_objects WHERE session_id=? AND kind='session' AND deleted IS NULL
                          AND backend=? ORDER BY id""", (sid, backend.kind)).fetchall()
    if not rows:
        raise KeyError("no sealed archive for this session on that backend")
    os.makedirs(dest_dir, exist_ok=True)
    for (name,) in rows:
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
            os.remove(dest_path)
        raise
    finally:
        if os.path.exists(sealed):
            os.remove(sealed)
    return dest_path

def destroy_session(con, sid, backends):

    by = {b.kind: b for b in backends}
    now = datetime.now().isoformat(timespec="seconds")
    rows = con.execute("SELECT id, name, backend FROM vault_objects WHERE session_id=? AND deleted IS NULL", (sid,)).fetchall()
    missing = {k for _, _, k in rows if k not in by}
    if missing:
        raise IOError(f"archive copies on {', '.join(sorted(missing))} are not configured here; nothing was deleted")
    for oid, name, kind in rows:

        con.execute("UPDATE vault_objects SET deleted=? WHERE id=?", (now, oid))
        con.commit()
        try:
            by[kind].delete_verified(name)
        except BaseException:
            con.execute("UPDATE vault_objects SET deleted=NULL WHERE id=?", (oid,))
            con.commit()
            raise
    keys.shred(con, sid)
    con.commit()

REWRAP_KINDS = ("photo-key", "session-key")

def rewrap_key_files(con, old_secret, new_pub, backends):

    import hashlib
    import io
    import json
    ident = keys.parse_secret(old_secret)
    rcpt = keys.recipient(new_pub)
    by_kind = {b.kind: b for b in backends}
    moved = left = 0
    rows = con.execute(f"""SELECT id, session_id, kind, name, backend FROM vault_objects WHERE deleted IS NULL
                           AND kind IN ({','.join('?' * len(REWRAP_KINDS))}) ORDER BY id""", REWRAP_KINDS).fetchall()
    for oid, sid, kind, name, where in rows:
        bk = by_kind.get(where)
        if bk is None:
            left += 1
            continue
        try:
            buf = io.BytesIO()
            bk.get_stream(name, buf)
            plain = pyrage.decrypt(buf.getvalue(), [ident])
            if kind == "session-key":
                w = con.execute("SELECT wrapped FROM vault_keys WHERE session_id=?", (sid,)).fetchone()
                m = json.loads(plain)
                if w:
                    m["wrapped"] = w[0]
                plain = json.dumps(m).encode()
            ct = pyrage.encrypt(plain, [rcpt])
            bk.put_stream(name, io.BytesIO(ct))
            sha = hashlib.sha256(ct).hexdigest()
            if bk.stat(name) != len(ct):
                raise IOError(f"the new copy of {name} did not verify")
            con.execute("UPDATE vault_objects SET size=?, sha256=? WHERE id=?", (len(ct), sha, oid))
            con.commit()
            moved += 1
        except Exception:
            left += 1
    return moved, left
