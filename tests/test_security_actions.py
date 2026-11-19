import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    return A, signed_in_client(A), tmp_path

def own_sid(c):
    with c.session_transaction() as s:
        return s["sid"]

def post(c, **data):
    return c.post("/security", data=data, environ_base=E)

def test_others_signs_out_every_other_device(env):
    A, c, _ = env
    import auth
    other = auth.start_session(1)
    mine = own_sid(c)
    r = post(c, current=TEST_PASSWORD, act="others")
    assert r.status_code == 200 and b"Every other device is signed out" in r.data
    assert other not in auth.SESSIONS
    assert auth.SESSIONS.get(mine) == 1

def test_wrong_current_password_changes_nothing(env):
    A, c, _ = env
    import auth
    other = auth.start_session(1)
    r = post(c, current="not the password", act="others")
    assert b"Your current password, please." in r.data
    assert auth.SESSIONS.get(other) == 1

def test_mismatched_new_passwords_are_refused(env):
    A, c, _ = env
    import auth
    import datakey
    other = auth.start_session(1)
    r = post(c, current=TEST_PASSWORD, act="password", password="a brand new password",
             password2="a different new password")
    assert b"New passwords must match" in r.data
    assert datakey.unwrap_password(TEST_PASSWORD) == datakey.dk()
    assert datakey.unwrap_password("a brand new password") is None
    assert auth.SESSIONS.get(other) == 1

def test_short_new_password_is_refused(env):
    A, c, _ = env
    import datakey
    r = post(c, current=TEST_PASSWORD, act="password", password="short", password2="short")
    assert b"New passwords must match" in r.data
    assert datakey.unwrap_password(TEST_PASSWORD) == datakey.dk()

def test_password_change_signs_out_others(env):
    A, c, _ = env
    import auth
    import datakey
    other = auth.start_session(1)
    new = "a brand new password"
    r = post(c, current=TEST_PASSWORD, act="password", password=new, password2=new)
    assert b"Password changed" in r.data
    assert datakey.unwrap_password(new) == datakey.dk()
    assert other not in auth.SESSIONS


