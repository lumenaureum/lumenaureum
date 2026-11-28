import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, make_studio, new_session, one, run, sql
from journey_walker import parse
from test_305_journeys import quoted, retouch
from test_307_fixes import booked, page, skipped_lines, stage, usb_paid_at_shot

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def drop_retouching(A, c, sid):
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "note_why": "No retouching for this session"}, environ_base=E)
    assert run(A, lambda: A.retouch_dropped(sid))
    return tid

def preview(c, sid):
    import re
    r = c.get(f"/session/{sid}/quote/preview", environ_base=E)
    assert r.status_code == 200
    return " ".join(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)).split())

def test_308a_no_retouching_for_this_session_reaches_the_quote_and_the_page(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid, sent=False)
    assert "Light retouching" in preview(c, sid)
    drop_retouching(A, c, sid)
    q_ = preview(c, sid)
    assert "Light retouching" not in q_ and "More retouching" not in q_
    p = page(c, sid)
    picker = [o.get("value") for o in parse(p).iter() if o.tag == "option" and o.get("value")]
    assert "retouch" not in picker, "the line picker offers no retouching for this session"
    r = c.post(f"/session/{sid}/quote/fee", data={"fee_key": "retouch", "qty": "1", "once": "r1"}, environ_base=E)
    assert r.status_code in (302, 303)
    assert not one(A, "SELECT 1 FROM lines WHERE session_id=? AND fee_key='retouch'", (sid,))

    line = next(n for n in parse(p).iter() if n.get("id") == "no-retouch")
    assert "No retouching for this session." in line.words() and "Bring retouching back" in line.words()

def test_308b_the_quote_delivery_follows_the_sessions_own_ways(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid, sent=False)
    drop_retouching(A, c, sid)
    q_ = preview(c, sid)
    assert "on a USB drive" in q_ and "online gallery" not in q_

    sql(A, "INSERT INTO audit (at, session_id, action) VALUES (datetime('now'), ?, ?)",
        (sid, "Share a download gallery too: the next build is the download gallery"))
    assert "online gallery" in preview(c, sid)

def test_308c_the_client_said_no_only_where_a_client_can_decline(studio):
    A, c = studio
    sid = booked(A, c)
    up = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Back up photos'", (sid,))[0]
    c.post(f"/session/{sid}/steps", data={"shown": [f"t{up}", "aagreement_signed"], "keep": []}, environ_base=E)
    sql(A, "UPDATE sessions SET stage='shot' WHERE id=?", (sid,))
    lines = skipped_lines(page(c, sid))
    backup = next(li for li in lines if "original photos" in li.words())
    assert "Undo" in backup.words() and "The client said no" not in backup.words()
    sql(A, "UPDATE sessions SET stage='booked' WHERE id=?", (sid,))
    agreement = next(li for li in skipped_lines(page(c, sid)) if "Agreement" in li.words())
    assert "The client said no" in agreement.words()

def test_308c_send_invoice_closed_by_the_payment_reads_done(studio):
    A, c = studio
    from test_305_journeys import booked_and_ready
    retouch(c, False)
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-5), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    due = run(A, lambda: A.totals(sid)["due"])
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "%.2f" % due, "method": "cash",
                                         "received": day(0), "once": "p1"}, environ_base=E)
    t_ = one(A, "SELECT done, outcome, outcome_note FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))
    assert t_[0] and t_[2] == "Paid in full", t_
    p = page(c, sid)
    assert not [li for li in skipped_lines(p) if "invoice" in li.words().lower()]
    done = next(li for li in parse(p).iter() if li.tag == "li" and "ui-past-line" in li.cls() and "Done:" in li.words())
    assert "paid in full" in done.words()

def test_308c_an_intake_skipped_earlier_stays_listed(studio):
    A, c = studio
    sid = booked(A, c)
    c.post("/close", data={"sid": sid, "step": "intake_done", "outcome": "na", "from": "row"}, environ_base=E)
    li = next(li for li in skipped_lines(page(c, sid)) if "ntake" in li.words())
    assert "Skipped" in li.words() and "Undo" in li.words()

def test_308d_f_undo_the_handover_on_a_no_retouching_session(studio):
    A, c = studio
    sid = usb_paid_at_shot(A, c, retouch_picks="")
    retouch(c, True)
    run(A, lambda: A.sync_workflow_tasks(sid))
    drop_retouching(A, c, sid)
    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"
    c.post(f"/session/{sid}/deliver/undo", data={}, environ_base=E)
    p = page(c, sid)
    assert not [li for li in skipped_lines(p) if "retouch" in li.words().lower()]

    assert stage(A, sid) == "shot" and "Delivered since" not in p

def test_308e_the_client_gallery_says_the_day_it_was_shot(studio):
    A, c = studio
    sid = booked(A, c, when=14)
    c.post(f"/session/{sid}/quick", data={"act": "mark_shot"}, environ_base=E)
    s_ = one(A, "SELECT * FROM sessions WHERE id=?", (sid,))
    from datetime import date
    want = date.today()
    got = run(A, lambda: A.app.config["gallery_brand"](s_))["date"]
    assert got == f"{want:%B} {want.day}, {want.year}"

def test_308g_paperwork_refreshes_in_place(studio):
    A, c = studio
    sid = booked(A, c)
    sec = next((n for n in parse(page(c, sid)).iter() if n.get("id") == "paperwork"), None)
    assert sec is not None and sec.has("data-ui-part")

def test_308h_inquiry_codeword_waits_for_an_online_gallery_and_no_stale_check_time(studio):
    A, c = studio
    form = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    assert 'class="inq-cw"' in form
    css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "style.css")).read()
    assert 'form:has(input[name="deliver_gallery"]:not(:checked)) .inq-cw { display: none; }' in css
    assert "#gallery-sec > .ui-sec-title { min-height: 46px" in css
    from plugins import gallery as G
    import time
    G.LAST_CHECK["at"] = time.time() - 9 * 60
    sid = new_session(A, "family", "closed", delivery_method="usb", shoot_date=day(-10))
    G.UP_AT[sid] = time.time()
    on_ = run(A, lambda: [fn([sid]) for fn in A.hooks.client_online])
    assert all(r_.get("at") is None for r_ in on_)

def test_308i_gallery_to_usb_after_booking_leaves_no_gallery_behind(studio):
    A, c = studio
    from test_307_fixes import agreement_text
    sid = booked(A, c, delivery_method="link")
    assert "online gallery" in agreement_text(c, sid)
    c.post(f"/session/{sid}/files-go", data={"delivery_method": "usb"}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert "online gallery" not in a_ and "Your gallery link works for anyone who has it" not in a_

    keys = run(A, lambda: [st["key"] for g_ in A.workflow(sid)["stages"] for st in g_["steps"] if st.get("key")])
    assert "first_visit" not in keys and "favorites" in keys

    p = page(c, sid)
    line = next(n for n in parse(p).iter() if n.get("id") == "quote-delivery-old")
    assert "The quote you sent says" in line.words() and "Send it again" in line.words()

    sql(A, "UPDATE sessions SET stage='shot', shoot_date=? WHERE id=?", (day(-2), sid))
    assert "once paid" not in page(c, sid)

def test_308j_steps_the_app_closed_read_not_needed_with_the_reason(studio):
    A, c = studio
    sid = booked(A, c)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-1), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    t_ = one(A, "SELECT outcome, outcome_note FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))
    assert tuple(t_) == ("na", "Settled: not needed after the shoot")
    w = run(A, lambda: A.workflow(sid))
    st = next(st for g_ in w["stages"] for st in g_["steps"] if st["label"] == "Send session details")
    assert st["done"] and not st["outcome"] and not st["undo"] and st["na_why"] == "not needed after the shoot"

def test_308k_the_client_said_no_is_a_tag_not_a_second_undo(studio):
    A, c = studio
    sid = booked(A, c)
    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "na", "from": "row"}, environ_base=E)
    li = next(li for li in skipped_lines(page(c, sid)) if "Agreement" in li.words())
    said = next(b for b in li.iter() if b.tag == "button" and b.words() == "The client said no")
    undo = next(b for b in li.iter() if b.tag == "button" and b.words() == "Undo")
    assert "ui-btn-reason" in said.cls() and "ui-btn-reason" not in undo.cls()

def test_308l_the_closed_box_says_the_handover_and_offers_its_undo(studio):
    A, c = studio
    sid = usb_paid_at_shot(A, c)
    c.post(f"/session/{sid}/deliver/another", data={"how": "usb", "step": "sd_shipped", "on": day(0)}, environ_base=E)
    assert stage(A, sid) == "closed"
    line = next(n for n in parse(page(c, sid)).iter() if n.get("id") == "closed-handover")
    assert "Photos handed over" in line.words() and "Undo the handover" in line.words()

def test_308m_steps_for_this_session_lists_done_steps_of_earlier_stages_to_reopen(studio):
    A, c = studio
    sid = usb_paid_at_shot(A, c)
    sql(A, "UPDATE sessions SET stage='delivered' WHERE id=?", (sid,))
    root = parse(c.get(f"/session/{sid}/steps", environ_base=E).get_data(as_text=True))
    cull = next(li for li in root.iter() if li.tag == "li" and "Cull" in li.words())
    btn = next(b for b in cull.iter() if b.tag == "button" and b.words() == "Reopen")
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Cull'", (sid,))[0]
    assert btn.get("formaction") == f"/task/{tid}" and btn.get("value") == "0"
    c.post(f"/task/{tid}", data={"set": "0"}, environ_base=E)
    assert one(A, "SELECT done FROM tasks WHERE id=?", (tid,))[0] is None

def test_308n_mark_the_agreement_not_signed_from_the_client_page_keeps_sent(studio):
    A, c = studio
    sid = booked(A, c)
    sql(A, "UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=? AND kind='agreement'", (day(-3), day(-2), sid))
    form = next(f for f in parse(page(c, sid)).iter() if f.tag == "form" and f.get("action") == f"/session/{sid}/agreement/unsign")
    assert "stays marked sent" in form.get("data-confirm")
    c.post(f"/session/{sid}/agreement/unsign", data={}, environ_base=E)
    assert tuple(one(A, "SELECT status, sent, signed FROM documents WHERE session_id=? AND kind='agreement'", (sid,))) == ("sent", day(-3), None)

    did = one(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0]
    sql(A, "UPDATE documents SET status='signed', signed=? WHERE id=?", (day(-1), did))
    c.post(f"/doc/{did}", data={"cycle": "1", "from": "signed"}, environ_base=E)
    assert one(A, "SELECT status FROM documents WHERE id=?", (did,))[0] == "sent"

def test_308o_offer_retouching_off_names_the_sessions_that_keep_it(studio):
    A, c = studio
    from test_307_fixes import picks_in_at_shot
    sid = picks_in_at_shot(A, c)
    retouch(c, False)
    p = c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)
    line = next(n for n in parse(p).iter() if n.get("id") == "retouch-owed")
    assert "The Hollis Family" in line.words() and "No retouching for this session" in line.words()
    assert f"s={sid}" in p

def test_308p_a_minor_client_points_to_a_parent_or_guardian(studio):
    A, c = studio
    from test_307_fixes import minor_client
    sid = minor_client(A, c)
    hid = one(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0]
    row = next(n for n in parse(page_hid(c, hid, sid)).iter() if n.get("id") == "add-guardian")
    assert "no parent or guardian is on file" in row.words() and "Add a parent or guardian" in row.words()
    c.post(f"/client/{hid}/person", data={"name": "Jo Halvorsen", "relation": "mother", "email": "jo@example.com"}, environ_base=E)
    to = run(A, lambda: A._send_ctx(sid, "quote", "Your quote", ["Hello"])["to"])
    assert to == "jo@example.com"
    assert not [n for n in parse(page_hid(c, hid, sid)).iter() if n.get("id") == "add-guardian"]

def page_hid(c, hid, sid):
    return c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)

def test_308q_r_viewer_and_gallery_lines_agree():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    v = open(os.path.join(root, "plugins", "gallery_assets", "viewer.html")).read()
    assert 'if (!state && was) { if (note) announce(note); else $("live").textContent = ""; }' in v
    assert 'if (!seen && !PREVIEW && sig() === "[]") openW();' in v
    g = open(os.path.join(root, "templates", "_gallery_block.html")).read()
    assert "'view and download' if (dl_online if stale else dl) else 'view'" in g
