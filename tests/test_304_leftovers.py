import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, make_studio, new_session, one, run, sql
from journey_walker import menu_items, parse
from test_305_journeys import booked_and_ready, quoted, retouch

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def page(c, sid):
    return c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)

def booked(A, c, when=14, **cols):
    sid = new_session(A, "family", "inquiry", shoot_date=day(when), **cols)
    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    return sid

def history(A, sid):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
    finally:
        con.close()

def stage(A, sid):
    return one(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0]

def test_mark_shot_before_the_date_keeps_the_date_and_stays_shot(studio):
    A, c = studio
    sid = booked(A, c, when=5)
    items = {lab: (on, why) for lab, on, why in menu_items(page(c, sid), "session")}
    assert items["Mark shot"][0] and not items["Shoot done"][0]
    c.post(f"/session/{sid}/quick", data={"act": "mark_shot"}, environ_base=E)
    assert stage(A, sid) == "shot" and one(A, "SELECT shoot_date FROM sessions WHERE id=?", (sid,))[0] == day(5)
    page(c, sid)
    run(A, lambda: A.advance(sid))
    assert stage(A, sid) == "shot"
    assert not any(h.startswith("Rescheduled") for h in history(A, sid))
    assert any(h.startswith("Marked shot before the shoot date") for h in history(A, sid))

def test_a_typed_date_change_never_reopens_closed_steps(studio):
    A, c = studio
    sid = booked(A, c)
    sql(A, "UPDATE sessions SET start_time='10:00', stops='Riverside Park' WHERE id=?", (sid,))
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND title IN ('Send session details', 'Confirm time and place')",
        (day(-1), sid))
    c.post(f"/session/{sid}/quick", data={"act": "date", "shoot_date": day(21), "start_time": "10:00", "where": "Riverside Park"}, environ_base=E)
    assert one(A, "SELECT shoot_date FROM sessions WHERE id=?", (sid,))[0] == day(21)
    assert not one(A, """SELECT 1 FROM tasks WHERE session_id=? AND title IN ('Send session details', 'Confirm time and place')
                         AND done IS NULL""", (sid,))
    assert any(h.startswith("Rescheduled from") and "open again" not in h for h in history(A, sid))

def test_a_skipped_agreement_is_not_to_sign(studio):
    A, c = studio
    sid = booked(A, c)
    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "na", "from": "row"}, environ_base=E)
    p = page(c, sid)
    paper = p.split('id="paperwork"', 1)[1].split("</section>", 1)[0]
    assert "to sign" not in paper and "not needed for this session" in paper and "Paperwork: all signed" in p
    assert "agreement still open" not in p

def test_from_the_client_says_not_opened_yet_before_the_first_open(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="link")
    booked_and_ready(A, c, sid)
    p = page(c, sid)
    if 'id="from-client"' in p:
        line_ = p.split('id="from-client"', 1)[1][:400]
        assert "last checked" not in line_ or "not opened yet" in line_

def test_place_stays_in_session_details_at_every_stage(studio):
    A, c = studio
    sid = booked(A, c, when=0)
    sql(A, "UPDATE sessions SET stops='Riverside Park', start_time='10:00' WHERE id=?", (sid,))
    facts = page(c, sid).split('id="session-facts"', 1)[1].split("</section>", 1)[0]
    assert '<span class="ui-fact-k">Place</span>' in facts and "Riverside Park" in facts
    assert "<span>Place" in c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)

def test_retouching_off_names_the_clients_still_owed_it(studio):
    A, c = studio
    retouch(c, False)
    with A.app.test_request_context():
        assert A.task_kind("Retouch 2 photos") == "Retouching owed"
    retouch(c, True)
    with A.app.test_request_context():
        assert A.task_kind("Retouch 2 photos") == "Retouch"

def test_the_new_inquiry_keeps_the_owners_confirmed_way_of_delivering(studio):
    A, c = studio
    usual = A.C.delivery_method("family", None)
    p = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    once = __import__("re").search(r'name="once" value="([^"]+)"', p).group(1)
    r = c.post("/inquiry", data={"household_id": "1", "type": "family", "once": once, "delivery_method": usual}, environ_base=E)
    assert r.status_code in (200, 302)
    sid = one(A, "SELECT MAX(id) FROM sessions")[0]
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (sid,))[0] == usual

def test_a_usb_job_hands_over_in_one_tap_and_offers_no_gallery_ways(studio):
    A, c = studio
    retouch(c, False)
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-3), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND done IS NULL", (day(-1), sid))
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "200", "method": "cash",
                                         "received": day(0), "once": "b1"}, environ_base=E)
    run(A, lambda: A.advance(sid))
    p = page(c, sid)
    row = next(li for li in parse(p).iter() if li.tag == "li" and "ui-todo" in li.cls() and "USB drive" in li.words())
    assert any(b.tag == "button" and b.words() == "Record the handover" for b in row.iter())
    ways = p.split(f'id="f-deliver-{sid}"', 1)[1].split("</div></div>", 1)[0]
    assert "Email the link" not in ways and "QR letter" not in ways
    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"

def test_a_codeword_free_gallery_says_who_can_open_it():

    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates",
                            "_gallery_block.html")).read()
    line = src.split('id="gal-open"', 1)[1].split("</p>", 1)[0]
    assert "Anyone with this link can" in line and "until" in line and "To ask for a passphrase instead, replace the link." in line and "in More" not in line
