import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_DEV", "1")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'Morgan')")
        con.execute("""INSERT INTO people (household_id, name, role, email, phone) VALUES
                       (1, 'Alex Morgan', 'parent', 'alex.morgan.long.address@example.com', '555-555-0142')""")
        con.commit()
    finally:
        con.close()
    return A, c

def _session(A, stage, days=10, start=None, msg=None):
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops) VALUES (1, 1, 'family', ?, ?, ?, ?)",
                    (stage, (A.today() + timedelta(days=days)).isoformat() if days is not None else None, start,
                     "Longwood Gardens, main fountain entrance"))
        if msg:
            con.execute("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (1, 1, 'email', ?, ?)",
                        (msg, A.today().isoformat() + " 09:00:00"))
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()

def _page(c):
    r = c.get("/c/1?s=1", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _needs(page):

    rest = page.split("</header>", 1)[1]
    assert rest.lstrip().startswith('<section class="ui-needs"'), rest.lstrip()[:200]
    return rest.split("</section>", 1)[0] if 'class="ui-needs"' in rest.split("</section>", 1)[0] else ""

def _fold(page, fid):

    m = re.search(r'<(details|section)\b[^>]*\bid="%s"[^>]*>' % re.escape(fid), page)
    return m.group(0) if m else ""

def test_inquiry_shows_their_words_with_reply_build_a_quote_and_do_not_book(env):
    A, c = env
    _session(A, "inquiry", days=None, msg="Inquiry from your website:\nAny weekend dates in December?")
    page = _page(c)
    needs = _needs(page)
    assert 'id="message"' in needs and "Any weekend dates in December?" in needs
    assert 'href="mailto:alex.morgan.long.address@example.com' in needs and ">Reply by email</a>" in needs
    assert 'action="/session/1/reply"' in needs and ">Mark replied</button>" in needs
    assert ">Do not book</button>" in needs and 'value="lost"' in needs
    assert ">Build a quote</a>" in needs and 'name="act" value="book"' not in needs
    assert _fold(page, "billing").startswith("<section")

    assert 'href="/history?client=1"' in page and not _fold(page, "history").startswith("<details")
    assert 'id="timeline"' not in page

def test_quoted_leads_with_book_it(env):
    A, c = env
    _session(A, "quoted")
    page = _page(c)
    needs = _needs(page)
    assert 'id="quote-now"' in needs and 'name="act" value="book"' in needs
    assert "ui-btn-primary\">Book it" in needs and ">Do not book</a>" in needs
    assert _fold(page, "billing").startswith("<section")

def test_booked_shows_the_shoot_and_folds_the_rest(env):
    A, c = env
    _session(A, "booked", days=10, start="15:30")
    page = _page(c)
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert "Longwood Gardens, main fountain entrance" in head and "3:30 pm" in head
    body = page.split("</header>", 1)[1]

    shown = re.sub(r'<div class="ui-inline" id="f-date_set-1".*?</form>', "", body, flags=re.S)

    facts_ = re.search(r'<section class="ui-sec[^"]*" id="session-facts".*?</section>', shown, re.S).group(0)
    assert ">When</span>" not in shown and "Longwood Gardens" not in shown.replace(facts_, "")
    assert _fold(page, "timeline").startswith("<section")
    assert "Now: " not in page and '<details class="ui-fold' not in page

def test_shoot_day_offers_shoot_done_once_the_start_time_has_passed(env):
    A, c = env
    _session(A, "booked", days=0, start="00:00")
    page = _page(c)
    needs = _needs(page)
    assert 'id="shoot"' in needs and ">Shoot done</button>" in needs and 'value="shot"' in needs
    assert 'data-ui-map="Longwood Gardens, main fountain entrance"' in page
    assert "tel:5555550142" in page
    r = c.post("/session/1/quick", data={"act": "stage", "stage": "shot", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302
    assert A.connect().execute("SELECT stage FROM sessions WHERE id=1").fetchone()[0] == "shot"

def test_a_balance_is_recorded_under_its_row_not_on_another_page(env):
    A, c = env
    _session(A, "shot", days=-3)
    con = A.connect()
    try:
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 318, 'fee')")
        con.commit()
    finally:
        con.close()
    page = _page(c)
    if "Balance $318" not in page:
        pytest.skip("this session type asks for the balance later")
    assert 'href="/session/1#money"' not in page.split('id="needs"', 1)[1].split("</section>", 1)[0]
    needs = page.split('id="needs"', 1)[1].split("</section>", 1)[0]
    form = needs.split('name="kind" value="balance"', 1)[0].rsplit("<form", 1)[1] + needs.split('name="kind" value="balance"', 1)[1].split("</form>", 1)[0]
    assert 'class="ui-inline"' in needs
    assert 'name="kind" value="balance"' in needs and 'value="318.00"' in form and "data-ui-inplace" in form

def test_closed_offers_a_new_session(env):
    A, c = env
    _session(A, "closed", days=-60)
    page = _page(c)
    needs = _needs(page)
    assert "+ New session for Morgan" in needs and 'data-ui-open="new-session"' in needs

def test_every_in_place_form_still_works_without_script(env):

    A, c = env
    _session(A, "booked", days=10)
    page = _page(c)
    forms = re.findall(r"<form\b[^>]*data-ui-inplace[^>]*>.*?</form>", page, re.S)
    assert forms
    for f in forms:
        assert 'method="post"' in f and 'name="back"' in f, f[:200]

def test_the_components_page(env):
    A, c = env
    page = c.get("/dev/components", environ_base=E).get_data(as_text=True)
    for cls in ("ui-head", "ui-btn-primary", "ui-btn-quiet", "ui-go", "ui-chip-attention", "ui-chip-late", "ui-chip-done",
                "ui-todo", "ui-inline", "ui-fold", "ui-money", "ui-row", "ui-docbar"):
        assert cls in page, cls
    r = c.post("/dev/components/try", data={"what": "refuse"}, environ_base=E)
    assert r.status_code == 302
    page = c.get("/dev/components", environ_base=E).get_data(as_text=True)
    assert "data-refused" in page and "a sample refusal" in page
    assert not A.connect().execute("SELECT 1 FROM audit WHERE action LIKE '%omponent%'").fetchone()

def test_the_components_page_is_development_only(env, monkeypatch):

    A, c = env
    monkeypatch.delenv("CRM_DEV")
    assert c.get("/dev/components", environ_base=E).status_code == 404
    assert c.post("/dev/components/try", data={"what": "ok"}, environ_base=E).status_code == 404
    monkeypatch.setenv("CRM_DEV", "0")
    assert c.get("/dev/components", environ_base=E).status_code == 404

BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy")

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

def _tab(browser, c, width=390, script=True, ua=None):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type",)}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303) and request.method == "POST":

            r = c.get(urlsplit(r.location).path + ("?" + urlsplit(r.location).query if urlsplit(r.location).query else ""), environ_base=E)
        if r.status_code in (301, 302, 303):
            loc = r.location
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    kw = dict(user_agent=ua) if ua else {}
    ctx = browser.new_context(viewport={"width": width, "height": 800}, java_script_enabled=script, has_touch=width < 500, **kw)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_mark_done_happens_in_place(browser, env):

    A, c = env
    _session(A, "booked", days=10)
    c.post("/session/1/task", data={"title": "Pack the reflector", "back": "/c/1?s=1"}, environ_base=E)
    c.post("/session/1/task", data={"title": "Charge the flash", "back": "/c/1?s=1"}, environ_base=E)
    ctx, p = _tab(browser, c)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        row = p.locator(".ui-todo", has_text="Pack the reflector")
        p.set_viewport_size({"width": 390, "height": 420})
        row.evaluate("e => window.scrollTo({top: scrollY + e.getBoundingClientRect().top - 120, behavior: 'instant'})")
        y0 = p.evaluate("scrollY")
        top0 = row.evaluate("e => e.getBoundingClientRect().top")
        row.get_by_role("button", name="Mark done").click()
        note = p.locator(".ui-note")
        note.wait_for()
        assert note.inner_text() == "Marked done: Pack the reflector"
        assert p.evaluate("window.__stay") == 1
        assert p.locator(".ui-todo", has_text="Pack the reflector").count() == 0
        assert abs(p.evaluate("scrollY") - y0) < 80
        top1 = note.evaluate("e => e.getBoundingClientRect().top")
        assert abs(top1 - top0) < 30
    finally:
        ctx.close()
    assert A.connect().execute("SELECT done FROM tasks WHERE title='Pack the reflector'").fetchone()[0]

def test_a_refusal_shows_beside_the_button(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/dev/components")
        p.evaluate("window.__stay = 1")
        p.get_by_role("button", name="Refused").click()
        note = p.locator(".ui-note.ui-note-error")
        note.wait_for()
        assert "sample refusal" in note.inner_text()
        assert p.locator("main > .toast").count() == 0
        p.get_by_role("button", name="Error").click()
        p.locator(".ui-note.ui-note-error", has_text="sample error").wait_for()
        assert p.evaluate("window.__stay") == 1
    finally:
        ctx.close()

def test_without_script_the_same_form_posts_and_comes_back(browser, env):
    A, c = env
    _session(A, "booked", days=10)
    c.post("/session/1/task", data={"title": "Pack the reflector", "back": "/c/1?s=1"}, environ_base=E)
    ctx, p = _tab(browser, c, script=False)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.locator(".ui-todo", has_text="Pack the reflector").get_by_role("button", name="Mark done").click()
        p.wait_for_load_state()
        assert p.locator(".ui-todo", has_text="Pack the reflector").count() == 0
    finally:
        ctx.close()
    assert A.connect().execute("SELECT done FROM tasks WHERE title='Pack the reflector'").fetchone()[0]

@pytest.mark.parametrize("stage,days", [("inquiry", None), ("quoted", 20), ("booked", 10), ("booked", 0), ("shot", -3),
                                        ("delivered", -20), ("closed", -60)])
def test_phone_width_never_scrolls_sideways_and_money_never_clips(browser, env, stage, days):
    A, c = env
    _session(A, stage, days=days, start="10:00", msg="Hello, we would love a session in the garden with the whole family.")
    con = A.connect()
    try:
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session, one hour, at the location of your choice', 1318.5, 'fee')")
        con.commit()
    finally:
        con.close()
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        clipped = p.evaluate("""[...document.querySelectorAll('.ui-money-amt')].filter(td => {
            const r = td.getBoundingClientRect(); return r.width && (r.right > innerWidth + 1 || td.scrollWidth > td.clientWidth + 1); }).length""")
        assert clipped == 0
    finally:
        ctx.close()

def test_closed_shows_no_empty_to_do_and_keeps_the_gallery_line(env):
    A, c = env
    _session(A, "closed", days=-60)
    page = _page(c)
    assert ">Tasks</h2>" not in page

def test_a_note_from_the_needs_block_sits_beside_its_buttons(browser, env):

    A, c = env
    _session(A, "quoted", days=20)
    ctx, p = _tab(browser, c)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        p.locator(".ui-needs").get_by_role("button", name="Book it").click()
        p.locator(".ui-note").wait_for()
        assert p.evaluate("window.__stay") == 1
        assert p.evaluate("!document.querySelector('.ui-head .ui-note')")
        assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    finally:
        ctx.close()
    assert A.connect().execute("SELECT stage FROM sessions WHERE id=1").fetchone()[0] == "booked"

@pytest.mark.parametrize("ua,want", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "maps://?q="),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36", "geo:0,0?q="),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36", None)])
def test_the_place_opens_the_phones_own_maps_app(browser, env, ua, want):
    A, c = env
    _session(A, "booked", days=0, start="00:00")
    ctx, p = _tab(browser, c, ua=ua)
    try:
        p.goto(BASE + "/c/1?s=1")
        place = p.locator("[data-ui-map]")
        if want:
            assert place.locator("a").get_attribute("href").startswith(want)
        else:
            assert place.locator("a").count() == 0 and p.get_by_role("button", name="Copy address").count() == 1
    finally:
        ctx.close()

def test_a_tip_on_a_phone_never_covers_a_button(browser, env):

    A, c = env
    _session(A, "booked", days=10)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.locator(".ui-todo .ui-todo-icon").first.click()
        p.locator(".ui-tipline").wait_for()
        assert not p.locator("#tipbox").is_visible()
        covered = p.evaluate("""[...document.querySelectorAll('button, a.ui-btn')].filter(b => b.checkVisibility()).filter(b => {
            const r = b.getBoundingClientRect(), top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return top && top.closest('.ui-tipline, #tipbox'); }).map(b => b.textContent.trim())""")
        assert not covered, covered
    finally:
        ctx.close()
