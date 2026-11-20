import html
import io
import json
import os
import re
import sys
import threading
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from helpers import host_bytes, host_has, host_write

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = b"SECRET-GPS-48.8584N"

def photo(path, size, color):
    from PIL import Image
    im = Image.new("RGB", size, color)
    for x_ in range(0, size[0], 37):
        im.putpixel((x_, x_ % size[1]), (255, 255, 255))
    exif = Image.Exif()
    exif[0x010E] = SECRET.decode()
    im.save(path, "JPEG", exif=exif.tobytes(), comment=SECRET)

def opened(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_realestate": "1"}, environ_base=E)
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    photo(src / "Kitchen.jpg", (3000, 2000), "red")
    photo(src / "kitchen.JPG", (2400, 3200), "blue")
    photo(src / "Café front.jpg", (1200, 800), "green")
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://gallery.test/g/')")
    con.execute("INSERT INTO households (name) VALUES ('The Sample Family')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Sample', 'adult', 'avery@example.com')")
    con.commit(); con.close()
    from plugins import gallery as G
    return A, c, G, tmp_path, src, host

def session(A, type_, **cols):
    con = A.connect()
    cur = con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, ?, 'shot', ?)",
                      (type_, (date.today() - timedelta(days=3)).isoformat()))
    sid = cur.lastrowid
    for k_, v_ in cols.items():
        con.execute(f"UPDATE sessions SET {k_}=? WHERE id=?", (v_, sid))
    con.commit(); con.close()
    return sid

def join(prefix, sid):
    for t in [t for t in threading.enumerate() if t.name == f"{prefix}-{sid}"]:
        t.join(timeout=60)
        assert not t.is_alive()

def build(c, sid, src):
    c.post(f"/session/{sid}/gallery/build", data={"source_dir": str(src)}, environ_base=E)
    join("gallery", sid)
    j = c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()
    assert j["state"] == "done", j

def row(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def manifest(G, gid, key):
    return json.loads(opened(G, key, (G.OUT_ROOT / gid / "manifest.bin").read_bytes(), "manifest.bin"))

def reassemble(G, folder, key, z):
    return b"".join(opened(G, key, (folder / p).read_bytes(), p) for p in z["parts"])

def test_build_seals_full_size_files_and_a_prepared_zip(env, monkeypatch):
    A, c, G, tmp, src, host = env
    monkeypatch.setattr(G, "ZIP_PART", 30_000)
    kept = {}
    real_unlink = Path.unlink

    def keep_zip(self, *a, **kw):
        if self.name.endswith(".zip") and self.exists():
            kept.setdefault("zips", []).append(self.read_bytes())
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", keep_zip)
    key = G.new_key()
    out, n, skipped = G.build("gal_dl_1", src, key, picks=dict(post=True), download=True,
                              brand=dict(client="The Sample Family"), mark=dict(style="tiled", text="PROOF", strength=50))
    assert n == 3 and not skipped
    m = manifest(G, "gal_dl_1", key)
    assert m["dl"] == 1 and m["picks"] == {"post": True} and m["title"] == "The Sample Family"
    assert sorted(it["n"] for it in m["items"]) == ["Café front.jpg", "Kitchen.jpg", "kitchen.JPG"]
    ons = [it["on"] for it in m["items"]]
    assert ons[0] == "Café front.jpg" and ons[1] in ("Kitchen.jpg", "kitchen.JPG")
    assert ons[2] in ("Kitchen (2).jpg", "kitchen (2).JPG") and ons[2].lower() != ons[1].lower()
    from PIL import Image
    want = {"Café front.jpg": (1200, 800), "Kitchen.jpg": (3000, 2000), "kitchen.JPG": (2400, 3200)}
    for it in m["items"]:
        size = want[it["n"]]
        full = opened(G, key, (out / it["o"]).read_bytes(), it["o"])
        assert SECRET not in full and b"Exif" not in full and len(full) == it["os"]
        with Image.open(io.BytesIO(full)) as im:
            assert im.size == size == (it["ow"], it["oh"])
            px = [im.getpixel((x_, y_)) for x_ in range(3, im.size[0], 31) for y_ in range(5, im.size[1], 29)]
            light = sum(1 for p_ in px if min(p_) > 150)
            assert light / len(px) < 0.02

    assert len(m["zips"]) == 1 and m["zips"][0]["name"] == "The Sample Family.zip"
    z = m["zips"][0]
    assert len(z["parts"]) > 1 and all((out / p).is_file() for p in z["parts"])
    for p in z["parts"]:
        assert not (out / p).read_bytes().startswith(b"PK")
    whole = reassemble(G, out, key, z)
    assert [whole] == kept["zips"] and len(whole) == z["size"]
    zf = zipfile.ZipFile(io.BytesIO(whole))
    assert zf.testzip() is None
    assert zf.namelist() == ons
    assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
    assert zf.infolist()[0].flag_bits & 0x800
    for it in m["items"]:
        assert zf.read(it["on"]) == opened(G, key, (out / it["o"]).read_bytes(), it["o"])

    assert not list(G.OUT_ROOT.glob("*.zip")) and not list(G.OUT_ROOT.glob(".*.zip"))
    G.upload_gallery("gal_dl_1", 30, "2099-01-01")
    sent = {p.name for p in (host / "gal_dl_1").iterdir()}
    assert G.DL_MARK not in sent and G.WAS_MARK not in sent and "index.html" in sent
    assert G.is_download("gal_dl_1")

def test_a_large_gallery_is_offered_as_several_whole_zips(env, monkeypatch):
    A, c, G, tmp, src, host = env
    photo(src / "Porch.jpg", (2000, 1500), "yellow")
    monkeypatch.setattr(G, "ZIP_PART", 150_000)
    key = G.new_key()
    sizes = {}
    real = G.clean_jpeg

    def spy(path, mark=None, full=False, meta=None):
        out = real(path, mark, full, meta)
        sizes[len(sizes)] = len(out[4])
        return out
    monkeypatch.setattr(G, "clean_jpeg", spy)
    G.build("gal_dl_split", src, key, download=True)
    biggest = max(sizes.values())
    monkeypatch.setattr(G, "ZIP_SPLIT", biggest + 1000)
    sizes.clear()
    out, n, _ = G.build("gal_dl_split", src, key, download=True, brand=dict(client="The Sample Family"))
    m = manifest(G, "gal_dl_split", key)
    zs = m["zips"]
    assert n == 4 and 2 <= len(zs) <= 4
    assert [z["name"] for z in zs] == [f"The Sample Family-part-{j}-of-{len(zs)}.zip" for j in range(1, len(zs) + 1)]
    seen = []
    for j, z in enumerate(zs):
        zf = zipfile.ZipFile(io.BytesIO(reassemble(G, out, key, z)))
        assert zf.testzip() is None
        assert z["size"] <= G.ZIP_SPLIT or len(zf.namelist()) == 1
        for name in zf.namelist():
            it = next(i_ for i_ in m["items"] if i_["on"] == name)
            assert it["z"] == j and zf.read(name) == opened(G, key, (out / it["o"]).read_bytes(), it["o"])
        seen += zf.namelist()
    assert sorted(seen) == sorted(it["on"] for it in m["items"])
    monkeypatch.setattr(G, "ZIP_SPLIT", 10)
    G.build("gal_dl_split", src, key, download=True)
    assert len(manifest(G, "gal_dl_split", key)["zips"]) == 4

@pytest.mark.parametrize("owns", [True, False])
def test_delivered_files_name_their_maker_and_nothing_else(env, owns):

    A, c, G, tmp, src, host = env
    from PIL import Image
    gps = tmp / "gps"
    gps.mkdir()
    im = Image.new("RGB", (800, 600), "teal")
    ex = Image.Exif()
    ex[0x010F], ex[0xA431], ex[0x013B] = "Nikon", "SERIAL-123456", "Someone Else"
    ex[0x8825] = {1: "N", 2: (40.0, 1.0, 2.0)}
    im.save(gps / "a.jpg", "JPEG", exif=ex.tobytes())
    key = G.new_key()
    out, n, _ = G.build("gal_meta", gps, key, download=True,
                        brand=dict(client="The Sample Family", studio="Café Studio", photographer="Avery Lane",
                                   date="November 12, 2026", owns=owns))
    m = manifest(G, "gal_meta", key)
    full = opened(G, key, (out / m["items"][0]["o"]).read_bytes(), m["items"][0]["o"])
    with Image.open(io.BytesIO(full)) as got:
        exif = got.getexif()
        xmp = got.info.get("xmp", b"")
    xmp = xmp.decode() if isinstance(xmp, bytes) else str(xmp)
    assert exif.get(0x013B) == "Cafe Studio" and "<rdf:li>Café Studio</rdf:li>" in xmp
    if owns:
        assert exif.get(0x8298) == "(c) 2026 Cafe Studio. All rights reserved."
        assert "\u00a9 2026 Café Studio. All rights reserved." in xmp
    else:
        assert 0x8298 not in exif and "dc:rights" not in xmp
    assert set(exif) <= {0x013B, 0x8298} and not exif.get_ifd(0x8825)
    for leak in (b"Sample Family", b"SERIAL-123456", b"Nikon", b"Someone Else"):
        assert leak not in full
    zf = zipfile.ZipFile(io.BytesIO(reassemble(G, out, key, m["zips"][0])))
    assert zf.read(m["items"][0]["on"]) == full

def test_upload_puts_the_page_last(env, monkeypatch):
    A, c, G, tmp, src, host = env
    monkeypatch.setattr(G, "ZIP_PART", 200_000)
    key = G.new_key()
    G.build("gal_dl_2", src, key, download=True)
    order = []
    real = G.gallery_host

    def spy():
        h = real()
        put = h.put_stream
        h.put_stream = lambda name, f, **kw: (order.append(name.rsplit("/", 1)[-1]), put(name, f, **kw))[1]
        return h
    monkeypatch.setattr(G, "gallery_host", spy)
    G.upload_gallery("gal_dl_2", 30, "2099-01-01")
    assert order[-2:] == ["manifest.bin", "index.html"]

def test_upload_sets_cache_control_per_file(env, monkeypatch):
    A, c, G, tmp, src, host = env
    monkeypatch.setattr(G, "ZIP_PART", 30_000)
    key = G.new_key()
    G.build("gal_dl_cc", src, key, download=True)
    sent = {}
    real = G.gallery_host

    def spy():
        h = real()
        put = h.put_stream
        h.put_stream = lambda name, f, **kw: (sent.__setitem__(name.rsplit("/", 1)[-1], kw.get("cache_control")),
                                              put(name, f, **kw))[1]
        return h
    monkeypatch.setattr(G, "gallery_host", spy)
    G.upload_gallery("gal_dl_cc", 30, "2099-01-01")
    assert sent["index.html"] == sent["manifest.bin"] == "no-cache"
    bins = [n for n in sent if n.endswith(".bin") and n != "manifest.bin"]
    assert len(bins) > 9 and all(sent[n] == "public, max-age=31536000, immutable" for n in bins)
    assert sent["gate.php"] == sent[".htaccess"] == "no-cache"
    ht = (G.ASSETS / "htaccess").read_text()
    assert 'FilesMatch "^(index\\.html|font(-light)?\\.woff2)$"' in ht and 'Header set Cache-Control "no-cache"' in ht

def test_s3_keeps_the_cache_control_with_the_file():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fake_s3 import ACCESS, SECRET, FakeS3
    from vault import backends as B
    f = FakeS3(buckets=("gal-test",))
    try:
        be = B.S3Backend("gal-test", ACCESS, SECRET, endpoint=f.endpoint, region="us-east-1", prefix="g")
        be.put_stream("a/manifest.bin", io.BytesIO(b"x"), content_type="application/octet-stream", cache_control="no-cache")
        be.put_stream("a/plain.bin", io.BytesIO(b"y"))
        head = be.s3.head_headers("g/a/manifest.bin")
        assert head["cache-control"] == "no-cache" and head["content-type"] == "application/octet-stream"
        assert "cache-control" not in be.s3.head_headers("g/a/plain.bin")
    finally:
        f.close()

def test_a_type_with_no_picking_round_builds_a_download_gallery(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "realestate")
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)

    assert "Upload the edited photos on the Edit row first" in page
    build(c, sid, src)
    gid = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0]
    g = row(A, "SELECT key, ekey, proof FROM galleries WHERE session_id=?", (sid,))
    m = manifest(G, gid, g[1] or g[0])
    assert m["dl"] == 1 and set(m["picks"]) <= {"post"} and g[2] == 0
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "3 photos to download · made" in html.unescape(page)

def test_first_open_of_a_download_gallery_is_files_delivered(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "realestate")
    build(c, sid, src)
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0] == "up"
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Email link" in page and "Your%20photos%20are%20ready%20to%20download" in page and "mailto:avery@example.com" in page
    assert "Please%20download%20your%20photos%20in%20that%20time." in page
    c.post("/gallery/sync", environ_base=E)
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] is None
    opened_on = date.today() - timedelta(days=2)
    host_write(host / gid, "seen.txt", opened_on.isoformat() + "T12:00:00Z")
    c.post("/gallery/sync", environ_base=E)
    sd, fv = row(A, "SELECT sd_shipped, first_visit FROM sessions WHERE id=?", (sid,))
    assert sd and fv and abs((date.fromisoformat(sd) - opened_on).days) <= 1
    with A.app.test_request_context():
        A.db()
        steps = {st_["label"]: st_["done"] for stage in A.workflow(sid)["stages"] for st_ in stage["steps"]}
        s_ = A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        exp = A.gallery_expires(s_)
    assert exp == date.fromisoformat(sd) + timedelta(days=A.C.gallery_days("realestate"))
    assert steps["Files delivered"]
    assert row(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE '%download gallery: files delivered%'", (sid,))

def test_portrait_proofs_first_then_the_download_gallery_in_their_place(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="link")
    build(c, sid, src)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert not G.is_download(gid)
    con = A.connect()
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Session', 300, 'custom')", (sid,))
    con.execute("UPDATE galleries SET proof=1 WHERE session_id=?", (sid,))

    con.execute("INSERT INTO step_closes (session_id, step, outcome) VALUES (?, 'deposit_paid', 'waived')", (sid,))
    con.commit(); con.close()
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0] == "up"
    host_write(host / gid, "seen.txt", (date.today() - timedelta(days=5)).isoformat() + "T12:00:00Z")
    (host / gid / "draft.las").write_bytes(b"LAS1" + b"x" * 40)
    c.post("/gallery/sync", environ_base=E)
    assert row(A, "SELECT first_visit FROM sessions WHERE id=?", (sid,))[0]
    link_before = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    con = A.connect()
    con.execute("UPDATE sessions SET retouch_picks='Kitchen.jpg: brighten' WHERE id=?", (sid,))
    con.commit(); con.close()
    assert "Build download gallery" in c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    build(c, sid, src)
    assert G.is_download(gid) and not G.online_download(gid)
    k = row(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))
    old_manifest = host_bytes(host / gid, "manifest.bin", k[1] or k[0])
    assert "dl" not in json.loads(opened(G, k[1] or k[0], old_manifest, "manifest.bin"))
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "still due. Your terms release the download gallery on payment." in page
    assert "The client can download the full-size photos right away." in page
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    state, err = row(A, "SELECT upload_state, upload_error FROM galleries WHERE session_id=?", (sid,))
    assert state == "up" and "released on payment" in err and host_has(host / gid, "seen.txt")
    con = A.connect()
    con.execute("INSERT INTO payments (session_id, amount, received, kind) VALUES (?, 300, ?, 'payment')",
                (sid, date.today().isoformat()))
    con.commit(); con.close()
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Upload puts the download gallery in its place" in page
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0] == "up"
    assert G.online_download(gid)
    assert not host_has(host / gid, "seen.txt") and not host_has(host / gid, "draft.las")
    m = json.loads(opened(G, k[1] or k[0], host_bytes(host / gid, "manifest.bin", k[1] or k[0]), "manifest.bin"))
    assert m["dl"] == 1
    assert c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True).count(f"{gid}/#k=") >= 1
    assert f"{gid}/#k=" in link_before

    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] is None
    with A.app.test_request_context():
        A.db()
        assert A.gallery_expires(A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)) is None
    con = A.connect()
    con.execute("INSERT INTO tasks (id, session_id, stage, title, sort) VALUES (9001, ?, 'delivered', 'Send retouched photos', 1)", (sid,))
    con.commit(); con.close()
    host_write(host / gid, "seen.txt", date.today().isoformat() + "T08:00:00Z")
    c.post("/gallery/sync", environ_base=E)
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]

    assert not row(A, "SELECT done FROM tasks WHERE id=9001")[0]
    assert row(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0] == "up"

def test_deliver_without_picks(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="link")
    build(c, sid, src)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert ">Deliver without picks</button>" in page and "Build download gallery" not in page
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert row(A, "SELECT COUNT(*) FROM audit WHERE session_id=? AND action LIKE 'Deliver without picks%'", (sid,))[0] == 1
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert ">Deliver without picks</button>" not in page and "Build download gallery" in page
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert row(A, "SELECT COUNT(*) FROM audit WHERE session_id=? AND action LIKE 'Deliver without picks%'", (sid,))[0] == 1
    build(c, sid, src)
    assert G.is_download(gid) and not G.online_download(gid)
    assert "Upload puts the download gallery in its place" in c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    join("gal-up", sid)
    assert G.online_download(gid)

    for sid_ in (session(A, "realestate"), session(A, "family", delivery_method="usb"),
                 session(A, "family", delivery_method="link", favorites="Kitchen.jpg")):
        assert ">Deliver without picks</button>" not in c.get(f"/c/1?s={sid_}", environ_base=E).get_data(as_text=True)
        c.post(f"/session/{sid_}/gallery/no-picks", environ_base=E)
        assert not row(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE 'Deliver without picks%'", (sid_,))

def upload(c, sid, name, data, kind):
    st = c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name}, environ_base=E).get_json()
    at = st["received"]
    while at < len(data):
        r = c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}", data=data[at:at + st["piece"]],
                   headers={"X-File-Name": name, "Content-Type": "application/octet-stream"}, environ_base=E)
        assert r.status_code == 200, r.get_data(as_text=True)
        at = r.get_json()["received"]

def test_after_proofs_the_download_gallery_is_built_from_the_retouched_finals(env):

    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="link", retouch_picks="Kitchen.jpg: brighten")
    for p_ in sorted(src.iterdir())[:2]:
        upload(c, sid, p_.name, p_.read_bytes(), "export")
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Retouched finals" in p and "Upload the retouched finals on the Retouch row" in p and ">Use the proofs</button>" in p
    c.post(f"/session/{sid}/gallery/build", data={"from": "uploads"}, environ_base=E)
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["error"] == "Upload the retouched finals on the Retouch row first."
    import io as _io
    from PIL import Image
    for n_ in ("final-1.jpg", "final-2.jpg", "final-3.jpg"):
        b_ = _io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(b_, "JPEG")
        upload(c, sid, n_, b_.getvalue(), "final")
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "3 retouched finals uploaded" in p and ">Use the proofs</button>" not in p
    c.post(f"/session/{sid}/gallery/build", data={"from": "finals"}, environ_base=E)
    join("gallery", sid)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    k = row(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))
    m = manifest(G, gid, k[1] or k[0])

    assert m["dl"] == 1 and [it["n"] for it in m["items"]] == ["Café front.jpg", "Kitchen.jpg", "final-1.jpg", "final-2.jpg", "final-3.jpg"]

    sid2 = session(A, "family", delivery_method="link")
    c.post(f"/session/{sid2}/gallery/no-picks", environ_base=E)
    assert "Retouched finals" in c.get(f"/c/1?s={sid2}", environ_base=E).get_data(as_text=True)
    assert "Upload the retouched finals" not in c.get(f"/c/1?s={sid2}", environ_base=E).get_data(as_text=True)

    sid3 = session(A, "family", delivery_method="usb")
    assert "Retouched finals" not in c.get(f"/c/1?s={sid3}", environ_base=E).get_data(as_text=True)

def test_without_picks_the_block_counts_and_builds_the_same_set(env):

    A, c, G, tmp, src, host = env
    import io as _io
    from PIL import Image

    def jpeg(color):
        b_ = _io.BytesIO()
        Image.new("RGB", (200, 150), color).save(b_, "JPEG")
        return b_.getvalue()
    sid = session(A, "senior", delivery_method="link")
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    for i in range(3):
        upload(c, sid, f"final-{i}.jpg", jpeg("white"), "final")
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "3 retouched finals uploaded" in p and "Upload the edited JPEGs in Photos first" not in p
    for i in range(2):
        upload(c, sid, f"export-{i}.jpg", jpeg("black"), "export")
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "3 retouched finals uploaded" in p and "2 edited photos uploaded" not in p
    c.post(f"/session/{sid}/gallery/build", data={"from": "finals"}, environ_base=E)
    join("gallery", sid)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    k = row(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))

    assert [it["n"] for it in manifest(G, gid, k[1] or k[0])["items"]] == ["export-0.jpg", "export-1.jpg", "final-0.jpg", "final-1.jpg", "final-2.jpg"]
    assert "5 photos to download · made" in html.unescape(c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True))

    sid2 = session(A, "senior", delivery_method="link")
    c.post(f"/session/{sid2}/gallery/no-picks", environ_base=E)
    upload(c, sid2, "export-a.jpg", jpeg("gray"), "export")
    p = c.get(f"/c/1?s={sid2}", environ_base=E).get_data(as_text=True)
    assert "1 edited photo uploaded" in p and "retouched finals uploaded" not in p

def test_the_download_gallery_after_picks_holds_every_photo_with_the_retouched_ones_in_place(env):

    A, c, G, tmp, src, host = env
    import io as _io
    from PIL import Image

    def jpeg(color):
        b_ = _io.BytesIO()
        Image.new("RGB", (200, 150), color).save(b_, "JPEG")
        return b_.getvalue()
    sid = session(A, "family", delivery_method="link", retouch_picks="IMG_2.jpg: warmer\nIMG_10.jpg: crop")
    for i in range(1, 13):
        upload(c, sid, f"IMG_{i}.jpg", jpeg("black"), "export")
    upload(c, sid, "IMG_2-Edit.jpg", jpeg("white"), "final")
    upload(c, sid, "img_10.jpg", jpeg("white"), "final")
    upload(c, sid, "extra.jpg", jpeg("white"), "final")
    p = html.unescape(c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True))
    assert "3 retouched finals uploaded · 13 photos in the download gallery" in p
    c.post(f"/session/{sid}/gallery/build", data={"from": "finals"}, environ_base=E)
    join("gallery", sid)
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["state"] == "done"
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    k = row(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))
    names = [it["n"] for it in manifest(G, gid, k[1] or k[0])["items"]]
    assert names == ["IMG_1.jpg", "IMG_2-Edit.jpg"] + [f"IMG_{i}.jpg" for i in range(3, 10)] + ["img_10.jpg", "IMG_11.jpg",
                                                                                                "IMG_12.jpg", "extra.jpg"]
    assert "13 photos to download · made" in html.unescape(c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True))
    with A.app.app_context():
        assert [n_ for n_, _ in A.app.config["gallery_delivery_set"](sid)][:2] == ["IMG_1.jpg", "IMG_2-Edit.jpg"]

def test_usb_delivery_keeps_proofing(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", retouch_picks="Kitchen.jpg")
    build(c, sid, src)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert not G.is_download(gid)
    sid2 = session(A, "realestate", delivery_method="sd")
    assert "Build download gallery" not in c.get(f"/c/1?s={sid2}", environ_base=E).get_data(as_text=True)

def test_the_letter_says_download(env, monkeypatch):
    A, c, G, tmp, src, host = env
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    sid = session(A, "family", delivery_method="link", favorites="Kitchen.jpg")
    build(c, sid, src)
    letter = c.get(f"/letters/{sid}/qr-letter.pdf", environ_base=E).get_data(as_text=True)
    assert "Your photos are finished and ready to download." in letter and "Scan to download your photos" in letter
    assert "pick" not in letter.lower()
    assert "through a download link" not in letter
    sid2 = session(A, "family", delivery_method="link")
    build(c, sid2, src)
    letter = c.get(f"/letters/{sid2}/qr-letter.pdf", environ_base=E).get_data(as_text=True)
    assert "ready to download" not in letter

@pytest.mark.parametrize("value,want", [("https://studio.example.com", "https://studio.example.com"),
                                        ("https://studio.example/about?x=1", "https://studio.example/about?x=1"),
                                        ("", ""), ("www.Studio.example.com", "https://www.studio.example.com"),
                                        ("HTTPS://MorganLane.com", "https://morganlane.com"),
                                        ("https://café-photo.fr", "https://café-photo.fr"),
                                        ("http://studio.example.com", None), ("https://bad host.com", None),
                                        ("javascript:alert(1)", None), ('https://a.com/"><script>', None),
                                        ("https://localhost", None), ("studio", None)])
def test_website_setting(env, value, want):
    A, c, G, tmp, src, host = env
    import settings_io
    assert settings_io.clean_site(value) == want
    c.post("/settings", data={"business_website": "https://keep.example.com"}, environ_base=E)
    r = c.post("/settings", data={"business_website": value, "business_phone": "555-0100"}, environ_base=E)
    saved, = row(A, "SELECT value FROM settings WHERE key='business_website'")
    assert saved == (want if want is not None else "https://keep.example.com")
    assert row(A, "SELECT value FROM settings WHERE key='business_phone'")[0] == "555-0100"
    shown = c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)
    assert ("Saved." in shown) == (want is not None)
    if want is None:
        assert "The website wasn" in shown and "Everything else was saved." in shown
    if want:
        sid = session(A, "realestate")
        with A.app.test_request_context():
            A.db()
            brand = A.app.config["gallery_brand"](A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True))
        assert brand["site"] == want

def test_the_website_box_is_a_plain_text_box(env):

    A, c, G, tmp, src, host = env
    p = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    box = re.search(r'<input [^>]*id="f-business_website"[^>]*>', p).group(0)
    assert 'type="text"' in box and 'inputmode="url"' in box and 'autocomplete="url"' in box
    assert 'class="sui-field' in p.split('id="f-business_website"', 1)[0].rsplit("<div", 2)[-2]

def test_website_export_and_import(env):
    A, c, G, tmp, src, host = env
    import settings_io
    assert "business_website" in settings_io.SETTING_KEYS
    with pytest.raises(settings_io.ImportProblem) as e:
        settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "settings": {"business_website": "http://x.com"}}), {})
    assert "business_website" not in str(e.value) and str(e.value).startswith("the website in this file")
    got = settings_io.parse(json.dumps({"lumen_aureum_settings": 1,
                                        "settings": {"business_website": " https://studio.example.com "}}), {})
    assert got[0]["business_website"] == "https://studio.example.com"
    c.post("/settings", data={"business_website": "https://studio.example.com"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        out = settings_io.export(A.setting, [], {})
    assert out["settings"]["business_website"] == "https://studio.example.com"

def test_uploading_refreshes_the_block_in_place_when_it_is_done(env):

    A, c, G, tmp, src, host = env
    sid = session(A, "realestate", delivery_method="link")
    build(c, sid, src)
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='uploading' WHERE session_id=?", (sid,))
    con.commit(); con.close()
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert f'data-poll-up="/session/{sid}/gallery/status"' in p
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["upload"] == "uploading"
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up' WHERE session_id=?", (sid,))
    con.commit(); con.close()
    assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["upload"] == "up"
    assert "data-poll-up" not in c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    js = (Path(ROOT) / "static" / "ui.js").read_text()
    assert "[data-poll-up]:not([data-poll-up-done])" in js and "r.upload !== 'uploading'" in js and "refreshParts();" in js
