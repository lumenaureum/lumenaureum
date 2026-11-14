from datetime import datetime

from . import backends as B, seal as S, web as W

SCHEMA = """CREATE TABLE IF NOT EXISTS vault_secondary (name TEXT PRIMARY KEY, size INTEGER, sha256 TEXT, copied TEXT);"""

def second_backend():
    cfg = W.config().get("secondary")
    return B.from_config(cfg) if cfg else None

def run(con, primary=None, second=None):

    second = second or second_backend()
    if not second:
        return 0, 0
    primary = primary or W.store_backends()[0]
    con.executescript(SCHEMA)
    now = datetime.now().isoformat(timespec="seconds")
    live = con.execute("""SELECT name, MAX(size), MAX(sha256) FROM vault_objects WHERE deleted IS NULL AND backend=?
                          GROUP BY name""", (primary.kind,)).fetchall()
    any_live = {r[0] for r in con.execute("SELECT DISTINCT name FROM vault_objects WHERE deleted IS NULL")}
    have = {r[0]: (r[1], r[2]) for r in con.execute("SELECT name, size, sha256 FROM vault_secondary")}
    copied = removed = 0
    for name, size, sha in live:
        h_have = have.get(name)
        if h_have and h_have[0] == size and (sha is None or h_have[1] == sha) and second.stat(name) == size:
            continue
        rd, t, box = S._pipe_thread(lambda wf, n=name: primary.get_stream(n, wf))
        try:
            s2, h2 = second.put_stream(name, rd)
        finally:
            rd.close()
        t.join(timeout=60)
        if "err" in box or s2 != size or (sha is not None and h2 != sha) or second.stat(name) != size:
            second.delete(name)
            raise IOError(f"second-location copy of {name} did not verify")
        con.execute("INSERT OR REPLACE INTO vault_secondary (name, size, sha256, copied) VALUES (?,?,?,?)", (name, size, sha or h2, now))
        con.commit()
        copied += 1
    live_names = any_live
    for name in [n for n in have if n not in live_names]:
        second.delete_verified(name)
        con.execute("DELETE FROM vault_secondary WHERE name=?", (name,))
        con.commit()
        removed += 1
    return copied, removed
