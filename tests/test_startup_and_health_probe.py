import io
import json
import os
import runpy
import sys
import urllib.error
import urllib.request

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

class Answer:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

def probe(monkeypatch, answer):

    fresh_modules()
    asked = []

    def fake_urlopen(url, timeout=None, context=None):
        asked.append(url)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as e:
        runpy.run_path(HEALTHCHECK, run_name="__main__")
    return e.value.code, asked[0]

def locked_healthz_answer(tmp_path, monkeypatch):

    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    r = A.app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "locked": True}
    return r.data

def test_a_locked_app_is_healthy(env):
    tmp, mp = env
    body = locked_healthz_answer(tmp, mp)
    code, url = probe(mp, Answer(200, body))
    assert code == 0
    assert url == "https://127.0.0.1:8780/healthz"

def test_an_unlocked_app_is_healthy_on_its_port_over_plain_http_behind_a_proxy(env):
    _, mp = env
    mp.setenv("CRM_TLS", "off")
    mp.setenv("CRM_TRUSTED_PROXIES", "10.0.0.5")
    mp.setenv("CRM_PORT", "8080")
    code, url = probe(mp, Answer(200, json.dumps({"ok": True, "locked": False}).encode()))
    assert code == 0 and url == "http://127.0.0.1:8080/healthz"

@pytest.mark.parametrize("answer", [
    Answer(503, b'{"ok": false, "locked": true}'),
    Answer(500, b""),
    urllib.error.HTTPError("https://127.0.0.1:8780/healthz", 403, "Forbidden", {}, io.BytesIO(b"")),
    urllib.error.URLError("connection refused"),
    TimeoutError("timed out"),
])
def test_an_app_that_does_not_answer_ok_is_unhealthy(env, answer):
    _, mp = env
    code, _ = probe(mp, answer)
    assert code == 1
