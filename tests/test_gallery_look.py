import base64
import io
import json
import os
import subprocess
import sys
import threading

import pytest

from helpers_tools import need_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
VIEWER = os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")

def png(color=(200, 30, 30, 255), size=(60, 30), hole=False):
    from PIL import Image
    im = Image.new("RGBA", size, color)
    if hole:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (size[0] // 2, size[1] // 2), color), (size[0] // 4, size[1] // 4))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()

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
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for i, col in enumerate(((120, 90, 60), (60, 90, 120)), 1):
        Image.new("RGB", (400, 300), col).save(src / f"img{i}.jpg", "JPEG", quality=95)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', '2026-10-01')")
    con.commit(); con.close()
    from plugins import gallery as G
    return A, c, G, tmp_path, src

def setting(A, k):
    con = A.connect()
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    con.close()
    return r[0] if r else None

def save_look(c, **form):
    data = dict(look_posted="1", gallery_font="classic", gallery_bg="#f6f3ed", proof_mark="", proof_mark_text="PROOF",
                proof_mark_strength="20")
    data.update(form)
    return c.post("/settings", data=data, environ_base=E)

def build(A, c, G):
    c.post("/session/1/gallery/build", data={"source_dir": str(G.PHOTOS_ROOT / "export" / "lane")}, environ_base=E)
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=20)
    j = c.get("/session/1/gallery/status", environ_base=E).get_json()
    assert j["state"] == "done", j
    con = A.connect()
    gid, = con.execute("SELECT gallery_id FROM sessions WHERE id=1").fetchone()
    key, = con.execute("SELECT COALESCE(NULLIF(ekey, ''), key) FROM galleries WHERE session_id=1").fetchone()
    con.close()
    return G.OUT_ROOT / gid, key

def opened(G, out, key, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = (out / name).read_bytes()
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def test_settings_save_and_validation(env):
    A, c, G, tmp, src = env
    r = c.get("/settings?tab=branding", environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Client galleries" in page and "The Sample Family" in page and "gallery_look.js" in page
    assert "Logo on your documents" in page
    profile = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    assert 'id="gallery-look"' not in profile and "Logo on your documents" not in profile
    save_look(c, gallery_font="script", gallery_bg="#262422", proof_mark="tiled", proof_mark_text=" Draft\x07 copy ",
              proof_mark_strength="35")
    assert [setting(A, k) for k in ("gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength")] == \
        ["script", "#262422", "tiled", "Draft copy", "35"]
    save_look(c, gallery_font="comic", gallery_bg="red", proof_mark="huge", proof_mark_text="x" * 50, proof_mark_strength="900")
    assert [setting(A, k) for k in ("gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength")] == \
        ["classic", "#f6f3ed", "", "x" * 20, "100"]
    save_look(c, gallery_bg="custom", gallery_bg_custom="#3A6EA5", proof_mark_strength="1")
    assert (setting(A, "gallery_bg"), setting(A, "proof_mark_strength")) == ("#3a6ea5", "5")
    save_look(c, proof_mark_text="", proof_mark_strength="soon")
    assert (setting(A, "proof_mark_text"), setting(A, "proof_mark_strength")) == ("PROOF", "20")
    f = c.get("/settings/gallery-font/great-vibes.woff2", environ_base=E)
    assert f.status_code == 200 and f.data[:4] == b"wOF2"
    assert c.get("/settings/gallery-font/..%2fviewer.woff2", environ_base=E).status_code == 404

def test_palettes_readable_and_match_the_preview_script():
    import settings_io as S
    assert S.palette("#f6f3ed")["ink"] == "#1f1b16" and S.palette("#0d0c0b")["mute"] == "#968d82"
    colors = ["#808080", "#3a6ea5", "#ffeecc", "#123456", "#000000", "#fefefe", "#5a1e2b", "#c8b89a", "#777777", "#262423",
              "#8a7a66", "#6b6b6b", "#d9667f", "#b8862f", "#f6f3ed", "#ffffff", "#262422", "#0d0c0b"]
    for bg in colors:
        p = S.palette(bg)
        c = S._rgb(bg)

        assert S._contrast(c, S._rgb(p["ink"])) >= 4.5 and S._contrast(c, S._rgb(p["mute"])) >= 4.5, (bg, p)
        assert S._contrast(c, S._rgb(p["ring"])) >= 3, (bg, p)
        assert S._contrast(c, S._rgb(p["mute"])) <= S._contrast(c, S._rgb(p["ink"]))
    node = need_tool("node")
    colors = [b for b in colors if b not in S.PALETTES]
    js = ("const {palette} = require(process.argv[1]); process.stdout.write(JSON.stringify(" + json.dumps(colors) +
          ".map(b => palette(b, {}))));")
    out = json.loads(subprocess.run([node, "-e", js, os.path.join(ROOT, "static", "gallery_look.js")], capture_output=True,
                                    text=True, check=True, timeout=60).stdout)
    assert out == [S.palette(b) for b in colors]

def test_build_puts_theme_in_manifest_and_copies_one_font(env):
    A, c, G, tmp, src = env
    out, key = build(A, c, G)
    m = json.loads(opened(G, out, key, "manifest.bin"))
    assert m["brand"]["theme"] == {"font": "classic", "colors": __import__("settings_io").palette("#f6f3ed")}
    assert not (out / "font.woff2").exists()
    for font, file_ in (("script", "great-vibes.woff2"), ("elegant", "cormorant-garamond.woff2")):
        save_look(c, gallery_font=font, gallery_bg="#262422")
        out, key = build(A, c, G)
        m = json.loads(opened(G, out, key, "manifest.bin"))
        assert m["brand"]["theme"]["font"] == font and m["brand"]["theme"]["colors"]["bg"] == "#262422"
        assert (out / "font.woff2").read_bytes() == (G.ASSETS / "fonts" / file_).read_bytes()
        assert len([p for p in out.iterdir() if p.suffix == ".woff2"]) == 1
        page = (out / "index.html").read_text()
        assert "262422" not in page and "great-vibes" not in page and "cormorant" not in page.lower()
    assert G.MIME[".woff2"] == "font/woff2"

def test_proof_mark_settings_reach_the_proofs(env):
    A, c, G, tmp, src = env
    from PIL import Image, ImageChops

    def first_proof():
        out, key = build(A, c, G)
        m = json.loads(opened(G, out, key, "manifest.bin"))
        return Image.open(io.BytesIO(opened(G, out, key, m["items"][0]["f"]))).convert("RGB")

    off = first_proof()
    assert ImageChops.difference(off, first_proof()).getbbox() is None or \
        max(max(b) for b in ImageChops.difference(off, first_proof()).getextrema()) <= 2
    save_look(c, proof_mark="tiled", proof_mark_strength="20")
    tiled = first_proof()
    assert max(max(b) for b in ImageChops.difference(off, tiled).getextrema()) > 20
    save_look(c, proof_mark="corner", proof_mark_strength="20")
    corner = first_proof()
    box = ImageChops.difference(off, corner).getbbox()
    assert box and box[0] > off.width // 2 and box[1] > off.height // 2
    save_look(c, proof_mark="")
    assert max(max(b) for b in ImageChops.difference(off, first_proof()).getextrema()) <= 2

def test_dark_background_logo_choice(env):
    A, c, G, tmp, src = env
    import settings_io as S
    clear, opaque, dark = png(hole=True), png(), png((240, 240, 240, 255), hole=True)
    b64 = lambda b: base64.b64encode(b).decode()

    assert S.gallery_logo("#f6f3ed", b64(clear), b64(dark)) == b64(clear)
    white = S.gallery_logo("#262422", b64(clear), "")
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(white))).convert("RGBA")
    src_ = Image.open(io.BytesIO(clear)).convert("RGBA")
    assert im.getchannel("A").tobytes() == src_.getchannel("A").tobytes()
    assert {px[:3] for px, a in zip(im.getdata(), im.getchannel("A").getdata()) if a} == {(255, 255, 255)}
    assert S.gallery_logo("#0d0c0b", b64(opaque), "") == b64(opaque)
    assert S.gallery_logo("#0d0c0b", b64(clear), b64(dark)) == b64(dark)

    c.post("/settings/logo", data={"logo": (io.BytesIO(clear), "logo.png")}, content_type="multipart/form-data", environ_base=E)
    c.post("/settings/logo", data={"which": "dark", "logo": (io.BytesIO(dark), "dark.png")}, content_type="multipart/form-data", environ_base=E)
    assert setting(A, "logo_png") and setting(A, "logo_dark_png") and setting(A, "logo_png") != setting(A, "logo_dark_png")
    save_look(c, gallery_bg="#262422")
    out, key = build(A, c, G)
    assert json.loads(opened(G, out, key, "manifest.bin"))["brand"]["logo"] == setting(A, "logo_dark_png")
    c.post("/settings/logo", data={"which": "dark", "remove": "1"}, environ_base=E)
    assert setting(A, "logo_dark_png") is None and setting(A, "logo_png")
    out, key = build(A, c, G)
    assert json.loads(opened(G, out, key, "manifest.bin"))["brand"]["logo"] == S.white_logo(setting(A, "logo_png"))
    save_look(c, gallery_bg="#ffffff")
    out, key = build(A, c, G)
    assert json.loads(opened(G, out, key, "manifest.bin"))["brand"]["logo"] == setting(A, "logo_png")

def test_viewer_csp_allows_own_fonts_only(env):
    A, c, G, tmp, src = env
    page = open(VIEWER).read()
    csp = page.split('http-equiv="Content-Security-Policy" content="')[1].split('"')[0]
    assert "font-src 'self'" in csp and "default-src 'none'" in csp and "img-src blob:" in csp
    assert G.VIEWER_SCRIPT_HASHES == G._script_hashes(page)
    save_look(c, gallery_font="script")
    out, key = build(A, c, G)
    r = c.get(f"/gallery-preview/{out.name}/", environ_base=E)
    assert r.status_code == 200 and "font-src 'self'" in r.headers["Content-Security-Policy"]
    f = c.get(f"/gallery-preview/{out.name}/font.woff2", environ_base=E)
    assert f.status_code == 200 and f.data[:4] == b"wOF2"

def test_settings_file_round_trips_the_look(env):
    A, c, G, tmp, src = env
    save_look(c, gallery_font="elegant", gallery_bg="#0d0c0b", proof_mark="corner", proof_mark_text="SAMPLE", proof_mark_strength="45")
    c.post("/settings/logo", data={"which": "dark", "logo": (io.BytesIO(png(hole=True)), "d.png")}, content_type="multipart/form-data", environ_base=E)
    data = json.loads(c.get("/settings/export.json", environ_base=E).data)
    want = {k: data["settings"][k] for k in ("gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength", "logo_dark_png")}
    assert want["gallery_font"] == "elegant" and want["proof_mark_strength"] == "45" and want["logo_dark_png"]
    save_look(c)
    c.post("/settings/logo", data={"which": "dark", "remove": "1"}, environ_base=E)
    c.post("/settings/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    assert {k: setting(A, k) for k in want} == want
    import settings_io as S
    data["settings"].update(gallery_font="wingdings", gallery_bg="url(x)", proof_mark_strength="-4")
    s_, *_ = S.parse(json.dumps(data), {})
    assert (s_["gallery_font"], s_["gallery_bg"], s_["proof_mark_strength"]) == ("classic", "#f6f3ed", "5")
    data["settings"]["logo_dark_png"] = base64.b64encode(b"GIF89a....").decode()
    with pytest.raises(S.ImportProblem):
        S.parse(json.dumps(data), {})

def items(*names):
    return [dict(f=f"{i:024x}.bin", t=f"{i + 100:024x}.bin", w=10, h=10, tw=5, th=5, n=n) for i, n in enumerate(names)]

@pytest.fixture(scope="module")
def viewer_runs(tmp_path_factory):
    node = need_tool("node")
    picks = dict(retouch=3, market=2, email="me@example.com")
    dark = dict(font="script", colors=dict(bg="#262422", ink="#eee7dc", mute="#a59b8f", line="#3a3631", cell="#33302c", edge="#57514a"))
    cases = {
        "theme": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg"), picks=picks, brand=dict(theme=dark))),

        "rebuilt": dict(manifest=dict(v=1, title="Lane", items=items("new.jpg", "a.jpg", "b.jpg"), picks=picks, brand={}),
                        store={"picks:/g/x/": json.dumps(dict(v=2, picks={"n:b.jpg": dict(r=True, n="soften"), "n:gone.jpg": dict(m=True)}))}),

        "migrated": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg", "b.jpg", "c.jpg"), picks=picks, brand={}),
                         store={"picks:/g/x/": json.dumps({"1": dict(r=True), "2": dict(m=True)}),
                                "picks:/g/x/:sent": json.dumps([["1", dict(r=True)], ["2", dict(m=True)]])}),
        "dropped": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg", "b.jpg"), picks=picks, brand={}),
                        store={"picks:/g/x/": json.dumps({"1": dict(r=True), "5": dict(m=True)})}),
        "owns": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg"), picks=picks,
                                   brand=dict(studio="Gold Light", date="May 2, 2026", owns=True))),
        "transfers": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg"), picks=picks,
                                        brand=dict(studio="Gold Light", date="May 2, 2026", owns=False))),
        "no_name": dict(manifest=dict(v=1, title="Lane", items=items("a.jpg"), picks=picks, brand=dict(owns=True))),
        "same_names": dict(manifest=dict(v=1, title="Lane", items=items("x.jpg", "x.jpg"), picks=picks, brand={}),
                           store={"picks:/g/x/": json.dumps(dict(v=2, picks={"n:x.jpg#2": dict(v=True)}))}),
    }
    f = tmp_path_factory.mktemp("look") / "cases.json"
    f.write_text(json.dumps(cases))
    out = subprocess.run([node, os.path.join(ROOT, "tests", "viewer_look_harness.js"), VIEWER, str(f)], capture_output=True,
                         text=True, timeout=120, check=True).stdout
    return json.loads(out)

def test_viewer_applies_the_theme(viewer_runs):
    r = viewer_runs["theme"]
    assert r["vars"]["--bg"] == "#262422" and r["vars"]["--ink"] == "#eee7dc" and r["vars"]["--edge"] == "#57514a"
    assert "dark" in r["classes"] and "f-script" in r["classes"]
    assert r["fonts"] == [["Gallery Script", "url(font.woff2)"]]
    assert json.loads(r["saved"]["look:/g/x/"])["colors"]["bg"] == "#262422"

def test_picks_follow_the_photo_after_a_rebuild(viewer_runs):
    r = viewer_runs["rebuilt"]
    assert r["lines"] == ["Retouch b.jpg (photo 3): soften"]

def test_picks_by_place_move_over_to_names(viewer_runs):
    r = viewer_runs["migrated"]
    assert r["lines"] == ["Retouch b.jpg (photo 2)", "Suggested for your portfolio: c.jpg (photo 3)"]
    assert json.loads(r["saved"]["picks:/g/x/"]) == dict(v=2, picks={"n:b.jpg": dict(r=True), "n:c.jpg": dict(m=True)})
    assert json.loads(r["saved"]["picks:/g/x/:sent"]) == [["n:b.jpg", dict(r=True)], ["n:c.jpg", dict(m=True)]]
    d = viewer_runs["dropped"]
    assert d["lines"] == [] and json.loads(d["saved"]["picks:/g/x/"]) == dict(v=2, picks={})
    s = viewer_runs["same_names"]
    assert s["lines"] == ["Please don't show: x.jpg (photo 2)"]

def test_copyright_line_only_when_the_photographer_keeps_it(viewer_runs):
    assert viewer_runs["owns"]["rights"] == "\u00a9 2026 Gold Light. All rights reserved."
    assert viewer_runs["transfers"]["rights"] == "Photos by Gold Light."
    assert viewer_runs["no_name"]["rights"] == ""
    assert viewer_runs["theme"]["rights"] == ""

def test_gallery_brand_says_who_keeps_the_copyright(env, monkeypatch):
    A, c, G, tmp_path, src = env
    lic = "realestate"
    monkeypatch.setattr(A.C, "LICENSE_TYPES", (lic,))
    con = A.connect()
    con.execute("INSERT INTO events (id, name, date, rights) VALUES (1, 'Recital', '2026-10-01', 'families')")
    con.execute("INSERT INTO events (id, name, date, rights) VALUES (2, 'Gala', '2026-10-01', 'organizer')")
    con.execute("INSERT INTO events (id, name, date, rights) VALUES (3, 'Meet', '2026-10-01', NULL)")
    con.execute("INSERT INTO events (id, name, date, rights) VALUES (4, 'Fair', '2026-10-01', 'custom')")
    for sid, typ, eid in ((2, lic, None), (3, "family", 1), (4, "family", 2), (5, "family", 3), (6, "family", 4)):
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date, event_id) VALUES (?, 1, ?, 'shot', '2026-10-01', ?)",
                    (sid, typ, eid))
    con.commit()
    con.close()
    brand = A.app.config["gallery_brand"]
    with A.app.test_request_context():
        owns = {sid: brand(A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True))["owns"] for sid in range(1, 7)}
    assert owns[1] is False
    assert owns[2] is True
    assert owns[3] is True and owns[5] is True
    assert owns[4] is False
    assert owns[6] is False

def test_the_device_keeps_nothing_readable(viewer_runs):

    for name in ("theme", "rebuilt", "migrated", "same_names"):
        stored = viewer_runs[name]["stored"]
        assert stored, name
        for k, v in stored.items():
            assert k.startswith("la:") and len(k) == 35, (name, k)
            for word in ("/g/x/", "picks:", "look:", "b.jpg", "soften", "#262422"):
                assert word not in k and word not in v, (name, word)
