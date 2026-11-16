import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E
import pytest
import time

@pytest.fixture(autouse=True)
def _clean_start(monkeypatch):

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("CRM_GALLERY_ROOT", raising=False)
    time.tzset()

def _payments_table(page):

    m = re.search(r"<h2>Payments</h2>\s*<table>(.*?)</table>", page, re.S)
    return re.sub(r"\s+", " ", m.group(1)) if m else "(no Payments table; page starts: " + page[:300] + ")"

def _line(c, sid, amount, label="Session"):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": label, "amount": amount}, environ_base=E)

def _pay(c, sid, amount, kind="deposit", **more):
    return c.post(f"/session/{sid}/money", data={"what": "payment", "kind": kind, "amount": amount, "method": "cash", **more},
                  environ_base=E)

def _tot(A, sid):
    with A.app.test_request_context():
        A.db()
        return A.totals(sid)

def _page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def _number(A, sid, kind):
    r = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind))
    return r[0][0] if r else None

def _html_pdfs(A, monkeypatch):
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)

def test_an_issued_invoice_stays_as_issued_after_later_payments(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _html_pdfs(A, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "100")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    num = _number(A, sid, "INV")
    assert num and _q(A, "SELECT 1 FROM bill_snapshots WHERE number=?", (num,))
    before = _page(c, f"/session/{sid}/invoice")
    _pay(c, sid, "200", kind="balance", received="2026-01-15")
    after = _page(c, f"/session/{sid}/invoice")
    assert num in after and "$200" in after and "Jan 15" not in after.split('class="page"')[1]
    assert re.search(r"Balance due</b></td><td class=\"n\"><b>\$200<", after)
    assert "It has since been paid in full." in after
    pdf = c.get(f"/session/{sid}/invoice.pdf", environ_base=E).get_data(as_text=True)
    assert num in pdf and re.search(r"Balance due</b></td><td class=\"n\"><b>\$200<", pdf) and "DRAFT" not in pdf

    assert re.search(r"<td>Date</td><td>([^<]+)</td>", before).group(1) == re.search(r"<td>Date</td><td>([^<]+)</td>", after).group(1)

def test_a_price_change_after_issue_retires_the_invoice_for_a_new_number(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    first = _number(A, sid, "INV")
    _line(c, sid, "50", label="Extra prints")
    assert _number(A, sid, "INV") is None
    assert _q(A, "SELECT 1 FROM doc_numbers WHERE session_id=? AND kind=?", (sid, "INV-replaced:" + first))
    assert _q(A, "SELECT 1 FROM audit WHERE action LIKE ?", (f"Invoice {first} was already issued and its lines changed%",))
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    page = _page(c, f"/session/{sid}/invoice")
    second = _number(A, sid, "INV")
    assert second != first and "Replaces" in page and first in page and "$350" in page

def test_an_issued_receipt_stays_as_issued_after_a_refund(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "300", kind="balance")
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    num = _number(A, sid, "R")
    _pay(c, sid, "50", kind="refund")
    page = _page(c, f"/session/{sid}/receipt")
    assert num in page and "refund" not in page.split('class="page"')[1].lower().replace("refunds", "")
    assert re.search(r"Total paid</b></td><td class=\"n\"><b>\$300<", page), (
        f"receipt {num} payments: {_payments_table(page)}; payments recorded: "
        f"{_q(A, 'SELECT kind, amount, received, voided FROM payments WHERE session_id=?', (sid,))}; "
        f"snapshots: {_q(A, 'SELECT number, at FROM bill_snapshots WHERE session_id=?', (sid,))}; "
        f"TZ={os.environ.get('TZ')!r} today={A.today()}")

def test_a_refund_is_money_back_and_never_lowers_the_quote(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "2800")
    _pay(c, sid, "840")
    _pay(c, sid, "100", kind="refund")
    t = _tot(A, sid)
    assert t["quoted"] == 2800 and round(t["paid"], 2) == 740 and round(t["due"], 2) == 1960
    assert not _q(A, "SELECT 1 FROM lines WHERE session_id=? AND label='Refund'", (sid,))
    page = _page(c, f"/session/{sid}")
    assert "$100 refunded, not owed again" in page
    inv = _page(c, f"/session/{sid}/invoice")
    services = inv.split("<h2>Services</h2>")[1].split("<h2>Payments</h2>")[0]
    assert "Refund" not in services and "$2,800" in services

    month = _q(A, "SELECT substr(received, 1, 7) FROM payments LIMIT 1")[0][0]
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    st = _page(c, f"/client/1/statement?m={month}")
    assert "$1,960" in st

def test_a_full_refund_of_an_overpaid_session_leaves_the_price_and_nothing_owed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "45")
    _pay(c, sid, "50", kind="balance")
    _pay(c, sid, "50", kind="refund")
    t = _tot(A, sid)
    assert t["quoted"] == 45 and abs(t["paid"]) < 0.005 and abs(t["due"]) < 0.005 and t["refund_credit"] == 45
    rid = _q(A, "SELECT id FROM payments WHERE kind='refund'")[0][0]
    c.post(f"/money/payment/{rid}/delete", data={"set": "void"}, environ_base=E)
    assert abs(_tot(A, sid)["due"] + 5) < 0.005

def test_refunds_recorded_the_old_way_fold_into_credits(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "100")
    con = A.connect()
    pid = con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (?, 'refund', -40, '2026-11-01')",
                      (sid,)).lastrowid
    con.execute("INSERT INTO lines (session_id, label, amount, kind, taxable, fee_key) VALUES (?, 'Refund', -40, 'credit', 0, ?)",
                (sid, f"refund:{pid}"))
    con.commit()
    con.close()
    assert _tot(A, sid)["quoted"] == 260 and round(_tot(A, sid)["due"], 2) == 200
    con = A.connect()
    A.MIGRATIONS[3](con)
    con.commit()
    con.close()
    t = _tot(A, sid)
    assert t["quoted"] == 300 and round(t["due"], 2) == 200 and t["refund_credit"] == 40
    assert not _q(A, "SELECT 1 FROM lines WHERE fee_key LIKE 'refund:%'")

def test_deleting_a_refunded_session_names_its_issued_documents(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "45")
    _pay(c, sid, "45", kind="balance")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    inv, rec = _number(A, sid, "INV"), _number(A, sid, "R")
    _pay(c, sid, "45", kind="refund")
    page = _page(c, f"/session/{sid}")
    assert f"Issued documents {inv}, {rec} are kept in History" in page
    assert c.post(f"/session/{sid}/delete", environ_base=E).status_code == 302
    assert not _q(A, "SELECT 1 FROM sessions WHERE id=?", (sid,))
    note = _q(A, "SELECT note FROM audit WHERE action LIKE 'Deleted the%session%'")[0][0]
    assert inv in note and rec in note
    other = _new(A, c, "family", subject_name="Ben")
    _line(c, other, "100")
    c.post(f"/session/{other}/invoice/issue", environ_base=E)
    assert _number(A, other, "INV") != inv and _number(A, other, "INV").endswith("-0002")

def test_issue_is_the_primary_button_and_says_what_it_issued(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    page = _page(c, f"/session/{sid}/invoice")
    assert 'class="primary">Issue this invoice' in page
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    num = _number(A, sid, "INV")
    page = _page(c, f"/session/{sid}/invoice")
    assert f"Invoice {num} issued." in page
    assert f"Invoice {num} issued." not in _page(c, f"/session/{sid}/invoice")
    n_lines = len(_q(A, "SELECT 1 FROM audit WHERE action='Issued the invoice'"))
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    assert len(_q(A, "SELECT 1 FROM audit WHERE action='Issued the invoice'")) == n_lines == 1

def test_download_print_and_email_on_a_draft_issue_it_first(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    page = _page(c, f"/session/{sid}/invoice")
    for then in ("download", "print", "email"):
        assert f'name="then" value="{then}"' in page
    r = c.post(f"/session/{sid}/invoice/issue", data={"then": "download"}, environ_base=E)
    assert r.status_code == 302 and r.location.endswith(f"/session/{sid}/invoice")
    num = _number(A, sid, "INV")
    page = _page(c, f"/session/{sid}/invoice")
    assert num and 'data-then="download"' in page and f"Invoice {num} issued." in page
    assert 'data-then' not in _page(c, f"/session/{sid}/invoice")
    js = open(os.path.join(ROOT, "static", "ui.js")).read()
    assert "[data-did-output][data-sends]" in js

def test_a_draft_pdf_says_draft_and_has_no_number(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _html_pdfs(A, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "300", kind="balance")
    for kind in ("invoice", "receipt"):
        pdf = c.get(f"/session/{sid}/{kind}.pdf", environ_base=E).get_data(as_text=True)
        assert "DRAFT" in pdf and "Draft, not issued" in pdf and not re.search(r"(INV|R)-\d{4}-\d{4}", pdf)
    assert not _q(A, "SELECT 1 FROM doc_numbers")

def test_the_receipt_page_is_titled_receipt_before_it_is_due(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "100")
    r = c.get(f"/session/{sid}/receipt", environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "<title>Receipt" in page and "Nothing to receipt yet" in page and "$200 still to pay" in page

def test_paid_in_full_closes_the_send_invoice_step(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    with A.app.test_request_context():
        A.db()
        A.x("INSERT INTO tasks (session_id, stage, title) VALUES (?, 'shot', 'Send invoice')", (sid,))
    _line(c, sid, "300")
    _pay(c, sid, "100")
    assert _q(A, "SELECT done FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))[0][0] is None
    _pay(c, sid, "200", kind="balance")
    assert _q(A, "SELECT done FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))[0][0]

def test_voiding_asks_first_names_an_issued_receipt_and_history_says_voided(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "300", kind="balance")
    page = _page(c, f"/session/{sid}")
    assert 'data-confirm="Void this $300 balance?' in page
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    rec = _number(A, sid, "R")
    assert f"Receipt {rec} was already issued for this session and stays as issued." in _page(c, f"/session/{sid}")
    pid = _q(A, "SELECT id FROM payments WHERE session_id=?", (sid,))[0][0]
    c.post(f"/money/payment/{pid}/delete", data={"set": "void"}, environ_base=E)
    acts = [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=?", (sid,))]
    assert any(a.startswith("Voided the $300 balance") for a in acts) and not any(a.startswith("Deleted payment") for a in acts)
    c.post(f"/money/payment/{pid}/delete", data={"set": "restore"}, environ_base=E)
    assert any(r[0].startswith("Restored the $300 balance") for r in _q(A, "SELECT action FROM audit WHERE session_id=?", (sid,)))

def test_a_payment_over_the_balance_is_recorded_with_a_warning(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "45")
    page = _page(c, f"/session/{sid}")
    assert 'data-balance="45.00"' in page
    _pay(c, sid, "50", kind="balance")
    assert round(_tot(A, sid)["paid"], 2) == 50
    page = _page(c, f"/session/{sid}")
    assert "$5 more than the balance" in page and "$5 credit" in page
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    rec = _page(c, f"/session/{sid}/receipt")
    assert "Credit due to you" in rec and "$5" in rec

def test_no_empty_checks_line_without_a_business_name(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    with A.app.test_request_context():
        A.db()
        for k in ("business_name", "photographer_name"):
            A.x("INSERT INTO settings (key, value) VALUES (?, '') ON CONFLICT(key) DO UPDATE SET value=''", (k,))
    monkeypatch.setattr(A.C, "PAYMENT_METHODS", ["cash", "check"])
    page = _page(c, f"/session/{sid}/invoice")
    assert "made out to ." not in page and "made out to  " not in page
    with A.app.test_request_context():
        A.db()
        A.x("UPDATE settings SET value='Jo Rivera' WHERE key='photographer_name'")
    assert "Checks are made out to Jo Rivera" in _page(c, f"/session/{sid}/invoice")
