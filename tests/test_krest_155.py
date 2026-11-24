import os
import re
import sys
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_and_quotes import E, _app

def _free(tmp_path, monkeypatch, free=True):
    A, c, sid = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    if free:
        c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    con = A.connect()
    con.execute("UPDATE people SET email='avery@example.com' WHERE id=1")
    con.commit(); con.close()
    return A, c, sid

def _get(c, url):
    return unquote(c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True))

def _invoice_task(A, sid):
    con = A.connect()
    r = con.execute("SELECT done, outcome FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,)).fetchone()
    con.close()
    return r

def _steps(A, sid):
    with A.app.test_request_context():
        A.db()
        return [st_["label"] for g_ in A.workflow(sid)["stages"] for st_ in g_["steps"]]

def test_no_invoice_step_and_no_paid_words_on_the_client_page(tmp_path, monkeypatch):
    A, c, sid = _free(tmp_path, monkeypatch)
    c.get(f"/session/{sid}", environ_base=E)
    assert "Send invoice" not in _steps(A, sid) and tuple(_invoice_task(A, sid))[1] == "na"
    c.post(f"/session/{sid}/quote/discount", data={"pct": "0", "reason": ""}, environ_base=E)
    assert "Send invoice" in _steps(A, sid) and tuple(_invoice_task(A, sid)) == (None, None)
    c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    c.post(f"/session/{sid}/stage", data={"stage": "quoted"}, environ_base=E)
    page = _get(c, f"/session/{sid}")
    assert "No charge" in page and "$0.00, not sent yet" not in page
    for st in ("booked", "shot"):
        c.post(f"/session/{sid}/stage", data={"stage": st}, environ_base=E)
    page = _get(c, f"/session/{sid}")
    assert "Send the invoice" not in page and "$0.00 of $0.00 paid" not in page and "Quote: no charge" in page
    assert "Send invoice" not in _steps(A, sid)
    assert tuple(_invoice_task(A, sid))[1] == "na"
    con = A.connect()
    con.execute("UPDATE tasks SET due='2026-01-01' WHERE title='Send invoice'")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        assert not [t_ for t_ in A._needs_you()["tasks"] if t_["r"]["title"] == "Send invoice"]

def test_invoice_reads_no_charge_and_no_receipt_is_given(tmp_path, monkeypatch):
    A, c, sid = _free(tmp_path, monkeypatch)
    for st in ("quoted", "booked", "shot"):
        c.post(f"/session/{sid}/stage", data={"stage": st}, environ_base=E)
    inv = _get(c, f"/session/{sid}/invoice")
    assert "so it needs no invoice" in inv and "No charge" in inv and "There is nothing to pay for this session." in inv
    assert not re.search(r"Balance due|Paid in full|<h2>Payments</h2>|How to pay|Issue this invoice", inv)
    r = c.post(f"/session/{sid}/invoice/issue", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "so it needs no invoice. Nothing was issued." in r
    rec = _get(c, f"/session/{sid}/receipt")
    assert "so it needs no receipt" in rec and "Open the invoice" not in rec and "Total paid" not in rec
    r = c.post(f"/session/{sid}/receipt/issue", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "so it needs no receipt. Nothing was issued." in r
    con = A.connect()
    assert not con.execute("SELECT 1 FROM doc_numbers WHERE session_id=?", (sid,)).fetchone()
    con.close()

def test_quote_and_session_page_say_no_charge(tmp_path, monkeypatch):
    A, c, sid = _free(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/quote/send", data={"total": "0.00"}, environ_base=E)
    con = A.connect()
    vid = con.execute("SELECT id FROM quote_versions WHERE session_id=?", (sid,)).fetchone()[0]
    con.close()
    doc = _get(c, f"/quote/{vid}")
    assert "<b>No charge</b>" in doc and ": no charge." in doc and "$0.00" not in doc.split("<h2>Terms</h2>")[0].split("Total")[1]
    page = _get(c, f"/session/{sid}/edit")
    billing = page.split('id="money"')[1].split("Quote lines")[0]
    assert "No charge" in billing and "Balance" not in billing and "Paid" not in billing

def test_a_priced_session_is_unchanged(tmp_path, monkeypatch):
    A, c, sid = _free(tmp_path, monkeypatch, free=False)
    for st in ("quoted", "booked", "shot"):
        c.post(f"/session/{sid}/stage", data={"stage": st}, environ_base=E)
    assert "Send invoice" in _steps(A, sid)
    con = A.connect()
    con.execute("UPDATE tasks SET due='2026-01-01' WHERE title='Send invoice'")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        assert [t_ for t_ in A._needs_you()["tasks"] if t_["r"]["title"] == "Send invoice"]
    page = _get(c, f"/session/{sid}")
    assert "No charge" not in page and "of $" in page
    inv = _get(c, f"/session/{sid}/invoice")
    assert "No charge" not in inv and "Balance due" in inv
    page = _get(c, f"/session/{sid}/edit")
    assert "Balance" in page.split('id="money"')[1].split("Quote lines")[0]
