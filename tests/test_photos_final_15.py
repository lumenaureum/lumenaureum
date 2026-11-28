import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_photos_b2 import E, _page, _session, _sql, env, jpeg, upload

def _x(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _photos(page):
    i = page.index('id="photos-sec"')
    return page[i:page.index("</section>", i)]

def test_the_photos_section_shows_the_latest_upload_and_never_everything(env):
    A, c, tmp = env
    _session(A, "shot")
    for n in range(1, 4):
        upload(c, 1, f"OLD_{n}.jpg", jpeg((n * 20, 40, 40)))
    _x(A, "UPDATE photo_files SET created='2026-11-01 09:00:00' WHERE name LIKE 'OLD_%'")
    for n in range(1, 16):
        upload(c, 1, f"NEW_{n:02d}.jpg", jpeg((40, n * 10, 40)))
        _x(A, "UPDATE photo_files SET created=? WHERE name=?", (f"2026-11-17 10:{n:02d}:00", f"NEW_{n:02d}.jpg"))
    page = _page(c)
    sec = _photos(page)
    assert '<section class="ui-sec' in page[page.rindex("<", 0, page.index('id="photos-sec"')):page.index('id="photos-sec"')]
    assert '<details class="ui-fold' not in sec
    assert re.search(r"<b>RAWs</b> <span[^>]*>18 files, [\d.]+ KB, sealed</span>", sec), sec[:600]
    shown = re.findall(r'<li class="ui-thumb" data-size="\d+" data-kind="raw">.*?alt="([^"]+)"', sec, re.S)
    assert shown == [f"NEW_{n:02d}.jpg" for n in range(15, 3, -1)]
    assert "OLD_1.jpg" not in sec
    hidden = re.findall(r'<li class="ui-thumb" hidden><a class="ui-thumb-pic" href="[^"]+" data-name="([^"]+)"', sec)
    assert hidden == ["NEW_03.jpg", "NEW_02.jpg", "NEW_01.jpg"]
    assert re.search(r'data-ui-viewer-more>\+ 3 more from this upload', sec)
    assert sec.count("<img") == 12 and sec.count("data-photo-del") == 12
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "photos.js")).read()
    assert "data-ui-viewer-more" in js and "form[data-photo-find]" in js

def test_find_a_file_shows_only_the_matches(env):
    A, c, tmp = env
    _session(A, "shot")
    for name in ("Smith_0412.jpg", "Smith_0413.jpg", "Jones_0001.jpg"):
        upload(c, 1, name, jpeg())
    import html
    sec = _photos(html.unescape(c.get("/c/1?s=1&find=smith", environ_base=E).get_data(as_text=True)))
    assert 'name="find" value="smith"' in sec and "2 files match." in sec
    names = re.findall(r'<span class="ui-thumb-name" title="([^"]+)"', sec)
    assert sorted(names) == ["Smith_0412.jpg", "Smith_0413.jpg"]
    sec = _photos(html.unescape(c.get("/c/1?s=1&find=nothing-like-it", environ_base=E).get_data(as_text=True)))
    assert 'No file name holds "nothing-like-it".' in sec and "ui-thumb-name" not in sec

def test_the_photos_section_is_shown_at_every_stage_with_photos(env):
    A, c, tmp = env
    _session(A, "delivered")
    upload(c, 1, "A_1.jpg", jpeg())
    page = _page(c)
    assert 'id="photos-sec"' in page and '<details class="ui-fold' not in _photos(page)

def _deliver_page(A, c, online):
    from test_photos_b2 import _needs
    _session(A, "shot")
    _sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1")
    A.app.config["gallery_link_mail"] = (lambda s: "mailto:rosa@example.com?subject=Your%20gallery") if online else (lambda s: "")
    A.app.config["gallery_link"] = (lambda sid: "https://photos.example/g/x") if online else (lambda sid: None)
    return _needs(_page(c))

def test_no_gallery_online_the_letter_and_email_wait_with_the_reason(env):
    A, c, tmp = env
    needs = _deliver_page(A, c, online=False)
    letter = needs.split('data-way="letter"', 1)[1].split('data-way="email"', 1)[0]
    email = needs.split('data-way="email"', 1)[1].split('data-way="person"', 1)[0]
    assert "Put the gallery online first: the letter's QR code is its link." in letter
    assert re.search(r'<button type="button" class="ui-btn ui-btn-quiet" disabled[^>]*>Print the QR letter</button>', letter)
    assert re.search(r'<button type="button" class="ui-btn ui-btn-quiet" disabled[^>]*>Mark mailed</button>', letter)
    assert ">Print a draft letter</a>" in letter.split('class="ui-more"', 1)[1]
    assert re.search(r'disabled[^>]*>Email the link</button>', email) and "Put the gallery online first" in email
    person = needs.split('data-way="person"', 1)[1]
    assert ">Record the handover</button>" in person and "disabled" not in person.split("</form>", 1)[0]

    c.post("/session/1/quick", data={"act": "mark", "field": "letter_mailed", "back": "/c/1?s=1"}, environ_base=E)
    assert _sql(A, "SELECT letter_mailed FROM sessions WHERE id=1")[0][0] is None

def test_once_the_gallery_is_online_the_letter_and_email_can_be_chosen(env):
    A, c, tmp = env
    needs = _deliver_page(A, c, online=True)
    letter = needs.split('data-way="letter"', 1)[1].split('data-way="email"', 1)[0]
    assert "Put the gallery online first" not in needs and "disabled" not in letter
    assert ">Print the QR letter</a>" in letter and ">Mark mailed</button>" in letter
    assert "Print a draft letter" not in letter
    c.post("/session/1/quick", data={"act": "mark", "field": "letter_mailed", "back": "/c/1?s=1"}, environ_base=E)
    assert _sql(A, "SELECT letter_mailed FROM sessions WHERE id=1")[0][0]

def test_the_photo_steps_read_upload_original_and_edited_photos(env):
    A, c, tmp = env
    _session(A, "shot")
    page = _page(c)
    todo = page.split('class="ui-todos"', 1)[1].split("</ul>", 1)[0]
    assert "Upload original photos" in todo and "Upload edited photos" in todo and ">Cull" in todo
    assert "Back up photos" not in todo and ">Edit<" not in todo
    assert _sql(A, "SELECT COUNT(*) FROM tasks WHERE session_id=1 AND title IN ('Back up photos', 'Edit')")[0][0] == 2
    with A.app.test_request_context():
        assert A.step_says("Back up photos") == "Upload original photos" and A.step_says("Edit") == "Upload edited photos"
        assert A.step_says("Cull") == "Cull"

def test_a_task_row_says_its_due_date_right_after_its_title(env):
    A, c, tmp = env
    with A.app.test_request_context():
        html = A.app.jinja_env.from_string('{% import "_ui.html" as ui with context %}'
                                           "{% call ui.todo_row('Cull', when='Mon Nov 16') %}x{% endcall %}"
                                           "{% call ui.todo_row('Edit', when='Mon Nov 9', when_late=true) %}x{% endcall %}").render()
    assert '<span class="ui-todo-what">Cull <span class="ui-todo-when">&middot; Due Mon Nov 16</span>' in html
    assert '<span class="ui-todo-when ui-late">&middot; Was due Mon Nov 9</span>' in html
    today_src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "_today.html")).read()
    assert "ui.due_words(" in today_src
