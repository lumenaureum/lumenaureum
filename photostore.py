import hashlib
import os
import re
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DATA = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db")).parent
PHOTOS_ROOT = Path(os.environ.get("CRM_PHOTOS_ROOT", _DATA / "photos"))
ROOT = PHOTOS_ROOT / "sealed"
MAGIC = b"LAP1"
REC = 1 << 20
PIECE = 8 * REC
MAX_FILE = 4 << 30
KINDS = {"raw": "RAWs", "export": "Edited exports", "final": "Retouched finals"}

SCHEMA = """CREATE TABLE IF NOT EXISTS photo_files (
  id TEXT PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, kind TEXT NOT NULL,
  name TEXT NOT NULL, size INTEGER NOT NULL, received INTEGER DEFAULT 0, ct_len INTEGER DEFAULT 0, records INTEGER DEFAULT 0,
  fkey TEXT NOT NULL, state TEXT DEFAULT 'partial', created TEXT DEFAULT CURRENT_TIMESTAMP, done_at TEXT, mirrored TEXT);
CREATE INDEX IF NOT EXISTS photo_files_sid ON photo_files(session_id, kind);
CREATE TABLE IF NOT EXISTS photo_deleted (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS photo_orphans (id TEXT PRIMARY KEY, first_seen TEXT NOT NULL);"""
UNIQUE = "CREATE UNIQUE INDEX IF NOT EXISTS photo_files_name ON photo_files(session_id, kind, name)"

def migrate(con):

    con.executescript(SCHEMA)
    for sid, kind, name in con.execute("""SELECT session_id, kind, name FROM photo_files GROUP BY session_id, kind, name
                                          HAVING COUNT(*) > 1""").fetchall():
        rows = con.execute("""SELECT * FROM photo_files WHERE session_id=? AND kind=? AND name=?
                              ORDER BY state='done' DESC, received DESC""", (sid, kind, name)).fetchall()
        for r in rows[1:]:
            delete(con, r)
    con.execute(UNIQUE)
    con.commit()

def ensure_trail(con):

    if "key_trail" not in [r[1] for r in con.execute("PRAGMA table_info(photo_files)")]:
        con.execute("ALTER TABLE photo_files ADD COLUMN key_trail TEXT DEFAULT '[]'")

def trail_tag(fkey, fid):

    import hmac
    return hmac.new(bytes.fromhex(fkey), b"lumen-aureum key trail|" + fid.encode(), hashlib.sha256).hexdigest()

def key_trail(row):
    import json
    try:
        t = json.loads(row["key_trail"] or "[]")
    except (IndexError, KeyError, ValueError, TypeError):
        return []
    return [x for x in t if isinstance(x, str)] if isinstance(t, list) else []

class UploadError(ValueError):
    pass

def clean_name(name):

    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", (name or "").split("/")[-1].split("\\")[-1]).strip().lstrip(".")
    if not name:
        raise UploadError("A file needs a name.")
    return name[:200]

def path_of(fid):
    if not re.fullmatch(r"[0-9a-f]{32}", fid or ""):
        raise UploadError("bad file id")
    return ROOT / fid[:2] / f"{fid}.lap"

def _aad(fid, index, last):
    return f"{fid}:{index}:{'last' if last else 'more'}".encode()

class DiskFull(UploadError):
    pass

def _write_all(fd, data):

    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError as e:
            import errno
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                raise DiskFull("The photos folder is full. Free some space (or give it more), then drop the file again: "
                               "it carries on from where it stopped.") from e
            raise
        if n <= 0:
            raise DiskFull("The photos folder is full. Free some space, then drop the file again.")
        view = view[n:]

def _rec_len(b4):

    if len(b4) != 4:
        raise ValueError("a sealed photo is cut short")
    n = int.from_bytes(b4, "big")
    if not 16 < n <= REC + 16:
        raise ValueError("a sealed photo has a record of the wrong length")
    return n

def _nonce(index):
    return b"\0\0\0\0" + index.to_bytes(8, "big")

def key_path(fid):
    return path_of(fid).with_suffix(".key")

def write_key(row, master_pub, label="", path=None):

    import json
    import pyrage
    from vault import keys as VK
    if not master_pub:
        return
    body = json.dumps(dict(v=1, id=row["id"], session=row["session_id"], label=label, kind=row["kind"], name=row["name"],
                           size=row["size"], fkey=row["fkey"], trail=key_trail(row))).encode()
    p = Path(path) if path else key_path(row["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(pyrage.encrypt(body, [VK.recipient(master_pub)]))
    os.replace(tmp, p)

def read_file_plain(path, fid, fkey, out, size=None):

    aes = AESGCM(bytes.fromhex(fkey))
    total = 0
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("not a sealed photo")
        index, nxt = 0, f.read(4)
        if not nxt:
            raise ValueError("a sealed photo with no records")
        while nxt:
            ct = f.read(_rec_len(nxt))
            nxt = f.read(4)
            pt = aes.decrypt(_nonce(index), ct, _aad(fid, index, not nxt))
            total += len(pt)
            out.write(pt)
            index += 1
    if size is not None and total != int(size):
        raise ValueError("a sealed photo is not the size it was")

def start(con, sid, kind, name, size, create=True, master_pub=None, label=""):

    if kind not in KINDS:
        raise UploadError("Unknown kind of photo.")
    name = clean_name(name)
    if not isinstance(size, int) or not 0 < size <= MAX_FILE:
        raise UploadError("Files from 1 byte to 4 GB.")
    get = lambda: con.execute("SELECT * FROM photo_files WHERE session_id=? AND kind=? AND name=?", (sid, kind, name)).fetchone()
    r = get()
    if not r and create:
        con.execute("""INSERT INTO photo_files (id, session_id, kind, name, size, fkey) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(session_id, kind, name) DO NOTHING""",
                    (secrets.token_hex(16), sid, kind, name, size, secrets.token_bytes(32).hex()))
        con.commit()
        r = get()
        if r and r["received"] == 0:
            write_key(r, master_pub, label)
    elif r and create:
        r = restart_if_diverged(con, r, master_pub, label)
    if not r:
        raise Gone("That file was deleted here; drop it again to upload it again.")
    if r["size"] != size:
        raise UploadError(f"A different {name} is already here. Delete it first to replace it.")
    return r

class Gone(UploadError):
    pass

def _on_disk(fid):
    try:
        return path_of(fid).stat().st_size
    except FileNotFoundError:
        return 0

def restart_if_diverged(con, row, master_pub=None, label=""):

    if row["state"] != "partial" or _on_disk(row["id"]) == row["ct_len"]:
        return row
    p = path_of(row["id"])
    if p.exists():
        with open(p, "r+b") as f:
            f.truncate(0)
            f.flush(); os.fsync(f.fileno())
    cur = con.execute("""UPDATE photo_files SET fkey=?, received=0, ct_len=0, records=0 WHERE id=? AND state='partial'
                         AND received=?""", (secrets.token_bytes(32).hex(), row["id"], row["received"]))
    con.commit()
    row = con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()
    if cur.rowcount == 1 and row:
        write_key(row, master_pub, label)
    return row

def append(con, row, offset, stream, length, master_pub=None, label=""):

    fid, size = row["id"], row["size"]
    if row["state"] == "partial" and _on_disk(fid) != row["ct_len"]:
        restart_if_diverged(con, row, master_pub, label)
        raise UploadError("resume at 0")
    start_at = row["received"]
    if row["state"] != "partial" or offset != start_at:
        raise UploadError(f"resume at {row['received']}")
    if length <= 0 or offset + length > size:
        raise UploadError("That piece doesn't fit the file.")
    p = path_of(fid)
    p.parent.mkdir(parents=True, exist_ok=True)
    aes = AESGCM(bytes.fromhex(row["fkey"]))
    index, received, ct_len = row["records"], row["received"], row["ct_len"]
    fd = os.open(p, os.O_RDWR | (os.O_CREAT if row["ct_len"] == 0 else 0), 0o600)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        if os.fstat(fd).st_size != ct_len:
            raise UploadError("resume at -1")

        if ct_len == 0:
            _write_all(fd, MAGIC)
            ct_len = len(MAGIC)
        os.lseek(fd, ct_len, os.SEEK_SET)
        left, buf = length, bytearray()
        try:
            while left > 0 or buf:
                want = min(REC - len(buf), left)
                if want > 0:
                    piece = stream.read(want)
                    if not piece:
                        raise UploadError("The upload stopped part way; it resumes from the last whole piece.")
                    buf += piece
                    left -= len(piece)
                last = received + len(buf) == size
                if len(buf) == REC or last:
                    ct = aes.encrypt(_nonce(index), bytes(buf), _aad(fid, index, last))
                    _write_all(fd, len(ct).to_bytes(4, "big") + ct)
                    ct_len += 4 + len(ct)
                    received += len(buf)
                    index += 1
                    buf = bytearray()
                elif left == 0:
                    break
        finally:
            os.ftruncate(fd, ct_len)
            os.fsync(fd)
            done = received == size
            cur = con.execute("""UPDATE photo_files SET received=?, ct_len=?, records=?, state=?,
                                 done_at=CASE WHEN ? THEN datetime('now') END WHERE id=? AND received=? AND state='partial'""",
                              (received, ct_len, index, "done" if done else "partial", done, fid, start_at))
            con.commit()
            if cur.rowcount != 1:
                raise UploadError("resume at -1")
    finally:
        os.close(fd)
    return received

def read_plain(row, out):

    for b in iter_plain(row):
        out.write(b)

def iter_plain(row):

    aes = AESGCM(bytes.fromhex(row["fkey"]))
    if row["state"] != "done":
        raise UploadError("That file isn't complete yet.")
    total = 0
    with open(path_of(row["id"]), "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("not a sealed photo")
        for index in range(row["records"]):
            n = _rec_len(f.read(4))
            ct = f.read(n)
            if len(ct) != n:
                raise ValueError("a sealed photo is cut short")
            pt = aes.decrypt(_nonce(index), ct, _aad(row["id"], index, index == row["records"] - 1))
            total += len(pt)
            yield pt
    if total != row["size"]:
        raise ValueError("a sealed photo is not the size it was")

def verify(row):

    for _ in iter_plain(row):
        pass

def plain_bytes(row):
    return b"".join(iter_plain(row))

class PlainReader:

    def __init__(self, row):
        self._it, self._buf = iter_plain(row), b""

    def read(self, n=-1):
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

def files(con, sid, kind=None, done_only=False):
    sql = "SELECT * FROM photo_files WHERE session_id=?" + (" AND kind=?" if kind else "") + (" AND state='done'" if done_only else "")
    return con.execute(sql + " ORDER BY kind, name", (sid, kind) if kind else (sid,)).fetchall()

def delete(con, row, commit=True):

    con.execute("DELETE FROM photo_files WHERE id=?", (row["id"],))
    con.execute("INSERT OR IGNORE INTO photo_deleted (id) VALUES (?)", (row["id"],))
    if not commit:
        return [row["id"]]
    con.commit()
    destroy_files([row["id"]])
    return []

def destroy_files(fids):

    for fid in fids:
        p = path_of(fid)
        try:
            n = p.stat().st_size
            with open(p, "r+b") as f:
                f.write(b"\0" * min(n, 1 << 20))
                f.flush(); os.fsync(f.fileno())
            p.unlink()
        except FileNotFoundError:
            pass
        key_path(fid).unlink(missing_ok=True)

def delete_session(con, sid):
    gone = []
    for r in con.execute("SELECT * FROM photo_files WHERE session_id=?", (sid,)).fetchall():
        gone += delete(con, r, commit=False)
    con.commit()
    destroy_files(gone)

def orphan_age(con, fids, days=30):

    from datetime import date, timedelta
    today_ = date.today()
    con.executemany("INSERT OR IGNORE INTO photo_orphans (id, first_seen) VALUES (?,?)", [(f, today_.isoformat()) for f in fids])
    cutoff = (today_ - timedelta(days=days)).isoformat()
    old = {r[0] for r in con.execute("SELECT id FROM photo_orphans WHERE first_seen <= ?", (cutoff,))}
    con.execute("DELETE FROM photo_orphans WHERE id IN (SELECT id FROM photo_files)")
    con.commit()
    return [f for f in fids if f in old]

def forget_orphans(con, fids):
    con.executemany("DELETE FROM photo_orphans WHERE id=?", [(f,) for f in fids])
    con.commit()

def sweep_orphans(con, days=30):
    known = {r[0] for r in con.execute("SELECT id FROM photo_files")}
    if not ROOT.is_dir():
        return 0
    loose = {p.stem: p for p in ROOT.glob("*/*.lap") if p.stem not in known}
    n = 0
    gone = orphan_age(con, list(loose), days)
    for fid in gone:
        loose[fid].unlink(missing_ok=True)
        key_path(fid).unlink(missing_ok=True)
        n += 1
    forget_orphans(con, gone)
    return n

def rekey(row, dst):

    old = AESGCM(bytes.fromhex(row["fkey"]))
    new_key = secrets.token_bytes(32)
    new = AESGCM(new_key)
    n = 0
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with open(path_of(row["id"]), "rb") as f:
            if f.read(4) != MAGIC:
                raise ValueError("not a sealed photo")
            _write_all(fd, MAGIC)
            n = 4
            for index in range(row["records"]):
                size = _rec_len(f.read(4))
                last = row["state"] == "done" and index == row["records"] - 1
                pt = old.decrypt(_nonce(index), f.read(size), _aad(row["id"], index, last))
                ct = new.encrypt(_nonce(index), pt, _aad(row["id"], index, last))
                del pt
                _write_all(fd, len(ct).to_bytes(4, "big") + ct)
                n += 4 + len(ct)
        os.fsync(fd)
    finally:
        os.close(fd)
    return new_key.hex(), n

VAULT_PREFIX = "p/"

def vault_name(row):
    return f"{VAULT_PREFIX}{row['session_id']}/{row['id']}.lap"

def _file_sha(path):
    h_ = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            h_.update(piece)
    return h_.hexdigest()

def _remote_sha(backend, name):

    h_ = hashlib.sha256()

    class _W:
        def write(self, b):
            h_.update(b)
            return len(b)
    backend.get_stream(name, _W())
    return h_.hexdigest()

def seal_to_vault(con, rows, backends, master_pub, label=""):

    import io
    from vault import seal as VS
    n = 0
    for r in rows:
        if r["state"] != "done":
            continue
        if not key_path(r["id"]).exists():
            write_key(r, master_pub, label)
        name = vault_name(r)
        with open(path_of(r["id"]), "rb") as f:
            lap = f.read() if r["ct_len"] < (64 << 20) else None
        copies = []
        for bk in backends:
            if lap is not None:
                bk.put_stream(name, io.BytesIO(lap))
            else:
                with open(path_of(r["id"]), "rb") as f:
                    bk.put_stream(name, f)
            with open(key_path(r["id"]), "rb") as f:
                kb = f.read()
            bk.put_stream(name[:-4] + ".key", io.BytesIO(kb))
            copies.append(bk)
        sha = hashlib.sha256(lap).hexdigest() if lap is not None else _file_sha(path_of(r["id"]))
        kb_sha = hashlib.sha256(kb).hexdigest()
        for bk in copies:
            if (bk.stat(name) != r["ct_len"] or bk.stat(name[:-4] + ".key") != len(kb)
                    or _remote_sha(bk, name) != sha or _remote_sha(bk, name[:-4] + ".key") != kb_sha):
                raise IOError(f"the vault copy of {r['name']} did not verify")
        VS.record(con, r["session_id"], "photo", name, [(b_, r["ct_len"], sha) for b_ in copies])
        VS.record(con, r["session_id"], "photo-key", name[:-4] + ".key",
                  [(b_, len(kb), hashlib.sha256(kb).hexdigest()) for b_ in copies])
        con.execute("UPDATE photo_files SET state='sealed', fkey='', mirrored=NULL WHERE id=?", (r["id"],))
        con.execute("INSERT OR IGNORE INTO photo_deleted (id) VALUES (?)", (r["id"],))
        con.commit()
        path_of(r["id"]).unlink(missing_ok=True)
        key_path(r["id"]).unlink(missing_ok=True)
        n += 1
    return n

def open_sealed(con, sid, master_secret, backend, dest_dir, only=()):

    import io
    import json
    import pyrage
    from pathlib import Path as _P
    from vault import keys as VK
    ident = VK.parse_secret(master_secret)
    n = 0
    for r in con.execute("SELECT * FROM photo_files WHERE session_id=? AND state='sealed' ORDER BY kind, name", (sid,)).fetchall():
        if only and not any(o.lower() in r["name"].lower() for o in only):
            continue
        name = vault_name(r)
        kb = io.BytesIO()
        backend.get_stream(name[:-4] + ".key", kb)
        m = json.loads(pyrage.decrypt(kb.getvalue(), [ident]))
        if m.get("id") != r["id"] or int(m.get("session") or 0) != int(sid) or int(m.get("size") or -1) != int(r["size"]):
            raise ValueError(f"the key record for {r['name']} belongs to another file")
        out = _P(dest_dir) / r["kind"] / clean_name(r["name"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".lap")
        with open(tmp, "wb") as f:
            backend.get_stream(name, f)
        try:
            with open(out, "wb") as f:
                read_file_plain(tmp, r["id"], m["fkey"], f, size=r["size"])
        finally:
            tmp.unlink(missing_ok=True)
        n += 1
    return n

MIRROR = "photos/"

def fetch_back(r, backend):

    p, name = path_of(r["id"]), MIRROR + f"{r['id']}.lap"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".fetch")
    with open(tmp, "wb") as f:
        backend.get_stream(name, f)
    if tmp.stat().st_size != r["ct_len"]:
        tmp.unlink()
        raise IOError(f"the mirror copy of {r['name']} is not the size it should be")
    os.replace(tmp, p)
    kp = key_path(r["id"])
    if not kp.exists():
        try:
            with open(kp.with_name(kp.name + ".fetch"), "wb") as f:
                backend.get_stream(MIRROR + f"{r['id']}.key", f)
            os.replace(kp.with_name(kp.name + ".fetch"), kp)
        except Exception:
            kp.with_name(kp.name + ".fetch").unlink(missing_ok=True)

def mirror_pass(con, backend, audit=False):

    copied = fetched = removed = 0
    rows = con.execute("SELECT * FROM photo_files WHERE state='done'").fetchall()
    for r in rows:
        p, name = path_of(r["id"]), MIRROR + f"{r['id']}.lap"
        if not p.exists() and r["mirrored"]:
            fetch_back(r, backend)
            fetched += 1
            continue
        if r["mirrored"] == backend.kind and (not audit or backend.stat(name) == r["ct_len"]):
            continue
        with open(p, "rb") as f:
            backend.put_stream(name, f)
        if key_path(r["id"]).exists():
            with open(key_path(r["id"]), "rb") as f:
                backend.put_stream(MIRROR + f"{r['id']}.key", f)
        if backend.stat(name) != r["ct_len"] or _remote_sha(backend, name) != _file_sha(p):
            backend.delete(name)
            raise IOError(f"the mirror copy of {r['name']} did not verify")
        con.execute("UPDATE photo_files SET mirrored=? WHERE id=?", (backend.kind, r["id"]))
        con.commit()
        copied += 1
    deleted = [r[0] for r in con.execute("SELECT id FROM photo_deleted")]
    for fid in deleted:
        for ext in (".lap", ".key"):
            try:
                if backend.stat(MIRROR + fid + ext) is not None:
                    backend.delete(MIRROR + fid + ext)
                    removed += ext == ".lap"
            except Exception:
                break
        else:
            con.execute("DELETE FROM photo_deleted WHERE id=?", (fid,))
    con.commit()
    if audit:
        keep = {r["id"] for r in rows}
        strays = [n_[len(MIRROR):-4] for n_ in backend.list(MIRROR) if n_.endswith(".lap") and n_[len(MIRROR):-4] not in keep
                  and not con.execute("SELECT 1 FROM photo_files WHERE id=?", (n_[len(MIRROR):-4],)).fetchone()]
        old = orphan_age(con, strays)
        for fid in old:
            for ext in (".lap", ".key"):
                try:
                    backend.delete(MIRROR + fid + ext)
                except Exception:
                    pass
            removed += 1
        forget_orphans(con, old)
    return copied, fetched, removed

def fingerprint(row):
    h = hashlib.sha256()
    for b in iter_plain(row):
        h.update(b)
    return h.hexdigest()
