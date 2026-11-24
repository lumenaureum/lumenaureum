import io
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

class RejectPolicy:
    pass

class AutoAddPolicy:
    pass

class FakeFile(io.BytesIO):
    def __init__(self, sftp, path):
        super().__init__()
        self.sftp, self.path = sftp, path

    def set_pipelined(self, on):
        pass

    def close(self):
        self.sftp.files[self.path] = self.getvalue()
        super().close()

class FakeSFTP:
    def __init__(self):
        self.files, self.dirs, self.opened = {}, set(), []

    def stat(self, p):
        if p in self.dirs:
            return types.SimpleNamespace(st_size=0)
        if p in self.files:
            return types.SimpleNamespace(st_size=len(self.files[p]))
        raise FileNotFoundError(p)

    def mkdir(self, p):
        self.dirs.add(p)

    def open(self, p, mode):
        self.opened.append(p)
        return FakeFile(self, p)

    def posix_rename(self, a, b):
        self.files[b] = self.files.pop(a)

    def remove(self, p):
        self.files.pop(p, None)

    def close(self):
        pass

class FakeClient:
    instances = []

    def __init__(self):
        self.policy, self.host_keys, self.connected, self.sftp = None, None, None, FakeSFTP()
        FakeClient.instances.append(self)

    def load_host_keys(self, path):
        self.host_keys = path

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, **kw):
        self.connected = (host, kw)

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass

@pytest.fixture
def sftp(monkeypatch):
    fake = types.ModuleType("paramiko")
    fake.SSHClient, fake.RejectPolicy, fake.AutoAddPolicy = FakeClient, RejectPolicy, AutoAddPolicy
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    FakeClient.instances = []
    fresh_modules()
    from vault import backends as B
    return B

def test_unknown_host_keys_are_rejected(sftp, tmp_path):
    kh = tmp_path / "known_hosts"
    bk = sftp.SFTPBackend("nas.example", "crm", str(tmp_path / "id"), "/srv/vault/", known_hosts=str(kh))
    assert bk.stat("db/abc.age") is None
    cli = FakeClient.instances[-1]
    assert isinstance(cli.policy, RejectPolicy)
    assert cli.host_keys == str(kh)
    host, kw = cli.connected
    assert host == "nas.example" and kw["username"] == "crm" and kw["key_filename"] == str(tmp_path / "id")
    assert kw["look_for_keys"] is False and kw["allow_agent"] is False

@pytest.mark.parametrize("name", ["../escape.age", "db/../../escape.age", "/etc/passwd", "db\\x.age", ""])
def test_names_that_escape_the_root_are_refused(sftp, name):
    bk = sftp.SFTPBackend("nas.example", "crm", "~/id", "/srv/vault")
    with pytest.raises(ValueError):
        bk.put_stream(name, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        bk.get_stream(name, io.BytesIO())
    with pytest.raises(ValueError):
        bk.delete(name)
    assert not any(c.sftp.files for c in FakeClient.instances)

def test_writes_stay_under_the_root(sftp):
    bk = sftp.SFTPBackend("nas.example", "crm", "~/id", "/srv/vault")
    size, sha = bk.put_stream("db/abc.age", io.BytesIO(b"sealed bytes"))
    files = FakeClient.instances[-1].sftp.files
    assert list(files) == ["/srv/vault/db/abc.age"] and size == 12
    import hashlib
    assert sha == hashlib.sha256(b"sealed bytes").hexdigest()

def test_app_key_and_pinned_host_key():

    fresh_modules()
    import paramiko
    from vault import backends as B
    import settings_io
    priv, pub = B.new_ssh_key()
    assert pub.startswith("ssh-ed25519 ") and "PRIVATE KEY" in priv
    assert B.load_private_key(priv).get_base64() == pub.split()[1]
    seen = {}

    class Pinned(paramiko.SSHClient):
        def connect(self, host, **kw):
            seen.update(keys=self.get_host_keys(), kw=kw, policy=self._policy)
            raise OSError("stop here")
    host_line = "ssh-ed25519 " + pub.split()[1]
    bk = B.SFTPBackend("nas.example", "crm", "", "/srv/vault", port=2222, private_key=priv, host_key=host_line)
    old = paramiko.SSHClient
    paramiko.SSHClient = Pinned
    try:
        with pytest.raises(OSError):
            bk._open()
    finally:
        paramiko.SSHClient = old
    assert isinstance(seen["policy"], paramiko.RejectPolicy)
    assert seen["keys"].lookup("[nas.example]:2222")["ssh-ed25519"].get_base64() == pub.split()[1]
    assert seen["kw"]["pkey"] is not None and seen["kw"]["key_filename"] is None
    assert B.host_key_fingerprint(host_line).startswith("SHA256:")
    out = settings_io.export(lambda k: None, [], {"primary": {"type": "sftp", "host": "h", "user": "u", "root": "/r",
                                                             "private_key": priv, "host_key": host_line}})
    assert "private_key" not in out["storage"]["primary"]
    assert "host_key" not in out["storage"]["primary"]

