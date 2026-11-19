import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_client_page_141 import BASE, _session, _tab, browser, env

FIELD = "form[method=post] input[type=text]:not([autocomplete]):visible, form[method=post] textarea:visible"

def _first_field(p):
    el = p.locator(FIELD).first
    name = el.get_attribute("name")
    assert name, "the edit page has a plain text field to type into"
    return name

def test_typed_text_comes_back_after_a_reload_with_a_note(browser, env):
    A, c = env
    _session(A, "booked", days=10)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/session/1/edit")
        name = _first_field(p)
        f = p.locator(f"form[method=post] [name='{name}']").first
        f.fill("typed but never saved")
        p.reload()
        assert p.locator(f"form[method=post] [name='{name}']").first.input_value() == "typed but never saved"
        assert p.locator("[data-kept]").count() == 1
        assert "not saved yet" in p.locator("[data-kept]").inner_text()
    finally:
        ctx.close()

def test_nothing_typed_means_no_note_and_saved_text_is_not_kept(browser, env):
    A, c = env
    _session(A, "booked", days=10)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/session/1/edit")
        p.reload()
        assert p.locator("[data-kept]").count() == 0

        name = _first_field(p)
        f = p.locator(f"form[method=post] [name='{name}']").first
        was = f.input_value()
        f.fill(was + "x")
        f.fill(was)
        p.reload()
        assert p.locator("[data-kept]").count() == 0
    finally:
        ctx.close()

def test_a_codeword_is_never_kept(browser, env):
    A, c = env
    _session(A, "booked", days=10)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/session/1/edit")
        cw = p.locator("input[name=gallery_codeword]")
        if not cw.count():
            return
        cw.first.fill("heron maple tide")
        kept = p.evaluate("Object.keys(sessionStorage).filter(k => k.startsWith('la-keep:'))"
                          ".map(k => sessionStorage.getItem(k)).join(' ')")
        assert "heron" not in kept
    finally:
        ctx.close()

def test_saving_one_photo_description_keeps_what_was_typed_in_the_others(browser, tmp_path, monkeypatch):

    import io
    from PIL import Image
    from test_user_testing_fixes import _app
    monkeypatch.setenv("CRM_SITE_ROOT", str(tmp_path / "site"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    A, c = _app(tmp_path, monkeypatch)
    from test_user_testing_fixes import E as E2
    c.post("/settings/studio-page/switch", data={"use": "1"}, environ_base=E2)
    for n in ("one", "two", "three"):
        b = io.BytesIO()
        Image.new("RGB", (400, 300), (120, 90, 60)).save(b, "JPEG")
        b.seek(0)
        c.post("/settings/studio-page/photos/add", data={"photos": (b, f"{n}.jpg")}, environ_base=E2,
               content_type="multipart/form-data")
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/settings/studio-page")
        boxes = p.locator("#photos input[name=alt]")
        assert boxes.count() == 3
        boxes.nth(0).fill("A towboat on the Ohio")
        boxes.nth(1).fill("A bridge from the deck")
        boxes.nth(2).fill("A meadow portrait")
        p.locator("#photos button[value=alt]").nth(1).click()
        p.wait_for_load_state()
        p.wait_for_timeout(300)
        vals = [p.locator("#photos input[name=alt]").nth(i).input_value() for i in range(3)]
        assert vals == ["A towboat on the Ohio", "A bridge from the deck", "A meadow portrait"]
        assert p.locator("[data-kept]").count() == 0
    finally:
        ctx.close()
