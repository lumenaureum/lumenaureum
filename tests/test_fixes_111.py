import base64
import io
import json
import os
import re
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

def _line(c, sid, amount, label="Session"):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": label, "amount": amount}, environ_base=E)

def _pay(c, sid, amount, kind="deposit", **more):
    return c.post(f"/session/{sid}/money", data={"what": "payment", "kind": kind, "amount": amount, "method": "cash", **more},
                  environ_base=E)

def _page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def test_an_issued_invoice_keeps_its_words_when_the_app_changes(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "100")
    r = c.post(f"/session/{sid}/invoice/issue", environ_base=E, follow_redirects=True).get_data(as_text=True)
    num = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,))[0][0]
    assert f'<span class="note" role="status">Invoice {num} issued.</span>' in r
    html = _q(A, "SELECT html FROM bill_snapshots WHERE number=?", (num,))[0][0]
    assert html and num in html and "Questions about this invoice?" in html

    tpl = os.path.join(ROOT, "templates", "_bill_doc.html")
    real = open(tpl).read()
    try:
        open(tpl, "w").write(real.replace("Questions about this", "Any questions on this"))
        A.app.jinja_env.cache.clear() if A.app.jinja_env.cache is not None else None
        page, pdf = _page(c, f"/session/{sid}/invoice"), _page(c, f"/session/{sid}/invoice.pdf")
        assert "Questions about this invoice?" in page and "Any questions" not in page
        assert "Questions about this invoice?" in pdf and "Any questions" not in pdf

        sid2 = _new(A, c, "family", subject_name="Bo")
        _line(c, sid2, "200")
        assert "Any questions on this invoice?" in _page(c, f"/session/{sid2}/invoice")
    finally:
        open(tpl, "w").write(real)
        A.app.jinja_env.cache.clear() if A.app.jinja_env.cache is not None else None

    _pay(c, sid, "200", kind="balance")
    assert "It has since been paid in full." in _page(c, f"/session/{sid}/invoice")

def test_documents_issued_before_1_1_1_are_frozen_at_unlock(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    con = A.connect()
    con.execute("UPDATE bill_snapshots SET html=NULL")
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        A._freeze_bill_pages()
        A.g.db.commit()
    html = _q(A, "SELECT html FROM bill_snapshots")[0][0]
    assert html and "Questions about this invoice?" in html

def test_a_paid_invoice_is_not_shown_as_sent(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    _pay(c, sid, "300", kind="balance")
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    page = _page(c, f"/session/{sid}/invoice")
    assert 'class="sent">Sent' not in page
    assert [tuple(r) for r in _q(A, "SELECT outcome FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))] in ([("na",)], [])

def test_setup_deposit_applies_to_weddings_until_the_type_changes_it(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings", "families"))
    import config as C
    c.post("/setup/money", data=dict(deposit="150", event_pct="50", reschedule_fee="25", delivery_days="10",
                                     event_delivery_days="20", net_days="15", pay_cash="1"), environ_base=E)
    assert C.DEPOSIT_RULE["wedding"] == ("pct", 50)
    assert "Deposit for weddings and events" in _page(c, "/setup/money")
    with A.app.test_request_context():
        A.db()
        assert A._deposit_for("wedding", 3200, False) == 1600.0

    page = _page(c, "/settings/type/wedding")
    assert re.search(r'name="deposit_value"[^>]*value=""', page)
    form = {k: v for k, v in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page)}
    form.update(deposit_value="40", deposit_mode="pct")
    c.post("/settings/type/wedding", data=form, environ_base=E)
    assert C.DEPOSIT_RULE["wedding"] == ("pct", 40)
    t = next(t for t in C.TYPES if t["key"] == "wedding")
    assert t.get("deposit_own") is True
    c.post("/setup/money", data=dict(deposit="150", event_pct="60", reschedule_fee="25", delivery_days="10",
                                     event_delivery_days="20", net_days="15", pay_cash="1"), environ_base=E)
    assert C.DEPOSIT_RULE["wedding"] == ("pct", 40)

def test_an_existing_install_keeps_a_changed_wedding_deposit(tmp_path, monkeypatch):
    import config as C
    shipped = dict(C.shipped_type("wedding"))
    assert C.deposit_follows_setup(shipped)
    assert not C.deposit_follows_setup(dict(shipped, deposit_pct=35))
    assert not C.deposit_follows_setup(dict(shipped, deposit_own=True))

def test_two_signers_are_marked_one_at_a_time(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    con = A.connect()
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Sam Lane', 'adult')")
    con.commit(); con.close()
    sid = _new(A, c, "wedding")
    c.post(f"/session/{sid}/signers", data={"signer": ["1", "2"]}, environ_base=E)
    rows = _q(A, "SELECT id, status FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,))
    assert len(rows) == 2
    page = _page(c, f"/session/{sid}/agreement")
    assert "Mark signed: Avery Lane" in page and "Mark signed: Sam Lane" in page
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement", "did": str(rows[0][0])}, environ_base=E)
    assert [r[1] for r in _q(A, "SELECT id, status FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,))] \
        == ["signed", "needed"]
    page = _page(c, f"/session/{sid}/agreement")
    assert "1 of 2 signed" in page and "Mark signed: Sam Lane" in page and "Mark signed: Avery Lane" not in page
    assert any("signed by Avery Lane" in r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=?", (sid,)))

def test_the_update_note_shows_once(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_version', '1.0.1')")
    con.commit(); con.close()
    A._UPGRADE_BACKED.append(True)
    try:
        with A.app.test_request_context():
            A.db()
            A._note_update()
            A.g.db.commit()
    finally:
        A._UPGRADE_BACKED.clear()
    import about
    first = _page(c, "/")
    assert f"Updated to Lumen Aureum {about.version()}." in first and "from before the update" in first
    assert "Updated to Lumen Aureum" not in _page(c, "/")

def test_newer_data_is_refused_on_the_sign_in_page(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    A._write_schema_mark(A.SCHEMA_VERSION + 1)
    r = c.get("/login", environ_base=E)
    assert r.status_code == 409 and "needs a newer version" in r.get_data(as_text=True)
    A._write_schema_mark(A.SCHEMA_VERSION)
    assert c.get("/login", environ_base=E).status_code in (200, 302)

def test_the_upgrade_backup_uses_the_saved_time_zone(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'America/New_York')")
    con.commit()
    monkeypatch.setenv("TZ", "UTC")
    A._tz_from_settings(con)
    con.close()
    assert os.environ["TZ"] == "America/New_York"

def _png(w, h, mode="RGBA"):
    from PIL import Image
    b = io.BytesIO()
    Image.new(mode, (w, h)).save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()

def test_an_imported_logo_is_checked_and_re_encoded():
    import settings_io as S
    head = {"lumen_aureum_settings": S.FORMAT}
    with pytest.raises(S.ImportProblem):
        S.parse(json.dumps(dict(head, settings={"logo_png": _png(9000, 9000, "L")})), {})
    poly = base64.b64encode(b"\x89PNG\r\n\x1a\n<html><script>1</script>").decode()
    with pytest.raises(S.ImportProblem):
        S.parse(json.dumps(dict(head, settings={"logo_dark_png": poly})), {})
    s, *_ = S.parse(json.dumps(dict(head, settings={"logo_png": _png(1200, 480)})), {})
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(s["logo_png"])))
    assert im.format == "PNG" and im.size == (600, 240)

GID = "galFix111"

@pytest.fixture
def gal(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    A, c = _app(tmp_path, monkeypatch)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for i, col in enumerate(("red", "blue", "green")):
        Image.new("RGB", (32, 24), col).save(src / f"p{i}.jpg", "JPEG")
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    from plugins import gallery as G
    key = G.new_key()
    G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True))
    sid = _new(A, c, "family", subject_name="Ava")
    con = A.connect()
    con.execute("UPDATE sessions SET stage='delivered', gallery_id=?, first_visit=? WHERE id=?", (GID, date.today().isoformat(), sid))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files) VALUES (?, ?, ?, 3)", (sid, key, date.today().isoformat()))
    con.commit(); con.close()
    G.upload_gallery(GID, A.C.GALLERY_DAYS, "2099-01-01")
    G.mark_online(GID)
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', uploaded_at=? WHERE session_id=?", (date.today().isoformat(), sid))
    con.commit(); con.close()
    return A, c, G, host, key, sid, src

def _sealed(G, key, picks, aad, at):
    import secrets as _s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = _s.token_bytes(12)
    return b"LAK1" + iv + AESGCM(G.key_bytes(key)).encrypt(iv, json.dumps({"v": 1, "at": at, "picks": picks}).encode(), aad)

@pytest.mark.parametrize("at", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"])
def test_a_bad_draft_date_never_stops_expiry(gal, at):
    A, c, G, host, key, sid, src = gal
    (host / GID / "draft.bin").write_bytes(_sealed(G, key, [{"i": 0, "name": "p0.jpg", "r": True}], G.DRAFT_AAD, at))
    old = (date.today() - timedelta(days=A.C.GALLERY_DAYS + 2)).isoformat()
    con = A.connect()
    con.execute("UPDATE sessions SET first_visit=? WHERE id=?", (old, sid))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert not (host / GID).exists()
    assert _q(A, "SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "removed"

def test_a_draft_listing_picks_in_another_order_is_not_still_picking(gal):
    A, c, G, host, key, sid, src = gal
    a = {"i": 0, "name": "p0.jpg", "r": True, "note": "soften"}
    b = {"i": 1, "name": "p1.jpg", "m": True}
    (host / GID / "picks.bin").write_bytes(_sealed(G, key, [a, b], G.PICKS_AAD, "2026-11-01T10:00:00Z"))
    (host / GID / "draft.bin").write_bytes(_sealed(G, key, [b, a], G.DRAFT_AAD, "2026-11-01T10:00:01Z"))
    c.post("/gallery/sync", environ_base=E)
    assert _q(A, "SELECT draft_n FROM galleries WHERE session_id=?", (sid,))[0][0] is None

def _rebuild(A, G, key, src, sid):
    G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True))

def test_an_update_keeps_the_old_files_until_a_later_check(gal, monkeypatch):
    A, c, G, host, key, sid, src = gal
    old = {p.name for p in (host / GID).iterdir() if re.fullmatch(r"[0-9a-f]{24}\.bin", p.name)}
    _rebuild(A, G, key, src, sid)
    c.post(f"/session/{sid}/gallery/upload", environ_base=E)
    import time
    for _ in range(100):
        if G.online_is_current(GID) and sid not in A.app.view_functions["upload_gallery_req"].__globals__["UPDATING"]:
            break
        time.sleep(0.05)
    have = {p.name for p in (host / GID).iterdir()}
    assert old <= have
    due = _q(A, "SELECT prune_due FROM galleries WHERE session_id=?", (sid,))[0][0]
    assert due
    c.post("/gallery/sync", environ_base=E)
    assert old <= {p.name for p in (host / GID).iterdir()}
    con = A.connect()
    con.execute("UPDATE galleries SET prune_due='2000-01-01T00:00:00' WHERE session_id=?", (sid,))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert not (old & {p.name for p in (host / GID).iterdir()})
    assert (host / GID / "manifest.bin").exists()

def test_one_bad_file_does_not_stop_the_cleanup(gal, tmp_path):
    A, c, G, host, key, sid, src = gal
    old = sorted(p.name for p in (host / GID).iterdir() if re.fullmatch(r"[0-9a-f]{24}\.bin", p.name))
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    planted = "0123456789abcdef01234567.bin"
    os.symlink(victim, host / GID / planted)
    _rebuild(A, G, key, src, sid)
    G.upload_gallery(GID)
    gone, skipped = G.prune_host(GID)
    assert gone == len(old) and skipped == [planted]
    assert victim.read_text() == "keep me"
    assert not any((host / GID / n).exists() for n in old)

def test_a_second_upload_press_does_nothing_while_one_runs(gal, monkeypatch):
    A, c, G, host, key, sid, src = gal
    _rebuild(A, G, key, src, sid)
    started = []
    import threading
    real = threading.Thread

    class Counting(real):
        def __init__(self, *a, **kw):
            if (kw.get("name") or "").startswith("gal-up-"):
                started.append(kw["name"])
            super().__init__(*a, **kw)
    monkeypatch.setattr(threading, "Thread", Counting)
    UPDATING = A.app.view_functions["upload_gallery_req"].__globals__["UPDATING"]
    UPDATING[sid] = dict(done=3, total=9)
    try:
        c.post(f"/session/{sid}/gallery/upload", environ_base=E)
        assert not started
        page = _page(c, f"/c/1?s={sid}")
        assert "Updating the copy online" in page and "Upload the new build" not in page
        assert c.get(f"/session/{sid}/gallery/status", environ_base=E).get_json()["state"] == "running"
    finally:
        UPDATING.pop(sid, None)

def test_a_proof_gallery_is_not_held_for_the_balance(gal, monkeypatch):
    A, c, G, host, key, sid, src = gal
    _line(c, sid, "500")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='', uploaded_at=NULL, proof=0 WHERE session_id=?", (sid,))
    con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        assert A.release_blocked(sid) > 0
    assert "still due. Your terms release the gallery on payment." in _page(c, f"/c/1?s={sid}")
    con = A.connect()
    con.execute("UPDATE galleries SET proof=1 WHERE session_id=?", (sid,))
    con.commit(); con.close()
    page = _page(c, f"/c/1?s={sid}")
    assert "release the gallery on payment" not in page and "can go online before payment" in page

def test_the_letter_for_proofs_says_so(gal, monkeypatch):
    A, c, G, host, key, sid, src = gal
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    con = A.connect()
    con.execute("UPDATE galleries SET proof=1 WHERE session_id=?", (sid,))
    con.commit(); con.close()
    letter = _page(c, f"/letters/{sid}/qr-letter.pdf")
    assert "Your proofs are ready to pick from." in letter and "Your photos are finished" not in letter
    con = A.connect()
    con.execute("UPDATE galleries SET proof=0 WHERE session_id=?", (sid,))
    con.commit(); con.close()
    assert "Your photos are finished" in _page(c, f"/letters/{sid}/qr-letter.pdf")

def test_sftp_uses_one_login_for_a_whole_upload(monkeypatch):
    from vault import backends as B
    opened = []

    class FakeSFTP:
        def __init__(self):
            self.files = {}

        def stat(self, p):
            if p not in self.files:
                raise FileNotFoundError(p)

            class S:
                st_size = len(self.files[p])
            return S()

        def mkdir(self, p):
            self.files[p] = b""

        def open(self, p, mode):
            fs, buf = self.files, io.BytesIO()

            class F:
                def __enter__(s): return s
                def __exit__(s, *a): fs[p] = buf.getvalue()
                def set_pipelined(s, v): pass
                def write(s, b): buf.write(b)
            return F()

        def posix_rename(self, a, b):
            self.files[b] = self.files.pop(a)

        def remove(self, p):
            self.files.pop(p, None)

        def close(self):
            pass

    class FakeCli:
        def get_transport(self):
            class T:
                def is_active(s): return True
            return T()

        def close(self):
            pass
    sf = FakeSFTP()
    be = B.SFTPBackend("h", "u", "", "/r")
    monkeypatch.setattr(be, "_open", lambda: (opened.append(1), (FakeCli(), sf))[1])
    with be.session():
        for i in range(20):
            be.put_stream(f"g/{i:02d}.bin", io.BytesIO(b"x" * 10))
        be.delete("g/00.bin")
    assert len(opened) == 1
    be.put_stream("g/solo.bin", io.BytesIO(b"y"))
    assert len(opened) == 2

def test_history_has_one_line_per_upload_batch(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    with A.app.test_request_context():
        A.db()
        for i in range(3):
            A._audit_upload(sid, dict(kind="export", name=f"Sample-{i:03d}.jpg", size=1048576))
        A.g.db.commit()
    rows = [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? AND route LIKE 'photos-batch:%'", (sid,))]
    assert rows == ["Uploaded 3 edited exports (3.0 MB, sealed)"]

def _deposit_due(A, sid):
    with A.app.test_request_context():
        A.db()
        w = A.workflow(sid)
    booked = next(g_ for g_ in w["stages"] if g_["stage"] == "booked")
    return next(st_["due"] for st_ in booked["steps"] if st_.get("key") == "deposit_paid")

def _agreement(A, sid, signed):
    con = A.connect()
    con.execute("UPDATE documents SET status='signed', signed=? WHERE session_id=? AND kind='agreement'", (signed, sid))
    con.commit(); con.close()

def test_the_deposit_is_due_on_the_signing_day(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    t0 = date.today()
    sid = _new(A, c, "family", subject_name="Ava", shoot_date=(t0 + timedelta(days=3)).isoformat())
    _line(c, sid, "300")
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "booked"
    did = _q(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0][0]

    assert _deposit_due(A, sid) == (t0 + timedelta(days=3)).isoformat()

    c.post(f"/doc/{did}", data={"status": "signed", "signed_on": t0.isoformat()}, environ_base=E)
    assert _q(A, "SELECT status FROM documents WHERE id=?", (did,))[0][0] == "signed"
    assert _deposit_due(A, sid) == t0.isoformat()

    con = A.connect()
    con.execute("UPDATE stage_log SET at=? WHERE session_id=? AND stage='booked'", ((t0 - timedelta(days=4)).isoformat(), sid))
    con.execute("UPDATE sessions SET priced_on=? WHERE id=?", ((t0 - timedelta(days=10)).isoformat(), sid))
    con.commit(); con.close()
    _agreement(A, sid, (t0 - timedelta(days=2)).isoformat())
    assert _deposit_due(A, sid) == (t0 - timedelta(days=2)).isoformat()

    _agreement(A, sid, (t0 - timedelta(days=9)).isoformat())
    assert _deposit_due(A, sid) == (t0 - timedelta(days=4)).isoformat()

def test_signed_before_booking_the_deposit_is_due_the_day_it_is_booked(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    t0 = date.today()
    sid = _new(A, c, "family", subject_name="Ava", shoot_date=(t0 + timedelta(days=30)).isoformat())
    _line(c, sid, "300")
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    if not _q(A, "SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (sid,)):
        con = A.connect()
        con.execute("INSERT INTO documents (session_id, kind, status) VALUES (?, 'agreement', 'needed')", (sid,))
        con.commit(); con.close()
    _agreement(A, sid, (t0 - timedelta(days=3)).isoformat())
    con = A.connect()
    con.execute("UPDATE sessions SET stage='quoted', stage_changed=? WHERE id=?", ((t0 - timedelta(days=5)).isoformat(), sid))
    con.commit(); con.close()
    assert _deposit_due(A, sid) == t0.isoformat()

def test_the_deposit_is_never_due_before_there_is_a_price(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    t0 = date.today()
    sid = _new(A, c, "family", subject_name="Ava", shoot_date=(t0 + timedelta(days=30)).isoformat())
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "booked"
    con = A.connect()
    con.execute("UPDATE stage_log SET at=? WHERE session_id=? AND stage='booked'", ((t0 - timedelta(days=6)).isoformat(), sid))
    con.commit(); con.close()
    _agreement(A, sid, (t0 - timedelta(days=5)).isoformat())
    with pytest.raises(StopIteration):
        _deposit_due(A, sid)
    assert _q(A, "SELECT priced_on FROM sessions WHERE id=?", (sid,))[0][0] is None
    _line(c, sid, "300")
    assert _deposit_due(A, sid) == t0.isoformat()
    assert _q(A, "SELECT priced_on FROM sessions WHERE id=?", (sid,))[0][0] == t0.isoformat()
    with A.app.test_request_context():
        A.db()
        due = next(st_ for g_ in A.workflow(sid)["stages"] for st_ in g_["steps"] if st_.get("key") == "deposit_paid")
        assert not due["done"] and due["due"] >= A.today().isoformat()

    con = A.connect()
    con.execute("UPDATE sessions SET priced_on=? WHERE id=?", ((t0 - timedelta(days=2)).isoformat(), sid))
    con.commit(); con.close()
    assert _deposit_due(A, sid) == (t0 - timedelta(days=2)).isoformat()

def test_a_type_with_no_agreement_keeps_the_booking_rule(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    t0 = date.today()
    sid = _new(A, c, "family", subject_name="Ava", shoot_date=(t0 + timedelta(days=30)).isoformat())
    _line(c, sid, "300")
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    _agreement(A, sid, t0.isoformat())
    monkeypatch.setattr(A.C, "NEEDS_AGREEMENT", set())
    assert _deposit_due(A, sid) == (t0 + timedelta(days=A.BOOKING_HOLD_DAYS)).isoformat()

def test_the_qr_letter_says_family_only_for_family_sessions(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families", "headshots", "realestate"))
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    fam = _new(A, c, "family", subject_name="Ava")
    letter = _page(c, f"/letters/{fam}/qr-letter.pdf")
    assert "Thank you for letting me photograph your family" in letter

    con = A.connect()
    con.execute("INSERT INTO households (id, name, company) VALUES (2, 'Acme', 'Acme Corp')")
    con.commit(); con.close()
    head = _new(A, c, "headshot", household_id="2")
    letter = _page(c, f"/letters/{head}/qr-letter.pdf")
    assert "Thank you for letting me photograph you" in letter and "your family" not in letter
    head2 = _new(A, c, "headshot", subject_name="Jordan Price")
    letter = _page(c, f"/letters/{head2}/qr-letter.pdf")
    assert "Thank you for letting me photograph Jordan" in letter and "your family" not in letter

    fam2 = _new(A, c, "family", household_id="2")
    assert "your family" not in _page(c, f"/letters/{fam2}/qr-letter.pdf")

    home = _new(A, c, "realestate")
    letter = _page(c, f"/letters/{home}/qr-letter.pdf")
    assert "Thank you for letting me photograph the property" in letter and "your family" not in letter
