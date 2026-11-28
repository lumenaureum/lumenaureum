import base64
import hashlib
import io
import json
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "_0fwTestAdopt"
BASE = "https://www.gallery.test/g/"

def _jpeg(size, color):
    from PIL import Image
    im = Image.new("RGB", size, color)
    for x_ in range(0, size[0], 29):
        im.putpixel((x_, x_ % size[1]), (255, 255, 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()

def legacy_gallery(folder, key, photos, title="Autumn Dance 2026"):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)))

    def seal(data):
        iv = secrets.token_bytes(12)
        return iv + aes.encrypt(iv, data, None)
    folder.mkdir(parents=True)
    used, items = set(), []

    def fresh():
        while True:
            n = secrets.token_hex(12) + ".bin"
            if n not in used:
                used.add(n)
                return n
    for name, data in photos:
        f, t, o = fresh(), fresh(), fresh()
        (folder / f).write_bytes(seal(data))
        (folder / t).write_bytes(seal(data))
        (folder / o).write_bytes(seal(data))
        items.append(dict(f=f, t=t, w=8, h=8, tw=8, th=8, o=o, ow=8, oh=8, n=name))
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_STORED) as z:
        for name, data in photos:
            z.writestr(name, data)
    part = fresh()
    (folder / part).write_bytes(seal(zb.getvalue()))
    m = dict(v=1, title=title, items=items, picks={}, dl=True,
             brand=dict(studio="Sample Studio", site="https://www.gallery.test/", date="November 14, 2026"),
             zip=dict(parts=[part], size=len(zb.getvalue())))
    (folder / "manifest.bin").write_bytes(seal(json.dumps(m, separators=(",", ":")).encode()))
    (folder / "index.html").write_text("<!DOCTYPE html>\n<html lang=\"en\"><head><title>Photos</title></head>"
                                       "<body><script>/* the old viewer */</script></body></html>\n")
    (folder / "seen.php").write_text("<?php\n// Records the first visit once.\nhttp_response_code(204);\n")
    (folder / "seen.key").write_text(hashlib.sha256(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))).hexdigest()[:32])
    (folder / ".htaccess").write_text("Options -Indexes\n")
    return m

def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(Path(folder).iterdir()) if p.is_file()}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1"}, environ_base=E)
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', ?)", (BASE,))
    con.execute("INSERT INTO households (name) VALUES ('Dance Committee')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Sample', 'adult', 'avery@example.com')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', ?)",
                ((date.today() - timedelta(days=4)).isoformat(),))
    con.commit(); con.close()
    from plugins import gallery as G
    with A.app.app_context():
        A.app.config["gallery_upgrade_run"]()
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    photos = [("IMG_0187.jpg", _jpeg((40, 30), "red")), ("IMG_0189.jpg", _jpeg((30, 40), "blue")),
              ("IMG_0193.jpg", _jpeg((36, 24), "green"))]
    m = legacy_gallery(host / GID, key, photos)
    return A, c, G, host, key, photos, m

def row(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def history(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit WHERE session_id=1 ORDER BY id").fetchall()]
    finally:
        con.close()

def adopt(c, link):
    return c.post("/session/1/gallery/adopt", data={"link": link, "back": "/c/1?s=1"}, environ_base=E)

def notice(c):
    with c.session_transaction() as fs:
        return fs.get("notice") or ""

def nothing_adopted(A):
    r = row(A, "SELECT s.gallery_id, g.adopted FROM sessions s LEFT JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    return not r[0] and not r[1]

def test_the_format_from_before_1_4_reads_in_full(env):
    A, c, G, host, key, photos, m = env
    from vault import backends as VB
    b = VB.from_config({"type": "local", "path": str(host), "public": True})
    before = snapshot(host / GID)
    got, fmt = G.adopt_manifest(b, GID, key)
    assert fmt == "pre-1.4" and got["title"] == "Autumn Dance 2026" and len(got["items"]) == 3
    assert got["brand"]["studio"] == "Sample Studio" and got["zip"]["parts"]
    out = G.pulled_back(b, GID, key, got)
    assert [n for n, _ in out] == [n for n, _ in photos]
    assert [o().read() for _, o in out] == [d for _, d in photos]
    blob = (host / GID / got["zip"]["parts"][0]).read_bytes()
    z = zipfile.ZipFile(io.BytesIO(G.open_any(key, blob, got["zip"]["parts"][0])[0]))
    assert z.namelist() == [n for n, _ in photos]
    assert snapshot(host / GID) == before

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    new_blob = iv + AESGCM(G.key_bytes(key)).encrypt(iv, b'{"items":[]}', b"lumen-aureum-gallery:manifest.bin")
    assert G.open_any(key, new_blob, "manifest.bin") == (b'{"items":[]}', "1.0")

def refit_rows(A, monkeypatch):

    import refit
    seen = []
    monkeypatch.setattr(refit, "refit_gallery", lambda run, r, *a, **k: seen.append(r["gallery_id"]))
    with A.app.app_context():
        A.app.config["gallery_upgrade_run"]()
    return seen

def test_adopting_records_the_gallery_and_changes_nothing_on_the_host(env, monkeypatch):
    A, c, G, host, key, photos, m = env
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Adopt a gallery already online" in page and 'action="/session/1/gallery/adopt"' in page
    before = snapshot(host / GID)
    link = f"{BASE}{GID}/#k={key}"
    r = adopt(c, link)
    assert r.status_code == 302
    assert snapshot(host / GID) == before
    g = row(A, "SELECT * FROM galleries WHERE session_id=1")
    assert row(A, "SELECT gallery_id FROM sessions WHERE id=1")[0] == GID
    assert g["key"] == key and g["ekey"] == key and g["adopted"] == date.today().isoformat() and g["adopted_old"] == 1
    assert g["adopted_fmt"] == "pre-1.4" and g["n_files"] == 3 and g["upload_state"] == "up" and g["sealed_gen"] == 0
    assert g["cw_fixed"] == "none"
    with A.app.test_request_context():
        assert A.app.config["gallery_link"](1) == link
    assert any(h.startswith("Gallery adopted from the web host") and "Nothing on the web host was changed" in h
               for h in history(A))
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert ">Bring this gallery up to date<" in page and "can download 3 photos" in page

    assert ">Replace this link</button>" not in page and "Offer prints from this gallery" not in page
    assert "Bring this gallery up to date first" in page
    assert "Adopt a gallery already online" not in page

    (host / GID / "seen.txt").write_text((datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT01:02:03Z"))
    before = snapshot(host / GID)
    with A.app.test_request_context():
        A.app.view_functions["gallery_sync_now"]()
    assert snapshot(host / GID) == before
    assert row(A, "SELECT first_visit, sd_shipped FROM sessions WHERE id=1")[1]
    assert refit_rows(A, monkeypatch) == [] and snapshot(host / GID) == before

def test_the_link_must_be_on_this_studios_web_host(env):
    A, c, G, host, key, photos, m = env
    before = snapshot(host / GID)
    adopt(c, f"https://elsewhere.example/g/{GID}/#k={key}")
    assert "isn't on this studio's web host" in notice(c) and nothing_adopted(A)
    adopt(c, f"{BASE}_0fwNotHere1/#k={key}")
    assert "no gallery folder _0fwNotHere1 on this studio's web host" in notice(c) and nothing_adopted(A)
    assert snapshot(host / GID) == before
    assert any(h.startswith("Adopting a gallery refused:") for h in history(A))

def test_refused_when_the_app_cannot_write_there(env, monkeypatch):
    A, c, G, host, key, photos, m = env
    from vault import backends as VB
    real = VB.LocalBackend.put_stream

    def no_write(self, name, reader, content_type=None, cache_control=None):
        if G.ADOPT_PROBE in name:
            raise PermissionError("permission denied")
        return real(self, name, reader, content_type=content_type, cache_control=cache_control)
    monkeypatch.setattr(VB.LocalBackend, "put_stream", no_write)
    before = snapshot(host / GID)
    adopt(c, f"{BASE}{GID}/#k={key}")
    assert "can't write to that gallery's folder with its own web host login" in notice(c)
    assert nothing_adopted(A) and snapshot(host / GID) == before

def test_refused_when_the_key_does_not_open_it(env):
    A, c, G, host, key, photos, m = env
    wrong = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    adopt(c, f"{BASE}{GID}/#k={wrong}")
    assert "key in that link doesn't open the gallery" in notice(c) and nothing_adopted(A)
    adopt(c, f"{BASE}{GID}/")
    assert "has no key" in notice(c) and nothing_adopted(A)
    adopt(c, f"{BASE}{GID}/#k={key}&c=1")
    assert "passphrase" in notice(c) and nothing_adopted(A)

def test_adopting_sets_the_closing_day_from_today_not_from_an_old_first_visit(env):
    A, c, G, host, key, photos, m = env
    usual = A.C.gallery_days("family")
    want = (date.today() + timedelta(days=usual)).isoformat()
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert f'name="until" value="{want}" min="{tomorrow}"' in page
    old = date.today() - timedelta(days=usual + 20)
    (host / GID / "seen.txt").write_text(old.isoformat() + "T12:00:00Z")
    adopt(c, f"{BASE}{GID}/#k={key}")
    assert row(A, "SELECT adopt_until FROM galleries WHERE session_id=1")[0] == want
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=1")[0]
    with A.app.test_request_context():
        s = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert A.gallery_expires(s) == date.fromisoformat(want)
        A.app.view_functions["gallery_sync_now"]()
    assert tuple(row(A, "SELECT upload_state, removed_at FROM galleries WHERE session_id=1")) == ("up", None)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Online until " + A.nice(want) in page

    con = A.connect()
    con.execute("UPDATE galleries SET adopt_until=? WHERE session_id=1", ((date.today() - timedelta(days=1)).isoformat(),))
    con.commit(); con.close()
    with A.app.test_request_context():
        A.app.view_functions["gallery_sync_now"]()
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    assert any("its last day is over" in h for h in history(A))

def test_the_closing_day_can_be_changed_but_never_before_tomorrow(env):
    A, c, G, host, key, photos, m = env
    link = f"{BASE}{GID}/#k={key}"
    for bad in (date.today().isoformat(), (date.today() - timedelta(days=3)).isoformat()):
        c.post("/session/1/gallery/adopt", data={"link": link, "until": bad, "back": "/c/1?s=1"}, environ_base=E)
        assert "closing day must be" in notice(c) and nothing_adopted(A)
    c.post("/session/1/gallery/adopt", data={"link": link, "until": "not a date", "back": "/c/1?s=1"}, environ_base=E)
    assert "isn't a date" in notice(c) and nothing_adopted(A)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    c.post("/session/1/gallery/adopt", data={"link": link, "until": tomorrow, "back": "/c/1?s=1"}, environ_base=E)
    assert row(A, "SELECT adopt_until FROM galleries WHERE session_id=1")[0] == tomorrow

def test_bring_up_to_date_keeps_the_link_and_key(env, monkeypatch):
    A, c, G, host, key, photos, m = env
    adopt(c, f"{BASE}{GID}/#k={key}")
    until = row(A, "SELECT adopt_until FROM galleries WHERE session_id=1")[0]
    with A.app.test_request_context():
        link = A.app.config["gallery_link"](1)
    old = set(snapshot(host / GID))
    c.post("/session/1/gallery/bring-up-to-date", environ_base=E)
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=120)
    j = c.get("/session/1/gallery/status", environ_base=E).get_json()
    assert j["state"] == "done", j
    assert row(A, "SELECT gallery_id FROM sessions WHERE id=1")[0] == GID
    g = row(A, "SELECT * FROM galleries WHERE session_id=1")
    assert g["key"] == key and (g["ekey"] or g["key"]) == key and g["adopted_old"] == 0 and g["sealed_gen"] == 1
    assert g["page_set"] and g["built_at"]
    with A.app.test_request_context():
        assert A.app.config["gallery_link"](1) == link
    import hostfiles
    now = snapshot(host / GID)
    gated = hostfiles.name_for(hostfiles.proof_of(G.key_bytes(key)), "manifest.bin")
    assert gated in now and "manifest.bin" not in now
    new_m = json.loads(G.open_any(key, now[gated], "manifest.bin")[0])
    assert G.open_any(key, now[gated], "manifest.bin")[1] == "1.0"
    assert new_m["dl"] == 1 and new_m["title"] == "Autumn Dance 2026" and len(new_m["items"]) == 3
    assert [it["n"] for it in new_m["items"]] == [n for n, _ in photos]
    assert "seen.key" not in now and "limits.php" in now and "gate.php" in now
    assert f"until={until}\n".encode() in now["limits.php"]
    assert not (old & set(n for n in now if n.endswith(".bin") and len(n) == 28))
    assert 'data-gid="' + GID + '"' in now["index.html"].decode()
    h = history(A)
    assert any(x_.startswith("Bringing the adopted gallery up to date") for x_ in h)
    assert any(x_.startswith("Adopted gallery brought up to date: the same link and key") for x_ in h)

    with A.app.test_request_context():
        import upgrade_140
        assert upgrade_140.finished(A.q)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert ">Bring this gallery up to date<" not in page and "Preview the gallery" in page
    con = A.connect()
    con.execute("UPDATE galleries SET page_set='an-earlier-release' WHERE session_id=1")
    con.commit(); con.close()
    assert refit_rows(A, monkeypatch) == [GID]

def test_bring_up_to_date_stops_before_any_write_when_photos_are_missing(env):
    A, c, G, host, key, photos, m = env
    adopt(c, f"{BASE}{GID}/#k={key}")
    (host / GID / m["items"][1]["o"]).unlink()
    before = snapshot(host / GID)
    c.post("/session/1/gallery/bring-up-to-date", environ_base=E)
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=60)
    j = c.get("/session/1/gallery/status", environ_base=E).get_json()
    assert j["state"] == "error" and "missing on the web host" in j["error"]
    assert snapshot(host / GID) == before
    assert row(A, "SELECT adopted_old FROM galleries WHERE session_id=1")[0] == 1

def test_bring_the_photos_home_keeps_the_gallerys_photos_as_the_sessions_own(env):

    A, c, G, host, key, photos, m = env
    adopt(c, f"{BASE}{GID}/#k={key}")
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Bring the photos home" in page and 'action="/session/1/gallery/photos-home"' in page
    before = snapshot(host / GID)
    r = c.post("/session/1/gallery/photos-home", environ_base=E)
    assert r.status_code == 302
    assert snapshot(host / GID) == before
    import photostore
    con = A.connect()
    try:
        rows = {r_["name"]: r_ for r_ in photostore.files(con, 1, "export", done_only=True)}
        assert sorted(rows) == sorted(n for n, _ in photos)
        for n, data in photos:
            assert photostore.plain_bytes(rows[n]) == data, n
    finally:
        con.close()
    assert row(A, "SELECT photos_home_at FROM galleries WHERE session_id=1")[0] == date.today().isoformat()
    assert any(h.startswith("Brought the adopted gallery's 3 photos home") for h in history(A))
    assert "Bring the photos home" not in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    c.post("/session/1/gallery/photos-home", environ_base=E)
    con = A.connect()
    try:
        assert len(photostore.files(con, 1, "export")) == 3
    finally:
        con.close()

def test_bring_the_photos_home_after_bringing_the_gallery_up_to_date(env):

    A, c, G, host, key, photos, m = env
    adopt(c, f"{BASE}{GID}/#k={key}")
    c.post("/session/1/gallery/bring-up-to-date", environ_base=E)
    for t in [t for t in threading.enumerate() if t.name == "gallery-1"]:
        t.join(timeout=120)
    assert c.get("/session/1/gallery/status", environ_base=E).get_json()["state"] == "done"
    c.post("/session/1/gallery/close-now", environ_base=E)
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    c.post("/session/1/gallery/photos-home", environ_base=E)
    import photostore
    con = A.connect()
    try:
        names = sorted(r_["name"] for r_ in photostore.files(con, 1, "export", done_only=True))
    finally:
        con.close()
    assert names == sorted(n for n, _ in photos)
