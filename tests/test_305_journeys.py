import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, jpeg, join, make_studio, new_session, one, run, sql
from journey_walker import Walker

BUDGETS = {
    "j01_closed_usb_to_gallery": dict(taps=3, pages=2, dismissals=0, forced=0, opens=0, screens=1.0),
    "j02_inquiry_to_quote_sent": dict(taps=10, pages=6, dismissals=0, forced=0, opens=0, screens=2.5),
    "j03_book_signed_and_deposit": dict(taps=5, pages=0, dismissals=0, forced=0, opens=1, screens=1.0),
    "j04_shoot_day_to_shot": dict(taps=1, pages=0, dismissals=0, forced=0, opens=0, screens=0.5),
    "j05_usb_only_booking_to_closed": dict(taps=19, pages=5, dismissals=0, forced=0, opens=0, screens=2.0),
    "j06_link_picks_to_finals": dict(taps=11, pages=5, dismissals=0, forced=1, opens=0, screens=4.5),
    "j07_chase_unpaid_balance": dict(taps=6, pages=2, dismissals=0, forced=0, opens=1, screens=1.0),
    "j08_change_shoot_date": dict(taps=2, pages=0, dismissals=0, forced=0, opens=0, screens=0.5),
    "j09_change_delivery_method": dict(taps=3, pages=0, dismissals=0, forced=0, opens=0, screens=1.0),
    "j10_retouching_off_in_flight": dict(taps=3, pages=2, dismissals=0, forced=0, opens=0, screens=0.5),
}
RESULTS = {}

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def check(name, w):
    got = w.counts()
    RESULTS[name] = got
    print(f"\nJOURNEY {name} {got}\n  " + "\n  ".join(w.log))
    over = {k: (v, BUDGETS[name][k]) for k, v in got.items() if v > BUDGETS[name][k]}
    assert not over, f"{name} is over its budget (got, budget): {over}"

def retouch(c, on):
    c.post("/settings/retouch", data={"offer_retouch": "1"} if on else {}, environ_base=E)

def upload(c, sid, name, data, kind):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name},
               environ_base=E).get_json()
    at = st["received"]
    while at < len(data):
        r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}", data=data[at:at + st["piece"]],
                   headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
        assert r.status_code == 200, r.get_data(as_text=True)
        at = r.get_json()["received"]

def pay(w, opener):

    n = w.find(opener)
    target = (n.attrs.get("data-ui-open") or (n.attrs.get("href") or "#")[1:])
    w.tap(opener)
    w.tap("Record payment", within=target)

def page_of(sid):
    return f"/c/1?s={sid}"

def stage(A, sid):
    return one(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0]

def quoted(A, c, sid, sent=True):

    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "family", "qty": "1", "once": f"q{sid}"}, environ_base=E)
    if sent:
        r = c.post(f"/session/{sid}/quote/send", data={"total": "%.2f" % run(A, lambda: A.totals(sid)["quoted"])},
                   environ_base=E)
        assert r.status_code in (200, 302)
        vid = one(A, "SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC", (sid,))[0]
        c.post(f"/quote/{vid}/sent", data={}, environ_base=E)

def test_j01_closed_usb_session_to_a_no_picks_gallery_online(studio):

    A, c = studio
    retouch(c, False)
    sid = new_session(A, "family", "closed", delivery_method="usb", shoot_date=day(-10))
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Share a gallery")
    w.upload("export", [(f"family-{i}.jpg", jpeg()) for i in range(3)], upload, sid)
    w.tap("Build and put online")
    join("gallery", sid)
    join("gal-up", sid)
    from plugins import gallery as G
    gid = one(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0]
    assert G.online_download(gid) and stage(A, sid) == "closed"
    check("j01_closed_usb_to_gallery", w)

def test_j02_new_family_inquiry_to_quote_sent(studio):
    A, c = studio
    w = Walker(A, c)
    w.open("/")
    w.tap("+ Contact")
    w.type_in("contact_name", "Robin Marlow").type_in("email", "robin@example.com")
    w.tap("Save and start an inquiry")
    w.tap("Family")
    w.type_in("shoot_date", day(20))
    w.tap("Create inquiry")
    w.tap("Build a quote")
    w.choose("fee_key", "family")
    w.tap("Add the line")
    w.tap("Prepare quote")
    w.tap("Email it")
    w.tap("Mark sent")
    sid = one(A, "SELECT MAX(id) FROM sessions")[0]
    assert stage(A, sid) == "quoted"
    check("j02_inquiry_to_quote_sent", w)

def test_j08_change_the_shoot_date_after_booking(studio):

    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14))
    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    sql(A, "UPDATE sessions SET start_time='10:00', stops='Riverside Park' WHERE id=?", (sid,))
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Shoot " + run(A, lambda: A.nice(day(14))), exact=False)
    w.type_in("shoot_date", day(21))
    w.tap("Set")
    assert one(A, "SELECT shoot_date FROM sessions WHERE id=?", (sid,))[0] == day(21)
    check("j08_change_shoot_date", w)

def test_j09_change_how_the_files_go_after_booking(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Change how the files go")
    w.choose("delivery_method", "link")
    w.tap("Save how the files go")
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (sid,))[0] == "link"
    check("j09_change_delivery_method", w)

def test_j10_switch_retouching_off_with_sessions_in_flight(studio):
    A, c = studio
    sid = new_session(A, "family", "shot", shoot_date=day(-2), delivery_method="link")
    w = Walker(A, c)
    w.open("/")
    w.tap("Settings")
    w.tap("Prices", exact=False)
    w.choose("offer_retouch", "1")
    assert not A.C.OFFER_RETOUCH
    assert not one(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (sid,))
    check("j10_retouching_off_in_flight", w)

def test_j03_book_agreement_signed_and_deposit_recorded(studio):

    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(30))
    quoted(A, c, sid)
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Book it")
    w.tap("Mark signed")
    pay(w, "Record payment")
    assert one(A, "SELECT status FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0] == "signed"
    assert run(A, lambda: A.totals(sid)["deposit_short"]) <= 0.005 and stage(A, sid) == "booked"
    check("j03_book_signed_and_deposit", w)

def booked_and_ready(A, c, sid):

    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    sql(A, "UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=?", (day(-5), day(-5), sid))
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "method": "check",
                                         "received": day(-5), "once": f"d{sid}"}, environ_base=E)
    sql(A, "UPDATE sessions SET start_time='00:01', stops='Riverside Park', intake=? WHERE id=?",
        ('{"who": "four of us", "interests": "the dog", "looks": "casual", "outfits": "2"}', sid))
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND stage IN ('booked','inquiry','quoted') AND done IS NULL",
        (day(-3), sid))

def test_j04_shoot_day_to_shoot_done(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7))
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(0), sid))
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Shoot done")
    assert stage(A, sid) == "shot", w.text()[:800]
    check("j04_shoot_day_to_shot", w)

def needs(w):
    return w.by_id("needs").words() if w.by_id("needs") is not None else ""

def test_j05_usb_only_job_from_booking_to_closed(studio):

    A, c = studio
    retouch(c, False)
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid)
    sql(A, "UPDATE sessions SET start_time='10:00', stops='Riverside Park' WHERE id=?", (sid,))
    w = Walker(A, c)
    w.open(page_of(sid))
    w.tap("Book it")

    w.tap("Steps for this session")
    for words in ("Get the intake answers", "Get the agreement signed", "Collect the deposit", "Send the session sheet",
                  "Upload original photos", "Cull", "Upload edited photos"):
        w.toggle(words)
    w.tap("Save the steps")
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-1), sid))
    w.reload()
    w.tap("Shoot done")
    w.tap("Send the invoice")
    w.tap("Issue and email it")
    w.tap("Mark sent")
    w.open(page_of(sid))
    pay(w, "Record payment ($300.00)")
    w.tap("Record the handover")
    assert stage(A, sid) == "closed", needs(w)[:600]
    check("j05_usb_only_booking_to_closed", w)

def owing(A, c, sid):

    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-30), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND done IS NULL AND title != 'Send invoice'", (day(-25), sid))
    c.post(f"/session/{sid}/invoice/issue", data={"sample_ok": "1", "then": "email"}, environ_base=E)
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))[0]
    c.post(f"/task/{tid}", data={"set": "1"}, environ_base=E)

def test_j07_chase_an_unpaid_balance(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    owing(A, c, sid)
    w = Walker(A, c)
    w.open("/")
    w.tap("Finances")
    w.tap("The Hollis Family", exact=False)
    w.tap("Remind them by email")

    pay(w, "Record payment ($200.00)")
    assert run(A, lambda: A.totals(sid)["due"]) <= 0.005
    check("j07_chase_unpaid_balance", w)

def test_j06_link_delivery_with_picks_to_retouched_finals_out(studio):

    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="link")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-20), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    for i in range(3):
        upload(c, sid, f"family-{i}.jpg", jpeg("gray"), "export")
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND stage='shot' AND done IS NULL AND title != 'Send invoice'", (day(-12), sid))
    sql(A, "UPDATE sessions SET retouch_picks=?, gallery_built=?, first_visit=? WHERE id=?",
        ("family-0.jpg: brighten\nfamily-2.jpg: soften", day(-12), day(-10), sid))
    run(A, lambda: A.advance(sid))
    w = Walker(A, c)
    w.open(page_of(sid))
    w.upload("final", [("family-0.jpg", jpeg("white")), ("family-2.jpg", jpeg("white"))], upload, sid)
    w.tap("Send the invoice")
    w.tap("Issue and email it")
    w.tap("Mark sent")
    w.open(page_of(sid))
    pay(w, "Record payment ($200.00)")

    w.tap("Build download gallery")
    join("gallery", sid)
    join("gal-up", sid)
    w.reload()
    w.tap("Upload", within="gallery-sec")
    join("gal-up", sid)
    w.reload()
    w.tap("Email link")
    w.tap("Mark done", forced=True)
    from plugins import gallery as G
    assert G.online_download(one(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0])
    assert one(A, "SELECT done FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    check("j06_link_picks_to_finals", w)

def test_zz_print_the_table():

    for k, v in sorted(RESULTS.items()):
        print(k, v)
