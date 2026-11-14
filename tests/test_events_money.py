import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def test_family_price_and_paid_column(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO events (name, date, billing) VALUES ('Spring team day', '2027-04-10', 'families')")
    for i, name in enumerate(("Bell", "Ruiz", "Duffy"), 1):
        con.execute("INSERT INTO households (name) VALUES (?)", (name,))
        con.execute("INSERT INTO sessions (household_id, type, event_id, stage) VALUES (?, 'event', 1, 'booked')", (i,))
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (3, 'Own price', 99, 'custom')")
    con.commit(); con.close()
    c.post("/event/1/family-price", data={"amount": "60"}, environ_base=E)
    con = A.connect()
    try:
        amounts = {r[0]: r[1] for r in con.execute("SELECT session_id, SUM(amount) FROM lines WHERE kind!='tax' GROUP BY session_id")}
    finally:
        con.close()
    assert amounts == {1: 60, 2: 60, 3: 99}
    con = A.connect()
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 60, '2027-04-01')")
    con.commit(); con.close()
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert "<th>Paid</th>" in page and ">paid<" in page and "of $60" in page

def test_year_view_shows_booked_days(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane wedding')")
    con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (1, 'portrait', '2027-06-12', 'booked')")
    con.commit(); con.close()
    page = c.get("/calendar/year?y=2027", environ_base=E).get_data(as_text=True)
    assert "Saturdays free" in page and 'data-tip="Lane wedding"' in page and "busy" in page
    assert "Year" in c.get("/calendar?m=2027-06", environ_base=E).get_data(as_text=True)

def test_company_billing_job_address_search_and_statement(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name, company) VALUES ('Dana Whitfield', 'Hearthstone Realty')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Dana Whitfield', 'adult')")
    for i, (day, addr) in enumerate((("2027-03-02", "12 Orchard Lane"), ("2027-03-09", "4 Mill Road")), 1):
        con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage, intake) VALUES (1, 'portrait', ?, 'shot', ?)",
                    (day, '{"address": "%s"}' % addr))
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Listing', 175, 'custom')", (i,))
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (1, 'balance', 175, '2027-03-05')")
    con.commit(); con.close()
    c.post("/session/2/invoice/issue", environ_base=E)
    inv = c.get("/session/2/invoice", environ_base=E).get_data(as_text=True)
    assert "Hearthstone Realty" in inv and "Attn: Dana Whitfield" in inv and "4 Mill Road" in inv
    r = c.get("/search?q=Orchard", environ_base=E)
    assert r.status_code in (200, 302) and ("Dana Whitfield" in r.get_data(as_text=True) or "/c/1" in (r.location or ""))

    from datetime import date as _d
    now = c.get(f"/client/1/statement?m={_d.today():%Y-%m}", environ_base=E).get_data(as_text=True)
    assert "4 Mill Road" in now and "INV-" in now and "Balance due" in now
    st = c.get("/client/1/statement?m=2027-03", environ_base=E).get_data(as_text=True)
    assert "12 Orchard Lane" in st and "No invoices this month." in st
    cal = c.get("/calendar?m=2027-03", environ_base=E).get_data(as_text=True)
    assert "4 Mill Road" in cal

def test_family_added_later_gets_the_event_price(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO events (name, date, billing) VALUES ('Team day', '2027-04-10', 'families')")
    con.commit(); con.close()
    c.post("/event/1/family-price", data={"amount": "95"}, environ_base=E)
    c.post("/event/1/family", data={"household_name": "Bell", "children": "Grace 15"}, environ_base=E)
    con = A.connect()
    try:
        assert con.execute("SELECT SUM(amount) FROM lines WHERE kind!='tax'").fetchone()[0] == 95
    finally:
        con.close()

def test_step_days_are_clamped(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    import config as C
    key = next(iter(C.SESSION_TYPES))
    c.post(f"/settings/type/{key}", data={"booked_new": "Far away", "booked_new_days": "99999999"}, environ_base=E)
    assert ("do", "Far away", 365) in C.WORKFLOW[key]["booked"]

def test_credit_on_sales_documents_never_on_legal_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (1, 'portrait', '2027-03-02', 'shot')")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 250, 'custom')")
    con.commit(); con.close()
    assert C.CREDIT in c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert C.CREDIT in c.get("/fees/sheet", environ_base=E).get_data(as_text=True)
    assert C.CREDIT not in c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    c.post("/settings", data={"main_posted": "1"}, environ_base=E)
    assert C.CREDIT not in c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)

def test_own_agreement_wording(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (1, 'portrait', '2027-03-02', 'booked')")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 250, 'custom')")
    con.commit(); con.close()
    page = c.get("/settings/wording/agreement", environ_base=E).get_data(as_text=True)
    assert "[HOW YOU DELIVER]" in page and "The standard wording." in page
    own = ("# My terms\nHi [CLIENT], total [PRICE]. {{ 7*7 }} <script>x</script> [NOT A FIELD]\n\n"
           "# Minors\n[MINORS]")
    c.post("/settings/wording/agreement", data={"text": own.replace("\\\\n", "\\n")}, environ_base=E)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert "<h2>My terms</h2>" in doc and "Hi Avery Lane, total $250" in doc
    assert "{{ 7*7 }}" in doc and "49" not in doc.split("My terms")[1][:200]
    assert "<script>x</script>" not in doc and "&lt;script&gt;" in doc
    assert "[NOT A FIELD]" in doc and "<h2>Minors</h2>" not in doc
    assert "1. Who this is between" not in doc
    c.post("/settings/wording/agreement", data={"reset": "1"}, environ_base=E)
    assert "1. Who this is between" in c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
