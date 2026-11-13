"""The one way to open the database. The file is SQLCipher (every page encrypted under a subkey of the data key);
opening it while the app is locked raises datakey.Locked."""
import os
import re
import sqlite3 as _plain
from pathlib import Path

import threading
from sqlcipher3 import dbapi2 as sqlite3   # same DB-API as the standard sqlite3 module

import datakey

DB_PATH = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db"))
PLAIN_HEADER = b"SQLite format 3\x00"
Row = sqlite3.Row


# Query rule: every value is a bound parameter. SQLite cannot bind three things, so they go through these checks:
#   identifiers (table/column names) -> ident(); a column declaration -> add_column(); PRAGMA values -> the checks below.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DECL = re.compile(r"[A-Z][A-Z ]*(?: DEFAULT (?:'[A-Za-z0-9_{}\[\]]*'|-?\d+(?:\.\d+)?|NULL|\(date\('now','localtime'\)\)))?"
                   r"(?: REFERENCES [a-z_]+\(id\)(?: ON DELETE (?:CASCADE|SET NULL))?)?")


def ident(name):
    """A table or column name, checked and quoted for use in SQL text."""
    if not _IDENT.fullmatch(name or ""):
        raise ValueError(f"not a valid SQL identifier: {name!r}")
    return f'"{name}"'


def add_column(con, table, column, decl):
    """ALTER TABLE ... ADD COLUMN, with every part checked (the declaration comes from code, never from input)."""
    if not _DECL.fullmatch(decl):
        raise ValueError(f"unexpected column declaration: {decl!r}")
    con.execute(f"ALTER TABLE {ident(table)} ADD COLUMN {ident(column)} {decl}")


def columns(con, table):
    return [r[0] for r in con.execute("SELECT name FROM pragma_table_info(?)", (table,))]


def key_literal(key_hex):
    """The raw-key literal SQLCipher takes: exactly 64 lowercase hex characters, nothing else can pass."""
    if not re.fullmatch(r"[0-9a-f]{64}", key_hex or ""):
        raise ValueError("bad database key")
    return f"x'{key_hex}'"


def set_user_version(con, schema, v):
    con.execute(f"PRAGMA {ident(schema)[1:-1]}.user_version = {int(v)}")


def _key(con, key_hex):
    con.execute(f"PRAGMA key = \"{key_literal(key_hex)}\"")   # PRAGMA cannot bind; key_literal admits only hex


# sqlcipher3 0.6 can deadlock the whole process when one thread closes a connection while another opens one (close
# holds the GIL while it waits on a SQLCipher lock the opener holds; reproduced within
# seconds with three threads). Opening and closing are therefore serialized process-wide; queries run in parallel as before.
_OPEN_CLOSE = threading.Lock()


class _Connection(sqlite3.Connection):
    def close(self):
        with _OPEN_CLOSE:
            super().close()


def connect(path=None, key=None):
    """An open, keyed connection. `key` is a raw data key (migration, restore); otherwise the unlocked one."""
    path = Path(path or DB_PATH)
    key_hex = datakey.db_key_hex(key)            # raises Locked first: nothing is opened while locked
    path.parent.mkdir(parents=True, exist_ok=True)
    with _OPEN_CLOSE:
        con = sqlite3.connect(str(path), factory=_Connection)
        try:
            _key(con, key_hex)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA secure_delete = ON")     # deleted client rows are zeroed, not left in free pages
            con.execute("PRAGMA temp_store = MEMORY")    # sorts and temp tables never touch the disk
        except BaseException:
            sqlite3.Connection.close(con)                # already holding the lock
            raise
    return con


def snapshot(dest, key=None, src_path=None):
    """A consistent copy of the live database at `dest`, encrypted under the same key (the copy is never plaintext)."""
    src, out = connect(src_path, key=key), connect(dest, key=key)
    try:
        src.backup(out)
        out.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out.execute("PRAGMA journal_mode = DELETE")      # one self-contained file to seal
    finally:
        out.close()
        src.close()


def remove_with_sidecars(path):
    """Overwrite, then delete, a database file and its -wal / -shm / -journal files. Overwriting is best effort: on
    copy-on-write filesystems and in snapshots the old blocks survive (the app says so after an upgrade)."""
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
