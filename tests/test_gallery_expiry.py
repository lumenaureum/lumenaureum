import os
import sys
import time
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from helpers import host_text

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "g-galExpiry01AAAAAAAAAAAA"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    Image.new("RGB", (32, 24), "red").save(src / "a.jpg", "JPEG")
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    from plugins import gallery as G
    key = G.new_key()
    G.build(GID, src, key)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id)
                   VALUES (1, 'family', 'delivered', '2026-01-10', ?)""", (GID,))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files) VALUES (1, ?, ?, 1)", (key, date.today().isoformat()))
    con.commit(); con.close()
    return A, c, G, host

def go_live(A, G, first_visit, extension=0):
    G.upload_gallery(GID, A.C.GALLERY_DAYS, "2099-01-01")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', uploaded_at=? WHERE session_id=1", (date.today().isoformat(),))
    con.execute("UPDATE sessions SET first_visit=?, extension_used=? WHERE id=1", (first_visit, extension))
    con.commit(); con.close()

def state(A):
    con = A.connect()
    r = con.execute("SELECT upload_state, removed_at, upload_error FROM galleries WHERE session_id=1").fetchone()
    con.close()
    return r

def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()

def gone_dark(host):

    return (host / GID).is_dir() and [p.name for p in (host / GID).iterdir()] == ["index.html"] \
        and "This gallery has gone dark." in (host / GID / "index.html").read_text()

def test_gallery_stays_through_its_last_day(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS))
    c.post("/gallery/sync", environ_base=E)
    assert (host / GID / "index.html").exists() and state(A)[0] == "up"

def test_gallery_is_removed_the_day_after_its_window(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    assert gone_dark(host)
    st, removed, _ = state(A)
    assert st == "removed" and removed == date.today().isoformat()

def test_extension_doubles_the_window_before_removal(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1), extension=1)
    c.post("/gallery/sync", environ_base=E)
    assert (host / GID / "index.html").exists() and state(A)[0] == "up"
    con = A.connect()
    con.execute("UPDATE sessions SET first_visit=? WHERE id=1", (days_ago(2 * A.C.GALLERY_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert gone_dark(host) and state(A)[0] == "removed"

def test_unopened_gallery_comes_down_after_a_year(env):
    A, c, G, host = env
    go_live(A, G, None)
    con = A.connect()
    con.execute("UPDATE galleries SET uploaded_at=? WHERE session_id=1", (days_ago(A.C.GALLERY_UNOPENED_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert gone_dark(host) and state(A)[0] == "removed"

def test_extension_doubles_the_host_deadline(env):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    assert f"days={A.C.GALLERY_DAYS}\n" in host_text(host / GID, "limits.txt")
    c.post("/session/1/quick", data={"act": "extend"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT extension_used FROM sessions WHERE id=1").fetchone()[0] == 1
    con.close()
    assert f"days={2 * A.C.GALLERY_DAYS}\n" in host_text(host / GID, "limits.txt")

def test_upload_error_text_is_bounded(env, monkeypatch):
    A, c, G, host = env

    def fail(*a, **k):
        raise RuntimeError("host said no " + "x" * 500)
    loaded = A.app.view_functions["upload_gallery_req"].__globals__
    monkeypatch.setitem(loaded, "upload_gallery", fail)
    c.post("/session/1/gallery/upload", environ_base=E)
    for _ in range(100):
        st, _, err = state(A)
        if st == "error":
            break
        time.sleep(0.05)
    assert st == "error"
    assert err.startswith("host said no") and len(err) == 200

def one_veto(A):
    con = A.connect()
    try:
        return con.execute("SELECT veto FROM sessions WHERE id=1").fetchone()[0]
    finally:
        con.close()

def _client_picks(G, key, picks, gid=GID):

    from helpers import seal_picks
    return seal_picks(key, gid, picks, "picks", at=None)

def test_picks_come_back_by_themselves(env):
    A, c, G, host = env
    go_live(A, G, None)
    assert (host / GID / "picks.php").is_file()
    con = A.connect()
    key = con.execute("SELECT key FROM galleries WHERE session_id=1").fetchone()[0]
    con.close()
    (host / GID / "picks.las").write_bytes(_client_picks(G, key, [
        {"i": 0, "name": "a.jpg", "r": True, "note": "soften the shadow", "m": True},
        {"i": 1, "name": "b.jpg", "v": True}]))
    c.post("/gallery/sync", environ_base=E)
    con = A.connect()
    r = con.execute("SELECT retouch_picks, favorites, veto FROM sessions WHERE id=1").fetchone()
    con.close()
    assert tuple(r) == ("a.jpg: soften the shadow", "a.jpg", "b.jpg")

    (host / GID / "picks.las").write_bytes(_client_picks(G, G.new_key(), [{"i": 0, "name": "x.jpg", "v": True}]))
    c.post("/gallery/sync", environ_base=E)
    con = A.connect()
    assert con.execute("SELECT veto FROM sessions WHERE id=1").fetchone()[0] == "b.jpg"
    con.close()

    (host / GID / "picks.las").write_bytes(_client_picks(G, key, [{"i": 0, "name": "y.jpg", "v": True}], gid="g-otherGalleryAAAAAAAAAAA"))
    c.post("/gallery/sync", environ_base=E)
    assert one_veto(A) == "b.jpg"
    with pytest.raises(Exception):
        G.open_picks(b'{"picks": []}', key, GID)
