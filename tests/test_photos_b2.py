import io
import json
import os
import re
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_DEV", "1")
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Diaz Family')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Rosa Diaz', 'parent', 'rosa@example.com')")
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://photos.example/g/')")
    con.commit()
    con.close()
    return A, c, tmp_path

def _session(A, stage, days=-3, **cols):
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_id) VALUES (1, 1, 'family', ?, ?, ?)",
                    (stage, (A.today() + timedelta(days=days)).isoformat(), "g-testgallery01"))
        for k, v in cols.items():
            con.execute(f"UPDATE sessions SET {k}=? WHERE id=1", (v,))
        con.commit()
    finally:
        con.close()
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        r = con.execute(sql, args).fetchall()
        con.commit()
        return r
    finally:
        con.close()

def _page(c):
    import html
    r = c.get("/c/1?s=1", environ_base=E)
    assert r.status_code == 200, r.status_code
    return html.unescape(r.get_data(as_text=True))

def _needs(page):
    rest = page.split("</header>", 1)[1]
    assert rest.lstrip().startswith('<section class="ui-needs"'), rest.lstrip()[:200]
    return rest.split("</section>", 1)[0]

def _row(page, words):

    for m in re.finditer(r'<li class="ui-todo[^"]*"[^>]*>(.*?)</li>', page, re.S):
        if re.search(r'class="ui-todo-what">\s*' + re.escape(words), m.group(1)):
            return m.group(0)
    return ""

def jpeg(color=(200, 120, 40), size=(64, 48)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()

def fake_raw():

    return b"II*\x00" + b"\x00" * 200 + jpeg((10, 10, 10), (32, 24)) + b"\x00" * 300 + jpeg((30, 160, 90), (640, 480)) + b"\x01" * 4000

def upload(c, sid, name, data, kind="raw"):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name}, environ_base=E).get_json()
    assert st["ok"], st
    at, last = st["received"], None
    while at < len(data):
        r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}", data=data[at:at + st["piece"]],
                   headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
        assert r.status_code == 200, r.get_data(as_text=True)
        last = r.get_json()
        at = last["received"]
    return last

def _task(A, title):
    r = _sql(A, "SELECT done, outcome FROM tasks WHERE session_id=1 AND title=?", (title,))
    return r[0] if r else None

def _history(A):
    return [r[0] for r in _sql(A, "SELECT action FROM audit WHERE session_id=1 ORDER BY id")]

def two_locations(tmp):

    cfg = tmp / "data" / "storage.json"
    cur = json.loads(cfg.read_text()) if cfg.is_file() else {}
    cur["secondary"] = {"type": "local", "path": str(tmp / "second")}
    cfg.write_text(json.dumps(cur))

def test_back_up_and_edit_rows_carry_their_drop_zones(env):
    A, c, tmp = env
    _session(A, "shot")
    page = _page(c)
    needs = _needs(page)
    back = _row(needs, "Upload original photos")
    assert back and "Browse RAW files" in back and "Mark done" not in back
    assert re.search(r'class="dropzone ui-drop" id="dz-raw-1"[^>]*data-kind="raw"', needs)
    assert "Drop your RAW files here, or" in needs
    assert re.search(r'class="dropzone ui-drop" id="dz-export-1"[^>]*data-kind="export"', needs)
    assert "Mark done" in _row(needs, "Upload edited photos") and "Mark done" in _row(needs, "Cull")
    rest = page.split("</section>", 1)[1]
    assert 'data-kind="raw"' not in rest and 'data-kind="export"' not in rest

def test_one_location_says_only_one_copy_and_offers_a_backup_location(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    assert not _task(A, "Back up photos")[0]
    needs = _needs(_page(c))
    row = _row(needs, "Upload original photos")
    assert "Only one copy so far" in row and "Add a backup location" in row and "/setup/storage" in row
    assert "Mark done" not in row and "I backed it up another way" in row

def test_another_way_warns_that_the_copy_is_not_encrypted_then_marks_it_done(env):
    A, c, tmp = env
    _session(A, "shot")
    r = c.post("/session/1/backup/elsewhere", data={"back": "/c/1?s=1"}, environ_base=E)
    import html
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 200 and "isn't encrypted by it" in body and "I backed it up another way" in body
    assert not _task(A, "Back up photos")[0]
    c.post("/session/1/backup/elsewhere", data={"back": "/c/1?s=1", "sure": "1"}, environ_base=E)
    assert _task(A, "Back up photos")[0]
    assert any("not encrypted by it" in h for h in _history(A))

def test_two_locations_clear_back_up_once_every_raw_is_on_both(env):
    A, c, tmp = env
    two_locations(tmp)
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    upload(c, 1, "IMG_0002.NEF", fake_raw())
    row = _row(_needs(_page(c)), "Upload original photos")
    assert "On both storage locations: 0 of 2 RAWs" in row and not _task(A, "Back up photos")[0]
    import photostore
    mark = photostore.config_mark({"type": "local", "path": str(tmp / "second")})
    _sql(A, "UPDATE photo_files SET mirrored=? WHERE name='IMG_0001.NEF'", (mark,))
    _page(c)
    assert not _task(A, "Back up photos")[0]
    _sql(A, "UPDATE photo_files SET mirrored='local'")
    _page(c)
    assert not _task(A, "Back up photos")[0]
    _sql(A, "UPDATE photo_files SET mirrored=?", (mark,))
    page = _page(c)
    assert _task(A, "Back up photos")[0] and not _row(_needs(page), "Upload original photos")
    assert "Upload original photos done: the 2 RAWs are on both storage locations" in _history(A)
    _page(c)
    assert _history(A).count("Upload original photos done: the 2 RAWs are on both storage locations") == 1

def test_edited_photos_arriving_settle_cull_and_edit_with_a_note(env):
    A, c, tmp = env
    _session(A, "shot")
    last = upload(c, 1, "img1.jpg", jpeg(), kind="export")
    assert last["done"] and last["note"] == "Cull and Upload edited photos marked done: the edited photos arrived."
    assert _task(A, "Cull")[0] and _task(A, "Edit")[0]
    assert not _task(A, "Back up photos")[0]
    assert "Cull and Upload edited photos marked done: the edited photos arrived." in _history(A)
    assert upload(c, 1, "img2.jpg", jpeg(), kind="export")["note"] == ""

def test_show_files_is_a_thumbnail_grid_and_raws_show_their_embedded_preview(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    upload(c, 1, "img1.jpg", jpeg(), kind="export")
    page = _page(c)
    grids = re.findall(r'<ul class="ui-thumbs" data-ui-viewer[^>]*>.*?</ul>', page, re.S)
    assert len(grids) == 1
    assert len(re.findall(r'href="/photo/([^/"]+)/thumb\?size=l"', page)) == 1
    for (fid,) in _sql(A, "SELECT id FROM photo_files"):
        r = c.get(f"/photo/{fid}/thumb", environ_base=E)
        assert r.status_code == 200 and r.mimetype == "image/jpeg" and "no-store" in r.headers["Cache-Control"]
        from PIL import Image
        im = Image.open(io.BytesIO(r.data))
        assert max(im.size) <= 360
    raw_fid = _sql(A, "SELECT id FROM photo_files WHERE kind='raw'")[0][0]
    from PIL import Image
    big = Image.open(io.BytesIO(c.get(f"/photo/{raw_fid}/thumb?size=l", environ_base=E).data))
    assert big.size == (640, 480)
    assert big.getpixel((5, 5))[1] > 120
    assert c.get(f"/photo/{raw_fid}/thumb?size=x", environ_base=E).status_code == 404
    assert c.get("/photo/nope/thumb", environ_base=E).status_code == 404

def test_browsing_show_files_and_the_viewer_writes_no_plaintext_image_anywhere(env):

    import time as _t
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    upload(c, 1, "img1.jpg", jpeg((1, 2, 3)), kind="export")
    _t.sleep(1.1)
    since = _t.time()
    before = {p: p.stat().st_mtime for p in tmp.rglob("*") if p.is_file()}
    page = _page(c)
    got = []
    for fid in _sql(A, "SELECT id FROM photo_files"):
        for size in ("s", "l"):
            r = c.get(f"/photo/{fid[0]}/thumb?size={size}", environ_base=E)
            assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
            got.append(r.data)
    assert page.count('class="ui-thumb-pic"') == 1
    changed = [p for p in tmp.rglob("*") if p.is_file() and (p not in before or p.stat().st_mtime >= since - 1)]
    for p in changed:
        data = p.read_bytes()
        assert b"\xff\xd8\xff" not in data and b"\x89PNG" not in data, f"image data written to {p}"
        assert not any(g_[:64] in data for g_ in got), f"a thumbnail written to {p}"
    new = [p for p in tmp.rglob("*") if p.is_file() and p not in before]
    assert not [p for p in new if not p.name.endswith(("-wal", "-shm", "-journal"))], new

PICKS = "img1.jpg: warmer skin\nimg2.jpg: crop tighter"

def _retouch_session(A):
    _session(A, "delivered", days=-20, retouch_picks=PICKS, first_visit=(date.today() - timedelta(days=5)).isoformat())

def test_retouch_row_leads_with_each_pick_its_note_and_the_finals_drop_zone(env):
    A, c, tmp = env
    _retouch_session(A)
    needs = _needs(_page(c))
    rows = re.findall(r'class="ui-todo-what">\s*([^<]+)', needs)
    assert rows[0].strip() == "Retouch 2 photos", rows
    assert "<b>img1.jpg</b>: warmer skin" in needs and "<b>img2.jpg</b>: crop tighter" in needs
    assert re.search(r'id="dz-final-1"[^>]*data-kind="final"', needs) and "Browse retouched photos" in needs
    assert "Send retouched photos" not in needs

def test_send_retouched_photos_is_done_only_with_a_final_for_every_pick(env):
    A, c, tmp = env
    _retouch_session(A)
    tid = _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND title='Send retouched photos'")[0][0]
    r = c.post(f"/task/{tid}", data={"set": "1", "back": "/c/1?s=1"}, environ_base=E, follow_redirects=True)
    assert "Not done yet: 0 of 2 retouched photos are in" in r.get_data(as_text=True)
    assert not _task(A, "Send retouched photos")[0]
    c.post("/close", data={"task": tid, "outcome": "done", "back": "/c/1?s=1"}, environ_base=E)
    assert not _task(A, "Send retouched photos")[0]
    upload(c, 1, "img1-Edit.jpg", jpeg(), kind="final")
    needs = _needs(_page(c))
    assert "1 of 2 retouched photos in" in _row(needs, "Retouch 2 photos")
    assert '<span class="ui-chip ui-chip-done">In</span> <b>img1.jpg</b>' in needs and "</span> <b>img2.jpg</b>" not in needs
    upload(c, 1, "IMG2.jpg", jpeg(), kind="final")
    needs = _needs(_page(c))
    assert "Retouch 2 photos" not in needs and "Mark done" in _row(needs, "Send retouched photos")
    c.post(f"/task/{tid}", data={"set": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert _task(A, "Send retouched photos")[0]

def test_progress_counts_only_steps_truly_done_and_names_the_rest(env):
    A, c, tmp = env
    _retouch_session(A)
    tid = _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND title='Send retouched photos'")[0][0]
    c.post("/close", data={"task": tid, "outcome": "na", "back": "/c/1?s=1"}, environ_base=E)
    assert _task(A, "Send retouched photos")[1] == "na"
    page = _page(c)
    summary = re.search(r'id="done-steps"[^>]*>\s*(?:<summary><span class="ui-fold-sum">|<h3 class="ui-sub-title">)([^<]+)', page)
    assert summary and re.match(r"\d+ of \d+ done, 1 not needed$", summary.group(1).strip()), summary.group(1)
    assert "Send retouched photos (not needed)" in page

def test_the_gallery_check_never_closes_the_retouch_step_without_the_finals(env):
    A, c, tmp = env
    _retouch_session(A)
    with A.app.app_context():
        assert A.app.config["retouch_refused"](1).startswith("Not done yet: 0 of 2")
    upload(c, 1, "img1.jpg", jpeg(), kind="final")
    upload(c, 1, "img2.jpg", jpeg(), kind="final")
    with A.app.app_context():
        assert A.app.config["retouch_refused"](1) == ""

def test_unnamed_picks_count_finals_by_number(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20, retouch_picks="the one by the tree\nthe group photo, soften")
    with A.app.test_request_context():
        st = A.retouch_state(A.q("SELECT * FROM sessions WHERE id=1", one=True))
    assert st["n"] == 2 and st["n_in"] == 0
    upload(c, 1, "final-a.jpg", jpeg(), kind="final")
    with A.app.test_request_context():
        st = A.retouch_state(A.q("SELECT * FROM sessions WHERE id=1", one=True))
    assert st["n_in"] == 1 and not st["all_in"]

def test_an_upgrade_never_closes_send_retouched_photos_while_picks_wait(env):

    A, c, tmp = env
    _retouch_session(A)
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.advance_all(force=True)
        A.db().commit()
    _page(c)
    assert not _task(A, "Send retouched photos")[0]

def test_delivery_row_is_about_the_outcome_with_the_usual_way_first(env):
    A, c, tmp = env
    _session(A, "shot")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    needs = _needs(_page(c))
    row = _row(needs, "Get the photos to Rosa")
    assert row, needs[:2000]

    assert "Choose how to deliver" in row and "Another way" not in row and "Print letter<" not in row
    assert "Print the QR letter" in needs and "Mark mailed" in needs
    assert "Handed over in person" in needs and "Record the handover" in needs

    assert re.search(r'disabled[^>]*>Email the link</button>', needs) and re.search(r'disabled[^>]*>Mark mailed</button>', needs)

def test_handed_over_in_person_closes_the_step_and_history_says_how(env):
    A, c, tmp = env
    _session(A, "shot")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    day = (date.today() - timedelta(days=1)).isoformat()
    c.post("/session/1/deliver/another", data={"how": "usb", "on": day, "note": "picked up at the studio",
                                               "step": "letter_mailed", "back": "/c/1?s=1"}, environ_base=E)
    page = _page(c)
    assert not _row(_needs(page), "Get the photos to Rosa")
    assert "Handed over in person on a USB drive (picked up at the studio)" in page
    assert any(h.startswith("Got the photos to the client: handed over in person on a USB drive") and "picked up at the studio" in h
               for h in _history(A))

def test_email_the_link_instead_drafts_it_without_the_codeword_and_closes_the_step(env):
    A, c, tmp = env
    _session(A, "shot", gallery_codeword="lighthouse")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    A.app.config["gallery_link"] = lambda sid: "https://photos.example/g/g-testgallery01/#k=abc&c=1"

    A.app.config["gallery_link_mail"] = lambda s: "mailto:rosa@example.com?subject=Your%20gallery&body=https%3A//photos.example/g/x"
    page = _page(c)
    needs = _needs(page)
    assert "Email the link<" in needs and "only as safe as their inbox" in needs
    assert "The codeword still guards the gallery." in needs

    assert re.search(r'Email the link<span class="qh"[^>]*>\?<span class="qh-pop"[^>]*>An emailed link is only as safe', needs)
    assert re.search(r'data-ui-mail="mailto:rosa@example.com[^"]*"[^>]*>(?:(?!</form>).)*Email the link<', needs, re.S)
    r = c.post("/session/1/deliver/another", data={"how": "email", "mail": "1", "step": "letter_mailed", "back": "/c/1?s=1"},
               environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].startswith("mailto:") and "lighthouse" not in r.headers["Location"]
    assert not _row(_needs(_page(c)), "Get the photos to Rosa")
    assert any("the gallery link by email instead" in h for h in _history(A))

def test_a_stale_letter_row_settles_itself_once_the_client_used_the_gallery(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20, first_visit=(date.today() - timedelta(days=4)).isoformat())
    page = _page(c)
    needs = _needs(page)
    assert "Mark mailed" not in needs and "Print the QR letter" not in needs and "Mail QR letter" not in needs
    with A.app.test_request_context():
        letter = [st for g_ in A.workflow(1)["stages"] for st in g_["steps"] if st.get("key") == "letter_mailed"][0]
    assert letter["done"] and letter["outcome"] == "na"

def _gallery(A, tmp, dl=True, opened_days_ago=None, uploaded_days_ago=10, state="up", **cols):
    from plugins import gallery as G
    gid = "g-testgallery01"
    out = G.OUT_ROOT / gid
    out.mkdir(parents=True, exist_ok=True)
    (out / G.ONLINE_MARK).write_text(date.today().isoformat())
    if dl:
        (out / G.DL_MARK).write_text("1")
    up = (date.today() - timedelta(days=uploaded_days_ago)).isoformat()
    _sql(A, """INSERT INTO galleries (session_id, key, built_at, n_files, upload_state, uploaded_at, link_base)
               VALUES (1, 'k', ?, 4, ?, ?, ?)""", (up, state, up, cols.pop("link_base", None)))
    for k, v in cols.items():
        _sql(A, f"UPDATE galleries SET {k}=? WHERE session_id=1", (v,))
    if opened_days_ago is not None:
        day = (date.today() - timedelta(days=opened_days_ago)).isoformat()
        _sql(A, "UPDATE sessions SET first_visit=?, sd_shipped=? WHERE id=1", (day, day))
    return G

def _block(page):
    m = re.search(r'<div id="gallery" class="ui-gal"[^>]*>(.*?)\n</div>\n', page, re.S)
    assert m, "no gallery block"
    return m.group(1)

def test_the_gallery_block_says_it_from_the_clients_side_with_its_actions(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20)
    _gallery(A, tmp, opened_days_ago=2)
    b = _block(_page(c))
    made = A.nice((date.today() - timedelta(days=10)).isoformat())
    assert f"Diaz can download 4 photos · made {made}" in b
    assert "Preview the gallery" in b and "Copy link" in b and "Replace this link" in b
    assert f"Online since {made}" in b
    assert "Opened " in b and "Check now" not in b
    for gone in ("Look for client activity now", "Planned deletion", ">Sent<", "Rebuild"):
        assert gone not in b, gone
    assert not re.search(r"built [A-Z][a-z]{2} ", b)

def test_the_phase_line_open_closing_soon_closed(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20)
    _gallery(A, tmp, opened_days_ago=2)
    b = _block(_page(c))
    until = A.nice((date.today() + timedelta(days=28)).isoformat())
    assert f"Online until {until} · 28 days left" in b and "Extend to " in b
    _sql(A, "UPDATE sessions SET first_visit=?, sd_shipped=? WHERE id=1", ((date.today() - timedelta(days=27)).isoformat(),) * 2)
    b = _block(_page(c))
    on = A.nice((date.today() + timedelta(days=3)).isoformat())
    assert f"Closing {on} · in 3 days · the client sees a warning" in b and "Extend to " in b
    _sql(A, "UPDATE galleries SET upload_state='removed', removed_at=?", (date.today().isoformat(),))
    b = _block(_page(c))
    assert f"Closed {A.nice(date.today().isoformat())}, off the web" in b and "Extend" not in b

def test_add_them_shows_only_when_uploads_arrived_after_the_gallery_was_made(env):
    A, c, tmp = env

    _session(A, "delivered", days=-20, delivery_method="link", favorites="img1.jpg")
    upload(c, 1, "img1.jpg", jpeg(), kind="export")
    _gallery(A, tmp, opened_days_ago=2, built_upto="2999-01-01 00:00:00")
    assert "Add them" not in _block(_page(c))
    _sql(A, "UPDATE galleries SET built_upto='2000-01-01 00:00:00'")
    b = _block(_page(c))
    assert "1 new photo since the gallery was made" in b and "Add them" in b

def test_address_changed_is_a_to_do_row_with_email_the_new_link_gone_once_sent(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20)
    _gallery(A, tmp, opened_days_ago=2, link_base="https://old.example/g/")
    A.app.config["gallery_link"] = lambda sid: "https://photos.example/g/g-testgallery01/#k=abc"
    page = _page(c)
    row = _row(_needs(page), "The gallery address changed")
    assert row and "Email the new link" in row and "data-ui-mail=" in row
    assert "address changed" not in _block(page) and ">Sent<" not in page
    c.post("/session/1/gallery/link-sent", data={"back": "/c/1?s=1"}, environ_base=E)
    page = _page(c)
    assert not _row(_needs(page), "The gallery address changed")
    assert "The new gallery link was sent" in _history(A)

def test_readable_copies_are_named_for_what_they_are(env):
    A, c, tmp = env
    _session(A, "delivered", days=-20, sd_shipped=(date.today() - timedelta(days=2)).isoformat())
    upload(c, 1, "img1.jpg", jpeg(), kind="export")
    page = _page(c)
    on = A.nice((date.today() + timedelta(days=5)).isoformat())
    assert f"Readable copies for editing: encrypted and removed {on}" in page and "Encrypt them now" in page
    assert "Seal now" not in page and "Seals " not in page

def test_what_they_want_is_findable_only_after_the_shoot(env):
    A, c, tmp = env
    with A.app.test_request_context():
        assert A.FOCUS_TIERS["intake"][A.FOCUS_STAGES.index("shot")] == 4

def test_the_header_after_the_shoot_says_when_it_was_shot_once(env):
    A, c, tmp = env
    _session(A, "shot", days=-46)
    head = _page(c).split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert "Shot " in head and " for " not in head.split("ui-status", 1)[1].split("</p>", 1)[0]
    assert head.count("Shot ") == 1

@pytest.fixture(scope="module")
def browser():
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"no browser here: {e}")
    yield b
    b.close()
    pw.stop()

BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy", "cache-control")

def _tab(browser, c, width=390):

    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "x-file-name", "x-fetch")}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303) and request.method == "POST":
            loc = urlsplit(r.location)
            r = c.get(loc.path + ("?" + loc.query if loc.query else ""), environ_base=E)
        if r.status_code in (301, 302, 303):
            route.fulfill(status=r.status_code, headers={"Location": BASE + r.location if r.location.startswith("/") else r.location})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 500)
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def _with_exports(A, c, n=4):
    _session(A, "shot")
    for i in range(n):
        upload(c, 1, f"img{i + 1}.jpg", jpeg((40 * i, 90, 160)), kind="export")

def test_show_files_opens_a_viewer_that_flips_and_closes_in_place(browser, env):
    A, c, tmp = env
    _with_exports(A, c, 3)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        fold = p.locator("#photo-hits")
        thumbs = fold.locator("a.ui-thumb-pic")
        assert thumbs.count() == 3
        thumbs.nth(0).scroll_into_view_if_needed()
        y0 = p.evaluate("scrollY")
        thumbs.nth(0).click()
        v = p.locator(".ui-viewer")
        v.wait_for()
        assert v.locator(".ui-viewer-count").inner_text() == "1 of 3"
        assert v.locator(".ui-viewer-name").inner_text() == "img3.jpg"
        p.keyboard.press("ArrowRight")
        assert v.locator(".ui-viewer-count").inner_text() == "2 of 3"
        v.get_by_role("button", name="Next photo").click()
        assert v.locator(".ui-viewer-name").inner_text() == "img1.jpg"
        assert v.get_by_role("button", name="Next photo").is_disabled()
        p.keyboard.press("ArrowLeft")
        assert v.locator(".ui-viewer-count").inner_text() == "2 of 3"
        assert p.evaluate("document.querySelector('.ui-viewer img').naturalWidth") > 0
        p.keyboard.press("Escape")
        assert v.is_hidden()
        assert p.evaluate("document.activeElement === document.querySelectorAll('#photo-hits a.ui-thumb-pic')[0]")
        assert p.evaluate("window.__stay") == 1 and abs(p.evaluate("scrollY") - y0) < 5
    finally:
        ctx.close()

def test_on_a_phone_the_grid_is_three_across_and_a_swipe_flips(browser, env):
    A, c, tmp = env
    _with_exports(A, c, 4)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        lefts = p.evaluate("[...document.querySelectorAll('#photo-hits li.ui-thumb')].map(e => Math.round(e.getBoundingClientRect().left))")
        assert len(set(lefts[:3])) == 3 and lefts[3] == lefts[0]
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        p.locator("#photo-hits a.ui-thumb-pic").nth(1).click()
        stage = p.locator(".ui-viewer-stage")
        stage.wait_for()
        box = stage.bounding_box()
        y = box["y"] + box["height"] / 2
        p.evaluate("""([x0, x1, y]) => { const s = document.querySelector('.ui-viewer-stage');
            const t = (x) => new Touch({identifier: 1, target: s, clientX: x, clientY: y});
            s.dispatchEvent(new TouchEvent('touchstart', {touches: [t(x0)], changedTouches: [t(x0)], bubbles: true}));
            s.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [t(x1)], bubbles: true})); }""",
                   [300, 80, y])
        assert p.locator(".ui-viewer-count").inner_text() == "3 of 4"
        p.get_by_role("button", name="Close").click()
        assert p.locator(".ui-viewer").is_hidden()
    finally:
        ctx.close()

def test_raws_dropped_on_the_back_up_row_update_it_in_place(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        row = p.locator(".ui-todo", has_text="Upload original photos")
        assert row.get_by_role("button", name="Browse RAW files").count() == 1
        p.set_input_files("#dz-raw-1-in", files=[{"name": "IMG_0001.NEF", "mimeType": "application/octet-stream",
                                                 "buffer": fake_raw()}])
        p.locator(".ui-todo", has_text="Only one copy so far").wait_for(timeout=16000)
        assert p.evaluate("window.__stay") == 1
        assert p.locator(".ui-todo", has_text="Upload original photos").get_by_role("link", name="Add a backup location").count() == 1
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        ctx.close()
    assert _sql(A, "SELECT COUNT(*) FROM photo_files WHERE kind='raw' AND state='done'")[0][0] == 1

def test_exports_dropped_on_the_edit_row_say_what_they_settled(browser, env):
    A, c, tmp = env
    _session(A, "shot")
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.set_input_files("#dz-export-1-in", files=[{"name": "img1.jpg", "mimeType": "image/jpeg", "buffer": jpeg()}])
        p.locator(".ui-note", has_text="Cull and Upload edited photos marked done").wait_for(timeout=16000)
        assert p.locator(".ui-todo", has_text="Cull").count() == 0
    finally:
        ctx.close()

def test_the_components_page_shows_the_photo_rows_and_facts_with_actions(env):
    A, c, tmp = env
    t = c.get("/dev/components", environ_base=E).get_data(as_text=True)
    for words in ("Retouch 2 photos", "Browse RAW files", "Get the photos to Alex", "Choose how to deliver", "Handed over in person",
                  "Preview the gallery", "the client sees a warning"):
        assert words in t, words
