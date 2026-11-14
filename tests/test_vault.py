import io
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vault import backends as B, keys as K, seal as S

def make_backends(tmp_path):
    out = [B.LocalBackend.init_root(str(tmp_path / "local"))]
    if os.environ.get("VAULT_SFTP_KEY"):
        out.append(B.SFTPBackend("127.0.0.1", os.environ["USER"], os.environ["VAULT_SFTP_KEY"],
                                 str(tmp_path / "sftp"), known_hosts=os.environ["VAULT_SFTP_KNOWN"]))
    return out

@pytest.fixture
def s3():
    from moto import mock_aws
    with mock_aws():
        import boto3
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vault-test")
        yield B.S3Backend("vault-test", "x", "y", region="us-east-1", prefix="lumen")

@pytest.fixture
def con(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    K.init(c)
    return c

def contract(bk):
    data = os.urandom(3 * 1024 * 1024 + 17)
    size, sha = bk.put_stream("s/abc.age", io.BytesIO(data))
    assert size == len(data) and len(sha) == 64
    assert bk.stat("s/abc.age") == len(data)
    out = io.BytesIO(); bk.get_stream("s/abc.age", out)
    assert out.getvalue() == data
    assert "s/abc.age" in bk.list("s/")
    assert not any(n.endswith(".part") for n in bk.list())
    bk.delete_verified("s/abc.age")
    assert bk.stat("s/abc.age") is None
    bk.delete("s/never-there.age")
    for bad in ("../x", "/abs", "a/../../b"):
        with pytest.raises(ValueError):
            bk.stat(bad)

def test_contract_local_and_sftp(tmp_path):
    for bk in make_backends(tmp_path):
        contract(bk)

def test_contract_s3(s3):
    contract(s3)

class Boom(io.RawIOBase):
    def __init__(self): self.n = 0
    def readable(self): return True
    def readinto(self, b):
        self.n += 1
        if self.n > 3:
            raise IOError("network dropped")
        b[:1024] = b"x" * 1024
        return 1024

def test_interrupted_upload_leaves_nothing(tmp_path):
    for bk in make_backends(tmp_path):
        with pytest.raises(Exception):
            bk.put_stream("s/half.age", io.BufferedReader(Boom()))
        assert bk.stat("s/half.age") is None
        assert bk.list() == []

def test_seal_open_shred(tmp_path, con, s3):
    secret, pub = K.generate_master()
    K.set_master_pub(con, pub)
    src = tmp_path / "2026-10-28_castellano"
    (src / "sub").mkdir(parents=True)
    (src / "a.NEF").write_bytes(os.urandom(200_000)); (src / "sub" / "b.jpg").write_bytes(b"jpeg!")
    bks = make_backends(tmp_path) + [s3]
    name = S.seal_session(con, 7, [str(src)], bks)
    assert "castellano" not in name and name.startswith("s/") and name.endswith(".age")
    rows = con.execute("SELECT backend, sha256 FROM vault_objects WHERE session_id=7 AND kind='session'").fetchall()
    assert len(rows) == len(bks) and len({r[1] for r in rows}) == 1

    raw = io.BytesIO(); bks[0].get_stream(name, raw)
    assert b"jpeg!" not in raw.getvalue() and raw.getvalue().startswith(b"age-encryption.org/v1")

    assert "AGE-SECRET-KEY" not in "".join(str(r) for r in con.execute("SELECT * FROM vault_keys").fetchall())

    for bk in bks:
        dest = tmp_path / f"open-{bk.kind}"
        S.open_session(con, 7, secret, bk, str(dest))
        assert (dest / src.name / "sub" / "b.jpg").read_bytes() == b"jpeg!"

    other, _ = K.generate_master()
    with pytest.raises(Exception):
        S.open_session(con, 7, other, bks[0], str(tmp_path / "nope"))

    keep = io.BytesIO(); bks[0].get_stream(name, keep)
    S.destroy_session(con, 7, bks)
    for bk in bks:
        assert bk.stat(name) is None
    with pytest.raises(KeyError):
        K.session_identity(con, 7, secret)
    import pyrage
    with pytest.raises(Exception):
        pyrage.decrypt(keep.getvalue(), [K.parse_secret(secret)])

def test_seal_db_backup_and_restore(tmp_path, con):
    secret, pub = K.generate_master()
    K.set_master_pub(con, pub)
    db = tmp_path / "crm-copy.db"
    c2 = sqlite3.connect(db); c2.execute("CREATE TABLE t (x)"); c2.execute("INSERT INTO t VALUES ('Dana Harlow')"); c2.commit(); c2.close()
    bk = make_backends(tmp_path)[0]
    name = S.seal_file(con, str(db), [bk])
    raw = io.BytesIO(); bk.get_stream(name, raw)
    assert b"Dana Harlow" not in raw.getvalue()
    out = tmp_path / "restored.db"
    S.open_file(name, secret, bk, str(out))
    assert sqlite3.connect(out).execute("SELECT x FROM t").fetchone()[0] == "Dana Harlow"

def test_no_master_key_refuses(tmp_path, con):
    (tmp_path / "f").mkdir(); (tmp_path / "f" / "x").write_text("x")
    with pytest.raises(RuntimeError):
        S.seal_session(con, 1, [str(tmp_path / "f")], make_backends(tmp_path))

def test_unmarked_local_root_is_refused(tmp_path):

    (tmp_path / "empty-mountpoint").mkdir()
    with pytest.raises(IOError):
        B.LocalBackend(str(tmp_path / "empty-mountpoint"))
    with pytest.raises(IOError):
        B.LocalBackend(str(tmp_path / "missing"))
    assert not (tmp_path / "missing").exists()

def test_split_seal_opens_whole(tmp_path, con):

    secret, pub = K.generate_master(); K.set_master_pub(con, pub)
    a = tmp_path / "raw"; a.mkdir(); (a / "1.NEF").write_bytes(b"one")
    b = tmp_path / "export"; b.mkdir(); (b / "1.jpg").write_bytes(b"two")
    bks = make_backends(tmp_path)[:1]
    S.seal_session(con, 3, [str(a)], bks)
    S.seal_session(con, 3, [str(b)], bks)
    S.open_session(con, 3, secret, bks[0], str(tmp_path / "out"))
    assert (tmp_path / "out" / "raw" / "1.NEF").read_bytes() == b"one" and (tmp_path / "out" / "export" / "1.jpg").read_bytes() == b"two"

def test_failed_seal_leaks_no_thread(tmp_path, con):
    import threading
    secret, pub = K.generate_master(); K.set_master_pub(con, pub)
    src = tmp_path / "big"; src.mkdir(); (src / "x.NEF").write_bytes(os.urandom(4_000_000))

    class Dead(B.LocalBackend):
        def put_stream(self, name, reader, content_type=None):
            reader.read(1000)
            raise IOError("NAS went away")
    dead = Dead.init_root(str(tmp_path / "dead"))
    before = threading.active_count()
    existing = set(threading.enumerate())
    for _ in range(3):
        with pytest.raises(IOError):
            S.seal_session(con, 9, [str(src)], [dead])
    for t in set(threading.enumerate()) - existing:
        t.join(timeout=5)
    assert threading.active_count() <= before
    assert (src / "x.NEF").exists()

def test_daily_copy_to_second_location(tmp_path, con, s3):
    from vault import sync as SY
    secret, pub = K.generate_master(); K.set_master_pub(con, pub)
    primary = B.LocalBackend.init_root(str(tmp_path / "p"))
    f = tmp_path / "f"; f.mkdir(); (f / "a.NEF").write_bytes(os.urandom(50_000))
    S.seal_session(con, 1, [str(f)], [primary])
    db = tmp_path / "x.db"; db.write_bytes(b"backup")
    S.seal_file(con, str(db), [primary])
    assert s3.list() == []
    assert SY.run(con, primary, s3) == (3, 0)
    assert len(s3.list()) == 3
    assert SY.run(con, primary, s3) == (0, 0)
    S.destroy_session(con, 1, [primary])
    assert SY.run(con, primary, s3) == (0, 2)
    assert len(s3.list()) == 1
