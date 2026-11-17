import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules
from test_viewer_browser import Host

sync_api = pytest.importorskip("playwright.sync_api")
GID = "g-galBrowse01AAAAAAAAAAAA"
BASE = f"https://gallery.test/g/{GID}/"

def _photos(folder, n):
    from PIL import Image
    folder.mkdir()
    for i in range(n):
        im = Image.effect_noise((900, 600), 60 + i).convert("RGB")
        im.save(folder / (f"Café {i + 1}.jpg" if i == 0 else f"img{i + 1}.jpg"), "JPEG", quality=92)

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("viewer_dl")
    was = os.environ.get("CRM_GALLERY_ROOT")
    os.environ["CRM_GALLERY_ROOT"] = str(tmp / "galleries")
    fresh_modules()
    from plugins import gallery as G
    _photos(tmp / "src", 4)
    out = {}
    for split, root in ((G.ZIP_SPLIT, "one"), (300_000, "parts")):
        G.OUT_ROOT = tmp / root
        G.ZIP_SPLIT, G.ZIP_PART = split, 100_000
        key = G.new_key()
        G.build(GID, tmp / "src", key, picks=dict(post=True), download=True,
                brand=dict(client="The Sample Family", studio="Sample Studio", what="Real estate shoot",
                           site="https://studio.example.com/"))
        m = json.loads(_open(G, key, (G.OUT_ROOT / GID / "manifest.bin").read_bytes(), "manifest.bin"))
        out[root] = (G.OUT_ROOT / GID, key, m)
    yield G, out
    if was is None:
        os.environ.pop("CRM_GALLERY_ROOT", None)
    else:
        os.environ["CRM_GALLERY_ROOT"] = was
    fresh_modules()

def _open(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

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

def _page(browser, host, key, phone=False):
    ctx = browser.new_context(bypass_csp=True, accept_downloads=True,
                              **(dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                                 if phone else dict(viewport={"width": 1280, "height": 860})))
    ctx.route("https://gallery.test/**", host.handle)
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    return ctx, page

@pytest.mark.parametrize("phone", [False, True])
def test_download_one_photo_and_all(built, browser, phone):
    G, out = built
    folder, key, m = out["one"]
    ctx, page = _page(browser, Host(folder), key, phone)
    b = page.locator("#dlall button")
    assert b.count() == 1
    size = m["zips"][0]["size"]
    assert b.inner_text() == f"Download all ({max(1, round(size / 1e6))} MB)"
    assert page.locator("#welcome").is_hidden() and page.locator("#filter").is_hidden() and page.locator("#tray").is_hidden()
    assert page.locator("#how").is_hidden()

    assert page.locator("#brand a").get_attribute("href") == "https://studio.example.com/"
    assert "studio.example.com" in page.locator("#contact").inner_text()
    page.locator(".cell").nth(0).click()
    page.wait_for_selector("#big.on:not(.ph)")
    for pick in ("#pr", "#pm", "#pv", "#note"):
        assert page.locator(pick).is_hidden()
    assert page.locator("#dl").is_visible() and page.locator("#done").is_visible()
    with page.expect_download() as d:
        page.locator("#dl").click()
    it = m["items"][0]
    assert d.value.suggested_filename == it["on"] == "Café 1.jpg"
    got = Path(d.value.path()).read_bytes()
    assert got == _open(G, key, (folder / it["o"]).read_bytes(), it["o"])
    from PIL import Image
    assert Image.open(io.BytesIO(got)).size == (it["ow"], it["oh"]) == (900, 600)
    page.locator("#done").click()
    with page.expect_download() as d:
        page.locator("#dlall button").click()
    assert d.value.suggested_filename == "The Sample Family.zip"
    data = Path(d.value.path()).read_bytes()
    assert len(data) == size
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None and sorted(zf.namelist()) == sorted(i_["on"] for i_ in m["items"])
    page.wait_for_function("document.querySelector('#dlall button').textContent.startsWith('Download all (')")
    ctx.close()

def test_a_large_gallery_offers_each_part(built, browser):
    G, out = built
    folder, key, m = out["parts"]
    n = len(m["zips"])
    assert n > 1
    ctx, page = _page(browser, Host(folder), key)
    labels = page.locator("#dlall button").all_inner_texts()
    assert [t.split(" (")[0] for t in labels] == [f"Download all, part {j} of {n}" for j in range(1, n + 1)]
    names = []
    for j in range(n):
        with page.expect_download() as d:
            page.locator("#dlall button").nth(j).click()
        assert d.value.suggested_filename == f"The Sample Family-part-{j + 1}-of-{n}.zip"
        zf = zipfile.ZipFile(io.BytesIO(Path(d.value.path()).read_bytes()))
        assert zf.testzip() is None
        names += zf.namelist()
    assert sorted(names) == sorted(i_["on"] for i_ in m["items"])
    ctx.close()

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
          "Mobile/15E148 Safari/604.1")
SHARE_STUB = """window.__shared = []; window.__fail = "";
navigator.canShare = d => !!(d && d.files && d.files.length);
navigator.share = async d => { if (window.__fail) throw new DOMException("no", window.__fail);
  const f = d.files[0]; window.__shared.push([f.name, f.size, f.type]); };"""

def test_on_an_iphone_one_photo_goes_to_the_share_sheet(built, browser):
    G, out = built
    folder, key, m = out["one"]
    ctx = browser.new_context(bypass_csp=True, accept_downloads=True, user_agent=IPHONE, viewport={"width": 390, "height": 844},
                              has_touch=True, is_mobile=True)
    ctx.route("https://gallery.test/**", Host(folder).handle)
    ctx.add_init_script(SHARE_STUB)
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    page.locator(".cell").nth(1).click()
    page.wait_for_selector("#big.on:not(.ph)")
    page.locator("#dl").click()
    page.wait_for_function("window.__shared.length === 1")
    it = m["items"][1]
    assert page.evaluate("window.__shared[0]") == [it["on"], it["os"], "image/jpeg"]
    page.evaluate("window.__fail = 'AbortError'")
    page.locator("#dl").click()
    page.wait_for_function("document.querySelector('#dl').textContent === 'Download'")
    page.evaluate("window.__fail = 'NotAllowedError'")
    with page.expect_download() as d:
        page.locator("#dl").click()
    assert d.value.suggested_filename == it["on"]
    assert not page.evaluate("[...document.querySelectorAll('a[download]')].length")
    ctx.close()

class Rebuilt(Host):

    def __init__(self, folder, gone, manifest, after):
        super().__init__(folder)
        self.gone, self.new_manifest, self.after, self.manifests = gone, manifest, after, 0

    def handle(self, route):
        name = route.request.url.split("#")[0][len(BASE):]
        if name.startswith("gate.php?f="):
            name = name.split("=", 1)[1]
        if name == "manifest.bin":
            self.manifests += 1
            if self.manifests >= self.after:
                return route.fulfill(status=200, body=self.new_manifest, headers={"content-type": "application/octet-stream"})
        elif name in self.gone and self.manifests >= self.after - 1:
            return route.fulfill(status=404, body=b"")
        return super().handle(route)

def _rebuilt(G, folder, key, m, after):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = G.seal(AESGCM(G.key_bytes(key)), json.dumps(dict(m, title="The Sample Family, updated")).encode(), "manifest.bin")
    return Rebuilt(folder, set(m["zips"][0]["parts"][-1:]) | {m["items"][0]["o"]}, blob, after)

@pytest.mark.parametrize("which", ["all", "one"])
def test_a_rebuild_is_noticed_before_a_download_starts(built, browser, which):

    G, out = built
    folder, key, m = out["one"]
    host = _rebuilt(G, folder, key, m, after=2)
    ctx, page = _page(browser, host, key)
    saved = []
    page.on("download", lambda d: saved.append(d))
    if which == "all":
        label = page.locator("#dlall button").inner_text()
        page.locator("#dlall button").click()
    else:
        page.locator(".cell").nth(1).click()
        page.wait_for_selector("#big.on:not(.ph)")
        page.locator("#dl").click()
    page.wait_for_selector("#upd:not([hidden])")
    assert "Reload" in page.locator("#upd").inner_text()
    page.wait_for_timeout(500)
    assert not saved
    if which == "all":
        assert page.locator("#dlall button").inner_text() == label
    ctx.close()

def test_a_rebuild_during_download_all_offers_a_reload(built, browser):
    G, out = built
    folder, key, m = out["one"]
    host = _rebuilt(G, folder, key, m, after=3)
    ctx, page = _page(browser, host, key)
    page.locator("#dlall button").click()
    page.wait_for_function("document.querySelector('#dlall button').textContent === 'Try again'")
    page.wait_for_selector("#upd:not([hidden])")
    ctx.close()

def test_the_update_notice_is_full_width_on_a_phone(built, browser):

    G, out = built
    folder, key, m = out["one"]
    host = _rebuilt(G, folder, key, m, after=2)
    ctx, page = _page(browser, host, key, phone=True)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_selector("#upd:not([hidden])")
    box = page.locator("#upd").bounding_box()
    assert box["width"] >= 390 - 2 * 8 - 1 and box["height"] < 120
    ctx.close()

def test_an_open_page_notices_a_rebuild_by_itself(built, browser):

    G, out = built
    folder, key, m = out["one"]
    host = _rebuilt(G, folder, key, m, after=2)
    ctx, page = _page(browser, host, key)
    assert page.locator("#upd").is_hidden()
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_selector("#upd:not([hidden])")
    ctx.close()

SLOW = """(() => { const f = window.fetch, ms = %d;
  window.fetch = (u, o) => /[0-9a-f]{24}\\.bin/.test(String(u)) && !String(u).includes("manifest")
    ? new Promise((ok, no) => { const t = setTimeout(() => f(u, o).then(ok, no), ms);
        if (o && o.signal) o.signal.addEventListener("abort", () => { clearTimeout(t); no(new DOMException("stopped", "AbortError")); }); })
    : f(u, o); })();"""

def _slow_page(browser, host, key, ms, phone=False):
    ctx = browser.new_context(bypass_csp=True, accept_downloads=True,
                              **(dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                                 if phone else dict(viewport={"width": 1280, "height": 860})))
    ctx.route("https://gallery.test/**", host.handle)
    ctx.add_init_script(SLOW % ms)
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    return ctx, page

@pytest.mark.parametrize("how", ["button", "escape"])
def test_download_all_can_be_canceled(built, browser, how):
    G, out = built
    folder, key, m = out["one"]
    ctx, page = _slow_page(browser, Host(folder), key, 1500)
    saved = []
    page.on("download", lambda d: saved.append(d))
    b = page.locator("#dlall button")
    label = b.inner_text()
    b.click()
    page.wait_for_function("document.querySelector('#dlall button').textContent.endsWith('Cancel')")
    assert b.inner_text().startswith("Downloading ")
    if how == "button":
        b.click()
    else:
        page.keyboard.press("Escape")
    page.wait_for_function(f"document.querySelector('#dlall button').textContent === {json.dumps(label)}")
    page.wait_for_timeout(2000)
    assert not saved and b.inner_text() == label
    with page.expect_download() as d:
        b.click()
    assert zipfile.ZipFile(io.BytesIO(Path(d.value.path()).read_bytes())).testzip() is None
    ctx.close()

def test_download_all_says_time_left_and_to_keep_the_page_open_on_a_phone(built, browser):
    G, out = built
    folder, key, m = out["one"]
    n = len(m["zips"][0]["parts"])
    ctx, page = _slow_page(browser, Host(folder), key, 1200, phone=True)
    assert page.locator("#dlwait").is_hidden()
    with page.expect_download(timeout=120000) as d:
        page.locator("#dlall button").click()
        page.wait_for_selector("#dlwait:not([hidden])")
        assert page.locator("#dlwait").inner_text() == "Keep this page open until the download finishes."
        assert n > 12
        page.wait_for_function("/ left \\u00b7 Cancel$/.test(document.querySelector('#dlall button').textContent)")
    assert d.value.suggested_filename.endswith(".zip")
    page.wait_for_selector("#dlwait", state="hidden")
    ctx.close()

def test_a_download_gallery_drops_picks_left_from_the_proofs(built, browser):

    G, out = built
    folder, key, m = out["one"]
    path = f"/g/{GID}/"
    left = {"v": 2, "picks": {f"n:{m['items'][1]['n']}": {"r": True, "n": "Soften the shadow"}, "#0": {"m": True}}}
    ctx = browser.new_context(bypass_csp=True)
    ctx.route("https://gallery.test/**", Host(folder).handle)
    ctx.add_init_script(f"localStorage.setItem('picks:{path}', {json.dumps(json.dumps(left))});"
                        f"localStorage.setItem('picks:{path}:sent', '[1]')")
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    assert page.locator(".tag i").count() == 0
    assert all(page.locator(".cell").nth(i).get_attribute("aria-label") == f"Photo {i + 1}" for i in range(len(m["items"])))
    assert page.evaluate(f"[localStorage.getItem('picks:{path}'), localStorage.getItem('picks:{path}:sent')]") == [None, None]
    ctx.close()

def test_the_visit_stamp_goes_as_a_form(built, browser):

    G, out = built
    folder, key, m = out["one"]
    host, seen = Host(folder), []
    ctx = browser.new_context(bypass_csp=True)

    def handle(route):
        if route.request.url.endswith("/seen.php"):
            seen.append((route.request.headers.get("content-type"), route.request.post_data))
        return host.handle(route)
    ctx.route("https://gallery.test/**", handle)
    page = ctx.new_page()
    page.goto(BASE + "#k=" + key)
    page.wait_for_selector(".cell")
    page.wait_for_timeout(300)
    import hashlib
    proof = hashlib.sha256(G.key_bytes(key)).hexdigest()[:32]
    assert seen == [("application/x-www-form-urlencoded;charset=UTF-8", "p=" + proof)]
    ctx.close()

def test_a_failed_piece_says_try_again(built, browser):
    G, out = built
    folder, key, m = out["one"]
    host = Host(folder)
    ctx, page = _page(browser, host, key)
    host.down = True
    page.locator("#dlall button").click()
    page.wait_for_function("document.querySelector('#dlall button').textContent === 'Try again'")
    host.down = False
    with page.expect_download() as d:
        page.locator("#dlall button").click()
    assert zipfile.ZipFile(io.BytesIO(Path(d.value.path()).read_bytes())).testzip() is None
    ctx.close()
