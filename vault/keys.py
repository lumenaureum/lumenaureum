"""Keys. The master private key never touches disk: it exists only in the owner's password manager and on paper, and is
typed in only to open something (a restore, a reprint). The CRM keeps the master PUBLIC key and, per session, a key
pair whose private half is stored only encrypted to the master. Deleting that row crypto-shreds the session."""
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
    if "orphaned" not in [r[1] for r in con.execute("PRAGMA table_info(vault_keys)")]:
        con.execute("ALTER TABLE vault_keys ADD COLUMN orphaned TEXT")
    con.commit()


def generate_master():
    """A new master key pair as (secret, public) strings. The caller shows the secret once and never stores it."""
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
    recipient(pub)  # validates
    con.execute("INSERT INTO settings (key, value) VALUES ('vault_master_pub', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pub,))
    con.commit()


def session_pub(con, sid):
    """The session's public key, creating the session key pair on first use (private half wrapped to the master)."""
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
    """Open a session's private key with the master secret (typed in by the owner for this one operation)."""
    r = con.execute("SELECT wrapped FROM vault_keys WHERE session_id=?", (sid,)).fetchone()
    if not r:
        raise KeyError("No key for this session: it was never sealed, or it has been shredded")
    plain = pyrage.decrypt(base64.b64decode(r[0]), [parse_secret(master_secret)])
    return x25519.Identity.from_str(plain.decode())


def shred(con, sid):
    """Destroy the session's key. Its archives, on every backend and in every copy, become unreadable."""
    con.execute("DELETE FROM vault_keys WHERE session_id=?", (sid,))
    con.commit()


def replace_master(con, old_secret, new_pub):
    """Re-wrap every session key from the old master key to a new one (the old secret is needed to open them).
    Returns how many. The caller also re-wraps the data key in the keyring and stores the new public key."""
    old = parse_secret(old_secret)
    rows = con.execute("SELECT session_id, wrapped FROM vault_keys").fetchall()
    out = []
    for sid, wrapped in rows:
        plain = pyrage.decrypt(base64.b64decode(wrapped), [old])       # fails loudly on a wrong old key, before any write
        out.append((base64.b64encode(pyrage.encrypt(plain, [recipient(new_pub)])).decode(), sid))
    con.executemany("UPDATE vault_keys SET wrapped=? WHERE session_id=?", out)
    con.execute("INSERT INTO settings (key, value) VALUES ('vault_master_pub', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_pub,))
    con.commit()
    return len(out)
