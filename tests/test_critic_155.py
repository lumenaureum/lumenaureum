import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, _page, _session, env

def q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def test_pencil_in_on_a_quoted_lead_with_every_step_done_stays_quoted(env):
    A, c = env
    _session(A, "quoted", days=14)
    run(A, "INSERT INTO lines (session_id, label, amount) VALUES (1, 'Family session', 375)")
    run(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=1 AND stage IN ('inquiry','quoted')",
        ((A.today() - timedelta(days=3)).isoformat(),))
    open_before = q(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND done IS NULL")[0][0]
    r = c.post("/session/1/pencil", data=dict(act="pencil", back="/c/1?s=1"), environ_base=E)
    assert r.status_code == 302
    row = q(A, "SELECT stage, pencil_until FROM sessions WHERE id=1")[0]
    assert row["stage"] == "quoted" and row["pencil_until"]
    hist = [h[0] for h in q(A, "SELECT action FROM audit WHERE session_id=1 ORDER BY id")]
    assert not any("Moved to Booked" in h or "is now the booked date" in h for h in hist), hist
    assert q(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND done IS NULL")[0][0] == open_before
    for act in ("until", "extend", "release"):
        data = dict(act=act, back="/c/1?s=1")
        if act == "until":
            data["until"] = (A.today() + timedelta(days=5)).isoformat()
        c.post("/session/1/pencil", data=data, environ_base=E)
        assert q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "quoted", act

@pytest.mark.parametrize("stage", ["quoted", "booked"])
def test_a_session_billed_on_its_plans_quote_shows_one_line_and_no_quote_tools(env, stage):
    A, c = env
    with A.app.test_request_context():
        head = A.create_session(1, "family", stage="quoted", shoot_date=(A.today() + timedelta(days=60)).isoformat())
        member = A.create_session(1, "family", stage=stage, shoot_date=(A.today() + timedelta(days=20)).isoformat())
    run(A, "INSERT INTO lines (session_id, label, amount) VALUES (?, 'Wedding day', 3000)", (head,))
    assert c.post(f"/session/{member}/plan", data={"plan_of": str(head)}, environ_base=E).status_code == 302
    with A.app.test_request_context():
        assert A.billed_on_plan(member)
    page = c.get(f"/c/1?s={member}", environ_base=E).get_data(as_text=True)
    bill = page.split('id="billing"', 1)[1].split('id="shoot"', 1)[0] if 'id="shoot"' in page else page.split('id="billing"', 1)[1]
    assert "Billing: on the quote of" in page
    for gone in ("Pick the first line", "Add a line", "Lines from other session types", "Add the line",
                 f"/session/{member}/discount", f"/session/{member}/portfolio", "Discount reason"):
        assert gone not in bill, gone
    assert f'href="/session/{head}/quote"' in bill

    head_page = c.get(f"/c/1?s={head}", environ_base=E).get_data(as_text=True)
    assert "Add the line" in head_page and f"/session/{head}/portfolio" in head_page

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

def _tab(browser, c, width=1440):
    from test_280_155 import BASE, _tab as tab280
    return BASE, tab280(browser, c, width)

def test_online_galleries_page_opens_with_no_unsaved_changes(browser, env):

    A, c = env
    BASE, (ctx, p) = _tab(browser, c)
    try:
        p.goto(BASE + "/setup/galleries")
        assert p.locator("select[name=gallery_type]").count() == 1
        shown = p.evaluate("""() => [...document.querySelectorAll('[data-ui-unsaved]')].filter(b => !b.hidden)
            .map(b => { const f = b.closest('form'); const sk = ['hidden','submit','button','reset','image','file'];
              return [...f.elements].filter(el => el.name && !sk.includes(el.type) && (
                el.type === 'checkbox' || el.type === 'radio' ? el.checked !== el.defaultChecked
                : el.tagName === 'SELECT' ? [...el.options].some(o => o.selected !== o.defaultSelected)
                : el.value !== el.defaultValue)).map(el => el.name).join(','); })""")
        assert shown == [], shown
    finally:
        ctx.close()

@pytest.fixture
def no_tz(tmp_path, monkeypatch):

    from test_setup_settings_101 import _env
    from helpers import signed_in_client
    A = _env(tmp_path, monkeypatch)
    return A, signed_in_client(A)

def test_profile_asks_for_the_time_zone_once_until_one_is_saved(no_tz):
    import re
    A, c = no_tz
    page = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    assert 'id="tz"' in page and "Set your time zone" in page
    assert len(re.findall(r'<select[^>]*name="timezone"', page)) == 1, re.findall(r'<select[^>]*name="timezone"[^>]*>', page)
    assert page.count('name="browser_tz"') == 1

    r = c.post("/setup/business", data=dict(home_zip="19103", back="/settings?tab=profile#location"), environ_base=E)
    assert r.status_code == 302
    con = A.connect()
    try:
        got = dict(con.execute("SELECT key, value FROM settings WHERE key IN ('timezone', 'home_zip')").fetchall())
    finally:
        con.close()
    assert got.get("home_zip") == "19103" and not got.get("timezone")

    c.post("/settings/timezone", data=dict(timezone="America/Chicago"), environ_base=E)
    page = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    assert 'id="tz"' not in page and len(re.findall(r'<select[^>]*name="timezone"', page)) == 1
    assert "<option selected>America/Chicago</option>" in page

def _booked_owing(A, c, shoot_days):

    with A.app.test_request_context():
        sid = A.create_session(1, "family", stage="quoted", shoot_date=(A.today() + timedelta(days=shoot_days)).isoformat())
    run(A, "INSERT INTO lines (session_id, label, amount) VALUES (?, 'Family session', 1000)", (sid,))
    with A.app.test_request_context():
        A.set_stage(sid, "booked")
        A.db().commit()
    run(A, "UPDATE sessions SET deposit_booked=300 WHERE id=?", (sid,))
    run(A, "INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?, 'deposit', 100, ?, 'check')",
        (sid, A.today().isoformat()))
    with A.app.test_request_context():
        A.sync_workflow_tasks(sid)
        A.db().commit()
        tt = A.totals(sid)
    return sid, tt

def _row(c, year):
    page = c.get(f"/money?year={year}", environ_base=E).get_data(as_text=True)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.split('id="upcoming"', 1)[1].split("ui-remind-row", 2)[1]))

def test_finances_after_the_shoot_asks_for_the_balance_not_a_deposit_due_later(env, monkeypatch):
    A, c = env
    sid, tt = _booked_owing(A, c, -60)
    assert tt["deposit_short"] > 0.005 and tt["due"] > tt["deposit_short"]
    monkeypatch.setattr(A, "deposit_step_due", lambda sid_: (A.today() + timedelta(days=1)).isoformat())
    with A.app.test_request_context():
        rem = A.payment_reminder(A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True))
        due_txt = A.money(tt["due"])
    assert rem["kind"] == "balance" and rem["late"], rem
    row = _row(c, (A.today() - timedelta(days=60)).year)
    assert f"Overdue {due_txt}" in row and "Deposit" not in row and "Balance " + due_txt in row, row

def test_finances_says_overdue_for_the_late_deposit_only_not_the_whole_balance(env, monkeypatch):
    A, c = env
    sid, tt = _booked_owing(A, c, 40)
    due_ = {"d": (A.today() - timedelta(days=2)).isoformat()}
    monkeypatch.setattr(A, "deposit_step_due", lambda sid_: due_["d"])
    with A.app.test_request_context():
        dep_txt, due_txt = A.money(tt["deposit_short"]), A.money(tt["due"])
    row = _row(c, (A.today() + timedelta(days=40)).year)
    assert f"Overdue {dep_txt}" in row and f"Overdue {due_txt}" not in row and "Deposit " + dep_txt in row, row
    due_["d"] = A.today().isoformat()
    row = _row(c, (A.today() + timedelta(days=40)).year)
    assert "Overdue" not in row and "Deposit " + dep_txt in row, row
