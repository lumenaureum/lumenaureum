import os
import re
import sys
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch, stype="family"):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_personal": "1"}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Lane', 'adult', 'avery@example.com')")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, stype)
    return A, c, sid

def _get(c, url):
    return unquote(c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True))

def _priced(A, c, sid, free):
    with A.app.test_request_context():
        A.db()
        key = A.current_schedule()["items"][0]["key"]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": key, "qty": "1"}, environ_base=E)
    if free:
        c.post(f"/session/{sid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)

def test_personal_project_with_no_quote_reads_no_charge(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch, "portfolio")
    page = _get(c, f"/session/{sid}")
    assert "Billing: no quote yet" not in page and "No charge" in page
    edit = _get(c, f"/session/{sid}/edit").split('id="money"')[1].split("Quote lines")[0]
    assert "No charge" in edit and "Balance" not in edit and "Paid" not in edit
    inv = _get(c, f"/session/{sid}/invoice")
    assert "so it needs no invoice" in inv and "Add the session's prices first" not in inv
    rec = _get(c, f"/session/{sid}/receipt")
    assert "so it needs no receipt" in rec

def test_a_priced_type_with_no_quote_is_still_not_priced_yet(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    page = _get(c, f"/session/{sid}")
    assert "No quote yet" in page and "No charge" not in page
    with A.app.test_request_context():
        A.db()
        assert not A.nothing_to_pay(sid)

def _numbers(A, sid):
    con = A.connect()
    r = con.execute("SELECT kind, number FROM doc_numbers WHERE session_id=?", (sid,)).fetchall()
    con.close()
    return [tuple(x) for x in r]

def test_no_charge_invoice_is_issued_only_when_asked(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    _priced(A, c, sid, free=True)
    for st in ("quoted", "booked", "shot"):
        c.post(f"/session/{sid}/stage", data={"stage": st}, environ_base=E)
    page = _get(c, f"/session/{sid}")
    assert "Issue a no-charge invoice" in page and "Send the invoice" not in page
    with A.app.test_request_context():
        A.db()
        assert "Send invoice" not in [st_["label"] for g_ in A.workflow(sid)["stages"] for st_ in g_["steps"]]
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    assert _numbers(A, sid) == []
    back = f"/client/1/stage?sid={sid}"
    r = c.post(f"/session/{sid}/invoice/issue", data={"no_charge": "1", "back": back}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(back)
    (kind, number), = _numbers(A, sid)
    assert kind == "INV" and number
    page = _get(c, f"/session/{sid}")
    assert f"No-charge invoice {number}" in page and "Issue a no-charge invoice" not in page
    inv = _get(c, f"/session/{sid}/invoice")
    total = inv.split("<b>Total</b>")[1][:80]
    assert "$0.00" in total and "There is nothing to pay for this session." in inv
    assert not re.search(r"How to pay|Balance due|<h2>Payments</h2>|Paid in full|Deposit due", inv)
    rec = _get(c, f"/session/{sid}/receipt")
    assert "so it needs no receipt" in rec
    c.post(f"/session/{sid}/invoice/issue", data={"no_charge": "1", "back": back}, environ_base=E)
    assert len(_numbers(A, sid)) == 1

def test_no_charge_invoice_takes_the_next_number_in_the_run(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    _priced(A, c, sid, free=False)
    with A.app.test_request_context():
        A.db()
        free_sid = A.create_session(1, "family")
    _priced(A, c, free_sid, free=True)
    for x_ in (sid, free_sid):
        for st in ("quoted", "booked", "shot"):
            c.post(f"/session/{x_}/stage", data={"stage": st}, environ_base=E)
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    c.post(f"/session/{free_sid}/invoice/issue", data={"no_charge": "1"}, environ_base=E)
    (_, n1), = _numbers(A, sid)
    (_, n2), = _numbers(A, free_sid)
    assert n1 != n2 and re.sub(r"\d+$", "", n1) == re.sub(r"\d+$", "", n2)
    assert int(re.search(r"\d+$", n2).group()) == int(re.search(r"\d+$", n1).group()) + 1

def test_a_priced_session_is_offered_no_no_charge_invoice(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    _priced(A, c, sid, free=False)
    for st in ("quoted", "booked", "shot"):
        c.post(f"/session/{sid}/stage", data={"stage": st}, environ_base=E)
    assert "no-charge invoice" not in _get(c, f"/session/{sid}")
    c.post(f"/session/{sid}/invoice/issue", data={"no_charge": "1"}, environ_base=E)
    inv = _get(c, f"/session/{sid}/invoice")
    assert "Balance due" in inv and "No charge" not in inv

def test_trivy_action_is_pinned_to_its_commit():
    ci = open(os.path.join(ROOT, ".github", "workflows", "ci.yml")).read()
    pins = re.findall(r"aquasecurity/trivy-action@([0-9a-f]+)\s+# (v[\d.]+)", ci)
    assert pins and all(p == ("ed142fd0673e97e23eac54620cfb913e5ce36c25", "v0.36.0") for p in pins)
    assert "a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8" not in ci

def _agreement(c, sid):
    page = _get(c, f"/session/{sid}/agreement")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.split("<h1>")[1])).replace("&#39;", "'")

def _cannot_shoot(tmp_path, monkeypatch, free):

    A, c, sid = _app(tmp_path, monkeypatch)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_personal": "1", "preset_weddings": "1"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        wid = A.create_session(1, "wedding")
    c.post(f"/session/{wid}/money", data={"what": "line", "label": "Wedding day", "amount": "2000"}, environ_base=E)
    if free:
        c.post(f"/session/{wid}/quote/discount", data={"pct": "100", "reason": ""}, environ_base=E)
    return _agreement(c, wid)

def test_agreement_for_a_free_session(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    _priced(A, c, sid, free=True)
    a = _agreement(c, sid)
    assert "Price No charge" in a and "Payment No payment due" in a and "Balance " not in a.split("1. Who")[0]
    assert "One free reschedule" in a and "Illness on either side is always a free reschedule." in a
    assert "deposit is not refunded" not in a and "per reschedule" not in a and "$50" not in a and "I keep the deposit" not in a
    assert "Once the balance is paid" not in a and "I keep the copyright. You, and the people photographed, receive" in a
    assert "I refund everything you have paid" not in a and "I will offer a new date." in a
    assert "refund the part of the price" not in a and "In no case will I owe you more" not in a
    assert "I will reshoot at no charge if that is possible. This is your only remedy against me." in a
    assert "venue fees are charged at what they cost me" in a

def test_agreement_for_a_priced_session_is_unchanged(tmp_path, monkeypatch):
    A, c, sid = _app(tmp_path, monkeypatch)
    _priced(A, c, sid, free=False)
    a = _agreement(c, sid)
    assert "No payment due" not in a and "No charge" not in a and "Balance " in a.split("1. Who")[0]
    assert "If you cancel, I keep the deposit for that reason." in a and "per reschedule, which I may waive" in a
    assert "I keep the deposit" in a and "Once the balance is paid, you, and the people photographed" in a
    assert "If no new date works for you, I refund everything you have paid." in a

def test_agreement_for_a_free_fixed_date_session_drops_the_full_refund(tmp_path, monkeypatch):
    free = _cannot_shoot(tmp_path, monkeypatch, True)
    assert "I will send a qualified photographer in my place." in free and "I refund everything you have paid" not in free
    assert "if you prefer" not in free and "your date can move, we pick a new date" in free
    assert "Payment No payment due" in free and "the deposit carries over" not in free

def test_agreement_for_a_priced_fixed_date_session_is_unchanged(tmp_path, monkeypatch):
    priced = _cannot_shoot(tmp_path, monkeypatch, False)
    assert ("If I cannot find one, I refund everything you have paid; or, if you prefer and your date can move, we pick"
            " a new date.") in priced and "No payment due" not in priced

def test_paramiko_license_is_lgpl_2_1_or_later():
    import about
    from email.message import Message
    m = Message()
    m["Name"] = "paramiko"
    m["License-Expression"] = "LGPL-2.1"
    assert about.license_of(m) == "LGPL-2.1-or-later"
    other = Message()
    other["Name"] = "somepkg"
    other["License-Expression"] = "LGPL-2.1"
    assert about.license_of(other) == "LGPL-2.1-only"
    try:
        from importlib import metadata
        d = metadata.distribution("paramiko")
    except Exception:
        return
    assert about.license_of(d.metadata) == "LGPL-2.1-or-later"
