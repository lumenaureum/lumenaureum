import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

sync_api = pytest.importorskip("playwright.sync_api")
GID, MAIL_GID = "galView111a", "galView111m"

def base(gid):
    return f"https://gallery.test/g/{gid}/"

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("viewer111")
    was = os.environ.get("CRM_GALLERY_ROOT")
    os.environ["CRM_GALLERY_ROOT"] = str(tmp / "galleries")
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp / "src"
    src.mkdir()
    for i in range(1, 13):
        Image.new("RGB", (400, 300), (20 * i, 90, 200 - 10 * i)).save(src / f"img{i}.jpg", "JPEG")
    key, mkey = G.new_key(), G.new_key()
    out = G.build(GID, src, key, picks=dict(retouch=3, market=2, email="", ref=GID, post=True),
                  brand=dict(client="The Sample Family", studio="Sample Studio"))
    mail = G.build(MAIL_GID, src, mkey, picks=dict(retouch=3, market=2, email="studio@example.com", ref=MAIL_GID),
                   brand=dict(client="The Sample Family", studio="Sample Studio"))
    yield G, dict(folder=Path(out.out_dir), key=key), dict(folder=Path(mail.out_dir), key=mkey)
    if was is None:
        os.environ.pop("CRM_GALLERY_ROOT", None)
    else:
        os.environ["CRM_GALLERY_ROOT"] = was
    fresh_modules()

@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def seal(G, key, obj, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    return b"LAK1" + iv + AESGCM(G.key_bytes(key)).encrypt(iv, json.dumps(obj).encode(), f"lumen-aureum-gallery:{name}".encode())

def manifest(G, folder, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b = (folder / "manifest.bin").read_bytes()
    return json.loads(AESGCM(G.key_bytes(key)).decrypt(b[:12], b[12:], b"lumen-aureum-gallery:manifest.bin"))

class Host:

    def __init__(self, folder, gid):
        self.folder, self.gid, self.files, self.gone, self.held, self.waiting = folder, gid, {}, set(), set(), []

    def handle(self, route):
        name = route.request.url.split("#")[0][len(base(self.gid)):] or "index.html"
        if name.startswith("gate.php?f="):
            name = name.split("=", 1)[1]
        if name.startswith("picks.php"):
            self.files["draft.bin" if "kind=draft" in name else "picks.bin"] = route.request.post_data_buffer
            return route.fulfill(status=204, body=b"")
        if name == "seen.php":
            return route.fulfill(status=204, body=b"")
        if name in self.gone:
            return route.fulfill(status=404, body=b"")
        if name in self.held:
            self.waiting.append((route, name))
            return None
        self.serve(route, name)

    def serve(self, route, name):
        if name in self.files:
            return route.fulfill(status=200, body=self.files[name], headers={"content-type": "application/octet-stream"})
        p = self.folder / name
        if not p.is_file() or name.endswith(".php"):
            return route.fulfill(status=404, body=b"")
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else "application/octet-stream"
        return route.fulfill(status=200, body=p.read_bytes(), headers={"content-type": ctype})

    def release(self):
        self.held.clear()
        for route, name in self.waiting:
            self.serve(route, name)
        self.waiting.clear()

def open_page(browser, host, key, gid=GID, store=None, init=""):
    ctx = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 860})
    ctx.route("https://gallery.test/**", host.handle)
    k = f"picks:/g/{gid}/"
    script = f"localStorage.setItem({k + ':welcomed'!r}, '1');"
    for kk, v in (store or {}).items():
        script += f"if (!sessionStorage.getItem('seeded')) localStorage.setItem({k + kk!r}, {json.dumps(v)});"
    script += "sessionStorage.setItem('seeded', '1');" + init
    ctx.add_init_script(script)
    page = ctx.new_page()
    page.goto(base(gid) + "#k=" + key)
    page.wait_for_selector(".cell")
    return ctx, page

def tray(page):
    return page.locator("#trayn").inner_text()

def test_a_forged_draft_saying_sent_is_never_shown_as_sent(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    ctx, page = open_page(browser, host, g["key"])
    page.locator(".cell").nth(0).click()
    page.locator("#pr").click()
    page.locator("#done").click()
    page.locator("#send").click()
    page.wait_for_function("document.querySelector('#sent').textContent.startsWith('Sent.')")
    ctx.close()

    at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    host.files["draft.bin"] = seal(G, g["key"], {"v": 1, "at": at, "sent": True, "picks": [
        {"i": 4, "name": "img5.jpg", "k": "n:img5.jpg", "r": True, "note": "Attacker-chosen note", "m": False, "v": False}]}, "draft.bin")
    ctx, page = open_page(browser, host, g["key"])
    page.wait_for_function("document.querySelector('#trayn').textContent.endsWith('Not sent yet')")
    assert "Retouch 1/3" in tray(page) and page.locator("#sent").inner_text() == ""
    ctx.close()

def test_a_draft_from_the_future_is_ignored(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    host.files["draft.bin"] = seal(G, g["key"], {"v": 1, "at": "2099-01-01T00:00:00Z", "picks": [
        {"i": 4, "name": "img5.jpg", "k": "n:img5.jpg", "r": False, "note": "", "m": True, "v": False}]}, "draft.bin")
    mine = {"v": 2, "picks": {"n:img2.jpg": {"r": True, "n": "soften"}}, "at": "2026-11-10T10:00:00.000Z"}
    ctx, page = open_page(browser, host, g["key"], store={"": json.dumps(mine)})
    page.wait_for_timeout(2500)
    kept = json.loads(page.evaluate(f"localStorage.getItem('picks:/g/{GID}/')"))
    assert kept["picks"] == mine["picks"]
    ctx.close()

def test_picks_sent_from_an_older_page_count_as_sent_when_picks_bin_matches(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)

    host.files["picks.bin"] = seal(G, g["key"], {"v": 1, "at": "2026-10-01T10:00:00Z", "picks": [
        {"i": 0, "name": "img1.jpg", "r": True, "note": "less glare", "m": False, "v": False},
        {"i": 2, "name": "img3.jpg", "r": False, "note": "", "m": True, "v": False}]}, "picks.bin")
    old = {"0": {"r": True, "n": "less glare"}, "2": {"m": True}}
    ctx, page = open_page(browser, host, g["key"], store={"": json.dumps(old)})
    page.wait_for_function("document.querySelector('#sent').textContent.startsWith('Sent.')")
    assert "Not sent yet" not in tray(page)
    ctx.close()

    host = Host(built[2]["folder"], MAIL_GID)
    ctx, page = open_page(browser, host, built[2]["key"], gid=MAIL_GID, store={"": json.dumps(old)})
    page.wait_for_function("document.querySelector('#trayn').textContent.includes('Retouch 1')")
    assert tray(page).endswith("Saved on this device") and "Not sent yet" not in tray(page)
    asked = []
    page.on("dialog", lambda d: (asked.append(d.type), d.accept()))
    page.goto("about:blank")
    assert asked == []
    ctx.close()

def test_a_photo_removed_by_an_update_offers_a_reload(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    m = manifest(G, g["folder"], g["key"])
    ctx, page = open_page(browser, host, g["key"])

    host.gone.add(m["items"][7]["f"])
    page.locator(".cell").nth(7).click()
    page.wait_for_selector("#lberr:not([hidden])")
    assert page.locator("#lbsay").inner_text() == "This photo can't load right now."
    assert page.locator("#lbagain").inner_text() == "Try again"
    page.locator("#close").click()

    host.files["manifest.bin"] = seal(G, g["key"], dict(m, title="Updated"), "manifest.bin")[4:]
    host.gone.update(it["f"] for it in m["items"])
    page.locator(".cell").nth(9).click()
    page.wait_for_function("document.querySelector('#lbsay').textContent.startsWith('These photos were just updated')")
    assert page.locator("#lbsay").inner_text() == "These photos were just updated. Reload to see the new version."
    assert page.locator("#lbagain").inner_text() == "Reload" and page.locator("#upd").is_visible()
    page.locator("#lbagain").click()
    page.wait_for_function("document.querySelector('#title').textContent === 'Updated'")
    ctx.close()

def test_only_a_few_big_photos_stay_in_memory(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    track = """(() => { const live = new Set(), c = URL.createObjectURL, r = URL.revokeObjectURL;
      URL.createObjectURL = b => { const u = c.call(URL, b); live.add(u); return u; };
      URL.revokeObjectURL = u => { live.delete(u); return r.call(URL, u); };
      window.__live = live; })();"""
    ctx, page = open_page(browser, host, g["key"], init=track)
    page.locator(".cell").nth(0).click()
    for i in range(1, 11):
        page.wait_for_selector("#big.on:not(.ph)")
        page.locator("#next").click()
        page.wait_for_function(f"document.querySelector('#lb').getAttribute('aria-label') === 'Photo {i + 1} of 12'")
    page.wait_for_selector("#big.on:not(.ph)")
    page.wait_for_timeout(300)
    big = page.evaluate("""[...window.__live].filter(u => ![...document.querySelectorAll('.cell img, #logo')]
                           .some(i => i.getAttribute('src') === u)).length""")
    assert big <= 5
    ctx.close()

def test_pick_buttons_wait_until_the_photo_is_shown(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    m = manifest(G, g["folder"], g["key"])
    ctx, page = open_page(browser, host, g["key"])
    host.held.add(m["items"][5]["f"])
    page.locator(".cell").nth(5).click()
    page.wait_for_selector("#lbload:not([hidden])")
    assert page.locator("#lbload").inner_text() == "Loading photo..."
    assert all(page.locator(f"#{b}").is_disabled() for b in ("pr", "pm", "pv"))
    host.release()
    page.wait_for_selector("#big.on:not(.ph)")
    assert page.locator("#lbload").is_hidden() and page.locator("#pr").is_enabled()
    page.locator("#pr").click()
    assert page.locator("#pr").get_attribute("aria-pressed") == "true"
    ctx.close()
