import os
import sys
import threading
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy")

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
    _sql(A, "INSERT INTO settings (key, value) VALUES ('business_name', 'Bright Meadow Photography'), "
            "('photographer_name', 'Jordan Ellery'), ('business_email', 'studio@brightmeadow.example.com'), "
            "('business_phone', '555-555-0142'), ('business_address', '12 Orchard Lane' || char(10) || 'Springfield, OH 43004')")
    _sql(A, "INSERT INTO households (id, name, business) VALUES (1, 'Keystone Realty Group', 1)")
    _sql(A, "INSERT INTO people (household_id, name, role, email, phone) VALUES (1, 'Dana Keystone', 'adult', "
            "'dana@keystone.example.com', '555-555-0199')")
    return A, c

def _sql(A, s, args=()):
    con = A.connect()
    try:
        r = con.execute(s, args).fetchall()
        con.commit()
        return r
    finally:
        con.close()

def _session(A, sid=1, hid=1, stype="portrait", stage="inquiry", days=0, **cols):
    keys = ["id", "household_id", "type", "stage", "shoot_date"] + list(cols)
    vals = [sid, hid, stype, stage, (A.today() + timedelta(days=days)).isoformat()] + list(cols.values())
    _sql(A, f"INSERT INTO sessions ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})", vals)
    _sql(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Portrait session', 175, 'fee'), "
            "(?, 'Twilight exteriors', 95, 'fee')", (sid, sid))
    with A.app.test_request_context():
        A.sync_workflow_tasks(sid)
        A.db().commit()

def _quote(A, c, sid=1):
    r = c.post(f"/session/{sid}/quote/send", data={}, environ_base=E)
    assert r.status_code == 302
    return int(r.location.rsplit("/", 1)[1])

def test_a_business_clients_license_terms_print_on_the_quote(env):
    A, c = env
    _session(A, po_number="KR-5521", lic_term="2 years from delivery", lic_territory="the United States")
    page = c.get(f"/quote/{_quote(A, c)}", environ_base=E).get_data(as_text=True)
    assert "<b>License</b>" in page
    assert "The license lasts 2 years from delivery. It covers use in the United States. It is not exclusive." in page
    pdf_html = None
    with A.app.test_request_context():
        v = A.quote_version(1)
        pdf_html = A.render_template("quote_doc.html", v=v, pdf=True)
    assert "The license lasts 2 years from delivery." in pdf_html

def test_a_quote_with_no_license_terms_says_what_it_said_before(env):

    A, c = env
    _sql(A, "UPDATE households SET business=0 WHERE id=1")
    _session(A, lic_term="2 years")
    vid = _quote(A, c)
    assert "<b>License</b>" not in c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True)
    assert "license_terms" not in _sql(A, "SELECT data FROM quote_versions WHERE id=?", (vid,))[0][0]
    assert _quote(A, c) == vid

def test_sample_prices_explain_in_a_bubble_on_screen_and_in_words_on_paper(env):
    A, c = env
    _session(A)
    _sql(A, "UPDATE quotes SET schedule_id=(SELECT MIN(id) FROM fee_schedules) WHERE session_id=1")
    vid = _quote(A, c)
    page = c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True)
    if "SAMPLE prices" not in page:
        pytest.skip("this install's starter prices are already approved")
    block = page.split("SAMPLE prices", 1)[1].split("</div>", 1)[0]
    assert 'class="qh"' in block and "starter prices that came with the app" in block.split('class="qh-pop"', 1)[1]
    assert "Approve these prices" in block

def test_a_draft_invoice_explains_issuing_in_a_bubble(env):
    A, c = env
    _session(A, stage="booked", days=5)
    page = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    bar = page.split('<div class="bar">', 1)[1].split('<div class="page">', 1)[0]
    assert "Draft, no number yet" in bar
    assert "Issuing gives it the next invoice number" in bar.split('class="qh-pop"', 1)[1]
    assert "Draft, no number yet. Issuing" not in bar

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

def _tab(browser, c, width=390):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        r = c.get(path, environ_base=E)
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def _no_overlap(p):

    return p.evaluate("""() => {
        const head = document.querySelector('.page h1').parentElement, tab = document.querySelector('.headfacts');
        const a = head.getBoundingClientRect(), b = tab.getBoundingClientRect();
        return b.top >= a.bottom - 1 || b.left >= a.right - 1;
    }""")

def _one_line(p, sel):
    return p.evaluate(f"""() => [...document.querySelectorAll({sel!r})].every(e => {{
        const r = e.getClientRects(); return r.length === 1; }})""")

@pytest.mark.parametrize("width", [390, 768, 1280])
def test_an_issued_invoice_with_a_po_never_draws_its_numbers_over_the_studio(browser, env, width):
    A, c = env
    _session(A, stage="booked", days=5, po_number="KR-5521")
    assert c.post("/session/1/invoice/issue", data={}, environ_base=E).status_code == 302
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/session/1/invoice")
        assert "KR-5521" in p.locator(".headfacts").inner_text()
        assert _no_overlap(p)
        assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert _one_line(p, ".contact .nb")
        if width == 390:
            assert p.evaluate("document.querySelector('.page h1').parentElement.getBoundingClientRect().width") > 300
    finally:
        ctx.close()

def test_an_invoice_issued_before_this_fix_reads_at_phone_width_too(browser, env):

    A, c = env
    _session(A, stage="booked", days=5, po_number="KR-5521")
    c.post("/session/1/invoice/issue", data={}, environ_base=E)
    old = _sql(A, "SELECT html FROM bill_snapshots")[0][0]
    assert 'class="billhead" ' in old
    _sql(A, "UPDATE bill_snapshots SET html=?", (old.replace('class="billhead" ', ''),))
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/session/1/invoice")
        assert p.locator(".billhead").count() == 0
        assert _no_overlap(p)
    finally:
        ctx.close()

def test_the_document_bar_keeps_its_buttons_on_one_row_on_a_phone(browser, env):
    A, c = env
    _session(A, days=20)
    vid = _quote(A, c)
    c.post(f"/quote/{vid}/sent", data={}, environ_base=E)
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + f"/quote/{vid}")
        assert p.locator(".bar .ui-chip", has_text="Sent").count() == 1
        tops = p.evaluate("""() => [...document.querySelectorAll('.bar .ui-btn, .bar .ui-chip')]
            .filter(e => e.offsetParent && !e.closest('.ui-more-pop')).map(e => Math.round(e.getBoundingClientRect().top + e.offsetHeight / 2))""")
        assert len(tops) == 4 and max(tops) - min(tops) <= 4, tops

        spare = p.evaluate("""() => { const b = document.querySelector('body > .bar'), cs = getComputedStyle(b);
            const items = [...b.querySelectorAll('.ui-btn, .ui-chip')].filter(e => e.offsetParent && !e.closest('.ui-more-pop'));
            const used = items.reduce((n, e) => n + e.getBoundingClientRect().width, 0) + (items.length - 1) * parseFloat(cs.columnGap || 0);
            return b.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - used; }""")
        assert spare >= 8, spare
        assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        ctx.close()

def test_a_document_heading_leaves_no_word_alone(browser, env):
    A, c = env
    _session(A, days=20)
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/session/1/agreement")
        assert p.evaluate("getComputedStyle(document.querySelector('.page h2')).textWrap").startswith("balance")
    finally:
        ctx.close()

def _month(A):

    t = A.today()
    day = t.replace(day=min(t.day, 20)).isoformat()
    _sql(A, "INSERT INTO households (id, name) VALUES (2, 'Morgan Owens'), (3, 'The Castellanos')")
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, intake) VALUES "
            "(2, 2, 'portrait', 'booked', ?, '{}'), (3, 3, 'family', 'booked', ?, '{\"address\": \"1418 Wexford Court\"}')",
         (day, day))
    with A.app.test_request_context():
        A.sync_workflow_tasks(2)
        A.sync_workflow_tasks(3)
        A.db().commit()
    return day

def test_a_shoot_chip_names_the_client_not_the_address(env):
    A, c = env
    _month(A)
    page = c.get("/calendar", environ_base=E).get_data(as_text=True)
    chips = page.split('class="month"', 1)[1].split('class="mdays"', 1)[0]
    assert "<span>Castellanos</span>" in chips and "Wexford" not in chips
    assert "1418 Wexford Court" in page.split('class="mdays"', 1)[1]

@pytest.mark.parametrize("width", [390, 768, 1280])
def test_the_month_reads_at_every_width(browser, env, width):
    A, c = env
    day = _month(A)
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/calendar")
        names = p.evaluate(f"""() => [...document.querySelectorAll('.mcell[data-day="{day}"] .mshoot span:last-child')]
            .map(e => [e.textContent, e.scrollHeight <= e.clientHeight + 1, getComputedStyle(e).display !== 'none'])""")
        assert sorted(n for n, _, _ in names) == ["Castellanos", "Morgan Owens"]
        assert all(fits and shown for _, fits, shown in names), names

        assert p.evaluate("""() => [...document.querySelectorAll('.month .av')].every(e => e.scrollHeight <= e.clientHeight + 1)""")

        assert p.evaluate("getComputedStyle(document.querySelector('.ui-head')).borderBottomStyle") == "none"
        assert p.evaluate("getComputedStyle(document.querySelector('.month')).borderTopStyle") == "solid"

        assert p.evaluate("""() => [...document.querySelectorAll('.mdue')].every(d => {
            const b = [...d.querySelectorAll('.badged')];
            return b.every((x, i) => { if (!i) return true; const prev = b[i - 1].getBoundingClientRect(), cur = x.getBoundingClientRect();
                if (cur.top < prev.bottom - 1) return true;          /* side by side */
                const face = b[i - 1].querySelector('.badge-face'); if (!face) return true;
                return face.getBoundingClientRect().bottom <= cur.top - 2; }); })""")
        assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        ctx.close()

@pytest.fixture
def gal(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for i, col in enumerate(((120, 90, 60), (60, 90, 120)), 1):
        Image.new("RGB", (400, 300), col).save(src / f"img{i}.jpg", "JPEG", quality=95)
    _sql(A, "INSERT INTO households (name) VALUES ('Lane')")
    _sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', '2026-10-01')")
    from plugins import gallery as G
    return A, c, G

def _build(A, c, G, **form):
    c.post("/session/1/gallery/build", data=form or {"source_dir": str(G.PHOTOS_ROOT / "export" / "lane")}, environ_base=E)
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=20)
    assert c.get("/session/1/gallery/status", environ_base=E).get_json()["state"] == "done"

def _look(c, mark):
    c.post("/settings", data=dict(look_posted="1", gallery_font="classic", gallery_bg="#f6f3ed", proof_mark=mark,
                                  proof_mark_text="PROOF", proof_mark_strength="20"), environ_base=E)

def test_a_gallery_built_before_the_proof_mark_says_so_and_rebuilds_with_it(gal):
    A, c, G = gal
    _build(A, c, G)
    page = lambda: c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Rebuild with the proof mark" not in page()
    _look(c, "tiled")
    p_ = page()
    assert "Built before the proof mark was turned on" in p_ and "Rebuild with the proof mark" in p_
    form = p_.split("Rebuild with the proof mark", 1)[0].rsplit("<form", 1)[1]
    assert 'name="source_dir"' in form
    _build(A, c, G)
    assert _sql(A, "SELECT proof FROM galleries WHERE session_id=1")[0][0] == 1
    assert "Rebuild with the proof mark" not in page()
    _look(c, "")
    assert "Rebuild with the proof mark" not in page()
