import base64
from datetime import datetime

import pyrage
from pyrage import x25519

SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_keys (session_id INTEGER PRIMARY KEY, pub TEXT NOT NULL, wrapped TEXT NOT NULL, created TEXT,
  orphaned TEXT);
CREATE TABLE IF NOT EXISTS vault_objects (id INTEGER PRIMARY KEY, session_id INTEGER, kind TEXT NOT NULL, name TEXT NOT NULL,
  backend TEXT NOT NULL, size INTEGER, sha256 TEXT, created TEXT, deleted TEXT);
"""

def init(con):
    con.executescript(SCHEMA)
    cols = [r[1] for r in con.execute("PRAGMA table_info(vault_keys)")]
    if "orphaned" not in cols:
        con.execute("ALTER TABLE vault_keys ADD COLUMN orphaned TEXT")
    if "lost_wrapped" not in cols:
        con.execute("ALTER TABLE vault_keys ADD COLUMN lost_wrapped TEXT")
    con.commit()

def generate_master():

    ident = x25519.Identity.generate()
    return str(ident), str(ident.to_public())

def parse_secret(secret):
    try:
        return x25519.Identity.from_str(secret.strip())
    except Exception as e:
        raise ValueError("That is not an age secret key (AGE-SECRET-KEY-1...)") from e

def recipient(pub):
    return x25519.Recipient.from_str(pub.strip())

def master_pub(con):
    r = con.execute("SELECT value FROM settings WHERE key='vault_master_pub'").fetchone()
    return r[0] if r and r[0] else None

def set_master_pub(con, pub):
    recipient(pub)
    con.execute("INSERT INTO settings (key, value) VALUES ('vault_master_pub', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pub,))
    con.commit()

def session_pub(con, sid):

    r = con.execute("SELECT pub FROM vault_keys WHERE session_id=?", (sid,)).fetchone()
    if r:
        return r[0]
    mp = master_pub(con)
    if not mp:
        raise RuntimeError("No master key yet: create it in Settings > Vault first")
    ident = x25519.Identity.generate()
    wrapped = base64.b64encode(pyrage.encrypt(str(ident).encode(), [recipient(mp)])).decode()
    con.execute("INSERT INTO vault_keys (session_id, pub, wrapped, created) VALUES (?,?,?,?)",
                (sid, str(ident.to_public()), wrapped, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return str(ident.to_public())

def session_identity(con, sid, master_secret):

    r = con.execute("SELECT wrapped FROM vault_keys WHERE session_id=?", (sid,)).fetchone()
    if not r:
        raise KeyError("No key for this session: it was never sealed, or it has been shredded")
    plain = pyrage.decrypt(base64.b64decode(r[0]), [parse_secret(master_secret)])
    return x25519.Identity.from_str(plain.decode())

def shred(con, sid):

    con.execute("DELETE FROM vault_keys WHERE session_id=?", (sid,))
    con.commit()

def replace_master(con, old_secret, new_pub):

    old = parse_secret(old_secret)
    rows = con.execute("SELECT session_id, wrapped FROM vault_keys").fetchall()
    out = []
    for sid, wrapped in rows:
        plain = pyrage.decrypt(base64.b64decode(wrapped), [old])
        out.append((base64.b64encode(pyrage.encrypt(plain, [recipient(new_pub)])).decode(), sid))
    con.executemany("UPDATE vault_keys SET wrapped=? WHERE session_id=?", out)
    con.execute("INSERT INTO settings (key, value) VALUES ('vault_master_pub', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_pub,))
    con.commit()
    return len(out)

LOST_KINDS = {"session": "session-lost", "session-key": "session-key-lost", "photo-key": "photo-key-lost"}
LOST_TEXT = "Sealed with a master key that was replaced; it can't be opened."

def replace_lost_master(con, new_pub):

    init(con)
    recipient(new_pub)
    now = datetime.now().isoformat(timespec="seconds")
    lost = {r[0] for r in con.execute("""SELECT DISTINCT session_id FROM vault_objects WHERE kind IN ('session', 'photo-key')
                                         AND deleted IS NULL""")}
    try:
        for sid, wrapped, old_lost in con.execute("SELECT session_id, wrapped, lost_wrapped FROM vault_keys").fetchall():
            ident = x25519.Identity.generate()
            con.execute("UPDATE vault_keys SET pub=?, wrapped=?, lost_wrapped=?, created=? WHERE session_id=?",
                        (str(ident.to_public()), base64.b64encode(pyrage.encrypt(str(ident).encode(), [recipient(new_pub)])).decode(),
                         "\n".join(w for w in (old_lost, wrapped) if w), now, sid))
        for kind, gone in LOST_KINDS.items():
            con.execute("UPDATE vault_objects SET kind=? WHERE kind=?", (gone, kind))
        con.execute("INSERT INTO settings (key, value) VALUES ('vault_master_pub', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (new_pub,))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return len(lost)
