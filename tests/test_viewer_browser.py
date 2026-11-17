import os
import sys
import time
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, posted_envelope

sync_api = pytest.importorskip("playwright.sync_api")
GID = "g-galBrowse01AAAAAAAAAAAA"
BASE = f"https://gallery.test/g/{GID}/"

@pytest.fixture(scope="module")
def gallery(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("viewer")
    was = os.environ.get("CRM_GALLERY_ROOT")
    os.environ["CRM_GALLERY_ROOT"] = str(tmp / "galleries")
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp / "src"
    src.mkdir()
    for i, col in enumerate(((120, 90, 60), (60, 90, 120), (90, 120, 60), (30, 30, 30)), 1):
        Image.new("RGB", (400, 300), col).save(src / f"img{i}.jpg", "JPEG")
    key = G.new_key()
    out = G.build(GID, src, key, picks=dict(retouch=2, market=2, email="", ref=GID, post=True),
                  brand=dict(client="The Sample Family", studio="Sample Studio", what="Family session"))
    yield Path(out.out_dir), key
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

class Host:

    def __init__(self, folder):
        self.folder, self.files, self.down = folder, {}, False

    def handle(self, route):
        req = route.request
        url = req.url.split("#")[0]
        name = url[len(BASE):] or "index.html"
        if self.down and name != "index.html" and "manifest" not in name and "draft" not in name and "picks" not in name:
            return route.abort()
        if name.startswith("gate.php?f="):
            name = name.split("=", 1)[1]
        if name.startswith("picks.php"):
            self.files["draft.las" if "kind=draft" in name else "picks.las"] = posted_envelope(req.post_data_buffer)
            return route.fulfill(status=204, body=b"")
        if name == "seen.php":
            return route.fulfill(status=204, body=b"")
        if name in self.files:
            return route.fulfill(status=200, body=self.files[name], headers={"content-type": "application/octet-stream"})
        p = self.folder / name
        if not p.is_file() or name.endswith(".php"):
            return route.fulfill(status=404, body=b"")
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else "application/octet-stream"
        return route.fulfill(status=200, body=p.read_bytes(), headers={"content-type": ctype})

def open_page(browser, host, key, phone=False, welcomed=True):
    ctx = browser.new_context(bypass_csp=True, **(dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                                 if phone else dict(viewport={"width": 1280, "height": 860})))
    ctx.route("https://gallery.test/**", host.handle)
    if welcomed:
        ctx.add_init_script(f"localStorage.setItem('picks:/g/{GID}/:welcomed', '1')")
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    return ctx, page

def pick_retouch(page, i, note=""):
    page.locator(".cell").nth(i).click()
    page.locator("#pr").click()
    if note:
        page.locator("#note").fill(note)
    page.locator("#done").click()

def test_sent_stays_and_the_other_device_knows(gallery, browser):
    folder, key = gallery[0], gallery[1]
    host = Host(folder)
    ctx, page = open_page(browser, host, key)
    pick_retouch(page, 0, "fix the glare")
    page.wait_for_function("document.querySelector('#trayn').textContent.endsWith('Saved  \\u00b7  Not sent yet')")
    page.locator("#send").click()
    page.wait_for_function("document.querySelector('#sent').textContent.startsWith('Sent.')")
    time.sleep(2.5)
    assert page.locator("#sent").inner_text() == "Sent. Thank you! You can change them and send again."
    assert page.locator("#sent").is_visible() and page.locator("#sent").get_attribute("role") == "status"
    assert "Not sent yet" not in page.locator("#trayn").inner_text()

    ctx2, page2 = open_page(browser, host, key)
    page2.wait_for_function("document.querySelector('#trayn').textContent.includes('Retouch 1')")
    assert "Not sent yet" not in page2.locator("#trayn").inner_text()
    assert page2.locator("#sent").inner_text().startswith("Sent.")
    asked = []
    page2.on("dialog", lambda d: (asked.append(d.type), d.accept()))
    page2.goto("about:blank")
    assert asked == []

    page.locator(".cell").nth(1).click()
    page.locator("#pm").click()
    assert page.locator("#sent").inner_text() == ""
    ctx.close(); ctx2.close()

def test_retouch_with_a_note_asks_first_and_done_is_always_there(gallery, browser):
    folder, key = gallery[0], gallery[1]
    ctx, page = open_page(browser, Host(folder), key)
    page.locator(".cell").nth(2).click()
    assert page.locator("#done").is_visible()
    page.locator("#pr").click()
    page.locator("#note").fill("brighten faces")
    page.locator("#pr").click()
    assert page.locator("#ask").is_visible()
    assert page.locator("#asktext").inner_text() == "Remove Retouch from photo 3? Your note goes too."
    assert page.evaluate("document.activeElement.id") == "askkeep"
    page.keyboard.press("Tab"); assert page.evaluate("document.activeElement.id") == "askgo"
    page.keyboard.press("Tab"); assert page.evaluate("document.activeElement.id") == "askkeep"
    page.keyboard.press("Escape")
    assert page.locator("#ask").is_hidden() and page.locator("#lb.open").count() == 1
    assert page.locator("#note").input_value() == "brighten faces"
    assert page.locator("#pr").get_attribute("aria-pressed") == "true"
    page.locator("#pr").click()
    page.locator("#askgo").click()
    assert page.locator("#pr").get_attribute("aria-pressed") == "false" and page.locator("#note").is_hidden()
    ctx.close()

def test_grid_names_badges_dialog_and_filters_by_keyboard(gallery, browser):
    folder, key = gallery[0], gallery[1]
    ctx, page = open_page(browser, Host(folder), key)
    pick_retouch(page, 2, "fix the glare")
    assert page.locator(".cell").nth(2).get_attribute("aria-label") == "Photo 3, Retouch: fix the glare"

    page.locator(".cell").nth(2).locator(".tag i").click()
    assert page.evaluate("document.activeElement.id") == "askkeep"
    for _ in range(3):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") in ("askkeep", "askgo")
    page.keyboard.press("Escape")
    assert page.locator("#ask").is_hidden() and page.evaluate("document.querySelector('#grid').inert") is False

    page.locator("#filter button[data-k=r]").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.dataset.k") == "r"
    assert page.locator("#filter button[data-k=r]").get_attribute("aria-pressed") == "true"
    page.keyboard.press("Shift+Tab"); page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.dataset.k") == "all"
    ctx.close()

def test_retouch_limit_is_said_and_the_welcome_box_hands_focus_to_the_first_photo(gallery, browser):
    folder, key = gallery[0], gallery[1]
    ctx, page = open_page(browser, Host(folder), key, welcomed=False)
    page.wait_for_selector("#welcome:not([hidden])")
    assert "take one off" in page.locator("#wlist").inner_text()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.className") == "cell"
    assert page.evaluate("document.activeElement.dataset.i") == "0"
    pick_retouch(page, 0)
    pick_retouch(page, 1)
    page.locator(".cell").nth(2).click()
    page.locator("#pr").click()
    msg = "You've picked 2 to retouch already. Remove one to pick this one."
    assert page.locator("#count").inner_text() == msg
    page.wait_for_function(f"document.querySelector('#live').textContent === {msg!r}")
    assert page.locator("#live").get_attribute("aria-live") == "polite"
    ctx.close()

def test_pinch_never_changes_or_closes_the_photo(gallery, browser):
    folder, key = gallery[0], gallery[1]
    ctx, page = open_page(browser, Host(folder), key, phone=True)
    page.locator(".cell").nth(1).tap()
    page.wait_for_selector("#lb.open")
    assert page.evaluate("getComputedStyle(document.querySelector('#lb')).touchAction") == "pan-y pinch-zoom"
    cdp = ctx.new_cdp_session(page)

    def touch(kind, pts):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [dict(x=x, y=y, id=i) for i, (x, y) in enumerate(pts)]})
    touch("touchStart", [(195, 400)])
    touch("touchStart", [(195, 400), (200, 420)])
    for k in range(1, 6):
        touch("touchMove", [(195 - 30 * k, 400 + 30 * k), (200 + 30 * k, 420 - 30 * k)])
    touch("touchEnd", [(200 + 150, 420 - 150)])
    touch("touchEnd", [])
    assert page.locator("#lb.open").count() == 1
    assert page.locator("#lb").get_attribute("aria-label") == "Photo 2 of 4"
    assert page.evaluate("visualViewport.scale") > 1.05
    ctx.close()
    ctx, page = open_page(browser, Host(folder), key, phone=True)
    page.locator(".cell").nth(1).tap()
    page.wait_for_selector("#lb.open")
    cdp = ctx.new_cdp_session(page)
    touch("touchStart", [(300, 400)])
    touch("touchMove", [(150, 405)])
    touch("touchEnd", [])
    page.wait_for_function("document.querySelector('#lb').getAttribute('aria-label') === 'Photo 3 of 4'")
    ctx.close()

def test_a_photo_that_cannot_load_says_so(gallery, browser):
    folder, key = gallery[0], gallery[1]
    host = Host(folder)
    ctx, page = open_page(browser, host, key)
    host.down = True
    page.locator(".cell").nth(3).click()
    page.wait_for_selector("#lberr:not([hidden])")
    assert "can't load right now" in page.locator("#lberr").inner_text()
    host.down = False
    page.locator("#lbagain").click()
    page.wait_for_selector("#big.on")
    assert page.locator("#lberr").is_hidden()
    ctx.close()

class Swapped(Host):

    def __init__(self, folder, manifest):
        super().__init__(folder)
        self.new_manifest, self.swapped = manifest, False

    def handle(self, route):
        name = route.request.url.split("#")[0][len(BASE):]
        if self.swapped and name in ("manifest.bin", "gate.php?f=manifest.bin"):
            return route.fulfill(status=200, body=self.new_manifest, headers={"content-type": "application/octet-stream"})
        return super().handle(route)

def test_a_stale_proofs_page_never_claims_to_send_picks(gallery, browser):
    folder, key = gallery[0], gallery[1]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from plugins import gallery as G
    blob = G.seal(AESGCM(G.key_bytes(key)), b'{"v":1,"title":"Newer","items":[],"picks":{},"dl":1,"zips":[]}', "manifest.bin")
    host = Swapped(folder, blob)
    ctx, page = open_page(browser, host, key)
    pick_retouch(page, 1, "brighter")
    host.swapped = True
    host.files.pop("picks.las", None)
    page.locator("#send").click()
    page.wait_for_selector("#upd:not([hidden])")
    page.wait_for_function("document.querySelector('#sent').textContent.includes('updated')")
    assert "Sent." not in page.locator("#sent").inner_text() and "picks.las" not in host.files
    page.locator("#send").click()
    page.wait_for_timeout(300)
    assert "picks.las" not in host.files
    ctx.close()
