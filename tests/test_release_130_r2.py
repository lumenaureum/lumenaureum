import os
import re
import shutil
import sys
from datetime import timedelta

import pytest

from helpers_tools import need_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c, tmp_path

def run(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def one(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def _client(A, sid=1, stage="shot"):
    run(A, "INSERT OR IGNORE INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (?, 1, 'portrait', ?)", (sid, stage))

def test_a_resume_after_a_restored_database_starts_over_under_a_fresh_key(env):

    A, c, tmp = env
    import photostore as P
    _client(A)
    rec = P.REC
    data = os.urandom(3 * rec + 777)
    h = {"X-File-Name": "IMG_1.NEF"}
    q = f"kind=raw&size={len(data)}"
    assert c.get(f"/session/1/photos/status?{q}", headers=h, environ_base=E).get_json()["received"] == 0

    def up(off, n):
        return c.post(f"/session/1/photos/upload?{q}&offset={off}", data=data[off:off + n],
                      headers=dict(h, **{"Content-Type": "application/octet-stream"}), environ_base=E)
    assert up(0, rec).get_json()["received"] == rec
    backup = tuple(one(A, "SELECT fkey, received, ct_len, records FROM photo_files"))
    fid = one(A, "SELECT id FROM photo_files")[0]
    first_old = P.path_of(fid).read_bytes()[:8 + rec + 16]
    assert up(rec, rec).get_json()["received"] == 2 * rec
    run(A, "UPDATE photo_files SET fkey=?, received=?, ct_len=?, records=?, state='partial'", backup)
    st = c.get(f"/session/1/photos/status?{q}", headers=h, environ_base=E).get_json()
    assert st["received"] == 0
    assert one(A, "SELECT fkey FROM photo_files")[0] != backup[0]
    off = 0
    while off < len(data):
        off = up(off, P.PIECE).get_json()["received"]
    assert P.path_of(fid).read_bytes()[:8 + rec + 16] != first_old
    con = A.connect()
    try:
        assert P.plain_bytes(con.execute("SELECT * FROM photo_files").fetchone()) == data
    finally:
        con.close()

@pytest.mark.parametrize("sfs, extra", [("cross-site", {}), ("same-site", {}), (None, {"Referer": "https://evil.example/"}),
                                         (None, {"Origin": "null"})])
def test_downloads_that_write_history_refuse_other_sites(env, sfs, extra):
    A, c, _ = env
    _client(A)
    hdrs = dict(extra, **({"Sec-Fetch-Site": sfs} if sfs else {}))
    n = one(A, "SELECT COUNT(*) FROM audit")[0]
    for url in ("/client/1/export.json", "/settings/export.json", f"/money/accounts.zip?year={A.today().year}"):
        assert c.get(url, headers=hdrs, environ_base=E).status_code == 403, url
    assert one(A, "SELECT COUNT(*) FROM audit")[0] == n
    assert c.get("/client/1/export.json", headers={"Sec-Fetch-Site": "same-origin"}, environ_base=E).status_code == 200

def test_every_master_key_field_is_hidden(env):
    A, c, _ = env
    pages = [c.get("/security/master-key", environ_base=E).get_data(as_text=True),
             A.app.test_client().get("/unlock/master", environ_base=E).get_data(as_text=True)]
    for page in pages:
        fields = re.findall(r'<input[^>]*name="secret"[^>]*>', page)
        assert fields and all('type="password"' in f for f in fields), fields

def test_wrong_master_keys_say_how_long_and_never_lock_sign_in(env):
    A, c, _ = env
    wrong = "AGE-SECRET-KEY-1" + "Q" * 58
    for _ in range(5):
        r = c.post("/security/master-key", data={"secret": wrong}, environ_base=E)
    page = r.get_data(as_text=True)
    assert "Too many wrong keys" in page and re.search(r"Try again in \d+ minutes", page)
    assert not A.app.config["auth_throttle"].ip_blocked("127.0.0.1")
    c.post("/vault/check", data={"secret": wrong}, environ_base=E)
    assert re.search(r"Try again in \d+ minutes", c.get("/vault", environ_base=E).get_data(as_text=True))

def test_unattended_unlock_at_start_refuses_a_key_file_on_the_data_volume(env, monkeypatch):

    A, c, tmp = env
    import datakey
    other = tmp / "other"
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(other / "unlock.key"))
    real_check = datakey.check_auto_place
    datakey.check_auto_place = lambda p: None
    try:
        datakey.enable_auto()
    finally:
        datakey.check_auto_place = real_check
    on_data = tmp / "unlock.key"
    shutil.copyfile(other / "unlock.key", on_data)
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(on_data))
    with pytest.raises(ValueError):
        datakey.check_auto_place(on_data)
    assert datakey.try_auto_unlock() is None

def test_a_refused_key_file_place_leaves_nothing_behind(env, monkeypatch):
    A, c, tmp = env
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(tmp / "sub" / "unlock.key"))
    r = c.post("/security", data={"current": TEST_PASSWORD, "act": "auto_on"}, environ_base=E)
    assert b"different volume" in r.data
    assert not (tmp / "sub").exists()

def test_forgot_password_fields_have_labels(env):
    A, c, _ = env
    page = A.app.test_client().get("/unlock/master", environ_base=E).get_data(as_text=True)
    for field in re.findall(r"<input(?![^>]*type=\"hidden\")[^>]*>", page):
        fid = re.search(r'id="([^"]+)"', field)
        assert "aria-label" in field or (fid and f'for="{fid.group(1)}"' in page), field

def test_security_page_fields_have_labels(env):
    A, c, _ = env
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    for field in re.findall(r"<input(?![^>]*type=\"hidden\")[^>]*>", page):
        fid = re.search(r'id="([^"]+)"', field)
        assert "aria-label" in field or (fid and f'for="{fid.group(1)}"' in page), field

def test_a_contact_with_no_phone_or_email_gets_no_empty_link(env):
    A, c, _ = env
    _client(A, stage="booked")
    run(A, "INSERT INTO people (household_id, name, role) VALUES (1, 'Dana Lane', 'parent')")
    page = c.get("/session/1", environ_base=E).get_data(as_text=True)
    assert not re.search(r'<a href="mailto:">\s*</a>', page)

def _ratio(a, b):
    def lum(h):
        v = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    x, y = sorted((lum(a), lum(b)))
    return (y + 0.05) / (x + 0.05)

def test_the_gallery_credit_small_print_reads():
    src = open(os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")).read()
    small = re.search(r"#la small \{[^}]*color: #([0-9a-f]{6})", src).group(1)
    assert _ratio(small, "141210") >= 4.5 and _ratio(small, "12100e") >= 4.5

def test_the_preview_placeholder_pages_have_lang_and_viewport(env):
    A, c, _ = env
    r = c.get("/studio-page/preview/", environ_base=E)
    page = r.get_data(as_text=True)
    assert "<html lang=" in page and 'name="viewport"' in page

try:
    from playwright import sync_api
except ImportError:
    sync_api = None

def test_client_faces_on_the_month_calendar_keep_their_contrast(env):
    from urllib.parse import urlsplit
    from test_release_130_browser import CONTRAST, KEEP
    A, c, _ = env
    run(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    run(A, "INSERT INTO people (household_id, name, role) VALUES (1, 'Sam Doe', 'parent')")
    day = A.today() - timedelta(days=1) if A.today().day > 1 else A.today()
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'portrait', 'delivered', ?)",
        (day.isoformat(),))
    base = "https://app.test"
    if sync_api is None:
        pytest.skip("playwright is not installed")
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    try:
        ctx = b.new_context()

        def handle(route, request):
            u = urlsplit(request.url)
            r = c.get(u.path + ("?" + u.query if u.query else ""), environ_base=E)
            route.fulfill(status=r.status_code, body=r.data, headers={k: v for k, v in r.headers.items() if k.lower() in KEEP})
        ctx.route(base + "/**", handle)
        p = ctx.new_page()
        p.goto(base + f"/calendar?m={day.isoformat()[:7]}")
        ratios = p.evaluate(CONTRAST, ".mshoot.done span.av")
        assert ratios and min(ratios) >= 4.5, ratios
    finally:
        b.close()
        pw.stop()

def test_a_refused_start_says_why_on_sign_in_and_security(env, monkeypatch, capsys):
    A, c, tmp = env
    import datakey
    other = tmp / "other"
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(other / "unlock.key"))
    real_check = datakey.check_auto_place
    datakey.check_auto_place = lambda p: None
    try:
        datakey.enable_auto()
    finally:
        datakey.check_auto_place = real_check
    assert datakey.try_auto_unlock() is None
    say = "Unattended unlock is off: the key file is on the data folder. Move it to its own volume."
    assert datakey.auto_refused() == say
    assert "WARNING: " + say in capsys.readouterr().out
    assert say in c.get("/security", environ_base=E).get_data(as_text=True)
    page = A.app.test_client().get("/login", environ_base=E).get_data(as_text=True)
    assert say in page

def test_the_container_filesystem_is_named_at_start(env, monkeypatch):
    A, c, tmp = env
    import datakey
    monkeypatch.setattr(datakey, "check_auto_place", lambda p: (_ for _ in ()).throw(
        datakey.PlaceRefused("no", "the container's own filesystem")))
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(tmp / "k" / "unlock.key"))
    (tmp / "k").mkdir()
    (tmp / "k" / "unlock.key").write_text("x")
    ring = __import__("json").loads(datakey.KEYRING.read_text())
    monkeypatch.setattr(datakey, "_read", lambda *a: dict(ring, auto="x"))
    assert datakey.try_auto_unlock() is None
    assert "the key file is on the container's own filesystem" in datakey.auto_refused()

def test_a_memory_only_place_is_refused_and_nothing_is_made(env, monkeypatch):
    A, c, tmp = env
    import datakey
    from test_security_actions import other_volume_dir
    base = other_volume_dir(tmp)
    if not base:
        pytest.skip("no writable folder on a second volume")
    target = os.path.join(base, "la-r2-" + os.urandom(4).hex(), "keys")
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", os.path.join(target, "unlock.key"))
    monkeypatch.setattr(datakey, "_in_container", lambda: False)
    monkeypatch.setattr(datakey, "_mount_fstype", lambda p: "tmpfs")
    r = c.post("/security", data={"current": TEST_PASSWORD, "act": "auto_on"}, environ_base=E)
    assert b"emptied at every restart" in r.data
    assert not os.path.exists(os.path.dirname(target))

def test_mount_types_are_read_from_mountinfo():
    import datakey
    if datakey._mounts() is None:
        pytest.skip("no /proc/self/mountinfo here")
    assert datakey._mount_fstype("/proc/self") == "proc"

def test_wrong_reprint_keys_are_limited_like_every_master_key_check(env):
    A, c, _ = env
    _client(A)
    wrong = "AGE-SECRET-KEY-1" + "Q" * 58
    for _ in range(5):
        r = c.post("/session/1/unseal", data={"master": wrong}, environ_base=E)
    assert "unseal=slow" in r.headers["Location"]
    assert A.app.config["key_throttle"].ip_blocked("127.0.0.1")
    assert not A.app.config["auth_throttle"].ip_blocked("127.0.0.1")
    r = c.post("/session/1/unseal", data={"master": c.master_secret or wrong}, environ_base=E)
    assert "unseal=slow" in r.headers["Location"]

def test_the_try_that_locks_a_vault_check_says_how_long(env):
    A, c, _ = env
    wrong = "AGE-SECRET-KEY-1" + "Q" * 58
    for i in range(5):
        c.post("/vault/check", data={"secret": wrong}, environ_base=E)
        page = c.get("/vault", environ_base=E).get_data(as_text=True)
        if i < 4:
            assert "the master key this install uses" in page
    assert re.search(r"Try again in \d+ minutes", page) and "the master key this install uses" not in page

def _labeled(page):
    for field in re.findall(r"<input(?![^>]*type=\"hidden\")[^>]*>", page):
        fid = re.search(r'id="([^"]+)"', field)
        assert "aria-label" in field or (fid and f'for="{fid.group(1)}"' in page), field

def test_setup_and_reprint_fields_have_labels(env):
    A, c, _ = env
    with A.app.test_request_context("/"):
        from flask import render_template
        _labeled(render_template("auth_setup_verify.html", qr="", secret="ABC", err=False))
        _labeled(render_template("auth_setup_key.html", secret="AGE-SECRET-KEY-1X", err=False))
    import plugins.retention as R
    form = R.BLOCK[R.BLOCK.index("retention_unseal"):]
    form = form[:form.index("</form>")]
    for name in ("master", "only"):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', form).group(0)
        fid = re.search(r'id="([^"]+)"', field).group(1)
        assert f'for="{fid}"' in form

def test_upload_announcements_keep_every_sealed_and_problem_message(tmp_path):
    import subprocess
    node = need_tool("node")
    js = open(os.path.join(ROOT, "static", "photos.js")).read()
    part = js[js.index("const regions = {};"):js.index("const flying")]
    script = tmp_path / "q.js"
    script.write_text("""
const document = { getElementById: () => ({ isConnected: true, textContent: '' }) };
const setTimeout = () => {};                     // nothing is read out: everything waits in the queue
const run = new Function('document', 'setTimeout', %s + '; return { say, queue };');
const { say, queue } = run(document, setTimeout);
say('start');                                    // taken at once
for (const f of ['a', 'b', 'c']) for (const p of [0, 25, 50, 75]) say(f + ': ' + p, false, f);
for (const f of ['a', 'b', 'c']) say(f + ': sealed');
say('d: The photos folder is full.', true);
for (let i = 0; i < 9; i++) say('x' + i + ': sealed');
console.log(JSON.stringify(queue.map(m => m.msg)));
""" % __import__("json").dumps(part))
    out = __import__("json").loads(subprocess.run([node, str(script)], capture_output=True, text=True, check=True).stdout)
    assert out[:3] == ["a: 75", "b: 75", "c: 75"]
    assert ["a: sealed", "b: sealed", "c: sealed", "d: The photos folder is full."] == out[3:7]
    assert out[7:] == [f"x{i}: sealed" for i in range(9)]

def _photo(A, size):
    import io as _io
    import photostore
    con = A.connect()
    try:
        data = os.urandom(size)
        r = photostore.start(con, 1, "raw", "a.NEF", len(data))
        photostore.append(con, r, 0, _io.BytesIO(data), len(data))
        return con.execute("SELECT * FROM photo_files").fetchone(), data
    finally:
        con.close()

def _spoil_last_record(row):
    import photostore
    p = photostore.path_of(row["id"])
    b = bytearray(p.read_bytes())
    b[-3] ^= 0xFF
    p.write_bytes(bytes(b))

def test_a_damaged_record_later_in_a_file_is_caught_before_the_download(env):
    A, c, _ = env
    _client(A)
    row, data = _photo(A, (1 << 20) * 2 + 5)
    _spoil_last_record(row)
    r = c.get(f"/photo/{row['id']}", environ_base=E)
    assert r.status_code == 302
    assert "is damaged" in c.get(r.headers["Location"], environ_base=E, follow_redirects=True).get_data(as_text=True)

def test_a_large_damaged_file_ends_short_with_one_log_line(env, monkeypatch, caplog):
    A, c, _ = env
    _client(A)
    row, data = _photo(A, (1 << 20) * 2 + 5)
    _spoil_last_record(row)
    monkeypatch.setattr(A, "DOWNLOAD_VERIFY_MAX", 1)
    r = c.get(f"/photo/{row['id']}", environ_base=E)
    body = r.get_data()
    assert r.status_code == 200 and len(body) == 2 << 20 and body == data[:2 << 20]
    lines = [rec for rec in caplog.records if "stopped part way" in rec.getMessage()]
    assert len(lines) == 1 and lines[0].exc_info is None

def test_a_session_being_deleted_shows_so_and_never_a_404(env):
    A, c, _ = env
    _client(A, stage="booked")
    import jobs
    jobs.JOBS["session-delete:1"] = dict(key="session-delete:1", label="Deleting", state="running", done=0, total=0,
                                         message="", started=0, ended=None, link="")
    try:
        r = c.get("/session/1", environ_base=E)
        assert r.status_code == 200 and "This session is being deleted" in r.get_data(as_text=True)
    finally:
        jobs.JOBS.pop("session-delete:1", None)
    assert "being deleted" not in c.get("/session/1", environ_base=E).get_data(as_text=True)
