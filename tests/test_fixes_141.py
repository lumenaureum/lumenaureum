import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

LICENSE_LINE = "Your license to use the photos is set out in your agreement"

def _paid_receipt(A, c, sid):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Shoot", "amount": "300"}, environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "300", "method": "cash"},
           environ_base=E)
    c.post(f"/session/{sid}/receipt/issue", environ_base=E)
    assert _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='R'", (sid,))
    return c.get(f"/session/{sid}/receipt", environ_base=E).get_data(as_text=True)

def _sign_agreement(A, sid):
    con = A.connect()
    con.execute("INSERT INTO documents (session_id, kind, status, signed) VALUES (?, 'agreement', 'signed', '2026-11-01')",
                (sid,))
    con.commit()
    con.close()

def test_a_licensed_receipt_names_the_agreement_only_when_one_is_signed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("products",))
    sid = _new(A, c, "product")
    page = _paid_receipt(A, c, sid)
    assert "Thank you." in page
    assert LICENSE_LINE not in page
    assert "copyright in your delivered photos is yours" not in page
    sid2 = _new(A, c, "product")
    _sign_agreement(A, sid2)
    assert LICENSE_LINE in _paid_receipt(A, c, sid2)

    _sign_agreement(A, sid)
    assert LICENSE_LINE not in c.get(f"/session/{sid}/receipt", environ_base=E).get_data(as_text=True)

def test_a_receipt_frozen_before_the_rule_reads_as_it_did(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("products",))
    with A.app.test_request_context():
        html = A.bill_doc_html(dict(receipt=True, license=True, event_family=False, lines=[], payments=[], total=0,
                                    paid=0, balance=0, number="R-1"))
    assert LICENSE_LINE in html

def test_a_month_that_took_money_back_draws_its_own_size_in_red_not_full_width(tmp_path, monkeypatch):
    import re
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family")
    con = A.connect()
    for day, amt, kind in (("2026-03-10", 818, "balance"), ("2026-04-02", -40, "refund"), ("2026-05-05", 200, "deposit")):
        con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (?,?,?,?)", (sid, kind, amt, day))
    con.commit()
    con.close()
    page = c.get("/money?year=2026", environ_base=E).get_data(as_text=True)
    box = page.split('aria-label="Received by month"', 1)[1].split("</ul>", 1)[0]
    bars = re.findall(r'<li class="ui-bar-row( ui-bar-neg)?">.*?<span class="ui-bar" style="width:(-?[\d.]+)%', box)
    assert [(bool(n), float(w)) for n, w in bars] == [(False, 100.0), (True, 5.0), (False, 24.0)]
    css = open(os.path.join(ROOT, "static", "style.css")).read()
    assert ".ui-bar-neg .ui-bar { background: var(--crit); }" in css

def test_finances_by_month_with_nothing_but_zero_does_not_fail(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family")
    con = A.connect()
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (?, 'deposit', 0, '2026-02-01')", (sid,))
    con.commit()
    con.close()
    assert c.get("/money?year=2026", environ_base=E).status_code == 200

def test_a_long_timeline_keeps_its_date_labels_apart(tmp_path, monkeypatch):
    import re
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family")
    con = A.connect()
    con.execute("UPDATE sessions SET created=date('now', '-200 days') WHERE id=?", (sid,))
    con.execute("UPDATE sessions SET stage='booked' WHERE id=?", (sid,))
    con.commit()
    con.close()
    page = c.get("/c/1", environ_base=E).get_data(as_text=True)
    ticks = [(m.group(1) == " minor", float(m.group(2)))
             for m in re.finditer(r'<i class="tick( minor)?" style="left:([\d.]+)%">', page)]
    assert len(ticks) > 3
    shown = [x for _, x in ticks]
    assert all(b - a >= 5 for a, b in zip(shown, shown[1:]))
    phone = [x for minor, x in ticks if not minor]
    assert phone and all(b - a >= 12 for a, b in zip(phone, phone[1:]))
    css = open(os.path.join(ROOT, "static", "style.css")).read()
    assert ".tick.minor b { display: none; }" in css

def test_a_short_timeline_keeps_every_week(tmp_path, monkeypatch):
    import re
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", shoot_date=__import__("datetime").date.today().isoformat())
    con = A.connect()
    con.execute("UPDATE sessions SET stage='booked' WHERE id=?", (sid,))
    con.commit()
    con.close()
    page = c.get("/c/1", environ_base=E).get_data(as_text=True)
    assert re.findall(r'<i class="tick"', page)

    marks = [float(x_) for x_ in re.findall(r'class="j-(?:today|shoot)" style="left:([\d.]+)%', page)]
    for x_ in re.findall(r'class="tick minor" style="left:([\d.]+)%', page):
        assert any(abs(float(x_) - m_) < 13 for m_ in marks), x_

def test_phone_rules_for_the_walk_bugs_are_in_the_stylesheet():

    css = open(os.path.join(ROOT, "static", "style.css")).read()
    for rule in (".t-stage .t-st { white-space: normal;", ".t-foot .t-shoot { white-space: normal;",
                 ".qtable { display: table; table-layout: fixed; width: 100%; }", "table.stack-phone thead { display: none; }",
                 ".ui-photo { display: grid; grid-template-columns: 100px minmax(0, 1fr);"):
        assert rule in css, rule
    client = open(os.path.join(ROOT, "templates", "client.html")).read()
    session = open(os.path.join(ROOT, "templates", "session.html")).read()

    assert 'people-t' not in client and "id='person-'" in client and "<table" not in session
    assert 'Signed on <input type="date"' in session

CW = "heron maple tide river"

def test_every_session_type_asks_for_a_codeword_by_default(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families", "products", "events"))
    import config as C
    assert C.SESSION_TYPES and all(C.POLICY[t].get("gallery_codeword") is True for t in C.SESSION_TYPES)
    page = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    assert 'name="gallery_codeword"' in page
    assert "in person or on the phone" in page and "never written into any email the app drafts" in page

def test_turning_the_codeword_off_for_a_type_stays_off(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    import config as C
    form = {"free_reschedule": "1", "notice_hours": "24", "late_minutes": "0", "delivery": "", "weather": "",
            "keep_raw_days": "90", "license_use": "x"}
    c.post("/settings/type/family", data=form, environ_base=E)
    assert C.POLICY["family"].get("gallery_codeword") is False
    assert 'name="gallery_codeword"' not in c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    c.post("/settings/type/family", data=dict(form, gallery_codeword="1"), environ_base=E)
    assert C.POLICY["family"].get("gallery_codeword") is True

def test_an_existing_session_without_a_codeword_is_not_given_one(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family")
    assert _q(A, "SELECT gallery_codeword FROM sessions WHERE id=?", (sid,))[0][0] in ("", None)
    c.get(f"/session/{sid}/edit", environ_base=E)
    assert _q(A, "SELECT gallery_codeword FROM sessions WHERE id=?", (sid,))[0][0] in ("", None)

def _drafts(page):

    import html
    import re
    from urllib.parse import unquote
    return [unquote(html.unescape(m)) for m in re.findall(r'href="((?:mailto|sms):[^"]*)"', page)]

def test_no_app_drafted_email_carries_the_codeword(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("UPDATE people SET email='avery@example.com', phone='555-0100' WHERE id=1")
    con.commit()
    con.close()

    sid = _new(A, c, "family", gallery_codeword=CW, title="Heron Maple  Tide River portraits")
    assert _q(A, "SELECT gallery_codeword FROM sessions WHERE id=?", (sid,))[0][0] == CW
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Shoot " + CW, "amount": "300"}, environ_base=E)
    vid = (_q(A, "SELECT MAX(id) FROM quote_versions WHERE session_id=?", (sid,)) or [[None]])[0][0]
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    import config as C
    monkeypatch.setattr(C, "pay_details", lambda: "Memo line: Heron maple tide river")
    urls = [f"/session/{sid}/details", f"/session/{sid}/agreement", f"/session/{sid}/invoice", f"/fees/sheet?type=family&sid={sid}"]
    if vid:
        urls.append(f"/quote/{vid}")
    seen, left_out = 0, 0
    from formats import CODEWORD_LEFT_OUT as LO
    for u in urls:
        page = c.get(u, environ_base=E).get_data(as_text=True)
        for d in _drafts(page):
            if "avery@example.com" in d or d.startswith("sms:"):
                seen += 1
                assert "heron" not in d.lower(), (u, d)
                left_out += LO in d
    assert seen >= 6 and left_out >= 1, (seen, left_out)
    from formats import drop_codewords, CODEWORD_LEFT_OUT
    assert drop_codewords("Your Heron  MAPLE tide river session", [CW]) == f"Your {CODEWORD_LEFT_OUT} session"
    assert drop_codewords("nothing here", ["", None]) == "nothing here"

def test_the_gallery_link_email_never_carries_the_codeword(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("UPDATE sessions SET title=?, gallery_codeword=? WHERE id=1", ("Heron maple tide river day", CW))
    con.execute("UPDATE galleries SET built_at=?, n_files=3 WHERE session_id=1", (__import__("datetime").date.today().isoformat(),))
    con.commit()
    con.close()
    drafts = [d for d in _drafts(e.c.get("/c/1", environ_base=PE.E).get_data(as_text=True)) if "avery@example.com" in d.lower()]
    from formats import CODEWORD_LEFT_OUT
    link = [d for d in drafts if "gallery" in d.lower() or "photos are ready" in d.lower()]
    assert link, drafts
    assert all("heron" not in d.lower() for d in drafts) and any(CODEWORD_LEFT_OUT in d for d in link)

def _wait_thread(name):
    import threading
    for t in [t for t in threading.enumerate() if t.name == name]:
        t.join(timeout=60)
        assert not t.is_alive(), name

def _row(A, sql, *a):
    return _q(A, sql, a)[0]

def test_replacing_a_leaked_link_kills_the_old_one_and_builds_a_new_one(tmp_path, monkeypatch):
    import shutil
    import prints_env as PE
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    e = PE.make(tmp_path, monkeypatch)
    A, c = e.A, e.c
    photos = tmp_path / "photos" / "lane"
    photos.mkdir(parents=True)
    for p in (tmp_path / "src").glob("*.jpg"):
        shutil.copy(p, photos / p.name)
    con = A.connect()
    con.execute("UPDATE galleries SET built_at=?, n_files=3, source_dir=? WHERE session_id=1",
                (__import__("datetime").date.today().isoformat(), str(photos) + "/"))
    con.execute("UPDATE sessions SET letter_mailed='2026-11-01' WHERE id=1")
    con.commit()
    con.close()
    old_gid, old_key = _row(A, "SELECT s.gallery_id, g.key FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    assert (e.host / old_gid).is_dir()
    oid = "ab" * 16
    PE.send(e, PE.payload(e, oid))
    page = c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
    assert "Replace this link" in page
    r = c.post("/session/1/gallery/replace-link", environ_base=PE.E)
    assert r.status_code == 302
    _wait_thread("gallery-1")
    new_gid, new_key, state, built = _row(A, """SELECT s.gallery_id, g.key, g.upload_state, g.built_at FROM sessions s
                                                JOIN galleries g ON g.session_id=s.id WHERE s.id=1""")
    assert new_gid != old_gid and new_gid.startswith("g-") and new_key != old_key
    assert not (e.host / old_gid).exists()
    assert state is None and built
    assert not (e.G.OUT_ROOT / old_gid).exists()
    assert [tuple(r_) for r_ in _q(A, "SELECT state, gid FROM print_orders WHERE oid=?", (oid,))] == [("new", new_gid)]
    assert _row(A, "SELECT letter_mailed FROM sessions WHERE id=1")[0] is None
    assert _q(A, "SELECT 1 FROM tasks WHERE session_id=1 AND title='Send the client the new gallery link' AND done IS NULL")
    hist = [r_[0] for r_ in _q(A, "SELECT action FROM audit WHERE session_id=1 ORDER BY id")]
    assert any(h.startswith("Replaced the gallery's link: the old link no longer opens anything") for h in hist)

    c.post("/session/1/gallery/upload", environ_base=PE.E)
    _wait_thread("gal-up-1")
    assert _row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    assert (e.host / new_gid / "index.html").is_file() and not (e.host / old_gid).exists()
    with A.app.test_request_context():
        link = A.app.config["gallery_link"](1)
    assert f"/{new_gid}/#k={new_key}" in link and old_key not in link

def test_replace_link_refuses_without_the_photos_and_changes_nothing(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    A, c = e.A, e.c
    con = A.connect()
    con.execute("UPDATE galleries SET built_at=?, n_files=3 WHERE session_id=1", (__import__("datetime").date.today().isoformat(),))
    con.commit()
    con.close()
    before = _row(A, "SELECT s.gallery_id, g.key, g.upload_state FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    c.post("/session/1/gallery/replace-link", environ_base=PE.E)
    assert _row(A, "SELECT s.gallery_id, g.key, g.upload_state FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1") == before
    assert (e.host / before[0]).is_dir()
    assert "no longer here" in c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)

def _served_env(tmp_path, monkeypatch):
    import prints_env as PE
    import hostcheck
    e = PE.make(tmp_path, monkeypatch)
    import refit
    con = e.A.connect()
    con.execute("UPDATE galleries SET built_at=?, page_set=? WHERE session_id=1",
                (__import__("datetime").date.today().isoformat(), refit.fingerprint(e.G)))
    con.commit()
    con.close()
    from plugins import gallery as G
    page = (G.OUT_ROOT / PE.GID / "index.html").read_text()
    (e.host / PE.GID / "index.html").write_bytes(refit.page_for(G, PE.GID, page))

    def fetch(url, headers=None):
        rel = url.split("https://gallery.test/g/", 1)[-1]
        p = e.host / rel
        return (200, p.read_bytes()) if p.is_file() else (404, b"not found")
    monkeypatch.setattr(hostcheck, "fetch", fetch)
    return e, PE

def _check(e):
    with e.A.app.app_context():
        e.A.app.config["gallery_served_check"](force=True)
        e.A.db().commit()

def test_a_changed_gallery_page_on_the_web_host_shows_on_today_and_can_be_taken_down(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    _check(e)
    today = lambda: e.c.get("/", environ_base=PE.E).get_data(as_text=True)
    assert "is not what Lumen Aureum put there" not in today()
    page = e.host / PE.GID / "index.html"
    page.write_bytes(page.read_bytes().replace(b"</body>", b"<script>steal()</script></body>"))
    _check(e)
    assert "is not what Lumen Aureum put there" not in today()
    _check(e)
    t = today()
    assert "The gallery of The Sample Family on your web host is not what Lumen Aureum put there" in t
    assert "the page as served" in t and "the page on the host" in t
    block = e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
    assert "Take down now" in block and "Put the published page back" in block
    hist = [r[0] for r in _q(e.A, "SELECT action FROM audit WHERE session_id=1")]
    assert sum(h.startswith("The gallery page or scripts on the web host are not the ones") for h in hist) == 1
    e.c.post("/session/1/gallery/take-down", environ_base=PE.E)
    assert not (e.host / PE.GID).exists()
    assert _q(e.A, "SELECT upload_state FROM galleries WHERE session_id=1")[0][0] == "removed"
    assert "is not what Lumen Aureum put there" not in today()

def test_a_changed_script_is_named_and_an_unreachable_host_is_not_judged(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    import hostcheck
    gate = e.host / PE.GID / "gate.php"
    assert gate.is_file()
    gate.write_bytes(gate.read_bytes() + b"\n<?php mail('x@example.org', 'key', $_SERVER['HTTP_X_PROOF']); ?>")
    _check(e)
    _check(e)
    assert "gate.php changed" in e.c.get("/", environ_base=PE.E).get_data(as_text=True)
    monkeypatch.setattr(hostcheck, "fetch", lambda url, headers=None: (0, b""))
    gate.write_bytes(b"")
    _check(e)
    assert "gate.php changed" in e.c.get("/", environ_base=PE.E).get_data(as_text=True)

def test_put_the_published_page_back(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    import hostcheck
    import refit
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="ok", version="8.2", wait=0, detail=""))
    monkeypatch.setattr(hostcheck, "door_closed", lambda *a, **k: True)
    monkeypatch.setattr(refit, "canary", lambda *a, **k: "")
    page = e.host / PE.GID / "index.html"
    good = page.read_bytes()
    gate = e.host / PE.GID / "gate.php"
    good_gate = gate.read_bytes()
    page.write_bytes(good + b"<!-- changed -->")
    gate.write_bytes(good_gate + b"\n")
    _check(e)
    _check(e)
    assert "is not what Lumen Aureum put there" in e.c.get("/", environ_base=PE.E).get_data(as_text=True)
    e.c.post("/session/1/gallery/put-back-page", environ_base=PE.E)
    assert page.read_bytes() == good and gate.read_bytes() == good_gate
    assert "is not what Lumen Aureum put there" not in e.c.get("/", environ_base=PE.E).get_data(as_text=True)
    _check(e)
    _check(e)
    assert "is not what Lumen Aureum put there" not in e.c.get("/", environ_base=PE.E).get_data(as_text=True)

def _cert(path, names):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key()).serial_number(1)
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(x_) for x_ in names]), critical=False)
         .sign(k, hashes.SHA256()))
    path.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    (path.parent / (path.stem + ".key")).write_bytes(k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

def test_the_names_on_your_own_certificate_are_accepted_without_crm_hostnames(tmp_path, monkeypatch):
    import netsec
    cert = tmp_path / "tls.crt"
    _cert(cert, ["nas.example.com", "*.home.example.org"])
    monkeypatch.delenv("CRM_TLS_CERT", raising=False)
    assert not netsec.host_ok("nas.example.com")
    monkeypatch.setenv("CRM_TLS_CERT", str(cert))
    for ok in ("nas.example.com", "NAS.example.com:30589", "studio.home.example.org"):
        assert netsec.host_ok(ok), ok
    for no in ("evil.example.net", "example.com", "home.example.org", "a.b.home.example.org", "example.com.evil.net"):
        assert not netsec.host_ok(no), no
    assert netsec.host_ok("192.168.1.10:30589") and netsec.host_ok("localhost")
    monkeypatch.setenv("CRM_TLS_CERT", str(tmp_path / "missing.crt"))
    assert not netsec.host_ok("nas.example.com")

def test_pillow_opens_only_the_formats_the_app_accepts(tmp_path, monkeypatch):
    import io
    A, c = _app(tmp_path, monkeypatch)
    from PIL import Image
    import safe_media
    assert sorted(Image.ID) == sorted(safe_media.PIL_FORMATS) and set(Image.OPEN) == set(safe_media.PIL_FORMATS)
    assert Image.MAX_IMAGE_PIXELS == safe_media.PIL_MAX_PIXELS
    for fmt in ("JPEG", "PNG", "GIF", "ICO"):
        b = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(b, fmt)
        with Image.open(io.BytesIO(b.getvalue())) as im:
            assert im.format == fmt
    for fmt in ("BMP", "TIFF", "WEBP", "PPM"):
        b = io.BytesIO()
        try:
            Image.new("RGB", (16, 16), "red").save(b, fmt)
        except (KeyError, OSError):
            continue
        try:
            Image.open(io.BytesIO(b.getvalue()))
        except Exception as e:
            assert "cannot identify image file" in str(e), (fmt, e)
        else:
            raise AssertionError(f"{fmt} was opened")

def test_pdfs_fetch_nothing_from_the_network_or_elsewhere_on_disk(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    import safe_media
    import socket
    asked = []

    def no_net(*a, **k):
        asked.append(a)
        raise OSError("no network in this test")
    monkeypatch.setattr(socket, "create_connection", no_net)
    secret = tmp_path / "secret.png"
    from PIL import Image
    Image.new("RGB", (40, 40), "blue").save(secret, "PNG")
    f = safe_media.pdf_fetcher()
    for bad in ("http://example.org/x.png", "https://example.org/x.css", "ftp://example.org/x", f"file://{secret}",
                "file:///etc/passwd"):
        try:
            f.fetch(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert f.fetch("data:text/plain;base64,aGk=") is not None
    font = next((A.ROOT / "static" / "fonts").rglob("*.*"), None)
    if font:
        assert f.fetch(font.as_uri()) is not None
    html = (f'<img src="https://example.org/track.png"><img src="file://{secret}">'
            '<link rel="stylesheet" href="http://example.org/x.css"><p>Receipt</p>')
    with A.app.test_request_context():
        pdf = A.html_pdf(html, "x.pdf").get_data()
    assert pdf.startswith(b"%PDF") and not asked
    assert b"/Subtype /Image" not in pdf and b"/Subtype/Image" not in pdf

def test_a_replaced_link_carries_the_clients_picks_and_draft_into_the_new_gallery(tmp_path, monkeypatch):

    import json
    import shutil
    import sealed
    import prints_env as PE
    from helpers import hosted, seal_picks
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    e = PE.make(tmp_path, monkeypatch, on=False)
    A, c, G = e.A, e.c, e.G
    photos = tmp_path / "photos" / "lane"
    photos.mkdir(parents=True)
    for p in (tmp_path / "src").glob("*.jpg"):
        shutil.copy(p, photos / p.name)
    con = A.connect()
    con.execute("UPDATE galleries SET built_at=?, n_files=3, source_dir=? WHERE session_id=1",
                (__import__("datetime").date.today().isoformat(), str(photos) + "/"))
    con.execute("UPDATE sessions SET type='family', stage='shot' WHERE id=1")
    con.commit()
    con.close()
    old_gid, old_key = _row(A, "SELECT s.gallery_id, g.key FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=1")
    picks = [dict(n="Lane-034.jpg", note="brighter please")]
    draft = [dict(n="Lane-035.jpg")]
    pick_env = seal_picks(old_key, old_gid, picks, "picks", at="2026-11-17T10:00:00.000Z")
    draft_env = seal_picks(old_key, old_gid, draft, "draft", at="2026-11-17T11:00:00.000Z")
    (e.host / old_gid / "picks.las").write_bytes(pick_env)
    (e.host / old_gid / "draft.las").write_bytes(draft_env)
    pick_body = sealed.open_link("picks", old_gid, G.key_bytes(old_key), pick_env)
    draft_body = sealed.open_link("draft", old_gid, G.key_bytes(old_key), draft_env)

    con = A.connect()
    con.execute("UPDATE sessions SET gallery_codeword=? WHERE id=1", (CW,))
    con.commit()
    con.close()
    c.post("/session/1/gallery/replace-link", environ_base=PE.E)
    _wait_thread("gallery-1")
    new_gid, ekey, ph = _row(A, """SELECT s.gallery_id, g.ekey, g.picks_hash FROM sessions s JOIN galleries g
                                   ON g.session_id=s.id WHERE s.id=1""")
    assert new_gid != old_gid and ekey and ekey != old_key
    assert not (e.host / old_gid).exists() and not (G.KEPT_ROOT / old_gid).exists()
    c.post("/session/1/gallery/upload", environ_base=PE.E)
    _wait_thread("gal-up-1")
    assert _row(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    folder = e.host / new_gid
    got_p = hosted(folder, "picks.las", ekey).read_bytes()
    got_d = hosted(folder, "draft.las", ekey).read_bytes()
    assert sealed.open_link("picks", new_gid, G.key_bytes(ekey), got_p) == pick_body
    assert sealed.open_link("draft", new_gid, G.key_bytes(ekey), got_d) == draft_body
    for env in (got_p, got_d):
        for gid_, k_ in ((old_gid, old_key), (new_gid, old_key)):
            try:
                sealed.open_link("picks", gid_, G.key_bytes(k_), env)
            except Exception:
                continue
            raise AssertionError("opened with the old link")
    import hashlib
    assert ph == hashlib.sha256(got_p).hexdigest()

    n_before = len([r for r in _q(A, "SELECT action FROM audit WHERE action LIKE 'Picks received%'")])
    c.post("/gallery/sync", environ_base=PE.E)
    assert len([r for r in _q(A, "SELECT action FROM audit WHERE action LIKE 'Picks received%'")]) == n_before
    assert json.loads(pick_body)["picks"] == picks
