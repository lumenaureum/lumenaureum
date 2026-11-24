import io
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, run

E = {"REMOTE_ADDR": "127.0.0.1"}
TPL = ROOT / "templates"
UI_JS = (ROOT / "static" / "ui.js").read_text()

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _setting(A, k):
    con = A.connect()
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    con.close()
    return r[0] if r else None

def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()

def test_the_look_says_unsaved_changes_and_what_saving_does(env):
    A, c = env
    page = _page(c, "/settings?tab=branding")
    form = page.split('id="gallery-look"', 1)[1].split("</form>", 1)[0]
    assert "Save the gallery look</button>" in form
    assert 'data-ui-unsaved hidden role="status">Unsaved changes<button type="reset" class="ui-btn ui-btn-quiet">Discard changes</button>' in form
    assert "keep theirs until you rebuild them" in form

def test_every_upload_beside_the_look_saves_the_look_first_and_says_so(env):
    A, c = env
    page = _page(c, "/settings?tab=branding")
    logo = page.split('id="logo"', 1)[1].split("</div>\n</div>", 1)[0]
    assert 'data-ui-save-first="#gallery-look"' in page.split('id="logo"', 1)[0][-200:] + page.split('id="logo"', 1)[1][:80]
    assert 'data-ui-dirty-label="Save the look and upload the logo">Upload the logo</button>' in logo
    font = page.split('id="own-font"', 1)[1].split('id="gl-bg-l"', 1)[0]
    assert 'id="own-font" data-ui-save-first="#gallery-look"' in page
    assert 'data-ui-dirty-label="Save the look and upload the font">Upload the font</button>' in font
    dark = page.split('id="gl-dark"', 1)[1][:1200]
    assert 'data-ui-dirty-label="Save the look and upload the logo">Upload the dark logo</button>' in dark

    assert "look_posted" not in (ROOT / "static" / "gallery_look.js").read_text()

def test_the_credit_line_is_a_switch_that_saves_on_its_own(env):
    A, c = env
    page = _page(c, "/settings?tab=branding")
    form = page.split('id="credit"', 1)[0].rsplit("<form", 1)[1] + page.split('id="credit"', 1)[1].split("</form>", 1)[0]
    assert "data-ui-autosave" in form and "data-no-keep" in form
    assert "<noscript>" in form and "Save the credit line" in form
    c.post("/settings?tab=branding", data=dict(credit_posted="1", form_id="credit"), environ_base=E)
    assert _setting(A, "credit_off") == "1"
    c.post("/settings?tab=branding", data=dict(credit_posted="1", form_id="credit", credit="1"), environ_base=E)
    assert _setting(A, "credit_off") == ""

@pytest.fixture
def studio(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    from helpers import fresh_modules, signed_in_client
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    for k, v in (("photographer_name", "Pat Lens"), ("business_email", "hello@example.com"),
                 ("gallery_base_url", "https://www.example.com/g/")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit(); con.close()
    web = tmp_path / "web"
    (web / "g").mkdir(parents=True)
    (web / "g" / ".vault-root").write_text("")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(web / "g")}
    VW.write_config(cfg)
    c.post("/settings/studio-page/switch", data={"use": "1"}, environ_base=E)
    return A, c, web

def test_studio_page_words_say_unsaved_and_the_ticks_save_apart(studio):
    A, c, web = studio
    page = _page(c, "/settings/studio-page")
    words = page.split('id="words"', 1)[1].split("</form>", 1)[0]
    assert "Save the words</button>" in words and "data-ui-unsaved" in words
    for n in ("site_show_phone", "site_show_address", "site_inquiries", "site_llms", "site_mark"):
        assert re.search(r'form="sp-show" data-no-keep name="%s"' % n, words), n
    assert '<form method="post" action="/settings/studio-page/show" id="sp-show" data-ui-autosave hidden></form>' in page
    use = page.split('action="/settings/studio-page/switch"', 1)[1].split("</form>", 1)[0]
    assert "data-ui-autosave data-ui-autosave-reload" in page and 'id="sp-use"' in use and "data-autosubmit" not in use

    c.post("/settings/studio-page/show", data={"site_show_phone": "1", "site_llms": "1"}, environ_base=E)
    assert _setting(A, "site_show_address") == "0" and _setting(A, "site_show_phone") == "1"
    c.post("/settings/studio-page/save", data={"shows_apart": "1", "site_about": "Words."}, environ_base=E)
    assert _setting(A, "site_show_phone") == "1" and _setting(A, "site_llms") == "1" and _setting(A, "site_about") == "Words."
    c.post("/settings/studio-page/save", data={"site_about": "Words."}, environ_base=E)
    assert _setting(A, "site_show_phone") == "0"

def test_studio_page_upload_saves_first_and_preview_asks(studio):
    A, c, web = studio
    page = _page(c, "/settings/studio-page")
    assert '<div data-ui-save-first="#words, #photos form[data-sp-desc], #sp-widget-sites">' in page
    pub = page.split('id="publish"', 1)[1]
    assert 'data-ui-dirty-label="Save and put online">Upload</button>' in pub
    prev = pub.split('action="/settings/studio-page/preview"', 1)[1].split(">", 1)[0]
    assert 'data-ui-save-first="#words, #photos form[data-sp-desc], #sp-widget-sites" data-ui-save-first-ask' in prev

def test_the_gallery_block_saves_a_typed_codeword_before_any_action():
    src = (TPL / "_gallery_block.html").read_text()
    assert '<div id="gallery" class="ui-gal" data-ui-part data-ui-save-first="#f-codeword-{{ s.id }} form">' in src
    for words in ("Save and build the gallery", "Save and put online", "Save and put the proofs online",
                  "Save and put online before payment", "Save and build from the folder"):
        assert words in src, words
    assert src.count("dirty='Save and put online'") == 3
    prints = src.split("prints_gallery_switch", 1)[1].split("</form>", 1)[0]
    assert "data-ui-autosave" in prints and "data-autosubmit" not in prints and "Save the prints choice" in prints

def test_the_web_host_step_says_unsaved_and_its_checks_ask_for_a_save(env):
    A, c = env
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    page = _page(c, "/setup/galleries")
    form = page.split('id="f"', 1)[1].split("</form>", 1)[0]
    assert "data-ui-unsaved" in form
    src = (TPL / "setup.html").read_text()
    for ep in ("setup_host_check", "setup_web_cert_trust", "setup_web_cert_refuse", "setup_web_cert_check"):
        for m in re.finditer(r"formaction=\"\{\{ url_for\('%s'\) \}\}\"[^>]*>" % ep, src):
            assert 'data-ui-save-first="#f" data-ui-save-first-ask' in m.group(0), ep

def test_no_typed_field_on_these_pages_saves_as_it_is_typed(env, studio):

    srcs = [(TPL / n).read_text() for n in ("settings.html", "_gallery_block.html", "setup.html", "_settings_ui.html")]
    from plugins import studio_page as S
    srcs.append(S.SETTINGS_PAGE)
    for s in srcs:
        for tag in re.findall(r"<(?:input|textarea|select)\b[^>]*data-autosubmit[^>]*>", s):
            assert re.search(r'type="(checkbox|radio|file)"', tag), tag

HARNESS = """<!doctype html><html><body><main>
<form id="look" method="post" action="/look"><input name="word" value="PROOF">
  <button class="ui-btn ui-btn-primary">Save the look</button>
  <span class="sui-unsaved" data-ui-unsaved hidden role="status">Unsaved changes<button type="reset" class="ui-btn ui-btn-quiet">Discard changes</button></span></form>
<div data-ui-save-first="#look"><form id="up" method="post" action="/upload" data-confirm="Put it online?"><button id="upb" data-ui-dirty-label="Save and put online">Upload</button></form></div>
<form id="pv" method="post" action="/preview" data-ui-save-first="#look" data-ui-save-first-ask><button id="pvb">Preview</button></form>
<form id="sw" method="post" action="/switch" data-ui-autosave><label><input type="checkbox" id="tick" name="on" value="1" data-no-keep>On</label></form>
</main><script>%s</script></body></html>"""

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

def _harness(browser, refuse=()):

    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    sent = []

    def handle(route, request):
        path = request.url.split("https://h.test", 1)[1]
        if request.method == "POST":
            sent.append((path, request.post_data or ""))
            toast = '<div class="toast" role="status" data-refused>The word is too long.</div>' if path in refuse else ""
            route.fulfill(status=200, body=HARNESS.replace("<main>", "<main>" + toast) % UI_JS, headers={"content-type": "text/html"})
        else:
            route.fulfill(status=200, body=HARNESS % UI_JS, headers={"content-type": "text/html"})
    ctx.route("https://h.test/**", handle)
    p = ctx.new_page()
    p.on("dialog", lambda d: d.accept())
    p.goto("https://h.test/page")
    return ctx, p, sent

def test_browser_upload_with_unsaved_edits_saves_first_then_uploads(browser):
    ctx, p, sent = _harness(browser)
    try:
        assert p.inner_text("#upb") == "Upload" and not p.is_visible("[data-ui-unsaved]")
        p.fill("input[name=word]", "DRAFT")
        assert p.inner_text("#upb") == "Save and put online" and p.is_visible("[data-ui-unsaved]")
        p.click("#upb")
        for _ in range(50):
            if len(sent) >= 2:
                break
            p.wait_for_timeout(100)
        p.wait_for_timeout(200)
        assert [s[0] for s in sent] == ["/look", "/upload"] and "DRAFT" in sent[0][1]
    finally:
        ctx.close()

def test_browser_a_refused_save_sends_nothing_and_keeps_the_edit(browser):
    ctx, p, sent = _harness(browser, refuse=("/look",))
    try:
        p.fill("input[name=word]", "TOO LONG A WORD")
        p.click("#upb")
        p.wait_for_selector(".ui-note-error")
        assert "The word is too long." in p.inner_text(".ui-note-error")
        assert [s[0] for s in sent] == ["/look"]
        assert p.input_value("input[name=word]") == "TOO LONG A WORD" and p.inner_text("#upb") == "Save and put online"
    finally:
        ctx.close()

def test_browser_discard_puts_the_upload_back_to_its_own_words(browser):
    ctx, p, sent = _harness(browser)
    try:
        p.fill("input[name=word]", "DRAFT")
        p.click("text=Discard changes")
        p.wait_for_timeout(50)
        assert p.input_value("input[name=word]") == "PROOF" and p.inner_text("#upb") == "Upload"
        p.click("#upb")
        p.wait_for_timeout(300)
        assert [s[0] for s in sent] == ["/upload"]
    finally:
        ctx.close()

def test_browser_an_action_that_cannot_take_a_save_asks_for_one(browser):
    ctx, p, sent = _harness(browser)
    try:
        p.fill("input[name=word]", "DRAFT")
        p.click("#pvb")
        p.wait_for_selector(".ui-note-error")
        assert p.inner_text(".ui-note-error") == "Save your changes first."
        assert sent == [] and p.evaluate("document.activeElement.textContent") == "Save the look"
    finally:
        ctx.close()

def test_browser_a_tick_saves_on_its_own_with_undo(browser):
    ctx, p, sent = _harness(browser)
    try:
        p.check("#tick")
        p.wait_for_selector(".ui-note-ok button")
        assert sent == [("/switch", sent[0][1])] and 'name="on"' in sent[0][1]
        assert p.inner_text(".ui-note-ok").startswith("Saved")
        p.click(".ui-note-ok button")
        p.wait_for_function("() => document.querySelector('.ui-note-ok') && document.querySelector('.ui-note-ok').textContent === 'Undone'")
        assert len(sent) == 2 and 'name="on"' not in sent[1][1] and not p.is_checked("#tick")
    finally:
        ctx.close()

def test_browser_a_refused_tick_snaps_back_and_says_why(browser):
    ctx, p, sent = _harness(browser, refuse=("/switch",))
    try:

        from playwright.sync_api import expect
        p.click("#tick")
        expect(p.locator(".ui-note-error")).to_contain_text("The word is too long.", timeout=15_000)
        expect(p.locator("#tick")).not_to_be_checked(timeout=15_000)
        assert [s[0] for s in sent] == ["/switch"] and 'name="on"' in sent[0][1]
    finally:
        ctx.close()

def _tab(browser, c, width):

    from urllib.parse import urlsplit
    BASE = "https://app.test"
    posts = []

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-la-background", "x-la-inplace")}
        if request.method == "POST":
            posts.append(path)
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E, follow_redirects=True)
             if request.method == "POST" else c.get(path, headers=hdrs, environ_base=E, follow_redirects=True))
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return BASE, ctx, ctx.new_page(), posts

def test_browser_the_look_is_saved_before_the_logo_upload(browser, env):

    A, c = env
    png = Path(os.environ.get("TMPDIR", "/tmp")) / f"logo-282-{os.getpid()}.png"
    png.write_bytes(_png())
    BASE, ctx, p, posts = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/settings?tab=branding")
        p.on("dialog", lambda d: d.accept())
        btn = p.locator("#logo form[enctype] button")
        assert btn.inner_text() == "Upload the logo"
        p.locator('label:has(input[name=gallery_font][value=script])').click()
        assert btn.inner_text() == "Save the look and upload the logo"
        p.set_input_files("#logo input[type=file]", str(png))
        btn.click()
        for _ in range(50):
            if "/settings/logo" in posts:
                break
            p.wait_for_timeout(100)

        assert posts == ["/settings?tab=branding", "/settings/logo"], posts
        assert _setting(A, "gallery_font") == "script"
    finally:
        ctx.close()
        png.unlink(missing_ok=True)

def test_browser_studio_words_are_saved_before_the_upload(browser, studio):
    A, c, web = studio
    BASE, ctx, p, posts = _tab(browser, c, 390)
    try:
        p.on("dialog", lambda d: d.accept())
        p.goto(BASE + "/settings/studio-page")
        up = p.locator("#publish button.ui-btn-primary")
        assert up.inner_text() == "Upload"
        p.fill("#sp-tag", "Photographer · New town")
        assert up.inner_text() == "Save and put online"
        up.click()
        p.wait_for_load_state()
        p.wait_for_timeout(500)
        assert _setting(A, "site_tagline") == "Photographer · New town"
        from plugins import studio_page as S
        import threading
        for t in [t for t in threading.enumerate() if t.name.startswith("studio-page")]:
            t.join(timeout=30)
        assert S.JOB.get("state") in ("done", "error", "running") or (web / "index.html").exists()
        assert "New town" in (web / "index.html").read_text()
    finally:
        ctx.close()
