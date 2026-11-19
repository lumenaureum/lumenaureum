import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _session, _tab, browser, env
from test_ui_library import NOT_YET_MIGRATED, TEMPLATES, bare_labels, one_offs

PAGES = ("session.html", "sessions.html", "pipeline.html", "clients.html", "contact_new.html", "search.html", "client.html")
STAGES = [("inquiry", None), ("quoted", 20), ("booked", 10), ("booked", 0), ("shot", -3), ("delivered", -20),
          ("closed", -60), ("lost", 10)]

def _fill(A):

    con = A.connect()
    try:
        con.execute("INSERT INTO people (household_id, name, role, relation, is_minor) VALUES (1, 'Riley Morgan', 'child', 'daughter', 1)")
        con.execute("INSERT INTO documents (session_id, kind, signer, status) VALUES (1, 'release', 'Alex Morgan', 'sent')")
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session, one hour, at the location of your choice', 1318.5, 'fee')")
        con.execute("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 100, '2026-11-01', 'cash')")
        con.execute("INSERT INTO expenses (session_id, date, label, miles, amount) VALUES (1, '2026-11-01', 'Parking at the gardens', 12, 8)")
        con.commit()
    finally:
        con.close()

def _main(page):
    return page.split("<main", 1)[1]

def test_the_migrated_pages_are_off_the_allow_list_and_clean():
    for name in PAGES:
        assert name not in NOT_YET_MIGRATED, name
        text = (TEMPLATES / name).read_text()
        assert not one_offs(text), (name, one_offs(text))
        assert not bare_labels(text), (name, bare_labels(text))
        assert '{% import "_ui.html" as ui' in text, name

@pytest.mark.parametrize("stage,days", STAGES)
def test_the_old_session_address_opens_the_client_page(env, stage, days):
    A, c = env
    _session(A, stage, days=days)
    r = c.get("/session/1", environ_base=E)
    assert r.status_code == 302 and r.location.endswith("/c/1?s=1"), (r.status_code, r.location)
    r = c.get("/session/1", environ_base=E, follow_redirects=True)
    assert r.status_code == 200 and r.request.path == "/c/1"

def test_the_client_page_lands_on_the_session_the_old_link_named(env):

    A, c = env
    _session(A, "booked", days=10)
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, title) VALUES (2, 1, 'headshot', 'inquiry', 'Work headshots')")
        con.commit()
    finally:
        con.close()
    r = c.get("/session/2", environ_base=E)
    assert r.location.endswith("/c/1?s=2")
    page = c.get("/c/1?s=2", environ_base=E).get_data(as_text=True)
    assert 'href="/session/2/edit"' in page.split('id="more"', 1)[1]

def test_a_deleted_session_is_still_gone_not_a_redirect(env):
    A, c = env
    _session(A, "booked", days=10)
    con = A.connect()
    try:
        for t_ in ("tasks", "session_people", "sessions"):
            con.execute("DELETE FROM " + t_ + (" WHERE session_id=1" if t_ != "sessions" else " WHERE id=1"))
        con.commit()
    finally:
        con.close()
    for path in ("/session/1", "/session/1/edit"):
        r = c.get(path, environ_base=E)
        assert r.status_code in (404, 410), (path, r.status_code)
    assert c.get("/session/999", environ_base=E).status_code == 404
    assert c.get("/session/999/edit", environ_base=E).status_code == 404

def test_a_new_session_for_a_client_opens_the_client_page(env):
    A, c = env
    r = c.post("/client/1/session", data={"type": "family"}, environ_base=E)
    assert r.status_code == 302 and re.search(r"/c/1\?s=\d+$", r.location), r.location

def test_more_on_the_client_page_names_both_edit_pages_and_never_reloads_itself(env):
    A, c = env
    _session(A, "booked", days=10)
    more = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True).split('id="more"', 1)[1]
    assert 'href="/session/1/edit">Edit session details' in more
    assert 'href="/client/1">Edit client details' in more
    assert not re.search(r'href="/session/1(#[^"]*)?"', more)

@pytest.mark.parametrize("stage,days", STAGES)
def test_edit_session_details_renders_at_every_stage(env, stage, days):
    A, c = env
    _session(A, stage, days=days, start="10:00")
    _fill(A)
    r = c.get("/session/1/edit", environ_base=E)
    assert r.status_code == 200
    page = _main(r.get_data(as_text=True))
    for part in ("shoot", "paperwork", "money"):
        assert f'id="{part}"' in page, part
    assert 'href="/c/1?s=1"' in page
    assert "<table class=\"data" not in page
    assert "Save details" in page and "Record payment" in page and "Delete session" in page
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert not re.search(r"<(button|form)\b", head)
    assert ("Reopen at Booked" in page) == (stage == "lost")

def test_saving_the_session_form_comes_back_to_it(env):
    A, c = env
    _session(A, "booked", days=10)
    r = c.post("/session/1", data={"title": "Garden portraits", "type": "family", "shoot_date": "2026-12-05"}, environ_base=E)
    assert r.status_code == 302 and r.location.endswith("/session/1/edit#shoot")
    assert A.connect().execute("SELECT title FROM sessions WHERE id=1").fetchone()[0] == "Garden portraits"

def test_paperwork_status_button_says_what_it_does_and_signed_on_is_its_own_row(env):

    A, c = env
    _session(A, "booked", days=10)
    _fill(A)
    page = _main(c.get("/session/1/edit", environ_base=E).get_data(as_text=True))
    papers = page.split('id="paperwork"', 1)[1].split("</section>", 1)[0]
    assert "<table" not in papers
    assert ">Mark signed</button>" in papers
    row = re.search(r'<div class="ui-form-row">\s*<form[^>]*>\s*<input type="hidden" name="back"[^>]*>\s*<label class="ui-check">Signed on <input type="date"', papers)
    assert row, papers[:1500]
    r = c.post("/doc/1", data={"cycle": "1", "from": "sent", "back": "/session/1/edit#paperwork"}, environ_base=E)
    assert r.location.endswith("/session/1/edit#paperwork")
    assert A.connect().execute("SELECT status FROM documents WHERE id=1").fetchone()[0] == "signed"

def test_billing_on_the_edit_page_uses_the_money_table(env):
    A, c = env
    _session(A, "booked", days=10)
    _fill(A)
    money = _main(c.get("/session/1/edit", environ_base=E).get_data(as_text=True)).split('id="money"', 1)[1].split("</section>", 1)[0]
    assert money.count('<table class="ui-money"') == 4
    assert "$1,318.50" in money and "Parking at the gardens" in money and ">Void</button>" in money

def test_the_steps_that_point_at_the_full_form_open_the_edit_page(env):

    A, c = env
    _session(A, "booked", days=10)
    with A.app.test_request_context():
        for a in A.build_actions(only_sid=1):
            for k in ("href", "doc"):
                v = a.get(k) or ""
                assert not re.match(r"^/session/1#", v), (a.get("what"), v)

def _rows(page):
    return re.findall(r'<a class="ui-row" href="([^"]*)">(.*?)</a>', page, re.S)

def test_the_sessions_list_is_list_rows_naming_stage_and_the_one_item(env):
    A, c = env
    _session(A, "booked", days=10)
    page = _main(c.get("/sessions", environ_base=E).get_data(as_text=True))
    assert "<table" not in page
    rows = _rows(page)
    assert rows and rows[0][0] == "/c/1?s=1"
    row = rows[0][1]
    assert "Booked" in row and "Morgan" in row and "Family" in row
    with A.app.test_request_context():
        nx = A.session_nexts([r_["s"] for r_ in A.session_rows()])[1]
    assert nx and nx["what"] in row
    owing = _main(c.get("/sessions?owing=1&view=all", environ_base=E).get_data(as_text=True))
    assert 'id="filter-sessions"' in owing

def test_the_board_is_list_rows_by_stage(env):
    A, c = env
    _session(A, "shot", days=-3)
    page = _main(c.get("/pipeline", environ_base=E).get_data(as_text=True))
    assert 'class="tile' not in page and 'class="board"' not in page
    shot = page.split('id="stage-shot"', 1)[1].split("</section>", 1)[0]
    assert _rows(shot) and _rows(shot)[0][0] == "/c/1?s=1"
    assert 'id="stage-lost"' in page
    assert c.get("/pipeline?lost=1", environ_base=E).status_code == 200

def test_the_clients_list_is_a_table_whose_rows_open_the_client_page(env):

    A, c = env
    _session(A, "booked", days=10)
    page = _main(c.get("/clients", environ_base=E).get_data(as_text=True))
    assert '<table class="ui-roster"' in page
    row = page.split('data-ui-href="/c/1"', 1)[1].split("</tr>", 1)[0]
    assert '<a class="ui-roster-name" href="/c/1">' in row and "Booked" in row and " open<" not in row
    assert "Morgan" in _main(c.get("/clients?q=alex", environ_base=E).get_data(as_text=True))

def test_search_results_are_list_rows(env):
    A, c = env
    _session(A, "booked", days=10)
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage) VALUES (2, 1, 'headshot', 'inquiry')")
        con.commit()
    finally:
        con.close()
    page = _main(c.get("/search?q=Morgan", environ_base=E).get_data(as_text=True))
    hrefs = [h for h, _t in _rows(page)]
    assert "/c/1" in hrefs and "/c/1?s=1" in hrefs and "/c/1?s=2" in hrefs
    assert 'class="acts"' not in page
    everyone = _main(c.get("/search", environ_base=E).get_data(as_text=True))
    assert "/c/1" in [h for h, _t in _rows(everyone)]

def test_new_contact_still_saves_and_starts_an_inquiry(env):
    A, c = env
    page = _main(c.get("/contact/new", environ_base=E).get_data(as_text=True))
    assert "Save and start an inquiry" in page and "Save contact only" in page
    r = c.post("/contact/new", data={"contact_name": "Sam Rivera", "email": "sam@example.com", "then": "inquiry"}, environ_base=E)
    assert r.status_code == 302 and "/inquiry" in r.location

def test_client_details_people_are_folds_of_stacked_fields(env):

    A, c = env
    _session(A, "booked", days=10)
    _fill(A)
    page = _main(c.get("/client/1", environ_base=E).get_data(as_text=True))
    people = page.split('id="people"', 1)[1].split("</section>", 1)[0]
    assert "<table" not in people and "people-t" not in people
    assert re.search(r'<details class="ui-fold\s*" id="person-\d+"', people)
    assert ">Save Riley Morgan</button>" in people and ">Add person</button>" in people
    assert [h for h, _t in _rows(page.split('id="sessions"', 1)[1].split("</section>", 1)[0])] == ["/c/1?s=1"]
    pid = A.connect().execute("SELECT id FROM people WHERE name='Riley Morgan'").fetchone()[0]
    seen = re.search(r'value="([0-9a-f]+)" name="seen"', people.split(f'id="person-{pid}"', 1)[1]).group(1)
    r = c.post(f"/person/{pid}", data={"seen": seen, "name": "Riley Morgan", "relation": "daughter", "phone": "555-0100",
                                        "minor_shown": "1", "is_minor": "1"}, environ_base=E)
    assert r.status_code == 302
    assert A.connect().execute("SELECT phone FROM people WHERE id=?", (pid,)).fetchone()[0] == "555-0100"

def test_a_client_with_no_sessions_has_no_link_back_to_a_page_it_does_not_have(env):
    A, c = env
    page = _main(c.get("/client/1", environ_base=E).get_data(as_text=True))
    assert 'href="/c/1' not in page
    assert "+ New inquiry for Morgan" in page

@pytest.mark.parametrize("path", ["/session/1/edit", "/sessions", "/pipeline", "/clients", "/search", "/client/1",
                                  "/contact/new"])
def test_phone_width_never_scrolls_sideways(browser, env, path):
    A, c = env
    _session(A, "shot", days=-3, start="10:00")
    _fill(A)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + path)
        p.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), path
        clipped = p.evaluate("""[...document.querySelectorAll('main input, main .ui-money-amt')].filter(el => {
            const r = el.getBoundingClientRect(); return r.width && (r.right > innerWidth + 1 || r.left < -1); }).length""")
        assert clipped == 0, path
        if path == "/session/1/edit":
            d = p.locator("input[name=signed_on]").first
            assert d.evaluate("el => el.scrollWidth <= el.clientWidth + 1 && el.getBoundingClientRect().width >= 110")
    finally:
        ctx.close()
