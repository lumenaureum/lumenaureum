import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, _needs, _page, _session, env
from test_walk_15 import _booked, _q, _row, _x

def _ago(A, n):
    return (A.today() - timedelta(days=n)).isoformat()

def test_a_stage_the_upgrade_pass_moved_counts_from_the_evidence_not_the_update_day(env):
    A, c = env
    _booked(A)
    _x(A, "DELETE FROM stage_log WHERE session_id=1")
    _x(A, "INSERT INTO stage_log (session_id, stage, at, era) VALUES (1, 'booked', ?, 'upgrade')", (A.today().isoformat() + " 08:00:00",))
    _x(A, "INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 50, ?, 'cash')", (_ago(A, 40),))
    with A.app.test_request_context():
        assert A.days_in_stage(A.q("SELECT * FROM sessions WHERE id=1", one=True)) == 40
    assert "for 40 days" in _page(c).split("</header>", 1)[0]

def test_with_nothing_to_date_it_the_page_says_no_since_at_all(env):
    A, c = env
    _booked(A)
    _x(A, "DELETE FROM stage_log WHERE session_id=1")
    _x(A, "INSERT INTO stage_log (session_id, stage, at, era) VALUES (1, 'booked', ?, 'upgrade')", (A.today().isoformat() + " 08:00:00",))
    with A.app.test_request_context():
        assert A.days_in_stage(A.q("SELECT * FROM sessions WHERE id=1", one=True)) is None
    head = _page(c).split("</header>", 1)[0]
    assert "since today" not in head and "Booked" in head

def test_a_real_stage_entry_still_counts(env):
    A, c = env
    _booked(A)
    _x(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, 'booked', ?)", (_ago(A, 3) + " 10:00:00",))
    with A.app.test_request_context():
        assert A.days_in_stage(A.q("SELECT * FROM sessions WHERE id=1", one=True)) == 3

def test_today_fills_the_date_already_set(env):
    A, c = env
    _session(A, "booked", days=14)
    day = (A.today() + timedelta(days=14)).isoformat()
    page = c.get("/", environ_base=E).get_data(as_text=True)
    form = page.split('name="act" value="date"', 1)[1].split("</form>", 1)[0]
    assert f'name="shoot_date" aria-label="Shoot date" required class="ui-input-date" data-avail="1" value="{day}"' in form

def test_send_the_intake_questions_shows_the_email_first(env):
    A, c = env
    _session(A, "inquiry", days=None, msg="Hello")
    page = _page(c)
    row = _row(page, "Send the intake questions")
    assert 'data-ui-open="f-intake_done-1"' in row and "data-ui-mail" not in row
    box = page.split('id="f-intake_done-1"', 1)[1].split("</form>", 1)[0]
    assert "Your mail app opens with this email to alex.morgan.long.address@example.com" in box
    assert "<b>Subject:</b> A few questions before your session" in box
    assert "could you answer a few questions?" in box
    assert 'action="/session/1/intake/send"' in box and "data-ui-mail=" in box

def test_the_deposit_row_says_what_is_left_and_offers_remind_them(env):
    A, c = env
    _booked(A)
    _x(A, "INSERT INTO invoice_sent (session_id, number, sig) VALUES (1, '2026-001', '')")
    _x(A, "INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 10, ?, 'cash')", (A.today().isoformat(),))
    with A.app.test_request_context():
        t = A.totals(1)
    assert t["deposit_short"] > 0.005
    page = _page(c)
    row = _row(page, "Collect the deposit")
    assert row and "Deposit paid" not in page.split('id="more"', 1)[0]
    assert f"{A.money(t['deposit_short'])} left of the {A.money(t['deposit_req'])} deposit; $10.00 paid so far." in row
    assert f"Record payment ({A.money(t['deposit_short'])})" in row
    remind = re.search(r'<form method="post" action="/session/1/remind" data-ui-inplace data-ui-mail="(mailto:[^"]+)"', row)
    assert remind and "alex.morgan.long.address@example.com" in remind.group(1)
    r = c.post("/session/1/remind", data={"kind": "deposit", "mail": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:")
    from urllib.parse import unquote
    assert A.money(t["deposit_short"]) in unquote(r.headers["Location"])
    assert _q(A, "SELECT summary FROM log WHERE session_id=1 AND summary LIKE 'Sent a payment reminder%'")

def test_reply_is_remembered_on_reload(env):
    A, c = env
    _session(A, "inquiry", days=None, msg="Any weekend dates?")
    needs = _needs(_page(c))
    assert "You replied " not in needs and ">Mark replied</button>" in needs
    c.post("/session/1/reply", data={"back": "/c/1?s=1", "how": "email"}, environ_base=E)
    needs = _needs(_page(c))
    assert "You replied " in needs and ">Reply by email</a>" in needs and ">Mark replied</button>" not in needs

def test_the_line_picker_keeps_other_types_in_their_own_fold_and_labels_how_many(env):
    A, c = env
    _session(A, "inquiry", days=None)
    page = _page(c)
    forms = re.findall(r'<form method="post" action="/session/1/quote/fee"[^>]*>.*?</form>', page, re.S)
    assert forms
    main = forms[0]
    assert "<span>How many</span>" in main
    with A.app.test_request_context():
        items = A.current_schedule()["items"]
    others = [it["label"] for it in items if it.get("type") and it["type"] != "family"]
    if others and any(it.get("type") == "family" for it in items):
        assert not any(f">{o.split(',')[0]}" in main for o in others)
        assert 'id="other-lines"' in page and "Lines from other session types" in page

def test_a_draft_invoice_asks_before_email_issues_it(env):
    A, c = env
    _booked(A)
    page = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    bar = page.split('<div class="bar">', 1)[1].split('<div class="page">', 1)[0]
    assert ">Issue and email it</button>" in bar and 'data-confirm="Issue this invoice now?' in bar
    assert ">Issue and download PDF</button>" in bar and ">Issue and print</button>" in bar
    assert "Draft, no number yet" in bar
    assert not _q(A, "SELECT 1 FROM doc_numbers WHERE session_id=1 AND kind='INV'")

def test_finances_marks_an_overdue_balance_and_offers_remind_them(env):
    A, c = env
    _booked(A)
    _x(A, "UPDATE sessions SET shoot_date=? WHERE id=1", (_ago(A, 60),))
    page = c.get(f"/money?year={A.today().year}", environ_base=E).get_data(as_text=True)
    if 'id="upcoming"' not in page:
        return
    sec = page.split('id="upcoming"', 1)[1].split("</section>", 1)[0]
    assert "Overdue " in sec
    assert 'action="/session/1/remind"' in sec and ">Remind them</button>" in sec

def test_history_is_first_and_open_and_cancel_stands_apart(env):
    A, c = env
    _booked(A)
    more = _page(c).split('id="more"', 1)[1]
    hist = re.search(r'<details class="ui-fold [^"]*" id="history"[^>]*>', more)
    assert hist and " open" in hist.group(0)
    assert more.index('id="history"') < more.index("New session for")
    assert 'class="ui-btnrow ui-danger-row"><a class="ui-btn ui-btn-danger" href="#cancel-booking"' in more

def test_reply_by_email_or_text_and_mark_how_it_went(env):

    A, c = env
    _session(A, "inquiry", days=None, msg="Would you do a family photo?")
    con = A.connect()
    con.execute("UPDATE people SET phone='(555) 010-2020' WHERE household_id=1")
    con.commit()
    con.close()
    needs = _needs(_page(c))
    assert 'href="mailto:alex.morgan.long.address@example.com' in needs and ">Reply by email</a>" in needs
    assert 'href="sms:5550102020"' in needs and ">Reply by text</a>" in needs
    assert 'value="call"' in needs and 'value="in person"' in needs and ">Mark replied</button>" in needs
    con = A.connect()
    con.execute("UPDATE people SET phone='', email='' WHERE household_id=1")
    con.commit()
    con.close()
    needs = _needs(_page(c))
    assert 'href="sms:"' in needs and 'href="mailto:?subject=' in needs
    c.post("/session/1/reply", data={"back": "/c/1?s=1", "how": "call"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT channel FROM log WHERE session_id=1 AND summary='Replied to their message'").fetchone()[0] == "call"
    con.close()
