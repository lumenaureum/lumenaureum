import os
import runpy
import sys

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

def test_unedited_agreement_prints_the_settings_text(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    import wording
    con = A.connect()
    sid = con.execute("SELECT id FROM sessions WHERE type='family' LIMIT 1").fetchone()[0]
    con.close()
    page = c.get(f"/session/{sid}/agreement", environ_base=E).data.decode()
    for line in wording.STANDARD["agreement"].splitlines():
        if line.startswith("# "):
            head = line[2:].split(". ", 1)[-1]
            if head not in ("Minors", "Products in my care"):
                assert head.replace('"', "&#34;") in page or head in page, head
    assert "I arrange the permits" not in page and "This page is our whole agreement" not in page
    assert "This agreement and the quote are our whole agreement." in page

def test_release_uses_the_standard_text(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    did = con.execute("SELECT id FROM documents WHERE kind='release' LIMIT 1").fetchone()[0]
    con.close()
    page = c.get(f"/doc/{did}/release", environ_base=E).data.decode()
    assert "Permission to photograph" in page and "How the photos are used" in page

def test_session_quote_address_and_send_row(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    sid = con.execute("SELECT session_id FROM quote_versions LIMIT 1").fetchone()
    nosent = con.execute("SELECT id FROM sessions WHERE id NOT IN (SELECT session_id FROM quote_versions) LIMIT 1").fetchone()[0]
    con.close()
    if sid:
        r = c.get(f"/session/{sid[0]}/quote", environ_base=E)
        assert r.status_code == 302 and "/quote/" in r.headers["Location"]
        page = c.get(r.headers["Location"], environ_base=E).data.decode()
        assert "mailto:" in page and "Email it" in page
    r = c.get(f"/session/{nosent}/quote", environ_base=E)
    assert r.status_code == 302 and "#billing" in r.headers["Location"]

def test_mark_sent_closes_the_session_details_step(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    row = con.execute("SELECT id, session_id FROM tasks WHERE title='Send session details' AND done IS NULL LIMIT 1").fetchone()
    con.close()
    assert row, "demo has an open Send session details step"
    tid, sid = row
    page = c.get(f"/session/{sid}/details", environ_base=E).data.decode()
    assert "Mark sent" in page and f"/task/{tid}" in page and "mailto:" in page
    c.post(f"/task/{tid}", data={"back": f"/session/{sid}/details"}, environ_base=E)
    page = c.get(f"/session/{sid}/details", environ_base=E).data.decode()
    assert "Mark sent" not in page and "Sent " in page

def test_example_documents_disclaimer(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    import wording
    assert "examples only" in wording.DISCLAIMER and "not legal advice" in wording.DISCLAIMER
    for url in ("/settings/wording/agreement", "/settings?tab=documents"):
        assert "examples only" in c.get(url, environ_base=E).data.decode()
    con = A.connect()
    sid = con.execute("SELECT id FROM sessions WHERE type='family' LIMIT 1").fetchone()[0]
    did = con.execute("SELECT id FROM documents WHERE kind='release' LIMIT 1").fetchone()[0]
    con.execute("DELETE FROM settings WHERE key='agreement_reviewed'"); con.commit(); con.close()
    assert "SAMPLE: example wording" in c.get(f"/session/{sid}/agreement", environ_base=E).data.decode()
    assert "SAMPLE: example wording" in c.get(f"/doc/{did}/release", environ_base=E).data.decode()
    assert "examples only" in open(os.path.join(ROOT, "README.md")).read()

def test_a_reviewed_document_stays_reviewed(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    sid = con.execute("SELECT id FROM sessions WHERE type='family' LIMIT 1").fetchone()[0]
    con.close()
    url = f"/session/{sid}/agreement"
    c.post("/settings/wording/agreement", data={"text": "# Terms\\nOne."}, environ_base=E)
    assert "SAMPLE: example wording" in c.get(url, environ_base=E).data.decode()
    c.post("/settings/review/agreement", environ_base=E)
    assert "SAMPLE: example wording" not in c.get(url, environ_base=E).data.decode()
    c.post("/settings/wording/agreement", data={"text": "# Terms\\nTwo."}, environ_base=E)
    assert "SAMPLE: example wording" not in c.get(url, environ_base=E).data.decode()
    c.post("/settings/review/agreement", data={"revert": "1"}, environ_base=E)
    page = c.get(url, environ_base=E).data.decode()
    assert "SAMPLE: example wording" in page and "Two." not in page
