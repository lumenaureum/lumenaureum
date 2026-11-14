import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, unlock_new_install

def test_backup_restore_round_trip_and_cli(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    con = A.connect(); con.execute("INSERT INTO households (name) VALUES ('The Harlows')"); con.commit(); con.close()
    from vault import backup as BK, cli
    names = [BK.sealed_backup(str(A.DB_PATH), None, keep=2)[0] for _ in range(3)]
    con = A.connect()
    live = [r[0] for r in con.execute("SELECT DISTINCT name FROM vault_objects WHERE kind='db' AND deleted IS NULL")]
    con.close()
    assert len(live) == 2 and names[0] not in live
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["backups"]) == 0
    assert names[-1] in out.getvalue()
    dest = tmp_path / "restored"
    BK.restore(names[-1], secret, str(dest))
    import datakey, db
    k = datakey.unwrap_master(secret, ring_path=str(dest / "keyring.json"))
    assert db.connect(dest / "crm.db", key=k).execute("SELECT name FROM households").fetchone()[0] == "The Harlows"
    assert (dest / "settings.json").exists() and (dest / "storage.json").exists()
    assert json.loads((dest / "settings.json").read_text()).get("lumen_aureum_settings")
    assert (dest / "storage.json").read_bytes() == (tmp_path / "storage.json").read_bytes()
    only = tmp_path / "settings-only"
    BK.restore(names[-1], secret, str(only), settings_only=True)
    assert sorted(p.name for p in only.iterdir()) == ["settings.json"]
    with pytest.raises(FileExistsError):
        BK.restore(names[-1], secret, str(dest))
    with pytest.raises(Exception):
        BK.restore(names[-1], "AGE-SECRET-KEY-1" + "Q" * 58, str(tmp_path / "wrong"))
    assert not (tmp_path / "wrong").exists()

def test_master_key_replacement(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    old = unlock_new_install(A)
    from vault import keys as K
    con = A.connect(); con.execute("INSERT INTO households (name) VALUES ('H')")
    con.execute("INSERT INTO sessions (household_id, type) VALUES (1, 'family')"); con.commit()
    K.session_pub(con, 1)
    new_secret, new_pub = K.generate_master()
    with pytest.raises(Exception):
        K.replace_master(con, K.generate_master()[0], new_pub)
    assert K.replace_master(con, old, new_pub) == 1
    K.session_identity(con, 1, new_secret)
    with pytest.raises(Exception):
        K.session_identity(con, 1, old)
    con.close()
    import datakey
    datakey.replace_master(new_pub)
    assert datakey.unwrap_master(new_secret) == datakey.dk()
    with pytest.raises(ValueError):
        datakey.unwrap_master(old)

def test_vault_page_back_up_now_list_and_check(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    from helpers import signed_in_client
    c = signed_in_client(A)
    secret = c.master_secret if hasattr(c, "master_secret") else None
    E = {"REMOTE_ADDR": "127.0.0.1"}
    if secret is None:
        pytest.skip("this helper does not expose the master key")
    c.post("/vault/backup-now", environ_base=E)
    page = c.get("/vault", environ_base=E).get_data(as_text=True)
    assert "Backed up." in page and "<h2" in page and "MB" in page
    c.post("/vault/check", data={"secret": secret}, environ_base=E)
    assert "intact" in c.get("/vault", environ_base=E).get_data(as_text=True)
    c.post("/vault/check", data={"secret": "AGE-SECRET-KEY-1" + "Q" * 58}, environ_base=E)
    page = c.get("/vault", environ_base=E).get_data(as_text=True)
    assert "intact" not in page and "Backups:" in page

def test_back_up_now_never_pushes_out_a_nightly(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    unlock_new_install(A)
    from vault import backup as BK
    nightly = BK.sealed_backup(str(A.DB_PATH), None, keep=2)[0]
    for _ in range(BK.MANUAL_KEEP + 3):
        BK.sealed_backup(str(A.DB_PATH), None, kind="manual", keep=2)
    con = A.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM vault_objects WHERE name=? AND deleted IS NULL", (nightly,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(DISTINCT name) FROM vault_objects WHERE kind='manual' AND deleted IS NULL").fetchone()[0] == BK.MANUAL_KEEP
    finally:
        con.close()
