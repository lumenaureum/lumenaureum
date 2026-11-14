import os
import runpy
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"), ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    return A, c

def test_year_link_on_today_and_plain_log_line(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    assert b"/year" in c.get("/", environ_base=E).data
    assert "step was closed" not in open(os.path.join(ROOT, "app.py")).read()

def test_future_signing_date_refused(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    did, sid = con.execute("SELECT id, session_id FROM documents WHERE status != 'signed' LIMIT 1").fetchone()
    con.close()
    later = (date.today() + timedelta(days=30)).isoformat()
    c.post(f"/doc/{did}", data={"signed_on": later}, environ_base=E)
    con = A.connect()
    st, signed = con.execute("SELECT status, signed FROM documents WHERE id=?", (did,)).fetchone()
    con.close()
    assert st != "signed" and signed != later

def test_people_columns_follow_the_client(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    pet = con.execute("SELECT household_id FROM sessions WHERE type='pet' LIMIT 1").fetchone()
    senior = con.execute("SELECT household_id FROM sessions WHERE type='senior' LIMIT 1").fetchone()
    con.close()
    if pet:
        page = c.get(f"/client/{pet[0]}", environ_base=E).data.decode()
        assert "<th>Grad year</th>" not in page
    if senior:
        assert "<th>Grad year</th>" in c.get(f"/client/{senior[0]}", environ_base=E).data.decode()

def test_same_name_notice(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    name, hid = con.execute("SELECT name, household_id FROM people LIMIT 1").fetchone()
    other = con.execute("SELECT id FROM households WHERE id != ? LIMIT 1", (hid,)).fetchone()[0]
    con.close()
    r = c.post(f"/client/{other}/person", data={"name": name}, environ_base=E, follow_redirects=True)
    assert b"isn&#39;t the same person twice" in r.data or b"isn't the same person twice" in r.data
