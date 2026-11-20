import os
import re
import sys
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def one(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def _booked(A, days, tentative=1, sid=1):
    run(A, "INSERT OR IGNORE INTO households (id, name) VALUES (1, 'Lane')")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (?, 1, 'portrait', 'booked', ?, ?)""", (sid, (A.today() + timedelta(days=days)).isoformat(), tentative))
    with A.app.test_request_context():
        A.sync_workflow_tasks(sid)
        A.db().commit()

def test_the_real_date_for_a_tentative_one_reopens_nothing_and_a_later_move_still_counts(env):
    A, c = env
    _booked(A, -5)
    run(A, "UPDATE tasks SET done=? WHERE session_id=1 AND title IN ('Send session details', 'Confirm time and place')",
        (A.today().isoformat(),))
    done_before = one(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND done IS NOT NULL")[0]
    new = (A.today() + timedelta(days=10)).isoformat()
    c.post("/session/1/quick", data={"act": "date", "firm": "1", "shoot_date": new, "start_time": "10:00"}, environ_base=E)
    assert tuple(one(A, "SELECT shoot_date, date_tentative FROM sessions WHERE id=1")) == (new, 0)
    assert one(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND done IS NOT NULL")[0] == done_before
    assert not one(A, "SELECT COUNT(*) FROM audit WHERE session_id=1 AND action LIKE 'Rescheduled%'")[0]
    later = (A.today() + timedelta(days=12)).isoformat()
    c.post("/session/1/quick", data={"act": "date", "shoot_date": later, "start_time": "10:00"}, environ_base=E)
    with A.app.test_request_context():
        assert A.reschedules(1) == 1

def test_a_tentative_date_changed_on_the_session_page_is_not_a_reschedule(env):
    A, c = env
    _booked(A, 20)
    form = dict(title="", type="portrait", shoot_date=(A.today() + timedelta(days=30)).isoformat(), start_time="",
                stops="", notes="", date_tentative_present="1", date_tentative="1")
    c.post("/session/1", data=form, environ_base=E)
    assert one(A, "SELECT shoot_date FROM sessions WHERE id=1")[0] == form["shoot_date"]
    with A.app.test_request_context():
        assert A.reschedules(1) == 0

def test_a_passed_tentative_date_reads_date_to_confirm(env):
    A, c = env
    _booked(A, -5)
    head = c.get("/c/1", environ_base=E).get_data(as_text=True).split('class="hn-when"', 1)[1].split("</div>", 1)[0]
    assert "Date to confirm" in head and "was tentative" in head and "Shot " not in head

def test_a_first_visit_with_no_gallery_is_saved_without_asking(env):
    A, c = env
    run(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'delivered', '2025-06-01')")
    r = c.post("/session/1/delivery", data={"first_visit": "2025-06-12", "favorites": "", "veto": ""}, environ_base=E)
    assert "This closes the gallery now" not in r.get_data(as_text=True)
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] == "2025-06-12"

def test_ticking_minor_fills_in_the_parent_on_unsigned_releases(env):
    A, c = env
    run(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO people (id, household_id, name, role) VALUES (1, 1, 'Dana Lane', 'parent')")
    run(A, "INSERT INTO people (id, household_id, name, role, relation) VALUES (2, 1, 'Ivy Lane', 'adult', 'daughter')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'portrait', 'booked')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (2, 1, 'portrait', 'booked')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (3, 1, 'portrait', 'booked')")
    run(A, "INSERT INTO documents (id, session_id, kind, about_person, signer) VALUES (1, 1, 'release', 2, '')")
    run(A, "INSERT INTO documents (id, session_id, kind, about_person, signer) VALUES (2, 2, 'release', 2, 'Aunt May, guardian')")
    run(A, "INSERT INTO documents (id, session_id, kind, about_person, signer, status) VALUES (3, 3, 'release', 2, '', 'signed')")
    form = {"name": "Ivy Lane", "relation": "daughter", "minor_shown": "1", "is_minor": "1"}
    c.post("/person/2", data=form, environ_base=E)
    assert one(A, "SELECT is_minor FROM people WHERE id=2")[0] == 1
    assert one(A, "SELECT signer FROM documents WHERE id=1")[0] == "Parent or guardian: Dana Lane"
    assert one(A, "SELECT signer FROM documents WHERE id=2")[0] == "Aunt May, guardian"
    assert one(A, "SELECT signer FROM documents WHERE id=3")[0] == ""
    c.post("/person/2", data=dict(form, is_minor=""), environ_base=E)
    assert one(A, "SELECT signer FROM documents WHERE id=1")[0] == ""

def test_a_minor_with_no_parent_on_file_reads_required(env):
    A, c = env
    run(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO people (id, household_id, name, role) VALUES (2, 1, 'Ivy Lane', 'adult')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'portrait', 'booked')")
    run(A, "INSERT INTO documents (id, session_id, kind, about_person, signer) VALUES (1, 1, 'release', 2, '')")
    c.post("/person/2", data={"name": "Ivy Lane", "minor_shown": "1", "is_minor": "1"}, environ_base=E)
    assert one(A, "SELECT signer FROM documents WHERE id=1")[0] == "Parent or guardian (required)"

def test_review_links_for_a_removed_type_go_to_the_general_wording(env):
    A, c = env
    import config as C
    with A.app.test_request_context():
        assert A.review_url("terms", "event") == "/settings/type/event#terms"
        was = C.TYPES
        C.TYPES = [t for t in was if t["key"] != "event"]
        try:
            assert A.review_url("terms", "event") == A.url_for("settings_view", tab="prices") + "#terms"
            assert A.review_url("agreement", "event") == A.url_for("wording_edit", doc="agreement")
        finally:
            C.TYPES = was

def _upload(c, sid, name, data, kind):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name}, environ_base=E)
    if st.status_code != 200:
        return st
    r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset=0", data=data,
               headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
    return r

def test_edited_exports_refuse_a_file_that_is_not_an_image(env):
    import io
    from PIL import Image
    A, c = env
    run(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'portrait', 'shot')")
    r = _upload(c, 1, "notes.jpg", b"These are my notes, not a photo." * 40, "export")
    assert r.status_code == 400 and "isn't a JPEG or PNG image" in r.get_json()["message"]
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 30)).save(b, "JPEG")
    cut = b.getvalue()[:-200]
    r = _upload(c, 1, "cut.jpg", cut, "export")
    assert r.status_code == 400 and "isn't a JPEG or PNG image" in r.get_json()["message"]
    assert not one(A, "SELECT COUNT(*) FROM photo_files")[0]
    r = _upload(c, 1, "good.jpg", b.getvalue(), "export")
    assert r.status_code == 200 and r.get_json()["done"]
    p = io.BytesIO()
    Image.new("RGB", (8, 8)).save(p, "PNG")
    assert _upload(c, 1, "good.png", p.getvalue(), "final").get_json()["done"]
    assert _upload(c, 1, "IMG_1.NEF", b"not checked: RAWs are any camera's format" * 10, "raw").get_json()["done"]

def test_sign_in_and_wording_fields_have_labels(env):
    A, c = env
    page = c.get("/settings/wording/agreement", environ_base=E).get_data(as_text=True)
    for field in re.findall(r"<(?:select|textarea)[^>]*>", page):
        fid = re.search(r'id="([^"]+)"', field)
        assert fid and f'for="{fid.group(1)}"' in page, field
    out = A.app.test_client()
    page = out.get("/login", environ_base=E).get_data(as_text=True)
    for name in ("email", "password"):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', page).group(0)
        assert "placeholder" not in field
        fid = re.search(r'id="([^"]+)"', field).group(1)
        assert f'for="{fid}"' in page

def _ratio(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    x, y = sorted((lum(a), lum(b)))
    return (y + 0.05) / (x + 0.05)

def test_the_studio_page_footer_credit_reads(env):
    src = open(os.path.join(ROOT, "plugins", "studio_page.py")).read()
    small = re.search(r"#la small \{\{[^}]*color: #([0-9a-f]{6})", src).group(1)
    footer = re.search(r"footer \{\{ background: #([0-9a-f]{6})", src).group(1)
    assert _ratio(small, footer) >= 4.5
