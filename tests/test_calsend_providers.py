import http.server
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calsend as S
from fake_calendars import FakeGoogle, FakeGraph, rsa_key_json

SALT = "ab" * 32
SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)

def pub(day, hm="15:30", minutes=90, summary="Family session", tentative=False):
    if hm is None:
        d = date.fromisoformat(day)
        return dict(summary=summary, start=d, end=d + timedelta(days=1), all_day=True, tentative=tentative)
    st = datetime.fromisoformat(f"{day}T{hm}:00+00:00")
    return dict(summary=summary, start=st, end=st + timedelta(minutes=minutes), all_day=False, tentative=tentative)

@pytest.fixture
def google(tmp_path, monkeypatch):
    key, public = rsa_key_json()
    g = FakeGoogle(tmp_path, public, email=key["client_email"])
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GoogleConnection, "TOKEN_URL", g.url + "/token")
    monkeypatch.setattr(S.GoogleConnection, "API", g.url)
    conn = S.GoogleConnection(json.dumps(key))
    yield g, S.GoogleCalendar(conn, g.calendar_id), key
    g.close()

@pytest.fixture
def graph(tmp_path, monkeypatch):
    g = FakeGraph(tmp_path)
    monkeypatch.setattr(S, "CA_FILE", g.cafile)
    monkeypatch.setattr(S.GraphConnection, "LOGIN", g.url + "/common/oauth2/v2.0")
    monkeypatch.setattr(S.GraphConnection, "API", g.url)
    yield g
    g.close()

def test_ids_are_stable_valid_everywhere_and_carry_no_record_number():
    a = S.ident(SALT, ("s", 1))
    assert a == S.ident(SALT, ("s", 1)) and a != S.ident(SALT, ("s", 2)) and a != S.ident("cd" * 32, ("s", 1))
    assert S.IDENT_RE.fullmatch(a) and len(a) == 26
    assert set(a) <= set("0123456789abcdefghijklmnopqrstuv")
    assert "1" not in a[:2]

def test_google_token_is_an_rs256_jwt_for_events_only_sent_to_google_not_the_key_files_address(google):
    g, cal, key = google
    cal.check()
    head, claims = g.jwts[0]
    assert head == {"alg": "RS256", "typ": "JWT", "kid": "abc123keyid"}
    assert claims["iss"] == key["client_email"]
    assert claims["scope"] == "https://www.googleapis.com/auth/calendar.events"
    assert claims["aud"] == g.url + "/token" and claims["exp"] - claims["iat"] == 3600
    assert set(claims) == {"iss", "scope", "aud", "iat", "exp"}
    assert "evil.example.invalid" not in json.dumps(cal.conn.secret())

def test_google_create_move_delete_and_re_add_after_delete(google):
    g, cal, _ = google
    a, b = S.ident(SALT, ("s", 1)), S.ident(SALT, ("s", 2))
    st = {}
    n = S.sync(cal, {a: pub("2026-12-03"), b: pub("2026-12-10", tentative=True)}, st, SINCE)
    assert n == dict(added=2, changed=0, removed=0, total=2)
    ev = g.live()[a]
    assert ev["id"] == a and ev["summary"] == "Family session" and ev["status"] == "confirmed"
    assert ev["start"] == {"dateTime": "2026-12-03T15:30:00Z", "timeZone": "UTC"}
    assert ev["end"] == {"dateTime": "2026-12-03T17:00:00Z", "timeZone": "UTC"}
    assert ev["extendedProperties"] == {"private": {"lumenAureum": S.studio_mark(SALT)}} and ev["visibility"] == "private"
    assert set(ev) <= {"id", "summary", "start", "end", "status", "transparency", "visibility",
                       "extendedProperties", "etag"}
    assert g.live()[b]["status"] == "tentative"

    before = len(g.requests)
    assert S.sync(cal, {a: pub("2026-12-03"), b: pub("2026-12-10", tentative=True)}, st, SINCE)["changed"] == 0
    assert [r["method"] for r in g.requests[before:]] == ["GET"]

    S.sync(cal, {a: pub("2026-12-04", "09:00", 60), b: pub("2026-12-10", tentative=True)}, st, SINCE)
    assert g.live()[a]["start"]["dateTime"] == "2026-12-04T09:00:00Z" and len(g.live()) == 2

    S.sync(cal, {a: pub("2026-12-04", "09:00", 60)}, st, SINCE)
    assert set(g.live()) == {a} and b not in st

    S.sync(cal, {a: pub("2026-12-04", "09:00", 60), b: pub("2026-12-11", None)}, st, SINCE)
    assert g.live()[b]["start"] == {"date": "2026-12-11"} and g.live()[b]["end"] == {"date": "2026-12-12"}

def test_google_restore_and_lost_record_give_no_duplicates_and_an_edit_in_google_is_put_back(google):
    g, cal, _ = google
    a = S.ident(SALT, ("s", 1))
    want = {a: pub("2026-12-03")}
    S.sync(cal, want, {}, SINCE)

    n = S.sync(cal, want, {}, SINCE)
    assert len(g.live()) == 1 and n["added"] == 0

    st = {}
    S.sync(cal, want, st, SINCE)
    g.events[a]["summary"], g.events[a]["etag"] = "edited", '"x"'
    S.sync(cal, want, st, SINCE)
    assert g.live()[a]["summary"] == "Family session"

def test_google_leaves_other_events_alone(google):
    g, cal, _ = google
    g.events["mine0000"] = dict(id="mine0000", summary="Dentist", status="confirmed", etag='"1"')
    g.events["la" + "0" * 24] = dict(id="la" + "0" * 24, summary="no marker", status="confirmed", etag='"1"')
    S.sync(cal, {}, {}, SINCE, mark=S.studio_mark(SALT))
    assert set(g.live()) == {"mine0000", "la" + "0" * 24}

def test_google_bad_key_and_refused_calendar_say_so_plainly(google, tmp_path):
    g, cal, _ = google
    with pytest.raises(S.SendError) as e:
        S.GoogleConnection('{"type": "authorized_user"}')
    assert "service account key" in e.value.words
    other, _ = rsa_key_json(email="someone-else@sample-project.iam.gserviceaccount.com")
    with pytest.raises(S.SendError) as e:
        S.GoogleCalendar(S.GoogleConnection(json.dumps(other)), g.calendar_id).check()
    assert e.value.signin and "didn't accept" in e.value.words
    g.fail = 404
    with pytest.raises(S.SendError) as e:
        cal.check()
    assert "isn't shared with the service account" in e.value.words

def _graph_signed_in(g):
    started = S.graph_device_start(g.client_id)
    assert started["user_code"] == "ABCD-EFGH" and started["verification_uri"] == "https://microsoft.com/devicelogin"
    with pytest.raises(S.SendError) as e:
        S.graph_device_finish(g.client_id, started["device_code"])
    assert e.value.words.startswith("not signed in yet")
    got = S.graph_device_finish(g.client_id, started["device_code"])
    kept = []
    conn = S.GraphConnection(g.client_id, got["refresh_token"], on_refresh=kept.append)
    return conn, kept

def test_graph_device_code_sign_in_calendars_and_refresh_token_rotation(graph):
    g = graph
    with pytest.raises(S.SendError):
        S.graph_device_start("not-a-guid")
    conn, kept = _graph_signed_in(g)
    assert S.graph_calendars(conn) == [("cal-main", "Calendar"), ("cal-sessions", "Studio sessions")]
    assert kept and kept[-1]["refresh_token"] == conn.refresh_token == g.issued[-1]
    form = [r for r in g.requests if r["path"].endswith("/token")][-1]["body"].decode()
    assert "grant_type=refresh_token" in form and "scope=offline_access+Calendars.ReadWrite" in form

def test_graph_create_move_delete_and_a_lost_record_is_found_again(graph):
    g = graph
    conn, _ = _graph_signed_in(g)
    cal = S.GraphCalendar(conn, "cal-sessions")
    a, b = S.ident(SALT, ("s", 1)), S.ident(SALT, ("s", 2))
    st = {}
    S.sync(cal, {a: pub("2026-12-03"), b: pub("2026-12-11", None, tentative=True)}, st, SINCE)
    evs = {e["singleValueExtendedProperties"][0]["value"]: e for e in g.in_calendar()}
    assert set(evs) == {a, b}
    e = evs[a]
    assert e["subject"] == "Family session" and e["showAs"] == "busy" and e["sensitivity"] == "private"
    assert e["start"] == {"dateTime": "2026-12-03T15:30:00", "timeZone": "UTC"} and e["isAllDay"] is False
    assert set(e) <= {"subject", "start", "end", "isAllDay", "showAs", "sensitivity",
                      "singleValueExtendedProperties", "id", "calendar", "etag"}
    assert evs[b]["isAllDay"] is True and evs[b]["showAs"] == "tentative"
    assert evs[b]["start"]["dateTime"] == "2026-12-11T00:00:00"

    S.sync(cal, {a: pub("2026-12-05"), b: pub("2026-12-11", None, tentative=True)}, {}, SINCE)
    assert len(g.in_calendar()) == 2
    moved = [x for x in g.in_calendar() if x["singleValueExtendedProperties"][0]["value"] == a][0]
    assert moved["start"]["dateTime"] == "2026-12-05T15:30:00" and moved["id"] == e["id"]
    S.sync(cal, {}, {}, SINCE, mark=S.studio_mark(SALT))
    assert g.in_calendar() == []
    assert not [r for r in g.requests if "/cal-main/" in r["path"]]

class _Plain(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Plain.seen.append((self.server.server_address[1], self.headers.get("Authorization")))
        loc = getattr(self.server, "location", None)
        self.send_response(302 if loc else 200)
        if loc:
            self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PROPFIND = do_REPORT = do_GET

def _plain_server(location=None):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Plain)
    s.location = location
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s

def test_a_redirect_to_another_host_is_never_followed_with_the_password():
    other = _plain_server()
    first = _plain_server(location=f"http://localhost:{other.server_address[1]}/stolen")
    _Plain.seen.clear()
    try:
        conn = S.CalDAVConnection(f"http://127.0.0.1:{first.server_address[1]}/", "user", "secret-app-pw")
        st, _, _ = conn.authorized("GET", conn.url)
        assert st == 302
        assert [p for p, _ in _Plain.seen] == [first.server_address[1]]
        st, _, _ = S.fetch("GET", conn.url, lan_http=True)
        assert st == 302 and len(_Plain.seen) == 2
    finally:
        first.shutdown()
        other.shutdown()

def test_plain_http_only_on_this_network_and_only_when_allowed():
    with pytest.raises(S.SendError) as e:
        S.fetch("GET", "http://127.0.0.1:9/")
    assert "https" in e.value.words
    with pytest.raises(S.SendError):
        S.fetch("GET", "http://192.0.2.1/", lan_http=True)
    with pytest.raises(S.SendError):
        S.fetch("GET", "ftp://127.0.0.1/")

def test_every_connection_has_a_timeout(monkeypatch):
    seen = []

    class Probe:
        def __init__(self, *a, **k):
            seen.append(k.get("timeout"))

        def request(self, *a, **k):
            raise OSError("stop")

        def close(self):
            pass
    monkeypatch.setattr(S.http.client, "HTTPSConnection", Probe)
    with pytest.raises(S.SendError):
        S.fetch("GET", "https://127.0.0.1:9/")
    assert seen == [S.TIMEOUT] and 0 < S.TIMEOUT <= 30

def test_caldav_follows_only_its_own_domain_and_xml_with_entities_is_refused():
    conn = S.CalDAVConnection("https://caldav.icloud.com/", "u", "p")
    assert conn.trusted("https://p52-caldav.icloud.com/123/calendars/")
    assert not conn.trusted("https://caldav.icloud.com.evil.example/")
    assert not conn.trusted("http://p52-caldav.icloud.com/")
    assert not conn.trusted("https://example.com/")
    with pytest.raises(S.SendError):
        conn.authorized("GET", "https://example.com/")
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>'
    with pytest.raises(S.SendError):
        S.safe_xml(bomb)
    with pytest.raises(S.SendError):
        S.safe_xml(b'<?xml version="1.0"?><!doctype x SYSTEM "file:///etc/passwd"><x/>')
    assert S.safe_xml(b"<d:multistatus xmlns:d='DAV:'/>").tag == "{DAV:}multistatus"
