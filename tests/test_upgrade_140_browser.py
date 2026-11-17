import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules
from helpers import host_bytes
from helpers_tools import need_tool

sync_api = pytest.importorskip("playwright.sync_api")
GID = "galOpenTab14"

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

def test_a_13_tab_left_open_fails_loudly_then_sends_after_a_reload(tmp_path, monkeypatch, browser):
    php = need_tool("php")
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp_path / "src"
    src.mkdir()
    for i, col in enumerate(("red", "blue")):
        Image.new("RGB", (40, 30), col).save(src / f"p{i}.jpg", "JPEG")
    key = G.new_key()
    out = G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True)).out_dir
    host = tmp_path / "web" / GID
    shutil.copytree(out, host)
    old = (ROOT / "tests" / "compat" / "pages" / "1.3.1" / "viewer.html").read_text()
    (host / "index.html").write_text(old.replace('<html lang="en">', f'<html lang="en" data-gid="{GID}" data-gate="1">', 1))
    (host / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\nproof=1\n")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(host.parent)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    try:
        ctx = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 860})
        ctx.add_init_script(f"localStorage.setItem('picks:/{GID}/:welcomed', '1');")
        page = ctx.new_page()
        url = f"http://127.0.0.1:{port}/{GID}/#k={key}"
        page.goto(url)
        page.wait_for_selector(".cell")
        page.locator(".cell").nth(0).click()
        page.wait_for_selector("#big.on")
        page.locator("#pr").click()
        page.locator("#done").click()
        page.locator("#send").click()
        page.wait_for_function("document.querySelector('#sent').textContent.length > 0")
        assert page.locator("#sent").inner_text() == "Couldn't send. Please tell your photographer."
        page.wait_for_timeout(2000)
        assert "Saved on this device" in page.locator("#trayn").inner_text()
        assert not [p.name for p in host.iterdir() if p.name in ("picks.las", "draft.las", "picks.bin", "draft.bin")]

        new = G.new_gid()
        nh = host.parent / new
        shutil.copytree(host, nh)
        (nh / "index.html").write_text(G.viewer_page(new, gate=True, credit=False, cws=GID))
        for p_ in host.iterdir():
            p_.unlink()
        (host / "index.html").write_bytes(G.redirect_page(new))
        page.reload()
        page.wait_for_selector(".cell")
        assert page.url == f"http://127.0.0.1:{port}/{new}/#k={key}"
        assert "Retouch 1/3" in page.locator("#trayn").inner_text()
        page.locator("#send").click()
        page.wait_for_function("document.querySelector('#sent').textContent.startsWith('Sent.')")
        assert G.open_picks(host_bytes(nh, "picks.las"), key, new)["retouch"] == ["p0.jpg"]
        assert sorted(p_.name for p_ in host.iterdir()) == ["index.html"]
        ctx.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
