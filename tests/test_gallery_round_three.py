import json
import os
import secrets
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import host_bytes, host_has, host_text, host_write
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _files, _job, _new_folder, _setup_host

def _G(site):
    return site.A.app.view_functions["build_gallery"].__globals__

def _back_to(site, folder):

    r = site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                              "gallery_path": str(folder), "gallery_base_url": "https://old.example/g/"},
                    environ_base=E)
    assert r.status_code in (302, 303)
    site.settings(gallery_base_url=site.base)

def _sealed(G, key, at, name, gid):
    from helpers import seal_picks
    return seal_picks(key, gid, [{"i": 0, "name": name, "r": True}], "picks", at)

def test_a_failed_upload_in_place_of_the_proofs_then_a_delete_leaves_nothing_online(site, monkeypatch):

    sid = site.gallery("Proofs Then Download")
    gid = site.gid(sid)
    site.q("UPDATE sessions SET delivery_method='link', retouch_picks='img1.jpg: warmer' WHERE id=?", (sid,))
    site.build(sid)
    G = _G(site)
    assert G["is_download"](gid)
    real, calls = G["delete_gallery"], []

    def flaky(g, host=None):
        calls.append(g)
        if len(calls) == 1:
            raise IOError("the web host did not answer")
        return real(g, host=host)
    monkeypatch.setitem(G, "delete_gallery", flaky)
    site.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
    for _ in range(200):
        if site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "error":
            break
        time.sleep(0.05)
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "error"
    assert (site.host / gid / "index.html").exists()
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}").get("state") in ("done", "error")
    kept = site.q("SELECT id FROM sessions WHERE id=?", (sid,))
    noted = site.q("SELECT 1 FROM gallery_leftovers WHERE gallery_id=?", (gid,))
    assert kept or noted or not _files(site.host, gid)

def test_a_session_deleted_after_going_back_to_the_first_host_leaves_nothing_there(site):

    sid = site.gallery("Round Trip")
    site.gallery("Stays")
    gid = site.gid(sid)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    assert "Moved 2" in _job("gallery-move")["message"]
    _back_to(site, site.host)
    site.sync()
    assert host_has(site.host / gid, "manifest.bin")
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(B, gid) == []
    site.c.post("/galleries/move", environ_base=E)
    site.c.post("/galleries/clear-old", environ_base=E)
    site.sync()
    assert _files(site.host, gid) == [] or site.q("SELECT 1 FROM gallery_leftovers WHERE gallery_id=?", (gid,))

def test_moving_back_to_the_first_host_never_brings_back_older_picks(site):

    sid = site.gallery("Picky")
    gid = site.gid(sid)
    G = _G(site)
    key = site.q("SELECT COALESCE(NULLIF(ekey, ''), key) FROM galleries WHERE session_id=?", (sid,))[0][0]
    host_write(site.host / gid, "picks.las", _sealed(G, key, "2026-11-01T10:00:00Z", "OLD.jpg", gid))
    site.sync()
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    host_write(B / gid, "picks.las", _sealed(G, key, "2026-11-10T10:00:00Z", "NEW.jpg", gid))
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEW.jpg"
    _back_to(site, site.host)
    (B / ".vault-root").unlink()
    site.c.post("/galleries/move", environ_base=E)
    assert "Moved 1" in _job("gallery-move")["message"]
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEW.jpg"

def _hid(site, sid):
    return site.q("SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]

def _remove(site, sid):

    import shutil
    from datetime import date
    gid = site.gid(sid)
    shutil.rmtree(site.host / gid)
    site.q("UPDATE galleries SET upload_state='removed', removed_at=? WHERE session_id=?", (date.today().isoformat(), sid))

def _wait_state(site, sid, want):
    for _ in range(200):
        st = site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))
        if not st or st[0][0] in want:
            break
        time.sleep(0.05)
    return st[0][0] if st else None

def test_a_put_back_upload_that_fails_after_its_files_went_up_then_a_delete(site, monkeypatch):

    sid = site.gallery("Put Back Then Failed")
    gid = site.gid(sid)
    _remove(site, sid)
    G = _G(site)
    real = G["upload_gallery"]

    def fails_after(g, *a, **k):
        real(g, *a, **k)
        raise IOError("the host dropped the connection")
    monkeypatch.setitem(G, "upload_gallery", fails_after)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert _wait_state(site, sid, ("error",)) == "error"
    assert (site.host / gid / "index.html").exists()
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(site.host, gid) == []
    assert not site.q("SELECT 1 FROM sessions WHERE id=?", (sid,))

def test_an_upload_interrupted_by_a_restart_then_a_delete(site):

    sid = site.gallery("Interrupted")
    gid = site.gid(sid)
    site.q("UPDATE galleries SET upload_state='error', uploaded_at=NULL, host_fp=NULL, "
           "upload_error='the upload stopped when the app restarted: press Upload again' WHERE session_id=?", (sid,))
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(site.host, gid) == []

def test_a_failed_upload_gallery_on_a_host_that_cant_be_reached_is_noted_then_removed(site):

    sid = site.gallery("Unreachable")
    gid = site.gid(sid)
    site.q("UPDATE galleries SET upload_state='error', upload_error='the web host did not answer' WHERE session_id=?", (sid,))
    (site.host / ".vault-root").unlink()
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert site.q("SELECT 1 FROM gallery_leftovers WHERE gallery_id=?", (gid,))
    assert (site.host / gid / "index.html").exists()
    (site.host / ".vault-root").write_text("")
    site.sync()
    assert _files(site.host, gid) == []
    assert not site.q("SELECT 1 FROM gallery_leftovers WHERE gallery_id=?", (gid,))

def test_an_upload_that_fails_after_a_take_down_stays_removed_and_leaves_nothing(site, monkeypatch):

    from datetime import date
    sid = site.gallery("Taken Down Meanwhile")
    gid = site.gid(sid)
    _remove(site, sid)
    G = _G(site)
    real = G["upload_gallery"]

    ran = []

    def taken_down_then_fails(g, *a, **k):
        real(g, *a, **k)
        site.q("UPDATE galleries SET upload_state='removed', removed_at=? WHERE session_id=?", (date.today().isoformat(), sid))
        ran.append(1)
        raise IOError("the host dropped the connection")
    monkeypatch.setitem(G, "upload_gallery", taken_down_then_fails)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)

    state = lambda: site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0]
    for _ in range(600):
        if ran and state() != "uploading" and not (site.host / gid).exists():
            break
        time.sleep(0.05)
    assert state() == "removed"
    assert _files(site.host, gid) == []

def test_a_failed_upload_gallery_on_an_earlier_host_is_listed_and_comes_down_with_a_delete(site):

    sid = site.gallery("Failed On A")
    gid = site.gid(sid)
    site.q("UPDATE galleries SET upload_state='error', upload_error='the web host did not answer' WHERE session_id=?", (sid,))
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    page = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert "Failed On A" in page and "its last upload failed" in page
    site.c.post("/galleries/move", environ_base=E)
    assert "Press Upload on those sessions" in _job("gallery-move")["message"]
    assert (site.host / gid / "index.html").exists()
    site.c.post(f"/session/{sid}/delete", environ_base=E)
    assert _job(f"session-delete:{sid}")["state"] == "done"
    assert _files(site.host, gid) == [] and _files(B, gid) == []

def test_moving_back_replaces_the_copy_left_there_and_the_newest_picks_win(site):

    sid = site.gallery("Round Trip Picks")
    gid = site.gid(sid)
    G = _G(site)
    key = site.q("SELECT COALESCE(NULLIF(ekey, ''), key) FROM galleries WHERE session_id=?", (sid,))[0][0]
    host_write(site.host / gid, "picks.las", _sealed(G, key, "2026-11-01T10:00:00Z", "OLD.jpg", gid))
    site.sync()
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    assert "moved=1" in host_text(site.host / gid, "limits.txt")
    host_write(B / gid, "picks.las", _sealed(G, key, "2026-11-10T10:00:00Z", "NEW.jpg", gid))
    _back_to(site, site.host)
    site.c.post("/galleries/move", environ_base=E)
    assert "Moved 1" in _job("gallery-move")["message"]
    assert "moved=1" not in host_text(site.host / gid, "limits.txt")
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEW.jpg"
    assert G["open_picks"](host_bytes(site.host / gid, "picks.las"), key, gid)["retouch"] == ["NEW.jpg"]

def test_a_check_never_writes_older_picks_over_the_sessions(site):

    sid = site.gallery("Older On The Host")
    gid = site.gid(sid)
    G = _G(site)
    key = site.q("SELECT COALESCE(NULLIF(ekey, ''), key) FROM galleries WHERE session_id=?", (sid,))[0][0]
    new = _sealed(G, key, "2026-11-10T10:00:00Z", "NEW.jpg", gid)
    host_write(site.host / gid, "picks.las", new)
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEW.jpg"
    host_write(site.host / gid, "picks.las", _sealed(G, key, "2026-11-01T10:00:00Z", "OLD.jpg", gid))
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEW.jpg"
    assert host_bytes(site.host / gid, "picks.las") == new
    host_write(site.host / gid, "picks.las", _sealed(G, key, "2026-11-12T10:00:00Z", "NEWER.jpg", gid))
    site.sync()
    assert site.q("SELECT retouch_picks FROM sessions WHERE id=?", (sid,))[0][0] == "NEWER.jpg"

def test_no_put_back_online_while_the_session_is_being_deleted(site, monkeypatch):

    import jobs
    sid = site.gallery("Going Away")
    _remove(site, sid)
    url = f"/c/{_hid(site, sid)}?s={sid}"
    assert "Put back online" in site.c.get(url, environ_base=E).get_data(as_text=True)
    key = f"session-delete:{sid}"
    monkeypatch.setitem(jobs.JOBS, key, dict(key=key, label="Deleting a session and everything on it", state="running",
                                             done=0, total=0, message="", started=time.time(), ended=None, link=None))
    r = site.c.get(url, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Going Away" in page
    assert "Put back online" not in page
