import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, flat, get_zip, household, line, one, pay, post, reconcile, run,
                                  session, stmt, tax_of, tot, year)

def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def booked_530(A, c, freeze, day="2026-02-28"):
    flat(A, "6")
    freeze(day)
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2027-12-01")
    line(c, 1, "Session", 500)
    run(A, """INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-01-01', '{"deposit": 100, "total": 530}')""")
    post(c, "/session/1/stage", stage="booked")

def test_reopening_next_year_is_a_correction_in_that_year(env):
    A, c, freeze = env
    booked_530(A, c, freeze)
    pay(c, 1, "balance", 530, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    assert tax_of(A, 2026) == 5.66
    freeze("2027-01-15")
    post(c, "/session/1/stage", stage="booked")
    assert tax_of(A, 2026) == 5.66 and tax_of(A, 2027) == 24.34
    rows = get_zip(c, 2027)["sales-tax.csv"]
    assert [r["counted as"] for r in rows] == ["reopened after it was called off"]
    reconcile(A, c, 2026)
    reconcile(A, c, 2027)

def test_called_off_next_year_takes_the_tax_back_that_day(env):

    A, c, freeze = env
    booked_530(A, c, freeze, "2025-11-01")
    pay(c, 1, "balance", 530, "2025-11-02")
    freeze("2026-02-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (30.0, -24.34)
    freeze("2026-03-01")
    pay(c, 1, "refund", 430, "2026-03-01")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (30.0, -24.34)
    assert abs(stmt(A, 1, "2026-03")["closing"]) < 0.005
    for y in (2025, 2026):
        assert "kept deposit" not in {r["counted as"] for r in get_zip(c, y)["sales-tax.csv"]}
        reconcile(A, c, y)

def test_a_refund_carries_its_own_tax_in_its_year(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2025-12-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2025-11-20")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 540, "2025-12-01")
    freeze("2026-01-10")
    pay(c, 1, "refund", 108, "2026-01-10")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (40.0, -8.0)
    assert one(A, "SELECT tax FROM payments WHERE kind='refund'")[0] == -8.0
    reconcile(A, c, 2026)

def test_voiding_a_payment_from_last_year_changes_last_year(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2025-12-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2025-11-20")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 540, "2025-12-01")
    pid = one(A, "SELECT MAX(id) FROM payments")[0]
    freeze("2026-01-10")
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert (year(A, 2025)["total"], tax_of(A, 2025), tax_of(A, 2026)) == (0, 0, 0)
    post(c, f"/money/payment/{pid}/delete", set="restore")
    assert (year(A, 2025)["total"], tax_of(A, 2025), tax_of(A, 2026)) == (540, 40.0, 0)

def test_price_change_after_payment_is_a_correction_this_year(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2025-12-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2025-11-20")
    line(c, 1, "Session", 500)
    pay(c, 1, "balance", 540, "2025-12-01")
    freeze("2026-02-01")
    post(c, "/session/1/quote/discount", pct="10", reason="loyal")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (40.0, -4.0)
    pay(c, 1, "refund", 54, "2026-02-02")
    assert (tax_of(A, 2025), tax_of(A, 2026)) == (40.0, -4.0)
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "$4.00 taken back this year on money received earlier" in text(page)

def test_a_voided_payment_after_a_write_off_brings_the_write_off_back(env):
    A, c, freeze = env
    freeze("2026-05-01")
    household(A, 1, "Chen")
    session(A, 1, 1, "shot", "2026-04-01", "family")
    line(c, 1, "Family session", 300)
    pay(c, 1, "deposit", 200, "2026-03-01")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    freeze("2027-01-10")
    pay(c, 1, "balance", 100, "2027-01-10")
    assert (year(A, 2026)["written_off"], year(A, 2027)["written_off"]) == (100, -100)
    assert tot(A, 1)["written_off"] == 0 and abs(tot(A, 1)["due"]) < 0.005
    v = stmt(A, 1, "2027-01")
    assert abs(v["closing"]) < 0.005 and "write-off reversed (paid after all)" in [r["kind"] for r in v["payments"]]
    pid = one(A, "SELECT MAX(id) FROM payments")[0]
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert tot(A, 1)["written_off"] == 100 and abs(tot(A, 1)["due"]) < 0.005
    assert year(A, 2026)["written_off"] == 100
    reconcile(A, c, 2027)

def test_waived_then_moved_back_to_booked_shows_no_credit(env):
    A, c, freeze = env
    freeze("2026-05-01")
    household(A, 1, "Chen")
    session(A, 1, 1, "shot", "2026-04-01", "family")
    line(c, 1, "Family session", 300)
    pay(c, 1, "deposit", 200, "2026-05-01")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    post(c, "/session/1/stage", stage="booked")
    v = stmt(A, 1, "2026-05")
    assert abs(v["closing"]) < 0.005, (v["invoices"], v["payments"])

def test_invoice_reissued_after_a_change_reverses_the_old_one(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-03-01")
    household(A, 1, "Okafor", "Okafor & Sons LLC")
    session(A, 1, 1, "booked", "2026-03-10", "headshot")
    line(c, 1, "Team headshots", 1000)
    post(c, "/session/1/invoice/issue")
    freeze("2026-04-15")
    post(c, "/session/1/quote/fee", fee_key="custom", label="Extra person", amount="100", qty="1", taxable="1",
         taxable_shown="1")
    post(c, "/session/1/invoice/issue")
    april = stmt(A, 1, "2026-04")
    assert [(r["number"], r["amount"]) for r in april["invoices"]] == [("INV-2026-0001 withdrawn", -1080),
                                                                      ("INV-2026-0002", 1188)]
    assert april["closing"] == 1188

def test_amounts_are_cents_and_too_much_is_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", "100.005")
    assert one(A, "SELECT amount FROM lines WHERE kind='custom'")[0] == 100.01
    pay(c, 1, "balance", "50.004", "2026-05-02")
    assert one(A, "SELECT amount FROM payments")[0] == 50.0
    for what, extra in (("line", dict(label="Big", amount="10000000.01")),
                        ("payment", dict(kind="balance", amount="20000000", received="2026-05-02")),
                        ("expense", dict(label="Big", amount="12000000"))):
        r = c.post("/session/1/money", data=dict(what=what, **extra), environ_base=E)
        assert r.status_code in (302, 303)
    assert one(A, "SELECT COUNT(*) FROM lines WHERE kind='custom'")[0] == 1
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 1
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 0
    post(c, "/money/overhead", label="Studio", amount="99999999", date="2026-06-01")
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 0
    with c.session_transaction() as s_:
        assert "aren't taken" in s_.get("notice", "")

def test_unknown_payment_kind_and_junk_dates_are_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 100)
    post(c, "/session/1/money", what="payment", kind="bribe", amount="10", received="2026-05-02")
    post(c, "/session/1/money", what="payment", kind="balance", amount="10", received="2026-05-03junk")
    post(c, "/session/1/money", what="expense", label="Prints", amount="5", date="2026-05-03 junk")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 0
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 0
    post(c, "/session/1/money", what="expense", label="Prints", amount="5", date=" 2026-05-03 ")
    assert one(A, "SELECT date FROM expenses")[0] == "2026-05-03"

    run(A, "INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 1, '2026-05-03junk')")
    assert c.get("/c/1", environ_base=E).status_code == 200

def test_sales_tax_file_shows_the_region_the_rate_was_kept_with(env):
    A, c, freeze = env
    A.hooks.tax_region = dict(name="Testland", areas=[("in", "Inside"), ("out", "Outside")],
                              area_of=lambda addr: ("in", "") if "City" in addr else ("out", ""),
                              extra_pct=lambda area, local: local if area == "in" else 0)
    try:
        run(A, """INSERT INTO settings (key, value) VALUES ('tax_mode','regional'),('sales_tax_pct','4'),('local_tax_pct','4.5');
                  INSERT INTO households (id, name, address) VALUES (1, 'Lane', '1 Main St, City');
                  INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'quoted', '2026-01-10')""")
        A.apply_settings()
        freeze("2025-11-01")
        line(c, 1, "Session", 400)
        pay(c, 1, "deposit", 100, "2025-11-02")
        freeze("2026-01-11")
        post(c, "/session/1/quote/exempt", tax_area="out")
        rows = get_zip(c, 2025)["sales-tax.csv"]
        assert [(r["region"], r["rate (%)"]) for r in rows] == [("Inside", "8.5")]
    finally:
        A.hooks.tax_region = None

def test_canceled_booking_profit_and_wording(env):

    A, c, freeze = env
    booked_530(A, c, freeze)
    pay(c, 1, "balance", 530, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    t = tot(A, 1)
    assert (t["kept"], t["refund_due"]) == (100, 430)
    assert round(t["profit"], 2) == round(100 - 100 * 30 / 530, 2)

    assert "Lane canceled. Refund owed: $430.00. Deposit of $100.00 is retained." in c.get("/", environ_base=E).get_data(as_text=True)
    household(A, 2, "Ruiz")
    session(A, 2, 2, "quoted", "2027-12-01")
    line(c, 2, "Session", 200)
    run(A, """INSERT INTO quote_versions (session_id, at, data) VALUES (2, '2026-01-01', '{"deposit": 0, "total": 212}')""")
    post(c, "/session/2/stage", stage="booked")
    pay(c, 2, "balance", 50, "2026-04-01")
    post(c, "/session/2/stage", stage="lost", lost_reason="No-show")
    assert (tot(A, 2)["kept"], tot(A, 2)["refund_due"]) == (0, 50)
    today_ = text(c.get("/", environ_base=E).get_data(as_text=True))
    assert "Ruiz did not show. Refund owed: $50.00." in today_ and "retained" not in today_.split("Ruiz did not show", 1)[1][:80]
    assert "no deposit to keep, all of it goes back" in text(c.get("/session/2/edit", environ_base=E).get_data(as_text=True))
    assert "No-show, deposit kept" not in text(c.get("/client/2/statement?m=2026-04", environ_base=E).get_data(as_text=True))

def test_payments_from_before_the_update_read_and_fill_as_before(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-01-10")
    household(A, 1, "Lane")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, tax_rate) VALUES (1, 1, 'portrait', 'booked', '2026-06-01', 8);
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 500, 'custom', 1);
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Sales tax (8%)', 40, 'tax', 0);
              INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 200, '2025-12-10')""")
    assert tax_of(A, 2025) == 14.81
    post(c, "/session/1/quote/fee", fee_key="custom", label="Album", amount="300", qty="1", taxable="0", taxable_shown="1")
    assert round(one(A, "SELECT tax FROM payments")[0], 2) == 14.81 and tax_of(A, 2025) == 14.81

def test_upgrade_records_charges_and_taxes_as_they_read(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-11-15")
    household(A, 1, "Lane")
    run(A, """DELETE FROM settings WHERE key='charges_seeded';
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'closed', '2026-03-05');
              INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 400, 'custom');
              INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 400, '2026-03-06')""")
    with A.app.test_request_context():
        A.db()
        A._fill_all_payment_taxes()
        A._seed_charges()
        A.g.db.commit()
    assert tuple(one(A, "SELECT at, amount, label FROM charges")) == ("2026-11-15", 400, "Work done")
    assert one(A, "SELECT tax FROM payments")[0] == 0

def test_a_test_build_write_offs_table_is_rebuilt(env):
    A, c, freeze = env
    run(A, """DROP TABLE write_offs;
              CREATE TABLE write_offs (session_id INTEGER PRIMARY KEY, amount REAL NOT NULL, at TEXT NOT NULL);
              INSERT INTO write_offs (session_id, amount, at) VALUES (7, 12.5, '2026-10-01')""")
    A.init_db()
    assert tuple(one(A, "SELECT session_id, amount, at, why FROM write_offs")) == (7, 12.5, "2026-10-01", "waived")

def test_unwaiving_the_balance_takes_the_write_off_back(env):
    A, c, freeze = env
    freeze("2026-05-01")
    household(A, 1, "Chen")
    session(A, 1, 1, "shot", "2026-04-01", "family")
    line(c, 1, "Family session", 300)
    pay(c, 1, "deposit", 200, "2026-03-01")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    assert tot(A, 1)["written_off"] == 100
    post(c, "/close", sid="1", step="balance_paid", outcome="done")
    assert tot(A, 1)["written_off"] == 0 and tot(A, 1)["due"] == 100
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Took back the write-off of $100%'")[0] == 1

def test_a_full_refund_never_releases_delivery(env):
    A, c, freeze = env
    freeze("2026-05-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-04-01")
    line(c, 1, "Session", 300)
    pay(c, 1, "balance", 300, "2026-04-02")
    pay(c, 1, "refund", 300, "2026-04-03")
    with A.app.test_request_context():
        assert A.release_blocked(1) == 300

def test_once_token_spent_only_on_a_save_and_per_entry(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    post(c, "/session/1/money", what="payment", kind="balance", amount="-5", received="2026-05-02", once="tok")
    post(c, "/session/1/money", what="payment", kind="balance", amount="50", received="2026-05-02", once="tok")
    post(c, "/session/1/money", what="payment", kind="balance", amount="50", received="2026-05-02", once="tok")
    post(c, "/session/1/money", what="payment", kind="tip", amount="10", received="2026-05-02", once="tok")
    assert [r[0] for r in A.connect().execute("SELECT amount FROM payments ORDER BY id").fetchall()] == [50, 10]

def test_a_quantity_below_half_is_refused_not_changed(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    post(c, "/session/1/quote/fee", fee_key="custom", label="Hour", amount="100", qty="2", taxable="0", taxable_shown="1")
    lid = one(A, "SELECT id FROM lines WHERE kind='custom'")[0]
    for bad in ("0", "-3", "0.2"):
        post(c, f"/line/{lid}/qty", qty=bad)
    assert tuple(one(A, "SELECT qty, amount FROM lines WHERE id=?", (lid,))) == (2, 200)
    post(c, f"/line/{lid}/qty", qty="3.50")
    assert tuple(one(A, "SELECT qty, amount FROM lines WHERE id=?", (lid,))) == (3.5, 350)
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE '%from 2 to 3.5,%'")[0] == 1
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE '%to 0%' OR action LIKE '%to -3%'")[0] == 0

def test_booked_deposit_stays_when_the_rule_changes(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-09-01")
    rules = A.C.DEPOSIT_RULE
    old = rules.get("portrait")
    try:
        rules["portrait"] = ("pct", 50)
        line(c, 1, "Session", 1000)
        post(c, "/session/1/stage", stage="booked")
        assert tot(A, 1)["deposit_req"] == 500
        rules["portrait"] = ("pct", 25)
        with A.app.test_request_context():
            assert A.totals(1)["deposit_req"] == 500 and A._totals(1)["deposit_req"] == 500
    finally:
        rules["portrait"] = old
        if old is None:
            rules.pop("portrait")

def test_tax_rows_add_up_to_the_cent(env):

    A, c, freeze = env
    flat(A, "8.875")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 333.33)
    for d_ in range(1, 8):
        pay(c, 1, "balance", 17.17, f"2026-0{1 + d_ % 5}-0{d_}")
    f = get_zip(c, 2026)
    total = float(f["summary.csv"][-1]["sales tax"])
    assert round(sum(float(m["sales tax"]) for m in f["summary.csv"][:-1]), 2) == total
    assert round(sum(float(t["sales tax"]) for t in f["sales-tax.csv"]), 2) == total
    assert all(round(r[0], 2) == r[0] for r in A.connect().execute("SELECT tax FROM payments").fetchall())

def test_refund_beyond_the_refund_due_says_so_and_a_tip_can_be_given_back(env):
    A, c, freeze = env
    booked_530(A, c, freeze)
    pay(c, 1, "balance", 530, "2026-03-01")
    pay(c, 1, "tip", 20, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    post(c, "/session/1/money", what="payment", kind="refund", amount="480", received="2026-04-02")
    with c.session_transaction() as s_:
        assert "more than the refund due" in s_.get("notice", "")
    freeze("2026-04-03")
    post(c, "/session/1/money", what="payment", kind="refund", amount="70", received="2026-04-03")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE kind='refund'")[0] == 1
    post(c, "/session/1/money", what="payment", kind="refund", amount="50", received="2026-04-03")
    post(c, "/session/1/money", what="payment", kind="tip_back", amount="20", received="2026-04-03")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE kind='refund'")[0] == 2
    assert (tot(A, 1)["tips"], tot(A, 1)["paid"]) == (0, 0)

def test_a_compact_date_is_saved_written_out(env):
    A, c, freeze = env
    freeze("2026-12-28")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-12-01")
    line(c, 1, "Session", 100)
    pay(c, 1, "balance", 100, "20261220")
    assert one(A, "SELECT received FROM payments")[0] == "2026-12-20"
    assert [m["received"] for m in get_zip(c, 2026)["summary.csv"] if m["month"] == "2026-12"] == ["100.00"]

def test_quick_stage_never_moves_a_tentative_date_to_shot(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (1, 1, 'newborn', 'booked', '2026-05-20', 1)""")
    post(c, "/session/1/quick", act="stage", stage="shot")
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"
