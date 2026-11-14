import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch, all_sign=False):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"), ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_families": "1"}, environ_base=E)
    r = c.get("/settings/type/wedding", environ_base=E)
    assert r.status_code == 200
    types = [dict(t) for t in C.TYPES]
    for t in types:
        if t["key"] == "wedding":
            t["all_sign"] = bool(all_sign)
            if not all_sign:
                t.pop("all_sign")
    with A.app.test_request_context():
        A.db(); A._save_rules(TYPES=types)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Patel')")
    hid = con.execute("SELECT id FROM households WHERE name='Patel'").fetchone()[0]
    con.execute("INSERT INTO people (household_id, name, role) VALUES (?, 'Priya Patel', 'adult')", (hid,))
    con.execute("INSERT INTO people (household_id, name, role) VALUES (?, 'Sam Reyes', 'adult')", (hid,))
    con.commit()
    ids = [r[0] for r in con.execute("SELECT id FROM people WHERE household_id=? ORDER BY id", (hid,))]
    con.close()
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(hid, "wedding", subject_ids=ids)
    return A, c, sid, ids

def _agreements(A, sid):
    con = A.connect()
    rows = con.execute("SELECT about_person, status FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,)).fetchall()
    rel = con.execute("SELECT about_person FROM documents WHERE session_id=? AND kind='release'", (sid,)).fetchall()
    con.close()
    return rows, rel

def test_one_signer_by_default_then_add_the_second(tmp_path, monkeypatch):
    A, c, sid, ids = _app(tmp_path, monkeypatch)
    rows, rel = _agreements(A, sid)
    assert [r[0] for r in rows] == [ids[0]]
    assert [r[0] for r in rel] == [ids[1]]
    c.post(f"/session/{sid}/signers", data={"signer": [str(ids[0]), str(ids[1])]}, environ_base=E)
    rows, rel = _agreements(A, sid)
    assert sorted(r[0] for r in rows) == sorted(ids) and not rel
    page = c.get(f"/session/{sid}/agreement", environ_base=E).data.decode()
    assert "Priya Patel and Sam Reyes" in page and page.count("<br><br>Date") == 3
    assert "couple" not in page.lower()

def test_every_adult_signs_when_the_type_says_so(tmp_path, monkeypatch):
    A, c, sid, ids = _app(tmp_path, monkeypatch, all_sign=True)
    rows, rel = _agreements(A, sid)
    assert sorted(r[0] for r in rows) == sorted(ids) and not rel

def test_agreement_step_needs_every_signer(tmp_path, monkeypatch):
    A, c, sid, ids = _app(tmp_path, monkeypatch, all_sign=True)
    con = A.connect()
    first = con.execute("SELECT id FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id LIMIT 1", (sid,)).fetchone()[0]
    con.close()
    c.post(f"/doc/{first}", data={"status": "signed"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        assert not A.AUTO["agreement_signed"][1](dict(docs=A.q("SELECT * FROM documents WHERE session_id=?", (sid,))))

def test_at_least_one_signer(tmp_path, monkeypatch):
    A, c, sid, ids = _app(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/signers", data={}, environ_base=E)
    rows, _ = _agreements(A, sid)
    assert len(rows) == 1
