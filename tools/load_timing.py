import argparse
import cProfile
import io
import os
import pstats
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST = ["Avery", "Blake", "Casey", "Drew", "Emery", "Finley", "Harper", "Jordan", "Kendall", "Logan", "Morgan",
         "Parker", "Quinn", "Reese", "Riley", "Rowan", "Sage", "Skyler", "Taylor", "Wren"]
LAST = ["Ashford", "Birchwood", "Calloway", "Dunmore", "Ellery", "Fairbanks", "Glenhaven", "Hollister", "Ingram",
        "Jessup", "Kingsley", "Lockhart", "Marlowe", "Northcott", "Oakridge", "Pembrook", "Quimby", "Radcliffe",
        "Stanwick", "Thornbury"]

def setup_env(folder):
    os.environ["CRM_TESTING"] = "1"
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        os.environ[k] = os.path.join(folder, v)
    os.environ["CRM_GALLERY_SYNC"] = "0"
    os.environ["CRM_DEV"] = "1"
    os.makedirs(os.path.join(folder, "data"), exist_ok=True)
    sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
    os.chdir(ROOT)

def jpeg(seed):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (32, 24), (seed * 7 % 255, seed * 13 % 255, seed * 29 % 255)).save(b, "JPEG")
    return b.getvalue()

def build(A, n_open, n_clients, n_photos, n_closed):

    rnd = random.Random(142)
    T = A.today()
    D = lambda n: (T + timedelta(days=n)).isoformat()
    t0 = time.perf_counter()
    types = [k for k in A.C.SESSION_TYPES if k != A.C.EVENT_TYPE]
    con = A.connect()
    try:
        con.executemany("INSERT INTO households (id, name, source, address) VALUES (?,?,?,?)",
                        [(i, f"{FIRST[i % 20]} {LAST[(i // 20) % 20]} {i}", "website", f"{i} Example Lane\nSampletown 00000")
                         for i in range(1, n_clients + 1)])
        con.executemany("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?,?,?,?)",
                        [(i, f"{FIRST[i % 20]} {LAST[(i // 20) % 20]}", "adult", f"client{i}@example.com", f"555-{i:04d}")
                         for i in range(1, n_clients + 1)])
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        stages = ["inquiry", "quoted", "booked", "shot", "delivered"]
        open_ids = []
        for i in range(n_open):
            hid = 1 + (i * 7) % n_clients
            st = stages[i % len(stages)]
            day = {"inquiry": None, "quoted": D(rnd.randint(5, 90)), "booked": D(rnd.randint(-2, 60)),
                   "shot": D(-rnd.randint(1, 20)), "delivered": D(-rnd.randint(20, 60))}[st]
            pid = A.q("SELECT id FROM people WHERE household_id=?", (hid,), one=True)["id"]
            sid = A.create_session(hid, types[i % len(types)], st, f"Session {i}", day, subject_ids=[pid])
            A.x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (sid, "Session fee", 300 + i % 5 * 50))
            if st in ("booked", "shot", "delivered"):
                A.x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)",
                    (sid, "deposit", 100, D(-rnd.randint(20, 90)), "check"))
            A.x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)",
                (hid, sid, D(-rnd.randint(1, 30)), "email", "Asked about dates"))
            open_ids.append(sid)
        for i in range(n_closed):
            hid = 1 + (i * 11) % n_clients
            pid = A.q("SELECT id FROM people WHERE household_id=?", (hid,), one=True)["id"]
            day = D(-rnd.randint(60, 900))
            sid = A.create_session(hid, types[i % len(types)], "closed", f"Old session {i}", day, subject_ids=[pid])
            A.x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (sid, "Session fee", 350))
            A.x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)",
                (sid, "balance", 350, day, "card"))
            A.x("INSERT INTO expenses (session_id, date, label, amount, miles) VALUES (?,?,?,?,?)", (sid, day, "Parking", 12, 20))
            A.x("UPDATE tasks SET done=? WHERE session_id=?", (day, sid))
        big = open_ids[4] if len(open_ids) > 4 else open_ids[-1]
        A.x("UPDATE sessions SET stage='delivered', gallery_built=? WHERE id=?", (D(-3), big))
        A.db().commit()
    t1 = time.perf_counter()
    with A.app.test_request_context():
        for i in range(n_photos):
            A.store_photo(big, "export", f"IMG_{i:05d}.jpg", jpeg(i))
    t2 = time.perf_counter()
    print(f"built: {n_clients} clients, {n_open} open sessions, {n_closed} closed, {n_photos} photos on session {big} "
          f"({t1 - t0:.1f} s records, {t2 - t1:.1f} s photos)")
    return big, 1 + (4 * 7) % n_clients

def timed(c, url, runs):
    t = time.perf_counter()
    r = c.get(url)
    first = time.perf_counter() - t
    assert r.status_code == 200, (url, r.status_code, r.location)
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        r = c.get(url)
        out.append(time.perf_counter() - t)
        assert r.status_code == 200, (url, r.status_code)
    return first, statistics.median(out), max(out), len(r.data)

def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "Time the app's pages on a studio of a given size.").split("\n")[0])
    ap.add_argument("--open", type=int, default=300)
    ap.add_argument("--clients", type=int, default=2000)
    ap.add_argument("--photos", type=int, default=1500)
    ap.add_argument("--closed", type=int, default=1500)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--keep", default="", help="build the studio in this folder and keep it (a folder built before is reused)")
    ap.add_argument("--profile", default="")
    ap.add_argument("--only", default="", help="time only these pages (comma-separated)")
    ap.add_argument("--no-bulk", action="store_true", help="skip the bulk photo delete timing")
    a = ap.parse_args()
    folder = a.keep or tempfile.mkdtemp(prefix="la-load-")
    setup_env(folder)
    try:
        import app as A
        from helpers import signed_in_client
        built = os.path.exists(os.environ["CRM_DB"])
        c = signed_in_client(A)
        if built:
            con = A.connect()
            big = con.execute("SELECT session_id, COUNT(*) n FROM photo_files GROUP BY session_id ORDER BY n DESC").fetchone()
            big = big[0] if big else con.execute("SELECT MIN(id) FROM sessions").fetchone()[0]
            hid = con.execute("SELECT household_id FROM sessions WHERE stage!='closed' ORDER BY id LIMIT 1 OFFSET 4").fetchone()[0]
            con.close()
        else:
            big, hid = build(A, a.open, a.clients, a.photos, a.closed)
        pages = {"today": "/", "sessions": "/sessions", "clients": "/clients", "client": f"/client/{hid}",
                 "session": c.get(f"/session/{big}").location, "galleries": "/galleries", "finances": "/money"}
        print(f"{'page':<10} {'first':>8} {'median':>8} {'slowest':>8} {'size':>9}")
        for name, url in pages.items():
            if a.only and name not in a.only.split(","):
                continue
            try:
                first, med, worst, size = timed(c, url, a.runs)
            except AssertionError as e:
                print(f"{name:<10} failed: {e}")
                continue
            print(f"{name:<10} {first * 1000:7.0f}ms {med * 1000:7.0f}ms {worst * 1000:7.0f}ms {size // 1024:7d}KB")
        if a.profile:
            c.get(pages[a.profile])
            pr = cProfile.Profile()
            pr.enable()
            c.get(pages[a.profile])
            pr.disable()
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(25)
            print(s.getvalue())
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(40)
            print(s.getvalue())
        if not a.no_bulk:
            bulk(A, c, big)
    finally:
        if not a.keep:
            shutil.rmtree(folder, ignore_errors=True)

def bulk(A, c, sid):

    con = A.connect()
    ids = [r[0] for r in con.execute("SELECT id FROM photo_files WHERE session_id=? ORDER BY id", (sid,))]
    con.close()
    if not ids:
        return
    t = time.perf_counter()
    for fid in ids[:20]:
        r = c.post(f"/photo/{fid}/delete", headers={"X-Fetch": "1"})
        assert r.status_code == 200, r.data
    one = (time.perf_counter() - t) / 20
    print(f"delete one by one: {one * 1000:.0f} ms a file ({one * (len(ids) - 20):.1f} s for the other {len(ids) - 20})")
    if "photos_delete_many" not in A.app.view_functions:
        print("delete many at once: not offered")
        return
    with A.app.test_request_context():
        rows = A.bulk_photo_rows(sid, "upload")
    t = time.perf_counter()
    r = c.post(f"/session/{sid}/photos/delete", data=dict(what="upload", n=len(rows), first=rows[0]["id"]))
    took = time.perf_counter() - t
    assert r.status_code == 302, r.status_code
    con = A.connect()
    left = con.execute("SELECT COUNT(*) FROM photo_files WHERE session_id=?", (sid,)).fetchone()[0]
    lines = con.execute("SELECT COUNT(*) FROM audit WHERE session_id=? AND action LIKE 'Deleted %'", (sid,)).fetchone()[0]
    con.close()
    print(f"delete many at once: {len(rows)} files in {took:.1f} s ({left} left; {lines} History lines for all the deletes)")

if __name__ == "__main__":
    main()
