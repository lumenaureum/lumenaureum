import html
import io
import os
import re
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_photo_uploads import upload

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_personal": "1"}, environ_base=E)
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://gallery.test/g/')")
    hid = con.execute("INSERT INTO households (name) VALUES ('Lake Walk')").lastrowid
    sid = con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, delivery_method)
                         VALUES (?, 'portfolio', 'delivered', '2026-11-01', 'link')""", (hid,)).lastrowid
    con.commit(); con.close()
    return A, c, hid, sid

def text(c, path):
    t = c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))

def add_photos(c, sid, n=3):
    from PIL import Image
    for i in range(n):
        b = io.BytesIO()
        Image.new("RGB", (64, 48), (i * 20, 90, 120)).save(b, "JPEG")
        upload(c, sid, f"img{i}.jpg", b.getvalue(), kind="export")

def join(prefix, sid):
    for t in [t for t in threading.enumerate() if t.name == f"{prefix}-{sid}"]:
        t.join(timeout=60)
        assert not t.is_alive()

def tasks(page):

    return page.split(" Tasks ", 1)[1].split(" Timeline ", 1)[0]

def test_nothing_uploaded_raises_nothing_new(env):
    A, c, hid, sid = env
    assert "no gallery yet" not in text(c, f"/c/{hid}?s={sid}")
    assert "no gallery yet" not in text(c, "/")

def test_the_personal_project_path_is_findable_at_every_step(env):
    A, c, hid, sid = env
    add_photos(c, sid)

    page = text(c, f"/c/{hid}?s={sid}")
    assert "Nothing open" not in tasks(page)
    assert "3 edited photos uploaded, no gallery yet" in tasks(page)
    today = text(c, "/")
    assert "Waiting on the client or nothing to do now" not in today
    assert "3 edited photos uploaded, no gallery yet" in today
    edit = text(c, f"/session/{sid}/edit")
    assert "3 edited photos uploaded, no gallery yet" in edit and "Gallery block on the client page" in edit
    raw = c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)
    assert f'/c/{hid}?s={sid}#gallery' in raw
    assert "Gallery g-" not in edit

    if A.C.POLICY["portfolio"].get("gallery_codeword"):
        assert "Add the passphrase, then build" in tasks(page)
        r = c.post(f"/session/{sid}/codeword", data={"cw_make": "1", "back": f"/c/{hid}?s={sid}"}, environ_base=E)
        assert r.status_code in (200, 302)
        page = text(c, f"/c/{hid}?s={sid}")

    assert "Build download gallery" in tasks(page)
    raw = c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    form = re.search(r'<form method="post" action="/session/%d/gallery/build" class="ui-action"[^>]*>' % sid, raw)
    assert form and "data-ui-inplace" not in form.group(0)
    r = c.post(f"/session/{sid}/gallery/build", data={"from": "uploads"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/c/{hid}?s={sid}#gallery")
    join("gallery", sid)
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["state"] == "done"

    page = text(c, f"/c/{hid}?s={sid}")
    assert "Download gallery built, not online yet" in tasks(page) and "Put the gallery online" in tasks(page)
    assert "Download gallery built, not online yet" in text(c, "/")
    assert "Download gallery built, not online yet" in text(c, f"/session/{sid}/edit")
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    con = A.connect()
    assert con.execute("SELECT upload_state FROM galleries WHERE session_id=?", (sid,)).fetchone()[0] == "up"
    con.close()

    page = text(c, f"/c/{hid}?s={sid}")
    assert "not online yet" not in page and "no gallery yet" not in page
    edit = text(c, f"/session/{sid}/edit")
    assert "Gallery built" in edit and "Gallery block on the client page" in edit

def test_a_type_whose_step_carries_the_gallery_gets_no_second_row(env):

    A, c, hid, sid = env
    from plugins import gallery as G
    con = A.connect()
    con.execute("UPDATE sessions SET stage='shot' WHERE id=?", (sid,))
    con.commit(); con.close()
    add_photos(c, sid, 1)
    flow = A.C.WORKFLOW["portfolio"]
    saved = flow.get("shot")
    flow["shot"] = list(saved or []) + [("auto", "gallery_built")]
    try:
        page = text(c, f"/c/{hid}?s={sid}")
        assert "no gallery yet" not in page
        assert "Build gallery" in tasks(page)
    finally:
        flow["shot"] = saved

def test_held_for_payment_or_no_gallery_type_raises_nothing(env):
    A, c, hid, sid = env
    con = A.connect()
    con.execute("UPDATE sessions SET delivery_method='sd' WHERE id=?", (sid,))
    con.commit(); con.close()
    add_photos(c, sid, 1)
    assert "no gallery yet" not in text(c, f"/c/{hid}?s={sid}")
    assert "Gallery block on the client page" not in text(c, f"/session/{sid}/edit")
