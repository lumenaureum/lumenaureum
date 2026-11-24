import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_sec_gal4_155 import FOUND, _copy_as, _forget_all, _move, _sweep, _today

LINK = "g-" + "A1b2C3d4E5f6G7h8I9j0K_-"
OLD = "oldcopy12345"

def _restored_backup(site, sid):

    site.q("DELETE FROM galleries WHERE session_id=?", (sid,))
    site.q("UPDATE sessions SET gallery_id=NULL WHERE id=?", (sid,))

def test_the_link_form_is_the_one_the_app_makes():
    import sealed
    assert len(LINK) == 25 and sealed.LINK_ID.fullmatch(LINK)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))
    import gallery
    g = gallery.new_gid()
    assert gallery.is_link(g) and sealed.LINK_ID.fullmatch(g)

def test_a_link_named_folder_on_a_restored_backup_is_adopt_only(site):
    keep = site.gid(site.gallery("Owner One"))
    sid = site.gallery("Owner Two")
    live = site.gid(sid)
    assert len(live) == 25 and live.startswith("g-")
    _restored_backup(site, sid)
    _sweep(site)
    page = _move(site)
    assert f"folder {live}<" in page
    assert "named like a gallery link from version 1.4 on" in page and "may be a live gallery" in page
    assert "adopt it on the client's page with the link the client received" in page
    assert "Removing a folder by hand on your web host is your choice" in page
    assert "Remove the folder" not in page and "/galleries/strays/remove" not in page
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / live).is_dir() and (site.host / keep).is_dir()
    assert FOUND in _today(site)

def test_mixed_only_the_older_folder_can_be_removed(site):
    gid = site.gid(site.gallery("Owner Three"))
    _copy_as(site, gid, OLD)
    _copy_as(site, gid, LINK)
    _sweep(site)
    page = _move(site)
    assert f"folder {OLD}<" in page and f"folder {LINK}<" in page
    assert "Remove the folder not named like a gallery link" in page
    assert f"Remove 1 gallery folder from the web host ({OLD})" in page
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert not (site.host / OLD).exists()
    assert (site.host / LINK).is_dir() and (site.host / gid).is_dir()
    page = _move(site)
    assert f"folder {LINK}<" in page and "Remove the folder" not in page
    hist = " ".join(str(tuple(r)) for r in site.q("SELECT action, note FROM audit WHERE action LIKE 'Removed%'"))
    assert OLD in hist and LINK in hist and "may be live" in hist

def test_remove_strays_itself_refuses_link_named_folders(site):
    gid = site.gid(site.gallery("Owner Four"))
    _copy_as(site, gid, LINK)
    _sweep(site)
    r = site.c.post("/galleries/strays/remove", environ_base=E)
    assert r.status_code in (302, 303)
    assert (site.host / LINK).is_dir()
    assert f"folder {LINK}<" in _move(site)

def test_fresh_install_link_named_and_older_both_adopt_only(site):
    gid = site.gid(site.gallery("Owner Five"))
    _copy_as(site, gid, OLD)
    _forget_all(site)
    _sweep(site)
    page = _move(site)
    assert "Remove the folder" not in page
    assert "never put a gallery on this web host" in page and "named like a gallery link" in page
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / gid).is_dir() and (site.host / OLD).is_dir()
