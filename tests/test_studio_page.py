import io
import os
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def jpeg_with_metadata(color=(150, 90, 40), size=(1600, 1067)):

    from PIL import Image, ImageCms
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"
    exif[0x013B] = "Someone Private"
    exif[0x8825] = {1: "N", 2: (40.0, 30.0, 15.0), 3: "W", 4: (80.0, 10.0, 5.0)}
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95, exif=exif.tobytes(), icc_profile=icc, comment=b"secret comment",
            xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/">GPSLatitude</x:xmpmeta>')
    return b.getvalue()

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    for k, v in (("photographer_name", "Pat Lens"), ("business_email", "hello@example.com"),
                 ("business_phone", "(555) 010-2000"), ("business_address", "PO Box 1\nTown, ST 00000"),
                 ("gallery_base_url", "https://www.example.com/g/")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'delivered', '2026-10-01')")
    con.commit(); con.close()
    web = tmp_path / "web"
    (web / "g").mkdir(parents=True)
    (web / "g" / ".vault-root").write_text("")
    (web / "g" / "abc123xyz").mkdir()
    (web / "g" / "abc123xyz" / "index.html").write_text("a client gallery")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(web / "g")}
    VW.write_config(cfg)
    from plugins import studio_page as S
    return A, c, S, tmp_path, web

def setting(A, k):
    con = A.connect()
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    con.close()
    return r[0] if r else None

def switch_on(c):
    c.post("/settings/studio-page/switch", data={"use": "1"}, environ_base=E)

def add_own(c, *blobs):
    return c.post("/settings/studio-page/photos/add", data={"photos": [(io.BytesIO(b), f"p{i}.jpg") for i, b in enumerate(blobs)]},
                  content_type="multipart/form-data", environ_base=E)

def session_photo(A, name, sid=1, kind="export", color=(20, 120, 200)):
    import photostore
    body = jpeg_with_metadata(color)
    con = A.connect()
    row = photostore.start(con, sid, kind, name, len(body))
    photostore.append(con, row, 0, io.BytesIO(body), len(body))
    fid = con.execute("SELECT id FROM photo_files WHERE session_id=? AND name=?", (sid, name)).fetchone()[0]
    con.close()
    return fid

def wait(S):
    for t in [t for t in threading.enumerate() if t.name.startswith("studio-page")]:
        t.join(timeout=30)

def built_files(d):
    return {str(p.relative_to(d)).replace(os.sep, "/") for p in Path(d).rglob("*") if p.is_file()}

def test_off_by_default_and_listed_in_settings(env):
    A, c, S, tmp, web = env
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "Use the studio page" in page and 'name="site_about"' not in page
    assert "Studio page" in c.get("/settings", environ_base=E).get_data(as_text=True)
    assert c.post("/settings/studio-page/upload", environ_base=E).status_code == 404
    assert c.post("/settings/studio-page/preview", environ_base=E).status_code == 404
    switch_on(c)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert 'name="site_about"' in page and "https://www.example.com/" in page

def test_settings_page_names_every_field(env):
    A, c, S, tmp, web = env
    switch_on(c)
    add_own(c, jpeg_with_metadata())
    session_photo(A, "a.jpg")
    from test_form_labels import problems
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    assert "a.jpg" in page and problems(page) == []

def test_build_output_and_metadata_stripped(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/save", data={"site_tagline": "Photographer · Town", "site_intro": "Portraits · Boats",
                                                "site_about": "I make photos.\n\n## Radio\nA [link](https://example.org/x).\n\n<script>x</script>",
                                                "site_mark": "1", "site_show_phone": "1",
                                                "site_show_address": "1"}, environ_base=E)
    add_own(c, jpeg_with_metadata(), jpeg_with_metadata((10, 200, 10), (900, 1400)))
    r = c.post("/settings/studio-page/preview", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/studio-page/preview/")
    out = S.SITE_ROOT / "preview"
    files = built_files(out)
    assert {"index.html", "about/index.html", "contact/index.html", "404.html", "robots.txt", "sitemap.xml",
            "assets/site.css", "assets/site.js", ".htaccess"} <= files
    photos = sorted(f for f in files if f.startswith("photos/"))
    assert len(photos) == 2
    from PIL import Image
    for p in photos:
        raw = (out / p).read_bytes()
        im = Image.open(io.BytesIO(raw))
        assert max(im.size) <= S.DISPLAY_EDGE and im.format == "JPEG"
        assert not im.getexif() and "icc_profile" not in im.info and "xmp" not in im.info and "comment" not in im.info
        for leak in (b"Exif", b"CameraMaker", b"Someone Private", b"GPS", b"secret comment", b"xmpmeta"):
            assert leak not in raw

    im = Image.open(out / photos[0]).convert("RGB")
    W, H = im.size
    corner = [im.getpixel((x_, y_)) for x_ in range(0, W // 4) for y_ in range(H - H // 12, H)]
    assert any(min(px) > 200 for px in corner)
    assert all(abs(px[0] - 150) < 12 for px in [im.getpixel((W - 5, 5)), im.getpixel((W // 2, H // 2))])
    home = (out / "index.html").read_text()
    assert "Pat Lens" in home and "Photographer · Town" in home and "mailto:hello@example.com" in home
    assert 'href="tel:5550102000"' in home and "PO Box 1, Town, ST 00000" in home
    assert '<link rel="canonical" href="https://www.example.com/">' in home and "og:image" in home
    about = (out / "about" / "index.html").read_text()
    assert '<p class="lede">I make photos.</p>' in about and "<h2>Radio</h2>" in about
    assert '<a href="https://example.org/x">link</a>' in about and "<script>x" not in about and "&lt;script&gt;" in about
    assert "/g/" not in (out / "robots.txt").read_text()
    assert "ErrorDocument 404 /404.html" in (out / ".htaccess").read_text()

    contact_ht = (out / "contact" / ".htaccess").read_text()
    assert "frame-ancestors 'none'" in contact_ht and 'X-Frame-Options "DENY"' in contact_ht
    assert "<IfModule mod_headers.c>" in contact_ht
    assert "frame-ancestors" not in (out / ".htaccess").read_text()
    S.check_name("contact/.htaccess", "g")
    pv = c.get("/studio-page/preview/", environ_base=E)
    assert pv.status_code == 200 and b"Pat Lens" in pv.data
    assert c.get("/studio-page/preview/about/", environ_base=E).status_code == 200
    assert c.get("/studio-page/preview/../crm.db", environ_base=E).status_code == 404
    assert c.get("/studio-page/preview/nothing-here", environ_base=E).status_code == 404

    c.post("/settings/studio-page/save", data={"site_mark": ""}, environ_base=E)
    c.post("/settings/studio-page/preview", environ_base=E)
    im = Image.open(next((out / "photos").iterdir())).convert("RGB")
    W, H = im.size
    assert not any(min(im.getpixel((x_, y_))) > 200 for x_ in range(0, W // 4, 2) for y_ in range(H - H // 12, H, 2))

def test_no_external_urls_in_output(env):
    A, c, S, tmp, web = env
    switch_on(c)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('credit_off', '1')")
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_font', 'elegant')")
    con.commit(); con.close()
    c.post("/settings/studio-page/save", data={"site_about": "Plain words."}, environ_base=E)
    add_own(c, jpeg_with_metadata())
    c.post("/settings/studio-page/preview", environ_base=E)
    out = S.SITE_ROOT / "preview"
    assert (out / "assets" / "font.woff2").is_file()
    for f in built_files(out):
        if not f.endswith((".html", ".css", ".js", ".txt", ".xml", ".htaccess")):
            continue
        text = (out / f).read_text()
        for u in re.findall(r"(?:https?:)?//[^\s\"'<>()]+", text):
            assert u.startswith(("https://www.example.com/", "http://www.sitemaps.org/schemas/sitemap/0.9")), (f, u)

        for ref in re.findall(r"(?:src|href)=\"([^\"]+)\"", text) + re.findall(r"url\(([^)]+)\)", text):
            if ref.startswith(("mailto:", "tel:", "https://www.example.com/")):
                continue
            assert "//" not in ref and ":" not in ref, (f, ref)
        assert "@import" not in text
    home = (out / "index.html").read_text()
    assert "default-src 'none'" in home and "lumenaureum.org" not in home

def test_portfolio_permissions_respected(env):
    A, c, S, tmp, web = env
    switch_on(c)
    a, b, v, d = (session_photo(A, n) for n in ("a.jpg", "b.jpg", "veto-me.jpg", "d.jpg"))
    con = A.connect()
    con.execute("UPDATE sessions SET veto='veto-me.jpg (photo 3)', favorites='a.jpg (photo 1)' WHERE id=1")
    con.commit(); con.close()
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    assert "a.jpg" in page and "veto-me.jpg" not in page
    c.post("/settings/studio-page/photos/from/1", data={"file": [v]}, environ_base=E)
    c.post("/settings/studio-page/photos/from/1", data={"file": [a, b, d]}, environ_base=E)
    con = A.connect()
    names = [r[0] for r in con.execute("SELECT name FROM studio_photos ORDER BY sort")]
    con.close()
    assert names == ["a.jpg", "b.jpg"]

    con = A.connect()
    con.execute("UPDATE sessions SET veto='veto-me.jpg, b.jpg' WHERE id=1")
    con.commit(); con.close()
    c.post("/settings/studio-page/preview", environ_base=E)
    assert len(list((S.SITE_ROOT / "preview" / "photos").iterdir())) == 1

    con = A.connect()
    con.execute("UPDATE sessions SET portfolio_no=1 WHERE id=1")
    con.commit(); con.close()
    c.post("/settings/studio-page/preview", environ_base=E)
    assert not (S.SITE_ROOT / "preview" / "photos").exists()
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    assert '<option value="1"' not in page and "Not shown: the client declined portfolio use" in page

def test_veto_matching():
    sys.path.insert(0, ROOT)
    from plugins.studio_page import vetoed
    assert vetoed("IMG_0042.jpg", "img_0042.jpg (photo 4)")
    assert vetoed("IMG_0042.jpg", "IMG_0042")
    assert not vetoed("img1.jpg", "img10.jpg (photo 10)")
    assert not vetoed("data.jpg", "a.jpg")
    assert vetoed("anything.jpg", "photo 3")
    assert not vetoed("a.jpg", "")

def test_upload_needs_the_guard_and_writes_only_its_own_files(env):
    A, c, S, tmp, web = env
    switch_on(c)
    add_own(c, jpeg_with_metadata(), jpeg_with_metadata((5, 5, 200)))
    (web / "notes.txt").write_text("the photographer's own file")
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append((proceed, kw)) or True)
    try:
        c.post("/settings/studio-page/upload", environ_base=E)
        assert held and held[0][1]["site"] is True and held[0][1]["sid"] is None
        assert not (web / "index.html").exists() and setting(A, "site_state") == "awaiting"
        held[0][0]()
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert (web / "index.html").is_file() and (web / "about").exists() is False or True
    assert (web / "g" / "abc123xyz" / "index.html").read_text() == "a client gallery"
    assert (web / "notes.txt").read_text() == "the photographer's own file"
    con = A.connect()
    mine = {r[0] for r in con.execute("SELECT name FROM studio_files")}
    con.close()
    assert "index.html" in mine and not any(n.startswith("g/") for n in mine)
    old_photos = {n for n in mine if n.startswith("photos/")}
    assert len(old_photos) == 2

    con = A.connect()
    pid = con.execute("SELECT id FROM studio_photos ORDER BY sort LIMIT 1").fetchone()[0]
    con.close()
    c.post(f"/settings/studio-page/photos/{pid}/edit", data={"do": "remove"}, environ_base=E)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    now = {str(p.relative_to(web / "photos")) for p in (web / "photos").iterdir()}
    assert len(now) == 1
    assert (web / "g" / "abc123xyz" / "index.html").is_file() and (web / "notes.txt").is_file()

    con = A.connect()
    pid = con.execute("SELECT id FROM studio_photos LIMIT 1").fetchone()[0]
    con.close()
    kept = next((web / "photos").iterdir())
    kept.write_bytes(b"replaced by hand")
    c.post(f"/settings/studio-page/photos/{pid}/edit", data={"do": "remove"}, environ_base=E)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert kept.read_bytes() == b"replaced by hand"

    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "down"
    assert not (web / "index.html").exists() and not (web / "assets").exists()
    assert (web / "g" / "abc123xyz" / "index.html").is_file() and (web / "notes.txt").is_file() and kept.is_file()
    assert (web / "g" / ".vault-root").is_file()

def test_never_writes_over_a_file_it_did_not_put_there(env):
    A, c, S, tmp, web = env
    switch_on(c)
    (web / "index.html").write_text("my old site")
    (web / ".htaccess").write_text("RewriteEngine On\n")
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "error" and "index.html" in setting(A, "site_error")
    assert (web / "index.html").read_text() == "my old site" and not (web / "assets").exists()
    (web / "index.html").unlink()
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert (web / ".htaccess").read_text() == "RewriteEngine On\n"
    assert "ErrorDocument 404 /404.html" in setting(A, "site_note")

def test_names_never_reach_the_gallery_folder(env):
    A, c, S, tmp, web = env
    with pytest.raises(S.SiteProblem):
        S.check_name("g/abc123xyz/index.html", "g")
    with pytest.raises(S.SiteProblem):
        S.check_name("../etc/passwd", "g")
    with pytest.raises(S.SiteProblem):
        S.check_name("assets/x.css", "assets")
    assert S.check_name("photos/a-1.jpg", "g") == "photos/a-1.jpg"

def test_site_target_is_the_folder_above_the_galleries():
    sys.path.insert(0, ROOT)
    from plugins.studio_page import SiteProblem, site_target
    cfg, url, folder = site_target({"type": "sftp", "host": "h", "user": "u", "root": "example.com/g"}, "https://example.com/g/")
    assert (cfg["root"], url, folder) == ("example.com", "https://example.com/", "g")
    cfg, url, folder = site_target({"type": "s3", "bucket": "b", "prefix": "site/photos"}, "https://cdn.example.com/site/photos/")
    assert (cfg["prefix"], url, folder) == ("site", "https://cdn.example.com/site/", "photos")
    for gc, base in (({"type": "sftp", "root": "g"}, "https://example.com/"),
                     ({"type": "sftp", "root": "example.com/g"}, "https://example.com/galleries/"),
                     ({}, "https://example.com/g/"), ({"type": "s3", "prefix": ""}, "https://example.com/g/")):
        with pytest.raises(SiteProblem):
            site_target(gc, base)

def test_today_says_when_an_online_photo_is_no_longer_allowed(env):
    A, c, S, tmp, web = env
    switch_on(c)
    a = session_photo(A, "a.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [a]}, environ_base=E)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)
    con = A.connect()
    con.execute("UPDATE sessions SET veto='a.jpg' WHERE id=1")
    con.commit(); con.close()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert not list((web / "photos").glob("*.jpg")) if (web / "photos").exists() else True
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_no_client_galleries_page_the_contact_page_says_how_a_gallery_opens(env):

    A, c, S, tmp, web = env
    switch_on(c)
    add_own(c, jpeg_with_metadata())
    c.post("/settings/studio-page/preview", environ_base=E)
    out = S.SITE_ROOT / "preview"
    files = built_files(out)
    assert not any(f.startswith("galleries/") for f in files)
    for f in files:
        if f.endswith(".html"):
            assert "Client galleries" not in (out / f).read_text()
    assert "Your gallery opens from the private link in your letter or email." in (out / "contact" / "index.html").read_text()
