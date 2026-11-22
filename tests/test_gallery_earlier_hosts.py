import os
import sys
import time
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import host_has, host_text
from test_gallery_lifecycle_e2e import E, _expire, site
from test_gallery_round_two import _files, _galleries_off, _job, _new_folder, _setup_host

def _plugin(site):

    return site.A.app.config["gallery_take_down"].__globals__

def _gone(folder):

    (folder / ".vault-root").rename(folder / ".vault-root-off")

def _back(folder):
    (folder / ".vault-root-off").rename(folder / ".vault-root")

def _today(site):
    return site.c.get("/", environ_base=E).get_data(as_text=True)

def _move_page(site):
    return site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)

def _history(site, sid=None):
    rows = site.q("SELECT action, COALESCE(note, '') FROM audit" + (" WHERE session_id=?" if sid else ""), (sid,) if sid else ())
    return [f"{a} {n}" for a, n in rows]

def test_setup_keeps_every_earlier_place_and_never_the_one_in_use():
    from plugins.gallery import earlier_host_cfgs, fingerprint_of, setup_host, setup_off
    A, B, C = ({"type": "local", "path": f"/srv/{n}"} for n in "abc")
    cfg = {"gallery_host": A}
    setup_host(cfg, B)
    setup_host(cfg, C)
    assert [h["path"] for h in earlier_host_cfgs(cfg)] == ["/srv/a", "/srv/b"]
    setup_off(cfg)
    assert cfg["gallery_host_off"] == C and "gallery_host" not in cfg
    assert [h["path"] for h in earlier_host_cfgs(cfg)] == ["/srv/a", "/srv/b"]
    setup_host(cfg, dict(C, path="/srv/c/"))
    assert [h["path"] for h in earlier_host_cfgs(cfg)] == ["/srv/a", "/srv/b"] and "gallery_host_off" not in cfg
    setup_off(cfg)
    setup_host(cfg, A)
    assert [h["path"] for h in earlier_host_cfgs(cfg)] == ["/srv/b", "/srv/c/"]
    assert fingerprint_of(cfg["gallery_host"]) == fingerprint_of(A)
    legacy = {"gallery_host": C, "gallery_host_prev": A}
    assert earlier_host_cfgs(legacy) == [A]

def test_a_host_is_named_by_its_address_never_a_path():
    from plugins.gallery import host_label
    assert host_label({"type": "sftp", "host": "sftp.example.com", "user": "ann", "root": "/home/ann/www/g"}) == "sftp.example.com"
    assert host_label({"type": "sftp", "host": "h.example", "port": 2222}) == "h.example:2222"
    assert "/srv" not in host_label({"type": "local", "path": "/srv/private/clients/g"})
    assert "secret" not in host_label({"type": "s3", "bucket": "pics", "secret_key": "secret", "access_key": "secret"})

def test_galleries_off_nothing_moves_or_comes_down_and_on_again_is_where_it_was(site):
    sid = site.gallery("Off And On")
    gid = site.gid(sid)
    before = _files(site.host, gid)
    _galleries_off(site)
    page = _move_page(site)
    assert "Galleries are turned off" in page and "The gallery address is now" not in page
    assert "Take the old copies down" not in page and "Put 1 gallery" not in page
    site.c.post("/galleries/move", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == before
    _setup_host(site, site.host)
    assert not site.VW.config().get("gallery_hosts_prev")
    assert "still on your earlier web host" not in _today(site)
    assert _files(site.host, gid) == before

def test_galleries_off_then_on_with_another_host_makes_the_first_one_earlier(site):
    sid = site.gallery("Off Then Elsewhere")
    gid = site.gid(sid)
    _galleries_off(site)
    new = _new_folder(site, "host2")
    _setup_host(site, new)
    assert "1 gallery is still on your earlier web host" in _today(site)
    site.c.post("/galleries/move", environ_base=E)
    assert host_has(new / gid, "manifest.bin")
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == [] and not site.VW.config().get("gallery_hosts_prev")

def test_a_session_deleted_while_galleries_are_off_comes_down_from_where_it_is(site):
    sid = site.gallery("Deleted While Off")
    gid = site.gid(sid)
    _galleries_off(site)
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(site.host, gid) == []

def test_a_deleted_clients_gallery_on_an_unreachable_earlier_host_is_kept_in_mind(site):
    sid = site.gallery("Client Gone")
    hid = site.q("SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]
    gid = site.gid(sid)
    _setup_host(site, _new_folder(site, "host2"))
    _gone(site.host)
    r = site.c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    assert r.status_code in (302, 303) and _job(f"client-delete:{hid}")["state"] == "done"
    assert not site.q("SELECT 1 FROM households WHERE id=?", (hid,))
    assert "manifest.bin" in _files(site.host, gid)
    assert [tuple(r) for r in site.q("SELECT gallery_id FROM gallery_leftovers")] == [(gid,)]
    assert "1 old gallery folder still has to come down from a web host" in _today(site)
    page = _move_page(site)
    assert f"gallery (folder {gid})" in page and "This host is gone for good" in page
    assert not any(str(site.host) in h for h in _history(site))
    _back(site.host)
    site.sync()
    assert "manifest.bin" in _files(site.host, gid)
    site.q("DELETE FROM settings WHERE key LIKE 'gallery_unreachable:%'")
    site.sync()
    assert _files(site.host, gid) == [] and not site.q("SELECT 1 FROM gallery_leftovers")
    assert "still has to come down" not in _today(site)

def test_take_the_old_copies_down_also_takes_deleted_sessions_galleries(site):
    sid = site.gallery("Moved Then Deleted Unreachable")
    gid = site.gid(sid)
    new = _new_folder(site, "host2")
    _setup_host(site, new)
    site.c.post("/galleries/move", environ_base=E)
    _gone(site.host)
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(new, gid) == [] and "manifest.bin" in _files(site.host, gid)
    _back(site.host)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == [] and not site.q("SELECT 1 FROM gallery_leftovers")

def test_a_leftover_never_removes_a_gallery_that_is_online_again_there(site):
    sid = site.gallery("Online Again")
    gid = site.gid(sid)
    from plugins.gallery import host_fingerprint
    with site.A.app.app_context():
        fp = host_fingerprint()
    site.q("INSERT INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?, ?, ?)", (gid, fp, date.today().isoformat()))
    site.sync()
    assert "manifest.bin" in _files(site.host, gid) and not site.q("SELECT 1 FROM gallery_leftovers")

def test_an_unreachable_earlier_host_is_tried_once_a_day(site, monkeypatch):
    sid = site.gallery("Picks Over There")
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post("/galleries/move", environ_base=E)
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0]
    P = _plugin(site)
    tries = []
    real = P["open_host"]

    def counted(cfg):
        tries.append(1)
        raise OSError("connect timed out")
    monkeypatch.setitem(P, "open_host", counted)
    site.sync()
    site.sync()
    site.sync()
    assert len(tries) == 1
    (key,), = site.q("SELECT key FROM settings WHERE key LIKE 'gallery_unreachable:%'")
    site.q("UPDATE settings SET value=? WHERE key=?", (str(time.time() - P["UNREACHABLE_RETRY"] - 1), key))
    site.sync()
    assert len(tries) == 2
    monkeypatch.setitem(P, "open_host", real)

def test_a_host_gone_for_good_can_be_forgotten_naming_what_may_still_be_there(site):
    moved = site.gallery("Moved Away")
    deleted = site.gallery("Deleted Meanwhile")
    gid_d = site.gid(deleted)
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post("/galleries/move", environ_base=E)
    _gone(site.host)
    site.c.post(f"/session/{deleted}/delete", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    assert "nothing was removed" in _job("gallery-clear-old")["message"].lower()
    assert str(site.host) not in _job("gallery-clear-old")["message"]
    P = _plugin(site)
    fp = P["fingerprint_of"]({"type": "local", "path": str(site.host)})
    site.c.post("/galleries/forget-host", data={"host": fp}, environ_base=E)
    assert not site.VW.config().get("gallery_hosts_prev")
    assert not site.q("SELECT 1 FROM gallery_leftovers")
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (moved,))[0][0] is None
    assert any("may still be online there" in h and "Moved Away" in h for h in _history(site, moved))
    assert any("Forgot an earlier web host" in h and gid_d in h for h in _history(site))
    assert "Earlier web hosts" not in _move_page(site)

def test_a_gallery_online_again_during_a_delete_comes_down_before_the_records_go(site, monkeypatch):
    sid = site.gallery("Slipped Through")
    gid = site.gid(sid)
    saved = {p.name: p.read_bytes() for p in (site.host / gid).iterdir()}
    real = site.A.app.config["retention_purge_now"]

    def purge(s_):
        (site.host / gid).mkdir(exist_ok=True)
        for n, b in saved.items():
            (site.host / gid / n).write_bytes(b)
        site.q("UPDATE galleries SET upload_state='up', removed_at=NULL WHERE session_id=?", (sid,))
        return real(s_)
    monkeypatch.setitem(site.A.app.config, "retention_purge_now", purge)
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert not site.q("SELECT 1 FROM sessions WHERE id=?", (sid,))
    assert _files(site.host, gid) == []

def test_upload_and_rebuild_are_refused_while_the_session_is_being_deleted(site, monkeypatch):
    sid = site.gallery("Being Deleted")
    import jobs
    monkeypatch.setitem(jobs.JOBS, f"session-delete:{sid}", dict(state="running"))
    site.c.post(f"/session/{sid}/gallery/build", data={"source_dir": ""}, environ_base=E)
    st = site.c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
    assert st["state"] == "error" and "being deleted" in st["error"]
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    time.sleep(0.3)
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"

def test_a_time_zone_change_moves_the_closing_instant_of_galleries_online(site, monkeypatch):
    sid = site.gallery("Night Owl")
    gid = site.gid(sid)
    _expire(site, sid)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert site.wait_up(sid) == "up"
    site.sync()
    P = _plugin(site)
    until = (date.today() + timedelta(days=7)).isoformat()

    def ts(key):
        return dict(l_.split("=", 1) for l_ in host_text(site.host / gid, "limits.txt").split("\n") if "=" in l_)[key]
    assert ts("until") == until and int(ts("until_ts")) == P["day_close"](until)
    old_tz = os.environ.get("TZ")
    zone = "Pacific/Kiritimati" if P["day_close"](until) != _close_in("Pacific/Kiritimati", until) else "America/Adak"
    try:
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        site.sync()
        assert int(ts("until_ts")) == P["day_close"](until) == _close_in(zone, until)
        assert ts("until") == until
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

def _close_in(zone, day):
    from datetime import datetime, time as _t
    from zoneinfo import ZoneInfo
    d = date.fromisoformat(day)
    return int(datetime.combine(d + timedelta(days=1), _t.min, tzinfo=ZoneInfo(zone)).timestamp()) - 1
