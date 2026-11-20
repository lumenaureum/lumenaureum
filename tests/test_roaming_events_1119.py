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
        r_ = con.execute(q_, args)
        out = r_.fetchall()
        con.commit()
        return out
    finally:
        con.close()

def _event(A, c, day):

    c.post("/events", data={"name": "Spring Recital", "date": day, "venue": "Town Hall"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    for name in ("Bell", "Chen"):
        c.post("/event/1/family", data={"household_name": name, "parent_name": name + " Parent"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "60"}, environ_base=E)
    bell = sql(A, "SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name='Bell'")[0][0]
    sql(A, "INSERT INTO payments (session_id, kind, amount, received) VALUES (?, 'balance', 60, ?)", (bell, day))

def _page(c, url):
    return html.unescape(c.get(url, environ_base=E).get_data(as_text=True))

def _agreement_event(A, day):

    sql(A, "INSERT INTO events (id, name, date, billing) VALUES (1, 'Spring Recital', ?, 'free')", (day,))
    for hid, name in ((1, "Bell"), (2, "Chen")):
        sql(A, "INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
        sql(A, """INSERT INTO sessions (id, household_id, type, event_id, stage, shoot_date)
                  VALUES (?, ?, 'family', 1, 'booked', ?)""", (hid, hid, day))
        sql(A, "INSERT INTO documents (session_id, kind) VALUES (?, 'agreement')", (hid,))

def test_today_never_says_everything_is_done_while_a_family_step_is_open(env):
    A, c = env
    _agreement_event(A, (date.today() - timedelta(days=30)).isoformat())
    with A.app.test_request_context():
        assert [(o_["key"], o_["n"], o_["words"]) for o_ in A.event_family_open(1)] == \
            [("agreement_signed", 2, "2 agreements still unsigned")]
    page = _page(c, "/")
    assert "Everything in Booked is done" not in page and "2 agreements still unsigned" in page

    assert 'data-confirm="2 agreements still unsigned. Move all to Shot anyway?"' in page
    sql(A, "UPDATE documents SET status='signed', signed=? WHERE session_id=1", (date.today().isoformat(),))
    page = _page(c, "/")
    assert "1 agreement still unsigned" in page and "Everything in Booked is done" not in page

    sql(A, "UPDATE documents SET status='signed', signed=? WHERE session_id=2", (date.today().isoformat(),))
    page = _page(c, "/")
    assert "still unsigned" not in page and "Everything in Booked is done" in page

def test_event_page_names_the_open_steps_by_its_move(env):
    A, c = env
    _agreement_event(A, (date.today() - timedelta(days=30)).isoformat())
    page = _page(c, "/event/1")
    assert "The families are still at Booked. 2 agreements still unsigned." in page
    assert 'data-confirm="2 agreements still unsigned. Move all 2 families to Shot?"' in page

def test_event_page_reads_plainly(env):
    A, c = env
    _event(A, c, (date.today() - timedelta(days=30)).isoformat())
    page = _page(c, "/event/1")

    rel = re.sub(r'<div class="k">Delivered</div><div class="v">[^<]*</div>', "", page)
    assert "0/0" not in rel and "0/2" not in rel and "None to sign" in page
    assert "Mark every release signed" not in page

    assert "<th>Payment</th>" in page and "$60.00 paid in full" in page and "$60.00 owed" in page
    assert ">paid<" not in page and "of $60" not in page

    if not sql(A, "SELECT 1 FROM tasks WHERE event_id=1"):
        assert "Event steps" not in page

    assert "The families are still at Booked." in page

def test_signing_date_comes_before_its_button(env):
    A, c = env
    _event(A, c, (date.today() - timedelta(days=30)).isoformat())
    for (sid,) in sql(A, "SELECT id FROM sessions WHERE event_id=1"):
        sql(A, "INSERT INTO documents (session_id, kind) VALUES (?, 'release')", (sid,))
    page = _page(c, "/event/1")
    form = re.search(r'<form[^>]*event/1/releases[^>]*class="row".*?</form>', page, re.S).group(0)
    assert form.index('<label for="f-signed_on" class="ui-label">Releases signed on</label>') < form.index('id="f-signed_on"') \
        < form.index("Mark every release signed")
    assert "0 of 2 families signed" in page and "0 of 1 signed" in page
    c.post("/event/1/releases", data={}, environ_base=E)
    page = _page(c, "/event/1")
    assert "2 of 2 families signed" in page and ">Signed<" in page and "Mark every release signed" not in page

def test_an_event_move_says_what_it_did_or_why_not(env):
    A, c = env
    _event(A, c, (date.today() + timedelta(days=20)).isoformat())

    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, follow_redirects=True)
    assert "Not yet: the event is" in html.unescape(r.get_data(as_text=True))
    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, headers={"X-Fetch": "1"})
    assert r.status_code == 409 and "Not yet: the event is" in r.get_data(as_text=True)
    assert "Not yet" not in _page(c, "/event/1")
    sql(A, "UPDATE events SET date=?", ((date.today() - timedelta(days=1)).isoformat(),))
    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, follow_redirects=True)
    assert "Moved 2 families to Shot." in html.unescape(r.get_data(as_text=True))
    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, follow_redirects=True)
    assert "Nothing moved: every family is already at Shot or further along." in html.unescape(r.get_data(as_text=True))
    r = c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E, headers={"X-Fetch": "1"})
    assert r.status_code == 409 and "Nothing moved" in r.get_data(as_text=True)
