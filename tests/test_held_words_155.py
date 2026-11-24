import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_three import _remove

def _state(site, sid):
    return tuple(site.q("SELECT upload_state, upload_error FROM galleries WHERE session_id=?", (sid,))[0])

def _client_page(site, sid):
    hid = site.q("SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]
    return site.c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)

def _hold(site, answer, kept):
    def guard(proceed, **kw):
        kept.append(kw)
        return answer
    site.A.hooks.guards.setdefault("gallery_upload", []).append(guard)

def test_held_with_the_plugins_words_then_given_up(site):
    sid = site.gallery("Held With Words")
    _remove(site, sid)
    kept = []
    _hold(site, "Waiting for the second look", kept)
    try:
        site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    finally:
        site.A.hooks.guards["gallery_upload"].pop()
    assert _state(site, sid) == ("awaiting", "Waiting for the second look")
    assert "Waiting for the second look" in _client_page(site, sid)
    kept[0]["give_up"]("Not this time")
    assert _state(site, sid) == ("", "Not this time")
    page = _client_page(site, sid)
    assert "Not this time" in page and "Upload failed" not in page

def test_held_without_words_says_on_hold(site):
    sid = site.gallery("Held Plainly")
    _remove(site, sid)
    _hold(site, True, [])
    try:
        site.c.post(f"/session/{sid}/gallery/back-online", data={"days": "7"}, environ_base=E)
    finally:
        site.A.hooks.guards["gallery_upload"].pop()
    assert _state(site, sid) == ("awaiting", "")
    assert "Upload on hold" in _client_page(site, sid)

def test_session_sheet_places_carry_a_time_and_a_note(site):
    sid = site.gallery("Sheet Places")
    site.q("UPDATE sessions SET stops=? WHERE id=?", ("North Lawn\nOld Mill", sid))
    page = site.c.get(f"/session/{sid}/details", environ_base=E).get_data(as_text=True)
    assert "North Lawn" in page and "Old Mill" in page
    site.A.hooks.detail_locations.append(lambda s: [dict(time="4:10 PM", name="North Lawn", note="Wide lawn\nSoft light")])
    try:
        page = site.c.get(f"/session/{sid}/details", environ_base=E).get_data(as_text=True)
    finally:
        site.A.hooks.detail_locations.pop()
    assert "4:10 PM" in page and "Wide lawn<br>Soft light" in page and "Old Mill" not in page
