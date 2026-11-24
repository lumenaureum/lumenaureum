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
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    return A, signed_in_client(A, setup_done=False)

def test_the_wipe_is_registered_on_lock(env):
    import datakey
    import setup
    assert setup._pending_forget in datakey.ON_LOCK

def test_locking_forgets_a_secret_typed_into_a_refused_step(env, monkeypatch):
    A, c = env
    import datakey
    import setup
    monkeypatch.setattr(setup, "test_backend", lambda cfg, **kw: "The server refused the key.")
    data = {"backup_time": "02:40", "primary_type": "s3", "primary_bucket": "studio",
            "primary_endpoint": "https://s3.example.com", "primary_access_key": "AK",
            "primary_secret_key": "typed-secret-123"}
    page = c.post("/setup/storage", data=data, environ_base=E).get_data(as_text=True)
    assert "refused" in page
    assert any(v == "typed-secret-123" for _, v in setup._PENDING.values())
    datakey.lock()
    assert setup._PENDING == {}

def test_lock_clears_every_session_held_secret(env):
    import datakey
    import setup
    setup._PENDING[("a", "primary_secret_key")] = (9e18, "one")
    setup._PENDING[("b", "second_password")] = (9e18, "two")
    datakey.lock()
    assert setup._PENDING == {}
