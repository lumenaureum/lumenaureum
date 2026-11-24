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

def _company_day(A, c):

    sql(A, "INSERT INTO households (name, company) VALUES ('Office manager', 'Northwind Supply')")
    c.post("/events", data={"name": "Staff headshots", "date": "2027-05-04", "venue": "Head office"}, environ_base=E)
    c.post("/event/1/attendees", data={"attendees": "people"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    c.post("/event/1/billing", data={"payer_hid": "1"}, environ_base=E)
    c.post("/event/1/organizer-price", data={"amount": "75", "per": "person"}, environ_base=E)
    for name in ("Avery Stone", "Blake Moss", "Casey Reed"):
        c.post("/event/1/family", data={"household_name": name, "email": f"{name.split()[0].lower()}@example.com"},
               environ_base=E)
    return sql(A, "SELECT id FROM sessions WHERE payer_event=1")[0][0]

def test_one_line_on_the_organizers_quote_counts_the_people(env):
    A, c = env
    ps = _company_day(A, c)
    lines = sql(A, "SELECT label, amount, qty FROM lines WHERE session_id=? AND kind!='tax'", (ps,))
    assert lines == [("Staff headshots, per person x 3", 225.0, 3)]

    assert sql(A, "SELECT COUNT(*) FROM lines l JOIN sessions s ON s.id=l.session_id WHERE s.event_id=1") == [(0,)]
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert f'href="/session/{ps}/invoice"' in page and f'href="/session/{ps}/quote"' in page
    t = text(page)
    assert "Organizer's invoice" in t and "$0.00 of $225.00 paid" in t
    inv = text(c.get(f"/session/{ps}/invoice", environ_base=E).get_data(as_text=True))
    assert "Northwind Supply" in inv and "$225.00" in inv

def test_a_fee_for_the_event_instead(env):
    A, c = env
    ps = _company_day(A, c)
    c.post("/event/1/organizer-price", data={"amount": "1200", "per": "event"}, environ_base=E)
    assert sql(A, "SELECT label, amount FROM lines WHERE session_id=? AND kind!='tax'", (ps,)) == \
        [("Staff headshots: event fee", 1200.0)]
    c.post("/event/1/family", data={"household_name": "Drew Lane"}, environ_base=E)
    assert sql(A, "SELECT amount FROM lines WHERE session_id=? AND kind!='tax'", (ps,)) == [(1200.0,)]

def test_a_booked_organizer_quote_stays_as_agreed(env):
    A, c = env
    ps = _company_day(A, c)
    sql(A, "UPDATE sessions SET stage='booked' WHERE id=?", (ps,))
    sql(A, "INSERT INTO quote_versions (session_id, data) VALUES (?, '{}')", (ps,))
    r = c.post("/event/1/family", data={"household_name": "Drew Lane"}, environ_base=E, follow_redirects=True)
    assert sql(A, "SELECT amount FROM lines WHERE session_id=? AND kind!='tax'", (ps,)) == [(225.0,)]
    assert "already booked, so its price stays as agreed" in text(r.get_data(as_text=True))

def test_an_event_of_adults_reads_people(env):
    A, c = env
    _company_day(A, c)
    t = text(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert "People 3" in t and "Add the person" in t and "Children" not in t
    assert "Move all people to Shot" in t and "Each person pays" in t and "Family" not in t
    who = sql(A, "SELECT p.role, p.email FROM people p JOIN households h ON h.id=p.household_id WHERE h.name='Blake Moss'")
    assert who == [("adult", "blake@example.com")]
    assert "3 people" in text(c.get("/events", environ_base=E).get_data(as_text=True))

def test_a_price_must_be_positive(env):
    A, c = env
    c.post("/events", data={"name": "Staff headshots", "date": "2027-05-04"}, environ_base=E)
    r = c.post("/event/1/organizer-price", data={"amount": "-5"}, environ_base=E, follow_redirects=True)
    assert "positive amount" in text(r.get_data(as_text=True))
    assert sql(A, "SELECT org_price FROM events WHERE id=1") == [(None,)]

