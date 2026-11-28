import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, env

NY = ZoneInfo("America/New_York")

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _z(dt):
    return dt.astimezone(timezone.utc).strftime("%H:%M")

def test_c10_a_session_in_the_repeated_hour_keeps_its_length():
    import calfeed
    e = calfeed.entry(1, "Family", "2026-11-01", "01:30", 60, NY)
    assert (_z(e["start"]), _z(e["end"])) == ("05:30", "06:30")
    e = calfeed.entry(1, "Family", "2026-11-01", "00:30", 120, NY)
    assert (e["end"].timestamp() - e["start"].timestamp()) == 7200

def test_c10_a_skipped_start_time_lands_after_the_gap_with_its_full_length():
    import calfeed
    for minutes in (15, 30, 90):
        e = calfeed.entry(1, "Family", "2027-03-14", "02:30", minutes, NY)
        assert e["start"].strftime("%H:%M") == "03:30" and _z(e["start"]) == "07:30"
        assert e["end"].timestamp() - e["start"].timestamp() == minutes * 60
    assert calfeed.skipped_time("2027-03-14", "02:30", NY)
    assert not calfeed.skipped_time("2027-03-14", "03:30", NY)
    assert not calfeed.skipped_time("2026-11-01", "01:30", NY)
    assert not calfeed.skipped_time("2027-03-14", "", NY)

def test_c10_the_feed_and_send_to_calendar_both_carry_the_real_length(env):
    A, c = env
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'America/New_York')")
    A.apply_settings()
    run(A, """INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, duration_min)
              VALUES (1, 1, 'family', 'booked', '2099-03-08', '02:30', 90)""")
    import calfeed
    with A.app.test_request_context():
        got = A.calendar_feed_entries()
    pubs = [calfeed.public(e_) for e_ in got]
    assert len(pubs) == 1 and pubs[0]["end"].timestamp() - pubs[0]["start"].timestamp() == 90 * 60
    text = calfeed.one_event(got[0], "x")
    assert "DTSTART:20990308T073000Z" in text and "DTEND:20990308T090000Z" in text

def test_c10_the_owner_is_warned_when_the_start_time_does_not_exist_that_day(env):
    A, c = env
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'America/New_York')")
    A.apply_settings()
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'family', 'booked')")
    c.post("/session/1/quick", data={"act": "date", "shoot_date": "2099-03-08", "start_time": "02:30"}, environ_base=E)
    with c.session_transaction() as s_:
        notice = s_.get("notice", "")
    assert "doesn't exist" in notice and "clocks go forward" in notice
    c.post("/session/1/quick", data={"act": "date", "shoot_date": "2099-03-09", "start_time": "02:30"}, environ_base=E)
    with c.session_transaction() as s_:
        assert "doesn't exist" not in s_.get("notice", "")

def _import(c, body):
    import io
    return c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(body), "s.json")},
                  content_type="multipart/form-data", environ_base=E)

def _import_error(c):
    with c.session_transaction() as s_:
        return (s_.get("import_result") or {}).get("error")

def test_d5_import_reads_as_much_as_export_can_make(env):
    A, c = env
    import json
    import settings_io
    assert settings_io.FILE_MAX > 2 * settings_io.FONT_TEXT_MAX + 2 * settings_io.LOGO_TEXT_MAX

    body = json.dumps({"lumen_aureum_settings": 1, "settings": {"studio_name": "Lantern Hill"}})
    body = (body[:-1] + " " * 1_400_000 + "}").encode()
    _import(c, body)
    assert _import_error(c) is None
    con = A.connect()
    assert con.execute("SELECT value FROM settings WHERE key='studio_name'").fetchone()[0] == "Lantern Hill"
    con.close()

def test_d5_a_file_too_big_to_be_an_export_is_refused_plainly(env):
    A, c = env
    import settings_io
    _import(c, b'{"lumen_aureum_settings": 1, "settings": {}' + b" " * settings_io.FILE_MAX + b"}")
    assert "too big" in (_import_error(c) or "")

def test_d6_removing_a_big_import_records_every_deletion_in_one_write(env, monkeypatch):
    A, c = env
    from test_37_155 import _upload, _token, _confirm
    rows = "".join(f"Client {i:03d},c{i:03d}@example.com\n" for i in range(60))
    _confirm(A, c, _token(_upload(c, ("Name,Email\n" + rows).encode())))
    con = A.connect()
    added = con.execute("SELECT COUNT(*) FROM households WHERE name LIKE 'Client %'").fetchone()[0]
    bid = A.json.loads(con.execute("SELECT value FROM settings WHERE key='client_imports'").fetchone()[0])[-1]["id"]
    con.close()
    assert added == 60
    writes = []
    real = A._write_plain
    monkeypatch.setattr(A, "_write_plain", lambda p_, t_: (writes.append(p_), real(p_, t_))[1])
    c.post(f"/clients/import/batch/{bid}/remove", environ_base=E)
    assert writes.count(A.DELETIONS_JSON) == 1
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM households WHERE name LIKE 'Client %'").fetchone()[0] == 0
    with A.app.test_request_context():
        key, good, legacy, forged = A._sort_deletions(con)
    con.close()
    assert len([d_ for d_ in good if d_.get("kind", "client") == "client"]) == 60 and not forged
    with A.app.test_request_context():
        A._record_deletions([(d_["id"], d_["created"]) for d_ in good])
        con = A.connect()
        assert len(A._sort_deletions(con)[1]) == 60
        con.close()

@pytest.fixture
def penv(tmp_path, monkeypatch):
    from helpers import fresh_modules, signed_in_client
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Sample')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'shot')", (hid,)).lastrowid
    con.commit()
    con.close()
    return A, c, sid

def _jpeg(color, size=(64, 48)):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG", quality=90)
    return b.getvalue()

def test_d7_a_shortened_name_keeps_its_extension_and_its_own_identity():
    import photostore
    a = photostore.clean_name("Smith wedding " + "x" * 220 + " first look.jpg")
    b = photostore.clean_name("Smith wedding " + "x" * 220 + " first dance.jpg")
    assert len(a) <= 200 and len(b) <= 200 and a.endswith(".jpg") and b.endswith(".jpg") and a != b
    assert photostore.clean_name(a) == a
    assert photostore.clean_name("short.jpg") == "short.jpg"

def test_d7_two_long_names_that_start_the_same_are_two_photos(penv):
    A, c, sid = penv
    from test_photo_uploads import upload
    stem = "Smith wedding " + "x" * 220
    one_, two_ = _jpeg((200, 10, 10)), _jpeg((10, 10, 200))
    n_ = max(len(one_), len(two_))
    one_, two_ = one_ + b"\0" * (n_ - len(one_)), two_ + b"\0" * (n_ - len(two_))
    upload(c, sid, stem + " first look.jpg", one_, kind="export")
    upload(c, sid, stem + " first dance.jpg", two_, kind="export")
    con = A.connect()
    names = [r_[0] for r_ in con.execute("SELECT name FROM photo_files WHERE session_id=?", (sid,))]
    con.close()
    assert len(names) == 2 and all(n_.endswith(".jpg") for n_ in names)

def test_d7_bringing_home_a_different_photo_under_a_name_already_here_is_refused(penv):
    A, c, sid = penv
    import photostore
    one_, two_ = _jpeg((200, 10, 10)), _jpeg((10, 10, 200))
    n_ = max(len(one_), len(two_))
    one_, two_ = one_ + b"\0" * (n_ - len(one_)), two_ + b"\0" * (n_ - len(two_))
    with A.app.app_context():
        assert A.store_photo(sid, "export", "a.jpg", one_) == "added"
        assert A.store_photo(sid, "export", "a.jpg", one_) == "had"
        with pytest.raises(photostore.UploadError):
            A.store_photo(sid, "export", "a.jpg", two_)

BAD_SERVERS = ("https://dav.example.com:abc/", "https://dav.example.com:99999/", "https://[::1/dav/",
               "https://dav.example.com/my calendars/")

def test_d8_fetch_says_a_malformed_address_plainly():
    import calsend as S
    for url_ in BAD_SERVERS:
        with pytest.raises(S.SendError) as e:
            S.fetch("PROPFIND", url_)
        assert "isn't a web address" in e.value.words, url_

def test_d8_a_malformed_caldav_address_is_a_plain_refusal_not_a_500(env):
    A, c = env
    from test_calsend_plugin import _on, _page
    _on(c)
    for url_ in BAD_SERVERS[:3]:
        r = c.post("/settings/calendar-send/caldav", environ_base=E,
                   data={"service": "other", "server": url_, "user": "me", "password": "app-pw"})
        assert r.status_code == 302, (url_, r.status_code)
        page = _page(c, r.location)
        assert "isn&#39;t a web address this can use" in page and "Nothing was saved." in page, url_

def test_d10_an_excel_sep_line_names_the_separator_and_is_not_a_row():
    import client_csv as CC
    header, rows, _enc = CC.read(b"sep=;\r\nName;Email;Phone\r\nSep Test;sep.test@example.com;503 555 0170\r\n")
    assert header == ["Name", "Email", "Phone"] and rows == [["Sep Test", "sep.test@example.com", "503 555 0170"]]
    header, rows, _enc = CC.read(b"\xef\xbb\xbfSEP=,\nName,Email\nAva,a@example.com\n")
    assert header == ["Name", "Email"] and rows == [["Ava", "a@example.com"]]

def test_d10_semicolons_inside_a_quoted_header_do_not_choose_the_separator():
    import client_csv as CC
    header, rows, _enc = CC.read(b'"Name (last; first; middle)",Email\n"Doe; Jane; Q",jane.semi@example.com\n')
    assert header == ["Name (last; first; middle)", "Email"] and rows == [["Doe; Jane; Q", "jane.semi@example.com"]]

def test_d10_rows_with_extra_cells_are_named_not_dropped_silently(env):
    A, c = env
    import client_csv as CC
    from test_37_155 import _upload
    raw = (b"First Name,Last Name,Email,Phone,Address,Notes,Birthday\n"
           b"Rag,Ged,rag@example.com\n"
           b"Too,Many,many@example.com,1,2,3,4,5,6,7\n"
           b"Fine,Row,fine@example.com,,,,,,\n")
    assert CC.read_full(raw)[3] == [3]
    loc = _upload(c, raw)
    page = c.get(loc, environ_base=E).get_data(as_text=True)
    assert 'id="import-extra-cells"' in page and "Row 3 has more cells than the first row has columns" in page
    loc = _upload(c, b"Name,Email\nAva,a@example.com\n")
    assert "import-extra-cells" not in c.get(loc, environ_base=E).get_data(as_text=True)

def _png(color, alpha=False, exif=False):
    import io
    from PIL import Image
    im = Image.new("RGBA" if alpha else "RGB", (80, 60), color + ((0,) if alpha else ()))
    b = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x0112] = 6
        ex[0x010F] = "SecretCam"
        kw["exif"] = ex.tobytes()
    im.save(b, "PNG", **kw)
    return b.getvalue()

def _build(A, c, sid):
    import time
    r = c.post(f"/session/{sid}/gallery/build", data={"from": "uploads"}, environ_base=E)
    assert r.status_code == 302
    for _ in range(400):
        st = c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    return st

def _manifest(A, sid):
    import json
    G = A.app.config["gallery_module"]
    con = A.connect()
    gid, key, ekey = con.execute("SELECT s.gallery_id, g.key, g.ekey FROM sessions s JOIN galleries g ON "
                                 "g.session_id=s.id WHERE s.id=?", (sid,)).fetchone()
    con.close()
    k_ = ekey or key
    open_ = lambda n_: G.open_any(k_, (G.OUT_ROOT / gid / n_).read_bytes(), n_)[0]
    return json.loads(open_("manifest.bin")), open_

def test_d1_pngs_and_any_named_jpegs_are_built_and_the_originals_kept(penv):
    A, c, sid = penv
    import io
    import photostore
    from PIL import Image
    from test_photo_uploads import upload
    pngs = {"edit-1.png": _png((200, 30, 30)), "edit-2.PNG": _png((30, 200, 30), alpha=True),
            "edit-3.png": _png((30, 30, 200), exif=True)}
    for n_, b_ in pngs.items():
        upload(c, sid, n_, b_, kind="export")
    upload(c, sid, "edit-4.jpg", _jpeg((90, 90, 90)), kind="export")
    upload(c, sid, "edit-5", _jpeg((10, 90, 90)), kind="export")
    upload(c, sid, "edit-6.jpeg.tmp", _jpeg((90, 10, 90)), kind="export")
    st = _build(A, c, sid)
    assert st["state"] == "done" and not st.get("error"), st
    con = A.connect()
    assert con.execute("SELECT n_files FROM galleries WHERE session_id=?", (sid,)).fetchone()[0] == 6
    for r_ in photostore.files(con, sid, "export", done_only=True):
        if r_["name"] in pngs:
            assert photostore.plain_bytes(r_) == pngs[r_["name"]]
    con.close()
    man, open_ = _manifest(A, sid)
    by = {it["n"]: it for it in man["items"]}
    assert set(by) == set(pngs) | {"edit-4.jpg", "edit-5", "edit-6.jpeg.tmp"}
    for n_ in pngs:
        web = open_(by[n_]["f"])
        assert web[:3] == b"\xff\xd8\xff"
        with Image.open(io.BytesIO(web)) as im:
            assert im.format == "JPEG" and not im.getexif() and "icc_profile" not in im.info
            if n_ == "edit-2.PNG":
                assert im.convert("RGB").getpixel((10, 10))[0] > 240
            if n_ == "edit-3.png":
                assert im.size == (60, 80)
    assert b"SecretCam" not in open_(by["edit-3.png"]["f"])

def test_d1_a_file_that_cannot_go_in_is_named_on_the_page_and_in_history_after_a_reload(penv):
    A, c, sid = penv
    from test_photo_uploads import upload
    for i in range(4):
        upload(c, sid, f"ok-{i}.jpg", _jpeg((40 * i, 50, 60)), kind="export")
    with A.app.app_context():
        A.store_photo(sid, "export", "broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    st = _build(A, c, sid)
    assert st["state"] == "done" and "broken.png" in (st.get("error") or ""), st
    con = A.connect()
    hid = con.execute("SELECT household_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    assert con.execute("SELECT n_files FROM galleries WHERE session_id=?", (sid,)).fetchone()[0] == 4
    hist = [r_[0] for r_ in con.execute("SELECT action FROM audit WHERE session_id=?", (sid,))]
    con.close()
    assert any("built: 4 photos" in h_ and "left out" in h_ and "broken.png" in h_ for h_ in hist), hist
    A.app.config["gallery_module"].JOBS.clear()
    page = c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    assert 'id="gal-skipped"' in page and "1 file left out of this gallery" in page and "broken.png" in page
    con = A.connect()
    assert "built_skipped" not in [r_[1] for r_ in con.execute("PRAGMA table_info(galleries)")]
    con.close()
    con = A.connect()
    con.execute("DELETE FROM photo_files WHERE name='broken.png'")
    con.commit()
    con.close()
    assert _build(A, c, sid)["state"] == "done"
    assert 'id="gal-skipped"' not in c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)

def test_d1_a_plain_folder_is_read_by_content(tmp_path):
    import importlib
    G = importlib.import_module("plugins.gallery")
    (tmp_path / "a.png").write_bytes(_png((1, 2, 3)))
    (tmp_path / "b").write_bytes(_jpeg((1, 2, 3)))
    (tmp_path / "c.jpg.xmp").write_text("<x/>")
    (tmp_path / "d.jpg").write_text("not a photo")
    assert [p_.name for p_ in G.list_jpegs(tmp_path)] == ["a.png", "b", "d.jpg"]
    assert G.zip_names(["x.png"]) == ["x.png"]
