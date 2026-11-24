import os
import sys
from datetime import date, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

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
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_engagements": "1", "preset_families": "1",
                                 "preset_portraits": "1"}, environ_base=E)
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        out = con.execute(q_, args).fetchall()
        con.commit()
        return out
    finally:
        con.close()

def person(A, hid, name, rel="", email="", minor=0, role=None):
    role = role or ("parent" if rel in A.PARENT_REL else "child" if minor else "adult")
    return sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (?,?,?,?,?,?) RETURNING id",
               (hid, name, rel, role, email, minor))[0][0]

def session(A, hid, type_, days=60, subjects=None):
    with A.app.test_request_context():
        sid = A.create_session(hid, type_, stage="booked", shoot_date=(date.today() + timedelta(days=days)).isoformat(),
                               subject_ids=subjects or ())
        A.db().commit()
    return sid

def signers(A, sid):
    return [r_[0] for r_ in sql(A, """SELECT p.name FROM documents d JOIN people p ON p.id=d.about_person
                                      WHERE d.session_id=? AND d.kind='agreement' ORDER BY d.id""", (sid,))]

@pytest.mark.parametrize("type_", ["wedding", "engagement"])
def test_a_partner_with_no_relationship_stays_in_the_couple(env, type_):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Reed')")
    person(A, 1, "Avery Reed", "", "avery@example.com")
    person(A, 1, "Jordan Hale", "partner", "jordan@example.com")
    sid = session(A, 1, type_)
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, type_)] == ["Avery Reed", "Jordan Hale"]
        v = A._send_ctx(sid, "quote", "Q", ["x"])
        assert v["to"] == "avery@example.com, jordan@example.com"
        assert "Hi Avery and Jordan," in unquote(v["mailto"])
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Avery Reed and Jordan Hale"
    assert signers(A, sid) == ["Avery Reed", "Jordan Hale"]

def test_an_elopement_type_keeps_both_partners(env, monkeypatch):
    A, _ = env
    monkeypatch.setattr(A.C, "COUPLE_TYPES", A.C.COUPLE_TYPES | {"elope"})
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Reed')")
    person(A, 1, "Avery Reed", "", "avery@example.com")
    person(A, 1, "Jordan Hale", "fiance", "jordan@example.com")
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, "elope")] == ["Avery Reed", "Jordan Hale"]

def test_the_blank_partner_rule_never_lets_in_a_parent_planner_or_third_adult(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Reed')")
    person(A, 1, "Quinn Planner", "planner", "quinn@example.com")
    person(A, 1, "Avery Reed", "", "avery@example.com")
    person(A, 1, "Morgan Reed", "mother", "morgan@example.com")
    person(A, 1, "Jordan Hale", "partner", "jordan@example.com")
    person(A, 1, "Sam Guest", "", "")
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, "wedding")] == ["Avery Reed", "Jordan Hale"]

def test_two_named_partners_still_win_over_a_blank_person_who_booked(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Reed')")
    person(A, 1, "Sam Friend", "", "sam@example.com")
    person(A, 1, "Avery Reed", "bride", "avery@example.com")
    person(A, 1, "Jordan Hale", "groom", "jordan@example.com")
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, "wedding")] == ["Avery Reed", "Jordan Hale"]

def family(A):

    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Moss')")
    ruth = person(A, 1, "Ruth Moss", "", "ruth@example.com")
    ned = person(A, 1, "Ned Moss", "son", "", minor=1)
    bea = person(A, 1, "Bea Moss", "grandmother", "bea@example.com")
    return ruth, ned, bea

def test_the_person_who_booked_stays_the_default_over_a_grandparent(env):
    A, _ = env
    _, ned, _ = family(A)
    sid = session(A, 1, "family", subjects=[ned])
    with A.app.test_request_context("/"):
        assert A._send_ctx(sid, "quote", "Q", ["x"])["to"] == "ruth@example.com"
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Ruth Moss"
        assert [p_["name"] for p_ in A.default_signers(1, "family")] == ["Ruth Moss"]
        assert A.signer_choices(1)[0]["name"] == "Ruth Moss"
    assert signers(A, sid) == ["Ruth Moss"]

def test_a_grandparent_is_never_named_parent_or_guardian_on_a_release(env):
    A, _ = env
    _, ned, _ = family(A)
    sid = session(A, 1, "family", subjects=[ned])
    rel_ = sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='release' AND about_person=?", (sid, ned))
    assert rel_ and "Bea" not in rel_[0][0]
    assert rel_[0][0] == "Parent or guardian (required)"

def test_a_typed_mother_is_named_on_the_release_before_a_grandparent(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Moss')")
    person(A, 1, "Bea Moss", "grandmother", "bea@example.com")
    ned = person(A, 1, "Ned Moss", "son", "", minor=1)
    person(A, 1, "Ruth Moss", "mother", "ruth@example.com")
    sid = session(A, 1, "family", subjects=[ned])
    rel_ = sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='release' AND about_person=?", (sid, ned))
    assert rel_[0][0] == "Parent or guardian: Ruth Moss"
    assert signers(A, sid) == ["Bea Moss"]

def test_a_typed_parent_who_booked_is_unchanged(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Moss')")
    person(A, 1, "Ruth Moss", "mother", "ruth@example.com")
    ned = person(A, 1, "Ned Moss", "son", "", minor=1)
    person(A, 1, "Bea Moss", "grandmother", "bea@example.com")
    sid = session(A, 1, "family", subjects=[ned])
    with A.app.test_request_context("/"):
        assert A._send_ctx(sid, "quote", "Q", ["x"])["to"] == "ruth@example.com"
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Ruth Moss"
    assert signers(A, sid) == ["Ruth Moss"]
    rel_ = sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='release' AND about_person=?", (sid, ned))
    assert rel_[0][0] == "Parent or guardian: Ruth Moss"

def lit(A, monkeypatch, tz="America/New_York"):
    monkeypatch.setattr(A.sun, "HOME", (42.44, -76.5))
    monkeypatch.setattr(A.sun, "TZ", ZoneInfo(tz))
    sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('home_zip', '14850')")

def test_light_far_away_is_for_the_place_in_its_own_time(env, monkeypatch):
    A, _ = env
    lit(A, monkeypatch)
    day = "2027-06-12"
    with A.app.test_request_context("/"):
        L = A.light(day, "engagement", "Delicate Arch trailhead\nMoab, UT 84532")
        home = A.light(day, "engagement")
    g_, s_ = A.sun.evening(date(2027, 6, 12), 38.52, -109.535, ZoneInfo("America/Denver"))
    assert L["sunset"] == A.clock(s_) != home["sunset"]
    assert "local time" in L["text"] and "home" not in L["text"]

def test_light_in_hawaii_by_state_name(env, monkeypatch):
    A, _ = env
    lit(A, monkeypatch)
    with A.app.test_request_context("/"):
        L = A.light("2027-03-02", "wedding", "Kaanapali Beach, Lahaina, Hawaii 96761")
    s_ = A.sun.evening(date(2027, 3, 2), 20.897, -156.614, ZoneInfo("Pacific/Honolulu"))[1]
    assert abs((s_.hour * 60 + s_.minute) - 18 * 60 - 40) < 30
    assert L["sunset"] == A.clock(s_)

def test_light_near_home_reads_as_before(env, monkeypatch):
    A, _ = env
    lit(A, monkeypatch)
    with A.app.test_request_context("/"):
        L = A.light("2027-06-12", "family", "Taughannock Falls, Trumansburg, NY 14886")
    assert L["text"] == f"Golden hour {L['golden']}, sunset {L['sunset']}"

@pytest.mark.parametrize("place", ["Lake Como, Italy", "Rue de Rivoli, 75001 Paris", "Somewhere quiet", ""])
def test_light_for_a_place_it_cannot_place_says_it_is_for_home(env, monkeypatch, place):
    A, _ = env
    lit(A, monkeypatch)
    with A.app.test_request_context("/"):
        L = A.light("2027-06-12", "wedding", place)
        home = A.light("2027-06-12", "wedding")
    assert L["sunset"] == home["sunset"]
    assert L["text"].endswith("(times for home, ZIP 14850)")

def test_a_zip_that_does_not_match_its_state_is_not_trusted(env, monkeypatch):
    A, _ = env
    lit(A, monkeypatch)
    with A.app.test_request_context("/"):
        L = A.light("2027-06-12", "wedding", "Main St, CA 84532")
    assert "times for home" in L["text"]

def test_the_client_page_today_and_details_use_the_shoots_place(env, monkeypatch):
    A, c = env
    lit(A, monkeypatch)
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Reed')")
    person(A, 1, "Avery Reed", "", "avery@example.com")
    sid = session(A, 1, "engagement", days=3)
    sql(A, "UPDATE sessions SET stops=? WHERE id=?", ("Moab, UT 84532", sid))
    shoot_day = (date.today() + timedelta(days=3))
    s_ = A.sun.evening(shoot_day, 38.52, -109.535, ZoneInfo("America/Denver"))[1]
    want = f"sunset {A.clock(s_)} (local time at the shoot)"
    page = c.get(f"/session/{sid}", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert want in page
