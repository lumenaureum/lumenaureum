import base64
import hashlib
import io
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
NAME = "sample_tool"
CODE = b'''import os
from pathlib import Path

os.environ["LA_SAMPLE_TOOL_RAN"] = "1"           # the tests see whether any of it ran (conftest restores the environment)

def register(ctx):
    os.environ["LA_SAMPLE_TOOL_REGISTERED"] = "1"
    note = (Path(__file__).with_name("sample_tool_assets") / "note.txt").read_text()
    ctx.switchable(default=True, label="Sample tool", about="A plugin for the tests.")
    ctx.hooks.today.append(lambda: [dict(text="Sample tool reminder", link="/settings")])

    @ctx.app.get("/sample-tool", endpoint="sample_tool_page")
    def sample_tool_page():
        return note
'''
FILES = {f"{NAME}.py": CODE, f"{NAME}_assets/note.txt": b"from the assets folder", f"{NAME}_data.json": b"{}"}

def new_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    k = Ed25519PrivateKey.generate()
    return k, base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()

KEY, PUB = new_key()

def build(key=KEY, name=NAME, version="1.0.0", min_app="1.0", files=None, sign=True, after=None, package=None,
          extra=None):

    import plugin_install as P
    files = dict(FILES if files is None else files)
    entries = [dict(path=p, size=len(b), sha256=hashlib.sha256(b).hexdigest()) for p, b in sorted(files.items())]
    m = dict(format=P.FORMAT, name=name, version=version, min_app=min_app, files=entries)
    if sign:
        m["signature"] = base64.b64encode(key.sign(P.signed_bytes(m))).decode()
    if package:
        m["package"] = package
    if after:
        after(m)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m))
        for p, b in files.items():
            z.writestr(p, b)
        if extra:
            extra(z)
    return m, buf.getvalue()

@pytest.fixture
def keyed(monkeypatch):
    import plugin_install as P
    monkeypatch.setattr(P, "PLUGIN_SIGNING_KEY", PUB)
    return P

def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

def confirm(A, password=TEST_PASSWORD):

    import auth
    con = A.connect()
    try:
        con.execute("UPDATE users SET totp_last=0")
        con.commit()
    except Exception:
        pass
    finally:
        con.close()
    return dict(current=password, code=auth.totp("JBSWY3DPEHPK3PXP"))

def add_file(A, c, data, **kw):
    f = dict(confirm(A), how="file", package=(io.BytesIO(data), "plugin.zip"))
    f.update(kw)
    return c.post("/settings/plugins/add", data=f, content_type="multipart/form-data", environ_base=E)

def notice(c):
    with c.session_transaction() as s:
        return s.get("notice") or ""

def recorded(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'plugin_package_%'")]
    finally:
        con.close()

def on(A, name=NAME):
    with A.app.test_request_context():
        return A.plugin_on(name)

def turn_on(A, name=NAME):

    import plugin_install as P
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value='1'",
                (f"plugin_on_{name}",))
    con.commit()
    con.close()
    P.set_on(name, True)

def text(r):
    import html
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)).split()))

def test_the_signed_bytes_are_pinned(keyed):

    P = keyed
    files = [dict(path="b.txt", size=2, sha256="b" * 64), dict(path="a.py", size=1, sha256="a" * 64)]
    lines = f"{'a' * 64} 1 a.py\n{'b' * 64} 2 b.txt\n"
    want = (f"lumenaureum-plugin/1\nname x1\nversion 1.2.3\nmin_app 1.7\nfiles "
            f"{hashlib.sha256(lines.encode()).hexdigest()}\n").encode()
    assert P.signed_bytes(dict(name="x1", version="1.2.3", min_app="1.7", files=files)) == want
    m, data = build()
    assert P.verify_package(data)[0]["name"] == NAME
    spaced = json.dumps(m, indent=4).encode()
    assert P.parse_manifest(spaced)["signature"] == m["signature"]

@pytest.mark.parametrize("case", ["bad_hash", "bad_signature", "unsigned", "wrong_key", "extra_file", "missing_file",
                                  "traversal", "symlink", "outside_assets"])
def test_a_package_that_does_not_check_out_is_refused(keyed, case):
    P = keyed
    other, _pub = new_key()
    if case == "bad_hash":
        _m, data = build()
        m = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json", json.dumps(m))
            for p, b in FILES.items():
                z.writestr(p, b + b"# changed" if p.endswith(".py") else b)
        data, words = buf.getvalue(), P.MISMATCH
    elif case == "bad_signature":
        _m, data = build(after=lambda m: m.update(version="1.0.1"))
        words = P.BAD_SIG
    elif case == "unsigned":
        _m, data = build(sign=False)
        words = P.UNSIGNED
    elif case == "wrong_key":
        _m, data = build(key=other)
        words = P.BAD_SIG
    elif case == "extra_file":
        _m, data = build(extra=lambda z: z.writestr(f"{NAME}_assets/more.txt", b"not signed"))
        words = P.MISMATCH
    elif case == "missing_file":
        _m, full = build()
        zin = zipfile.ZipFile(io.BytesIO(full))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in zin.namelist():
                if not n.endswith("note.txt"):
                    z.writestr(n, zin.read(n))
        data, words = buf.getvalue(), P.MISMATCH
    elif case == "traversal":
        _m, data = build(extra=lambda z: z.writestr("../evil.py", b"x"))
        words = P.NOT_SAFE
    elif case == "symlink":
        def link(z):
            zi = zipfile.ZipInfo(f"{NAME}_assets/link")
            zi.external_attr = (0o120777 << 16)
            z.writestr(zi, "/etc/passwd")
        _m, data = build(extra=link)
        words = P.NOT_SAFE
    else:
        _m, data = build(files=dict(FILES, **{"other_assets/x.txt": b"x"}))
        words = P.NOT_SAFE
    with pytest.raises(P.Refused) as e:
        P.verify_package(data)
    assert e.value.words == words

def test_oversize_and_zip_bombs_are_refused(keyed, monkeypatch):
    P = keyed
    _m, data = build()
    monkeypatch.setattr(P, "MAX_PACKAGE", len(data) - 1)
    with pytest.raises(P.Refused, match="larger than a plugin can be"):
        P.verify_package(data)
    monkeypatch.setattr(P, "MAX_PACKAGE", 16 * 1024 * 1024)
    bomb = b"\0" * (2 * 1024 * 1024)
    _m, data = build(files=dict(FILES, **{f"{NAME}_assets/big.bin": bomb}))
    assert len(data) < 64 * 1024
    monkeypatch.setattr(P, "MAX_FILE", 1024 * 1024)
    with pytest.raises(P.Refused, match="larger than a plugin can be"):
        P.verify_package(data)
    monkeypatch.setattr(P, "MAX_FILE", 16 * 1024 * 1024)
    monkeypatch.setattr(P, "MAX_TOTAL", 1024 * 1024)
    with pytest.raises(P.Refused, match="larger than a plugin can be"):
        P.verify_package(data)

def test_a_name_of_a_built_in_or_core_plugin_and_a_too_new_version_are_refused(keyed):
    P = keyed
    for name in ("gallery", "retention"):
        files = {f"{name}.py": CODE}
        _m, data = build(name=name, files=files)
        with pytest.raises(P.Refused, match="already part of Lumen Aureum"):
            P.verify_package(data, taken={"gallery", "retention", "print_core"})
    _m, data = build(min_app="99.0")
    with pytest.raises(P.Refused, match=r"needs Lumen Aureum 99\.0 or newer"):
        P.verify_package(data)

def test_with_no_key_set_every_plugin_is_refused(monkeypatch):
    import plugin_install as P
    monkeypatch.setattr(P, "PLUGIN_SIGNING_KEY", "")
    _m, data = build()
    with pytest.raises(P.Refused) as e:
        P.verify_package(data)
    assert e.value.words == P.NO_KEY
    assert open(os.path.join(ROOT, "plugin_install.py")).read().count('\nPLUGIN_SIGNING_KEY = "uT2STQ03ZoKPNb3Tdb4V5IDffJuTvYtTsCB30rFnpvU="\n') == 1

def _serve(tmp_path, routes):
    from fake_calendars import _Server

    def fn(method, path, query, headers, body):
        r = routes.get(path)
        if r is None:
            return 404, {}, b"not here"
        return r if isinstance(r, tuple) else (200, {}, r)
    return _Server(tmp_path, fn)

def test_from_a_link_follows_redirects_on_https_and_refuses_http(keyed, tmp_path, monkeypatch):
    P = keyed
    import calsend
    m, data = build(package="pkg/sample_tool-1.0.0.zip")
    link_m = json.dumps(m).encode()
    srv = _serve(tmp_path, {})
    srv_routes = {"/m.json": (302, {"Location": "/real/m.json"}, b""), "/real/m.json": link_m,
                  "/real/pkg/sample_tool-1.0.0.zip": data,
                  "/to-http.json": (302, {"Location": "http://127.0.0.1:1/m.json"}, b"")}
    srv.close()
    srv = _serve(tmp_path, srv_routes)
    monkeypatch.setattr(calsend, "CA_FILE", srv.cafile)
    try:
        got, pkg, files = P.from_link(srv.url + "/m.json")
        assert got["name"] == NAME and pkg == data and files[f"{NAME}_assets/note.txt"] == b"from the assets folder"
        assert [r["path"] for r in srv.requests] == ["/m.json", "/real/m.json", "/real/pkg/sample_tool-1.0.0.zip"]
        assert all(set(r["headers"]) & {"authorization", "cookie"} == set() for r in srv.requests)
        with pytest.raises(P.Refused, match="must start with https://"):
            P.from_link(srv.url.replace("https://", "http://") + "/m.json")
        with pytest.raises(P.Refused, match="isn't https://"):
            P.from_link(srv.url + "/to-http.json")
        n = len(srv.requests)
        bad, _d = build(key=new_key()[0], package="pkg/sample_tool-1.0.0.zip")
        srv_routes["/bad.json"] = json.dumps(bad).encode()
        with pytest.raises(P.Refused) as e:
            P.from_link(srv.url + "/bad.json")
        assert e.value.words == P.BAD_SIG and len(srv.requests) == n + 1
        monkeypatch.setattr(P, "MAX_PACKAGE", len(data) - 1)
        with pytest.raises(P.Refused, match="larger than a plugin can be"):
            P.from_link(srv.url + "/m.json")
    finally:
        srv.close()

def test_a_dripping_server_is_cut_off_by_the_overall_deadline(keyed, tmp_path, monkeypatch):

    P = keyed
    import calsend
    from fake_s3 import make_cert
    cert, key = make_cert(tmp_path)
    monkeypatch.setattr(calsend, "CA_FILE", cert)
    monkeypatch.setattr(calsend, "TIMEOUT", 3)
    monkeypatch.setattr(P, "DEADLINE", 2)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def drip():
        raw, _ = srv.accept()
        try:
            conn = ctx.wrap_socket(raw, server_side=True)
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
            while not stop.is_set():
                conn.sendall(b" ")
                time.sleep(0.3)
        except OSError:
            pass
        finally:
            raw.close()

    threading.Thread(target=drip, daemon=True).start()
    t0 = time.monotonic()
    try:
        with pytest.raises(P.Refused, match="took too long"):
            P.from_link(f"https://127.0.0.1:{port}/m.json")
        assert time.monotonic() - t0 < 5
    finally:
        stop.set()
        srv.close()

def test_add_from_a_file_loads_at_the_next_start_off_until_turned_on_then_remove(keyed, tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Add from a link" in page and "Add from a file" in page and "None yet." in page
    _m, data = build()
    r = add_file(A, c, data)
    assert r.status_code == 302 and r.headers["Location"].endswith("#added")
    assert "Added sample_tool 1.0.0. It stays off until you turn it on." in notice(c)
    assert "Restart the studio for it to take effect." in notice(c)
    assert recorded(A) == ["plugin_package_sample_tool"]
    folder = tmp_path / "plugins" / NAME
    assert (folder / f"{NAME}_assets" / "note.txt").read_bytes() == b"from the assets folder"
    assert sorted(os.listdir(folder)) == ["manifest.json", f"{NAME}.py", f"{NAME}_assets", f"{NAME}_data.json"]
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Version 1.0.0. It loads at the next start." in page and "Restart the studio for the plugin changes" in page
    con = A.connect()
    assert con.execute("SELECT action FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0] == \
        "Added the plugin sample_tool 1.0.0, from a file"
    con.close()
    r = add_file(A, c, data)
    assert "already has sample_tool 1.0.0" in notice(c)

    A, c = studio(tmp_path, monkeypatch)
    assert NAME not in A.LOADED and NAME in A.ADDED_OFF and not A.PLUGIN_ERRORS
    assert "LA_SAMPLE_TOOL_RAN" not in os.environ and "LA_SAMPLE_TOOL_REGISTERED" not in os.environ
    assert "sample_tool_page" not in A.app.view_functions and not on(A)
    assert c.get("/sample-tool", environ_base=E).status_code == 404
    assert "Sample tool reminder" not in text(c.get("/", environ_base=E))
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Added from Lumen Aureum, version 1.0.0. While it is off, nothing of it runs." in page
    assert "Version 1.0.0. Off: nothing of it runs until you turn it on." in page
    r = c.post("/settings/plugins", data={"name": NAME, "turn": "on"}, environ_base=E)
    assert r.status_code == 302 and "Turned sample_tool on. Restart the studio for it to take effect." in notice(c)
    assert (tmp_path / "plugins" / NAME / ".on").is_file() and A.PLUGIN_CHANGES
    assert "On at the next start" in text(c.get("/settings/plugins", environ_base=E))

    A, c = studio(tmp_path, monkeypatch)
    assert NAME in A.LOADED and not A.PLUGIN_ERRORS and os.environ.get("LA_SAMPLE_TOOL_REGISTERED") == "1"
    assert on(A) and "Sample tool reminder" in text(c.get("/", environ_base=E))
    assert c.get("/sample-tool", environ_base=E).get_data() == b"from the assets folder"
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Sample tool" in page and "Version 1.0.0." in page

    _m, newer = build(version="1.1.0")
    add_file(A, c, newer)
    assert "Updated sample_tool to 1.1.0." in notice(c)
    _m, older = build(version="0.9.0")
    add_file(A, c, older)
    assert "already has sample_tool 1.1.0" in notice(c)
    assert json.loads((tmp_path / "plugins" / NAME / "manifest.json").read_text())["version"] == "1.1.0"
    assert (tmp_path / "plugins" / NAME / ".on").is_file()

    import plugin_install as P
    asked = []
    monkeypatch.setattr(P, "restart_soon", lambda: asked.append(1) or True)
    r = c.post("/settings/plugins", data={"name": NAME}, environ_base=E)
    assert asked and "Turned Sample tool off. The studio restarts now for it to take effect" in notice(c)
    monkeypatch.setattr(P, "restart_soon", lambda: False)
    assert not (tmp_path / "plugins" / NAME / ".on").exists() and not on(A)
    assert c.get("/sample-tool", environ_base=E).status_code == 404
    monkeypatch.delenv("LA_SAMPLE_TOOL_REGISTERED")
    A, c = studio(tmp_path, monkeypatch)
    assert NAME in A.ADDED_OFF and "LA_SAMPLE_TOOL_REGISTERED" not in os.environ

    r = c.post("/settings/plugins/remove", data=dict(confirm(A), name=NAME), environ_base=E)
    assert r.status_code == 302 and "Removed sample_tool." in notice(c)
    assert recorded(A) == [] and not (tmp_path / "plugins" / NAME).exists()
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM settings WHERE key='plugin_on_sample_tool'").fetchone()[0] == 0
    con.close()
    A, c = studio(tmp_path, monkeypatch)
    assert NAME not in A.LOADED and c.get("/sample-tool", environ_base=E).status_code == 404

def test_add_from_a_link_in_the_studio(keyed, tmp_path, monkeypatch):
    import calsend
    m, data = build(package="sample_tool.zip")
    srv = _serve(tmp_path, {"/p/manifest.json": json.dumps(m).encode(), "/p/sample_tool.zip": data})
    monkeypatch.setattr(calsend, "CA_FILE", srv.cafile)
    try:
        A, c = studio(tmp_path, monkeypatch)
        r = c.post("/settings/plugins/add", data=dict(confirm(A), how="link", link=srv.url + "/p/manifest.json"),
                   environ_base=E)
        assert r.status_code == 302 and "Added sample_tool 1.0.0." in notice(c)
        assert recorded(A) == ["plugin_package_sample_tool"]
        con = A.connect()
        assert con.execute("SELECT action FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0] == \
            f"Added the plugin sample_tool 1.0.0, from {srv.url}/p/manifest.json"
        con.close()
        r = c.post("/settings/plugins/add", data=dict(confirm(A), how="link", link="http://127.0.0.1/x.json"),
                   environ_base=E)
        assert "must start with https://" in notice(c)
    finally:
        srv.close()

def test_refusals_in_the_studio_add_nothing(keyed, tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    for data, words in ((build(sign=False)[1], "isn't signed"), (build(key=new_key()[0])[1], "signature doesn't match"),
                        (build(name="gallery", files={"gallery.py": CODE})[1], "already part of Lumen Aureum"),
                        (build(name="print_core", files={"print_core.py": CODE})[1], "already part of Lumen Aureum"),
                        (build(min_app="99.0")[1], "needs Lumen Aureum 99.0"), (b"not a zip", "isn't a Lumen Aureum")):
        add_file(A, c, data)
        assert words in notice(c)
    assert recorded(A) == [] and not (tmp_path / "plugins").exists()
    r = c.get("/settings/plugins", environ_base=E)
    assert 'data-refused' in r.get_data(as_text=True)

def test_owner_only_and_confirmed_with_password_and_two_factor(keyed, tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    _m, data = build()
    stranger = A.app.test_client()
    r = stranger.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code="000000",
                                                         package=(io.BytesIO(data), "p.zip")),
                      content_type="multipart/form-data", environ_base=E)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    add_file(A, c, data, current="wrong password")
    assert "Your current password, please." in notice(c)
    add_file(A, c, data, code="000000")
    assert "two-factor code didn't work" in notice(c)
    add_file(A, c, data, current="", code="")
    assert "Your current password, please." in notice(c)
    assert recorded(A) == []
    add_file(A, c, data)
    assert recorded(A) == ["plugin_package_sample_tool"]
    r = c.post("/settings/plugins/remove", data=dict(name=NAME, current=TEST_PASSWORD, code="000000"), environ_base=E)
    assert "Nothing was removed." in notice(c) and recorded(A) == ["plugin_package_sample_tool"]
    r = stranger.post("/settings/plugins/remove", data=dict(confirm(A), name=NAME), environ_base=E)
    assert "/login" in r.headers["Location"] and recorded(A) == ["plugin_package_sample_tool"]

def test_a_changed_folder_is_refused_at_start_and_put_back_at_sign_in(keyed, tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    add_file(A, c, build()[1])
    assert "Added sample_tool" in notice(c), notice(c)
    folder = tmp_path / "plugins" / NAME
    turn_on(A)
    for change in ("code", "extra", "link"):
        if change == "code":
            (folder / f"{NAME}.py").write_bytes(CODE + b"\n# changed\n")
        elif change == "extra":
            (folder / f"{NAME}_assets" / "planted.txt").write_bytes(b"x")
        else:
            os.symlink("/etc/hostname", folder / f"{NAME}_assets" / "planted")
        fresh_modules()
        import app as A
        assert NAME not in A.LOADED, change
        assert A.PLUGIN_ERRORS == [f"{NAME} (added): its files changed since it was added"], change
        c = signed_in_client(A)
        assert A.PLUGIN_CHANGES
        page = text(c.get("/settings/plugins", environ_base=E))
        assert "Didn't load at the last start: its files changed since it was added." in page
        assert "Plugins that didn't load at the last start" in page
        assert sorted(os.listdir(folder / f"{NAME}_assets")) == ["note.txt"]
        assert (folder / f"{NAME}.py").read_bytes() == CODE
    A, c = studio(tmp_path, monkeypatch)
    assert NAME in A.LOADED and not A.PLUGIN_ERRORS

def test_with_no_key_nothing_is_added_and_nothing_added_before_loads(tmp_path, monkeypatch):
    import plugin_install as P
    monkeypatch.setattr(P, "PLUGIN_SIGNING_KEY", PUB)
    A, c = studio(tmp_path, monkeypatch)
    _m, data = build()
    add_file(A, c, data)
    monkeypatch.setattr(P, "PLUGIN_SIGNING_KEY", "")
    A, c = studio(tmp_path, monkeypatch)
    assert NAME not in A.LOADED
    assert A.PLUGIN_ERRORS == [f"{NAME} (added): this version has no key to check plugins with"]
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Plugins can't be added yet" in page and "Add from the link" not in page
    add_file(A, c, build(version="2.0.0")[1])
    assert notice(c) == P.NO_KEY

RUN_ENV = """
import sys; sys.path.insert(0, 'tests')
import plugin_install as P
P.PLUGIN_SIGNING_KEY = PUBKEY
"""

def _run(code, data):
    env = dict(os.environ, CRM_TESTING="1", CRM_DB=str(data / "crm.db"), CRM_STORAGE_CFG=str(data / "storage.json"),
               CRM_PHOTOS_ROOT=str(data.parent / "photos"), CRM_GALLERY_ROOT=str(data / "galleries"),
               CRM_GALLERY_SYNC="0")
    r = subprocess.run([sys.executable, "-c", (RUN_ENV + code).replace("PUBKEY", repr(PUB))], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-4000:]
    return r.stdout

def test_backup_then_restore_onto_a_new_install_keeps_it(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".vault-root").touch()
    old = tmp_path / "old" / "data"
    old.mkdir(parents=True)
    (old / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(vault)}}))
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(build()[1])
    out = _run("""
import io
from helpers import signed_in_client
import app as A, auth
from vault import backup as BK
c = signed_in_client(A)
r = c.post('/settings/plugins/add', data={'how': 'file', 'current': 'test password 123', 'code': auth.totp('JBSWY3DPEHPK3PXP'),
           'package': (io.BytesIO(open(PKG, 'rb').read()), 'p.zip')}, content_type='multipart/form-data',
           environ_base={'REMOTE_ADDR': '127.0.0.1'})
print('added', r.status_code)
con = A.connect(); con.execute("INSERT INTO settings (key, value) VALUES ('plugin_on_sample_tool', '1')"); con.commit(); con.close()
BK.sealed_backup(str(A.DB_PATH), None, kind='manual')
print('SECRET', c.master_secret)
""".replace("PKG", repr(str(pkg))), old)
    assert "added 302" in out, out
    secret = out.split("SECRET ")[1].split()[0]
    new = tmp_path / "new" / "data"
    new.mkdir(parents=True)
    out = _run("""
import os, re
from helpers import HttpsClient
import app as A, netsec
A.app.test_client_class = HttpsClient
c = A.app.test_client()
ip = {'environ_base': {'REMOTE_ADDR': '127.0.0.1'}}
p = c.post('/setup/restore', data={'go': 'list', 'folder': VAULT, 'claim': netsec.claim_code()}, **ip).get_data(as_text=True)
names = re.findall(r'value="folder:(db/[^"]+)"', p)
r = c.post('/setup/restore', data={'go': 'check', 'source': 'folder:' + names[0], 'folder': VAULT, 'secret': SECRET,
                                   'claim': netsec.claim_code()}, **ip)
print('checked', r.status_code)
r = c.post('/setup/restore', data={'go': 'restore'}, **ip)
print('restored', r.status_code)
print('folder', sorted(os.listdir(A.DB_PATH.parent / 'plugins' / 'sample_tool')))
""".replace("VAULT", repr(str(vault))).replace("SECRET", repr(secret)), new)
    assert "checked 302" in out and "restored 200" in out, out
    assert "folder ['.on', 'manifest.json', 'sample_tool.py', 'sample_tool_assets', 'sample_tool_data.json']" in out, out
    out = _run("""
from helpers import signed_in_client
import app as A
print('loaded', 'sample_tool' in A.LOADED, A.PLUGIN_ERRORS)
c = signed_in_client(A)
with A.app.test_request_context():
    print('on', A.plugin_on('sample_tool'))
""", new)
    assert "loaded True []" in out and "on True" in out, out

def test_a_restore_sets_the_plugins_folder_aside_and_follows_the_restored_database(keyed, tmp_path, monkeypatch):

    P = keyed
    A, c = studio(tmp_path, monkeypatch)
    add_file(A, c, build()[1])
    (tmp_path / "plugins" / "stale_one").mkdir()
    aside = tmp_path / "set-aside" / "now"
    aside.mkdir(parents=True)
    import datakey
    import restore
    assert P.after_restore in restore.AFTER_APPLY
    P.after_restore(datakey.dk(), aside)
    assert sorted(os.listdir(aside / "plugins")) == ["sample_tool", "stale_one"]
    assert sorted(os.listdir(tmp_path / "plugins")) == [NAME]
    assert P.check_folder(NAME)[0]["version"] == "1.0.0"

def test_a_planted_marker_only_turns_on_a_signed_plugin_and_sign_in_takes_it_back(keyed, tmp_path, monkeypatch):

    A, c = studio(tmp_path, monkeypatch)
    add_file(A, c, build()[1])
    folder = tmp_path / "plugins" / NAME
    (folder / ".on").touch()
    A, c = studio(tmp_path, monkeypatch)
    assert NAME in A.LOADED and not on(A) and c.get("/sample-tool", environ_base=E).status_code == 404
    assert not (folder / ".on").exists() and A.PLUGIN_CHANGES
    (folder / ".on").touch()
    (folder / f"{NAME}.py").write_bytes(CODE + b"\nprint('not signed')\n")
    monkeypatch.delenv("LA_SAMPLE_TOOL_RAN", raising=False)
    fresh_modules()
    import app as A
    assert NAME not in A.LOADED and "LA_SAMPLE_TOOL_RAN" not in os.environ
    assert A.PLUGIN_ERRORS == [f"{NAME} (added): its files changed since it was added"]
    (folder / f"{NAME}.py").write_bytes(CODE)
    (folder / ".on").unlink()
    os.symlink("/etc/hostname", folder / ".on")
    fresh_modules()
    import app as A
    assert NAME not in A.LOADED and A.PLUGIN_ERRORS
