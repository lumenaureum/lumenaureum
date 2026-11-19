import os
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
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (1, 1, 'Dana Lane', 'parent', 0)")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (2, 1, 'Ava Lane', 'child', 1)")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (3, 1, 'Ben Lane', 'child', 1)")
    con.commit(); con.close()
    return A, c

def _new_session(A, type_="family", subjects=(2,)):
    with A.app.test_request_context():
        sid = A.create_session(1, type_, stage="booked", shoot_date="2027-06-01", subject_ids=subjects)
        A.db().commit()
    return sid

def _releases(A, sid):
    con = A.connect()
    try:
        return {r_[0]: (r_[1], r_[2]) for r_ in con.execute(
            "SELECT about_person, status, signer FROM documents WHERE session_id=? AND kind='release'", (sid,))}
    finally:
        con.close()

def _save_subjects(c, sid, pids):
    return c.post(f"/session/{sid}", data={"subjects_present": "1", "subjects": [str(p_) for p_ in pids], "type": "family",
                                           "shoot_date": "2027-06-01"}, environ_base=E)

def test_a_subject_added_after_booking_gets_a_release(env):
    A, c = env
    sid = _new_session(A)
    assert set(_releases(A, sid)) == {2}
    _save_subjects(c, sid, [2, 3])
    rel = _releases(A, sid)
    assert set(rel) == {2, 3} and rel[3] == ("needed", "Parent or guardian: Dana Lane")
    _save_subjects(c, sid, [1, 2, 3])
    assert set(_releases(A, sid)) == {2, 3}

def test_a_subject_taken_off_loses_an_unsigned_release_but_never_a_signed_one(env):
    A, c = env
    sid = _new_session(A, subjects=(2, 3))
    con = A.connect()
    con.execute("UPDATE documents SET status='signed', signed='2026-11-01' WHERE session_id=? AND kind='release' AND about_person=2", (sid,))
    con.commit(); con.close()
    _save_subjects(c, sid, [])
    assert _releases(A, sid) == {2: ("signed", "Parent or guardian: Dana Lane")}

def test_a_double_click_records_a_payment_or_adds_a_line_once(env):
    import re
    A, c = env
    sid = _new_session(A)
    page = c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)
    forms = re.findall(r'name="what" value="(line|payment|expense)"><input type="hidden" name="once" value="([^"]+)"', page)
    toks = dict(forms)
    assert set(toks) == {"line", "payment", "expense"}
    for _ in range(2):
        c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300", "once": toks["line"]},
               environ_base=E)
        c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "once": toks["payment"]},
               environ_base=E)
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM lines WHERE session_id=? AND label='Session'", (sid,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM payments WHERE session_id=?", (sid,)).fetchone()[0] == 1
    con.close()
    for _ in range(2):
        c.post(f"/session/{sid}/quote/fee", data={"fee_key": "custom", "label": "Extra hour", "amount": "75", "once": "fee-form-1"},
               environ_base=E)
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM lines WHERE session_id=? AND label='Extra hour'", (sid,)).fetchone()[0] == 1
    con.close()
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "50", "once": "a-fresh-form"},
           environ_base=E)
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM payments WHERE session_id=?", (sid,)).fetchone()[0] == 2
    con.close()

def test_a_double_click_on_a_paperwork_status_moves_it_one_step(env):
    A, c = env
    sid = _new_session(A)
    con = A.connect()
    did = con.execute("SELECT id FROM documents WHERE session_id=? AND kind='release'", (sid,)).fetchone()[0]
    con.close()
    page = c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)
    assert f'action="/doc/{did}"' in page and 'name="from" value="needed"' in page
    for _ in range(2):
        c.post(f"/doc/{did}", data={"cycle": "1", "from": "needed"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()[0] == "sent"
    con.close()

def test_a_client_delete_that_fails_on_a_gallery_destroys_no_photos(env):
    A, c = env
    s1, s2 = _new_session(A), _new_session(A)
    down, purged = [], []

    def take_down(sid):
        if sid == s2:
            raise OSError("web host unreachable")
        down.append(sid)
    old = dict(A.app.config)
    A.app.config["gallery_take_down"] = take_down
    A.app.config["retention_purge_now"] = purged.append
    try:
        with A.app.test_request_context():
            con = A.connect()
            created = con.execute("SELECT created FROM households WHERE id=1").fetchone()[0]
            con.close()
            with pytest.raises(RuntimeError, match="Could not take a gallery down"):
                A._delete_client(1, created)
    finally:
        A.app.config.clear(); A.app.config.update(old)
    assert purged == []
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM sessions WHERE household_id=1").fetchone()[0] == 2
    con.close()

def test_a_deposit_rule_changed_mid_season_leaves_quoted_bookings_alone(env, monkeypatch):
    A, c = env
    import config as C
    monkeypatch.setattr(C, "DEPOSIT_RULE", {})
    monkeypatch.setattr(C, "DEPOSIT_AMOUNT", 100)
    sid = _new_session(A)
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "500"}, environ_base=E)
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100"}, environ_base=E)
    monkeypatch.setattr(C, "DEPOSIT_AMOUNT", 250)
    with A.app.test_request_context():
        t_ = A.totals(sid)
        assert (t_["deposit_req"], t_["deposit_short"]) == (100, 0)
        assert A._totals(sid)["deposit_req"] == 100
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Album", "amount": "200"}, environ_base=E)
    with A.app.test_request_context():
        assert A.totals(sid)["quoted"] == 500 and A.totals(sid)["deposit_req"] == 100

    c.post(f"/session/{sid}/quote/replace", environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Album", "amount": "200"}, environ_base=E)
    c.post(f"/session/{sid}/quick", data={"act": "rebook"}, environ_base=E)
    with A.app.test_request_context():
        t_ = A.totals(sid)
        assert (t_["quoted"], t_["deposit_req"], t_["deposit_short"]) == (700, 250, 150)

def test_a_family_that_dropped_out_of_an_event_is_left_alone(env):
    A, c = env
    con = A.connect()
    con.execute("INSERT INTO events (id, name, date, billing) VALUES (1, 'First Communion', '2026-05-02', 'families')")
    for hid, name in ((2, "Bell"), (3, "Ruiz")):
        con.execute("INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
        con.execute("INSERT INTO sessions (id, household_id, type, event_id, stage) VALUES (?, ?, 'event', 1, 'booked')", (hid, hid))
        con.execute("INSERT INTO documents (session_id, kind) VALUES (?, 'release')", (hid,))
    con.execute("UPDATE sessions SET stage='lost', lost_reason='Canceled' WHERE id=3")
    con.execute("INSERT INTO tasks (id, session_id, title, stage) VALUES (900, 2, 'Group photo list', 'booked')")
    con.execute("INSERT INTO tasks (id, session_id, title, stage) VALUES (901, 3, 'Group photo list', 'booked')")
    con.commit(); con.close()
    c.post("/event/1/family-price", data={"amount": "60"}, environ_base=E)
    c.post("/event/1/releases", data={}, environ_base=E)
    c.post("/event/1/mark", data={"field": "letter_mailed"}, environ_base=E)
    c.post("/event/1/task", data={"title": "Group photo list"}, environ_base=E)
    c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E)
    con = A.connect()
    try:
        st = dict(con.execute("SELECT id, stage FROM sessions WHERE event_id=1").fetchall())
        rel = dict(con.execute("SELECT session_id, status FROM documents WHERE kind='release'").fetchall())
        priced = {r_[0] for r_ in con.execute("SELECT session_id FROM lines WHERE kind!='tax'")}
        mailed = dict(con.execute("SELECT id, letter_mailed FROM sessions WHERE event_id=1").fetchall())
        done = dict(con.execute("SELECT id, done FROM tasks WHERE id IN (900, 901)").fetchall())
    finally:
        con.close()
    assert st == {2: "shot", 3: "lost"}
    assert rel == {2: "signed", 3: "needed"}
    assert priced == {2}
    assert mailed[2] and not mailed[3]
    assert done[900] and not done[901]
