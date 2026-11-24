import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_271_155 import E, FLAG, built, env, join, online, rebuild_form, sealed, sql, text
from test_client_page_141 import BASE, _tab, browser
from test_money_audit_130 import household, run, session

def plugin_on(c, name):
    assert c.post("/settings/plugins", data={"name": name, "turn": "on"}, environ_base=E).status_code == 302

def test_a_gallery_delivered_with_use_the_proofs_is_offered_the_rebuild(env):

    A, c, hid, sid = env
    sql(A, "UPDATE sessions SET type='portrait', stage='shot' WHERE id=?", (sid,))
    built(A, c, sid)
    online(A, c, sid)
    sql(A, "UPDATE sessions SET favorites='img0.jpg' WHERE id=?", (sid,))
    assert "Use the proofs" in text(c, f"/c/{hid}?s={sid}")
    c.post(f"/session/{sid}/gallery/build", data={"from": "uploads", "proofs_ok": "1"}, environ_base=E)
    join("gallery", sid)
    join("gal-up", sid)
    G = A.app.config["gallery_module"]
    gid = sql(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert G.is_download(gid) and sql(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "up"
    sql(A, "UPDATE settings SET value='Another Studio' WHERE key='studio_name'")
    assert sealed(A, sid)["studio"] == "Sample Light Studio"
    assert FLAG in text(c, f"/c/{hid}?s={sid}"), "Gallery block never offers Rebuild with the new details"
    assert FLAG in text(c, "/galleries"), "Galleries page leaves it out of Rebuild all"
    assert "1 gallery shows old studio details" in text(c, "/"), "Today never mentions it"
    url, fields = rebuild_form(c, hid, sid)
    assert fields == {"from": "uploads", "proofs_ok": "1"}
    assert c.post("/galleries/rebuild-details", environ_base=E).status_code == 302
    join("gallery", sid)
    assert sealed(A, sid)["studio"] == "Another Studio" and G.is_download(gid)
    assert FLAG not in text(c, "/galleries")

def test_a_gallery_made_from_finals_since_deleted_is_not_rebuilt_from_the_proofs(env):

    import io
    from PIL import Image
    from test_photo_uploads import upload
    A, c, hid, sid = env
    sql(A, "UPDATE sessions SET type='portrait', stage='shot' WHERE id=?", (sid,))
    built(A, c, sid)
    sql(A, "UPDATE sessions SET favorites='img0.jpg' WHERE id=?", (sid,))
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 10, 10)).save(b, "JPEG")
    upload(c, sid, "img0-retouched.jpg", b.getvalue(), kind="final")
    sql(A, "UPDATE photo_files SET done_at='2999-01-01T00:00:00' WHERE session_id=? AND kind='final'", (sid,))
    c.post(f"/session/{sid}/gallery/build", data={"from": "finals"}, environ_base=E)
    join("gallery", sid)
    G = A.app.config["gallery_module"]
    assert G.is_download(sql(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0])
    sql(A, "DELETE FROM photo_files WHERE session_id=? AND kind='final'", (sid,))
    sql(A, "UPDATE settings SET value='Another Studio' WHERE key='studio_name'")
    assert FLAG not in text(c, f"/c/{hid}?s={sid}")
    assert FLAG not in text(c, "/galleries")

def test_equipment_price_and_crew_pay_typed_as_nan_are_refused_not_a_server_error(env):

    A, c, hid, sid = env
    plugin_on(c, "equipment")
    plugin_on(c, "crew")
    r = c.post("/equipment/add", data={"name": "Camera body", "price": "nan"}, environ_base=E)
    assert r.status_code == 302, f"Add equipment answered {r.status_code}"
    r = c.post("/crew/add", data={"name": "Alex Assistant", "rate": "NaN"}, environ_base=E)
    assert r.status_code == 302, f"Add to the crew answered {r.status_code}"

def test_equipment_price_and_crew_pay_round_half_up_like_the_rest_of_the_app(env):

    A, c, hid, sid = env
    plugin_on(c, "equipment")
    plugin_on(c, "crew")
    c.post("/equipment/add", data={"name": "Lens", "price": "12.345"}, environ_base=E)
    c.post("/crew/add", data={"name": "Sam Second", "rate": "12.345"}, environ_base=E)
    assert A.amount_in("12.345") == 12.35
    assert sql(A, "SELECT price_cents FROM equipment WHERE name='Lens'")[0][0] == 1235
    assert sql(A, "SELECT rate_cents FROM crew WHERE name='Sam Second'")[0][0] == 1235

def test_discard_puts_the_accent_color_choice_back_on_screen_too(env, browser, tmp_path):

    A, c, hid, sid = env
    sql(A, "INSERT INTO settings (key, value) VALUES ('site_on', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    ctx, page = _tab(browser, c, width=1280)
    try:
        page.goto(BASE + "/settings/studio-page")
        picker = page.locator("input[name=site_accent]")
        assert not picker.is_visible()
        page.locator("#words label:has-text('Your own')").first.click()
        assert picker.is_visible()
        page.locator("#words [data-ui-unsaved] button").click()
        page.wait_for_timeout(200)
        assert page.evaluate("document.querySelector('[name=site_accent_own]:checked').value") == "0"
        page.screenshot(path=os.path.join(str(tmp_path), "discard.png"),
                        full_page=True)
        assert not picker.is_visible(), "Gold is chosen but the color picker for Your own is still on screen"
    finally:
        ctx.close()

@pytest.fixture
def menv(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    return A, signed_in_client(A)

def test_search_finds_a_session_by_its_shoot_place(menv):

    A, c = menv
    household(A, 1, "Lopez")
    household(A, 2, "Nguyen")
    session(A, 1, 1, "booked", "2026-12-05", "family")
    session(A, 2, 2, "booked", "2026-12-05", "family")
    run(A, "UPDATE sessions SET stops='Riverside Park, north trail' WHERE id=1")
    run(A, "UPDATE sessions SET stops='Main Street studio' || char(10) || 'Riverside Park' WHERE id=2")
    page = c.get("/search?q=Riverside", environ_base=E).get_data(as_text=True)
    assert "Nothing found" not in page, "searching a shoot place finds nothing"
    assert "Lopez" in page and "Nguyen" in page
    assert "<mark>Riverside Park, north trail</mark>" in page and "<mark>Riverside Park</mark>" in page
    r = c.get("/search?q=north trail", environ_base=E)
    assert r.status_code == 302 and "/1" in r.location
