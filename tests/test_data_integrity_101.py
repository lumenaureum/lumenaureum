"""1.0.1 data-integrity fixes: a stale tab never silently overwrites a session or client (130); quote numbers are never
reused after a delete, and opening an invoice or receipt uses up no number (131); a session holding payments can't be
deleted out from under Finances (132)."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E  # noqa: E402

CLASH = "was changed in another tab or window. Your edits were not saved; reload to see the latest."


def _seen(c, url):
    page = c.get(url, environ_base=E).get_data(as_text=True)
    return re.search(r'name="seen" value="([0-9a-f]+)"', page).group(1)


def _line(c, sid, amount="300"):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": amount}, environ_base=E)


# ---------------------------------------------------------------- 130: two tabs

def test_a_stale_session_tab_is_refused_and_keeps_what_was_typed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    form = {"title": "", "type": "family", "shoot_date": "2027-12-04", "start_time": "", "stops": "", "notes": ""}
    c.post(f"/session/{sid}", data=form, environ_base=E)
    tab_a = _seen(c, f"/session/{sid}")
    tab_b = _seen(c, f"/session/{sid}")
    r = c.post(f"/session/{sid}", data={**form, "seen": tab_b, "shoot_date": "2027-12-05", "start_time": "15:30",
                                        "notes": "gate code 9911"}, environ_base=E)
    assert r.status_code == 302
    r = c.post(f"/session/{sid}", data={**form, "seen": tab_a, "title": "TAB A title"}, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 409 and "This session " + CLASH in page
    assert "TAB A title" in page                                    # what was typed is shown, not lost
    row = _q(A, "SELECT title, shoot_date, start_time, notes FROM sessions WHERE id=?", (sid,))[0]
    assert tuple(row) == ("", "2027-12-05", "15:30", "gate code 9911")   # tab B's save stands
    assert 'value="gate code 9911"' in page or "gate code 9911</textarea>" in page   # the form shows the latest
    fresh = re.search(r'name="seen" value="([0-9a-f]+)"', page).group(1)
    r = c.post(f"/session/{sid}", data={**form, "seen": fresh, "title": "TAB A title", "shoot_date": "2027-12-05",
                                        "start_time": "15:30", "notes": "gate code 9911"}, environ_base=E)
    assert r.status_code == 302 and _q(A, "SELECT title FROM sessions WHERE id=?", (sid,))[0][0] == "TAB A title"


def test_saving_twice_from_one_tab_and_other_forms_are_not_clashes(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    seen = _seen(c, f"/session/{sid}")
    c.post(f"/session/{sid}/delivery", data={"favorites": "12, 14"}, environ_base=E)    # another form: not a clash
    _line(c, sid)
    r = c.post(f"/session/{sid}", data={"seen": seen, "title": "One", "type": "family"}, environ_base=E)
    assert r.status_code == 302
    r = c.post(f"/session/{sid}", data={"seen": _seen(c, f"/session/{sid}"), "title": "Two", "type": "family"}, environ_base=E)
    assert r.status_code == 302 and _q(A, "SELECT title FROM sessions WHERE id=?", (sid,))[0][0] == "Two"


def test_a_stale_client_tab_is_refused(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    tab_a, tab_b = _seen(c, "/client/1"), _seen(c, "/client/1")
    r = c.post("/client/1", data={"seen": tab_b, "name": "Lane", "address": "4 Mill Road", "notes": "prefers texts"},
               environ_base=E)
    assert r.status_code == 302
    r = c.post("/client/1", data={"seen": tab_a, "name": "Lane family", "address": "", "notes": ""}, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 409 and "This client " + CLASH in page and "Lane family" in page
    assert tuple(_q(A, "SELECT name, address, notes FROM households WHERE id=1")[0]) == ("Lane", "4 Mill Road", "prefers texts")


# ---------------------------------------------------------------- 131: numbers

def test_quote_numbers_are_never_reused_after_a_delete(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    first = _new(A, c, "family", subject_name="Ava")
    _line(c, first)
    c.post(f"/session/{first}/quote/send", environ_base=E)
    gone = _new(A, c, "family", subject_name="Ben")
    _line(c, gone)
    c.post(f"/session/{gone}/quote/send", environ_base=E)
    top = _q(A, "SELECT MAX(id) FROM quote_versions WHERE session_id=?", (gone,))[0][0]
    assert c.post(f"/session/{gone}/delete", environ_base=E).status_code == 302
    assert not _q(A, "SELECT 1 FROM quote_versions WHERE id=?", (top,))
    later = _new(A, c, "family", subject_name="Cole")
    _line(c, later)
    r = c.post(f"/session/{later}/quote/send", environ_base=E)
    new = _q(A, "SELECT id FROM quote_versions WHERE session_id=?", (later,))[0][0]
    assert new == top + 1 and r.location.endswith(f"/quote/{new}")
    assert json.loads((tmp_path / "doc-numbers.json").read_text())["Q"] == new


def test_quote_mark_is_seeded_from_history_at_unlock(tmp_path, monkeypatch):
    """An install from before the mark existed: the History of quotes sent still knows the highest number used."""
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid)
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions")[0][0]
    c.post(f"/session/{sid}/delete", environ_base=E)
    (tmp_path / "doc-numbers.json").write_text("{}")
    with A.app.test_request_context():
        A.db()
        A._seed_number_marks()
    assert json.loads((tmp_path / "doc-numbers.json").read_text())["Q"] == vid


def test_opening_an_invoice_uses_no_number_until_it_is_issued(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    empty = _new(A, c, "family", subject_name="Ava")
    sid = _new(A, c, "family", subject_name="Ben")
    _line(c, sid)
    for s_ in (empty, sid):
        page = c.get(f"/session/{s_}/invoice", environ_base=E).get_data(as_text=True)
        assert "Assigned when issued" in page and "Issue this invoice" in page
        assert 'href="/session/%d/invoice.pdf"' % s_ not in page    # 1.0.1: Download on a draft issues it first (a form, not a link)
    assert c.get(f"/session/{sid}/invoice.pdf", environ_base=E).status_code == 200
    assert not _q(A, "SELECT 1 FROM doc_numbers")
    c.post(f"/session/{empty}/invoice/issue", environ_base=E)       # nothing to invoice: nothing issued
    assert not _q(A, "SELECT 1 FROM doc_numbers")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)         # a second click keeps the same number
    nums = _q(A, "SELECT number FROM doc_numbers WHERE kind='INV'")
    assert len(nums) == 1 and nums[0][0].endswith("-0001")
    page = c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True)
    assert nums[0][0] in page and "Download PDF" in page and "Assigned when issued" not in page


def test_marking_an_invoice_sent_issues_it_and_receipts_wait_too(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "200")
    with A.app.test_request_context():
        A.db()
        A._invoice_marked_sent(sid)
        A._commit()
    assert _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,))
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "200"}, environ_base=E)
    page = c.get(f"/session/{sid}/receipt", environ_base=E).get_data(as_text=True)
    assert "Issue this receipt" in page and not _q(A, "SELECT 1 FROM doc_numbers WHERE kind='R'")
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    assert _q(A, "SELECT number FROM doc_numbers WHERE kind='R'")[0][0].startswith("R-")


# ---------------------------------------------------------------- 132: payments and deleting a session

def test_a_session_holding_payments_is_not_deleted(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "788.64")
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "788.64", "method": "card"},
           environ_base=E)
    r = c.post(f"/session/{sid}/delete", environ_base=E)
    assert r.status_code == 302 and r.location.endswith(f"/session/{sid}#money")
    assert _q(A, "SELECT 1 FROM sessions WHERE id=?", (sid,))
    page = c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    assert "still holds $788.64 in recorded payments" in page
    assert not _q(A, "SELECT 1 FROM audit WHERE action LIKE 'Deleted the%session%'")   # refused: no History line
    pid = _q(A, "SELECT id FROM payments WHERE session_id=?", (sid,))[0][0]
    c.post(f"/money/payment/{pid}/delete", data={"set": "void"}, environ_base=E)       # entered by mistake: void it
    assert c.post(f"/session/{sid}/delete", environ_base=E).status_code == 302
    assert not _q(A, "SELECT 1 FROM sessions WHERE id=?", (sid,))
    assert not _q(A, "SELECT 1 FROM books")                                            # nothing left in Finances


def test_a_session_refunded_in_full_can_be_deleted_and_the_books_net_to_zero(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "100"}, environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "refund", "amount": "100"}, environ_base=E)
    assert c.post(f"/session/{sid}/delete", environ_base=E).status_code == 302
    assert not _q(A, "SELECT 1 FROM sessions WHERE id=?", (sid,))
    assert abs(_q(A, "SELECT COALESCE(SUM(amount), 0) FROM books")[0][0]) < 0.005
