import os
import re
import sqlite3 as _plain
from pathlib import Path

import threading
from sqlcipher3 import dbapi2 as sqlite3

import datakey

DB_PATH = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db"))
PLAIN_HEADER = b"SQLite format 3\x00"
Row = sqlite3.Row
BUSY_TIMEOUT = 30

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DECL = re.compile(r"[A-Z][A-Z ]*(?: DEFAULT (?:'[A-Za-z0-9_{}\[\]]*'|-?\d+(?:\.\d+)?|NULL|\(date\('now','localtime'\)\)))?"
                   r"(?: REFERENCES [a-z_]+\(id\)(?: ON DELETE (?:CASCADE|SET NULL))?)?")

def ident(name):

    if not _IDENT.fullmatch(name or ""):
        raise ValueError(f"not a valid SQL identifier: {name!r}")
    return f'"{name}"'

def add_column(con, table, column, decl):

    if not _DECL.fullmatch(decl):
        raise ValueError(f"unexpected column declaration: {decl!r}")
    con.execute(f"ALTER TABLE {ident(table)} ADD COLUMN {ident(column)} {decl}")

def columns(con, table):
    return [r[0] for r in con.execute("SELECT name FROM pragma_table_info(?)", (table,))]

def key_literal(key_hex):

    if not re.fullmatch(r"[0-9a-f]{64}", key_hex or ""):
        raise ValueError("bad database key")
    return f"x'{key_hex}'"

def set_user_version(con, schema, v):
    con.execute(f"PRAGMA {ident(schema)[1:-1]}.user_version = {int(v)}")

def _key(con, key_hex):
    con.execute(f"PRAGMA key = \"{key_literal(key_hex)}\"")

_OPEN_CLOSE = threading.Lock()

class _Connection(sqlite3.Connection):
    def close(self):
        with _OPEN_CLOSE:
            super().close()

def connect(path=None, key=None):

    path = Path(path or DB_PATH)
    key_hex = datakey.db_key_hex(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _OPEN_CLOSE:
        con = sqlite3.connect(str(path), factory=_Connection, timeout=BUSY_TIMEOUT)
        try:
            _key(con, key_hex)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA secure_delete = ON")
            con.execute("PRAGMA temp_store = MEMORY")
        except BaseException:
            sqlite3.Connection.close(con)
            raise
    return con

def snapshot(dest, key=None, src_path=None):

    src, out = connect(src_path, key=key), connect(dest, key=key)
    try:
        src.backup(out)
        out.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out.execute("PRAGMA journal_mode = DELETE")
    finally:
        out.close()
        src.close()

def remove_with_sidecars(path):

    for p in (Path(str(path) + s) for s in ("", "-wal", "-shm", "-journal")):
        if not p.exists():
            continue
        try:
            n = p.stat().st_size
            with open(p, "r+b") as f:
                while n > 0:
                    k = min(n, 1 << 20)
                    f.write(b"\0" * k)
                    n -= k
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        p.unlink()
