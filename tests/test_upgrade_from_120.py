import html
import hashlib
import hmac
import io
import json
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

from helpers import fresh_modules, signed_in_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from make_fixture import doc_text, doc_text_any

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "db-1.2.0"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json")
E = {"REMOTE_ADDR": "127.0.0.1"}
V120_SCHEMA = 6
DIRS = ("galleries", "host", "photos")
NEW_GALLERY_COLS = ("limits_contact", "dark_at", "restored_at", "restore_days", "restore_once", "host_fp", "link_base")

PICKS_IN_AT_SHOT = {str(EXPECTED["sessions_by_name"]["reyes"])}

def _stage_15(sid, stage):
    return "delivered" if str(sid) in PICKS_IN_AT_SHOT and stage == "shot" else stage

def _place(tmp_path, side_files=SIDE_FILES):

    shutil.copyfile(FIXTURE / "crm.db", tmp_path / "crm.db")
    for name in side_files:
        if (FIXTURE / name).exists():
            shutil.copyfile(FIXTURE / name, tmp_path / name)
    for name in DIRS:
        shutil.copytree(FIXTURE / name, tmp_path / name)
    assert sorted(EXPECTED["storage_keys"]) == ["gallery_host", "primary"]
    (tmp_path / "storage.json").write_text(json.dumps({
        "primary": {"type": "local", "path": str(tmp_path / "vault")},
        "gallery_host": dict(EXPECTED["gallery_host"], path=str(tmp_path / "host"))}))

def _open(tmp_path, monkeypatch, version=None):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    if version:
        import about
        monkeypatch.setattr(about, "version", lambda: version)
    import app as A
    from vault import backup as B
    taken = []
    monkeypatch.setattr(B, "sealed_backup", lambda *a, **kw: taken.append(kw.get("kind")))
    c = signed_in_client(A)
    return A, c, taken

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _in_request(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def _finances(A, c, year):
    seen = {}
    real = A.render_template

    def capture(name, **kw):
        if name == "money.html":
            seen.update(kw)
        return real(name, **kw)
    A.render_template = capture
    try:
        assert c.get(f"/money?year={year}", environ_base=E).status_code == 200
    finally:
        A.render_template = real
    return dict(total=round(seen["total"], 2), refunds=round(seen["refunds"], 2), tax_due=round(seen["tax_due"], 2),
                received={r["type"]: round(r["v"], 2) for r in seen["received"]},
                by_month={r["mo"]: round(r["v"], 2) for r in seen["by_month"]},
                outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["outstanding"] + seen.get("to_invoice", [])},
                upcoming={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["upcoming"]})

def _bill(A, sid, receipt):
    v = _in_request(A, lambda: A.bill_ctx(sid, receipt=receipt))
    return dict(number=v["number"], since=bool(v.get("since")), paid=round(v["paid"], 2), total=round(v["total"], 2),
                balance=round(v["balance"], 2))

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    _place(tmp_path)
    return _open(tmp_path, monkeypatch) + (tmp_path,)

def test_the_fixture_is_a_1_2_0_database():
    assert EXPECTED["schema_version"] == V120_SCHEMA and EXPECTED["made_with"].startswith("1.2.0")
    assert EXPECTED["app_version"] == "1.2.0"
    assert (FIXTURE / "crm.db").read_bytes()[:16] != b"SQLite format 3\x00"

def test_this_sqlcipher_opens_it_with_the_same_settings(upgraded):

    A, c, taken, tmp_path = upgraded
    con = A.connect()
    try:
        assert con.execute("PRAGMA cipher_version").fetchone()[0].startswith("4.")
        assert con.execute("PRAGMA cipher_page_size").fetchone()[0] in (4096, "4096")
        assert str(con.execute("PRAGMA kdf_iter").fetchone()[0]) == "256000"
        assert con.execute("PRAGMA cipher_hmac_algorithm").fetchone()[0] == "HMAC_SHA512"
        assert con.execute("PRAGMA cipher_kdf_algorithm").fetchone()[0] == "PBKDF2_HMAC_SHA512"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA cipher_integrity_check").fetchall() == []
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()
    con = A.connect()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    assert (tmp_path / "crm.db").read_bytes()[:16] != b"SQLite format 3\x00"

def test_the_schema_moves_to_the_current_version_after_a_sealed_backup(upgraded):
    A, c, taken, _ = upgraded
    assert A.SCHEMA_VERSION >= V120_SCHEMA
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert taken == (["upgrade"] if A.SCHEMA_VERSION > V120_SCHEMA else [])
    assert A.SCHEMA_VERSION == 9 and taken == ["upgrade"]
    cols = {t: [r[1] for r in _q(A, f"PRAGMA table_info({t})")] for t in ("bill_snapshots", "sessions", "galleries")}
    assert "html" in cols["bill_snapshots"] and {"priced_on", "delivery_method"} <= set(cols["sessions"])
    assert set(NEW_GALLERY_COLS) | {"proof", "prune_due"} <= set(cols["galleries"])

def test_today_says_once_that_it_was_updated(tmp_path, monkeypatch):
    import about
    now = about.version()
    if now == EXPECTED["app_version"]:
        now = "1.3.0"
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch, version=now)
    assert now != EXPECTED["app_version"] and taken == ["upgrade"]
    assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == now
    first, second = _page(c, "/"), _page(c, "/")
    assert f"Updated to Lumen Aureum {now}." in first
    assert "A backup of your data from before the update is under" in first
    assert "Updated to Lumen Aureum" in second
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    assert "Updated to Lumen Aureum" not in _page(c, "/")

def test_every_record_is_still_there(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, name FROM households")} == EXPECTED["clients"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == {
        k: _stage_15(k, v["stage"]) for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4]) for r in
            _q(A, "SELECT id, session_id, kind, amount, received FROM payments")} == EXPECTED["payments"]
    ev = EXPECTED["event"]
    assert [r[0] for r in _q(A, """SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id
                                   WHERE s.event_id=? ORDER BY h.name""", (ev["id"],))] == ev["families"]
    for sid in EXPECTED["sessions"]:
        assert c.get(f"/session/{sid}/edit", environ_base=E).status_code == 200

def test_balances_and_refund_credits_match_1_2_0(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): dict(session=r[1], amount=r[2]) for r in
            _q(A, "SELECT payment_id, session_id, amount FROM refund_credits")} == EXPECTED["refund_credits"]
    for sid, want in EXPECTED["sessions"].items():
        t = _in_request(A, lambda: A.totals(int(sid)))
        assert round(t["quoted"], 2) == want["quoted"], sid
        assert round(t["paid"], 2) == want["paid"], sid
        assert round(t["due"], 2) == want["due"], sid
        assert round(t.get("refund_credit", 0.0), 2) == want["refund_credit"], sid

def test_finances_match_1_2_0(upgraded):
    A, c, taken, _ = upgraded
    assert _finances(A, c, EXPECTED["year"]) == EXPECTED["finances"]

def test_issued_documents_are_the_ones_1_2_0_kept(upgraded):

    A, c, taken, _ = upgraded
    assert {r[0]: dict(session=r[1], kind=r[2], issued=r[3]) for r in
            _q(A, "SELECT number, session_id, kind, issued FROM doc_numbers")} == EXPECTED["doc_numbers"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT session_id, number FROM invoice_sent")} == EXPECTED["invoice_sent"]
    snaps = {}
    for number, sid, kind, data in _q(A, "SELECT number, session_id, kind, data FROM bill_snapshots"):
        d_ = json.loads(data)
        snaps[number] = dict(session=sid, kind=kind, paid=round(d_["paid"], 2), total=round(d_["total"], 2),
                             balance=round(d_["balance"], 2), payments=len(d_["payments"]))
    assert snaps == EXPECTED["bill_snapshots"]
    assert {r[0]: hashlib.sha256(r[1].encode()).hexdigest() for r in
            _q(A, "SELECT number, html FROM bill_snapshots")} == EXPECTED["snapshot_html"]
    assert set(EXPECTED["issued_text"]) == set(EXPECTED["bill_snapshots"])
    for number, want in EXPECTED["issued_text"].items():
        sid, kind = _q(A, "SELECT session_id, kind FROM bill_snapshots WHERE number=?", (number,))[0]
        page = _page(c, f"/session/{sid}/{'receipt' if kind == 'R' else 'invoice'}")
        assert doc_text(page) == want, number
        html = _q(A, "SELECT html FROM bill_snapshots WHERE number=?", (number,))[0][0]
        assert html in page
    for sid, kinds in EXPECTED["bills"].items():
        for kind, want in kinds.items():
            assert _bill(A, int(sid), kind == "receipt") == dict(want, since=bool(want["since"])), (sid, kind)

def test_drafts_stay_unnumbered_and_the_next_numbers_follow(upgraded):
    A, c, taken, _ = upgraded
    ortiz, bell = EXPECTED["sessions_by_name"]["ortiz"], EXPECTED["sessions_by_name"]["bell"]
    assert EXPECTED["bills"][str(ortiz)]["invoice"]["number"] == "" and EXPECTED["bills"][str(bell)]["receipt"]["number"] == ""
    assert c.get(f"/session/{ortiz}/invoice", environ_base=E).status_code == 200
    assert c.get(f"/session/{bell}/receipt", environ_base=E).status_code == 200
    assert not _q(A, "SELECT 1 FROM doc_numbers WHERE session_id IN (?, ?)", (ortiz, bell))
    for sid, kind, pre in ((ortiz, "invoice", "INV"), (bell, "receipt", "R")):
        assert c.post(f"/session/{sid}/{kind}/issue", environ_base=E).status_code in (302, 303)
        new = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, pre))[0][0]
        same_year = [int(n[-4:]) for n, d in EXPECTED["doc_numbers"].items() if d["kind"] == pre and n[:-4] == new[:-4]]
        assert new not in EXPECTED["doc_numbers"] and int(new[-4:]) == max(same_year, default=0) + 1
        assert _q(A, "SELECT html FROM bill_snapshots WHERE number=?", (new,))[0][0]

def test_the_signed_deletion_still_verifies(upgraded):
    A, c, taken, tmp_path = upgraded
    gone = EXPECTED["deleted"]
    assert not _q(A, "SELECT 1 FROM households WHERE id=?", (gone["client"],))
    assert [r[0] for r in _q(A, "SELECT name FROM side_keys ORDER BY name")] == EXPECTED["side_keys"]
    items = json.loads((tmp_path / "deletions.json").read_text())
    assert items == EXPECTED["deletions"]
    key = _in_request(A, lambda: A._deletion_key(A.db()))
    for d_ in items:
        assert hmac.compare_digest(d_["mac"], A._deletion_mac(key, d_))
    assert _in_request(A, A.unconfirmed_deletions) == []

def test_ids_and_quote_numbers_are_not_reused(upgraded):
    A, c, taken, _ = upgraded
    gone = EXPECTED["deleted"]
    assert {r[0]: r[1] for r in _q(A, "SELECT name, n FROM id_marks")} == EXPECTED["id_marks"]
    hid = _in_request(A, lambda: A.next_id("households"))
    sid = _in_request(A, lambda: A.next_id("sessions"))
    assert hid == EXPECTED["id_marks"]["households"] + 1 and hid > gone["client"]
    assert sid == EXPECTED["id_marks"]["sessions"] + 1 and sid > max(gone["session"], gone["quoted_session"])
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, session_id FROM quote_versions")} == EXPECTED["quote_versions"]
    other = EXPECTED["sessions_by_name"]["ortiz_inquiry"]
    c.post(f"/session/{other}/money", data=dict(what="line", label="Holiday session", amount="120"), environ_base=E)
    assert c.post(f"/session/{other}/quote/send", environ_base=E).status_code in (302, 303)
    vid = _q(A, "SELECT MAX(id) FROM quote_versions WHERE session_id=?", (other,))[0][0]
    assert vid == EXPECTED["number_marks"]["Q"] + 1 and vid > gone["quote"]

def test_the_galleries_their_picks_and_proof_marks_survive(upgraded):
    A, c, taken, _ = upgraded
    from plugins import gallery as G
    want = EXPECTED["gallery"]
    sid = want["session"]
    cols = ("key", "ekey", "n_files", "built_at", "cw_fixed", "upload_state", "uploaded_at", "removed_at",
            "draft_n", "draft_at", "picks_hash", "limits_days")
    row = _q(A, f"SELECT {', '.join(cols)} FROM galleries WHERE session_id=?", (sid,))[0]
    assert dict(zip(cols, row)) == {k: want[k] for k in cols}
    gid, built, word, first, retouch, fav, veto = _q(
        A, "SELECT gallery_id, gallery_built, gallery_codeword, first_visit, retouch_picks, favorites, veto FROM sessions"
           " WHERE id=?", (sid,))[0]
    assert (gid, built, word) == (want["gallery_id"], want["session_built"], want["codeword"])
    assert (first, retouch, fav, veto) == (want["first_visit"], want["retouch_picks"], want["favorites"], want["veto"])
    assert want["ekey"] == G.codeword_key(want["key"], gid, word)
    link = _in_request(A, lambda: A.app.config["gallery_link"](sid))
    assert link and link.endswith("&c=1") == want["link_asks_for_word"] and f"{gid}/#k={want['key']}" in link
    picking = _in_request(A, lambda: [a_["what"] for fn in A.hooks.session_actions
                                      for a_ in fn(A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)) or []])
    assert picking == want["still_picking"]

    got = {str(r[0]): dict(proof=r[1], prune_due=r[2], upload_state=r[3], n_files=r[4], built_at=r[5]) for r in
           _q(A, "SELECT session_id, proof, prune_due, upload_state, n_files, built_at FROM galleries")}
    assert got == EXPECTED["galleries"]
    assert EXPECTED["galleries"][str(sid)]["prune_due"] is None
    assert {k for k, v in got.items() if v["proof"]} == {str(sid), str(EXPECTED["sessions_by_name"]["kim"])}

def test_a_proof_gallery_stays_online_before_the_balance(upgraded):
    A, c, taken, _ = upgraded
    kim = EXPECTED["sessions_by_name"]["kim"]
    assert round(_in_request(A, lambda: A.release_blocked(kim)), 2) == EXPECTED["proof_owed"][str(kim)] > 0
    assert _in_request(A, lambda: A.app.config["gallery_proofs"](kim)) is True
    assert _in_request(A, lambda: A.app.config["gallery_link"](kim))

def test_priced_days_and_deposit_due_dates_are_unchanged(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, priced_on FROM sessions")} == EXPECTED["priced_on"]
    assert {str(r[0]): [r[1], r[2]] for r in _q(
        A, "SELECT session_id, status, signed FROM documents WHERE kind='agreement' ORDER BY session_id, id")} \
        == EXPECTED["agreements"]
    assert {str(r[0]): r[1] for r in _q(
        A, "SELECT session_id, MIN(at) FROM stage_log WHERE stage='booked' GROUP BY session_id")} == EXPECTED["booked_on"]

    def due(sid):
        s = A.session_full(sid)
        return A.deposit_due(sid, s, A.priced_on(sid, s, A.totals(sid)["quoted"]))
    assert {sid: _in_request(A, lambda: due(int(sid))) for sid in EXPECTED["deposit_due"]} == EXPECTED["deposit_due"]
    kim = str(EXPECTED["sessions_by_name"]["kim"])
    assert EXPECTED["agreements"][kim][0] == "signed" and kim in EXPECTED["deposit_due"]

def test_the_gallery_look_logos_and_time_zone_are_kept(upgraded):
    A, c, taken, _ = upgraded
    keys = tuple(EXPECTED["settings"])
    assert {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(keys)),
                                    keys)} == EXPECTED["settings"]
    for k, digest in EXPECTED["logos"].items():
        v = _q(A, "SELECT value FROM settings WHERE key=?", (k,))
        assert v and hashlib.sha256(v[0][0].encode()).hexdigest() == digest, k
    page = _page(c, "/settings?tab=gallery")
    assert 'value="elegant"' in page and EXPECTED["settings"]["proof_mark_text"] in page
    assert getattr(A.TZ, "key", None) == EXPECTED["settings"]["timezone"]

def test_who_owns_the_photos_is_unchanged(upgraded):
    A, c, taken, _ = upgraded
    assert sorted(A.C.LICENSE_TYPES) == EXPECTED["license_types"]
    got = {sid: bool(_in_request(A, lambda: A.licensed(int(sid)))) for sid in EXPECTED["licensed"]}
    assert got == EXPECTED["licensed"]
    assert set(got.values()) == {True, False}

def test_booked_sessions_say_the_same_thing_is_open(upgraded):
    A, c, taken, _ = upgraded
    got = {sid: _in_request(A, lambda: A.booked_hint(int(sid))) for sid in EXPECTED["booked_hint"]}
    assert got == EXPECTED["booked_hint"]
    assert len({v for v in got.values() if v}) >= 2

def _opened(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def _manifest(A, G, tmp_path, sid, where="host"):
    gid = _q(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    key, ekey = _q(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))[0]
    blob = (tmp_path / where / gid / "manifest.bin").read_bytes()
    return gid, ekey or key, blob, json.loads(_opened(G, ekey or key, blob, "manifest.bin"))

def test_delivery_methods_and_the_history_that_chose_them(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, delivery_method FROM sessions")} == EXPECTED["delivery_method"]
    got = {}
    for sid, action in _q(A, "SELECT session_id, action FROM audit WHERE action LIKE 'Delivery changed%' ORDER BY id"):
        got[str(sid)] = got[str(sid)] + " | " + action if str(sid) in got else action
    assert got == EXPECTED["delivery_history"]
    reyes, diaz = EXPECTED["sessions_by_name"]["reyes"], EXPECTED["sessions_by_name"]["diaz"]
    assert set(got) == {str(reyes), str(diaz)} and all(v.endswith("to Download link") for v in got.values())
    for sid in (reyes, diaz):
        s_ = _q(A, "SELECT type, delivery_method FROM sessions WHERE id=?", (sid,))[0]
        assert A.C.delivery_method(*s_) == "link"

    assert {sid: _in_request(A, lambda: A.signed_delivery_differs(int(sid))) for sid in EXPECTED["signed_delivery"]} \
        == EXPECTED["signed_delivery"]
    assert EXPECTED["link_clause"] in doc_text_any(_page(c, f"/session/{reyes}/agreement"))

def test_download_galleries_their_files_and_zip_pieces_survive(upgraded):
    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    assert EXPECTED["download_galleries"]
    for sid, want in EXPECTED["download_galleries"].items():
        gid, key, blob, m = _manifest(A, G, tmp_path, int(sid))
        assert gid == want["gallery_id"] and G.GID_OK.match(gid)
        assert G.is_download(gid) is want["is_download"] is True
        assert G.online_download(gid) is want["online_download"] is True
        assert hashlib.sha256(blob).hexdigest() == want["manifest_sha256"]
        assert blob == (tmp_path / "galleries" / gid / "manifest.bin").read_bytes()
        assert m["dl"] == want["dl"] == 1 and m["title"] == want["title"]
        assert (m.get("brand") or {}).get("site") == want["site"]
        assert [dict(n=it["n"], on=it["on"], o=it["o"], os=it["os"]) for it in m["items"]] == want["items"]
        assert [dict(name=z["name"], size=z["size"], parts=z["parts"]) for z in m["zips"]] == want["zips"]

        assert sorted(p.name for p in (tmp_path / "galleries" / gid).iterdir()) == sorted(want["built_files"])
        assert sorted(p.name for p in (tmp_path / "host" / gid).iterdir()) == want["host_files"]
        fulls = {}
        for it in m["items"]:
            full = _opened(G, key, (tmp_path / "host" / gid / it["o"]).read_bytes(), it["o"])
            assert len(full) == it["os"] and full[:2] == b"\xff\xd8"
            fulls[it["on"]] = full
        for z in m["zips"]:
            assert len(z["parts"]) > 1
            whole = b"".join(_opened(G, key, (tmp_path / "host" / gid / p).read_bytes(), p) for p in z["parts"])
            assert len(whole) == z["size"]
            zf = zipfile.ZipFile(io.BytesIO(whole))
            assert zf.testzip() is None and sorted(zf.namelist()) == sorted(fulls)
            assert all(zf.read(n) == fulls[n] for n in zf.namelist())
        page = _page(c, f"/c/{_q(A, 'SELECT household_id FROM sessions WHERE id=?', (int(sid),))[0][0]}?s={sid}")
        assert f"{len(m['items'])} photos to download · made" in html.unescape(page) or f"can download {len(m['items'])} photos · made" in html.unescape(page) and f"{gid}/#k=" in page

def test_finals_picks_and_deliver_without_picks_are_kept(upgraded):
    A, c, taken, _ = upgraded
    reyes, diaz = EXPECTED["sessions_by_name"]["reyes"], EXPECTED["sessions_by_name"]["diaz"]
    from plugins import gallery as G
    finals = {}
    for sid, name in _q(A, "SELECT session_id, name FROM photo_files WHERE kind='final' AND state='done' ORDER BY session_id, name"):
        finals[str(sid)] = finals[str(sid)] + "," + name if str(sid) in finals else name
    assert finals == EXPECTED["finals"] and set(finals) == {str(reyes)}
    built_from = [it["n"] for it in EXPECTED["download_galleries"][str(reyes)]["items"]]
    assert built_from == EXPECTED["finals"][str(reyes)].split(",")
    assert {str(r[0]): dict(retouch_picks=r[1], favorites=r[2], veto=r[3]) for r in _q(
        A, "SELECT id, retouch_picks, favorites, veto FROM sessions WHERE id IN (?, ?)", (reyes, diaz))} == EXPECTED["picks"]
    assert [r[0] for r in _q(A, "SELECT DISTINCT session_id FROM audit WHERE action=? ORDER BY session_id",
                             (G.NO_PICKS_ACTION,))] == EXPECTED["no_picks"] == [diaz]
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (diaz,))[0][0]
    assert ">Deliver without picks</button>" not in _page(c, f"/c/{hid}?s={diaz}")

def test_files_delivered_by_the_first_open_and_its_days(upgraded):
    A, c, taken, _ = upgraded

    def steps(sid):
        return {st_["label"]: bool(st_["done"]) for stage in A.workflow(sid)["stages"] for st_ in stage["steps"]}
    for sid, want in EXPECTED["delivered"].items():
        sd, fv, stage = _q(A, "SELECT sd_shipped, first_visit, stage FROM sessions WHERE id=?", (int(sid),))[0]
        assert (sd, fv, stage) == (want["sd_shipped"], want["first_visit"], _stage_15(sid, want["stage"]))
        assert _in_request(A, lambda: steps(int(sid)))["Files delivered"] is want["files_delivered"]
        exp = _in_request(A, lambda: A.gallery_expires(A.q("SELECT * FROM sessions WHERE id=?", (int(sid),), one=True)))
        assert (exp.isoformat() if exp else None) == want["expires"]
    reyes = EXPECTED["delivered"][str(EXPECTED["sessions_by_name"]["reyes"])]
    assert reyes["files_delivered"] and reyes["expires"] and reyes["sd_shipped"] != reyes["first_visit"]
    assert _q(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE '%download gallery: files delivered%'",
              (EXPECTED["sessions_by_name"]["reyes"],))

def test_the_website_and_the_gallery_titles(upgraded):
    A, c, taken, _ = upgraded
    assert _q(A, "SELECT value FROM settings WHERE key='business_website'")[0][0] == EXPECTED["website"]
    assert f'value="{EXPECTED["website"]}"' in _page(c, "/settings")
    got = {}
    for (sid,) in _q(A, "SELECT session_id FROM galleries"):
        b_ = _in_request(A, lambda: A.app.config["gallery_brand"](A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)))
        got[str(sid)] = dict(client=b_["client"], site=b_["site"])
    assert got == EXPECTED["gallery_brand"]
    reyes, diaz = EXPECTED["sessions_by_name"]["reyes"], EXPECTED["sessions_by_name"]["diaz"]
    assert got[str(reyes)]["client"] == "Reyes autumn portraits" and got[str(diaz)]["client"] == "Diaz"

def test_the_new_gallery_columns_start_sane(upgraded):

    A, c, taken, _ = upgraded
    from plugins import gallery as G
    fp = G.host_fingerprint()
    assert fp
    rows = _q(A, f"SELECT session_id, upload_state, {', '.join(NEW_GALLERY_COLS)} FROM galleries")
    assert len(rows) == len(EXPECTED["galleries"])
    for r in rows:
        v = dict(zip(("sid", "state") + NEW_GALLERY_COLS, r))
        assert v["state"] == "up"
        assert (v["limits_contact"], v["dark_at"], v["restored_at"], v["restore_days"], v["restore_once"]) == (None,) * 5
        assert v["host_fp"] == fp and v["link_base"] == EXPECTED["gallery_base_url"]
    for sid in EXPECTED["galleries"]:
        link = _in_request(A, lambda: A.app.config["gallery_link"](int(sid)))
        assert link.startswith(EXPECTED["gallery_base_url"])
        hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (int(sid),))[0][0]
        page = _page(c, f"/c/{hid}?s={sid}")
        assert "Move galleries" not in page and "Put back online" not in page

def test_a_gallery_check_keeps_the_1_2_galleries_online(upgraded, monkeypatch):

    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    monkeypatch.setitem(A.app.config, "clock_ok", lambda: False)
    before = {sid: _manifest(A, G, tmp_path, int(sid))[2] for sid in EXPECTED["download_galleries"]}
    lane = EXPECTED["sessions_by_name"]["lane"]
    picks_before = _q(A, "SELECT retouch_picks, favorites, veto FROM sessions WHERE id IN (?, ?) ORDER BY id",
                      (lane, EXPECTED["sessions_by_name"]["reyes"]))
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    for sid, state, removed, dark in _q(A, "SELECT session_id, upload_state, removed_at, dark_at FROM galleries"):
        assert (state, removed, dark) == ("up", None, None), sid
    for sid, want in EXPECTED["download_galleries"].items():
        gid, key, blob, m = _manifest(A, G, tmp_path, int(sid))
        assert blob == before[sid] and G.online_download(gid) and G.is_download(gid)
        assert all((tmp_path / "host" / gid / p).is_file() for z in m["zips"] for p in z["parts"])
        assert all((tmp_path / "host" / gid / it["o"]).is_file() for it in m["items"])
    assert _q(A, "SELECT retouch_picks, favorites, veto FROM sessions WHERE id IN (?, ?) ORDER BY id",
              (lane, EXPECTED["sessions_by_name"]["reyes"])) == picks_before
    assert {r[0] for r in _q(A, "SELECT limits_contact FROM galleries")} <= {None, G.contact_email()}
    reyes = EXPECTED["sessions_by_name"]["reyes"]
    assert _q(A, "SELECT sd_shipped FROM sessions WHERE id=?", (reyes,))[0][0] == EXPECTED["delivered"][str(reyes)]["sd_shipped"]

    diaz = EXPECTED["sessions_by_name"]["diaz"]
    gid = EXPECTED["download_galleries"][str(diaz)]["gallery_id"]
    (tmp_path / "host" / gid / "seen.txt").write_text(date.today().isoformat() + "T12:00:00Z")
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    sd, fv = _q(A, "SELECT sd_shipped, first_visit FROM sessions WHERE id=?", (diaz,))[0]
    assert sd and fv and abs((date.fromisoformat(sd) - date.today()).days) <= 1
    assert _q(A, "SELECT 1 FROM audit WHERE session_id=? AND action LIKE '%download gallery: files delivered%'", (diaz,))
    assert _q(A, "SELECT upload_state FROM galleries WHERE session_id=?", (diaz,))[0][0] == "up"

def test_older_code_refuses_a_newer_database(upgraded, monkeypatch):
    A, c, taken, _ = upgraded
    monkeypatch.setattr(A, "SCHEMA_VERSION", A.SCHEMA_VERSION - 1)
    with pytest.raises(A.SchemaTooNew):
        A.init_db()
