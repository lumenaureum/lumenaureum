import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_271_155 import E, FLAG, built, env, sql, text

TODAY = "galleries show old studio details"

def set_(A, k, v):
    sql(A, "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

def test_today_has_one_line_leading_to_the_galleries_page(env):
    A, c, hid, sid = env
    built(A, c, sid)
    assert "old studio details" not in text(c, "/")
    set_(A, "business_phone", "555-0199")
    raw = c.get("/", environ_base=E).get_data(as_text=True)
    assert "1 gallery shows old studio details" in text(c, "/")
    assert 'href="/galleries#gal-rebuild"' in raw
    assert 'id="gal-rebuild"' in c.get("/galleries", environ_base=E).get_data(as_text=True)
    set_(A, "business_phone", "555-0100")
    assert "old studio details" not in text(c, "/")

def test_today_counts_them_all_in_one_line(env):
    A, c, hid, sid = env
    built(A, c, sid)
    con = A.connect()
    hid2 = con.execute("INSERT INTO households (name) VALUES ('The Example Household')").lastrowid
    sid2 = con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, delivery_method)
                          VALUES (?, 'portfolio', 'delivered', '2026-11-02', 'link')""", (hid2,)).lastrowid
    con.commit()
    con.close()
    built(A, c, sid2)
    set_(A, "studio_name", "Another Studio")
    page = text(c, "/")
    assert page.count(TODAY) == 1 and "2 " + TODAY in page

def test_a_font_change_counts_as_a_studio_details_change(env):
    A, c, hid, sid = env
    built(A, c, sid)
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    set_(A, "gallery_font", "elegant")
    page = text(c, f"/c/{hid}?s={sid}")
    assert FLAG in page and "Rebuild with the new details" in page
    assert "1 gallery shows old studio details" in text(c, "/")
    set_(A, "gallery_font", "own")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")

def test_a_color_change_counts_as_a_studio_details_change(env):
    A, c, hid, sid = env
    built(A, c, sid)
    set_(A, "gallery_bg", "#222222")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")
    assert "Studio details changed" in text(c, "/galleries")

def test_a_value_from_before_the_look_counted_is_read_from_the_build(env):
    A, c, hid, sid = env
    built(A, c, sid)
    fp = sql(A, "SELECT brand_fp FROM galleries WHERE session_id=?", (sid,))[0][0]
    assert fp.startswith("2.")
    sql(A, "UPDATE galleries SET brand_fp=?", ("0123456789abcdef0123456789abcdef",))
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    set_(A, "gallery_font", "script")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")
    set_(A, "gallery_font", "classic")
    sql(A, "UPDATE galleries SET brand_fp=NULL")
    set_(A, "gallery_bg", "#203040")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")

def test_a_build_without_a_look_is_never_flagged_for_it(env):
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    A, c, hid, sid = env
    built(A, c, sid)
    G = A.app.config["gallery_module"]
    sql(A, "UPDATE galleries SET brand_fp=NULL")
    gid, key, ekey = sql(A, "SELECT s.gallery_id, g.key, g.ekey FROM sessions s JOIN galleries g ON g.session_id=s.id "
                            "WHERE s.id=?", (sid,))[0]
    m_ = G.OUT_ROOT / gid / "manifest.bin"
    plain, _ = G.open_any(ekey or key, m_.read_bytes(), "manifest.bin")
    man = json.loads(plain)
    man["brand"].pop("theme")
    m_.write_bytes(G.seal(AESGCM(G.key_bytes(ekey or key)), json.dumps(man).encode(), "manifest.bin"))
    set_(A, "gallery_font", "elegant")
    set_(A, "gallery_bg", "#203040")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    set_(A, "business_phone", "555-0199")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")
