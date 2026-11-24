import os
import sys
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
PARENT = "Parent or guardian: Dana Lane"

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
    con.commit(); con.close()
    return A, c

def years_before(day, n):

    try:
        return day.replace(year=day.year - n)
    except ValueError:
        return day.replace(year=day.year - n, day=28)

def set_born(A, pid, born):
    con = A.connect()
    con.execute("UPDATE people SET birth_date=?, minor_box=NULL WHERE id=?", (born.isoformat(), pid))
    con.commit(); con.close()

def new_session(A, shoot):
    with A.app.test_request_context():
        sid = A.create_session(1, "family", stage="booked", shoot_date=shoot.isoformat() if shoot else None, subject_ids=(2,))
        A.db().commit()
    return sid

def release(A, sid):
    con = A.connect()
    try:
        return con.execute("SELECT id, status, signer FROM documents WHERE session_id=? AND kind='release' AND about_person=2",
                           (sid,)).fetchone()
    finally:
        con.close()

def ctx(A, fn, *args):
    with A.app.test_request_context():
        return fn(*args)

def save_ava(c, born, **extra):
    data = {"name": "Ava Lane", "relation": "daughter", "birth_date": born.isoformat() if born else "", "minor_shown": "1"}
    data.update(extra)
    return c.post("/person/2", data=data, environ_base=E)

def move_shoot(c, sid, day):
    return c.post(f"/session/{sid}", data={"type": "family", "shoot_date": day.isoformat()}, environ_base=E)

def test_turning_18_before_a_shoot_ahead_signs_their_own_release(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=10), 18))
    sid = new_session(A, t + timedelta(days=30))
    assert release(A, sid)[2] == ""
    ava = ctx(A, A.q, "SELECT * FROM people WHERE id=2", (), True)
    assert ctx(A, A.minor_on, ava) and not ctx(A, A.minor_on, ava, (t + timedelta(days=30)).isoformat())
    assert ctx(A, A.release_ctx, release(A, sid)[0])["minor"] is False
    assert ctx(A, A.agreement_ctx, sid)["minor"] is False

def test_turning_18_after_a_shoot_already_past_keeps_the_parent_signing(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t - timedelta(days=10), 18))
    sid = new_session(A, t - timedelta(days=30))
    assert release(A, sid)[2] == PARENT
    ava = ctx(A, A.q, "SELECT * FROM people WHERE id=2", (), True)
    assert not ctx(A, A.minor_on, ava)
    assert ctx(A, A.release_ctx, release(A, sid)[0])["minor"] is True
    assert ctx(A, A.agreement_ctx, sid)["minor"] is True

def test_the_signer_follows_the_shoot_date_when_it_moves(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=20), 18))
    sid = new_session(A, t + timedelta(days=40))
    assert release(A, sid)[2] == ""
    move_shoot(c, sid, t + timedelta(days=5))
    assert release(A, sid)[2] == PARENT
    move_shoot(c, sid, t + timedelta(days=60))
    assert release(A, sid)[2] == ""

def test_the_signer_follows_a_changed_birth_date(env):
    A, c = env
    t = A.today()
    sid = new_session(A, t + timedelta(days=30))
    assert release(A, sid)[2] == PARENT
    save_ava(c, years_before(t + timedelta(days=10), 18), is_minor="1")
    assert release(A, sid)[2] == ""
    save_ava(c, years_before(t + timedelta(days=50), 18), is_minor="1")
    assert release(A, sid)[2] == PARENT

def test_a_signed_release_never_changes(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=20), 18))
    sid = new_session(A, t + timedelta(days=5))
    assert release(A, sid)[2] == PARENT
    con = A.connect()
    con.execute("UPDATE documents SET status='signed', signed=? WHERE session_id=? AND kind='release'", (t.isoformat(), sid))
    con.commit(); con.close()
    move_shoot(c, sid, t + timedelta(days=60))
    save_ava(c, years_before(t - timedelta(days=400), 18))
    assert release(A, sid)[1:] == ("signed", PARENT)

def test_a_signer_typed_by_hand_stays(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=20), 18))
    sid = new_session(A, t + timedelta(days=5))
    con = A.connect()
    con.execute("UPDATE documents SET signer='Legal guardian: Sam Reyes' WHERE session_id=? AND kind='release'", (sid,))
    con.commit(); con.close()
    move_shoot(c, sid, t + timedelta(days=60))
    assert release(A, sid)[2] == "Legal guardian: Sam Reyes"

def test_no_shoot_date_judges_as_of_today(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=10), 18))
    sid = new_session(A, None)
    assert release(A, sid)[2] == PARENT
    move_shoot(c, sid, t + timedelta(days=30))
    assert release(A, sid)[2] == ""

def test_the_minor_box_set_against_the_birth_date_still_wins(env):
    A, c = env
    t = A.today()
    sid = new_session(A, t + timedelta(days=30))
    save_ava(c, years_before(t - timedelta(days=400), 18), is_minor="1")
    assert ctx(A, A.q, "SELECT is_minor, minor_box FROM people WHERE id=2", (), True)[:] == (1, 1)
    assert release(A, sid)[2] == PARENT
    save_ava(c, years_before(t - timedelta(days=400), 18))
    assert ctx(A, A.q, "SELECT is_minor, minor_box FROM people WHERE id=2", (), True)[:] == (0, None)
    assert release(A, sid)[2] == ""

def test_a_box_shown_as_the_birth_date_gives_it_is_not_a_lasting_choice(env, monkeypatch):

    A, c = env
    t = A.today()
    born = years_before(t + timedelta(days=10), 18)
    save_ava(c, born, is_minor="1")
    assert ctx(A, A.q, "SELECT minor_box FROM people WHERE id=2", (), True)[0] is None
    assert 'name="is_minor" value="1" checked' in c.get("/client/1", environ_base=E).get_data(as_text=True)
    monkeypatch.setattr(A, "today", lambda: t + timedelta(days=11))
    page = c.get("/client/1", environ_base=E).get_data(as_text=True)
    assert 'name="is_minor" value="1" checked' not in page

def test_a_new_baby_with_a_birth_date_is_a_minor_even_with_the_box_left_empty(env):
    A, c = env
    t = A.today()
    c.post("/client/1/person", data={"name": "Ivy Lane", "relation": "daughter", "birth_date": (t - timedelta(days=30)).isoformat(),
                                     "minor_shown": "1"}, environ_base=E)
    c.post("/client/1/person", data={"name": "Jo Lane", "relation": "aunt", "birth_date": "1990-05-01",
                                     "minor_shown": "1"}, environ_base=E)
    rows = dict(ctx(A, A.q, "SELECT name, is_minor FROM people WHERE name IN ('Ivy Lane', 'Jo Lane')"))
    assert rows == {"Ivy Lane": 1, "Jo Lane": 0}

def choices(A, sid):
    with A.app.test_request_context():
        return [p_["id"] for p_ in A.signer_choices(1, A.agreement_days(sid))]

def test_agreement_signer_choices_follow_the_shoot_date(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=10), 18))
    sid = new_session(A, t + timedelta(days=30))
    assert choices(A, sid) == [1, 2]
    with A.app.test_request_context():
        assert [p_["id"] for p_ in A.default_signers(1, "family", A.agreement_days(sid))] == [1]
    assert b'name="signer" value="2"' in c.get(f"/session/{sid}/edit", environ_base=E, follow_redirects=True).data
    c.post(f"/session/{sid}/signers", data={"signer": ["1", "2"]}, environ_base=E)
    con = A.connect()
    signs = [r_[0] for r_ in con.execute("SELECT about_person FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,))]
    con.close()
    assert signs == [1, 2] and release(A, sid) is None
    move_shoot(c, sid, t + timedelta(days=5))
    assert choices(A, sid) == [1]

def test_agreement_signer_choices_for_a_past_shoot_and_no_date(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t - timedelta(days=10), 18))
    past = new_session(A, t - timedelta(days=30))
    assert choices(A, past) == [1]
    c.post(f"/session/{past}/signers", data={"signer": ["2"]}, environ_base=E)
    con = A.connect()
    assert [r_[0] for r_ in con.execute("SELECT about_person FROM documents WHERE session_id=? AND kind='agreement'", (past,))] == [1]
    con.close()
    undated = new_session(A, None)
    assert choices(A, undated) == [1, 2]
    set_born(A, 2, years_before(t + timedelta(days=10), 18))
    assert choices(A, undated) == [1]

def test_a_plan_offers_only_who_is_grown_on_every_date(env):
    A, c = env
    t = A.today()
    set_born(A, 2, years_before(t + timedelta(days=20), 18))
    head = new_session(A, t + timedelta(days=40))
    later = new_session(A, t + timedelta(days=5))
    assert choices(A, head) == [1, 2]
    c.post(f"/session/{later}/plan", data={"plan_of": str(head)}, environ_base=E)
    assert choices(A, head) == [1] and choices(A, later) == [1]

def test_a_child_without_a_birth_date_is_not_offered(env):
    A, c = env
    con = A.connect()
    con.execute("UPDATE people SET is_minor=0 WHERE id=2")
    con.commit(); con.close()
    sid = new_session(A, A.today() + timedelta(days=30))
    assert choices(A, sid) == [1]
