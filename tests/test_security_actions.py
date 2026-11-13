"""The security page signs out other devices, turns unattended unlock on and off safely, and refuses a wrong current password or mismatched new passwords."""
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client  # noqa: E402

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


def other_volume_dir(data_dir):
    """A writable folder on a different device from data_dir, or None."""
    data_dev = os.stat(data_dir).st_dev
    for cand in ("/dev/shm", "/run/user/%d" % os.getuid(), tempfile.gettempdir()):
        if os.path.isdir(cand) and os.access(cand, os.W_OK) and os.stat(cand).st_dev != data_dev:
            return cand
    return None


@pytest.fixture
def key_dir(tmp_path):
    base = other_volume_dir(tmp_path)
    if not base:
        pytest.skip("no writable folder on a second volume")
    d = tempfile.mkdtemp(dir=base)
    yield d
    shutil.rmtree(d, ignore_errors=True)


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


def test_auto_unlock_on_and_off(env, key_dir, monkeypatch):
    A, c, _ = env
    import datakey
    key_file = os.path.join(key_dir, "unlock.key")
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", key_file)
    r = post(c, current=TEST_PASSWORD, act="auto_on")
    assert b"Unattended unlock is on." in r.data, r.data
    assert datakey.auto_enabled()
    assert oct(os.stat(key_file).st_mode)[-3:] == "600"
    assert datakey.try_auto_unlock() == datakey.dk()
    r = post(c, current=TEST_PASSWORD, act="auto_off")
    assert b"Unattended unlock is off." in r.data
    assert not datakey.auto_enabled()
    assert not os.path.exists(key_file)


def test_auto_unlock_refused_on_the_data_volume(env, monkeypatch):
    A, c, tmp = env
    import datakey
    key_file = tmp / "keys" / "unlock.key"
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(key_file))
    r = post(c, current=TEST_PASSWORD, act="auto_on")
    assert b"different volume" in r.data
    assert not datakey.auto_enabled()
    assert not key_file.exists()


def test_auto_unlock_refused_without_a_key_file_path(env):
    A, c, _ = env
    import datakey
    r = post(c, current=TEST_PASSWORD, act="auto_on")
    assert b"CRM_UNLOCK_KEY_FILE" in r.data
    assert not datakey.auto_enabled()


def test_auto_on_needs_the_current_password(env, key_dir, monkeypatch):
    A, c, _ = env
    import datakey
    key_file = os.path.join(key_dir, "unlock.key")
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", key_file)
    r = post(c, current="wrong", act="auto_on")
    assert b"Your current password, please." in r.data
    assert not datakey.auto_enabled() and not os.path.exists(key_file)
