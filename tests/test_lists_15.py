import json
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env
from test_today_15 import _day, _studio, _sync, _today, _x

def _clients_studio(A):

    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'booked', ?)", (_day(A, 10),))
    for hid, name, email, created in ((2, "Ellis Family", "ellis@example.com", "2024-01-01"), (3, "Jamie Abbott", "jamie@example.com", "2025-06-01"),
                                      (4, "The Zane Family", "zane@example.com", "2023-01-01"), (5, "Bright Meadow Realty", "office@example.com", "2026-01-01")):
        _x(A, "INSERT INTO households (id, name, created) VALUES (?, ?, ?)", (hid, name, created))
        _x(A, "INSERT INTO people (household_id, name, role, email) VALUES (?, ?, 'parent', ?)", (hid, name.replace("The ", "").replace(" Family", "") + " Parent", email))
    _x(A, "UPDATE households SET created='2022-05-01' WHERE id=1")
    _x(A, "UPDATE households SET company='Bright Meadow Realty' WHERE id=5")
    for sid, hid, stage, days in ((10, 2, "closed", -900), (11, 2, "closed", -700), (12, 2, "closed", -425),
                                  (13, 3, "closed", -335), (14, 3, "inquiry", None), (15, 4, "closed", -730), (16, 5, "delivered", -40)):
        _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (?, ?, 'family', ?, ?)",
           (sid, hid, stage, _day(A, days) if days is not None else None))
    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (16, 'Family session', 318, 'fee')")
    _x(A, "INSERT INTO client_end_contact (household_id, since) VALUES (4, ?)", (_day(A, -10),))
    _x(A, "INSERT INTO invoice_sent (session_id, number, sig) VALUES (16, '2026-001', '')")

def _rows(page):
    return re.findall(r'<a class="ui-roster-name" href="/c/(\d+)">', page.split('id="client-rows"', 1)[1])

def _item(page, hid):

    i = page.index(f'<a class="ui-roster-name" href="/c/{hid}">')
    return page[page.rindex("<tr", 0, i):page.index("</tr>", i)]

def test_clients_sort_by_last_name_and_by_each_column_header(env):

    A, c = env
    _clients_studio(A)
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert "Sorted by" not in page and "<select" not in page.split("<main", 1)[1]
    heads = re.findall(r'data-r-sort="(\w+)"[^>]*>([^<]+)</a>', page)
    assert heads == [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("stand", "Where they stand"), ("owed", "Owes"),
                     ("toinv", "To invoice")]
    assert 'aria-sort="ascending"><a class="ui-roster-sort" href="/clients?sort=name&amp;dir=desc"' in page

    assert _rows(page) == ["3", "5", "2", "1", "4"], _rows(page)
    assert _rows(c.get("/clients?sort=owed&dir=desc", environ_base=E).get_data(as_text=True))[0] == "5"
    assert _rows(c.get("/clients?sort=email", environ_base=E).get_data(as_text=True))[0] == "1"
    assert _rows(c.get("/clients?sort=name&dir=desc", environ_base=E).get_data(as_text=True))[0] == "4"
    with A.app.test_request_context():
        assert A.client_sort_name("Jamie Ellis") < A.client_sort_name("Ellis Morgan")
        assert A.client_sort_name("The Ellis Family").startswith("ellis ")
        assert A.client_sort_name("Ana and Ben Ruiz").startswith("ruiz ")

def test_clients_rows_show_links_where_they_stand_and_what_they_owe(env):

    A, c = env
    _clients_studio(A)
    _x(A, "UPDATE people SET phone='(555) 010-1234' WHERE household_id=2")
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    ellis, morgan, realty, abbott = _item(page, 2), _item(page, 1), _item(page, 5), _item(page, 3)
    assert '<a href="mailto:ellis@example.com">ellis@example.com</a>' in ellis and '<a href="tel:5550101234">(555) 010-1234</a>' in ellis
    assert "Closed \u00b7 last session " in ellis or "Closed &middot; last session" in ellis or "Closed · last session" in ellis
    assert "Booked · shoot " in morgan and "Delivered · shoot " in realty and "$318.00" in realty
    assert "Inquiry" in abbott
    assert 'data-ui-href="/c/2"' in ellis
    for gone in ("Email all", " open</span>", "Repeat:", "Time to check in", "End contact", "data-f-key"):
        assert gone not in page.split("<main", 1)[1], gone
    with A.app.test_request_context():
        assert A.where_they_stand([], "2026-01-01") == ("No sessions yet", "8|")

def test_clients_filter_box_matches_every_column_and_digits_match_the_phone(env):
    A, c = env
    _clients_studio(A)
    _x(A, "UPDATE people SET phone='(555) 010-1234' WHERE household_id=2")
    get = lambda q_: _rows(c.get("/clients", query_string={"q": q_}, environ_base=E).get_data(as_text=True))
    assert get("0101234") == ["2"] and get("010-1234") == ["2"] and get("1234") == ["2"]
    assert get("office@") == ["5"] and get("delivered") == ["5"] and get("ellis parent") == ["2"]
    assert get("nobody-here") == [] and "No one matches." in c.get("/clients?q=zzz", environ_base=E).get_data(as_text=True)

def test_check_in_and_end_contact_keep_working_off_the_clients_page(env):
    A, c = env
    _clients_studio(A)
    with A.app.test_request_context():
        assert A.check_in_due() == {2}
    r = c.post("/client/2/check-in", data={"mail": "1", "back": "/clients"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:ellis@example.com")
    with A.app.test_request_context():
        assert A.check_in_due() == set()
        links = A.bulk_mail_links([1, 2, 3, 5], size=3)
        assert [l_[0] for l_ in links] == ["Email clients 1 to 3", "Email clients 4 to 4"]
    assert c.post("/client/3/end-contact", data={"end": "1"}, environ_base=E).status_code == 302
    details = c.get("/client/3", environ_base=E).get_data(as_text=True)
    assert ">Resume contact</button>" in details and "End contact since" in details

def test_labels_are_the_photographers_own_words_filterable_exported_and_never_on_documents(env):
    A, c = env
    _clients_studio(A)
    details = c.get("/client/2", environ_base=E).get_data(as_text=True)
    box = details.split('id="labels"', 1)[1].split("</section>", 1)[0]
    assert 'class="qh"' in box and "part of what a client gets if they ask" in box
    assert "<option" not in box
    c.post("/client/2/labels", data={"label": "  Slow   to pay "}, environ_base=E)
    c.post("/client/5/labels", data={"label": "slow to pay"}, environ_base=E)
    c.post("/client/5/labels", data={"label": "Big project"}, environ_base=E)
    rows = A.connect().execute("SELECT household_id, label FROM client_labels ORDER BY household_id, label").fetchall()
    assert [tuple(r) for r in rows] == [(2, "Slow to pay"), (5, "Big project"), (5, "Slow to pay")]
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert 'data-r-find="Big project Slow to pay' in _item(page, 5)
    assert "Slow to pay" in c.get("/c/2", environ_base=E).get_data(as_text=True).split("</header>", 1)[0]
    assert _rows(c.get("/clients?q=slow", environ_base=E).get_data(as_text=True)) == ["5", "2"] or \
        sorted(_rows(c.get("/clients?q=slow", environ_base=E).get_data(as_text=True))) == ["2", "5"]

    exp = json.loads(c.get("/client/5/export.json", environ_base=E).get_data(as_text=True))
    assert sorted(r_["label"] for r_ in exp["client_labels"]) == ["Big project", "Slow to pay"]

    for path in ("/session/16/invoice", "/client/5/statement"):
        r = c.get(path, environ_base=E)
        if r.status_code == 200:
            assert "Slow to pay" not in r.get_data(as_text=True) and "Big project" not in r.get_data(as_text=True), path

    c.post("/client/5/labels", data={"remove": "Big project"}, environ_base=E)
    assert A.connect().execute("SELECT COUNT(*) FROM client_labels WHERE household_id=5").fetchone()[0] == 1
    con = A.connect()
    try:
        con.execute("DELETE FROM households WHERE id=5")
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM client_labels WHERE household_id=5").fetchone()[0] == 0
    finally:
        con.close()

def test_labels_filter_today_too(env):
    A, c = env
    _studio(A)
    c.post("/client/4/labels", data={"label": "Slow to pay"}, environ_base=E)
    page = _today(c)
    assert 'data-f-g-label="Slow to pay"' in page.split('id="line-s4"', 1)[1].split(">", 1)[0]

    assert 'data-f-key="label"' not in page and "label:name" in page

def test_today_chips_count_kinds_and_mark_mine_waiting_and_due(env):
    A, c = env
    _studio(A)
    page = _today(c)
    bar = page.split('id="filter-today"', 1)[1].split('data-f-none', 1)[0]

    for k_, v_, chip in (("who", "mine", "Mine"), ("who", "waiting", "Waiting"), ("due", "today", "Due today"), ("due", "week", "Due in 7 days")):
        assert re.search(rf'data-f-key="{k_}" data-f-val="{v_}"( data-f-one)? aria-pressed="false">{chip} <span class="ui-fchip-n" data-f-n></span></button>', bar), chip
    assert ">All <" not in bar and bar.count('class="ui-filter-row"') == 1
    assert '<button type="button" class="ui-btn ui-btn-quiet ui-fclear" data-f-clear hidden>Clear filters</button>' in bar
    assert 'type="number"' not in bar and "data-f-days" not in bar and 'data-f-scope="work"' in page
    assert 'data-f-key="kind"' in bar and "Inquiries <span" in bar and "Shoot <span" not in bar
    line = page.split('id="line-s4"', 1)[1].split(">", 1)[0]
    assert 'data-f-status="mine"' in line and "data-f-g-kind=" in line
    shoot = page.split('id="shoots"', 1)[1].split('id="shoot-', 1)[0]
    assert "data-f" not in shoot
    with A.app.test_request_context():
        assert [A.task_kind(w) for w in ("Send retouched photos", "Back up photos", "Collect $318 first", "Cull", "")] == \
            ["Retouch", "Back up", "Payments", "Cull", ""]

def test_setting_up_is_in_the_alert_bell_and_urgent_items_are_red(env):

    A, c = env
    _studio(A)
    page = _today(c)
    assert 'id="setup"' not in page and "Setting up</h2>" not in page and "/today/setup-dismiss" not in page
    A.app.config["urgent_items"] = lambda: [("Your backups are on the same disk as your data: move the vault or add a second location", "/setup/storage")]
    page = _today(c)
    assert "Your backups are on the same disk" not in page and 'id="check-first"' not in page
    alerts = c.get("/alerts", environ_base=E).get_data(as_text=True)
    fix = alerts.split('id="alerts-fix"', 1)[1].split("</section>", 1)[0]
    assert "Your backups are on the same disk" in fix and 'href="/setup/storage"' in fix
    assert '<span class="ui-bell-n">' in page

def test_a_canceled_booking_tells_its_refund_in_the_decided_words(env):
    A, c = env
    _x(A, "INSERT INTO households (id, name) VALUES (7, 'Quinn')")
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, lost_reason) VALUES (7, 7, 'family', 'lost', ?, 'Canceled')", (_day(A, 20),))
    with A.app.test_request_context():
        assert A.refund_words("Quinn", "Canceled", 10, 100) == "Quinn canceled. Refund owed: $10.00. Deposit of $100.00 is retained."
        assert A.refund_words("Quinn", None, 10, 0) == "Quinn canceled. Refund owed: $10.00."

def test_today_filters_instantly_and_remembers_and_never_changes_the_badge(browser, env):
    A, c = env
    _studio(A)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/")
        badge = p.locator("nav.top .ui-inbox-n").count()
        head = p.locator("#today-head").inner_text()
        n_all = p.locator("[data-f]:visible").count()
        assert n_all >= 3
        url = p.url
        p.locator('#filter-today [data-f-key="kind"][data-f-val="Inquiries"]').click()
        assert p.url == url
        assert p.locator("[data-f]:visible").count() < n_all
        assert p.locator("#line-s2").is_visible() and not p.locator("#line-s4").is_visible()
        assert p.locator("nav.top .ui-inbox-n").count() == badge and p.locator("#today-head").inner_text() == head
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
        p.reload()
        assert not p.locator("#line-s4").is_visible() and p.locator("#line-s2").is_visible()
        assert p.locator('#filter-today [data-f-val="Inquiries"]').get_attribute("aria-pressed") == "true"
        p.locator('#filter-today [data-f-val="Inquiries"]').click()
        assert p.locator("#filter-today [data-f-clear]").is_hidden()
        p.locator('#filter-today [data-f-key="who"][data-f-val="waiting"]').click()
        assert not p.locator("#line-s2").is_visible()
        assert p.locator("#filter-today [data-f-clear]").is_visible()
        p.locator("#filter-today [data-f-clear]").click()
        assert p.locator("[data-f]:visible").count() == n_all and p.locator("#filter-today [data-f-clear]").is_hidden()
        p.locator("#filter-today [data-f-q]").fill("ellis")
        assert p.locator("#line-s4").is_visible() and not p.locator("#line-s2").is_visible()
        p.locator("#filter-today [data-f-q]").fill("")

        n_tasks = p.locator("#work [data-f]").count()
        mine = p.locator('#work [data-f][data-f-status="mine"]').count()
        waiting = p.locator('#work [data-f][data-f-status="waiting"]').count()
        assert p.locator('#filter-today [data-f-val="mine"] [data-f-n]').inner_text() == str(mine)
        p.locator('#filter-today [data-f-key="who"][data-f-val="mine"]').click()
        assert p.locator("#work [data-f]:visible").count() == mine
        p.locator('#filter-today [data-f-key="who"][data-f-val="waiting"]').click()
        assert p.locator("#work [data-f]:visible").count() == mine + waiting
        p.locator('#filter-today [data-f-key="due"][data-f-val="today"]').click()
        shown = p.locator("#work [data-f]:visible").count()
        assert shown <= mine + waiting and p.locator('#filter-today [data-f-val="today"] [data-f-n]').inner_text() == str(shown)
        p.locator('#filter-today [data-f-key="due"][data-f-val="week"]').click()
        assert p.locator('#filter-today [data-f-val="today"]').get_attribute("aria-pressed") == "false"
        assert p.locator('#filter-today [data-f-val="week"]').get_attribute("aria-pressed") == "true"
        assert n_tasks >= mine + waiting
    finally:
        ctx.close()

def test_sessions_and_clients_chips_and_sort_work_in_place(browser, env):
    A, c = env
    _clients_studio(A)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/sessions?view=all")
        n_all = p.locator("[data-f]:visible").count()
        p.locator('#filter-sessions [data-f-key="stage"][data-f-val="Closed"]').click()
        assert 0 < p.locator("[data-f]:visible").count() < n_all
        p.locator('#filter-sessions [data-f-key="type"]').first.is_visible()
        assert p.locator('#filter-sessions [data-f-noscript]').is_hidden()
        assert "view=" not in p.url
        p.locator("#filter-sessions [data-f-clear]").click()
        assert p.locator("[data-f]:visible").count() == n_all
        p.goto(BASE + "/clients")
        names = lambda: p.eval_on_selector_all("#clients tbody tr:not([hidden]) a.ui-roster-name", "els => els.map(e => e.textContent)")
        assert names()[0] == "Jamie Abbott"
        url = p.url
        p.locator('[data-r-sort="owed"]').click()
        p.locator('[data-r-sort="owed"]').click()
        assert p.url == url and names()[0] == "Bright Meadow Realty"
        p.evaluate("window.scrollTo(0, 0)")
        p.locator("[data-r-q]").press_sequentially("ellis")
        assert names() == ["Ellis Family"] and p.evaluate("window.scrollY") == 0
        p.locator("#clients tbody tr:not([hidden]) td.ui-roster-stand").click()
        p.wait_for_url("**/c/2**")
    finally:
        ctx.close()

def test_without_script_the_lists_show_in_full(browser, env):
    A, c = env
    _studio(A)
    ctx, p = _tab(browser, c, width=390, script=False)
    try:
        p.goto(BASE + "/")
        assert not p.locator("#filter-today").locator("button").first.is_visible()
        assert p.locator("#line-s4").is_visible() and p.locator("#line-s2").is_visible()
        p.goto(BASE + "/clients?q=morgan")
        assert p.locator("[data-r-noscript]").is_visible()
        assert p.eval_on_selector_all("#clients a.ui-roster-name", "els => els.map(e => e.textContent)") == ["Morgan"]
    finally:
        ctx.close()

def test_upgrade_from_140_adds_the_label_tables_empty_and_changes_no_client(tmp_path, monkeypatch):

    from test_upgrade_from_140 import _open, _place, _q
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    names = {r[0] for r in _q(A, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"client_labels", "client_end_contact"} <= names
    assert _q(A, "SELECT COUNT(*) FROM client_labels")[0][0] == 0 and _q(A, "SELECT COUNT(*) FROM client_end_contact")[0][0] == 0
    before = _q(A, "SELECT id, name FROM households ORDER BY id")
    con = A.connect()
    try:
        con.executescript(A.SCHEMA)
        con.commit()
    finally:
        con.close()
    assert _q(A, "SELECT id, name FROM households ORDER BY id") == before
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert len(_rows(page)) == len(before)
