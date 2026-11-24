import html
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
from test_photo_uploads import upload

E = {"REMOTE_ADDR": "127.0.0.1"}
FLAG = "Studio details changed since this build"

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    for k_, v_ in (("gallery_base_url", "https://gallery.test/g/"), ("studio_name", "Sample Light Studio"),
                   ("photographer_name", "Sam Sample"), ("business_email", "studio@example.com"),
                   ("business_phone", "555-0100"), ("business_website", "https://studio.example.com")):
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k_, v_))
    hid = con.execute("INSERT INTO households (name) VALUES ('The Sample Family')").lastrowid
    sid = con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, delivery_method)
                         VALUES (?, 'portfolio', 'delivered', '2026-11-01', 'link')""", (hid,)).lastrowid
    con.commit()
    con.close()
    return A, c, hid, sid

def sql(A, s, args=()):
    con = A.connect()
    rows = con.execute(s, args).fetchall()
    con.commit()
    con.close()
    return rows

def text(c, path):
    t = c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))

def join(prefix, sid):
    for t in [t for t in threading.enumerate() if t.name == f"{prefix}-{sid}"]:
        t.join(timeout=60)
        assert not t.is_alive()

def built(A, c, sid, n=2):
    from PIL import Image
    for i in range(n):
        b = io.BytesIO()
        Image.new("RGB", (64, 48), (i * 40, 90, 120)).save(b, "JPEG")
        upload(c, sid, f"img{i}.jpg", b.getvalue(), kind="export")
    sql(A, "UPDATE sessions SET gallery_codeword='' WHERE id=?", (sid,))
    assert c.post(f"/session/{sid}/gallery/build", data={"from": "uploads"}, environ_base=E).status_code == 302
    join("gallery", sid)
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["state"] == "done"

def online(A, c, sid):
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    assert sql(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "up"

def sealed(A, sid):

    G = A.app.config["gallery_module"]
    gid, key, ekey = sql(A, "SELECT s.gallery_id, g.key, g.ekey FROM sessions s JOIN galleries g ON g.session_id=s.id "
                            "WHERE s.id=?", (sid,))[0]
    plain, _ = G.open_any(ekey or key, (G.OUT_ROOT / gid / "manifest.bin").read_bytes(), "manifest.bin")
    return json.loads(plain)["brand"]

def rebuild_form(c, hid, sid):
    raw = c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    row = re.search(r'<div class="ui-factrow[^"]*" id="gal-details">.*?</div></div>', raw, re.S)
    assert row, "no Studio details row"
    form = re.search(r'<form method="post" action="(/session/%d/gallery/build)"[^>]*>(.*?)</form>' % sid, row.group(0), re.S)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form.group(2)))
    assert "Rebuild with the new details" in form.group(2)
    return form.group(1), fields

def test_a_build_records_its_details_and_a_settings_change_shows_on_the_gallery(env):
    A, c, hid, sid = env
    built(A, c, sid)
    assert sql(A, "SELECT brand_fp FROM galleries WHERE session_id=?", (sid,))[0][0]
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    sql(A, "UPDATE settings SET value='555-0199' WHERE key='business_phone'")
    page = text(c, f"/c/{hid}?s={sid}")
    assert FLAG in page and "Rebuild with the new details" in page
    sql(A, "UPDATE settings SET value='555-0100' WHERE key='business_phone'")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    sql(A, "UPDATE settings SET value='A New Name' WHERE key='studio_name'")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")

def test_rebuild_keeps_the_link_and_key_and_updates_the_gallery_online(env):
    A, c, hid, sid = env
    built(A, c, sid)
    online(A, c, sid)
    G = A.app.config["gallery_module"]
    before = sql(A, "SELECT s.gallery_id, g.key, g.ekey FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=?",
                 (sid,))[0]
    sql(A, "UPDATE settings SET value='hello@example.com' WHERE key='business_email'")
    url, fields = rebuild_form(c, hid, sid)
    assert fields == {"from": "uploads"}
    assert c.post(url, data=fields, environ_base=E).status_code == 302
    join("gallery", sid)
    join("gal-up", sid)
    after = sql(A, "SELECT s.gallery_id, g.key, g.ekey FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=?",
                (sid,))[0]
    assert tuple(after) == tuple(before)
    assert sealed(A, sid)["email"] == "hello@example.com"
    assert G.online_is_current(before[0])
    page = text(c, f"/c/{hid}?s={sid}")
    assert FLAG not in page and "The copy online is older than this build" not in page

def test_a_gallery_not_online_yet_waits_for_its_upload_after_the_rebuild(env):
    A, c, hid, sid = env
    built(A, c, sid)
    sql(A, "UPDATE settings SET value='Sam B. Sample' WHERE key='photographer_name'")
    url, fields = rebuild_form(c, hid, sid)
    c.post(url, data=fields, environ_base=E)
    join("gallery", sid)
    assert sealed(A, sid)["photographer"] == "Sam B. Sample"
    assert (sql(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] or "") not in ("up", "uploading")
    page = text(c, f"/c/{hid}?s={sid}")
    assert FLAG not in page and "Upload" in page

def test_a_gallery_from_before_this_release_is_judged_by_what_its_build_holds(env):
    A, c, hid, sid = env
    built(A, c, sid)
    sql(A, "UPDATE galleries SET brand_fp=NULL")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    sql(A, "UPDATE settings SET value='https://new.example.com' WHERE key='business_website'")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")
    G = A.app.config["gallery_module"]
    gid = sql(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    (G.OUT_ROOT / gid / "manifest.bin").write_bytes(b"\0" * 64)
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")

def test_an_adopted_gallery_as_it_came_is_left_to_bring_up_to_date(env):
    A, c, hid, sid = env
    built(A, c, sid)
    sql(A, "UPDATE settings SET value='555-0199' WHERE key='business_phone'")
    sql(A, "UPDATE galleries SET adopted='2026-11-01', adopted_old=1")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    assert "Studio details changed" not in text(c, "/galleries")

def test_no_photos_left_to_build_from_raises_nothing(env):
    A, c, hid, sid = env
    built(A, c, sid)
    sql(A, "UPDATE settings SET value='555-0199' WHERE key='business_phone'")
    sql(A, "DELETE FROM photo_files WHERE session_id=?", (sid,))
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    assert FLAG not in text(c, "/galleries")

def test_the_dark_background_logo_counts_as_the_logo(env):
    A, c, hid, sid = env
    sql(A, "INSERT INTO settings (key, value) VALUES ('gallery_bg', '#111111')")
    built(A, c, sid)
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    sql(A, "INSERT INTO settings (key, value) VALUES ('logo_dark_png', 'ZGFyaw==')")
    assert FLAG in text(c, f"/c/{hid}?s={sid}")

def test_the_galleries_page_lists_them_and_rebuilds_them_all(env):
    A, c, hid, sid = env
    built(A, c, sid)
    online(A, c, sid)
    con = A.connect()
    hid2 = con.execute("INSERT INTO households (name) VALUES ('The Example Household')").lastrowid
    sid2 = con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, delivery_method)
                          VALUES (?, 'portfolio', 'delivered', '2026-11-02', 'link')""", (hid2,)).lastrowid
    con.commit()
    con.close()
    built(A, c, sid2)
    page = text(c, "/galleries")
    assert "Studio details changed" not in page and "Rebuild all" not in page
    sql(A, "UPDATE settings SET value='Another Studio' WHERE key='studio_name'")
    page = text(c, "/galleries")
    assert "2 galleries were built before the studio details in Settings changed." in page
    assert page.count(FLAG) == 2 and "Rebuild all with the new details" in page
    raw = c.get("/galleries", environ_base=E).get_data(as_text=True)
    assert raw.index('id="gal-rebuild"') < raw.index('id="gallery-cards"')
    r = c.post("/galleries/rebuild-details", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/galleries")
    for s_ in (sid, sid2):
        join("gallery", s_)
        assert sealed(A, s_)["studio"] == "Another Studio"
    join("gal-up", sid)
    G = A.app.config["gallery_module"]
    gid = sql(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert G.online_is_current(gid)
    assert (sql(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid2,))[0][0] or "") != "up"
    page = text(c, "/galleries")
    assert FLAG not in page and "Rebuild all" not in page
    hist = [r_[0] for r_ in sql(A, "SELECT action FROM audit")]
    assert "Rebuilt 2 galleries with the new studio details." in hist
