import base64
import io
import json
import os
import re
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from helpers import host_bytes, host_has, host_write, hosted

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "sample"
    src.mkdir(parents=True)
    for i, col in enumerate(("red", "blue"), 1):
        Image.new("RGB", (64, 48), col).save(src / f"img{i}.jpg", "JPEG")
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('The Sample Family')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', '2026-10-01')")
    con.commit(); con.close()
    from plugins import gallery as G
    return A, c, G, tmp_path, src

def wait(name):
    for t in [t for t in threading.enumerate() if t.name == name]:
        t.join(timeout=20)
        assert not t.is_alive(), name

def setting(A, k, v):
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    con.commit(); con.close()

def row(A, sql, *a):
    con = A.connect()
    try:
        return con.execute(sql, a).fetchone()
    finally:
        con.close()

def host_ready(A, tmp):
    from vault import web as VW
    host = tmp / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    setting(A, "gallery_base_url", "https://example.com/g/")
    return host

def gallery_block(c):
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    return page[page.index('id="gallery"'):][:6000]

def test_rebuild_of_an_online_gallery_goes_online(env):
    A, c, G, tmp, src = env
    host = host_ready(A, tmp)
    c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    wait("gallery-1")
    c.post("/session/1/gallery/upload", environ_base=E)
    wait("gal-up-1")
    gid, key = row(A, "SELECT s.gallery_id, g.key FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    old = {p.name for p in (host / gid).glob("*.bin")}
    (host / gid / "picks.las").write_bytes(b"LAS1 the client's picks")
    host_write(host / gid, "seen.txt", "2026-10-05T12:00:00Z")   # fixed date: already past, stays past
    block = gallery_block(c)
    assert "older than this build" not in block
    link = re.search(r'<span hidden id="gal-link-1">([^<]+)</span>', block).group(1)
    assert link == f"https://example.com/g/{gid}/#k={key}" and 'data-copy="#gal-link-1"' in block and "Copy link" in block

    setting(A, "gallery_bg", "#262422")
    c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    wait("gallery-1")
    out = G.OUT_ROOT / gid
    assert host_bytes(host / gid, "manifest.bin", key) == (out / "manifest.bin").read_bytes()
    assert (host / gid / "index.html").read_bytes() == (out / "index.html").read_bytes()
    now = {p.name for p in (host / gid).glob("*.bin")}
    assert not (old - {hosted(host / gid, "manifest.bin", key).name}) & now
    assert not row(A, "SELECT prune_due FROM galleries WHERE session_id=1")[0]
    assert host_bytes(host / gid, "picks.las") == b"LAS1 the client's picks" and host_has(host / gid, "seen.txt")
    assert host_has(host / gid, "limits.txt") and not (host / gid / G.ONLINE_MARK).exists()
    assert tuple(row(A, "SELECT s.gallery_id, g.key, g.upload_state FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")) \
        == (gid, key, "up")
    assert row(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Gallery updated on the web%'")[0] == 1
    assert "older than this build" not in gallery_block(c)

def test_a_held_update_says_the_copy_online_is_older_and_offers_upload(env):
    A, c, G, tmp, src = env
    host = host_ready(A, tmp)
    c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    wait("gallery-1")
    c.post("/session/1/gallery/upload", environ_base=E)
    wait("gal-up-1")
    gid, key = row(A, "SELECT s.gallery_id, g.key FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(kw) or True)
    try:
        c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
        wait("gallery-1")
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert held and held[0]["update"] is True and held[0]["gallery_id"] == gid
    assert host_bytes(host / gid, "manifest.bin", key) != (G.OUT_ROOT / gid / "manifest.bin").read_bytes()
    block = gallery_block(c)
    assert "The copy online is older than this build" in block and "Upload the new build" in block
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    c.post("/session/1/gallery/upload", environ_base=E)
    wait("gal-up-1")
    assert host_bytes(host / gid, "manifest.bin", key) == (G.OUT_ROOT / gid / "manifest.bin").read_bytes()
    assert "older than this build" not in gallery_block(c)

def test_corner_mark_with_a_logo_that_has_no_see_through_part_uses_the_word(env):
    A, c, G, tmp, src = env
    from PIL import Image, ImageChops
    photo = Image.new("RGB", (400, 300), (40, 60, 80))
    box = io.BytesIO()
    Image.new("RGB", (120, 40), (0, 0, 0)).save(box, "JPEG")
    opaque = G.watermark(photo, dict(style="corner", text="PROOF", strength=40, logo=box.getvalue()))
    word = G.watermark(photo, dict(style="corner", text="PROOF", strength=40, logo=None))
    assert not ImageChops.difference(opaque, word).getbbox()
    hole = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
    hole.paste(Image.new("RGBA", (60, 20), (0, 0, 0, 255)), (30, 10))
    b = io.BytesIO(); hole.save(b, "PNG")
    shaped = G.watermark(photo, dict(style="corner", text="PROOF", strength=40, logo=b.getvalue()))
    assert ImageChops.difference(shaped, word).getbbox()

def test_copyright_follows_the_terms_the_client_agreed_to(env):
    A, c, G, tmp, src = env
    now = "family" in A.C.LICENSE_TYPES
    with A.app.test_request_context():
        brand = A.app.config["gallery_brand"]
        s = lambda: row(A, "SELECT * FROM sessions WHERE id=1")
        assert brand(s())["owns"] is now and A.licensed(1) is now
        con = A.connect()
        con.execute("INSERT INTO quote_versions (id, session_id, at, expires, data) VALUES (?, 1, '2026-09-01', '2026-10-01', ?)",
                    (9001, json.dumps(dict(type="family", license=not now, lines=[], total=0, deposit=0))))
        con.commit(); con.close()
        assert brand(s())["owns"] is (not now) and A.licensed(1) is (not now)
        con = A.connect()
        con.execute("INSERT INTO doc_snapshots (kind, ref, at, data) VALUES ('agreement', 1, '2026-09-02', ?)",
                    (json.dumps(dict(license=now)),))
        con.commit(); con.close()
        assert brand(s())["owns"] is now
        assert brand(s())["what"] == "Family session"

def test_dark_logo_upload_keeps_the_client_galleries_choices(env):
    A, c, G, tmp, src = env
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (60, 30), (255, 255, 255, 0)).save(b, "PNG")
    r = c.post("/settings/logo", data=dict(which="dark", look_posted="1", gallery_font="script", gallery_bg="#262422",
                                           proof_mark="tiled", proof_mark_text="Sample", proof_mark_strength="30",
                                           logo=(io.BytesIO(b.getvalue()), "dark.png")),
               content_type="multipart/form-data", environ_base=E)
    assert r.status_code == 302
    got = {k: row(A, "SELECT value FROM settings WHERE key=?", k)[0]
           for k in ("gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength", "logo_dark_png")}
    assert got["gallery_bg"] == "#262422" and got["gallery_font"] == "script" and got["proof_mark_text"] == "Sample"
    assert base64.b64decode(got["logo_dark_png"])[:4] == b"\x89PNG"
    assert "Remove the dark logo" in c.get("/settings?tab=branding", environ_base=E).get_data(as_text=True)

def test_today_card_keeps_the_name_and_puts_the_hint_on_its_own_line(env):
    A, c, G, tmp, src = env
    con = A.connect()
    con.execute("UPDATE sessions SET stage='booked', shoot_date='2099-01-01' WHERE id=1")
    con.execute("INSERT INTO households (name) VALUES ('The Sample Group')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (2, 'portrait', 'booked', '2099-01-02')")
    con.commit(); con.close()
    page = c.get("/", environ_base=E).get_data(as_text=True)

    assert re.search(r"<b>The Sample Family</b><small>[^<]*, Booked", page) or "The Sample Family</a>" in page
    assert "Portraits, Booked" in page and 't-stage' not in page
