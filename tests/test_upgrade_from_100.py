import hmac
import re
import json
import shutil
from pathlib import Path

import pytest

from helpers import fresh_modules, signed_in_client

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "db-1.0.0"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json")
E = {"REMOTE_ADDR": "127.0.0.1"}

def _place(tmp_path, db_name="crm.db"):

    shutil.copyfile(FIXTURE / db_name, tmp_path / "crm.db")
    for name in SIDE_FILES:
        if (FIXTURE / name).exists():
            shutil.copyfile(FIXTURE / name, tmp_path / name)

def _open(tmp_path, monkeypatch):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    from vault import backup as B
    taken = []
    monkeypatch.setattr(B, "sealed_backup", lambda *a, **kw: taken.append(kw.get("kind")))
    c = signed_in_client(A)
    return A, c, taken

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _in_request(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def _finances(A, c, year):
    seen = {}
    real = A.render_template

    def capture(name, **kw):
        if name == "money.html":
            seen.update(kw)
        return real(name, **kw)
    A.render_template = capture
    try:
        assert c.get(f"/money?year={year}", environ_base=E).status_code == 200
    finally:
        A.render_template = real
    return dict(total=round(seen["total"], 2), refunds=round(seen["refunds"], 2), tax_due=round(seen["tax_due"], 2),
                received={r["type"]: round(r["v"], 2) for r in seen["received"]},
                by_month={r["mo"]: round(r["v"], 2) for r in seen["by_month"]},
                outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["outstanding"] + seen.get("to_invoice", [])},
                upcoming={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["upcoming"]})

@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    _place(tmp_path)
    return _open(tmp_path, monkeypatch) + (tmp_path,)

def test_the_fixture_is_a_1_0_0_database():
    assert EXPECTED["schema_version"] == 2 and EXPECTED["made_with"].startswith("1.0.0")

def test_upgrade_takes_a_backup_and_reaches_the_current_version(upgraded):
    A, c, taken, _ = upgraded
    assert A.SCHEMA_VERSION == 9
    assert taken == ["upgrade"]
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION

def test_every_record_is_still_there(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, name FROM households")} == EXPECTED["clients"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == {
        k: v["stage"] for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4]) for r in
            _q(A, "SELECT id, session_id, kind, amount, received FROM payments")} == EXPECTED["payments"]
    ev = EXPECTED["event"]
    assert [r[0] for r in _q(A, """SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id
                                   WHERE s.event_id=? ORDER BY h.name""", (ev["id"],))] == ev["families"]
    for sid in EXPECTED["sessions"]:
        assert c.get(f"/session/{sid}/edit", environ_base=E).status_code == 200

def test_refunds_become_credits_and_balances_match_1_0_0(upgraded):
    A, c, taken, _ = upgraded
    assert not _q(A, "SELECT 1 FROM lines WHERE fee_key LIKE 'refund:%'")
    credits = {str(r[0]): (r[1], r[2]) for r in _q(A, "SELECT payment_id, session_id, amount FROM refund_credits")}
    assert credits == {v["fee_key"].split(":")[1]: (v["session"], -v["amount"]) for v in EXPECTED["refund_lines"].values()}
    for sid, want in EXPECTED["sessions"].items():
        t = _in_request(A, lambda: A.totals(int(sid)))
        assert round(t["quoted"], 2) == want["quoted_without_refund_lines"], sid
        assert round(t["paid"], 2) == want["paid"], sid
        assert round(t["due"], 2) == want["due"], sid
    lane = str(EXPECTED["sessions_by_name"]["lane"])
    assert EXPECTED["sessions"][lane]["quoted"] < EXPECTED["sessions"][lane]["quoted_without_refund_lines"]

def test_finances_match_1_0_0(upgraded):
    A, c, taken, _ = upgraded
    assert _finances(A, c, EXPECTED["year"]) == EXPECTED["finances"]

def test_invoice_numbers_are_kept_and_the_next_one_follows(upgraded):
    A, c, taken, _ = upgraded
    have = {r[0]: r[1] for r in _q(A, "SELECT number, session_id FROM doc_numbers WHERE kind='INV'")}
    assert have == EXPECTED["invoice_numbers"]
    lane = EXPECTED["sessions_by_name"]["lane"]
    assert c.get(f"/session/{lane}/invoice", environ_base=E).status_code == 200

    assert {str(r[0]): r[1] for r in _q(A, "SELECT session_id, number FROM invoice_sent")} == EXPECTED["invoice_sent"]
    assert {r[0] for r in _q(A, "SELECT number FROM doc_numbers WHERE kind='INV'")} == set(EXPECTED["invoice_numbers"])
    ortiz = EXPECTED["sessions_by_name"]["ortiz"]
    assert c.post(f"/session/{ortiz}/invoice/issue", environ_base=E).status_code in (200, 302, 303)
    new = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (ortiz,))[0][0]
    same_year = [int(n[-4:]) for n in EXPECTED["invoice_numbers"] if n[:9] == new[:9]]
    assert new not in EXPECTED["invoice_numbers"] and int(new[-4:]) == max(same_year, default=0) + 1

def test_the_1_0_0_deletion_is_signed_and_its_ids_are_not_reused(upgraded):
    A, c, taken, tmp_path = upgraded
    gone = EXPECTED["deleted"]
    assert not _q(A, "SELECT 1 FROM households WHERE id=?", (gone["client"],))
    items = json.loads((tmp_path / "deletions.json").read_text())
    assert [d["id"] for d in items] == [gone["client"]]
    key = _in_request(A, lambda: A._deletion_key(A.db()))
    assert hmac.compare_digest(items[0]["mac"], A._deletion_mac(key, items[0]))
    assert not (tmp_path / "deletions.unverified.json").exists()
    assert _in_request(A, A.unconfirmed_deletions) == []
    hid = _in_request(A, lambda: A.next_id("households"))
    sid = _in_request(A, lambda: A.next_id("sessions"))
    assert hid > gone["client"] and str(hid) not in EXPECTED["clients"]

    assert sid == max(int(k) for k in EXPECTED["sessions"]) + 1 and str(sid) not in EXPECTED["sessions"]
    assert gone["session"] == sid

def test_an_older_1_0_0_backup_restored_after_the_delete_asks_first(tmp_path, monkeypatch):

    _place(tmp_path, "crm-before-delete.db")
    A, c, taken = _open(tmp_path, monkeypatch)
    gone = EXPECTED["deleted"]
    assert taken == ["upgrade"] and _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert _q(A, "SELECT 1 FROM households WHERE id=?", (gone["client"],))
    assert [r["id"] for r in _in_request(A, A.unconfirmed_deletions)] == [gone["client"]]
    assert _q(A, "SELECT 1 FROM audit WHERE household_id=? AND action LIKE 'Not deleted:%'", (gone["client"],))

def test_restoring_the_1_0_0_backup_after_upgrading_upgrades_it_again(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    ortiz = EXPECTED["sessions_by_name"]["ortiz"]
    c.post(f"/session/{ortiz}/invoice/issue", environ_base=E)
    issued = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (ortiz,))[0][0]
    shutil.copyfile(FIXTURE / "crm.db", tmp_path / "crm.db")
    for extra in ("crm.db-wal", "crm.db-shm"):
        (tmp_path / extra).unlink(missing_ok=True)
    A, c, taken = _open(tmp_path, monkeypatch)
    assert taken == ["upgrade"] and _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert not _q(A, "SELECT 1 FROM lines WHERE fee_key LIKE 'refund:%'")
    lane = str(EXPECTED["sessions_by_name"]["lane"])
    t = _in_request(A, lambda: A.totals(int(lane)))
    assert round(t["quoted"], 2) == EXPECTED["sessions"][lane]["quoted_without_refund_lines"]
    assert round(t["due"], 2) == EXPECTED["sessions"][lane]["due"]
    c.post(f"/session/{ortiz}/invoice/issue", environ_base=E)
    again = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (ortiz,))[0][0]
    assert again != issued and again not in EXPECTED["invoice_numbers"]

def test_version_2_code_refuses_the_upgraded_database(upgraded, monkeypatch):
    A, c, taken, _ = upgraded
    monkeypatch.setattr(A, "SCHEMA_VERSION", 2)
    with pytest.raises(A.SchemaTooNew):
        A.init_db()

def _services(html):

    part = html.split("<h2>Services</h2>", 1)[1].split("<h2>Payments</h2>", 1)[0]
    rows = re.findall(r'<tr><td>(.*?)</td><td class="n">(.*?)</td></tr>', part, re.S)
    total = [a for lab, a in rows if lab == "<b>Total</b>"]
    return [(lab, a) for lab, a in rows if lab != "<b>Total</b>"], total[0].replace("<b>", "").replace("</b>", "")

def _balance_due(html):
    part = html.split("<h2>Payments</h2>", 1)[1]
    return re.search(r'<b>Balance due</b></td><td class="n"><b>(.*?)</b>', part).group(1)

def test_the_1_0_0_invoice_reads_as_1_0_0_issued_it(upgraded):

    A, c, taken, _ = upgraded
    lane = EXPECTED["sessions_by_name"]["lane"]
    want = EXPECTED["sessions"][str(lane)]
    number = [n for n, s in EXPECTED["invoice_numbers"].items() if s == lane][0]
    assert not _q(A, "SELECT 1 FROM bill_snapshots WHERE number=?", (number,))

    refund = [v for v in EXPECTED["refund_lines"].values() if v["session"] == lane]
    assert [v["amount"] for v in refund] == [-40.0]
    stored = [(r[0], r[1]) for r in _q(A, """SELECT label, amount FROM lines WHERE session_id=?
                                              ORDER BY kind IN ('discount','tax'), kind='tax', id""", (lane,))]
    as_1_0_0 = stored + [("Refund", v["amount"]) for v in refund]
    assert round(sum(a for _, a in as_1_0_0), 2) == want["quoted"] == 310.0
    money = A.money
    page = c.get(f"/session/{lane}/invoice", environ_base=E)
    assert page.status_code == 200
    data, html = _q(A, "SELECT data, html FROM bill_snapshots WHERE number=?", (number,))[0]
    data = json.loads(data)
    assert [(l["label"], l["amount"]) for l in data["lines"]] == as_1_0_0
    assert data["total"] == want["quoted"] and round(data["paid"], 2) == want["paid"] and data["balance"] == want["due"]
    assert [p["amount"] for p in data["payments"]] == [100.0, 250.0, -40.0]

    assert round(data["total"] - sum(p["amount"] for p in data["payments"]), 2) == data["balance"] == 0
    again = c.get(f"/session/{lane}/invoice", environ_base=E).get_data(as_text=True)
    for doc in (html, page.get_data(as_text=True), again):
        lines, total = _services(doc)
        assert lines == [(lab, money(a)) for lab, a in as_1_0_0] and total == money(310) == "$310.00"
        assert _balance_due(doc) == "$0.00"
    assert number in html and ("Refund", "-$40.00") in _services(html)[0]

    t = _in_request(A, lambda: A.totals(lane))
    assert round(t["quoted"], 2) == want["quoted_without_refund_lines"] and round(t["due"], 2) == want["due"]
    assert _finances(A, c, EXPECTED["year"]) == EXPECTED["finances"]
    assert round(_in_request(A, lambda: A._charge_target(lane))[0], 2) == want["quoted_without_refund_lines"]
    _in_request(A, lambda: A.sync_charges(lane))
    assert round(sum(r[0] for r in _q(A, "SELECT amount FROM charges WHERE session_id=?", (lane,))), 2) == \
        want["quoted_without_refund_lines"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT session_id, number FROM invoice_sent")} == EXPECTED["invoice_sent"]

    assert c.post(f"/session/{lane}/invoice/issue", environ_base=E).status_code in (200, 302, 303)
    assert _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (lane,))[0][0] == number
    assert _q(A, "SELECT html FROM bill_snapshots WHERE number=?", (number,))[0][0] == html

def test_an_invoice_issued_after_the_upgrade_has_no_1_0_0_refund_line(upgraded):

    A, c, taken, _ = upgraded
    lane = EXPECTED["sessions_by_name"]["lane"]
    con = A.connect()
    try:
        con.execute("DELETE FROM invoice_sent WHERE session_id=?", (lane,))
        con.execute("DELETE FROM doc_numbers WHERE session_id=?", (lane,))
        con.commit()
    finally:
        con.close()
    assert c.post(f"/session/{lane}/invoice/issue", environ_base=E).status_code in (200, 302, 303)
    number = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (lane,))[0][0]
    data = json.loads(_q(A, "SELECT data FROM bill_snapshots WHERE number=?", (number,))[0][0])
    assert "Refund" not in [l["label"] for l in data["lines"]] and "refund_lines_1_0_0" not in data
    assert data["total"] == EXPECTED["sessions"][str(lane)]["quoted_without_refund_lines"]
