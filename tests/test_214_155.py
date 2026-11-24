import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER, TOPIC = "https://ntfy.example.net", "studio-k3j9x2q7"
CFG = dict(server=SERVER, topic=TOPIC, token="")
T0 = datetime(2026, 11, 21, 10, 0, 0)
KEYS = {"storage": "storage_notice", "closing": "closing_notice", "update": "updated_notice", "served": "served_notice"}

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "s.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    for k in KEYS.values():
        con.execute("INSERT INTO settings VALUES (?, '1')", (k,))
    con.commit()
    con.close()
    return lambda: sqlite3.connect(p, isolation_level=None)

@pytest.fixture
def sent(monkeypatch):
    import inboxnotice
    out = []
    monkeypatch.setattr(inboxnotice.locknotice, "_post", lambda url, body, headers: out.append((body.decode(), dict(headers))))
    return out

def _plain(body):

    assert "http" not in body and "@" not in body and "$" not in body and "/" not in body
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body)
    assert not re.search(r"\b\d{3,}\b", body)

def test_storage_is_told_when_low_then_daily_and_cleared_when_fine(db, sent):
    import inboxnotice as N
    assert N.status_tick(db, CFG, "storage", False, T0) is None and not sent
    assert N.status_tick(db, CFG, "storage", True, T0) == "sent"
    (body, headers), = sent
    assert headers["Title"] == "Studio storage" and "less than 10% free" in body
    assert not {"Click", "Actions", "Attach"} & set(headers)
    _plain(body)
    assert N.status_tick(db, CFG, "storage", True, T0 + timedelta(hours=5)) is None and len(sent) == 1
    assert N.status_tick(db, CFG, "storage", True, T0 + timedelta(hours=25)) == "sent"
    assert N.status_tick(db, CFG, "storage", False, T0 + timedelta(hours=26)) is None
    assert N.status_tick(db, CFG, "storage", True, T0 + timedelta(hours=27)) == "sent" and len(sent) == 3

def test_a_gallery_closing_tomorrow_is_told_once_a_day_in_the_daytime_never_how_many(db, sent):
    import inboxnotice as N
    night = datetime(2026, 11, 21, 0, 1)
    assert N.status_tick(db, CFG, "closing", 2, night) is None and not sent
    assert N.status_tick(db, CFG, "closing", 0, T0) is None and not sent
    assert N.status_tick(db, CFG, "closing", 2, T0) == "sent"
    assert sent[-1][0].startswith("One or more galleries close tomorrow.") and sent[-1][1]["Title"] == "Gallery closing"
    assert "2" not in sent[-1][0]
    _plain(sent[-1][0])
    assert N.status_tick(db, CFG, "closing", 3, T0 + timedelta(hours=4)) is None and len(sent) == 1
    assert N.status_tick(db, CFG, "closing", 1, datetime(2026, 11, 21, 22, 0)) is None
    assert N.status_tick(db, CFG, "closing", 1, datetime(2026, 11, 22, 8, 59)) is None
    assert N.status_tick(db, CFG, "closing", 1, datetime(2026, 11, 22, 9, 0)) == "sent"
    assert sent[-1][0].startswith("One or more galleries close tomorrow.")

def test_an_update_is_told_once_and_never_again(db, sent):
    import inboxnotice as N
    assert N.status_tick(db, CFG, "update", "", T0) is None
    assert N.status_tick(db, CFG, "update", "1.5.5|1.5.4|", T0) == "sent"
    body, headers = sent[-1]
    assert headers["Title"] == "Studio updated" and "1.5" not in body
    _plain(body)
    assert N.status_tick(db, CFG, "update", "1.5.5|1.5.4|", T0 + timedelta(days=3)) is None and len(sent) == 1
    assert N.status_tick(db, CFG, "update", "1.5.6|1.5.5|", T0 + timedelta(days=9)) == "sent"

def test_a_changed_gallery_page_is_told_without_a_count_and_again_only_when_another_is_found(db, sent):
    import inboxnotice as N
    assert N.status_tick(db, CFG, "served", [], T0) is None
    assert N.status_tick(db, CFG, "served", [7, 3], T0) == "sent"
    body, headers = sent[-1]
    assert headers["Title"] == "Gallery page changed" and body.startswith("One or more gallery pages on your web host")
    assert "7" not in body and "3" not in body and "2" not in body
    assert N.status_tick(db, CFG, "served", [3], T0 + timedelta(hours=1)) is None
    assert N.status_tick(db, CFG, "served", [3, 7, 9], T0 + timedelta(hours=2)) == "sent"
    assert sent[-1][0].startswith("One or more gallery pages") and len(sent) == 2

def test_a_failed_send_is_tried_again_after_the_retry_time(db, monkeypatch):
    import inboxnotice as N
    calls = []
    monkeypatch.setattr(N.locknotice, "send", lambda cfg, text, title="": calls.append(title) or "refused")
    assert N.status_tick(db, CFG, "storage", True, T0) == "failed"
    assert N.status_tick(db, CFG, "storage", True, T0 + timedelta(minutes=5)) is None
    assert N.status_tick(db, CFG, "storage", True, T0 + timedelta(minutes=10)) == "failed" and len(calls) == 2

def test_every_title_is_its_own_and_listed(db):
    import inboxnotice as N
    assert N.TITLES == ("Studio backup", "Studio storage", "Gallery closing", "Studio updated", "Gallery page changed",
                        "Studio sign-in changed", "Studio master key")
    assert len(set(N.TITLES) | {N.TITLE}) == 8

def test_off_kinds_are_not_asked_locked_asks_nothing_and_a_failing_check_stops_nothing(db, sent, monkeypatch):
    import inboxnotice as N
    monkeypatch.setattr(N, "_now", lambda: T0)
    con = db()
    con.execute("UPDATE settings SET value='0' WHERE key='closing_notice'")
    con.execute("INSERT INTO settings VALUES ('inbox_notice', '1')")
    con.close()
    asked = []

    def boom():
        raise RuntimeError("disk gone")
    problems = dict(storage=boom, closing=lambda: asked.append("closing") or 4,
                    update=lambda: asked.append("update") or "1.5.5||", served=lambda: asked.append("served") or [1])
    N.step(lambda: False, db, lambda: CFG, T0, problems=problems)
    assert not sent and not asked
    con = db()
    N.arrived(con, "inquiry")
    con.close()
    N.step(lambda: True, db, lambda: CFG, T0 + timedelta(minutes=3), problems=problems)
    assert "closing" not in asked
    assert sorted(h["Title"] for _, h in sent) == ["Gallery page changed", "Studio inbox", "Studio updated"]

def test_with_no_notice_server_nothing_is_sent(db, sent):
    import inboxnotice as N
    assert N.status_tick(db, None, "storage", True, T0) is None and not sent

def _env(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch, saved=False, on=False)
    con = e.A.connect()
    for k, v in (("ntfy_server", SERVER), ("ntfy_topic", TOPIC), ("ntfy_token", "")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit()
    con.close()
    return e, PE

def test_four_boxes_off_by_default_each_turns_on_and_off_on_its_own(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    page = lambda: e.c.get("/settings?tab=notifications", environ_base=PE.E).get_data(as_text=True)
    labels = {"storage": "Storage running low", "closing": "A gallery closes tomorrow",
              "update": "An update was installed", "served": "A gallery page on your web host changed"}
    html = page()
    for kind, label in labels.items():
        assert f'name="{kind}_on" value="1" data-autosubmit>{label}' in html
    e.c.post("/security/notice", data=dict(act="storage", storage_on="1"), environ_base=PE.E)
    html = page()
    assert 'name="storage_on" value="1" data-autosubmit checked>Storage running low' in html
    assert 'name="closing_on" value="1" data-autosubmit>A gallery closes tomorrow' in html
    e.c.post("/security/notice", data=dict(act="storage"), environ_base=PE.E)
    assert 'name="storage_on" value="1" data-autosubmit>Storage running low' in page()
    acts = [r["action"] for r in PE.q(e, "SELECT action FROM audit WHERE action LIKE '%storage notice%'")]
    assert acts == ["Turned on the storage notice", "Turned off the storage notice"]

def test_an_update_already_on_today_is_not_news_to_whoever_ticks_the_box(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    import inboxnotice as N
    con = e.A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
                (json.dumps(dict(version="9.9.9", was="9.9.8", backup=True, backup_at="")),))
    con.commit()
    con.close()
    e.c.post("/security/notice", data=dict(act="update", update_on="1"), environ_base=PE.E)
    with e.A.app.app_context():
        fact = e.A.update_installed()
    assert fact == "9.9.9|9.9.8|"
    out = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: out.append(body))
    assert N.status_tick(e.A.connect, CFG, "update", fact) is None and not out

def test_the_app_finds_each_fact_and_no_body_names_anyone(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    import inboxnotice as N
    import shutil

    con = e.A.connect()
    con.execute("UPDATE galleries SET close_on=? WHERE session_id=1", ((date.today() + timedelta(days=1)).isoformat(),))
    for k in KEYS.values():
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (k,))
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
                (json.dumps(dict(version="9.9.9", was="9.9.8", backup=True, backup_at="")),))
    con.commit()
    con.close()
    import refit
    with e.A.app.app_context():
        refit.save_state(e.A.x, {"bad": {PE.GID: dict(sid=1, what=["the page as served"], n=2, since="2026-11-20")}},
                         "served_check")
        assert e.A.galleries_closing_tomorrow() == 1
        assert e.A.served_page_changed() == [1]
        assert e.A.storage_problem() is False or shutil.disk_usage(tmp_path).free * 10 < shutil.disk_usage(tmp_path).total
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 << 30, 99 << 30, 1 << 30))
    with e.A.app.app_context():
        assert e.A.storage_problem() is True
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(1000 << 30, 880 << 30, 120 << 30))
    with e.A.app.app_context():
        assert e.A.storage_problem() is False
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10000 << 30, 9100 << 30, 900 << 30))
    with e.A.app.app_context():
        assert e.A.storage_problem() is True
    out = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: out.append((body.decode(), headers["Title"])))
    noon = datetime.combine(date.today(), datetime.min.time()).replace(hour=12)
    problems = {k: (lambda fn=fn: e.A._with_app(fn)) for k, fn in e.A.STATUS_PROBLEMS.items()}
    N.step(lambda: True, e.A.connect, lambda: CFG, noon, problems=problems)
    assert sorted(t for _, t in out) == ["Gallery closing", "Gallery page changed", "Studio storage", "Studio updated"]
    names = [r["name"] for r in PE.q(e, "SELECT name FROM households")] + [r["name"] for r in PE.q(e, "SELECT name FROM people")]
    names += [r["email"] for r in PE.q(e, "SELECT email FROM people")] + [PE.GID, "Lane-034", "9.9.9", "Sample"]
    for body, _ in out:
        _plain(body)
        for n in names:
            assert n.lower() not in body.lower()

def test_a_broken_check_in_the_app_never_breaks_the_loop(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    import inboxnotice as N
    con = e.A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('closing_notice', '1')")
    con.commit()
    con.close()
    monkeypatch.setattr(e.A, "gallery_expires", lambda s: (_ for _ in ()).throw(ValueError("bad row")))
    problems = {"closing": lambda: e.A._with_app(e.A.galleries_closing_tomorrow)}
    N.step(lambda: True, e.A.connect, lambda: CFG, T0, problems=problems)
