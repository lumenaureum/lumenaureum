import io
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def studio(tmp_path, monkeypatch, setup_done=True):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    return A, signed_in_client(A, setup_done=setup_done)

def text(r):
    import html
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)).split()))

def notice(c):
    with c.session_transaction() as s:
        return s.get("notice") or ""

def test_a_recovery_code_works_without_its_hyphens_and_in_capitals(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    import auth
    codes, hashes = auth.new_recovery_codes(3)
    con = A.connect()
    con.execute("UPDATE users SET recovery=? WHERE id=1", (json.dumps(hashes),))
    con.commit()
    con.close()
    ok = A.app.config["second_factor_ok"]
    assert auth.recovery_form(codes[0].replace("-", "").upper()) == codes[0]
    assert ok(1, codes[0].replace("-", "").upper())
    assert ok(1, codes[1].replace("-", " "))
    assert ok(1, codes[2])
    assert not ok(1, codes[2])
    assert not ok(1, "0000-0000-0000")

@pytest.fixture
def keyed(monkeypatch):
    import plugin_install as P
    from test_300_plugin_install import PUB
    monkeypatch.setattr(P, "PLUGIN_SIGNING_KEY", PUB)
    return P

def test_a_refused_package_keeps_the_code_counts_nothing_and_keeps_the_link(keyed, tmp_path, monkeypatch):
    from test_300_plugin_install import build, recorded
    import auth
    A, c = studio(tmp_path, monkeypatch)
    th = A.app.config["auth_throttle"]
    before = len(th.log)
    code = auth.totp("JBSWY3DPEHPK3PXP")
    _m, data = build()
    for _ in range(6):
        c.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code=code,
                                                  package=(io.BytesIO(b"not a plugin"), "wrong.zip")),
               content_type="multipart/form-data", environ_base=E)
        assert "isn't a Lumen Aureum plugin" in notice(c) or "Nothing was added" in notice(c), notice(c)
    assert len(th.log) == before and not th.ip_blocked("127.0.0.1")
    c.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code=code,
                                              package=(io.BytesIO(data), "plugin.zip")),
           content_type="multipart/form-data", environ_base=E)
    assert "Added sample_tool 1.0.0" in notice(c), notice(c)
    assert recorded(A) == ["plugin_package_sample_tool"]
    link = "http://example.invalid/plugin.json"
    c.post("/settings/plugins/add", data=dict(how="link", link=link, current=TEST_PASSWORD, code=code), environ_base=E)
    assert "https://" in notice(c) and len(th.log) == before
    page = c.get("/settings/plugins", environ_base=E).get_data(as_text=True)
    assert f'value="{link}"' in page
    assert 'value="http' not in c.get("/settings/plugins", environ_base=E).get_data(as_text=True)

HALF = b'''def register(ctx):
    ctx.switchable(default=True, label="Half crash", about="Fails partway.")
    ctx.hooks.today.append(lambda: [dict(text="Half crash reminder", link="/settings")])
    ctx.on_unlock(lambda: None)

    @ctx.app.get("/halfcrash", endpoint="halfcrash_page")
    def halfcrash_page():
        return "halfcrash still serving"
    raise RuntimeError()
'''

def test_a_plugin_that_fails_partway_leaves_nothing_live_and_no_error_names(keyed, tmp_path, monkeypatch):
    from test_300_plugin_install import build, turn_on
    A, c = studio(tmp_path, monkeypatch)
    _m, data = build(name="halfcrash", files={"halfcrash.py": HALF})
    c.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code=_code(A),
                                              package=(io.BytesIO(data), "p.zip")),
           content_type="multipart/form-data", environ_base=E)
    assert "Added halfcrash" in notice(c), notice(c)
    turn_on(A, "halfcrash")
    A, c = studio(tmp_path, monkeypatch)
    assert "halfcrash" not in A.LOADED and "halfcrash" not in A.SWITCHES
    assert A.PLUGIN_ERRORS == ["halfcrash (added): it stopped with an error while starting (an unexpected error)"]
    assert c.get("/halfcrash", environ_base=E).status_code == 404
    assert "Half crash reminder" not in text(c.get("/", environ_base=E))
    page = text(c.get("/settings/plugins", environ_base=E))
    assert "Didn't load at the last start: it stopped with an error while starting" in page
    assert "RuntimeError" not in page and "Error:" not in page

def test_a_plugin_of_your_own_that_fails_partway_is_undone_too(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "halfcrash.py").write_bytes(HALF.replace(b"raise RuntimeError()", b"import not_a_module_here"))
    monkeypatch.setenv("CRM_PLUGIN_DIRS", str(mine))
    A, c = studio(tmp_path, monkeypatch)
    assert "halfcrash" not in A.LOADED and "halfcrash" not in A.SWITCHES
    assert A.PLUGIN_ERRORS == ["halfcrash.py: it stopped with an error while starting (it needs a part this studio "
                               "doesn't have (not_a_module_here))"]
    assert c.get("/halfcrash", environ_base=E).status_code == 404
    assert "Half crash reminder" not in text(c.get("/", environ_base=E))

def test_an_added_plugin_keeps_its_name_and_place_on_or_off(keyed, tmp_path, monkeypatch):
    from test_300_plugin_install import build, turn_on
    A, c = studio(tmp_path, monkeypatch)
    _m, data = build()
    c.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code=_code(A),
                                              package=(io.BytesIO(data), "p.zip")),
           content_type="multipart/form-data", environ_base=E)
    turn_on(A)

    def rows():
        with A.app.test_request_context():
            return [(r["name"], r["label"]) for r in A._plugin_rows()]
    A, c = studio(tmp_path, monkeypatch)
    rows_on = rows()
    assert rows_on[-1] == ("sample_tool", "Sample tool")
    c.post("/settings/plugins", data={"name": "sample_tool", "turn": "off"}, environ_base=E)
    A, c = studio(tmp_path, monkeypatch)
    assert "sample_tool" in A.ADDED_OFF and rows() == rows_on

def _code(A):
    from test_300_plugin_install import confirm
    return confirm(A)["code"]

def test_a_failed_backup_says_what_happened_without_an_error_name(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    import jobs
    from vault import backup as BK

    def refused(*a, **k):
        raise PermissionError(13, "Permission denied", "/backups/crm.db.age")
    monkeypatch.setattr(BK, "sealed_backup", refused)
    monkeypatch.delenv("CRM_JOBS_ASYNC", raising=False)
    c.post("/vault/backup-now", environ_base=E)
    msg = jobs.JOBS["backup-now"]["message"]
    assert msg.startswith("Backup failed: Permission denied: /backups/crm.db.age."), msg
    con = A.connect()
    kept = con.execute("SELECT value FROM settings WHERE key='backup_last_error'").fetchone()[0]
    con.close()
    assert "PermissionError" not in msg + kept and "Errno" not in msg + kept
    import errtext
    assert errtext.plain(PermissionError()) == "permission was refused"
    assert errtext.plain(KeyError("x")) == "an unexpected error"

def test_the_scheduled_backup_waits_for_setup_to_finish(tmp_path, monkeypatch):
    from datetime import datetime
    A, c = studio(tmp_path, monkeypatch, setup_done=False)
    from vault import backup as BK, web as W
    ran = []
    monkeypatch.setattr(BK, "sealed_backup", lambda *a, **k: ran.append(1))
    late = datetime(2026, 11, 26, 23, 0)
    assert W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=late) is False and not ran
    con = A.connect()
    assert not con.execute("SELECT 1 FROM settings WHERE key='backup_last_error'").fetchone()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    con.commit()
    con.close()
    assert W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=late) is True and ran

def test_back_to_the_master_key_page_mid_setup_does_not_say_setup_is_finished(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch, setup_done=False)
    r = c.get("/setup/owner/key", environ_base=E)
    assert r.status_code == 302 and "/security" not in r.headers["Location"]
    assert "Setup is finished" not in notice(c) and "Your account and master key are saved" in notice(c)
    r = c.get(r.headers["Location"], environ_base=E)
    assert r.status_code == 302 and "/setup" in r.headers["Location"]
    (tmp_path / "done").mkdir()
    A2, c2 = studio(tmp_path / "done", monkeypatch)
    r = c2.get("/setup/owner/key", environ_base=E)
    assert r.headers["Location"].endswith("/security") and "Setup is finished" in notice(c2)

def test_a_form_sent_to_a_locked_studio_leads_back_to_its_page_after_sign_in(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    import datakey
    from urllib.parse import parse_qs, urlsplit
    datakey.lock()

    def sent(referer):
        h = {"Referer": referer} if referer else {}
        r = c.post("/client/1", data={"notes": "typed"}, headers=h, environ_base=E)
        assert r.status_code == 302 and "/login" in r.headers["Location"]
        return parse_qs(urlsplit(r.headers["Location"]).query).get("next", [None])[0]
    assert sent("https://localhost/client/1?tab=notes") == "/client/1"
    assert sent("https://evil.example/client/1") is None
    assert sent("https://localhost//evil.example/x") is None
    assert sent("javascript:alert(1)") is None
    assert sent("") is None

def test_ui_js_keeps_the_typing_until_the_next_page_says_the_save_worked():
    js = open(os.path.join(ROOT, "static", "ui.js"), encoding="utf-8").read()

    assert "la-keep-sent" in js and "[data-refused], [data-keep-typing]" in js
    assert "if (f.closest('[data-inplace]')) { delete s[fid(f)]" in js
    assert "data-keep-typing" in open(os.path.join(ROOT, "templates", "auth_base.html"), encoding="utf-8").read()

def test_in_a_browser_typing_survives_a_save_sent_to_a_locked_studio(tmp_path, monkeypatch):
    from test_client_page_141 import BASE, _session, _tab, env as _env
    pytest.importorskip("playwright")
    from playwright import sync_api
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    try:
        A, c = _env.__wrapped__(tmp_path, monkeypatch)
        _session(A, "booked", days=10)
        ctx, p = _tab(b, c, width=1280)
        kept = "Object.keys(sessionStorage).filter(k => k.startsWith('la-keep:')).map(k => sessionStorage.getItem(k)).join(' ')"
        try:
            p.goto(BASE + "/session/1/edit")
            f = p.locator("form[method=post] textarea:visible").first
            f.fill("typed before the restart")
            import datakey
            key = datakey.dk()
            datakey.lock()
            with p.expect_navigation():
                f.evaluate("el => el.form.requestSubmit()")
            assert p.locator("body[data-keep-typing] input[name=password]").count() == 1
            assert "typed before the restart" in p.evaluate(kept)
            datakey.set_unlocked(key, ready=True)
            import auth
            with c.session_transaction() as s_:
                s_["uid"] = 1
                s_["sid"] = auth.start_session(1)
            p.goto(BASE + "/session/1/edit")
            assert p.locator("form[method=post] textarea:visible").first.input_value() == "typed before the restart"
            with p.expect_navigation():
                p.locator("form[method=post] textarea:visible").first.evaluate("el => el.form.requestSubmit()")
            p.goto(BASE + "/session/1/edit")
            assert "typed before the restart" not in p.evaluate(kept)
        finally:
            ctx.close()
    finally:
        b.close()
        pw.stop()

def _restore_run(tmp_path, code):
    from test_158_155 import SETUP, _install, run
    return run(SETUP + code, _install(tmp_path, str(tmp_path / "vault")))

def test_a_damaged_file_is_not_called_a_wrong_key_and_the_right_key_clears_the_count(tmp_path):
    out = _restore_run(tmp_path, """
import io
name = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
good = open(os.path.join(os.path.dirname(os.path.dirname(os.environ['CRM_DB'])), 'vault', name), 'rb').read()
th = A.app.config['key_throttle']
def up(data, secret=secret):
    r = c.post('/vault/restore', data={'go': 'check', 'secret': secret, 'file': (io.BytesIO(data), 'backup.age')},
               content_type='multipart/form-data', **ip())
    return r.status_code, ' '.join(re.sub(r'<[^>]+>', ' ', r.get_data(as_text=True)).split())
for what, data in (('empty', b''), ('text', b'just some notes'), ('cut', good[:len(good) // 2]),
                   ('flipped', good[:-40] + bytes([good[-40] ^ 1]) + good[-39:])):
    st, t = up(data)
    print(what, st, 'damaged or isn' in t, 'master key doesn' in t)
print('counted', len(th.log))
st, t = up(good, 'AGE-SECRET-KEY-1' + 'Q' * 58)
print('wrong key', st, 'master key doesn' in t, len(th.log))
st, t = up(good)
print('right key', st, len(th.log) and th.near, th.ip_blocked('127.0.0.1'))
""")
    for what in ("empty", "text", "cut", "flipped"):
        assert f"{what} 400 True False" in out, out
    assert "counted 0" in out and "wrong key 400 True 1" in out, out
    assert "right key 302 {} False" in out, out

def test_undoing_a_restore_keeps_the_galleries_it_would_have_taken_down(tmp_path):
    out = _restore_run(tmp_path, """
sql("INSERT INTO households (name) VALUES ('Client A')")
sql("INSERT INTO sessions (household_id, type) VALUES (1, 'senior')")
old = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
gallery(1, 'galleryone1')                                   # online now, not in the older backup
c.post('/vault/restore', data={'go': 'check', 'source': old, 'secret': secret}, **ip())
c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
signin()
print('mistake', [tuple(r_) for r_ in sql("SELECT gallery_id FROM gallery_leftovers")])
aside = R.set_aside_copies()
c = signed_in_client(A)
c.post('/vault/restore', data={'go': 'check', 'source': 'aside:' + aside[0]['stamp'], 'secret': secret}, **ip())
c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
signin()
print('undone', [tuple(r_) for r_ in sql("SELECT gallery_id FROM gallery_leftovers")],
      [tuple(r_) for r_ in sql("SELECT upload_state, removed_at FROM galleries WHERE session_id=1")])
""")
    assert "mistake [('galleryone1',)]" in out, out
    assert "undone [] [('up', None)]" in out, out

def test_the_restore_review_shows_a_new_client_on_the_studio_day(tmp_path):
    out = _restore_run(tmp_path, """
sql("INSERT INTO settings (key, value) VALUES ('timezone', 'America/New_York') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
sql("INSERT INTO households (name, created) VALUES ('Late client', '2026-11-26 03:30:00')")   # 10:30 pm on the 25th
con = A.connect()
print('newest', R.summary(con)['newest_client'])
con.close()
""")
    assert "newest 2026-11-25" in out, out

def test_the_right_master_key_on_the_backup_check_clears_the_wrong_tries(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    th = A.app.config["key_throttle"]
    c.post("/vault/check", data={"secret": "AGE-SECRET-KEY-1" + "Q" * 58}, environ_base=E)
    assert th.near
    c.post("/vault/check", data={"secret": c.master_secret}, environ_base=E)
    assert not th.near and not th.ip_blocked("127.0.0.1")

def test_error_pages_are_the_apps_own_with_a_way_back(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    for path, method, code in (("/client/999999", "get", 404), ("/no/such/page", "get", 404),
                               ("/settings/plugins/add", "get", 405), ("/search?q=" + "x" * 9000, "get", 414)):
        r = getattr(c, method)(path, environ_base=E)
        page = r.get_data(as_text=True)
        assert r.status_code == code, (path, r.status_code)
        assert f'data-error-page="{code}"' in page and "Back to Today" in page and "Lumen Aureum" in page, path
        assert "Werkzeug" not in page and "The requested URL" not in page, path

def test_a_plugin_file_too_large_says_the_plugin_is_too_large(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    monkeypatch.setitem(A.app.config, "MAX_CONTENT_LENGTH", 1024)
    r = c.post("/settings/plugins/add", data=dict(how="file", current=TEST_PASSWORD, code="123456",
                                                  package=(io.BytesIO(b"x" * 4096), "big.zip")),
               content_type="multipart/form-data", environ_base=E)
    assert r.status_code == 413
    t = text(r)
    assert "That plugin is larger than a plugin can be" in t and "Back to Plugins" in t
    r = c.post("/contact/new", data={"notes": "x" * 2048, "photo": (io.BytesIO(b"x" * 4096), "big.jpg")},
               content_type="multipart/form-data", environ_base=E)
    assert r.status_code == 413 and "larger than the studio takes" in text(r)

def test_an_over_long_field_gets_a_page_of_the_apps_own(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    r = c.post("/contact/new", data={"notes": "x" * 20001}, environ_base=E)
    t = text(r)
    assert r.status_code == 413 and "Too long to save" in t and "20,000 characters" in t and "Back to Today" in t
    assert "'notes'" not in t

def test_a_server_error_shows_no_trace(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    monkeypatch.setitem(A.app.config, "PROPAGATE_EXCEPTIONS", False)

    def boom():
        raise RuntimeError("secret detail in a trace")
    ep = next(r.endpoint for r in A.app.url_map.iter_rules() if r.rule == "/about")
    monkeypatch.setitem(A.app.view_functions, ep, boom)
    r = c.get("/about", environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 500 and 'data-error-page="500"' in page and "Back to Today" in page
    assert "secret detail" not in page and "Traceback" not in page and "RuntimeError" not in page

def test_gunicorn_lets_a_long_address_reach_the_apps_page():
    src = open(os.path.join(ROOT, "serve.py"), encoding="utf-8").read()
    assert '"--limit-request-line", "8190"' in src
    most = int(re.search(r"^MAX_ADDRESS = (\d+)", open(os.path.join(ROOT, "app.py"), encoding="utf-8").read(), re.M)[1])
    assert most < 8190 - len("GET  HTTP/1.1")

DIRECTION = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f"

def test_direction_characters_never_stay_in_a_file_name():
    import photostore
    assert photostore.clean_name("C:\\photos\\rtl\u202egpj.exe") == "rtlgpj.exe"
    assert not set(photostore.clean_name("a" + DIRECTION + "b.jpg")) & set(DIRECTION)

def test_direction_characters_never_show_in_the_csv_preview(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    data = ("Name,Notes\nAva\u202eSmith,likes\u2066 tea\u200f\n").encode()
    r = c.post("/clients/import", data={"file": (io.BytesIO(data), "list\u202e.csv")},
               content_type="multipart/form-data", environ_base=E)
    page = c.get(r.headers["Location"].split("#")[0], environ_base=E).get_data(as_text=True)
    assert "AvaSmith" in page and not set(page) & set(DIRECTION)

def test_restart_now_shows_only_when_a_restart_is_needed_and_restarts(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    import plugin_install as P
    asked = []
    monkeypatch.setattr(P, "can_restart", lambda: True)
    monkeypatch.setattr(P, "restart_soon", lambda: asked.append(1) or True)
    page = c.get("/settings/plugins", environ_base=E).get_data(as_text=True)
    assert 'id="restart-now"' not in page
    c.post("/settings/restart", environ_base=E)
    assert not asked and "Nothing is waiting for a restart." in notice(c)
    A.PLUGIN_CHANGES.append(True)
    page = c.get("/settings/plugins", environ_base=E).get_data(as_text=True)
    assert page.count('id="restart-now"') == 1
    assert 'data-confirm="Restart now? You&#39;ll sign in again."' in page or \
        "data-confirm=\"Restart now? You'll sign in again.\"" in page
    assert c.get("/settings/restart", environ_base=E).status_code == 405
    r = c.post("/settings/restart", headers={"Sec-Fetch-Site": "cross-site"}, environ_base=E)
    assert r.status_code == 403 and not asked
    r = c.post("/settings/restart", environ_base=E)
    assert r.status_code == 302 and asked == [1] and "The studio restarts now" in notice(c)
    con = A.connect()
    assert con.execute("SELECT action FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0] == "Restarted the studio"
    con.close()
    monkeypatch.setattr(P, "can_restart", lambda: False)
    assert 'id="restart-now"' not in c.get("/settings/plugins", environ_base=E).get_data(as_text=True)

def test_storage_json_defaults_to_beside_the_database_and_keeps_an_old_one(tmp_path):
    import subprocess
    data = tmp_path / "elsewhere" / "data"
    data.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k != "CRM_STORAGE_CFG"}
    env.update(CRM_TESTING="1", CRM_DB=str(data / "crm.db"))
    out = subprocess.run([sys.executable, "-c", "from vault import web as W, keys; import setup; "
                          "print(W.CFG_PATH); print(W.DEFAULT_CFG['primary']['path'])"],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == [str(data / "storage.json"), str(data / "vault")]
    from vault import web as W
    program = tmp_path / "program"
    (program / "data").mkdir(parents=True)
    assert W.default_cfg_path(data / "crm.db", program) == data / "storage.json"
    (program / "data" / "storage.json").write_text("{}")
    assert W.default_cfg_path(data / "crm.db", program) == program / "data" / "storage.json"
    (data / "storage.json").write_text("{}")
    assert W.default_cfg_path(data / "crm.db", program) == data / "storage.json"

def test_the_one_disk_check_follows_the_data_folder(tmp_path, monkeypatch):
    A, c = studio(tmp_path, monkeypatch)
    import setup
    from vault import web as W
    (tmp_path / "vault").mkdir()
    monkeypatch.setattr(W, "CFG_PATH", tmp_path / "storage.json")
    assert setup.backups_share_disk({"primary": {"type": "local", "path": str(tmp_path / "vault")}}) is True
