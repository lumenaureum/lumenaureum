import csv
import io
import json
import os
import sys
import zipfile

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
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.commit()
    con.close()
    return A, c

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

def get_zip(c, year):
    r = c.get(f"/money/accounts.zip?year={year}", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.data))
    return {n: list(csv.DictReader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in z.namelist()}

def taxed(A, pct="6"):
    run(A, f"INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '{pct}')")
    A.apply_settings()

def test_e5_waived_balance_is_written_off(env):
    A, c = env
    run(A, """
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'shot', '2026-11-01');
        INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 500, 'custom');
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 450, '2026-10-01');""")
    with A.app.test_request_context():
        assert A.release_blocked(1) == 50
    c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": "waived"}, environ_base=E)
    with A.app.test_request_context():
        t = A.totals(1)
        assert abs(t["due"]) < 0.005 and t["written_off"] == 50 and A.release_blocked(1) == 0
        assert t["paid"] == 450 and t["quoted"] == 500
        ym = A.year_money(2026)
    assert ym["written_off"] == 50 and ym["total"] == 450
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "Balances waived" in page and "$50.00" in page
    files = get_zip(c, 2026)
    assert [r["amount written off"] for r in files["write-offs.csv"]] == ["50.00"]
    total = files["summary.csv"][-1]
    assert total["written off (not received)"] == "50.00" and total["received"] == f"{ym['total']:.2f}"
    assert round(sum(float(m["written off (not received)"]) for m in files["summary.csv"][:-1]), 2) == 50.0

    A.app.config["gallery_link"] = lambda sid: "https://photos.example/g/x"
    c.post("/session/1/quick", data={"act": "mark", "field": "letter_mailed"}, environ_base=E)
    assert one(A, "SELECT letter_mailed FROM sessions WHERE id=1")[0]

def canceled_paid_in_full(A, c, paid_on="2026-03-01"):
    taxed(A)
    run(A, f"""
        INSERT INTO sessions (id, household_id, type, stage, shoot_date, tax_rate) VALUES (1, 1, 'portrait', 'booked', '2026-12-01', 6);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 500, 'custom', 1);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Sales tax (6%)', 30, 'tax', 0);
        INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-02-01', '{{"deposit": 100, "total": 530}}');
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 530, '{paid_on}');""")
    c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "Canceled"}, environ_base=E)

def test_e6_canceled_paid_in_full_keeps_only_the_deposit(env):
    A, c = env
    canceled_paid_in_full(A, c)
    with A.app.test_request_context():
        t = A.totals(1)
        assert (t["kept"], t["refund_due"]) == (100, 430)
        assert 5 < A.year_money(2026)["tax_due"] <= 6.0
    page = c.get("/c/1", environ_base=E).get_data(as_text=True)
    assert "You keep the deposit, $100" in page and "Refund due: $430" in page
    assert "Refund owed: $430.00" in c.get("/", environ_base=E).get_data(as_text=True)
    c.post("/session/1/money", data={"what": "payment", "kind": "refund", "amount": "430", "received": "2026-11-10"},
           environ_base=E)
    with A.app.test_request_context():
        t = A.totals(1)
        assert t["refund_due"] == 0 and t["kept"] == 100
    assert "Refund due" not in c.get("/c/1", environ_base=E).get_data(as_text=True)

def test_e7_kept_deposit_tax_stays_in_the_year_it_arrived(env):
    A, c = env
    taxed(A)
    run(A, """
        INSERT INTO sessions (id, household_id, type, stage, shoot_date, tax_rate) VALUES (1, 1, 'portrait', 'booked', '2027-02-01', 6);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', 500, 'custom', 1);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Sales tax (6%)', 30, 'tax', 0);
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 100, '2025-12-15');""")

    def years():
        with A.app.test_request_context():
            return round(A.year_money(2025)["tax_due"], 2), round(A.year_money(2026)["tax_due"], 2)
    before = years()
    assert before[0] > 0 and before[1] == 0
    c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "Canceled"}, environ_base=E)
    assert years() == before
    with A.app.test_request_context():
        assert A.kept_deposit_tax(1) == before[0]
    c.post("/session/1/stage", data={"stage": "inquiry"}, environ_base=E)
    assert years() == before
    files = get_zip(c, 2025)
    assert files["summary.csv"][-1]["sales tax"] == f"{before[0]:.2f}"

def test_e7_paid_in_full_last_year_canceling_takes_the_tax_back_this_year(env):
    A, c = env
    canceled_paid_in_full(A, c, paid_on="2025-12-15")
    with A.app.test_request_context():
        assert round(A.year_money(2025)["tax_due"], 2) == 30.0

        assert round(A.year_money(2026)["tax_due"], 2) == round(-430 * 30 / 530, 2)
    c.post("/session/1/money", data={"what": "payment", "kind": "refund", "amount": "430", "received": "2026-11-10"},
           environ_base=E)
    with A.app.test_request_context():
        assert round(A.year_money(2025)["tax_due"], 2) == 30.0
        assert round(A.year_money(2026)["tax_due"], 2) == round(-430 * 30 / 530, 2)
        assert round(A.year_money(2025)["tax_due"] + A.year_money(2026)["tax_due"], 2) == round(100 * 30 / 530, 2)

def quote_530(A, sid=1):
    run(A, f"""
        INSERT INTO sessions (id, household_id, type, stage, shoot_date, tax_rate) VALUES ({sid}, 1, 'portrait', 'shot', '2026-11-01', 6);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES ({sid}, 'Session', 500, 'custom', 1);
        INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES ({sid}, 'Sales tax (6%)', 30, 'tax', 0);""")

def test_e2_tax_capped_at_the_quote(env):
    A, c = env
    taxed(A)
    quote_530(A)
    c.post("/session/1/money", data={"what": "payment", "kind": "balance", "amount": "580", "received": "2026-11-02"},
           environ_base=E)
    with A.app.test_request_context():
        assert round(A.year_money(2026)["tax_due"], 2) == 30.0
    tax = get_zip(c, 2026)["sales-tax.csv"]
    assert [t["sales tax"] for t in tax] == ["30.00"] and tax[0]["taxable amount"] == "500.00"

def test_e2_tip_kind_kept_overpayment(env):
    A, c = env
    taxed(A)
    quote_530(A)
    for kind, amt in (("balance", "530"), ("tip", "50")):
        c.post("/session/1/money", data={"what": "payment", "kind": kind, "amount": amt, "received": "2026-11-02"},
               environ_base=E)
    with A.app.test_request_context():
        t = A.totals(1)
        assert t["paid"] == 530 and abs(t["due"]) < 0.005 and t["tips"] == 50
        ym = A.year_money(2026)
    assert ym["tips"] == 50 and ym["total"] == 580 and round(ym["tax_due"], 2) == 30.0
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "Tips" in page and "$50" in page
    assert "Credit" not in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    files = get_zip(c, 2026)
    assert files["summary.csv"][-1]["tips (in received, not taxed)"] == "50.00"
    assert sorted(p["kind"] for p in files["payments.csv"]) == ["balance", "tip"]

def statement_text(c, month):
    return c.get(f"/client/1/statement?m={month}", environ_base=E).get_data(as_text=True)

def test_e8_canceled_invoiced_job_shows_deposit_kept_not_a_credit(env):
    A, c = env
    run(A, """
        UPDATE households SET company='Lane Realty' WHERE id=1;
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'booked', '2026-12-01');
        INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 500, 'custom');
        INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-10-01', '{"deposit": 100, "total": 500}');
        INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (1, 'INV', 'INV-2026-0001', '2026-11-02');
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 100, '2026-11-03');""")
    c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "Canceled"}, environ_base=E)
    st = statement_text(c, "2026-11")
    assert "Canceled, deposit kept" in st
    assert "Credit on" not in st and "Balance on November 30, 2026" in st

def test_e9_paid_on_the_day_without_an_invoice_balances(env):
    A, c = env
    run(A, """
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'closed', '2026-10-05');
        INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 400, 'custom');
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 400, '2026-10-05');""")
    st = statement_text(c, "2026-10")
    assert "Work done" in st
    assert "Credit on" not in st and "Balance on October 31, 2026" in st
    assert "Credit on" not in statement_text(c, "2026-11")

def test_e14_price_list_quantity_and_discount_after_booking(env):
    A, c = env
    run(A, """
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'booked', '2026-12-01');
        INSERT INTO lines (session_id, label, amount, kind, qty, unit) VALUES (1, 'Session', 500, 'custom', 1, 500);
        INSERT INTO quote_versions (session_id, at, data) VALUES (1, '2026-10-01', '{"deposit": 100, "total": 500}');
        INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'deposit', 100, '2026-10-02');""")
    page = c.get("/c/1", environ_base=E).get_data(as_text=True)
    assert "/session/1/quote/fee" not in page and "/session/1/quote/discount" not in page
    lid = one(A, "SELECT id FROM lines WHERE label='Session'")[0]

    for url, data in (("/session/1/quote/fee", {"fee_key": "custom", "label": "Extra hour", "amount": "150", "qty": "1"}),
                      (f"/line/{lid}/qty", {"qty": "2"}), ("/session/1/quote/discount", {"pct": "10", "reason": ""}),
                      ("/session/1/money", {"what": "line", "label": "Extra", "amount": "50"}),
                      (f"/money/line/{lid}/delete", {}), ("/session/1/quote/exempt", {"tax_exempt": "1"})):
        c.post(url, data=data, environ_base=E)
    assert [tuple(r) for r in A.connect().execute("SELECT label, amount, qty FROM lines WHERE session_id=1")] == [("Session", 500, 1)]

    c.post("/session/1/quote/replace", data={"back": "/c/1?s=1"}, environ_base=E)
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"
    c.post("/session/1/quote/fee", data={"fee_key": "custom", "label": "Extra hour", "amount": "150", "qty": "1"},
           environ_base=E)
    lid2 = one(A, "SELECT id FROM lines WHERE label='Extra hour'")[0]
    c.post(f"/line/{lid2}/qty", data={"qty": "2"}, environ_base=E)
    assert one(A, "SELECT amount FROM lines WHERE id=?", (lid2,))[0] == 300
    c.post("/session/1/quote/discount", data={"pct": "10", "reason": ""}, environ_base=E)
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)
    with A.app.test_request_context():
        t = A.totals(1)
        assert A.replacing(1) is None and A.quote_locked(1)
    assert t["quoted"] == 720 and t["paid"] == 100
    con = A.connect()
    hist = [r[0] for r in con.execute("SELECT action FROM audit WHERE session_id=1 ORDER BY id")]
    con.close()
    assert any(h.startswith("Canceled the booked quote") and "kept in History" in h for h in hist)
    assert any("Changed the quantity of 'Extra hour' from 1 to 2" in h for h in hist)
    assert any("Set the discount to 10%" in h for h in hist)
    assert any(h.startswith("Booked the new quote") and "$720" in h for h in hist)

def test_e15_date_move_keeps_closed_steps_closed_and_counts_reschedules(env):

    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops)
              VALUES (1, 1, 'portrait', 'booked', '2026-12-05', '16:00', 'Riverside park')""")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    titles = ("Send session details", "Confirm time and place")
    run(A, "UPDATE tasks SET done='2026-11-10', outcome='done' WHERE session_id=1 AND title IN (?, ?)", titles)
    c.post("/session/1/quick", data={"act": "date", "shoot_date": "2026-12-13", "start_time": "16:00"}, environ_base=E)
    con = A.connect()
    assert [r[0] for r in con.execute("SELECT done FROM tasks WHERE session_id=1 AND title IN (?, ?)", titles)] == ["2026-11-10", "2026-11-10"]
    con.close()
    run(A, "UPDATE tasks SET done='2026-11-12', outcome='done' WHERE session_id=1 AND title IN (?, ?)", titles)
    s = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    stamp = s.split('name="seen" value="')[1].split('"')[0] if 'name="seen" value="' in s else ""
    c.post("/session/1", data={"title": "", "type": "portrait", "shoot_date": "2026-12-20", "start_time": "16:00",
                               "stops": "Riverside park", "seen": stamp}, environ_base=E)
    assert one(A, "SELECT shoot_date FROM sessions WHERE id=1")[0] == "2026-12-20"
    assert one(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND done IS NULL AND title IN (?, ?)", titles)[0] == 0
    with A.app.test_request_context():
        assert A.reschedules(1) == 2
    assert "Rescheduled 2 times" in c.get("/c/1", environ_base=E).get_data(as_text=True)

def test_e16_passed_tentative_date_never_moves_to_shot(env):
    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, date_tentative)
              VALUES (1, 1, 'portrait', 'booked', '2026-11-10', 1)""")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    run(A, "UPDATE tasks SET done='2026-11-01', outcome='done' WHERE session_id=1 AND stage='booked'")
    run(A, "INSERT INTO step_closes (session_id, step, outcome) SELECT 1, key, 'na' FROM (SELECT 'agreement_signed' key "
           "UNION SELECT 'deposit_paid' UNION SELECT 'releases_signed' UNION SELECT 'date_set')")
    c.post("/session/1/quick", data={"act": "favorites"}, environ_base=E)
    with A.app.test_request_context():
        A.advance(1)
        A.db().commit()
        assert A.workflow(1)["ready"]
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"
    today_page = c.get("/", environ_base=E).get_data(as_text=True)
    assert "The tentative date (Tue Nov 10) has come" in today_page
    assert "It happened on that day" in c.get("/c/1", environ_base=E).get_data(as_text=True)
    c.post("/session/1/quick", data={"act": "firm"}, environ_base=E)
    assert tuple(one(A, "SELECT stage, date_tentative FROM sessions WHERE id=1")) == ("shot", 0)

def test_e32_reopen_stays_at_the_chosen_stage_despite_a_past_date(env):
    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'booked', '2026-05-01');
              INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 500, 'custom');""")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    run(A, "UPDATE tasks SET done='2026-04-01', outcome='done' WHERE session_id=1 AND stage IN ('inquiry','quoted','booked')")
    run(A, "INSERT INTO step_closes (session_id, step, outcome) SELECT 1, key, 'na' FROM (SELECT 'agreement_signed' key "
           "UNION SELECT 'deposit_paid' UNION SELECT 'releases_signed' UNION SELECT 'logged' UNION SELECT 'quote_built')")
    c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "Canceled"}, environ_base=E)
    assert "Reopen at" in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    c.post("/session/1/stage", data={"stage": "booked"}, environ_base=E)
    with A.app.test_request_context():
        A.advance(1)
        A.db().commit()
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"
    c.post("/session/1/quick", data={"act": "favorites"}, environ_base=E)
    assert one(A, "SELECT stage FROM sessions WHERE id=1")[0] == "booked"

def test_e26_mark_signed_with_several_pending_opens_the_paperwork(env):
    A, c = env
    run(A, """INSERT INTO people (id, household_id, name, role) VALUES (1, 1, 'Ann Lane', 'adult'), (2, 1, 'Bo Lane', 'adult');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'wedding', 'booked', '2027-06-01');
              INSERT INTO documents (session_id, kind, about_person, signer) VALUES (1, 'agreement', 1, 'Ann Lane');
              INSERT INTO documents (session_id, kind, about_person, signer) VALUES (1, 'agreement', 2, 'Bo Lane');""")
    with A.app.test_request_context():
        a = next(a for a in A.build_actions() if a.get("what") == "Send the agreement")
    assert a.get("doc") == "/session/1/agreement" and not a.get("theirs")
    c.post("/session/1/docs/sent?kind=agreement", environ_base=E)
    with A.app.test_request_context():
        a = next(a for a in A.build_actions() if a.get("what") == "Agreement signed")
    assert a.get("doc", "").endswith("#paperwork") and a.get("doc_verb") == "Mark who signed" and not a.get("post")
    r = c.post("/session/1/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    assert r.headers["Location"].endswith("#paperwork")
    assert one(A, "SELECT COUNT(*) FROM documents WHERE status='signed'")[0] == 0
    run(A, "UPDATE documents SET status='signed', signed='2026-11-01' WHERE id=1")
    with A.app.test_request_context():
        a = next(a for a in A.build_actions() if a.get("what") == "Agreement signed")
    assert a.get("verb") == "Mark signed" and a.get("post")
    c.post("/session/1/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    assert one(A, "SELECT COUNT(*) FROM documents WHERE status='signed'")[0] == 2

def test_e45_first_visit_that_closes_the_gallery_asks_first(env):
    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_built)
              VALUES (1, 1, 'portrait', 'delivered', '2025-06-01', '2025-06-10')""")
    form = {"gallery_built": "2025-06-10", "first_visit": "2025-06-12", "favorites": "", "veto": ""}
    r = c.post("/session/1/delivery", data=form, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "This closes the gallery now. Save anyway?" in page
    assert 'name="confirm_close" value="1"' in page and 'name="first_visit" value="2025-06-12"' in page
    assert "<script>" not in page
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] is None
    c.post("/session/1/delivery", data=dict(form, confirm_close="1"), environ_base=E)
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] == "2025-06-12"
    run(A, "UPDATE sessions SET first_visit=NULL WHERE id=1")
    with A.app.test_request_context():
        recent = dict(form, first_visit=A.today().isoformat())
    c.post("/session/1/delivery", data=recent, environ_base=E)
    assert one(A, "SELECT first_visit FROM sessions WHERE id=1")[0] == recent["first_visit"]

@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    run(A, """INSERT INTO households (id, name) VALUES (1, 'Lane');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (5, 1, 'portrait', 'inquiry', '2027-01-01');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (6, 1, 'portrait', 'inquiry', '2027-02-01');""")
    return A, c

def test_e54_deleted_session_stays_deleted_after_a_restore(install, tmp_path):
    A, c = install
    import shutil
    from pathlib import Path
    import db as dbmod
    snap = tmp_path / "before.db"
    dbmod.snapshot(snap)
    c.post("/session/5/delete", environ_base=E)
    assert not one(A, "SELECT 1 FROM sessions WHERE id=5")
    items = json.loads(A.DELETIONS_JSON.read_text())
    assert [(d["kind"], d["id"]) for d in items] == [("session", 5)] and len(items[0]["mac"]) == 64
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)
    assert one(A, "SELECT 1 FROM sessions WHERE id=5")
    assert A.reapply_deletions() == 1
    assert not one(A, "SELECT 1 FROM sessions WHERE id=5") and one(A, "SELECT 1 FROM sessions WHERE id=6")
    assert one(A, "SELECT 1 FROM households WHERE id=1")
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Deleted again a session%'")[0] == 1
    assert A.reapply_deletions() == 0

def test_e54_a_session_entry_is_never_taken_for_a_client(install):
    A, c = install
    created = one(A, "SELECT created FROM sessions WHERE id=6")[0]
    run(A, "INSERT INTO households (id, name, created) VALUES (6, 'Moss', ?)", (created,))
    with A.app.test_request_context():
        key = A._deletion_key(A.db())
        good = A._signed(key, dict(id=6, created=created, kind="session"))
    forged = {k: v for k, v in good.items() if k != "kind"}
    A.DELETIONS_JSON.write_text(json.dumps([forged]))
    assert A.reapply_deletions() == 0 and one(A, "SELECT 1 FROM households WHERE id=6")
    A.DELETIONS_JSON.write_text(json.dumps([dict(good, mac="0" * 64)]))
    assert A.reapply_deletions() == 0 and one(A, "SELECT 1 FROM sessions WHERE id=6")

def test_e36_imported_steps_reach_open_sessions(env):
    A, c = env
    import config as C
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'booked', '2027-06-01')")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    t = dict(C.PRESETS["families"]["types"][0], steps={"booked": [["do", "Confirm parking", -1]]})
    body = json.dumps({"lumen_aureum_settings": 1, "rules": {"TYPES": [t]}}).encode()
    c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(body), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    assert one(A, "SELECT 1 FROM tasks WHERE session_id=1 AND title='Confirm parking' AND done IS NULL")

def plan(A):
    run(A, """INSERT INTO people (id, household_id, name, role) VALUES (1, 1, 'Ann Lane', 'adult');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'newborn', 'booked', '2027-01-10');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date, plan_of) VALUES (2, 1, 'newborn', 'booked', '2027-04-10', 1);
              INSERT INTO sessions (id, household_id, type, stage, shoot_date, plan_of) VALUES (3, 1, 'newborn', 'booked', '2027-07-10', 1);
              INSERT INTO documents (session_id, kind, about_person, signer) VALUES (1, 'agreement', 1, 'Ann Lane');""")

def test_e38_mark_signed_on_a_plan_member_signs_the_plans_agreement(env):
    A, c = env
    plan(A)
    c.post("/session/2/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    assert one(A, "SELECT status FROM documents WHERE session_id=1 AND kind='agreement'")[0] == "signed"

def test_e37_deleting_a_plans_first_session_passes_the_plan_on(env):
    A, c = env
    plan(A)
    run(A, "UPDATE documents SET status='signed', signed='2026-11-01' WHERE session_id=1")
    c.post("/session/1/delete", environ_base=E)
    assert not one(A, "SELECT 1 FROM sessions WHERE id=1")
    assert one(A, "SELECT plan_of FROM sessions WHERE id=2")[0] is None
    assert one(A, "SELECT plan_of FROM sessions WHERE id=3")[0] == 2
    assert tuple(one(A, "SELECT session_id, status FROM documents WHERE kind='agreement'")) == (2, "signed")
    r = c.get("/session/3/agreement", environ_base=E, follow_redirects=True)
    assert r.status_code == 200
    with A.app.test_request_context():
        steps = [st for g_ in A.workflow(3)["stages"] for st in g_["steps"] if st.get("key") == "agreement_signed"]
    assert steps and all(st["done"] for st in steps)

def test_e48_upload_stuck_by_a_restart_can_be_pressed_again(env):
    A, c = env
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'delivered', '2026-10-01');
              INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (2, 1, 'portrait', 'delivered', '2026-10-02');
              INSERT INTO galleries (session_id, key, built_at, upload_state) VALUES (1, 'k', '2026-10-05', 'uploading');
              INSERT INTO galleries (session_id, key, built_at, upload_state) VALUES (2, 'k', '2026-10-05', 'awaiting');""")
    for fn in A.UNLOCK_HOOKS:
        fn()
    con = A.connect()
    rows = con.execute("SELECT upload_state, upload_error FROM galleries ORDER BY session_id").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["error", "error"] and all("restarted" in r[1] for r in rows)

def test_e22_an_adults_birth_date_is_not_a_minor(env):
    A, c = env
    c.post("/client/1/person", data={"name": "Dana Lane", "relation": "mother", "birth_date": "2000-01-01"}, environ_base=E)
    c.post("/client/1/person", data={"name": "Ivy Lane", "relation": "daughter", "birth_date": "2026-09-01"}, environ_base=E)
    assert tuple(one(A, "SELECT is_minor FROM people WHERE name='Dana Lane'")) == (0,)
    assert tuple(one(A, "SELECT is_minor FROM people WHERE name='Ivy Lane'")) == (1,)
    pid = one(A, "SELECT id FROM people WHERE name='Dana Lane'")[0]
    c.post(f"/person/{pid}", data={"name": "Dana Lane", "relation": "mother", "birth_date": "2000-01-01",
                                   "minor_shown": "1", "is_minor": "1"}, environ_base=E)
    assert one(A, "SELECT is_minor FROM people WHERE id=?", (pid,))[0] == 1

