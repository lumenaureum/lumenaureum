import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, flat, get_zip, household, line, one, pay, post, reconcile, run,
                                  session, stmt, tax_of, tot, year)
from test_money_r2_130 import booked

BOOK_1_2 = """
INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8'), ('mileage_rate', '0.70');
INSERT INTO households (id, name) VALUES (1,'H1'),(2,'H2'),(3,'H3'),(4,'H4'),(5,'H5'),(6,'H6'),(7,'H7'),(8,'H8');
INSERT INTO sessions (id, household_id, type, stage, lost_reason, stage_changed, shoot_date, tax_rate, tax_exempt, created) VALUES
 (1,1,'portrait','booked','','2025-01-05 12:00:00','2025-02-15',8,0,'2025-01-01'),
 (2,2,'portrait','lost','Canceled','2025-04-01 12:00:00','2025-03-15',8,0,'2025-01-01'),
 (3,3,'portrait','booked','','2025-01-05 12:00:00','2025-04-15',8,0,'2025-01-01'),
 (4,4,'portrait','booked','','2025-01-05 12:00:00','2025-05-15',8,0,'2025-01-01'),
 (5,5,'portrait','booked','','2025-01-05 12:00:00','2025-06-15',8,0,'2025-01-01'),
 (6,6,'portrait','booked','','2025-01-05 12:00:00','2025-07-15',8,0,'2025-01-01'),
 (7,7,'portrait','lost','No-show','2026-03-01 12:00:00','2025-08-15',8,0,'2025-01-01'),
 (8,8,'portrait','booked','','2025-01-05 12:00:00','2025-01-15',8,0,'2025-01-01');
INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES
 (1,'S',350,'custom',1),(1,'Sales tax (8%)',28,'tax',0),(2,'S',400,'custom',1),(2,'Sales tax (8%)',32,'tax',0),
 (3,'S',450,'custom',1),(3,'Sales tax (8%)',36,'tax',0),(4,'S',500,'custom',1),(4,'Sales tax (8%)',40,'tax',0),
 (5,'S',550,'custom',1),(6,'S',600,'custom',1),(7,'S',650,'custom',1),(7,'Sales tax (8%)',52,'tax',0),
 (8,'S',700,'custom',1),(8,'Sales tax (8%)',56,'tax',0),(5,'Extra',100,'custom',1),(5,'Sales tax (8%)',52,'tax',0),
 (6,'Extra',80,'custom',1),(6,'Sales tax (8%)',54.4,'tax',0);
INSERT INTO payments (id, session_id, kind, amount, received, voided, method) VALUES
 (1,1,'deposit',100,'2025-01-10',NULL,'cash'),(2,1,'balance',278,'2025-03-01',NULL,'cash'),
 (3,2,'deposit',100,'2025-01-10',NULL,'cash'),(4,2,'balance',332,'2025-02-01',NULL,'cash'),
 (5,2,'refund',-200,'2025-05-01',NULL,'cash'),(6,3,'balance',536,'2025-04-01',NULL,'cash'),
 (7,4,'deposit',100,'2025-02-01',NULL,'cash'),(8,5,'balance',594,'2025-06-01',NULL,'cash'),
 (9,6,'deposit',100,'2025-02-01',NULL,'cash'),(10,7,'deposit',150,'2025-03-01',NULL,'cash'),
 (11,8,'balance',756,'2025-03-01',NULL,'cash'),(12,8,'balance',99,'2025-03-02','2025-04-01','cash'),
 (13,2,'refund',-132,'2026-01-10',NULL,'cash'),(14,3,'refund',-50,'2026-01-10',NULL,'cash'),
 (15,4,'balance',440,'2026-01-10',NULL,'cash'),(16,5,'balance',108,'2026-02-01',NULL,'cash');
INSERT INTO refund_credits (payment_id, session_id, amount) VALUES (5,2,200),(13,2,132);
INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (1,'2025-03-01','trip',0,33.3,0.655),
 (4,'2026-01-10','trip',0,12.5,0.7);
INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (6,'INV-replaced:INV-2025-0001','INV-2025-0001','2025-04-01');
"""

FIN_1_2 = {2025: dict(total=2846.0, refunds=-200.0, tax_due=190.52, mileage=21.81,
                      by_month={"2025-01": 200.0, "2025-02": 532.0, "2025-03": 1184.0, "2025-04": 536.0,
                                "2025-05": -200.0, "2025-06": 594.0}),
           2026: dict(total=366.0, refunds=-182.0, tax_due=48.89, mileage=8.75,
                      by_month={"2026-01": 258.0, "2026-02": 108.0})}

STMT_1_2 = {1: [("2025-01", -100.0), ("2025-03", -378.0)], 2: [("2025-01", -100.0), ("2025-02", -432.0)],
            3: [("2025-04", -536.0), ("2026-01", -486.0)], 4: [("2025-02", -100.0), ("2026-01", -540.0)],
            5: [("2025-06", -594.0), ("2026-02", -702.0)], 6: [("2025-02", -100.0)], 7: [("2025-03", -150.0)],
            8: [("2025-03", -756.0)]}

def _months(upto):
    return [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13) if f"{y}-{m:02d}" < upto]

def _upgrade(A):
    run(A, "DELETE FROM settings WHERE key='charges_seeded'")
    with A.app.test_request_context():
        A.db()
        A._fill_all_payment_taxes()
        A._seed_charges()
        A.g.db.commit()

def _as_1_2_showed(A, c, upto):
    for y_, want in FIN_1_2.items():
        ym = year(A, y_)
        assert (round(ym["total"], 2), round(ym["refunds"], 2), ym["tax_due"], round(ym["exp"]["mc"], 2)) == \
            (want["total"], want["refunds"], want["tax_due"], want["mileage"]), y_
        assert {r_["mo"]: round(r_["v"], 2) for r_ in ym["by_month"]} == want["by_month"]
        got = reconcile(A, c, y_)
        assert (float(got[0]), float(got[1]), float(got[5])) == (want["total"], want["tax_due"], want["mileage"])
    for hid, changes in STMT_1_2.items():
        bal = 0.0
        for m in _months(upto):
            bal = dict(changes).get(m, bal)
            v = stmt(A, hid, m)
            assert (v["closing"], round(v["opening"] + sum(r_["amount"] for r_ in v["invoices"])
                                        - sum(r_["amount"] for r_ in v["payments"]), 2)) == (bal, bal), (hid, m)

def test_the_update_from_1_2_reads_every_year_and_past_statement_as_1_2_showed_them(env):

    A, c, freeze = env
    run(A, BOOK_1_2)
    A.apply_settings()
    freeze("2026-11-15")
    _upgrade(A)
    _as_1_2_showed(A, c, "2026-11")

    with A.app.test_request_context():
        charges = {r_["session_id"]: (r_["at"], r_["label"]) for r_ in A.q("SELECT * FROM charges WHERE amount > 0")}
    assert charges[2] == ("2026-11-15", "Canceled, deposit kept") and charges[7] == ("2026-11-15", "No-show, deposit kept")
    assert all(at_ >= "2026-11-15" for at_, label_ in charges.values() if label_)

    _upgrade(A)
    _as_1_2_showed(A, c, "2026-11")

def test_after_the_update_the_1_3_rules_apply_from_the_day_of_each_change(env):

    A, c, freeze = env
    run(A, BOOK_1_2)
    A.apply_settings()
    freeze("2026-11-15")
    _upgrade(A)
    freeze("2027-01-10")
    post(c, "/session/7/stage", stage="lost", lost_reason="Canceled")
    post(c, "/session/3/money", what="line", label="Album", amount="100", taxable="1", taxable_shown="1")
    _as_1_2_showed(A, c, "2026-11")
    with A.app.test_request_context():
        assert round(A.stored_tax(7), 2) == round(A._tax_target(7)[0], 2)
        assert round(A.stored_tax(3), 2) == round(A._tax_target(3)[0], 2)
    assert tax_of(A, 2027) != 0
    reconcile(A, c, 2027)

def test_a_1_2_kept_deposit_with_no_cancel_date_or_exempt_is_not_taxed_twice(env):

    A, c, freeze = env
    flat(A, "8")
    run(A, """INSERT INTO households (id, name) VALUES (1, 'H1');
              INSERT INTO sessions (id, household_id, type, stage, lost_reason, stage_changed, shoot_date, tax_rate, tax_exempt)
                VALUES (1, 1, 'portrait', 'lost', 'Canceled', '2025-06-01 12:00:00', '2025-08-01', 8, 1);
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'S', 500, 'custom', 1);
              INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 200, '2025-02-01', 'cash')""")
    freeze("2026-11-15")
    _upgrade(A)
    assert tax_of(A, 2025) == 0 and one(A, "SELECT COUNT(*) FROM tax_adjustments")[0] == 0

def test_tip_back_is_offered_only_with_tips_and_moves_tips_only(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 540, "2026-05-02")
    assert 'value="tip_back"' not in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    pay(c, 1, "tip", 60, "2026-05-02")
    assert 'value="tip_back"' in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    before = dict(tot(A, 1)), stmt(A, 1, "2026-05")["closing"]
    pay(c, 1, "tip_back", 80, "2026-05-20")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 2
    pay(c, 1, "refund", 600, "2026-05-20")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 2
    pay(c, 1, "tip_back", 25, "2026-05-20")
    t = tot(A, 1)
    assert (t["tips"], t["paid"], t["due"], t["refund_credit"], t["kept"]) == (35, 540, 0, 0, 0)
    assert t["profit"] == before[0]["profit"] - 25
    assert stmt(A, 1, "2026-05")["closing"] == before[1]
    ym = year(A, 2026)
    assert (ym["tips"], ym["refunds"], ym["total"], ym["tax_due"]) == (35, 0, 575, 40)
    reconcile(A, c, 2026)

def test_a_booking_lost_for_price_keeps_its_deposit_and_owes_back_the_rest(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    booked(A, c, 1, 1)
    pay(c, 1, "deposit", 100, "2026-02-01")
    pay(c, 1, "balance", 200, "2026-02-01")
    freeze("2026-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Price")
    t = tot(A, 1)
    assert (t["kept"], t["refund_due"]) == (100, 200)
    assert round(tax_of(A, 2026), 2) == round(100 * 40 / 540, 2)
    with A.app.test_request_context():
        assert A.q("SELECT label FROM charges WHERE amount > 0")[-1]["label"] == "Lost, deposit kept"
    assert stmt(A, 1, "2026-03")["closing"] == -200
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "You keep the deposit, $100.00, per the Terms, whatever the reason it was lost" in page and "Refund due: $200.00<" in page
    pay(c, 1, "refund", 200, "2026-03-02")
    assert stmt(A, 1, "2026-03")["closing"] == 0 and tot(A, 1)["kept"] == 100
    post(c, "/session/1/stage", stage="lost", lost_reason="Chose someone else")
    assert tot(A, 1)["kept"] == 100 and stmt(A, 1, "2026-03")["closing"] == 0

def test_changing_the_organizer_after_an_invoice_says_the_billing_stays(env):
    A, c, freeze = env
    flat(A, "8")
    household(A, 1, "Org One", "Acme School")
    household(A, 2, "Org Two", "Beta PTA")
    freeze("2026-03-01")
    post(c, "/events", name="Gala", date="2026-06-10", venue="Hall")
    eid = one(A, "SELECT MAX(id) FROM events")[0]
    post(c, f"/event/{eid}/billing", billing="organizer", payer_hid="1")
    sid = one(A, "SELECT id FROM sessions WHERE payer_event=?", (eid,))[0]
    post(c, f"/event/{eid}/billing", payer_hid="2")
    assert one(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0] == 2
    line(c, sid, "Event coverage", 1000)
    post(c, f"/session/{sid}/invoice/issue")
    r = c.post(f"/event/{eid}/billing", data=dict(payer_hid="1"), environ_base=E, follow_redirects=True)
    assert one(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0] == 2
    assert "stays with the previous organizer" in r.get_data(as_text=True)

def test_statement_months_outside_the_app_s_years_are_refused(env):
    A, c, freeze = env
    household(A, 1, "Lane")
    for m in ("9999-12", "0001-01", "1899-12", "2101-01"):
        assert c.get(f"/client/1/statement.pdf?m={m}", environ_base=E).status_code == 400
    assert c.get("/client/1/statement?m=2100-12", environ_base=E).status_code == 200
    assert c.post("/close", data=dict(task="x1", outcome="done"), environ_base=E).status_code == 400

def test_back_then_another_method_is_a_new_payment(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received="2026-05-02", method="cash", once="t")
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received="2026-05-02", method="check", once="t")
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received="2026-05-02", method="check", once="t")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 2

def test_negative_expenses_and_payments_dated_ahead_are_refused(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    post(c, "/session/1/money", what="expense", label="Returned lens", amount="-80", date="2026-05-02")
    post(c, "/money/overhead", label="Returned lens", amount="-80", date="2026-05-02")
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 0
    pay(c, 1, "deposit", 100, "2026-06-03")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 0
    pay(c, 1, "deposit", 100, "2026-06-02")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 1
    post(c, "/session/1/quote/fee", fee_key="custom", label="Loyalty discount", amount="-50", qty="1", taxable="0",
         taxable_shown="1")
    assert one(A, "SELECT amount FROM lines WHERE label='Loyalty discount'")[0] == -50

def test_done_after_a_write_off_was_paid_after_all_keeps_the_closed_year(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2025-06-02")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2025-06-01")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 300, "2025-06-02")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    freeze("2026-03-01")
    pay(c, 1, "balance", 240, "2026-03-01")
    before = (year(A, 2025)["written_off"], year(A, 2026)["written_off"], stmt(A, 1, "2025-06")["closing"],
              stmt(A, 1, "2026-03")["closing"])
    assert before[:2] == (240, -240)
    freeze("2027-01-05")
    post(c, "/close", sid="1", step="balance_paid", outcome="done")
    assert (year(A, 2025)["written_off"], year(A, 2026)["written_off"], stmt(A, 1, "2025-06")["closing"],
            stmt(A, 1, "2026-03")["closing"]) == before
    assert tot(A, 1)["due"] == 0

def test_un_waiving_with_money_still_owed_takes_the_write_off_back(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-06-02")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 300, "2026-06-02")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    pay(c, 1, "balance", 100, "2026-06-03")
    post(c, "/close", sid="1", step="balance_paid", outcome="done")
    assert tot(A, 1)["due"] == 140 and one(A, "SELECT COUNT(*) FROM write_offs")[0] == 0

def test_a_backdated_payment_is_matched_by_work_done_in_its_month(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-05-20")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    april = stmt(A, 1, "2026-04")["closing"]
    freeze("2026-06-10")
    pay(c, 1, "balance", 540, "2026-05-02")
    assert stmt(A, 1, "2026-05")["closing"] == 0 and stmt(A, 1, "2026-04")["closing"] == april == 0
    with A.app.test_request_context():
        assert [tuple(r_) for r_ in A.q("SELECT at, amount, label FROM charges")] == [("2026-05-01", 540.0, "Work done")]

def test_a_backdated_payment_after_an_earlier_one_never_reaches_back(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-03-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-05-01")
    line(c, 1, "Session", 500)
    pay(c, 1, "deposit", 100, "2026-03-01")
    freeze("2026-05-05")
    post(c, "/session/1/stage", stage="shot")
    before = [stmt(A, 1, m)["closing"] for m in ("2026-03", "2026-04", "2026-05")]
    freeze("2026-06-10")
    pay(c, 1, "balance", 440, "2026-05-02")
    after = [stmt(A, 1, m)["closing"] for m in ("2026-03", "2026-04")]
    assert after == before[:2]

def test_a_backdated_payment_on_an_updated_session_is_not_dated_before_the_update(env):
    A, c, freeze = env
    flat(A, "8")
    run(A, """INSERT INTO households (id, name) VALUES (1, 'H1');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date, tax_rate) VALUES (1, 1, 'portrait', 'shot', '2026-03-01', 8);
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'S', 500, 'custom', 1), (1, 'Sales tax (8%)', 40, 'tax', 0)""")
    freeze("2026-04-10")
    _upgrade(A)
    freeze("2026-06-10")
    pay(c, 1, "balance", 540, "2026-03-05")
    with A.app.test_request_context():
        assert [r_["at"] for r_ in A.q("SELECT at FROM charges WHERE amount > 0")] == ["2026-04-10"]
    assert stmt(A, 1, "2026-03")["closing"] == -540

def test_voiding_a_payment_takes_its_cancel_correction_out_of_its_year(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2025-02-01")
    household(A, 1, "Lane")
    booked(A, c, 1, 1, 500, "2025-06-01")
    pay(c, 1, "balance", 540, "2025-02-01")
    freeze("2025-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    assert round(tax_of(A, 2025), 2) == 7.41
    freeze("2026-01-15")
    pid = one(A, "SELECT id FROM payments")[0]
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (0, 0)
    reconcile(A, c, 2025)
    post(c, f"/money/payment/{pid}/delete", set="restore")
    assert (round(tax_of(A, 2025), 2), tax_of(A, 2026)) == (7.41, 0)

def test_voiding_one_of_two_payments_leaves_the_other_s_kept_share(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2025-02-01")
    household(A, 1, "Lane")
    booked(A, c, 1, 1, 500, "2025-06-01")
    pay(c, 1, "deposit", 270, "2025-02-01")
    pay(c, 1, "balance", 270, "2025-02-02")
    freeze("2025-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    freeze("2026-01-15")
    pid = one(A, "SELECT MIN(id) FROM payments")[0]
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert round(tax_of(A, 2025), 2) == 7.41
    assert tax_of(A, 2025) >= 0
