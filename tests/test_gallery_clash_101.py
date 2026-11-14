import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E
from test_data_integrity_101 import CLASH, _seen

VIEWER = os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")

def test_viewer_messages_are_live_regions():
    page = open(VIEWER).read()
    assert re.search(r'<div id="msgsay" role="status" aria-live="polite"', page)
    assert re.search(r'<div id="msgerr" role="alert"', page)
    assert "innerHTML" not in page.split("<script>")[1].split("function say")[1].split("async function main")[0]

def test_viewer_script_hash_is_computed_from_the_file():

    from plugins import gallery as G
    page = open(VIEWER).read()
    assert len(re.findall(r"<script>", page)) == 1
    assert G.VIEWER_SCRIPT_HASHES == G._script_hashes(page) and G.VIEWER_SCRIPT_HASHES.startswith(" 'sha256-")

@pytest.fixture(scope="module")
def viewer_runs():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "viewer_harness.js"), VIEWER], capture_output=True,
                         text=True, timeout=120, check=True).stdout
    return json.loads(out)

def test_unreachable_does_not_ask_for_the_word_and_retries_with_it(viewer_runs):
    r = viewer_runs["word_network_retry"]
    first, retry = r["seen"][0], r["seen"][1]
    assert "protected by a word" in first["say"] and first["form"] and not first["err"]
    assert retry["what"] == "retry"
    assert retry["err"].startswith("Could not reach the gallery") and "Check your connection and try again." in retry["err"]
    assert "Enter the word" not in retry["err"] + retry["say"]
    assert retry["buttons"] == ["Try again", "Use a different word"] and retry["focus"] == "Try again"
    assert not retry["form"] and retry["prefill"] == "blue river stone"

    assert r["title"] == "Lane" and r["fetches"] == 3

def test_wrong_word_is_an_alert_and_asks_again(viewer_runs):
    r = viewer_runs["word_wrong_then_right"]
    wrong = r["seen"][1]
    assert wrong["err"].startswith("That word doesn't open this gallery") and "Enter the word" in wrong["err"]
    assert wrong["form"] and wrong["prefill"] == "" and not wrong["say"]
    assert r["title"] == "Lane"

def test_plain_link_unreachable_offers_try_again(viewer_runs):
    r = viewer_runs["plain_network_retry"]
    assert r["seen"][0]["err"].startswith("Could not reach the gallery") and r["seen"][0]["buttons"] == ["Try again"]
    assert r["title"] == "Lane"
    assert viewer_runs["plain_closed"]["seen"][-1]["err"] == "This gallery has closed"
    assert viewer_runs["plain_ok"]["title"] == "Lane" and viewer_runs["plain_ok"]["seen"][-1]["say"] == "Opening your photos..."

def _delivery(c, sid, word):
    return c.post(f"/session/{sid}/delivery", data={"gallery_codeword": word}, environ_base=E)

def _shot(A, sid):
    con = A.connect()
    con.execute("UPDATE sessions SET stage='shot' WHERE id=?", (sid,))
    con.commit()
    con.close()

def test_a_refused_codeword_stays_in_the_field(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _shot(A, sid)
    assert _delivery(c, sid, "spruce otter raven").status_code == 302
    r = _delivery(c, sid, "blue")
    assert r.status_code == 302
    assert _q(A, "SELECT gallery_codeword FROM sessions WHERE id=?", (sid,))[0][0] == "spruce otter raven"
    page = c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    assert "That codeword is too easy to guess. Use three or more words, or 14 letters or more" in page
    field = re.search(r'<input name="gallery_codeword"[^>]*>', page).group(0)
    assert 'value="blue"' in field and 'aria-invalid="true"' in field
    assert "Not saved: this codeword is too easy to guess." in page
    assert 'The saved codeword is still "spruce otter raven".' in page

    page = c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    assert 'value="spruce otter raven"' in re.search(r'<input name="gallery_codeword"[^>]*>', page).group(0)
    assert "Not saved:" not in page

def test_a_refused_codeword_at_intake_is_quoted_back(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava", gallery_codeword="blue  sky")
    assert (_q(A, "SELECT gallery_codeword FROM sessions WHERE id=?", (sid,))[0][0] or "") == ""
    page = c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    assert "The gallery codeword &#34;blue sky&#34; wasn&#39;t saved: it is too easy to guess." in page
    assert "Use three or more words, or 14 letters or more" in page

def test_a_refused_codeword_is_only_put_back_on_its_own_session(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    one, two = _new(A, c, "family", subject_name="Ava"), _new(A, c, "family", subject_name="Ben")
    for s_ in (one, two):
        _shot(A, s_)
        _delivery(c, s_, "spruce otter raven")
    _delivery(c, one, "blue")
    page = c.get(f"/session/{two}", environ_base=E).get_data(as_text=True)
    assert 'value="spruce otter raven"' in re.search(r'<input name="gallery_codeword"[^>]*>', page).group(0)
    assert "Not saved:" not in page

def _stale_session(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    form = {"title": "", "type": "family", "shoot_date": "2027-12-04", "start_time": "", "stops": "", "notes": ""}
    c.post(f"/session/{sid}", data=form, environ_base=E)
    tab_a, tab_b = _seen(c, f"/session/{sid}"), _seen(c, f"/session/{sid}")
    c.post(f"/session/{sid}", data={**form, "seen": tab_b, "title": "From B", "notes": "gate code 9911"}, environ_base=E)
    r = c.post(f"/session/{sid}", data={**form, "seen": tab_a, "title": "TAB A title", "notes": "bring the dog\nand a ball"},
               environ_base=E)
    return A, c, sid, r

def test_clash_lists_each_typed_field_by_name_for_the_refill(tmp_path, monkeypatch):
    A, c, sid, r = _stale_session(tmp_path, monkeypatch)
    page = r.get_data(as_text=True)
    assert r.status_code == 409 and "This session " + CLASH in page
    assert '<tr data-field="title" data-mine="TAB A title">' in page
    assert '<tr data-field="notes" data-mine="bring the dog\nand a ball">' in page
    assert 'data-field="shoot_date"' not in page
    assert "Latest saved" in page and "Use mine" not in page.split("<script")[0]

    assert 'value="From B"' in page and "gate code 9911</textarea>" in page
    fresh = re.search(r'name="seen" value="([0-9a-f]+)"', page).group(1)
    r2 = c.post(f"/session/{sid}", data={"seen": fresh, "title": "TAB A title", "type": "family", "shoot_date": "2027-12-04",
                                         "notes": "bring the dog\nand a ball"}, environ_base=E)
    assert r2.status_code == 302
    assert tuple(_q(A, "SELECT title, notes FROM sessions WHERE id=?", (sid,))[0]) == ("TAB A title", "bring the dog\nand a ball")

def test_clash_reload_is_a_button_and_the_message_clears_the_header(tmp_path, monkeypatch):
    A, c, sid, r = _stale_session(tmp_path, monkeypatch)
    page = r.get_data(as_text=True)
    assert f'<a class="btn" href="/session/{sid}">Reload and discard what I typed</a>' in page
    assert 'class="clash edit-clash" id="edit-clash" tabindex="-1"' in page
    assert re.search(r'<p[^>]*role="alert"><strong>This session ' + re.escape(CLASH), page)
    css = open(os.path.join(ROOT, "static", "style.css")).read()
    assert re.search(r"\.edit-clash \{ scroll-margin-top: \d+px; \}", css)
    assert "scrollIntoView" in page and 'addEventListener("load", land)' in page

def test_clash_script_runs_under_the_page_policy(tmp_path, monkeypatch):
    A, c, sid, r = _stale_session(tmp_path, monkeypatch)
    page = r.get_data(as_text=True)
    nonce = re.search(r'<script nonce="([^"]+)">\s*\(function \(\) \{\s*"use strict";\s*var box = document.getElementById\("edit-clash"\)',
                      page).group(1)
    assert f"'nonce-{nonce}'" in r.headers["Content-Security-Policy"]

def test_client_clash_lists_fields_by_name(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    tab_a, tab_b = _seen(c, "/client/1"), _seen(c, "/client/1")
    c.post("/client/1", data={"seen": tab_b, "name": "Lane", "address": "4 Mill Road", "notes": "prefers texts"}, environ_base=E)
    r = c.post("/client/1", data={"seen": tab_a, "name": "Lane family", "address": "", "notes": "", "source": "Fair"},
               environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 409 and "This client " + CLASH in page
    for name, mine in (("name", "Lane family"), ("address", ""), ("notes", ""), ("source", "Fair")):
        assert f'<tr data-field="{name}" data-mine="{mine}">' in page
    assert '<a class="btn" href="/client/1">Reload and discard what I typed</a>' in page
