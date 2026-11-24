import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, _tab, browser, env

def test_the_rate_box_is_where_a_tax_is_added(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/setup/tax")
        assert p.get_by_text("No taxes: quotes and invoices carry none.").is_visible()
        assert p.locator("#add-tax").text_content().strip() == "Add your tax"
        assert p.locator("#f-new_tax_rate").is_visible()
        assert p.locator("#f-tax_rate_0").count() == 0
    finally:
        ctx.close()
