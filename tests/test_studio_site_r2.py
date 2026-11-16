import base64
import json
import os
import re
from urllib.parse import urljoin

import pytest

from test_studio_page import E, env, session_photo, setting, switch_on, wait
from test_studio_site_release import WORK, php_site, upload
from test_studio_inquiries import GOOD, put_inquiry

NAMES = ["IMG_0042.jpg", "IMG_0043.jpg", "IMG_0044.jpg", "IMG_0042-Edit.jpg"]

def q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def ex(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def on_host(A, web, sid):
    return [n for (n,) in q(A, "SELECT name FROM studio_files WHERE session_id=?", (sid,)) if (web / n).exists()]

def host_photos(web):
    return sorted(p.name for p in (web / "photos").iterdir()) if (web / "photos").is_dir() else []

def test_round_one_veto_forms_still_hold_back():
    from plugins.studio_page import read_veto
    v = read_veto("IMG_0042.jpg", NAMES)
    assert not v.unclear and v.hides("IMG_0042-Edit.jpg") and not v.hides("IMG_0043.jpg")
    for note in ("photo 3", "#2", "2, 3", "IMG_0042.jpg, photo 3", "the one by the lake"):
        assert read_veto(note, NAMES).unclear, note

def test_a_shared_veto_tail_is_not_a_prefix_match():
    from plugins.studio_page import read_veto
    assert not read_veto("IMG_004", NAMES).hides("IMG_0043.jpg") or read_veto("IMG_004", NAMES).unclear

@pytest.mark.parametrize("note", ["IMG_0042 and IMG_0043", "IMG_0042.jpg IMG_0043.jpg", "IMG_0042 - IMG_0043",
                                  "IMG_0042 (and IMG_0043)"])
def test_two_names_in_one_entry_hold_both_back(note):
    from plugins.studio_page import read_veto
    v = read_veto(note, NAMES)
    assert v.unclear or (v.hides("IMG_0043.jpg") and v.hides("IMG_0042-Edit.jpg"))

@pytest.mark.parametrize("note", ["IMG_0042 or any with her eyes closed", "IMG_0042 thru 45", "IMG_0042 not 43 either"])
def test_a_name_with_words_after_it_holds_the_session(note):
    from plugins.studio_page import read_veto
    assert read_veto(note, NAMES).unclear

def test_two_names_in_one_entry_never_reach_the_host(env):
    A, c, S, tmp, web = env
    switch_on(c)
    ids = {n: session_photo(A, n, kind="final" if "Edit" in n else "export", color=(i * 60, 90, 200 - i * 50))
           for i, n in enumerate(NAMES)}
    ex(A, "UPDATE sessions SET veto=? WHERE id=1", ("IMG_0042 and IMG_0043",))
    c.post("/settings/studio-page/photos/from/1", data={"file": [ids["IMG_0043.jpg"], ids["IMG_0042-Edit.jpg"]]},
           environ_base=E)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert on_host(A, web, 1) == []

def test_an_approved_upload_leaves_out_a_client_who_declined_while_it_waited(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(proceed) or True)
    try:
        c.post("/settings/studio-page/upload", environ_base=E)
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert setting(A, "site_state") == "awaiting"
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    held[0]()
    wait(S)
    assert on_host(A, web, 1) == [] and not [n for n in host_photos(web) if n.startswith("photo-")]

def test_a_declined_photo_left_on_the_host_keeps_today_reminding(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE session_id=1")
    b = bytearray((web / name).read_bytes())
    b[-3] ^= 0xFF
    (web / name).write_bytes(bytes(b))
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    upload(c, S)
    assert (web / name).exists()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

def test_switching_the_page_off_keeps_the_stale_photo_reminder(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    c.post("/settings/studio-page/switch", data={}, environ_base=E)
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    assert on_host(A, web, 1)
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

def test_the_review_link_on_today_leads_to_the_list(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    assert A.app.config["studio_page_collect"]()["added"] == 1
    c.post("/settings/studio-page/switch", data={}, environ_base=E)
    today = c.get("/", environ_base=E).get_data(as_text=True)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "to review" not in today or 'id="inquiries"' in page

def test_one_ipv6_subscriber_cannot_fill_the_folder(php_site, env):
    site, post = php_site
    A, c, S, tmp, web = env
    _, pub = S.new_inquiry_key()

    def d():
        blob = S.seal_inquiry(pub, GOOD)
        return b"w=" + S.solve_work(blob, WORK).encode() + b"&d=" + base64.urlsafe_b64encode(blob).rstrip(b"=")
    codes = [post(d(), addr=f"2001:db8:77:ab{n:02x}::{h + 1:x}") for n in range(40) for h in range(5)]
    assert codes.count(204) <= 100, codes.count(204)
    assert post(d(), addr="198.51.100.7") == 204

def test_preview_not_found_page_is_styled_at_any_depth(env):
    A, c, S, tmp, web = env
    switch_on(c)
    ex(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('site_about', 'About us')")
    c.post("/settings/studio-page/preview", environ_base=E)
    for path in ("/studio-page/preview/nope", "/studio-page/preview/about/nope.html",
                 "/studio-page/preview/photos/x.jpg"):
        r = c.get(path, environ_base=E)
        assert r.status_code == 404
        css = re.search(r'<link[^>]+href="([^"]*site\.css)"', r.get_data(as_text=True)).group(1)
        assert c.get(urljoin("https://localhost" + path, css), environ_base=E).status_code == 200, (path, css)

def test_each_entry_must_be_exactly_one_of_the_sessions_names():
    from plugins.studio_page import read_veto
    names = NAMES + ["IMG_0042v2.jpg", "IMG_00421.jpg", "img1.jpg", "img10.jpg"]
    for note in ("IMG_0042", "img_0042.JPG", "IMG_0042.jpg (photo 4)", "IMG_0042.jpg (photo 4)\nIMG_0043.jpg",
                 "IMG_0042.jpg, IMG_0043"):
        assert not read_veto(note, names).unclear, note
    for note in ("IMG_0042 - IMG_0045", "IMG_0042 (and the next one)", "IMG_0042, IMG_0043 and IMG_0044",
                 "IMG_00", "IMG_0042.jpg.jpg x"):
        assert read_veto(note, names).unclear, note
    v = read_veto("IMG_0042", names)
    assert v.hides("IMG_0042v2.jpg") and v.hides("IMG_0042-Edit.jpg")
    assert not v.hides("IMG_00421.jpg") and not v.hides("IMG_0043.jpg")
    assert not read_veto("img1", names).hides("img10.jpg")

def _blob(S, pub):
    blob = S.seal_inquiry(pub, GOOD)
    return blob, b"w=" + S.solve_work(blob, WORK).encode() + b"&d=" + base64.urlsafe_b64encode(blob).rstrip(b"=")

def test_a_full_folder_makes_room_from_the_busiest_sender(php_site):
    import hashlib
    import hmac
    import ipaddress
    from plugins import studio_page as S
    site, post = php_site
    inq = site / "inquiries"
    _, pub = S.new_inquiry_key()
    blob = S.seal_inquiry(pub, GOOD)
    for g in range(20):
        for i in range(10):
            p = inq / f"{g:016x}-{g:08x}{i:024x}.bin"
            p.write_bytes(blob)
            os.utime(p, (1_000_000 + i, 1_000_000 + i))
    assert post(_blob(S, pub)[1], addr="198.51.100.20") == 204
    left = sorted(p.name for p in inq.glob("*.bin"))
    assert len(left) == 200 and sum(1 for n in left if n.endswith(f"{9:024x}.bin")) == 19

    grp = hmac.new(("cd" * 16).encode(), ipaddress.ip_address("198.51.100.30").packed, hashlib.sha256).hexdigest()[:16]
    for p in list(inq.glob("*.bin"))[:10]:
        p.unlink()
    for i in range(10):
        (inq / f"{grp}-{i:032x}.bin").write_bytes(blob)
    assert post(_blob(S, pub)[1], addr="198.51.100.30") == 429
    assert post(_blob(S, pub)[1], addr="198.51.100.31") == 204

def test_ipv4_is_still_five_an_hour_from_one_address(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    codes = [post(_blob(S, pub)[1], addr="203.0.113.9") for _ in range(7)]
    assert codes == [204] * 5 + [429] * 2
    assert post(_blob(S, pub)[1], addr="203.0.113.10") == 204

def test_the_app_collects_files_named_by_sender(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, GOOD), name="0123456789abcdef-" + "ab" * 16 + ".bin")
    assert A.app.config["studio_page_collect"]()["added"] == 1
    assert not list((web / "inquiries").glob("*.bin"))

def _declined_and_edited(A, c, S, web):
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE session_id=1")
    b = bytearray((web / name).read_bytes())
    b[-3] ^= 0xFF
    (web / name).write_bytes(bytes(b))
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    upload(c, S)
    return name

def test_remove_it_anyway_takes_off_a_declined_photo_edited_on_the_host(env):
    A, c, S, tmp, web = env
    name = _declined_and_edited(A, c, S, web)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "Remove it anyway" in page and name in page
    assert c.post("/settings/studio-page/remove-file", data={"name": "index.html"}, environ_base=E).status_code == 302
    assert (web / "index.html").exists()
    c.post("/settings/studio-page/remove-file", data={"name": name}, environ_base=E)
    wait(S)
    assert not (web / name).exists() and on_host(A, web, 1) == []
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)
    assert (web / "index.html").exists()

def test_a_declined_photo_deleted_by_hand_on_the_host_is_forgotten_at_the_next_upload(env):
    A, c, S, tmp, web = env
    name = _declined_and_edited(A, c, S, web)
    (web / name).unlink()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)
    upload(c, S)
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_take_down_keeps_a_declined_edited_photo_listed(env):
    A, c, S, tmp, web = env
    name = _declined_and_edited(A, c, S, web)
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert (web / name).exists() and not (web / "index.html").exists()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)
    assert "Remove it anyway" in c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)

def test_switched_off_the_page_still_collects_and_offers_take_down(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    c.post("/settings/studio-page/switch", data={}, environ_base=E)
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    assert A.app.config["studio_page_collect"]()["added"] == 1
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert 'id="inquiries"' in page and "Take the site down" in page and "still online" in page
    assert 'name="site_about"' not in page and "studio_page_upload" not in page
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert not (web / "index.html").exists() and not (web / "inquiry.php").exists()
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "Take the site down" not in page and 'id="inquiries"' in page
    assert A.app.config["studio_page_collect"]() is None

def _review(A, c, S, web, *fields):
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    for f in fields:
        put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, **f)))
    assert A.app.config["studio_page_collect"]()["added"] == len(fields)
    return {f_["name"]: rid for rid, f_ in ((r[0], json.loads(r[1]))
                                           for r in q(A, "SELECT id, fields FROM studio_inquiry_review"))}

def review_page(c):
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    return page[page.index('id="inquiries"'):]

def test_review_flags_a_known_client_and_adds_to_it(env):
    A, c, S, tmp, web = env
    ex(A, "INSERT INTO people (household_id, name, role, email, phone) VALUES (1, 'Ann Lane', 'adult', 'Ann@Example.org', '(555) 010-7777')")
    ids = _review(A, c, S, web, dict(name="Ann L", email="ann@example.ORG"), dict(name="By Phone", email="", phone="555.010.7777"),
                  dict(name="New One", email="new@example.org"), dict(name="New Again", email="NEW@example.org"))
    sec = review_page(c)
    assert sec.count("Matches Lane") == 2 and "Add to Lane" in sec
    assert sec.count("as another inquiry waiting here") == 2
    h0 = q(A, "SELECT COUNT(*) FROM households")[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"id": ids["Ann L"], "add_to": "1"}, environ_base=E)
    assert q(A, "SELECT COUNT(*) FROM households")[0][0] == h0
    assert q(A, "SELECT COUNT(*) FROM sessions WHERE household_id=1 AND stage='inquiry'")[0][0] == 1
    c.post("/settings/studio-page/inquiries/review", data={"id": ids["New One"], "add_to": "1"}, environ_base=E)
    assert q(A, "SELECT COUNT(*) FROM sessions WHERE household_id=1 AND stage='inquiry'")[0][0] == 1

    sec = review_page(c)
    assert re.search(r'data-confirm="Add all 2 inquiries as new clients[^"]*match a client', sec)
    form = re.search(r'<form[^>]*data-confirm="Add all[^>]*>(.*?)</form>', sec, re.S).group(1)
    c.post("/settings/studio-page/inquiries/review", data={"do": "add_all", "id": re.findall(r'name="id" value="(\d+)"', form)},
           environ_base=E)
    assert q(A, "SELECT COUNT(*) FROM households")[0][0] == h0 + 2
    assert [r[0] for r in q(A, "SELECT fields FROM studio_inquiry_review")] and "By Phone" in review_page(c)

def test_spam_all_takes_only_what_was_shown_and_writes_one_line(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="Spam A"), dict(name="Spam B"))
    a0 = q(A, "SELECT COALESCE(MAX(id), 0) FROM audit")[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"do": "spam", "id": ids["Spam A"]}, environ_base=E)
    assert [r[0] for r in q(A, "SELECT action FROM audit WHERE id>?", (a0,))] == ["Website inquiry marked as spam and removed"]
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, name="Real Visitor")))
    A.app.config["studio_page_collect"]()
    a0 = q(A, "SELECT COALESCE(MAX(id), 0) FROM audit")[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"do": "spam_all", "id": [ids["Spam B"]]}, environ_base=E)
    assert len(q(A, "SELECT action FROM audit WHERE id>?", (a0,))) == 1
    assert [json.loads(r[0])["name"] for r in q(A, "SELECT fields FROM studio_inquiry_review")] == ["Real Visitor"]
    assert c.post("/settings/studio-page/inquiries/review", data={"do": "spam_all"}, environ_base=E).status_code == 302
    assert q(A, "SELECT COUNT(*) FROM studio_inquiry_review")[0][0] == 1
    assert "Spam all" not in review_page(c) and "Remove all" not in review_page(c)

def test_review_shows_a_missing_kind_and_a_cut_message_and_add_asks_the_kind(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="No Kind", type="", date="", message="x" * 5000))
    sec = review_page(c)
    assert "Kind of session: not given" in sec and "only the first 4000 were kept" in sec
    kinds = re.findall(r'<select id="rv-type-\d+" name="type"[^>]*>(.*?)</select>', sec)[0]
    first = re.search(r'<option value="([^"]+)"', kinds).group(1)
    other = re.findall(r'<option value="([^"]+)"', kinds)[-1]
    assert first != other
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": ids["No Kind"], "type": other}, environ_base=E)
    (t, sid), = q(A, "SELECT s.type, s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.source='Website'")
    assert t == other
    assert "only its first 4000 characters" in q(A, "SELECT summary FROM log WHERE session_id=?", (sid,))[0][0]

def test_a_file_edited_on_the_host_is_named_as_such_when_it_blocks_an_upload(env):
    from test_studio_page import add_own, jpeg_with_metadata
    A, c, S, tmp, web = env
    switch_on(c)
    add_own(c, jpeg_with_metadata())
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE name LIKE 'photos/%'")
    (pid,), = q(A, "SELECT id FROM studio_photos")
    with open(web / name, "ab") as f:
        f.write(b"edited")
    c.post(f"/settings/studio-page/photos/{pid}/edit", data={"do": "remove"}, environ_base=E)
    upload(c, S)
    assert (web / name).exists()
    add_own(c, jpeg_with_metadata())
    (web / "robots.txt").unlink()
    ex(A, "DELETE FROM studio_files WHERE name='robots.txt'")
    (web / "robots.txt").write_text("someone else's")
    upload(c, S)
    err = setting(A, "site_error")
    assert "weren't put there by Lumen Aureum" in err and "robots.txt" in err
    assert "went up with an earlier upload but was changed on your web host since" in err and name in err
    assert err.index("robots.txt") < err.index("went up with")

def test_a_variable_own_font_declares_its_weight_range(tmp_path):
    import io
    import settings_io
    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables._f_v_a_r import Axis
    from plugins import studio_page as SP
    from plugins import gallery as G
    f = TTFont(io.BytesIO((G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()))
    fv = newTable("fvar")
    ax = Axis()
    ax.axisTag, ax.minValue, ax.defaultValue, ax.maxValue, ax.axisNameID = "wght", 300, 400, 700, 256
    fv.axes, fv.instances = [ax], []
    f["fvar"] = fv
    b = io.BytesIO()
    f.save(b)
    var, _ = settings_io.clean_font(b.getvalue())
    static = (G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()
    assert settings_io.font_weights(var, 400) == "300 700" and settings_io.font_weights(static, 400) == "400"
    conf = dict(name="Studio", owner="Studio", tagline="", intro="", description="", about="", email="", phone="",
                address="", site_url="", site_path="/", logo=None, palette=settings_io.palette("#0d0c0b"), dark=True,
                font="own", credit=False, favicon=None, year=2026, htaccess=False, php=False, inquiry=None,
                own_font={400: var, 300: static})
    SP.build_site(tmp_path / "site", conf, [])
    css = (tmp_path / "site" / "assets" / "site.css").read_text()
    assert "font-weight: 300 700; font-display: swap; src: url(own-400.woff2)" in css
    assert "font-weight: 300; font-display: swap; src: url(own-300.woff2)" in css
    page = (G.ASSETS / "viewer.html").read_text()
    assert "weight: W(T.wr, \"400\")" in page and "weight: W(T.wl, \"300\")" in page
