import base64
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _install(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

def _with_work(wrap_b64, n):

    raw = base64.b64decode(wrap_b64).split(b"\n")
    parts = raw[1].split(b" ")
    parts[3] = str(n).encode()
    raw[1] = b" ".join(parts)
    return base64.b64encode(b"\n".join(raw)).decode()

def test_the_work_factor_is_read_from_the_wrap_and_shown_on_security(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    import datakey
    ring = json.loads(datakey.KEYRING.read_text())
    assert datakey.password_work() == int(base64.b64decode(ring["password"]).split(b"\n")[1].split(b" ")[3])
    assert datakey.password_work({"password": "not base64!"}) is None
    monkeypatch.setattr(datakey, "password_work", lambda ring=None: 18)
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert 'id="pw-strength"' in page and "work factor 18" in page and "rewrap-password" not in page
    monkeypatch.setattr(datakey, "password_work", lambda ring=None: 15)
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert "work factor 15, below the\n    18 the standard age tool uses" in page and "vault.cli rewrap-password" in page

def test_a_wrap_too_costly_for_this_machine_is_said_at_sign_in(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    import datakey
    ring = json.loads(datakey.KEYRING.read_text())
    ring["password"] = _with_work(ring["password"], 30)
    datakey.KEYRING.write_text(json.dumps(ring))
    with pytest.raises(datakey.TooSlow):
        datakey.unwrap_password(TEST_PASSWORD)
    datakey.lock()
    fresh = A.app.test_client()
    r = fresh.post("/login", data=dict(email="owner@example.com", password=TEST_PASSWORD), environ_base=E)
    assert b"too slow to check it" in r.data and b"Wrong email or password" not in r.data

def test_rewrap_here_needs_the_password_and_a_stronger_but_checkable_wrap(tmp_path, monkeypatch):
    A, c = _install(tmp_path, monkeypatch)
    import datakey
    before = datakey.KEYRING.read_text()
    assert datakey.rewrap_password_here("wrong password") is None
    seq = []
    monkeypatch.setattr(datakey, "password_work", lambda ring=None: seq.pop(0))
    for old, new in ((18, 18), (18, 17), (15, 19)):
        seq[:] = [old, new]
        with pytest.raises(datakey.NotStronger):
            datakey.rewrap_password_here(TEST_PASSWORD)
        assert datakey.KEYRING.read_text() == before
    seq[:] = [15, 18]
    assert datakey.rewrap_password_here(TEST_PASSWORD) == (15, 18)
    after = json.loads(datakey.KEYRING.read_text())
    assert after["password"] != json.loads(before)["password"]
    assert datakey.unwrap_password(TEST_PASSWORD) == datakey.dk()

def test_cli_rewrap_password_on_a_copy(tmp_path, monkeypatch, capsys):
    A, c = _install(tmp_path, monkeypatch)
    import datakey
    from vault import cli
    copy = tmp_path / "copy" / "keyring.json"
    copy.parent.mkdir()
    copy.write_text(datakey.KEYRING.read_text())
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TEST_PASSWORD)
    seq = [16, 18]
    monkeypatch.setattr(datakey, "password_work", lambda ring=None: seq.pop(0))
    assert cli.main(["rewrap-password", str(copy)]) == 0
    assert "work factor 16 -> 18" in capsys.readouterr().out
    assert copy.read_text() != datakey.KEYRING.read_text()
    assert datakey.unwrap_password(TEST_PASSWORD, str(copy)) == datakey.dk()
    seq[:] = [18, 18]
    with pytest.raises(SystemExit) as e:
        cli.main(["rewrap-password", str(copy)])
    assert "no faster" in str(e.value)
