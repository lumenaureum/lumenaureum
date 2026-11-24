import html
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _files, _new_folder, _setup_host
from test_sec_gal4_155 import _copy_as, _move, _sweep, _today

OLD = "oldcopy12345"
STRAYS, CLEAR = "gallery-strays-remove", "gallery-clear-old"

def _plugin(site):
    return site.A.app.config["gallery_take_down"].__globals__

def _wait(key):
    import jobs
    for _ in range(600):
        if not jobs.running(key):
            return jobs.JOBS[key]
        time.sleep(0.05)
    raise AssertionError(f"{key} still running")

def _quick_save(site):
    t0 = time.monotonic()
    r = site.c.post("/settings/reset", data={"confirm": "no"}, environ_base=E)
    return r.status_code, time.monotonic() - t0

def _history(site, like):
    return " ".join(str(tuple(r)) for r in site.q("SELECT action, note FROM audit WHERE action LIKE ?", (like,)))

def test_remove_the_folders_runs_in_the_background_and_a_save_meanwhile_never_waits(site, monkeypatch):
    import jobs
    import locks
    gid = site.gid(site.gallery("Owner One"))
    _copy_as(site, gid, OLD)
    _sweep(site)
    jobs.JOBS.pop(STRAYS, None)
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    P = _plugin(site)
    real = P["delete_gallery"]
    go, inside, held = threading.Event(), threading.Event(), []

    def slow(n, **kw):
        held.append(locks.WRITES._is_owned())
        inside.set()
        go.wait(30)
        return real(n, **kw)
    monkeypatch.setitem(P, "delete_gallery", slow)
    try:
        t0 = time.monotonic()
        r = site.c.post("/galleries/strays/remove", environ_base=E)
        assert r.status_code == 302 and time.monotonic() - t0 < 5
        with site.c.session_transaction() as s_:
            assert s_.get("notice") == "Removing the folders now. This page updates when it is done."
        assert inside.wait(20) and held == [False]
        page = _move(site)
        assert "Removing the folders from the web host now" in page
        assert f'data-ui-job="/jobs/state?key={STRAYS}"' in page and 'id="cleanup" data-refresh' in page
        assert "/galleries/strays/remove" not in page and "/galleries/strays/keep" not in page
        today = html.unescape(_today(site))
        assert "Removing gallery folders this studio doesn't know from the web host" in today
        assert f'data-ui-job="/jobs/state?key={STRAYS}"' in today and "not known to this studio" not in today
        code, took = _quick_save(site)
        assert code == 302 and took < 5
        site.c.post("/galleries/strays/keep", environ_base=E)
        assert OLD in str(site.q("SELECT value FROM settings WHERE key='gallery_strays'")[0][0])
    finally:
        go.set()
        _wait(STRAYS)
    j = jobs.JOBS[STRAYS]
    assert j["state"] == "done" and j["message"].startswith("Removed 1 gallery folder this studio didn't know"), j
    assert not (site.host / OLD).exists() and (site.host / gid).is_dir()
    assert OLD in _history(site, "Removed%")
    assert "not known to this studio" not in _today(site) and "Removing the folders" not in _move(site)

def test_a_folder_adopted_while_the_removal_runs_is_never_removed(site, monkeypatch):

    sid = site.gallery("Owner Two")
    gid = site.gid(sid)
    _copy_as(site, gid, OLD)
    _sweep(site)
    P = _plugin(site)
    real = P["gallery_host"]

    def listed_then_owned():
        if sys._getframe(1).f_code.co_name == "remove_strays":
            site.q("UPDATE galleries SET link_was=? WHERE session_id=?", (OLD, sid))
        return real()
    monkeypatch.setitem(P, "gallery_host", listed_then_owned)
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / OLD).is_dir() and (site.host / gid).is_dir()

def test_today_says_while_a_cleanup_runs_and_adopt_waits(site):
    import jobs
    sid = site.gallery("Owner Three")
    site.q("DELETE FROM galleries WHERE session_id=?", (sid,))
    site.q("UPDATE sessions SET gallery_id=NULL WHERE id=?", (sid,))
    try:
        jobs.JOBS[STRAYS] = dict(key=STRAYS, label="x", state="running", done=0, total=0, message="", started=0,
                                 ended=None, link="", quiet=True)
        site.c.post(f"/session/{sid}/gallery/adopt", data={"link": "https://example.com/g/x#k"}, environ_base=E)
        with site.c.session_transaction() as s_:
            assert s_.get("notice", "").startswith("Not adopted: gallery folders this studio doesn't know are being")
        jobs.JOBS[CLEAR] = dict(jobs.JOBS[STRAYS], key=CLEAR)
        today = _today(site)
        assert "Taking the old gallery copies down from the earlier web host" in today
        assert f'data-ui-job="/jobs/state?key={CLEAR}"' in today
    finally:
        jobs.JOBS.pop(STRAYS, None)
        jobs.JOBS.pop(CLEAR, None)
    assert "Taking the old gallery copies down" not in _today(site)

def test_take_the_old_copies_down_never_holds_the_write_lock_and_the_page_says_so(site, monkeypatch):
    import jobs
    import locks
    sid = site.gallery("Moved Away")
    gid = site.gid(sid)
    new = _new_folder(site, "host2")
    _setup_host(site, new)
    site.c.post("/galleries/move", environ_base=E)
    assert "manifest.bin" in _files(new, gid) and "manifest.bin" in _files(site.host, gid)
    jobs.JOBS.pop(CLEAR, None)
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    P = _plugin(site)
    real = P["final_collect"]
    go, inside, held = threading.Event(), threading.Event(), []

    def slow(g_, host):
        held.append(locks.WRITES._is_owned())
        inside.set()
        go.wait(30)
        return real(g_, host)
    monkeypatch.setitem(P, "final_collect", slow)
    fp = P["fingerprint_of"](P["earlier_host_cfgs"]()[0])
    try:
        t0 = time.monotonic()
        assert site.c.post("/galleries/clear-old", data={"host": fp}, environ_base=E).status_code == 302
        assert time.monotonic() - t0 < 5
        assert inside.wait(20) and held == [False]
        page = _move(site)
        assert "Taking the old copies down now" in page and f'data-ui-job="/jobs/state?key={CLEAR}"' in page
        assert "/galleries/clear-old" not in page and "/galleries/forget-host" not in page
        assert "Taking the old gallery copies down from the earlier web host" in _today(site)
        code, took = _quick_save(site)
        assert code == 302 and took < 5
        site.c.post("/galleries/forget-host", data={"host": fp}, environ_base=E)
        assert P["earlier_host_cfgs"]()
    finally:
        go.set()
        _wait(CLEAR)
    assert jobs.JOBS[CLEAR]["state"] == "done" and jobs.JOBS[CLEAR]["message"].startswith("Removed 1 gallery from ")
    assert _files(site.host, gid) == [] and "manifest.bin" in _files(new, gid)
    assert "Removed 1 gallery from the earlier web host" in _history(site, "Removed%")
    assert "Taking the old copies down now" not in _move(site)

def _build(e, monkeypatch):
    import shutil
    import prints_env as PE
    from prints_env import E as PEE
    P = e.A.app.config["gallery_take_down"].__globals__
    monkeypatch.setitem(P, "FOLDERS", True)
    dst = P["PHOTOS_ROOT"] / "shoot"
    if not dst.exists():
        shutil.copytree(e.tmp / "src", dst)
    r = e.c.post("/session/1/gallery/build", data={"from": "folder", "source_dir": str(dst)}, environ_base=PEE)
    assert r.status_code == 302
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=60)
    st = e.c.get("/session/1/gallery/status", environ_base=PEE).get_json()
    assert st["state"] == "done", st
    return PE.manifest(e)

def test_selling_prints_from_proofs_builds_nothing_to_pick_and_switching_off_brings_picks_back(tmp_path, monkeypatch):
    import prints_env as PE
    from prints_env import E as PEE
    e = PE.make(tmp_path, monkeypatch, proofs=True)
    m = _build(e, monkeypatch)
    assert m.get("pr") == 1 and not m.get("dl")
    assert not m["picks"].get("retouch") and not m["picks"].get("market") and not m["picks"].get("email")
    assert e.c.post("/session/1/prints/switch", data={}, environ_base=PEE).status_code == 302
    m = _build(e, monkeypatch)
    assert not m.get("pr") and m["picks"].get("retouch") and m["picks"].get("market") and m["picks"].get("email")

def test_the_page_hides_retouch_and_portfolio_while_selling_prints(tmp_path):

    import json as _json
    import pytest
    sync_api = pytest.importorskip("playwright.sync_api")
    from helpers import fresh_modules
    from test_viewer_browser import Host
    from test_viewer_download import GID, BASE, _photos
    was = os.environ.get("CRM_GALLERY_ROOT")
    os.environ["CRM_GALLERY_ROOT"] = str(tmp_path / "galleries")
    fresh_modules()
    try:
        from plugins import gallery as G
        _photos(tmp_path / "src", 2)
        picks = dict(post=True, retouch=3, market=5, email="studio@example.com")
        try:
            pw = sync_api.sync_playwright().start()
            b = pw.chromium.launch()
        except Exception as ex:
            pytest.skip(f"chromium is not available: {ex}")
        seen = {}
        try:
            for shop in (True, False):
                G.OUT_ROOT = tmp_path / ("shop" if shop else "plain")
                key = G.new_key()
                G.build(GID, tmp_path / "src", key, picks=picks, keep_full=shop, brand=dict(client="The Sample Family"))
                ctx = b.new_context(bypass_csp=True, viewport={"width": 1280, "height": 860})
                ctx.route("https://gallery.test/**", Host(G.OUT_ROOT / GID).handle)
                page = ctx.new_page()
                page.goto(BASE + "#k=" + key)
                page.wait_for_selector(".cell")
                welcome = page.locator("#welcome").is_visible()
                if welcome:
                    page.keyboard.press("Escape")
                    page.wait_for_selector("#welcome", state="hidden")
                page.locator(".cell").nth(0).click()
                page.wait_for_selector("#big.on:not(.ph)")
                seen[shop] = dict(welcome=welcome, done=page.locator("#done").is_visible(),
                                  **{k: page.locator("#" + k).is_visible() for k in ("pr", "pm", "pv")})
                ctx.close()
        finally:
            b.close()
            pw.stop()
        assert seen[True] == dict(welcome=False, done=True, pr=False, pm=False, pv=False), _json.dumps(seen)
        assert seen[False]["pr"] and seen[False]["pm"] and seen[False]["pv"] and seen[False]["done"]
    finally:
        if was is None:
            os.environ.pop("CRM_GALLERY_ROOT", None)
        else:
            os.environ["CRM_GALLERY_ROOT"] = was
        fresh_modules()
