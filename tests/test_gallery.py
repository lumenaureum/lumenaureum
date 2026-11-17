import io
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from helpers import host_text, host_write

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = b"SECRET-GPS-48.8584N"

def jpeg_with_metadata(path, color):
    from PIL import Image
    im = Image.new("RGB", (64, 48), color)
    exif = Image.Exif()
    exif[0x010E] = SECRET.decode()
    im.save(path, "JPEG", exif=exif.tobytes(), comment=SECRET)
    assert SECRET in path.read_bytes()

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for i, col in enumerate(("red", "blue", "green"), 1):
        jpeg_with_metadata(src / f"img{i}.jpg", col)
    (src / "notes.txt").write_text("not a photo")
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', '2026-10-01')")
    con.commit(); con.close()
    from plugins import gallery as G
    return A, c, G, tmp_path, src

def wait_built(c, sid=1):

    for t in [t for t in threading.enumerate() if t.name == f"gallery-{sid}"]:
        t.join(timeout=10)
        if t.is_alive():
            raise AssertionError("gallery build did not finish")
    j = c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
    if j["state"] not in ("done", "error"):
        raise AssertionError(f"gallery build did not finish: {j}")
    return j

def decrypt(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def _opens_as_image(data):
    import io
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
        return True
    except Exception:
        return False

def test_build_encrypts_and_strips_metadata(env):
    A, c, G, tmp, src = env
    key = G.new_key()
    out, n, skipped = G.build("gal_test_1", src, key, credit="Made <with> care")
    assert n == 3 and skipped == []
    names = sorted(p.name for p in out.iterdir())
    assert {"index.html", "manifest.bin", "seen.php", "gate.php", "seen-key.php", ".htaccess"} <= set(names) and "seen.key" not in names
    page = (out / "index.html").read_text()
    assert "<with>" not in page and "data-credit" not in page and 'href="https://lumenaureum.org"' in page
    for p in out.glob("*.bin"):
        data = p.read_bytes()
        assert SECRET not in data and not _opens_as_image(data)

    manifest = json.loads(decrypt(G, key, (out / "manifest.bin").read_bytes(), "manifest.bin"))
    assert len(manifest["items"]) == 3
    web = decrypt(G, key, (out / manifest["items"][0]["f"]).read_bytes(), manifest["items"][0]["f"])
    import pytest as _pt
    with _pt.raises(Exception):
        decrypt(G, key, (out / manifest["items"][0]["f"]).read_bytes(), manifest["items"][0]["t"])
    assert web.startswith(b"\xff\xd8") and SECRET not in web and b"Exif" not in web
    with pytest.raises(Exception):
        decrypt(G, G.new_key(), (out / "manifest.bin").read_bytes(), "manifest.bin")
    empty = tmp / "photos" / "export" / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        G.build("gal_test_2", empty, key)

def test_build_returns_its_own_skipped_files(env):

    A, c, G, tmp, src = env
    other = tmp / "photos" / "export" / "other"
    other.mkdir()
    jpeg_with_metadata(other / "good.jpg", "red")
    (other / "broken.jpg").write_bytes(b"not a jpeg at all")
    key = G.new_key()
    r1 = G.build("gal_skip_1", other, key)
    r2 = G.build("gal_skip_2", src, key)
    assert (r1.n, r1.skipped) == (1, ["broken.jpg"])
    assert (r2.n, r2.skipped) == (3, [])
    assert not hasattr(G.build, "skipped")

def test_build_route_reads_only_under_photos(env):
    A, c, G, tmp, src = env
    c.post("/session/1/gallery/build", data={"source_dir": str(tmp)}, environ_base=E)
    assert "Folder must be under" in c.get("/session/1/gallery/status", environ_base=E).get_json()["error"]
    c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    assert wait_built(c)["state"] == "done"
    with A.app.test_request_context():
        link = A.app.config["gallery_link"](1)
    con = A.connect()
    gid, built = con.execute("SELECT gallery_id, gallery_built FROM sessions WHERE id=1").fetchone()
    con.close()
    assert built and link.endswith(f"{gid}/#k=" + link.split("#k=")[1]) and len(link.split("#k=")[1]) >= 40
    r = c.get(f"/gallery-preview/{gid}/", environ_base=E)
    assert r.status_code == 200 and "script-src" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert c.get("/gallery-preview/bad id!/", environ_base=E).status_code == 404
    assert c.get(f"/gallery-preview/{gid}/../../crm.db", environ_base=E).status_code == 404
    assert c.get(f"/gallery-preview/{gid}/seen.php", environ_base=E).status_code == 204

def test_upload_waits_for_balance_then_copies_and_deletes(env):
    A, c, G, tmp, src = env
    from vault import web as VW
    host = tmp / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    c.post("/session/1/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    assert wait_built(c)["state"] == "done"
    con = A.connect()
    gid = con.execute("SELECT gallery_id FROM sessions WHERE id=1").fetchone()[0]
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 300, 'custom')")
    con.commit(); con.close()
    c.post("/session/1/gallery/upload", environ_base=E)
    con = A.connect()
    state, err = con.execute("SELECT upload_state, upload_error FROM galleries WHERE session_id=1").fetchone()
    con.close()
    assert state == "error"
    if state == "error":
        assert "due" in err and not (host / gid).exists()
        con = A.connect()
        con.execute("INSERT INTO payments (session_id, amount, received, kind) VALUES (1, 300, '2026-10-02', 'payment')")
        con.execute("UPDATE galleries SET upload_state='' WHERE session_id=1")
        con.commit(); con.close()
    n = G.upload_gallery(gid, 5, "2027-12-31")
    files = {p.name for p in (host / gid).iterdir()}
    assert "index.html" in files and "limits.php" in files and n == len(files)
    assert "days=5" in host_text(host / gid, "limits.txt")
    assert G.seen_at(gid) is None
    host_write(host / gid, "seen.txt", "2026-10-05T12:00:00Z")
    assert G.seen_at(gid) == "2026-10-05T12:00:00Z"
    G.delete_gallery(gid)
    assert not (host / gid).exists()
    with pytest.raises(ValueError):
        G.upload_gallery("../etc", 5, "2027-12-31")

def test_viewer_reads_the_key_from_the_link_and_preview_signs_in():

    import os
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "gallery_assets", "viewer.html")).read()
    assert "const HP = new URLSearchParams(location.hash.slice(1))" in html and "let raw = (() =>" in html
    assert 'credentials: PREVIEW ? "same-origin" : "omit"' in html

def test_picks_email_fingerprint(env):
    A, c, G, tmp, src = env
    key = G.new_key()
    lines = ["Retouch a.jpg (photo 1): brighten", "Please don't show: b.jpg (photo 2)"]
    fp = G.picks_fingerprint(lines, key)
    assert len(fp) == 19 and fp.count("-") == 3
    pk, same, _ = G.read_picks_email("Hello\n" + "\n".join(lines) + f"\n\nFingerprint: {fp}\nThanks", key)
    assert same and pk == {"retouch": ["a.jpg (photo 1): brighten"], "suggest": [], "veto": ["b.jpg (photo 2)"]}
    for edited in ("\n".join(lines[:1]) + f"\nFingerprint: {fp}",
                   "\n".join(lines).replace("brighten", "remove tattoo") + f"\nFingerprint: {fp}",
                   "\n".join(lines)):
        pk, same, _ = G.read_picks_email(edited, key)
        assert pk is not None and not same
    assert not G.read_picks_email("\n".join(lines) + f"\nFingerprint: {fp}", G.new_key())[1]
