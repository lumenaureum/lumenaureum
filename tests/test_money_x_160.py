import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, get_zip, household, line, one, pay, post, run, session, tot,
                                  year)
from test_285_155 import TWO, taxes

def rows(A, sql, args=()):
    con = A.connect()
    try:
        return [dict(r_) for r_ in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def ctx(A, fn, *args, **kw):
    with A.app.test_request_context():
        return fn(*args, **kw)

def srow(A, sid=1):
    return A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?",
               (sid,), one=True)

def cancel(c, sid, deposit=None):
    data = dict(stage="lost", need_reason="1", lost_reason="Canceled", back=f"/c/1?s={sid}")
    if deposit:
        data["deposit"] = deposit
    assert c.post(f"/session/{sid}/stage", data=data, environ_base=E).status_code == 302

def use_credit(c, sid, credit=1):
    assert c.post(f"/session/{sid}/credit-use", data=dict(credit=str(credit), back=f"/c/1?s={sid}"),
                  environ_base=E).status_code == 302

def booked(A, c, sid, shoot, price, on=None, stype="family"):
    session(A, sid, 1, "booked", shoot, stype)
    run(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, 'booked', ?)", (sid, on or A.today().isoformat()))
    line(c, sid, "Session", price)

def plan(A, sid=1):

    with A.app.test_request_context():
        s_ = srow(A, sid)
        return A.installment_schedule(sid), A.overdue(s_), A.payment_reminder(s_)

def rescheduled(A, times, day, sid=1):
    for n_ in range(times):
        run(A, "INSERT INTO audit (at, session_id, household_id, action) VALUES (?, ?, 1, ?)",
            (day + " 10:00:00", sid, f"Rescheduled from Mon Jan {n_ + 1} to Tue Jan {n_ + 2} (so far)"))

def parts_add_up(A):

    for t_ in ("payments", "tax_adjustments"):
        for r_ in rows(A, f"SELECT * FROM {t_} WHERE tax_parts IS NOT NULL"):
            assert abs(sum(v_[0] for v_ in json.loads(r_["tax_parts"]).values()) - (r_["tax"] or 0)) < 0.005, (t_, r_)

def export_adds_up(A, c, y):

    f, ym = get_zip(c, y), year(A, y)
    total = f["summary.csv"][-1]
    assert abs(sum(float(r_["tax amount"]) for r_ in f["sales-tax.csv"]) - float(total["taxes"])) < 0.005
    assert abs(ym["tax_due"] - sum(v_ for _n, v_ in ym["tax_names"])) < 0.005
    for n_, v_ in ym["tax_names"]:
        assert abs(float(total[n_]) - v_) < 0.005
    for m_ in f["summary.csv"]:
        assert abs(sum(float(m_[n_]) for n_, _v in ym["tax_names"]) - float(m_["taxes"])) < 0.005, m_
    return f, ym

def _credit_moved(A, c, freeze):

    taxes(A, TWO)
    freeze("2025-06-01")
    household(A, 1, "Lane")
    booked(A, c, 1, "2025-09-12", 1000)
    pay(c, 1, "deposit", 100, "2025-06-01")
    freeze("2025-07-01")
    cancel(c, 1, "credit")
    freeze("2026-03-01")
    booked(A, c, 2, "2026-06-12", 500)
    use_credit(c, 2)

def test_correcting_one_half_of_a_used_credit_corrects_the_other_half_too(env):

    A, c, freeze = env
    _credit_moved(A, c, freeze)
    z26 = get_zip(c, 2026, raw=True)
    freeze("2027-03-01")
    rid, pid = one(A, "SELECT refund_id, payment_id FROM credit_uses")
    post(c, f"/money/payment/{rid}/correct", should_be="0", why="the credit was never used")
    fixes = rows(A, "SELECT session_id, amount, received, tax, corrects FROM payments WHERE corrects IS NOT NULL ORDER BY id")
    assert [(r_["session_id"], r_["amount"], r_["received"], r_["corrects"]) for r_ in fixes] == [
        (1, 100.0, "2027-03-01", rid), (2, -100.0, "2027-03-01", pid)]
    assert abs(fixes[0]["tax"] + fixes[1]["tax"]) < 0.011
    ym = year(A, 2027)
    assert abs(ym["total"]) < 0.005 and abs(ym["tax_due"]) < 0.011
    assert tot(A, 1)["kept"] == 100.0 and tot(A, 2)["paid"] == 0.0
    assert ctx(A, A.credit_of, 1)["left"] == 100.0
    assert get_zip(c, 2026, raw=True) == z26
    parts_add_up(A)

    fix_id = one(A, "SELECT id FROM payments WHERE corrects=?", (pid,))[0]
    post(c, f"/money/payment/{fix_id}/delete", set="void")
    assert [r_["voided"] is not None for r_ in rows(A, "SELECT voided FROM payments WHERE corrects IS NOT NULL ORDER BY id")] == [True, True]
    assert ctx(A, A.credit_of, 1)["left"] == 0.0 and tot(A, 2)["paid"] == 100.0

def test_a_used_credit_is_only_corrected_down(env):
    A, c, freeze = env
    _credit_moved(A, c, freeze)
    freeze("2027-03-01")
    pid = one(A, "SELECT payment_id FROM credit_uses")[0]
    post(c, f"/money/payment/{pid}/correct", should_be="150", why="more")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE corrects IS NOT NULL")[0] == 0
    post(c, f"/money/payment/{pid}/correct", should_be="40", why="only part of it was meant")
    assert sorted(r_["amount"] for r_ in rows(A, "SELECT amount FROM payments WHERE corrects IS NOT NULL")) == [-60.0, 60.0]
    assert ctx(A, A.credit_of, 1)["left"] == 60.0 and tot(A, 2)["paid"] == 40.0 and tot(A, 1)["kept"] == 60.0

def test_a_credit_is_never_worth_more_than_the_deposit_its_session_still_keeps(env):

    A, c, freeze = env
    freeze("2026-01-10")
    household(A, 1, "Lane")
    booked(A, c, 1, "2026-03-12", 1000)
    pay(c, 1, "deposit", 100, "2026-01-10")
    cancel(c, 1, "credit")
    booked(A, c, 2, "2026-06-12", 500)
    use_credit(c, 2)
    freeze("2026-02-01")
    cancel(c, 2, "credit")
    assert ctx(A, A.credit_of, 2)["left"] == 100.0 and ctx(A, A.credit_of, 1)["left"] == 0.0
    pid = one(A, "SELECT payment_id FROM credit_uses")[0]
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert tot(A, 2)["kept"] == 0.0 and tot(A, 1)["kept"] == 100.0
    booked(A, c, 3, "2026-09-12", 800)
    offered = ctx(A, A.credits_for, 3)
    assert [(c_["from_sid"], c_["left"]) for c_ in offered] == [(1, 100.0)]
    assert ctx(A, A.credit_of, 2)["left"] == 0.0
    post(c, f"/money/payment/{pid}/delete", set="restore")
    assert [(c_["from_sid"], c_["left"]) for c_ in ctx(A, A.credits_for, 3)] == [(2, 100.0)]

def test_a_closed_years_export_reads_the_same_after_its_kept_deposit_is_used_as_a_credit(env):

    A, c, freeze = env
    taxes(A, TWO)
    freeze("2025-06-01")
    household(A, 1, "Lane")
    booked(A, c, 1, "2025-09-12", 1000)
    pay(c, 1, "deposit", 100, "2025-06-01")
    freeze("2025-07-01")
    cancel(c, 1, "credit")
    freeze("2026-03-01")
    z25 = get_zip(c, 2025, raw=True)
    booked(A, c, 2, "2026-06-12", 500)
    use_credit(c, 2)
    assert get_zip(c, 2025, raw=True) == z25
    f, _ym = export_adds_up(A, c, 2026)
    assert abs(float(f["summary.csv"][-1]["received"])) < 0.005

def test_a_tax_that_nets_to_nothing_in_a_year_keeps_its_column_in_the_export(env):

    A, c, freeze = env
    taxes(A, TWO)
    freeze("2026-03-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-02-12")
    line(c, 1, "Session", 100)
    pay(c, 1, "balance", 108, "2026-03-01")
    freeze("2026-06-01")
    pay(c, 1, "refund", 108, "2026-06-01")
    f, ym = export_adds_up(A, c, 2026)
    assert ym["tax_names"] == [("State tax", 0.0), ("City tax", 0.0)]
    by_month = {m_["month"]: (m_["taxes"], m_["State tax"], m_["City tax"]) for m_ in f["summary.csv"]}
    assert by_month["2026-03"] == ("8.00", "6.00", "2.00") and by_month["2026-06"] == ("-8.00", "-6.00", "-2.00")
    assert sorted((r_["tax"], r_["tax amount"]) for r_ in f["sales-tax.csv"]) == [("City tax", "0.00"), ("State tax", "0.00")]

def _plan_of_four(A, c, freeze):
    taxes(A, TWO)
    freeze("2026-01-10")
    household(A, 1, "Lane")
    run(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Alex Lane', 'parent', 'alex@example.com')")
    booked(A, c, 1, "2026-06-01", 1000)
    post(c, "/session/1/installments", n="4")
    pay(c, 1, "deposit", 100, "2026-01-10")
    freeze("2026-02-10")
    pay(c, 1, "balance", 245, "2026-02-10")

def test_a_reschedule_fee_never_makes_a_paid_installment_overdue(env):

    A, c, freeze = env
    _plan_of_four(A, c, freeze)
    freeze("2026-02-20")
    sch, ov, rem = plan(A)
    assert [r_["amount"] for r_ in sch] == [245.0] * 4 and ov["amount"] == 0.0
    rescheduled(A, 2, "2026-02-20")
    run(A, "UPDATE sessions SET shoot_date='2026-07-01' WHERE id=1")
    post(c, "/session/1/reschedule-fee", act="add")
    assert tot(A, 1)["quoted"] == 1134.0
    sch, ov, rem = plan(A)
    assert [r_["amount"] for r_ in sch] == [245.0, 245.0, 245.0, 299.0]
    assert [r_["status"] for r_ in sch] == ["paid", "due", "due", "due"]
    assert sch[-1]["due"] == "2026-06-15"
    assert ov == dict(amount=0.0, since=None)
    assert rem["words"] == "Installment (2 of 4) $245.00, due Tue Mar 10" and not rem["late"]
    page = c.get("/money", environ_base=E).get_data(as_text=True)
    assert "Overdue" not in page.split('id="upcoming"', 1)[1].split("</section>", 1)[0]
    freeze("2026-03-11")
    sch, ov, rem = plan(A)
    assert ov == dict(amount=245.0, since="2026-03-10")
    assert rem["words"] == "Installment (2 of 4) $245.00, was due Tue Mar 10"
    assert round(sum(r_["amount"] for r_ in sch), 2) == round(tot(A, 1)["quoted"] - 100, 2)

def test_a_reschedule_fee_is_taxed_in_full_beside_a_discount(env):

    A, c, freeze = env
    taxes(A, TWO)
    freeze("2026-01-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-06-01", "family")
    line(c, 1, "Session", 1000)
    post(c, "/session/1/quote/discount", pct="10", reason="")
    run(A, "UPDATE sessions SET stage='booked' WHERE id=1")
    rescheduled(A, 2, "2026-01-10")
    post(c, "/session/1/reschedule-fee", act="add")
    got = {r_["label"]: r_["amount"] for r_ in rows(A, "SELECT label, amount FROM lines WHERE session_id=1")}
    assert got["Reschedule fee"] == 50.0 and got["Discount (10%)"] == -100.0
    assert (got["State tax (6%)"], got["City tax (2%)"]) == (57.0, 19.0)
    assert tot(A, 1)["quoted"] == 1026.0

    pay(c, 1, "balance", 1026, "2026-01-10")
    assert dict(year(A, 2026)["tax_names"]) == {"State tax": 57.0, "City tax": 19.0}
    parts_add_up(A)

def test_a_fee_in_a_price_that_holds_its_tax_is_not_discounted_for_tax_either(env):
    A, c, freeze = env
    taxes(A, [dict(key="vat", name="VAT", rate=10, included=True, default=True)])
    freeze("2026-01-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-06-01", "family")
    line(c, 1, "Session", 1100)
    post(c, "/session/1/quote/discount", pct="10", reason="")
    run(A, "UPDATE sessions SET stage='booked' WHERE id=1")
    rescheduled(A, 2, "2026-01-10")
    post(c, "/session/1/reschedule-fee", act="add")
    assert tot(A, 1)["quoted"] == 1040.0
    assert ctx(A, A.included_taxes, 1) == [dict(name="VAT", rate=10.0, amount=94.55)]

def test_taking_back_a_waived_balance_leaves_the_closed_year_it_was_written_off_in(env):

    A, c, freeze = env
    freeze("2026-11-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-10-12")
    line(c, 1, "Session", 500)
    pay(c, 1, "deposit", 100, "2026-11-10")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived", note_why="hardship")
    assert year(A, 2026)["written_off"] == 400.0 and tot(A, 1)["due"] == 0.0
    freeze("2027-02-10")
    z26 = get_zip(c, 2026, raw=True)
    post(c, "/close", sid="1", step="balance_paid", outcome="na")
    assert tot(A, 1)["due"] == 400.0 and tot(A, 1)["written_off"] == 0.0
    assert year(A, 2026)["written_off"] == 400.0 and get_zip(c, 2026, raw=True) == z26
    assert year(A, 2027)["written_off"] == -400.0
    assert [(r_["at"], r_["amount"]) for r_ in rows(A, "SELECT at, amount FROM write_offs ORDER BY id")] == [
        ("2026-11-10", 400.0), ("2027-02-10", -400.0)]

    post(c, "/close", sid="1", step="balance_paid", outcome="waived", note_why="again")
    post(c, "/close", sid="1", step="balance_paid", outcome="na")
    assert [(r_["at"], r_["amount"]) for r_ in rows(A, "SELECT at, amount FROM write_offs ORDER BY id")] == [
        ("2026-11-10", 400.0), ("2027-02-10", -400.0)]

def test_changing_a_print_order_leaves_a_closed_years_print_figures_as_they_were(tmp_path, monkeypatch):

    import prints_env as PE
    import test_print_orders as TP
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    for k_, v_ in (("tax_mode", "flat"), ("sales_tax_pct", "6"), ("tax_prints", "1"), ("tax_print_shipping", "0")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k_, v_))
    con.commit()
    con.close()
    oid, _total = TP.invoiced(e)
    this = date.today().year
    last_day = f"{this - 1}-12-30"
    con = e.A.connect()
    con.execute("UPDATE print_orders SET invoice_issued=? WHERE id=?", (f"{this - 1}-12-20", oid))
    con.commit()
    con.close()
    monkeypatch.setattr(e.A, "books_open_from", lambda: f"{this - 1}-01-01")
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": "10.00", "received": last_day}, environ_base=E)
    monkeypatch.setattr(e.A, "books_open_from", lambda: f"{this}-01-01")

    def last_year():
        with e.A.app.test_request_context():
            y_ = e.A.app.config["prints_year"](this - 1)
            return {k_: y_[k_] for k_ in ("received", "sales", "shipping", "tax", "rows")}
    before = last_year()
    assert before["tax"] > 0 and before["rows"][0]["invoice"]
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    assert last_year() == before
    con = e.A.connect()
    con.execute("UPDATE settings SET value='1' WHERE key='tax_print_shipping'")
    con.commit()
    con.close()
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert last_year() == before

    inv = json.loads(PE.q(e, "SELECT invoice_data FROM print_orders WHERE id=?", (oid,))[0]["invoice_data"])
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": f"{(inv['total'] - 1000) / 100:.2f}",
                                                   "received": date.today().isoformat()}, environ_base=E)
    with e.A.app.test_request_context():
        now_ = e.A.app.config["prints_year"](this)
    assert last_year() == before
    assert before["received"] + now_["received"] == inv["total"]
    assert before["tax"] + now_["tax"] == inv["tax_cents"] and before["shipping"] + now_["shipping"] == inv["ship_cents"]

def test_crew_pay_corrected_in_a_closed_year_reads_as_corrected_on_the_session(env):

    A, c, freeze = env
    freeze("2026-12-20")
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_crew', '1')")
    household(A, 1, "Lane")
    booked(A, c, 1, "2026-12-12", 1000)
    run(A, "INSERT INTO crew (id, name, role) VALUES (1, 'Sam Reed', 'Second shooter')")
    run(A, "INSERT INTO session_crew (id, session_id, crew_id, role) VALUES (1, 1, 1, 'Second shooter')")

    def fold():
        page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
        assert 'id="crew"' in page
        return page.split('id="crew"', 1)[1].split("</details>", 1)[0]
    post(c, "/session/1/crew/1/pay", amount="200", date="2026-12-20")
    eid = one(A, "SELECT expense_id FROM session_crew WHERE id=1")[0]
    assert eid and "Paid $200.00 on" in fold()
    freeze("2027-02-10")
    post(c, f"/money/expense/{eid}/correct", should_be="150", why="paid less in the end")
    assert "Paid $150.00 on" in fold() and "Paid $200.00" not in fold()
    post(c, f"/money/expense/{eid}/correct", should_be="0", why="never paid")
    assert "Paid $" not in fold() and "Pay Sam" in fold()
    post(c, "/session/1/crew/1/pay", amount="120", date="2027-02-10")
    assert "Paid $120.00 on" in fold()
    assert one(A, "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE substr(date,1,4)='2026'")[0] == 200.0

def test_moving_the_shoot_date_never_makes_an_installment_overdue_that_day(env):

    A, c, freeze = env
    _plan_of_four(A, c, freeze)
    agreed = ["2026-02-10", "2026-03-10", "2026-04-10", "2026-06-15"]
    assert json.loads(one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0]) == agreed
    freeze("2026-03-09")
    sch, ov, rem = plan(A)
    assert sch[1]["due"] == "2026-03-10" and ov["amount"] == 0.0
    run(A, "UPDATE sessions SET shoot_date='2026-04-20' WHERE id=1")
    sch, ov, rem = plan(A)
    assert ov["amount"] == 0.0 and not rem["late"]
    assert [r_["due"] for r_ in sch] == agreed
    assert rem["words"] == "Installment (2 of 4) $245.00, due Tue Mar 10"
    assert ctx(A, A.plan_dates_moved, 1)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Its dates stay as agreed" in page and "Set the dates again from the new shoot date" in page

    freeze("2026-05-10")
    assert plan(A)[1] == dict(amount=490.0, since="2026-03-10")

    freeze("2026-03-09")
    post(c, "/session/1/installments", n="4", redate="1")
    sch, ov, rem = plan(A)
    assert [r_["due"] for r_ in sch] == ["2026-02-07", "2026-03-08", "2026-04-06", "2026-05-04"]
    assert not ctx(A, A.plan_dates_moved, 1)
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'The payment plan''s dates were set again%'")[0] == 1

def test_a_plan_is_agreed_at_booking_and_follows_the_date_until_then(env):
    A, c, freeze = env
    freeze("2026-01-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-06-01", "family")
    run(A, "INSERT INTO tasks (session_id, stage, title) VALUES (1, 'quoted', 'Client said yes')")
    line(c, 1, "Session", 1000)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    run(A, "UPDATE sessions SET shoot_date='2026-07-01' WHERE id=1")
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "quoted"
    assert one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0] is None
    assert ctx(A, A.installment_schedule, 1)[-1]["due"] == "2026-07-15"
    run(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, 'booked', '2026-01-10')")
    with A.app.test_request_context():
        A.set_stage(1, "booked")
        A.db().commit()
    assert json.loads(one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0]) == ["2026-02-10", "2026-03-10", "2026-07-15"]
    post(c, "/session/1/installments", n="2")
    assert json.loads(one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0]) == ["2026-02-10", "2026-07-15"]
    post(c, "/session/1/installments", n="1")
    assert one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0] is None

def test_a_plan_from_before_the_update_keeps_the_dates_it_shows_that_day(env):
    A, c, freeze = env
    freeze("2026-01-10")
    household(A, 1, "Lane")
    booked(A, c, 1, "2026-06-01", 1000)
    run(A, "UPDATE sessions SET installments=3 WHERE id=1")
    run(A, "DELETE FROM settings WHERE key='plan_dates_seeded'")
    with A.app.test_request_context():
        A._seed_plan_dates()
        A.db().commit()
    assert json.loads(one(A, "SELECT plan_dates FROM sessions WHERE id=1")[0]) == ["2026-02-10", "2026-03-10", "2026-06-15"]
    run(A, "UPDATE sessions SET shoot_date='2026-08-01' WHERE id=1")
    assert [r_["due"] for r_ in ctx(A, A.installment_schedule, 1)] == ["2026-02-10", "2026-03-10", "2026-06-15"]

MIXED = [dict(key="in", name="Held", rate=10, included=True, default=True),
         dict(key="top", name="Top", rate=2.75, included=False, default=True)]

def test_a_tax_in_the_price_and_one_on_top_an_exempt_client_and_a_closed_years_correction(env):

    A, c, freeze = env
    taxes(A, MIXED)
    freeze("2026-06-01")
    household(A, 1, "Lane")
    household(A, 2, "Exempt")
    run(A, "UPDATE households SET tax_exempt=1, tax_cert='X1' WHERE id=2")
    session(A, 1, 1, "shot", "2026-05-12")
    session(A, 2, 2, "shot", "2026-05-12")
    line(c, 1, "Session", 333.33)
    line(c, 2, "Session", 333.33)
    assert (round(tot(A, 1)["quoted"], 2), tot(A, 2)["quoted"]) == (341.66, 333.33)
    pay(c, 1, "deposit", 100, "2026-06-01")
    pay(c, 2, "deposit", 100, "2026-06-01")
    freeze("2026-12-20")
    pay(c, 1, "balance", 241.66, "2026-12-20")
    pay(c, 1, "tip", 20, "2026-12-20")
    assert dict(year(A, 2026)["tax_names"]) == {"Held": 30.3, "Top": 8.33}
    export_adds_up(A, c, 2026)
    z26 = get_zip(c, 2026, raw=True)
    freeze("2027-02-10")
    for pid in (1, 2):
        post(c, f"/money/payment/{pid}/correct", should_be="40", why="half bounced")
    fix = {r_["session_id"]: r_ for r_ in rows(A, "SELECT * FROM payments WHERE corrects IS NOT NULL")}
    assert json.loads(fix[1]["tax_parts"]) == {"Top": [-1.46, -53.22], "Held": [-5.32, -53.22]} and fix[1]["tax"] == -6.78
    assert fix[2]["tax"] == 0 and fix[2]["tax_parts"] is None
    pay(c, 1, "refund", 50, "2027-02-10")
    post(c, "/session/1/money", what="payment", kind="tip_back", amount="5", received="2027-02-10", method="Cash")
    assert get_zip(c, 2026, raw=True) == z26
    f, ym = export_adds_up(A, c, 2027)
    assert dict(ym["tax_names"]) == {"Held": -9.76, "Top": -2.68} and ym["tips"] == -5.0
    assert [p_["correction of"] for p_ in f["payments.csv"]][:2] == ["Correction of the June 1, 2026 deposit: half bounced"] * 2
    parts_add_up(A)

def test_each_tax_by_name_comes_to_its_line_however_the_price_is_paid(env):

    import random
    A, c, freeze = env
    rnd = random.Random(7)
    freeze("2026-03-01")
    household(A, 1, "Lane")
    for sid in range(1, 13):
        taxes(A, [dict(key=f"k{i_}", name=f"Tax{i_}", rate=rnd.choice([6, 2, 1.5, 2.75, 6.25, 8.875, 10, 0.5, 7]),
                       included=rnd.random() < 0.35, default=True) for i_ in range(rnd.choice([2, 2, 3]))])
        session(A, sid, 1, "shot", "2026-02-12")
        line(c, sid, "Session", round(rnd.uniform(50, 3000), 2))
        if rnd.random() < 0.4:
            line(c, sid, "Album", round(rnd.uniform(10, 500), 2), taxable=rnd.random() < 0.5)
        if rnd.random() < 0.4:
            post(c, f"/session/{sid}/quote/discount", pct=str(rnd.choice([10, 15, 33])), reason="")
        left = round(tot(A, sid)["quoted"], 2)
        n_ = rnd.choice([1, 2, 3, 5])
        for j_ in range(n_):
            amt = left if j_ == n_ - 1 else round(rnd.uniform(0.01, left), 2)
            pay(c, sid, "balance", amt, "2026-03-01")
            left = round(left - amt, 2)
        assert abs(tot(A, sid)["due"]) < 0.005
        with A.app.test_request_context():
            want = {f_["name"]: f_["amount"] for f_ in A.tax_figures(sid) if f_["amount"]}
            have = {}
            for e_ in A.tax_entries(sid):
                for name_, v_ in e_["parts"].items():
                    have[name_] = round(have.get(name_, 0) + v_[0], 2)
        assert {k_: v_ for k_, v_ in have.items() if v_} == want, sid
        if sid % 3 == 0:
            pay(c, sid, "refund", round(rnd.uniform(1, 40), 2), "2026-03-01")
    parts_add_up(A)
    export_adds_up(A, c, 2026)

def test_the_plan_sentence_says_where_a_reschedule_fee_goes(env):

    A, c, freeze = env
    _plan_of_four(A, c, freeze)
    want = ("paid in 4 equal payments on the dates in the payment schedule that comes with this {doc}; a reschedule fee, "
            "if any, is added to the last, due on delivery; your photos are released on payment")
    assert ctx(A, A.balance_rule, "family", 1) == want.format(doc="agreement")
    assert ctx(A, A.balance_rule, "family", 1, "quote") == want.format(doc="quote")
    assert A.C.balance_text("family", 0) == "due on delivery; your photos are released on payment"
    for url in ("/session/1/agreement",):
        page = c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True)
        assert "a reschedule fee, if any, is added to the last" in page, url

def _seen(page):

    import html
    import re
    page = page.split('id="history"', 1)[0]
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<template.*?</template>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page.replace("<option", " <option"))))

def test_the_studios_word_for_the_deposit_is_used_on_steps_status_and_payment_rows(env):

    import re
    A, c, freeze = env
    freeze("2026-01-10")
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('deposit_word', 'retainer')")
    household(A, 1, "Lane")
    run(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Alex Lane', 'parent', 'alex@example.com')")
    booked(A, c, 1, "2026-06-01", 1000)
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
        assert A.booked_hint(1) == "retainer and agreement still open"

    def read(url):
        return _seen(c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True))
    client = read("/c/1?s=1")
    assert "retainer and agreement still open" in client and "Send the retainer invoice" in client
    assert "Send the retainer invoice" in read("/")
    edit = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert 'value="deposit"' in edit and "retainer" in _seen(edit)
    for url in ("/c/1?s=1", "/", "/money", "/session/1/edit"):
        assert not re.search(r"deposit", read(url), re.I), url
    pay(c, 1, "deposit", 100, "2026-01-10")
    assert one(A, "SELECT kind FROM payments")[0] == "deposit"
    client = read("/c/1?s=1")
    assert "Retainer received" in client and "retainer, cash" in read("/session/1/edit")
    assert "retainer, cash" in read("/session/1/invoice")
    for url in ("/c/1?s=1", "/", "/money", "/session/1/edit", "/session/1/invoice"):
        assert not re.search(r"deposit", read(url), re.I), url

    cancel(c, 1)
    assert "Canceled, deposit kept" in read("/client/1/statement?month=2026-01")
    assert get_zip(c, 2026)["payments.csv"][0]["kind"] == "deposit"
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE '%deposit%'")[0] >= 1

def test_reopening_a_lost_booking_leaves_a_closed_years_export_as_it_was(env):

    A, c, freeze = env
    taxes(A, TWO)
    freeze("2025-06-01")
    household(A, 1, "Lane")
    booked(A, c, 1, "2025-09-12", 1000)
    pay(c, 1, "deposit", 100, "2025-06-01")
    freeze("2025-07-01")
    cancel(c, 1)
    run(A, "UPDATE stage_log SET at='2025-07-01 12:00:00' WHERE session_id=1 AND stage='lost'")
    freeze("2026-03-01")
    z25 = get_zip(c, 2025, raw=True)
    assert b"kept deposit" in z25["sales-tax.csv"]
    post(c, "/session/1/stage", stage="booked", back="/c/1?s=1")
    run(A, "UPDATE stage_log SET at='2026-03-01 12:00:00' WHERE session_id=1 AND stage='booked' AND at > '2026-03-01'")
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"
    assert get_zip(c, 2025, raw=True) == z25
    pay(c, 1, "balance", 50, "2026-03-01")
    assert all(r_["counted as"] != "kept deposit" for r_ in get_zip(c, 2026)["sales-tax.csv"])

def test_a_print_payment_with_no_date_typed_is_dated_today_in_the_studios_time_zone(tmp_path, monkeypatch):

    import prints_env as PE
    import test_print_orders as TP
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    e = PE.make(tmp_path, monkeypatch)
    oid, _total = TP.invoiced(e)
    utc_day = datetime.now(timezone.utc).date()
    zone = next(z_ for z_ in ("Pacific/Kiritimati", "Etc/GMT+12") if datetime.now(ZoneInfo(z_)).date() != utc_day)
    con = e.A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', ?)", (zone,))
    con.commit()
    con.close()
    e.A.apply_settings()
    studio_day = datetime.now(ZoneInfo(zone)).date().isoformat()
    assert e.A.today().isoformat() == studio_day != utc_day.isoformat()
    monkeypatch.setattr(e.A, "books_open_from", lambda: "2000-01-01")
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": "1.00"}, environ_base=E)
    assert [r_["received"] for r_ in PE.q(e, "SELECT received FROM print_payments")] == [studio_day]

def test_a_closed_years_deposit_is_not_corrected_below_what_already_moved_on_as_a_credit(env):

    A, c, freeze = env
    _credit_moved(A, c, freeze)
    freeze("2027-03-01")
    dep = one(A, "SELECT id FROM payments WHERE session_id=1 AND kind='deposit'")[0]
    r = post(c, f"/money/payment/{dep}/correct", should_be="0", why="the check was never cashed")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE corrects IS NOT NULL")[0] == 0
    assert tot(A, 1)["paid"] == 0.0 and tot(A, 2)["paid"] == 100.0
    page = c.get(r.headers["Location"], environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "moved on as a credit" in page

    rid = one(A, "SELECT refund_id FROM credit_uses")[0]
    post(c, f"/money/payment/{rid}/correct", should_be="0", why="the deposit was never real")
    post(c, f"/money/payment/{dep}/correct", should_be="0", why="the check was never cashed")
    assert tot(A, 1)["paid"] == 0.0 and tot(A, 2)["paid"] == 0.0 and tot(A, 1)["kept"] == 0.0
    assert ctx(A, A.credit_of, 1)["left"] == 0.0 and abs(year(A, 2027)["total"] + 100.0) < 0.005
