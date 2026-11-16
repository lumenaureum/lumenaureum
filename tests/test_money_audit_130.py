import csv
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    real = A.datetime

    def freeze(iso):
        instant = datetime.fromisoformat(iso + "T12:00:00").replace(tzinfo=timezone.utc)

        class Frozen(real):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
        monkeypatch.setattr(A, "datetime", Frozen)
    return A, c, freeze

def run(A, sql, args=()):
    con = A.connect()
    con.executescript(sql) if not args else con.execute(sql, args)
    con.commit()
    con.close()

def one(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def post(c, url, **data):
    r = c.post(url, data=data, environ_base=E)
    assert r.status_code in (200, 302, 303, 204), (url, r.status_code)
    return r

def pay(c, sid, kind, amount, received, method="cash"):
    return post(c, f"/session/{sid}/money", what="payment", kind=kind, amount=str(amount), received=received, method=method)

def line(c, sid, label, amount, taxable=True):
    post(c, f"/session/{sid}/money", what="line", label=label, amount=str(amount), taxable="1" if taxable else "0",
         taxable_shown="1")

def tot(A, sid):
    with A.app.test_request_context():
        return dict(A.totals(sid))

def year(A, y):
    with A.app.test_request_context():
        return A.year_money(y)

def tax_of(A, y):
    return year(A, y)["tax_due"]

def stmt(A, hid, month):
    with A.app.test_request_context():
        return A.statement_ctx(hid, month)

def get_zip(c, y, raw=False):
    r = c.get(f"/money/accounts.zip?year={y}", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.data))
    if raw:
        return {n: z.read(n) for n in z.namelist()}
    return {n: list(csv.DictReader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in z.namelist()}

def reconcile(A, c, y, cents=0.01):

    ym, f = year(A, y), get_zip(c, y)
    total, months = f["summary.csv"][-1], f["summary.csv"][:-1]
    assert total["month"] == "Total"

    def same(a, b, tol=0.005):
        assert abs(float(a) - float(b)) <= tol, (y, a, b)
    same(ym["total"], total["received"])
    same(ym["tax_due"], total["sales tax"])
    same(ym["tips"], total["tips (in received, not taxed)"])
    same(ym["written_off"], total["written off (not received)"])
    same(ym["refunds"], total["refunds (in received)"])
    same(sum(float(m["received"]) for m in months), total["received"])
    same(sum(float(m["sales tax"]) for m in months), total["sales tax"], cents)
    same(sum(float(p["amount"]) for p in f["payments.csv"] if p["voided"] == "no"), total["received"])
    same(sum(float(t["sales tax"]) for t in f["sales-tax.csv"]), total["sales tax"], cents)
    same(sum(float(w["amount written off"]) for w in f["write-offs.csv"]), total["written off (not received)"])
    same(sum(float(e["amount"]) for e in f["expenses.csv"]), total["expenses"])
    same(sum(float(m["amount"] or 0) for m in f["mileage.csv"]), total["mileage"], cents)
    same(float(total["received"]) - float(total["expenses"]) - float(total["mileage"]) - float(total["sales tax"]),
         total["net, after sales tax"], 0.011)
    return (total["received"], total["sales tax"], total["tips (in received, not taxed)"],
            total["written off (not received)"], total["expenses"], total["mileage"])

def flat(A, pct="8", rate=None):
    run(A, f"INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '{pct}')")
    if rate:
        run(A, "INSERT INTO settings (key, value) VALUES ('mileage_rate', ?)", (rate,))
    A.apply_settings()

def household(A, hid, name, company=""):
    run(A, "INSERT INTO households (id, name, company) VALUES (?,?,?)", (hid, name, company))

def session(A, sid, hid, stage, shoot, stype="portrait"):
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (?,?,?,?,?)", (sid, hid, stype, stage, shoot))

def test_two_year_book_reconciles_and_last_year_holds(env):

    A, c, freeze = env
    flat(A, "8", "0.67")
    freeze("2025-10-04")
    household(A, 1, "Lane")
    household(A, 2, "Zoë Ångström-Ñúñez")
    household(A, 3, "=Okafor", "Okafor & Sons LLC")
    household(A, 4, "-Chen")
    session(A, 1, 1, "quoted", "2026-01-10")
    session(A, 2, 2, "quoted", "2026-06-20")
    session(A, 3, 3, "quoted", "2025-12-15", "headshot")
    session(A, 4, 4, "quoted", "2025-12-28", "family")
    line(c, 1, "Portrait session", 500)
    line(c, 2, "Wedding day", 3000)
    line(c, 2, "Album", 400, taxable=False)
    line(c, 3, "Team headshots", 900)
    line(c, 4, "Family session", 300)
    for sid in (1, 2, 3, 4):
        post(c, f"/session/{sid}/stage", stage="booked")
    pay(c, 1, "deposit", 200, "2025-10-01", "check")
    pay(c, 2, "deposit", 1000, "2025-10-02", "card")
    pay(c, 3, "deposit", 250, "2025-10-03")
    pay(c, 4, "deposit", 100, "2025-10-04")
    post(c, "/session/3/money", what="expense", label="Drive to the office", miles="42", amount="0", date="2025-12-15")
    post(c, "/money/overhead", label="Lens", amount="1299.99", date="2025-12-31")
    freeze("2025-12-30")
    run(A, "UPDATE sessions SET stage='shot' WHERE id IN (3, 4)")
    pay(c, 3, "balance", 722, "2025-12-30", "check")
    wrong = one(A, "SELECT MAX(id) FROM payments")[0]
    post(c, f"/money/payment/{wrong}/delete", set="void")
    pay(c, 3, "balance", 722, "2025-12-31", "check")
    pay(c, 4, "balance", 100, "2025-12-30")
    last_year = reconcile(A, c, 2025)
    assert last_year[:2] == ("2372.00", "167.55")

    freeze("2026-01-05")
    run(A, "UPDATE settings SET value='0.725' WHERE key='mileage_rate'")
    A.apply_settings()
    post(c, "/session/4/money", what="expense", label="Second trip", miles="10", amount="0", date="2026-01-05")
    post(c, "/close", sid="4", step="balance_paid", outcome="waived")
    t4 = tot(A, 4)
    assert abs(t4["due"]) < 0.005 and t4["written_off"] == 124
    freeze("2026-01-20")
    run(A, "UPDATE sessions SET stage='shot' WHERE id=1")
    pay(c, 1, "balance", 340, "2026-01-20")
    pay(c, 1, "tip", 60, "2026-01-20")
    post(c, "/session/1/quote/fee", fee_key="custom", label="Extra hour", amount="150", qty="1", taxable="1",
         taxable_shown="1")
    assert tot(A, 1)["due"] == 162
    freeze("2026-02-01")
    pay(c, 1, "balance", 162, "2026-02-01")
    freeze("2026-03-01")
    post(c, "/session/2/stage", stage="lost", lost_reason="Canceled")
    t2 = tot(A, 2)
    assert (t2["kept"], t2["refund_due"]) == (100, 900)
    assert reconcile(A, c, 2025) == last_year
    freeze("2026-03-10")
    pay(c, 2, "refund", 900, "2026-03-10")
    assert tot(A, 2)["refund_due"] == 0

    assert reconcile(A, c, 2025) == last_year
    this_year = reconcile(A, c, 2026)
    assert this_year[2:4] == ("60.00", "124.00")

    with A.app.test_request_context():
        kept_tax = sum(t_ for _p, _s, t_ in A.payment_taxes(2))
    assert round(kept_tax, 2) == round(100 * 240 / 3640, 2)
    with A.app.test_request_context():
        every = A._all_totals()
        for sid in every:
            one_ = A._totals(sid)
            assert all(abs(float(every[sid][k] or 0) - float(one_[k] or 0)) < 0.001 for k in one_), sid

def test_csv_files_open_cleanly(env):

    A, c, freeze = env
    flat(A, "8.875", "0.655")
    freeze("2026-06-01")
    household(A, 1, "@Zoë 🎉 Ñúñez")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Séance", 412.5)
    pay(c, 1, "deposit", 100, "2026-05-02")
    pay(c, 1, "refund", 25, "2026-05-03")
    post(c, "/session/1/money", what="expense", label="+Prints, «matte»", amount="40.10", miles="12.1", date="2026-05-04")
    raw = get_zip(c, 2026, raw=True)
    for name, data in raw.items():
        assert data.startswith(b"\xef\xbb\xbf"), name
        data.decode("utf-8-sig")
    f = get_zip(c, 2026)
    assert {p["client"] for p in f["payments.csv"]} == {"'@Zoë 🎉 Ñúñez"}
    assert f["expenses.csv"][0]["what"] == "'+Prints, «matte»"
    assert [p["amount"] for p in f["payments.csv"]] == ["100.00", "-25.00"]
    for name, rows in f.items():
        for r in rows:
            for k, v in r.items():
                if k in ("amount", "received", "sales tax", "taxable amount", "miles", "mileage", "expenses",
                         "amount written off", "refunds (in received)") and v:
                    float(v)
                    assert not v.startswith("'"), (name, k, v)

def test_session_money_forms_are_once_only(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    for what, extra in (("line", dict(label="Session", amount="300")), ("payment", dict(kind="deposit", amount="50")),
                        ("expense", dict(label="Prints", amount="5"))):
        for _ in range(2):
            post(c, "/session/1/money", what=what, once="tok-" + what, **extra)
    for _ in range(2):
        post(c, "/session/1/quote/fee", fee_key="custom", label="Fee", amount="20", qty="1", once="tok-fee")
    assert one(A, "SELECT COUNT(*) FROM payments")[0] == 1
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 1
    assert one(A, "SELECT COUNT(*) FROM lines WHERE kind='custom'")[0] == 2

def test_statement_chains_month_to_month(env):
    A, c, freeze = env
    flat(A, "6")
    freeze("2026-01-05")
    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-02-10")
    session(A, 2, 1, "booked", "2026-04-02")
    line(c, 1, "Portrait", 500)
    line(c, 2, "Family", 300)
    pay(c, 1, "deposit", 100, "2026-01-05")
    post(c, "/session/1/invoice/issue")
    freeze("2026-02-20")
    run(A, "UPDATE sessions SET stage='shot' WHERE id=1")
    pay(c, 1, "balance", 430, "2026-02-20")
    pay(c, 1, "tip", 40, "2026-02-20")
    freeze("2026-04-03")
    run(A, "UPDATE sessions SET stage='shot' WHERE id=2")
    pay(c, 2, "balance", 318, "2026-04-03")
    prev = None
    for m in ("2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        v = stmt(A, 1, m)
        if prev is not None:
            assert abs(prev - v["opening"]) < 0.005, m
        prev = v["closing"]
    assert abs(prev) < 0.005

def test_price_change_after_booking_leaves_last_year_alone(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2025-12-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-01-20")
    line(c, 1, "Session", 500)
    post(c, "/session/1/stage", stage="booked")
    freeze("2025-12-10")
    pay(c, 1, "deposit", 200, "2025-12-10")
    before = tax_of(A, 2025)
    assert before == 14.81
    freeze("2026-01-21")
    post(c, "/session/1/quote/fee", fee_key="custom", label="Album", amount="300", qty="1", taxable="0", taxable_shown="1")
    assert tax_of(A, 2025) == before

def test_reopening_a_canceled_booking_next_year_leaves_last_year_alone(env):
    A, c, freeze = env
    flat(A, "6")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-12-01")
    line(c, 1, "Session", 500)
    post(c, "/session/1/stage", stage="booked")
    freeze("2026-03-01")
    pay(c, 1, "balance", 530, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    filed = tax_of(A, 2026)
    assert filed == 5.66
    freeze("2027-01-15")
    post(c, "/session/1/stage", stage="booked")
    assert tax_of(A, 2026) == filed

def test_correcting_the_lost_reason_next_year_leaves_last_year_alone(env):
    A, c, freeze = env
    flat(A, "6")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-12-01")
    line(c, 1, "Session", 500)
    post(c, "/session/1/stage", stage="booked")
    freeze("2026-03-01")
    pay(c, 1, "balance", 530, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Price")
    filed = tax_of(A, 2026)
    assert filed == 5.66
    freeze("2027-01-10")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    assert tax_of(A, 2026) == filed and tax_of(A, 2027) == 0

def canceled_and_refunded(A, c, freeze):
    flat(A, "6")
    freeze("2026-02-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-12-01")
    line(c, 1, "Session", 500)
    run(A, """INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-02-01', '{"deposit": 100, "total": 530}')""")
    post(c, "/session/1/stage", stage="booked")
    freeze("2026-03-01")
    pay(c, 1, "balance", 530, "2026-03-01")
    freeze("2026-04-01")
    post(c, "/session/1/stage", stage="lost", lost_reason="Canceled")
    freeze("2026-05-10")
    pay(c, 1, "refund", 430, "2026-05-10")
    t = tot(A, 1)
    assert (t["kept"], t["refund_due"]) == (100, 0)

def test_statement_after_the_refund_due_is_paid_back_nets_to_zero(env):

    A, c, freeze = env
    canceled_and_refunded(A, c, freeze)
    assert abs(stmt(A, 1, "2026-06")["closing"]) < 0.005

def test_reopening_after_the_refund_does_not_forgive_it(env):
    A, c, freeze = env
    canceled_and_refunded(A, c, freeze)
    post(c, "/session/1/stage", stage="booked")
    assert tot(A, 1)["due"] == 430

def test_a_past_month_statement_stays_as_sent(env):

    A, c, freeze = env
    flat(A, "8")
    freeze("2026-03-01")
    household(A, 1, "Okafor", "Okafor & Sons LLC")
    session(A, 1, 1, "booked", "2026-03-10", "headshot")
    line(c, 1, "Team headshots", 1000)
    post(c, "/session/1/invoice/issue")
    march = stmt(A, 1, "2026-03")
    assert [(r["number"], r["amount"]) for r in march["invoices"]] == [("INV-2026-0001", 1080)]
    freeze("2026-04-15")
    post(c, "/session/1/quote/fee", fee_key="custom", label="Extra person", amount="100", qty="1", taxable="1",
         taxable_shown="1")
    post(c, "/session/1/invoice/issue")
    again = stmt(A, 1, "2026-03")
    assert (again["closing"], [r["amount"] for r in again["invoices"]]) == (march["closing"], [1080])

def test_invoice_of_a_written_off_balance_adds_up(env):
    A, c, freeze = env
    flat(A, "8")
    freeze("2026-05-01")
    household(A, 1, "Chen")
    session(A, 1, 1, "shot", "2026-04-01", "family")
    line(c, 1, "Family session", 300)
    pay(c, 1, "deposit", 100, "2026-03-01")
    pay(c, 1, "balance", 100, "2026-04-01")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)))
    assert "written off" in page.lower() and "Paid in full" not in page

def test_paying_a_written_off_balance_is_not_counted_twice(env):
    A, c, freeze = env
    freeze("2026-05-01")
    household(A, 1, "Chen")
    session(A, 1, 1, "shot", "2026-04-01", "family")
    line(c, 1, "Family session", 300)
    pay(c, 1, "deposit", 200, "2026-03-01")
    post(c, "/close", sid="1", step="balance_paid", outcome="waived")
    freeze("2026-05-20")
    pay(c, 1, "balance", 100, "2026-05-20")
    t = tot(A, 1)
    assert t["paid"] + t["written_off"] <= t["quoted"] + 0.005

def test_deleting_a_client_keeps_last_years_mileage(env):
    A, c, freeze = env
    flat(A, "8", "0.67")
    freeze("2025-12-15")
    household(A, 1, "Okafor")
    session(A, 1, 1, "shot", "2025-12-15", "headshot")
    post(c, "/session/1/money", what="expense", label="Drive", miles="42", amount="0", date="2025-12-15")
    before = year(A, 2025)["exp"]["mc"]
    freeze("2026-01-05")
    run(A, "UPDATE settings SET value='0.725' WHERE key='mileage_rate'")
    A.apply_settings()
    with A.app.test_request_context():
        A._delete_client_rows(1, None, [1])
    assert round(year(A, 2025)["exp"]["mc"], 2) == round(before, 2) == 28.14

def test_a_received_date_with_spaces_still_counts(env):
    A, c, freeze = env
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    line(c, 1, "Session", 100)
    post(c, "/session/1/money", what="payment", kind="balance", amount="100", received=" 2026-05-03 ", method="cash")
    assert tot(A, 1)["paid"] == 100
    assert year(A, 2026)["total"] == 100
    assert c.get("/c/1", environ_base=E).status_code == 200

def test_overhead_expense_is_once_only(env):
    A, c, freeze = env
    freeze("2026-06-01")
    page = c.get("/money", environ_base=E).get_data(as_text=True)
    form = page[page.find('action="/money/overhead"'):][:1500]
    tok = re.search(r'name="once" value="([^"]+)"', form)
    for _ in range(2):
        post(c, "/money/overhead", label="Lens cap", amount="12", date="2026-06-01", once=tok.group(1) if tok else "")
    assert one(A, "SELECT COUNT(*) FROM expenses")[0] == 1

def test_mileage_rows_add_up_to_the_summary(env):
    A, c, freeze = env
    flat(A, "8", "0.655")
    freeze("2026-06-01")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-05-01")
    for _ in range(40):
        post(c, "/session/1/money", what="expense", label="School run", miles="3.3", amount="0", date="2026-05-01")
    f = get_zip(c, 2026)
    assert abs(sum(float(m["amount"]) for m in f["mileage.csv"]) - float(f["summary.csv"][-1]["mileage"])) <= 0.01
