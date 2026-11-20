import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, _tab, browser, env

def test_the_rate_shows_only_once_a_rate_is_picked(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/setup/tax")
        p.check('input[name=tax_mode][value=none]')
        assert not p.locator("#f-sales_tax_pct").is_visible()
        p.check('input[name=tax_mode][value=flat]')
        assert p.locator("#f-sales_tax_pct").is_visible()
    finally:
        ctx.close()
