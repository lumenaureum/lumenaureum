import base64
import json
import os
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = b"SECRET-GPS-48.8584N"
NAMES = ("Kitchen-Window.jpg", "Porch Swing.jpg", "Café front.jpg")

def photo(path, size, color):
    from PIL import Image
    im = Image.new("RGB", size, color)
    for x_ in range(0, size[0], 37):
        im.putpixel((x_, x_ % size[1]), (255, 255, 255))
    exif = Image.Exif()
    exif[0x010E] = SECRET.decode()
    im.save(path, "JPEG", exif=exif.tobytes(), comment=SECRET)

@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "data/photos"),
                 ("CRM_GALLERY_ROOT", "data/galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_realestate": "1"}, environ_base=E)
    src = tmp_path / "data" / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for n_, (sz, col) in zip(NAMES, (((1600, 1000), "red"), ((900, 1400), "blue"), ((1200, 800), "green"))):
        photo(src / n_, sz, col)
    con = A.connect()
    con.execute("INSERT INTO households (name, address) VALUES ('The Sample Family', '1 Test Road\nSampletown')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Sample', 'adult', 'avery@example.com')")
    con.commit(); con.close()
    from plugins import gallery as G
    return A, c, G, tmp_path, src

def session(A, type_="realestate", **cols):
    con = A.connect()
    cur = con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, ?, 'shot', ?)",
                      (type_, (date.today() - timedelta(days=3)).isoformat()))
    sid = cur.lastrowid
    for k_, v_ in cols.items():
        con.execute(f"UPDATE sessions SET {k_}=? WHERE id=?", (v_, sid))
    con.commit(); con.close()
    return sid

def row(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def opened(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def write_card(c, sid, src, mount):
    mount.mkdir(exist_ok=True)
    r = c.post(f"/session/{sid}/gallery/card", data={"to": "folder", "folder": str(mount), "source_dir": str(src)},
               environ_base=E)
    assert r.status_code == 302
    return mount

def secrets_of(G, code):

    key = G.card_key(code)
    raw = G.key_bytes(key)
    bare = code.replace("-", "")
    return [code.encode(), bare.encode(), bare.lower().encode(), key.encode(), raw, raw.hex().encode(),
            base64.b64encode(raw), base64.b64encode(raw).rstrip(b"=")]

def assert_sealed(G, files, code):

    bad = secrets_of(G, code)
    names = [n_.encode() for n_ in NAMES] + [Path(n_).stem.encode() for n_ in NAMES] + \
            [n_.encode("utf-16-le") for n_ in NAMES]
    seen = set()
    for path, data in files:
        seen.add(path)
        for b_ in bad:
            assert b_ not in data, f"the key is on the card ({path})"
        for n_ in names:
            assert n_ not in data and n_.decode("utf-8", "ignore") not in path, f"a photo's name is readable ({path})"
        assert SECRET not in data
        assert b"\xff\xd8\xff" not in data[:4] and b"JFIF" not in data and b"Exif\x00" not in data, f"a readable photo ({path})"
        if path.startswith(G.CARD_DIR + "/") and path != G.CARD_DIR + "/" + G.THEME_FONT_FILE:
            assert Path(path).suffix == ".bin"
    assert {G.CARD_PAGE, G.CARD_README, G.CARD_DIR + "/manifest.bin"} <= seen
    assert not any(p_.endswith((".php", ".htaccess", "index.html")) for p_ in seen)

def test_card_code_reads_back_and_catches_typos():
    fresh_modules()
    from plugins import gallery as G
    code = G.new_card_code()
    assert len(code) == 33 and code.count("-") == 6
    assert G.card_code_norm(code.lower().replace("-", " ")) == code.replace("-", "")
    assert G.card_key(code) == G.card_key(" " + code.lower() + " ")
    typo = code[:-1] + ("0" if code[-1] != "0" else "1")
    assert G.card_code_norm(typo) is None
    assert G.card_code_norm(code[:-3]) is None
    assert G.card_key(code) != G.card_key(G.new_card_code())
    assert len(G.key_bytes(G.card_key(code))) == 32

def test_card_method_is_offered(env):
    A, c, G, tmp, src = env
    assert A.C.DELIVERY_METHODS["card"][0] == "Sealed card (SD or USB)"
    sid = session(A, delivery_method="card", stage="delivered")
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Write the sealed card" in page and "Print the card letter" in page and "Write to the card" in page
    assert "On a sealed card" in page

def test_card_folder_holds_no_key_and_no_plaintext(env):
    A, c, G, tmp, src = env
    sid = session(A, delivery_method="card")
    mount = write_card(c, sid, src, tmp / "mount")
    (mount / "DCIM").mkdir()
    (mount / "DCIM" / "keep.txt").write_text("the card's own files stay")
    code = row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (sid,))[0]
    files = [(p_.relative_to(mount).as_posix(), p_.read_bytes()) for p_ in sorted(mount.rglob("*"))
             if p_.is_file() and "DCIM" not in p_.parts]
    assert_sealed(G, files, code)

    key = G.card_key(code)
    box = mount / G.CARD_DIR
    m = json.loads(opened(G, key, (box / "manifest.bin").read_bytes(), "manifest.bin"))
    assert m["dl"] == 1 and [it["n"] for it in m["items"]] == sorted(NAMES, key=lambda n_: G.natural(Path(n_)))
    for it in m["items"]:
        assert opened(G, key, (box / it["o"]).read_bytes(), it["o"])[:3] == b"\xff\xd8\xff"
    z = b"".join(opened(G, key, (box / p_).read_bytes(), p_) for p_ in m["zips"][0]["parts"])
    import io
    assert sorted(zipfile.ZipFile(io.BytesIO(z)).namelist()) == sorted(NAMES)
    with pytest.raises(Exception):
        opened(G, G.card_key(G.new_card_code()), (box / "manifest.bin").read_bytes(), "manifest.bin")
    page = (mount / G.CARD_PAGE).read_text()
    assert 'data-card="1"' in page and G.VIEWER_POLICY in page and "unsafe-inline" not in page.split("<style>")[0].replace("style-src 'unsafe-inline'", "")
    assert "computer" in (mount / G.CARD_README).read_text()

    write_card(c, sid, src, mount)
    assert row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (sid,))[0] == code
    assert (mount / "DCIM" / "keep.txt").is_file()
    assert sorted(p_.name for p_ in mount.iterdir()) == sorted([G.CARD_PAGE, G.CARD_README, G.CARD_DIR, "DCIM"])
    assert not any(G.OUT_ROOT.glob("g-*")), "nothing of the card stays in the build folder"

def test_writing_the_card_is_the_delivery(env):
    A, c, G, tmp, src = env
    sid = session(A, delivery_method="card")
    assert not row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]
    write_card(c, sid, src, tmp / "mount")
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] == date.today().isoformat()
    assert "sealed card" in row(A, "SELECT note FROM step_closes WHERE session_id=? AND step='sd_shipped'", (sid,))[0]
    hist = [r_[0] for r_ in A.connect().execute("SELECT action FROM audit WHERE session_id=?", (sid,)).fetchall()]
    assert any(h_.startswith("Sealed card written: 3 photos") for h_ in hist), hist
    assert not any(str(tmp / "mount") in h_ for h_ in hist)

def test_card_zip_to_copy(env):
    A, c, G, tmp, src = env
    sid = session(A, delivery_method="card", stage="delivered")
    r = c.post(f"/session/{sid}/gallery/card", data={"to": "zip", "source_dir": str(src)}, environ_base=E)
    assert r.status_code == 302
    assert not row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Save the card zip (" in page
    r = c.get(f"/session/{sid}/gallery/card.zip", environ_base=E, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    data = r.get_data()
    r.close()
    import io
    zf = zipfile.ZipFile(io.BytesIO(data))
    code = row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (sid,))[0]
    assert_sealed(G, [(n_, zf.read(n_)) for n_ in zf.namelist()], code)
    assert not row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]
    assert not list((G.OUT_ROOT / ".cards").glob("*.zip")), "the zip leaves this computer once saved"
    assert c.get(f"/session/{sid}/gallery/card.zip", environ_base=E, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 404

def test_card_refusals(env):
    A, c, G, tmp, src = env
    sid = session(A, delivery_method="usb")
    c.post(f"/session/{sid}/gallery/card", data={"to": "folder", "folder": str(tmp)}, environ_base=E)
    assert not row(A, "SELECT 1 FROM gallery_cards WHERE session_id=?", (sid,))
    sid = session(A, delivery_method="card")
    for folder in ("", "relative/path", str(tmp / "not-there"), str(tmp / "data"), str(tmp)):
        c.post(f"/session/{sid}/gallery/card", data={"to": "folder", "folder": folder, "source_dir": str(src)}, environ_base=E)
        assert not row(A, "SELECT written_at FROM gallery_cards WHERE session_id=?", (sid,)), folder
    assert not row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]

def test_card_waits_for_payment(env, monkeypatch):
    A, c, G, tmp, src = env
    sid = session(A, "family", delivery_method="card")
    fn = A.app.view_functions["gallery_card"]
    ctx = next(c_.cell_contents for c_ in fn.__closure__ if type(c_.cell_contents).__name__ == "PluginContext")
    monkeypatch.setattr(ctx, "release_blocked", lambda sid_: 150.0)
    (tmp / "mount").mkdir()
    r = c.post(f"/session/{sid}/gallery/card", data={"to": "folder", "folder": str(tmp / "mount"), "source_dir": str(src)},
               environ_base=E)
    assert r.status_code == 302 and not list((tmp / "mount").iterdir())
    monkeypatch.setattr(ctx, "release_blocked", lambda sid_: 0.0)
    c.post(f"/session/{sid}/gallery/card", data={"to": "folder", "folder": str(tmp / "mount"), "source_dir": str(src)},
           environ_base=E)
    assert (tmp / "mount" / G.CARD_PAGE).is_file()

def test_card_letter_carries_the_code_and_the_computer_note(env, monkeypatch):
    A, c, G, tmp, src = env
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    sid = session(A, delivery_method="card")
    before = c.get(f"/letters/{sid}/card-letter.pdf", environ_base=E).get_data(as_text=True)
    assert "has not been written yet" in before
    write_card(c, sid, src, tmp / "mount")
    code = row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (sid,))[0]
    letter = c.get(f"/letters/{sid}/card-letter.pdf", environ_base=E).get_data(as_text=True)
    assert code in letter and "DRAFT" not in letter
    assert "Open them on a computer. A phone or tablet can't open them from the card." in letter
    assert "Open your photos.html" in letter and "Sealed photos" in letter
    assert "—" not in letter and "colour" not in letter

def _card(tmp):

    fresh_modules()
    os.environ["CRM_GALLERY_ROOT"] = str(tmp / "galleries")
    from plugins import gallery as G
    import importlib
    G = importlib.reload(G)
    src = tmp / "src"
    src.mkdir()
    for n_, col in zip(NAMES, ("red", "blue", "green")):
        photo(src / n_, (800, 600), col)
    code = G.new_card_code()
    b = G.build(G.new_gid(), src, G.card_key(code), card=True, brand=dict(client="Sample photos", studio="Sample Studio"),
                credit="1")
    mount = tmp / "mount"
    mount.mkdir()
    G.write_card(b.out_dir, mount)
    return G, mount, code

def _launch(kind):
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
    except Exception as e:
        pytest.skip(f"playwright is not available: {e}")
    try:
        return pw, getattr(pw, kind).launch()
    except Exception as e:
        pw.stop()
        pytest.skip(f"{kind} is not available: {e}")

@pytest.mark.parametrize("kind", ["chromium", "firefox"])
def test_page_opens_from_the_card_with_the_code_only(tmp_path, kind, monkeypatch):
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    G, mount, code = _card(tmp_path)
    pw, br = _launch(kind)
    try:
        pg = br.new_page(accept_downloads=True)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.add_init_script("delete window.showDirectoryPicker")
        pg.goto((mount / G.CARD_PAGE).as_uri())
        pg.wait_for_selector("#msgact button:has-text('Choose the folder')")
        assert pg.evaluate("isSecureContext && !!crypto.subtle")
        pg.set_input_files("#cardpick", str(mount / G.CARD_DIR))
        pg.wait_for_selector("#cardcode")
        pg.fill("#cardcode", code[:-1] + ("0" if code[-1] != "0" else "1"))
        pg.click("text=Open my photos")
        pg.wait_for_selector("text=That code doesn't look right")
        pg.fill("#cardcode", G.new_card_code())
        pg.click("text=Open my photos")
        pg.wait_for_selector("text=That code doesn't open these photos")
        assert pg.locator(".cell").count() == 0 and pg.text_content("#title") == ""
        pg.fill("#cardcode", code.lower().replace("-", " "))
        pg.click("text=Open my photos")
        pg.wait_for_selector(".cell img", timeout=30000)
        assert pg.text_content("#title") == "Sample photos" and pg.locator(".cell").count() == 3
        assert pg.locator("#send").is_hidden()
        pg.click(".cell >> nth=0")
        pg.wait_for_selector("#dl:visible")
        with pg.expect_download() as d:
            pg.click("#dl")
        assert d.value.suggested_filename in NAMES and Path(d.value.path()).read_bytes()[:3] == b"\xff\xd8\xff"
        pg.keyboard.press("Escape")
        with pg.expect_download() as d:
            pg.click("#dlallb")
        zf = zipfile.ZipFile(d.value.path())
        assert sorted(zf.namelist()) == sorted(NAMES)
        assert not errs, errs
    finally:
        br.close()
        pw.stop()

def test_page_takes_the_card_itself_from_the_folder_picker(tmp_path, monkeypatch):

    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    G, mount, code = _card(tmp_path)
    files = {p_.name: base64.b64encode(p_.read_bytes()).decode() for p_ in (mount / G.CARD_DIR).iterdir()}
    pw, br = _launch("chromium")
    try:
        pg = br.new_page()
        pg.add_init_script("""window.__card = null;
          window.showDirectoryPicker = async () => {
            const f = (n, b) => ({ kind: "file", getFile: async () => new File([Uint8Array.from(atob(b), c => c.charCodeAt(0))], n) });
            const box = { kind: "directory", files: () => window.__card,
              async getFileHandle(n) { if (!window.__card[n]) throw new DOMException("x", "NotFoundError"); return f(n, window.__card[n]); },
              async *entries() { for (const n of Object.keys(window.__card)) yield [n, f(n, window.__card[n])]; } };
            return { kind: "directory", async getFileHandle(n) { throw new DOMException("x", "NotFoundError"); },
              async getDirectoryHandle(n) { if (n !== "Sealed photos") throw new DOMException("x", "NotFoundError"); return box; },
              async *entries() {} };
          };""")
        pg.goto((mount / G.CARD_PAGE).as_uri())
        pg.evaluate("f => { window.__card = f; }", files)
        pg.click("#msgact button:has-text('Choose the folder')")
        pg.wait_for_selector("#cardcode")
        pg.fill("#cardcode", code)
        pg.click("text=Open my photos")
        pg.wait_for_selector(".cell img", timeout=30000)
        assert pg.locator(".cell").count() == 3
    finally:
        br.close()
        pw.stop()
