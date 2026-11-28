import html
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, _session, env

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _iso(A, days):
    return (A.today() + timedelta(days=days)).isoformat()

def _priced(A, sid=1, amount=1000.0, booked_on=None):
    run(A, "INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?, 'Session', ?, 'custom', 0)", (sid, amount))
    run(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, 'booked', ?)", (sid, booked_on or A.today().isoformat()))
    with A.app.test_request_context():
        A.ensure_quote(sid)
        A.db().commit()

def _pay(c, amount, kind="deposit", sid=1):
    r = c.post(f"/session/{sid}/money", data=dict(what="payment", kind=kind, amount=str(amount), method="check",
                                                  received="", back=f"/c/1?s={sid}"), environ_base=E)
    assert r.status_code == 302

def _s(A, sid=1):
    return A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?",
               (sid,), one=True)

def _ctx(A, fn, *args, **kw):
    with A.app.test_request_context():
        return fn(*args, **kw)

def _text(page):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page)))

def _section(c, A, sec_id):
    page = c.get(f"/money?year={A.today().year}", environ_base=E).get_data(as_text=True)
    if f'id="{sec_id}"' not in page:
        return ""
    return _text(page.split(f'id="{sec_id}"', 1)[1].split("</section>", 1)[0])

def test_after_the_shoot_and_before_the_balance_due_date_the_balance_is_due_not_overdue(env):
    A, c = env
    _session(A, "booked", days=-3)
    _priced(A, booked_on=_iso(A, -40))
    _pay(c, 100)
    bd = _ctx(A, A.balance_due_date, _iso(A, -3), "family", sid=1)
    assert bd > A.today().isoformat()
    with A.app.test_request_context():
        rem, ov = A.payment_reminder(_s(A)), A.overdue(_s(A))
        bd_txt = A.nice(bd)
    assert ov["amount"] == 0.0 and not rem["late"] and rem["due"] == bd
    assert rem["words"] == f"Balance $900.00, due {bd_txt}"
    row = _section(c, A, "upcoming")
    assert "Overdue" not in row and f"Balance $900.00, due {bd_txt}" in row, row
    run(A, "UPDATE sessions SET shoot_date=? WHERE id=1", (_iso(A, -60),))
    with A.app.test_request_context():
        assert A.overdue(_s(A))["amount"] == 900.0 and A.payment_reminder(_s(A))["late"]
    assert "Overdue $900.00" in _section(c, A, "upcoming")

def test_owed_after_the_shoot_reads_due_until_the_terms_date_on_finances_and_clients(env):
    A, c = env
    _session(A, "shot", days=-3)
    _priced(A, booked_on=_iso(A, -40))
    _pay(c, 100)
    run(A, "INSERT INTO invoice_sent (session_id, number, sig) VALUES (1, 'INV-1', '')")
    bd = _ctx(A, A.balance_due_date, _iso(A, -3), "family", sid=1)
    owed = _section(c, A, "owed")
    assert f"due {_ctx(A, A.nice, bd)}" in owed and "was due" not in owed, owed
    clients = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert "$900.00" in clients and 'data-tip="Overdue"' not in clients
    run(A, "UPDATE sessions SET shoot_date=? WHERE id=1", (_iso(A, -60),))
    assert "was due" in _section(c, A, "owed")
    assert 'data-tip="Overdue">$900.00' in c.get("/clients", environ_base=E).get_data(as_text=True)

def test_a_late_deposit_after_the_shoot_is_the_only_overdue_part(env, monkeypatch):
    A, c = env
    _session(A, "booked", days=-3)
    _priced(A, booked_on=_iso(A, -40))
    run(A, "UPDATE sessions SET deposit_booked=300 WHERE id=1")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    monkeypatch.setattr(A, "deposit_step_due", lambda sid_: _iso(A, -30))
    with A.app.test_request_context():
        ov, rem = A.overdue(_s(A)), A.payment_reminder(_s(A))
        dep = A.deposit_held(1)
    assert dep > 0.005 and ov["amount"] == dep and ov["since"] == _iso(A, -30)
    assert rem["kind"] == "balance" and not rem["late"] and f"deposit was due {_ctx(A, A.nice, _iso(A, -30))}" in rem["words"]
    row = _section(c, A, "upcoming")
    assert f"Overdue {_ctx(A, A.money, dep)}" in row and "Overdue $1,000.00" not in row, row

def test_today_and_finances_agree_on_a_late_installment(env):
    A, c = env
    _session(A, "booked", days=-3)
    _priced(A, booked_on=_iso(A, -75))
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    _pay(c, 100)
    with A.app.test_request_context():
        rows, ov = A.installment_schedule(1), A.overdue(_s(A))
        when = A.balance_when(_s(A))
    late = [r for r in rows if r["status"] == "late"]
    assert late and rows[-1]["status"] == "due"
    assert ov["amount"] == round(sum(r["left"] for r in late), 2) and when == late[0]["due"] == ov["since"]
    assert f"Overdue {_ctx(A, A.money, ov['amount'])}" in _section(c, A, "upcoming")

def test_months_after(env):
    A, _c = env
    assert A.months_after(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert A.months_after(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert A.months_after(date(2027, 1, 31), 2) == date(2027, 3, 31)
    assert A.months_after(date(2027, 11, 15), 3) == date(2028, 2, 15)

def test_installments_fall_monthly_on_the_booking_day_and_the_last_on_the_balance_date(env):
    A, c = env
    _session(A, "booked", days=150)
    _priced(A, booked_on=_iso(A, -10))
    run(A, "UPDATE sessions SET installments=4 WHERE id=1")
    with A.app.test_request_context():
        rows = A.installment_schedule(1)
        end = A.balance_due_date(_iso(A, 150), "family", sid=1)
        booked = A.today() - timedelta(days=10)
        want = [A.months_after(booked, k).isoformat() for k in (1, 2, 3)] + [end]
    assert [r["due"] for r in rows] == want and all(r["spacing"] == "monthly" for r in rows)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "fewer months between booking" not in _text(page)

def test_too_few_months_falls_back_to_even_spacing_and_the_plan_form_says_so(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A, booked_on=_iso(A, -5))
    run(A, "UPDATE sessions SET installments=4 WHERE id=1")
    with A.app.test_request_context():
        rows = A.installment_schedule(1)
        end = A.balance_due_date(_iso(A, 30), "family", sid=1)
    assert rows[-1]["due"] == end and all(r["spacing"] == "even" for r in rows)
    dues = [r["due"] for r in rows]
    assert dues == sorted(dues) and len(set(dues)) == 4
    fold = _text(c.get("/c/1?s=1", environ_base=E).get_data(as_text=True).split('id="installments"', 1)[1][:4000])
    assert "fewer months between booking" in fold and "spread evenly instead of one a month" in fold, fold

def _plan(A, n=3, days=150, booked=-10):
    _session(A, "booked", days=days)
    _priced(A, booked_on=_iso(A, booked))
    run(A, "UPDATE sessions SET installments=? WHERE id=1", (n,))

def test_the_agreement_names_the_schedule_and_prints_the_dated_amounts(env):
    A, c = env
    _plan(A)
    t = _text(c.get("/session/1/agreement", environ_base=E).get_data(as_text=True))
    assert ("paid in 3 equal payments on the dates in the payment schedule that comes with this agreement; a reschedule "
            "fee, if any, is added to the last, due" in t), t
    rows = _ctx(A, A.installment_schedule, 1)
    for r in rows:
        assert f"Payment {r['n']} of 3: {_ctx(A, A.money, r['amount'])}, due {_ctx(A, A.long_date, r['due'])}" in t

def test_a_moved_shoot_date_moves_the_printed_schedule(env):
    A, c = env
    _plan(A)
    before = _ctx(A, A.installment_schedule, 1)[-1]["due"]
    run(A, "UPDATE sessions SET shoot_date=? WHERE id=1", (_iso(A, 200),))
    after = _ctx(A, A.installment_schedule, 1)[-1]["due"]
    assert after != before
    t = _text(c.get("/session/1/agreement", environ_base=E).get_data(as_text=True))
    assert f"Payment 3 of 3: " in t and f"due {_ctx(A, A.long_date, after)}" in t
    assert f"due {_ctx(A, A.long_date, before)}" not in t

def test_the_quote_and_the_invoice_print_the_schedule(env):
    A, c = env
    _plan(A)
    rows = _ctx(A, A.installment_schedule, 1)
    with A.app.test_request_context():
        s = _s(A)
        lines = A.q("SELECT * FROM lines WHERE session_id=1")
        qd = A.quote_data(1, s, lines, A.totals(1))
        bill = A.bill_ctx(1, False, live=True)
    assert "comes with this quote" in qd["balance_rule"]
    assert [r["n"] for r in qd["schedule"]] == [1, 2, 3] and "paid" not in qd["schedule"][0]
    assert qd["schedule"][-1]["due"] == _ctx(A, A.long_date, rows[-1]["due"])

    dep_ = [r for r in bill["schedule"] if r.get("deposit")]
    assert [r["amount"] for r in bill["schedule"] if not r.get("deposit")] == [r["amount"] for r in rows]
    with A.app.test_request_context():
        assert round(sum(r["amount"] for r in bill["schedule"]), 2) == round(A.totals(1)["due"], 2)
        doc = _text(A.render_template("_bill_doc.html", v=bill))
    assert not dep_ or "Deposit, due now" in doc
    assert f"Payment 1 of 3, due {_ctx(A, A.long_date, rows[0]['due'])}" in doc

def test_a_quote_before_booking_says_the_dates_follow_the_booking(env):
    A, c = env
    _session(A, "quoted", days=150)
    run(A, "INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 1000, 'custom', 0)")
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    with A.app.test_request_context():
        A.ensure_quote(1)
        A.db().commit()
        qd = A.quote_data(1, _s(A), A.q("SELECT * FROM lines WHERE session_id=1"), A.totals(1))
    assert [r["due"] for r in qd["schedule"][:2]] == ["set by the day you book"] * 2
    assert qd["schedule"][-1]["due"] != "set by the day you book"

def test_no_plan_no_schedule_anywhere(env):
    A, c = env
    _session(A, "booked", days=150)
    _priced(A)
    with A.app.test_request_context():
        bill = A.bill_ctx(1, False, live=True)
        qd = A.quote_data(1, _s(A), A.q("SELECT * FROM lines WHERE session_id=1"), A.totals(1))
    assert "schedule" not in bill and "schedule" not in qd
    assert "Payment schedule" not in c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)

def test_plans_are_capped_at_four(env):
    A, c = env
    _plan(A, n=2)
    assert A.C.INSTALLMENTS_MAX == 4
    for n in ("5", "12"):
        c.post("/session/1/installments", data=dict(n=n, back="/c/1?s=1"), environ_base=E)
        assert _inst(A) == 2
    c.post("/session/1/installments", data=dict(n="4", back="/c/1?s=1"), environ_base=E)
    assert _inst(A) == 4
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert 'name="n" min="1" max="4"' in page and "set before plans were limited" not in page

def test_a_plan_above_four_set_before_the_cap_is_kept_never_rewritten(env):
    A, c = env
    _plan(A, n=6, days=300)
    rows = _ctx(A, A.installment_schedule, 1)
    assert len(rows) == 6
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    t = _text(page)
    assert "This plan has 6 payments, set before plans were limited to 4. It stays as agreed." in t
    c.post("/session/1/installments", data=dict(n="6", back="/c/1?s=1"), environ_base=E)
    assert _inst(A) == 6
    assert _ctx(A, A.installment_schedule, 1) == rows
    c.post("/session/1/installments", data=dict(n="8", back="/c/1?s=1"), environ_base=E)
    assert _inst(A) == 6
    c.post("/session/1/installments", data=dict(n="3", back="/c/1?s=1"), environ_base=E)
    assert _inst(A) == 3

def test_a_type_or_walkthrough_value_above_four_is_kept_only_when_unchanged(env):
    A, _c = env
    import walkthrough as W
    t = {"installments": 6}
    W._put_installments(t, {"installments": "6"})
    assert t["installments"] == 6
    W._put_installments(t, {"installments": "9"})
    assert t["installments"] == 4
    t = {}
    W._put_installments(t, {"installments": "7"})
    assert t["installments"] == 4

def _inst(A):
    return _ctx(A, A.q, "SELECT installments FROM sessions WHERE id=1", one=True)["installments"]
