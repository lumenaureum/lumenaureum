import json
import os
import re
import sys
from datetime import timedelta

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

def _day(A, days=10):
    return (A.today() + timedelta(days=days)).isoformat()

def _nice(A, iso):
    with A.app.test_request_context():
        return A.nice(iso)

def _other(A, sid, stage, day, name="Other Family", hid=None, until=None, start=None):

    hid = hid or sid
    run(A, "INSERT OR IGNORE INTO households (id, name) VALUES (?, ?)", (hid, name))
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, pencil_until)
              VALUES (?, ?, 'family', ?, ?, ?, ?)""", (sid, hid, stage, day, start, until))

def _pencil(c, act, sid=1, **kw):
    return c.post(f"/session/{sid}/pencil", data=dict(act=act, back=f"/c/1?s={sid}", **kw), environ_base=E)

def _until(A, sid=1):
    return q(A, "SELECT pencil_until FROM sessions WHERE id=?", (sid,))[0][0]

def _history(A):
    return [r[0] for r in q(A, "SELECT action FROM audit WHERE session_id=1 ORDER BY id")]

def test_pencil_in_holds_the_date_a_week_and_release_lets_it_go(env):
    A, c = env
    _session(A, "inquiry", days=10)
    day = _nice(A, _day(A))
    page = _page(c)
    assert f">Pencil in {day}</button>" in page and "Only you see it." in page
    assert _pencil(c, "pencil").status_code == 302
    assert _until(A) == (A.today() + timedelta(days=A.C.PENCIL_DAYS)).isoformat()
    page = _page(c)
    assert f"Penciled in {day}, held until {_nice(A, _until(A))}." in page
    assert ">Release the date</button>" in page and ">Change the hold</a>" in page and "Pencil in " + day not in page
    assert any(h_.startswith(f"Penciled in {day}, held until") for h_ in _history(A))
    _pencil(c, "release")
    assert _until(A) is None and f">Pencil in {day}</button>" in _page(c)
    assert f"Released the penciled date, {day}" in _history(A)

def test_hold_until_a_day_picked_and_never_a_past_day(env):
    A, c = env
    _session(A, "inquiry", days=20)
    pick = _day(A, 14)
    _pencil(c, "pencil", until=pick)
    assert _until(A) == pick
    later = _day(A, 18)
    _pencil(c, "until", until=later)
    assert _until(A) == later
    r = _pencil(c, "until", until=(A.today() - timedelta(days=1)).isoformat())
    assert r.status_code == 302 and _until(A) == later
    assert "Not saved: pick a day from today on." in _page(c)

def test_only_a_lead_with_a_date_is_penciled(env):
    A, c = env
    _session(A, "booked", days=10)
    _pencil(c, "pencil")
    assert _until(A) is None and ">Pencil in " not in _page(c)
    run(A, "UPDATE sessions SET stage='inquiry', shoot_date=NULL WHERE id=1")
    _pencil(c, "pencil")
    assert _until(A) is None

def test_a_lapsed_hold_is_asked_once_on_today_release_or_extend(env):
    A, c = env
    _session(A, "inquiry", days=10)
    run(A, "UPDATE sessions SET pencil_until=? WHERE id=1", ((A.today() - timedelta(days=1)).isoformat(),))
    day = _nice(A, _day(A))
    today_ = c.get("/", environ_base=E).get_data(as_text=True)
    assert today_.count(f"The hold on {day} lapsed") == 1
    row = today_[today_.index(f"The hold on {day} lapsed"):]
    row = row[:row.index("</form>", row.index(f"Extend the hold {A.C.PENCIL_DAYS} days"))]
    assert ">Release the date</button>" in row
    page = _page(c)
    assert page.count(f"The hold on {day} lapsed") == 1 and f">Extend the hold {A.C.PENCIL_DAYS} days</button>" in page
    _pencil(c, "extend")
    assert _until(A) == (A.today() + timedelta(days=A.C.PENCIL_DAYS)).isoformat()
    assert f"The hold on {day} lapsed" not in c.get("/", environ_base=E).get_data(as_text=True)
    assert any(h_.startswith(f"Extended the hold on {day} to ") for h_ in _history(A))
    run(A, "UPDATE sessions SET pencil_until=? WHERE id=1", ((A.today() - timedelta(days=2)).isoformat(),))
    _pencil(c, "release")
    assert _until(A) is None and f"The hold on {day}" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_a_running_hold_is_not_asked_about(env):
    A, c = env
    _session(A, "quoted", days=10)
    run(A, "UPDATE sessions SET pencil_until=? WHERE id=1", (A.today().isoformat(),))
    assert "The hold on" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_booking_the_lead_makes_the_penciled_date_its_booked_date(env):
    A, c = env
    _session(A, "quoted", days=10)
    _pencil(c, "pencil")
    r = c.post("/session/1/quick", data=dict(act="book", back="/c/1?s=1"), environ_base=E)
    assert r.status_code == 302
    row = q(A, "SELECT stage, shoot_date, pencil_until FROM sessions WHERE id=1")[0]
    assert row["stage"] == "booked" and row["shoot_date"] == _day(A) and row["pencil_until"] is None
    assert f"The penciled date, {_nice(A, _day(A))}, is now the booked date" in _history(A)

def test_not_booking_the_lead_releases_its_pencil(env):
    A, c = env
    _session(A, "inquiry", days=10)
    _pencil(c, "pencil")
    c.post("/session/1/stage", data=dict(stage="lost", lost_reason="Other", lost_note="went elsewhere", need_reason="1",
                                         back="/c/1?s=1"), environ_base=E)
    assert q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "lost" and _until(A) is None

def test_the_clash_warning_counts_a_penciled_date_and_says_so(env):
    A, c = env
    _session(A, "booked", days=10, start="10:00")
    _other(A, 2, "inquiry", _day(A), name="Penciled Family", start="10:30", until=_day(A, 3))
    _other(A, 3, "quoted", _day(A), name="Plain Lead", start="10:15")
    with A.app.test_request_context():
        cl = {c_["name"]: c_ for c_ in A.clashes(1)}
    assert cl["Penciled Family"]["penciled"] and cl["Penciled Family"]["when"].endswith(", penciled")
    assert not cl["Plain Lead"]["penciled"] and "penciled" not in cl["Plain Lead"]["when"]
    r = c.get(f"/availability?d={_day(A)}&t=10:00&sid=1", environ_base=E).get_json()
    assert any(x_.startswith("Penciled Family:") and x_.endswith(", penciled") for x_ in r["clashes"])
    run(A, "DELETE FROM sessions WHERE id=3")
    page = _page(c)
    row = page[page.index('<span class="ui-todo-what">Overlaps Penciled Family'):]
    assert ", penciled)" in row[:row.index("</span>")]

def test_an_inquiry_shows_at_once_what_already_sits_on_its_date(env):
    A, c = env
    _session(A, "inquiry", days=12, msg="Inquiry from your website:\nThat Saturday?")
    day = _day(A, 12)
    assert "already has" not in _page(c)
    _other(A, 2, "booked", day, name="Booked Family")
    _other(A, 3, "inquiry", day, name="Pencil One", until=_day(A, 5))
    _other(A, 4, "quoted", day, name="Pencil Two", until=_day(A, 5))
    _other(A, 5, "inquiry", day, name="Not Penciled")
    _other(A, 6, "lost", day, name="Gone", until=_day(A, 5))
    words = f"{_nice(A, day)} already has a booking and 2 pencils."
    needs = _page(c).split("</header>", 1)[1].split("</section>", 1)[0]
    assert words in needs

    run(A, "INSERT INTO studio_inquiry_review (sha256, fields, received) VALUES ('t1', ?, ?)",
        (json.dumps(dict(name="Web Visitor", email="web@example.org", date=day, type="family")), A.today().isoformat() + "T09:00"))
    today_ = c.get("/", environ_base=E).get_data(as_text=True)
    item = today_[today_.index('id="inq-'):]
    item = item[:item.index("</li>")]
    assert words in item
    run(A, "UPDATE sessions SET stage='lost' WHERE id=2")
    assert f"{_nice(A, day)} already has 2 pencils." in _page(c)

def test_penciled_dates_show_lighter_on_the_calendar_and_sessions(env):
    A, c = env
    _session(A, "inquiry", days=3)
    month = c.get(f"/calendar?m={_day(A, 3)[:7]}&d={_day(A, 3)}", environ_base=E).get_data(as_text=True)
    assert "ui-penciled" not in month
    _pencil(c, "pencil")
    month = c.get(f"/calendar?m={_day(A, 3)[:7]}&d={_day(A, 3)}", environ_base=E).get_data(as_text=True)
    assert re.search(r'class="mshoot [^"]*ui-penciled"', month) and "Penciled in" in month
    sess = c.get("/sessions?view=leads", environ_base=E).get_data(as_text=True)
    assert 'class="ui-fitem ui-penciled"' in sess and f"{_nice(A, _day(A, 3))}, penciled" in sess
    year = c.get(f"/calendar/year?y={_day(A, 3)[:4]}", environ_base=E).get_data(as_text=True)
    assert "Morgan (penciled)" in year

def test_nothing_of_a_pencil_reaches_a_client_document(env):
    A, c = env
    _session(A, "quoted", days=10)
    _pencil(c, "pencil")
    for path in ("/session/1/quote", "/session/1/invoice", "/session/1/agreement"):
        r = c.get(path, environ_base=E)
        assert r.status_code in (200, 302, 404), (path, r.status_code)
        assert "pencil" not in r.get_data(as_text=True).lower(), path

    import plugins.studio_page as S
    src = open(S.__file__).read().lower()
    assert "pencil" not in src
