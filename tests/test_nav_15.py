import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env

ITEMS = ["Today", "Clients", "Sessions", "Calendar", "Finances", "Search", "Settings", "+ Contact", "+ Inquiry", "Sign out"]
ALWAYS = ["Today", "Clients", "Sessions", "Search", "+ Contact"]
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
                _desktop_checks(p, path, ITEMS)
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

def _one_row(p):
    mid = p.evaluate("[...document.querySelectorAll('nav.top > *, nav.top nav.sections > a')].filter(e => e.checkVisibility())"
                     ".map(e => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; })")
    return max(mid) - min(mid) < 20, mid

def _desktop_checks(p, path, items):

    ok, mid = _one_row(p)
    assert ok, (path, mid)
    for label in ALWAYS:
        assert _visible_in_bar(p, label), (path, label)
    folded = [label for label in items if not _visible_in_bar(p, label)]
    more = p.locator("nav.top .ui-nav-more > summary")
    if not folded:
        assert not more.is_visible(), path
        return
    assert more.is_visible(), (path, folded)
    more.click()
    pop = p.locator("nav.top .ui-nav-more .ui-more-pop")
    for label in folded:
        assert pop.get_by_text(label, exact=True).is_visible(), (path, label)
    for label in items:
        if label not in folded and label != "Search":
            assert not pop.get_by_text(label, exact=True).is_visible(), (path, "in the bar AND in More", label)
    more.click()

def _equipment_on(A):
    con = A.connect()
    try:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_equipment', '1')")
        con.commit()
    finally:
        con.close()

@pytest.mark.parametrize("width", [1024, 1280, 1440])
def test_a_plugin_section_never_spills_the_desktop_bar(browser, env, width):

    A, c = env
    _inquiry(A)
    _equipment_on(A)
    ctx, p = _tab(browser, c, width=width)
    try:
        p.goto(BASE + "/")
        items = ITEMS + ["Equipment"]
        _desktop_checks(p, "/", items)
        if not _visible_in_bar(p, "Equipment"):
            p.locator("nav.top .ui-nav-more > summary").click()
            p.locator("nav.top .ui-nav-more .ui-more-pop").get_by_text("Equipment", exact=True).click()
            p.wait_for_url("**/equipment**")
        else:
            p.goto(BASE + "/equipment")
        assert _visible_in_bar(p, "Equipment"), "the section you are on stays in the bar"
        _desktop_checks(p, "/equipment", items)

        p.set_viewport_size({"width": 900, "height": 800})
        p.wait_for_timeout(150)
        _desktop_checks(p, "/equipment (900px)", items)
        p.set_viewport_size({"width": width, "height": 800})
        p.wait_for_timeout(150)
        _desktop_checks(p, "/equipment (back)", items)
    finally:
        ctx.close()
