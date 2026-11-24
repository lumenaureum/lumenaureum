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
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
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

def _event(c, n=3, date="2027-05-04"):
    c.post("/events", data={"name": "Spring day", "date": date, "venue": "Main hall"}, environ_base=E)
    for i in range(n):
        c.post("/event/1/family", data={"household_name": f"Fam{i}", "parent_name": f"Parent {i}",
                                        "children": f"Kid {i}"}, environ_base=E)

def _org(c, price="20", per="person"):
    c.post("/event/1/billing", data={"billing": "organizer", "org_name": "Coach Lee", "org_company": "Eagles FC"}, environ_base=E)
    c.post("/event/1/organizer-price", data={"amount": price, "per": per}, environ_base=E)

def _bulk_pay(c, sids, day):
    pg = c.get("/event/1", environ_base=E).get_data(as_text=True)
    once = re.search(r'name="once" value="([^"]+)"', pg[pg.index('id="fpay"'):]).group(1)
    c.post("/event/1/payments", data={"pick": [str(s_) for s_ in sids], "method": "cash", "received": day, "once": once},
           environ_base=E)

def test_moving_the_event_date_moves_the_organizers_billing_session(env):

    A, c = env
    _event(c, date="2027-05-04")
    _org(c)
    r = c.post("/event/1/edit", data={"name": "Spring day", "date": "2027-06-12", "venue": "Main hall", "notes": ""},
               environ_base=E)
    assert r.status_code == 302
    assert sql(A, "SELECT shoot_date FROM sessions WHERE event_id=1 GROUP BY shoot_date") == [("2027-06-12",)]
    quote = text(c.get("/session/4/quote/preview", environ_base=E).get_data(as_text=True))
    agreement = text(c.get("/session/4/agreement", environ_base=E, follow_redirects=True).get_data(as_text=True))
    assert "May 4, 2027" not in quote, "organizer's quote still names the old event day"
    assert "May 4, 2027" not in agreement, "organizer's agreement still names the old event day"
    assert sql(A, "SELECT shoot_date FROM sessions WHERE payer_event=1") == [("2027-06-12",)]

def test_moving_the_event_date_from_today_moves_the_organizers_billing_session(env):
    A, c = env
    _event(c, date="2027-05-04")
    _org(c)
    c.post("/event/1/date", data={"date": "2027-06-12"}, environ_base=E)
    assert sql(A, "SELECT shoot_date FROM sessions WHERE payer_event=1") == [("2027-06-12",)]

@pytest.mark.parametrize("now_pays", ["free", "families"])
def test_organizer_no_longer_paying_is_still_billed(env, now_pays):

    A, c = env
    _event(c, date="2026-11-10")
    _org(c)
    c.post("/event/1/billing", data={"billing": now_pays}, environ_base=E)
    if now_pays == "families":
        c.post("/event/1/family-price", data={"amount": "35"}, environ_base=E)
    c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E)
    ev = text(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert "Eagles FC - " not in ev
    money_ = text(c.get("/money", environ_base=E).get_data(as_text=True))
    today_ = text(c.get("/", environ_base=E).get_data(as_text=True))
    assert "Eagles FC Group events - shot" not in money_, "Finances: organizer still 'To invoice' $60"
    assert "Eagles FC Group events, Shot" not in today_, "Today: still asks to quote/invoice the organizer"

def test_switching_to_organizer_after_families_were_priced_and_paid(env):

    A, c = env
    _event(c)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "35"}, environ_base=E)
    _bulk_pay(c, [1], "2026-11-20")
    assert sql(A, "SELECT session_id, amount FROM payments") == [(1, 35.0)]
    r = c.post("/event/1/billing", data={"billing": "organizer", "org_name": "Coach Lee", "org_company": "Eagles FC"},
               environ_base=E, follow_redirects=True)
    assert "Some families have already paid" in text(r.get_data(as_text=True))
    assert sql(A, "SELECT billing FROM events WHERE id=1") == [("families",)]
    assert not sql(A, "SELECT id FROM sessions WHERE payer_event=1")
    assert not sql(A, "SELECT id FROM households WHERE company='Eagles FC'")
    assert len(sql(A, "SELECT id FROM lines WHERE label='Spring day - family session'")) == 3

def test_switching_to_organizer_after_families_were_priced_takes_the_price_off_the_families(env):

    A, c = env
    _event(c)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "35"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "organizer", "org_name": "Coach Lee", "org_company": "Eagles FC"},
           environ_base=E)
    c.post("/event/1/organizer-price", data={"amount": "20", "per": "person"}, environ_base=E)
    money_ = text(c.get("/money", environ_base=E).get_data(as_text=True))
    billed_org = sql(A, "SELECT SUM(amount) FROM lines WHERE session_id=(SELECT id FROM sessions WHERE payer_event=1)")[0][0]
    assert billed_org == 60.0
    assert not sql(A, "SELECT id FROM lines WHERE label='Spring day - family session'")
    assert "Fam1 Group events" not in money_ and "Fam2 Group events" not in money_, \
        "Finances still chases families for $35 after the organizer took over paying"

def test_switching_to_organizer_after_a_rename_still_sees_an_invoiced_family(env):

    A, c = env
    _event(c)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "35"}, environ_base=E)
    c.post("/session/1/invoice/issue", environ_base=E)
    assert sql(A, "SELECT 1 FROM doc_numbers WHERE session_id=1 AND kind LIKE 'INV%'")
    c.post("/event/1/edit", data={"name": "Spring Day", "date": "2027-05-04", "venue": "Main hall"}, environ_base=E)
    assert sql(A, "SELECT name FROM events WHERE id=1") == [("Spring Day",)]
    r = c.post("/event/1/billing", data={"billing": "organizer", "org_name": "Coach Lee", "org_company": "Eagles FC"},
               environ_base=E, follow_redirects=True)
    assert "Some families have already paid" in text(r.get_data(as_text=True))
    assert sql(A, "SELECT billing FROM events WHERE id=1") == [("families",)]
    assert len(sql(A, "SELECT id FROM lines WHERE label LIKE '% - family session'")) == 3

def test_switching_to_organizer_takes_off_a_price_line_with_an_older_name(env):

    A, c = env
    _event(c)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "35"}, environ_base=E)
    sql(A, "UPDATE lines SET label='Old name - family session' WHERE session_id=1 AND label LIKE '% - family session'")
    c.post("/event/1/billing", data={"billing": "organizer", "org_name": "Coach Lee", "org_company": "Eagles FC"},
           environ_base=E)
    assert sql(A, "SELECT billing FROM events WHERE id=1") == [("organizer",)]
    assert not sql(A, "SELECT id FROM lines WHERE label LIKE '% - family session'")

def test_organizer_who_paid_keeps_paying(env):

    A, c = env
    _event(c, date="2026-11-10")
    _org(c)
    sql(A, "INSERT INTO payments (session_id, kind, amount, received, method) VALUES (4, 'deposit', 20, '2026-11-01', 'cash')")
    r = c.post("/event/1/billing", data={"billing": "free"}, environ_base=E, follow_redirects=True)
    assert "The organizer has already paid" in text(r.get_data(as_text=True))
    assert sql(A, "SELECT billing FROM events WHERE id=1") == [("organizer",)]
    assert sql(A, "SELECT stage FROM sessions WHERE id=4") == [("inquiry",)]

def test_organizer_pays_again_reopens_their_bill(env):
    A, c = env
    _event(c)
    _org(c)
    c.post("/event/1/billing", data={"billing": "free"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE payer_event=1") == [("lost",)]
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE payer_event=1") == [("inquiry",)]
    assert sql(A, "SELECT amount FROM lines WHERE fee_key='event_org'") == [(60.0,)]

@pytest.mark.parametrize("how", ["lost", "delete"])
def test_family_dropping_out_on_its_own_page_leaves_organizer_overbilled(env, how):

    A, c = env
    _event(c)
    _org(c)
    assert sql(A, "SELECT label, amount FROM lines WHERE fee_key='event_org'") == [("Spring day, per family x 3", 60.0)]
    if how == "lost":
        c.post("/session/2/stage", data={"stage": "lost", "lost_reason": "Other"}, environ_base=E)
    else:
        c.post("/session/2/delete", environ_base=E)
        import time
        for _ in range(50):
            if not sql(A, "SELECT 1 FROM sessions WHERE id=2"):
                break
            time.sleep(0.1)
        assert not sql(A, "SELECT 1 FROM sessions WHERE id=2")
    assert sql(A, "SELECT stage FROM sessions WHERE payer_event=1") == [("inquiry",)]
    assert sql(A, "SELECT label, amount FROM lines WHERE fee_key='event_org'") == [("Spring day, per family x 2", 40.0)]

def test_changing_the_organizer_after_the_quote_was_prepared_hands_the_old_quote_to_the_new_one(env):

    A, c = env
    _event(c)
    _org(c)
    c.post("/session/4/quote/send", environ_base=E)
    vid = sql(A, "SELECT id FROM quote_versions WHERE session_id=4")[0][0]
    c.post("/event/1/billing", data={"org_name": "Ms Park", "org_company": "Hawks Club"}, environ_base=E)
    owner = sql(A, "SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=4")[0][0]
    qd = text(c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True))
    addressed = re.search(r"Quote for (.+?) Quote \d", qd).group(1)
    assert addressed == owner, f"the billing session now belongs to {owner} but its quote is addressed to {addressed}"

def day(c, name="Picture Day", date="2027-01-20", price="25"):
    c.post("/events", data={"name": name, "date": date, "venue": "Gym"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": price}, environ_base=E)

def add(c, fam, kids="Kid 8", parent="Pat"):
    return c.post("/event/1/family", data={"household_name": fam, "parent_name": parent, "children": kids}, environ_base=E)

def test_renaming_the_event_fixes_the_name_on_each_familys_price_line(env):

    A, c = env
    day(c, name="Lincon Elementary Picture Day")
    add(c, "Fam A")
    add(c, "Fam B")
    c.post("/event/1/edit", data={"name": "Lincoln Elementary Picture Day", "date": "2027-01-20", "venue": "Gym", "notes": ""}, environ_base=E)
    labels = [r[0] for r in sql(A, "SELECT label FROM lines")]
    titles = [r[0] for r in sql(A, "SELECT title FROM sessions")]
    assert all("Lincoln" in s_ for s_ in labels + titles), (labels, titles)

def test_adding_the_same_family_twice_to_an_event_is_not_billed_twice(env):

    A, c = env
    day(c)
    add(c, "Lee family", "Mia 7", "Pat Lee")
    add(c, "Lee family", "Mia 7", "Pat Lee")
    assert len(sql(A, "SELECT id FROM sessions WHERE event_id=1")) == 1

def test_a_blank_family_name_is_refused(env):

    A, c = env
    day(c)
    add(c, "   ")
    assert not sql(A, "SELECT id FROM households WHERE trim(name)=''")

def test_a_child_typed_with_a_comma_before_the_age_is_not_a_second_child(env):

    A, c = env
    day(c)
    add(c, "Lee family", "Mia, 7")
    names = [r[0] for r in sql(A, "SELECT name FROM people WHERE role='child'")]
    assert "7" not in names, names

def test_marking_every_release_signed_completes_the_count_when_a_family_dropped_out(env):

    A, c = env
    day(c)
    for n in "ABC":
        add(c, f"Fam{n}")
    c.post("/session/2/stage", data={"stage": "lost"}, environ_base=E)
    c.post("/event/1/releases", data={"signed_on": "2020-01-01"}, environ_base=E)
    t = text(c.get("/event/1", environ_base=E).get_data(as_text=True))
    m = re.search(r"Releases (\d+) of (\d+) families signed", t)
    assert m is None or m.group(1) == m.group(2), t[:400]

def test_adding_a_returning_family_to_a_mini_day_does_not_make_a_second_client(env):

    A, c = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'The Lopezes')")
    sql(A, "INSERT INTO people (household_id, name, role, email, phone) VALUES (1, 'Sarah Lopez', 'parent', 'sarah@example.com', '555-0199')")
    sql(A, "INSERT INTO events (id, name, date, venue) VALUES (1, 'Holiday mini day', '2026-12-05', 'Riverside Park')")
    r = c.post("/event/1/family", data=dict(household_name="The Lopezes", parent_name="Sarah Lopez", phone="555-0199",
                                            children="Ava 6, Ben 4"), environ_base=E)
    assert r.status_code == 302
    n = sql(A, "SELECT COUNT(*) FROM households WHERE name='The Lopezes'")[0][0]
    assert n == 1, f"{n} clients named The Lopezes after adding the family to the event"
    assert sql(A, "SELECT household_id FROM sessions WHERE event_id=1") == [(1,)]
    assert sql(A, "SELECT COUNT(*) FROM people WHERE name='Sarah Lopez'") == [(1,)]
    assert sorted(r_[0] for r_ in sql(A, "SELECT name FROM people WHERE role='child'")) == ["Ava", "Ben"]

def test_deleting_a_family_says_a_booked_organizer_quote_stays_as_agreed(env):

    A, c = env
    _event(c)
    _org(c)
    c.post("/session/4/quote/send", environ_base=E)
    sql(A, "UPDATE sessions SET stage='booked' WHERE id=4")
    hid, created = sql(A, "SELECT household_id, created FROM sessions WHERE id=2")[0]
    with A.app.test_request_context("/"):
        said = A._delete_session(2, hid, created, "Deleted the session", 1)
    assert not sql(A, "SELECT 1 FROM sessions WHERE id=2")
    assert said == A.ORG_LINE_KEPT
    assert sql(A, "SELECT amount FROM lines WHERE fee_key='event_org'") == [(60.0,)]

def test_organizer_pays_again_reopens_a_booked_bill_as_booked(env):

    A, c = env
    _event(c)
    _org(c)
    c.post("/session/4/stage", data={"stage": "booked"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=4") == [("booked",)]
    c.post("/event/1/billing", data={"billing": "free"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=4") == [("lost",)]
    c.post("/event/1/billing", data={"billing": "organizer"}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=4") == [("booked",)]
