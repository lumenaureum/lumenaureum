import html
import os
import re
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
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        out = con.execute(q_, args).fetchall()
        con.commit()
        return [tuple(r_) for r_ in out]
    finally:
        con.close()

def text(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def _event(c, people=False):
    c.post("/events", data={"name": "Spring day", "date": "2027-05-04", "venue": "Main hall"}, environ_base=E)
    if people:
        c.post("/event/1/attendees", data={"attendees": "people"}, environ_base=E)

def _release_of(A, name):
    return sql(A, """SELECT d.id, d.signer FROM documents d JOIN people p ON p.id=d.about_person
                     WHERE d.kind='release' AND p.name=?""", (name,))[0]

def test_grown_child_typed_with_age_is_not_a_minor(env):
    A, c = env
    _event(c)
    c.post("/event/1/family", data={"household_name": "The Rivera family", "parent_name": "Ana Rivera",
                                    "children": "Jordan 18, Sam 9"}, environ_base=E)
    rows = dict((n, m) for n, m in sql(A, "SELECT name, is_minor FROM people WHERE role='child'"))
    assert rows == {"Jordan": 0, "Sam": 1}
    assert _release_of(A, "Jordan")[1] == ""
    assert _release_of(A, "Sam")[1] == "Parent or guardian: Ana Rivera"
    page = text(c.get(f"/doc/{_release_of(A, 'Jordan')[0]}/release", environ_base=E).get_data(as_text=True))
    assert "I am the parent or legal guardian" not in page
    page = text(c.get(f"/doc/{_release_of(A, 'Sam')[0]}/release", environ_base=E).get_data(as_text=True))
    assert "I am the parent or legal guardian of Sam" in page

def test_majority_age_setting_decides(env):
    A, c = env
    sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('age_majority', '19')")
    _event(c)
    c.post("/event/1/family", data={"household_name": "Reunion: the Okafors", "parent_name": "Grace Okafor",
                                    "children": "Tobi 18 and Ada 40"}, environ_base=E)
    rows = dict((n, m) for n, m in sql(A, "SELECT name, is_minor FROM people WHERE role='child'"))
    assert rows == {"Tobi": 1, "Ada": 0}

def test_child_with_no_age_stays_a_minor(env):
    A, c = env
    _event(c)
    c.post("/event/1/family", data={"household_name": "The Lees", "parent_name": "Min Lee", "children": "Jun"},
           environ_base=E)
    assert sql(A, "SELECT is_minor FROM people WHERE name='Jun'") == [(1,)]

def _staff_day(A, c, billing="free"):
    _event(c, people=True)
    if billing != "free":
        c.post("/event/1/billing", data={"billing": billing}, environ_base=E)
    c.post("/event/1/family", data={"household_name": "Avery Stone", "email": "avery@example.com"}, environ_base=E)
    return _release_of(A, "Avery Stone")[0]

def rights(A, *a, **k):
    with A.app.test_request_context():
        return A.event_rights_text(*a, **k)

def _release_text(c, did):
    return text(c.get(f"/doc/{did}/release", environ_base=E).get_data(as_text=True))

def test_people_event_release_is_person_shaped(env):
    A, c = env
    did = _staff_day(A, c)
    page = _release_text(c, did)
    assert "My family" not in page and "other families" not in page and "without names" not in page
    assert "I receive a permanent personal license to my own photos" in page
    assert "The edited photos are delivered to me for personal use" in page

def test_family_event_release_keeps_family_wording(env):
    A, c = env
    _event(c)
    c.post("/event/1/family", data={"household_name": "The Duffys", "parent_name": "Kate Duffy", "children": "Owen 7"},
           environ_base=E)
    page = _release_text(c, _release_of(A, "Owen")[0])
    assert "My family receives a permanent personal license" in page
    assert "Group photos that include Owen also go to the other families in the group" in page
    assert "without names" in page

def test_people_event_organizer_owns_wording(env):
    A, c = env
    did = _staff_day(A, c)
    c.post("/event/1/billing", data={"rights": "organizer"}, environ_base=E)
    page = _release_text(c, did)
    assert "The organizer owns the copyright" in page and "I receive a permanent personal license" in page
    assert "My family" not in page
    assert "each person photographed" in rights(A, 1, voice="organizer").lower()
    assert "famil" not in rights(A, 1, voice="organizer")

def test_people_event_minor_release_names_the_person(env):
    A, c = env
    did = _staff_day(A, c)
    sql(A, "UPDATE people SET is_minor=1 WHERE name='Avery Stone'")
    page = _release_text(c, did)
    assert "Avery Stone receives a permanent personal license to their own photos" in page

def test_people_event_rights_seg_and_agreement_text(env):
    A, c = env
    _staff_day(A, c)
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert "I own; each person licensed" in page and "families and organizer licensed" not in page
    assert "without names" not in rights(A, 1)
    assert "without names" not in rights(A, 1, voice="organizer")

def test_people_event_price_terms_are_the_persons(env):
    A, c = env
    _event(c, people=True)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "40"}, environ_base=E)
    c.post("/event/1/family", data={"household_name": "Blake Moss"}, environ_base=E)
    did = _release_of(A, "Blake Moss")[0]
    with A.app.test_request_context():
        money_ = A._family_money(did)
    assert money_.startswith("My price is") and "family" not in money_ and "we " not in money_

def test_custom_rights_in_photographer_voice_are_quoted_in_the_release(env):
    A, c = env
    did = _staff_day(A, c)
    c.post("/event/1/billing", data={"rights": "custom"}, environ_base=E)
    c.post("/event/1/billing", data={"rights_note": "I keep the copyright; the company may use the photos with names."},
           environ_base=E)
    page = _release_text(c, did)
    assert "terms for the event photos: \"I keep the copyright; the company may use the photos with names.\"" in page
    assert rights(A, 1) == "I keep the copyright; the company may use the photos with names."

def test_custom_rights_in_third_person_stay_as_written(env):
    A, c = env
    did = _staff_day(A, c)
    c.post("/event/1/billing", data={"rights": "custom"}, environ_base=E)
    c.post("/event/1/billing", data={"rights_note": "The company owns the photos and may use them with names."},
           environ_base=E)
    page = _release_text(c, did)
    assert "The company owns the photos and may use them with names." in page
    assert "terms for the event photos:" not in page

def test_people_event_letter_renders_with_person_rights(env):
    A, c = env
    _staff_day(A, c)
    sid = sql(A, "SELECT id FROM sessions WHERE event_id=1")[0][0]
    r = c.get(f"/letters/{sid}/print-release.pdf", environ_base=E)
    assert r.status_code == 200
    c.post("/event/1/billing", data={"rights": "custom"}, environ_base=E)
    assert c.get(f"/letters/{sid}/print-release.pdf", environ_base=E).status_code == 200

def _two_staff(A, c, t1, t2):
    sql(A, "INSERT INTO households (name, company) VALUES ('Office manager', 'Northwind Supply')")
    _event(c, people=True)
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    c.post("/event/1/billing", data={"payer_hid": "1"}, environ_base=E)
    for name in ("Avery Stone", "Blake Moss"):
        c.post("/event/1/family", data={"household_name": name}, environ_base=E)
    a, b = [r_[0] for r_ in sql(A, "SELECT id FROM sessions WHERE event_id=1 ORDER BY id")]
    sql(A, "UPDATE sessions SET start_time=? WHERE id=?", (t1, a))
    sql(A, "UPDATE sessions SET start_time=? WHERE id=?", (t2, b))
    return a, b

def clashes(A, sid):
    with A.app.test_request_context():
        return A.clashes(sid)

def test_same_event_same_time_clashes(env):
    A, c = env
    a, b = _two_staff(A, c, "10:00", "10:00")
    assert [x_["name"] for x_ in clashes(A, a)] == ["Blake Moss"]
    assert "same event" in clashes(A, a)[0]["when"]
    assert [x_["name"] for x_ in clashes(A, b)] == ["Avery Stone"]
    r = c.get(f"/availability?d=2027-05-04&t=10:00&sid={a}", environ_base=E).get_json()
    assert any("Blake Moss" in x_ for x_ in r["clashes"])

def test_same_event_no_times_or_apart_stays_quiet(env):
    A, c = env
    a, b = _two_staff(A, c, None, None)
    assert clashes(A, a) == [] and clashes(A, b) == []
    sql(A, "UPDATE sessions SET start_time='09:00' WHERE id=?", (a,))
    assert clashes(A, a) == []
    sql(A, "UPDATE sessions SET start_time='14:00' WHERE id=?", (b,))
    assert clashes(A, a) == []

def test_organizer_billing_session_is_not_a_slot(env):
    A, c = env
    a, _ = _two_staff(A, c, "10:00", "11:30")
    org = sql(A, "SELECT id FROM sessions WHERE payer_event=1")[0][0]
    sql(A, "UPDATE sessions SET shoot_date='2027-05-04', start_time='10:00' WHERE id=?", (org,))
    assert clashes(A, org) == []
    assert clashes(A, a) == []

def test_other_bookings_still_clash_as_before(env):
    A, c = env
    a, _ = _two_staff(A, c, "10:00", "13:00")
    sql(A, "INSERT INTO households (name) VALUES ('Portrait client')")
    hid = sql(A, "SELECT id FROM households WHERE name='Portrait client'")[0][0]
    with A.app.test_request_context():
        s = A.create_session(hid, "portrait", "booked", "Portrait", "2027-05-04")
    sql(A, "UPDATE sessions SET start_time='10:30' WHERE id=?", (s,))
    assert [x_["name"] for x_ in clashes(A, a)] == ["Portrait client"]

def test_same_event_slot_lengths_decide_overlap(env):
    A, c = env
    a, b = _two_staff(A, c, "10:00", "10:15")
    assert clashes(A, a) == []
    sql(A, "UPDATE sessions SET duration_min=20 WHERE event_id=1")
    assert [x_["name"] for x_ in clashes(A, a)] == ["Blake Moss"]
    sql(A, "UPDATE sessions SET start_time='10:20' WHERE id=?", (b,))
    assert clashes(A, a) == []
