import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    return A, c

def test_numbers_issue_year_in_sequence_and_kept(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    sids = [r[0] for r in con.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 3")]
    con.close()
    yr = None
    with A.app.test_request_context():
        A.db()
        yr = A.today().isoformat()[:4]
        n1, n2, again = A.doc_number(sids[0], "INV"), A.doc_number(sids[1], "INV"), A.doc_number(sids[0], "INV")
        r1 = A.doc_number(sids[2], "R")
    assert n1 == f"INV-{yr}-0001" and n2 == f"INV-{yr}-0002" and again == n1
    assert r1 == f"R-{yr}-0001"
    r = c.get(f"/session/{sids[0]}/invoice", environ_base=E)
    assert r.status_code == 200 and n1.encode() in r.data

def test_quote_built_counts_as_first_contact(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    sid = con.execute("""SELECT s.id FROM sessions s WHERE EXISTS (SELECT 1 FROM lines l WHERE l.session_id=s.id AND l.kind != 'discount')
                         LIMIT 1""").fetchone()[0]
    con.execute("DELETE FROM log WHERE session_id=?", (sid,))
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        ctx_s = A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        c_ = dict(s=ctx_s, n_log=0, t=A.totals(sid))
        assert A.AUTO["logged"][1](c_)

def test_price_list_has_no_expiry(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    r = c.get("/fees/sheet", environ_base=E)
    assert r.status_code == 200 and b"Prices as of" in r.data and b"good through" not in r.data

def test_test_button_leaves_no_marker(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    vault = tmp_path / "v"; vault.mkdir()
    r = c.post("/setup/test/primary", data=dict(primary_type="local", primary_path=str(vault), primary_init="1"), environ_base=E)
    assert r.get_json()["ok"] or "same disk" in r.get_json()["message"]
    assert not (vault / ".vault-root").exists() and not any(vault.iterdir())
    newdir = tmp_path / "made"
    c.post("/setup/test/primary", data=dict(primary_type="local", primary_path=str(newdir), primary_init="1"), environ_base=E)
    assert not newdir.exists()

def test_search_names_the_matched_address(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    hid = con.execute("SELECT id FROM households LIMIT 1").fetchone()[0]
    con.execute("UPDATE households SET address='9 Zebulon Lane' WHERE id=?", (hid,))
    hid2 = con.execute("SELECT id FROM households WHERE id != ? LIMIT 1", (hid,)).fetchone()[0]
    con.execute("UPDATE households SET address='12 Zebulon Lane' WHERE id=?", (hid2,))
    con.commit(); con.close()
    r = c.get("/search?q=zebulon", environ_base=E)
    assert r.status_code == 200 and b"Address: 9 Zebulon Lane" in r.data and b"Address: 12 Zebulon Lane" in r.data

def test_search_finds_settings_notes_and_numbers(tmp_path, monkeypatch):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json")):
        monkeypatch.setenv(k, str(tmp_path / v))
    from helpers import fresh_modules, signed_in_client
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    E_ = {"REMOTE_ADDR": "127.0.0.1"}
    page = c.get("/search?q=time+zone", environ_base=E_).get_data(as_text=True)
    assert "Settings, Business and time zone" in page
    assert "Settings, Payments and terms" in c.get("/search?q=deposit", environ_base=E_).get_data(as_text=True)
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Quill')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage, title, intake) VALUES (?, 'portrait', 'booked', 'Spring minis', ?)",
                      (hid, '{"must": "the old oak tree"}')).lastrowid
    con.execute("INSERT INTO log (household_id, session_id, summary) VALUES (?,?, 'Wants the lighthouse at dusk')", (hid, sid))
    con.execute("INSERT INTO payments (session_id, kind, amount, received, ref) VALUES (?, 'deposit', 100, '2026-11-01', '4417')", (sid,))
    con.commit(); con.close()
    for q_, line in (("lighthouse", "History: Wants the lighthouse"), ("oak tree", "Intake: the old oak tree"),
                     ("4417", "Check #4417"), ("Spring minis", "Session: Spring minis")):
        r = c.get(f"/search?q={q_}", environ_base=E_)
        body = r.get_data(as_text=True) if r.status_code == 200 else ""
        assert r.status_code == 302 or (line in body and "Quill" in body), q_
