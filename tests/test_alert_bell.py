import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    A.app.config["conn_mark"]("primary", True)
    monkeypatch.setattr(A, "STARTED_AT", time.monotonic())
    yield A, c
    A.PLUGIN_ERRORS.clear()

def _set(A, k, v):
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    con.commit()
    con.close()

def _sql(A, sql, args=()):
    con = A.connect()
    con.execute(sql, args)
    con.commit()
    con.close()

def _page(c, url="/alerts"):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def _fix(c):
    p_ = _page(c)
    return p_.split('id="alerts-fix"', 1)[1].split("</section>", 1)[0] if 'id="alerts-fix"' in p_ else ""

def _look(c):
    p_ = _page(c)
    return p_.split('id="alerts-look"', 1)[1].split("</section>", 1)[0] if 'id="alerts-look"' in p_ else ""

def _bell(page):
    return page.split('id="alert-bell"', 1)[1].split("</a>", 1)[0]

def test_the_bell_is_in_the_top_bar_on_every_page_and_on_a_phone(env):
    A, c = env
    for url in ("/", "/clients", "/settings"):
        page = _page(c, url)
        assert 'id="alert-bell"' in page and 'href="/alerts"' in page, url
        bar = page.split('<nav class="top', 1)[1].split("</nav>", 2)[1]
        tag = bar.split('id="alert-bell"', 1)[0].rsplit("<a ", 1)[1]
        assert "ui-nav-wide" not in tag and "data-nav-wide" not in tag

def test_no_bell_before_setup_is_finished(env):
    A, c = env
    _set(A, "setup_complete", "0")
    with A.app.test_request_context():
        assert A.system_alerts() == []

def test_today_has_no_setting_up_section(env):
    A, c = env
    _set(A, "walk", "{}")
    today = _page(c, "/")
    assert 'id="setup"' not in today and "Setting up" not in today and "Walk through your session types" not in today
    assert "Review your prices" not in today and "/today/setup-dismiss" not in today
    assert "has not been walked through yet" in _look(c)

def test_badge_counts_what_needs_fixing_and_a_dot_means_only_worth_a_look(env):
    A, c = env
    _set(A, "walk", "{}")
    bell = _bell(_page(c, "/"))
    assert "ui-bell-n" not in bell and "ui-bell-dot" in bell and "that can wait" in bell
    _set(A, "photo_mirror_error", "OSError: disk full")
    _set(A, "backup_last_error", "OSError: disk full")
    bell = _bell(_page(c, "/"))
    assert '<span class="ui-bell-n">2</span>' in bell and 'aria-label="2 alerts to fix"' in bell

def test_a_backup_location_never_tested_or_failing_needs_fixing_until_a_test_passes(env):
    A, c = env
    _set(A, "conn_status", "{}")
    fix = _fix(c)
    assert "Your backup location has never been tested." in fix and 'href="/setup/storage#f-primary_type"' in fix
    A.app.config["conn_mark"]("primary", False, "the folder is not there")
    assert "The last test of your backup location failed: the folder is not there" in _fix(c)
    A.app.config["conn_mark"]("primary", True)
    assert "backup location" not in _fix(c)

def test_a_failed_backup_needs_fixing_until_one_works(env):
    A, c = env
    from vault import web as VW
    VW._record_error(A.connect, "OSError: no space left")
    fix = _fix(c)
    assert "The last backup failed: OSError: no space left" in fix and 'href="/vault"' in fix
    assert "The last backup failed" in _page(c, "/vault")
    VW._record_run(A.connect, datetime.now())
    assert "The last backup failed" not in _page(c)

def test_a_backup_more_than_two_days_old_needs_fixing_once_the_app_has_run_a_while(env, monkeypatch):
    A, c = env
    assert "No backup has been made yet." not in _fix(c)
    monkeypatch.setattr(A, "STARTED_AT", time.monotonic() - 3 * 3600)
    assert "No backup has been made yet." in _fix(c)
    old = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    _sql(A, "INSERT INTO vault_objects (kind, name, backend, size, created) VALUES ('db', 'db-old', 'local', 1, ?)", (old,))
    assert "more than 2 days ago" in _fix(c)
    _sql(A, "INSERT INTO vault_objects (kind, name, backend, size, created) VALUES ('db', 'db-new', 'local', 1, ?)",
         (datetime.now().isoformat(timespec="seconds"),))
    fix = _fix(c)
    assert "more than 2 days ago" not in fix and "No backup" not in fix

def test_the_second_copy_and_the_photo_mirror(env):
    A, c = env
    from vault import web as VW
    _set(A, "backup_second_error", "timed out")
    assert "second backup copy" not in _fix(c)
    cfg = VW.config()
    cfg["secondary"] = {"type": "local", "path": "/nowhere"}
    VW.write_config(cfg)
    fix = _fix(c)
    assert "Your second backup copy could not be checked" in fix
    assert "Your second backup location has never been tested." in fix and "#f-secondary_type" in fix
    _set(A, "photo_mirror_error", "OSError: gone")
    assert "The last copy of your photos to the backup failed." in _fix(c)
    _set(A, "photo_mirror_error", "")
    _set(A, "backup_second_error", "")
    assert "photos" not in _fix(c) and "second backup copy" not in _fix(c)

def test_no_master_key_means_no_backups(env):
    A, c = env
    _sql(A, "DELETE FROM settings WHERE key='vault_master_pub'")
    assert "Backups are not running: there is no master key yet." in _fix(c)

def test_a_plugin_that_failed_to_load(env):
    A, c = env
    A.PLUGIN_ERRORS.append("mine.py: SyntaxError")
    fix = _fix(c)
    assert "A plugin of yours did not load at the last start: mine.py: SyntaxError." in fix and 'href="/settings/plugins"' in fix
    assert "mine.py: SyntaxError" in _page(c, "/settings/plugins")

def test_the_locked_studio_notice_failing_its_test(env):
    A, c = env
    for k_, v_ in (("ntfy_server", "https://ntfy.example"), ("ntfy_topic", "studio-xyz")):
        _set(A, k_, v_)
    assert "test notice" not in _fix(c)
    A.app.config["conn_mark"]("ntfy", False, "connection refused")
    fix = _fix(c)
    assert "The last test notice to your phone could not be sent: connection refused" in fix and "#notice" in fix

def test_the_web_host_unreachable_at_the_last_check(env, tmp_path):
    A, c = env
    from vault import web as VW
    from plugins.gallery import fingerprint_of
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(tmp_path / "web")}
    VW.write_config(cfg)
    A.app.config["conn_mark"]("gallery", True)
    ok = dict(kind="ok", version="8.2", gate=True)
    _set(A, "gallery_host_caps", json.dumps(dict(fp=fingerprint_of(cfg["gallery_host"]), setup=ok, now=ok)))
    assert "web host" not in _fix(c)
    _set(A, "gallery_host_caps", json.dumps(dict(fp=fingerprint_of(cfg["gallery_host"]), setup=ok,
                                                 now=dict(kind="unreachable", version=""))))
    fix = _fix(c)
    assert "Your web host could not be reached at the last daily check." in fix and 'href="/setup/galleries"' in fix
    A.app.config["conn_mark"]("gallery", False, "no answer")
    assert "The last test of your web host failed: no answer" in _fix(c)

def test_a_session_type_not_walked_through_links_to_its_walk_through(env):
    A, c = env
    _set(A, "walk", "{}")
    look = _look(c)
    key = next(k_ for k_ in A.C.SESSION_TYPES if k_ != A.C.PERSONAL_TYPE["key"])
    lab = A.C.SESSION_TYPES[key]
    assert f"{lab}: this session type has not been walked through yet." in look
    assert f'href="/settings/type/{key}/walk/0"' in look
    _set(A, "walk", json.dumps({key: {"walked": "test"}}))
    assert f"{lab}: this session type" not in _look(c)

def test_master_key_ask_next_week_returns_to_the_alerts(env):
    A, c = env
    _sql(A, "UPDATE users SET created=?", ((A.today() - timedelta(days=30)).isoformat(),))
    look = _look(c)
    assert "Check you still have your master key." in look and ">Ask next week</button>" in look
    r = c.post("/security/master-key/later", data={"back": "/alerts"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/alerts")
    assert "master key" not in _page(c)

def test_nothing_to_fix_says_so(env, monkeypatch):
    A, c = env
    monkeypatch.setattr(A, "system_alerts", lambda: [])
    page = _page(c)
    assert "Nothing to fix." in page and 'id="alerts-fix"' not in page
    assert "Client work is on" in page
