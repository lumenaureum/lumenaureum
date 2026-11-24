import html
import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_ui_library import bare_labels, one_offs

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'The Sample Family')")
        con.execute("INSERT INTO people (household_id, name, role, phone, email) VALUES "
                    "(1, 'Pat Sample', 'adult', '555-0100', 'pat@example.com')")
        con.execute("""INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops, intake)
                       VALUES (1, 1, 'wedding', 'booked', ?, '15:00', 'Town Hall\nRiverside Park', ?)""",
                    ((A.today() + timedelta(days=5)).isoformat(), '{"timeline": "3:00 ceremony\\n5:00 reception"}'))
        con.commit()
    finally:
        con.close()
    return A, c

def turn_on(A, on=True):
    sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_crew', ?)", ("1" if on else "0",))

def sql(A, s, args=()):
    con = A.connect()
    try:
        rows = con.execute(s, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def text(c, path):
    t = c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).replace(" ,", ",")

def post(c, path, **data):
    return c.post(path, data=data, environ_base=E)

def add_new(c, name="Alex Rivera", role="Second shooter", call="14:30", **more):
    return post(c, "/session/1/crew", new="1", name=name, role=role, call_time=call, back="/c/1?s=1#crew", **more)

def test_off_by_default_and_nothing_shows(env):
    A, c = env
    assert "Crew" not in text(c, "/c/1?s=1")
    assert c.get("/crew", environ_base=E).status_code == 404
    assert c.get("/session/1/call-sheet", environ_base=E).status_code == 404
    assert add_new(c).status_code == 404
    assert "Crew and call sheets" in text(c, "/settings/plugins")
    assert not sql(A, "SELECT 1 FROM crew")

def test_crew_fold_shows_from_booked_and_stays_out_of_the_way(env):
    A, c = env
    turn_on(A)
    assert "Crew (0)" in text(c, "/c/1?s=1")
    sql(A, "UPDATE sessions SET stage='quoted' WHERE id=1")
    assert "Crew (" not in text(c, "/c/1?s=1")
    add_new(c)
    assert "Crew (1)" in text(c, "/c/1?s=1")
    sql(A, "UPDATE sessions SET stage='lost' WHERE id=1")
    assert "Crew (" not in text(c, "/c/1?s=1")
    assert 'href="/crew"' not in c.get("/", environ_base=E).get_data(as_text=True)

def test_add_someone_new_and_from_the_list(env):
    A, c = env
    turn_on(A)
    assert add_new(c, phone="(555) 010-2000", email="alex@example.com").status_code == 302
    assert [tuple(r) for r in sql(A, "SELECT name, role, phone, email FROM crew")] == \
        [("Alex Rivera", "Second shooter", "(555) 010-2000", "alex@example.com")]
    assert [tuple(r) for r in sql(A, "SELECT session_id, role, call_time FROM session_crew")] == [(1, "Second shooter", "14:30")]
    t = text(c, "/c/1?s=1")
    assert "Alex Rivera, Second shooter, called 2:30 pm" in t

    post(c, "/crew/add", name="Sam Lee", role="Assistant", rate="150")
    sid_ = sql(A, "SELECT id FROM crew WHERE name='Sam Lee'")[0][0]
    raw = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert f'<option value="{sid_}">Sam Lee, Assistant</option>' in raw
    assert ">Alex Rivera" not in re.search(r'<select name="crew_id".*?</select>', raw, re.S).group(0)
    post(c, "/session/1/crew", crew_id=str(sid_), call_time="")
    t = text(c, "/c/1?s=1")
    assert "Crew (2)" in t and "Sam Lee, Assistant, called at the start" in t
    post(c, "/session/1/crew", crew_id=str(sid_))
    assert sql(A, "SELECT COUNT(*) FROM session_crew")[0][0] == 2

def test_refusals_save_nothing(env):
    A, c = env
    turn_on(A)
    assert add_new(c, name="  ").status_code == 302
    assert add_new(c, call="25:99").status_code == 302
    assert add_new(c, email="not-an-email").status_code == 302
    post(c, "/session/1/crew", crew_id="999")
    assert not sql(A, "SELECT 1 FROM crew") and not sql(A, "SELECT 1 FROM session_crew")
    assert post(c, "/session/999/crew", new="1", name="X").status_code == 404

def test_change_call_and_take_off(env):
    A, c = env
    turn_on(A)
    add_new(c)
    aid = sql(A, "SELECT id FROM session_crew")[0][0]
    post(c, f"/session/1/crew/{aid}", role="Assistant", call_time="13:00")
    assert "Alex Rivera, Assistant, called 1:00 pm" in text(c, "/c/1?s=1")
    post(c, f"/session/1/crew/{aid}/off")
    assert not sql(A, "SELECT 1 FROM session_crew") and sql(A, "SELECT 1 FROM crew")
    sql(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (2, 1, 'family', 'booked')")
    add_new(c, name="Other")
    oid = sql(A, "SELECT id FROM session_crew")[0][0]
    assert post(c, f"/session/2/crew/{oid}/off").status_code == 404

def test_pay_is_an_ordinary_session_expense(env):
    A, c = env
    turn_on(A)
    post(c, "/crew/add", name="Sam Lee", role="Second shooter", rate="250")
    post(c, "/session/1/crew", crew_id=str(sql(A, "SELECT id FROM crew")[0][0]))
    aid = sql(A, "SELECT id FROM session_crew")[0][0]
    raw = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert 'value="250.00"' in raw and "Pay Sam<" in raw
    day = A.today().isoformat()
    r = post(c, f"/session/1/crew/{aid}/pay", amount="275", date=day, once="t1", back="/c/1?s=1#crew")
    assert r.status_code == 302
    assert [tuple(e) for e in sql(A, "SELECT session_id, label, amount, date FROM expenses")] == \
        [(1, "Second shooter: Sam Lee", 275.0, day)]
    t = text(c, "/c/1?s=1")
    assert "Paid $275.00 on" in t and "Pay Sam " not in t
    assert "Second shooter: Sam Lee" in text(c, "/session/1/edit")
    post(c, f"/session/1/crew/{aid}/pay", amount="275", date=day, once="t2")
    assert sql(A, "SELECT COUNT(*) FROM expenses")[0][0] == 1

    eid = sql(A, "SELECT id FROM expenses")[0][0]
    post(c, f"/money/expense/{eid}/delete", back="/c/1?s=1")
    assert "Pay Sam " in text(c, "/c/1?s=1")
    assert any("crew pay expense" in r_[0] for r_ in sql(A, "SELECT action FROM audit"))

def test_pay_follows_the_expense_rules(env):
    A, c = env
    turn_on(A)
    add_new(c)
    aid = sql(A, "SELECT id FROM session_crew")[0][0]
    for amount, day in (("", A.today().isoformat()), ("-5", A.today().isoformat()), ("abc", A.today().isoformat()),
                        ("100", (A.today() + timedelta(days=5)).isoformat()),
                        ("100", f"{A.today().year - 1}-06-01"),
                        ("99999999", A.today().isoformat())):
        r = post(c, f"/session/1/crew/{aid}/pay", amount=amount, date=day, back="/c/1?s=1#crew")
        assert r.status_code == 302, (amount, day)
    assert not sql(A, "SELECT 1 FROM expenses")
    assert not sql(A, "SELECT expense_id FROM session_crew WHERE expense_id IS NOT NULL")

def test_call_sheet_page_and_pdf(env):
    A, c = env
    turn_on(A)
    sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('photographer_name', 'Jo Shooter')")
    add_new(c, phone="555-0142", email="alex@example.com")
    post(c, "/session/1/call-sheet/notes", notes="Park behind the hall.\nBring the long lens.")
    page = c.get("/session/1/call-sheet", environ_base=E).get_data(as_text=True)
    t = text(c, "/session/1/call-sheet")
    assert "Call sheet" in t and "Jo Shooter" in t
    assert "2:30 pm Alex Rivera, Second shooter 555-0142 alex@example.com" in t
    assert "3:00 pm at Town Hall" in t and "Riverside Park" in t
    assert "3:00 ceremony" in t and "5:00 reception" in t
    assert "Park behind the hall." in t and "Bring the long lens." in t
    assert 'href="tel:5550142"' in page
    assert "Pat Sample" not in t and "555-0100" not in t
    assert "Email it" not in page and "mailto:" not in page
    post(c, "/session/1/call-sheet/client", show="1")
    t = text(c, "/session/1/call-sheet")
    assert "Pat Sample" in t and "555-0100" in t and "pat@example.com" in t
    post(c, "/session/1/call-sheet/client")
    assert "Pat Sample" not in text(c, "/session/1/call-sheet")
    r = c.get("/session/1/call-sheet.pdf", environ_base=E)
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:4] == b"%PDF"
    assert "call-sheet-the-sample-family" in r.headers["Content-Disposition"]

def test_crew_list_page_edit_and_delete(env):
    A, c = env
    turn_on(A)
    post(c, "/crew/add", name="Sam Lee", role="Assistant")
    cid = sql(A, "SELECT id FROM crew")[0][0]
    post(c, "/session/1/crew", crew_id=str(cid))
    t = text(c, "/crew")
    assert "Sam Lee, Assistant" in t and "1 session" in t
    post(c, f"/crew/{cid}", name="Sam Lee", role="Second shooter", phone="555-0199", email="", rate="1,200")
    assert [tuple(r) for r in sql(A, "SELECT role, phone, rate_cents FROM crew")] == [("Second shooter", "555-0199", 120000)]
    post(c, f"/crew/{cid}", name="Sam Lee", rate="lots")
    assert sql(A, "SELECT rate_cents FROM crew")[0][0] == 120000
    post(c, f"/crew/{cid}/delete")
    assert not sql(A, "SELECT 1 FROM crew") and not sql(A, "SELECT 1 FROM session_crew")

def test_deleting_the_session_takes_its_crew_rows(env):
    A, c = env
    turn_on(A)
    add_new(c)
    post(c, "/session/1/call-sheet/notes", notes="x")
    sql(A, "DELETE FROM sessions WHERE id=1")
    assert not sql(A, "SELECT 1 FROM session_crew") and not sql(A, "SELECT 1 FROM call_sheets")
    assert sql(A, "SELECT 1 FROM crew")

def test_crew_is_not_in_the_settings_file(env):
    A, c = env
    turn_on(A)
    add_new(c, phone="555-0142")
    r = c.get("/settings/export.json", environ_base=E)
    assert r.status_code == 200
    blob = r.get_data(as_text=True)
    assert "Alex Rivera" not in blob and "555-0142" not in blob

def test_templates_use_the_library():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "crew.py")
    spec = importlib.util.spec_from_file_location("crew_tpl", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for name in ("PANEL", "LIST_PAGE", "SHEET"):
        tpl = getattr(m, name)
        assert not one_offs(tpl), (name, one_offs(tpl))
        assert not bare_labels(tpl), (name, bare_labels(tpl))

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

def _tab(browser, c, width):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        r = c.get(path, environ_base=E)
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_the_crew_fold_and_the_call_sheet_fit_a_phone(browser, env, tmp_path):
    A, c = env
    turn_on(A)
    add_new(c, name="Alexandra Rivera-Montgomery", phone="(555) 010-2000", email="alexandra.rivera@example.com")
    post(c, "/crew/add", name="Sam Lee", role="Assistant", rate="150")
    post(c, "/session/1/call-sheet/notes", notes="Park behind the hall, past the long white fence on the left side.")
    ctx, p = _tab(browser, c, 390)
    try:
        for path in ("/c/1?s=1", "/session/1/call-sheet", "/crew"):
            p.goto(BASE + path)
            if path.startswith("/c/"):
                p.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                p.evaluate("document.querySelectorAll('.ui-inline').forEach(d => d.style.display = 'block')")
            p.screenshot(path=str(tmp_path / (path.strip('/').split('?')[0].replace('/', '-') + ".png")), full_page=True)
            assert p.evaluate("document.scrollingElement.scrollWidth") <= 390, path
    finally:
        ctx.close()
