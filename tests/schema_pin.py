"""The pinned schema: what a fresh install at SCHEMA_VERSION holds (sqlite_master), normalized so only real changes
show. Used by tests/test_schema_pinned.py and tools/pin_schema.py."""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGENERATE = "python tools/pin_schema.py"


def pinned_path(version):
    return HERE / f"schema_v{version}.sql"


def normalized(con):
    """One line per table, index, view, and trigger: type, name, and its SQL with whitespace collapsed, sorted."""
    rows = con.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    lines = sorted(f"{t} {n}: " + re.sub(r"\s+", " ", sql).strip() for t, n, sql in rows)
    return "\n".join(lines) + "\n"


def render(version, text):
    return (f"-- The schema of a fresh install at SCHEMA_VERSION {version}. Do not edit by hand: regenerate with\n"
            f"-- {REGENERATE} (see the schema versions checklist in app.py).\n" + text)


def fresh_install_schema():
    """Build a fresh install in the folders named by CRM_DB etc. (already set) and return (version, normalized)."""
    from helpers import fresh_modules, signed_in_client
    fresh_modules()
    import app as A
    signed_in_client(A)
    con = A.connect()
    try:
        return A.SCHEMA_VERSION, normalized(con)
    finally:
        con.close()
