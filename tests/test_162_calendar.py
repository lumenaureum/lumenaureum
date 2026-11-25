import json
import os
import socket
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_calendars import FakeGoogle, FakeGraph, rsa_key_json
from test_264_160 import _studio, rows, run
from test_calsend_plugin import _on, _page, _setup_google, google
from test_client_page_141 import E, env

def _pub(start, minutes=60, summary="Family session", all_day=False):
    end = start + (timedelta(days=1) if all_day else timedelta(minutes=minutes))
    return dict(summary=summary, start=start, end=end, all_day=all_day, tentative=False)

@pytest.fixture
def gcal(tmp_path, monkeypatch):
    import calsend as S
    key, public = rsa_key_json()
    g = FakeGoogle(tmp_path, public, email=key["client_email"])
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GoogleConnection, "TOKEN_URL", g.url + "/token")
    monkeypatch.setattr(S.GoogleConnection, "API", g.url)
    yield S, g, S.GoogleCalendar(S.GoogleConnection(key), g.calendar_id)
    g.close()

def test_two_studios_sending_to_one_calendar_never_delete_each_others_events(gcal):

    S, g, cal = gcal
    since = datetime.now(timezone.utc) - timedelta(days=90)
    t = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)
    a = {S.ident("aa" * 32, ("s", 1)): _pub(t)}
    b = {S.ident("bb" * 32, ("s", 1)): _pub(t + timedelta(hours=3))}
    S.sync(cal, a, {}, since)
    S.sync(cal, b, {}, since)
    assert set(a) <= set(g.live()), "studio B deleted studio A's event"

def test_outlook_all_day_session_on_the_windows_first_day_is_not_added_twice(tmp_path, monkeypatch):

    import calsend as S
    g = FakeGraph(tmp_path)
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GraphConnection, "LOGIN", g.url + "/common/oauth2/v2.0")
    monkeypatch.setattr(S.GraphConnection, "API", g.url)
    try:
        g.access.add("at-fixed")
        conn = S.GraphConnection(g.client_id, "rt-0")
        conn._token, conn._until = "at-fixed", time.time() + 3600
        cal = S.GraphCalendar(conn, "cal-sessions")
        tz = ZoneInfo("America/New_York")
        d = date(2026, 8, 26)
        since = datetime(d.year, d.month, d.day, tzinfo=tz)
        want = {S.ident("cc" * 32, ("s", 9)): _pub(d, all_day=True)}
        state = {}
        S.sync(cal, want, state, since)
        S.sync(cal, want, state, since)
        assert len(g.in_calendar("cal-sessions")) == 1
    finally:
        g.close()

def test_setup_and_test_event_never_hold_the_write_lock_while_talking_to_the_service(google, monkeypatch):

    A, c, g, key = google
    import calsend as S
    import locks
    held = []
    real = S.fetch

    def spy(*a, **k):
        held.append(locks.WRITES._is_owned())
        return real(*a, **k)

    monkeypatch.setattr(S, "fetch", spy)
    _studio(A)
    _setup_google(c, g, key)
    c.post("/settings/calendar-send/test", environ_base=E)
    assert held and not any(held), f"{sum(held)} of {len(held)} calls to the service were made holding the write lock"

def test_a_dripping_server_is_cut_off_after_a_bounded_time(monkeypatch):

    import calsend as S
    monkeypatch.setattr(S, "TIMEOUT", 1)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def drip():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 207 Multi-Status\r\nContent-Type: application/xml\r\nContent-Length: 100000\r\n\r\n")
        while not stop.is_set():
            try:
                conn.sendall(b" ")
            except OSError:
                break
            time.sleep(0.5)
        conn.close()

    threading.Thread(target=drip, daemon=True).start()
    done = threading.Event()
    def go():
        try:
            S.fetch("PROPFIND", f"http://127.0.0.1:{port}/", lan_http=True)
        except S.SendError:
            pass
        finally:
            done.set()

    threading.Thread(target=go, daemon=True).start()
    try:
        assert done.wait(8), "fetch was still reading after 8 s with TIMEOUT = 1 s"
    finally:
        stop.set()
        srv.close()

def test_an_entity_declaration_in_utf16_is_refused_too():

    import calsend as S
    doc = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">'
           '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
           '<d:multistatus xmlns:d="DAV:"><d:response><d:href>&b;</d:href></d:response></d:multistatus>')
    with pytest.raises(S.SendError):
        S.safe_xml(doc.encode("utf-16"))

def test_caldav_never_trusts_a_stranger_under_a_public_suffix():

    import calsend as S
    conn = S.CalDAVConnection("https://dav.myphotos.co.uk/", "me", "pw")
    assert not conn.trusted("https://attacker.co.uk/calendars/")
    conn = S.CalDAVConnection("https://studio.duckdns.org/remote.php/dav", "me", "pw")
    assert not conn.trusted("https://someone-else.duckdns.org/")

def test_an_abandoned_outlook_setup_does_not_keep_the_refresh_token(env, tmp_path, monkeypatch):

    A, c = env
    import calsend as S
    g = FakeGraph(tmp_path)
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GraphConnection, "LOGIN", g.url + "/common/oauth2/v2.0")
    monkeypatch.setattr(S.GraphConnection, "API", g.url)
    try:
        g.pending_polls = 0
        _on(c)
        c.post("/settings/calendar-send/outlook", data={"client_id": g.client_id}, environ_base=E)
        c.post("/settings/calendar-send/outlook/done", environ_base=E)
        assert "Pick the calendar" in _page(c)
        run(A, "UPDATE calsend_secret SET at = at - 7200 WHERE name='pending'")
        page = _page(c)
        assert "Pick the calendar" not in page
        c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
        assert rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0
    finally:
        g.close()

def test_stop_and_leave_does_not_strand_the_test_event(google):

    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    c.post("/settings/calendar-send/test", environ_base=E)
    c.post("/settings/calendar-send/off", data={"remove": "0"}, environ_base=E)
    assert not [e for e in g.live().values() if e["summary"] == "Test event from your studio"]

def test_the_page_does_not_say_inquiries_never_show_while_a_penciled_inquiry_is_sent(google):
    A, c, g, key = google
    _studio(A)
    run(A, "UPDATE sessions SET pencil_until=? WHERE id=3", ((date.today() + timedelta(days=5)).isoformat(),))
    _setup_google(c, g, key)
    sent = [e["summary"] for e in g.live().values()]
    page = _page(c)
    assert not (any("(penciled)" in s for s in sent) and len(sent) == 4 and "Inquiries never show" in page)

def _status(A):
    return json.loads(rows(A, "SELECT value FROM settings WHERE key='calsend_status'")[0][0])

def _set_status(A, **kw):
    st = dict(_status(A), **kw)
    run(A, "UPDATE settings SET value=? WHERE key='calsend_status'", (json.dumps(st),))

def test_after_a_failure_saves_do_not_each_start_a_full_send(google):

    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    g.fail = 503
    run(A, "UPDATE sessions SET start_time='12:00' WHERE id=1")
    c.post("/settings/calendar-send/now", environ_base=E)
    st = _status(A)
    assert st["ok"] is False and st["fails"] == 1 and st["next_at"] - time.time() > 4 * 60
    n = len(g.requests)
    for hm in ("12:15", "12:30", "12:45"):
        run(A, "UPDATE sessions SET start_time=? WHERE id=1", (hm,))
        c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) == n, "a save started a send while the studio was waiting to try again"
    assert "tries again by itself at" in _page(c)
    _set_status(A, next_at=time.time() - 1)
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) > n and _status(A)["fails"] == 2
    assert _status(A)["next_at"] - time.time() > 9 * 60
    g.fail = None
    c.post("/settings/calendar-send/now", environ_base=E)
    st = _status(A)
    assert st["ok"] is True and not st.get("fails") and "Last sent" in _page(c)

def test_a_429_retry_after_is_honored_even_by_send_now(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    g.fail, g.fail_headers = 429, {"Retry-After": "7200"}
    run(A, "UPDATE sessions SET start_time='12:00' WHERE id=1")
    c.post("/settings/calendar-send/now", environ_base=E)
    st = _status(A)
    assert st["ok"] is False and 7100 < st["next_at"] - time.time() <= 7200
    n = len(g.requests)
    c.post("/settings/calendar-send/now", environ_base=E)
    assert len(g.requests) == n, "Send now ignored the service's Retry-After"
    g.fail, g.fail_headers = None, {}
    _set_status(A, asked_until=time.time() - 1, next_at=time.time() - 1)
    c.post("/settings/calendar-send/now", environ_base=E)
    assert _status(A)["ok"] is True

def test_a_refused_sign_in_is_not_tried_again_on_every_save(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    g.tokens.clear()
    g.check_jwt = lambda jwt: (_ for _ in ()).throw(ValueError("revoked"))
    run(A, "UPDATE sessions SET start_time='12:00' WHERE id=1")
    c.post("/settings/calendar-send/now", environ_base=E)
    st = _status(A)
    assert st["ok"] is False and st["next_at"] - time.time() > 5 * 3600
    n = len(g.requests)
    run(A, "UPDATE sessions SET start_time='12:30' WHERE id=1")
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) == n

def test_send_now_page_follows_the_job_and_updates_when_it_ends(google):

    A, c, g, key = google
    import jobs
    _studio(A)
    _setup_google(c, g, key)
    r = c.post("/settings/calendar-send/now", environ_base=E)
    assert r.status_code == 302 and "#" not in r.location
    page = _page(c)
    assert "This page updates when it is done." in page and "This page shows when it is done" not in page
    assert "data-poll=" not in page.split('id="calsend-conn"')[1].split("</div>")[0]
    jobs.JOBS["calendar-send"] = dict(key="calendar-send", label="x", state="running", done=0, total=0, message="",
                                      started=time.time(), ended=None, link="", quiet=True)
    try:
        page = _page(c)
        head = page.split('id="calsend-conn"')[1]
        assert "Sending now" in head and 'data-poll="/jobs/state?key=calendar-send"' in head
        assert c.get("/jobs/state?key=calendar-send", environ_base=E).get_json()["state"] == "running"
    finally:
        jobs.JOBS.pop("calendar-send", None)
    assert c.get("/jobs/state?key=calendar-send", environ_base=E).get_json()["state"] == "idle"
    ui = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "ui.js")).read()
    assert "if (r.state !== 'running') { clearInterval(t); location.reload(); return; }" in ui

def test_an_icloud_refusal_asks_only_for_what_is_missing_and_keeps_what_was_typed(env):
    A, c = env
    _on(c)
    r = c.post("/settings/calendar-send/caldav", environ_base=E,
               data={"service": "icloud", "server": "", "user": "me@example.org", "password": ""})
    assert r.status_code == 302 and "#" not in r.location
    page = _page(c, r.location)
    assert "Enter the app password. Nothing was saved." in page and "server address. Nothing" not in page
    assert 'value="me@example.org"' in page and '<option value="icloud" selected>' in page
    r = c.post("/settings/calendar-send/caldav", environ_base=E,
               data={"service": "other", "server": "", "user": "", "password": "pw"})
    assert "Enter the server address and your user name. Nothing was saved." in _page(c, r.location)

def test_a_connection_refused_is_said_without_a_python_error_name(env):
    A, c = env
    _on(c)
    s_ = socket.socket()
    s_.bind(("127.0.0.1", 0))
    port = s_.getsockname()[1]
    s_.close()
    r = c.post("/settings/calendar-send/caldav", environ_base=E, data={
        "service": "other", "server": f"http://127.0.0.1:{port}/dav/", "user": "me", "password": "app-pw"})
    page = _page(c, r.location)
    assert "The calendar service couldn&#39;t be reached: the server refused the connection" in page
    assert "Error" not in page.split("couldn&#39;t be reached")[1][:200]
    assert f'value="http://127.0.0.1:{port}/dav/"' in page and 'value="me"' in page and "app-pw" not in page
    import calsend as S
    with pytest.raises(S.SendError) as e:
        S.fetch("GET", f"http://127.0.0.1:{port}/", lan_http=True)
    assert "Error" not in e.value.words

def test_the_google_refusal_keeps_the_calendar_id_and_does_not_jump(google):
    A, c, g, key = google
    _on(c)
    r = c.post("/settings/calendar-send/google", environ_base=E, content_type="multipart/form-data",
               data={"key": (__import__("io").BytesIO(json.dumps(key).encode()), "k.json"), "calendar": "nope@x"})
    assert r.status_code == 302 and "#" not in r.location
    page = _page(c, r.location)
    assert "Google doesn&#39;t know that calendar ID" in page and 'value="nope@x"' in page

def test_the_page_shows_the_google_calendars_name_not_its_id(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    page = _page(c)
    assert "Studio sessions" in page and g.calendar_id not in page

def test_events_are_titled_with_the_singular_type_name(google):

    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    one = A.C.doc_label("family")
    assert one != A.C.SESSION_TYPES["family"]
    assert sorted(e["summary"] for e in g.live().values()) == sorted([one, one, one + " (penciled)"])

def test_caldav_status_heading_names_the_service_and_the_calendar(env, tmp_path):
    import test_calsend_caldav as T
    if not T._have_radicale():
        pytest.skip("Radicale is not installed for the tests")
    A, c = env
    with T.start_radicale(tmp_path) as port:
        _studio(A)
        _on(c)
        c.post("/settings/calendar-send/caldav", environ_base=E, data={
            "service": "other", "server": f"http://127.0.0.1:{port}/", "user": T.USER, "password": T.PW})
        page = _page(c)
        pick = page.index("Studio sessions") > page.index("Personal")
        c.post("/settings/calendar-send/pick", data={"pick": "1" if pick else "0"}, environ_base=E)
        page = _page(c)
        assert "Sending to 127.0.0.1" in page and "your calendar service" not in page
        assert "<b>Studio sessions</b>" in page.split('id="calsend-conn"')[1]
