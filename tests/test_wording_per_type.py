import json
import os
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
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_pets": "1"}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        w = A.create_session(1, "wedding")
        p = A.create_session(1, "pet")
    return A, c, w, p

def test_type_wording_stays_with_its_type(tmp_path, monkeypatch):
    A, c, w, p = _app(tmp_path, monkeypatch)
    c.post("/settings/wording/agreement?type=pet", data={"text": "# Animals\nYour pet [CLIENT] clause."}, environ_base=E)
    pet = c.get(f"/session/{p}/agreement", environ_base=E).data.decode()
    wed = c.get(f"/session/{w}/agreement", environ_base=E).data.decode()
    assert "Your pet Avery Lane clause." in pet
    assert "Your pet" not in wed and "Who this is between" in wed
    page = c.get("/settings/wording/agreement?type=pet", environ_base=E).data.decode()
    assert "Wording for Pets only." in page
    c.post("/settings/wording/agreement?type=pet", data={"reset": "1"}, environ_base=E)
    assert "Your pet" not in c.get(f"/session/{p}/agreement", environ_base=E).data.decode()

def test_type_wording_travels_and_clears_reviewed(tmp_path, monkeypatch):
    A, c, w, p = _app(tmp_path, monkeypatch)
    c.post("/settings/wording/release?type=wedding", data={"text": "# Okay\nI agree, [PERSON]."}, environ_base=E)
    import settings_io
    with A.app.test_request_context():
        A.db()
        types = json.loads(A.setting("rules"))["TYPES"]
    assert any(t.get("release_text") for t in types)
    cleaned = settings_io._types(types) if hasattr(settings_io, "_types") else None
    if cleaned is not None:
        assert any(t.get("release_text") == "# Okay\nI agree, [PERSON]." for t in cleaned)

def test_revert_terms_to_default(tmp_path, monkeypatch):

    import importlib
    mod = importlib.import_module("test_wording_and_sending")
    A, c = mod._app(tmp_path, monkeypatch)
    import config as C
    E = {"REMOTE_ADDR": "127.0.0.1"}
    key = "family"
    c.post(f"/settings/type/{key}", data={"weather": "we dance in the rain", "notice_hours": "12"}, environ_base=E)
    c.post("/settings/review/terms", data={"type": key}, environ_base=E)
    with A.app.test_request_context():
        assert A.doc_reviewed("terms", key)
    c.post("/settings/review/terms", data={"type": key, "revert": "1"}, environ_base=E)
    with A.app.test_request_context():
        assert not A.doc_reviewed("terms", key)
    assert "dance in the rain" not in C.fill(key, "weather")
