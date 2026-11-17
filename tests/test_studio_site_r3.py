import pytest

from test_studio_page import E, env, session_photo, setting, switch_on, wait
from test_studio_site_release import upload

def q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def ex(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def reach_same_folder_another_way(tmp, web):

    from vault import web as VW
    alias = tmp / "web-alias"
    alias.symlink_to(web)
    cfg = VW.config()
    cfg["gallery_host"] = dict(cfg["gallery_host"], path=str(alias / "g"))
    VW.write_config(cfg)

def follow_the_refusals(A, c, S, web):

    import re
    for _ in range(5):
        upload(c, S)
        if setting(A, "site_state") != "error":
            return
        err = setting(A, "site_error")
        m = re.search(r"left as they are: (.*?)\. Move", err)
        assert m, err
        for n in m.group(1).replace(" and more", "").split(", "):
            (web / n).unlink()

def test_a_declined_photo_stays_reminded_after_the_host_settings_change(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE session_id=1")
    assert (web / name).exists()
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)
    reach_same_folder_another_way(tmp, web)
    follow_the_refusals(A, c, S, web)
    assert setting(A, "site_state") == "up"

    assert not (web / name).exists() or "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

def test_a_kept_declined_photo_stays_reminded_after_the_host_settings_change(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    upload(c, S)
    (name,), = q(A, "SELECT name FROM studio_files WHERE session_id=1")
    b = bytearray((web / name).read_bytes())
    b[-3] ^= 0xFF
    (web / name).write_bytes(bytes(b))
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert (web / name).exists() and not (web / "index.html").exists()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)
    reach_same_folder_another_way(tmp, web)
    upload(c, S)
    assert setting(A, "site_state") == "up"
    assert (web / name).exists()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

def add_and_upload(A, c, S, *names):
    switch_on(c)
    fids = [session_photo(A, n, color=(40 + 50 * i, 120, 200 - 40 * i)) for i, n in enumerate(names)]
    c.post("/settings/studio-page/photos/from/1", data={"file": fids}, environ_base=E)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    return [n for (n,) in q(A, "SELECT name FROM studio_files WHERE session_id=1 ORDER BY name")]

def spelled_as_typed(monkeypatch):

    from plugins import gallery as G
    real = G.host_identity
    monkeypatch.setattr(G, "host_identity", lambda cfg: real(cfg) if (cfg or {}).get("type") != "local"
                        else ("local", str((cfg or {}).get("path") or "")))

def test_a_folder_typed_another_way_is_the_same_host():
    from plugins import studio_page as S
    a = {"type": "sftp", "host": "Web.Example.com", "user": "pat", "root": "/srv/www/", "password": "x"}
    b = {"type": "sftp", "host": "web.example.com.", "user": "pat", "root": "/srv/www", "port": 22, "password": "y"}
    assert S.host_fingerprint(a) == S.host_fingerprint(b)
    assert S.host_fingerprint(a) != S.host_fingerprint(dict(b, user="sam"))

def test_the_same_folder_under_another_identity_carries_its_list_over_by_probe(env, monkeypatch):
    A, c, S, tmp, web = env
    spelled_as_typed(monkeypatch)
    (name,) = add_and_upload(A, c, S, "IMG_0001.jpg")
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    fps_before = {f for (f,) in q(A, "SELECT DISTINCT host_fp FROM studio_files")}
    reach_same_folder_another_way(tmp, web)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    fps_after = {f for (f,) in q(A, "SELECT DISTINCT host_fp FROM studio_files")}
    assert len(fps_after) == 1 and fps_after != fps_before
    assert not (web / name).exists()
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)
    assert not [p.name for p in web.iterdir() if p.name.startswith(".la-probe")]

def other_place(tmp):
    from vault import web as VW
    web2 = tmp / "web2"
    (web2 / "g").mkdir(parents=True)
    (web2 / "g" / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = dict(cfg["gallery_host"], path=str(web2 / "g"))
    VW.write_config(cfg)
    return web2

def test_another_place_keeps_the_earlier_hosts_files_and_takes_them_down_there(env):
    A, c, S, tmp, web = env
    (name,) = add_and_upload(A, c, S, "IMG_0001.jpg")
    ex(A, "UPDATE sessions SET portfolio_no=1 WHERE id=1")
    web2 = other_place(tmp)
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert (web2 / "index.html").exists() and (web / name).exists()
    assert q(A, "SELECT COUNT(*) FROM studio_files WHERE session_id=1")[0][0] == 1
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "earlier web host" in today and "no longer allows" in today
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "Left on an earlier web host" in page and "Take down there" in page and name in page
    (old_fp,) = {f for (f,) in q(A, "SELECT DISTINCT host_fp FROM studio_files WHERE session_id=1")}
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(kw) or True)
    try:
        c.post("/settings/studio-page/take-down-earlier", data={"host": old_fp}, environ_base=E)
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert held and held[0].get("remove") and (web / name).exists()
    c.post("/settings/studio-page/take-down-earlier", data={"host": old_fp}, environ_base=E)
    wait(S)
    assert not (web / name).exists() and not (web / "index.html").exists()
    assert (web / "g" / "abc123xyz" / "index.html").exists()
    assert (web2 / "index.html").exists()
    assert not q(A, "SELECT 1 FROM studio_files WHERE host_fp=?", (old_fp,))
    assert "no longer allows" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_a_refusal_names_the_apps_own_earlier_files_and_the_declined_ones(env, monkeypatch):
    A, c, S, tmp, web = env
    spelled_as_typed(monkeypatch)
    names = add_and_upload(A, c, S, "IMG_0001.jpg", "IMG_0002.jpg")
    ex(A, "UPDATE sessions SET veto='IMG_0001.jpg' WHERE id=1")
    declined = [n for (n,) in q(A, """SELECT f.name FROM studio_files f JOIN studio_photos p ON p.id=f.photo_id
                                      JOIN photo_files pf ON pf.id=p.file_id WHERE pf.name='IMG_0001.jpg'""")]
    assert len(declined) == 1 and declined[0] in names
    ex(A, "DELETE FROM settings WHERE key='site_hosts'")
    (web / "robots.txt").write_text("someone else's")
    ex(A, "DELETE FROM studio_files WHERE name='robots.txt'")
    reach_same_folder_another_way(tmp, web)
    upload(c, S)
    assert setting(A, "site_state") == "error"
    err = setting(A, "site_error")
    theirs = err.split("weren't put there by Lumen Aureum, so they are left as they are: ")[1].split(".")[0]
    assert theirs.strip() == "robots"
    said = {k: next(t for t in err.split(". ") if k in t) for k in ("no longer allows", "under your earlier", "weren't put")}
    assert declined[0] in said["no longer allows"]
    assert declined[0] not in said["under your earlier"] and declined[0] not in said["weren't put"]
    assert "index.html" in said["under your earlier"] and "index.html" not in said["weren't put"]
    assert (web / declined[0]).exists()
    assert "no longer allows" in c.get("/", environ_base=E).get_data(as_text=True)

@pytest.mark.parametrize("note", ["IMG_0042-Edit", "IMG_0042 copy", "IMG_0042-2", "IMG_0042_final.jpg"])
def test_a_version_named_holds_back_the_photo_and_all_its_versions(note):
    from plugins.studio_page import read_veto
    names = ["IMG_0042.jpg", "IMG_0042-Edit.jpg", "IMG_0042 copy.jpg", "IMG_0042-2.jpg", "IMG_0042_final.jpg",
             "IMG_00421.jpg", "IMG_0043.jpg"]
    for given in (names, None):
        v = read_veto(note, given)
        assert not v.unclear
        assert [n for n in names if v.hides(n)] == names[:5], (note, given)

def test_a_bare_number_after_a_word_is_another_photo_not_a_version():
    from plugins.studio_page import read_veto
    v = read_veto("shoot-2", ["shoot-1.jpg", "shoot-2.jpg"])
    assert v.hides("shoot-2.jpg") and not v.hides("shoot-1.jpg")

def gallery_with_picks(A, web, veto_names):
    import json as _j
    import secrets as _s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from plugins import gallery as G
    gid = "g-galStudio01AAAAAAAAAAAA"
    key = G.new_key()
    ex(A, "UPDATE sessions SET gallery_id=? WHERE id=1", (gid,))
    ex(A, "INSERT INTO galleries (session_id, key, upload_state, uploaded_at) VALUES (1, ?, 'up', '2026-11-01')", (key,))
    picks = [{"i": i, "name": n, "r": False, "m": False, "v": True} for i, n in enumerate(veto_names)]
    from helpers import seal_picks
    blob = seal_picks(key, gid, picks, "picks", "2026-11-14T15:00:00.000Z")
    (web / "g" / gid).mkdir(exist_ok=True)
    (web / "g" / gid / "picks.las").write_bytes(blob)

def test_a_dont_show_not_yet_collected_counts_before_the_build(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    gallery_with_picks(A, web, ["IMG_0001.jpg"])
    upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert not q(A, "SELECT 1 FROM studio_files WHERE session_id=1")
    assert not [p for p in (web / "photos").iterdir() if p.name.startswith("photo-")] if (web / "photos").is_dir() else True
    assert q(A, "SELECT veto FROM sessions WHERE id=1")[0][0] == "IMG_0001.jpg"

def test_a_dont_show_sent_while_the_upload_waits_counts_at_approval(env):
    A, c, S, tmp, web = env
    switch_on(c)
    fid = session_photo(A, "IMG_0001.jpg")
    c.post("/settings/studio-page/photos/from/1", data={"file": [fid]}, environ_base=E)
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(proceed) or True)
    try:
        c.post("/settings/studio-page/upload", environ_base=E)
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert setting(A, "site_state") == "awaiting"
    gallery_with_picks(A, web, ["IMG_0001-Edit.jpg"])
    held[0]()
    wait(S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert not q(A, "SELECT 1 FROM studio_files WHERE session_id=1")
