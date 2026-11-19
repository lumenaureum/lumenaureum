import html
import io
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_photos_b2 import (BASE, E, _history, _needs, _page, _row, _session, _sql, _tab, browser, env, fake_raw,
                            jpeg, upload)

def png(size=(64, 48)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(b, "PNG")
    return b.getvalue()

def _fid(A, name):
    return _sql(A, "SELECT id FROM photo_files WHERE name=?", (name,))[0][0]

def test_a_png_filed_as_a_raw_previews_as_itself(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "Screenshot 1.png", png())
    for size in ("s", "l"):
        r = c.get(f"/photo/{_fid(A, 'Screenshot 1.png')}/thumb?size={size}", environ_base=E)
        assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:3] == b"\xff\xd8\xff"
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    assert c.get(f"/photo/{_fid(A, 'IMG_0001.NEF')}/thumb", environ_base=E).status_code == 200

def test_a_file_with_no_picture_shows_a_file_icon_and_its_name_never_a_broken_image(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "notes.pdf", b"%PDF-1.4 not a picture" * 20)
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    page = _page(c)
    tile = re.search(r'<li class="ui-thumb"[^>]*>(?:(?!</li>).)*notes\.pdf(?:(?!</li>).)*</li>', page, re.S).group(0)
    assert 'class="ui-thumb-file"' in tile and "<img" not in tile and ">PDF<" in tile and "Download notes.pdf" in tile
    nef = re.search(r'<li class="ui-thumb"[^>]*>(?:(?!</li>).)*IMG_0001\.NEF(?:(?!</li>).)*</li>', page, re.S).group(0)
    assert "<img" in nef and "ui-thumb-file" not in nef
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "photos.js")).read()
    assert "addEventListener('error'" in js and "ui-thumb-file" in js

def test_the_photos_line_says_how_many_how_big_and_sealed(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "A_1.NEF", fake_raw())
    upload(c, 1, "B_2.NEF", fake_raw())
    page = _page(c)
    head = re.search(r'<p class="ui-photos-sum">(.*?)</p>', page, re.S).group(1)
    assert re.search(r"2 files, [\d.]+ KB, sealed", head), head
    assert "files sealed" not in page

def test_the_rows_count_what_was_dropped_on_them(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "A_1.NEF", fake_raw())
    upload(c, 1, "B_2.NEF", fake_raw())
    upload(c, 1, "img1.jpg", jpeg(), kind="export")
    needs = _needs(_page(c))
    assert "2 RAW files in · Only one copy so far" in _row(needs, "Upload original photos")
    assert "Browse RAW files" in needs and "Drop your RAW files here, or <u>Browse</u>" in needs
    assert "choose them" not in needs and "Choose RAW files" not in needs

def test_delete_answers_in_place_for_the_page_and_still_works_without_script(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "A_1.NEF", fake_raw())
    upload(c, 1, "B_2.NEF", fake_raw())
    r = c.post(f"/photo/{_fid(A, 'A_1.NEF')}/delete", data={"back": "/c/1?s=1#photos"}, headers={"X-Fetch": "1"}, environ_base=E)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "message": "Deleted A_1.NEF."}
    r = c.post(f"/photo/{_fid(A, 'B_2.NEF')}/delete", data={"back": "/c/1?s=1#photos"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("#photos")
    assert _sql(A, "SELECT COUNT(*) FROM photo_files")[0][0] == 0
    assert "Deleted A_1.NEF" in _history(A) and "Deleted B_2.NEF" in _history(A)
    page = _page(c)
    assert "data-photo-del" not in page

def test_the_delivery_row_is_one_button_with_the_usual_way_first_and_no_label(env):
    A, c, tmp = env
    _session(A, "shot")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    A.app.config["gallery_link_mail"] = lambda s: "mailto:rosa@example.com?subject=Your%20gallery"
    A.app.config["gallery_link"] = lambda sid: "https://photos.example/g/x"
    needs = _needs(_page(c))
    row = _row(needs, "Get the photos to Rosa")
    assert row.count("ui-btn-primary") + row.count("ui-btn-quiet") == 1, row
    assert ">Choose how to deliver</a>" in row and "Print the QR letter" not in row and "Another way" not in row
    ways = re.findall(r'data-way="(\w+)"', needs)
    assert ways == ["letter", "email", "person"], ways
    letter = needs.split('data-way="letter"', 1)[1].split('data-way="email"', 1)[0]
    assert "Mail the QR letter" in letter and "Print the QR letter" in letter and "Mark mailed" in letter
    assert ">usual<" not in needs
    assert "Record the handover" in needs and 'id="f-deliver-1"' in needs

def test_files_on_an_sd_card_put_handed_over_in_person_first_and_record_how(env):
    A, c, tmp = env
    day0 = (date.today() - timedelta(days=5)).isoformat()
    _session(A, "delivered", days=-20, letter_mailed=day0, first_visit=day0, favorites="3, 7", delivery_method="sd")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    needs = _needs(_page(c))
    row = _row(needs, "Get the photos to Rosa")
    assert "On an SD card" in row and ">Choose how to deliver</a>" in row, needs[:3000]
    ways = re.findall(r'data-way="(\w+)"', needs)
    assert ways[0] == "person" and "letter" not in ways, ways
    person = needs.split('data-way="person"', 1)[1]
    assert ">usual<" not in needs
    assert re.search(r'value="sd" checked', person.split("</form>", 1)[0])
    day = (date.today() - timedelta(days=1)).isoformat()
    c.post("/session/1/deliver/another", data={"how": "sd", "on": day, "step": "sd_shipped", "back": "/c/1?s=1"}, environ_base=E)
    assert _sql(A, "SELECT sd_shipped FROM sessions WHERE id=1")[0][0] == day
    assert any("handed over in person on an SD card" in h for h in _history(A))

def _inquiry(A):
    con = A.connect()
    con.execute("INSERT INTO sessions (id, household_id, type, stage) VALUES (1, 1, 'family', 'inquiry')")
    con.commit()
    con.close()

def test_do_not_book_requires_a_reason_and_other_needs_a_few_words(env):
    A, c, tmp = env
    _inquiry(A)
    page = _page(c)
    form = page.split('id="dont-book"', 1)[1].split("</form>", 1)[0]
    assert "Why, if you like" not in form and re.search(r'name="lost_reason" value="They went elsewhere"[^>]*required', form)
    assert 'name="lost_note"' in form and 'data-required-if="lost_reason=Other"' in form and 'name="need_reason"' in form
    base = {"stage": "lost", "need_reason": "1", "back": "/c/1?s=1"}
    c.post("/session/1/stage", data=base, environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "inquiry"
    c.post("/session/1/stage", data=dict(base, lost_reason="Other"), environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "inquiry"
    assert not any(h.startswith("Did not book") for h in _history(A))
    c.post("/session/1/stage", data=dict(base, lost_reason="Other", lost_note="moving away"), environ_base=E)
    assert tuple(_sql(A, "SELECT stage, lost_reason FROM sessions WHERE id=1")[0]) == ("lost", "Other")
    assert "Did not book: Other (moving away)" in _history(A)

def test_a_booked_session_says_cancel_the_booking_with_a_required_reason(env):
    A, c, tmp = env
    _session(A, "booked", days=10)
    page = _page(c)
    assert "Mark lost" not in page and ">Cancel the booking</a>" in page
    form = page.split('id="cancel-booking"', 1)[1].split("</form>", 1)[0]
    assert "They canceled" in form and re.search(r'value="Canceled"[^>]*required', form) and "Cancel this booking?" in form
    base = {"stage": "lost", "need_reason": "1", "back": "/c/1?s=1"}
    c.post("/session/1/stage", data=base, environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "booked"
    c.post("/session/1/stage", data=dict(base, lost_reason="They went elsewhere"), environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "booked"
    c.post("/session/1/stage", data=dict(base, lost_reason="Canceled"), environ_base=E)
    assert tuple(_sql(A, "SELECT stage, lost_reason FROM sessions WHERE id=1")[0]) == ("lost", "Canceled")
    assert "Canceled the booking: They canceled" in _history(A)

def test_skip_to_names_the_stage_and_its_confirm_lists_the_open_items(env):
    A, c, tmp = env
    _session(A, "shot")
    page = _page(c)
    assert " anyway" not in re.sub(r"<[^>]+>", " ", page.split("</header>", 1)[1]).replace("Upload them as RAWs anyway", "")
    m = re.search(r'data-confirm="(Skip to Delivered\?[^"]*)"', page)
    assert m and "Still open: " in m.group(1) and "They carry along to Delivered." in m.group(1), m and m.group(1)
    assert ">Skip to Delivered</button>" in page

def test_add_buttons_say_what_they_add(env):
    A, c, tmp = env
    _session(A, "booked", days=10)
    page = _page(c)
    assert ">Add the step</button>" in page
    assert not re.search(r">\s*Add\s*</(?:button|a)>", page)
    src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "client_stage.html")).read()
    assert not re.search(r">Add</button>|'Add'\)", src)

def test_an_old_build_preview_says_so_plainly_with_rebuild_to_preview(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20)
    from plugins import gallery as G
    out = G.OUT_ROOT / "g-testgallery01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<!doctype html><script>old()</script>")
    _sql(A, "INSERT INTO galleries (session_id, key, built_at) VALUES (1, 'k', '2026-01-01')")
    r = c.get("/gallery-preview/g-testgallery01/", environ_base=E)
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 200
    assert "Built by an earlier version, so previewing it here needs a rebuild. Your client's copy online isn't affected." in body
    assert "(The copy" not in body and "older version" not in body
    assert re.search(r'<form method=post action="/session/1/gallery/build">.*>Rebuild to preview</button></form>', body, re.S)

def test_a_png_dropped_on_the_back_up_row_is_asked_about_then_the_row_says_files_added(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    ctx, p = _tab(browser, c, width=390)
    asked = []
    p.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        p.set_input_files("#dz-raw-1-in", files=[{"name": f"Screenshot {i}.png", "mimeType": "image/png", "buffer": png()} for i in range(2)])
        row = p.locator(".ui-todo", has_text="Upload original photos")
        row.locator(".ui-added", has_text="2 files added").wait_for(timeout=16000)
        assert asked and "don't look like RAW files" in asked[0] and "Screenshot 0.png" in asked[0]
        assert "2 RAW files in" in row.inner_text()
        assert p.evaluate("window.__stay") == 1
    finally:
        ctx.close()
    fid = _fid(A, "Screenshot 0.png")
    assert c.get(f"/photo/{fid}/thumb", environ_base=E).status_code == 200

def test_delete_happens_in_place_with_the_section_open_and_the_page_still(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    for i in range(3):
        upload(c, 1, f"img{i + 1}.jpg", jpeg((40 * i, 90, 160)), kind="export")
    ctx, p = _tab(browser, c, width=1280)
    asked = []
    p.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        fold = p.locator("#photo-hits")
        fold.scroll_into_view_if_needed()
        y0 = p.evaluate("scrollY")
        fold.get_by_role("button", name="Delete img2.jpg").click()
        p.locator(".ui-note", has_text="Deleted img2.jpg.").wait_for(timeout=10000)
        assert len(asked) == 1 and asked[0].startswith("Delete img2.jpg?")
        assert fold.locator("li.ui-thumb").count() == 2
        assert p.evaluate("window.__stay") == 1 and abs(p.evaluate("scrollY") - y0) < 5
        assert p.locator('[data-photo-n="export"]').inner_text().startswith("2 files, ")
    finally:
        ctx.close()
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE kind='export'")[0][0] == 2

def test_the_viewer_fits_the_whole_photo_in_the_window(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "a-portrait.jpg", jpeg((200, 40, 40), (900, 1800)), kind="export")
    upload(c, 1, "b-landscape.jpg", jpeg((40, 200, 40), (1800, 900)), kind="export")
    upload(c, 1, "c-square.jpg", jpeg((40, 40, 200), (1200, 1200)), kind="export")
    for width, height in ((390, 844), (1280, 800)):
        ctx = None
        try:
            ctx, p = _tab(browser, c, width=width)
            p.set_viewport_size({"width": width, "height": height})
            p.goto(BASE + "/c/1?s=1")
            p.locator("#photo-hits a.ui-thumb-pic").nth(0).click()
            for i in range(2):
                p.wait_for_function("() => { const i = document.querySelector('.ui-viewer img'); return i && i.complete && i.naturalWidth > 0; }")
                r = p.evaluate("""() => { const i = document.querySelector('.ui-viewer img'), b = i.getBoundingClientRect();
                    const k = Math.min(b.width / i.naturalWidth, b.height / i.naturalHeight);
                    const w = i.naturalWidth * k, h = i.naturalHeight * k, top = b.top + (b.height - h) / 2, left = b.left + (b.width - w) / 2;
                    const bar = document.querySelector('.ui-viewer-bar').getBoundingClientRect();
                    return {top, left, bottom: top + h, right: left + w, barBottom: bar.bottom, H: innerHeight, W: innerWidth, h}; }""")
                assert r["top"] >= r["barBottom"] - 1 and r["bottom"] <= r["H"] + 1 and r["left"] >= -1 and r["right"] <= r["W"] + 1, (width, i, r)
                assert r["h"] > 100, r
                if i == 0:
                    p.get_by_role("button", name="Next photo").click()
        finally:
            if ctx:
                ctx.close()

def test_leaving_a_page_that_filled_its_own_fields_does_not_ask(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    ctx, p = _tab(browser, c, width=1280)
    asked = []
    p.on("dialog", lambda d: (asked.append(d.type), d.dismiss()))
    try:
        p.goto(BASE + "/c/1?s=1")
        p.locator("header h1").first.click()
        p.evaluate("""() => { const d = document.querySelector('#f-deliver-1 input[type=date]');      // the page fills a day
            if (d) d.value = '2026-01-02'; document.querySelectorAll('select').forEach(s => { if (s.options.length > 1) s.selectedIndex = 1; }); }""")
        p.evaluate("location.href = '/'")
        p.wait_for_url(BASE + "/", timeout=10000)
        assert asked == []

        p.goto(BASE + "/c/1?s=1")
        p.evaluate("""() => { for (let e = document.querySelector('#add-step'); e; e = e.parentElement) if (e.tagName === 'DETAILS') e.open = true; }""")
        p.locator("#add-step input[name=title]").fill("Call the venue")
        p.evaluate("location.href = '/'")
        p.wait_for_timeout(800)
        assert asked == ["beforeunload"]
        assert p.locator("#add-step .ui-unsaved", has_text="Not saved yet").count() == 1
    finally:
        ctx.close()
