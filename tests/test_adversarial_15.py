import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_stage_logic_150 import (E, _day, _history, _in, _invoice_sent, _pay, _price, _ready_delivered,
                                  _session, _sql, _stage, env)

DUE = "retention_due"

def _due(A, sid=1):
    with A.app.app_context():
        return A.app.config[DUE](sid)

def _as_1_4(A):

    _sql(A, "DELETE FROM settings WHERE key IN ('stage_rules_mark', 'stage_rules_todo')")

def _event_shot(A, sid, first_visit_days, picks=True):

    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, created, stage_changed) VALUES (?,1,'event','shot',?,?,?)",
         (sid, _day(A, -200), _day(A, -210), _day(A, -199)))
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (?, 'shot', ?)", (sid, _day(A, -199)))
    _in(A, lambda: A.sync_workflow_tasks(sid))
    _sql(A, "UPDATE sessions SET gallery_built=?, first_visit=? WHERE id=?", (_day(A, -190), _day(A, first_visit_days), sid))
    if picks:
        _sql(A, "UPDATE sessions SET favorites='IMG_1' WHERE id=?", (sid,))

def _notices(A):
    return [tuple(r) for r in _sql(A, "SELECT session_id, notice FROM retention WHERE notice IS NOT NULL AND notice != ''")] \
        if "notice" in [r[1] for r in _sql(A, "PRAGMA table_info(retention)")] else []

def test_c1_upgrade_never_gives_a_closed_session_a_clock_it_did_not_have(env):
    A, c = env
    _as_1_4(A)
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, first_visit, gallery_built) VALUES (1,1,'event','closed',?,?,?)",
         (_day(A, -400), _day(A, -380), _day(A, -390)))
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1,'shot',?), (1,'closed',?)", (_day(A, -400), _day(A, -300)))
    assert _due(A) is None
    _in(A, A.stage_rules_upgrade)
    assert _due(A) is None
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not _notices(A)

def test_h1_upgrade_pass_moving_a_gallery_only_session_starts_no_clock(env):
    A, c = env
    _event_shot(A, 1, -180)
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert _stage(A) == "delivered"
    assert _due(A) is None
    assert _sql(A, "SELECT era FROM stage_log WHERE session_id=1 AND stage='delivered'")[0][0] == "upgrade"
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not _notices(A)

def test_h1_picks_long_after_the_gallery_opened_start_the_clock_on_the_move_day(env):
    A, c = env
    _event_shot(A, 2, -120, picks=False)
    _sql(A, "UPDATE sessions SET favorites='IMG_1' WHERE id=2")
    _in(A, lambda: A.advance(2))
    assert _stage(A, 2) == "delivered"
    with A.app.app_context():
        keep = A.C.raw_keep_days("event")
    d_ = _due(A, 2)
    assert d_ is not None and d_ >= A.today() + timedelta(days=keep)

def test_h1_gallery_opened_after_the_move_dates_the_clock_from_the_visit(env):
    A, c = env
    _event_shot(A, 3, -10, picks=False)
    _sql(A, "UPDATE sessions SET first_visit=NULL WHERE id=3")
    _sql(A, "UPDATE sessions SET favorites='IMG_1' WHERE id=3")
    _in(A, lambda: A.advance(3))
    assert _stage(A, 3) == "shot" and _due(A, 3) is None
    c.post("/session/3/stage", data={"stage": "delivered"}, environ_base=E)
    assert _stage(A, 3) == "delivered" and _due(A, 3) is None
    _sql(A, "UPDATE sessions SET first_visit=? WHERE id=3", (_day(A, 5),))
    with A.app.app_context():
        keep = A.C.raw_keep_days("event")
    assert _due(A, 3) == A.d(_day(A, 5)) + timedelta(days=keep)

def test_h2_a_deleted_session_never_makes_new_moves_read_as_1_4_ones(env):
    A, c = env
    _session(A, "shot", days=-5, sid=1)
    _session(A, "booked", days=30, sid=2, logged=[("booked", -3), ("booked", -2), ("booked", -1)])
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert {r[0] for r in _sql(A, "SELECT era FROM stage_log")} == {"1.4"}
    _sql(A, "DELETE FROM sessions WHERE id=2")
    _sql(A, "DELETE FROM stage_log WHERE session_id=2")
    c.post("/session/1/stage", data={"stage": "delivered"}, environ_base=E)
    assert _stage(A, 1) == "delivered"
    new_ = _sql(A, "SELECT id, era FROM stage_log WHERE session_id=1 AND stage='delivered'")
    assert new_ and new_[0][1] is None
    assert _due(A, 1) is None

def test_h2_a_database_marked_by_row_number_is_marked_once_at_unlock(env):

    A, c = env
    _session(A, "delivered", days=-60, logged=[("delivered", -50)])
    mark = _sql(A, "SELECT MAX(id) FROM stage_log")[0][0]
    _sql(A, "UPDATE settings SET value=? WHERE key='stage_rules_mark'", (str(mark),))
    _sql(A, "ALTER TABLE stage_log DROP COLUMN era")
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, 'delivered', ?)", (_day(A, 0),))
    with A.app.app_context():
        A.init_db()
    rows = _sql(A, "SELECT id, era FROM stage_log ORDER BY id")
    assert all(e == "1.4" for i, e in rows if i <= mark) and rows[-1][1] is None
    assert _due(A) is not None and (_due(A) - A.d(_day(A, -50))).days > 0

def test_h2_moves_an_earlier_1_5_pass_made_are_marked_as_its_own_at_unlock(env):
    A, c = env
    _event_shot(A, 1, -180)
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert _stage(A) == "delivered"
    _sql(A, "ALTER TABLE stage_log DROP COLUMN era")
    with A.app.app_context():
        A.init_db()
    assert _sql(A, "SELECT era FROM stage_log WHERE session_id=1 AND stage='delivered'")[0][0] == "upgrade"
    assert _due(A) is None

def test_h4_a_removed_type_starts_no_clock_from_a_proof_gallery_visit(env):
    A, c = env
    _session(A, "delivered")
    _sql(A, "UPDATE sessions SET first_visit=? WHERE id=1", (_day(A, -5),))
    assert _due(A) is None
    _sql(A, "UPDATE sessions SET type='gone_type' WHERE id=1")
    assert _due(A) is None

def test_m1_one_failing_session_never_stops_the_others_and_is_tried_again(env):
    A, c = env
    _session(A, "shot", days=-5, sid=1)
    _session(A, "shot", days=-5, sid=2)
    _sql(A, "UPDATE sessions SET sd_shipped=? WHERE id IN (1, 2)", (_day(A, -1),))
    _as_1_4(A)
    orig = A.advance

    def boom(sid, log=True):
        if sid == 1:
            raise RuntimeError("one bad session")
        return orig(sid, log=log)
    A.advance = boom
    try:
        _in(A, A.stage_rules_upgrade)
    finally:
        A.advance = orig
    assert _stage(A, 2) == "delivered" and _stage(A, 1) == "shot"
    assert _sql(A, "SELECT value FROM settings WHERE key='stage_rules_todo'")[0][0] == "[1]"
    out = _in(A, A.stage_rules_upgrade)
    assert out is not None and _stage(A, 1) == "delivered"
    for sid in (1, 2):
        assert len([h for h in _history(A, sid) if h.startswith("Updated to the 1.5 stage rules.")]) == 1
    assert _in(A, A.stage_rules_upgrade) is None

def test_h5_not_needed_or_declined_is_refused_while_money_is_owed(env):
    A, c = env
    _ready_delivered(A, c)
    _invoice_sent(A, c)
    _pay(A, c, "100")
    for outcome in ("na", "declined"):
        c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": outcome, "back": "/"}, environ_base=E)
        assert not _sql(A, "SELECT 1 FROM step_closes WHERE session_id=1 AND step='balance_paid'")
        assert _stage(A) == "delivered"
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert 'name="outcome" value="na"' not in page.split("Paid in full", 1)[-1].split("</details>", 1)[0]

def test_h5_a_1_4_step_closed_not_needed_with_money_owed_never_closes_the_session(env):
    A, c = env
    _ready_delivered(A, c)
    _invoice_sent(A, c)
    _pay(A, c, "100")
    _sql(A, "INSERT INTO step_closes (session_id, step, outcome) VALUES (1, 'balance_paid', 'na')")
    _in(A, lambda: A.advance(1))
    assert _stage(A) == "delivered"
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert _stage(A) == "delivered"

def _waived_then_replaced(A, c, new_amount):
    _session(A, "shot", days=-5)
    _price(A, c, "300")
    _invoice_sent(A, c)
    _pay(A, c, "200")
    c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": "waived", "note_why": "friend"}, environ_base=E)
    c.post("/session/1/quote/replace", data={"back": "/"}, environ_base=E)
    lid = _sql(A, "SELECT id FROM lines WHERE session_id=1")[0][0]
    c.post(f"/money/line/{lid}/delete", data={"back": "/"}, environ_base=E)
    c.post("/session/1/money", data={"what": "line", "label": "New package", "amount": new_amount}, environ_base=E)
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)

def test_h6_a_higher_new_quote_opens_paid_in_full_again_and_never_closes_owed(env):
    A, c = env
    _waived_then_replaced(A, c, "550")
    with A.app.test_request_context():
        assert abs(A.totals(1)["due"] - 250) < 0.01 and not A.nothing_owed(1)
    assert not _sql(A, "SELECT 1 FROM step_closes WHERE session_id=1 AND step='balance_paid'")
    for t_ in _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND done IS NULL"):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (_day(A, -1), t_[0]))
    _sql(A, "UPDATE sessions SET sd_shipped=?, favorites='IMG_1', gallery_built=?, letter_mailed=?, first_visit=? WHERE id=1",
         (_day(A, -1),) * 4)
    _in(A, lambda: A.advance(1))
    assert _stage(A) == "delivered"

def test_h7_a_lower_new_quote_never_turns_the_write_off_into_a_credit(env):
    A, c = env
    _waived_then_replaced(A, c, "250")
    with A.app.test_request_context():
        A.db()
        t = A.totals(1)
        assert abs(t["due"]) < 0.01 and abs(t["written_off"] - 50) < 0.01 and not A.owed_back(1)
        v = A.bill_ctx(1, live=True)
    assert not v.get("credit")
    assert any("Reversed $50.00 of the write-off" in h for h in _history(A))

def test_h8_deleting_the_old_line_while_replacing_never_closes_the_session(env):
    A, c = env
    _ready_delivered(A, c)
    _invoice_sent(A, c)
    _pay(A, c, "100")
    c.post("/session/1/quote/replace", data={"back": "/"}, environ_base=E)
    lid = _sql(A, "SELECT id FROM lines WHERE session_id=1")[0][0]
    c.post(f"/money/line/{lid}/delete", data={"back": "/"}, environ_base=E)
    assert _stage(A) == "delivered"
    c.post("/session/1/money", data={"what": "line", "label": "Family session + album", "amount": "450"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)
    assert _stage(A) == "delivered"
    with A.app.test_request_context():
        assert abs(A.totals(1)["due"] - 350) < 0.01

def test_h8_paid_with_no_price_left_is_owed_back_and_never_closes(env):
    A, c = env
    _session(A, "delivered", days=-20)
    for t in _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND done IS NULL"):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (_day(A, -1), t[0]))
    _sql(A, "UPDATE sessions SET sd_shipped=?, favorites='IMG_1' WHERE id=1", (_day(A, -1),))
    _sql(A, "INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 100, ?)", (_day(A, -40),))
    _in(A, lambda: A.advance(1))
    assert _stage(A) == "delivered"
    with A.app.test_request_context():
        assert A.owed_back(1)

def test_h8_a_closed_session_never_books_a_new_quote(env):
    A, c = env
    _ready_delivered(A, c)
    _invoice_sent(A, c)
    _pay(A, c, "100")
    c.post("/session/1/quote/replace", data={"back": "/"}, environ_base=E)
    _sql(A, "UPDATE sessions SET stage='closed' WHERE id=1")
    c.post("/session/1/money", data={"what": "line", "label": "Album", "amount": "150"}, environ_base=E)
    assert _sql(A, "SELECT COUNT(*) FROM lines WHERE session_id=1")[0][0] == 1
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)
    assert _sql(A, "SELECT booked_at FROM quote_replacements WHERE session_id=1")[0][0] is None

def test_retouch_picks_with_no_finals_never_close_and_the_upgrade_opens_the_step_again(env):

    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "300")
    for t in _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND done IS NULL"):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (_day(A, -1), t[0]))
    _sql(A, "UPDATE sessions SET sd_shipped=?, retouch_picks='IMG_1.jpg: soften the light' WHERE id=1", (_day(A, -1),))
    _invoice_sent(A, c)
    _pay(A, c, "300")
    assert _stage(A) == "delivered"
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert _stage(A) == "delivered"
    assert _sql(A, "SELECT done FROM tasks WHERE session_id=1 AND title='Send retouched photos'")[0][0] is None
    assert any("'Send retouched photos' is open again: 0 of 1" in h for h in _history(A))

    _sql(A, "UPDATE tasks SET done=?, outcome='na' WHERE session_id=1 AND title='Send retouched photos'", (_day(A, 0),))
    _in(A, lambda: A.advance(1))
    assert _stage(A) == "closed"

def test_a_waived_balance_reads_as_waived_beside_what_was_paid(env):
    A, c = env
    _session(A, "shot", days=-5)
    _price(A, c, "212")
    _pay(A, c, "50", kind="deposit")
    c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": "waived", "note_why": "friend"}, environ_base=E)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "$50.00 of $212.00 paid, $162.00 waived" in page
    assert "Paid in full (waived)" not in page and "$162.00 waived" in page

def test_b_a_1_3_style_session_the_pass_moved_gets_no_deletion_date_from_the_move(env):

    A, c = env
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, created, stage_changed, sd_shipped, favorites) "
            "VALUES (5,1,'family','shot',?,?,?,?, 'IMG_1')", (_day(A, -40), _day(A, -50), _day(A, -39), _day(A, -10)))
    _sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (5, 'shot', ?)", (_day(A, -39),))
    _in(A, lambda: A.sync_workflow_tasks(5))
    assert _due(A, 5) is None
    _as_1_4(A)
    _in(A, A.stage_rules_upgrade)
    assert _stage(A, 5) in ("delivered", "closed")
    assert _due(A, 5) is None
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not _sql(A, "SELECT 1 FROM retention WHERE session_id=5 AND (sealed_at IS NOT NULL OR purged_at IS NOT NULL)")
