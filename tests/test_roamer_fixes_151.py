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

def test_the_job_notice_never_takes_a_tap_on_a_phone(browser, env):

    import time

    import jobs
    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('setup_complete', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    jobs.JOBS["test-231"] = dict(key="test-231", label="Updating the galleries online", state="running", done=1, total=4,
                                 message="", started=time.time(), ended=None, link="")
    ctx, p = _tab(browser, c, 390)
    try:
        p.route(BASE + "/jobs/**", lambda route, request: route.fulfill(status=200, content_type="application/json",
                                                                        body='{"state": "running", "done": 1, "total": 4}'))
        p.goto(BASE + "/setup/money")
        box = p.locator("aside.jobs").bounding_box()
        assert box and box["y"] + box["height"] <= 800 and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
        assert p.evaluate("getComputedStyle(document.querySelector('aside.jobs')).pointerEvents") == "none"
        hit = p.evaluate(f"document.elementFromPoint({box['x'] + box['width'] / 2}, {box['y'] + box['height'] / 2})"
                         ".closest('aside.jobs') === null")
        assert hit
        pad = p.evaluate("parseFloat(getComputedStyle(document.querySelector('main')).paddingBottom)")
        assert pad >= box["height"], (pad, box)
    finally:
        jobs.JOBS.pop("test-231", None)
        ctx.close()

def test_settings_are_found_from_the_one_search(browser, env):

    A, c = env
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/settings")
        assert p.locator(".settabs > .sg").first.is_visible() and p.locator("#ui-find-q").count() == 0
        p.locator("nav.top .ui-nav-search").click()
        p.wait_for_url("**/search")
        p.locator("form.sui-searchbox input[name=q]").fill("deposit")
        p.locator("form.sui-searchbox input[name=q]").press("Enter")
        p.wait_for_url("**/search?q=deposit")
        assert p.locator("#setting-hits a").first.is_visible()
        _shot(p, "3-search-answers")
    finally:
        ctx.close()
    r = c.get("/settings?find=deposit", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/search?q=deposit")

def test_mileage_rate_lives_on_payments_and_terms_only(env):
    A, c = env
    general = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    assert "f-mileage_rate" not in general.split('class="setmain', 1)[1]
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'id="f-mileage_rate"' in money and 'value="0.0"' not in money
    _sql(A, "INSERT INTO settings (key, value) VALUES ('mileage_rate', '0.7') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'name="mileage_rate" step="0.001" min="0" value="0.70"' in money
    _sql(A, "UPDATE settings SET value='0.655' WHERE key='mileage_rate'")
    money = c.get("/setup/money", environ_base=E).get_data(as_text=True)
    assert 'value="0.655"' in money

def test_gallery_look_has_its_own_page_and_profile_one_save(env):
    A, c = env
    general = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True).split('class="setmain', 1)[1]
    assert 'id="gallery-look"' not in general and "Save the gallery look" not in general
    assert "Business name on agreements" in general
    look = c.get("/settings?tab=branding", environ_base=E).get_data(as_text=True)
    assert 'id="gallery-look"' in look and "Save the gallery look" in look
    nav = look.split('class="settabs', 1)[1].split("</nav>", 1)[0]
    assert "Branding" in nav and "logo, gallery font, proof mark" in nav
    assert "database, reset" in nav and "plugins, reset" not in nav

def test_business_and_time_zone_keeps_the_names_on_general_once_set_up(env):
    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('setup_complete', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    _sql(A, "INSERT INTO settings (key, value) VALUES ('business_name', 'North Light') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    _sql(A, "INSERT INTO settings (key, value) VALUES ('photographer_name', 'Sam') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    r = c.get("/setup/business", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings?tab=profile#location")
    page = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    where = page.split('id="location"', 1)[1]
    assert 'id="f-business_name"' not in where and 'id="f-timezone"' in where
    assert 'id="f-business_name"' in page.split('id="location"', 1)[0]
    r = c.post("/setup/business", data=dict(timezone="America/Denver"), environ_base=E)
    assert r.status_code in (302, 303)
    row = lambda k: _sql(A, "SELECT value FROM settings WHERE key=?", (k,))[0][0]
    assert row("business_name") == "North Light" and row("photographer_name") == "Sam"
    assert row("timezone") == "America/Denver"
