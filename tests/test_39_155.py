import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, _page, _session, env

def q(A, sql, args=()):
    con = A.connect()
    try:
        return [tuple(r_) for r_ in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _iso(A, days):
    return (A.today() + timedelta(days=days)).isoformat()

def _priced(A, sid=1, amount=1000.0, booked_days_ago=0):

    run(A, "INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?, 'Session', ?, 'custom', 1)", (sid, amount))
    run(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, 'booked', ?)", (sid, _iso(A, -booked_days_ago)))
    with A.app.test_request_context():
        A.ensure_quote(sid)
        A.db().commit()

def _pay(c, amount, kind="deposit", sid=1):
    r = c.post(f"/session/{sid}/money", data=dict(what="payment", kind=kind, amount=str(amount), method="check",
                                                  received="", back=f"/c/1?s={sid}"), environ_base=E)
    assert r.status_code == 302

def _ctx(A, fn, *args, **kw):
    with A.app.test_request_context():
        out = fn(*args, **kw)
        A.db().commit()
        return out

def _history(A, sid=1):
    return [r[0] for r in q(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

def test_installments_split_the_balance_evenly_and_none_is_late_before_its_day(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    assert c.post("/session/1/installments", data=dict(n="3", back="/c/1?s=1"), environ_base=E).status_code == 302
    rows = _ctx(A, A.installment_schedule, 1)
    end = _ctx(A, A.balance_due_date, _iso(A, 30), "family", sid=1)
    assert [r["amount"] for r in rows] == [300.0, 300.0, 300.0]
    assert rows[-1]["due"] == end
    assert rows[0]["due"] > A.today().isoformat() and all(r["status"] == "due" for r in rows)
    page = _page(c)
    assert "Next installment (1 of 3)" in page and page.count("<tr class=\"ui-money-row \">") >= 3 and "late" not in page.split('id="installments"')[1][:2000]
    assert "The balance is now paid in 3 installments" in _history(A)

def test_paid_money_covers_installments_in_order_and_the_reminder_names_the_next(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A, amount=1000.01)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    _pay(c, 100)
    _pay(c, 300, kind="balance")
    rows = _ctx(A, A.installment_schedule, 1)
    assert [r["amount"] for r in rows] == [300.0, 300.0, 300.01]
    assert rows[0]["status"] == "paid" and rows[1]["status"] == "due"
    with A.app.test_request_context():
        s_ = A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=1", one=True)
        rem = A.payment_reminder(s_)
    assert rem["amount"] == 300.0 and rem["due"] == rows[1]["due"] and not rem["late"]
    assert rem["words"].startswith("Installment (2 of 3) $300.00, due")

def test_an_installment_is_late_only_once_its_own_day_has_passed(env):
    A, c = env
    _session(A, "shot", days=-20)
    _priced(A, booked_days_ago=60)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    _pay(c, 100)
    _pay(c, 300, kind="balance")
    rows = _ctx(A, A.installment_schedule, 1)
    assert [r["status"] for r in rows][0] == "paid"
    assert all(r["status"] == "late" for r in rows[1:] if r["due"] < A.today().isoformat())
    assert all(r["status"] == "due" for r in rows[1:] if r["due"] >= A.today().isoformat())

def test_one_payment_is_the_default_and_the_terms_say_installments_only_when_set(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    assert _ctx(A, A.installment_schedule, 1) == []
    assert ">Pay in installments</a>" in _page(c)
    assert _ctx(A, A.balance_rule, "family", 1) == A.C.balance_text("family", 0)
    run(A, "UPDATE sessions SET installments=4 WHERE id=1")
    assert _ctx(A, A.balance_rule, "family", 1).startswith("paid in 4 equal payments on the dates in the payment schedule that comes with this agreement; a reschedule fee, if any, is added to the last, due")
    assert c.post("/session/1/installments", data=dict(n="40", back="/c/1?s=1"), environ_base=E).status_code == 302
    assert q(A, "SELECT installments FROM sessions WHERE id=1")[0][0] == 4

def test_a_session_type_sets_installments_for_its_sessions(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    types = [dict(t_) for t_ in A.C.TYPES]
    for t_ in types:
        if t_["key"] == "family":
            t_["installments"] = 2
    with A.app.test_request_context():
        A._save_rules(TYPES=types)
        A.db().commit()
    assert A.C.INSTALLMENTS["family"] == 2
    assert len(_ctx(A, A.installment_schedule, 1)) == 2
    run(A, "UPDATE sessions SET installments=1 WHERE id=1")
    assert _ctx(A, A.installment_schedule, 1) == []

def _rescheduled(A, times, sid=1):
    for n_ in range(times):
        run(A, "INSERT INTO audit (at, session_id, household_id, action) VALUES (?, ?, 1, ?)",
            (A.today().isoformat() + " 10:00:00", sid, f"Rescheduled from Mon Jan {n_ + 1} to Tue Jan {n_ + 2} (so far)"))

def test_the_free_reschedule_asks_nothing_and_the_next_asks_add_or_waive(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _rescheduled(A, 1)
    assert _ctx(A, A.resched_fees_open, 1) == 0 and 'id="resched-fee"' not in _page(c)
    _rescheduled(A, 1)
    page = _page(c)
    assert _ctx(A, A.resched_fees_open, 1) == 1 and 'id="resched-fee"' in page and ">Add the $50.00 fee</button>" in page
    assert c.post("/session/1/reschedule-fee", data=dict(act="add", back="/c/1?s=1"), environ_base=E).status_code == 302
    fee = q(A, "SELECT amount, fee_key FROM lines WHERE session_id=1 AND fee_key='reschedule'")
    assert fee == [(50.0, "reschedule")]
    assert _ctx(A, A.totals, 1)["quoted"] == 1050.0 and _ctx(A, A.resched_fees_open, 1) == 0
    assert any(h_.startswith("Added the reschedule fee, $50.00") for h_ in _history(A))
    assert c.post("/session/1/reschedule-fee", data=dict(act="add", back="/c/1?s=1"), environ_base=E).status_code == 302
    assert len(q(A, "SELECT 1 FROM lines WHERE fee_key='reschedule'")) == 1

def test_waiving_adds_nothing_and_a_discount_never_reaches_the_fee(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    run(A, "UPDATE quotes SET discount_pct=10 WHERE session_id=1")
    _rescheduled(A, 3)
    assert _ctx(A, A.resched_fees_open, 1) == 2
    c.post("/session/1/reschedule-fee", data=dict(act="waive", back="/c/1?s=1"), environ_base=E)
    assert not q(A, "SELECT 1 FROM lines WHERE fee_key='reschedule'") and _ctx(A, A.resched_fees_open, 1) == 1
    assert any(h_.startswith("Waived the reschedule fee") for h_ in _history(A))
    c.post("/session/1/reschedule-fee", data=dict(act="add", back="/c/1?s=1"), environ_base=E)
    disc = q(A, "SELECT amount FROM lines WHERE session_id=1 AND kind='discount'")[0][0]
    assert disc == -100.0

def test_a_type_with_no_free_reschedule_or_a_fee_of_nothing_asks_nothing(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _rescheduled(A, 3)
    A.C.RESCHEDULE_FEE = 0
    try:
        assert _ctx(A, A.resched_fees_open, 1) == 0
    finally:
        A.C.RESCHEDULE_FEE = 50

def _cancel(c, deposit=None, sid=1):
    data = dict(stage="lost", need_reason="1", lost_reason="Canceled", back=f"/c/1?s={sid}")
    if deposit:
        data["deposit"] = deposit
    return c.post(f"/session/{sid}/stage", data=data, environ_base=E)

def _second(A, amount=500.0):
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (2, 1, 'family', 'booked', ?)", (_iso(A, 60),))
    _priced(A, sid=2, amount=amount)

def test_cancel_asks_about_the_deposit_and_keep_is_the_terms_answer(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _pay(c, 100)
    page = _page(c)
    assert "The deposit, $100.00" in page and "Credit toward a future session" in page
    _cancel(c)
    assert not q(A, "SELECT 1 FROM deposit_credits") and _ctx(A, A.totals, 1)["kept"] == 100.0
    assert "You keep the deposit, $100.00" in _page(c)

def test_a_deposit_held_as_credit_is_used_on_the_next_session_as_money_moved_today(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _pay(c, 100)
    _cancel(c, "credit")
    assert q(A, "SELECT household_id, from_session, amount FROM deposit_credits") == [(1, 1, 100.0)]
    assert _ctx(A, A.totals, 1)["kept"] == 100.0
    assert "is held as a credit toward a future session" in _page(c)
    _second(A)
    page = c.get("/c/1?s=2", environ_base=E).get_data(as_text=True)
    assert 'id="credit-1"' in page and ">Use $100.00 of the credit</button>" in page
    assert c.post("/session/2/credit-use", data=dict(credit="1", back="/c/1?s=2"), environ_base=E).status_code == 302
    t1, t2 = _ctx(A, A.totals, 1), _ctx(A, A.totals, 2)
    assert t1["paid"] == 0.0 and t1["kept"] == 0.0 and t1["refund_due"] == 0.0
    assert t2["paid"] == 100.0 and t2["deposit_short"] == 0.0
    moved = q(A, "SELECT session_id, kind, amount, received, method FROM payments WHERE method LIKE 'credit%' ORDER BY id")
    today = A.today().isoformat()
    assert moved == [(1, "refund", -100.0, today, A.CREDIT_OUT), (2, "deposit", 100.0, today, A.CREDIT_IN)]
    assert _ctx(A, A.credit_of, 1)["left"] == 0.0 and _ctx(A, A.credits_for, 2) == []
    assert round(sum(r[0] for r in q(A, "SELECT amount FROM charges WHERE session_id=1")), 2) == 0.0

    c.post("/session/1/credit-choice", data=dict(deposit="keep", back="/c/1?s=1"), environ_base=E)
    assert q(A, "SELECT 1 FROM deposit_credits")

def test_a_credit_not_used_yet_can_go_back_to_kept_and_reopening_takes_it_back(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _pay(c, 100)
    _cancel(c, "credit")
    c.post("/session/1/credit-choice", data=dict(deposit="keep", back="/c/1?s=1"), environ_base=E)
    assert not q(A, "SELECT 1 FROM deposit_credits") and "You keep the deposit, $100.00" in _page(c)
    c.post("/session/1/credit-choice", data=dict(deposit="credit", back="/c/1?s=1"), environ_base=E)
    assert q(A, "SELECT amount FROM deposit_credits") == [(100.0,)]
    c.post("/session/1/stage", data=dict(stage="booked", back="/c/1?s=1"), environ_base=E)
    assert not q(A, "SELECT 1 FROM deposit_credits")
    assert any("credit of $100.00 is withdrawn" in h_ for h_ in _history(A))

def test_no_credit_is_offered_where_nothing_is_owed_or_on_the_canceled_session_itself(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _pay(c, 100)
    _cancel(c, "credit")
    assert ">Use $100.00 of the credit</button>" not in _page(c)
    assert c.post("/session/1/credit-use", data=dict(credit="1", back="/c/1?s=1"), environ_base=E).status_code == 302
    assert not q(A, "SELECT 1 FROM credit_uses")

def _taxes(A, items):
    import json
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('taxes', ?)", (json.dumps(items),))
    A.apply_settings()

TWO = [dict(key="st", name="State tax", rate=6, included=False, default=True),
       dict(key="ci", name="City tax", rate=2, included=False, default=True)]

def test_the_reschedule_fee_follows_the_fee_schedules_own_item_and_each_named_tax(env):
    A, c = env
    _taxes(A, TWO)
    _session(A, "booked", days=30)
    _priced(A)
    _ctx(A, A.lock_tax_rate, 1)
    _ctx(A, A.reapply_discount, 1)
    with A.app.test_request_context():
        sch = A.current_schedule()
        items = [dict(i_) for i_ in sch["items"]] + [dict(key="reschedule", label="Reschedule", amount=50, unit="flat",
                                                          untaxed=["ci"])]
        A.x("UPDATE fee_schedules SET items=? WHERE id=?", (__import__("json").dumps(items), sch["id"]))
        A.db().commit()
    _rescheduled(A, 2)
    c.post("/session/1/reschedule-fee", data=dict(act="add", back="/c/1?s=1"), environ_base=E)
    assert q(A, "SELECT taxable, untaxed FROM lines WHERE fee_key='reschedule'") == [(1, "ci")]
    taxes = dict(q(A, "SELECT label, amount FROM lines WHERE session_id=1 AND kind='tax'"))
    assert sorted(taxes.values()) == [20.0, 63.0]

def test_a_credit_used_carries_each_named_tax_on_both_sides(env):
    import json
    A, c = env
    _taxes(A, TWO)
    _session(A, "booked", days=30)
    _priced(A)
    _ctx(A, A.lock_tax_rate, 1)
    _ctx(A, A.reapply_discount, 1)
    _pay(c, 108)
    _cancel(c, "credit")
    _second(A)
    _ctx(A, A.lock_tax_rate, 2)
    _ctx(A, A.reapply_discount, 2)
    c.post("/session/2/credit-use", data=dict(credit="1", back="/c/1?s=2"), environ_base=E)
    t1 = _ctx(A, A.totals, 1)
    assert t1["kept"] == 0.0 and t1["refund_due"] == 8.0
    out, inn = q(A, "SELECT tax, tax_parts FROM payments WHERE method LIKE 'credit%' ORDER BY id")
    assert out[0] < 0 < inn[0] and abs(out[0] + inn[0]) < 0.011
    assert set(json.loads(out[1])) == set(json.loads(inn[1])) == {"State tax", "City tax"}

def test_finances_overdue_names_only_late_installments_before_and_after_the_shoot(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A, booked_days_ago=60)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    _pay(c, 100)
    with A.app.test_request_context():
        s_ = A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=1", one=True)
        rows, rem = A.installment_schedule(1), A.payment_reminder(s_)
    late = [r for r in rows if r["status"] == "late"]
    assert late and len(late) < 3
    assert rem["late"] and rem["amount"] == round(sum(r["left"] for r in late), 2)
    page = c.get("/money", environ_base=E).get_data(as_text=True)
    with A.app.test_request_context():
        assert "Overdue " + A.money(rem["amount"]) in page and "Overdue " + A.money(900) not in page
    run(A, "UPDATE quotes SET discount_pct=0 WHERE session_id=1")
    run(A, "UPDATE sessions SET installments=3, shoot_date=? WHERE id=1", (_iso(A, -1),))
    with A.app.test_request_context():
        s_ = A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=1", one=True)
        rows, rem = A.installment_schedule(1), A.payment_reminder(s_)

    assert A.balance_due_date(_iso(A, -1), "family", sid=1) >= A.today().isoformat()
    late = [r for r in rows if r["status"] == "late"]
    assert rem["amount"] == round(sum(r["left"] for r in late), 2) < 900.0

def test_no_installment_not_yet_due_reads_overdue_in_finances(env):
    A, c = env
    _session(A, "booked", days=60)
    _priced(A)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    _pay(c, 100)
    page = c.get("/money", environ_base=E).get_data(as_text=True)
    assert 'id="upcoming"' in page and "Overdue" not in page.split('id="upcoming"', 1)[1].split("</section>", 1)[0]

def test_voiding_either_half_of_a_used_credit_voids_both(env):
    A, c = env
    _session(A, "booked", days=30)
    _priced(A)
    _pay(c, 100)
    _cancel(c, "credit")
    _second(A)
    c.post("/session/2/credit-use", data=dict(credit="1", back="/c/1?s=2"), environ_base=E)
    pid = q(A, "SELECT payment_id FROM credit_uses")[0][0]
    c.post(f"/money/payment/{pid}/delete", data=dict(set="void", back="/c/1?s=2"), environ_base=E)
    assert [r[0] is not None for r in q(A, "SELECT voided FROM payments WHERE method LIKE 'credit%' ORDER BY id")] == [True, True]
    assert _ctx(A, A.totals, 1)["kept"] == 100.0 and _ctx(A, A.credit_of, 1)["left"] == 100.0
    c.post(f"/money/payment/{pid}/delete", data=dict(set="restore", back="/c/1?s=2"), environ_base=E)
    assert [r[0] for r in q(A, "SELECT voided FROM payments WHERE method LIKE 'credit%'")] == [None, None]

def test_a_deposit_kept_in_a_closed_year_used_as_credit_now_leaves_that_year_as_it_was(env):
    A, c = env
    last = f"{A.today().year - 1}-06-01"
    _session(A, "booked", days=30)
    _priced(A)
    run(A, "INSERT INTO payments (session_id, kind, amount, received, method, tax, taxable) VALUES (1, 'deposit', 100, ?, 'check', 0, 0)", (last,))
    _cancel(c, "credit")
    run(A, "UPDATE sessions SET stage_changed=? WHERE id=1", (last,))
    with A.app.test_request_context():
        assert A.closed_year_refusal(last)
    before = q(A, "SELECT id, amount, received, voided FROM payments WHERE substr(received,1,4)=?", (last[:4],))
    _second(A)
    assert c.post("/session/2/credit-use", data=dict(credit="1", back="/c/1?s=2"), environ_base=E).status_code == 302
    assert q(A, "SELECT id, amount, received, voided FROM payments WHERE substr(received,1,4)=?", (last[:4],)) == before
    today = A.today().isoformat()
    assert [r[0] for r in q(A, "SELECT received FROM payments WHERE method LIKE 'credit%'")] == [today, today]
    assert _ctx(A, A.totals, 2)["paid"] == 100.0

    did = q(A, "SELECT id FROM payments WHERE received=?", (last,))[0][0]
    c.post(f"/money/payment/{did}/delete", data=dict(set="void", back="/c/1?s=1"), environ_base=E)
    assert q(A, "SELECT voided FROM payments WHERE id=?", (did,))[0][0] is None
