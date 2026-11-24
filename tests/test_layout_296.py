import os
import runpy
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_client_page_141 import BASE, E, _tab, browser

@pytest.fixture
def demo(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_equipment', '1')")
    con.commit()
    con.close()
    return A, c

def _open(browser, c, url, width, height=800):
    ctx, p = _tab(browser, c, width=width)
    p.set_viewport_size({"width": width, "height": height})
    p.goto(BASE + url, wait_until="load")
    p.wait_for_timeout(150)
    return ctx, p

def test_next_two_weeks_sticks_below_the_top_bar(browser, demo):
    A, c = demo
    for w in (1024, 1440):
        ctx, p = _open(browser, c, "/", w, 700)
        try:
            p.evaluate("document.querySelector('.ui-today-cols > :first-child').style.minHeight = '2600px'")
            p.mouse.wheel(0, 900)
            p.wait_for_timeout(400)
            side, bar = p.evaluate("""() => [document.querySelector('.ui-today-side').getBoundingClientRect().top,
                                             document.querySelector('nav.top').getBoundingClientRect().bottom]""")
            assert p.evaluate("scrollY") > 500
            assert side >= bar, (w, side, bar)
        finally:
            ctx.close()

def test_event_facts_strip_has_no_overlap_and_no_hole(browser, demo):
    A, c = demo
    eid = A.connect().execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]
    for w in (390, 820, 1024, 1280):
        ctx, p = _open(browser, c, f"/event/{eid}", w)
        try:
            r = p.evaluate("""() => {
              const box = document.querySelector('.highlights'), br = box.getBoundingClientRect();
              const cells = [...box.children].map(d => d.getBoundingClientRect());
              const over = [...box.querySelectorAll('.pill')].filter(pl => pl.getBoundingClientRect().right > pl.closest('.highlights > div').getBoundingClientRect().right + 0.5).length;
              const rows = {}; cells.forEach(r => { const k = Math.round(r.top); rows[k] = Math.max(rows[k] || 0, r.right); });
              return [over, Object.values(rows).map(x => br.right - x)]; }""")
            assert r[0] == 0, (w, "a pill runs out of its cell")
            assert all(gap < 2 for gap in r[1]), (w, "a row of the strip ends short (a hole)", r[1])
        finally:
            ctx.close()

def test_equipment_has_no_sideways_scroll(browser, demo):
    A, c = demo
    for w in (390, 820, 1024):
        ctx, p = _open(browser, c, "/equipment", w)
        try:
            assert p.evaluate("document.documentElement.scrollWidth") <= w
            assert p.locator("label.ui-filepick input[type=file][name=receipt]").count() == 1
        finally:
            ctx.close()

SEP_JS = """() => [...document.querySelectorAll('.ui-sep')].filter(s => !s.classList.contains('ui-sep-off')
    && getComputedStyle(s).display !== 'none').map(s => {
  const a = s.getClientRects(), nx = s.nextElementSibling;
  const b = nx ? [...nx.getClientRects()].filter(r => r.width > 0) : [];
  const pr = document.createRange(); pr.setStartBefore(s.parentElement.firstChild); pr.setEndBefore(s);
  const pp = [...pr.getClientRects()].filter(r => r.width > 0);
  const top = a[a.length - 1].top;
  return [b.length ? Math.abs(b[0].top - top) : 99, pp.length ? Math.abs(pp[pp.length - 1].top - top) : 99]; })"""

def test_no_header_line_starts_or_ends_with_the_dot(browser, demo):

    A, c = demo
    for url in ("/money", "/calendar/year", "/c/1", "/c/4"):
        ctx, p = _open(browser, c, url, 390)
        try:
            for w in range(340, 1000, 30):
                p.set_viewport_size({"width": w, "height": 800})
                p.wait_for_timeout(50)
                for nxt, prev in p.evaluate(SEP_JS):
                    assert nxt < 8 and prev < 8, (url, w, nxt, prev)
        finally:
            ctx.close()

def test_a_timeline_count_is_never_under_the_next_mark(browser, demo):
    A, c = demo
    for w in (390, 820, 1440):
        ctx, p = _open(browser, c, "/c/1", w)
        try:
            hits = p.evaluate("""() => { const out = [];
              document.querySelectorAll('.journey .jn').forEach(n => { const r = n.getBoundingClientRect();
                n.closest('.j-past, .j-future').querySelectorAll('.jm').forEach(m => { if (m.contains(n)) return;
                  const q = m.getBoundingClientRect();
                  if (r.left < q.right && q.left < r.right && r.top < q.bottom && q.top < r.bottom) out.push(n.textContent); }); });
              return [document.querySelectorAll('.journey .jn').length, out]; }""")
            assert hits[0] > 0 and hits[1] == [], (w, hits)
        finally:
            ctx.close()

def test_the_settings_list_says_it_scrolls(browser, demo):
    A, c = demo
    ctx, p = _open(browser, c, "/settings?tab=prices", 1280, 800)
    try:
        cls = lambda: p.evaluate("document.querySelector('.settabs').className")
        assert p.evaluate("(n => n.scrollHeight > n.clientHeight)(document.querySelector('.settabs'))")
        assert "ui-more-below" in cls()
        p.evaluate("(n => n.scrollTop = n.scrollHeight)(document.querySelector('.settabs'))")
        p.wait_for_timeout(100)
        assert "ui-more-above" in cls() and "ui-more-below" not in cls()
        assert p.evaluate("getComputedStyle(document.querySelector('.settabs')).maskImage") != "none"
    finally:
        ctx.close()

def test_phone_numbers_and_words_stay_whole(browser, demo):
    A, c = demo
    eid = A.connect().execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]
    for url in ("/c/1", f"/event/{eid}", "/clients"):
        ctx, p = _open(browser, c, url, 390)
        try:
            for w in (340, 390):
                p.set_viewport_size({"width": w, "height": 800})
                p.wait_for_timeout(50)
                broken = p.evaluate("""() => [...document.querySelectorAll('main a.tel')].filter(a => a.checkVisibility()
                    && a.getClientRects().length > 1).map(a => a.textContent)""")
                assert broken == [], (url, w, broken)

            assert p.evaluate("getComputedStyle(document.querySelector('main')).overflowWrap") == "anywhere"
        finally:
            ctx.close()

def test_floating_contact_steps_aside_while_scrolling_down(browser, demo):
    A, c = demo
    ctx, p = _open(browser, c, "/", 390, 700)
    try:
        away = lambda: p.evaluate("document.body.classList.contains('ui-fab-away')")
        assert not away()
        p.mouse.wheel(0, 600)
        p.wait_for_timeout(400)
        assert away()
        p.mouse.wheel(0, -200)
        p.wait_for_timeout(400)
        assert not away()
    finally:
        ctx.close()

def test_sign_out_matches_the_bar(browser, demo):
    A, c = demo
    ctx, p = _open(browser, c, "/", 1440)
    try:
        so, link = p.evaluate("""() => { const s = getComputedStyle(document.querySelector('nav.top [data-more-key="signout"] button')),
            a = getComputedStyle(document.querySelector('nav.top a[data-more-key="inquiry"]'));
            return [[s.fontSize, s.borderTopWidth, s.color], [a.fontSize, '0px', a.color]]; }""")
        assert so == link
        assert 'style=' not in p.evaluate("document.querySelector('nav.top [data-more-key=\"signout\"]').outerHTML")
    finally:
        ctx.close()

def test_month_calendar_names_keep_whole_words(browser, demo):

    A, c = demo
    ctx, p = _open(browser, c, "/calendar", 1024)
    try:
        ws = p.evaluate("[...document.querySelectorAll('main .mshoot')].map(e => getComputedStyle(e).overflowWrap)")
        assert ws and set(ws) == {"break-word"}, ws
    finally:
        ctx.close()
