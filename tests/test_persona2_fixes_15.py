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

def test_move_all_families_never_moves_a_family_back(env):

    A, c = env
    past = (date.today() - timedelta(days=10)).isoformat()
    sql(A, "INSERT INTO events (name, date, billing) VALUES ('Team day', ?, 'families')", (past,))
    for i, (name, st) in enumerate((("Abbott", "booked"), ("Bell", "delivered"), ("Ruiz", "delivered"), ("Duffy", "closed")), 1):
        sql(A, "INSERT INTO households (name) VALUES (?)", (name,))
        sql(A, "INSERT INTO sessions (household_id, type, event_id, stage, shoot_date) VALUES (?, 'event', 1, ?, ?)", (i, st, past))
    page = html.unescape(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert "Move the 1 family at Booked to Shot" in page and "Move all families to Shot" not in page
    c.post("/event/1/stage", data={"stage": "shot"}, environ_base=E)
    assert [r_[0] for r_ in sql(A, "SELECT stage FROM sessions ORDER BY id")] == ["shot", "delivered", "delivered", "closed"]
    c.post("/event/1/stage", data={"stage": "booked"}, environ_base=E)
    assert [r_[0] for r_ in sql(A, "SELECT stage FROM sessions ORDER BY id")] == ["shot", "delivered", "delivered", "closed"]
    page = html.unescape(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert "Move the 1 family at Shot to Delivered" in page

    sql(A, "INSERT INTO events (name, date, billing) VALUES ('Empty day', ?, 'families')", (past,))
    assert "Move all families" not in c.get("/event/2", environ_base=E).get_data(as_text=True)

def test_a_step_that_asks_for_a_day_shows_a_date_field_not_its_markup_as_text(env):

    A, c = env
    a_ = dict(post="/session/1/quick", verb="Mark opened", fields=dict(act="mark", field="first_visit"), ask_date=True,
              date_tip="The day the client first opened it. The gallery comes down 30 days later.")
    with A.app.test_request_context():
        out = A.app.jinja_env.from_string('{% import "_ui.html" as ui with context %}{{ ui.step_action(a_, "/c/1") }}').render(a_=a_)
    assert "&lt;input" not in out
    assert re.search(r'<input type="date" name="on" value="[\d-]+" max="[\d-]+" title="The day the client first opened it', out), out
    a_["date_tip"] = 'a "quoted" <tip>'
    with A.app.test_request_context():
        out = A.app.jinja_env.from_string('{% import "_ui.html" as ui with context %}{{ ui.step_action(a_, "/c/1") }}').render(a_=a_)
    assert 'title="a &#34;quoted&#34; &lt;tip&gt;"' in out

def _booked(A, amount=400, days=200):
    sql(A, "INSERT INTO households (name) VALUES ('Kim')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Sophie Kim', 'adult', 'sophie@example.com')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'booked', ?)",
        ((date.today() + timedelta(days=days)).isoformat(),))
    sql(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', ?, 'fee')", (amount,))

def test_send_the_deposit_invoice_bills_the_deposit_due_now(env):

    A, c = env
    _booked(A)
    with A.app.test_request_context():
        t_ = A.totals(1)
    dep = t_["deposit_short"]
    assert 0 < dep < 400
    m = A.money
    with A.app.test_request_context():
        dep_s, rest_s = m(dep), m(400 - dep)
    inv = html.unescape(c.get("/session/1/invoice", environ_base=E).get_data(as_text=True))
    assert "Deposit invoice" in inv and "<td>Terms</td><td>Deposit due now</td>" in inv
    assert re.search(r"<b>Deposit due now</b></td><td class=\"n\"><b>" + re.escape(dep_s) + "<", inv), inv
    assert "Balance due</b>" not in inv
    assert f"The {dep_s} deposit is due now and holds your date. The balance of {rest_s} is due" in inv

    c.post("/session/1/invoice/issue", environ_base=E)
    assert "Deposit due now" in c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    sql(A, "INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', ?, ?)", (dep, date.today().isoformat()))
    with A.app.test_request_context():
        v = A.bill_ctx(1, live=True)
    assert not v["deposit_due"] and v["balance"] == pytest.approx(400 - dep)

def test_a_guests_release_names_them_and_a_property_release_is_one(env):

    A, c = env
    _booked(A)
    c.post("/session/1/doc", data={"kind": "release", "about_person": "", "signer": "Grace Lee", "covers": "Grace Lee"}, environ_base=E)
    c.post("/session/1/doc", data={"kind": "release", "about_person": "", "signer": "Ana Ortiz",
                                   "covers": "Leo Ortiz", "covers_minor": "1"}, environ_base=E)
    c.post("/session/1/doc", data={"kind": "property release", "about_person": "", "signer": "Riverbend Barn LLC"}, environ_base=E)
    rows = sql(A, "SELECT id, kind, covers, covers_kind FROM documents ORDER BY id")
    assert [tuple(r_)[1:] for r_ in rows] == [("release", "Grace Lee", "guest"), ("release", "Leo Ortiz", "minor"),
                                              ("release", "Riverbend Barn LLC", "property")]
    leo = html.unescape(c.get(f"/doc/{rows[1][0]}/release", environ_base=E).get_data(as_text=True))
    assert "permission to photograph Leo Ortiz" in leo and "Sophie Kim" not in leo.split("permission to photograph", 1)[1][:40]
    assert "<td>Leo Ortiz (a minor)</td>" in leo and "parent or legal guardian of Leo Ortiz" in leo
    assert "delivered to the family" not in leo and "delivered to me" not in leo
    assert "The edited photos are delivered to Kim, who booked the session, not to us" in leo
    grace = html.unescape(c.get(f"/doc/{rows[0][0]}/release", environ_base=E).get_data(as_text=True))
    assert "permission to photograph Grace Lee" in grace and "not to me" in grace and "copyright in them is transferred" not in grace
    prop = html.unescape(c.get(f"/doc/{rows[2][0]}/release", environ_base=E).get_data(as_text=True))
    assert "<h1>Property release</h1>" in prop and "permission to photograph the property Riverbend Barn LLC" in prop
    page = html.unescape(c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    assert f'href="/doc/{rows[2][0]}/release"' in page and "Property release to sign - Riverbend Barn LLC" in page
    assert "Release to sign - Leo Ortiz" in page and "Release to sign - Grace Lee" in page
    assert "Send the 3 releases" in page

def test_an_engagement_on_the_signed_wedding_quote_shares_its_signature_and_not_its_hours(env):

    A, c = env
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_engagements": "1"}, environ_base=E)
    sql(A, "INSERT INTO households (name) VALUES ('Kim')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Sophie Kim', 'adult', 'sophie@example.com')")
    later = lambda n: (date.today() + timedelta(days=n)).isoformat()
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'wedding', 'booked', ?)", (later(300),))
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, fee_key) VALUES (1, 'Wedding day (8 hr)', 2800, 'fee', 'wedding')")
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, fee_key) VALUES (1, 'Engagement session (1.5 hr)', 350, 'fee', 'engagement')")
    sql(A, "INSERT INTO documents (session_id, kind, signer, status, sent, signed) VALUES (1, 'agreement', 'Sophie Kim', 'signed', ?, ?)",
        (date.today().isoformat(), date.today().isoformat()))
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'engagement', 'booked', ?)", (later(100),))
    with A.app.test_request_context():
        assert A.session_minutes(1, "wedding") == 480
    c.post("/session/2/plan", data={"plan_of": "1", "back": "/c/1?s=2"}, environ_base=E)
    assert sql(A, "SELECT plan_of FROM sessions WHERE id=2")[0][0] == 1
    assert sql(A, "SELECT status FROM documents WHERE session_id=1 AND kind='agreement'")[0][0] == "signed"
    assert not sql(A, "SELECT 1 FROM tasks WHERE title LIKE 'Have the plan%'")
    page = html.unescape(c.get("/c/1?s=2", environ_base=E).get_data(as_text=True))
    assert "agreement still open" not in page and "Agreement signed (waiting on the client)" not in page
    assert "Billing: on the quote of Wedding" in page and "Billing: no quote yet" not in page

    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'booked', ?)", (later(50),))
    c.post("/session/3/plan", data={"plan_of": "1", "back": "/c/1?s=3"}, environ_base=E)
    assert sql(A, "SELECT status FROM documents WHERE session_id=1 AND kind='agreement'")[0][0] != "signed"

def test_due_in_n_days_holds_a_line_with_anything_due_today(env, monkeypatch):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Brennan')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'booked', ?)",
        ((date.today() + timedelta(days=60)).isoformat(),))
    t_ = A.today().isoformat()
    monkeypatch.setattr(A.hooks, "today", A.hooks.today + [lambda: [dict(
        text="Print order from Brennan: paid, ready for the lab", link="/session/1", verb="Open the print order", when=t_,
        who=dict(name="Brennan", sub="", link="/session/1", hid=1))]])
    page = c.get("/", environ_base=E).get_data(as_text=True)
    line = re.search(r'<li class="ui-tline[^"]*" id="line-s1"[^>]*>', page)
    assert line and f'data-f-due="{t_}"' in line.group(0), line and line.group(0)
    assert A.task_kind("Confirm the venue's photo rules") == "Paperwork"
    assert A.task_kind("Confirm time and place") == "Date and place"

def test_a_crowded_day_is_one_overlap_line_not_every_pair(env):

    A, c = env
    d1, d2 = (date.today() + timedelta(days=17)).isoformat(), (date.today() + timedelta(days=37)).isoformat()
    for i in range(9):
        sql(A, "INSERT INTO households (name) VALUES (?)", (f"Mini {i}",))
        sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (?, 'family', 'booked', ?)",
            (i + 1, d1 if i < 7 else d2))
    with A.app.test_request_context():
        acts = A.build_actions()
        lines = [a_["what"] for a_ in acts if "overlap" in a_["what"].lower()]
        assert lines == [f"7 sessions overlap on {A.nice(d1)}", "Overlaps Mini 8 (same day, no start time yet)"], lines
        one = [a_["what"] for a_ in A.build_actions(only_sid=2) if "overlap" in a_["what"].lower()]
        assert one == [f"7 sessions overlap on {A.nice(d1)}"]
    cal = html.unescape(c.get(f"/calendar?m={d1[:7]}&d={d1}", environ_base=E).get_data(as_text=True))
    assert cal.count("Overlaps") <= 1 and "7 sessions overlap" in cal

def test_the_check_in_draft_names_the_session_once(env):

    A, c = env
    from urllib.parse import unquote
    sql(A, "INSERT INTO households (name) VALUES ('Park')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Olivia Park', 'adult', 'olivia@example.com')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'closed', '2025-12-06')")
    with A.app.test_request_context():
        body = unquote(A.check_in_draft(1))
    assert "since your portrait session, and" in body and "session session" not in body
    sql(A, "UPDATE sessions SET type='family'")
    with A.app.test_request_context():
        body = unquote(A.check_in_draft(1))
    assert "session session" not in body and re.search(r"since your [a-z ]+ session, and", body), body

def test_setup_names_the_types_whose_own_ready_days_print(env):

    A, c = env
    c.post("/setup/shoot", data={"preset_weddings": "1"}, environ_base=E)
    page = html.unescape(c.get("/setup/money", environ_base=E).get_data(as_text=True))
    assert "Not for Weddings (60 days): that type has its own number, set on its page under What you shoot." in page
    assert A.C.delivery_days("wedding") == 60

def test_finances_and_the_year_summary_agree_on_print_orders(env):

    A, c = env
    _booked(A)
    y = A.today().year
    sql(A, "INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 100, ?)", (f"{y}-03-02",))
    pr = dict(any=True, received=6360, tax=360, cost=2000, lab_tax=0, sales=6000, shipping=0, profit=4000, refunds=0)
    A.app.config["prints_year"] = lambda y_: pr
    with A.app.test_request_context():
        v = A.year_summary(y)
        all_in, tax = A.money(v["net"]), A.money(-v["tax"])
    page = html.unescape(c.get(f"/money?year={y}", environ_base=E).get_data(as_text=True))
    assert f"{all_in} received" in page, all_in
    assert "For print orders" in page and "Print lab cost" in page
    row_ = re.search(r"Sales tax to remit.*?</tr>", page, re.S).group(0)
    assert tax in row_, row_

def test_pre_shoot_steps_close_as_not_needed_on_delivery_and_never_lead_after(env):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Brennan')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Hannah Brennan', 'adult', 'h@example.com')")
    with A.app.test_request_context():
        sid = A.create_session(1, "portrait", "inquiry", shoot_date=(date.today() - timedelta(days=5)).isoformat())
        A.db().commit()
        A.move_on(sid, "quoted", "test")
        A.move_on(sid, "booked", "test")
        A.db().commit()
        assert any("intake" in (a_.get("what") or "").lower() for a_ in A.build_actions(only_sid=sid))
        A.move_on(sid, "shot", "test")
        A.move_on(sid, "delivered", "test")
        A.db().commit()
    assert ("intake_done", "na") in [tuple(r_) for r_ in sql(A, "SELECT step, outcome FROM step_closes WHERE session_id=?", (sid,))]
    hist = [r_[0] for r_ in sql(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
    assert any(h_.startswith("Moved to Delivered") and "Not needed:" in h_ for h_ in hist), hist
    with A.app.test_request_context():
        acts = A.build_actions(only_sid=sid)
        assert not any("intake" in (a_.get("what") or "").lower() for a_ in acts), [a_.get("what") for a_ in acts]
        s_ = A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        rows = A.focus_rows([a for a in acts if a["who"]["link"] == f"/session/{sid}"], s_, A.totals(sid))
        do = A.today_action(s_, rows, None)
        assert not do or "intake" not in (do.get("what") or "").lower()

    assert not sql(A, "SELECT 1 FROM step_closes WHERE session_id=? AND step IN ('agreement_signed','deposit_paid','balance_paid')", (sid,))

    sql(A, "DELETE FROM step_closes WHERE session_id=?", (sid,))
    with A.app.test_request_context():
        assert not any("intake" in (a_.get("what") or "").lower() for a_ in A.build_actions(only_sid=sid))

def test_shoot_done_on_a_past_shoot_says_what_is_open_and_asks(env):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Lane')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Lane', 'adult', 'a@example.com')")
    with A.app.test_request_context():
        sid = A.create_session(1, "portrait", "booked", shoot_date=(date.today() - timedelta(days=20)).isoformat())
        A.db().commit()
    page = html.unescape(c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True))
    form = re.search(r'<form[^>]*data-confirm="([^"]*still open\. Move to Shot\?)"[^>]*>(?:(?!</form>).)*>Shoot done</button>', page, re.S)
    assert form, re.findall(r'<form[^>]*>(?:(?!</form>).)*Shoot done(?:(?!</form>).)*</form>', page, re.S)[:2]
    assert "gree" in form.group(1)

def test_a_wedding_and_its_own_engagement_are_one_booking_not_a_repeat_client(env):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Kim')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'booked', '2027-11-13')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date, plan_of) VALUES (1, 'portrait', 'booked', '2027-06-01', 1)")
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert "Repeat:" not in page and 'data-f-g-show="Repeat clients' not in page

    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'closed', '2025-06-01')")
    assert "Repeat:" not in c.get("/clients", environ_base=E).get_data(as_text=True)

def test_a_new_contact_with_a_name_on_file_asks_first_and_pickers_tell_them_apart(env):

    A, c = env
    c.post("/contact/new", data={"contact_name": "Avery Adams", "email": "avery1@example.com"}, environ_base=E)
    n0 = sql(A, "SELECT COUNT(*) FROM households")[0][0]
    r = c.post("/contact/new", data={"contact_name": "avery  adams", "phone": "555-0100"}, environ_base=E)
    page = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 200 and "Already a client?" in page and "avery1@example.com" in page
    assert 'name="same_ok" value="1"' in page and 'value="555-0100"' in page
    assert sql(A, "SELECT COUNT(*) FROM households")[0][0] == n0
    c.post("/contact/new", data={"contact_name": "avery  adams", "phone": "555-0100", "same_ok": "1"}, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM households")[0][0] == n0 + 1
    form = html.unescape(c.get("/contact/new", environ_base=E).get_data(as_text=True))
    assert "Avery Adams (avery1@example.com)</option>" in form and "(555-0100)</option>" in form
