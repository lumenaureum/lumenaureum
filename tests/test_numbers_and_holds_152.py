import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_stage_logic_150 import E, _price, _session, _sql, env

def _found(c, term, what):

    r = c.get(f"/search?q={term}", environ_base=E)
    if r.status_code == 302:
        return r.location.endswith("/c/1?s=1")
    return r.status_code == 200 and what in r.get_data(as_text=True)

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def test_a_new_quote_is_numbered_by_its_day_and_shows_that_number_everywhere(env):
    A, c = env
    day = A.today().strftime("%Y%m%d")
    _session(A, "inquiry", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid, no = _sql(A, "SELECT id, number FROM quote_versions WHERE session_id=1")[0]
    assert no == day + "01"
    assert f"Quote {no}," in _page(c, f"/quote/{vid}")
    page = _page(c, "/c/1?s=1")
    assert f"Quote {no}, " in page and f"Quote {vid}, " not in page
    with A.app.test_request_context():
        A.db()
        past = A.journey(A.q("SELECT * FROM sessions WHERE id=1", one=True), [])["past"]
    assert any(f"Quote {no} sent" in e["tip"] for e in past)
    assert _sql(A, "SELECT note FROM audit WHERE route='/session/1/quote/send'")[0][0].startswith(f"quote {no}, ")
    assert _found(c, no, f"Quote {no}")
    assert not _found(c, day + "99", f"Quote {day}99")
    c.post(f"/quote/{vid}/sent", environ_base=E)
    assert _sql(A, "SELECT note FROM audit WHERE route=?", (f"/quote/{vid}/sent",))[0][0] == f"quote {no}"

    _session(A, "inquiry", days=40, sid=2)
    _price(A, c, sid=2)
    c.post("/session/2/quote/send", environ_base=E)
    assert _sql(A, "SELECT number FROM quote_versions WHERE session_id=2")[0][0] == day + "02"
    _sql(A, "DELETE FROM sessions WHERE id=2")
    _session(A, "inquiry", days=50, sid=3)
    _price(A, c, sid=3)
    c.post("/session/3/quote/send", environ_base=E)
    assert _sql(A, "SELECT number FROM quote_versions WHERE session_id=3")[0][0] == day + "03"

def test_a_quote_numbered_before_1_5_2_keeps_its_number(env):
    A, c = env
    _session(A, "inquiry", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    _sql(A, "UPDATE quote_versions SET number=NULL WHERE id=?", (vid,))
    assert f"Quote {vid}, " in _page(c, "/c/1?s=1")
    assert f"Quote {vid}," in _page(c, f"/quote/{vid}")
    assert _found(c, vid, f"Quote {vid}")

def test_price_list_codes_are_by_day_and_old_codes_stay(env):
    A, c = env
    with A.app.test_request_context():
        A.db()
        first = A.current_schedule()["id"]
        A._put_setting(A.PRICE_APPROVALS, json.dumps({str(first): {"on": "2026-11-18", "code": "20261118-1"}}))
        A.db().commit()
    _sql(A, "INSERT INTO fee_schedules (created, items, note) SELECT created, items, '' FROM fee_schedules WHERE id=?", (first,))
    with A.app.test_request_context():
        A.db()
        new_ = A.current_schedule()["id"]
        assert A.approve_prices(new_, on="2026-11-18", quiet=True)
        A.db().commit()
        assert A.sched_code(first) == "20261118-1"
        assert A.sched_code(new_) == "2026111802"
    page = _page(c, f"/fees/{new_}")
    assert "2026111802" in page

def test_sessions_the_update_moved_on_are_listed_once_with_the_usual_date(env):
    A, c = env
    shipped = (A.today() - timedelta(days=10)).isoformat()
    for sid_, stage_ in ((1, "closed"), (2, "delivered"), (3, "closed")):
        _session(A, stage_, days=-20, sid=sid_, initial=False)
        _sql(A, "UPDATE sessions SET sd_shipped=? WHERE id=?", (shipped, sid_))
        _sql(A, "INSERT INTO stage_log (session_id, stage, at, era) VALUES (?, 'shot', ?, '1.4')", (sid_, shipped))
        _sql(A, "INSERT INTO stage_log (session_id, stage, at, era) VALUES (?, ?, ?, 'upgrade')",
             (sid_, stage_, A.today().isoformat() + " 08:00:00"))
    mark = _sql(A, "SELECT MAX(id) FROM stage_log")[0][0]
    _sql(A, "INSERT INTO settings (key, value) VALUES ('stage_rules_mark', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
         (str(mark),))
    kept = (A.today() + timedelta(days=300)).isoformat()
    _sql(A, "INSERT INTO retention (session_id, keep_until) VALUES (3, ?)", (kept,))
    with A.app.app_context():
        due = A.app.config["retention_due"]
        assert due(1) is None and due(2) is None and due(3) == A.d(kept)
        usual = A.d(shipped) + timedelta(days=A.C.raw_keep_days("family"))
    today_ = _page(c, "/")
    sect = today_.split('id="raw-held"', 1)[1].split("</section>", 1)[0]
    assert "2 sessions the update moved on have no deletion date for the RAWs and outtakes." in sect
    assert 'id="raw-held-1"' in sect and 'id="raw-held-2"' in sect and 'id="raw-held-3"' not in sect
    assert f"Use the usual date ({A.nice(usual.isoformat())})" in sect and 'href="/c/1?s=1#raw-date"' in sect
    r = c.post("/session/1/raw/date", data={"until": usual.isoformat(), "back": "/"}, environ_base=E)
    assert r.status_code in (302, 303)
    with A.app.app_context():
        assert A.app.config["retention_due"](1) == usual
    sect = _page(c, "/").split('id="raw-held"', 1)[1].split("</section>", 1)[0]
    assert "1 session the update moved on has no deletion date" in sect and 'id="raw-held-1"' not in sect
    c.post("/today/raw-held/hide", data={"back": "/"}, environ_base=E)
    assert 'id="raw-held"' not in _page(c, "/")
    assert "RAWs and outtakes: no deletion date set" in _page(c, "/c/1?s=2")
