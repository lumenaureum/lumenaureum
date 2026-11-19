import io
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, browser, env
from test_today_15 import _day, _x

ROOT = Path(__file__).resolve().parent.parent

def _set(A, k, v):
    _x(A, "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _check_in_client(A):
    _x(A, "INSERT INTO households (id, name) VALUES (2, 'Ellis Family')")
    _x(A, "INSERT INTO people (household_id, name, role, email) VALUES (2, 'Jo Ellis', 'parent', 'jo@example.com')")
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (10, 2, 'family', 'closed', ?)", (_day(A, -500),))

def _checked_in(A, days_ago):
    _x(A, "INSERT INTO log (household_id, channel, summary, at) VALUES (2, 'email', 'Checked in by email', ?)",
       (_day(A, -days_ago) + " 09:00:00",))

def _due(A):
    with A.app.test_request_context():
        return A.check_in_due()

def test_check_in_wait_is_a_setting_default_six_months(env):
    A, c = env
    _check_in_client(A)
    _checked_in(A, 7 * 30)

    due = lambda: 2 in _due(A)
    assert due()
    _set(A, "check_in_snooze_months", "8")
    assert not due()
    _set(A, "check_in_snooze_months", "")
    _checked_in(A, 60)
    assert not due()
    page = _page(c, "/settings")
    assert 'name="check_in_snooze_months"' in page and 'value="6"' in page and "After a check-in, wait (months)" in page
    c.post("/settings", data={"main_posted": "1", "credit": "1", "check_in_snooze_months": "1", "idle_signout_hours": "8"}, environ_base=E)
    with A.app.test_request_context():
        assert A.check_in_snooze() == 1 and A.idle_hours() == 8
    c.post("/settings", data={"main_posted": "1", "credit": "1", "check_in_snooze_months": "99"}, environ_base=E)
    with A.app.test_request_context():
        assert A.check_in_snooze() == 1

def test_business_client_box_follows_the_rule_and_changes_both_ways(env):
    A, c = env
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'booked', ?)", (_day(A, 10),))
    with A.app.test_request_context():
        s = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert not A.is_business_client(1) and not A.business_client(s)
    page = _page(c, "/client/1")
    assert re.search(r'<input type="checkbox" name="business" value="1">Business client', page)
    form = {"name": "Morgan", "company": "", "address": "", "source": "", "notes": "", "business_posted": "1"}
    c.post("/client/1", data=dict(form, business="1"), environ_base=E)
    with A.app.test_request_context():
        s = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert A.business_choice(1) is True and A.business_client(s)
    assert 'name="business" value="1" checked' in _page(c, "/client/1")
    c.post("/client/1", data=dict(form, company="Morgan Media"), environ_base=E)
    with A.app.test_request_context():
        assert A.business_rule(1) and A.business_choice(1) is False and not A.is_business_client(1)
    c.post("/client/1", data=dict(form, company="Morgan Media", business="1"), environ_base=E)
    with A.app.test_request_context():
        assert A.business_choice(1) is None and A.is_business_client(1)

def test_finances_owed_only_once_the_invoice_is_sent(env):
    A, c = env
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'delivered', ?)", (_day(A, -30),))
    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 318, 'fee')")
    page = _page(c, "/money")
    assert 'id="to-invoice"' in page and "To invoice" in page and "invoice not sent yet" in page
    assert 'id="owed"' not in page
    assert _page(c, "/sessions?owing=1").count('href="/c/1') == 0
    c.post("/session/1/invoice/deposit-sent", data={"back": "/"}, environ_base=E)
    page = _page(c, "/money")
    assert 'id="owed"' in page and "Owed to you" in page and 'id="to-invoice"' not in page

def test_partial_payment_words_and_the_bubble():
    for p in list((ROOT / "templates").glob("*.html")) + [ROOT / "app.py"] + list((ROOT / "plugins").glob("*.py")):
        assert not re.search(r"\bpart (payment|paid)\b", p.read_text(), re.I), p
    src = (ROOT / "plugins" / "prints.py").read_text()
    assert 'partially paid, the rest unpaid after' in src and 'help="A partial payment keeps the invoice from expiring."' in src
    assert "(a part" not in src
    today_ = (ROOT / "templates" / "_today.html").read_text()
    assert "{% if do.help %}{{ help(do.help) }}{% endif %}" in today_

def test_year_summary_says_its_basis_and_other_matches_the_payment_method(env):
    A, c = env
    with A.app.test_request_context():
        v = A.year_summary(A.today().year)
        html = A.render_template("year_summary.html", v=v, pdf=True)
    assert "<td>Basis</td><td>Cash (money received)</td>" in html
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'delivered', ?)", (_day(A, -30),))
    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 300, 'fee')")
    r = c.post("/session/1/money", data={"what": "payment", "kind": "balance", "amount": "120", "method": "other",
                                         "note": "a trade: framing", "received": A.today().isoformat()}, environ_base=E)
    assert r.status_code in (302, 303)
    with A.app.test_request_context():
        assert A.year_summary(A.today().year)["other"] == 120

def test_idle_sign_out_after_the_set_time_and_only_real_taps_count(env):
    A, c = env
    import auth
    with c.session_transaction() as s_:
        sid = s_["sid"]
    with A.app.test_request_context():
        assert A.idle_hours() == 8
    assert 'data-idle="28800"' in _page(c, "/") and ">Stay signed in</button>" in _page(c, "/")
    auth.LAST_SEEN[sid] = time.time() - 7 * 3600
    before = auth.LAST_SEEN[sid]
    r = c.get("/session/idle", environ_base=E)
    assert r.status_code == 200 and 3500 < r.get_json()["left"] <= 3600
    c.get("/", headers={"Sec-Fetch-Mode": "cors"}, environ_base=E)
    c.get("/", headers={"X-LA-Background": "1"}, environ_base=E)
    assert auth.LAST_SEEN[sid] == before
    assert c.post("/session/stay", headers={"X-LA-Inplace": "1"}, environ_base=E).get_json()["ok"]
    assert auth.LAST_SEEN[sid] > before
    auth.LAST_SEEN[sid] = time.time() - 8 * 3600 - 5
    r = c.get("/c/1", environ_base=E)
    assert r.status_code in (302, 303) and "/login" in r.headers["Location"] and "idle=1" in r.headers["Location"]
    assert sid not in auth.SESSIONS
    assert "Signed out after a while with no activity" in c.get("/login?idle=1", environ_base=E).get_data(as_text=True)

def test_the_update_notice_says_an_update_signs_everyone_out():
    src = (ROOT / "templates" / "today.html").read_text()
    assert "An update signs everyone out, so each device asks you to sign in again." in src

def test_idle_warning_in_a_browser_and_stay_signed_in(browser, env):

    A, c = env
    import auth
    from urllib.parse import urlsplit
    _set(A, "idle_signout_hours", "1")
    with c.session_transaction() as s_:
        sid = s_["sid"]
    keep = ("content-type", "sec-fetch-mode", "x-la-background", "x-la-inplace")

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in keep}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303):
            loc = r.location
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type",)})
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    ctx.route(BASE + "/**", handle)
    p = ctx.new_page()
    try:
        p.clock.install()
        p.goto(BASE + "/settings")
        warn = p.locator("[data-idle-warn]")
        assert warn.is_hidden()
        auth.LAST_SEEN[sid] = time.time() - 3600 + 100
        before = auth.LAST_SEEN[sid]
        p.clock.run_for(3600 * 1000 - 119 * 1000)
        warn.wait_for(state="visible")
        assert "signed out in about 2 minutes" in warn.inner_text()
        assert auth.LAST_SEEN[sid] == before
        warn.get_by_role("button", name="Stay signed in").click()
        warn.wait_for(state="hidden")
        assert auth.LAST_SEEN[sid] > before and sid in auth.SESSIONS
    finally:
        ctx.close()

def _conf(**kw):
    import settings_io
    c = dict(name="Lane Studio", owner="Lane Studio", tagline="Portraits in Springfield", intro="Families, seniors, newborns",
             description="", about="I photograph families where they live.\n\n## Prices\nAsk me.", email="hi@lane.example",
             phone="", address="12 Elm St, Springfield, IL", site_url="https://lane.example/", site_path="/", logo=None,
             palette=settings_io.palette("#0d0c0b"), dark=True, font="", credit=False, favicon=None, year=2026,
             htaccess=False, php=False, inquiry=None)
    c.update(kw)
    return c

def test_studio_page_default_icon_upload_wins_and_every_file_is_there(tmp_path):
    from helpers import fresh_modules
    fresh_modules()
    from plugins import studio_page as SP
    from PIL import Image
    built = SP.build_site(tmp_path / "a", _conf(), [])
    d = tmp_path / "a"
    for f in ("index.html", "about/index.html", "contact/index.html", "404.html", "robots.txt", "sitemap.xml",
              "favicon.ico", "apple-touch-icon.png", "llms.txt"):
        assert (d / f).is_file() and f in built.files, f
    assert (d / "favicon.ico").read_bytes() == (ROOT / "static" / "brand" / "favicon.ico").read_bytes()
    assert '<link rel="icon" href="favicon.ico" sizes="any">' in (d / "index.html").read_text()
    assert "Sitemap: https://lane.example/sitemap.xml" in (d / "robots.txt").read_text()
    b = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 0, 0, 255)).save(b, "PNG")
    SP.build_site(tmp_path / "b", _conf(favicon=b.getvalue()), [])
    assert (tmp_path / "b" / "favicon.ico").read_bytes() != (ROOT / "static" / "brand" / "favicon.ico").read_bytes()

def test_llms_txt_says_only_what_the_site_shows(tmp_path):
    from helpers import fresh_modules
    fresh_modules()
    from plugins import studio_page as SP
    SP.build_site(tmp_path / "a", _conf(), [])
    t = (tmp_path / "a" / "llms.txt").read_text()
    assert t.startswith("# Lane Studio\n\n> Portraits in Springfield\n")
    assert "I photograph families where they live." in t and "Prices" not in t and "Ask me" not in t
    assert "Where: 12 Elm St, Springfield, IL" in t
    assert "- [About](https://lane.example/about/)" in t
    assert "- [Send an inquiry](https://lane.example/contact/): email hi@lane.example" in t
    assert "/g/" not in t and "galler" not in t.lower()
    SP.build_site(tmp_path / "b", _conf(address="", llms=False), [])
    assert not (tmp_path / "b" / "llms.txt").exists()
    SP.build_site(tmp_path / "c", _conf(draft=True), [])
    assert not (tmp_path / "c" / "llms.txt").exists()

def test_llms_switch_on_by_default_and_found_by_settings_search(env):
    A, c = env
    with A.app.test_request_context():
        assert A.settings_find("search tools")[0]["text"] == "Studio page: summary for modern search tools (llms.txt)"
        assert A.settings_find("llms")[0]["link"].endswith("#sp-llms")
    src = (ROOT / "plugins" / "studio_page.py").read_text()
    assert 'llms=setting("site_llms") != "0"' in src and 'id="sp-llms"' in src

def test_backed_up_another_way_asks_in_place_and_keeps_the_page_without_script(env):
    A, c = env
    with A.app.test_request_context():
        html = A.render_template_string(
            '{% import "_ui.html" as ui with context %}{{ ui.row_more(dict(what="Back up photos"), "/", [m]) }}',
            m=dict(verb="I backed it up another way", post="/session/1/backup/elsewhere", fields={},
                   confirm=A.BACKUP_ELSEWHERE_ASK, confirm_sets="sure"))
    assert 'data-confirm="Mark Upload original photos done?' in html and 'data-confirm-sets="sure"' in html
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'shot', ?)", (_day(A, -2),))
    r = c.post("/session/1/backup/elsewhere", data={"back": "/c/1"}, environ_base=E)
    assert r.status_code == 200 and "encrypted by it" in r.get_data(as_text=True)
    assert c.post("/session/1/backup/elsewhere", data={"back": "/c/1", "sure": "1"}, environ_base=E).status_code in (302, 303)
    js = (ROOT / "static" / "ui.js").read_text()
    assert "f.dataset.confirmSets" in js
