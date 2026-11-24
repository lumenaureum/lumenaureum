import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_139_155 as T139
from test_money_audit_130 import (E, env, flat, get_zip, household, line, one, pay, post, reconcile,
                                  session, year)

def text(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def _book(A, c):
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-12-12")
    line(c, 1, "Session", 500)

def _n(A, table):
    return one(A, f"SELECT COUNT(*) FROM {table}")[0]

def _rule(A):
    with A.app.test_request_context():
        return A.books_open_from(), A.year_closed(2026), A.year_closed(2027)

def _expense(c, day):
    post(c, "/session/1/money", what="expense", label="Drive", miles="20", amount="0", date=day)
    post(c, "/money/overhead", label="Lens cap", amount="12", date=day)

def test_the_rule_through_january_and_after(env):
    A, c, freeze = env
    freeze("2026-12-31")
    assert _rule(A) == ("2026-01-01", False, False)
    freeze("2027-01-15")
    assert _rule(A) == ("2026-01-01", False, False)
    freeze("2027-01-31")
    assert _rule(A) == ("2026-01-01", False, False)
    freeze("2027-02-01")
    assert _rule(A) == ("2027-01-01", True, False)
    freeze("2028-01-10")
    with A.app.test_request_context():
        assert A.books_open_from() == "2027-01-01" and A.year_closed(2026) and not A.year_closed(2027)

def test_a_december_check_entered_in_january_is_dated_in_december(env):
    A, c, freeze = env
    freeze("2027-01-15")
    _book(A, c)
    pay(c, 1, "deposit", 100, "2026-12-30", "check")
    pay(c, 1, "tip", 20, "2026-12-30")
    post(c, "/session/1/money", what="payment", kind="tip_back", amount="5", received="2026-12-31", method="Cash")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE substr(received,1,4)='2026'")[0] == 3
    _expense(c, "2026-12-29")
    assert _n(A, "expenses") == 2
    pay(c, 1, "deposit", 50, "2025-12-30")
    assert _n(A, "payments") == 3

def test_february_first_refuses_last_year_with_the_same_words_everywhere(env):
    A, c, freeze = env
    freeze("2027-02-01")
    _book(A, c)
    pay(c, 1, "deposit", 100, "2026-12-30", "check")
    pay(c, 1, "tip", 20, "2027-01-02")
    post(c, "/session/1/money", what="payment", kind="tip_back", amount="5", received="2026-12-31", method="Cash")
    _expense(c, "2026-12-29")
    assert (_n(A, "payments"), _n(A, "expenses")) == (1, 0)
    words = "That date is in 2026, and 2026 is closed: the app records nothing more in a closed year. Nothing was recorded."
    with A.app.test_request_context():
        assert A.closed_year_refusal("2026-12-30") == words and A.closed_year_refusal("2027-01-01") == ""
    pay(c, 1, "deposit", 100, "2026-12-30", "check")
    assert words in text(c.get("/session/1/edit", environ_base=E).get_data(as_text=True))

def test_the_bulk_path_follows_the_same_rule(env):
    A, c, freeze = env
    freeze("2027-01-31")
    sid = T139._event(A, c)
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert 'min="2026-01-01"' in page[page.index('id="fpay"'):]
    c.post("/event/1/payments", data={"pick": str(sid["Bell"]), "method": "cash", "received": "2026-12-30"}, environ_base=E)
    assert _n(A, "payments") == 1
    freeze("2027-02-01")
    r = c.post("/event/1/payments", data={"pick": str(sid["Chen"]), "method": "cash", "received": "2026-12-30"},
               environ_base=E, follow_redirects=True)
    assert "2026 is closed" in text(r.get_data(as_text=True))
    assert _n(A, "payments") == 1
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert 'min="2027-01-01"' in page[page.index('id="fpay"'):]

def test_closed_by_hand_in_january(env):
    A, c, freeze = env
    freeze("2027-01-10")
    _book(A, c)
    page = text(c.get("/money", environ_base=E).get_data(as_text=True))
    assert "2026 is open through January 31." in page and "Close 2026" in page
    raw = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "This can&#39;t be undone." in raw or "This can't be undone." in raw
    post(c, "/money/close-year", year="2026", back="/money?year=2026#year")
    assert _rule(A) == ("2027-01-01", True, False)
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Closed 2026 on Finances%'")[0] == 1
    pay(c, 1, "deposit", 100, "2026-12-30", "check")
    _expense(c, "2026-12-29")
    assert (_n(A, "payments"), _n(A, "expenses")) == (0, 0)
    pay(c, 1, "deposit", 100, "2027-01-10", "check")
    assert _n(A, "payments") == 1
    page = text(c.get("/money?year=2026", environ_base=E).get_data(as_text=True))
    assert "2026 is closed." in page and "Close 2026" not in page
    assert "Close 2026" not in text(c.get("/money", environ_base=E).get_data(as_text=True))
    post(c, "/money/close-year", year="2026")
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Closed 2026%'")[0] == 1
    freeze("2027-03-01")
    assert _rule(A) == ("2027-01-01", True, False)

def test_close_is_offered_only_in_january_for_last_year(env):
    A, c, freeze = env
    freeze("2027-02-01")
    page = text(c.get("/money?year=2026", environ_base=E).get_data(as_text=True))
    assert "2026 is closed." in page and "Close 2026" not in page
    assert "is closed." not in text(c.get("/money", environ_base=E).get_data(as_text=True))
    post(c, "/money/close-year", year="2026")
    post(c, "/money/close-year", year="2027")
    assert one(A, "SELECT COUNT(*) FROM settings WHERE key='books_closed_by_hand'")[0] == 0
    freeze("2027-01-20")
    post(c, "/money/close-year", year="2025")
    assert one(A, "SELECT COUNT(*) FROM settings WHERE key='books_closed_by_hand'")[0] == 0

def test_a_late_expense_is_recorded_in_the_open_year_marked_with_its_closed_year(env):
    A, c, freeze = env
    freeze("2027-03-05")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-12-12")
    before = year(A, 2026)["exp"]
    post(c, "/money/overhead", label="Lens cap", amount="12", date="2026-12-29")
    r = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "To add a 2026 expense found late, use Add a late expense on Finances for 2026" in text(r)
    assert "Add a late expense" in text(r)
    post(c, "/money/overhead", label="Lens cap", amount="12", date="2026-12-29", late="1")
    post(c, "/session/1/money", what="expense", label="Drive", miles="20", amount="5", date="2026-12-12", from_shoot="1")
    rows = [tuple(r_) for r_ in A.connect().execute("SELECT date, late_from, amount FROM expenses ORDER BY id")]
    assert rows == [("2027-03-05", "2026-12-29", 12.0), ("2027-03-05", "2026-12-12", 5.0)]
    assert year(A, 2026)["exp"] == before
    page = text(c.get("/money", environ_base=E).get_data(as_text=True))
    assert "Late, from 2026 (spent December 29, 2026)" in page and "Corrections to closed years" in page
    exp = get_zip(c, 2027)["expenses.csv"]
    assert [e_["correction of"] for e_ in exp] == ["Late, from 2026 (spent December 29, 2026)", "Late, from 2026 (spent December 12, 2026)"]

def _paid_last_december(A, c, freeze):
    flat(A, "8")
    freeze("2026-12-30")
    _book(A, c)
    pay(c, 1, "deposit", 108, "2026-12-30", "check")
    post(c, "/session/1/money", what="expense", label="Drive", miles="20", amount="15", date="2026-12-30")
    post(c, "/money/overhead", label="Lens cap", amount="12", date="2026-12-30")
    freeze("2027-02-10")
    return one(A, "SELECT id FROM payments")[0]

def test_a_closed_years_payment_or_expense_is_never_voided_or_removed(env):
    A, c, freeze = env
    pid = _paid_last_december(A, c, freeze)
    figures = year(A, 2026)
    post(c, f"/money/payment/{pid}/delete", set="void")
    assert one(A, "SELECT voided FROM payments WHERE id=?", (pid,))[0] is None
    page = text(c.get("/session/1/edit", environ_base=E).get_data(as_text=True))
    assert "That payment is in 2026, which is closed, so it stays as it is. Use Correct on it to record the change in 2027." in page
    for eid in (1, 2):
        post(c, f"/money/expense/{eid}/delete")
    assert _n(A, "expenses") == 2 and year(A, 2026) == figures
    raw = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert 'data-ui-open="fix-p%d"' % pid in raw and ">Correct<" in raw and "Undo void" not in raw

def test_a_payment_correction_lands_in_the_open_year_and_names_the_original(env):
    A, c, freeze = env
    pid = _paid_last_december(A, c, freeze)
    figures, tax26, zip26 = year(A, 2026), year(A, 2026)["tax_due"], get_zip(c, 2026)
    post(c, f"/money/payment/{pid}/correct", should_be="54", why="check bounced, half paid in cash")
    fix = A.connect().execute("SELECT * FROM payments WHERE corrects=?", (pid,)).fetchone()
    assert fix["received"] == "2027-02-10" and fix["amount"] == -54.0 and fix["kind"] == "deposit"
    assert round(fix["tax"], 2) == -4.0
    assert year(A, 2026) == figures and get_zip(c, 2026) == zip26 and year(A, 2026)["tax_due"] == tax26
    assert round(year(A, 2027)["total"], 2) == -54.0
    reconcile(A, c, 2027)
    pays = get_zip(c, 2027)["payments.csv"]
    assert pays[0]["correction of"] == "Correction of the December 30, 2026 deposit: check bounced, half paid in cash"
    page = text(c.get("/money", environ_base=E).get_data(as_text=True))
    assert "Corrections to closed years" in page and "Correction of the December 30, 2026 deposit" in page
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Corrected the $108.00 payment from Wed Dec 30, 2026%'")[0] == 1
    with A.app.test_request_context():
        assert round(A.totals(1)["paid"], 2) == 54.0
    post(c, f"/money/payment/{pid}/correct", should_be="54", why="again")
    post(c, f"/money/payment/{pid}/correct", should_be="0", why="")
    post(c, f"/money/payment/{pid}/correct", should_be="0", why="late", day="2026-12-31")
    assert one(A, "SELECT COUNT(*) FROM payments WHERE corrects=?", (pid,))[0] == 1
    post(c, f"/money/payment/{pid}/correct", should_be="0", why="never cashed")
    with A.app.test_request_context():
        assert abs(A.totals(1)["paid"]) < 0.005

def test_an_expense_correction_lands_in_the_open_year(env):
    A, c, freeze = env
    _paid_last_december(A, c, freeze)
    figures = year(A, 2026)
    post(c, "/money/expense/2/correct", should_be="0", why="returned it")
    post(c, "/money/expense/1/correct", should_be="15", miles_should_be="12", why="shorter route")
    rows = [tuple(r_) for r_ in A.connect().execute("SELECT date, amount, miles, corrects, note FROM expenses WHERE corrects IS NOT NULL ORDER BY id")]
    assert rows == [("2027-02-10", -12.0, 0.0, 2, "returned it"), ("2027-02-10", 0.0, -8.0, 1, "shorter route")]
    assert year(A, 2026) == figures
    assert round(year(A, 2027)["exp"]["a"], 2) == -12.0
    exp = get_zip(c, 2027)["expenses.csv"]
    assert exp[0]["correction of"] == "Correction of the December 30, 2026 expense Lens cap: returned it"
    reconcile(A, c, 2027)

def test_today_says_last_year_closes_in_its_last_week(env):
    A, c, freeze = env
    words = "2026 closes on or before January 31: enter any 2026 money first."
    for day, shown in (("2027-01-24", False), ("2027-01-25", True), ("2027-01-31", True), ("2027-02-01", False)):
        freeze(day)
        assert (words in text(c.get("/", environ_base=E).get_data(as_text=True))) is shown, day
    freeze("2027-01-27")
    assert 'href="/money?year=2026#year"' in c.get("/", environ_base=E).get_data(as_text=True)
    post(c, "/money/close-year", year="2026")
    assert words not in text(c.get("/", environ_base=E).get_data(as_text=True))

def test_a_print_order_payment_follows_the_same_rule(tmp_path, monkeypatch):
    import prints_env as PE
    import test_print_orders as TP
    from datetime import date
    e = PE.make(tmp_path, monkeypatch)
    oid, _total = TP.invoiced(e)
    this = date.today().year
    monkeypatch.setattr(e.A, "books_open_from", lambda: f"{this}-01-01")
    for day in (f"{this - 1}-12-30", date.today().isoformat()):
        e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": "1.00", "received": day},
                 environ_base=E)
    assert [r_["received"] for r_ in PE.q(e, "SELECT received FROM print_payments")] == [date.today().isoformat()]
    monkeypatch.setattr(e.A, "books_open_from", lambda: f"{this - 1}-01-01")
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": "1.00", "received": f"{this - 1}-12-30"},
             environ_base=E)
    assert len(PE.q(e, "SELECT received FROM print_payments")) == 2
    monkeypatch.setattr(e.A, "books_open_from", lambda: f"{this}-01-01")
    old = PE.q(e, "SELECT id FROM print_payments WHERE received=?", (f"{this - 1}-12-30",))[0]["id"]
    e.c.post(f"/prints/order/{oid}/void_payment", data={"pid": old}, environ_base=E)
    assert PE.q(e, "SELECT voided FROM print_payments WHERE id=?", (old,))[0]["voided"] is None
    with e.A.app.test_request_context():
        before = e.A.app.config["prints_year"](this - 1)
    e.c.post(f"/prints/order/{oid}/correct_payment", data={"pid": old, "should_be": "0", "why": "entered twice"},
             environ_base=E)
    fix = PE.q(e, "SELECT * FROM print_payments WHERE corrects=?", (old,))
    assert len(fix) == 1 and fix[0]["amount_cents"] == -100 and fix[0]["received"] == date.today().isoformat()
    with e.A.app.test_request_context():
        assert e.A.app.config["prints_year"](this - 1) == before
        rows = e.A.year_corrections(str(this))
    assert [r_["words"] for r_ in rows] == [f"Correction of the {'December 30, ' + str(this - 1)} print order payment: entered twice"]

def test_a_correction_gives_back_each_named_tax_in_the_open_year(env):

    import json
    from test_285_155 import TWO, taxes
    A, c, freeze = env
    taxes(A, TWO)
    freeze("2026-12-30")
    _book(A, c)
    pay(c, 1, "deposit", 270, "2026-12-30", "check")
    pid = one(A, "SELECT id FROM payments")[0]
    assert json.loads(one(A, "SELECT tax_parts FROM payments")[0]) == {"State tax": [15.0, 250.0], "City tax": [5.0, 250.0]}
    freeze("2027-02-10")
    names26 = year(A, 2026)["tax_names"]
    post(c, f"/money/payment/{pid}/correct", should_be="0", why="never cashed")
    fix = one(A, "SELECT received, tax, tax_parts FROM payments WHERE corrects=?", (pid,))
    assert fix[0] == "2027-02-10" and round(fix[1], 2) == -20.0
    assert json.loads(fix[2]) == {"State tax": [-15.0, -250.0], "City tax": [-5.0, -250.0]}
    assert year(A, 2026)["tax_names"] == names26
    assert dict(year(A, 2027)["tax_names"]) == {"State tax": -15.0, "City tax": -5.0}
    assert sorted((r["tax"], r["tax amount"]) for r in get_zip(c, 2027)["sales-tax.csv"]) == [("City tax", "-5.00"),
                                                                                               ("State tax", "-15.00")]
