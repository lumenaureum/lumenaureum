"""The vault command line unlocks with the password or master key, seals a backup, lists backups, and rotates the data key."""
import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, unlock_new_install  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    con = A.connect(); con.execute("INSERT INTO households (name) VALUES ('The Harlows')"); con.commit(); con.close()
    from vault import cli
    import datakey
    datakey.lock()
    return A, cli, datakey, secret, store


def _run(cli, argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


def test_backup_then_backups_with_the_password(install, monkeypatch):
    A, cli, datakey, secret, store = install
    asked = []
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": asked.append(prompt) or TEST_PASSWORD)
    code, out = _run(cli, ["backup"])
    assert code == 0 and asked and datakey.dk() == datakey.unwrap_password(TEST_PASSWORD)
    name = out.strip().splitlines()[0]
    assert name.startswith("db/") and (store / name).is_file()
    code, out = _run(cli, ["backups"])
    assert code == 0 and name in out and " db " in out


def test_backups_with_the_master_key(install, monkeypatch):
    A, cli, datakey, secret, store = install
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": secret)
    code, out = _run(cli, ["backups"])
    assert code == 0 and datakey.dk() == datakey.unwrap_master(secret)


def test_a_wrong_password_unlocks_nothing(install, monkeypatch):
    A, cli, datakey, secret, store = install
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "not the password")
    with pytest.raises(SystemExit):
        cli.main(["backup"])
    with pytest.raises(datakey.Locked):
        datakey.dk()
    assert not [p for p in store.rglob("*") if p.is_file() and p.name != ".vault-root"]


def test_rotate_replaces_the_data_key(install, monkeypatch):
    A, cli, datakey, secret, store = install
    import rotate
    old = datakey.unwrap_password(TEST_PASSWORD)
    monkeypatch.setattr(rotate, "app_running", lambda: False)
    monkeypatch.setattr(rotate, "hold_exclusive", __import__("contextlib").nullcontext)   # the test process is the app
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TEST_PASSWORD)
    code, out = _run(cli, ["rotate"])
    assert code == 0 and "New data key" in out
    new = datakey.unwrap_password(TEST_PASSWORD)
    assert new and new != old and datakey.unwrap_master(secret) == new
    import db
    assert db.connect(key=new).execute("SELECT name FROM households").fetchone()[0] == "The Harlows"
    with pytest.raises(Exception):
        db.connect(key=old).execute("SELECT name FROM households").fetchone()


def test_rotate_refuses_a_wrong_password_and_a_running_app(install, monkeypatch):
    A, cli, datakey, secret, store = install
    import rotate
    before = datakey.unwrap_password(TEST_PASSWORD)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "not the password")
    with pytest.raises(SystemExit):
        cli.main(["rotate"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TEST_PASSWORD)
    monkeypatch.setattr(rotate, "app_running", lambda: True)
    with pytest.raises(SystemExit, match="running"):
        cli.main(["rotate"])
    assert datakey.unwrap_password(TEST_PASSWORD) == before


def test_help_and_unknown_commands(install):
    A, cli, datakey, secret, store = install
    assert _run(cli, [])[0] == 0
    code, out = _run(cli, ["nonsense"])
    assert code == 2 and "python -m vault.cli" in out
