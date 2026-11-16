import io
import json
import os
import sys

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
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO households (name) VALUES ('Ruiz')")
    con.commit(); con.close()
    return A, c

def test_kept_deposit_tax_counted_once(env):
    A, c = env
    con = A.connect()
    con.execute("""INSERT INTO sessions (household_id, type, stage, lost_reason, shoot_date, stage_changed)
                   VALUES (1, 'portrait', 'lost', 'Canceled', '2026-06-01', '2026-05-01')""")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 250, 'custom')")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Sales tax', 20, 'tax')")
    con.execute("INSERT INTO payments (session_id, amount, received, kind) VALUES (1, 100, '2026-04-01', 'deposit')")
    con.execute("INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8')")
    con.commit(); con.close()
    A.apply_settings()
    with A.app.test_request_context():

        assert A.kept_deposit_tax(1) == 8.0
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    assert "$92" in page and "$84" not in page

def test_no_deposit_step_done_once_quoted(env):
    A, c = env
    import config as C
    old = C.DEPOSIT_AMOUNT
    C.DEPOSIT_AMOUNT = 0
    try:
        con = A.connect()
        con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'portrait', 'booked', '2027-06-01')")
        con.commit(); con.close()
        with A.app.test_request_context():
            assert not A.AUTO["deposit_paid"][1](dict(t=A.totals(1)))
        con = A.connect()
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 250, 'custom')")
        con.commit(); con.close()
        with A.app.test_request_context():
            assert A.totals(1)["deposit_req"] == 0 and A.AUTO["deposit_paid"][1](dict(t=A.totals(1)))
    finally:
        C.DEPOSIT_AMOUNT = old

def test_new_organizer_takes_untouched_billing_session(env):
    A, c = env
    con = A.connect()
    con.execute("INSERT INTO events (name, date, billing) VALUES ('Spring day', '2027-04-10', 'organizer')")
    con.commit(); con.close()
    c.post("/event/1/billing", data={"payer_hid": "1"}, environ_base=E)
    c.post("/event/1/billing", data={"payer_hid": "2"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT household_id FROM sessions WHERE payer_event=1").fetchone()[0] == 2
    sid = con.execute("SELECT id FROM sessions WHERE payer_event=1").fetchone()[0]
    con.execute("INSERT INTO payments (session_id, amount, received, kind) VALUES (?, 50, '2027-01-01', 'deposit')", (sid,))
    con.commit(); con.close()
    c.post("/event/1/billing", data={"payer_hid": "1"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT household_id FROM sessions WHERE payer_event=1").fetchone()[0] == 2
    con.close()
    c.post("/event/1/billing", data={"payer_hid": "999"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT payer_hid FROM events WHERE id=1").fetchone()[0] == 1
    con.close()

def test_export_carries_cleared_settings(env):
    A, c = env
    data = c.get("/settings/export.json", environ_base=E).get_json()
    assert data["settings"].get("credit_off") == "" and "home_base" in data["settings"]
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('credit_off', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    con.commit(); con.close()
    c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(json.dumps(data).encode()), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    con = A.connect()
    assert con.execute("SELECT value FROM settings WHERE key='credit_off'").fetchone()[0] == ""
    con.close()

def test_bad_year_on_money_is_not_an_error(env):
    A, c = env
    assert c.get("/money?year=abc", environ_base=E).status_code == 200
    assert c.get("/money?year=99999999", environ_base=E).status_code == 200

def test_numbers_from_forms(env):
    A, c = env
    assert A.num("nan") == 0 and A.num("inf", 5) == 5 and A.num("abc", 2) == 2 and A.num("$1,250.50") == 1250.5
    assert A.num("-3", lo=0) == 0 and A.num("") == 0 and A.num(None, 1.0) == 1.0
