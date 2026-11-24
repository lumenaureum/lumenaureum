import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, env
from test_264_160 import run, rows, _day, _token, _turn, _feed, _events

def _first_saturday(A, after=14):
    d_ = A.today() + timedelta(days=after)
    while d_.weekday() != 5:
        d_ += timedelta(days=1)
    return d_

def test_calendar_link_calls_a_booked_group_event_penciled(env):

    A, c = env
    day = _day(A, 30)
    run(A, "INSERT INTO events (id, name, date) VALUES (1, 'Team day', ?)", (day,))
    run(A, "INSERT INTO households (id, name) VALUES (3, 'Parents')")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, duration_min)
              VALUES (6, 3, 'family', 'inquiry', ?, '09:00', 30)""", (day,))
    assert c.post("/session/6/pencil", data={"act": "pencil"}, environ_base=E).status_code == 302
    r = c.post("/session/6", data={"title": "", "type": "family", "shoot_date": day, "start_time": "09:00",
                                   "stops": "", "notes": "", "event_id": "1"}, environ_base=E)
    assert r.status_code == 302
    assert tuple(rows(A, "SELECT event_id, pencil_until IS NOT NULL FROM sessions WHERE id=6")[0]) == (1, 1)
    run(A, """INSERT INTO sessions (id, household_id, event_id, type, stage, shoot_date, start_time, duration_min)
              VALUES (7, 1, 1, 'family', 'booked', ?, '11:00', 30)""", (day,))
    _turn(c, "on")
    evs = _events(_feed(A, _token(A)).get_data(as_text=True))
    assert len(evs) == 1
    ev = evs[0]
    assert "STATUS:CONFIRMED" in ev

    assert "SUMMARY:Family (penciled)" not in ev, ev

def test_an_unpenciled_inquiry_shows_as_a_full_shoot_on_the_month_calendar(env):

    A, c = env
    day = (A.today() + timedelta(days=20)).isoformat()
    run(A, "INSERT INTO households (id, name) VALUES (2, 'Booked Family')")
    run(A, "INSERT INTO households (id, name) VALUES (3, 'Penciled Family')")
    run(A, "INSERT INTO households (id, name) VALUES (4, 'Website Asker')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time) VALUES (2, 2, 'family', 'booked', ?, '09:00')", (day,))
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, pencil_until)
              VALUES (3, 3, 'family', 'quoted', ?, '13:00', ?)""", (day, _day(A, 5)))

    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (4, 4, 'family', 'inquiry', ?, 1)""", (day,))
    page = c.get(f"/calendar?m={day[:7]}&d={day}", environ_base=E).get_data(as_text=True)
    sec = page.split(f'id="day-{day}"', 1)[1].split("</section>", 1)[0]
    assert "Booked Family" in sec and "Penciled Family" in sec
    row = sec[sec.index("Website Asker"):][:400]
    print(row)

    assert "Shoot" not in row.split("</a>", 1)[0], row
    cell = page.split(f'data-day="{day}"', 1)[1].split("</a>", 1)[0]
    asker = cell[:cell.index("Website Asker")]
    asker = asker[asker.rindex('<span class="mshoot'):]
    assert "ui-penciled" in asker or "lead" in asker, asker

def test_an_unpenciled_inquiry_takes_a_saturday_off_the_year_view(env):

    A, c = env
    sat = _first_saturday(A).isoformat()
    run(A, "INSERT INTO households (id, name) VALUES (4, 'Website Asker')")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (4, 4, 'family', 'inquiry', ?, 1)""", (sat,))
    with A.app.test_request_context():
        A.db()
        assert A.date_holds(sat) is None
    page = c.get(f"/calendar/year?y={sat[:4]}", environ_base=E).get_data(as_text=True)
    m = re.search(r'<span class="yday[^"]*"(?: data-tip="([^"]*)")?>' + str(int(sat[8:10])) + "<", page)

    cells = re.findall(r'<span class="(yday[^"]*)"(?: data-tip="([^"]*)")?>(\d+)</span>', page)
    hit = [cl for cl, tip, n in cells if "Website Asker" in (tip or "")]
    assert not hit, f"an unheld inquiry marks the Saturday busy: {hit}"

def test_a_penciled_lead_put_into_a_group_event_can_not_be_released(env):

    A, c = env
    day = _day(A, 30)
    run(A, "INSERT INTO events (id, name, date) VALUES (1, 'Team day', ?)", (day,))
    run(A, "INSERT INTO households (id, name) VALUES (3, 'Parents')")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, duration_min)
              VALUES (6, 3, 'family', 'inquiry', ?, '09:00', 30)""", (day,))
    c.post("/session/6/pencil", data={"act": "pencil", "until": _day(A, 25)}, environ_base=E)
    c.post("/session/6", data={"title": "", "type": "family", "shoot_date": day, "start_time": "09:00",
                               "stops": "", "notes": "", "event_id": "1"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        assert A.date_holds(day)["pencils"] == 1
    page = c.get("/c/3?s=6", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "Parents" in page and "Team day" in page
    assert ">Release the date</button>" in page, "the held date can't be released from the client page"

def test_turn_on_again_silently_replaces_a_link_phones_use(env):

    A, c = env
    _turn(c, "on")
    first = _token(A)
    assert _feed(A, first).status_code == 200
    _turn(c, "on")
    assert _feed(A, first).status_code == 200, "the phone's calendar link stopped working without any warning"

def test_today_lists_an_unheld_inquiry_as_a_shoot(env):

    A, c = env
    run(A, "INSERT INTO households (id, name) VALUES (4, 'Website Asker')")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (4, 4, 'family', 'inquiry', ?, 1)""", (_day(A, 5),))
    page = c.get("/", environ_base=E).get_data(as_text=True)
    side = page.split('id="two-weeks"', 1)[1].split("</section>", 1)[0]
    assert "Shoot: Website Asker" not in side, side[:600]

def test_today_does_not_call_a_booked_group_event_day_penciled(env):

    A, c = env
    day = _day(A, 5)
    run(A, "INSERT INTO events (id, name, date) VALUES (1, 'Team day', ?)", (day,))
    run(A, "INSERT INTO households (id, name) VALUES (3, 'Parents')")
    run(A, """INSERT INTO sessions (id, household_id, event_id, type, stage, shoot_date, start_time, duration_min,
              pencil_until) VALUES (6, 3, 1, 'family', 'inquiry', ?, '09:00', 30, ?)""", (day, _day(A, 20)))
    run(A, """INSERT INTO sessions (id, household_id, event_id, type, stage, shoot_date, start_time, duration_min)
              VALUES (7, 1, 1, 'family', 'booked', ?, '11:00', 30)""", (day,))
    page = c.get("/", environ_base=E).get_data(as_text=True)
    side = page.split('id="two-weeks"', 1)[1].split("</section>", 1)[0]
    assert "Penciled in: Team day" not in side, side[:600]
    assert "Shoot: Team day" in side, side[:600]
