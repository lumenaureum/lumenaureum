import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
BASE = "https://app.test"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

@pytest.fixture(scope="module")
def browser():
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def _tab(browser, c, width=1280, **kw):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "sec-fetch-mode", "x-la-background", "x-la-inplace")}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        for _ in range(5):
            if r.status_code not in (301, 302, 303):
                break
            lu = urlsplit(r.location)
            r = c.get(lu.path + ("?" + lu.query if lu.query else ""), environ_base=E)
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, **kw)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def _bg(p):
    return p.evaluate("getComputedStyle(document.body).backgroundColor")

DARK_BG, LIGHT_BG = "rgb(18, 16, 14)", "rgb(239, 232, 218)"

def test_appearance_switches_the_theme_and_the_top_bar_stays_dark(browser, env):
    A, c = env
    ctx, p = _tab(browser, c)
    try:
        p.on("requestfailed", lambda r: print("FAILED REQ", r.url, r.failure)); p.on("request", lambda r: print("REQ", r.method, r.url))
        p.goto(BASE + "/settings?tab=appearance")
        assert _bg(p) == DARK_BG
        p.get_by_role("radio", name="Light", exact=True).check()
        with p.expect_navigation():
            p.get_by_role("button", name="Save").click()
        assert p.evaluate("document.documentElement.dataset.theme") == "light"
        assert _bg(p) == LIGHT_BG and p.locator(".sui-saved").is_visible()
        assert p.evaluate("getComputedStyle(document.querySelector('nav.top')).backgroundColor") == "rgb(43, 38, 32)"
        p.goto(BASE + "/clients")
        assert _bg(p) == LIGHT_BG
    finally:
        ctx.close()

@pytest.mark.parametrize("scheme, want", [("light", LIGHT_BG), ("dark", DARK_BG)])
def test_match_this_device_follows_the_browser(browser, env, scheme, want):
    A, c = env
    c.post("/settings/appearance", data={"app_theme": "auto"}, environ_base=E)
    ctx, p = _tab(browser, c, color_scheme=scheme)
    try:
        p.goto(BASE + "/")
        assert p.evaluate("document.documentElement.dataset.theme") == "auto" and _bg(p) == want
        other = "dark" if scheme == "light" else "light"
        p.emulate_media(color_scheme=other)
        assert _bg(p) == (DARK_BG if other == "dark" else LIGHT_BG)
    finally:
        ctx.close()

def _notice(c, text, refused):
    with c.session_transaction() as s:
        s["notice"] = text
        if refused:
            s["notice_refused"] = True

def test_a_refusal_is_a_banner_in_the_page_that_stays_until_dismissed(browser, env):
    A, c = env
    _notice(c, "Not saved: a sample refusal.", True)
    ctx, p = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/money")
        b = p.locator("main > .toast[data-refused]")
        assert b.is_visible() and b.get_attribute("role") == "alert"
        assert p.evaluate("getComputedStyle(document.querySelector('main > .toast')).position") == "sticky"
        assert p.evaluate("getComputedStyle(document.querySelector('main > .toast')).boxShadow") == "none"
        top = b.bounding_box()["y"]
        assert top > p.locator("nav.top").bounding_box()["height"] - 1
        p.wait_for_timeout(7500)
        assert b.is_visible()

        p.evaluate("document.querySelector('main').style.minHeight = '4000px'; scrollTo(0, 1500)")
        p.wait_for_timeout(100)
        y = b.bounding_box()["y"]
        assert 0 <= y < 120
        b.locator("[data-dismiss]").click()
        assert p.locator("main > .toast").count() == 0
    finally:
        ctx.close()

def test_a_confirmation_is_a_quiet_line_that_fades(browser, env):
    A, c = env
    _notice(c, "Test notice sent. If it doesn't arrive, check the topic in the ntfy app on your phone.", False)
    ctx, p = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/money")
        q = p.locator("main > .toast.ui-said")
        assert q.is_visible() and q.get_attribute("role") == "status" and not q.get_attribute("data-refused")
        assert q.locator("[data-dismiss]").count() == 0
        st = p.evaluate("""() => { const s = getComputedStyle(document.querySelector('main > .toast'));
            return [s.boxShadow, s.borderLeftWidth, s.position]; }""")
        assert st[0] == "none" and st[1] == "1px" and st[2] == "sticky"
        p.wait_for_timeout(7500)
        assert not q.is_visible()
    finally:
        ctx.close()

def test_with_less_motion_the_line_goes_without_fading(browser, env):
    A, c = env
    _notice(c, "Kept. Those clients stay, and this won't ask about them again.", False)
    ctx, p = _tab(browser, c, 1280, reduced_motion="reduce")
    try:
        p.goto(BASE + "/money")
        q = p.locator("main > .toast.ui-said")
        assert q.is_visible()
        assert p.evaluate("getComputedStyle(document.querySelector('main > .toast')).animationDuration") == "0.01s"
        p.wait_for_timeout(8600)
        assert not q.is_visible()
    finally:
        ctx.close()

def test_the_idle_warning_is_a_modal_dialog(browser, env):
    A, c = env
    import auth
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('idle_signout_hours', '1') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit()
    con.close()
    with c.session_transaction() as s:
        sid = s["sid"]
    ctx, p = _tab(browser, c, 1280)
    try:
        p.clock.install()
        p.goto(BASE + "/settings")
        d = p.locator("dialog[data-idle-warn]")
        assert d.is_hidden()
        auth.LAST_SEEN[sid] = time.time() - 3600 + 100
        before = auth.LAST_SEEN[sid]
        p.clock.run_for(3600 * 1000 - 119 * 1000)
        d.wait_for(state="visible")
        assert p.evaluate("document.querySelector('dialog[data-idle-warn]').matches(':modal')")
        assert p.evaluate("document.activeElement.textContent.trim()") == "Stay signed in"
        p.keyboard.press("Tab")
        p.keyboard.press("Tab")
        assert p.evaluate("!!document.activeElement.closest('dialog') || document.activeElement === document.body")
        p.keyboard.press("Escape")
        d.wait_for(state="hidden")
        assert auth.LAST_SEEN[sid] == before
        p.evaluate("document.querySelector('dialog[data-idle-warn]').showModal()")
        d.get_by_role("button", name="Stay signed in").click()
        d.wait_for(state="hidden")
        assert auth.LAST_SEEN[sid] > before
    finally:
        ctx.close()
