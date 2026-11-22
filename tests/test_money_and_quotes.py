import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1"}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
    return A, c, sid

def _tot(A, sid):
    with A.app.test_request_context():
        A.db()
        return A.totals(sid)

def test_lines_payments_refunds_and_voids(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    assert _tot(A, sid)["quoted"] == 300
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "-100", "method": "cash"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 0
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "abc"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 0
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "method": "cash"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 100
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "refund", "amount": "40", "method": "cash"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 60
    assert _tot(A, sid)["due"] == 200 and _tot(A, sid)["quoted"] == 300

    con = A.connect()
    pid = con.execute("SELECT id FROM payments WHERE session_id=? AND amount=100", (sid,)).fetchone()[0]
    rid = con.execute("SELECT id FROM payments WHERE session_id=? AND kind='refund'", (sid,)).fetchone()[0]
    lid = con.execute("SELECT id FROM lines WHERE session_id=?", (sid,)).fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM lines WHERE session_id=?", (sid,)).fetchone()[0] == 1
    con.close()
    c.post(f"/money/payment/{pid}/delete", environ_base=E)
    assert _tot(A, sid)["paid"] == -40
    c.post(f"/money/payment/{pid}/delete", environ_base=E)
    assert _tot(A, sid)["paid"] == 60
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM payments WHERE session_id=?", (sid,)).fetchone()[0] == 2
    con.close()
    c.post(f"/money/payment/{rid}/delete", data={"set": "void"}, environ_base=E)
    assert _tot(A, sid)["quoted"] == 300 and _tot(A, sid)["paid"] == 100 and _tot(A, sid)["due"] == 200
    c.post(f"/money/payment/{rid}/delete", data={"set": "restore"}, environ_base=E)
    assert _tot(A, sid)["quoted"] == 300 and _tot(A, sid)["due"] == 200
    c.post(f"/money/line/{lid}/delete", environ_base=E)
    assert _tot(A, sid)["quoted"] == 0 and _tot(A, sid)["refund_credit"] == 40

def test_fees_discount_requote_and_send(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    q1 = _tot(A, sid)["quoted"]
    assert q1 > 0
    assert c.post(f"/session/{sid}/quote/fee", data={"fee_key": "no-such-fee"}, environ_base=E).status_code == 400
    c.post(f"/session/{sid}/quote/discount", data={"pct": "10", "reason": "Returning client"}, environ_base=E)
    assert round(_tot(A, sid)["quoted"], 2) < q1
    c.post(f"/session/{sid}/quote/discount", data={"pct": "150"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT discount_pct FROM quotes WHERE session_id=?", (sid,)).fetchone()[0] == 100
    con.close()
    c.post(f"/session/{sid}/quote/discount", data={"pct": "10"}, environ_base=E)
    r = c.post(f"/session/{sid}/quote/send", environ_base=E)
    assert r.status_code == 302 and "/quote/" in r.headers["Location"]
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM quote_versions WHERE session_id=?", (sid,)).fetchone()[0] == 1
    con.close()
    assert c.post(f"/session/{sid}/quote/requote", environ_base=E).status_code in (302, 303)

def test_a_discount_with_no_reason_never_reads_no_discount(tmp_path, monkeypatch):

    A, c, sid = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    con = A.connect()
    labels = [r[0] for r in con.execute("SELECT label FROM lines WHERE session_id=? AND kind='discount'", (sid,))]
    con.close()
    assert labels == ["Discount (100%)"] and round(_tot(A, sid)["quoted"], 2) == 0

def test_a_quote_with_nothing_to_pay_names_no_deposit(tmp_path, monkeypatch):

    A, c, sid = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        s = A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True)
        paid_terms = dict(A.quote_terms(s, A.totals(sid)))
    assert "deposit" in paid_terms["Booking"].lower() and "Payment" in paid_terms
    c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        free_terms = dict(A.quote_terms(s, A.totals(sid)))
    assert free_terms["Booking"] == "There is nothing to pay for this session: no deposit and no balance."
    assert "Payment" not in free_terms

def test_a_quote_with_nothing_to_pay_talks_about_money_nowhere(tmp_path, monkeypatch):

    import re as _re
    A, c, sid = _app(tmp_path, monkeypatch)
    import config as C
    for t_ in C.SESSION_TYPES:
        terms = dict(A.nothing_to_pay_terms([list(x_) for x_ in C.build_terms(t_, "Studio")]))
        assert terms["Booking"] == "There is nothing to pay for this session: no deposit and no balance." and "Payment" not in terms
        for h_, v_ in terms.items():
            if h_ != "Booking":
                assert not _re.search(r"\$\s?\d|deposit|balance|refund|\bfee|mileage rate|once the balance", v_, _re.I), (t_, h_, v_)
    fam = dict(A.nothing_to_pay_terms([list(x_) for x_ in C.build_terms("family", "Studio")]))
    assert "free reschedule" in fam["Cancellation"] and "Delivery" in fam

def test_an_agreement_with_nothing_to_pay_says_no_charge(tmp_path, monkeypatch):

    A, c, sid = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "<td>Balance</td>" in page
    c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "<td>Price</td><td>No charge" in page and "<td>Balance</td>" not in page and "<td>Deposit</td>" not in page

def test_a_quote_is_never_good_for_zero_days(tmp_path, monkeypatch):

    A, c, sid = _app(tmp_path, monkeypatch)
    import config as C
    assert C.QUOTE_TERM_DAYS == 14
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('quote_term_days', '0')")
    con.commit()
    con.close()
    with A.app.test_request_context():
        A.db()
        assert A.quote_term() == 14
    for bad in ("0", "400"):
        c.post("/settings", data={"quote_term_days": bad}, environ_base=E)
        con = A.connect()
        assert con.execute("SELECT value FROM settings WHERE key='quote_term_days'").fetchone()[0] == "0"
        con.close()
    c.post("/settings", data={"quote_term_days": "10"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        assert A.quote_term() == 10
