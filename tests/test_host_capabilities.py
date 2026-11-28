import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, signed_in_client
from helpers_tools import need_tool

E = {"REMOTE_ADDR": "127.0.0.1"}
PUBLIC = "https://www.example.com/"

class _Folder:

    def __init__(self, root):
        self.root = Path(root)

    def put_stream(self, name, reader, content_type=None, cache_control=None):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(reader.read())

    def delete(self, name):
        (self.root / name).unlink(missing_ok=True)

    def remove_dir(self, prefix):
        try:
            (self.root / prefix.rstrip("/")).rmdir()
        except OSError:
            pass

@pytest.fixture
def served(tmp_path, monkeypatch):
    php = need_tool("php")
    root = tmp_path / "web"
    (root / "g").mkdir(parents=True)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    import hostcheck
    real = hostcheck._http
    monkeypatch.setattr(hostcheck, "HTTP", lambda m, u, **kw: real(m, u.replace(PUBLIC, f"http://127.0.0.1:{port}/"), **kw))
    yield root, port
    p.terminate()
    p.wait(timeout=10)

def test_the_probe_reads_php_and_leaves_nothing(served):
    import hostcheck
    root, _ = served
    p = hostcheck.probe(_Folder(root / "g"), PUBLIC + "g/")
    assert p.kind == "ok" and tuple(int(x) for x in p["version"].split(".")) >= (7, 4) and p["sha256"]
    assert p["wait"] >= 2
    assert not list((root / "g").glob("probe-*.php"))

def test_a_host_that_serves_php_as_text_runs_no_php(monkeypatch):
    import hostcheck
    monkeypatch.setattr(hostcheck, "HTTP", lambda m, u, **kw: (200, {}, hostcheck.PROBE_PHP.encode()))
    assert hostcheck.probe(_Folder(Path("/nonexistent-never-written")), PUBLIC) .kind in ("none", "unreachable")

def test_old_php_and_an_unreachable_address(monkeypatch, tmp_path):
    import hostcheck
    f = _Folder(tmp_path)
    monkeypatch.setattr(hostcheck, "HTTP", lambda m, u, **kw: (200, {}, json.dumps(dict(la=1, php="7.3.33", sha256=True)).encode()))
    p = hostcheck.probe(f, PUBLIC)
    assert p.kind == "old" and "PHP 7.3; Lumen Aureum needs 7.4 or later" in p["detail"]
    monkeypatch.setattr(hostcheck, "HTTP", lambda m, u, **kw: (_ for _ in ()).throw(OSError("no route")))
    assert hostcheck.probe(f, PUBLIC).kind == "unreachable"
    monkeypatch.setattr(hostcheck, "HTTP", lambda m, u, **kw: (404, {}, b""))
    assert hostcheck.probe(f, PUBLIC).kind == "unreachable"
    assert not list(tmp_path.glob("probe-*"))

def test_the_door_wait_follows_opcache():
    import hostcheck
    assert hostcheck.door_wait({"opcache_enable": "0"}) == 2
    assert hostcheck.door_wait({"opcache_enable": "1", "validate_timestamps": "1", "revalidate_freq": "2"}) == 4
    assert hostcheck.door_wait({"opcache_enable": "1", "validate_timestamps": "0"}) == 62

def test_htaccess_is_told_apart(served):
    import hostcheck
    root, _ = served
    assert hostcheck.htaccess_honored(_Folder(root / "g"), PUBLIC + "g/") is False
    assert not [p for p in (root / "g").iterdir() if p.name.startswith(".la-check")]

def test_tests_never_reach_out():
    import hostcheck
    with pytest.raises(OSError):
        hostcheck._http("GET", "https://example.com/")

@pytest.fixture
def app_env(tmp_path, monkeypatch, served):
    root, port = served
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    (root / "g" / ".vault-root").write_text("")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(root / "g")}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', ?)", (PUBLIC + "g/",))
    con.commit()
    con.close()
    return A, c

def test_setup_shows_what_the_host_can_do_and_today_flags_a_change(app_env, monkeypatch):
    A, c = app_env
    r = c.post("/setup/host-check", environ_base=E)
    assert r.status_code in (302, 303)
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert "What this host can do" in page and "Galleries close on their last day" in page and "Works" in page
    assert "It hands out gallery files without asking for the key" in page
    assert "Galleries with a passphrase" in page

    import hostcheck
    con = A.connect()
    caps = json.loads(con.execute("SELECT value FROM settings WHERE key='gallery_host_caps'").fetchone()[0])
    caps["checked"] = time.time() - 2 * 86400
    con.execute("UPDATE settings SET value=? WHERE key='gallery_host_caps'", (json.dumps(caps),))
    con.commit(); con.close()
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="none", version="", wait=0, detail=""))
    c.post("/gallery/sync", environ_base=E)
    today = c.get("/alerts", environ_base=E).get_data(as_text=True)
    assert "Your web host no longer runs PHP" in today

    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert "needs a host that runs PHP" in page

