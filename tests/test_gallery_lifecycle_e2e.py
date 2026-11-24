import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

from helpers_tools import need_tool
from helpers import host_bytes, host_has, host_text, host_write, remember_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
DESK = dict(viewport={"width": 1280, "height": 860})
PHONE = dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

def _php(folder):
    php = need_tool("php")
    port = _free_port()
    p = subprocess.Popen([php, "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return p, f"http://127.0.0.1:{port}/"

class Site:

    def __init__(self, tmp_path):
        import app as A
        self.A, self.tmp = A, tmp_path
        self.c = signed_in_client(A)
        self.c.post("/setup/shoot", data={"preset_families": "1", "preset_realestate": "1"}, environ_base=E)
        self.host = tmp_path / "host"
        self.host.mkdir()
        (self.host / ".vault-root").write_text("")
        from vault import web as VW
        self.VW = VW
        cfg = VW.config()
        cfg["gallery_host"] = {"type": "local", "path": str(self.host)}
        VW.write_config(cfg)
        self.php, self.base = _php(self.host)
        self.settings(gallery_base_url=self.base, business_email="hello@studio.example", business_name="Lark & Pine Studio")
        self.n = 0

    def settings(self, **kv):
        for k, v in kv.items():
            self.q("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))

    def q(self, sql, args=()):
        con = self.A.connect()
        try:
            r = con.execute(sql, args).fetchall()
            con.commit()
            return r
        finally:
            con.close()

    def gallery(self, name, stype="family", n=3, title=None):

        from PIL import Image
        self.n += 1
        folder = self.tmp / "photos" / "export" / f"s{self.n}"
        folder.mkdir(parents=True)
        for i in range(n):
            Image.effect_noise((320, 240), 50 + i).convert("RGB").save(folder / f"img{i + 1}.jpg", "JPEG")
        con = self.A.connect()
        hid = con.execute("INSERT INTO households (name) VALUES (?)", (name,)).lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date, title) VALUES (?, ?, 'shot', '2026-10-10', ?)",
                          (hid, stype, title)).lastrowid
        con.commit()
        con.close()
        self.build(sid, folder)
        self.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
        assert self.wait_up(sid) == "up"
        return sid

    def build(self, sid, folder=None):
        if folder is None:
            folder = self.q("SELECT source_dir FROM galleries WHERE session_id=?", (sid,))[0][0]
        self.c.post(f"/session/{sid}/gallery/build", data={"source_dir": str(folder).rstrip("/") + "/"}, environ_base=E)
        for _ in range(600):
            j = self.c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
            if j["state"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert j["state"] == "done", j
        for _ in range(200):
            if not self.c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json().get("label", "").startswith("Updating"):
                break
            time.sleep(0.05)
        time.sleep(0.3)

    def wait_up(self, sid):
        for _ in range(400):
            st = self.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0]
            if st in ("up", "error"):
                return st
            time.sleep(0.05)

    def gid(self, sid):
        r = self.q("SELECT s.gallery_id, g.key, g.ekey FROM sessions s LEFT JOIN galleries g ON g.session_id=s.id "
                   "WHERE s.id=?", (sid,))[0]
        remember_key(r[0], r[2] or r[1])
        return r[0]

    def link(self, sid):
        with self.A.app.app_context():
            return self.A.app.config["gallery_link"](sid)

    def sync(self):
        self.c.post("/gallery/sync", environ_base=E)

    def seen(self, sid, days_ago):

        at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        host_write(self.host / self.gid(sid), "seen.txt", at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        day = (date.today() - timedelta(days=days_ago)).isoformat()
        self.q("UPDATE sessions SET first_visit=?, sd_shipped=CASE WHEN type='realestate' THEN ? ELSE sd_shipped END WHERE id=?",
               (day, day, sid))

@pytest.fixture
def site(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    s = Site(tmp_path)
    yield s
    s.php.terminate()
    s.php.wait()

@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def _open(browser, url, opts=DESK, errors=None):
    ctx = browser.new_context(accept_downloads=True, **opts)
    page = ctx.new_page()
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("document.addEventListener('securitypolicyviolation', e => console.error('CSP ' + e.violatedDirective))")
        page.on("console", lambda m: errors.append(m.text) if m.text.startswith("CSP ") else None)
    page.goto(url)
    return ctx, page

def _expect(locator):
    from playwright.sync_api import expect
    return expect(locator)

def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

FOCUS_IN_NOTICE = "document.getElementById('sunset').contains(document.activeElement)"

@pytest.mark.parametrize("opts", [DESK, PHONE], ids=["desktop", "phone"])
def test_sunset_notice_on_every_visit_with_download_all_inside(site, browser, opts):
    proof = site.gallery("Lane")
    dl = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    for sid in (proof, dl):
        site.seen(sid, 25)
    errors = []
    ctx, page = _open(browser, site.link(dl), opts, errors)
    page.wait_for_selector("#sunset:not([hidden])")
    assert "Download your photos before then." in page.locator("#sswhen").inner_text()
    assert page.locator("#sunset #dlallb").count() == 1
    assert page.evaluate("document.activeElement.id") == "dlallb"
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUS_IN_NOTICE)
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(FOCUS_IN_NOTICE)
    with page.expect_download() as d:
        page.locator("#dlallb").focus()
        page.keyboard.press("Enter")
    assert d.value.suggested_filename == "418 Pine Street.zip"
    page.reload()
    page.wait_for_selector("#sunset:not([hidden])")
    page.keyboard.press("Escape")
    assert page.locator("#sunset").is_hidden() and page.locator("header #dlall").count() == 1
    ctx.close()
    ctx, page = _open(browser, site.link(proof), opts, errors)
    page.wait_for_selector("#sunset:not([hidden])")
    assert page.locator("#sunset #dlallb").count() == 0 and "hello@studio.example" in page.locator("#ssask").inner_text()
    page.keyboard.press("Escape")
    assert page.locator("#sunset").is_hidden() and page.evaluate("document.activeElement.id") == "wgo"
    ctx.close()
    assert errors == []

def test_no_sunset_notice_before_the_last_week(site, browser):
    sid = site.gallery("Lane")
    site.seen(sid, 10)
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector(".cell")
    page.wait_for_timeout(500)
    assert page.locator("#sunset").is_hidden()
    ctx.close()

def test_esc_still_closes_the_notice_after_download_all_inside_it(site, browser):
    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    site.seen(sid, 25)
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector("#sunset:not([hidden])")
    with page.expect_download():
        page.locator("#dlallb").click()
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    assert page.locator("#sunset").is_hidden()
    ctx.close()

def test_gone_dark_in_the_viewer_then_the_dark_page_on_the_host(site, browser):
    sid = site.gallery("Lane Family", title="Olivia Lane senior portraits")
    gid, link = site.gid(sid), site.link(sid)
    site.seen(sid, 31)
    code, body = _get(site.base + gid + "/gate.php?f=manifest.bin")
    assert code == 410 and b"hello@studio.example" in body
    for opts in (DESK, PHONE):
        ctx, page = _open(browser, link, opts)
        page.wait_for_selector("#darkmark:not([hidden])")
        assert "This gallery has gone dark." in page.locator("#msg").inner_text()
        assert page.locator("#msgact a").get_attribute("href") == "mailto:hello@studio.example"
        ctx.close()
    site.sync()
    assert [p.name for p in (site.host / gid).iterdir()] == ["index.html"]
    code, body = _get(site.base + gid + "/")
    page_ = body.decode()
    assert code == 200 and "This gallery has gone dark." in page_ and "mailto:hello@studio.example" in page_
    for secret in ("Lane", "Olivia", "senior", "<script"):
        assert secret not in page_
    errors = []
    ctx, page = _open(browser, link, PHONE, errors)
    assert "This gallery has gone dark." in page.locator("body").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390 and errors == []
    ctx.close()

def test_picks_sent_after_the_gate_closed_say_it_has_gone_dark(site, browser):
    sid = site.gallery("Lane")
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector(".cell")
    page.locator("#wgo").click()
    page.locator(".cell").nth(0).click()
    page.wait_for_selector("#big.on")
    page.locator("#pm").click()
    page.locator("#done").click()
    site.seen(sid, 31)
    page.locator("#send").click()
    _expect(page.locator("#sent")).to_contain_text("gone dark")
    ctx.close()

def _expire(site, sid):
    site.seen(sid, 70)
    site.sync()
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"

def test_one_download_closes_an_hour_after_download_all(site, browser):
    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    gid = site.gid(sid)
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"mode": "once"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    ctx, page = _open(browser, site.link(sid), PHONE)
    page.wait_for_selector("#sunset:not([hidden])")
    assert page.locator("#sstitle").inner_text() == "This gallery is open for one download."
    with page.expect_download():
        page.locator("#dlallb").click()
    left = int(host_text(site.host / gid, "done.txt")) - time.time()
    assert 3500 < left <= 3600
    ctx.close()
    host_write(site.host / gid, "done.txt", str(int(time.time()) - 1))
    assert _get(site.base + gid + "/gate.php?f=manifest.bin")[0] == 410
    site.sync()
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"
    assert [p.name for p in (site.host / gid).iterdir()] == ["index.html"]

def test_a_rebuild_keeps_the_one_download_closing(site, browser):
    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    gid = site.gid(sid)
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"mode": "once"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    site.build(sid)
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector("#sunset:not([hidden])")
    with page.expect_download():
        page.locator("#dlallb").click()
    ctx.close()
    assert host_has(site.host / gid, "done.txt")

def test_put_back_on_an_extended_session_shows_the_real_last_day(site):
    sid = site.gallery("Lane")
    site.q("UPDATE sessions SET extension_used=1 WHERE id=?", (sid,))
    site.seen(sid, 70)
    site.sync()
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    until = (date.today() + timedelta(days=7)).isoformat()
    assert f"until={until}\n" in host_text(site.host / site.gid(sid), "limits.txt")
    page = site.c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    with site.A.app.app_context():
        nice = site.A.nice(until)
    assert f"Online until {nice}" in page

def _change_host(site, path, base):
    return site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                                 "gallery_path": str(path), "gallery_init": "1", "gallery_base_url": base},
                       environ_base=E)

def test_changing_the_gallery_host_in_setup_with_open_sessions(site):
    site.gallery("Lane")
    new = site.tmp / "host2"
    new.mkdir()
    r = _change_host(site, new, "https://photos.newhost.example/g/")
    assert r.status_code == 302

def test_move_to_a_new_address_carries_picks_and_lists_the_client(site, browser):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector(".cell")
    page.locator("#wgo").click()
    page.locator(".cell").nth(0).click()
    page.wait_for_selector("#big.on")
    page.locator("#pm").click()
    page.locator("#done").click()
    page.locator("#send").click()
    _expect(page.locator("#sent")).to_have_text(re.compile("^Sent"))
    ctx.close()
    site.sync()
    new = site.tmp / "host2"
    new.mkdir()
    (new / ".vault-root").write_text("")
    cfg = site.VW.config()
    cfg["gallery_host_prev"], cfg["gallery_host"] = cfg["gallery_host"], {"type": "local", "path": str(new)}
    site.VW.write_config(cfg)
    php2, base2 = _php(new)
    try:
        site.settings(gallery_base_url=base2)
        assert "1 gallery is still on your earlier web host" in site.c.get("/", environ_base=E).get_data(as_text=True)
        site.c.post("/galleries/move", environ_base=E)
        assert host_bytes(new / gid, "picks.las") == host_bytes(site.host / gid, "picks.las")
        page_ = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
        assert "Lane" in page_ and base2 + gid + "/#k=" in page_
        ctx, page = _open(browser, site.link(sid), PHONE)
        page.wait_for_selector(".cell")
        page.wait_for_timeout(1000)

        assert page.locator(".cell").nth(0).get_attribute("aria-label") == "Photo 1, Suggested for portfolio"
        ctx.close()
        site.c.post("/galleries/clear-old", environ_base=E)
        assert not (site.host / gid).exists() and host_has(new / gid, "manifest.bin")
    finally:
        php2.terminate()
        php2.wait()

def test_take_the_old_copies_down_never_touches_the_host_in_use(site):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    cfg = site.VW.config()
    cfg["gallery_host_prev"] = dict(cfg["gallery_host"])
    site.VW.write_config(cfg)
    assert "Take the old copies down" not in site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert host_has(site.host / gid, "manifest.bin")

def test_the_same_folder_with_a_trailing_slash_is_not_a_new_host(site):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    cfg = site.VW.config()
    cfg["gallery_host_prev"] = dict(cfg["gallery_host"])
    cfg["gallery_host"] = {"type": "local", "path": str(site.host) + "/"}
    site.VW.write_config(cfg)
    site.c.post("/galleries/move", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert host_has(site.host / gid, "manifest.bin")
