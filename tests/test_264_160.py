import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, env

SECRETS = ("Morgan", "Alex", "alex.morgan.long.address@example.com", "555-555-0142", "Longwood", "fountain",
           "Birch Hollow", "allergic", "Maple Lane", "Lakeside", "Cedar Hall", "Quiet Pines", "Otter")

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def rows(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _day(A, n):
    return (A.today() + timedelta(days=n)).isoformat()

def _studio(A):

    run(A, "UPDATE households SET address='12 Maple Lane', notes='Otter the dog' WHERE id=1")
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops, notes, title, intake,
              duration_min) VALUES (1, 1, 'family', 'booked', ?, '15:30', 'Longwood Gardens, main fountain entrance',
              'Youngest is allergic to grass', 'Birch Hollow', '{"address": "12 Maple Lane"}', 90)""", (_day(A, 10),))
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, pencil_until)
              VALUES (2, 1, 'family', 'quoted', ?, '10:00', ?)""", (_day(A, 20), _day(A, 5)))
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time) VALUES (3, 1, 'family', 'inquiry', ?, '09:00')",
        (_day(A, 21),))
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (4, 1, 'family', 'lost', ?)", (_day(A, 22),))
    run(A, "INSERT INTO households (id, name, demo) VALUES (2, 'Quiet Pines', 1)")
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (5, 2, 'family', 'booked', ?)", (_day(A, 23),))
    run(A, "INSERT INTO events (id, name, date, venue, notes) VALUES (1, 'Lakeside School', ?, 'Cedar Hall', 'Otter')",
        (_day(A, 30),))
    run(A, "INSERT INTO households (id, name) VALUES (3, 'Lakeside parents')")
    for sid_, hm_ in ((6, "09:00"), (7, "11:00")):
        run(A, """INSERT INTO sessions (id, household_id, event_id, type, stage, shoot_date, start_time, duration_min)
                  VALUES (?, 3, 1, 'family', 'booked', ?, ?, 30)""", (sid_, _day(A, 30), hm_))
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, payer_event) VALUES (8, 3, 'family', 'booked', ?, 1)",
        (_day(A, 30),))

def _token(A):
    r_ = rows(A, "SELECT value FROM settings WHERE key='calendar_feed_token'")
    return r_[0][0] if r_ else None

def _turn(c, act):
    return c.post("/settings/calendar-link", data={"act": act}, environ_base=E)

def _feed(A, token, addr="127.0.0.1"):
    return A.app.test_client().get(f"/calendar-feed/{token}.ics", environ_base={"REMOTE_ADDR": addr})

def _events(text):
    return re.findall(r"BEGIN:VEVENT\r\n(.*?)END:VEVENT", text, re.S)

def test_off_by_default_and_the_page_says_so(env):
    A, c = env
    assert _token(A) is None
    page = c.get("/settings?tab=calendar", environ_base=E).get_data(as_text=True)
    assert 'id="calendar-link"' in page and ">Turn on the calendar link</button>" in page
    assert "/calendar-feed/" not in page
    assert _feed(A, "x" * 43).status_code == 404

def test_the_feed_is_redacted_to_type_day_time_and_length(env):
    A, c = env
    _studio(A)
    assert _turn(c, "on").status_code == 302
    tok = _token(A)
    assert tok and len(tok) == 43
    r = _feed(A, tok)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/calendar")
    text = r.get_data(as_text=True)
    for s_ in SECRETS:
        assert s_.lower() not in text.lower(), s_
    for field_ in ("LOCATION", "DESCRIPTION", "URL", "ATTENDEE", "ORGANIZER", "GEO", "CONTACT", "COMMENT"):
        assert not re.search(r"(^|\r\n)" + field_ + r"[;:]", text), field_
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert all(len(ln.encode()) <= 75 for ln in text.split("\r\n"))
    evs = _events(text)

    assert len(evs) == 3, evs
    summaries = sorted(re.search(r"SUMMARY:(.*?)\r\n", e_).group(1) for e_ in evs)
    label = A.C.SESSION_TYPES["family"]
    assert summaries == sorted([label, label, label + " (penciled)"])
    pencil = next(e_ for e_ in evs if "(penciled)" in e_)
    assert "STATUS:TENTATIVE" in pencil
    booked = next(e_ for e_ in evs if "CONFIRMED" in e_ and "15" in e_)
    start = re.search(r"DTSTART:(\d{8}T\d{6}Z)", booked).group(1)
    end = re.search(r"DTEND:(\d{8}T\d{6}Z)", booked).group(1)
    from datetime import datetime
    span = datetime.strptime(end, "%Y%m%dT%H%M%SZ") - datetime.strptime(start, "%Y%m%dT%H%M%SZ")
    assert span == timedelta(minutes=90)

    ev = [e_ for e_ in evs if e_ is not booked and e_ is not pencil][0]
    s_, e2_ = (datetime.strptime(re.search(k_ + r":(\d{8}T\d{6}Z)", ev).group(1), "%Y%m%dT%H%M%SZ") for k_ in ("DTSTART", "DTEND"))
    assert e2_ - s_ == timedelta(hours=2, minutes=30)

    assert all(re.search(r"UID:[0-9a-f]{32}@lumen-aureum\r\n", e_) for e_ in evs)

def test_a_new_link_stops_the_old_one_and_turning_off_stops_both(env):
    A, c = env
    _turn(c, "on")
    old = _token(A)
    assert _feed(A, old).status_code == 200
    _turn(c, "new")
    new = _token(A)
    assert new != old
    assert _feed(A, old).status_code == 404 and _feed(A, new).status_code == 200
    page = c.get("/settings?tab=calendar", environ_base=E).get_data(as_text=True)
    assert new in page and old not in page and ">Make a new link</button>" in page and ">Turn off the calendar link</button>" in page
    _turn(c, "off")
    assert _token(A) is None and _feed(A, new).status_code == 404

def test_the_token_never_reaches_history(env):
    A, c = env
    _turn(c, "on")
    t1 = _token(A)
    _turn(c, "new")
    t2 = _token(A)
    _feed(A, t2)
    _feed(A, "y" * 43)
    _turn(c, "off")
    lines = [" ".join(str(v_) for v_ in r_) for r_ in rows(A, "SELECT * FROM audit")]
    assert any("Turned on the calendar link" in ln for ln in lines)
    assert any("Made a new calendar link" in ln for ln in lines)
    assert any("Turned off the calendar link" in ln for ln in lines)
    assert not [ln for ln in lines if t1 in ln or t2 in ln]

def test_while_locked_it_answers_later_and_says_nothing_about_the_token(env):
    A, c = env
    _studio(A)
    _turn(c, "on")
    tok = _token(A)
    import datakey
    datakey.lock()
    for t_ in (tok, "z" * 43):
        r = _feed(A, t_)
        assert r.status_code == 503 and r.headers["Retry-After"] == "900"
        body = r.get_data(as_text=True)
        assert "BEGIN:VCALENDAR" not in body and "Family" not in body
    assert not A.app.config["feed_throttle"].ip

def test_wrong_addresses_are_throttled_apart_from_sign_in(env):
    A, c = env
    _turn(c, "on")
    tok = _token(A)
    for _ in range(5):
        assert _feed(A, "w" * 43, addr="127.0.0.9").status_code == 404
    r = _feed(A, tok, addr="127.0.0.9")
    assert r.status_code == 429 and "BEGIN:VCALENDAR" not in r.get_data(as_text=True)
    assert _feed(A, tok).status_code == 200
    assert not A.app.config["auth_throttle"].ip_blocked("127.0.0.9")

def test_outside_the_allowed_networks_it_is_refused(env):
    A, c = env
    _turn(c, "on")
    assert _feed(A, _token(A), addr="203.0.113.7").status_code == 403

def test_escaping_and_folding_follow_rfc_5545():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import calfeed
    assert calfeed.escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    long_ = "SUMMARY:" + "é" * 80
    folded = calfeed.fold(long_)
    parts = folded.split("\r\n")
    assert all(len(p_.encode()) <= 75 for p_ in parts) and all(p_.startswith(" ") for p_ in parts[1:])
    assert "".join(p_[1:] if i_ else p_ for i_, p_ in enumerate(parts)) == long_
    assert not calfeed.token_matches("short", "short") and not calfeed.token_matches("a" * 43, "")

def test_the_calendar_is_named_after_the_studio_or_studio_sessions():
    import calfeed
    assert calfeed.cal_name("Lantern  Hill Photo") == "Lantern Hill Photo sessions"
    assert calfeed.cal_name("") == calfeed.cal_name("   ") == calfeed.cal_name(None) == "Studio sessions"
    body = calfeed.build([], "t" * 32, studio="Lantern Hill Photo")
    assert "X-WR-CALNAME:Lantern Hill Photo sessions" in body
