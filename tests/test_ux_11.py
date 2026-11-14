import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

def _page(c, url):
    r = c.get(url, environ_base=E, follow_redirects=True)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _title(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return m.group(1).strip() if m else None

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _studio(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _sql(A, "UPDATE sessions SET shoot_date='2026-11-19', start_time='16:10' WHERE id=?", (sid,))
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Portrait session", "amount": "300"}, environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "method": "cash",
                                         "received": "2026-10-05"}, environ_base=E)
    return A, c, sid

PAGES = {"/": "Today", "/clients": "Clients", "/sessions": "Sessions", "/pipeline": "Board", "/money": "Finances",
         "/events": "Events", "/search?q=nobody": "Search", "/calendar": None, "/calendar/year": None, "/settings": "Settings",
         "/settings/wording/agreement": "Agreement wording", "/settings/wording/release": "Release wording",
         "/contact/new": "New contact", "/inquiry": "New inquiry", "/client/1": "Lane", "/c/1": "Lane", "/vault": "Vault",
         "/security": "Security", "/about": "About"}

def test_every_app_page_has_its_own_title(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    pages = dict(PAGES, **{f"/session/{sid}": "Lane"})
    for url, word in pages.items():
        t = _title(_page(c, url))
        assert t and t != "Lumen Aureum" and t.endswith(" - Lumen Aureum"), (url, t)
        assert "<" not in t and ">" not in t, (url, t)
        if word:
            assert word in t, (url, t)
    assert _title(_page(c, "/settings/wording/agreement")) == "Agreement wording - Lumen Aureum"

SECTION = {"/": "Today", "/clients": "Clients", "/client/1": "Clients", "/c/1": "Clients", "/sessions": "Sessions",
           "/pipeline": "Sessions", "/events": "Sessions", "/calendar": "Calendar", "/calendar/year": "Calendar",
           "/money": "Finances"}

def _nav(html):
    m = re.search(r'<nav class="sections" aria-label="Main">(.*?)</nav>', html, re.S)
    assert m, "no main navigation"
    return m.group(1)

def test_the_main_navigation_is_on_every_page_and_marks_where_you_are(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    for url, here in dict(SECTION, **{f"/session/{sid}": "Sessions"}).items():
        nav = _nav(_page(c, url))
        assert [x for x in re.findall(r">([^<]+)</a>", nav)] == ["Today", "Clients", "Sessions", "Calendar", "Finances"]
        current = re.findall(r'aria-current="page">([^<]+)</a>', nav)
        assert current == [here], (url, current)
        assert len(re.findall(r'class="on"', nav)) == 1
    nav = _nav(_page(c, "/settings"))
    assert "aria-current" not in nav
    for href, ep in (("/", "Today"), ("/clients", "Clients"), ("/sessions", "Sessions"), ("/calendar", "Calendar"), ("/money", "Finances")):
        assert f'href="{href}"' in nav, href
    assert 'class="skip" href="#main"' in _page(c, "/") and '<main id="main"' in _page(c, "/")

def test_the_clock_helper():
    sys.path.insert(0, ROOT)
    from formats import clock, month_label
    from datetime import date, datetime
    assert clock("16:10") == "4:10 pm" and clock("09:05") == "9:05 am"
    assert clock("00:00") == "12:00 am" and clock("12:30") == "12:30 pm" and clock("16:10:00") == "4:10 pm"
    assert clock(datetime(2026, 11, 19, 16, 10)) == "4:10 pm"
    assert clock("") == "" and clock(None) == "" and clock("soon") == "" and clock("25:00") == ""
    assert month_label("2026-10") == "Oct 2026" and month_label(date(2026, 1, 3)) == "Jan 2026"
    assert month_label("") == ""

def test_the_session_strip_and_documents_use_the_one_clock_style(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    page = _page(c, f"/session/{sid}")
    strip = re.search(r'<div class="k">Shoot</div><div class="v"[^>]*>(.*?)</div>', page).group(1)
    assert "4:10 pm" in strip and "16:10" not in strip
    for url in (f"/session/{sid}/agreement", f"/session/{sid}/agreement.pdf", f"/session/{sid}/details",
                f"/session/{sid}/details.pdf"):
        html = c.get(url, environ_base=E).get_data(as_text=True)
        assert "4:10 pm" in html, url
        assert "4:10 PM" not in html and "16:10" not in html and "04:10" not in html, url
    assert "4:10 pm" in _page(c, f"/c/1")

def test_light_strip_says_nothing_without_a_home_location(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    monkeypatch.setattr(A.sun, "HOME", None)
    page = _page(c, f"/session/{sid}")
    assert '<div class="k">Light</div>' not in page and ">to <" not in page
    with A.app.test_request_context():
        assert A.light("2026-11-19") is None
    monkeypatch.setattr(A.sun, "HOME", (40.7, -74.0))
    with A.app.test_request_context():
        L = A.light("2026-11-19")
    assert re.fullmatch(r"\d{1,2}:\d\d [ap]m", L["golden"]) and re.fullmatch(r"\d{1,2}:\d\d [ap]m", L["sunset"])
    assert L["text"] == f"Golden hour {L['golden']}, sunset {L['sunset']}"

def test_finances_months_read_like_oct_2026(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    page = _page(c, "/money?year=2026")
    by_month = page.split("<h2>By month</h2>")[1].split("</div></div>")[0]
    assert "Oct 2026" in by_month and "2026-10" not in by_month

def test_the_statement_moves_by_named_months(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    page = _page(c, "/client/1/statement?m=2026-10")
    assert "&lsaquo; Sep 2026</a>" in page and "Nov 2026 &rsaquo;</a>" in page
    assert "October 5, 2026 - deposit" in page

def test_icon_only_buttons_have_names(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/money", data={"what": "expense", "label": "Parking", "amount": "12"}, environ_base=E)
    c.post("/money/overhead", data={"label": "Lens cloth", "amount": "9", "date": "2026-11-02"}, environ_base=E)
    pages = [_page(c, u) for u in (f"/session/{sid}", "/c/1", "/client/1", "/money?year=2026")]
    seen = 0
    for html in pages:
        for b in re.findall(r"<button\b[^>]*>[^<]{0,8}</button>", html):
            text = re.sub(r"<[^>]+>|\s", "", b.split(">", 1)[1])
            if text in ("x", "×", "&times;", "…", "&hellip;", ""):
                seen += 1
                assert 'aria-label="' in b and not re.search(r'aria-label=""', b), b
        assert not re.search(r"<button[^>]*>x</button>", html)
    assert seen >= 4
    s = pages[0]
    assert 'aria-label="Remove line Portrait session"' in s and 'aria-label="Remove expense Parking"' in s
    assert re.search(r'class="linkbtn"[^>]*aria-label="Void deposit payment of \$100"', s)
    assert 'aria-label="Remove expense Lens cloth"' in pages[3]
    assert 'aria-label="Remove line Portrait session"' in pages[1]
    for html in pages:
        for summ in re.findall(r"<summary\b[^>]*>&hellip;</summary>", html):
            assert "aria-label=" in summ, summ

def _submit_buttons(form_html):
    return [b for b in re.findall(r"<button\b[^>]*>.*?</button>", form_html, re.S) if 'type="button"' not in b]

def test_enter_presses_the_main_button_on_forms_with_several(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)

    form = re.search(r'<form method="post" class="card f".*?</form>', _page(c, "/contact/new"), re.S).group(0)
    btns = _submit_buttons(form)
    assert len(btns) == 2 and "Save and start an inquiry" in btns[0] and 'class="primary"' in btns[0]
    assert 'name="then" value="inquiry"' in btns[0]
    r = c.post("/contact/new", data={"contact_name": "Rowan Hale", "then": "inquiry"}, environ_base=E)
    assert "/inquiry" in r.headers["Location"]

    form = re.search(r'<form method="post" class="card f" id="f".*?</form>', _page(c, "/setup/business"), re.S).group(0)
    first = _submit_buttons(form)[0]
    assert "data-enter-default" in first and 'name="skip"' not in first

    form = re.search(r'<form[^>]*>(?:(?!</form>).)*name="start_time".*?</form>', _page(c, f"/session/{sid}"), re.S).group(0)
    assert 'class="primary"' in _submit_buttons(form)[0]

def test_the_inquiry_steps_put_focus_where_the_keyboard_goes_next(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    types = _page(c, "/inquiry?hid=1")
    first = re.search(r'<a class="btn quiet" href="[^"]*type=(\w+)"( autofocus)?>', types)
    assert first.group(1) == "family" and first.group(2)
    assert re.search(r'name="shoot_date"[^>]*autofocus', _page(c, "/inquiry?hid=1&type=family"))
    focus = _page(c, "/c/1")
    assert '<a href="#billing">Skip to billing</a>' in focus and "data-focus-after" in focus

def test_adding_a_step_on_the_focus_page_stays_there(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    r = c.post(f"/session/{sid}/task", data={"title": "Scout the park", "back": "/c/1"}, environ_base=E)
    assert r.headers["Location"].endswith("/c/1")

def test_the_document_bar_puts_close_last(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    bar = re.search(r'<div class="bar">.*?</div>', _page(c, f"/session/{sid}/details"), re.S).group(0)
    links = re.findall(r"<a [^>]*>([^<]+)", bar)
    assert links[-1] == "Close" and "Email it(" not in bar

def test_booked_says_which_booking_steps_are_still_open(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    _sql(A, "DELETE FROM payments WHERE session_id=?", (sid,))
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "booked"

    def hint():
        with A.app.test_request_context():
            A.db()
            return A.booked_hint(sid)
    assert hint() == "deposit and agreement still open"
    for url in ("/", "/c/1", "/client/1", f"/session/{sid}", "/pipeline"):
        assert "eposit and agreement still open" in _page(c, url), url
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "method": "cash"}, environ_base=E)
    assert hint() == "agreement still open"
    assert "&middot; agreement still open" in _page(c, "/c/1")
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    if _q(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "booked":
        assert hint() == ""
    assert "still open</span>" not in _page(c, "/c/1")

    _sql(A, "UPDATE documents SET status='sent', signed=NULL WHERE session_id=? AND kind='agreement'", (sid,))
    _sql(A, "UPDATE sessions SET stage='booked', hold_stage='booked' WHERE id=?", (sid,))
    assert hint() == "agreement still open"
    _sql(A, "DELETE FROM payments WHERE session_id=?", (sid,))
    _sql(A, "UPDATE documents SET status='signed', signed='2026-11-01' WHERE session_id=? AND kind='agreement'", (sid,))
    assert hint() == "deposit still open"
    _sql(A, "UPDATE sessions SET stage='quoted' WHERE id=?", (sid,))
    assert hint() == ""

def test_header_and_now_block_name_the_same_stage(tmp_path, monkeypatch):

    A, c, sid = _studio(tmp_path, monkeypatch)
    page = _page(c, "/c/1")
    assert re.search(r'<div class="now-head" id="now-head" data-refresh>', page)
    assert 'id="hero-now" data-refresh' in page
    stage = _q(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0]
    label = A.C.STAGE_LABELS[stage]
    head = re.search(r'<div class="hn-stage">([^<&]+)', page).group(1).strip()
    now = re.search(r'<h2 class="now-title">Now: ([^<]+)</h2>', page).group(1).strip()
    assert head == now == label

    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, ?, '2026-01-01')", (sid, stage))
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, ?, date('now','localtime'))", (sid, stage))
    with A.app.test_request_context():
        A.db()
        assert A.days_in_stage(dict(id=sid, stage="inquiry")) <= 1
