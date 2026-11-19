from test_upgrade_from_140 import BY, _in_request, _open, _place, _q

ELLIS, LANE, CHEN = BY["ellis"], BY["lane"], BY["chen"]

def test_an_invoice_issued_under_1_4_counts_as_sent(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    assert not _q(A, "SELECT 1 FROM invoice_sent WHERE session_id=?", (ELLIS,))
    sent_before = _q(A, "SELECT COUNT(*) FROM invoice_sent")[0][0]
    went = _in_request(A, lambda: A.invoice_went(ELLIS))
    assert went and went["number"] == "INV-2026-0003" and went["day"] == "2026-11-17"
    whats = _in_request(A, lambda: [a["what"] for a in A.build_actions(only_sid=ELLIS)])
    assert not [w for w in whats if w.startswith("Send the invoice")], whats
    assert _q(A, "SELECT COUNT(*) FROM invoice_sent")[0][0] == sent_before
    assert _in_request(A, lambda: A.invoice_went(ELLIS)) == went

def test_an_invoice_issued_after_the_update_still_waits_for_mark_sent(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    from_ = _q(A, "SELECT value FROM settings WHERE key='stage_rules_from'")[0][0]
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date, created) VALUES (900, 1, 'family', 'shot', ?, ?)",
                    (from_, from_ + " 10:00:00"))
        con.execute("INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (900, 'INV', 'INV-2099-0900', ?)", (from_,))
        con.commit()
    finally:
        con.close()
    assert _in_request(A, lambda: A.invoice_went(900)) is None
    con = A.connect()
    try:
        con.execute("DELETE FROM settings WHERE key='stage_rules_from'")
        con.commit()
    finally:
        con.close()
    assert _in_request(A, lambda: A.invoice_went(ELLIS)) is None
