import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_settings_fixes_15 import BASE, E, _sql, browser, env

def _tab(browser, c, width=390):

    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-la-background", "x-la-inplace")}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303):
            loc = r.location.replace("https://localhost", "").replace("http://localhost", "")
            if request.method == "POST" and loc.startswith("/"):
                r = c.get(loc, environ_base=E)
            else:
                route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
                return
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

SHOTS = os.environ.get("LA_SHOTS")

def _shot(p, name):
    if SHOTS:
        p.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=False)

def test_saved_is_in_view_after_saving_at_the_bottom_of_a_phone_page(browser, env):
    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('setup_complete', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/setup/money")
        p.locator("#f-deposit").fill("150")
        p.add_style_tag(content="aside.jobs { display: none !important; }")
        btn = p.locator(".sbar button").last
        btn.scroll_into_view_if_needed()
        assert p.evaluate("scrollY") > 400
        btn.click()
        p.wait_for_load_state()
        p.wait_for_timeout(200)
        note = p.locator(".ok-msg").first
        box = note.bounding_box()
        _shot(p, "1-saved-in-view")
        assert box and 0 <= box["y"] < 800, box
        assert p.locator("#f-deposit").input_value() == "150"
    finally:
        ctx.close()

def test_a_refused_save_is_in_view_too(browser, env):
    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('setup_complete', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/setup/money")
        p.evaluate("document.getElementById('f-deposit').removeAttribute('min')")
        p.locator("#f-deposit").fill("-5")
        p.add_style_tag(content="aside.jobs { display: none !important; }")
        btn = p.locator(".sbar button").last
        btn.scroll_into_view_if_needed()
        btn.click()
        p.wait_for_load_state()
        p.wait_for_timeout(200)
        err = p.locator(".err").first
        _shot(p, "2-refusal-in-view")
        assert "negative" in err.inner_text()
        box = err.bounding_box()
        assert box and 0 <= box["y"] and box["y"] + box["height"] <= 800, box
    finally:
        ctx.close()

def test_search_answers_replace_the_settings_list(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/settings")
        groups = p.locator(".settabs > .sg").first
        assert groups.is_visible()
        p.locator("#ui-find-q").fill("deposit")
        assert p.locator(".ui-find-out a").first.is_visible()
        _shot(p, "3-search-answers")
        assert not groups.is_visible()
        p.locator("#ui-find-q").fill("xyzzy")
        assert "No setting matches" in p.locator(".ui-find-out").inner_text()
        assert not groups.is_visible()
        p.locator("#ui-find-q").fill("")
        p.locator("#ui-find-q").dispatch_event("input")
        assert groups.is_visible()
        _shot(p, "4-settings-list")
        assert p.locator("button", has_text="Find the setting").count() == 0
    finally:
        ctx.close()
    html = c.get("/settings?find=deposit", environ_base=E).get_data(as_text=True)
    assert ".settabs:has(.ui-find-out:not(:empty)) > .sg" in open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "style.css")).read()
    assert 'class="ui-find-out"' in html

def test_mileage_rate_lives_on_payments_and_terms_only(env):
    A, c = env
    general = c.get("/settings?tab=general", environ_base=E).get_data(as_text=True)
    assert "f-mileage_rate" not in general.split('class="setmain', 1)[1]
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'id="f-mileage_rate"' in money and 'value="0.0"' not in money
    _sql(A, "INSERT INTO settings (key, value) VALUES ('mileage_rate', '0.7') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'name="mileage_rate" step="0.001" min="0" value="0.70"' in money
    _sql(A, "UPDATE settings SET value='0.655' WHERE key='mileage_rate'")
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'value="0.655"' in money

def test_gallery_look_has_its_own_page_and_general_one_save(env):
    A, c = env
    general = c.get("/settings?tab=general", environ_base=E).get_data(as_text=True).split('class="setmain', 1)[1]
    assert 'id="gallery-look"' not in general and "Save the gallery look" not in general
    assert "Business name on agreements" in general
    look = c.get("/settings?tab=gallery", environ_base=E).get_data(as_text=True)
    assert 'id="gallery-look"' in look and "Save the gallery look" in look
    nav = look.split('class="settabs', 1)[1].split("</nav>", 1)[0]
    assert "Gallery look" in nav and "font, background, proof mark" in nav
    assert "database, reset" in nav and "plugins, reset" not in nav

def test_business_and_time_zone_keeps_the_names_on_general_once_set_up(env):
    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('setup_complete', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    _sql(A, "INSERT INTO settings (key, value) VALUES ('business_name', 'North Light') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    _sql(A, "INSERT INTO settings (key, value) VALUES ('photographer_name', 'Sam') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    page = c.get("/setup/business", environ_base=E).get_data(as_text=True)
    assert 'id="f-business_name"' not in page and 'id="f-timezone"' in page
    assert "are on" in page and "tab=general" in page
    r = c.post("/setup/business", data=dict(timezone="America/Denver"), environ_base=E)
    assert r.status_code in (302, 303)
    row = lambda k: _sql(A, "SELECT value FROM settings WHERE key=?", (k,))[0][0]
    assert row("business_name") == "North Light" and row("photographer_name") == "Sam"
    assert row("timezone") == "America/Denver"
