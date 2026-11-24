import csv
import io
import os
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
    return A, c

def seed_year(A):

    con = A.connect()
    con.executescript("""
        INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8'), ('mileage_rate', '0.70');
        INSERT INTO households (id, name) VALUES (1, '=HYPERLINK("http://x","Lane")'), (2, 'Ruiz');
        INSERT INTO sessions (id, household_id, type, stage, title, shoot_date, tax_rate)
               VALUES (1, 1, 'portrait', 'delivered', '', '2026-05-02', 8);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 500, 'custom', 1);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Sales tax (8%)', 40, 'tax', 0);
        INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'deposit', 200, '2026-01-01', 'cash');
        INSERT INTO payments (session_id, kind, amount, received, method, ref) VALUES (1, 'balance', 340, '2026-05-20', 'check', '+1042');
        INSERT INTO payments (session_id, kind, amount, received, method, voided) VALUES (1, 'balance', 340, '2026-05-19', 'cash', '2026-05-19');
        INSERT INTO payments (session_id, kind, amount, received, method) VALUES (1, 'refund', -54, '2026-12-31', 'cash');
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (3, 1, 'portrait', 'closed', '2025-06-01');
        INSERT INTO payments (session_id, kind, amount, received, method) VALUES (3, 'deposit', 999, '2025-12-31', 'cash');
        INSERT INTO payments (session_id, kind, amount, received, method) VALUES (3, 'other', 777, '2027-01-01', 'cash');
        INSERT INTO sessions (id, household_id, type, stage, lost_reason, shoot_date, stage_changed, tax_rate)
               VALUES (2, 2, 'portrait', 'lost', 'Canceled', '2026-09-01', '2026-08-01 10:00:00', 8);
        INSERT INTO lines (session_id, label, amount, kind) VALUES (2, 'Session', 250, 'custom');
        INSERT INTO lines (session_id, label, amount, kind) VALUES (2, 'Sales tax (8%)', 20, 'tax');
        INSERT INTO payments (session_id, kind, amount, received, method) VALUES (2, 'deposit', 100, '2026-07-10', 'cash');
        INSERT INTO books (received, amount, tax, type, kind) VALUES ('2026-03-05', 108, 8, 'portrait', 'balance');
        INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (NULL, '2026-02-10', '@SUM(A1)', 120.5, 0, NULL);
        INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (1, '2026-05-02', 'Drive to the park', 0, 30, 0.655);
        INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (NULL, '2025-12-31', 'Old lens', 900, 0, NULL);
    """)
    con.commit()
    con.close()
    A.apply_settings()

def get_zip(c, year):
    r = c.get(f"/money/accounts.zip?year={year}", environ_base=E)
    assert r.status_code == 200
    assert r.headers["Content-Disposition"] == f'attachment; filename="lumen-aureum-{year}-accounts.zip"'
    assert r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    return {n: list(csv.DictReader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in z.namelist()}

def test_zip_holds_the_files_and_is_audited(env):
    A, c = env
    seed_year(A)
    files = get_zip(c, 2026)
    assert sorted(files) == ["expenses.csv", "mileage.csv", "payments.csv", "sales-tax.csv", "summary.csv", "write-offs.csv"]
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM audit WHERE action='Exported accounts for 2026'").fetchone()[0] == 1
    con.close()
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "Details (CSV)" in page and "/money/accounts.zip" in page
    assert '<option value="2026" selected>' in page and '<option value="2025"' in page

def test_totals_match_finances(env):
    A, c = env
    seed_year(A)
    files = get_zip(c, 2026)
    total = files["summary.csv"][-1]
    assert total["month"] == "Total"
    with A.app.test_request_context():
        ym = A.year_money(2026)

    assert total["received"] == "694.00" == f"{ym['total']:.2f}"
    assert total["refunds (in received)"] == "-54.00"
    assert total["expenses"] == "120.50" and total["miles"] == "30" and total["mileage"] == "19.65"

    assert total["sales tax"] == "52.00" == f"{ym['tax_due']:.2f}"
    assert total["net, after sales tax"] == "501.85"
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    for shown in ("$694", "$120.50", "$19.65", "$52", "$501.85"):
        assert shown in page

    months = files["summary.csv"][:-1]
    assert [m["month"] for m in months] == [f"2026-{n:02d}" for n in range(1, 13)]
    assert round(sum(float(m["received"]) for m in months), 2) == 694.0
    assert round(sum(float(m["sales tax"]) for m in months), 2) == 52.0
    pays = files["payments.csv"]
    assert round(sum(float(p["amount"]) for p in pays if p["voided"] == "no"), 2) == 694.0
    assert round(sum(float(e["amount"]) for e in files["expenses.csv"]), 2) == 120.5
    assert round(sum(float(m["amount"]) for m in files["mileage.csv"]), 2) == 19.65
    tax = files["sales-tax.csv"]
    assert round(sum(float(t["sales tax"]) for t in tax), 2) == 52.0
    lane = next(t for t in tax if t["counted as"] == "payments received" and t["session date"] == "2026-05-02")
    assert lane["received"] == "486.00" and lane["taxable amount"] == "450.00" and lane["rate (%)"] == "8" \
        and lane["sales tax"] == "36.00" and lane["exempt"] == "no"
    kept = next(t for t in tax if t["counted as"] == "kept deposit")
    assert kept["client"] == "Ruiz" and kept["received"] == "100.00" and kept["sales tax"] == "0.00"
    kept = next(t for t in tax if t["counted as"].startswith("called off before 1.3"))
    assert kept["client"] == "Ruiz" and kept["taxable amount"] == "100.00" and kept["sales tax"] == "8.00"
    assert any(t["client"] == "Deleted client" and t["sales tax"] == "8.00" for t in tax)
    trip = files["mileage.csv"][0]
    assert trip["miles"] == "30" and trip["rate"] == "0.655" and trip["amount"] == "19.65" and trip["date"] == "2026-05-02"

def test_voided_marked_and_refunds_negative(env):
    A, c = env
    seed_year(A)
    pays = get_zip(c, 2026)["payments.csv"]
    void = [p for p in pays if p["voided"] == "yes"]
    assert len(void) == 1 and void[0]["amount"] == "340.00" and void[0]["date received"] == "2026-05-19"
    refund = next(p for p in pays if p["kind"] == "refund")
    assert refund["amount"] == "-54.00" and refund["voided"] == "no"
    chk = next(p for p in pays if p["method"] == "check")
    assert chk["check number"] == "'+1042" and chk["amount"] == "340.00"
    assert all("$" not in p["amount"] for p in pays)

def test_formula_injection_guarded(env):
    A, c = env
    seed_year(A)
    files = get_zip(c, 2026)
    lane = [p for p in files["payments.csv"] if p["session date"] == "2026-05-02"]
    assert lane and all(p["client"] == "'=HYPERLINK(\"http://x\",\"Lane\")" for p in lane)
    assert files["expenses.csv"][0]["what"] == "'@SUM(A1)"
    with A.app.test_request_context():
        for raw in ("=1+1", "+1", "-1", "@x", "\tx", "\rx"):
            assert A._text_cell(raw) == "'" + raw
        assert A._text_cell("Lane") == "Lane"
        assert A._money_cell(-0.001) == "0.00" and A._money_cell(-54) == "-54.00"

def test_year_boundaries(env):
    A, c = env
    seed_year(A)
    files = get_zip(c, 2026)
    dates = [p["date received"] for p in files["payments.csv"]]
    assert "2026-01-01" in dates and "2026-12-31" in dates
    assert "2025-12-31" not in dates and "2027-01-01" not in dates
    assert all(e["date"].startswith("2026-") for e in files["expenses.csv"])
    before = get_zip(c, 2025)
    assert [p["amount"] for p in before["payments.csv"]] == ["999.00"]
    assert before["expenses.csv"][0]["what"] == "Old lens"

def test_year_runs_in_the_app_time_zone(env, monkeypatch):

    A, c = env
    con = A.connect()
    con.executescript("""
        INSERT INTO settings (key, value) VALUES ('timezone', 'Pacific/Honolulu');
        INSERT INTO households (id, name) VALUES (1, 'Lane');
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'booked', '2027-03-01');
        INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 500, 'custom');
    """)
    con.commit()
    con.close()
    A.apply_settings()
    instant = datetime(2027, 1, 1, 5, 0, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(A, "datetime", Frozen)
    with A.app.test_request_context():
        assert A.today().isoformat() == "2026-12-31"
    c.post("/session/1/money", data={"what": "payment", "kind": "deposit", "amount": "250", "method": "cash"},
           environ_base=E)
    r = c.get("/money/accounts.zip", environ_base=E)
    assert 'filename="lumen-aureum-2026-accounts.zip"' in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    pays = list(csv.DictReader(io.StringIO(z.read("payments.csv").decode("utf-8-sig"))))
    assert [(p["date received"], p["amount"]) for p in pays] == [("2026-12-31", "250.00")]
    assert not get_zip(c, 2027)["payments.csv"]

def test_one_sales_tax_keeps_the_columns_it_always_had(env):

    A, c = env
    seed_year(A)
    row = get_zip(c, 2026)["sales-tax.csv"][0]
    assert "region" not in row and "tax" not in row and "sales tax" in row
