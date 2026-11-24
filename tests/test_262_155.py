import io
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
BASE = "https://app.test"
KEEP = ("content-type", "content-security-policy")
PASS = ("content-type", "x-file-name", "x-fetch")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

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
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Test Household')")
    _sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Sam Example', 'parent', 'sam@example.com')")
    _sql(A, "INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://photos.example/g/')")
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops, gallery_id)"
            " VALUES (1, 1, 'family', 'quoted', ?, '10:00', 'Park', 'g-testgallery01')",
         ((A.today() - timedelta(days=3)).isoformat(),))
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    return A, c

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(b, "JPEG")
    return b.getvalue()

def test_the_upload_script_is_on_a_client_page_before_it_has_photos(env):

    A, c = env
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert 'class="dropzone' not in page
    assert "/static/photos.js" in page

@pytest.fixture(scope="module")
def browser():
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def _tab(browser, c):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in PASS}
        r = (c.post(path, data=request.post_data_buffer or b"", headers=hdrs, environ_base=E) if request.method == "POST"
             else c.get(path, headers=hdrs, environ_base=E))
        if r.status_code in (301, 302, 303) and request.method == "POST":
            lu = urlsplit(r.location)
            r = c.get(lu.path + ("?" + lu.query if lu.query else ""), environ_base=E)
        if r.status_code in (301, 302, 303):
            loc = r.location
            route.fulfill(status=r.status_code, headers={"Location": BASE + loc if loc.startswith("/") else loc})
            return
        route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_browse_uploads_after_the_stage_moves_from_quoted_to_shot_in_place(browser, env):
    A, c = env
    ctx, p = _tab(browser, c)
    try:
        p.goto(BASE + "/c/1?s=1")
        assert not p.locator(".dropzone").count()
        p.evaluate("window.__stay = 1")

        p.evaluate("""() => { const f = document.createElement('form'); f.method = 'post'; f.action = '/session/1/stage';
            f.setAttribute('data-ui-inplace', '');
            f.innerHTML = '<input type="hidden" name="stage" value="shot"><input type="hidden" name="back" value="/c/1?s=1">'
              + '<button>Move to Shot</button>';
            document.querySelector('[data-ui-part]').appendChild(f); f.requestSubmit(); }""")
        p.locator("#dz-export-1-in").wait_for(state="attached", timeout=10000)
        assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "shot"
        assert p.evaluate("window.__stay") == 1
        p.set_input_files("#dz-export-1-in", files=[{"name": "img1.jpg", "mimeType": "image/jpeg", "buffer": jpeg()}])
        p.locator("#dz-export-1 .dz-row.ok, .ui-added").first.wait_for(timeout=15_000)
        assert p.evaluate("window.__stay") == 1
    finally:
        ctx.close()
    assert [tuple(r) for r in _sql(A, "SELECT name, kind, state FROM photo_files WHERE session_id=1")] == [("img1.jpg", "export", "done")]
