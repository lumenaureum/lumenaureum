import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, make_studio, new_session, one, run, sql
from journey_walker import parse
from test_305_journeys import booked_and_ready, quoted, retouch

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def page(c, sid):
    r = c.get(f"/c/1?s={sid}", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def booked(A, c, when=14, **cols):
    sid = new_session(A, "family", "inquiry", shoot_date=day(when), **cols)
    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    return sid

def stage(A, sid):
    return one(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0]

def by_id(root, i):
    return next((n for n in root.iter() if n.get("id") == i), None)

def skipped_lines(p):
    return [li for li in parse(p).iter() if li.tag == "li" and "ui-past-skipped" in li.cls()]

def test_307d_a_skip_says_skipped_and_its_reason_is_a_plain_link(studio):
    A, c = studio
    sid = booked(A, c)
    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "na", "from": "row"}, environ_base=E)
    li = next(li for li in skipped_lines(page(c, sid)) if "Agreement" in li.words())
    assert "Skipped" in li.words() and "Client didn't want it" not in li.words()
    buttons = [b for b in li.iter() if b.tag == "button"]
    assert {b.words() for b in buttons} == {"Undo", "The client said no"}
    assert all("ui-btn-inline" in b.cls() for b in buttons), "styled like a link, not a button"

    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "declined"}, environ_base=E)
    li = next(li for li in skipped_lines(page(c, sid)) if "Agreement" in li.words())
    assert "Client didn't want it" in li.words() and "The client said no" not in li.words()

def test_307d_a_step_switched_off_reads_not_needed_for_this_session(studio):
    A, c = studio
    sid = booked(A, c)
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))[0]
    c.post(f"/session/{sid}/steps", data={"shown": [f"t{tid}", "aagreement_signed"], "keep": []}, environ_base=E)
    lines = skipped_lines(page(c, sid))
    for words in ("session sheet", "Agreement"):
        li = next(li for li in lines if words in li.words())
        assert "Not needed for this session" in li.words() and "Client didn't want it" not in li.words(), li.words()
        assert "Undo" in li.words()

def test_307e_mark_shot_is_on_the_tasks_before_the_shoot_date(studio):
    A, c = studio
    sid = booked(A, c, when=14)
    sql(A, "UPDATE sessions SET start_time='15:30', stops='Ridley Creek park' WHERE id=?", (sid,))
    root = parse(page(c, sid))
    row = by_id(root, "mark-shot")
    assert row is not None and any(a.get("id") == "needs" for a in row.ancestors()), "on the session's own tasks"
    btn = next(b for b in row.iter() if b.tag == "button" and b.words() == "Mark shot")
    form = next(a for a in btn.ancestors() if a.tag == "form")
    when_ = run(A, lambda: A.nice(day(14)))
    assert form.attrs.get("data-confirm") == f"The shoot date is {when_}. Mark shot anyway?"
    assert not any(a.tag == "details" for a in btn.ancestors()), "visible, not under a menu"
    c.post(f"/session/{sid}/quick", data={"act": "mark_shot"}, environ_base=E)
    assert stage(A, sid) == "shot"
    p = page(c, sid)
    head = p.split('id="needs"')[0][-600:]
    today_ = run(A, lambda: A.nice(day(0)))
    assert f"Shot {today_} (early)" in p
    hdr = p.split('id="needs"')[0]
    assert f"Shot {when_}" not in p and f"Shoot {when_}" not in hdr

    assert "Ridley Creek park" in hdr, head

    sql(A, "UPDATE sessions SET stage='closed' WHERE id=?", (sid,))
    p = page(c, sid)
    assert f"Shot {today_} (early)" in p and f"Shot {when_}" not in p

def minor_client(A, c, delivery="usb"):

    r = c.post("/contact/new", data={"contact_name": "Emery Halvorsen", "email": "emery@example.com", "contact_age": "17",
                                     "then": "inquiry"}, environ_base=E)
    assert r.status_code == 302
    hid = one(A, "SELECT id FROM households WHERE name='Emery Halvorsen'")[0]
    pid = one(A, "SELECT id, is_minor FROM people WHERE household_id=?", (hid,))
    assert pid[1] == 1, "the age typed on the new contact makes them a minor"
    form = c.get(f"/inquiry?hid={hid}&type=family", environ_base=E).get_data(as_text=True)
    assert "Emery Halvorsen <span class=\"sub\">(17, minor)</span>" in form, "the age on file shows, never typed twice"
    r = c.post("/inquiry", data={"household_id": hid, "type": "family", "subjects": str(pid[0]), "shoot_date": day(20),
                                 "delivery_method": delivery, "once": "m1"}, environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return one(A, "SELECT id FROM sessions WHERE household_id=?", (hid,))[0]

def agreement_text(c, sid):
    import re
    r = c.get(f"/session/{sid}/agreement", environ_base=E)
    assert r.status_code == 200
    body = r.get_data(as_text=True).split("<h1>Session agreement", 1)[1]
    return " ".join(re.sub(r"<[^>]+>", " ", body).replace("&#34;", '"').replace("&quot;", '"').split())

def test_307f_a_minor_client_has_a_parent_or_guardian_sign_for_them(studio):
    A, c = studio
    sid = minor_client(A, c)
    a_ = agreement_text(c, sid)
    assert 'This agreement is between the parent or legal guardian of Emery Halvorsen, signing on their behalf ("you")' in a_
    assert ("Emery Halvorsen is under 18. Their parent or legal guardian signs this agreement on their behalf, agrees to it"
            " for them, and is present for the whole session.") in a_
    assert "Parent or legal guardian of Emery Halvorsen, signing for them Printed name:" in a_
    assert "Signed for by a parent or legal guardian" in a_

    did = one(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0]
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement", "did": did, "guardian": "Jo Halvorsen"},
           environ_base=E)
    assert one(A, "SELECT status FROM documents WHERE id=?", (did,))[0] == "signed"
    a_ = agreement_text(c, sid)
    assert "Jo Halvorsen, parent or legal guardian of Emery Halvorsen, signing for them" in a_
    assert "between Jo Halvorsen, parent or legal guardian of Emery Halvorsen, signing on their behalf" in a_
    assert "Signed for by Jo Halvorsen" in a_
    assert one(A, "SELECT 1 FROM doc_snapshots WHERE kind='agreement' AND ref=?", (sid,)) is not None

def test_307f_mark_signed_from_the_client_page_still_names_a_guardian(studio):
    A, c = studio
    sid = minor_client(A, c)
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert "Parent or legal guardian of Emery Halvorsen, signing for them" in a_ and "Signed copy received" in a_

def test_307f_an_adult_client_keeps_the_plain_agreement(studio):
    A, c = studio
    sid = booked(A, c)
    a_ = agreement_text(c, sid)
    assert "Dana Hollis" in a_ and "guardian" not in a_

def test_307f_a_drive_only_agreement_promises_no_gallery_link(studio):
    A, c = studio
    retouch(c, False)
    usb = booked(A, c, delivery_method="usb")
    assert "Your gallery link works for anyone who has it" not in agreement_text(c, usb)
    link = booked(A, c, delivery_method="link")
    assert "Your gallery link works for anyone who has it" in agreement_text(c, link)

def inquire(A, c, once, **ways):
    r = c.post("/inquiry", data=dict({"household_id": "1", "type": "family", "once": once, "deliver_shown": "1"}, **ways),
               environ_base=E)
    assert r.status_code == 302
    return one(A, "SELECT MAX(id) FROM sessions")[0]

def test_307g_a_new_inquiry_goes_more_than_one_way_an_online_gallery_among_them(studio):
    A, c = studio
    form = parse(c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True))
    labels = [n.words() for n in form.iter() if n.tag == "label" and "ui-check" in n.cls()]
    assert "Online gallery (download link)" in labels and "USB drive" in labels
    boxes = {n.get("name"): n.get("type") for n in form.iter() if n.tag == "input" and n.get("name") in ("deliver_gallery", "delivery_method")}
    assert boxes == {"deliver_gallery": "checkbox", "delivery_method": "radio"}

    both = inquire(A, c, "g1", delivery_method="usb", deliver_gallery="1")
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (both,))[0] == "usb"
    assert run(A, lambda: A.session_gallery_planned(both))
    assert "USB drive, and a download gallery" in page(c, both)

    online = inquire(A, c, "g2", deliver_gallery="1", delivery_method="")
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (online,))[0] == "link"

    usb = inquire(A, c, "g3", delivery_method="usb")
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (usb,))[0] == "usb"
    assert not one(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE 'Share a download gallery too%'", (usb,))

def usb_paid_at_shot(A, c, retouch_picks=""):

    from test_305_journeys import owing
    retouch(c, bool(retouch_picks))
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    owing(A, c, sid)
    if retouch_picks:
        sql(A, "UPDATE sessions SET retouch_picks=? WHERE id=?", (retouch_picks, sid))
    due = run(A, lambda: A.totals(sid)["due"])
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "%.2f" % due, "method": "check",
                                         "received": day(0), "once": f"b{sid}"}, environ_base=E)
    assert run(A, lambda: A.totals(sid)["due"]) <= 0.005
    return sid

def test_307a_paid_in_full_is_not_delivered_while_the_drive_is_not_handed_over(studio):
    A, c = studio
    sid = usb_paid_at_shot(A, c)
    run(A, lambda: A.advance(sid))
    assert stage(A, sid) == "shot", "paid, but the USB drive is not handed over yet"
    p = page(c, sid)
    assert "Record the handover" in p.split('id="needs"', 1)[1].split("</section>", 1)[0], "the handover is on Shot's tasks"
    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"

def test_307a_picks_in_never_mean_delivered(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-5), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    c.post(f"/session/{sid}/quick", data={"act": "favorites", "retouch_picks": "IMG_1", "favorites": "IMG_2", "veto": ""},
           environ_base=E)
    assert stage(A, sid) == "shot"

    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND stage='shot' AND done IS NULL", (day(0), sid))
    sql(A, "UPDATE sessions SET sd_shipped=NULL WHERE id=?", (sid,))
    run(A, lambda: A.advance(sid))
    assert stage(A, sid) == "shot"
    assert one(A, "SELECT done FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0] is None

def test_307b_the_handover_undoes_from_any_later_stage(studio):
    A, c = studio
    sid = usb_paid_at_shot(A, c)
    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"

    root = parse(page(c, sid))
    form = next(f for f in root.iter() if f.tag == "form" and f.get("action") == f"/session/{sid}/deliver/undo")
    assert "Undo the handover" in form.words() and "goes back to Shot" in form.get("data-confirm")
    c.post(f"/session/{sid}/deliver/undo", data={"back": f"/c/1?s={sid}"}, environ_base=E)

    assert stage(A, sid) == "shot"
    assert one(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] is None
    assert one(A, "SELECT 1 FROM step_closes WHERE session_id=? AND step='sd_shipped'", (sid,)) is None
    assert "Record the handover" in page(c, sid)

    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"
    c.post(f"/session/{sid}/stage", data={"stage": "delivered"}, environ_base=E)
    assert stage(A, sid) == "delivered"
    assert f"/session/{sid}/deliver/undo" in page(c, sid)
    c.post(f"/session/{sid}/deliver/undo", data={}, environ_base=E)
    assert one(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] is None and stage(A, sid) == "shot"
    assert one(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE 'Undid the handover recorded%'", (sid,))

def picks_in_at_shot(A, c):

    from test_305_journeys import upload
    from journey_env import jpeg
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="link")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-20), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    for i in range(2):
        upload(c, sid, f"family-{i}.jpg", jpeg("gray"), "export")
    sql(A, "UPDATE sessions SET retouch_picks='family-0.jpg: brighten', gallery_built=?, first_visit=? WHERE id=?",
        (day(-12), day(-10), sid))
    run(A, lambda: A.advance(sid))
    return sid

def test_307c_retouching_dropped_for_one_session_leaves_no_retouch_copy(studio):
    A, c = studio
    sid = picks_in_at_shot(A, c)
    p = page(c, sid)
    assert "Picks: 1 to retouch" in p
    root = parse(p)
    form = next(f for f in root.iter() if f.tag == "form" and "No retouching for this session" in f.words()
                and any(a.get("id") == "more-tasks" for a in f.ancestors()))
    fields = {i.get("name"): i.get("value") for i in form.iter() if i.tag == "input"}
    assert form.get("data-confirm", "").startswith("No retouching for this session?")
    c.post(form.get("action"), data=fields, environ_base=E)
    assert run(A, lambda: A.retouch_dropped(sid))
    p = page(c, sid)
    for stale in ("Upload the retouched finals on the Retouch row", "to retouch", "Retouched finals", "Use the proofs"):
        assert stale not in p, stale
    assert "Picks (no retouching for this session)" in p
    s_ = one(A, "SELECT * FROM sessions WHERE id=?", (sid,))
    assert not run(A, lambda: A.app.config["gallery_wants_finals"](s_))
    assert "Build download gallery" in p

    c.post("/step/reopen", data={"task": fields["task"]}, environ_base=E)
    assert not run(A, lambda: A.retouch_dropped(sid)) and "Picks: 1 to retouch" in page(c, sid)

def test_307c_offer_retouching_off_reaches_sessions_with_no_picks_and_says_who_keeps_it(studio):
    A, c = studio
    owed = picks_in_at_shot(A, c)
    fresh = booked(A, c)
    assert one(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (fresh,))
    r = c.post("/settings/retouch", data={}, environ_base=E, follow_redirects=True)
    assert "already asked for retouching, so it stays on for them" in r.get_data(as_text=True).replace("&#39;", "'")
    assert not one(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (fresh,))
    assert one(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (owed,))
    assert "retouch" not in page(c, fresh).split('id="needs"', 1)[1].split("</section>", 1)[0].lower()
