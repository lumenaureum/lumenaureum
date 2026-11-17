import json
import os
import re
import subprocess
from urllib.parse import urljoin

import pytest

from helpers_tools import need_tool

from test_studio_page import E, env, session_photo, setting, switch_on
from test_studio_site_r3 import ex, q
from test_studio_site_release import upload

TESTS = os.path.dirname(os.path.abspath(__file__))

def kept_on_the_host(A, web, name):

    b = bytearray((web / name).read_bytes())
    b[-3] ^= 0xFF
    (web / name).write_bytes(bytes(b))
    con = A.connect()
    try:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('site_left', ?)",
                    (json.dumps({q(A, "SELECT host_fp FROM studio_files WHERE name=?", (name,))[0][0]: {name: "kept"}}),))
        con.commit()
    finally:
        con.close()

def test_page_off_with_a_settings_problem_still_shows_what_to_act_on(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE session_id=1")
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    kept_on_the_host(A, web, name)
    c.post("/settings/studio-page/switch", data={}, environ_base=E)
    ex(A, "UPDATE settings SET value='' WHERE key='gallery_base_url'")
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "no longer allows" in today and "#publish" in today
    assert "An earlier web host" not in today
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert 'id="publish"' in page
    pub = page[page.index('id="publish"'):]
    assert "The gallery address in Online galleries must be set" in pub
    assert name in pub and "no longer allows" in pub
    assert "Remove it anyway" in pub and "Take down there" in pub
    assert "before your web host settings changed" not in pub and "Gone for good" not in pub
    fp = q(A, "SELECT host_fp FROM studio_files WHERE name=?", (name,))[0][0]
    c.post("/settings/studio-page/forget-host", data={"host": fp}, environ_base=E)
    assert q(A, "SELECT 1 FROM studio_files WHERE host_fp=?", (fp,))

def test_preview_not_found_page_is_styled_two_and_more_levels_down(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/preview", environ_base=E)
    for path in ("/studio-page/preview/a/b/nope", "/studio-page/preview/about/x/nope.html",
                 "/studio-page/preview/a/b/c/", "/studio-page/preview/photos/x/y.jpg"):
        r = c.get(path, environ_base=E)
        assert r.status_code == 404, path
        css = re.search(r'<link[^>]+href="([^"]*site\.css)"', r.get_data(as_text=True)).group(1)
        assert c.get(urljoin("https://localhost" + path, css), environ_base=E).status_code == 200, (path, css)
    r = c.get("/studio-page/preview/a/../../crm.db", environ_base=E)
    assert r.status_code == 404 and b"SQLite" not in r.data

def variable_font(lo, hi):
    import io
    import settings_io
    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables._f_v_a_r import Axis
    from plugins import gallery as G
    f = TTFont(io.BytesIO((G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()))
    fv = newTable("fvar")
    ax = Axis()
    ax.axisTag, ax.minValue, ax.defaultValue, ax.maxValue, ax.axisNameID = "wght", lo, max(lo, min(400, hi)), hi, 256
    fv.axes, fv.instances = [ax], []
    f["fvar"] = fv
    b = io.BytesIO()
    f.save(b)
    return settings_io.clean_font(b.getvalue())[0]

def test_a_light_file_beside_a_variable_regular_one_draws_the_light_text(tmp_path):
    import settings_io
    from plugins import studio_page as SP
    from plugins import gallery as G
    static = (G.ASSETS / "fonts" / "cormorant-garamond.woff2").read_bytes()
    var = variable_font(100, 900)
    assert settings_io.own_font_weights(var, None) == ("100 900", None)
    assert settings_io.own_font_weights(var, static) == ("400 900", "300")
    assert settings_io.own_font_weights(static, static) == ("400", "300")
    assert settings_io.own_font_weights(var, variable_font(200, 500)) == ("400 900", "200 399")
    assert settings_io.own_font_weights(variable_font(300, 400), static) == ("400", "300")
    conf = dict(name="Studio", owner="Studio", tagline="", intro="", description="", about="", email="", phone="",
                address="", site_url="", site_path="/", logo=None, palette=settings_io.palette("#0d0c0b"), dark=True,
                font="own", credit=False, favicon=None, year=2026, htaccess=False, php=False, inquiry=None,
                own_font={400: var, 300: static})
    SP.build_site(tmp_path / "site", conf, [])
    css = (tmp_path / "site" / "assets" / "site.css").read_text()
    faces = dict((m.group(2), m.group(1)) for m in re.finditer(r'font-weight: ([\d ]+); font-display: swap; src: url\((own-\d+)\.woff2\)', css))
    assert faces == {"own-400": "400 900", "own-300": "300"}
    assert ".text, .intro, .text .lede { font-weight: 300; }" in css

def test_the_send_button_reads_send_again_after_a_send():
    need_tool("node")
    from plugins import studio_page as S
    _, pub = S.new_inquiry_key()
    form = dict(name="Ada Lovelace", email="ada@example.org", phone="", type="", date="", message="Hello", website="",
                work=4)
    run = lambda f: json.loads(subprocess.run(["node", os.path.join(TESTS, "inquiry_harness.js"), str(S.ASSETS / "inquiry.js"),
                                                pub, json.dumps(f)], capture_output=True, text=True, timeout=60, check=True).stdout)
    sent = run(form)
    assert sent["msg"] == "Thanks." and len(sent["posted"]) == 1
    assert sent["busy"]["text"] == "Sending..."
    assert sent["after"] == dict(text="Send", aria=None, disabled=False)
    assert sent["cleared"]
    failed = run(dict(form, fail=True))
    assert failed["msg"] == "Failed." and failed["after"] == dict(text="Send", aria=None, disabled=False)
    assert not failed["cleared"]
