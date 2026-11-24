import json
import os
import runpy
import socket
import ssl
import subprocess
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

SERVE = os.path.join(ROOT, "serve.py")
HEALTHCHECK = os.path.join(ROOT, "healthcheck.py")

class Started(Exception):
    pass

@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in ("CRM_TLS", "CRM_TRUSTED_PROXIES", "CRM_PORT", "CRM_THREADS", "CRM_TLS_CERT", "CRM_TLS_KEY", "CRM_TLS_SAN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    return tmp_path, monkeypatch

def startup(monkeypatch):

    fresh_modules()
    calls = []

    def fake_execvp(file, args):
        calls.append((file, list(args)))
        raise Started()
    monkeypatch.setattr(os, "execvp", fake_execvp)
    with pytest.raises(Started):
        runpy.run_path(SERVE, run_name="__main__")
    return calls[0]

def flag(args, name):
    return args[args.index(name) + 1]

@pytest.mark.parametrize("value", ["off", "OFF", "0", "no", "false", " Off "])
def test_tls_off_without_a_trusted_proxy_is_refused(env, value):
    _, mp = env
    mp.setenv("CRM_TLS", value)
    fresh_modules()
    import netsec
    assert netsec.tls_mode() == "off"
    assert "CRM_TRUSTED_PROXIES" in netsec.startup_problem()

@pytest.mark.parametrize("value", [None, "", "on", "1", "yes"])
def test_tls_on_starts_without_a_proxy(env, value):
    _, mp = env
    if value is not None:
        mp.setenv("CRM_TLS", value)
    fresh_modules()
    import netsec
    assert netsec.tls_mode() == "on"
    assert netsec.startup_problem() is None

@pytest.mark.parametrize("proxies", ["10.0.0.5", "10.0.0.0/24", " 192.168.1.2 , fd00::1 "])
def test_tls_off_behind_a_trusted_proxy_starts(env, proxies):
    _, mp = env
    mp.setenv("CRM_TLS", "off")
    mp.setenv("CRM_TRUSTED_PROXIES", proxies)
    fresh_modules()
    import netsec
    assert netsec.startup_problem() is None

def test_an_empty_proxy_list_counts_as_none(env):
    _, mp = env
    mp.setenv("CRM_TLS", "off")
    mp.setenv("CRM_TRUSTED_PROXIES", " , ")
    fresh_modules()
    import netsec
    assert netsec.startup_problem()

def test_serve_refuses_an_unsafe_setup_and_starts_nothing(env):
    _, mp = env
    mp.setenv("CRM_TLS", "off")
    fresh_modules()
    started = []
    mp.setattr(os, "execvp", lambda *a: started.append(a))
    with pytest.raises(SystemExit) as e:
        runpy.run_path(SERVE, run_name="__main__")
    assert "CRM_TRUSTED_PROXIES" in str(e.value.code)
    assert started == []

def test_serve_defaults_with_its_own_certificate(env):
    tmp, mp = env
    file, args = startup(mp)
    assert file == "gunicorn" and args[0] == "gunicorn" and args[-1] == "app:app"
    assert flag(args, "--bind") == "0.0.0.0:8780"
    assert flag(args, "--threads") == "8"
    assert flag(args, "--workers") == "1"
    assert flag(args, "--worker-class") == "gthread"
    assert "--no-control-socket" in args
    assert flag(args, "--certfile") == str(tmp / "tls" / "cert.pem")
    assert flag(args, "--keyfile") == str(tmp / "tls" / "key.pem")
    assert (tmp / "tls" / "cert.pem").exists() and (tmp / "tls" / "key.pem").exists()
    assert (tmp / "tls" / "key.pem").stat().st_mode & 0o077 == 0

def test_serve_uses_the_port_threads_and_certificate_it_is_given(env):
    tmp, mp = env
    cert, key = tmp / "mine.crt", tmp / "mine.key"
    cert.write_text("my certificate")
    key.write_text("my key")
    mp.setenv("CRM_PORT", "9443")
    mp.setenv("CRM_THREADS", "3")
    mp.setenv("CRM_TLS_CERT", str(cert))
    mp.setenv("CRM_TLS_KEY", str(key))
    _, args = startup(mp)
    assert flag(args, "--bind") == "0.0.0.0:9443"
    assert flag(args, "--threads") == "3"
    assert flag(args, "--certfile") == str(cert) and flag(args, "--keyfile") == str(key)
    assert cert.read_text() == "my certificate" and key.read_text() == "my key"
    assert not (tmp / "tls").exists()

def test_serve_behind_a_proxy_has_no_certificate(env):
    tmp, mp = env
    mp.setenv("CRM_TLS", "off")
    mp.setenv("CRM_TRUSTED_PROXIES", "10.0.0.5")
    mp.setenv("CRM_PORT", "8080")
    _, args = startup(mp)
    assert flag(args, "--bind") == "0.0.0.0:8080"
    assert "--certfile" not in args and "--keyfile" not in args
    assert not (tmp / "tls").exists()

def serve_once(answers, tls_files=None):

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port, seen = srv.getsockname()[1], []
    ctx = None
    if tls_files:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*map(str, tls_files))

    def run():
        for answer in answers:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                if ctx:
                    conn = ctx.wrap_socket(conn, server_side=True)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                seen.append(data)
                if answer is not None:
                    code, body = answer
                    conn.sendall(b"HTTP/1.1 %d X\r\nContent-Length: %d\r\n\r\n" % (code, len(body)) + body)
            except (OSError, ssl.SSLError):
                pass
            finally:
                conn.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    def stop():
        srv.close()
        t.join(5)
    return port, seen, stop

def run_probe():

    env = {k: v for k, v in os.environ.items() if k.startswith(("CRM_", "PATH", "SYSTEMROOT"))}
    return subprocess.run([sys.executable, "-I", "-S", HEALTHCHECK], env=env, timeout=30).returncode

def locked_healthz_answer(tmp_path, monkeypatch):

    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    r = A.app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "locked": True}
    return r.data

def app_certificate():
    fresh_modules()
    import netsec
    return netsec.ensure_cert()

def test_a_locked_app_is_healthy_over_https_with_its_own_certificate(env):
    tmp, mp = env
    body = locked_healthz_answer(tmp, mp)
    port, seen, stop = serve_once([(200, body)], app_certificate())
    mp.setenv("CRM_PORT", str(port))
    try:
        assert run_probe() == 0
    finally:
        stop()
    assert seen[0].startswith(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % port)

def test_an_unlocked_app_is_healthy_on_its_port_over_plain_http_behind_a_proxy(env):
    _, mp = env
    mp.setenv("CRM_TLS", "off")
    mp.setenv("CRM_TRUSTED_PROXIES", "10.0.0.5")
    port, seen, stop = serve_once([(200, json.dumps({"ok": True, "locked": False}).encode())])
    mp.setenv("CRM_PORT", str(port))
    try:
        assert run_probe() == 0
    finally:
        stop()
    assert seen and seen[0].startswith(b"GET /healthz ")

@pytest.mark.parametrize("answer", [(503, b'{"ok": false, "locked": true}'), (500, b""), (403, b"Forbidden"), None])
@pytest.mark.parametrize("tls", [True, False])
def test_an_app_that_does_not_answer_ok_is_unhealthy(env, answer, tls):
    _, mp = env
    if not tls:
        mp.setenv("CRM_TLS", "off")
    port, _, stop = serve_once([answer], app_certificate() if tls else None)
    mp.setenv("CRM_PORT", str(port))
    try:
        assert run_probe() == 1
    finally:
        stop()

def test_nothing_listening_or_plain_http_to_an_https_probe_is_unhealthy(env):
    _, mp = env
    port, _, stop = serve_once([])
    stop()
    mp.setenv("CRM_PORT", str(port))
    assert run_probe() == 1
    port, _, stop = serve_once([(200, b"{}")])
    mp.setenv("CRM_PORT", str(port))
    try:
        assert run_probe() == 1
    finally:
        stop()

def test_the_probe_uses_no_app_code():
    src = open(HEALTHCHECK).read()
    imports = {l_.split()[1] for l_ in src.splitlines() if l_.strip().startswith(("import ", "from "))}
    assert imports <= {"os", "socket", "sys", "ssl"}
