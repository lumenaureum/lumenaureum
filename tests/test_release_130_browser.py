import os
import sys
from datetime import timedelta
from urllib.parse import urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

sync_api = pytest.importorskip("playwright.sync_api")
E = {"REMOTE_ADDR": "127.0.0.1"}
BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy")

@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

@pytest.fixture
def app_(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'Lopez')")
        con.execute("""INSERT INTO people (household_id, name, role, email, phone) VALUES
                       (1, 'Maria Lopez-Fernandez', 'parent', 'maria.lopez.fernandez@example.com', '555-123-4567')""")
        con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (1, 'Ana Lopez', 'child', 1)")
        con.commit()
    finally:
        con.close()
    return A, c

def _page(browser, c, width=1280, hover=True):
    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-file-name", "x-fetch")}
        if request.method == "POST":
            r = c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E)
        else:
            r = c.get(path, headers=hdrs, environ_base=E)
        if r.status_code in (301, 302, 303):
            loc = r.location
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=not hover)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_a_timeline_day_mark_shows_its_tip_on_keyboard_focus(browser, app_):
    A, c = app_
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'inquiry', ?)",
                    ((A.today() + timedelta(days=40)).isoformat(),))
        for what in ("Called about the date", "Sent the price list", "Talked about outfits"):
            con.execute("INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (1, 1, ?, 'call', ?)",
                        (A.today().isoformat() + " 10:00:00", what))
        con.commit()
    finally:
        con.close()
    ctx, p = _page(browser, c)
    try:
        p.goto(BASE + "/c/1?s=1")
        mark = p.locator('.jm[role="button"][data-tip-lines]').first
        assert mark.count() == 1
        mark.focus()
        p.keyboard.press("Shift+Tab")
        p.keyboard.press("Tab")
        tip = p.locator("#tipbox")
        assert tip.is_visible() and "Talked about outfits" in tip.inner_text()
        p.keyboard.press("Enter")
        assert not tip.is_visible()
        p.keyboard.press(" ")
        assert tip.is_visible()
        p.keyboard.press("Escape")
        assert not tip.is_visible()
        p.keyboard.press("Enter")
        p.keyboard.press("Tab")
        assert not tip.is_visible()
    finally:
        ctx.close()

def test_each_photo_upload_announcement_is_heard(browser, app_, tmp_path):
    A, c = app_
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'shot', ?)",
                    ((A.today() - timedelta(days=2)).isoformat(),))
        con.commit()
    finally:
        con.close()
    ctx, p = _page(browser, c)
    p.add_init_script("""window.__heard = [];
      new MutationObserver(ms => ms.forEach(m => { const t = m.target.nodeType === 3 ? m.target.parentNode : m.target;
        if (t && t.id && t.id.startsWith('photos-') && t.textContent) window.__heard.push(t.id + ': ' + t.textContent); }))
      .observe(document, { subtree: true, childList: true, characterData: true });""")
    try:
        p.goto(BASE + "/c/1?s=1")
        assert p.locator('#photos-status[role="status"]').count() == 1
        assert p.locator('#photos-alert[role="alert"]').count() == 1
        assert p.locator("#photos #photos-status").count() == 0
        files = []
        for n in ("A_1.NEF", "B_2.NEF"):
            f = tmp_path / n
            f.write_bytes(b"raw " * 300)
            files.append(str(f))
        p.locator('.dropzone[data-kind="raw"] input[type=file]').set_input_files(files)
        p.wait_for_function("() => window.__heard.some(h => h.includes('2 of 2 files sealed'))", timeout=20000)
        heard = p.evaluate("window.__heard")
        want = ["Uploading 2 files, one at a time", "A_1.NEF: uploading", "A_1.NEF: sealed", "B_2.NEF: uploading",
                "B_2.NEF: sealed", "2 of 2 files sealed"]
        said = [h.split(": ", 1)[1] for h in heard]
        assert [w for w in said if w in want] == want, heard
    finally:
        ctx.close()

def test_the_client_page_has_no_sideways_scroll_on_a_phone(browser, app_):
    A, c = app_
    ctx, p = _page(browser, c, width=390, hover=False)
    try:
        p.goto(BASE + "/client/1")
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()

CONTRAST = """sel => {
  const P = s => { const m = s.match(/[\\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const over = (t, b) => ({ r: t.r * t.a + b.r * (1 - t.a), g: t.g * t.a + b.g * (1 - t.a), b: t.b * t.a + b.b * (1 - t.a), a: 1 });
  return [...document.querySelectorAll(sel)].map(el => {
    let op = 1; for (let e = el; e; e = e.parentElement) op *= Number(getComputedStyle(e).opacity);
    const bg = P(getComputedStyle(el).backgroundColor), page = P(getComputedStyle(document.body).backgroundColor);
    const fg = P(getComputedStyle(el).color);
    const seen = over({ ...over(fg, bg), a: op }, page), base = over({ ...bg, a: op }, page);
    const x = lum(seen), y = lum(base);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  });
}"""

def test_client_faces_keep_their_contrast_on_today(browser, app_):
    A, c = app_
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'booked', ?)",
                    ((A.today() + timedelta(days=3)).isoformat(),))
        con.execute("INSERT INTO documents (session_id, kind, about_person, status) VALUES (1, 'release', 2, 'needed')")
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    ctx, p = _page(browser, c)
    try:
        p.goto(BASE + "/")
        ratios = p.evaluate(CONTRAST, "span.av")
        assert ratios and min(ratios) >= 4.5, ratios
        p.goto(BASE + "/c/1?s=1")
        ratios = p.evaluate(CONTRAST, "span.av")
        assert ratios and min(ratios) >= 4.5, ratios
    finally:
        ctx.close()
