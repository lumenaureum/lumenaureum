import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules
from test_auth import E, claim

@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = A.app.test_client()
    claim(c, A)
    return A, A.app.test_client()

def post(c):
    return c.post("/login", data=dict(email="owner@example.com", password="anything"), environ_base=E)

def test_busy_is_not_counted(owner, monkeypatch):
    A, c = owner
    import auth

    def busy(pw):
        raise auth.Busy()
    monkeypatch.setattr(auth, "unwrap_password_limited", busy)
    th = A.app.config["auth_throttle"]
    for _ in range(auth.MAX_FAILS * 2):
        assert b"Busy checking other sign-ins" in post(c).data
    assert not th.ip_blocked(E["REMOTE_ADDR"])
    assert not th._trim(E["REMOTE_ADDR"])
    assert not any(ip == E["REMOTE_ADDR"] for _, ip, _ in th.log)

def test_wrong_passwords_still_lock(owner, monkeypatch):
    A, c = owner
    import auth
    monkeypatch.setattr(auth, "unwrap_password_limited", lambda pw: None)
    th = A.app.config["auth_throttle"]
    for _ in range(auth.MAX_FAILS):
        post(c)
    assert th.ip_blocked(E["REMOTE_ADDR"])

def test_busy_between_wrong_passwords_adds_nothing(owner, monkeypatch):
    A, c = owner
    import auth
    th = A.app.config["auth_throttle"]
    monkeypatch.setattr(auth, "unwrap_password_limited", lambda pw: None)
    for _ in range(auth.MAX_FAILS - 1):
        post(c)

    def busy(pw):
        raise auth.Busy()
    monkeypatch.setattr(auth, "unwrap_password_limited", busy)
    for _ in range(3):
        post(c)
    assert not th.ip_blocked(E["REMOTE_ADDR"])
