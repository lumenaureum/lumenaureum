import base64
import contextlib
import http.client
import importlib.util
import os
import re
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calsend as S

EXTRA = os.environ.get("LA_RADICALE_PATH", "")
SALT = "ef" * 32
SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)
USER, PW = "studio", "app-pw-sample-123"

def _have_radicale():
    if importlib.util.find_spec("radicale"):
        return True
    return bool(EXTRA) and os.path.isdir(os.path.join(EXTRA, "radicale"))

pytestmark = pytest.mark.skipif(not _have_radicale(), reason="Radicale is not installed for the tests")

def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

def _raw(port, method, path, body=b"", headers=None, user=USER, pw=PW):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data

MKCAL = """<?xml version="1.0" encoding="utf-8"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:set><d:prop><d:displayname>{name}</d:displayname><c:supported-calendar-component-set><c:comp name="VEVENT"/>
</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>"""

@contextlib.contextmanager
def start_radicale(tmp_path):

    port = _port()
    (tmp_path / "users").write_text(f"{USER}:{PW}\n")
    (tmp_path / "config").write_text(f"""[server]
hosts = 127.0.0.1:{port}
[auth]
type = htpasswd
htpasswd_filename = {tmp_path / 'users'}
htpasswd_encryption = plain
[storage]
filesystem_folder = {tmp_path / 'collections'}
[rights]
type = owner_only
[logging]
level = warning
""")
    env = dict(os.environ)
    if EXTRA:
        env["PYTHONPATH"] = EXTRA + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen([sys.executable, "-m", "radicale", "--config", str(tmp_path / "config")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                pytest.fail("radicale did not start: " + proc.stderr.read().decode()[-800:])
            time.sleep(0.1)
    for slug, name in (("sessions", "Studio sessions"), ("personal", "Personal")):
        st, body = _raw(port, "MKCALENDAR", f"/{USER}/{slug}/", MKCAL.format(name=name).encode(),
                        {"Content-Type": "application/xml"})
        assert st == 201, (st, body)
    try:
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)

@pytest.fixture
def radicale(tmp_path):
    with start_radicale(tmp_path) as port:
        yield port

def pub(day, hm="15:30", minutes=90, summary="Portrait session", tentative=False):
    if hm is None:
        d = date.fromisoformat(day)
        return dict(summary=summary, start=d, end=d + timedelta(days=1), all_day=True, tentative=tentative)
    st = datetime.fromisoformat(f"{day}T{hm}:00+00:00")
    return dict(summary=summary, start=st, end=st + timedelta(minutes=minutes), all_day=False, tentative=tentative)

def _files(tmp_path):
    d = tmp_path / "collections" / "collection-root" / USER / "sessions"
    return sorted(p.name for p in d.iterdir() if p.suffix == ".ics")

def test_finds_the_accounts_calendars_from_the_servers_address(radicale):
    conn = S.CalDAVConnection(f"http://127.0.0.1:{radicale}/", USER, PW)
    cals = dict((n, u) for u, n in S.caldav_calendars(conn))
    assert set(cals) == {"Studio sessions", "Personal"}
    assert cals["Studio sessions"] == f"http://127.0.0.1:{radicale}/{USER}/sessions/"
    S.CalDAVCalendar(conn, cals["Studio sessions"]).check()

def test_a_wrong_app_password_is_said_plainly(radicale):
    conn = S.CalDAVConnection(f"http://127.0.0.1:{radicale}/", USER, "wrong")
    with pytest.raises(S.SendError) as e:
        S.caldav_calendars(conn)
    assert e.value.signin and "app password" in e.value.words

def test_create_move_delete_restore_and_other_events_left_alone(radicale, tmp_path):
    conn = S.CalDAVConnection(f"http://127.0.0.1:{radicale}/", USER, PW)
    cal = S.CalDAVCalendar(conn, f"http://127.0.0.1:{radicale}/{USER}/sessions/")
    foreign = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nBEGIN:VEVENT\r\nUID:dentist-1\r\n"
               "DTSTAMP:20261101T000000Z\r\nDTSTART:20261205T150000Z\r\nDTEND:20261205T160000Z\r\nSUMMARY:Dentist\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert _raw(radicale, "PUT", f"/{USER}/sessions/dentist.ics", foreign.encode(),
                {"Content-Type": "text/calendar"})[0] == 201
    a, b = S.ident(SALT, ("s", 1)), S.ident(SALT, ("e", 4, "2026-12-11"))
    st = {}
    n = S.sync(cal, {a: pub("2026-12-03"), b: pub("2026-12-11", None, tentative=True)}, st, SINCE)
    assert n["added"] == 2 and _files(tmp_path) == sorted(["dentist.ics", a + ".ics", b + ".ics"])
    status, body = _raw(radicale, "GET", f"/{USER}/sessions/{a}.ics")
    text = body.decode()
    assert status == 200
    assert f"UID:{a}@lumen-aureum" in text and "SUMMARY:Portrait session" in text
    assert "DTSTART:20261203T153000Z" in text and "DTEND:20261203T170000Z" in text and "STATUS:CONFIRMED" in text
    props = {ln.split(":")[0].split(";")[0] for ln in re.findall(r"^[A-Z-]+[;:][^\r\n]*", text, re.M)}
    assert props <= {"BEGIN", "END", "VERSION", "PRODID", "CALSCALE", "UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY",
                     "STATUS", "TRANSP", "CLASS"}, props
    tb = _raw(radicale, "GET", f"/{USER}/sessions/{b}.ics")[1].decode()
    assert "DTSTART;VALUE=DATE:20261211" in tb and "STATUS:TENTATIVE" in tb

    puts = []
    real_put = cal.put
    cal.put = lambda *a_, **k_: puts.append(a_) or real_put(*a_, **k_)
    assert S.sync(cal, {a: pub("2026-12-03"), b: pub("2026-12-11", None, tentative=True)}, st, SINCE)["changed"] == 0
    assert puts == []

    S.sync(cal, {a: pub("2026-12-04", "10:00", 60), b: pub("2026-12-11", None, tentative=True)}, st, SINCE)
    assert "DTSTART:20261204T100000Z" in _raw(radicale, "GET", f"/{USER}/sessions/{a}.ics")[1].decode()

    S.sync(cal, {a: pub("2026-12-04", "10:00", 60), b: pub("2026-12-11", None, tentative=True)}, {}, SINCE)
    assert _files(tmp_path) == sorted(["dentist.ics", a + ".ics", b + ".ics"])

    S.sync(cal, {b: pub("2026-12-11", None, tentative=True)}, st, SINCE)
    assert _files(tmp_path) == sorted(["dentist.ics", b + ".ics"])
    assert S.remove_all(cal, st, SINCE) == 1 and _files(tmp_path) == ["dentist.ics"]
