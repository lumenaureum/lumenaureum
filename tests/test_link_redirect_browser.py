import functools
import http.server
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules
from helpers_tools import need_tool

sync_api = pytest.importorskip("playwright.sync_api")
OLD = "Ab3dEf6hIj9k"
HASH = "#k=3q2-7wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA_8&c=1"

LANDING = ("<!doctype html><html><head><meta charset='utf-8'><title>new</title></head><body>"
           "<script>document.title = 'at:' + location.pathname + '|' + location.hash;</script></body></html>")

@pytest.fixture
def browser():
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

def _wait(port):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)

def _folders(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from plugins import gallery as G
    new = G.new_gid()
    web = tmp_path / "web" / "g"
    (web / OLD).mkdir(parents=True)
    (web / new).mkdir()
    (web / OLD / "index.html").write_bytes(G.redirect_page(new))
    (web / new / "index.html").write_text(LANDING)

    (web / OLD / ".htaccess").write_text("Require all denied\n")
    return tmp_path / "web", new

def _visit(browser, url):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("document.title.startsWith('at:')", timeout=10000)
    title = page.title()
    final = page.url
    ctx.close()
    return title, final

@pytest.mark.parametrize("form", ["slash", "no-slash", "index"])
def test_the_old_link_lands_on_the_new_folder_with_its_key_on_php(tmp_path, monkeypatch, browser, form):
    php = need_tool("php")
    root, new = _folders(tmp_path, monkeypatch)
    port = _port()
    srv = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(root)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    _wait(port)
    try:
        path = {"slash": f"/g/{OLD}/", "no-slash": f"/g/{OLD}", "index": f"/g/{OLD}/index.html"}[form]
        title, final = _visit(browser, f"http://127.0.0.1:{port}{path}{HASH}")
        assert title == f"at:/g/{new}/|{HASH}"
        assert final == f"http://127.0.0.1:{port}/g/{new}/{HASH}"
    finally:
        srv.terminate()
        srv.wait(timeout=10)

@pytest.mark.parametrize("form", ["slash", "index"])
def test_the_old_link_lands_on_the_new_folder_with_its_key_on_a_static_host(tmp_path, monkeypatch, browser, form):
    root, new = _folders(tmp_path, monkeypatch)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        path = {"slash": f"/g/{OLD}/", "index": f"/g/{OLD}/index.html"}[form]
        title, final = _visit(browser, f"http://127.0.0.1:{port}{path}{HASH}")
        assert title == f"at:/g/{new}/|{HASH}"
        assert final == f"http://127.0.0.1:{port}/g/{new}/{HASH}"
    finally:
        srv.shutdown()

def test_the_page_runs_under_its_own_policy_and_goes_nowhere_else(tmp_path, monkeypatch, browser):

    php = need_tool("php")
    root, new = _folders(tmp_path, monkeypatch)
    bad = (root / "g" / OLD / "index.html").read_text().replace(f'data-to="{new}"', 'data-to="//evil.example/x"')
    (root / "g" / OLD / "index.html").write_text(bad)
    port = _port()
    srv = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(root)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    _wait(port)
    try:
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:{port}/g/{OLD}/{HASH}")
        page.wait_for_timeout(500)
        assert page.url == f"http://127.0.0.1:{port}/g/{OLD}/{HASH}"
        assert not [e for e in errors if "Content Security Policy" in e]
        ctx.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
