import os
import sys

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
    c = signed_in_client(A)
    return A, c

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

def _tab(browser, c, width=1280):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-la-background", "x-la-inplace")}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303):
            loc = r.location.replace("https://localhost", "")
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def _notice(c, text="A date before 2000 isn't one this can use. Nothing was changed."):
    with c.session_transaction() as s:
        s["notice"] = text
        s["notice_refused"] = True

def _open(p, q, width):
    if width < 500:
        q.tap()
    else:
        q.click()
        p.mouse.move(5, 790)

def _press(p, loc, width):
    if width < 500:
        loc.tap()
    else:
        loc.click()

def _pop(q):
    return q.locator(".qh-pop").is_visible()

def _page(browser, c, width, notice=True):
    if notice:
        _notice(c)
    ctx, p = _tab(browser, c, width)
    p.goto(BASE + "/money")
    q = p.locator(".qh:visible").first
    q.scroll_into_view_if_needed()
    return ctx, p, q

def _toast(p):
    return p.locator(".toast[data-refused]")

W = [390, 1280]

@pytest.mark.parametrize("width", W)
def test_dismissing_the_notice_leaves_an_open_bubble_open(browser, env, width):
    A, c = env
    ctx, p, q = _page(browser, c, width)
    try:
        _open(p, q, width)
        assert _pop(q) and _toast(p).is_visible()
        _press(p, _toast(p).locator("[data-dismiss]"), width)
        assert _toast(p).count() == 0
        assert _pop(q)
        assert q.get_attribute("aria-expanded") == "true"
    finally:
        ctx.close()

@pytest.mark.parametrize("width", W)
def test_tapping_the_notice_text_leaves_the_bubble_open(browser, env, width):
    A, c = env
    ctx, p, q = _page(browser, c, width)
    try:
        _open(p, q, width)
        box = _toast(p).bounding_box()
        if width < 500:
            p.touchscreen.tap(box["x"] + 12, box["y"] + box["height"] / 2)
        else:
            p.mouse.click(box["x"] + 12, box["y"] + box["height"] / 2)
        assert _pop(q) and _toast(p).is_visible()
    finally:
        ctx.close()

@pytest.mark.parametrize("width", W)
def test_a_notice_or_note_appearing_and_leaving_leaves_the_bubble_open(browser, env, width):

    A, c = env
    ctx, p, q = _page(browser, c, width, notice=False)
    try:
        _open(p, q, width)
        p.evaluate("""() => { document.querySelector('[data-idle-warn]').showModal();
            const n = document.createElement('div'); n.className = 'ui-flash'; n.innerHTML = '<span class="ui-note ui-note-ok">Saved</span>';
            document.querySelector('main').prepend(n); }""")
        assert _pop(q)
        p.evaluate("""() => { document.querySelector('[data-idle-warn]').close(); document.querySelector('.ui-flash').remove(); }""")
        assert _pop(q)
    finally:
        ctx.close()

@pytest.mark.parametrize("width", W)
def test_opening_and_closing_a_bubble_leaves_the_notice(browser, env, width):
    A, c = env
    ctx, p, q = _page(browser, c, width)
    try:
        _open(p, q, width)
        assert _pop(q) and _toast(p).is_visible()
        _press(p, q.locator(".qh-pop"), width)
        assert _pop(q) and _toast(p).is_visible()
        _open(p, q, width)
        assert not _pop(q) and _toast(p).is_visible()
    finally:
        ctx.close()

@pytest.mark.parametrize("width", W)
def test_escape_closes_the_bubble_only(browser, env, width):
    A, c = env
    ctx, p, q = _page(browser, c, width)
    try:
        _open(p, q, width)
        p.keyboard.press("Escape")
        assert not _pop(q) and q.get_attribute("aria-expanded") == "false"
        assert _toast(p).is_visible()
    finally:
        ctx.close()

@pytest.mark.parametrize("width", W)
def test_a_tap_elsewhere_closes_the_bubble_only(browser, env, width):
    A, c = env
    ctx, p, q = _page(browser, c, width)
    try:
        _open(p, q, width)
        h = p.locator("main h1, main h2").first
        _press(p, h, width)
        assert not _pop(q)
        assert _toast(p).is_visible()
    finally:
        ctx.close()

def test_tab_opens_the_bubble_and_tab_away_closes_it(browser, env):
    A, c = env
    ctx, p, q = _page(browser, c, 1280)
    try:
        p.evaluate("""() => { const all = [...document.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
            .filter(e => e.offsetParent && e.tabIndex >= 0); const q = [...document.querySelectorAll('.qh')].find(x => x.offsetParent);
            all[all.indexOf(q) - 1].focus(); }""")
        p.mouse.move(5, 790)
        p.keyboard.press("Tab")
        assert p.evaluate("() => document.activeElement.classList.contains('qh')")
        assert _pop(q)
        p.keyboard.press("Enter")
        assert not _pop(q)
        p.keyboard.press("Enter")
        assert _pop(q)
        p.keyboard.press("Tab")
        assert not _pop(q) and _toast(p).is_visible()
    finally:
        ctx.close()

def test_an_open_bubble_is_never_under_the_notice(browser, env):

    A, c = env
    ctx, p, q = _page(browser, c, 1280)
    try:
        t = _toast(p).bounding_box()
        y = p.evaluate("() => [...document.querySelectorAll('.qh')].find(x => x.offsetParent).getBoundingClientRect().top + scrollY")
        p.evaluate(f"() => scrollTo({{top: {y} - {t['y']} + 10, behavior: 'instant'}})")
        p.evaluate("() => { const q = [...document.querySelectorAll('.qh')].find(x => x.offsetParent); "
                   "q.dispatchEvent(new MouseEvent('click', {bubbles: true})); }")
        p.mouse.move(5, 790)
        assert _pop(q)
        top = p.evaluate("""() => { const r = document.querySelector('.qh.qh-open .qh-pop').getBoundingClientRect();
            const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return !!(at && at.closest('.qh-pop')); }""")
        assert top
        assert _toast(p).count() == 1
    finally:
        ctx.close()

def test_a_bubble_without_script_still_opens_on_focus():

    css = open(os.path.join(os.path.dirname(__file__), "..", "static", "style.css")).read()
    assert ".qh:hover .qh-pop, .qh:focus .qh-pop, .qh:focus-within .qh-pop { display: block; }" in css
    assert ".qh-js .qh:not(.qh-open) .qh-pop { display: none; }" in css
    doc = open(os.path.join(os.path.dirname(__file__), "..", "templates", "_doc_style.html")).read()
    assert ".qh-js .qh:not(.qh-open) .qh-pop { display: none; }" in doc
