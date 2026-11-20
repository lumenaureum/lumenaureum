import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env

ITEMS = ["Today", "Clients", "Sessions", "Calendar", "Finances", "Search", "Settings", "+ Contact", "+ Inquiry", "Sign out"]
PAGES = ["/", "/clients", "/sessions", "/money"]

def _inquiry(A):
    con = A.connect()
    try:
        con.execute("INSERT INTO studio_inquiry_review (sha256, fields, received) VALUES ('t1', '{\"name\": \"Web Visitor\"}', '2026-11-18T09:00')")
        con.commit()
    finally:
        con.close()

def _visible_in_bar(p, label):

    return p.evaluate("""([label, w]) => {
      const bar = document.querySelector('nav.top');
      const els = [...bar.querySelectorAll('a, button, input, summary')].filter(e => !e.closest('.ui-more-pop'));
      const hit = els.find(e => {
        const t = (e.getAttribute('aria-label') || '').trim(), x = (e.textContent || '').trim(), ph = e.getAttribute('placeholder') || '';
        return x === label || t === label || ph === label || (label === 'Settings' && e.matches('a.gear'));
      });
      if (!hit || !hit.checkVisibility()) return null;
      const r = hit.getBoundingClientRect();
      return r.width > 0 && r.left >= -0.5 && r.right <= w + 0.5 ? [r.left, r.right] : null;
    }""", [label, p.viewport_size["width"]])

def test_the_phone_bar_shows_the_main_three_and_more_holds_the_rest(env):
    A, c = env
    page = c.get("/", environ_base=E).get_data(as_text=True)
    bar = page.split('<nav class="top', 1)[1].split("</nav>\n<main", 1)[0]
    more = bar.split('class="ui-more ui-nav-more"', 1)[1]
    for label in ("Calendar", "Finances", "Settings", "+ Contact", "+ Inquiry", "Sign out"):
        assert f">{label}</" in more, label
    assert 'placeholder="Search"' not in more
    assert 'class="ui-nav-search' in bar.split('class="ui-more ui-nav-more"', 1)[0]
    for label in ("Today", "Clients", "Sessions"):
        assert f">{label}</a>" in bar.split('class="ui-more ui-nav-more"', 1)[0]
        assert f">{label}</a>" not in more

@pytest.mark.parametrize("width", [390, 1280, 820, 1024])
def test_every_nav_item_is_visible_or_reachable(browser, env, width):
    A, c = env
    _inquiry(A)
    ctx, p = _tab(browser, c, width=width)
    try:
        for path in PAGES:
            p.goto(BASE + path)
            assert p.evaluate("document.documentElement.scrollWidth") <= width, path
            bar = p.locator("nav.top").bounding_box()
            assert bar["x"] >= 0 and bar["x"] + bar["width"] <= width + 0.5

            right = p.evaluate("Math.max(...[...document.querySelectorAll('nav.top > *, nav.top nav.sections > *')]"
                               ".filter(e => e.checkVisibility() && !e.closest('.ui-more-pop')).map(e => e.getBoundingClientRect().right))")
            assert right <= width + 0.5, (path, right)
            assert p.locator("nav.top a.ui-inbox").is_visible(), "the badge is always on screen"
            if width > 760:
                for label in ITEMS:
                    assert _visible_in_bar(p, label), (path, label)
                assert not p.locator("nav.top .ui-nav-more").is_visible()
                if width >= 1280:
                    tops = p.evaluate("[...document.querySelectorAll('nav.top > *')].filter(e => e.checkVisibility())"
                                      ".map(e => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; })")
                    assert max(tops) - min(tops) < 20, tops
            else:
                for label in ("Today", "Clients", "Sessions"):
                    assert _visible_in_bar(p, label), (path, label)
                more = p.locator("nav.top .ui-nav-more > summary")
                assert more.is_visible()
                more.click()
                pop = p.locator("nav.top .ui-nav-more .ui-more-pop")
                box = pop.bounding_box()
                assert box["x"] >= -0.5 and box["x"] + box["width"] <= width + 0.5, box
                for label in ("Calendar", "Finances", "Settings", "+ Contact", "+ Inquiry", "Sign out"):
                    assert pop.get_by_text(label, exact=True).is_visible(), (path, label)

                assert not pop.get_by_placeholder("Search").is_visible()
                more.click()
                assert p.locator("nav.top a.ui-nav-search").is_visible(), (path, "the search button")

        if width <= 760:
            p.goto(BASE + "/")
            p.locator("nav.top .ui-nav-more > summary").click()
            p.locator("nav.top .ui-nav-more .ui-more-pop").get_by_text("Finances", exact=True).click()
            p.wait_for_url(BASE + "/money")
            p.locator("nav.top a.ui-nav-search").click()
            p.wait_for_url(BASE + "/search")
    finally:
        ctx.close()
