import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, flat, household, line, one, pay, post, reconcile, run,
                                  session, stmt, tax_of, tot, year)

def booked(A, c, sid, hid, amount=500, shoot="2026-08-01", deposit=100):
    session(A, sid, hid, "quoted", shoot)
    line(c, sid, "Session", amount)
    total = round(amount * 1.08, 2)
    run(A, "INSERT INTO quote_versions (session_id, at, data) VALUES (?, '2025-01-01', ?)",
        (sid, f'{{"deposit": {deposit}, "total": {total}}}'))
    post(c, f"/session/{sid}/stage", stage="booked")

def months(upto):
    return [f"{y}-{m:02d}" for y in (2025, 2026, 2027) for m in range(1, 13) if f"{y}-{m:02d}" < upto]

def test_three_year_book_closed_years_and_past_statements_hold(env):

    A, c, freeze = env
    flat(A, "8", "0.67")
    years, months_seen = {}, {}

    def check():
        today = A.today()
        for y in (2025, 2026, 2027):
            got = reconcile(A, c, y) + (year(A, y)["refunds"],)
            if y < today.year:
                assert years.setdefault(y, got) == got, (y, years[y], got)
        for hid in [r[0] for r in A.connect().execute("SELECT id FROM households").fetchall()]:
            prev = None
            for m in months(today.isoformat()[:7]) + [today.isoformat()[:7]]:
                v = stmt(A, hid, m)
                if prev is not None:
                    assert abs(prev - v["opening"]) < 0.005, (hid, m)
                prev = v["closing"]
                if m < today.isoformat()[:7]:
                    key = (v["opening"], v["closing"], [(r["number"], r["amount"]) for r in v["invoices"]],
                           [(r["kind"], r["amount"]) for r in v["payments"]])
                    assert months_seen.setdefault((hid, m), key) == key, (hid, m, months_seen[(hid, m)], key)

    freeze("2025-01-10")
    for hid, name in enumerate(("Adams", "Baker", "Cruz", "Diaz", "Evans", "Fox"), 1):
        household(A, hid, name, "Baker Events LLC" if name == "Baker" else "")
        booked(A, c, hid, hid, 400 + 100 * hid, f"2025-0{hid + 2}-15")
    for sid in range(1, 7):
        pay(c, sid, "deposit", 100, "2025-01-10")
    check()
    freeze("2025-03-20")
    post(c, "/session/1/stage", stage="shot")
    pay(c, 1, "balance", 440, "2025-03-20")
    pay(c, 1, "tip", 30, "2025-03-20")
    post(c, "/session/1/money", what="expense", label="Drive", miles="40", amount="0", date="2025-03-20")
    check()
    freeze("2025-04-01")
    post(c, "/session/2/invoice/issue")
    pay(c, 2, "balance", 600, "2025-04-01")
    check()
    freeze("2025-06-01")
    post(c, "/session/2/stage", stage="lost", lost_reason="Canceled")
    pay(c, 2, "refund", 300, "2025-06-02")
    post(c, "/session/3/stage", stage="shot")
    pay(c, 3, "balance", 656, "2025-06-01")
    post(c, "/session/5/stage", stage="shot")
    pay(c, 5, "balance", 500, "2025-06-01")
    post(c, "/close", sid="5", step="balance_paid", outcome="waived")
    check()
    freeze("2025-12-30")
    post(c, "/session/3/quote/discount", pct="10", reason="loyal")
    check()
    freeze("2026-01-10")
    post(c, "/settings", mileage_rate="0.70")
    pay(c, 2, "refund", float(tot(A, 2)["refund_due"]), "2026-01-10")
    check()
    freeze("2026-02-01")
    post(c, "/session/3/quote/exempt", tax_exempt="1")
    pay(c, 5, "balance", 48, "2026-02-01")
    post(c, "/session/6/stage", stage="lost", lost_reason="No-show")
    check()
    freeze("2026-03-01")
    post(c, "/session/6/stage", stage="lost", lost_reason="Price")
    pay(c, 3, "refund", 50, "2026-03-01")
    check()
    freeze("2026-05-01")
    post(c, "/session/2/stage", stage="booked")
    post(c, "/session/4/money", what="line", label="Extra hour", amount="150", taxable="1", taxable_shown="1")
    check()
    with A.app.test_request_context():
        A._delete_client_rows(1, None, [1])
        A.db().commit()
    check()
    freeze("2027-01-15")
    post(c, "/session/2/stage", stage="lost", lost_reason="Canceled")
    post(c, "/session/6/stage", stage="booked")
    post(c, "/session/3/quote/exempt")
    post(c, "/session/3/quote/discount", pct="0", reason="")
    run(A, "UPDATE settings SET value='0' WHERE key='sales_tax_pct'")
    A.apply_settings()
    check()

def test_refunding_a_tip_leaves_the_price_its_tax_and_delivery_alone(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 540, "2026-05-02")
    pay(c, 1, "tip", 60, "2026-05-02")
    pay(c, 1, "tip_back", 60, "2026-05-20")
    assert tax_of(A, 2026) == 40.0
    with A.app.test_request_context():
        assert A.release_blocked(1) == 0
    assert year(A, 2026)["tips"] == 0

def test_refunding_the_tip_of_a_canceled_booking_keeps_the_deposit(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-03-01")
    household(A, 1, "Lane")
    booked(A, c, 1, 1, 500, "2026-08-01", deposit=100)
    pay(c, 1, "balance", 540, "2026-03-01")
    pay(c, 1, "tip", 20, "2026-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    pay(c, 1, "refund", 440, "2026-03-02")
    pay(c, 1, "tip_back", 20, "2026-03-02")
    assert tot(A, 1)["kept"] == 100
    post(c, "/session/1/stage", stage="booked")
    assert tot(A, 1)["due"] == 440

def test_back_then_the_same_amount_on_another_day_is_a_new_payment(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received="2026-05-02", method="cash", once="t")
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received="2026-05-09", method="cash", once="t")
    post(c, "/session/1/money", what="expense", label="Mileage", amount="0", miles="12", date="2026-05-02", once="e")
    post(c, "/session/1/money", what="expense", label="Mileage", amount="0", miles="30", date="2026-05-09", once="e")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 2
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 2

def test_a_new_quote_line_with_quantity_below_half_is_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    for bad in ("0", "-2", "0.2"):
        post(c, "/session/1/quote/fee", fee_key="custom", label="Hour", amount="100", qty=bad, taxable="0",
             taxable_shown="1")
    assert one(A, "SELECT COUNT(*) FROM lines WHERE label LIKE 'Hour%'")[0] == 0

def _a_1_2_book(A):

    run(A, """INSERT INTO households (id, name) VALUES (1, 'H1'), (2, 'H2'), (3, 'H3');
              INSERT INTO sessions (id, household_id, type, stage, lost_reason, stage_changed, shoot_date, tax_rate) VALUES
                (1, 1, 'portrait', 'lost', 'No-show', '2026-03-01 12:00:00', '2026-06-15', 8),
                (2, 2, 'portrait', 'booked', '', '2025-01-05 12:00:00', '2026-06-15', 8),
                (3, 3, 'portrait', 'lost', 'Canceled', '2025-06-01 12:00:00', '2026-06-15', 8);
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES
                (1, 'S', 500, 'custom', 1), (1, 'Sales tax (8%)', 40, 'tax', 0),
                (2, 'S', 500, 'custom', 1), (2, 'Sales tax (8%)', 40, 'tax', 0),
                (3, 'S', 500, 'custom', 1), (3, 'Sales tax (8%)', 40, 'tax', 0);
              INSERT INTO payments (session_id, kind, amount, received, method) VALUES
                (1, 'deposit', 150, '2025-02-01', 'cash'), (2, 'balance', 600, '2025-02-01', 'cash'),
                (3, 'balance', 540, '2025-02-01', 'cash'), (2, 'refund', -60, '2026-03-01', 'cash')""")

def test_upgrade_keeps_every_year_s_sales_tax_as_1_2_showed_it(env):
    A, c, freeze = env
    flat(A, "8")
    _a_1_2_book(A)
    with A.app.test_request_context():
        A.db()
        A._fill_all_payment_taxes()
        A.g.db.commit()
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (87.64, 7.56)

def test_money_kept_after_a_booking_is_lost_for_another_reason_is_not_also_a_credit(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    booked(A, c, 1, 1)
    pay(c, 1, "deposit", 100, "2026-02-01")
    freeze("2026-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Chose someone else")
    t = tot(A, 1)
    taxed_as_kept = tax_of(A, 2026) > 0
    assert taxed_as_kept and t["refund_due"] == 0
    assert stmt(A, 1, "2026-03")["closing"] == 0

def test_changing_the_organizer_after_an_invoice_keeps_past_statements(env):
    A, c, freeze = env
    flat(A, "8")
    household(A, 1, "Org One", "Acme School")
    household(A, 2, "Org Two", "Beta PTA")
    freeze("2026-03-01")
    post(c, "/events", name="Gala", date="2026-06-10", venue="Hall")
    eid = one(A, "SELECT MAX(id) FROM events")[0]
    post(c, f"/event/{eid}/billing", billing="organizer", payer_hid="1")
    sid = one(A, "SELECT id FROM sessions WHERE payer_event=?", (eid,))[0]
    line(c, sid, "Event coverage", 1200)
    post(c, f"/session/{sid}/invoice/issue")
    before = stmt(A, 1, "2026-03")["closing"]
    freeze("2026-04-05")
    post(c, f"/event/{eid}/billing", payer_hid="2")
    assert stmt(A, 1, "2026-03")["closing"] == before == 1296
    assert stmt(A, 2, "2026-03")["closing"] == 0

def test_odd_statement_months_and_close_ids_are_refused_not_errors(env):
    A, c, freeze = env
    household(A, 1, "Lane")
    for m in ("9999-12", "0001-01"):
        assert c.get(f"/client/1/statement?m={m}", environ_base=E).status_code == 400
    assert c.post("/close", data=dict(sid="abc", step="balance_paid", outcome="waived"), environ_base=E).status_code == 400
