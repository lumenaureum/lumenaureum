import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import E, env, flat, household, line, one, pay, post, run, session

def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def blocked(A, sid=1):
    with A.app.test_request_context():
        return A.release_blocked(sid)

def paid_in_full(A, c, freeze, price=350):
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", price, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    pay(c, 1, "balance", price - 100, "2026-06-02")

def test_paid_in_full_then_a_partial_refund_releases_delivery(env):

    A, c, freeze = env
    paid_in_full(A, c, freeze)
    pay(c, 1, "refund", 40, "2026-07-10")
    page = text(c.get("/session/1/edit", environ_base=E).get_data(as_text=True))
    assert "$40.00 refunded, not owed again" in page
    assert blocked(A) == 0

def test_a_partial_refund_with_sales_tax_releases_delivery(env):
    A, c, freeze = env
    flat(A, "6")
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 300)
    pay(c, 1, "balance", 318, "2026-06-02")
    pay(c, 1, "refund", 53, "2026-07-10")
    assert blocked(A) == 0

def test_underpaid_still_blocks_delivery(env):
    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    assert blocked(A) == 250

def test_tips_never_count_toward_release(env):
    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    pay(c, 1, "tip", 250, "2026-06-02")
    assert blocked(A) == 250

def test_a_refund_that_leaves_money_owed_still_blocks(env):

    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 200, "2026-05-01")
    pay(c, 1, "refund", 50, "2026-06-10")
    with A.app.test_request_context():
        due = A.totals(1)["due"]
    assert blocked(A) == round(due, 2) == 150

def test_a_full_refund_still_never_releases_delivery(env):

    A, c, freeze = env
    paid_in_full(A, c, freeze)
    pay(c, 1, "refund", 350, "2026-07-10")
    assert blocked(A) == 350

def _doc(c, kind):
    post(c, f"/session/1/{kind}/issue")
    return text(c.get(f"/session/1/{kind}", environ_base=E).get_data(as_text=True))

def test_a_receipt_issued_after_a_refund_lists_the_credit(env):

    A, c, freeze = env
    paid_in_full(A, c, freeze, price=375)
    pay(c, 1, "refund", 25, "2026-07-10")
    doc = _doc(c, "receipt")
    assert "Total $375" in doc and "Total paid $350" in doc
    assert "Refunded, not owed again $25" in doc

def test_an_invoice_issued_after_a_refund_lists_the_credit(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze, price=375)
    pay(c, 1, "refund", 25, "2026-07-10")
    doc = _doc(c, "invoice")
    assert "Total $375" in doc and "Refunded, not owed again $25" in doc and "Balance due $0" in doc

def test_a_refund_of_an_overpayment_is_no_credit_line(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze, price=375)
    pay(c, 1, "balance", 20, "2026-06-03")
    pay(c, 1, "refund", 20, "2026-07-10")
    doc = _doc(c, "receipt")
    assert "Total paid $375" in doc and "not owed again" not in doc

def _header(c):

    page = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    head = page.split('id="money"', 1)[1].split('<table class="ui-money" aria-label="Summary">', 1)[1].split("</table>", 1)[0]
    return text(head)

def test_a_canceled_booking_header_says_what_the_money_panel_says(env):

    A, c, freeze = env
    flat(A, "6")
    freeze("2026-05-15")
    household(A, 1, "Hughes")
    session(A, 1, 1, "quoted", "2026-09-01")
    line(c, 1, "Session", 250)
    run(A, """INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-04-01', '{"deposit": 100, "total": 265}')""")
    post(c, "/session/1/stage", stage="booked")
    pay(c, 1, "deposit", 75, "2026-05-01")
    pay(c, 1, "balance", 100, "2026-05-02")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    head = _header(c)
    assert "Refund due $75" in head and "Balance" not in head
    pay(c, 1, "refund", 75, "2026-05-10")
    head = _header(c)
    assert "Deposit kept $100" in head and "Balance" not in head and "$165" not in head


