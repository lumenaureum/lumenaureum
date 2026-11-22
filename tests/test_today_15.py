import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env

def _x(A, sql, args=()):
    con = A.connect()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()

def _sync(A, *sids):
    with A.app.test_request_context():
        for sid in sids:
            A.sync_workflow_tasks(sid)
        A.db().commit()

def _day(A, n):
    return (A.today() + timedelta(days=n)).isoformat()

def _studio(A):

    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops, intake) VALUES (1, 1, 'family', 'booked', ?, '00:00', ?, ?)",
       (_day(A, 0), "Longwood Gardens, main fountain entrance", '{"who": "Four and a dog"}'))
    for hid, name, email, phone in ((2, "Lee", "lee@example.com", "555-555-0100"), (3, "Kim", "kim@example.com", ""),
                                    (4, "Ellis", "ellis@example.com", "")):
        _x(A, "INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
        _x(A, "INSERT INTO people (household_id, name, role, email, phone) VALUES (?, ?, 'parent', ?, ?)", (hid, name + " Parent", email, phone))
    _x(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (2, 2, 'family', 'inquiry')")
    _x(A, "INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (2, 2, 'email', ?, ?)",
       ("Inquiry from your website:\nAny weekend dates in December?", A.today().isoformat() + " 09:00:00"))
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (3, 3, 'family', 'delivered', ?)", (_day(A, -60),))
    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (3, 'Family session', 318, 'fee')")

    _x(A, "INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (3, 'INV', '2026-014', ?)", (_day(A, -50),))
    _x(A, "INSERT INTO invoice_sent (session_id, number, sig) VALUES (3, '2026-014', '')")
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (4, 4, 'family', 'shot', ?)", (_day(A, -3),))
    _x(A, "INSERT INTO events (id, name, date) VALUES (1, 'Spring Recital', ?)", (_day(A, 185),))
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, event_id) VALUES (5, 2, 'family', 'booked', ?, 1)", (_day(A, 185),))
    _sync(A, 1, 2, 3, 4, 5)

def _today(c):
    r = c.get("/", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _sections(page):

    main = page.split("<main", 1)[1]
    return re.findall(r'<(?:section|details|div) class="ui-(?:needs|sec|fold|flat)[^"]*" id="([^"]+)"', main)

def _lines(page):

    return re.findall(r'<li class="ui-tline[^"]*" id="(line-[^"]+)"[^>]*>(.*?)</li>', page, re.S)

def test_today_keeps_the_fixed_order_and_needs_you_comes_first(env):
    A, c = env
    _studio(A)
    page = _today(c)
    order = _sections(page)
    want = ["shoots", "work", "two-weeks"]
    assert [s_ for s_ in order if s_ in want] == want, order
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert not re.search(r"<(button|form)\b", head) and "ui-btn" not in head
    assert "1 shoot today" in head and "1 past due" in head and "Inbox" not in head
    after = page.split("</header>", 1)[1].lstrip()
    assert re.match(r'<div class="ui-today-cols"><div class="ui-today-main">\s*<section class="ui-needs" id="shoots"', after), after[:200]
    for old in ('class="kpi', "t-track", "t-next", 'class="cday', "home-grid", "t-stage"):
        assert old not in page, old

def test_each_session_is_one_line_with_its_one_action_and_opens_the_client_page(env):
    A, c = env
    _studio(A)
    page = _today(c)
    lines = dict(_lines(page))

    event = lines.pop("line-e1", "")
    assert 'href="/event/1"' in event and len(re.findall(r'class="ui-btn ', event.split('class="ui-tline-act"', 1)[1])) == 1
    assert set(lines) == {"line-s2", "line-s3", "line-s4"}, sorted(lines)
    for lid, html in lines.items():
        act = html.split('class="ui-tline-act"', 1)[1]
        assert len(re.findall(r'class="ui-btn ', act)) == 1, (lid, act)
        assert re.search(r'<a class="ui-tline-name" href="/c/\d+\?s=\d+">', html), lid
        assert 'href="/session/' not in html and 'href="/client/' not in html, lid

    assert 'action="/session/2/reply"' in lines["line-s2"] and 'data-ui-mail="mailto:lee@example.com' in lines["line-s2"]
    assert ">Reply</button>" in lines["line-s2"] and "Any weekend dates in December?" in lines["line-s2"]
    assert "Inquiry from your website" not in lines["line-s2"]

    assert "Collect the $318.00 balance first" in lines["line-s3"] and 'class="ui-todo-when ui-late">&middot; Was due ' in lines["line-s3"]
    assert ">Record payment ($318.00)</a>" in lines["line-s3"]

    assert ">Upload the RAWs</a>" in lines["line-s4"] and "Upload original photos" in lines["line-s4"]
    assert 'id="f-sd_shipped-3"' in page
    assert 'href="/c/4?s=4#row-t' in lines["line-s4"]

def test_the_shoot_block_matches_the_client_page_on_shoot_day(env):
    A, c = env
    _studio(A)
    page = _today(c)
    block = page.split('id="shoots"', 1)[1].split("</section>", 1)[0]
    assert 'data-ui-map="Longwood Gardens, main fountain entrance"' in block
    assert 'href="tel:5555550142">Call Alex Morgan</a>' in block
    assert ">Four and a dog<" in block
    assert "Still open" in block and "<li>Send the session sheet</li>" in block and "ui-tshoot-act" in block
    assert ">Shoot done</button>" in block and 'value="shot"' in block and "data-confirm=" in block
    assert 'href="/c/1?s=1">Morgan</a>' in block
    _x(A, "UPDATE sessions SET start_time='23:59' WHERE id=1")
    assert ">Shoot done</button>" not in _today(c).split('id="shoots"', 1)[1].split("</section>", 1)[0]

def test_money_is_late_only_past_a_real_due_date(env):
    A, c = env
    _studio(A)
    _x(A, "UPDATE sessions SET shoot_date=? WHERE id=3", (_day(A, -2),))
    page = _today(c)
    assert 'id="late-money"' not in page
    assert "ui-chip-late" not in page and "ui-todo-when ui-late" not in page

def test_people_who_sent_picks_come_before_money_and_work(env):
    A, c = env
    _studio(A)
    _x(A, "UPDATE sessions SET retouch_picks='AM_01.jpg: soften the shadow' WHERE id=4")
    page = _today(c)

    work = page.split('id="work"', 1)[1].split("</section>", 1)[0]
    line = dict(_lines(page))["line-s4"]
    assert 'id="line-s4"' in work and "Sent their picks: " in line and "soften the shadow" in line and ">New</span>" in line
    assert page.count('id="line-s4"') == 1 and 'id="wrote-in"' not in page

def test_next_two_weeks_is_folded_without_empty_days_or_today(env):
    A, c = env
    _studio(A)
    for i in range(7):
        c.post("/session/4/task", data={"title": f"Extra {i}", "due": _day(A, 2), "back": "/"}, environ_base=E)
    page = _today(c)

    assert re.search(r'<aside class="ui-today-side">\s*<section class="ui-sec\s*" id="two-weeks" data-ui-part>', page)
    body = page.split('id="two-weeks"', 1)[1].split('</aside>', 1)[0]
    assert A.today().strftime("%a %b %-d") not in body
    day2 = (A.today() + timedelta(days=2)).strftime("%a %b %-d")
    assert f'id="day-{_day(A, 2)}"' in body and f"{day2}: 7 steps" in body
    days_shown = re.findall(r'<h3 class="ui-sub-title">([^<]+)</h3>', body)
    assert len(days_shown) == len(set(days_shown)) and all("steps" not in d_ for d_ in days_shown)
    assert 'href="/calendar/year"' in body

def test_the_header_badge_counts_only_the_unread_inbox(env):

    A, c = env
    _studio(A)
    for path in ("/", "/clients"):
        assert "ui-inbox" not in c.get(path, environ_base=E).get_data(as_text=True).split("<main", 1)[0], path
    assert ">New</span>" in dict(_lines(_today(c)))["line-s2"]

def test_the_old_focus_address_opens_the_client_page(env):
    A, c = env
    _studio(A)
    r = c.get("/?c=2", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/2")
    r = c.get("/?e=1", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/event/1")

def test_today_action_is_the_one_place_a_line_gets_its_action(env):

    A, c = env
    _studio(A)
    _x(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (6, 3, 'family', 'quoted')")
    with A.app.test_request_context():
        s6 = A.q("SELECT * FROM sessions WHERE id=6", one=True)
        do = A.today_action(s6, [], None)
        assert do["a"]["verb"] == "Book it" and do["a"]["fields"] == {"act": "book"}
        s1 = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert A.today_action(s1, [], None)["a"]["verb"] == "Shoot done"
        s4 = A.q("SELECT * FROM sessions WHERE id=4", one=True)
        rows = A.focus_rows([a for a in A.build_actions(only_sid=4) if a["who"]["link"] == "/session/4"], s4, A.totals(4))
        assert A.today_action(s4, rows, None)["a"]["verb"] == "Upload the RAWs"
    src = open(os.path.join(os.path.dirname(A.__file__), "app.py")).read()
    board = src[src.index("def today_board("):src.index("def shoot_block(")]
    assert board.count("today_action(") == 1

def test_an_action_on_today_comes_back_to_today(env):

    A, c = env
    _studio(A)
    _x(A, "UPDATE tasks SET done=date('now') WHERE session_id=4 AND title='Back up photos'")
    page = _today(c)
    tid = re.search(r'action="/task/(\d+)"', dict(_lines(page))["line-s4"]).group(1)
    r = c.post(f"/task/{tid}", data={"set": "1", "back": "/"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert "Cull" not in dict(_lines(_today(c))).get("line-s4", "")
    for part in ("shoots", "work", "update-note"):
        assert re.search(r'id="%s" data-ui-part' % part, page), part

def test_a_group_event_line_never_overprints_on_a_phone(browser, env):

    A, c = env
    _studio(A)
    _x(A, "UPDATE events SET name='Spring Recital at the Grand Riverside Performing Arts Center' WHERE id=1")
    _x(A, "UPDATE sessions SET stage='shot', shoot_date=? WHERE id=5", (_day(A, -3),))
    _sync(A, 5)
    page = _today(c)
    assert 'id="line-e1"' in page
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/")
        name = p.locator("#line-e1 .ui-tline-name").bounding_box()
        stage = p.locator("#line-e1 .ui-tline-stage").bounding_box()
        what = p.locator("#line-e1 .ui-tline-what").bounding_box()
        btn = p.locator("#line-e1 .ui-tline-act .ui-btn").bounding_box()

        def apart(a, b):
            return a["x"] + a["width"] <= b["x"] + 0.5 or b["x"] + b["width"] <= a["x"] + 0.5 or \
                a["y"] + a["height"] <= b["y"] + 0.5 or b["y"] + b["height"] <= a["y"] + 0.5
        assert apart(name, what) and apart(stage, what) and apart(name, btn) and apart(what, btn)
        assert p.evaluate("document.documentElement.scrollWidth") <= 390

        right = p.evaluate("Math.max(...[...document.querySelectorAll('main *')].filter(e => e.checkVisibility() && !e.closest('.ui-filter-row')).map(e => e.getBoundingClientRect().right))")
        assert right <= 390.5, right
        p.locator("#line-e1 .ui-tline-what").click(force=True)
        p.wait_for_url(BASE + "/event/1")
    finally:
        ctx.close()
