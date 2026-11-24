import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

NEW = "Something new came in. Sign in to your studio to see it."

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER, TOPIC = "https://ntfy.example.net", "studio-k3j9x2q7"
CFG = dict(server=SERVER, topic=TOPIC, token="")
T0 = datetime(2026, 11, 21, 9, 0, 0)

@pytest.fixture
def sent(monkeypatch):

    import inboxnotice
    locknotice = inboxnotice.locknotice
    out = []
    monkeypatch.setattr(locknotice, "_post", lambda url, body, headers: out.append((url, body.decode(), dict(headers))))
    return out

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "s.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('inbox_notice', '1')")
    con.commit()
    con.close()

    def connect():
        return sqlite3.connect(p, isolation_level=None)
    return connect

def _arrive(connect, kind, n=1, at=T0, monkeypatch=None):
    import inboxnotice
    con = connect()
    if monkeypatch:
        monkeypatch.setattr(inboxnotice, "_now", lambda: at)
    inboxnotice.arrived(con, kind, n)
    con.close()

def test_the_words_say_something_new_never_how_many_or_what():
    import inboxnotice as N
    for p in ({"print": 1}, {"inquiry": 3}, {"picks": 1}, {"inquiry": 2, "picks": 1}, {"inquiry": 1, "picks": 2, "print": 1}):
        assert N.text(p) == NEW
    assert N.text({}) == "" and N.text({"inquiry": 0}) == ""

def test_arrivals_are_gathered_into_one_notice_then_a_quiet_period(db, sent, monkeypatch):
    import inboxnotice as N
    _arrive(db, "inquiry", at=T0, monkeypatch=monkeypatch)
    _arrive(db, "picks", at=T0 + timedelta(seconds=30), monkeypatch=monkeypatch)
    _arrive(db, "inquiry", at=T0 + timedelta(seconds=50), monkeypatch=monkeypatch)
    assert N.tick(db, CFG, T0 + timedelta(minutes=1)) is None and not sent
    assert N.tick(db, CFG, T0 + timedelta(minutes=2)) == "sent"
    (url, body, headers), = sent
    assert url == f"{SERVER}/{TOPIC}" and body == NEW
    assert headers["Title"] == "Studio inbox"
    assert not {"Click", "Actions", "Attach"} & set(headers)

    _arrive(db, "print", at=T0 + timedelta(minutes=5), monkeypatch=monkeypatch)
    _arrive(db, "print", at=T0 + timedelta(minutes=20), monkeypatch=monkeypatch)
    assert N.tick(db, CFG, T0 + timedelta(minutes=25)) is None and len(sent) == 1
    assert N.tick(db, CFG, T0 + timedelta(minutes=32)) == "sent"
    assert sent[-1][1] == NEW
    assert N.tick(db, CFG, T0 + timedelta(hours=3)) is None and len(sent) == 2

def test_a_failed_send_keeps_the_counts_and_tries_again_later(db, monkeypatch):
    import inboxnotice as N
    locknotice = N.locknotice
    calls = []

    def refuse(url, body, headers):
        calls.append(body)
        raise OSError("the server answered 503")
    monkeypatch.setattr(locknotice, "_post", refuse)
    _arrive(db, "inquiry", at=T0, monkeypatch=monkeypatch)
    assert N.tick(db, CFG, T0 + timedelta(minutes=3)) == "failed"
    assert N.tick(db, CFG, T0 + timedelta(minutes=8)) is None and len(calls) == 1
    out = []
    monkeypatch.setattr(locknotice, "_post", lambda url, body, headers: out.append(body.decode()))
    assert N.tick(db, CFG, T0 + timedelta(minutes=14)) == "sent" and out == [NEW]

def test_what_comes_in_while_a_notice_is_sending_waits_for_the_next(db, monkeypatch):
    import inboxnotice as N
    locknotice = N.locknotice
    _arrive(db, "inquiry", at=T0, monkeypatch=monkeypatch)
    out = []

    def post_and_arrive(url, body, headers):
        out.append(body.decode())
        _arrive(db, "print")
    monkeypatch.setattr(locknotice, "_post", post_and_arrive)
    assert N.tick(db, CFG, T0 + timedelta(minutes=3)) == "sent" and out == [NEW]
    con = db()
    assert N.load(con)["pending"] == {"print": 1}
    con.close()
    assert N.tick(db, CFG, T0 + timedelta(minutes=34)) == "sent" and out[-1] == NEW

def test_off_nothing_is_counted_or_sent(db, sent):
    import inboxnotice as N
    con = db()
    con.execute("UPDATE settings SET value='0' WHERE key='inbox_notice'")
    N.arrived(con, "inquiry")
    assert N.load(con) == {}
    con.close()
    assert N.tick(db, CFG, T0 + timedelta(hours=1)) is None and not sent

def test_nothing_while_the_studio_is_locked(db, sent, monkeypatch):
    import inboxnotice as N
    _arrive(db, "inquiry", at=T0, monkeypatch=monkeypatch)
    asked = []
    assert N.step(lambda: False, db, lambda: asked.append(1) or CFG, T0 + timedelta(hours=1)) is None
    assert not sent and not asked
    assert N.step(lambda: True, db, lambda: CFG, T0 + timedelta(hours=1)) == "sent"

def test_a_count_that_cant_be_written_never_breaks_taking_an_item_in(monkeypatch):
    import inboxnotice as N

    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")
    N.arrived(Broken(), "print")

def _capture(monkeypatch):

    import inboxnotice
    out = []
    monkeypatch.setattr(inboxnotice.locknotice, "_post", lambda url, body, headers: out.append((url, body.decode(), dict(headers))))
    return out

def _on(c, E):
    return c.post("/security/notice", data=dict(act="inbox", inbox_on="1"), environ_base=E)

def _ntfy(A):
    con = A.connect()
    for k, v in (("ntfy_server", SERVER), ("ntfy_topic", TOPIC), ("ntfy_token", "")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit()
    con.close()

def _state(A):
    import inboxnotice as N
    con = A.connect()
    try:
        return N.load(con), N.is_on(con)
    finally:
        con.close()

def _tick_later(A, minutes=5):
    import inboxnotice as N
    with A.app.app_context():
        cfg = A.ntfy_settings()
    return N.tick(A.connect, cfg, datetime.now() + timedelta(minutes=minutes))

def test_off_by_default_beside_the_locked_notice_and_on_only_with_ntfy_set_up(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    page = lambda: e.c.get("/settings?tab=notifications", environ_base=PE.E).get_data(as_text=True)
    _on(e.c, PE.E)
    assert _state(e.A) == ({}, False)
    _ntfy(e.A)
    p = page()
    assert "Something new in the studio inbox" in p and 'name="inbox_on" value="1" data-autosubmit>' in p
    _on(e.c, PE.E)
    assert _state(e.A)[1] is True
    assert 'data-autosubmit checked>Something new in the studio inbox' in page()
    e.c.post("/security/notice", data=dict(act="inbox"), environ_base=PE.E)
    assert _state(e.A)[1] is False
    acts = [r["action"] for r in PE.q(e, "SELECT action FROM audit WHERE action LIKE '%studio inbox%' ORDER BY id")]
    assert acts == ["Turned on the studio inbox notice", "Turned off the studio inbox notice"]

def test_a_print_order_taken_in_is_counted_and_told_without_who_or_what(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    sent = _capture(monkeypatch)
    _ntfy(e.A)
    _on(e.c, PE.E)
    PE.send(e, PE.payload(e, "a" * 32))
    PE.collect(e)
    assert _state(e.A)[0]["pending"] == {"print": 1}
    PE.collect(e)
    assert _state(e.A)[0]["pending"] == {"print": 1}
    assert _tick_later(e.A) == "sent"
    (url, body, headers), = [s for s in sent if s[2].get("Title") == "Studio inbox"]
    assert body == NEW
    told = body + json.dumps(headers)
    for secret in ("Avery", "avery@example.com", "a" * 32, PE.GID, "Sample", "$", "http://", "https://", "2026"):
        assert secret not in told, secret

def test_an_inquiry_is_counted_and_spam_is_not(tmp_path, monkeypatch):
    from test_studio_inquiries import GOOD, put_inquiry
    from test_studio_page import E, env as _env_fixture, switch_on, wait, setting
    A, c, S, tmp, web = _env_fixture.__wrapped__(tmp_path, monkeypatch)
    sent = _capture(monkeypatch)
    _ntfy(A)
    _on(c, E)
    switch_on(c)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, hp="buy now")))
    assert A.app.config["studio_page_collect"]() == dict(added=1, dropped=1, bad=0)
    assert _state(A)[0]["pending"] == {"inquiry": 1}
    assert _tick_later(A) == "sent"
    body = [s[1] for s in sent if s[2].get("Title") == "Studio inbox"]
    assert body == [NEW]
    assert "Ada" not in body[0] and "example.org" not in body[0]
