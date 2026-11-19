import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
ROOT = Path(__file__).resolve().parent.parent
BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy")

def _env(tmp_path, monkeypatch, setup_done=True):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    return A, signed_in_client(A, setup_done=setup_done)

@pytest.fixture
def env(tmp_path, monkeypatch):
    return _env(tmp_path, monkeypatch)

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    return _env(tmp_path, monkeypatch, setup_done=False)

def _sql(A, s, args=()):
    con = A.connect()
    try:
        r = con.execute(s, args).fetchall()
        con.commit()
        return r
    finally:
        con.close()

def _get(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def test_the_update_note_about_and_the_year_summary_all_read_VERSION(env, monkeypatch):
    A, c = env
    import about
    monkeypatch.setattr(about, "version", lambda: "9.8.7")
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_version', '9.8.6')")
    with A.app.test_request_context():
        A.db()
        A._note_update()
        A.g.db.commit()
    assert "Updated to Lumen Aureum 9.8.7." in _get(c, "/")
    assert "Version 9.8.7" in _get(c, "/about")
    with A.app.test_request_context():
        v = A.year_summary(A.today().year)
        assert "Lumen Aureum 9.8.7" in A.render_template("year_summary.html", v=v, pdf=True)

def test_compose_names_the_same_version_as_VERSION():

    want = (ROOT / "VERSION").read_text().strip()
    tag = re.search(r"image:\s*\S+:(\S+)", (ROOT / "compose.yaml").read_text()).group(1)
    assert tag == want

def _note(A, **kw):
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
         (json.dumps(dict(dict(version="1.5.0", backup=True, changed=True, backup_at="2026-11-18T15:21"), **kw)),))

def test_the_update_note_says_what_changed_and_names_the_backup(env):
    A, c = env
    _note(A)
    page = _get(c, "/")
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert "Updated to Lumen Aureum 1.5.0." in head and "<button" not in head
    note = page.split('id="update-note"', 1)[1].split("</section>", 1)[0]
    assert "/about#whats-new" in note and "What&#39;s new in 1.5.0" in note
    assert "the one marked Before the update" in note
    assert "Nov 18" in note and "3:21" in note
    assert "Hide this note" in note

def test_the_update_note_stays_until_hidden(env):
    A, c = env
    _note(A)
    assert "Updated to Lumen Aureum" in _get(c, "/")
    assert "Updated to Lumen Aureum" in _get(c, "/")
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    assert "Updated to Lumen Aureum" not in _get(c, "/")
    assert "Updated to Lumen Aureum" not in _get(c, "/about")

def test_the_update_note_goes_the_day_after_it_was_first_shown(env, monkeypatch):
    A, c = env
    _note(A)
    day = A.today()
    assert "Updated to Lumen Aureum" in _get(c, "/")
    monkeypatch.setattr(A, "today", lambda: day + timedelta(days=1))
    assert "Updated to Lumen Aureum" not in _get(c, "/")
    assert not _sql(A, "SELECT 1 FROM settings WHERE key='update_notice'")

def test_about_shows_whats_new_from_the_changelog(env, monkeypatch):
    A, c = env
    import about
    top = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)
    monkeypatch.setattr(about, "version", lambda: top)
    news = about.whats_new()
    assert news and all(lines for _h, lines in news)
    page = _get(c, "/about")
    assert 'id="whats-new"' in page and f"What's new in {top}" in page
    assert news[0][1][0].split(":")[0][:30].replace("'", "&#39;") in page
    assert about.whats_new("0.0.0") == []

def test_the_backups_page_is_called_backups_and_marks_the_copy_taken_before_an_update(env):
    A, c = env
    _sql(A, """INSERT INTO vault_objects (name, kind, backend, created, size) VALUES
               ('db-1', 'db', 'local', '2026-11-17T02:40:00', 1048576), ('db-2', 'upgrade', 'local', '2026-11-18T15:21:00', 1048576)""")
    page = _get(c, "/vault")
    assert "<h1>Backups</h1>" in page and "<h1>Vault</h1>" not in page
    assert "<b>Before the update</b>" in page and "Daily" in page
    assert "upgrade on local" not in page and "Before an update on local" in page

def test_setup_documents_says_approve_where_used(fresh):
    A, c = fresh
    page = _get(c, "/setup/documents")
    assert "approve each one right where you use it" in page.lower()
    assert "review each one in Settings, Documents" not in page
    assert "lasting license to print" in page.split('class="qh-pop"', 1)[1]

def test_setup_explanations_are_bubbles_not_lines(fresh):
    A, c = fresh
    biz, store = _get(c, "/setup/business"), _get(c, "/setup/storage")
    assert '<div class="hint">Filled in from this device' not in biz and "Filled in from this device" in biz
    assert '<div class="hint">Each location is tested' not in store and "Each location is tested first" in store
    assert "Backups (encrypted)" not in store and "Backups are encrypted before they are written" in store

def test_host_check_before_the_certificate_is_one_line_not_five(fresh, monkeypatch):
    A, c = fresh
    import hostcheck
    import setup as S
    feats = [[k, l, False, "its certificate must be checked first"] for k, l in hostcheck.FEATURES]
    caps = dict(at="2026-11-18 15:30", kind="unreachable", version="", detail="", htaccess=None, gate=None, proxy="",
                features=feats, cert="untrusted")
    monkeypatch.setattr(S.VW, "config", lambda: {"gallery_host": {"type": "sftp", "host": "h"}})
    from plugins import gallery
    monkeypatch.setattr(gallery, "fingerprint_of", lambda gh: "fp")
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_host_caps', ?)",
         (json.dumps(dict(fp="fp", setup=caps, now=caps)),))
    page = _get(c, "/setup/galleries")
    caps_ = page.split('id="hostcaps"', 1)[1].split("</div>", 1)[0]
    assert caps_.count("its certificate must be checked first") == 1
    assert "Doesn&#39;t work" not in caps_

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
    ctx = browser.new_context(viewport={"width": width, "height": 844}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

NO_SIDEWAYS = "document.documentElement.scrollWidth <= innerWidth"

WORD_FITS = """sel => [...document.querySelectorAll(sel)].filter(e => e.offsetParent).every(e => {
  const r = document.createRange(); let ok = true;
  for (const w of e.textContent.split(/\\s+/).filter(Boolean)) {
    const t = document.createElement('span'); t.textContent = w; t.style.whiteSpace = 'nowrap';
    e.appendChild(t); ok = ok && t.getBoundingClientRect().width <= e.getBoundingClientRect().width + 0.5; t.remove(); }
  return ok; })"""

def test_setup_stepper_never_breaks_a_word_on_a_phone(browser, fresh):
    A, c = fresh
    ctx, p = _tab(browser, c)
    try:
        for step in ("storage", "money", "galleries"):
            p.goto(f"{BASE}/setup/{step}")
            assert p.evaluate(NO_SIDEWAYS)
            assert p.locator(".stepper .lab:visible").count() == 0
            cap = p.locator(".stepper-cap")
            assert cap.is_visible() and re.match(r"Step \d of 9", cap.inner_text())
            assert p.evaluate(WORD_FITS, ".stepper-cap, h1")
    finally:
        ctx.close()

def test_host_fields_have_labels_that_stay_and_fit_on_a_phone(browser, fresh):
    A, c = fresh
    ctx, p = _tab(browser, c)
    try:
        p.goto(f"{BASE}/setup/galleries")
        p.select_option("#f-gallery_type", "sftp")
        for name, label in (("gallery_host", "Host"), ("gallery_user", "User name on the server"),
                            ("gallery_root", "Web folder on the server"), ("gallery_port", "Port (blank for 22)")):
            lab = p.locator(f"label[for=f-{name}]")
            assert lab.is_visible() and lab.inner_text() == label
            box, field = lab.bounding_box(), p.locator(f"#f-{name}").bounding_box()
            assert field["width"] > 300 and box["width"] <= 390
        assert not p.locator("#f-gallery_key_file").is_visible()
        p.get_by_text("A key file I mounted").first.click()
        assert p.locator("label[for=f-gallery_key_file]").is_visible()
        assert not p.locator("#pub_gallery_").is_visible()
        p.fill("#f-gallery_host", "example.com")
        assert p.locator("label[for=f-gallery_host]").is_visible()
        p.goto(f"{BASE}/setup/storage")
        p.select_option("#f-primary_type", "sftp")
        assert p.locator("label[for=f-primary_host]").is_visible()
        assert p.evaluate(NO_SIDEWAYS)
    finally:
        ctx.close()

def test_payments_and_terms_reads_on_a_phone(browser, fresh):
    A, c = fresh
    ctx, p = _tab(browser, c)
    try:
        p.goto(f"{BASE}/setup/money")
        assert p.evaluate(NO_SIDEWAYS)
        for id_ in ("f-delivery_days", "f-event_delivery_days", "f-before_days", "f-net_days", "f-pay_other"):
            assert p.locator(f"label[for={id_}]").is_visible(), id_

        for id_ in ("f-reschedule_fee", "f-quote_term_days", "f-before_days"):
            lab, fld = p.locator(f"label[for={id_}]").bounding_box(), p.locator(f"#{id_}").bounding_box()
            assert lab["y"] + lab["height"] <= fld["y"] + 1, id_
        for q_ in p.locator("table.rs .qh").all():
            lab = q_.locator("xpath=preceding-sibling::label[1]").bounding_box()
            assert abs(q_.bounding_box()["y"] - lab["y"]) < 12
        before = p.locator("label[for=f-before_days]").bounding_box()["y"]
        assert before > p.locator("#bal-l").bounding_box()["y"]
        assert p.locator("#f-pay_other").get_attribute("placeholder") == "e.g. Venmo, gift card"
        assert p.locator("#f-pay_other").bounding_box()["width"] > 300
    finally:
        ctx.close()

def test_setup_step_one_heading_matches_the_later_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    src = (ROOT / "templates" / "auth_setup_owner.html").read_text()
    assert "<h1>Step 1 of 3: you</h1>" in src and "<h2>Step 1" not in src
    for f in ("auth_setup_verify.html", "auth_setup_key.html"):
        assert re.search(r"<h1>Step [23] of 3", (ROOT / "templates" / f).read_text())
