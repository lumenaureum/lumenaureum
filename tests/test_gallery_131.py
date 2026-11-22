import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, _expire, site
from test_gallery_round_three import _back_to, _remove, _wait_state
from test_gallery_round_two import _files, _job, _new_folder, _setup_host

def _G(site):
    return site.A.app.view_functions["build_gallery"].__globals__

def _move_page(site):
    return site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)

def _fp(site):
    with site.A.app.app_context():
        return _G(site)["host_fingerprint"]()

def test_expiring_on_b_after_going_back_to_a_leaves_only_the_dark_page_on_a(site):

    sid = site.gallery("There And Back")
    gid = site.gid(sid)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    assert "manifest.bin" in _files(site.host, gid)
    _back_to(site, site.host)
    _expire(site, sid)
    assert _files(site.host, gid) == ["index.html"]
    assert "gone dark" in (site.host / gid / "index.html").read_text()
    assert _files(B, gid) == []
    assert tuple(site.q("SELECT moved_from, dark_at IS NOT NULL FROM galleries WHERE session_id=?", (sid,))[0]) == (None, 1)

def test_expiring_on_b_takes_the_old_copy_on_a_down_and_a_move_back_leaves_only_the_dark_page(site):
    sid = site.gallery("Expires On B")
    gid = site.gid(sid)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    _expire(site, sid)
    assert _files(site.host, gid) == []
    assert _files(B, gid) == ["index.html"]
    _back_to(site, site.host)
    site.c.post("/galleries/move", environ_base=E)
    assert _files(site.host, gid) == ["index.html"]

def test_an_old_copy_that_cant_be_reached_at_expiry_is_listed_and_comes_down_later(site):
    sid = site.gallery("Old Copy Away")
    gid = site.gid(sid)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    site.c.post("/galleries/move", environ_base=E)
    (site.host / ".vault-root").rename(site.host / ".vault-root-off")
    _expire(site, sid)
    assert "manifest.bin" in _files(site.host, gid)
    assert [tuple(r) for r in site.q("SELECT gallery_id FROM gallery_leftovers")] == [(gid,)]
    assert f"Old Copy Away (an old copy, folder {gid})" in _move_page(site)
    assert "1 old gallery folder still has to come down" in site.c.get("/", environ_base=E).get_data(as_text=True)
    (site.host / ".vault-root-off").rename(site.host / ".vault-root")
    site.q("DELETE FROM settings WHERE key LIKE 'gallery_unreachable:%'")
    site.sync()
    assert _files(site.host, gid) == [] and not site.q("SELECT 1 FROM gallery_leftovers")
    assert _files(B, gid) == ["index.html"]

def _held(site, monkeypatch):
    waiting = []
    site.A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: waiting.append(proceed) or True)
    return waiting

def test_a_held_upload_approved_after_a_take_down_stays_removed(site, monkeypatch):

    from datetime import date
    sid = site.gallery("Held Then Down")
    gid = site.gid(sid)
    _remove(site, sid)
    waiting = _held(site, monkeypatch)
    try:
        site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    finally:
        site.A.hooks.guards["gallery_upload"].pop()
    assert len(waiting) == 1
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "awaiting"
    with site.A.app.app_context():
        site.A.app.config["gallery_take_down"](sid)
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"
    assert not site.q("SELECT 1 FROM galleries WHERE session_id=? AND removed_at IS NULL", (sid,))
    G = _G(site)

    def fails(g, *a, **k):
        raise IOError("the host dropped the connection")
    monkeypatch.setitem(G, "upload_gallery", fails)
    waiting[0]()
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"
    monkeypatch.undo()
    waiting[0]()
    assert tuple(site.q("SELECT upload_state, removed_at FROM galleries WHERE session_id=?", (sid,))[0]) == \
        ("removed", date.today().isoformat())
    assert _files(site.host, gid) == []

def test_a_held_download_gallery_in_place_of_proofs_never_goes_up_after_a_take_down(site, monkeypatch):
    sid = site.gallery("Proofs Held")
    gid = site.gid(sid)
    site.q("UPDATE sessions SET delivery_method='link', retouch_picks='img1.jpg: warmer' WHERE id=?", (sid,))
    site.build(sid)
    waiting = _held(site, monkeypatch)
    try:
        site.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
    finally:
        site.A.hooks.guards["gallery_upload"].pop()
    assert len(waiting) == 1
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "up"
    with site.A.app.app_context():
        site.A.app.config["gallery_take_down"](sid)
    waiting[0]()
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"
    assert _files(site.host, gid) == []

def test_a_first_upload_that_failed_partway_is_listed_on_the_earlier_host_and_blocks_its_clearing(site, monkeypatch):
    sid = site.gallery("Half Up")
    gid = site.gid(sid)
    shutil.rmtree(site.host / gid)
    site.q("""UPDATE galleries SET upload_state='', uploaded_at=NULL, host_fp=NULL, link_base=NULL WHERE session_id=?""", (sid,))
    G = _G(site)
    real = G["upload_gallery"]

    def partway(g, *a, **k):
        real(g, *a, **k)
        raise IOError("the host dropped the connection")
    monkeypatch.setitem(G, "upload_gallery", partway)
    site.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
    assert _wait_state(site, sid, ("error",)) == "error"
    monkeypatch.undo()
    A_fp = _fp(site)
    assert tuple(site.q("SELECT host_fp, uploaded_at FROM galleries WHERE session_id=?", (sid,))[0]) == (A_fp, None)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    page = _move_page(site)
    assert "its upload failed partway: press Upload on its session" in page
    assert f"Half Up (its upload failed partway; parts of it may be there, folder {gid})" in page
    site.c.post("/galleries/clear-old", environ_base=E)
    assert "manifest.bin" in _files(site.host, gid)
    site.c.post(f"/session/{sid}/gallery/upload", data={"early": "1"}, environ_base=E)
    assert _wait_state(site, sid, ("up",)) == "up"
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0] == A_fp
    site.c.post("/galleries/clear-old", environ_base=E)
    assert _files(site.host, gid) == [] and "manifest.bin" in _files(B, gid)

def test_a_put_back_that_failed_partway_on_another_host_is_noted_and_comes_down(site, monkeypatch):

    sid = site.gallery("Back On B")
    gid = site.gid(sid)
    _remove(site, sid)
    B = _new_folder(site, "hostB")
    _setup_host(site, B)
    G = _G(site)
    real = G["upload_gallery"]

    def partway(g, *a, **k):
        real(g, *a, **k)
        raise IOError("the host dropped the connection")
    monkeypatch.setitem(G, "upload_gallery", partway)
    site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert _wait_state(site, sid, ("error",)) == "error"
    monkeypatch.undo()
    assert "manifest.bin" in _files(B, gid)
    B_fp = _fp(site)
    assert [tuple(r) for r in site.q("SELECT gallery_id, host_fp FROM gallery_leftovers")] == [(gid, B_fp)]
    site.sync()
    assert _files(B, gid) == [] and not site.q("SELECT 1 FROM gallery_leftovers")
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "error"

def test_the_unreachable_host_memory_is_in_the_database(site):

    site.gallery("Moved Away")
    _setup_host(site, _new_folder(site, "hostB"))
    site.c.post("/galleries/move", environ_base=E)
    A_fp = site.q("SELECT moved_from FROM galleries")[0][0]
    (site.host / ".vault-root").rename(site.host / ".vault-root-off")
    site.sync()
    P = _G(site)
    rows = [tuple(r) for r in site.q("SELECT key FROM settings WHERE key LIKE 'gallery_unreachable:%'")]
    assert rows == [(f"gallery_unreachable:{A_fp}",)]
    assert P["_UNREACHABLE"] == {}
    with site.A.app.app_context():
        assert not P["may_try"](A_fp)
    assert "couldn't be reached at the last try" in _move_page(site)
    (site.host / ".vault-root-off").rename(site.host / ".vault-root")
    site.q("UPDATE settings SET value=? WHERE key=?", (str(time.time() - P["UNREACHABLE_RETRY"] - 1), rows[0][0]))
    site.sync()
    assert not site.q("SELECT 1 FROM settings WHERE key LIKE 'gallery_unreachable:%'")
    assert _job("gallery-move")["state"] == "done"
