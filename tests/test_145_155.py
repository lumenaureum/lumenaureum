import html
import os
import re
import sys
from datetime import date, timedelta

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

def test_the_picker_is_labeled_and_the_asking_contact_comes_first(env):
    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Another client')")
    sql(A, "INSERT INTO households (name) VALUES ('Team parent')")
    c.post("/events", data={"name": "Fall finals", "date": "2027-10-02", "contact": "2"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert '<label for="f-payer" class="ui-label">Who is the organizer?</label>' in page
    assert '<option value="2" selected>Team parent (asked for this event)</option>' in page
    assert "Set the organizer" in page and "Add as the organizer" in page

def test_an_event_a_company_asked_for_starts_as_organizer_pays(env):

    A, c = env
    sql(A, "INSERT INTO households (name, company) VALUES ('Office manager', 'Northwind Supply')")
    sql(A, "INSERT INTO households (name) VALUES ('Team parent')")
    t = text(c.get("/events?contact=1", environ_base=E).get_data(as_text=True))
    assert "Office manager is the organizer of the event you add below, and pays for it" in t
    c.post("/events", data={"name": "Staff headshots", "date": "2027-05-04", "contact": "1"}, environ_base=E)
    c.post("/events", data={"name": "Fall finals", "date": "2027-10-02", "contact": "2"}, environ_base=E)
    assert sql(A, "SELECT id, billing, payer_hid, contact_hid FROM events ORDER BY id") == [
        (1, "organizer", 1, 1), (2, "free", None, 2)]
    assert sql(A, "SELECT household_id, payer_event FROM sessions WHERE payer_event IS NOT NULL") == [(1, 1)]

def test_a_new_organizer_is_added_in_place(env):
    A, c = env
    c.post("/events", data={"name": "Picture day", "date": "2027-10-02"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    r = c.post("/event/1/billing", data={"org_name": "Robin Hale", "org_company": "Maple School PTA",
                                         "org_email": "pta@example.com"}, environ_base=E)
    assert r.location.endswith("/event/1#billing")
    hh = sql(A, "SELECT id, name, company FROM households")
    assert hh == [(1, "Maple School PTA", "Maple School PTA")]
    assert sql(A, "SELECT name, role, email FROM people") == [("Robin Hale", "adult", "pta@example.com")]
    assert sql(A, "SELECT payer_hid FROM events") == [(1,)]
    assert sql(A, "SELECT household_id, stage FROM sessions WHERE payer_event=1") == [(1, "inquiry")]

    r = c.post("/event/1/billing", data={"org_name": "robin hale"}, environ_base=E, follow_redirects=True)
    assert "is already a contact" in text(r.get_data(as_text=True))
    assert sql(A, "SELECT COUNT(*) FROM households") == [(1,)]

def test_who_pays_never_changes_who_owns(env):
    A, c = env
    c.post("/events", data={"name": "Picture day", "date": "2027-10-02"}, environ_base=E)
    for billing in ("organizer", "families", "organizer", "free"):
        c.post("/event/1/billing", data={"billing": billing}, environ_base=E)
        assert sql(A, "SELECT rights FROM events") == [("families",)]
    c.post("/event/1/billing", data={"rights": "organizer"}, environ_base=E)
    assert sql(A, "SELECT rights FROM events") == [("organizer",)]
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    assert sql(A, "SELECT rights FROM events") == [("organizer",)]

def test_the_organizer_moves_with_the_families(env):
    A, c = env
    day = (date.today() - timedelta(days=3)).isoformat()
    c.post("/events", data={"name": "Picture day", "date": day}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    c.post("/event/1/billing", data={"org_name": "Robin Hale"}, environ_base=E)
    c.post("/event/1/organizer-price", data={"amount": "600", "per": "event"}, environ_base=E)
    for name in ("Bell", "Ruiz"):
        c.post("/event/1/family", data={"household_name": name}, environ_base=E)
    ps = sql(A, "SELECT id FROM sessions WHERE payer_event=1")[0][0]
    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, follow_redirects=True)
    assert "Moved 2 families and the organizer to Shot." in text(r.get_data(as_text=True))
    assert sql(A, "SELECT stage FROM sessions WHERE id=?", (ps,)) == [("shot",)]
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert f'href="/session/{ps}/invoice"' in page and "Robin Hale - Shot, $0.00 of $600.00 paid" in text(page)

    sql(A, "UPDATE sessions SET stage='delivered' WHERE id=?", (ps,))
    sql(A, "UPDATE sessions SET stage='booked' WHERE payer_event IS NULL")
    c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=?", (ps,)) == [("delivered",)]
