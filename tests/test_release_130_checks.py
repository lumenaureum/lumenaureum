import os
import re
from datetime import timedelta
import sys

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
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c, tmp_path

def _open_session(A):
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'Lopez')")
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'inquiry',"
                    " '2027-06-01')")
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()

@pytest.mark.parametrize("step, form", [
    ("galleries", dict(gallery_days="30", unopened_days="365")),
    ("documents", {}),
])
def test_a_setup_step_saves_again_with_a_session_open(env, step, form):

    A, c, _ = env
    _open_session(A)
    if step == "documents":
        page = c.get(f"/setup/{step}", environ_base=E).get_data(as_text=True)
        form = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page))
    r = c.post(f"/setup/{step}", data=form, environ_base=E)
    assert r.status_code in (200, 302), r.status_code
    assert b"Internal Server Error" not in r.data

def test_the_one_disk_button_is_not_the_enter_key(env, monkeypatch):

    A, c, tmp = env
    import setup
    monkeypatch.setattr(setup, "backups_share_disk", lambda cfg=None: True)
    vault = tmp / "vault"
    vault.mkdir()
    (vault / ".vault-root").write_text("")
    r = c.post("/setup/storage", data=dict(primary_type="local", primary_path=str(vault)), environ_base=E)
    page = r.get_data(as_text=True)
    assert "Continue anyway" in page
    form_at = page.index('id="f"')
    owned = [m.start() for m in re.finditer(r'<button[^>]*form="f"', page)]
    submit_before_form = [i for i in owned if i < form_at]
    assert not submit_before_form, "a button that accepts the warning comes first in tree order for form f"

@pytest.fixture
def booked_tentative(env):

    A, c, _ = env
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
        con.execute("""INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
                       VALUES (1, 1, 'portrait', 'booked', ?, 1)""",
                    ((A.today() - timedelta(days=5)).isoformat(),))
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    return A, c

def test_setting_the_real_date_for_a_tentative_one_is_not_a_reschedule(booked_tentative):

    A, c = booked_tentative
    new = (A.today() + timedelta(days=10)).isoformat()
    c.post("/session/1/quick", data={"act": "date", "firm": "1", "shoot_date": new, "start_time": "10:00",
                                     "where": "Riverside park"}, environ_base=E)
    con = A.connect()
    try:
        assert con.execute("SELECT shoot_date, date_tentative FROM sessions WHERE id=1").fetchone()[0] == new
    finally:
        con.close()
    with A.app.test_request_context():
        assert A.reschedules(1) == 0
    assert "Rescheduled" not in c.get("/c/1", environ_base=E).get_data(as_text=True)

def test_a_passed_tentative_date_is_not_shown_as_shot(booked_tentative):

    A, c = booked_tentative
    page = c.get("/c/1", environ_base=E).get_data(as_text=True)
    head = page.split('class="hn-when"', 1)[1].split("</div>", 1)[0]
    assert "Shot " not in head

def test_a_timeline_day_mark_with_a_count_has_a_role_for_its_label(env):

    A, c, _ = env
    _open_session(A)
    con = A.connect()
    try:
        for what in ("Called about the date", "Sent the price list", "Talked about outfits"):
            con.execute("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (1, 1, ?, 'call', ?)",
                        (A.today().isoformat() + " 10:00:00", what))
        con.commit()
    finally:
        con.close()
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    marks = re.findall(r'<div class="jm[^"]*"[^>]*data-tip-lines[^>]*>', page)
    assert marks, "three things on one day make one mark with a count"
    for m in marks:
        assert "aria-label" not in m or re.search(r'\brole="(img|button|note|listitem|group)"', m), m
