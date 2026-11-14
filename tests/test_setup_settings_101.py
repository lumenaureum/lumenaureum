"""1.0.1 fixes to setup, Settings, time zone, the session Delivery row, and the restore docs:
- the storage step never pre-fills a backup folder its own check refuses, and the refusal says what would work
- "Use the defaults" on the last setup step finishes setup
- the ... step menu names itself and says what its choices do
- Delete the demo clients works when a demo client has photos (it was a 500: database is locked)
- the Delivery checklist on the session page has its own layout, not the stage track's
- the time zone: the browser's zone from the wizard, a one-time Settings prompt for installs without one
- docs: security.md points to the one set of restore steps in operations.md, which names every file"""
import io
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}


def _env(tmp_path, monkeypatch, tz=None):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    if tz:
        monkeypatch.setenv("TZ", tz)
    else:
        monkeypatch.delenv("TZ", raising=False)
    fresh_modules()
    import app as A
    return A


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """An install part way through setup (no time zone, nothing saved)."""
    A = _env(tmp_path, monkeypatch)
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    return A, signed_in_client(A, setup_done=False), tmp_path


@pytest.fixture
def done(tmp_path, monkeypatch):
    """A finished install with no time zone saved (as an install set up before 1.0.1 can be)."""
    A = _env(tmp_path, monkeypatch)
    return A, signed_in_client(A), tmp_path


def post(c, url, **data):
    return c.post(url, data=data, environ_base=E)


# ------------------------------------------------------------------ the backup folder

def test_a_folder_inside_the_program_is_refused_with_a_folder_that_would_work(fresh):
    A, c, tmp = fresh
    import settings_io
    import setup
    bad = os.path.join(ROOT, "data", "vault")                 # the old default on an install run from its own folder
    t = post(c, "/setup/test/primary", primary_type="local", primary_path=bad, primary_init="1").get_json()
    assert not t["ok"]
    msg = t["message"]
    assert "Choose a folder outside both" in msg and ROOT in msg
    ex = setup.suggested_vault()
    assert ex and ex in msg and not settings_io._inside_private(ex)
    assert not os.path.exists(bad)                            # nothing was made there


def test_the_storage_step_never_offers_a_folder_it_refuses(fresh, monkeypatch):
    """When the default vault folder sits inside the data or program folder, the step offers one that passes."""
    A, c, tmp = fresh
    import settings_io
    real = settings_io._inside_private
    data_vault = str(tmp / "vault")                           # the built-in default (beside crm.db)
    monkeypatch.setattr(settings_io, "_inside_private", lambda p: os.path.realpath(p) == data_vault or real(p))
    page = c.get("/setup/storage", environ_base=E).get_data(as_text=True)
    offered = re.search(r'name="primary_path" placeholder="/vault" value="([^"]*)"', page).group(1)
    assert offered and offered != data_vault and not settings_io._inside_private(offered)
    assert offered == os.path.join(os.path.dirname(os.path.realpath(ROOT)), "lumen-aureum-vault")


def test_a_valid_default_is_kept(fresh):
    A, c, tmp = fresh
    page = c.get("/setup/storage", environ_base=E).get_data(as_text=True)
    assert f'value="{tmp / "vault"}"' in page                # beside crm.db, outside the program: accepted as it is
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(tmp / "vault"), primary_init="1", backup_time="02:40")
    assert r.status_code == 302, r.get_data(as_text=True)


# ------------------------------------------------------------------ "Use the defaults" on the last step

def test_use_the_defaults_on_the_last_step_finishes_setup(fresh):
    A, c, tmp = fresh
    r = post(c, "/setup/network", skip="1")                  # required steps not done yet: says which, doesn't loop
    assert r.status_code == 200 and "Still needed before you can start" in r.get_data(as_text=True)
    post(c, "/setup/business", business_name="North Light", photographer_name="Sam", timezone="America/New_York")
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(tmp / "vault"), primary_init="1", backup_time="02:40")
    assert r.status_code == 302
    r = post(c, "/setup/network", skip="1")
    assert r.status_code == 302 and "/setup/" not in r.headers["Location"]
    con = A.connect()
    try:
        assert con.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()[0] == "1"
    finally:
        con.close()
    assert c.get("/", environ_base=E).status_code == 200      # no longer sent back to setup


def test_use_the_defaults_on_an_earlier_step_still_moves_on(fresh):
    A, c, _ = fresh
    r = post(c, "/setup/shoot", skip="1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup/money")


# ------------------------------------------------------------------ the ... step menu

def test_the_step_menu_names_itself_and_its_choices(done):
    A, c, _ = done
    with A.app.test_request_context():
        mod = A.app.jinja_env.get_template("_macros.html").make_module({"OUTCOMES": A.OUTCOMES})
        html = str(mod.close_menu({"what": "Files delivered", "close": {"session_id": 1, "step": "files"}}, "/"))
    summ = re.search(r"<summary([^>]*)>", html).group(1)
    assert 'aria-label="More for Files delivered: mark it waived, not needed, or client declined"' in summ
    assert "data-tip=" in summ and 'title="More"' not in summ
    assert "Close this step without doing it:" in html
    for word in ("Waived", "Not needed", "Client declined"):
        assert f">{word}</button>" in html


# ------------------------------------------------------------------ Delete the demo clients

def _demo_with_photo(A):
    import datakey
    import photostore
    con = A.connect()
    try:
        hid = con.execute("INSERT INTO households (name, demo) VALUES ('Demo Family', 1)").lastrowid
        keep = con.execute("INSERT INTO households (name) VALUES ('Real Client')").lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'shot')", (hid,)).lastrowid
        con.execute("INSERT INTO events (name, date, demo) VALUES ('Demo day', '2026-12-01', 1)")
        con.commit()
        body = b"F" * 1000
        r = photostore.start(con, sid, "raw", "a.NEF", len(body), master_pub=datakey.master_pub())
        photostore.append(con, r, 0, io.BytesIO(body), len(body))
    finally:
        con.close()
    return hid, keep, sid


def test_delete_the_demo_clients_with_photos_works_and_says_so(done):
    """It was a bare 500: the photo cleanup opened its own connection to write while the Settings save held the
    database in an open write, so it waited, then failed with "database is locked"."""
    A, c, _ = done
    hid, keep, sid = _demo_with_photo(A)
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    form = re.search(r'<form[^>]*action="/settings/demo/delete"[^>]*>', page, re.S).group(0)
    assert "data-confirm=" in form and "can&#39;t be undone" in form.replace("can't", "can&#39;t")
    assert 'name="clear_demo"' not in page                   # no longer a checkbox inside the General form
    A.app.config["PROPAGATE_EXCEPTIONS"] = True
    r = post(c, "/settings/demo/delete")
    assert r.status_code == 302
    import jobs
    j = jobs.JOBS["demo-delete"]
    assert j["state"] == "done" and j["message"] == "Deleted the 1 demo client and their photos.", j
    assert j["done"] == j["total"] == 2
    con = A.connect()
    try:
        assert not con.execute("SELECT 1 FROM households WHERE demo=1").fetchone()
        assert not con.execute("SELECT 1 FROM events WHERE demo=1").fetchone()
        assert not con.execute("SELECT 1 FROM photo_files WHERE session_id=?", (sid,)).fetchone()
        assert con.execute("SELECT 1 FROM households WHERE id=?", (keep,)).fetchone()      # real clients stay
    finally:
        con.close()
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    assert "/settings/demo/delete" not in page                # nothing left to delete: the card is gone
    assert "Deleted the 1 demo client and their photos." in page   # the result, in the job panel
    r = post(c, "/settings/demo/delete")                     # a second click: a plain answer, nothing deleted
    assert r.status_code == 302


def test_a_failed_photo_cleanup_keeps_the_demo_clients_and_says_why(done):
    A, c, _ = done
    hid, keep, sid = _demo_with_photo(A)

    def broken(_sid):
        raise OSError("vault unreachable")
    A.app.config["retention_purge_now"] = broken
    post(c, "/settings/demo/delete")
    import jobs
    j = jobs.JOBS["demo-delete"]
    assert j["state"] == "error" and "the demo clients were kept" in j["message"]
    con = A.connect()
    try:
        assert con.execute("SELECT 1 FROM households WHERE id=?", (hid,)).fetchone()
    finally:
        con.close()


# ------------------------------------------------------------------ the Delivery row

def test_the_session_delivery_checklist_has_its_own_layout(done):
    A, c, _ = done
    con = A.connect()
    try:
        hid = con.execute("INSERT INTO households (name) VALUES ('Park Family')").lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (?, 'portrait', 'shot', '2026-11-01')",
                          (hid,)).lastrowid
        con.commit()
    finally:
        con.close()
    page = c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)
    sec = page[page.index('id="delivery"'):]
    sec = sec[:sec.index("</section>")]
    assert '<ul class="dtrack">' in sec and 'class="track"' not in sec
    assert '<span class="dlab">Files delivered by</span>' in sec
    ul = sec[sec.index('<ul class="dtrack">'):sec.index("</ul>")]
    assert 'type="hidden"' not in ul                          # only list items inside the list
    css = (Path(ROOT) / "static" / "style.css").read_text()
    assert ".dtrack li { display: flex" in css
    assert not re.search(r"^\s*\.track li \{", css, re.M)     # the stage track's .track is no longer shared


# ------------------------------------------------------------------ the time zone

def test_settings_asks_once_for_a_time_zone_and_issued_dates_stay(done):
    A, c, _ = done
    assert A.TZ.key == "UTC"
    con = A.connect()
    try:
        hid = con.execute("INSERT INTO households (name) VALUES ('Park Family')").lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'shot')", (hid,)).lastrowid
        con.execute("INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (?, 'INV', 'INV-2026-0001', '2026-11-14')", (sid,))
        con.commit()
    finally:
        con.close()
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    assert 'id="tz"' in page and "Set your time zone" in page and "already issued keep the dates" in page
    assert "setup.js" in page                                 # preselects the browser's zone
    with A.app.test_request_context():
        items = [t for t, _ in A.app.config["finish_items"]()]
    assert any("time zone" in t for t in items)
    r = post(c, "/settings/timezone", timezone="Not/AZone")
    assert r.status_code == 302 and A.TZ.key == "UTC"
    r = post(c, "/settings/timezone", timezone="", browser_tz="America/New_York")
    assert r.status_code == 302 and A.TZ.key == "America/New_York"
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    assert 'id="tz"' not in page                              # once set, the prompt is gone for good
    assert "Time zone set to America/New_York" in page
    con = A.connect()
    try:
        assert con.execute("SELECT issued FROM doc_numbers WHERE session_id=?", (sid,)).fetchone()[0] == "2026-11-14"
        assert con.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()[0] == "America/New_York"
    finally:
        con.close()
    with A.app.test_request_context():
        assert not any("time zone" in t for t, _ in A.app.config["finish_items"]())


def test_the_machine_time_zone_is_used_and_offered_first(tmp_path, monkeypatch):
    A = _env(tmp_path, monkeypatch, tz="America/Chicago")
    c = signed_in_client(A)
    A.apply_settings()
    assert A.TZ.key == "America/Chicago"
    page = c.get("/settings", environ_base=E).get_data(as_text=True)
    sel = re.search(r'<select id="tz-pick"[^>]*>', page).group(0)
    assert 'data-keep="1"' in sel and "<option selected>America/Chicago</option>" in page
    post(c, "/settings/timezone", timezone="America/Denver")
    import setup
    assert A.TZ.key == "America/Denver" and setup.host_tz() == "America/Chicago"   # the machine's own is remembered


def test_the_wizard_takes_the_browser_zone(fresh):
    A, c, _ = fresh
    page = c.get("/setup/business", environ_base=E).get_data(as_text=True)
    assert 'name="browser_tz"' in page and "Filled in from this device" in page
    assert 'data-keep' not in re.search(r'<select name="timezone"[^>]*>', page).group(0)
    r = post(c, "/setup/business", business_name="B", photographer_name="P", timezone="", browser_tz="America/Denver")
    assert r.status_code == 302 and A.TZ.key == "America/Denver"
    page = c.get("/setup/business", environ_base=E).get_data(as_text=True)
    assert 'data-keep="1"' in re.search(r'<select name="timezone"[^>]*>', page).group(0)   # a saved zone wins
    js = (Path(ROOT) / "static" / "setup.js").read_text()
    assert "resolvedOptions().timeZone" in js and "browser_tz" in js


# ------------------------------------------------------------------ the restore docs

def test_security_doc_points_to_the_one_set_of_restore_steps():
    sec = (Path(ROOT) / "docs" / "security.md").read_text()
    part = sec[sec.index("## Restoring a backup"):]
    part = part[:part.index("\n## ", 3)]
    assert "operations.md#restoring-a-backup" in part
    assert "deletions.json" in part and "doc-numbers.json" in part
    assert "put `crm.db`, `keyring.json` and `storage.json` in a fresh data folder" not in part


def test_operations_doc_lists_every_file_and_numbered_steps():
    ops = (Path(ROOT) / "docs" / "operations.md").read_text()
    part = ops[ops.index("## Restoring a backup"):]
    part = part[:part.index("\n## ", 3)]
    for f in ("crm.db", "keyring.json", "settings.json", "storage.json", "deletions.json", "deletions.unverified.json",
              "doc-numbers.json"):
        assert f"`{f}`" in part, f
    assert "asks for your password" in part and "`db/` included" in part and "vault.cli backups" in part
    steps = re.findall(r"^(\d+)\. \*\*", part, re.M)
    assert steps == [str(i) for i in range(1, len(steps) + 1)] and len(steps) >= 8
    assert "—" not in part
