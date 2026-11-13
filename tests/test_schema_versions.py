"""Schema versions: a new database records the 1.0 baseline; numbered steps run once, in order, in one transaction;
a failing step leaves the database as it was; a database newer than the app is refused."""
import pytest

from helpers import fresh_modules, signed_in_client


def fresh_app(tmp_path, monkeypatch, backups=None):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from vault import backup as B
    monkeypatch.setattr(B, "sealed_backup", lambda *a, **kw: (backups if backups is not None else []).append(kw.get("kind")))
    return A, c


def test_baseline_and_numbered_steps(tmp_path, monkeypatch):
    taken = []
    A, c = fresh_app(tmp_path, monkeypatch, taken)
    con = A.connect()
    base = A.SCHEMA_VERSION
    assert A._schema_version(con) == base                     # a fresh install starts at the current version
    con.close()
    ran = []
    monkeypatch.setattr(A, "SCHEMA_VERSION", base + 2)
    monkeypatch.setattr(A, "MIGRATIONS", {base + 1: lambda con: (ran.append(1), con.execute("CREATE TABLE t2 (x)")),
                                          base + 2: lambda con: (ran.append(2), con.execute("INSERT INTO t2 VALUES (1)"))})
    A.init_db()
    con = A.connect()
    assert ran == [1, 2] and A._schema_version(con) == base + 2 and con.execute("SELECT x FROM t2").fetchone()[0] == 1
    assert taken == ["upgrade"]                              # a sealed backup came first
    con.close()
    A.init_db()
    assert ran == [1, 2]                                     # never twice


def test_failed_step_changes_nothing(tmp_path, monkeypatch):
    A, c = fresh_app(tmp_path, monkeypatch)
    def bad(con):
        con.execute("CREATE TABLE half (x)")
        raise RuntimeError("boom")
    base = A.SCHEMA_VERSION
    monkeypatch.setattr(A, "SCHEMA_VERSION", base + 1)
    monkeypatch.setattr(A, "MIGRATIONS", {base + 1: bad})
    with pytest.raises(RuntimeError):
        A.init_db()
    con = A.connect()
    assert A._schema_version(con) == base
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='half'").fetchone()
    con.close()


def test_newer_database_is_refused(tmp_path, monkeypatch):
    A, c = fresh_app(tmp_path, monkeypatch)
    con = A.connect()
    A.dbmod.set_user_version(con, "main", A.SCHEMA_VERSION + 1)
    con.commit(); con.close()
    with pytest.raises(A.SchemaTooNew):
        A.init_db()


def test_no_upgrade_when_the_backup_fails(tmp_path, monkeypatch):
    A, c = fresh_app(tmp_path, monkeypatch)
    from vault import backup as B
    def fail(*a, **kw):
        raise IOError("vault unreachable")
    monkeypatch.setattr(B, "sealed_backup", fail)
    base = A.SCHEMA_VERSION
    monkeypatch.setattr(A, "SCHEMA_VERSION", base + 1)
    monkeypatch.setattr(A, "MIGRATIONS", {base + 1: lambda con: con.execute("CREATE TABLE t9 (x)")})
    with pytest.raises(A.UpgradeBlocked):
        A.init_db()
    con = A.connect()
    assert A._schema_version(con) == base and not con.execute("SELECT 1 FROM sqlite_master WHERE name='t9'").fetchone()
    con.close()


def test_an_install_from_before_1_0_upgrades_with_a_backup(tmp_path, monkeypatch):
    """A version-1 database (an early release candidate) moves to the current version, taking a sealed backup first."""
    taken = []
    A, c = fresh_app(tmp_path, monkeypatch, taken)
    con = A.connect()
    A.dbmod.set_user_version(con, "main", 1)
    con.commit(); con.close()
    A.init_db()
    con = A.connect()
    assert A._schema_version(con) == A.SCHEMA_VERSION >= 2 and taken == ["upgrade"]
    con.close()
