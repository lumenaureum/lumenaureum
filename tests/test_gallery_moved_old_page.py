import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import host_has, host_text
from test_gallery_lifecycle_e2e import DESK, _expect, _php, browser
from test_upgrade_from_120 import EXPECTED, _manifest, _q, upgraded

E = {"REMOTE_ADDR": "127.0.0.1"}

def _link(base, gid, key):
    return f"{base}{gid}/#k={key}"

@pytest.mark.parametrize("who", ["lane", "kim", "reyes", "diaz"])
def test_a_1_2_page_left_behind_by_a_move_is_told_it_moved(upgraded, browser, who):
    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('business_email', 'hello@studio.example') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit(); con.close()
    sid = EXPECTED["sessions_by_name"][who]
    gid, key, _, m = _manifest(A, G, tmp_path, sid)
    old_dir = tmp_path / "host"
    before = (old_dir / gid / "index.html").read_text()
    assert G._script_hashes(before) != G.VIEWER_SCRIPT_HASHES
    new_dir = tmp_path / "host2"
    new_dir.mkdir()
    (new_dir / ".vault-root").write_text("")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_hosts_prev"], cfg["gallery_host"] = [cfg["gallery_host"]], {"type": "local", "path": str(new_dir)}
    VW.write_config(cfg)
    c.post("/galleries/move", environ_base=E)

    new = _q(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    k = _q(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))[0]
    assert G.is_link(new) and host_has(new_dir / new, "manifest.bin", k[1] or k[0]) and not (new_dir / gid).exists()
    after = (old_dir / gid / "index.html").read_text()
    assert G._script_hashes(after) == G.VIEWER_SCRIPT_HASHES and f'data-gid="{new}"' in after[:400]
    assert f'data-cws="{gid}"' in after[:400]
    assert "moved=1" in host_text(old_dir / gid, "limits.txt"), host_text(old_dir / gid, "limits.txt")
    assert "hello@studio.example" in host_text(old_dir / gid, "limits.txt"), host_text(old_dir / gid, "limits.txt")
    php, base = _php(old_dir)
    errors = []
    try:
        ctx = browser.new_context(**DESK)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        drafts = []
        page.on("request", lambda r: drafts.append(r.url) if r.method == "POST" and "kind=draft" in r.url else None)
        page.goto(_link(base, gid, key))
        page.wait_for_selector(".cell img")
        page.wait_for_function("() => [...document.querySelectorAll('.cell img')].some(i => i.complete && i.naturalWidth > 0)")
        assert page.locator(".cell").count() == len(m["items"])
        if m.get("dl"):
            assert page.locator("#dlallb").count() == 1
        else:
            if page.locator("#wgo").is_visible():
                page.locator("#wgo").click()
            page.locator(".cell").nth(0).click()
            page.wait_for_selector("#big.on")
            page.locator("#pr").click()
            drop = page.get_by_role("button", name="Remove")
            if drop.count() and drop.is_visible():
                drop.click()
            page.locator("#done").click()
            page.wait_for_timeout(2500)
            assert len(drafts) == 1
            page.locator("#send").click()
            _expect(page.locator("#sent")).to_contain_text("has moved")
            _expect(page.locator("#sent")).to_contain_text("hello@studio.example")
            page.locator(".cell").nth(1).click()
            page.wait_for_selector("#big.on")
            page.locator("#pv").click() if page.locator("#pv").is_visible() else page.locator("#pr").click()
            page.locator("#done").click()
            page.wait_for_timeout(2500)
            assert len(drafts) == 1
        ctx.close()
    finally:
        php.terminate()
        php.wait()
    assert not errors
