import json
import os
import re
import sys
import threading
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_DEV", "1")
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def _studio(A, n=24):

    T = A.today()
    out = []
    with A.app.test_request_context():
        types = [k for k in A.C.SESSION_TYPES if k != A.C.EVENT_TYPE]
        stages = ["inquiry", "quoted", "booked", "shot", "delivered", "closed", "lost"]
        for i in range(n):
            hid = A.x("INSERT INTO households (id, name) VALUES (?,?)", (A.next_id("households"), f"Client {i}"))
            pid = A.x("INSERT INTO people (household_id, name, role, email) VALUES (?,?,?,?)",
                      (hid, f"Person {i}", "adult", f"p{i}@example.com"))
            st = stages[i % len(stages)]
            day = (T + timedelta(days=(i % 9) - 4)).isoformat() if st != "inquiry" else None
            sid = A.create_session(hid, types[i % len(types)], st, f"Session {i}", day, subject_ids=[pid])
            if i % 3:
                A.x("INSERT INTO lines (session_id, label, amount) VALUES (?,?,?)", (sid, "Fee", 200 + i))
            if i % 4 == 0:
                A.x("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?,?,?,?,?)",
                    (sid, "deposit", 50, T.isoformat(), "check"))
            if i % 5 == 0:
                A.x("UPDATE documents SET status='signed', signed=? WHERE session_id=?", (T.isoformat(), sid))
                A.x("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (?,?,?,?,?)",
                    (hid, sid, T.isoformat(), "email", "Asked about dates"))
            if i % 6 == 1:
                A.x("INSERT INTO tasks (session_id, stage, title, own, due) VALUES (?,?,?,1,?)", (sid, st, "My own step", day))
            if i % 7 == 2:
                A.x("INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?,?,?,?)", (sid, "deposit_paid", "na", ""))
            if st in ("shot", "delivered", "closed"):
                A.x("INSERT INTO stage_log (session_id, stage) VALUES (?, 'booked')", (sid,))
                A.x("INSERT INTO stage_log (session_id, stage) VALUES (?, ?)", (sid, st))
            out.append(sid)
        A.db().commit()
    return out

def _plain(w):
    return json.loads(json.dumps(w, default=lambda o: dict(o) if hasattr(o, "keys") else str(o)))

def test_prefetched_steps_are_the_same_as_one_session_at_a_time(env):
    A, _c = env
    sids = _studio(A)
    with A.app.test_request_context():
        alone = {sid: _plain(A._workflow(sid)) for sid in sids}
    with A.app.test_request_context():
        A.prefetch_workflows(sids)
        assert A.g.get("wf_pre"), "a list of this size is prefetched"
        together = {sid: _plain(A._workflow(sid)) for sid in sids}
        now = {sid: A.workflow_now(sid) for sid in sids}
    assert together == alone
    for sid in sids:
        full = alone[sid]
        n_ = _plain(now[sid])
        assert (n_["next_step"], n_["next_stage"], n_["ready"]) == (full["next_step"], full["next_stage"], full["ready"])
        assert n_["current"] == full["current"]

def test_a_write_drops_what_was_prefetched(env):
    A, _c = env
    sids = _studio(A)
    with A.app.test_request_context():
        A.prefetch_workflows(sids)
        A.x("UPDATE sessions SET title='x' WHERE id=?", (sids[0],))
        assert "wf_pre" not in A.g

def test_sessions_and_today_say_the_same_with_and_without_prefetch(env, monkeypatch):
    A, c = env
    _studio(A, 30)
    pages = {u: c.get(u).data for u in ("/", "/sessions", "/sessions?view=all", "/clients")}
    monkeypatch.setattr(A, "prefetch_workflows", lambda sids: None)
    monkeypatch.setattr(A, "prefetch_faces", lambda: None)
    for u, before in pages.items():
        assert c.get(u).data == before, u

def test_a_list_page_never_works_out_every_sessions_money_again_for_each_one(env, monkeypatch):

    A, c = env
    sids = _studio(A, 40)
    _sql(A, "UPDATE sessions SET priced_on=NULL")
    calls = []
    real = A._all_totals
    monkeypatch.setattr(A, "_all_totals", lambda: calls.append(1) or real())
    assert c.get("/sessions?view=all").status_code == 200
    assert len(calls) <= 2
    kept = _sql(A, "SELECT COUNT(*) FROM sessions WHERE priced_on IS NOT NULL")[0][0]
    priced = _sql(A, "SELECT COUNT(DISTINCT session_id) FROM lines")[0][0]
    assert kept == priced and len(sids) == 40

def test_clients_faces_and_business_sort_match_the_one_at_a_time_rules(env):
    A, c = env
    _studio(A, 12)
    _sql(A, "UPDATE households SET company='Bright Meadow Realty' WHERE id=3")
    _sql(A, "UPDATE households SET business=0 WHERE id=3")
    _sql(A, "UPDATE households SET business=1 WHERE id=4")
    _sql(A, "INSERT INTO households (id, name) VALUES (99, 'Solo')")
    with A.app.test_request_context():
        hids = [r[0] for r in A.q("SELECT id FROM households")]
        alone = {h: A._face(h) for h in hids}
        biz = {h: A.is_business_client(h) for h in hids}
        A.prefetch_faces()
        assert {h: A._face(h) for h in hids} == alone
    html = c.get("/clients").data.decode()
    assert html.count('class="av"') == len(hids)
    assert biz[3] is False and biz[4] is True

def jpeg(seed=0):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (32, 24), (seed * 40 % 255, 90, 160)).save(b, "JPEG")
    return b.getvalue()

@pytest.fixture
def shot(env):
    A, c = env
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Test Household')")
    _sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Sam Example', 'parent', 'sam@example.com')")
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_id) VALUES (1, 1, 'family', 'delivered', ?, 'g-testgallery01')",
         ((A.today() - timedelta(days=3)).isoformat(),))
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
        for i in range(5):
            A.store_photo(1, "export", f"IMG_{i:04d}.jpg", jpeg(i))
        A.store_photo(1, "export", "PORTRAIT_1.jpg", jpeg(9))
    return A, c

def _page(c, find=""):
    return c.get("/c/1?s=1" + (f"&find={find}" if find else "")).data.decode()

def _form(html, label):

    at = html.index(">" + label + "</button>")
    start = html.rindex("<form", 0, at)
    form = html[start:at]
    action = re.search(r'action="([^"]+)"', form).group(1)
    ask = re.search(r'data-confirm="([^"]*)"', form).group(1)
    return action, ask, dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))

def test_delete_this_upload_removes_the_whole_upload_with_one_history_line(shot):
    A, c = shot
    html = _page(c)
    assert "Delete this upload" in html
    url, ask, fields = _form(html, "Delete this upload")
    assert "Delete all 6 files of this upload?" in ask and "cannot be undone" in ask
    assert fields["what"] == "upload" and fields["n"] == "6"
    files = [r[0] for r in _sql(A, "SELECT id FROM photo_files WHERE session_id=1")]
    r = c.post(url, data=fields)
    assert r.status_code == 302
    assert not _sql(A, "SELECT 1 FROM photo_files WHERE session_id=1")
    assert all(not os.path.exists(os.path.join(os.environ["CRM_PHOTOS_ROOT"], f)) for f in files)
    lines = _sql(A, "SELECT action, note FROM audit WHERE session_id=1 AND action LIKE 'Deleted%'")
    assert len(lines) == 1 and lines[0][0] == "Deleted 6 edited exports" and lines[0][1].endswith(" and 3 more")
    assert "Deleted 6 edited exports." in _page(c)

def test_a_page_left_open_while_more_arrived_deletes_nothing(shot):
    A, c = shot
    _u, _a, fields = _form(_page(c), "Delete this upload")
    with A.app.test_request_context():
        A.store_photo(1, "export", "IMG_LATE.jpg", jpeg(7))
    r = c.post("/session/1/photos/delete", data=fields)
    assert r.status_code == 302
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE session_id=1")[0][0] == 7
    assert "changed since the page was shown" in _page(c)
    assert not _sql(A, "SELECT 1 FROM audit WHERE session_id=1 AND action LIKE 'Deleted%'")

def test_delete_the_files_that_match_takes_every_match_and_only_those(shot):
    A, c = shot
    html = _page(c, "IMG")
    url, ask, fields = _form(html, "Delete the 5 files that match")
    assert fields == dict(what="find", n="5", find="IMG", first="", back=fields["back"])
    assert c.post(url, data=fields).status_code == 302
    assert [r[0] for r in _sql(A, "SELECT name FROM photo_files WHERE session_id=1")] == ["PORTRAIT_1.jpg"]
    assert [r[0] for r in _sql(A, "SELECT action FROM audit WHERE session_id=1 AND action LIKE 'Deleted%'")] == ["Deleted 5 edited exports"]

def test_a_file_still_uploading_is_left_as_it_is(shot):
    A, c = shot
    import locks
    _u, _a, fields = _form(_page(c), "Delete this upload")
    busy = _sql(A, "SELECT id FROM photo_files WHERE name='IMG_0003.jpg'")[0][0]
    lk = locks.photo(busy)
    lk.acquire()
    try:
        c.post("/session/1/photos/delete", data=fields)
    finally:
        lk.release()
    assert [r[0] for r in _sql(A, "SELECT name FROM photo_files WHERE session_id=1")] == ["IMG_0003.jpg"]
    assert "1 still uploading was left as it is" in _page(c)

def test_one_file_shows_no_bulk_action(shot):
    A, c = shot
    _sql(A, "DELETE FROM photo_files WHERE name != 'PORTRAIT_1.jpg'")
    assert "Delete this upload" not in _page(c)

def test_uploads_finishing_together_share_one_history_line(shot):

    A, c = shot
    _sql(A, "DELETE FROM audit")
    rows = [dict(r) for r in _sql(A, "SELECT * FROM photo_files WHERE session_id=1")]
    go = threading.Barrier(len(rows))

    def finish(row):
        with A.app.test_request_context():
            go.wait()
            A._audit_upload(1, row)
            A.db().commit()
    ts = [threading.Thread(target=finish, args=(r_,)) for r_ in rows]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = _sql(A, "SELECT action, data FROM audit WHERE session_id=1")
    assert len(lines) == 1 and lines[0][0].startswith("Uploaded 6 edited exports")
    assert sorted(json.loads(lines[0][1])["names"]) == sorted(r_["name"] for r_ in rows)

def test_small_files_go_up_three_at_once_and_a_piece_is_the_line(env):
    A, _c = env
    import photostore
    js = open(os.path.join(ROOT, "static", "photos.js")).read()
    assert "const SMALL = 8 << 20;" in js and (8 << 20) == photostore.PIECE
    assert "pool.size >= 3" in js and "one at a time'" not in js

def test_the_timing_script_runs_on_a_small_studio(tmp_path):
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "load_timing.py"), "--open", "8", "--clients", "12",
                          "--photos", "25", "--closed", "6", "--runs", "1", "--keep", str(tmp_path / "studio")],
                         capture_output=True, text=True, timeout=300, env={k: v for k, v in os.environ.items() if not k.startswith("CRM_")})
    assert out.returncode == 0, out.stderr[-2000:]
    for page in ("today", "sessions", "clients", "client", "session", "galleries", "finances"):
        assert re.search(rf"^{page}\s+\d+ms", out.stdout, re.M), out.stdout
    assert "delete many at once: 5 files" in out.stdout and "(0 left; 21 History lines" in out.stdout

def _ticked(c, fids, n=None):
    data = [("what", "pick"), ("back", "/c/1?s=1#photos")] + [("pick", f) for f in fids]
    if n is not None:
        data.append(("n", str(n)))
    from werkzeug.datastructures import MultiDict
    return c.post("/session/1/photos/delete", data=MultiDict(data))

def test_every_thumbnail_has_a_tick_box_for_the_one_delete_form(shot):
    A, c = shot
    html = _page(c)
    assert html.count('form="pk-1"') == 6 and html.count('id="pk-1"') == 1
    assert ">Delete the selected files</button>" in html
    url, ask, fields = _form(html, "Delete the selected files")
    assert url == "/session/1/photos/delete" and fields["what"] == "pick" and "cannot be undone" in ask
    found = _page(c, "IMG")
    assert found.count('form="pk-1"') == 5 and found.count('id="pk-1"') == 1

def test_delete_the_ticked_ones_only_with_one_history_line(shot):
    A, c = shot
    ids = {n: i for i, n in _sql(A, "SELECT id, name FROM photo_files WHERE session_id=1")}
    assert _ticked(c, [ids["IMG_0001.jpg"], ids["PORTRAIT_1.jpg"], ids["IMG_0004.jpg"]], n=3).status_code == 302
    assert sorted(r[0] for r in _sql(A, "SELECT name FROM photo_files WHERE session_id=1")) == \
        ["IMG_0000.jpg", "IMG_0002.jpg", "IMG_0003.jpg"]
    assert [r[0] for r in _sql(A, "SELECT action FROM audit WHERE session_id=1 AND action LIKE 'Deleted%'")] == \
        ["Deleted 3 edited exports"]
    assert "Deleted 3 edited exports." in _page(c)

def test_without_script_the_ticked_ones_are_counted_by_the_app(shot):
    A, c = shot
    ids = [r[0] for r in _sql(A, "SELECT id FROM photo_files WHERE session_id=1 LIMIT 2")]
    assert _ticked(c, ids).status_code == 302
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE session_id=1")[0][0] == 4

def test_a_ticked_file_gone_since_the_page_was_shown_deletes_nothing(shot):
    A, c = shot
    ids = [r[0] for r in _sql(A, "SELECT id FROM photo_files WHERE session_id=1 ORDER BY name LIMIT 3")]
    _sql(A, "DELETE FROM photo_files WHERE id=?", (ids[0],))
    _ticked(c, ids, n=3)
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE session_id=1")[0][0] == 5
    assert "changed since the page was shown" in _page(c)
    _ticked(c, ids[1:], n=3)
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE session_id=1")[0][0] == 5
    assert not _sql(A, "SELECT 1 FROM audit WHERE session_id=1 AND action LIKE 'Deleted%'")
    _ticked(c, [], n=0)
    assert "Tick the photos to delete first" in _page(c)

def test_a_ticked_file_still_uploading_is_left_as_it_is(shot):
    A, c = shot
    import locks
    ids = {n: i for i, n in _sql(A, "SELECT id, name FROM photo_files WHERE session_id=1")}
    lk = locks.photo(ids["IMG_0002.jpg"])
    lk.acquire()
    try:
        _ticked(c, [ids["IMG_0002.jpg"], ids["IMG_0003.jpg"]], n=2)
    finally:
        lk.release()
    assert "IMG_0002.jpg" in [r[0] for r in _sql(A, "SELECT name FROM photo_files")]
    assert "IMG_0003.jpg" not in [r[0] for r in _sql(A, "SELECT name FROM photo_files")]

def test_tick_and_delete_in_place_on_a_phone(shot):

    pytest.importorskip("playwright")
    from test_262_155 import BASE, _tab
    from playwright import sync_api
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    A, c = shot
    ctx, p = _tab(b, c)
    try:
        p.set_viewport_size({"width": 390, "height": 844})
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        row = p.locator("[data-photo-picked]")
        assert row.is_hidden()
        boxes = p.locator("#photos input[name=pick]")
        assert boxes.count() == 6
        boxes.nth(0).check()
        assert row.is_visible() and row.locator("button").inner_text() == "Delete the selected file"
        boxes.nth(2).check()
        assert row.locator("button").inner_text() == "Delete the 2 selected"
        asked = []
        p.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        row.locator("button").click()
        p.wait_for_function("document.querySelectorAll('#photos input[name=pick]').length === 4", timeout=15_000)
        assert asked and asked[0].startswith("Delete the 2 selected files?")
        assert p.evaluate("window.__stay") == 1
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE session_id=1")[0][0] == 4
    finally:
        ctx.close()
        b.close()
        pw.stop()
