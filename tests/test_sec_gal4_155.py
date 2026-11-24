import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site

FOUND = "not known to this studio"

def _sweep(site):
    site.q("DELETE FROM settings WHERE key='gallery_strays_swept'")
    for fn in site.A.AFTER_UNLOCK_HOOKS:
        fn()

def _today(site):
    return site.c.get("/", environ_base=E).get_data(as_text=True)

def _move(site):
    return site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)

def _copy_as(site, gid, name):

    shutil.copytree(site.host / gid, site.host / name)
    page = site.host / name / "index.html"
    page.write_text(page.read_text().replace(f'data-gid="{gid}"', f'data-gid="{name}"', 1))

def _forget_all(site):

    site.q("DELETE FROM gallery_leftovers")
    site.q("DELETE FROM galleries")
    site.q("UPDATE sessions SET gallery_id=NULL")

def test_only_a_gallery_page_made_for_the_folder_with_a_manifest_counts(site):
    gid = site.gid(site.gallery("Owner One"))
    names = sorted(p.name for p in (site.host / gid).iterdir())
    assert "manifest.bin" not in names
    _copy_as(site, gid, "oldcopy12345")
    shutil.copytree(site.host / gid, site.host / "plaincopy123")
    web = site.host / "website01"
    web.mkdir()
    (web / "index.html").write_text('<!doctype html><html lang="en"><body>My site</body></html>')
    (web / "data.bin").write_bytes(b"\0" * 10)
    (web / "limits.php").write_text("<?php exit; ?>\n")
    nopage = site.host / "nopage1234"
    nopage.mkdir()
    (nopage / ("a" * 24 + ".bin")).write_bytes(b"\0" * 10)
    (nopage / "manifest.bin").write_bytes(b"\0" * 10)
    nomani = site.host / "nomanifest1"
    nomani.mkdir()
    (nomani / "index.html").write_text('<!doctype html><html lang="en" data-gid="nomanifest1"><body></body></html>')
    _sweep(site)
    page = _move(site)
    assert "folder oldcopy12345" in page
    for n in ("plaincopy123", "website01", "nopage1234", "nomanifest1", gid):
        assert f"folder {n}<" not in page
    assert "1 gallery folder on your web host is not known to this studio" in _today(site)

def test_a_plain_manifest_from_before_the_1_4_layout_counts(site):
    gid = site.gid(site.gallery("Owner Two"))
    old = site.host / "pre14gallery"
    old.mkdir()
    (old / "index.html").write_text('<!doctype html>\n<html lang="en" data-gid="pre14gallery"><body></body></html>')
    (old / "manifest.bin").write_bytes(b"\0" * 40)
    _sweep(site)
    assert "folder pre14gallery" in _move(site) and (site.host / gid).is_dir()

def test_a_studio_that_has_used_the_host_sees_adopt_before_remove_and_plain_words(site):
    gid = site.gid(site.gallery("Owner Three"))
    _copy_as(site, gid, "oldcopy12345")
    _sweep(site)
    page = _move(site)
    assert "Gallery folders not known to this studio (1)" in page
    assert "may be a live gallery from another install or an older backup" in page
    assert "left by an earlier move" not in page and "old gallery folder" not in page
    assert page.index("Adopt a gallery already online") < page.index("Remove the folder")
    assert "its clients lose it" in page
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert not (site.host / "oldcopy12345").exists() and (site.host / gid).is_dir()
    assert FOUND not in _today(site)

def test_a_fresh_or_restored_database_is_never_offered_removal_of_live_galleries(site):
    gid = site.gid(site.gallery("Owner Four"))
    _copy_as(site, gid, "oldcopy12345")
    _forget_all(site)
    _sweep(site)
    assert "2 gallery folders on your web host are not known to this studio" in _today(site)
    page = _move(site)
    assert f"folder {gid}<" in page and "folder oldcopy12345" in page
    assert "Adopt a gallery already online" in page and "Keep the folders" in page
    assert "Remove the folder" not in page and "galleries_strays_remove" not in page
    assert "/galleries/strays/remove" not in page
    assert "never put a gallery on this web host" in page
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / gid).is_dir() and (site.host / "oldcopy12345").is_dir()
    assert FOUND in _today(site)

def test_a_gallery_put_on_the_host_later_does_not_unlock_removal(site):
    gid = site.gid(site.gallery("Owner Five"))
    _forget_all(site)
    _sweep(site)
    site.gallery("Owner Six")
    assert "Remove the folder" not in _move(site)
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / gid).is_dir()

def test_keep_clears_the_offer_in_either_case(site):
    gid = site.gid(site.gallery("Owner Seven"))
    _forget_all(site)
    _sweep(site)
    site.c.post("/galleries/strays/keep", environ_base=E)
    assert (site.host / gid).is_dir() and FOUND not in _today(site)
