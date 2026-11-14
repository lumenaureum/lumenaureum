import io
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
    con.execute("INSERT INTO households (name) VALUES ('Harlow')")
    con.commit(); con.close()
    return A, c

def upload_face(c, hid):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (400, 400), "red").save(b, "JPEG")
    c.post(f"/client/{hid}/face", data={"photo": (io.BytesIO(b.getvalue()), "f.jpg")}, environ_base=E,
           content_type="multipart/form-data")

def test_a_face_photo_copied_to_another_household_is_refused(env):
    A, c = env
    upload_face(c, 1)
    r = c.get("/face/1", environ_base=E)
    assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"
    (A.FACES / "2.jpg").write_bytes((A.FACES / "1.jpg").read_bytes())
    r = c.get("/face/2", environ_base=E)
    assert r.status_code == 404 and b"\xff\xd8" not in r.data[:2]
    assert c.get("/face/1", environ_base=E).status_code == 200

def test_a_sealed_blob_opens_only_under_its_own_context(env):
    A, _ = env
    import datakey
    datakey.write_sealed(A.FACES / "1.jpg", b"\xff\xd8 face of household one", "face:1", label="faces")
    blob = (A.FACES / "1.jpg").read_bytes()
    assert datakey.open_bytes(blob, "face:1", label="faces") == b"\xff\xd8 face of household one"
    for aad, label in (("face:2", "faces"), ("face:10", "faces"), ("face:1", "files")):
        with pytest.raises(ValueError):
            datakey.open_bytes(blob, aad, label=label)
