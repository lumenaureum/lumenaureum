import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _setup_host
from test_studio_page import env

EARLIER = "still on your earlier web host"

def _cfg(site):
    return site.VW.config()

def _fps(site):
    return {r[0] for r in site.q("SELECT host_fp FROM galleries WHERE upload_state='up'")}

def _today(site):
    return site.c.get("/", environ_base=E).get_data(as_text=True)

def _unlock(site):
    for fn in site.A.UNLOCK_HOOKS:
        fn()

def test_a_marked_folder_gets_one_id_kept_in_its_marker(tmp_path):
    from vault import backends as VB
    d = tmp_path / "g"
    assert VB.LocalBackend.host_id(str(d), make=True) is None
    VB.LocalBackend.init_root(str(d))
    assert VB.LocalBackend.host_id(str(d)) is None
    hid = VB.LocalBackend.host_id(str(d), make=True)
    assert hid and len(hid) == 32
    assert VB.LocalBackend.host_id(str(d), make=True) == hid
    VB.LocalBackend.init_root(str(d))
    assert VB.LocalBackend.host_id(str(d)) == hid
    VB.LocalBackend(str(d))
    other = tmp_path / "o"
    other.mkdir()
    (other / ".vault-root").write_text("something else")
    assert VB.LocalBackend.host_id(str(other), make=True) is None
    assert (other / ".vault-root").read_text() == "something else"
    assert not [p for p in d.iterdir() if p.name.endswith(".part")]

def test_identity_is_the_id_wherever_the_folder_is_and_the_path_without_one():
    from plugins.gallery import fingerprint_of, path_fps
    a = {"type": "local", "path": "/mnt/a/g", "host_id": "1" * 32}
    b = {"type": "local", "path": "/mnt/b/g", "host_id": "1" * 32}
    assert fingerprint_of(a) == fingerprint_of(b) == "1" * 16
    assert fingerprint_of({"type": "local", "path": "/mnt/a/g"}) != fingerprint_of({"type": "local", "path": "/mnt/b/g"})
    assert fingerprint_of(a) != fingerprint_of(dict(a, host_id="2" * 32))
    by_path = fingerprint_of({"type": "local", "path": "/mnt/a/g"})
    assert fingerprint_of({"type": "local", "path": "/mnt/b/g", "host_id": by_path}) == by_path
    assert path_fps(dict(a, was_paths=["/mnt/old/g"])) == [fingerprint_of({"type": "local", "path": p})
                                                           for p in ("/mnt/a/g", "/mnt/old/g")]
    assert path_fps({"type": "local", "path": "/mnt/a/g"}) == []
    assert fingerprint_of({"type": "local", "path": "/mnt/a/g", "host_id": "not an id"}) == by_path

def test_the_studio_page_folder_is_the_one_above_and_never_the_galleries_own():
    from plugins.gallery import fingerprint_of
    from plugins.studio_page import site_target
    g = {"type": "local", "path": "/mnt/a/www/g", "host_id": "1" * 32, "was_paths": ["/mnt/old/www/g"]}
    site_cfg, _, folder = site_target(g, "https://example.com/g/")
    assert folder == "g" and site_cfg["path"] == "/mnt/a/www" and "host_id" not in site_cfg and "was_paths" not in site_cfg
    assert site_cfg["parent_of"] == "1" * 32
    assert fingerprint_of(site_cfg) == fingerprint_of({"type": "local", "path": "/mnt/a/www"}) != fingerprint_of(g)

def test_the_first_unlock_gives_the_folder_its_id_and_galleries_stay_where_they_are(site):
    from plugins.gallery import fingerprint_of
    sid = site.gallery("Before The Update")
    path_fp = fingerprint_of({"type": "local", "path": str(site.host)})
    assert _fps(site) == {path_fp} and "host_id" not in _cfg(site)["gallery_host"]
    _unlock(site)
    gh = _cfg(site)["gallery_host"]
    assert gh["host_id"] and (site.host / ".vault-root").read_text().strip() == gh["host_id"]
    assert gh["host_id"] == fingerprint_of(gh) == path_fp
    assert _fps(site) == {path_fp}
    assert EARLIER not in _today(site) and not _cfg(site).get("gallery_hosts_prev")
    _unlock(site)
    assert _cfg(site)["gallery_host"] == gh and _fps(site) == {fingerprint_of(gh)}
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0] is None

def test_the_same_folder_at_a_new_mount_path_is_the_same_host(site):
    sid = site.gallery("Mount Moved")
    _unlock(site)
    hid = _cfg(site)["gallery_host"]["host_id"]
    moved = site.tmp / "mnt2"
    os.rename(site.host, moved)
    os.symlink(moved, site.host)
    _setup_host(site, moved)
    cfg = _cfg(site)
    assert cfg["gallery_host"]["path"] == str(moved) and cfg["gallery_host"]["host_id"] == hid
    assert not cfg.get("gallery_hosts_prev")
    assert EARLIER not in _today(site)
    page = site.c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    assert "This gallery is still on your earlier web host" not in page

def test_a_folder_that_moved_before_the_update_is_recognized_in_setup(site):
    from plugins.gallery import fingerprint_of
    sid = site.gallery("Restored Elsewhere")
    gid = site.gid(sid)
    moved = site.tmp / "restored"
    os.rename(site.host, moved)
    assert (moved / gid).is_dir()
    _setup_host(site, moved)
    cfg = _cfg(site)
    assert not cfg.get("gallery_hosts_prev") and str(site.host) in cfg["gallery_host"]["was_paths"]
    assert _fps(site) == {fingerprint_of(cfg["gallery_host"])}
    assert EARLIER not in _today(site)

def test_a_folder_that_moved_and_was_set_up_before_the_update_is_recognized_at_unlock(site):
    from plugins.gallery import fingerprint_of
    site.gallery("Set Up Before")
    moved = site.tmp / "restored"
    os.rename(site.host, moved)
    cfg = _cfg(site)
    cfg["gallery_hosts_prev"] = [cfg["gallery_host"]]
    cfg["gallery_host"] = {"type": "local", "path": str(moved)}
    site.VW.write_config(cfg)
    assert EARLIER in _today(site)
    _unlock(site)
    cfg = _cfg(site)
    assert not cfg.get("gallery_hosts_prev") and cfg["gallery_host"]["host_id"]
    assert _fps(site) == {fingerprint_of(cfg["gallery_host"])}
    assert EARLIER not in _today(site)

def test_another_folder_is_still_another_host(site):

    site.gallery("Elsewhere")
    gone = site.tmp / "gone"
    os.rename(site.host, gone)
    new = site.tmp / "empty"
    new.mkdir()
    _setup_host(site, new)
    assert len(_cfg(site)["gallery_hosts_prev"]) == 1 and EARLIER in _today(site)
    os.rename(gone, site.host)
    third = site.tmp / "third"
    third.mkdir()
    _setup_host(site, third)
    assert len(_cfg(site)["gallery_hosts_prev"]) == 2

def test_the_studio_pages_files_carry_over_when_the_folder_moves(env):

    from test_studio_page import setting
    from test_studio_site_r3 import add_and_upload, q
    from test_studio_site_release import upload
    A, c, S, tmp, web = env
    from plugins import gallery as G
    from vault import web as VW
    cfg = VW.config()
    assert G.name_local_host(cfg, cfg["gallery_host"])
    VW.write_config(cfg)
    names = add_and_upload(A, c, S, "IMG_0001.jpg")
    moved = tmp / "web-moved"
    web.rename(moved)
    cfg = VW.config()
    new = {"type": "local", "path": str(moved / "g")}
    G.name_local_host(cfg, new)
    assert new["host_id"] == cfg["gallery_host"]["host_id"]
    G.setup_host(cfg, new)
    VW.write_config(cfg)
    assert not cfg.get("gallery_hosts_prev")
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    fps = {f for (f,) in q(A, "SELECT DISTINCT host_fp FROM studio_files")}
    assert fps == {S.host_fingerprint(S.site_target(new, "https://www.example.com/g/")[0])}
    assert all((moved / n).is_file() for n in names)
    assert "earlier web host" not in c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)

def test_setup_warns_when_the_folder_is_a_copy_of_the_gallery_folder(site):
    import shutil
    site.gallery("Copied")
    _unlock(site)
    copy = site.tmp / "copy"
    shutil.copytree(site.host, copy)
    r = site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                              "gallery_path": str(copy), "gallery_base_url": "https://new.example/g/",
                                              "gallery_init": "1"}, environ_base=E)
    page = site.c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)
    assert f"This folder looks like a copy of your gallery folder at {os.path.realpath(site.host)}" in page
    moved = site.tmp / "moved"
    os.rename(copy, moved)
    shutil.rmtree(site.host)
    r = site.c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                              "gallery_path": str(moved), "gallery_base_url": "https://new.example/g/",
                                              "gallery_init": "1"}, environ_base=E)
    assert "looks like a copy" not in site.c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)

def _old_copy(site, gid, name="oldid1234abcd"):

    import shutil
    shutil.copytree(site.host / gid, site.host / name)
    page = site.host / name / "index.html"
    page.write_text(page.read_text().replace(f'data-gid="{gid}"', f'data-gid="{name}"', 1))

def _after_unlock(site):
    site.q("DELETE FROM settings WHERE key='gallery_strays_swept'")
    for fn in site.A.AFTER_UNLOCK_HOOKS:
        fn()

def test_old_folders_no_gallery_owns_are_offered_once_and_removed_only_when_asked(site):
    import shutil
    sid = site.gallery("Still Here")
    gid = site.gid(sid)
    _old_copy(site, gid)
    (site.host / "notagallery").mkdir()
    (site.host / "notagallery" / "readme.txt").write_text("someone else's")
    _after_unlock(site)
    assert (site.host / "oldid1234abcd").is_dir()
    assert "1 gallery folder on your web host is not known to this studio" in _today(site)
    page = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert "folder oldid1234abcd" in page and "notagallery" not in page and f"folder {gid}<" not in page
    for fn in site.A.AFTER_UNLOCK_HOOKS:
        fn()
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert not (site.host / "oldid1234abcd").exists()
    assert (site.host / gid).is_dir() and (site.host / "notagallery" / "readme.txt").is_file()
    assert "not known to this studio" not in _today(site)

def test_old_folders_can_be_kept(site):
    import shutil
    gid = site.gid(site.gallery("Kept"))
    _old_copy(site, gid)
    _after_unlock(site)
    site.c.post("/galleries/strays/keep", environ_base=E)
    assert (site.host / "oldid1234abcd").is_dir() and "not known to this studio" not in _today(site)
    for fn in site.A.AFTER_UNLOCK_HOOKS:
        fn()
    assert "not known to this studio" not in _today(site)

@pytest.mark.parametrize("kind", ["sftp", "s3"])
def test_the_sweep_lists_any_kind_of_web_host_and_a_failure_offers_nothing(site, monkeypatch, kind):

    import shutil
    from vault import backends as VB
    gid = site.gid(site.gallery("Remote"))
    _old_copy(site, gid)
    real = VB.from_config
    down = {"on": True}

    class Remote(VB.LocalBackend):
        def list(self, prefix=""):
            if down["on"]:
                raise OSError("Connection timed out")
            return super().list(prefix)

    Remote.kind = kind

    def from_config(cfg):
        return Remote(str(site.host), public=True) if cfg.get("type") == kind else real(cfg)
    monkeypatch.setattr(VB, "from_config", from_config)
    cfg = _cfg(site)
    cfg["gallery_host"] = ({"type": "sftp", "host": "sftp.example.com", "user": "pat", "root": "/www/g"} if kind == "sftp"
                           else {"type": "s3", "bucket": "pics", "access_key": "a", "secret_key": "b", "prefix": "g"})
    site.VW.write_config(cfg)
    from plugins.gallery import fingerprint_of
    site.q("UPDATE galleries SET host_fp=?", (fingerprint_of(cfg["gallery_host"]),))
    _after_unlock(site)
    assert "not known to this studio" not in _today(site)
    assert not site.q("SELECT 1 FROM settings WHERE key='gallery_strays_swept'")
    down["on"] = False
    for fn in site.A.AFTER_UNLOCK_HOOKS:
        fn()
    assert "1 gallery folder on your web host is not known to this studio" in _today(site)
    down["on"] = True
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert (site.host / "oldid1234abcd").is_dir()
    down["on"] = False
    site.c.post("/galleries/strays/remove", environ_base=E)
    assert not (site.host / "oldid1234abcd").exists() and (site.host / gid).is_dir()
    assert "not known to this studio" not in _today(site)
