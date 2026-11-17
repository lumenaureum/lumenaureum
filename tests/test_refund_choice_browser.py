import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_release_130_browser import BASE, _page, app_, browser

def _checked(page):
    return page.eval_on_selector("[data-after-refund]", "b => (b.querySelector('input:checked') || {}).value")

def test_the_refund_choice_follows_the_amount_until_picked_by_hand(browser, app_):
    A, c = app_
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'shot', '2026-06-01')")
        con.execute("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 350, 'custom', 0)")
        con.execute("""INSERT INTO payments (session_id, kind, amount, received, method, tax, taxable)
                       VALUES (1, 'balance', 350, '2026-06-02', 'cash', 0, 0)""")
        con.commit()
    finally:
        con.close()
    ctx, page = _page(browser, c)
    try:
        page.goto(BASE + "/session/1")
        box = page.locator("[data-after-refund]")
        assert box.is_hidden()
        form = page.locator("form:has([data-after-refund])")
        form.locator("label:has(input[name=kind][value=refund])").click()
        assert box.is_visible() and "After this refund, the gallery:" in box.inner_text()
        amount = form.locator("input[name=amount]")
        amount.fill("40")
        assert _checked(page) == "release"
        amount.fill("350")
        assert _checked(page) == "hold"
        box.locator("label:has(input[value=release])").click()
        amount.fill("349")
        amount.fill("350")
        assert _checked(page) == "release"
        form.locator("label:has(input[name=kind][value=balance])").click()
        assert box.is_hidden()
    finally:
        ctx.close()
