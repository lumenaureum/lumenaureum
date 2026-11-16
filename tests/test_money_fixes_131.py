import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, flat, household, line, one, pay, post, reconcile, run,
                                  session, stmt, tot, year)
from test_money_fixes_r2_130 import _upgrade

def _rows(A, sql, args=()):
    con = A.connect()
    try:
        return [tuple(r_) for r_ in con.execute(sql, args).fetchall()]
    finally:
        con.close()

BOOK_1_2_TIP = """
INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8');
INSERT INTO households (id, name) VALUES (1, 'H1'), (2, 'H2');
INSERT INTO sessions (id, household_id, type, stage, lost_reason, stage_changed, shoot_date, tax_rate, tax_exempt, created) VALUES
 (1, 1, 'portrait', 'booked', '', '2025-01-05 12:00:00', '2025-02-15', 8, 0, '2025-01-01'),
 (2, 2, 'portrait', 'lost', 'No-show', '2025-06-01 12:00:00', '2025-05-15', 8, 0, '2025-01-01');
INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES
 (1, 'S', 500, 'custom', 1), (1, 'Sales tax (8%)', 40, 'tax', 0), (2, 'S', 400, 'custom', 1), (2, 'Sales tax (8%)', 32, 'tax', 0);
INSERT INTO payments (id, session_id, kind, amount, received, voided, method) VALUES
 (1, 1, 'deposit', 100, '2025-01-10', NULL, 'cash'), (2, 1, 'tip', 440, '2025-03-01', NULL, 'cash'),
 (3, 2, 'tip', 150, '2025-02-01', NULL, 'cash');
INSERT INTO books (received, amount, tax, type, kind) VALUES ('2025-04-01', 54, 4.0, 'portrait', 'tip');
"""

def test_a_1_2_row_marked_tip_reads_as_1_2_read_it(env):

    A, c, freeze = env
    run(A, BOOK_1_2_TIP)
    A.apply_settings()
    freeze("2026-11-15")
    _upgrade(A)
    assert _rows(A, "SELECT id, kind, tax FROM payments ORDER BY id") == [(1, "deposit", 7.41), (2, "other", 32.59),
                                                                          (3, "other", 0.0)]
    ym = year(A, 2025)
    assert (ym["total"], ym["tips"], ym["tax_due"]) == (744, 0, 40 + 12 + 4)
    t = tot(A, 1)
    assert (t["paid"], t["tips"], round(t["due"], 2)) == (540, 0, 0)
    assert stmt(A, 1, "2025-01")["closing"] == -100 and stmt(A, 1, "2025-03")["closing"] == -540
    assert [r_["amount"] for r_ in stmt(A, 1, "2025-03")["payments"]] == [440]
    reconcile(A, c, 2025)
    _upgrade(A)
    assert year(A, 2025)["tax_due"] == 56 and stmt(A, 1, "2025-03")["closing"] == -540
    pay(c, 1, "tip", 20, "2026-11-15")
    assert (tot(A, 1)["tips"], year(A, 2026)["tips"]) == (20, 20)

def _count(A, table):
    return one(A, f"SELECT COUNT(*) FROM {table}")[0]

def test_payment_and_expense_dates_before_2000_are_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 500)
    for day in ("1900-01-01", "1999-12-31"):
        pay(c, 1, "deposit", 100, day)
        post(c, "/session/1/money", what="expense", label="Lens", amount="80", date=day)
        post(c, "/money/overhead", label="Lens", amount="80", date=day)
    assert (_count(A, "payments"), _count(A, "expenses")) == (0, 0)
    page = c.get("/session/1", environ_base=E).get_data(as_text=True)
    assert "before 2000" in page
    pay(c, 1, "deposit", 100, "2000-01-01")
    post(c, "/session/1/money", what="expense", label="Lens", amount="80", date="2000-01-01")
    assert (_count(A, "payments"), _count(A, "expenses")) == (1, 1)
    pay(c, 1, "tip", 50, "2026-05-02")
    post(c, "/session/1/money", what="payment", kind="tip_back", amount="10", received="1900-01-01", method="cash")
    assert _count(A, "payments") == 2

def test_expenses_dated_ahead_are_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    post(c, "/session/1/money", what="expense", label="Lens", amount="80", date="2026-06-03")
    post(c, "/money/overhead", label="Insurance", amount="300", date="2026-12-01")
    assert _count(A, "expenses") == 0
    assert "the future" in c.get("/money", environ_base=E).get_data(as_text=True)
    post(c, "/session/1/money", what="expense", label="Lens", amount="80", date="2026-06-02")
    post(c, "/money/overhead", label="Insurance", amount="300", date="2026-06-01")
    post(c, "/money/overhead", label="Software", amount="30")
    assert _count(A, "expenses") == 3

def test_miles_must_be_above_0_and_an_empty_expense_is_refused(env):
    A, c, freeze = env
    flat(A, "8", "0.70")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    for miles in ("-10", "0", "abc", "1e400", "200000"):
        post(c, "/session/1/money", what="expense", label="Trip", miles=miles, date="2026-05-30")
        post(c, "/money/overhead", label="Trip", miles=miles, amount="5", date="2026-05-30")
    assert "Miles are a number above 0" in c.get("/money", environ_base=E).get_data(as_text=True)
    post(c, "/session/1/money", what="expense", label="Nothing", date="2026-05-30")
    assert _count(A, "expenses") == 0
    post(c, "/session/1/money", what="expense", label="Trip", miles="12.5", date="2026-05-30")
    post(c, "/money/overhead", label="Parking", amount="5", date="2026-05-30")
    assert _rows(A, "SELECT label, amount, miles, rate FROM expenses ORDER BY id") == [("Trip", 0.0, 12.5, 0.7),
                                                                                       ("Parking", 5.0, 0, None)]

def test_a_quantity_that_isn_t_a_number_is_refused(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-07-01")
    for qty in ("abc", "1e400", "0"):
        post(c, "/session/1/quote/fee", fee_key="custom", label="Album", amount="100", qty=qty, taxable="1",
             taxable_shown="1")
    assert one(A, "SELECT COUNT(*) FROM lines WHERE label LIKE 'Album%'")[0] == 0
    post(c, "/session/1/quote/fee", fee_key="custom", label="Album", amount="100", qty="", taxable="1", taxable_shown="1")
    lid, amount = one(A, "SELECT id, amount FROM lines WHERE label='Album'")
    assert amount == 100
    post(c, f"/line/{lid}/qty", qty="abc")
    assert tuple(one(A, "SELECT qty, amount FROM lines WHERE id=?", (lid,))) == (1, 100)
    post(c, f"/line/{lid}/qty", qty="2")
    assert tuple(one(A, "SELECT qty, amount FROM lines WHERE id=?", (lid,))) == (2, 200)

def test_statement_months_are_yyyy_mm_in_ascii_digits(env):
    A, c, freeze = env
    household(A, 1, "Lane")
    for m in ("2026-1", "2026-001", "02026-01", "2026-01-01", " 2026-01", "２０２６-０１",
              "2026-٠١", "+2026-01", "2026-+1"):
        assert c.get("/client/1/statement", query_string={"m": m}, environ_base=E).status_code == 400, m
        assert c.get("/client/1/statement.pdf", query_string={"m": m}, environ_base=E).status_code == 400, m
    assert c.get("/client/1/statement?m=2026-01", environ_base=E).status_code == 200

def test_a_refused_form_is_filled_again_with_what_was_typed(env):

    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    post(c, "/session/1/money", what="expense", label="Trip", miles="-10", date="2026-05-30")
    page = c.get("/session/1", environ_base=E).get_data(as_text=True)
    assert 'data-refill="/session/1/money"' in page
    assert 'data-refill' not in c.get("/session/1", environ_base=E).get_data(as_text=True)
    js = open(os.path.join(ROOT, "static", "ui.js")).read()
    assert "data-refill" in js and "la-form" in js

def _browser_page(b, c):

    from urllib.parse import urlsplit

    def local(url):
        u = urlsplit(url)
        return u.path + ("?" + u.query if u.query else "")

    def handle(route, request):
        if request.method == "POST":
            r = c.post(local(request.url), data=request.post_data_buffer or b"", environ_base=E,
                       headers={"Content-Type": request.headers.get("content-type", "")})
        else:
            r = c.get(local(request.url), environ_base=E)
        while r.status_code in (301, 302, 303):
            r = c.get(local(r.location), environ_base=E)
        route.fulfill(status=r.status_code, body=r.data, headers={
            k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = b.new_context(viewport={"width": 1280, "height": 800})
    ctx.route("https://app.test/**", handle)
    return ctx, ctx.new_page()

def test_a_refused_expense_comes_back_filled_in_a_browser(env):
    sync_api = pytest.importorskip("playwright.sync_api")
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    line(c, 1, "Session", 500)
    pw = sync_api.sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as e:
        pw.stop()
        pytest.skip(f"chromium is not available: {e}")
    expense = "form:has(input[name=what][value=expense])"
    try:
        ctx, page = _browser_page(b, c)
        page.goto("https://app.test/session/1")
        page.locator(expense).evaluate("f => { const d = f.closest('details'); if (d) d.open = true; }")
        page.locator(expense + " [name=label]").fill("Trip to the park")
        page.locator(expense + " [name=miles]").fill("-10")
        with page.expect_navigation():
            page.locator(expense).evaluate("f => f.requestSubmit()")
        page.wait_for_selector(".toast[data-refill]", state="attached")
        assert page.locator(expense + " [name=label]").input_value() == "Trip to the park"
        assert page.locator(expense + " [name=miles]").input_value() == "-10"
        assert _count(A, "expenses") == 0
        page.goto("https://app.test/session/1")
        assert page.locator(expense + " [name=label]").input_value() == ""
        ctx.close()
    finally:
        b.close()
        pw.stop()

def test_close_with_an_id_that_isn_t_a_number_is_a_plain_400(env, monkeypatch):
    A, c, freeze = env
    logged = []
    monkeypatch.setattr(A.app.logger, "exception", lambda *a, **kw: logged.append(a))
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-07-01")
    for data in (dict(sid="abc", step="balance_paid"), dict(sid="²", step="balance_paid"), dict(task="abc"),
                 dict(task="²"), dict(task="١"), dict(sid="1", task="x")):
        r = c.post("/close", data=dict(data, outcome="done"), environ_base=E)
        assert r.status_code == 400, data
    assert logged == []
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Closed%'")[0] == 0

@pytest.mark.parametrize("price", [0, 500])
def test_a_booking_called_off_with_money_paid_writes_one_charge(env, price):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-08-01")
    if price:
        line(c, 1, "Session", price)
    pay(c, 1, "deposit", 100, "2026-02-01")
    assert _count(A, "charges") == 0
    freeze("2026-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    assert _rows(A, "SELECT at, amount, label FROM charges") == [("2026-03-01", 100.0, "Canceled, deposit kept")]
    assert stmt(A, 1, "2026-03")["closing"] == 0
    freeze("2026-03-05")
    post(c, "/session/1/stage", stage="lost", lost_reason="No-show")
    assert _rows(A, "SELECT at, amount, label FROM charges ORDER BY id")[1:] == [
        ("2026-03-05", -100.0, "Canceled, deposit kept replaced"), ("2026-03-05", 100.0, "No-show, deposit kept")]
    reconcile(A, c, 2026)

def test_deleting_a_session_leaves_no_0_00_row_on_finances(env):

    A, c, freeze = env
    run(A, """INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8');
              INSERT INTO households (id, name) VALUES (1, 'H1');
              INSERT INTO sessions (id, household_id, type, stage, lost_reason, stage_changed, shoot_date, tax_rate,
                                    tax_exempt, created)
                VALUES (1, 1, 'portrait', 'lost', 'No-show', '2026-03-01 12:00:00', '2025-08-15', 8, 0, '2025-01-01');
              INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'S', 650, 'custom', 1),
                (1, 'Sales tax (8%)', 52, 'tax', 0);
              INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 150, '2025-03-01', 'cash')""")
    A.apply_settings()
    freeze("2026-11-15")
    _upgrade(A)
    before = {y: year(A, y) for y in (2025, 2026)}
    assert (before[2025]["tax_due"], before[2026]["tax_due"], before[2026]["total"]) == (0, 12, 0)
    post(c, "/session/1/delete")
    after = {y: year(A, y) for y in (2025, 2026)}
    for y in (2025, 2026):
        assert (after[y]["tax_due"], after[y]["total"]) == (before[y]["tax_due"], before[y]["total"]), y
    assert [dict(r_) for r_ in after[2026]["received"]] == [] and [dict(r_) for r_ in after[2026]["by_month"]] == []
    assert [(r_["type"], r_["v"]) for r_ in after[2025]["received"]] == [("portrait", 150)]
    reconcile(A, c, 2025)
    reconcile(A, c, 2026)

def test_a_correction_that_carries_no_tax_is_not_kept_on_delete(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2025-02-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2025-08-01")
    line(c, 1, "Session", 500)
    post(c, "/session/1/stage", stage="booked")
    pay(c, 1, "deposit", 100, "2025-02-01")
    pay(c, 1, "balance", 440, "2025-02-01")
    freeze("2025-03-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    for (pid,) in _rows(A, "SELECT id FROM payments"):
        post(c, f"/money/payment/{pid}/delete", set="void")
    freeze("2026-01-10")
    post(c, "/session/1/delete")
    assert _count(A, "books") == 0
    ym = year(A, 2025)
    assert (ym["tax_due"], ym["total"], list(ym["received"]), list(ym["by_month"])) == (0, 0, [], [])

def test_environment_written_by_a_test_part_1(env):

    A, c, freeze = env
    run(A, "INSERT INTO settings (key, value) VALUES ('timezone', 'Pacific/Kiritimati')")
    A.apply_settings()
    os.environ["CRM_LEAK_PROBE"] = "1"
    assert os.environ.get("TZ") == "Pacific/Kiritimati"

def test_environment_written_by_a_test_part_2():

    assert "CRM_LEAK_PROBE" not in os.environ
    assert os.environ.get("TZ") != "Pacific/Kiritimati"
    assert time.strftime("%z", time.localtime(0)) != "+1400"
