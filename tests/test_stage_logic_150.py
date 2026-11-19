import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
FETCH = {"X-Fetch": "1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    for k, v in (("CRM_PHOTOS_DIR", "photos"), ("CRM_DATA_DIR", "data")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'Morgan')")
        con.execute("""INSERT INTO people (household_id, name, role, email, phone) VALUES
                       (1, 'Alex Morgan', 'parent', 'alex@example.com', '555-555-0142')""")
        con.commit()
    finally:
        con.close()
    return A, c

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        cur = con.execute(sql, args)
        rows = cur.fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def _day(A, n):
    return (A.today() + timedelta(days=n)).isoformat()

def _session(A, stage, days=10, start=None, sid=1, logged=None, initial=True):

    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops, created, stage_changed) "
            "VALUES (?, 1, 'family', ?, ?, ?, 'Longwood Gardens', ?, ?)",
         (sid, stage, _day(A, days) if days is not None else None, start, _day(A, -30) + " 09:00:00", _day(A, -1) + " 09:00:00"))
    for st, n in ([(stage, -30)] if initial else []) + list(logged or []):
        _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?,?,?)", (sid, st, _day(A, n)))
    _in(A, lambda: A.sync_workflow_tasks(sid))

def _in(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def _stage(A, sid=1):
    return _sql(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0]

def _history(A, sid=1):
    return [r[0] for r in _sql(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

def _task(A, title, sid=1):
    r = _sql(A, "SELECT id, done, outcome, due FROM tasks WHERE session_id=? AND title=?", (sid, title))
    return r[0] if r else None

def _price(A, c, amount="400", sid=1):

    if _stage(A, sid) in ("inquiry", "quoted"):
        c.post(f"/session/{sid}/money", data={"what": "line", "label": "Family session", "amount": amount}, environ_base=E)
    else:
        _sql(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Family session', ?, 'custom')", (sid, float(amount)))

def _invoice_sent(A, c, sid=1):

    c.post(f"/session/{sid}/invoice/deposit-sent", data={"back": "/"}, environ_base=E)

def _pay(A, c, amount, kind="balance", sid=1):
    return c.post(f"/session/{sid}/money", data={"what": "payment", "kind": kind, "amount": amount, "method": "card",
                                                 "received": A.today().isoformat()}, environ_base=E)

def _acts(A, sid=1):
    return _in(A, lambda: A.build_actions(only_sid=sid))

def test_quote_marked_sent_moves_inquiry_to_quoted(env):
    A, c = env
    _session(A, "inquiry", days=None)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    assert _stage(A) == "inquiry"
    r = c.post(f"/quote/{vid}/sent", data={"back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    assert _stage(A) == "quoted"
    h = _history(A)
    assert h[-2] == "Marked the quote sent" and h[-1].startswith("Moved to Quoted: the quote was marked sent.")
    assert "Intake answered" in h[-1]
    acts = _acts(A)

    intake = [a for a in acts if a["what"].startswith("Send the intake questions")]

    assert intake and intake[0]["note"] == "From Inquiry" and not intake[0]["theirs"] and not intake[0]["when"]
    c.post("/session/1/intake/send", data={"back": "/c/1?s=1"}, environ_base=E)
    intake = [a for a in _acts(A) if a["what"].startswith("Intake answers")]
    assert intake and intake[0]["note"] == "From Inquiry" and intake[0]["theirs"] and not intake[0]["when"]

def test_quote_marked_sent_through_the_checkbox(env):
    A, c = env
    _session(A, "inquiry", days=None)
    _sql(A, "INSERT INTO tasks (id, session_id, stage, title, own) VALUES (900, 1, 'quoted', 'Send quote', 0)")
    t = _task(A, "Send quote")
    r = c.post(f"/task/{t[0]}", data={"set": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and _stage(A) == "quoted"
    assert any(h.startswith("Moved to Quoted: the quote was marked sent.") for h in _history(A))

def test_book_it_ticks_yes_and_moves_to_booked_locks_deposit(env):
    A, c = env
    _session(A, "quoted", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    r = c.post("/session/1/quick", data={"act": "book", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    assert _stage(A) == "booked"
    assert _task(A, "Client said yes")[1] and _task(A, "Client said yes")[2] == "done"
    assert _task(A, "Send quote")[2] == "done"
    assert _sql(A, "SELECT 1 FROM quote_marks WHERE vid=?", (vid,))
    assert _sql(A, "SELECT deposit_booked FROM sessions WHERE id=1")[0][0] is not None
    assert _history(A)[-1].startswith("Moved to Booked: the client said yes.")
    assert not any(h == "Moved to Booked" for h in _history(A))

    r = c.post("/session/1/quick", data={"act": "book", "back": "/c/1?s=1"}, environ_base={**E}, headers=FETCH)
    assert _stage(A) == "booked" and sum(h.startswith("Moved to Booked") for h in _history(A)) == 1

def test_book_it_with_no_numbered_quote_closes_send_quote_not_needed(env):
    A, c = env
    _session(A, "inquiry", days=30)
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    assert _stage(A) == "booked"
    send = _task(A, "Send quote")
    assert send and send[2] == "na"

def test_do_not_book_marks_lost_and_reopens(env):

    A, c = env
    _session(A, "inquiry", days=None)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert ">Do not book</button>" in page
    r = c.post("/session/1/stage", data={"stage": "lost", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and _stage(A) == "lost"
    assert _history(A)[-1] == "Did not book"
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Not booked" in page
    c.post("/session/1/stage", data={"stage": "inquiry"}, environ_base=E)
    assert _stage(A) == "inquiry" and _history(A)[-1] == "Undid Do not book: back at Inquiry"

def test_follow_up_after_5_days_only_and_resets_on_sent(env):
    A, c = env
    _session(A, "quoted", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    _sql(A, "INSERT INTO quote_marks (vid, session_id, at) VALUES (?, 1, ?)", (vid, _day(A, -4)))
    _sql(A, "DELETE FROM log")
    assert not [a for a in _acts(A) if a.get("followup")]
    _sql(A, "UPDATE quote_marks SET at=? WHERE vid=?", (_day(A, -5), vid))
    fu = [a for a in _acts(A) if a.get("followup")]
    assert len(fu) == 1
    assert fu[0]["doc"].startswith("mailto:alex@example.com?subject=Following%20up") and f"quote {vid}" in fu[0]["what"]
    assert "%24400" in fu[0]["doc"] and "good%20through" in fu[0]["doc"]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    needs = page.split('id="quote-now"', 1)[1].split("</section>", 1)[0]
    assert ">Follow up</a>" in needs and ">I sent the follow-up</button>" in needs
    r = c.post("/session/1/followup", data={"vid": str(vid), "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    assert _sql(A, "SELECT summary FROM log WHERE session_id=1")[-1][0] == f"Followed up on quote {vid}"
    assert not [a for a in _acts(A) if a.get("followup")]
    _sql(A, "UPDATE log SET at=?", (_day(A, -5) + " 10:00:00",))
    assert [a for a in _acts(A) if a.get("followup")]
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    assert not [a for a in _acts(A) if a.get("followup")]

class _Clock:

    @staticmethod
    def install(A, monkeypatch, hh=10):
        real, day = A.datetime, A.today()

        class Fixed(real):
            @classmethod
            def now(cls, tz=None):
                return real(day.year, day.month, day.day, hh, 0, tzinfo=tz)
        monkeypatch.setattr(A, "datetime", Fixed)

def test_shoot_done_hidden_before_start_time(env, monkeypatch):
    A, c = env
    _Clock.install(A, monkeypatch, 10)
    _session(A, "booked", days=0, start="11:00")
    assert not [a for a in _acts(A) if a.get("shoot_done")]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert ">Shoot done</button>" not in page
    _sql(A, "UPDATE sessions SET start_time='09:30'")
    assert [a for a in _acts(A) if a.get("shoot_done")]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert page.count(">Shoot done</button>") == 1 and 'name="act" value="shot"' in page
    r = c.post("/session/1/quick", data={"act": "shot", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and _stage(A) == "shot"
    assert _history(A)[-1].startswith("Moved to Shot: shoot done.")

def test_shoot_done_offered_from_the_shoot_day_without_a_time_and_never_tentative(env):
    A, c = env
    _session(A, "booked", days=-1)
    assert [a for a in _acts(A) if a.get("shoot_done")]
    _sql(A, "UPDATE sessions SET date_tentative=1")
    assert not [a for a in _acts(A) if a.get("shoot_done")]
    r = c.post("/session/1/quick", data={"act": "shot"}, environ_base=E, headers=FETCH)
    assert r.status_code == 409 and _stage(A) == "booked"

def test_booked_never_auto_moves_to_shot(env):
    A, c = env
    _session(A, "booked", days=-2)
    for t in _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND stage='booked'"):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (A.today().isoformat(), t[0]))
    _sql(A, "INSERT INTO step_closes (session_id, step, outcome) VALUES (1, 'agreement_signed', 'na'), (1, 'deposit_paid', 'na')")
    _in(A, lambda: A.advance(1))
    _in(A, lambda: A.advance_all(force=True))
    assert _stage(A) == "booked"
    acts = _acts(A)
    assert not [a for a in acts if a.get("verb") == "Move to Shot"] and [a for a in acts if a.get("shoot_done")]

def test_forward_move_carries_open_steps_and_lists_them_in_history(env):
    A, c = env
    _session(A, "booked", days=-1)
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    line = _history(A)[-1]
    assert line.startswith("Moved to Shot: shoot done.") and "Still open: " in line and "Agreement signed" in line
    acts = _acts(A)
    carried = [a for a in acts if a.get("carried") == "booked"]
    assert carried and all(a["note"] == "From Booked" and "(from" not in a["what"] for a in carried)
    assert {"Upload original photos", "Cull"} <= {a["what"] for a in acts}

    c.post("/session/1/quick", data={"act": "favorites", "retouch_picks": "IMG_0001", "favorites": "", "veto": ""}, environ_base=E)
    assert _stage(A) == "delivered"

def test_shot_entry_closes_preshoot_steps_as_not_needed(env):
    A, c = env
    _session(A, "booked", days=-1)
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    for title in ("Send session details", "Confirm time and place"):
        t = _task(A, title)
        assert t[1] and t[2] == "na", title
    assert "Not needed: Send the session sheet, Confirm time and place." in _history(A)[-1]

def test_new_stage_steps_are_never_late_on_entry(env):
    A, c = env
    _session(A, "booked", days=-12, logged=[("booked", -40)])
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    for title in ("Back up photos", "Cull", "Edit"):
        assert _task(A, title)[3] == A.today().isoformat(), title
    assert not [a for a in _acts(A) if a.get("status") == "late" and a["what"] in ("Upload original photos", "Cull", "Upload edited photos")]

def test_sync_never_redates_to_today(env):
    A, c = env
    _session(A, "shot", days=-20, logged=[("shot", -20)])
    assert _task(A, "Edit")[3] == _day(A, -10)
    for _ in range(3):
        _in(A, A.sync_open_sessions)
        _in(A, lambda: A.sync_workflow_tasks(1))
    assert _task(A, "Edit")[3] == _day(A, -10) and _task(A, "Cull")[3] == _day(A, -17)

def test_date_change_is_fair_once(env):
    A, c = env
    _session(A, "booked", days=20)
    assert _task(A, "Send session details")[3] == _day(A, 13)
    c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 3), "start_time": "10:00"}, environ_base=E)
    assert _task(A, "Send session details")[3] == A.today().isoformat()
    assert _task(A, "Confirm time and place")[3] == _day(A, 2)
    _in(A, A.sync_open_sessions)
    assert _task(A, "Send session details")[3] == A.today().isoformat()

def test_new_step_in_past_stage_inserted_not_needed(env):
    A, c = env
    _session(A, "delivered", days=-30)
    _sql(A, "DELETE FROM tasks WHERE session_id=1 AND title='Cull'")
    _in(A, lambda: A.sync_workflow_tasks(1))
    t = _task(A, "Cull")
    assert t[1] and t[2] == "na"
    assert not [a for a in _acts(A) if a["what"].startswith("Cull")]

def test_picks_move_shot_to_delivered_from_sync_paste_and_form(env):
    A, c = env

    _session(A, "shot", days=-5, sid=1)
    c.post("/session/1/quick", data={"act": "favorites", "retouch_picks": "IMG_0002", "favorites": "IMG_0003", "veto": ""}, environ_base=E)
    assert _stage(A, 1) == "delivered"
    assert _history(A, 1)[-1].startswith("Moved to Delivered: the client's picks came in.")

    _session(A, "shot", days=-5, sid=2)
    c.post("/session/2/delivery", data={"retouch_picks": "IMG_0009", "favorites": "", "veto": ""}, environ_base=E)
    assert _stage(A, 2) == "delivered"

    _session(A, "shot", days=-5, sid=3)
    _sql(A, "UPDATE sessions SET favorites='IMG_0004' WHERE id=3")
    with A.app.app_context():
        A.advance(3)
    assert _stage(A, 3) == "delivered"
    assert any(h.startswith("Moved to Delivered: the client's picks came in.") for h in _history(A, 3))

    _session(A, "booked", days=-3, sid=4)
    _sql(A, "UPDATE sessions SET favorites='IMG_0005' WHERE id=4")
    with A.app.app_context():
        A.advance(4)
    assert _stage(A, 4) == "delivered"
    assert [r[0] for r in _sql(A, "SELECT stage FROM stage_log WHERE session_id=4 ORDER BY id")] == ["booked", "shot", "delivered"]

def test_picks_never_move_a_session_moved_back_by_hand(env):
    A, c = env
    _session(A, "shot", days=-5)
    c.post("/session/1/quick", data={"act": "favorites", "retouch_picks": "IMG_0002", "favorites": "", "veto": ""}, environ_base=E)
    assert _stage(A) == "delivered"
    c.post("/session/1/stage", data={"stage": "shot"}, environ_base=E)
    _in(A, lambda: A.advance(1))
    _in(A, lambda: A.advance(1))
    assert _stage(A) == "shot"

def test_picks_move_does_not_start_raw_clock(env):

    A, c = env
    due = A.app.config["retention_due"]
    _session(A, "shot", days=-5)
    c.post("/session/1/quick", data={"act": "favorites", "retouch_picks": "IMG_0002", "favorites": "", "veto": ""}, environ_base=E)
    assert _stage(A) == "delivered" and _sql(A, "SELECT 1 FROM stage_log WHERE session_id=1 AND stage='delivered'")
    with A.app.app_context():
        assert due(1) is None
        A.app.config["retention_pass"]()
    assert not _sql(A, "SELECT keep_days, keep_until FROM retention WHERE session_id=1 AND (keep_days IS NOT NULL OR keep_until IS NOT NULL)")
    today_items = _in(A, lambda: [it for fn in A.hooks.today for it in (fn() or [])])
    assert not [it for it in today_items if "delete" in it.get("text", "").lower() or "seal" in it.get("text", "").lower()]

    _sql(A, "UPDATE sessions SET stage='closed'")
    with A.app.app_context():
        assert due(1) is None

    _sql(A, "UPDATE sessions SET stage='delivered', sd_shipped=?", (_day(A, -2),))
    with A.app.app_context():
        d_ = due(1)
    assert d_ is not None and d_.isoformat() > _day(A, 30)
    assert (d_ - A.d(_day(A, -2))).days >= 30

def test_raw_clock_of_a_delivery_from_before_the_rules_is_kept(env):

    A, c = env
    _session(A, "delivered", days=-60, logged=[("delivered", -50)])
    mark = _sql(A, "SELECT MAX(id) FROM stage_log")[0][0]
    _sql(A, "UPDATE settings SET value=? WHERE key='stage_rules_mark'", (str(mark),))
    _sql(A, "UPDATE stage_log SET era='1.4' WHERE id <= ?", (mark,))
    with A.app.app_context():
        d_ = A.app.config["retention_due"](1)
    assert d_ is not None and (d_ - A.d(_day(A, -50))).days > 0
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, 'delivered', ?)", (_day(A, 0),))
    with A.app.app_context():
        assert A.app.config["retention_due"](1) == d_

def _ready_delivered(A, c, sid=1, logged=None):
    _session(A, "delivered", days=-20, sid=sid, logged=logged, initial=not logged)
    _price(A, c, "300", sid=sid)
    for t in _sql(A, "SELECT id FROM tasks WHERE session_id=? AND done IS NULL", (sid,)):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (_day(A, -1), t[0]))
    _sql(A, "UPDATE sessions SET sd_shipped=?, favorites='IMG_1' WHERE id=?", (_day(A, -1), sid))

def test_delivered_and_paid_auto_closes(env):
    A, c = env
    _ready_delivered(A, c)
    assert _stage(A) == "delivered"
    r = _pay(A, c, "300")
    assert r.status_code == 302 and _stage(A) == "closed"
    h = _history(A)
    assert h[-1] == "Moved to Closed: delivered and paid in full." and h[-2].startswith("Recorded balance")
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert ">Reopen at Delivered</button>" in page and "+ New session for Morgan" in page
    assert "closes by itself" not in page

def test_closed_reopen_holds(env):
    A, c = env
    _ready_delivered(A, c)
    _pay(A, c, "300")
    r = c.post("/session/1/stage", data={"stage": "delivered", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and _stage(A) == "delivered"
    _in(A, lambda: A.advance(1))
    _in(A, lambda: A.advance_all(force=True))
    assert _stage(A) == "delivered"

def test_no_autoclose_with_carried_or_owed(env):
    A, c = env
    _ready_delivered(A, c)
    _pay(A, c, "100")
    assert _stage(A) == "delivered"

    _ready_delivered(A, c, sid=2, logged=[("shot", -10), ("delivered", -1)])

    _sql(A, "UPDATE tasks SET done=NULL, outcome=NULL WHERE session_id=2 AND title='Back up photos'")
    _pay(A, c, "300", sid=2)
    assert _stage(A, 2) == "delivered"
    assert [a for a in _acts(A, 2) if a["what"] == "Upload original photos" and a.get("note") == "From Shot"]

def test_gallery_expiry_tile_for_closed_session(env):
    A, c = env
    _ready_delivered(A, c)
    _pay(A, c, "300")
    assert _stage(A) == "closed"
    days = A.C.gallery_days("family")
    _sql(A, "UPDATE sessions SET first_visit=? WHERE id=1", (_day(A, 1 - days),))
    tiles = [a for a in _acts(A) if a.get("verb") == "Give extension"]
    assert len(tiles) == 1 and tiles[0]["stage"] == "closed"

def test_collect_balance_first_replaces_mark_delivered(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "318")

    collect = [a for a in _acts(A) if a.get("collect")]
    assert len(collect) == 1 and collect[0]["what"] == "Send the invoice ($318.00 balance, Family session)" and collect[0]["doc"]
    _invoice_sent(A, c)
    acts = _acts(A)
    collect = [a for a in acts if a.get("collect")]
    assert len(collect) == 1 and collect[0]["verb"] == "Record payment ($318.00)" and not collect[0].get("post")
    assert collect[0]["what"] == "Collect the $318.00 balance first"
    assert collect[0]["detail"].startswith("Balance $318.00 of $318.00 (Family session, invoice ")
    assert not [a for a in acts if a.get("verb") == "Mark delivered"]
    assert not [a for a in acts if (a.get("what") or "").startswith("Balance")]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Collect the $318.00 balance first" in page and "Record payment ($318.00)" in page and ">Mark delivered<" not in page

    r = c.post("/session/1/quick", data={"act": "mark", "field": "sd_shipped"}, environ_base=E)
    assert r.status_code == 302 and not _sql(A, "SELECT sd_shipped FROM sessions WHERE id=1")[0][0]
    _pay(A, c, "318")
    acts = _acts(A)
    assert not [a for a in acts if a.get("collect")] and [a for a in acts if a.get("verb") == "Mark delivered"]

def test_delivery_date_typed_while_owed_asks_first(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "318")
    r = c.post("/session/1/delivery", data={"sd_shipped": _day(A, -1)}, environ_base=E)
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "$318.00 is still owed" in body and 'name="confirm_owed"' in body
    assert not _sql(A, "SELECT sd_shipped FROM sessions WHERE id=1")[0][0]
    c.post("/session/1/delivery", data={"sd_shipped": _day(A, -1), "confirm_owed": "1"}, environ_base=E)
    assert _sql(A, "SELECT sd_shipped FROM sessions WHERE id=1")[0][0] == _day(A, -1)
    h = _history(A)
    assert h[-2] == "Updated delivery dates" and "with $318.00 still owed: chosen over the hold" in h[-1]

def test_reply_logs_contact_and_opens_the_draft(env):
    A, c = env
    _session(A, "inquiry", days=None)
    _sql(A, "INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (1, 1, 'email', 'Any December dates?', ?)",
         (_day(A, 0) + " 08:00:00",))
    r = c.post("/session/1/reply", data={"mail": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:alex@example.com?subject=Re")
    assert _sql(A, "SELECT summary FROM log WHERE session_id=1 ORDER BY id")[-1][0] == "Replied to their message"
    r = c.post("/session/1/reply", data={"back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")

def test_reply_hides_the_website_inquiry_on_today(env):
    A, c = env
    _session(A, "inquiry", days=None)
    if not _sql(A, "SELECT 1 FROM sqlite_master WHERE name='studio_inquiries'"):
        pytest.skip("the studio page plugin is off")
    _sql(A, "INSERT INTO studio_inquiries (sha256, session_id, household_id, received, outcome) VALUES ('ab', 1, 1, ?, 'added')",
         (_day(A, 0) + " 08:00:00",))
    _sql(A, "INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (1, 1, 'email', 'Any December dates?', ?)",
         (_day(A, 0) + " 08:00:00",))

    def items():
        return [it for fn in A.hooks.today for it in (fn() or []) if it.get("text") == "New inquiry from the website"]
    assert _in(A, items)
    c.post("/session/1/reply", data={}, environ_base=E)
    assert not _in(A, items)

def test_every_stage_change_writes_history(env):
    A, c = env
    _session(A, "inquiry", days=-1)
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "stage", "stage": "delivered"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "stage", "stage": "closed"}, environ_base=E)
    c.post("/session/1/stage", data={"stage": "delivered"}, environ_base=E)
    stages = [r[0] for r in _sql(A, "SELECT stage FROM stage_log WHERE session_id=1 ORDER BY id")]
    h = [x for x in _history(A) if x.startswith("Moved to")]
    assert stages == ["inquiry", "booked", "shot", "delivered", "closed", "delivered"]
    assert [x.split(":")[0].split(" ")[2] if ":" in x else x.split(" ")[2] for x in h] == ["Booked", "Shot", "Delivered", "Closed", "Delivered"]
    assert h[2].startswith("Moved to Delivered: moved by hand.") and h[3].startswith("Moved to Closed: moved by hand.")

def test_in_place_answers_without_reload(env):

    A, c = env
    _session(A, "quoted", days=30)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    form = page.split('name="act" value="book"', 1)[0].rsplit("<form", 1)[1]
    assert "data-ui-inplace" in form
    r = c.post("/session/1/quick", data={"act": "book", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    assert c.get(r.headers["Location"], environ_base=E).status_code == 200
