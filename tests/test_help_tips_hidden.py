import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, _tab, browser, env

SHOTS = os.environ.get("LA_SHOTS")

@pytest.mark.parametrize("width", [390, 1280])
def test_new_contact_help_tips_are_closed_until_hovered(browser, env, width):
    A, c = env
    ctx, page = _tab(browser, c, width=width)
    try:
        page.goto(BASE + "/contact/new")
        pops = page.locator(".ui-form-grid .qh-pop")
        assert pops.count() >= 2
        assert all(not pops.nth(i).is_visible() for i in range(pops.count()))
        for i in range(page.locator(".ui-form-grid .qh").count()):
            assert page.locator(".ui-form-grid .qh").nth(i).evaluate("e => getComputedStyle(e).display") == "inline-flex"
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, f"contact-new-{width}.png"), full_page=True)
        page.locator(".ui-form-grid .qh").first.hover() if width > 500 else page.locator(".ui-form-grid .qh").first.tap()
        assert pops.first.is_visible()
        assert sum(pops.nth(i).is_visible() for i in range(pops.count())) == 1
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, f"contact-new-{width}-open.png"), full_page=True)
    finally:
        ctx.close()

def test_a_tip_in_a_heading_reads_as_a_sentence_not_in_capitals(browser, env):

    A, c = env
    ctx, page = _tab(browser, c, width=1280)
    try:
        page.goto(BASE + "/settings?tab=branding")
        q = page.locator("h2.ui-sec-title .qh").first
        q.hover()
        pop = page.locator("h2.ui-sec-title .qh .qh-pop").first
        assert pop.is_visible()
        assert pop.evaluate("e => getComputedStyle(e).textTransform") == "none"
        assert pop.evaluate("e => getComputedStyle(e).fontWeight") == "400"
    finally:
        ctx.close()
