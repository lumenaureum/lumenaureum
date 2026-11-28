import os
import re
import sys
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
    c.post("/setup/shoot", data={"preset_families": "1", "preset_realestate": "1"}, environ_base=E)
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Keystone Realty Group')")
    _sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Kim Ray', 'adult', 'kim@example.com')")
    return A, c

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def _day(A, n):
    return (A.today() + timedelta(days=n)).isoformat()

def _in(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def _session(A, type_="family", stage="inquiry", days=None, start=None, place=None, sid=1):
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops) VALUES (?, 1, ?, ?, ?, ?, ?)",
         (sid, type_, stage, _day(A, days) if days is not None else None, start, place or ""))
    _in(A, lambda: A.sync_workflow_tasks(sid))

def _stage(A, sid=1):
    return _sql(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0]

def _acts(A, sid=1):
    return _in(A, lambda: A.build_actions(only_sid=sid))

def _page(c, sid=1):
    r = c.get(f"/c/1?s={sid}", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _history(A, sid=1):
    return [r[0] for r in _sql(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

def test_net_terms_balance_row_offers_waive_before_delivery(env):

    A, c = env
    _session(A, "realestate", "shot", days=-3, start="10:00", place="12 Main St")
    _sql(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Listing', 344.50, 'custom')")
    c.post("/session/1/invoice/deposit-sent", data={"back": "/"}, environ_base=E)
    c.post("/session/1/money", data={"what": "payment", "kind": "balance", "amount": "200", "method": "other",
                                      "note": "trade", "received": A.today().isoformat()}, environ_base=E)
    row = next(a for a in _acts(A) if "owes $144.50" in (a.get("what") or ""))

    assert [m["verb"] for m in row["more_items"]] == ["Remind them", "Waive the balance"]
    assert "Waive the balance" in _page(c)
    c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": "waived", "note_why": "Good client"},
           environ_base=E)
    assert not [a for a in _acts(A) if "owes" in (a.get("what") or "")]

def test_collect_first_row_offers_waive(env):

    A, c = env
    _session(A, "family", "delivered", days=-20, start="10:00", place="Park")
    _sql(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 344.50, 'custom')")
    c.post("/session/1/invoice/deposit-sent", data={"back": "/"}, environ_base=E)
    row = next(a for a in _acts(A) if a.get("collect"))
    assert [m["verb"] for m in row["more_items"]] == ["Waive the balance"]

def test_contact_form_words_become_the_inquiry_message(env):
    A, c = env
    r = c.post("/contact/new", data={"contact_name": "Hannah Cole", "email": "h@example.com", "channel": "text",
                                      "note": "Can we do photos at the lake in June?", "then": "inquiry"}, environ_base=E)
    loc = r.headers["Location"]
    assert "msg=" in loc
    hid = int(re.search(r"hid=(\d+)", loc).group(1))
    form = c.get(loc.replace("https://localhost", "") + "&type=family", environ_base=E).get_data(as_text=True)
    assert "Can we do photos at the lake in June?" in form
    assert 'placeholder="What they said"' not in form
    msg = re.search(r'name="msg" value="(\d+)"', form).group(1)
    r = c.post("/inquiry", data={"household_id": hid, "type": "family", "msg": msg}, environ_base=E)
    sid = _sql(A, "SELECT id FROM sessions WHERE household_id=?", (hid,))[0][0]
    page = c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Can we do photos at the lake in June?" in page and "No message, only the inquiry" not in page
    logs = _sql(A, "SELECT summary, session_id FROM log WHERE household_id=?", (hid,))
    assert [tuple(r_) for r_ in logs] == [("Can we do photos at the lake in June?", sid)]
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "New inquiry, no message" not in today

def test_a_message_from_another_client_is_never_taken(env):
    A, c = env
    lid = _sql(A, "INSERT INTO log (household_id, channel, summary, at) VALUES (1, 'email', 'Keystone words', '2026-01-01 09:00') RETURNING id")[0][0]
    _sql(A, "INSERT INTO households (id, name) VALUES (2, 'Cole')")
    c.post("/inquiry", data={"household_id": 2, "type": "family", "msg": lid}, environ_base=E)
    assert _sql(A, "SELECT session_id FROM log WHERE id=?", (lid,))[0][0] is None

def test_business_inquiry_with_answers_and_a_line_stays_inquiry_until_sent(env):
    A, c = env
    _session(A, "realestate", "inquiry")
    c.post("/session/1/intake", data={k: "yes" for k, _ in A.C.INTAKE["realestate"]}, environ_base=E)
    c.post("/log", data={"household_id": 1, "session_id": 1, "channel": "email", "summary": "Listing next week"}, environ_base=E)
    c.post("/session/1/money", data={"what": "line", "label": "Listing", "amount": "291.50"}, environ_base=E)
    assert _stage(A) == "inquiry"
    c.post("/session/1/quote/send", environ_base=E)
    assert _stage(A) == "inquiry"
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    c.post(f"/quote/{vid}/sent", data={"back": "/c/1?s=1"}, environ_base=E)
    assert _stage(A) == "quoted"

def test_replace_the_quote_puts_the_new_quote_under_its_row(env):
    A, c = env
    _session(A, "family", "quoted", days=10, start="10:00", place="Park")
    c.post("/session/1/money", data={"what": "line", "label": "Family session", "amount": "291.50"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    c.post("/session/1/quote/replace", data={"back": "/c/1?s=1"}, environ_base=E)
    page = _page(c)
    needs = page.split('id="needs"', 1)[1].split("</section>", 1)[0]
    row_at, editor_at = needs.index("New quote waiting for their OK"), needs.index('name="fee_key"')
    assert row_at < editor_at
    others = [m.start() for m in re.finditer(r'<li class="ui-todo', needs)][1:]
    assert others and editor_at < others[0]
    assert page.count('id="billing"') == 1
    r = c.post("/session/1/quick", data={"act": "rebook", "back": "/c/1?s=1"}, environ_base=E, follow_redirects=True)
    assert "Booked the new quote ($291.50)" in r.get_data(as_text=True)

def test_book_without_a_quote_asks_for_no_quote_and_notes_a_past_date(env):
    A, c = env
    _session(A, "family", "inquiry", days=-300)
    r = c.post("/session/1/quick", data={"act": "book", "back": "/c/1?s=1"}, environ_base=E, follow_redirects=True)
    assert _stage(A) == "booked"
    assert "has already passed" in r.get_data(as_text=True)
    whats = [a["what"] for a in _acts(A)]
    assert not [w for w in whats if "quote" in w.lower()], whats

def test_setting_the_date_while_the_facts_are_open_is_not_a_reschedule(env):
    A, c = env
    _session(A, "family", "booked", days=17)
    c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 3), "start_time": "10:00", "where": "Park",
                                      "back": "/c/1?s=1"}, environ_base=E)
    assert not [h for h in _history(A) if h.startswith("Rescheduled")]
    assert "Rescheduled" not in _page(c)
    c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 5), "start_time": "10:00", "where": "Park",
                                      "back": "/c/1?s=1"}, environ_base=E)
    assert [h for h in _history(A) if h.startswith("Rescheduled")]

def test_not_booked_reads_without_blame(env):
    A, c = env
    _session(A, "family", "inquiry")
    _sql(A, "INSERT INTO stage_log (session_id, stage) VALUES (1, 'inquiry')")
    c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "They went elsewhere"}, environ_base=E)
    page = _page(c)
    assert "Not booked: They went elsewhere." in page and "You decided" not in page

def test_clients_sort_companies_by_their_name_and_people_by_last_name(env):
    A, c = env
    _sql(A, "INSERT INTO households (id, name) VALUES (2, 'Brightpath Dental'), (3, 'Hannah Brooks'), (4, 'The Ellis Family')")
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'realestate', 'inquiry'), (2, 2, 'realestate', 'inquiry')")
    _sql(A, "INSERT INTO people (household_id, name, role) VALUES (2, 'Dr. Lee Park', 'adult'), (3, 'Hannah Brooks', 'adult'), "
            "(4, 'Jamie Ellis', 'adult')")
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    order = [n for n in re.findall(r'<a class="ui-roster-name" href="/c/\d+">([^<]+)</a>', page) if n in
             ("Brightpath Dental", "Hannah Brooks", "The Ellis Family", "Keystone Realty Group")]
    assert order == ["Brightpath Dental", "Hannah Brooks", "The Ellis Family", "Keystone Realty Group"], order
    with A.app.app_context():
        assert A.client_sort_name("Keystone Realty Group", "", ["Kim Ray"], True).startswith("keystone")
        assert A.client_sort_name("Jamie Ellis", "", ["Jamie Ellis"], True).startswith("ellis ")
        assert A.client_sort_name("The Ellis Family", "", ["Dana Harlow"]).startswith("ellis ")

def test_new_contact_company_is_the_company_field(env):
    A, c = env
    c.post("/contact/new", data={"contact_name": "Ana Ruiz", "company": "Brightpath Dental"}, environ_base=E)
    c.post("/contact/new", data={"contact_name": "Ben Ortiz", "household_name": "The Ortiz Family"}, environ_base=E)
    got = {r[0]: r[1] for r in _sql(A, "SELECT name, company FROM households WHERE id > 1")}
    assert got == {"Brightpath Dental": "Brightpath Dental", "The Ortiz Family": ""}
    form = c.get("/contact/new", environ_base=E).get_data(as_text=True)
    assert "(mother, agent, owner)" not in form and "(blank: their name)" not in form

def test_new_session_links_are_labeled(env):
    A, c = env
    _session(A, "family", "inquiry")

    more = _page(c).split('id="more-session"', 1)[1].split("</details>", 1)[0]
    assert ">New session for Keystone Realty Group</span>" in more
    assert 'type="url"' not in more

def test_codeword_saved_from_the_client_page(env):
    A, c = env
    _session(A, "family", "shot", days=-3, start="10:00", place="Park")
    r = c.post("/session/1/codeword", data={"gallery_codeword": "heron", "back": "/c/1?s=1"}, environ_base=E,
               follow_redirects=True)
    assert 'Use "heron" as the passphrase?' in r.get_data(as_text=True)
    assert _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0] in ("", None)
    c.post("/session/1/codeword", data={"gallery_codeword": "heron", "cw_ok": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0] == "heron"
    r = c.post("/session/1/codeword", data={"cw_make": "1", "back": "/c/1?s=1"}, environ_base=E, follow_redirects=True)
    made = _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0]
    assert len(made.split()) == 5 and f"Made the passphrase &#34;{made}&#34;" in r.get_data(as_text=True)
    r = c.post("/session/1/codeword", data={"gallery_codeword": "heron maple tide", "back": "/c/1?s=1"}, environ_base=E)
    assert r.headers["Location"].endswith("/c/1?s=1")
    assert _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0] == "heron maple tide"
    assert not [h for h in _history(A) if "heron" in h]

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
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type",)}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303) and request.method == "POST":
            lu = urlsplit(r.location)
            r = c.get(lu.path + ("?" + lu.query if lu.query else ""), environ_base=E)
        if r.status_code in (301, 302, 303):
            loc = r.location
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_codeword_enter_saves_in_place(browser, env):
    A, c = env
    _session(A, "family", "shot", days=-3, start="10:00", place="Park")
    ctx, p = _tab(browser, c)
    try:
        p.goto(BASE + "/c/1?s=1")
        if not p.get_by_role("link", name="Add the passphrase").count():
            pytest.skip("this session type asks for no passphrase")
        p.evaluate("window.__stay = 1")
        p.get_by_role("link", name="Add the passphrase").click()
        box = p.get_by_label("Gallery passphrase")
        box.fill("heron maple tide")
        box.press("Enter")
        p.locator(".ui-note", has_text="Saved the gallery passphrase").wait_for()
        assert p.evaluate("window.__stay") == 1
    finally:
        ctx.close()
    assert _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0] == "heron maple tide"

@pytest.mark.parametrize("width", [768, 1280])
def test_timeline_stage_names_never_print_over_each_other(browser, env, width):

    A, c = env
    _session(A, "family", "shot", days=-30, start="10:00", place="Park")
    _sql(A, "UPDATE sessions SET created=? WHERE id=1", (_day(A, -40),))
    _sql(A, "DELETE FROM stage_log WHERE session_id=1")
    for st, n in (("inquiry", -35), ("quoted", -90), ("booked", -60), ("shot", -30)):
        _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, ?, ?)", (st, _day(A, n)))
    ctx, p = _tab(browser, c, width)
    try:
        p.goto(BASE + "/c/1?s=1")
        boxes = p.eval_on_selector_all(".journey .band span", "els => els.filter(e => e.offsetParent).map(e => { "
                                       "const r = e.getBoundingClientRect(); return [r.left, r.right, e.textContent]; })")
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert a[1] <= b[0] or b[1] <= a[0], (a, b)
        lefts = p.eval_on_selector_all(".journey .band", "els => els.map(e => e.getBoundingClientRect().left)")
        assert lefts == sorted(lefts), lefts
    finally:
        ctx.close()

def test_a_move_to_delivered_names_the_files_delivered_not_picks(env):

    A, c = env
    _session(A, "family", "shot", days=-20, start="10:00", place="Park")
    _sql(A, "UPDATE sessions SET retouch_picks='IMG_1', sd_shipped=?, gallery_built=? WHERE id=1", (_day(A, -1), _day(A, -5)))

    _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=1 AND done IS NULL", (_day(A, -1),))
    _in(A, lambda: A.advance(1))
    moved = [h for h in _history(A) if h.startswith("Moved to Delivered")]
    assert moved and moved[0].startswith("Moved to Delivered: the files were delivered."), _history(A)

def test_picks_alone_never_move_to_delivered(env):

    A, c = env
    _session(A, "family", "shot", days=-20, start="10:00", place="Park")
    _sql(A, "UPDATE sessions SET favorites='IMG_1' WHERE id=1")
    _in(A, lambda: A.advance(1))
    assert not [h for h in _history(A) if h.startswith("Moved to Delivered")]

def test_make_one_for_me_is_never_a_short_list_and_make_another_replaces_it(env):

    import math
    from plugins import gallery as G
    assert len(set(G.CODEWORD_WORDS)) >= 1000 and G.CODEWORD_PICKS * math.log2(len(set(G.CODEWORD_WORDS))) >= 50
    assert not {"grope", "panty", "arson", "trump", "polio", "xerox"} & set(G.CODEWORD_WORDS)
    A, c = env
    _session(A, "family", "shot", days=-3, start="10:00", place="Park")
    c.post("/session/1/codeword", data={"cw_make": "1", "back": "/c/1?s=1"}, environ_base=E)
    first = _sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert f"Passphrase: {first}" in page and "Make another" in page
    seen = {first}
    for _ in range(3):
        c.post("/session/1/codeword", data={"cw_make": "1", "back": "/c/1?s=1"}, environ_base=E)
        seen.add(_sql(A, "SELECT gallery_codeword FROM sessions WHERE id=1")[0][0])
    assert len(seen) == 4
