import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import host_bytes, host_has, host_text, host_write
from test_gallery_lifecycle_e2e import (DESK, E, PHONE, _expect, _expire, _open, _php, browser, site)

def _keys(site, sid):
    key, ekey = site.q("SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))[0]
    return ekey or key

def _sealed(site, sid, name, at, photo="img2.jpg"):

    from helpers import seal_picks
    return seal_picks(_keys(site, sid), site.gid(sid), [{"i": 1, "name": photo, "k": "n:" + photo, "r": True}],
                      "picks" if name == "picks.las" else "draft", at)

def _proof(site, sid):
    from plugins import gallery as G
    return hashlib.sha256(G.key_bytes(_keys(site, sid))).hexdigest()[:32]

def _post_picks(url, blob, proof):
    import base64
    d = base64.urlsafe_b64encode(blob).decode().rstrip("=")
    req = urllib.request.Request(url, data=("d=" + d).encode(), method="POST",
                                 headers={"X-Proof": proof, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def _new_host(site):
    new = site.tmp / "host2"
    new.mkdir()
    (new / ".vault-root").write_text("")
    cfg = site.VW.config()
    cfg["gallery_host_prev"], cfg["gallery_host"] = cfg["gallery_host"], {"type": "local", "path": str(new)}
    site.VW.write_config(cfg)
    return new

def _now(minus_min=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minus_min)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def test_picks_sent_to_the_old_host_after_the_last_check_come_with_the_move(site):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    (site.host / gid / "picks.las").write_bytes(_sealed(site, sid, "picks.las", _now(60), "img1.jpg"))
    site.sync()
    newer = _sealed(site, sid, "picks.las", _now(1), "img3.jpg")
    (site.host / gid / "picks.las").write_bytes(newer)
    new = _new_host(site)
    php2, base2 = _php(new)
    try:
        site.settings(gallery_base_url=base2)
        today = site.c.get("/", environ_base=E).get_data(as_text=True)
        assert "1 gallery is still on your earlier web host. Put it on the new one" in today
        site.c.post("/galleries/move", environ_base=E)
        assert host_bytes(new / gid, "picks.las") == newer
        assert "moved=1\n" in host_text(site.host / gid, "limits.txt")
        assert "moved=" not in host_text(new / gid, "limits.txt")
        site.sync()
        assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "img3.jpg"

        code, body = _post_picks(site.base + gid + "/picks.php", newer, _proof(site, sid))
        assert code == 409 and json.loads(body) == {"moved": 1, "contact": "hello@studio.example"}
        assert _post_picks(base2 + gid + "/picks.php", newer, _proof(site, sid))[0] == 204
        words = [r[0] for r in site.q("SELECT action FROM audit ORDER BY rowid")]
        assert "Started putting galleries on the new web host" in words and "Galleries move go" not in words
    finally:
        php2.terminate()
        php2.wait()

def test_after_the_move_the_old_host_is_still_read_until_its_copies_come_down(site):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    new = _new_host(site)
    site.c.post("/galleries/move", environ_base=E)
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0]

    late = _sealed(site, sid, "picks.las", _now(0), "img2.jpg")
    (site.host / gid / "picks.las").write_bytes(late)
    site.sync()
    assert host_bytes(new / gid, "picks.las") == late
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "img2.jpg"
    older = _sealed(site, sid, "picks.las", _now(600), "img1.jpg")
    (site.host / gid / "picks.las").write_bytes(older)
    site.sync()
    assert host_bytes(new / gid, "picks.las") == late
    site.c.post("/galleries/clear-old", environ_base=E)
    words = [r[0] for r in site.q("SELECT action FROM audit ORDER BY rowid")]
    assert "Started taking the old copies down from the earlier web host" in words
    site.sync()
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0] is None

def test_the_viewer_on_the_old_copy_says_it_has_moved(site, browser):
    sid = site.gallery("Lane")
    link = site.link(sid)
    _new_host(site)
    site.c.post("/galleries/move", environ_base=E)
    ctx, page = _open(browser, link)
    page.wait_for_selector(".cell")
    page.locator("#wgo").click()
    page.locator(".cell").nth(0).click()
    page.wait_for_selector("#big.on")
    page.locator("#pm").click()
    page.locator("#done").click()
    page.locator("#send").click()
    _expect(page.locator("#sent")).to_contain_text("moved")
    assert "hello@studio.example" in page.locator("#sent").inner_text()
    ctx.close()

@pytest.fixture
def sydney():
    was = os.environ.get("TZ")
    os.environ["TZ"] = "Australia/Sydney"
    time.tzset()
    yield
    if was is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = was
    time.tzset()

def test_a_fixed_last_day_closes_at_the_end_of_that_day_here(sydney):
    from plugins import gallery as G
    end = G.day_close("2026-12-01")
    assert datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-12-01T12:59:59Z"

def test_the_gate_uses_the_exact_instant_and_still_reads_the_date_alone(site):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    d = site.host / gid
    today = datetime.now(timezone.utc).date().isoformat()
    proof = {"X-Proof": _proof(site, sid)}

    def head():
        req = urllib.request.Request(site.base + gid + "/gate.php?f=manifest.bin", headers=proof)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)
    soon = int(time.time()) + 3600
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nproof=1\nwarn_days=7\nuntil={today}\nuntil_ts={soon}\n")
    code, h = head()
    assert code == 200 and h["X-Sunset"] == datetime.fromtimestamp(soon, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nproof=1\nuntil=2099-01-01\nuntil_ts={int(time.time()) - 5}\n")
    assert head()[0] == 410
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nproof=1\nwarn_days=7\nuntil={today}\n")
    code, h = head()
    assert code == 200 and h["X-Sunset"] == today + "T23:59:59Z"
    host_write(d, "limits.txt", f"days=30\nunopened_until={today}\nunopened_ts={int(time.time()) - 5}\nproof=1\n")
    assert head()[0] == 410

def test_put_back_online_writes_the_instant_the_app_takes_it_down(site):
    sid = site.gallery("Lane")
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    from plugins import gallery as G
    until = (date.today() + timedelta(days=7)).isoformat()
    lim = host_text(site.host / site.gid(sid), "limits.txt")
    assert f"until={until}\nuntil_ts={G.day_close(until)}\n" in lim and "unopened_ts=" in lim

def test_no_extension_is_offered_for_a_gallery_put_back_online(site):
    sid = site.gallery("Lane")
    site.seen(sid, 70)
    site.sync()
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "14"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    page = site.c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)
    with site.A.app.app_context():
        nice = site.A.nice((date.today() + timedelta(days=14)).isoformat())
    assert f"Deletes {nice}" in page and 'name="extension_used" value="1" ' not in page and "back online" in page
    site.c.post(f"/session/{sid}/quick", data={"act": "extend"}, environ_base=E)
    assert f"until={(date.today() + timedelta(days=14)).isoformat()}\n" in host_text(site.host / site.gid(sid), "limits.txt")

def test_a_removed_gallery_shows_no_planned_deletion(site):
    sid = site.gallery("Lane")
    _expire(site, sid)
    page = site.c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert ", off the web" in page and "Online until" not in page and "Closing " not in page

def test_esc_closes_the_notice_and_the_download_carries_on(site, browser):
    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    site.seen(sid, 25)
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector("#sunset:not([hidden])")
    assert page.evaluate("document.querySelector('header').inert && document.querySelector('footer').inert")
    held, hold = [], [True]
    page.route("**/gate.php?f=*", lambda route: held.append(route) if hold[0] and "manifest" not in route.request.url
               else route.continue_())
    page.locator("#dlallb").click()
    _expect(page.locator("#dlallb")).to_contain_text("Cancel")
    assert page.evaluate("document.activeElement.id") == "dlallb"
    page.keyboard.press("Escape")
    assert page.locator("#sunset").is_hidden()
    assert not page.evaluate("document.querySelector('header').inert")
    assert "Cancel" in page.locator("#dlallb").inner_text()
    with page.expect_download() as d:
        hold[0] = False
        while held:
            held.pop().continue_()
    assert d.value.suggested_filename == "418 Pine Street.zip"
    ctx.close()

def test_put_back_online_brings_the_clients_picks_back(site, browser):
    sid = site.gallery("Lane")
    gid = site.gid(sid)
    picks = _sealed(site, sid, "picks.las", _now(5), "img1.jpg")
    (site.host / gid / "picks.las").write_bytes(picks)
    site.sync()
    _expire(site, sid)
    assert not host_has(site.host / gid, "picks.las")
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    assert host_bytes(site.host / gid, "picks.las") == picks

def test_a_second_device_after_the_one_download_reads_that_it_was_made(site, browser):
    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    gid = site.gid(sid)
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"mode": "once"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector("#sunset:not([hidden])")
    assert "single photos doesn't count" in page.locator("#sswhen").inner_text()
    with page.expect_download():
        page.locator("#dlallb").click()
    ctx.close()
    assert host_has(site.host / gid, "done.txt")
    ctx, page = _open(browser, site.link(sid), PHONE)
    page.wait_for_selector("#sunset:not([hidden])")
    assert page.locator("#sstitle").inner_text() == "This gallery's one download has been made."
    assert "If it isn't used" not in page.locator("#sswhen").inner_text()
    ctx.close()

def test_long_words_wrap_on_a_phone(site, browser):
    site.settings(business_email="photographs.and.portraits.by.lark@studio.example",
                  business_name="LarkAndPinePhotographyStudioOfFineArtPortraiture")
    sid = site.gallery("Lane", title="TheVeryLongUnbrokenTitleOfThisSeniorPortraitSession")
    site.seen(sid, 25)
    ctx, page = _open(browser, site.link(sid), PHONE)
    page.wait_for_selector("#sunset:not([hidden])")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.keyboard.press("Escape")
    page.locator("#wgo").click()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()

def test_a_proofing_visit_asks_for_no_saved_picks_that_are_not_there(site, browser):
    sid = site.gallery("Lane")
    ctx = browser.new_context(**DESK)
    page = ctx.new_page()
    missing = []
    page.on("response", lambda r: missing.append(r.url) if r.status == 404 else None)
    page.goto(site.link(sid))
    page.wait_for_selector(".cell img.on")
    page.wait_for_timeout(800)
    assert missing == []

    page.locator("#wgo").click()
    page.locator(".cell").nth(0).click()
    page.wait_for_selector("#big.on")
    page.locator("#pr").click()
    page.locator("#note").fill("soften the shadow")
    page.locator("#note").press("Enter")
    page.wait_for_timeout(200)
    assert page.locator("#lb.open").count() == 1
    page.wait_for_timeout(2000)
    ctx.close()
    ctx, page = _open(browser, site.link(sid))
    missing.clear()
    page.on("response", lambda r: missing.append(r.url) if r.status == 404 else None)
    page.reload()
    page.wait_for_selector(".cell .tag i")
    page.wait_for_timeout(500)
    assert missing == [] and host_has(site.host / site.gid(sid), "draft.las")
    ctx.close()
