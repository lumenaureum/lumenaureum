import html
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

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

def _sql(A, s, args=()):
    con = A.connect()
    try:
        r = con.execute(s, args).fetchall()
        con.commit()
        return r
    finally:
        con.close()

def _activity(c):
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert '<details class="ui-fold' in page and 'id="activity"' in page
    return page.split('id="account-activity"', 1)[1].split("</details>", 1)[0]

def test_account_activity_shows_every_line_in_the_studio_time_zone(env):

    A, c = env
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-17 20:21:00', 'Created the owner account and the master key', 'Owner')")
    r = c.post("/settings/timezone", data={"timezone": "America/New_York"}, environ_base=E)
    assert r.status_code == 302
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-17 15:51:00', 'Set up the locked-studio notice', 'Owner')")
    acts = _activity(c)
    assert "3:21 pm" in acts and "8:21 pm" not in acts
    assert acts.index("Set up the locked-studio notice") < acts.index("Created the owner account")
    assert acts.count("Tue Nov 17") == 1
    stored = _sql(A, "SELECT at FROM audit WHERE action LIKE 'Created the owner%'")[0][0]
    assert stored == "2026-11-17 20:21:00"

def test_an_install_from_before_the_zone_note_shows_the_owner_line_in_local_time(env):

    A, c = env
    _sql(A, "INSERT INTO settings (key, value) VALUES ('timezone', 'America/New_York')")
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-17 20:48:00', 'Created the owner account and the master key', 'Owner')")
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-17 16:00:00', 'Made new recovery codes', 'Owner')")
    _sql(A, "DELETE FROM settings WHERE key LIKE 'audit_zone%'")
    with A.app.app_context():
        A.apply_settings()
    with A.app.test_request_context():
        A.db()
        A.audit("Checked the master key")
        A.db().commit()
    acts = _activity(c)
    assert "3:48 pm" in acts and "4:00 pm" in acts

def test_the_zone_note_shares_the_request_connection(env):

    A, c = env
    r = c.post("/setup/business", data=dict(business_name="Bright Meadow", photographer_name="Sam", timezone="America/Denver"),
               environ_base=E)
    assert r.status_code in (200, 302)
    c.post("/settings/timezone", data={"timezone": "America/New_York"}, environ_base=E)
    assert _sql(A, "SELECT value FROM settings WHERE key='audit_zone_now'")[0][0] == "America/New_York"

def test_a_later_zone_change_keeps_earlier_lines_at_their_real_time(env):
    A, c = env
    c.post("/settings/timezone", data={"timezone": "America/New_York"}, environ_base=E)
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-10 09:00:00', 'Changed the password', 'Owner')")
    c.post("/settings/timezone", data={"timezone": "America/Chicago"}, environ_base=E)
    acts = _activity(c)
    assert "8:00 am" in acts and "Changed the password" in acts

def test_sign_ins_show_and_repeats_on_a_day_are_one_line(env):
    A, c = env
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Keystone Realty Group')")
    for i in range(9):
        _sql(A, "INSERT INTO audit (at, household_id, action, who) VALUES (?, 1, 'Exported everything held about this client', 'Owner')",
             (f"2026-11-18 10:{i:02d}:00",))
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-16 09:00:00', 'Signed in from 10.0.0.5', 'Owner')")
    _sql(A, "INSERT INTO audit (at, action, who) VALUES ('2026-11-16 08:00:00', 'Signed in and unlocked the data from 10.0.0.5', 'Owner')")
    acts = _activity(c)
    assert "Signed in from 10.0.0.5" in acts and "Signed in and unlocked the data" in acts
    assert acts.count("Downloaded a file of everything kept about Keystone Realty Group") == 1
    assert "9 times" in acts
    assert acts.count('class="hday"') == 2

SERVER, TOPIC = "https://ntfy.example.net", "studio-a1b2c3d4e5"

@pytest.mark.parametrize("exc,want", [
    ("dns", "couldn't find ntfy.example.net. Check the server address"),
    ("timeout", "the server didn't answer"),
    ("refused", "the server didn't answer"),
    ("403", "the server refused it. Check the topic and the access token"),
    ("500", "the server refused it"),
])
def test_a_failed_test_notice_says_why_in_plain_words(env, monkeypatch, exc, want):
    A, c = env
    import socket
    import urllib.error
    import locknotice

    def fail(url, body, headers):
        if exc == "dns":
            raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        if exc == "timeout":
            raise urllib.error.URLError(TimeoutError("timed out"))
        if exc == "refused":
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        raise urllib.error.HTTPError(url, int(exc), "x", {}, None)
    monkeypatch.setattr(locknotice, "_post", fail)
    c.post("/security/notice", data=dict(act="save", server=SERVER, topic=TOPIC), environ_base=E)
    c.post("/security/notice", data=dict(act="test"), environ_base=E)
    page = html.unescape(c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True))
    assert f"The test notice couldn't be sent: {want}." in page
    assert "urlopen" not in page and "Errno" not in page and "HTTP Error" not in page

def test_the_test_notice_works_before_saving_and_saves_what_went_through(env, monkeypatch):

    A, c = env
    import locknotice
    sent = []
    monkeypatch.setattr(locknotice, "_post", lambda url, body, headers: sent.append(url))
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    form = page.split('id="ntfy-server"', 1)[0].rsplit("<form", 1)[1] + page.split('id="ntfy-server"', 1)[1].split("</form>", 1)[0]
    assert 'name="act" value="test"' in form and 'name="act" value="save"' in form
    c.post("/security/notice", data=dict(act="test", server=SERVER, topic=TOPIC), environ_base=E)
    assert sent == [f"{SERVER}/{TOPIC}"]
    with c.session_transaction() as fs_:
        assert "these settings are saved" in (fs_.get("notice") or "")
    assert _sql(A, "SELECT 1 FROM settings WHERE key='ntfy_server' AND value != ''")
    r = c.post("/security/notice", data=dict(act="test", server="http://ntfy.example.net", topic=TOPIC), environ_base=E)
    assert r.status_code == 302 and len(sent) == 1

def test_after_the_page_learns_it_was_signed_out_the_sign_in_page_says_why(env):
    A, c = env
    import time
    import auth
    _sql(A, "INSERT INTO settings (key, value) VALUES ('idle_signout_hours', '1')")
    with c.session_transaction() as s_:
        sid = s_["sid"]
    auth.LAST_SEEN[sid] = time.time() - 3600 - 5
    assert c.get("/session/idle", headers={"X-LA-Background": "1"}, environ_base=E).get_json()["left"] == 0
    r = c.get("/c/1", environ_base=E)
    assert "idle=1" in r.headers["Location"]
    page = c.get(r.headers["Location"].replace("https://localhost", ""), environ_base=E).get_data(as_text=True)
    assert "Signed out after a while with no activity" in page
    r = c.get("/c/1", environ_base=E)
    assert "idle=1" not in r.headers["Location"]

def test_a_setting_found_by_search_opens_its_own_page(env):
    A, c = env
    page = c.get("/search?q=proof", environ_base=E).get_data(as_text=True)
    assert 'href="/settings?tab=branding#gl-mark-l"' in page
    page = c.get("/search?q=idle", environ_base=E).get_data(as_text=True)
    assert 'href="/security#f-idle_signout_hours"' in page
    with A.app.test_request_context():
        assert all("tab=" in e_["link"] for e_ in A.settings_find_entries() if e_["link"].startswith("/settings?")
                   or e_["link"].startswith("/settings#"))

def test_who_can_reach_this_is_a_named_fold_with_nothing_to_save(env):
    A, c = env
    page = c.get("/setup/network", environ_base=E).get_data(as_text=True)
    main = page.split("<h1", 1)[1]
    assert "<summary>Details</summary>" not in main
    assert 'class="ui-fold' in main and "Addresses that can connect" in main
    assert '<label class="l">Who can reach it</label>' not in main
    assert ">Save</button>" not in main.split("</form>", 1)[0]

def test_print_prices_cells_carry_their_labels(env):
    A, c = env
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_prints', '1')")
    page = c.get("/settings/prints", environ_base=E).get_data(as_text=True)
    assert 'class="r prices ui-stack"' in page and 'style="overflow-x:auto"' not in page
    for lab in ("Shown as", "Finish", "Price", "Your lab cost", "Margin", "Offered", "Remove"):
        assert f'data-label="{lab}"' in page

BASE = "https://app.test"

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

def test_on_a_phone_a_search_result_opens_the_setting(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/search?q=idle")
        p.locator("#setting-hits a").first.click()
        p.wait_for_url("**/security#f-idle_signout_hours")
        fld = p.locator("#f-idle_signout_hours")
        assert fld.is_visible()
        box = fld.bounding_box()
        assert 0 <= box["y"] < 800
    finally:
        ctx.close()

@pytest.mark.parametrize("width", [390, 1280])
def test_the_search_placeholder_fits(browser, env, width):
    A, c = env
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/search")
        fits = p.evaluate("""() => { const i = document.querySelector('form.sui-searchbox input[name=q]'), s = getComputedStyle(i);
            const cx = document.createElement('canvas').getContext('2d'); cx.font = s.font;
            const room = i.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
            return cx.measureText(i.placeholder).width <= room; }""")
        assert fits
    finally:
        ctx.close()

def test_the_settings_list_shows_the_page_shown(browser, env):

    A, c = env
    ctx, p = _tab(browser, c, 1280)
    try:
        p.set_viewport_size({"width": 1280, "height": 600})
        p.goto(BASE + "/security")
        on = p.locator(".settabs a.on")
        assert on.inner_text().startswith("Security")
        nav, row = p.locator(".settabs").bounding_box(), on.bounding_box()
        assert nav["y"] <= row["y"] and row["y"] + row["height"] <= nav["y"] + nav["height"] + 1
    finally:
        ctx.close()

@pytest.mark.parametrize("width", [390, 1280])
def test_url_fields_look_like_every_other_field(browser, env, width):
    A, c = env
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/settings?tab=notifications")
        got = p.evaluate("""() => ['ntfy-server', 'ntfy-topic'].map(id => { const s = getComputedStyle(document.getElementById(id));
            return [s.borderTopColor, s.borderRadius, s.paddingLeft, s.backgroundColor, document.getElementById(id).getBoundingClientRect().width]; })""")
        assert got[0] == got[1]
    finally:
        ctx.close()

@pytest.mark.parametrize("width", [390, 768])
def test_print_prices_fit_a_phone_and_a_tablet(browser, env, width):
    A, c = env
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_prints', '1')")
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/settings/prints")
        assert p.evaluate("document.documentElement.scrollWidth") <= width
        cut = p.evaluate("""() => [...document.querySelectorAll('table.prices input, table.prices td')].filter(e => {
            const r = e.getBoundingClientRect(); return r.width && (r.right > innerWidth + 0.5 || r.left < -0.5); }).length""")
        assert cut == 0
        assert p.locator('table.prices td[data-label="Markup"], table.prices td[data-label="Finish"]').first.is_visible()
    finally:
        ctx.close()

def test_account_activity_opens_grouped_by_day(browser, env):
    A, c = env
    for d in range(3):
        _sql(A, "INSERT INTO audit (at, action, who) VALUES (?, 'Signed in from 10.0.0.5', 'Owner')", (f"2026-11-1{d + 1} 09:00:00",))
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/security")
        fold = p.locator("details#activity")
        assert fold.get_attribute("open") is None
        fold.locator("summary").click()
        assert p.locator("#account-activity .hday").count() == 3
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()

def test_search_matches_use_the_app_accent_not_bright_yellow(browser, env):
    A, c = env
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Keystone Realty Group')")
    _sql(A, "INSERT INTO people (household_id, name) VALUES (1, 'Derek Lane')")
    ctx, p = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/search?q=Keystone")
        css = p.evaluate("""() => { const m = document.createElement('mark'); document.querySelector('main').append(m);
            const s = getComputedStyle(m); return [s.backgroundColor, s.color, getComputedStyle(m.parentNode).color]; }""")
        assert css[0] not in ("rgb(255, 255, 0)",) and css[1] == css[2]
    finally:
        ctx.close()

@pytest.mark.parametrize("width", [390, 1280])
def test_the_idle_warning_is_full_width_and_opaque(browser, env, width):
    A, c = env
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/security")
        p.evaluate("document.querySelector('[data-idle-warn]').hidden = false")
        p.wait_for_timeout(50)
        box = p.locator("[data-idle-warn]").bounding_box()
        st = p.evaluate("getComputedStyle(document.querySelector('[data-idle-warn]')).opacity")
        assert st == "1"
        assert box["width"] >= min(640, width - 32) - 1 and box["x"] >= 15 and box["x"] + box["width"] <= width - 15
    finally:
        ctx.close()

def test_an_upgraded_1_4_studio_shows_its_owner_line_in_local_time(tmp_path, monkeypatch):

    import test_upgrade_from_140 as U
    U._place(tmp_path)
    A, c, _ = U._open(tmp_path, monkeypatch)
    rows = U._q(A, "SELECT id, at FROM audit WHERE action LIKE 'Created the owner account%'")
    if not rows:
        con = A.connect()
        con.execute("INSERT INTO audit (at, action, who) VALUES ('2026-11-14 21:39:00', 'Created the owner account and the master key', 'Owner')")
        con.commit()
        con.close()
        rows = U._q(A, "SELECT id, at FROM audit WHERE action LIKE 'Created the owner account%'")
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('timezone', 'America/New_York') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.execute("DELETE FROM settings WHERE key LIKE 'audit_zone%'")
    con.commit()
    con.close()
    monkeypatch.setattr(A, "HOST_TZ", "")
    with A.app.app_context():
        A.apply_settings()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stored = rows[0][1]
    want = datetime.strptime(stored, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
    want_s = want.strftime("%-I:%M ") + ("am" if want.hour < 12 else "pm")

    def owner_line():
        page = c.get("/security", environ_base=E).get_data(as_text=True)
        acts = page.split('id="account-activity"', 1)[1].split("</details>", 1)[0]
        return acts.split("Created the owner account", 1)[0].rsplit('class="htime">', 1)[1].split("<", 1)[0]

    assert owner_line() == want_s
    with A.app.test_request_context():
        A.db()
        A.audit("Checked the master key")
        A.db().commit()
    assert owner_line() == want_s
    assert U._q(A, "SELECT at FROM audit WHERE id=?", (rows[0][0],))[0][0] == stored
