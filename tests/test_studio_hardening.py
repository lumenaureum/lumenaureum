import base64
import io
import json
import os
import struct
import time

import pytest

from test_studio_page import E, env, jpeg_with_metadata, session_photo, setting, switch_on, wait
from test_studio_inquiries import GOOD, put_inquiry
from test_studio_site_release import WORK, _d, php_site, set_veto, upload

def test_a_vetoed_name_holds_back_its_edits_and_nothing_else():
    from plugins.studio_page import read_veto, vetoed
    names = ["IMG_0042.jpg", "IMG_0042-Edit.jpg", "img_0042 copy.png", "IMG_0042-2.jpg", "IMG_00421.jpg", "IMG_0043.jpg"]
    v = read_veto("IMG_0042.jpg", names)
    assert not v.unclear
    assert [n for n in names if v.hides(n)] == ["IMG_0042.jpg", "IMG_0042-Edit.jpg", "img_0042 copy.png", "IMG_0042-2.jpg"]
    assert vetoed("IMG_0042.jpg", "IMG_0042-Edit.jpg", names)
    assert not vetoed("img1.jpg", "img10.jpg")
    assert not read_veto("IMG_0042.jpg (photo 4), img_0043", names).unclear

@pytest.mark.parametrize("note", ["photo 3", "#2", "2, 3", "Photo #2", "photos 2 and 3", "the one by the lake",
                                  "IMG_0042.jpg, IMG_9999.jpg", "Please don't show: IMG_0042.jpg"])
def test_a_note_that_isnt_only_file_names_names_every_photo(note):
    from plugins.studio_page import read_veto
    v = read_veto(note, ["IMG_0042.jpg", "IMG_0043.jpg"])
    assert v.unclear and v.hides("IMG_0043.jpg")

def test_the_studio_page_says_why_a_session_offers_nothing(env):
    A, c, S, tmp, web = env
    switch_on(c)
    a = session_photo(A, "IMG_0001.jpg")
    session_photo(A, "IMG_0002.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [a]}, environ_base=E)
    set_veto(A, "the second one, please")
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    assert "Don't show note for" in page and "isn't specific file names, so none of its photos are offered" in page
    assert "IMG_0002.jpg" not in page
    assert "Not shown: the client&#39;s Don&#39;t show note isn&#39;t specific file names" in page
    set_veto(A, "IMG_0002.jpg")
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    assert "isn't specific file names" not in page and "Not shown" not in page

def test_today_warns_when_a_retouched_final_online_is_vetoed(env):
    A, c, S, tmp, web = env
    switch_on(c)
    session_photo(A, "IMG_0042.jpg")
    fin = session_photo(A, "IMG_0042-Edit.jpg", kind="final")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fin]}, environ_base=E)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)
    set_veto(A, "IMG_0042.jpg")
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

def test_an_edit_on_the_host_is_left_and_named_and_the_rest_still_goes_up(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/save", data={"site_about": "Words."}, environ_base=E)
    upload(c, S)
    about = web / "about" / "index.html"
    about.write_bytes(about.read_bytes().replace(b"Words.", b"Wordz."))
    c.post("/settings/studio-page/save", data={"site_about": "Other words.", "site_tagline": "New line"}, environ_base=E)
    upload(c, S)
    assert setting(A, "site_state") == "up" and "about/index.html" in setting(A, "site_note")
    assert b"Wordz." in about.read_bytes() and b"New line" in (web / "index.html").read_bytes()
    upload(c, S)
    assert b"Wordz." in about.read_bytes() and "about/index.html" in setting(A, "site_note")

def _up_with_form(A, c, S):
    switch_on(c)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    return S.inquiry_public(setting(A, "site_inquiry_key"))

def test_take_down_collects_then_removes_only_the_folders_own_files(env):
    A, c, S, tmp, web = env
    pub = _up_with_form(A, c, S)
    inq = web / "inquiries"
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    (inq / ".rate").write_text("{}")
    (inq / ("." + "ab" * 16 + ".tmp")).write_bytes(b"half")
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "down" and not inq.exists()
    con = A.connect()
    assert [json.loads(r[0])["name"] for r in con.execute("SELECT fields FROM studio_inquiry_review")] == ["Ada Lovelace"]
    assert not con.execute("SELECT 1 FROM studio_files").fetchone()
    con.close()

def test_take_down_keeps_the_htaccess_while_another_file_stays(env):
    A, c, S, tmp, web = env
    _up_with_form(A, c, S)
    inq = web / "inquiries"
    (inq / "notes.txt").write_text("the host's own note")
    (inq / ("ef" * 16 + ".bin")).mkdir()
    (inq / ".rate").write_text("{}")
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert sorted(p.name for p in inq.iterdir()) == [".htaccess", "ef" * 16 + ".bin", "notes.txt"]
    assert "notes.txt" in setting(A, "site_note") and ".htaccess stays" in setting(A, "site_note")

def test_take_down_leaves_inquiries_it_could_not_collect(env):
    A, c, S, tmp, web = env
    pub = _up_with_form(A, c, S)
    waiting = put_inquiry(web, S.seal_inquiry(pub, GOOD))
    key = setting(A, "site_inquiry_key")
    con = A.connect()
    con.execute("DELETE FROM settings WHERE key='site_inquiry_key'")
    con.commit(); con.close()
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert waiting.is_file() and (web / "inquiries" / ".htaccess").is_file()
    assert "1 inquiry still waiting" in setting(A, "site_note") and not (web / "index.html").exists()
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('site_inquiry_key', ?)", (key,))
    con.commit(); con.close()
    assert A.app.config["studio_page_collect"]()["added"] == 1
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert not (web / "inquiries").exists()

def test_an_upload_without_the_form_retires_the_folder(env):
    A, c, S, tmp, web = env
    pub = _up_with_form(A, c, S)
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    c.post("/settings/studio-page/save", data={"site_about": "Words."}, environ_base=E)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert not (web / "inquiry.php").exists() and not (web / "inquiries").exists()
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM studio_inquiry_review").fetchone()[0] == 1
    con.close()

def test_a_flood_is_taken_a_check_at_a_time_and_today_says_check_for_spam(env):
    A, c, S, tmp, web = env
    pub = _up_with_form(A, c, S)
    collect = lambda: A.app.config["studio_page_collect"](limit=25)
    for i in range(30):
        put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, name=f"Sender {i}")))
    assert collect() == dict(added=25, dropped=0, bad=0)
    assert len(list((web / "inquiries").glob("*.bin"))) == 5
    assert "more waiting for the next check" in setting(A, "site_inq_note")
    assert "Many website inquiries at once; check for spam" in c.get("/", environ_base=E).get_data(as_text=True)
    assert collect() == dict(added=5, dropped=0, bad=0)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM households WHERE source='Website'").fetchone()
    ids = [str(r[0]) for r in con.execute("SELECT id FROM studio_inquiry_review ORDER BY id LIMIT 28")]
    con.close()
    assert "30 website inquiries to review: Add or Spam" in c.get("/", environ_base=E).get_data(as_text=True)
    c.post("/settings/studio-page/inquiries/review", data={"do": "spam", "id": ids}, environ_base=E)
    c.post("/settings/studio-page/inquiries/review", data={"do": "add_all"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM households WHERE source='Website'").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM studio_inquiries WHERE outcome='spam'").fetchone()[0] == 28
    hist = [r[0] for r in con.execute("SELECT action FROM audit")]
    con.close()
    assert "Website inquiries: 28 marked as spam and removed" in hist and "Sender" not in json.dumps(hist)

def test_a_flood_past_50_a_day_still_reaches_the_review_list(env):

    A, c, S, tmp, web = env
    pub = _up_with_form(A, c, S)
    for i in range(60):
        put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, name=f"Sender {i}")))
    assert A.app.config["studio_page_collect"]()["added"] == 60
    for i in range(5):
        put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, name=f"Later {i}")))
    assert A.app.config["studio_page_collect"]()["added"] == 5
    assert not list((web / "inquiries").glob("*.bin"))
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "65 website inquiries to review" in today and "Many website inquiries at once; check for spam" in today

def test_builds_and_thumbnails_are_not_kept_in_the_clear(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/photos/add", data={"photos": [(io.BytesIO(jpeg_with_metadata()), "p.jpg")]},
           content_type="multipart/form-data", environ_base=E)
    c.post("/settings/studio-page/preview", environ_base=E)
    assert (S.SITE_ROOT / "preview" / "index.html").is_file()
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert not (S.SITE_ROOT / "ready").exists() and not (S.SITE_ROOT / "preview").exists()
    c.post("/settings/studio-page/preview", environ_base=E)
    con = A.connect()
    pid = con.execute("SELECT id FROM studio_photos").fetchone()[0]
    con.close()
    assert c.get(f"/settings/studio-page/photos/{pid}.jpg", environ_base=E).status_code == 200
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(proceed) or True)
    try:
        c.post("/settings/studio-page/upload", environ_base=E)
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert (S.SITE_ROOT / "ready" / "index.html").is_file()
    import datakey
    key = datakey._state["dk"]
    datakey.lock()
    try:
        assert not (S.SITE_ROOT / "ready").exists() and not (S.SITE_ROOT / "preview").exists() and not S.READY
    finally:
        datakey.set_unlocked(key, ready=True)
    held[0]()
    assert setting(A, "site_state") == "awaiting"
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert not (S.SITE_ROOT / "ready").exists() and not (S.SITE_ROOT / "preview").exists()

def test_collection_removes_unfinished_writes_older_than_a_day(env):
    A, c, S, tmp, web = env
    _up_with_form(A, c, S)
    old, new = web / "inquiries" / ("." + "1" * 32 + ".tmp"), web / "inquiries" / ("." + "2" * 32 + ".tmp")
    other = web / "inquiries" / ".keep.tmp"
    for p in (old, new, other):
        p.write_bytes(b"x")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    os.utime(other, (time.time() - 2 * 86400,) * 2)
    A.app.config["studio_page_collect"]()
    assert not old.exists() and new.exists() and other.exists()

def test_the_built_form_and_script_carry_this_installs_salt_and_work(env):
    A, c, S, tmp, web = env
    _up_with_form(A, c, S)
    php = (web / "inquiry.php").read_text()
    salt = setting(A, "site_inquiry_salt")
    assert len(salt) == 32 and f"$SALT = '{salt}';" in php and f"$WORK = {S.INQ_WORK};" in php and "unbuilt" not in php
    assert f'data-work="{S.INQ_WORK}"' in (web / "contact" / "index.html").read_text()
    assert salt not in "".join(p.read_text(errors="replace") for p in web.rglob("*") if p.is_file() and p.suffix != ".php")

def test_inquiry_php_wants_the_proof_of_work_and_keeps_a_resent_one_once(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    blob = S.seal_inquiry(pub, GOOD)
    w = S.solve_work(blob, WORK)
    bad = next(f"{i:016x}" for i in range(1000) if not S.work_ok(blob, f"{i:016x}", WORK))
    d = base64.urlsafe_b64encode(blob).rstrip(b"=")
    assert post(b"w=" + bad.encode() + b"&d=" + d, addr="198.51.100.7") == 400
    assert post(b"d=" + d, addr="198.51.100.7") == 400
    assert post(b"w=" + w.encode() + b"&d=" + d, addr="198.51.100.7") == 204
    assert post(b"w=" + w.encode() + b"&d=" + d, addr="198.51.100.8") == 204
    assert len(list((site / "inquiries").glob("*.bin"))) == 1

def test_inquiry_php_counts_ipv4_by_address_and_ipv6_by_64(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    assert [post(_d(S, pub), addr=f"2001:db8:0:{n}::1") for n in range(3)] == [204] * 3
    assert [post(_d(S, pub), addr=f"198.51.100.{n}") for n in range(3)] == [204] * 3
    assert [post(_d(S, pub), addr="::ffff:198.51.100.9") for _ in range(6)].count(204) == 5
    rate = (site / "inquiries" / ".rate").read_text()
    assert "198.51" not in rate and "2001" not in rate

def test_a_full_tracker_makes_room_instead_of_refusing_everyone(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    now = int(time.time())
    seen = {f"a{i:016x}": [now - 3000 + i % 2000] for i in range(5000)}
    (site / "inquiries" / ".rate").write_text(json.dumps(seen))
    assert post(_d(S, pub), addr="203.0.113.5") == 204
    kept = json.loads((site / "inquiries" / ".rate").read_text())
    assert 4000 <= len(kept) <= 4001 and f"a{0:016x}" not in kept and f"a{1999:016x}" in kept

def _font():
    from plugins import gallery as G
    return (G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()

def _with_table(flavor, size):
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables.DefaultTable import DefaultTable
    f = TTFont(io.BytesIO(_font()))
    t = DefaultTable("zzzz")
    t.data = b"\0" * size
    f["zzzz"] = t
    f.flavor = flavor
    b = io.BytesIO()
    f.save(b)
    return b.getvalue()

@pytest.mark.parametrize("flavor", ["woff2", "woff"])
def test_a_font_that_unpacks_too_large_is_refused(flavor):
    import settings_io
    bomb = _with_table(flavor, 30 * 1024 * 1024)
    assert len(bomb) < settings_io.FONT_MAX
    with pytest.raises(ValueError):
        settings_io.clean_font(bomb)

def test_a_woff2_that_understates_its_size_is_stopped_while_unpacking():
    import brotli
    import settings_io
    body = brotli.compress(b"\0" * (40 * 1024 * 1024))
    entry = bytes([63]) + b"zzzz" + bytes([0x81, 0x00])
    head = b"wOF2" + b"\x00\x01\x00\x00" + struct.pack(">IHHII", 48 + len(entry) + len(body), 1, 0, 1000, len(body)) \
        + b"\0" * 24
    with pytest.raises(ValueError, match="too large"):
        settings_io._font_unpacked(head + entry + body)

@pytest.mark.parametrize("flavor", ["woff2", "woff", None])
def test_only_the_tables_for_drawing_text_are_kept(flavor):
    import settings_io
    from fontTools.ttLib import TTFont
    out, fam = settings_io.clean_font(_with_table(flavor, 1000))
    kept = TTFont(io.BytesIO(out))
    assert "zzzz" not in kept and "cmap" in kept and fam.startswith("Cormorant Garamond")
    assert set(kept.keys()) - {"GlyphOrder"} <= settings_io.FONT_TABLES

def test_settings_import_refuses_a_font_that_unpacks_too_large():
    import settings_io
    blob = base64.b64encode(_with_table("woff2", 30 * 1024 * 1024)).decode()
    with pytest.raises(settings_io.ImportProblem, match="brand_font_400"):
        settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "settings": {"brand_font_400": blob}}), {})

def test_joiners_stay_in_the_gallery_mark_word():
    import settings_io
    assert settings_io.clean_look("", "", "", "a‍b‌c​d", 20)["proof_mark_text"] == "a‍b‌cd"

def test_preview_not_found_page_names_the_preview_and_robots_leaves_the_galleries_to_themselves(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/preview", environ_base=E)
    lost = (S.SITE_ROOT / "preview" / "404.html").read_text()
    assert 'href="/studio-page/preview/assets/site.css"' in lost
    assert 'href="/assets' not in lost and 'href="/"' not in lost
    r = c.get("/studio-page/preview/nothing-here", environ_base=E)
    assert r.status_code == 404 and b'href="/studio-page/preview/assets/site.css"' in r.data
    upload(c, S)
    assert "Disallow: /g/" not in (web / "robots.txt").read_text()
    assert 'href="/assets/site.css"' in (web / "404.html").read_text()

def test_the_studio_page_warns_of_a_photo_without_a_description(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/photos/add", data={"photos": [(io.BytesIO(jpeg_with_metadata()), "p.jpg")]},
           content_type="multipart/form-data", environ_base=E)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "1 photo on the page has no description" in page and "No description yet" in page
    con = A.connect()
    pid = con.execute("SELECT id FROM studio_photos").fetchone()[0]
    con.close()
    c.post(f"/settings/studio-page/photos/{pid}/edit", data={"do": "alt", "alt": "A boat at dawn"}, environ_base=E)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "no description" not in page and "No description yet" not in page
