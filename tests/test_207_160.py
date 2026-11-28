import io
import os
import re
import socket
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

paramiko = pytest.importorskip("paramiko")
E = {"REMOTE_ADDR": "127.0.0.1"}
PASSWORD = "correct horse battery 207"

class _Server(paramiko.ServerInterface):
    def __init__(self, rec):
        self.rec = rec

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        self.rec["passwords"].append((username, password))
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("studio", PASSWORD) else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.rec["keys"].append(username)
        return paramiko.AUTH_FAILED

class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

class _SFTP(paramiko.SFTPServerInterface):
    ROOT = None

    def _real(self, path):
        return os.path.join(self.ROOT, self.canonicalize(path).lstrip("/"))

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
    lstat = stat

    def list_folder(self, path):
        try:
            out = []
            for n in os.listdir(self._real(path)):
                a = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._real(path), n)))
                a.filename = n
                out.append(a)
            return out
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def open(self, path, flags, attr):
        p = self._real(path)
        try:
            fd = os.open(p, flags | getattr(os, "O_BINARY", 0), 0o644)
            mode = "wb" if flags & os.O_WRONLY else ("r+b" if flags & os.O_RDWR else "rb")
            f = os.fdopen(fd, mode)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        h = _Handle(flags)
        h.filename, h.readfile, h.writefile = p, f, f
        return h

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rename(self, old, new):
        try:
            os.rename(self._real(old), self._real(new))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK
    posix_rename = rename

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

@pytest.fixture
def sftpd(tmp_path):

    root = tmp_path / "srv"
    root.mkdir()
    host_key = paramiko.Ed25519Key.from_private_key(io.StringIO(_ed25519_pem()))
    rec = dict(passwords=[], keys=[], root=str(root), line=f"{host_key.get_name()} {host_key.get_base64()}")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    rec["port"] = sock.getsockname()[1]
    stop = threading.Event()

    class Sftp(_SFTP):
        ROOT = str(root)

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, Sftp)
            try:
                t.start_server(server=_Server(rec))
            except Exception:
                continue
    th = threading.Thread(target=serve, daemon=True)
    th.start()
    yield rec
    stop.set()
    sock.close()

def _ed25519_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()

def _other_line():
    k = paramiko.Ed25519Key.from_private_key(io.StringIO(_ed25519_pem()))
    return f"{k.get_name()} {k.get_base64()}"

def cfg(sftpd, **kw):
    c = dict(type="sftp", host="127.0.0.1", port=sftpd["port"], user="studio", root="g", auth="password",
             password=PASSWORD, host_key=sftpd["line"])
    c.update(kw)
    return c

def test_password_sign_in_writes_reads_and_removes(sftpd, tmp_path):
    fresh_modules()
    from vault import backends as B
    be = B.from_config(cfg(sftpd))
    assert isinstance(be, B.SFTPBackend) and be.by_password
    with be.session():
        size, _ = be.put_stream("abc/one.bin", io.BytesIO(b"sealed"))
        out = io.BytesIO()
        be.get_stream("abc/one.bin", out)
        assert size == 6 and out.getvalue() == b"sealed" and be.list() == ["abc/one.bin"]
        be.delete_verified("abc/one.bin")
    assert sftpd["passwords"] and set(sftpd["passwords"]) == {("studio", PASSWORD)}
    assert sftpd["keys"] == []
    assert PASSWORD not in repr(be) and PASSWORD not in str(vars(be).get("host"))

def test_the_password_is_never_sent_to_a_server_whose_key_was_not_trusted(sftpd, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    from vault import backends as B
    (tmp_path / "none").write_text("")
    for c in (cfg(sftpd, host_key=_other_line()),
              {k: v for k, v in cfg(sftpd).items() if k != "host_key"}):
        with pytest.raises(paramiko.SSHException):
            B.from_config(dict(c, known_hosts=str(tmp_path / "none"))).stat("x")
    assert sftpd["passwords"] == []

def test_a_wrong_password_reads_plainly_and_never_shows_it(sftpd, monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import setup
    c = cfg(sftpd, password="not it 99")
    msg = setup.test_backend(c, keep=False)
    assert msg == "The server refused the user name or password." and "not it 99" not in msg
    assert setup.test_backend(cfg(sftpd, password=""), keep=False).startswith("Type the password")
    assert setup.test_backend(cfg(sftpd), keep=False) == ""

def test_the_password_is_dropped_from_memory_on_lock(sftpd, tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    signed_in_client(A)
    import datakey
    import setup
    from vault import backends as B
    be = B.from_config(cfg(sftpd))
    with be.session():
        be.put_stream("a.bin", io.BytesIO(b"x"))
        assert be._held
        datakey.lock()
        assert be._password == "" and be._held is None
        with pytest.raises(B.PasswordMissing):
            be.stat("a.bin")
    assert len(sftpd["passwords"]) == 1

def test_a_saved_password_only_carries_to_the_same_account():
    fresh_modules()
    from vault import backends as B
    old = dict(type="sftp", host="files.example.com", user="studio", root="g", auth="password", password="pw")
    assert B.carry_secrets(old, dict(old, password=None, root="other"))["password"] == "pw"
    assert B.carry_secrets(old, dict(old, password=None, host="FILES.example.com"))["password"] == "pw"
    for change in (dict(host="evil.example.net"), dict(user="someone"), dict(port=2222)):
        new = {k: v for k, v in dict(old, **change).items() if k != "password"}
        assert "password" not in B.carry_secrets(old, new)
    s3 = dict(type="s3", bucket="b", secret_key="sk")
    assert B.carry_secrets(s3, dict(s3, secret_key=None, bucket="c"))["secret_key"] == "sk"

def test_settings_files_never_carry_the_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import settings_io
    gh = dict(type="sftp", host="h.example.com", user="u", root="g", auth="password", password="secret-pw-207")
    out = settings_io.export(lambda k: None, [], {"gallery_host": gh})
    assert "password" not in out["storage"]["gallery_host"] and out["storage"]["gallery_host"]["auth"] == "password"
    assert "secret-pw-207" not in repr(out)

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "check_host", lambda *a, **kw: None, raising=False)
    return A, signed_in_client(A, setup_done=False), tmp_path

def _gallery_form(sftpd, **kw):
    f = dict(gallery_days="30", unopened_days="14", gallery_base_url="https://example.com/g/", gallery_type="sftp",
             gallery_host="127.0.0.1", gallery_port=str(sftpd["port"]), gallery_user="studio", gallery_root="g",
             gallery_keymode="password", gallery_password=PASSWORD)
    f.update(kw)
    return f

def test_setup_offers_a_password_for_the_web_host_only(env):
    A, c, tmp = env
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert re.search(r'name="gallery_keymode" value="password"', page) and 'name="gallery_password"' in page
    assert re.search(r'<input type="password" id="f-gallery_password" name="gallery_password"[^>]*value=""', page)
    assert "Key sign-in only, no passwords" not in page
    store = c.get("/setup/storage", environ_base=E).get_data(as_text=True)
    assert 'keymode" value="password"' not in store

def test_setup_saves_a_password_web_host_sealed_and_never_shows_it_back(env, sftpd, monkeypatch):
    A, c, tmp = env
    import setup
    monkeypatch.setattr(setup.B, "fetch_host_key", lambda host, port=22, **kw: (sftpd["line"], setup.B.host_key_fingerprint(sftpd["line"])))
    page = c.post("/setup/galleries", data=_gallery_form(sftpd), environ_base=E).get_data(as_text=True)
    assert "First connection to 127.0.0.1" in page and sftpd["passwords"] == []
    assert PASSWORD not in page and "kept from your last try" in page
    r = c.post("/setup/galleries", data=_gallery_form(sftpd, gallery_password="", gallery_trust="1",
                                                       gallery_host_key=sftpd["line"]), environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)
    assert set(sftpd["passwords"]) == {("studio", PASSWORD)}
    assert setup._PENDING == {}
    raw = open(tmp / "storage.json", "rb").read()
    assert PASSWORD.encode() not in raw
    from vault import web as VW
    gh = VW.config()["gallery_host"]
    assert gh["auth"] == "password" and gh["password"] == PASSWORD and "private_key" not in gh
    page = c.get("/setup/galleries?replace=gallery", environ_base=E).get_data(as_text=True)
    assert PASSWORD not in page
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert PASSWORD not in page
    rows = setup.conn_rows(gh)
    assert ("Sign-in", "A password, saved encrypted") in rows and PASSWORD not in repr(rows)

    f = setup.backend_from_form(_gallery_form(sftpd, gallery_password=""), "gallery_", gh)
    assert f["password"] == PASSWORD
    f = setup.backend_from_form(_gallery_form(sftpd, gallery_password="", gallery_host="other.example.net"), "gallery_", gh)
    assert f["password"] == "" and setup.test_backend(f).startswith("Type the password")

    f = setup.backend_from_form(dict(primary_type="sftp", primary_host="h", primary_user="u", primary_root="r",
                                     primary_keymode="password", primary_password="x"), "primary_", {})
    assert "password" not in f and f.get("auth") != "password"

def test_an_imported_file_keeps_the_saved_password_only_for_the_same_account(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import settings_io
    cur = {"gallery_host": dict(type="sftp", host="h.example.com", user="u", root="g", auth="password", password="pw")}
    same = settings_io._storage({"gallery_host": dict(type="sftp", host="h.example.com", user="u", root="g2",
                                                      auth="password")}, cur)
    assert same["gallery_host"]["password"] == "pw"
    other = settings_io._storage({"gallery_host": dict(type="sftp", host="other.example.net", user="u", root="g",
                                                       auth="password")}, cur)
    assert "password" not in other["gallery_host"]
    with pytest.raises(settings_io.ImportProblem):
        settings_io._storage({"primary": dict(type="sftp", host="h", user="u", root="r", auth="password")}, {})
    with pytest.raises(settings_io.ImportProblem):
        settings_io._storage({"gallery_host": dict(type="sftp", host="h", user="u", root="r", auth="agent")}, {})

def test_a_codeword_gallery_is_refused_on_an_s3_host_before_any_check(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host=dict(type="s3", bucket="galleries", access_key="AK", secret_key="SK",
                                                        endpoint="https://s3.example.com")))
    G = A.app.view_functions["build_gallery"].__globals__
    key = G["new_key"]()
    gid = "g-galStatic207xAAAAAAAAA"
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, gallery_id, gallery_codeword) VALUES (1, 1, 'family', ?, ?)",
                (gid, "maple lantern"))
    con.execute("INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, n_files) "
                "VALUES (1, ?, ?, '2026-11-17', 'built', 3)", (key, "e" + key[1:]))
    con.commit()
    con.close()
    started = []

    class _T:
        def __init__(self, *a, **kw):
            started.append(kw)

        def start(self):
            pass
    monkeypatch.setitem(G, "codeword_key", lambda k, i, w: "e" + key[1:] if w else k)
    monkeypatch.setitem(G, "host_check_now", lambda: {})
    monkeypatch.setattr(G["threading"], "Thread", _T)
    assert G["word_refused"]() == G["WORD_REFUSED_STATIC"]
    c.post("/session/1/gallery/upload", environ_base=E)
    con = A.connect()
    st, err = con.execute("SELECT upload_state, upload_error FROM galleries WHERE session_id=1").fetchone()
    con.close()
    assert st == "error" and err == G["WORD_REFUSED_STATIC"] and not started
    assert "static website" in err and "remove the passphrase" in err
    monkeypatch.setitem(G, "host_check_now", lambda: dict(kind="ok", gate=True))
    assert G["word_refused"]() == G["WORD_REFUSED_STATIC"]
    VW.write_config(dict(VW.config(), gallery_host=dict(type="sftp", host="h.example.com", user="u", root="g")))
    assert G["word_refused"]() == ""

def test_setup_says_what_a_static_host_cannot_do(env):
    A, c, tmp = env
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    m = re.search(r'<div class="be" data-be="gallery_s3"><div class="hint">([^<]*)', page)
    assert m and "Galleries with a passphrase, print orders, and the inquiry form need a web host that runs PHP" in m.group(1)

def test_security_doc_names_every_method_and_why():
    text = open(os.path.join(ROOT, "docs", "security.md")).read()
    sec = text.split("### Reaching the web host", 1)[1].split("\n## ", 1)[0]
    for words in ("SFTP with a key", "SFTP with a password", "S3-compatible storage as a static website",
                  "plain FTP", "WebDAV", "FTPS"):
        assert words in sec, words
    assert "—" not in sec
