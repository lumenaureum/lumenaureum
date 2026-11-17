import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_expiry import E, GID, days_ago, env, go_live
from test_gallery_sunset import settings, stamp, switch_host

def text(page):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))

def _setup_galleries(c, folder, base):
    return c.post("/setup/galleries", data={"gallery_days": "30", "unopened_days": "365", "gallery_type": "local",
                                            "gallery_path": str(folder), "gallery_base_url": base, "gallery_init": "1"},
                  environ_base=E)

def test_the_studio_page_names_are_reserved():
    from plugins.studio_page import reserved_folder
    for name in ("photos", "Photos", "assets", "about", "contact", "inquiries", "index.html"):
        assert reserved_folder(name), name
    for name in ("g", "galleries", "proofs", "client-photos", ""):
        assert not reserved_folder(name), name

def test_setup_refuses_a_gallery_folder_the_studio_page_uses(env, tmp_path):
    A, c, G, host = env
    from vault import web as VW
    before = VW.config()["gallery_host"]
    new = tmp_path / "public_html" / "photos"
    new.mkdir(parents=True)
    r = _setup_galleries(c, new, "https://studio.example/photos/")
    assert "can't be called photos/: the studio page uses that name for its own files" in text(r.get_data(as_text=True))
    assert VW.config()["gallery_host"] == before
    other = tmp_path / "public_html" / "g"
    other.mkdir()
    r = _setup_galleries(c, other, "https://studio.example/g/")
    assert r.status_code in (302, 303) and VW.config()["gallery_host"]["path"] == str(other)

def test_setup_refuses_a_host_folder_the_studio_page_uses(env, tmp_path):
    A, c, G, host = env
    new = tmp_path / "public_html" / "assets"
    new.mkdir(parents=True)
    r = _setup_galleries(c, new, "https://studio.example/g/")
    assert "can't be called assets/: the studio page uses that name for its own files" in text(r.get_data(as_text=True))

def test_setup_keeps_a_gallery_folder_already_in_use(env):

    A, c, G, host = env
    settings(A, gallery_base_url="https://studio.example/photos/")
    r = _setup_galleries(c, host, "https://studio.example/photos/")
    assert r.status_code in (302, 303) and "saved=1" in r.headers.get("Location", "")

def test_move_galleries_refuses_a_folder_the_studio_page_uses(env, tmp_path):
    A, c, G, host = env
    go_live(A, G, days_ago(3))
    stamp(A, G, "https://studio.example/g/")
    new = switch_host(A, tmp_path, base="https://studio.example/photos/")
    page = text(c.get("/galleries/move", environ_base=E).get_data(as_text=True))
    assert "can't be called photos/: the studio page uses that name for its own files" in page and "Put 1 gallery on the new host" not in page
    c.post("/galleries/move", environ_base=E)
    assert not (new / GID).exists()
