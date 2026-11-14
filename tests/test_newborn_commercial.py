import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch, presets=("newborn", "products", "realestate")):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"), ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{p}": "1" for p in presets}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.commit(); con.close()
    return A, c

def _session(A, type_, **kw):
    with A.app.test_request_context():
        A.db()
        return A.create_session(1, type_, **kw)

def test_baby_age_in_weeks_and_due_dates(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _session(A, "newborn")
    born = (date.today() - timedelta(days=45)).isoformat()
    c.post("/client/1/person", data={"name": "Baby Lane", "birth_date": born}, environ_base=E)
    due = (date.today() + timedelta(days=20)).isoformat()
    c.post("/client/1/person", data={"name": "Second Lane", "birth_date": due}, environ_base=E)
    page = c.get("/client/1", environ_base=E).data.decode()
    assert "6 weeks" in page and "due " in page and "Born or due" in page
    con = A.connect()
    assert con.execute("SELECT is_minor FROM people WHERE name='Baby Lane'").fetchone()[0] == 1
    con.close()

def test_indoor_type_has_no_golden_hour_and_tentative_date_shows(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _session(A, "newborn", shoot_date=(date.today() + timedelta(days=10)).isoformat())
    with A.app.test_request_context():
        assert A.light("2027-06-01", "newborn") is None
    c.post(f"/session/{sid}", data={"date_tentative_present": "1", "date_tentative": "1", "shoot_date": (date.today() + timedelta(days=10)).isoformat()}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT date_tentative FROM sessions WHERE id=?", (sid,)).fetchone()[0] == 1
    con.close()

def test_plan_one_agreement_for_several_sessions(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    head = _session(A, "newborn", title="Newborn")
    other = _session(A, "newborn", title="Six months")
    c.post(f"/session/{other}/plan", data={"plan_of": str(head)}, environ_base=E)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (other,)).fetchone()
    con.close()
    r = c.get(f"/session/{other}/agreement", environ_base=E)
    assert r.status_code == 302 and f"/session/{head}/agreement" in r.headers["Location"]
    page = c.get(f"/session/{head}/agreement", environ_base=E).data.decode()
    assert "Six months" in page and "covers every session listed" in page
    con = A.connect()
    con.execute("UPDATE documents SET status='signed' WHERE session_id=? AND kind='agreement'", (head,))
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        s_ = A.q("SELECT * FROM sessions WHERE id=?", (other,), one=True)
        assert A.AUTO["agreement_signed"][1](dict(s=s_, docs=[]))
    c.post(f"/session/{other}/plan", data={"plan_of": ""}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (other,)).fetchone()
    con.close()

def test_license_scope_quoted_scope_and_product_care(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _session(A, "product")
    con = A.connect()
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, '12 product images', 480, 'custom')", (sid,))
    con.commit(); con.close()
    c.post(f"/session/{sid}", data={"lic_term": "2 years", "lic_territory": "the United States", "lic_present": "1", "lic_exclusive": "1",
                                    "po_number": "PO-778"}, environ_base=E)
    page = c.get(f"/session/{sid}/agreement", environ_base=E).data.decode()
    assert "The license lasts 2 years." in page and "the United States" in page and "It is exclusive" in page
    assert "12 product images" in page and "Products in my care" in page
    inv = c.get(f"/session/{sid}/invoice", environ_base=E).data.decode()
    assert "PO-778" in inv

def test_net_terms_from_invoice_date_and_bank_details(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _session(A, "realestate", shoot_date=(date.today() + timedelta(days=60)).isoformat())
    con = A.connect()
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Listing', 200, 'custom')", (sid,))
    con.execute("INSERT INTO settings (key, value) VALUES ('bank_details', 'Routing 000 Account 111') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.execute("INSERT INTO settings (key, value) VALUES ('business_tax_id', '12-3456789') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        if "realestate" in A.C.NET_TERMS_TYPES:
            v = A.bill_ctx(sid)
            due = (date.today() + timedelta(days=A.C.BALANCE_NET_DAYS))
            assert A.long_date(due.isoformat()) in v["due_rule"]
    inv = c.get(f"/session/{sid}/invoice", environ_base=E).data.decode()
    assert "Routing 000 Account 111" in inv and "12-3456789" in inv

def test_deposit_per_type(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _session(A, "newborn")
    con = A.connect()
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Newborn', 400, 'custom')", (sid,))
    con.commit(); con.close()
    c.post("/settings/type/newborn", data={"deposit_value": "25", "deposit_mode": "pct"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        assert A.deposit_required(sid) == 100.0
        assert "25% of the price" in " ".join(" ".join(x) if isinstance(x, (list, tuple)) else str(x) for x in A.C.build_terms("newborn"))
