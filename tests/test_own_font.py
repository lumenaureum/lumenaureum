import base64
import io
import json

import pytest

from test_gallery_look import E, build, env, opened, save_look, setting

FONT = None

def font_bytes():
    global FONT
    if FONT is None:
        from plugins import gallery as G
        FONT = (G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()
    return FONT

def ttf_bytes():
    from fontTools.ttLib import TTFont
    f = TTFont(io.BytesIO(font_bytes()))
    f.flavor = None
    b = io.BytesIO()
    f.save(b)
    return b.getvalue()

def upload(c, regular=None, light=None, **extra):
    data = dict(extra)
    if regular is not None:
        data["regular"] = (io.BytesIO(regular), "font.ttf")
    if light is not None:
        data["light"] = (io.BytesIO(light), "font-light.ttf")
    return c.post("/settings/font", data=data, content_type="multipart/form-data", environ_base=E)

def test_clean_font_accepts_fonts_and_refuses_anything_else():
    import settings_io as S
    out, fam = S.clean_font(ttf_bytes())
    assert out[:4] == b"wOF2" and fam.startswith("Cormorant Garamond")
    for bad in (b"", b"<svg onload=alert(1)>", b"wOF2" + b"\0" * 200, b"OTTO" + b"x" * 50, b"\x00\x01\x00\x00" * 10,
                b"wOF2" + b"\0" * (S.FONT_MAX + 1)):
        with pytest.raises(ValueError):
            S.clean_font(bad)

def test_upload_picks_it_and_the_gallery_carries_it(env):
    A, c, G, tmp, src = env
    r = upload(c, light=ttf_bytes())
    assert r.status_code == 302 and setting(A, "brand_font_300") is None
    r = upload(c, regular=b"not a font")
    assert setting(A, "brand_font_400") is None
    upload(c, regular=ttf_bytes(), light=font_bytes())
    assert base64.b64decode(setting(A, "brand_font_400"))[:4] == b"wOF2"
    assert setting(A, "gallery_font") == "own" and setting(A, "brand_font_name").startswith("Cormorant Garamond")
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    assert 'value="own" checked' in page and "Cormorant Garamond" in page
    assert c.get("/settings/gallery-font/own.woff2", environ_base=E).data[:4] == b"wOF2"
    out, key = build(A, c, G)
    m = json.loads(opened(G, out, key, "manifest.bin"))
    assert m["brand"]["theme"]["font"] == "own" and m["brand"]["theme"]["light"] is True
    assert (out / "font.woff2").read_bytes() == base64.b64decode(setting(A, "brand_font_400"))
    assert (out / "font-light.woff2").read_bytes() == base64.b64decode(setting(A, "brand_font_300"))
    assert "font(-light)?" in (G.ASSETS / "htaccess").read_text()
    upload(c, remove="1")
    assert setting(A, "brand_font_400") is None and setting(A, "gallery_font") == "classic"
    out, key = build(A, c, G)
    assert not (out / "font.woff2").exists() and not (out / "font-light.woff2").exists()

def test_own_chosen_without_a_font_falls_back_to_classic(env):
    A, c, G, tmp, src = env
    save_look(c, gallery_font="own")
    out, key = build(A, c, G)
    m = json.loads(opened(G, out, key, "manifest.bin"))
    assert m["brand"]["theme"]["font"] == "classic" and not (out / "font.woff2").exists()

def test_settings_file_carries_the_font_checked_again(env):
    A, c, G, tmp, src = env
    import settings_io as S
    good = base64.b64encode(ttf_bytes()).decode()
    raw = {"lumen_aureum_settings": 1, "settings": {"brand_font_400": good, "brand_font_name": "X<script>", "gallery_font": "own"}}
    settings, *_ = S.parse(json.dumps(raw), {})
    assert base64.b64decode(settings["brand_font_400"])[:4] == b"wOF2" and settings["brand_font_name"] == "Xscript"
    raw["settings"]["brand_font_400"] = base64.b64encode(b"<svg/>").decode()
    with pytest.raises(S.ImportProblem):
        S.parse(json.dumps(raw), {})
    raw["settings"].pop("brand_font_400")
    settings, *_ = S.parse(json.dumps(raw), {})
    assert settings["gallery_font"] == "classic"

def test_viewer_uses_the_own_font_from_beside_the_page():
    from plugins import gallery as G
    page = (G.ASSETS / "viewer.html").read_text()
    assert 'new FontFace("Gallery Own", "url(font.woff2)"' in page and "url(font-light.woff2)" in page
    assert "fonts.googleapis" not in page and "font-src 'self'" in page

def test_studio_page_build_uses_the_own_font():
    from plugins import studio_page as SP
    import settings_io
    import tempfile
    from pathlib import Path
    out = Path(tempfile.mkdtemp()) / "site"
    conf = dict(name="Studio", owner="Studio", tagline="", intro="", description="", about="", email="", phone="",
                address="", site_url="", site_path="/", logo=None, palette=settings_io.palette("#0d0c0b"), dark=True,
                font="own", credit=False, favicon=None, year=2026, htaccess=False, php=False, inquiry=None,
                own_font={400: font_bytes(), 300: font_bytes()})
    SP.build_site(out, conf, [])
    css = (out / "assets" / "site.css").read_text()
    assert (out / "assets" / "own-400.woff2").exists() and (out / "assets" / "own-300.woff2").exists()
    assert '"Site Own"' in css and "url(own-400.woff2)" in css and "googleapis" not in css
