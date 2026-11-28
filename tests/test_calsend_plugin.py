import io
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_calendars import FakeGoogle, FakeGraph, rsa_key_json
from test_264_160 import SECRETS, _day, _studio, rows, run
from test_client_page_141 import E, env

NAMES = ("Okonkwo-Lindqvist", "Wren Abernathy-Soto", "Thistlewood", "Quillfeather", "Marchetti Vale",
         "Hollowbrook Lane", "Juniper Ridge Chapel", "$1,234.56", "1234.56", "wren.soto@example.org",
         "Gate code 4471", "peanut allergy", "Fennimore Hall")

def _on(c):
    return c.post("/settings/plugins", data={"name": "calendar_send", "turn": "on"}, environ_base=E)

@pytest.fixture
def google(env, tmp_path, monkeypatch):
    A, c = env
    import calsend as S
    key, public = rsa_key_json()
    g = FakeGoogle(tmp_path, public, email=key["client_email"])
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GoogleConnection, "TOKEN_URL", g.url + "/token")
    monkeypatch.setattr(S.GoogleConnection, "API", g.url)
    yield A, c, g, key
    g.close()

def _setup_google(c, g, key):
    _on(c)
    return c.post("/settings/calendar-send/google", environ_base=E, content_type="multipart/form-data",
                  data={"key": (io.BytesIO(json.dumps(key).encode()), "studio-key.json"), "calendar": g.calendar_id})

def _send_now(c):
    return c.post("/settings/calendar-send/now", environ_base=E)

def _page(c, path="/settings/calendar-send"):
    return c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)

def _plant_everywhere(A, names):

    nm = list(names)
    pick = lambda: random.choice(nm)
    run(A, "UPDATE households SET name=?, address=?, notes=? WHERE id=1", (pick(), pick(), pick()))
    run(A, "UPDATE people SET name=?, email=?, phone=? WHERE household_id=1", (pick(), pick(), pick()))
    run(A, "UPDATE sessions SET notes=?, title=?, stops=?, intake=? WHERE id IN (1, 2, 6, 7)",
        (pick(), pick(), pick(), json.dumps({"address": pick(), "timeline": pick()})))
    run(A, "UPDATE events SET name=?, venue=?, notes=? WHERE id=1", (pick(), pick(), pick()))
    run(A, "UPDATE households SET name=? WHERE id=3", (pick(),))

def test_off_by_default_and_settings_offers_the_two_ways(env):
    A, c = env
    assert not rows(A, "SELECT value FROM settings WHERE key='plugin_on_calendar_send'")
    r = c.get("/settings/calendar-send", environ_base=E)
    assert r.status_code == 302 and "/settings/plugin/calendar_send" in r.location
    page = _page(c, "/settings?tab=calendar")
    assert 'id="calendar-send"' in page and "Send sessions to your calendar right away" in page
    assert page.index('id="calendar-link"') < page.index('id="calendar-send"')
    assert "ICSx5" in page and "Tailscale" in page
    assert "Send sessions to your calendar is off." in _page(c, "/settings/plugin/calendar_send")
    assert c.post("/settings/calendar-send/now", environ_base=E).status_code == 404
    _on(c)
    page = _page(c)
    for w in ("Google Calendar", "Outlook or Microsoft 365", "iCloud and others"):
        assert w in page
    assert "Make changes to events" in _page(c, "/settings/calendar-send?with=google")
    caldav = _page(c, "/settings/calendar-send?with=caldav")
    assert "reaches every calendar of that Apple Account" in caldav and "Proton" in caldav
    assert "Calendars (CalDAV) only" in caldav
    assert "every calendar of that account" in _page(c, "/settings/calendar-send?with=outlook")

def test_google_setup_sends_exactly_what_the_calendar_link_carries(google):
    A, c, g, key = google
    _studio(A)
    random.seed(7)
    _plant_everywhere(A, SECRETS + NAMES)
    r = _setup_google(c, g, key)
    assert r.status_code == 302, r.get_data(as_text=True)
    live = g.live()
    label = A.C.doc_label("family")
    assert sorted(e["summary"] for e in live.values()) == sorted([label, label + " (penciled)", label])

    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    import calfeed
    with A.app.app_context():
        pubs = [calfeed.public(e) for e in A.calendar_feed_entries()]
    sent = sorted((e["summary"], e["start"].get("dateTime") or e["start"].get("date")) for e in live.values())
    want = sorted((p["summary"], p["start"].astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                   if not p["all_day"] else p["start"].isoformat()) for p in pubs)
    assert sent == want
    text = g.bodies().lower()
    for s_ in SECRETS + NAMES:
        assert s_.lower() not in text, s_
    for e in live.values():
        assert set(e) <= {"id", "summary", "start", "end", "status", "transparency", "visibility",
                          "extendedProperties", "etag"}
    assert not [r_ for r_ in g.requests if "evil.example" in r_["raw"]]
    page = _page(c)
    assert "Last sent" in page and "3 sessions" in page and key["client_email"] in page
    assert "PRIVATE KEY" not in page and "abc123keyid" not in page

def test_fuzz_no_client_word_ever_leaves_in_any_request(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    rnd = random.Random(2026)
    for round_ in range(6):
        words = ["".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(6, 14))).capitalize()
                 for _ in range(12)]
        _plant_everywhere(A, words)
        run(A, "UPDATE sessions SET shoot_date=?, start_time=? WHERE id=1",
            (_day(A, 10 + round_), f"{9 + round_:02d}:15"))
        _send_now(c)
        text = g.bodies().lower()
        for w in words:
            assert w.lower() not in text, w

def test_a_saved_change_is_sent_in_the_background_move_cancel_and_inquiry(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    assert len(g.live()) == 3
    import calsend as S
    salt = rows(A, "SELECT value FROM settings WHERE key='calsend_salt'")[0][0]
    one = S.ident(salt, ("s", 1))
    assert one in g.live()

    run(A, "UPDATE sessions SET shoot_date=?, start_time='08:45' WHERE id=1", (_day(A, 12),))
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert g.live()[one]["start"]["dateTime"].endswith(":45:00Z")

    run(A, "UPDATE sessions SET pencil_until=NULL WHERE id=2")
    _send_now(c)
    assert S.ident(salt, ("s", 2)) not in g.live() and len(g.live()) == 2

    run(A, "UPDATE sessions SET stage='lost' WHERE id=1")
    _send_now(c)
    assert one not in g.live() and len(g.live()) == 1

def test_a_restore_or_a_week_off_adds_nothing_twice(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    run(A, "DELETE FROM calsend_events")
    run(A, "DELETE FROM settings WHERE key IN ('calsend_digest', 'calsend_full_at')")
    run(A, "UPDATE sessions SET start_time='11:00' WHERE id=1")
    _send_now(c)
    assert len(g.live()) == 3
    posts = [r for r in g.requests if r["method"] == "POST" and r["path"].endswith("/events")]
    assert len(posts) == 3

def test_nothing_new_means_no_call_at_all(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    n = len(g.requests)
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) == n

def test_the_sign_in_is_sealed_and_never_shown_exported_or_in_history(google):
    A, c, g, key = google
    _setup_google(c, g, key)
    raw = rows(A, "SELECT value FROM calsend_secret WHERE name='conn'")[0][0]
    assert bytes(raw).startswith(b"LAE1") and b"PRIVATE KEY" not in bytes(raw)
    assert key["client_email"].encode() not in bytes(raw)
    allset = json.dumps([list(r_) for r_ in rows(A, "SELECT key, value FROM settings")])
    assert "PRIVATE KEY" not in allset and key["client_email"] not in allset
    export = c.get("/settings/export.json", environ_base=E).get_data(as_text=True)
    assert "PRIVATE KEY" not in export and "calsend" not in export
    hist = json.dumps([[str(v) for v in r_] for r_ in rows(A, "SELECT * FROM audit")])
    assert "PRIVATE KEY" not in hist and "abc123keyid" not in hist
    assert "Started sending sessions to Google Calendar" in hist

def test_a_failure_is_said_on_the_page_then_on_today_and_as_a_phone_notice(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    g.fail = 503
    run(A, "UPDATE sessions SET start_time='12:00' WHERE id=1")
    _send_now(c)
    page = _page(c)
    assert "Couldn&#39;t reach Google Calendar: it had a problem of its own" in page
    with A.app.test_request_context():
        A.db()
        assert not A.calendar_send_failing()
    st = json.loads(rows(A, "SELECT value FROM settings WHERE key='calsend_status'")[0][0])
    st["failing_since"] -= 2 * 3600
    run(A, "UPDATE settings SET value=? WHERE key='calsend_status'", (json.dumps(st),))
    with A.app.test_request_context():
        A.db()
        assert A.calendar_send_failing() is True
    assert "Sessions couldn&#39;t be sent to your calendar" in _page(c, "/")
    import inboxnotice as N
    said = N.STATUS["calendar"][3]("failing", None)
    assert said[1].startswith("Your studio couldn't send sessions") and "Google" not in said[1]
    g.fail = None
    _send_now(c)
    assert "Last sent" in _page(c) and "Sessions couldn&#39;t be sent" not in _page(c, "/")

def test_a_test_event_shows_then_is_taken_out(google):
    A, c, g, key = google
    _setup_google(c, g, key)
    c.post("/settings/calendar-send/test", environ_base=E)
    tests = [e for e in g.live().values() if e["summary"] == "Test event from your studio"]
    assert len(tests) == 1
    _studio(A)
    _send_now(c)
    assert [e for e in g.live().values() if e["summary"] == "Test event from your studio"]
    t = json.loads(rows(A, "SELECT value FROM settings WHERE key='calsend_test'")[0][0])
    t["at"] -= 3600
    run(A, "UPDATE settings SET value=? WHERE key='calsend_test'", (json.dumps(t),))
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert not [e for e in g.live().values() if e["summary"] == "Test event from your studio"]

def test_stop_and_remove_takes_its_events_out_and_forgets_the_sign_in(google):
    A, c, g, key = google
    _studio(A)
    g.events["dentist01"] = dict(id="dentist01", summary="Dentist", status="confirmed", etag='"1"')
    _setup_google(c, g, key)
    assert len(g.live()) == 4
    c.post("/settings/calendar-send/off", data={"remove": "1"}, environ_base=E)
    assert set(g.live()) == {"dentist01"}
    assert rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0
    assert not rows(A, "SELECT value FROM settings WHERE key='calsend_kind'")
    assert "Google Calendar" in _page(c) and "Check and start sending" not in _page(c)

def test_stop_and_remove_that_fails_keeps_sending_stopped_and_says_so(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    g.fail = 503
    c.post("/settings/calendar-send/off", data={"remove": "1"}, environ_base=E)
    assert "Couldn&#39;t remove the events" in _page(c)
    g.fail = None
    n = len(g.requests)
    run(A, "UPDATE sessions SET start_time='07:00' WHERE id=1")
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) == n
    c.post("/settings/calendar-send/off", data={"remove": "1"}, environ_base=E)
    assert g.live() == {} and rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0

def test_stop_and_leave_keeps_the_events_and_forgets_the_sign_in(google):
    A, c, g, key = google
    _studio(A)
    _setup_google(c, g, key)
    c.post("/settings/calendar-send/off", data={"remove": "0"}, environ_base=E)
    assert len(g.live()) == 3
    assert rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0
    n = len(g.requests)
    run(A, "UPDATE sessions SET start_time='07:00' WHERE id=1")
    c.post("/settings/calendar-link", data={"act": "on"}, environ_base=E)
    assert len(g.requests) == n

def test_switching_the_plugin_off_forgets_the_sign_in(google):
    A, c, g, key = google
    _setup_google(c, g, key)
    r = c.post("/settings/plugins", data={"name": "calendar_send", "turn": "off"}, environ_base=E)
    assert "The events already there stay" in r.get_data(as_text=True)
    c.post("/settings/plugins", data={"name": "calendar_send", "turn": "off", "anyway": "1"}, environ_base=E)
    assert rows(A, "SELECT value FROM settings WHERE key='plugin_on_calendar_send'")[0][0] == "0"
    assert rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0

def test_a_bad_key_or_calendar_saves_nothing(google):
    A, c, g, key = google
    _on(c)
    r = c.post("/settings/calendar-send/google", environ_base=E, content_type="multipart/form-data",
               data={"key": (io.BytesIO(b'{"type": "user"}'), "k.json"), "calendar": g.calendar_id})
    assert r.status_code == 302
    assert "isn&#39;t a service account key file" in _page(c, r.location)
    r = c.post("/settings/calendar-send/google", environ_base=E, content_type="multipart/form-data",
               data={"key": (io.BytesIO(json.dumps(key).encode()), "k.json"), "calendar": "wrong@group.calendar"})
    assert "isn&#39;t shared with the service account" in _page(c, r.location)
    assert rows(A, "SELECT COUNT(*) FROM calsend_secret")[0][0] == 0

def test_outlook_sign_in_with_a_code_pick_a_calendar_and_send(env, tmp_path, monkeypatch):
    A, c = env
    import calsend as S
    g = FakeGraph(tmp_path)
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GraphConnection, "LOGIN", g.url + "/common/oauth2/v2.0")
    monkeypatch.setattr(S.GraphConnection, "API", g.url)
    try:
        _studio(A)
        _on(c)
        assert "Application (client) ID" in _page(c, "/settings/calendar-send?with=outlook")
        c.post("/settings/calendar-send/outlook", data={"client_id": g.client_id}, environ_base=E)
        page = _page(c)
        assert "ABCD-EFGH" in page and "https://microsoft.com/devicelogin" in page
        r = c.post("/settings/calendar-send/outlook/done", environ_base=E)
        assert "hasn&#39;t seen the code yet" in _page(c, r.location)
        c.post("/settings/calendar-send/outlook/done", environ_base=E)
        page = _page(c)
        assert "Studio sessions" in page and "Pick the calendar" in page
        c.post("/settings/calendar-send/pick", data={"pick": "1"}, environ_base=E)
        evs = g.in_calendar("cal-sessions")
        assert len(evs) == 3 and not g.in_calendar("cal-main")
        assert all(e["singleValueExtendedProperties"][0]["id"] == FakeGraph.PROP for e in evs)
        text = g.bodies().lower()
        for s_ in SECRETS:
            assert s_.lower() not in text, s_

        run(A, "UPDATE sessions SET start_time='13:00' WHERE id=1")
        _send_now(c)
        with A.app.app_context():
            A.db()
            import datakey
            raw = rows(A, "SELECT value FROM calsend_secret WHERE name='conn'")[0][0]
            kept = json.loads(datakey.open_bytes(bytes(raw), "calendar_send", label="calendar-send"))
        assert kept["refresh_token"] == g.issued[-1] and kept["refresh_token"] in g.refresh

        run(A, "DELETE FROM calsend_events")
        run(A, "UPDATE sessions SET start_time='14:00' WHERE id=1")
        _send_now(c)
        assert len(g.in_calendar("cal-sessions")) == 3
        assert "Outlook" in _page(c) and g.issued[-1] not in _page(c)
    finally:
        g.close()

def test_caldav_setup_finds_calendars_and_sends(env, tmp_path):
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
        assert "Pick the calendar" in page and "Studio sessions" in page and "Personal" in page and T.PW not in page
        cals = [w for w in ("Studio sessions", "Personal") if w in page]
        pick = page.index("Studio sessions") > page.index("Personal")
        c.post("/settings/calendar-send/pick", data={"pick": "1" if pick else "0"}, environ_base=E)
        files = sorted((tmp_path / "collections" / "collection-root" / T.USER / "sessions").glob("*.ics"))
        assert len(files) == 3 and cals
        assert not list((tmp_path / "collections" / "collection-root" / T.USER / "personal").glob("*.ics"))
        body = "".join(f.read_text() for f in files).lower()
        for s_ in SECRETS:
            assert s_.lower() not in body, s_
        assert T.PW not in json.dumps([[str(v) for v in r_] for r_ in rows(A, "SELECT * FROM audit")])
        page = _page(c)
        assert T.PW not in page and "\u2022" * 8 in page and "Studio sessions" in page

def test_the_setup_reads_on_a_phone_and_turns_on_in_one_tap(env):
    from test_client_page_141 import BASE, _tab
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    A, c = env
    try:
        for width in (390, 1280):
            ctx, p = _tab(browser, c, width=width)
            try:
                p.goto(BASE + "/settings?tab=calendar")
                choice = p.locator("#calendar-send")
                assert choice.is_visible() and "right away" in choice.inner_text().lower()
                assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
                if width == 390:
                    choice.get_by_role("link", name="Set up").click()
                    with p.expect_request(lambda r_: r_.method == "POST"):
                        p.get_by_role("button", name="Turn on").click()
                    p.wait_for_timeout(300)
                p.goto(BASE + "/settings/calendar-send?with=caldav")
                assert p.get_by_role("link", name="Google Calendar").is_visible()
                assert p.get_by_role("textbox", name="App password").is_visible()
                assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
            finally:
                ctx.close()
    finally:
        browser.close()
        pw.stop()
    assert rows(A, "SELECT value FROM settings WHERE key='plugin_on_calendar_send'")[0][0] == "1"

def test_with_a_built_in_microsoft_app_no_registration_is_asked_for(env, tmp_path, monkeypatch):
    A, c = env
    import calsend as S
    g = FakeGraph(tmp_path)
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GraphConnection, "LOGIN", g.url + "/common/oauth2/v2.0")
    monkeypatch.setattr(S.GraphConnection, "API", g.url)
    monkeypatch.setitem(A.app.view_functions["calendar_send_settings"].__globals__, "MS_CLIENT_ID", g.client_id)
    try:
        _on(c)
        page = _page(c, "/settings/calendar-send?with=outlook")
        assert "entra.microsoft.com" not in page and "Use your own app registration" in page
        c.post("/settings/calendar-send/outlook", data={"client_id": ""}, environ_base=E)
        assert "ABCD-EFGH" in _page(c)
    finally:
        g.close()
