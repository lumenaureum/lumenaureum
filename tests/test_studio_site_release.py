import base64
import hashlib
import io
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_studio_page import E, add_own, env, session_photo, setting, switch_on, wait
from test_studio_inquiries import GOOD, put_inquiry

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "plugins" / "studio_assets"

def snap(d):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).rglob("*")) if p.is_file()}

def upload(c, S):
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)

def studio_names(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT name FROM studio_photos ORDER BY sort")]
    finally:
        con.close()

def set_veto(A, veto, sid=1):
    con = A.connect()
    con.execute("UPDATE sessions SET veto=? WHERE id=?", (veto, sid))
    con.commit()
    con.close()

def test_own_photos_are_turned_upright_flattened_and_stripped(env):
    A, c, S, tmp, web = env
    from PIL import Image
    switch_on(c)

    def enc(im, fmt, **kw):
        b = io.BytesIO()
        im.save(b, fmt, **kw)
        return b.getvalue()
    ex = Image.Exif()
    ex[0x0112] = 6
    ex[0x010F] = "CameraMaker"
    rotated = enc(Image.new("RGB", (1200, 600), (240, 240, 0)), "JPEG", exif=ex.tobytes())
    alpha = enc(Image.new("RGBA", (800, 600), (0, 0, 255, 128)), "PNG")
    cmyk = enc(Image.new("CMYK", (900, 600), (0, 200, 200, 0)), "JPEG")
    gif = enc(Image.new("RGB", (100, 100)), "GIF")
    add_own(c, rotated, alpha, cmyk, gif, b"not a picture")
    assert len(studio_names(A)) == 3
    c.post("/settings/studio-page/preview", environ_base=E)
    sizes = []
    for p in sorted((S.SITE_ROOT / "preview" / "photos").iterdir()):
        raw = p.read_bytes()
        im = Image.open(io.BytesIO(raw))
        assert im.format == "JPEG" and im.mode == "RGB" and not im.getexif() and b"CameraMaker" not in raw
        assert "icc_profile" not in im.info
        sizes.append(im.size)
    assert (550, 1100) in sizes

def test_a_cross_session_file_id_is_refused(env):
    A, c, S, tmp, web = env
    switch_on(c)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Other')")
    con.execute("INSERT INTO sessions (household_id, type, stage, portfolio_no) VALUES (2, 'family', 'delivered', 1)")
    con.commit()
    con.close()
    theirs = session_photo(A, "declined.jpg", sid=2)
    c.post("/settings/studio-page/photos/from/1", data={"file": [theirs]}, environ_base=E)
    assert studio_names(A) == []

def test_a_retouched_final_of_a_vetoed_photo_is_not_offered(env):
    A, c, S, tmp, web = env
    switch_on(c)
    session_photo(A, "IMG_0042.jpg")
    fin = session_photo(A, "IMG_0042-Edit.jpg", kind="final")
    set_veto(A, "IMG_0042.jpg")
    page = c.get("/settings/studio-page?from=1", environ_base=E).get_data(as_text=True)
    c.post("/settings/studio-page/photos/from/1", data={"file": [fin]}, environ_base=E)
    assert "IMG_0042-Edit.jpg" not in page and studio_names(A) == []

@pytest.mark.parametrize("veto", ["photos 2 and 3", "#2", "2, 3", "Photo #2"])
def test_a_veto_by_number_in_other_words_holds_every_photo_back(env, veto):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0002.jpg")
    set_veto(A, veto)
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    assert studio_names(A) == []

def test_foreign_files_in_the_sites_own_folders_survive_upload_and_take_down(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/save", data={"site_about": "Words.", "site_inquiries": "1"}, environ_base=E)
    add_own(c, __import__("test_studio_page").jpeg_with_metadata())
    foreign = {"assets/theirs.css": b"/* theirs */", "photos/keep.jpg": b"theirs", "about/team.html": b"theirs",
               "contact/old.html": b"theirs", "inquiries/README.txt": b"host note", "notes.txt": b"theirs"}
    for n, b in foreign.items():
        (web / n).parent.mkdir(parents=True, exist_ok=True)
        (web / n).write_bytes(b)
    g0 = snap(web / "g")
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert all((web / n).read_bytes() == b for n, b in foreign.items()) and snap(web / "g") == g0
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "down"
    assert all((web / n).read_bytes() == b for n, b in foreign.items()) and snap(web / "g") == g0
    assert not (web / "index.html").exists() and not (web / "inquiry.php").exists()

def test_a_foreign_robots_txt_stops_the_upload_before_anything_is_written(env):
    A, c, S, tmp, web = env
    switch_on(c)
    (web / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n")
    before = snap(web)
    upload(c, S)
    assert setting(A, "site_state") == "error" and "robots.txt" in setting(A, "site_error")
    assert snap(web) == before

def test_a_file_edited_on_the_host_is_not_replaced(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/save", data={"site_about": "Words."}, environ_base=E)
    upload(c, S)
    about = web / "about" / "index.html"
    about.write_bytes(about.read_bytes() + b"<!-- edited on the host -->")
    upload(c, S)
    assert b"edited on the host" in about.read_bytes()

def test_a_same_size_edit_on_the_host_is_left_at_take_down(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    idx = web / "index.html"
    orig = idx.read_bytes()
    edited = orig.replace(b"<body>", b"<BODY>", 1)
    assert len(edited) == len(orig) and edited != orig
    idx.write_bytes(edited)
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert idx.is_file() and idx.read_bytes() == edited

def test_take_down_never_leaves_waiting_inquiries_open_to_the_web(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, GOOD))
    (web / "inquiries" / ".rate").write_text("{}")
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    left = [p.name for p in (web / "inquiries").iterdir()] if (web / "inquiries").exists() else []
    assert not left or ".htaccess" in left

def test_collect_leaves_what_isnt_an_inquiry_and_survives_odd_entries(env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    good = put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, name="Zoë 山田", message="Café \U0001F4F7 日本 שלום")))
    oversize = put_inquiry(web, b"LAQ1" + os.urandom(S.INQ_MAX + 10))
    (web / "inquiries" / ("ef" * 16 + ".bin")).mkdir()
    (web / "inquiries" / "README.txt").write_text("host note")
    (web / "inquiries" / ("AB" * 16 + ".bin")).write_bytes(b"upper case: not a name inquiry.php makes")
    res = A.app.config["studio_page_collect"]()
    assert res == dict(added=1, dropped=0, bad=1), res
    assert not good.exists() and not oversize.exists()
    assert (web / "inquiries" / "README.txt").is_file() and (web / "inquiries" / ("AB" * 16 + ".bin")).is_file()
    c.post("/settings/studio-page/inquiries/review", data={"do": "add_all"}, environ_base=E)
    con = A.connect()
    name = con.execute("SELECT name FROM households WHERE source='Website'").fetchone()[0]
    log = con.execute("SELECT summary FROM log WHERE summary LIKE 'Inquiry from your website%'").fetchone()[0]
    con.close()
    assert name == "Zoë 山田" and "Café \U0001F4F7 日本 שלום" in log

def test_joiners_in_names_and_messages_are_kept():
    from plugins import studio_page as S
    family = "\U0001F468‍\U0001F469‍\U0001F467"
    word = "می‌خواهم"
    f, why = S.clean_inquiry(dict(GOOD, name="Ada " + family, message=word), [("family", "Family")])
    assert why == "" and family in f["name"] and word in f["message"]

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

@pytest.fixture
def php_site(tmp_path):

    if not shutil.which("php"):
        pytest.skip("php is not installed")
    site = tmp_path / "site"
    (site / "inquiries").mkdir(parents=True)
    from plugins import studio_page as S
    (site / "inquiry.php").write_text(S.inquiry_php_source("cd" * 16, WORK))
    (site / "_t.php").write_text("<?php $_SERVER['REMOTE_ADDR'] = $_SERVER['HTTP_X_T_ADDR'] ?? $_SERVER['REMOTE_ADDR'];"
                                 " include __DIR__ . '/inquiry.php';\n")
    port = _free_port()
    srv = subprocess.Popen(["php", "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)

    def post(body, addr=None, path="_t.php", ctype="application/x-www-form-urlencoded", method="POST"):
        h = {"Content-Type": ctype} if ctype else {}
        if addr:
            h["X-T-Addr"] = addr
        req = urllib.request.Request(f"http://127.0.0.1:{port}/{path}", data=body, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        yield site, post
    finally:
        srv.terminate()
        srv.wait(timeout=10)

WORK = 8

def _d(S, pub, blob=None, **kw):
    blob = blob or S.seal_inquiry(pub, dict(GOOD, **kw))
    return b"w=" + S.solve_work(blob, WORK).encode() + b"&d=" + base64.urlsafe_b64encode(blob).rstrip(b"=")

def test_inquiry_php_refuses_every_other_shape(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    d = _d(S, pub)
    assert post(None, method="GET", ctype=None) == 405
    assert post(d, method="PUT") == 405
    assert post(d, ctype="multipart/form-data; boundary=x") == 415
    assert post(d, ctype="text/plain") == 415
    assert post(d, path="_t.php?x=1") == 400
    assert post(d.replace(b"&d=", b"&d[]=")) == 400
    assert post(d + b"%3D") == 413
    assert post(b"d=" + b"A" * 2_000_000) == 413
    assert not list((site / "inquiries").glob("*.bin"))
    assert post(d, ctype="application/x-www-form-urlencoded; charset=UTF-8") == 204

def test_inquiry_php_holds_at_most_200_waiting(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    blob = S.seal_inquiry(pub, GOOD)
    for i in range(199):
        (site / "inquiries" / (f"{i:032x}.bin")).write_bytes(blob)
    assert post(_d(S, pub), addr="198.51.100.1") == 204
    assert post(_d(S, pub), addr="198.51.100.2") == 507
    assert len(list((site / "inquiries").glob("*.bin"))) == 200

def test_inquiry_php_counts_one_ipv6_network_as_one_sender(php_site):
    from plugins import studio_page as S
    site, post = php_site
    _, pub = S.new_inquiry_key()
    codes = [post(_d(S, pub), addr=f"2001:db8::{i:x}") for i in range(8)]
    assert codes.count(204) <= 5
