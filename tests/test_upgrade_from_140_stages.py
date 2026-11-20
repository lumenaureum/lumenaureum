import json
from datetime import timedelta

from test_upgrade_from_140 import BY, EXPECTED, _in_request, _open, _place, _q

REYES, LANE, KIM, FOSTER = BY["reyes"], BY["lane"], BY["kim"], BY["foster"]

def _audit(A, sid):
    return [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

def _due(A, sid):
    with A.app.app_context():
        return A.app.config["retention_due"](sid)

def test_the_1_4_0_sessions_take_the_new_rules_once(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    mark = int(_q(A, "SELECT value FROM settings WHERE key='stage_rules_mark'")[0][0])
    log = _q(A, "SELECT id, session_id, stage, at FROM stage_log ORDER BY id")
    assert mark == max(r[0] for r in log if r[0] <= mark) and _q(A, "SELECT value FROM settings WHERE key='stage_rules_from'")[0][0] == A.today().isoformat()

    assert EXPECTED["delivered"][str(REYES)]["stage"] == "shot"
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (REYES,))[0][0] == "delivered"
    notes = [a for a in _audit(A, REYES) if a.startswith("Updated to the 1.5 stage rules.")]

    assert _q(A, "SELECT sd_shipped FROM sessions WHERE id=?", (REYES,))[0][0]
    assert notes == ["Updated to the 1.5 stage rules. Moved to Delivered: the files were delivered."]

    after = [r for r in log if r[0] > mark]
    assert after and all(r[2] in ("delivered", "closed") for r in after)
    for sid, stage in _q(A, "SELECT id, stage FROM sessions WHERE stage='booked'"):
        assert not any(r[1] == sid for r in after)

    assert _due(A, REYES) is None

    upd = json.loads(_q(A, "SELECT value FROM settings WHERE key='update_notice'")[0][0])
    assert "Reyes" in upd["stages"]

    n_audit = _q(A, "SELECT COUNT(*) FROM audit")[0][0]
    assert _in_request(A, A.stage_rules_upgrade) is None
    A2, c2, _ = _open(tmp_path, monkeypatch)
    assert _q(A2, "SELECT COUNT(*) FROM audit")[0][0] == n_audit
    assert _q(A2, "SELECT id, session_id, stage, at FROM stage_log ORDER BY id") == log
    assert _q(A2, "SELECT value FROM settings WHERE key='stage_rules_mark'")[0][0] == str(mark)

def test_a_real_1_4_install_is_re_dated_and_moved_on_without_starting_a_clock(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    t = A.today()
    old = (t - timedelta(days=2)).isoformat()
    con = A.connect()
    try:
        con.execute("DELETE FROM settings WHERE key IN ('stage_rules_mark', 'stage_rules_from')")
        con.execute("UPDATE stage_log SET at='2026-08-01'")
        con.execute("DELETE FROM stage_log WHERE session_id=? AND stage IN ('delivered', 'closed') AND id > ?",
                    (REYES, 0))
        con.execute("UPDATE sessions SET stage='shot' WHERE id=?", (REYES,))
        con.execute("UPDATE tasks SET due=? WHERE done IS NULL AND due IS NOT NULL", (old,))

        con.execute("UPDATE tasks SET title='Back up photos', done=NULL WHERE session_id=? AND title='Back up photos'", (KIM,))
        con.execute("INSERT INTO tasks (id, session_id, stage, title, offset_days, sort, done, outcome) VALUES "
                    "(9001, ?, 'shot', 'Back up RAWs', 0, 300, '2026-08-23', 'done')", (KIM,))

        con.execute("UPDATE tasks SET done='2026-11-01', outcome='done' WHERE session_id=? AND done IS NULL", (LANE,))
        con.execute("UPDATE tasks SET outcome='na' WHERE session_id=? AND title='Send retouched photos'", (LANE,))

        con.execute("UPDATE sessions SET sd_shipped='2026-11-02' WHERE id=?", (LANE,))
        con.commit()
    finally:
        con.close()
    lane_due_before = _due(A, LANE)
    owed = _in_request(A, lambda: A.totals(LANE)["due"])
    if owed > 0.005:
        con = A.connect()
        try:
            con.execute("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?, 'balance', ?, '2026-11-02', 'card')",
                        (LANE, round(owed, 2)))
            con.commit()
        finally:
            con.close()
    before_log = _q(A, "SELECT id FROM stage_log ORDER BY id DESC LIMIT 1")[0][0]
    before_audit = _q(A, "SELECT MAX(id) FROM audit")[0][0]
    out = _in_request(A, A.stage_rules_upgrade)
    assert out is not None and out["dated"] > 0 and out["renamed"] == 1

    shoot = A.d(_q(A, "SELECT shoot_date FROM sessions WHERE id=?", (FOSTER,))[0][0])
    for title, off in (("Cull", 3), ("Edit", 10)):
        due = _q(A, "SELECT due FROM tasks WHERE session_id=? AND title=?", (FOSTER, title))[0][0]
        assert due == max(shoot + timedelta(days=off), A.d("2026-08-01")).isoformat() and due != t.isoformat()
    note = [a for a in _audit(A, FOSTER) if a.startswith("Updated to the 1.5 stage rules.")]
    assert len(note) == 1 and "Step dates now count from the shoot: " in note[0] and "Cull " in note[0] and f"(was {A.nice(old)})" in note[0]

    assert _q(A, "SELECT done FROM tasks WHERE session_id=? AND title='Back up photos'", (KIM,))[0][0] == "2026-08-23"
    assert any("'Back up photos' counts as done on" in a for a in _audit(A, KIM))

    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (REYES,))[0][0] == "delivered"
    assert _due(A, REYES) is None

    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (LANE,))[0][0] == "closed"
    assert any("Moved to Closed: delivered and paid in full." in a for a in _audit(A, LANE))
    assert _due(A, LANE) == lane_due_before

    for (sid_,) in _q(A, "SELECT id FROM sessions WHERE stage IN ('delivered', 'closed')"):
        d_ = _due(A, sid_)
        assert d_ is None or (d_ - t).days != A.C.raw_keep_days("family"), sid_

    new_lines = _q(A, "SELECT session_id, action FROM audit WHERE id > ?", (before_audit,))
    assert new_lines and all(r[1].startswith("Updated to the 1.5 stage rules.") for r in new_lines)
    assert len({r[0] for r in new_lines}) == len(new_lines)
    assert all(r[0] > before_log for r in _q(A, "SELECT id FROM stage_log WHERE id > ?", (before_log,)))

    assert _in_request(A, A.stage_rules_upgrade) is None

def test_the_1_4_0_prices_used_on_sent_quotes_count_as_approved_once(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    ap = json.loads(_q(A, "SELECT value FROM settings WHERE key='price_approvals'")[0][0])
    cur = _q(A, "SELECT MAX(id) FROM fee_schedules")[0][0]
    used = {r[0] for r in _q(A, """SELECT q.schedule_id FROM quotes q JOIN sessions s ON s.id=q.session_id
                                   WHERE s.stage IN ('booked','shot','delivered','closed')""")}
    assert used and used <= {int(k) for k in ap} and str(cur) in ap
    created = _q(A, "SELECT created FROM fee_schedules WHERE id=?", (cur,))[0][0][:10]
    code_ = ap[str(cur)]["code"]
    assert ap[str(cur)]["on"] == created and code_.startswith(created.replace("-", "")) and code_.isdigit() and len(code_) == 10
    assert "Your prices are still the starter prices" not in c.get("/alerts", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert _in_request(A, A.price_approval_upgrade) is None
    A2, c2, _ = _open(tmp_path, monkeypatch)
    assert json.loads(_q(A2, "SELECT value FROM settings WHERE key='price_approvals'")[0][0]) == ap

def test_a_1_4_0_install_that_never_sent_a_quote_starts_with_sample_prices(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    con = A.connect()
    try:
        con.execute("DELETE FROM settings WHERE key='price_approvals'")
        con.execute("DELETE FROM quote_marks")
        con.execute("UPDATE tasks SET done=NULL WHERE title='Send quote'")
        con.execute("UPDATE sessions SET stage='quoted' WHERE stage IN ('booked','shot','delivered','closed')")
        con.commit()
    finally:
        con.close()
    assert _in_request(A, A.price_approval_upgrade) == []
    assert "Your prices are still the starter prices" in c.get("/alerts", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)

def test_a_session_the_pass_moved_keeps_its_files_until_a_date_is_set_or_a_new_event(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (REYES,))[0][0] == "delivered"
    assert _q(A, "SELECT sd_shipped FROM sessions WHERE id=?", (REYES,))[0][0]
    assert _due(A, REYES) is None
    page = c.get(f"/c/{_q(A, 'SELECT household_id FROM sessions WHERE id=?', (REYES,))[0][0]}?s={REYES}",
                 environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "RAWs and outtakes: no deletion date set" in page and "Set the deletion date" in page
    assert "Readable copies for editing: encrypted and removed a week after delivery" in page or "encrypted and removed" not in page
    today_ = c.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "no deletion date for the RAWs and outtakes" in today_

    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not _q(A, "SELECT purged_at FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (REYES,))
    assert not _q(A, "SELECT sealed_at FROM retention WHERE session_id=? AND sealed_at IS NOT NULL", (REYES,))

    until = (A.today() + timedelta(days=200)).isoformat()
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (REYES,))[0][0]
    r = c.post(f"/session/{REYES}/raw/date", data={"until": until, "back": f"/c/{hid}?s={REYES}"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (302, 303)
    assert _due(A, REYES) == A.d(until)
    assert "no deletion date for the RAWs and outtakes" not in c.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)

    c.post(f"/session/{REYES}/raw/date", data={"until": A.today().isoformat()}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert _due(A, REYES) == A.d(until)

def test_a_new_event_after_the_upgrade_starts_the_usual_clock(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    assert _due(A, REYES) is None
    with A.app.app_context():
        keep = A.C.raw_keep_days("family")
        r_ = A.q("SELECT keep_days FROM retention WHERE session_id=?", (REYES,), one=True)
    keep = r_["keep_days"] if r_ and r_["keep_days"] else keep
    later = (A.today() + timedelta(days=3)).isoformat()
    con = A.connect()
    try:
        con.execute("UPDATE sessions SET sd_shipped=? WHERE id=?", (later, REYES))
        con.commit()
    finally:
        con.close()
    assert _due(A, REYES) == A.d(later) + timedelta(days=keep)
    con = A.connect()
    try:
        con.execute("UPDATE sessions SET sd_shipped=? WHERE id=?", (EXPECTED["delivered"][str(REYES)]["sd_shipped"], REYES))
        con.execute("INSERT INTO stage_log (session_id, stage, at) VALUES (?, 'closed', ?)", (REYES, A.today().isoformat()))
        con.commit()
    finally:
        con.close()
    assert _due(A, REYES) == A.today() + timedelta(days=keep)
