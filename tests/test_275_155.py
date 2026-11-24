import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, HttpsClient, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = "JBSWY3DPEHPK3PXP"
OWNER = "owner@example.com"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    signed_in_client(A)
    import datakey
    datakey.lock()
    A.app.test_client_class = HttpsClient
    return A

def _ring():
    import datakey
    return json.loads(datakey.KEYRING.read_text())

def _count_db_opens(monkeypatch):
    import auth
    opened = []
    real = auth.dbmod.connect

    def spy(*a, **kw):
        opened.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(auth.dbmod, "connect", spy)
    return opened

def _sign_in(A, email=OWNER, when=None):
    import auth
    c = A.app.test_client()
    r = c.post("/login", data=dict(email=email, password=TEST_PASSWORD), environ_base=E)
    if "/login/2fa" in r.headers.get("Location", ""):
        c.post("/login/2fa", data=dict(code=auth.totp(SECRET, when or time.time())), environ_base=E)
    return c, r

def test_an_old_keyring_still_signs_in_and_gets_the_hash_at_the_first_good_sign_in(env):
    import datakey
    assert "owner_email" not in _ring()
    c, r = _sign_in(env)
    assert datakey.is_unlocked()
    tag = _ring().get("owner_email")
    assert tag and OWNER not in json.dumps(_ring())
    key = datakey.dk()
    assert datakey.unwrap_password(TEST_PASSWORD) == key
    assert datakey.owner_email_matches(OWNER, key) is True
    assert datakey.owner_email_matches("  Owner@Example.com ", key) is True
    assert datakey.owner_email_matches("someone@example.com", key) is False

def test_an_old_keyring_with_a_wrong_email_falls_back_to_the_database_and_refuses(env):
    import datakey
    c, r = _sign_in(env, email="someone@example.com")
    assert "/login/2fa" not in r.headers.get("Location", "") and not datakey.is_unlocked()
    assert "owner_email" not in _ring()

def test_with_the_hash_a_wrong_email_never_opens_the_database(env, monkeypatch):
    import datakey
    _sign_in(env)
    assert _ring().get("owner_email")
    datakey.lock()
    opened = _count_db_opens(monkeypatch)
    c, r = _sign_in(env, email="someone@example.com")
    assert r.status_code == 200 and b"/login/2fa" not in r.data
    assert not datakey.is_unlocked() and opened == []
    c2 = env.app.test_client()
    r2 = c2.post("/login", data=dict(email=OWNER, password="wrong wrong wrong"), environ_base=E)
    assert r2.status_code == 200 and opened == []

def test_with_the_hash_the_right_email_goes_to_two_factor_without_the_database(env, monkeypatch):
    import datakey
    _sign_in(env)
    datakey.lock()
    opened = _count_db_opens(monkeypatch)
    c = env.app.test_client()
    r = c.post("/login", data=dict(email=" OWNER@example.com", password=TEST_PASSWORD), environ_base=E)
    assert "/login/2fa" in r.headers["Location"] and opened == []

def test_a_removed_or_edited_hash_is_caught_by_the_keyring_check(env):
    import datakey
    _sign_in(env)
    datakey.lock()
    ring = _ring()
    ring["owner_email"] = "0" * 64
    datakey.KEYRING.write_text(json.dumps(ring))
    with pytest.raises(ValueError):
        datakey.unwrap_password(TEST_PASSWORD)
    ring.pop("owner_email")
    datakey.KEYRING.write_text(json.dumps(ring))
    with pytest.raises(ValueError):
        datakey.unwrap_password(TEST_PASSWORD)

def test_changing_the_owner_email_updates_the_hash(env):
    import datakey
    _sign_in(env)
    key = datakey.dk()
    assert datakey.set_owner_email("new@example.com", key) is True
    assert datakey.owner_email_matches("new@example.com", key) is True
    assert datakey.owner_email_matches(OWNER, key) is False
    assert datakey.set_owner_email("new@example.com", key, only_if_missing=True) is False
    assert datakey.unwrap_password(TEST_PASSWORD) == key

def test_a_new_keyring_carries_the_hash_from_the_start(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import secrets
    import datakey
    from vault import keys as K
    _, pub = K.generate_master()
    key = secrets.token_bytes(32)
    datakey.create_from(key, datakey.password_wrap(key, TEST_PASSWORD), pub, owner_email=OWNER)
    assert datakey.unwrap_password(TEST_PASSWORD) == key
    assert datakey.owner_email_matches(OWNER, key) is True
    new = secrets.token_bytes(32)
    out = tmp_path / "ring.staged"
    datakey.new_ring_for(new, TEST_PASSWORD, pub, path=out, owner_email=OWNER)
    assert datakey.owner_email_matches(OWNER, new, path=out) is True
