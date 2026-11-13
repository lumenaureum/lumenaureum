"""The money and quote screens: lines, payments, refunds, voids, fees, discounts, requotes and sending."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

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
    assert _tot(A, sid)["paid"] == 0                       # a negative amount is refused, never flipped
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "abc"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 0
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100", "method": "cash"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 100
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "refund", "amount": "40", "method": "cash"}, environ_base=E)
    assert _tot(A, sid)["paid"] == 60
    assert _tot(A, sid)["due"] == 200 and _tot(A, sid)["quoted"] == 260     # money back never makes the client owe more (E1)
    con = A.connect()
    pid = con.execute("SELECT id FROM payments WHERE session_id=? AND amount=100", (sid,)).fetchone()[0]
    rid = con.execute("SELECT id FROM payments WHERE session_id=? AND kind='refund'", (sid,)).fetchone()[0]
    lid = con.execute("SELECT id FROM lines WHERE session_id=? AND kind!='credit'", (sid,)).fetchone()[0]
    con.close()
    c.post(f"/money/payment/{pid}/delete", environ_base=E)
    assert _tot(A, sid)["paid"] == -40                     # voided: kept, out of the totals
    c.post(f"/money/payment/{pid}/delete", environ_base=E)
    assert _tot(A, sid)["paid"] == 60                      # un-voided
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM payments WHERE session_id=?", (sid,)).fetchone()[0] == 2   # never erased
    con.close()
    c.post(f"/money/payment/{rid}/delete", data={"set": "void"}, environ_base=E)
    assert _tot(A, sid)["quoted"] == 300 and _tot(A, sid)["paid"] == 100   # the refund voided: its price line goes too
    c.post(f"/money/payment/{rid}/delete", data={"set": "restore"}, environ_base=E)
    assert _tot(A, sid)["quoted"] == 260 and _tot(A, sid)["due"] == 200
    c.post(f"/money/line/{lid}/delete", environ_base=E)
    assert _tot(A, sid)["quoted"] == -40                   # the refund's own line stays until the refund is voided


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
    assert con.execute("SELECT discount_pct FROM quotes WHERE session_id=?", (sid,)).fetchone()[0] == 100   # kept in range
    con.close()
    c.post(f"/session/{sid}/quote/discount", data={"pct": "10"}, environ_base=E)
    r = c.post(f"/session/{sid}/quote/send", environ_base=E)
    assert r.status_code == 302 and "/quote/" in r.headers["Location"]
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM quote_versions WHERE session_id=?", (sid,)).fetchone()[0] == 1
    con.close()
    assert c.post(f"/session/{sid}/quote/requote", environ_base=E).status_code in (302, 303)
