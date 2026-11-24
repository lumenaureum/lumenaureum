import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client, unlock_new_install
from test_upgrade_from_154 import EXPECTED, _place, _same_places

E = {"REMOTE_ADDR": "127.0.0.1"}
NAME = "db-20261123-upgrade-test"

def _env(tmp_path, monkeypatch, version):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import about
    monkeypatch.setattr(about, "version", lambda: version)

def _watch_backups(monkeypatch, seen):

    from vault import backup as B

    def backup(path, *a, **kw):
        import db as dbmod
        con = dbmod.connect(path)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")} if "sessions" in tables else set()
            row = con.execute("SELECT value FROM settings WHERE key='app_version'").fetchone() \
                if "settings" in tables else None
            seen.append(dict(kind=kw.get("kind"), tables=tables, cols=cols, was=row[0] if row else None,
                             schema=con.execute("PRAGMA user_version").fetchone()[0]))
        finally:
            con.close()
        return [NAME]
    monkeypatch.setattr(B, "sealed_backup", backup)

def _open_154(tmp_path, monkeypatch, version):
    _env(tmp_path, monkeypatch, version)
    import app as A
    _same_places(monkeypatch, A, tmp_path)
    seen = []
    _watch_backups(monkeypatch, seen)
    c = signed_in_client(A)
    return A, c, seen

def _q(A, sql):
    con = A.connect()
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()

def test_a_new_version_takes_one_backup_of_the_data_as_it_was(tmp_path, monkeypatch):

    assert EXPECTED["app_version"] == "1.5.4"
    _place(tmp_path)
    A, c, seen = _open_154(tmp_path, monkeypatch, "1.6.0")
    assert [s["kind"] for s in seen] == ["upgrade"]
    at = seen[0]
    assert at["was"] == "1.5.4" and at["schema"] == 9
    assert not {"plan_dates", "installments", "resched_settled"} & at["cols"]
    assert not {"session_taxes", "deposit_credits", "crew"} & at["tables"]
    assert "plan_dates" in {r[1] for r in _q(A, "PRAGMA table_info(sessions)")}
    assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == "1.6.0"
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert "from before the update" in page.split('id="update-note"', 1)[1].split("</section>", 1)[0]

def test_backups_lists_it_as_upgrade_with_the_version_it_came_from(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, seen = _open_154(tmp_path, monkeypatch, "1.6.0")
    con = A.connect()
    try:
        con.execute("""INSERT INTO vault_objects (session_id, kind, name, backend, size, sha256, created)
                       VALUES (NULL, 'upgrade', ?, 'local', 1048576, '', '2026-11-23T09:00:00')""", (NAME,))
        con.commit()
    finally:
        con.close()
    from vault import web as W
    got = [b for b in W.backups(A.connect) if b["name"] == NAME]
    assert got and got[0]["kind"] == "upgrade" and got[0]["was"] == "1.5.4"
    page = c.get("/vault", environ_base=E).get_data(as_text=True)
    assert "<b>Before the update</b> from 1.5.4" in page

def test_the_same_version_takes_none(tmp_path, monkeypatch):
    _place(tmp_path)
    A, c, seen = _open_154(tmp_path, monkeypatch, "1.5.4")
    assert seen == []

def test_a_fresh_install_takes_none_and_later_versions_one_each(tmp_path, monkeypatch):

    _env(tmp_path, monkeypatch, "1.6.0")
    import app as A
    seen = []
    _watch_backups(monkeypatch, seen)
    signed_in_client(A)
    assert seen == []
    assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == "1.6.0"
    for version, was, n in (("1.6.0", None, 0), ("1.6.1", "1.6.0", 1), ("1.6.0", "1.6.1", 2)):
        import datakey
        _env(tmp_path, monkeypatch, version)
        import app as A
        _watch_backups(monkeypatch, seen)
        unlock_new_install(A)
        assert len(seen) == n, version
        if was:
            assert seen[-1]["kind"] == "upgrade" and seen[-1]["was"] == was
        assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == version

def test_an_install_from_before_the_record_counts_as_a_change(tmp_path, monkeypatch):

    _place(tmp_path)
    _env(tmp_path, monkeypatch, "1.5.4")
    import datakey
    import db as dbmod
    con = dbmod.connect(key=datakey.unwrap_password(EXPECTED["password"]))
    try:
        con.execute("DELETE FROM settings WHERE key='app_version'")
        con.commit()
    finally:
        con.close()
    A, c, seen = _open_154(tmp_path, monkeypatch, "1.5.4")
    assert [s["kind"] for s in seen] == ["upgrade"] and seen[0]["was"] is None

def test_a_schema_step_and_a_new_version_take_one(tmp_path, monkeypatch):

    import test_upgrade_from_141 as T141
    T141._place(tmp_path)
    _env(tmp_path, monkeypatch, "1.6.0")
    import app as A
    T141._same_places(monkeypatch, A, tmp_path)
    seen = []
    _watch_backups(monkeypatch, seen)
    signed_in_client(A)
    assert [s["kind"] for s in seen] == ["upgrade"]
    assert seen[0]["schema"] == 8 and seen[0]["was"] == T141.EXPECTED["app_version"]
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
