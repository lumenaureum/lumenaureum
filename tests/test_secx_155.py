import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
S3 = dict(type="s3", bucket="galleries", access_key="AK", secret_key="SK", endpoint="https://s3.example.com")

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import hostcheck
    monkeypatch.setattr(hostcheck, "capabilities", lambda *a, **kw: dict(kind="none", features=[], at="now"))
    return A, signed_in_client(A, setup_done=True)

def test_restore_folder_list_reads_only_the_db_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    fresh_modules()
    import restore
    vault = tmp_path / "vault"
    (vault / "db").mkdir(parents=True)
    (vault / "db" / "crm-2026-11-20-020000.age").write_bytes(b"sealed")
    (vault / "db" / "nested").mkdir()
    (vault / "db" / "nested" / "crm-2026-11-19-020000.age").write_bytes(b"sealed")
    (vault / "photos" / "2026" / "deep").mkdir(parents=True)
    (vault / "photos" / "2026" / "deep" / "a.bin").write_bytes(b"x")
    opened = []
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p=".": (opened.append(os.path.abspath(os.fspath(p))), real(p))[1])
    rows = restore.folder_backups(str(vault))
    assert [r["name"] for r in rows] == ["db/crm-2026-11-20-020000.age"]
    assert opened and all(p == str(vault / "db") for p in opened), opened

def test_restore_folder_list_of_the_top_folder_does_not_walk_the_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    fresh_modules()
    import restore
    monkeypatch.setattr(os, "walk", lambda *a, **kw: pytest.fail("walked the disk"))
    assert restore.folder_backups("/") == []
    assert restore.folder_backups(str(tmp_path / "not-there")) == []

def _on_s3(A):
    from vault import web as VW
    cur = VW.config()
    cur["gallery_host"] = dict(S3)
    VW.write_config(cur)

def _gallery(A, gid, state, word="maple lantern heron", uploaded=None, fixed=None, host_fp=None):
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, gallery_id, gallery_codeword) VALUES (1, 1, 'family', ?, ?)",
                (gid, word))
    con.execute("INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, n_files, host_fp, uploaded_at, cw_fixed) "
                "VALUES (1, 'k', 'e', '2026-11-17', ?, 3, ?, ?, ?)", (state, host_fp, uploaded, fixed))
    con.commit()
    con.close()

@pytest.mark.parametrize("state", ["uploading", "awaiting"])
def test_a_gallery_going_up_keeps_its_codeword_when_codewords_are_turned_off(tmp_path, monkeypatch, state):
    A, c = _app(tmp_path, monkeypatch)
    G = A.app.view_functions["build_gallery"].__globals__
    _gallery(A, "g-galGoingUpSecxAAAAAAAA", state)
    con = A.connect()
    try:
        assert G["codewords_waiting"](con) == []
        assert G["codewords_kept"](con) == 1
    finally:
        con.close()

def test_a_gallery_built_with_a_codeword_is_refused_on_s3_whatever_the_session_says(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _on_s3(A)
    G = A.app.view_functions["build_gallery"].__globals__
    gid = "g-galWordSecxAAAAAAAAAAA"
    _gallery(A, gid, "up", word="", uploaded="2026-11-17", fixed="word", host_fp="an-earlier-php-host")
    opened = []
    monkeypatch.setitem(G, "gallery_host", lambda *a: opened.append(1))
    with pytest.raises(RuntimeError) as e:
        G["upload_gallery"](gid)
    assert str(e.value) == G["WORD_REFUSED_STATIC"] and not opened

def test_move_galleries_leaves_a_gallery_online_with_a_codeword_the_session_lost(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _on_s3(A)
    G = A.app.view_functions["build_gallery"].__globals__
    gid = "g-galMovedSecxAAAAAAAAAA"
    _gallery(A, gid, "up", word="", uploaded="2026-11-17", fixed="word", host_fp="an-earlier-php-host")
    out = G["OUT_ROOT"] / gid
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.bin").write_bytes(b"x")
    uploaded = []
    monkeypatch.setitem(G, "upload_gallery", lambda *a, **kw: uploaded.append(a))
    monkeypatch.setitem(G, "gallery_host", lambda *a: object())
    import jobs
    ran = []

    def _start(app_, key, label, fn, **kw):
        with app_.test_request_context():
            ran.append(fn({}))
    monkeypatch.setattr(jobs, "start", _start)
    c.post("/galleries/move", environ_base=E)
    assert ran and not uploaded
    assert "Left on the earlier host, as this web host can't check codewords: Lane." in ran[0]
    con = A.connect()
    fp = con.execute("SELECT host_fp FROM galleries WHERE session_id=1").fetchone()[0]
    con.close()
    assert fp == "an-earlier-php-host"

def test_a_gallery_without_a_codeword_still_goes_to_s3(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _on_s3(A)
    G = A.app.view_functions["build_gallery"].__globals__
    gid = "g-galPlainSecxAAAAAAAAAA"
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, gallery_id, gallery_codeword) VALUES (1, 1, 'family', ?, '')", (gid,))
    con.execute("INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, n_files, cw_fixed) "
                "VALUES (1, 'k', 'k', '2026-11-17', 'built', 3, 'none')")
    con.commit()
    con.close()
    assert not G["has_codeword"](gid)

KEY_A = "web.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
KEY_B = "web.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
SAVED = dict(type="sftp", host="web.example.com", user="studio", root="g", key_file="", auth="password",
             password="the saved password", host_key=KEY_A)

def test_a_settings_file_with_another_server_key_does_not_keep_the_saved_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import settings_io
    file_host = {k: v for k, v in SAVED.items() if k != "password"}
    kept = settings_io._storage(dict(gallery_host=dict(file_host)), dict(gallery_host=dict(SAVED)))["gallery_host"]
    assert kept["password"] == "the saved password" and kept["host_key"] == KEY_A
    swapped = settings_io._storage(dict(gallery_host=dict(file_host, host_key=KEY_B)),
                                   dict(gallery_host=dict(SAVED)))["gallery_host"]
    assert swapped["host_key"] == KEY_A and swapped["password"] == "the saved password"
    bare = settings_io._storage(dict(gallery_host={k: v for k, v in file_host.items() if k != "host_key"}),
                                dict(gallery_host=dict(SAVED)))["gallery_host"]
    assert bare["host_key"] == KEY_A and bare["password"] == "the saved password"
    other = settings_io._storage(dict(gallery_host=dict(file_host, host="other.example.com", host_key=KEY_B)),
                                 dict(gallery_host=dict(SAVED)))["gallery_host"]
    assert not other.get("host_key") and not other.get("password")
    new = settings_io._storage(dict(gallery_host=dict(file_host, host_key=KEY_B)), {})["gallery_host"]
    assert not new.get("host_key") and not new.get("password")
    keyed = {k: v for k, v in file_host.items() if k != "auth"}
    for part in ("primary", "secondary", "gallery_host"):
        got = settings_io._storage({part: dict(keyed, host_key=KEY_B, known_hosts="/somewhere/known_hosts")}, {})[part]
        assert "host_key" not in got and "known_hosts" not in got
    mine = {k: v for k, v in SAVED.items() if k not in ("auth", "password")}
    got = settings_io._storage(dict(primary=dict(keyed, host_key=KEY_B)), dict(primary=mine))["primary"]
    assert got["host_key"] == KEY_A

def test_a_settings_file_is_written_without_any_server_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    import json
    import settings_io
    cfg = dict(primary=dict(SAVED, known_hosts="/somewhere/known_hosts"), gallery_host=dict(SAVED))
    out = settings_io.export(lambda k: None, [], cfg)
    text = json.dumps(out)
    assert out["storage"]["primary"]["host"] == "web.example.com"
    assert "host_key" not in text and "known_hosts" not in text and "ssh-ed25519" not in text
    assert "the saved password" not in text

def test_carrying_a_password_needs_the_same_account_and_the_same_server_key():
    from vault import backends as B
    new = {k: v for k, v in SAVED.items() if k != "password"}
    assert B.carry_secrets(SAVED, new)["password"] == "the saved password"
    assert "password" not in B.carry_secrets(SAVED, dict(new, host_key=KEY_B))
    assert "password" not in B.carry_secrets(SAVED, dict(new, known_hosts="/somewhere/else"))
    assert "password" not in B.carry_secrets(SAVED, dict(new, host="other.example.com"))
    s3 = dict(type="s3", bucket="b", access_key="AK", secret_key="SK")
    assert B.carry_secrets(s3, dict(type="s3", bucket="b", access_key="AK"))["secret_key"] == "SK"

from test_grp_wedding_155 import GID, call, gal, php_host, pick, put, seal

MAX_FILES = 64

def _las(folder):
    import re
    return [p_ for p_ in folder.iterdir() if re.fullmatch(r"[0-9a-f]{24}\.las", p_.name)]

def test_picks_php_stops_taking_new_devices_at_the_limit(php_host):
    base, d, proof, key = php_host
    codes = []
    for i in range(MAX_FILES + 6):
        dev = f"{i:016x}"
        codes.append(call(base + f"picks.php?dev={dev}", seal(key, [pick("a.jpg", 0, r=True)], dev=dev), proof)[0])
    assert codes[:MAX_FILES] == [204] * MAX_FILES
    assert set(codes[MAX_FILES:]) == {507}
    assert len(_las(d)) == MAX_FILES
    first = f"{0:016x}"
    again = seal(key, [pick("b.jpg", 1, r=True)], dev=first)
    assert call(base + f"picks.php?dev={first}", again, proof)[0] == 204
    assert len(_las(d)) == MAX_FILES and not [p_ for p_ in d.iterdir() if p_.name.endswith(".tmp")]

def test_the_app_reads_no_more_device_files_than_the_host_takes(gal, monkeypatch):
    A, c, _G, d, key = gal
    G = A.app.view_functions["build_gallery"].__globals__
    assert G["DEVICE_FILES_MAX"] == MAX_FILES
    for i in range(MAX_FILES + 20):
        dev = f"{i:016x}"
        put(d, key, f"picks-{dev}.las", seal(key, [pick("a.jpg", 0, r=True)], dev=dev))
    read = []
    real = G["_read"]
    monkeypatch.setitem(G, "_read", lambda host, n: (read.append(n), real(host, n))[1])
    found = G["device_files"](G["gallery_host"](), GID, key)
    assert len(read) == MAX_FILES and len(found) == MAX_FILES

def test_the_gallery_page_tells_a_refused_device_why():
    page = open(os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")).read()
    words = "This gallery has reached the most devices it can save picks from. Please ask your photographer."
    assert page.count(words) == 1 and "—" not in words
    assert page.count("r.status === 507") == 2
