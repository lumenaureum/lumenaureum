import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, unlock_new_install

E = {"REMOTE_ADDR": "127.0.0.1"}

def test_dropping_without_a_token_keeps_other_pending_sign_ins():
    fresh_modules()
    import datakey
    tok = datakey.hold_pending(b"k" * 32, {})
    datakey.drop_pending(None)
    assert datakey.take_pending(tok)

def test_right_password_wrong_email_reads_as_wrong(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    unlock_new_install(A)
    c = A.app.test_client()
    r = c.post("/login", data={"email": "nobody@example.com", "password": TEST_PASSWORD}, environ_base=E,
               base_url="https://localhost")
    assert r.status_code == 200 and b"Wrong email or password" in r.data

def test_imported_storage_paths_and_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    fresh_modules()
    import settings_io
    for bad in ({"primary": {"type": "local", "path": str(tmp_path / "data")}},
                {"primary": {"type": "sftp", "host": "h", "user": "u", "root": "/r", "key_file": str(tmp_path / "data" / "keyring.json")}},
                {"primary": {"type": "sftp", "host": "h", "user": "u", "root": "/r", "port": "22x"}},
                {"primary": {"type": "sftp", "host": "h", "user": "u", "root": "/r", "port": 70000}}):
        with pytest.raises(settings_io.ImportProblem):
            settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "storage": bad}), {})

def test_plain_http_s3_refused_in_the_backend():
    fresh_modules()
    from vault import backends as B
    with pytest.raises(ValueError):
        B.S3Backend("b", "a", "s", endpoint="http://93.184.215.14")
    with pytest.raises(ValueError):
        B.S3Backend("b", "a", "s", endpoint="ftp://192.168.20.5")
    B.S3Backend("b", "a", "s", endpoint="http://192.168.20.5:9000")
