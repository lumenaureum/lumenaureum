import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import DESK, E, _expire, _open, _php, browser, site

def _files(folder, gid):
    p = folder / gid
    return sorted(x.name for x in p.iterdir()) if p.is_dir() else []

def _setup_host(site, folder):

    r = site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                              "gallery_path": str(folder), "gallery_base_url": "https://new.example/g/",
                                              "gallery_init": "1"}, environ_base=E)
    assert r.status_code in (302, 303) and "saved=1" in r.headers.get("Location", "")
    site.settings(gallery_base_url=site.base)

def _galleries_off(site):
    r = site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365"}, environ_base=E)
    assert r.status_code in (302, 303)

def _job(key):
    import jobs
    return jobs.JOBS.get(key) or {}

def _new_folder(site, name):
    p = site.tmp / name
    p.mkdir()
    return p

def test_deleting_a_moved_session_leaves_nothing_on_the_earlier_host(site):

    sid = site.gallery("Moved Then Deleted")
    keep = site.gallery("Stays")
    gid = site.gid(sid)
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post("/galleries/move", environ_base=E)
    assert "Moved 2" in _job("gallery-move")["message"]
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    site.c.post("/galleries/clear-old", environ_base=E)
    assert site.gid(keep) not in [p.name for p in site.host.iterdir()]
    assert _files(site.host, gid) == []

def test_deleting_a_session_still_on_the_earlier_host_takes_it_down_there(site):

    sid = site.gallery("Stranded Then Deleted")
    site.gallery("Other")
    gid = site.gid(sid)
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    if _job(f"session-delete:{sid}").get("state") == "error":
        assert site.q("SELECT id FROM sessions WHERE id=?", (sid,))
        return
    site.c.post("/galleries/move", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == []

def test_a_session_being_deleted_is_not_put_back_online(site, monkeypatch):

    sid = site.gallery("Changed My Mind")
    gid = site.gid(sid)
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    real = site.A.app.config["retention_purge_now"]

    def slow(s_):
        time.sleep(3)
        return real(s_)
    monkeypatch.setitem(site.A.app.config, "retention_purge_now", slow)
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    time.sleep(0.5)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    for _ in range(200):
        if _job(f"session-delete:{sid}").get("state") != "running":
            break
        time.sleep(0.1)
    time.sleep(1)
    assert not site.q("SELECT id FROM sessions WHERE id=?", (sid,))
    assert not (site.host / gid / "manifest.bin").exists()

def test_take_the_old_copies_down_is_not_offered_while_galleries_are_off(site):

    sid = site.gallery("Live While Off")
    gid = site.gid(sid)
    _galleries_off(site)
    page = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert "Take the old copies down" not in page
    assert "manifest.bin" in _files(site.host, gid)

def test_galleries_off_and_on_after_a_move_keeps_the_earlier_host(site):

    sid = site.gallery("Hop")
    gid = site.gid(sid)
    new = _new_folder(site, "host2")
    _setup_host(site, new)
    site.c.post("/galleries/move", environ_base=E)
    _galleries_off(site)
    _setup_host(site, new)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == []

def test_a_second_host_change_before_the_move_keeps_the_first_host(site):

    sid = site.gallery("Two Hops")
    gid = site.gid(sid)
    _setup_host(site, _new_folder(site, "hostB"))
    _setup_host(site, _new_folder(site, "hostC"))
    site.c.post("/galleries/move", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == []

def test_the_same_folder_through_a_link_or_dot_dot_is_no_host_change(site):
    sid = site.gallery("Same Folder")
    gid = site.gid(sid)
    before = _files(site.host, gid)
    alias = site.tmp / "alias"
    os.symlink(site.host, alias)
    for path in (alias, f"{site.host}/../{site.host.name}", f"{site.host}/"):
        _setup_host(site, path)
        assert not (site.VW.config().get("gallery_host_prev") or site.VW.config().get("gallery_hosts_prev"))
        assert "Take the old copies down" not in site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
        site.c.post("/galleries/clear-old", environ_base=E)
        assert _files(site.host, gid) == before

def test_two_sftp_accounts_with_a_relative_root_are_two_places():

    from plugins.gallery import host_identity
    a = {"type": "sftp", "host": "sftp.provider.example", "user": "alice", "root": "public_html/g"}
    b = dict(a, user="bob")
    assert host_identity(a) != host_identity(b)

def test_the_download_gallery_after_a_restored_proofing_gallery_has_its_own_days(site):

    sid = site.gallery("Rowan")
    gid = site.gid(sid)
    site.q("UPDATE sessions SET delivery_method='link' WHERE id=?", (sid,))
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    site.q("UPDATE sessions SET retouch_picks='img1.jpg: warmer' WHERE id=?", (sid,))
    site.build(sid)
    from plugins import gallery as G
    assert G.is_download(gid)
    site.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
    for _ in range(400):
        if site.q("SELECT 1 FROM audit WHERE session_id=? AND action LIKE '%in place of the one that was online%'", (sid,)):
            break
        time.sleep(0.05)
    lim = (site.host / gid / "limits.txt").read_text().split("\n")
    assert not [l_ for l_ in lim if l_.startswith(("until=", "until_ts="))]
    (site.host / gid / "seen.txt").write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    site.sync()
    site.q("UPDATE galleries SET restored_at=? WHERE session_id=?", ((date.today() - timedelta(days=8)).isoformat(), sid))
    site.sync()
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "up"

def test_a_one_download_already_made_stays_made_after_a_move(site, browser):

    sid = site.gallery("Pine Street Realty", stype="realestate", title="418 Pine Street")
    gid = site.gid(sid)
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"mode": "once"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    ctx, page = _open(browser, site.link(sid))
    page.wait_for_selector("#sunset:not([hidden])")
    with page.expect_download():
        page.locator("#dlallb").click()
    ctx.close()
    assert (site.host / gid / "done.txt").exists()
    new = _new_folder(site, "host2")
    _setup_host(site, new)
    site.c.post("/galleries/move", environ_base=E)
    assert (new / gid / "manifest.bin").exists()
    assert (new / gid / "done.txt").exists()
