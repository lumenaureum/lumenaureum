import hashlib
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calfeed
import calsend

GOLDEN_161 = "18f02ea4ea06ab2472ca7e40d0863cded811d6ffdc188ea2ce2b8cdb774efb85"

def _entries():
    tz = ZoneInfo("America/Chicago")
    return [calfeed.entry(("s", 1), "Family", "2026-12-03", "15:30", 90, tz),
            calfeed.entry(("s", 2), "Family (penciled)", "2026-12-10", "10:00", 0, tz, tentative=True),
            calfeed.entry(("e", 1, "2026-12-11"), "Wedding, commas; and \\ semis\nnew", "2026-12-11", "", 60, tz),
            calfeed.entry(("s", 3), "Ünïcödé " * 12, "2027-03-14", "01:30", 45, tz)]

def test_the_calendar_link_file_is_byte_identical_to_161():
    out = calfeed.build(_entries(), "t" * 43, now=datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc),
                        studio="Sample  Studio")
    assert hashlib.sha256(out.encode()).hexdigest() == GOLDEN_161

def test_public_is_the_only_projection_and_ignores_anything_else_an_entry_holds():
    e = dict(_entries()[0], client="Morgan", place="Longwood Gardens", notes="allergic", amount=1234.5,
             email="alex@example.com")
    p = calfeed.public(e)
    assert set(p) == {"summary", "start", "end", "all_day", "tentative"}
    assert "Morgan" not in repr(p) and "Longwood" not in repr(p) and "1234" not in repr(p)
    assert calfeed.public(_entries()[2])["all_day"] is True and isinstance(calfeed.public(_entries()[2])["start"], date)

    g = calsend.GoogleCalendar.__new__(calsend.GoogleCalendar).body("la" + "0" * 24, p)
    m = calsend.GraphCalendar.__new__(calsend.GraphCalendar).body("la" + "0" * 24, p)
    ics = calfeed.one_event(dict(p, key="k"), "la" + "0" * 24 + "@lumen-aureum")
    for blob in (repr(g), repr(m), ics):
        for w in ("Morgan", "Longwood", "allergic", "1234", "alex@"):
            assert w not in blob

def test_one_event_is_the_link_entry_in_a_file_of_its_own():
    e = _entries()[1]
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    one = calfeed.one_event(e, "x@lumen-aureum", now)
    lines = calfeed.vevent(e, "x@lumen-aureum", now)
    assert "".join(calfeed.fold(ln) + "\r\n" for ln in lines) in one
    assert one.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and one.endswith("END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert "STATUS:TENTATIVE" in one and "X-WR-CALNAME" not in one
