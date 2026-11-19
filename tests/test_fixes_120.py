import json
import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def app_(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_seniors": "1", "preset_realestate": "1"}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('The Sample Family')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Sample', 'adult', 'avery@example.com')")
    con.commit(); con.close()
    return A, c

def new_session(A, type_, **cols):
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, type_, stage=cols.pop("stage", "quoted"),
                               shoot_date=cols.pop("shoot_date", (date.today() + timedelta(days=20)).isoformat()))
        A.g.db.commit()
    con = A.connect()
    for k_, v_ in cols.items():
        con.execute(f"UPDATE sessions SET {k_}=? WHERE id=?", (v_, sid))
    con.commit(); con.close()
    return sid

def q1(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def shoot_form(A, c, sid, **over):

    s = q1(A, "SELECT title, type, shoot_date, stops, notes FROM sessions WHERE id=?", (sid,))
    with A.app.test_request_context():
        A.db()
        seen = A.edit_stamp("sessions", sid)
    data = dict(seen=seen, title=s[0] or "", type=s[1], shoot_date=s[2] or "", stops=s[3] or "", notes=s[4] or "")
    data.update(over)
    return c.post(f"/session/{sid}", data=data, environ_base=E)

def test_delivery_method_is_chosen_on_the_shoot_form_from_the_quote_on(app_):
    A, c = app_
    sid = new_session(A, "family")
    p = page(c, f"/session/{sid}/edit")
    assert '<span>Files delivered by</span><select name="delivery_method">' in p and '<option value="link"' in p
    shoot_form(A, c, sid, delivery_method="link")
    assert q1(A, "SELECT delivery_method FROM sessions WHERE id=?", (sid,))[0] == "link"
    assert q1(A, "SELECT 1 FROM audit WHERE session_id=? AND action='Delivery changed from USB drive to Download link'", (sid,))
    shoot_form(A, c, sid, delivery_method="link")
    assert q1(A, "SELECT COUNT(*) FROM audit WHERE session_id=? AND action LIKE 'Delivery changed%'", (sid,))[0] == 1

def test_a_change_after_signing_warns_that_the_agreement_says_otherwise(app_):
    A, c = app_
    sid = new_session(A, "family", stage="booked")
    page(c, f"/session/{sid}/edit")
    con = A.connect()
    con.execute("UPDATE documents SET status='signed', signed=? WHERE session_id=? AND kind='agreement'",
                (date.today().isoformat(), sid))
    con.commit(); con.close()
    page(c, f"/session/{sid}/edit")
    with A.app.test_request_context():
        A.db()
        A._snapshot_signed()
        A.g.db.commit()
    assert "on a USB drive" in json.loads(q1(A, "SELECT data FROM doc_snapshots WHERE kind='agreement' AND ref=?", (sid,))[0])["delivery"]
    shoot_form(A, c, sid, delivery_method="link")
    p = page(c, f"/session/{sid}/edit")
    assert "The signed agreement says you deliver the photos" in p and "USB drive" in p
    shoot_form(A, c, sid, delivery_method="usb")
    assert "The signed agreement says" not in page(c, f"/session/{sid}/edit")

def gallery_host(A, tmp_path):
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir(exist_ok=True)
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', 'https://gallery.test/g/')")
    con.commit(); con.close()
    return host

def test_today_asks_for_the_download_gallery(app_, tmp_path):
    A, c = app_
    gallery_host(A, tmp_path)
    sid = new_session(A, "realestate", stage="delivered", shoot_date=(date.today() - timedelta(days=2)).isoformat())
    today = page(c, "/")

    assert "Download gallery" in today and "Build download gallery" in today
    n = today.count("Build download gallery")
    new_session(A, "realestate", stage="delivered", shoot_date=(date.today() - timedelta(days=2)).isoformat(),
                delivery_method="usb")
    assert page(c, "/").count("Build download gallery") == n
    assert sid

@pytest.mark.parametrize("backup,changed,says", [
    (True, True, "A backup of your data from before the update is under"),
    (False, True, "Your data was updated. No backup was made first, because backups aren"),
    (False, False, "Your data didn"),
])
def test_the_update_note_says_whether_a_backup_was_made(app_, backup, changed, says):
    A, c = app_
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
                (json.dumps(dict(version="9.9.9", backup=backup, changed=changed)),))
    con.commit(); con.close()
    p = page(c, "/")
    assert "Updated to Lumen Aureum 9.9.9." in p and says in p
    if not changed:
        assert "so no backup was made." in p

def test_the_agreement_says_how_long_the_link_stays_open(app_, tmp_path):
    A, c = app_
    sid = new_session(A, "realestate", stage="booked")
    assert "The link stays open" not in page(c, f"/session/{sid}/agreement")
    gallery_host(A, tmp_path)
    days = A.C.gallery_days("realestate")
    p = page(c, f"/session/{sid}/agreement")
    assert f"The link stays open for {days} days after you first open it; ask once and I&#39;ll extend it." in p
    usb = new_session(A, "realestate", stage="booked", delivery_method="usb")
    assert "The link stays open" not in page(c, f"/session/{usb}/agreement")
    fam = new_session(A, "family", stage="booked", delivery_method="link")
    assert f"The link stays open for {A.C.gallery_days('family')} days" in page(c, f"/session/{fam}/agreement")

def test_letter_wording_for_who_was_photographed(app_, monkeypatch):
    A, c = app_
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Daniel Ruiz')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (2, 'Daniel Ruiz', 'adult')")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        solo = A.create_session(2, "senior", stage="shot", shoot_date=(date.today() - timedelta(days=3)).isoformat())
        A.g.db.commit()
    letter = page(c, f"/letters/{solo}/qr-letter.pdf")
    assert "photograph you on" in letter and "your family" not in letter
    pid = q1(A, "SELECT id FROM people WHERE household_id=2")[0]
    con = A.connect()
    con.execute("INSERT OR IGNORE INTO session_people VALUES (?, ?)", (solo, pid))
    con.commit(); con.close()
    letter = page(c, f"/letters/{solo}/qr-letter.pdf")
    assert "photograph you on" in letter and "photograph Daniel" not in letter
    house = new_session(A, "realestate", stage="shot", title="418 Pine Street",
                        shoot_date=(date.today() - timedelta(days=3)).isoformat())
    assert "photograph 418 Pine Street on" in page(c, f"/letters/{house}/qr-letter.pdf")

def test_the_session_title_names_the_gallery_and_the_zip(app_, tmp_path):
    A, c = app_
    sid = new_session(A, "realestate", stage="shot", title="418 Pine Street")
    with A.app.test_request_context():
        A.db()
        brand = A.app.config["gallery_brand"](A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True))
        untitled = new_session(A, "realestate", stage="shot")
        other = A.app.config["gallery_brand"](A.q("SELECT * FROM sessions WHERE id=?", (untitled,), one=True))
    assert brand["client"] == "418 Pine Street" and other["client"] == "The Sample Family"
    from PIL import Image
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (60, 40), "red").save(src / "a.jpg")
    from plugins import gallery as G
    key = G.new_key()
    G.build("gal_title", src, key, download=True, brand=dict(brand))
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = (G.OUT_ROOT / "gal_title" / "manifest.bin").read_bytes()
    m = json.loads(AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))
    assert m["title"] == "418 Pine Street" and m["zips"][0]["name"] == "418 Pine Street.zip"

def test_email_link_wording(app_, tmp_path):

    A, c = app_
    gallery_host(A, tmp_path)
    from urllib.parse import unquote
    sid = new_session(A, "family", stage="delivered", title="Fall minis", shoot_date=(date.today() - timedelta(days=9)).isoformat())
    con = A.connect()
    con.execute("UPDATE sessions SET gallery_id='galMail01', retouch_picks='a.jpg: brighten' WHERE id=?", (sid,))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files, upload_state, uploaded_at) VALUES (?, ?, ?, 1, 'up', ?)",
                (sid, "k" * 43, date.today().isoformat(), date.today().isoformat()))
    con.commit(); con.close()
    p = unquote(page(c, f"/c/1?s={sid}"))
    assert "subject=Your gallery link: Fall minis" in p and "Thank you for your picks. Here is your gallery link again:" in p
    assert "pick your favorites" not in p

@pytest.mark.parametrize("value,says", [("http://example.com", "it must start with https://"),
                                        ("not a website", "that isn")])
def test_a_refused_website_says_why(app_, value, says):
    A, c = app_
    r = c.post("/settings", data={"business_website": value}, environ_base=E)
    shown = c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)
    assert "The website wasn" in shown and says in shown
    import settings_io
    with pytest.raises(settings_io.ImportProblem) as e:
        settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "settings": {"business_website": value}}), {})
    assert str(e.value).startswith("the website in this file " + ("must start with https://" if value.startswith("http:")
                                                                   else "isn't a web address"))

def test_the_backups_page_counts_the_update_backup(app_):
    A, c = app_
    con = A.connect()
    con.execute("INSERT INTO vault_objects (kind, name, backend, size, created) VALUES ('upgrade', 'db/x.age', 'local', 300000, ?)",
                (date.today().isoformat() + "T23:29:00",))
    con.commit(); con.close()
    p = page(c, "/vault")
    i = p.index("Last database backup")
    assert "<td>-</td>" not in p[i:i + 120]

def test_the_working_page_also_says_the_signed_agreement_differs(app_):
    A, c = app_
    sid = new_session(A, "family", stage="booked")
    page(c, f"/session/{sid}/edit")
    con = A.connect()
    con.execute("UPDATE documents SET status='signed', signed=? WHERE session_id=? AND kind='agreement'",
                (date.today().isoformat(), sid))
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        A._snapshot_signed()
        A.g.db.commit()
    assert "The signed agreement says" not in page(c, f"/c/1?s={sid}")
    shoot_form(A, c, sid, delivery_method="link")
    assert "The signed agreement says you deliver the photos" in page(c, f"/c/1?s={sid}")
