import re
from datetime import datetime

ITEM_OK = re.compile(r"[0-9a-f]{32}")

TABLE = """CREATE TABLE IF NOT EXISTS sealed_seen (
    purpose TEXT NOT NULL, item TEXT NOT NULL, link TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (purpose, link, item));"""

def migrate(con):

    pk = sorted((r[5], r[1]) for r in con.execute("PRAGMA table_info(sealed_seen)") if r[5])
    if [c for _, c in pk] != ["purpose", "item"]:
        return False
    try:
        con.executescript("BEGIN IMMEDIATE; ALTER TABLE sealed_seen RENAME TO sealed_seen_before_link; " + TABLE +
                          """ INSERT OR IGNORE INTO sealed_seen (purpose, item, link, first_seen, n)
                              SELECT purpose, item, link, first_seen, n FROM sealed_seen_before_link;
                              DROP TABLE sealed_seen_before_link; COMMIT;""")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return True

def migrate_hook(connect):

    def run():
        con = connect()
        try:
            migrate(con)
        finally:
            con.close()
    return run

def item_of(payload):

    v = payload.get("id") if isinstance(payload, dict) else None
    return v if isinstance(v, str) and ITEM_OK.fullmatch(v) else None

def state(q, purpose, link, item):

    link = link or ""
    r = q("SELECT n FROM sealed_seen WHERE purpose=? AND link=? AND item=?", (purpose, link, item), one=True)
    if r is None:
        return "new"
    top = q("SELECT MAX(n) AS n FROM sealed_seen WHERE purpose=? AND link=?", (purpose, link), one=True)
    return "latest" if top and top["n"] == r["n"] else "older"

def take(q, x, purpose, link, item):

    if q("SELECT 1 FROM sealed_seen WHERE purpose=? AND link=? AND item=?", (purpose, link or "", item), one=True):
        return False
    top = q("SELECT MAX(n) AS n FROM sealed_seen", one=True)
    x("INSERT OR IGNORE INTO sealed_seen (purpose, item, link, first_seen, n) VALUES (?,?,?,?,?)",
      (purpose, item, link or "", datetime.now().isoformat(timespec="seconds"), ((top["n"] if top else 0) or 0) + 1))
    return True

def relink(x, old, new):

    x("UPDATE OR IGNORE sealed_seen SET link=? WHERE link=?", (new, old))
