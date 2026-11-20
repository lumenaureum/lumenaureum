import csv
import hashlib
import hmac
import re
import io
import json
import os
import shutil
import socket
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

from fake_s3 import SECRET, FakeS3, make_cert
from helpers import fresh_modules, hosted, signed_in_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from make_fixture import doc_text, doc_text_any, page_text

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "db-1.5.1"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json", "ntfy.json")
E = {"REMOTE_ADDR": "127.0.0.1"}
V150_SCHEMA = 9
DIRS = ("galleries", "galleries-kept", "host", "web", "photos", "faces")
HOST, EARLIER = EXPECTED["hosts"]["current"], EXPECTED["hosts"]["earlier"][0]
BY = EXPECTED["sessions_by_name"]
ORDERS = EXPECTED["print_orders"]
YEAR = EXPECTED["year"]
ADDR = EXPECTED["web_cert"]["address"]

S3 = EXPECTED["s3"]
LICENSE_LINE = "Your license to use the photos is set out in your agreement"

def _place(tmp_path):

    shutil.copyfile(FIXTURE / "crm.db", tmp_path / "crm.db")
    for name in SIDE_FILES:
        shutil.copyfile(FIXTURE / name, tmp_path / name)
    for name in DIRS:
        shutil.copytree(FIXTURE / name, tmp_path / name)
    assert EXPECTED["storage_keys"] == ["gallery_host", "gallery_hosts_prev", "primary", "s3_certs", "secondary", "web_certs"]
    assert EXPECTED["primary_type"] == "local"
    (tmp_path / "storage.json").write_text(json.dumps({
        "primary": {"type": "local", "path": str(tmp_path / "vault")},
        "secondary": dict(S3["secondary"], secret_key=SECRET),
        "s3_certs": S3["pins"],
        "gallery_host": dict(EXPECTED["gallery_host"], path=str(tmp_path / HOST)),
        "gallery_hosts_prev": [{"type": "local", "path": str(tmp_path / EARLIER)}],
        "web_certs": EXPECTED["web_cert"]["pins"]}))

def _same_places(monkeypatch, A, tmp_path):

    there, here = EXPECTED["data_folder"], os.path.realpath(tmp_path)
    from plugins import gallery as G_pkg
    for ns in (G_pkg.__dict__, A.app.view_functions["build_gallery"].__globals__):
        real = ns["host_identity"]

        def ident(cfg, _real=real):
            i = _real(cfg)
            if i and i[0] == "local" and (i[1] == here or i[1].startswith(here + os.sep)):
                return ("local", there + i[1][len(here):])
            return i
        monkeypatch.setitem(ns, "host_identity", ident)

def _open(tmp_path, monkeypatch, version=None):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    if version:
        import about
        monkeypatch.setattr(about, "version", lambda: version)
    import app as A
    from vault import backup as B
    _same_places(monkeypatch, A, tmp_path)
    taken = []

    def backup(path, *a, **kw):

        import db as dbmod
        con = dbmod.connect(path)
        try:
            SCHEMA_AT_BACKUP.append(con.execute("PRAGMA user_version").fetchone()[0])
        finally:
            con.close()
        taken.append(kw.get("kind"))
    SCHEMA_AT_BACKUP.clear()
    monkeypatch.setattr(B, "sealed_backup", backup)
    c = signed_in_client(A)
    return A, c, taken

SCHEMA_AT_BACKUP = []

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

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def _table(A, name, order):

    con = A.connect()
    try:
        cur = con.execute(f"SELECT * FROM {name} ORDER BY {order}")
        cols = [d[0] for d in cur.description]
        got = cur.fetchall()
    finally:
        con.close()
    fix = lambda v: (hashlib.sha256(v if isinstance(v, bytes) else v.encode()).hexdigest()
                     if isinstance(v, bytes) or (isinstance(v, str) and len(v) > 400) else v)
    return [{c_: fix(v) for c_, v in zip(cols, r)} for r in got]

PRINT_TABLES = (("print_orders", "id"), ("print_order_lines", "id"), ("print_payments", "id"), ("print_lab_costs", "id"),
                ("print_galleries", "session_id"), ("print_order_oids", "oid"), ("price_list_items", "sort, id"),
                ("price_list_versions", "plv"))

def _gallery_rows(A):
    cols = tuple(EXPECTED["galleries_130"]["1"])
    return {str(r[0]): dict(zip(cols, r[1:])) for r in _q(A, f"SELECT session_id, {', '.join(cols)} FROM galleries")}

def _on_host(A, tmp_path):
    out = {}
    for sid, gid in _q(A, "SELECT g.session_id, s.gallery_id FROM galleries g JOIN sessions s ON s.id=g.session_id"):
        out[str(sid)] = {w: sorted(p.name for p in (tmp_path / w / gid).iterdir()) if (tmp_path / w / gid).is_dir() else None
                         for w in (HOST, EARLIER)}
    return out

def _links(A):
    return {str(r[0]): dict(gallery_id=r[1], link_was=r[2], sealed_gen=r[3], page_set=r[4])
            for r in _q(A, """SELECT s.id, s.gallery_id, g.link_was, g.sealed_gen, g.page_set FROM galleries g
                              JOIN sessions s ON s.id=g.session_id""")}

@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    _place(tmp_path)
    return _open(tmp_path, monkeypatch) + (tmp_path,)

def test_the_fixture_is_a_1_5_0_database():
    assert EXPECTED["schema_version"] == V150_SCHEMA and EXPECTED["made_with"].startswith("1.5.1")
    assert EXPECTED["app_version"] == "1.5.1"
    assert (FIXTURE / "crm.db").read_bytes()[:16] != b"SQLite format 3\x00"
    assert (FIXTURE / "faces" / f"{EXPECTED['face']['household']}.jpg").read_bytes()[:3] != b"\xff\xd8\xff"

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

def test_the_schema_stays_9_with_no_upgrade_backup(upgraded):

    A, c, taken, _ = upgraded
    assert A.SCHEMA_VERSION == V150_SCHEMA
    assert _q(A, "PRAGMA user_version")[0][0] == V150_SCHEMA
    assert taken == [] and SCHEMA_AT_BACKUP == []

def test_today_says_once_that_it_was_updated(tmp_path, monkeypatch):

    import about
    now = about.version()
    if now == EXPECTED["app_version"]:
        now = "1.5.2"
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch, version=now)
    assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == now
    first, second = _page(c, "/"), _page(c, "/")
    assert f"Updated to Lumen Aureum {now}." in first
    note = first.split('id="update-note"', 1)[1].split("</section>", 1)[0]
    assert taken == [] and "from before the update" not in note
    assert "Moved on under the new stage rules" not in note
    assert "Updated to Lumen Aureum" in second
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    assert "Updated to Lumen Aureum" not in _page(c, "/")

def test_every_record_is_still_there(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, name FROM households")} == EXPECTED["clients"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == {
        k: v["stage"] for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, lost_reason FROM sessions WHERE stage='lost'")} == EXPECTED["lost"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, tax_rate FROM sessions")} == EXPECTED["tax_rates"]
    for sid in EXPECTED["sessions"]:
        assert c.get(f"/session/{sid}/edit", environ_base=E).status_code == 200
    for hid in EXPECTED["clients"]:
        assert c.get(f"/c/{hid}", environ_base=E).status_code == 200

def test_payments_tips_and_their_tax_are_as_1_5_0_kept_them(upgraded):
    A, c, taken, _ = upgraded
    got = {str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4], tax=r[5], taxable=r[6], voided=r[7])
           for r in _q(A, "SELECT id, session_id, kind, amount, received, tax, taxable, voided FROM payments")}
    assert got == EXPECTED["payments_kept"]
    tips = {k for k, v in EXPECTED["payments_kept"].items() if v["kind"] == "tip"}
    assert len(tips) == 2 and all(EXPECTED["payments_kept"][k]["tax"] == 0.0 for k in tips)
    _in_request(A, lambda: A._fill_payment_taxes())
    _in_request(A, lambda: A._fill_payment_taxes(BY["ellis"]))
    assert {str(r[0]) for r in _q(A, "SELECT id FROM payments WHERE kind='tip'")} == tips
    assert [list(r) for r in _q(A, "SELECT id, received, amount, tax, type, kind FROM books ORDER BY id")] == EXPECTED["books"]
    assert [r[0] for r in _q(A, "SELECT kind FROM books ORDER BY id")].count("tip") == 1
    assert _in_request(A, lambda: A.year_money(YEAR))["tips"] == EXPECTED["year_money"]["tips"] == 60.0

def test_balances_match_1_5_0(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): dict(session=r[1], amount=r[2]) for r in
            _q(A, "SELECT payment_id, session_id, amount FROM refund_credits")} == EXPECTED["refund_credits"]
    for sid, want in EXPECTED["money"].items():
        t = _in_request(A, lambda: A.totals(int(sid)))
        assert {k: round(t[k] or 0.0, 2) for k in want} == want, sid
    assert EXPECTED["money"][str(BY["grant"])]["kept"] == 100.0 and EXPECTED["money"][str(BY["foster"])]["written_off"] == 162.0

def test_finances_and_the_export_match_1_5_0(upgraded):

    A, c, taken, _ = upgraded
    assert _finances(A, c, YEAR) == EXPECTED["finances"]
    ym = _in_request(A, lambda: A.year_money(YEAR))
    r2 = lambda v: round(v or 0.0, 2)
    got = dict(total=r2(ym["total"]), refunds=r2(ym["refunds"]), tax_due=r2(ym["tax_due"]),
               tax_adjusted=r2(ym["tax_adjusted"]), written_off=r2(ym["written_off"]), tips=r2(ym["tips"]),
               by_month={r["mo"]: r2(r["v"]) for r in ym["by_month"]},
               tax_rows=[dict(kind=r["kind"], day=r["day"], amount=r2(r["amount"]), tax=r2(r["tax"]),
                              taxable=r2(r["taxable"])) for r in ym["tax_rows"]])
    assert got == EXPECTED["year_money"]
    assert _in_request(A, lambda: A.app.config["prints_year"](YEAR)) == EXPECTED["prints_year"]
    r = c.get(f"/money/accounts.zip?year={YEAR}", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.data))
    export = {n: list(csv.reader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in sorted(z.namelist())}
    assert export == EXPECTED["export"]
    assert {"prints.csv", "print-lab-costs.csv"} <= set(export)

def test_statements_and_the_charges_they_are_made_of(upgraded):

    A, c, taken, _ = upgraded
    assert [list(r) for r in _q(A, "SELECT id, session_id, at, amount, number, label FROM charges ORDER BY id")] \
        == EXPECTED["charges"]
    assert [list(r) for r in _q(A, "SELECT id, session_id, amount, at, why FROM write_offs ORDER BY id")] == EXPECTED["write_offs"]
    assert [list(r) for r in _q(A, "SELECT id, session_id, at, tax, taxable, why FROM tax_adjustments ORDER BY id")] \
        == EXPECTED["tax_adjustments"]
    assert [ch[3:] for ch in EXPECTED["charges"] if ch[1] == BY["grant"]] == [[100.0, "", "Canceled, deposit kept"]]
    r2 = lambda v: round(v or 0.0, 2)
    for key, want in EXPECTED["statements"].items():
        hid, month = key.split()
        v = _in_request(A, lambda: A.statement_ctx(int(hid), month))
        got = dict(opening=r2(v["opening"]), closing=r2(v["closing"]),
                   invoices=[[i["date"], i["number"], r2(i["amount"])] for i in v["invoices"]],
                   payments=[[p["date"], p["kind"], r2(p["amount"])] for p in v["payments"]])
        assert got == want, key

def test_issued_documents_are_the_ones_1_5_0_kept(upgraded):

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
        assert _q(A, "SELECT html FROM bill_snapshots WHERE number=?", (number,))[0][0] in page
    for sid, kinds in EXPECTED["bills"].items():
        for kind, want in kinds.items():
            v = _in_request(A, lambda: A.bill_ctx(int(sid), receipt=kind == "receipt"))
            assert dict(number=v["number"], since=v.get("since", ""), paid=round(v["paid"], 2), total=round(v["total"], 2),
                        balance=round(v["balance"], 2)) == want, (sid, kind)
    assert len(EXPECTED["print_invoices"]) == 4
    for oid, want in EXPECTED["print_invoices"].items():
        assert page_text(_page(c, f"/prints/order/{oid}/invoice")) == want["text"], oid
        assert want["number"] in want["text"]

def test_drafts_stay_unnumbered_and_the_next_numbers_follow(upgraded):

    A, c, taken, _ = upgraded
    ortiz, bell = BY["ortiz"], BY["bell"]
    assert not _q(A, "SELECT 1 FROM doc_numbers WHERE session_id IN (?, ?)", (ortiz, bell))
    for sid, kind, pre in ((ortiz, "invoice", "INV"), (bell, "receipt", "R")):
        assert c.post(f"/session/{sid}/{kind}/issue", environ_base=E).status_code in (302, 303)
        new = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, pre))[0][0]
        same_year = [int(n[-4:]) for n in EXPECTED["doc_numbers"] if n[:-4] == new[:-4]]
        assert new not in EXPECTED["doc_numbers"] and int(new[-4:]) == max(same_year, default=0) + 1
        if pre == "INV" and new[:-4] == f"INV-{YEAR}-":
            assert int(new[-4:]) == EXPECTED["number_marks"][f"INV-{YEAR}-"] + 1 == 8

def test_the_signed_deletions_still_verify_and_ids_are_not_reused(upgraded):
    A, c, taken, tmp_path = upgraded
    items = json.loads((tmp_path / "deletions.json").read_text())
    assert items == EXPECTED["deletions"] and len(items) == 3
    key = _in_request(A, lambda: A._deletion_key(A.db()))
    for d_ in items:
        assert hmac.compare_digest(d_["mac"], A._deletion_mac(key, d_))
    assert _in_request(A, A.unconfirmed_deletions) == []
    gone = EXPECTED["deleted_130"]
    assert not _q(A, "SELECT 1 FROM households WHERE id=?", (gone["client"],))
    assert {r[0]: r[1] for r in _q(A, "SELECT name, n FROM id_marks")} == EXPECTED["id_marks"]
    assert _in_request(A, lambda: A.next_id("households")) == EXPECTED["id_marks"]["households"] + 1
    assert _in_request(A, lambda: A.next_id("sessions")) == EXPECTED["id_marks"]["sessions"] + 1
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, session_id FROM quote_versions")} == EXPECTED["quote_versions"]

def test_settings_carried_over(upgraded):
    A, c, taken, _ = upgraded
    keys = tuple(EXPECTED["settings"])
    assert {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(keys)),
                                    keys)} == EXPECTED["settings"]
    for k, digest in EXPECTED["logos"].items():
        v = _q(A, "SELECT value FROM settings WHERE key=?", (k,))
        assert v and hashlib.sha256(v[0][0].encode()).hexdigest() == digest, k
    for group in ("business", "sales_tax"):
        assert {k: (_q(A, "SELECT value FROM settings WHERE key=?", (k,)) or [[None]])[0][0]
                for k in EXPECTED[group]} == EXPECTED[group]
    assert _q(A, "SELECT value FROM settings WHERE key='business_website'")[0][0] == EXPECTED["website"]
    assert getattr(A.TZ, "key", None) == EXPECTED["settings"]["timezone"]
    assert sorted(A.C.LICENSE_TYPES) == EXPECTED["license_types"]
    assert {sid: bool(_in_request(A, lambda: A.licensed(int(sid)))) for sid in EXPECTED["licensed"]} == EXPECTED["licensed"]
    assert {sid: _in_request(A, lambda: A.booked_hint(int(sid))) for sid in EXPECTED["booked_hint"]} == EXPECTED["booked_hint"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, priced_on FROM sessions")} == EXPECTED["priced_on"]

    def due(sid):
        s = A.session_full(sid)
        return A.deposit_due(sid, s, A.priced_on(sid, s, A.totals(sid)["quoted"]))
    assert {sid: _in_request(A, lambda: due(int(sid))) for sid in EXPECTED["deposit_due"]} == EXPECTED["deposit_due"]
    assert json.loads(_q(A, "SELECT value FROM settings WHERE key='upgrade_140'")[0][0]) == EXPECTED["upgrade_140"]
    assert EXPECTED["upgrade_140"].get("finished")

def test_the_master_key_check_is_remembered(upgraded):
    A, c, taken, _ = upgraded
    assert EXPECTED["master_checked"]
    assert _q(A, "SELECT value FROM settings WHERE key='master_checked'")[0][0] == EXPECTED["master_checked"]
    assert c.get("/security/master-key", environ_base=E).status_code == 200

def _opened(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def _manifest(A, G, tmp_path, sid):
    gid = _q(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    key, ekey = _q(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))[0]
    blob = hosted(tmp_path / HOST / gid, "manifest.bin", ekey or key).read_bytes()
    return gid, ekey or key, blob, json.loads(_opened(G, ekey or key, blob, "manifest.bin"))

def test_every_gallery_and_its_host_record_is_as_1_5_0_left_it(upgraded):

    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    assert G.host_fingerprint() == EXPECTED["hosts"]["current_fp"]
    assert [G.fingerprint_of(h) for h in G.earlier_host_cfgs()] == EXPECTED["hosts"]["earlier_fp"]
    assert _gallery_rows(A) == EXPECTED["galleries_130"]
    assert _links(A) == EXPECTED["links"]
    assert all(v["gallery_id"].startswith("g-") and v["link_was"] is None for v in EXPECTED["links"].values())
    assert _on_host(A, tmp_path) == EXPECTED["on_host"]

    assert [list(r) for r in _q(A, "SELECT gallery_id, host_fp, added FROM gallery_leftovers")] == EXPECTED["leftovers"]
    assert [r[:2] for r in EXPECTED["leftovers"]] == [[EXPECTED["replaced"]["old_gallery_id"], EXPECTED["hosts"]["earlier_fp"][0]]]
    g = EXPECTED["galleries_130"]
    lane, hale, ivers, moss, nash = (str(BY[n]) for n in ("lane", "hale", "ivers", "moss", "nash"))
    assert g[lane]["moved_from"] == EXPECTED["hosts"]["earlier_fp"][0] and g[lane]["host_fp"] == EXPECTED["hosts"]["current_fp"]
    assert EXPECTED["on_host"][lane][EARLIER]
    assert g[hale]["dark_at"] and g[hale]["upload_state"] == "removed" and EXPECTED["on_host"][hale][HOST] == ["index.html"]
    assert g[ivers]["restored_at"] and g[ivers]["restore_days"] == 14 and g[ivers]["upload_state"] == "up"
    assert g[moss]["dark_at"] and g[moss]["moved_from"] is None and EXPECTED["on_host"][moss] == {HOST: ["index.html"], EARLIER: None}
    assert g[nash]["upload_state"] == "error" and g[nash]["uploaded_at"] is None
    assert {str(r[0]): r[1] for r in _q(A, "SELECT session_id, upload_error FROM galleries WHERE upload_state='error'")} \
        == EXPECTED["upload_errors"] == {nash: "the host dropped the connection"}
    hale_gid = EXPECTED["links"][hale]["gallery_id"]
    page = (tmp_path / HOST / hale_gid / "index.html").read_bytes()
    assert hashlib.sha256(page).hexdigest() == EXPECTED["dark_page_sha256"] and b"This gallery has gone dark." in page
    move = _page(c, "/galleries/move")
    assert "Nothing: every gallery is on the web host set up now." in move
    today = _page(c, "/")
    assert "needs the new link" not in today and "still on your earlier web host" not in today

    assert "1 gallery of a deleted session still has to come down from a web host" in today
    for sid in (lane, ivers):
        link = _in_request(A, lambda: A.app.config["gallery_link"](int(sid)))
        assert link.startswith(EXPECTED["gallery_base_url"] + EXPECTED["links"][sid]["gallery_id"] + "/")
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (BY["hale"],))[0][0]
    assert "Put back online" in _page(c, f"/c/{hid}?s={BY['hale']}")

def test_the_codeword_gallery_and_its_sealed_picks_and_draft(upgraded):

    A, c, taken, tmp_path = upgraded
    import sealed
    G = A.app.config["gallery_module"]
    want = EXPECTED["gallery"]
    sid = want["session"]
    cols = ("key", "ekey", "n_files", "built_at", "cw_fixed", "upload_state", "uploaded_at", "removed_at",
            "draft_n", "draft_at", "picks_hash", "limits_days")
    assert dict(zip(cols, _q(A, f"SELECT {', '.join(cols)} FROM galleries WHERE session_id=?", (sid,))[0])) \
        == {k: want[k] for k in cols}
    assert want["has_codeword_key"] and want["codeword"] and want["draft_n"] == 3
    assert tuple(_q(A, "SELECT gallery_codeword, retouch_picks FROM sessions WHERE id=?", (sid,))[0]) == \
        (want["codeword"], want["retouch_picks"])
    link = _in_request(A, lambda: A.app.config["gallery_link"](sid))
    assert link.endswith("&c=1") == want["link_asks_for_word"] is True
    gid = EXPECTED["links"][str(sid)]["gallery_id"]
    kept = G.KEPT_ROOT / gid
    assert sorted(p.name for p in kept.iterdir()) == EXPECTED["kept_picks"] == ["draft.las", "picks.las"]
    k = G.key_bytes(want["ekey"])
    picks = json.loads(sealed.open_link("picks", gid, k, (kept / "picks.las").read_bytes()))
    draft = json.loads(sealed.open_link("draft", gid, k, (kept / "draft.las").read_bytes()))
    assert len(picks["picks"]) == 1 and len(draft["picks"]) == want["draft_n"]
    assert hashlib.sha256((kept / "picks.las").read_bytes()).hexdigest() == want["picks_hash"]
    assert hosted(tmp_path / HOST / gid, "picks.las", want["ekey"]).read_bytes() == (kept / "picks.las").read_bytes()

def test_the_1_2_galleries_picks_and_download_galleries_survive(upgraded):
    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    got = {str(r[0]): dict(proof=r[1], prune_due=r[2], upload_state=r[3], n_files=r[4], built_at=r[5]) for r in
           _q(A, "SELECT session_id, proof, prune_due, upload_state, n_files, built_at FROM galleries")}
    assert got == EXPECTED["galleries"]
    for sid_, want_ in EXPECTED["download_galleries"].items():
        gid, key, blob, m = _manifest(A, G, tmp_path, int(sid_))
        assert gid == want_["gallery_id"] and G.is_download(gid) and G.online_download(gid)
        assert hashlib.sha256(blob).hexdigest() == want_["manifest_sha256"]
        assert [dict(name=z["name"], size=z["size"], parts=z["parts"]) for z in m["zips"]] == want_["zips"]
        assert sorted(p.name for p in (tmp_path / HOST / gid).iterdir()) == want_["host_files"]
        fulls = {it["on"]: _opened(G, key, hosted(tmp_path / HOST / gid, it["o"], key).read_bytes(), it["o"])
                 for it in m["items"]}
        for z in m["zips"]:
            whole = b"".join(_opened(G, key, hosted(tmp_path / HOST / gid, p, key).read_bytes(), p) for p in z["parts"])
            zf = zipfile.ZipFile(io.BytesIO(whole))
            assert len(whole) == z["size"] and all(zf.read(n) == fulls[n] for n in zf.namelist())
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, delivery_method FROM sessions")} == EXPECTED["delivery_method"]
    finals = {}
    for s_, name in _q(A, "SELECT session_id, name FROM photo_files WHERE kind='final' AND state='done' ORDER BY session_id, name"):
        finals[str(s_)] = finals[str(s_)] + "," + name if str(s_) in finals else name
    assert finals == EXPECTED["finals"]

    def steps(s_):
        return {st_["label"]: bool(st_["done"]) for stage in A.workflow(s_)["stages"] for st_ in stage["steps"]}
    for s_, want_ in EXPECTED["delivered"].items():
        sd, fv, stage = _q(A, "SELECT sd_shipped, first_visit, stage FROM sessions WHERE id=?", (int(s_),))[0]
        assert (sd, fv, stage) == (want_["sd_shipped"], want_["first_visit"], want_["stage"])
        assert _in_request(A, lambda: steps(int(s_)))["Files delivered"] is want_["files_delivered"]
    assert EXPECTED["link_clause"] in doc_text_any(_page(c, f"/session/{BY['reyes']}/agreement"))

def _web_host_as_it_was(monkeypatch, A):

    import hostcheck
    caps = json.loads(_q(A, "SELECT value FROM settings WHERE key='gallery_host_caps'")[0][0])
    monkeypatch.setattr(hostcheck, "capabilities", lambda *a, **kw: caps["now"])

def test_a_gallery_check_changes_nothing(upgraded, monkeypatch):

    A, c, taken, tmp_path = upgraded
    monkeypatch.setitem(A.app.config, "clock_ok", lambda: False)
    _web_host_as_it_was(monkeypatch, A)
    before = _on_host(A, tmp_path)
    assert before == EXPECTED["on_host"]
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    rows = _gallery_rows(A)
    keep = ("upload_state", "removed_at", "dark_at", "restored_at", "restore_days", "restore_once", "host_fp",
            "moved_from", "link_base", "n_files", "built_at")
    assert {s: {k: v[k] for k in keep} for s, v in rows.items()} == \
        {s: {k: v[k] for k in keep} for s, v in EXPECTED["galleries_130"].items()}
    assert _on_host(A, tmp_path) == before
    assert [list(r) for r in _q(A, "SELECT gallery_id, host_fp, added FROM gallery_leftovers")] == EXPECTED["leftovers"]
    assert {t: _table(A, t, o) for t, o in PRINT_TABLES} == EXPECTED["prints_tables"]
    diaz = BY["diaz"]
    gid = EXPECTED["download_galleries"][str(diaz)]["gallery_id"]
    key = _q(A, "SELECT key FROM galleries WHERE session_id=?", (diaz,))[0][0]
    from helpers import host_write
    host_write(tmp_path / HOST / gid, "seen.txt", date.today().isoformat() + "T12:00:00Z", key)
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    sd, fv = _q(A, "SELECT sd_shipped, first_visit FROM sessions WHERE id=?", (diaz,))[0]
    assert sd and fv and abs((date.fromisoformat(sd) - date.today()).days) <= 1

def test_the_unreachable_host_memory_is_kept_and_read_by_its_age(upgraded):
    import time
    A, c, taken, tmp_path = upgraded
    want = EXPECTED["unreachable"]
    fp_old = EXPECTED["hosts"]["earlier_fp"][0]
    assert list(want) == [f"gallery_unreachable:{fp_old}"]
    assert {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key LIKE 'gallery_unreachable:%'")} == want
    P = A.app.view_functions["build_gallery"].__globals__
    due = time.time() - float(want[f"gallery_unreachable:{fp_old}"]) >= P["UNREACHABLE_RETRY"]
    with A.app.app_context():
        assert P["may_try"](fp_old) is due

def test_the_password_wrap_and_its_work_factor_are_kept(upgraded):
    A, c, taken, tmp_path = upgraded
    import datakey
    n = EXPECTED["password_work"]
    assert n and datakey.password_work() == n
    assert json.loads((tmp_path / "keyring.json").read_text()) == json.loads((FIXTURE / "keyring.json").read_text())
    page = _page(c, "/security")
    assert 'id="pw-strength"' in page and f"scrypt work factor {n}" in page

def test_the_studio_page_its_files_and_inquiries_are_kept(upgraded):

    A, c, taken, tmp_path = upgraded
    got = {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key LIKE 'site\\_%' ESCAPE '\\'"
                                     " AND key NOT IN ('site_inquiry_key')")}
    assert got.pop("site_left", "{}") == "{}" and {k: v for k, v in got.items() if k != "site_left"} == \
        {k: v for k, v in EXPECTED["site_settings"].items() if k != "site_left"}
    assert hashlib.sha256(_q(A, "SELECT value FROM settings WHERE key='site_inquiry_key'")[0][0].encode()).hexdigest() \
        == EXPECTED["site_key_sha256"]
    assert [dict(id=r[0], source=r[1], name=r[2], data_sha256=hashlib.sha256(r[3]).hexdigest() if r[3] else None)
            for r in _q(A, "SELECT id, source, name, data FROM studio_photos ORDER BY sort, id")] == EXPECTED["studio_photos"]
    files = [list(r) for r in _q(A, "SELECT name, host_fp, size, sha256 FROM studio_files ORDER BY name")]
    assert [[n, fp, sha] for n, fp, _size, sha in files] == EXPECTED["studio_files"]
    web = (tmp_path / HOST).parent
    for name, _fp, size, sha in files:
        body = (web / name).read_bytes()
        assert len(body) == size and hashlib.sha256(body).hexdigest() == sha, name
    assert [list(r) for r in _q(A, "SELECT sha256, session_id, household_id, outcome FROM studio_inquiries ORDER BY sha256")] \
        == EXPECTED["studio_inquiries"]
    assert [json.loads(r[0]) for r in _q(A, "SELECT fields FROM studio_inquiry_review ORDER BY id")] == EXPECTED["inquiry_review"]
    added = EXPECTED["inquiry_added"]
    assert list(_q(A, """SELECT h.id, h.name, h.source, s.type, s.stage, s.shoot_date, s.date_tentative FROM sessions s
                         JOIN households h ON h.id=s.household_id WHERE s.id=?""", (BY["ada"],))[0]) == [
        added[k] for k in ("household", "name", "source", "type", "stage", "shoot_date", "date_tentative")]
    page = _page(c, "/")
    assert "1 website inquiry to review: Add or Spam" in page and "Grace Hopper" in page
    rid = _q(A, "SELECT id FROM studio_inquiry_review")[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": str(rid)}, environ_base=E)
    assert not _q(A, "SELECT 1 FROM studio_inquiry_review")
    assert _q(A, "SELECT source FROM households WHERE name='Grace Hopper'")[0][0] == "Website"

def test_print_orders_are_on_and_every_order_is_as_1_5_0_kept_it(upgraded):

    A, c, taken, tmp_path = upgraded
    assert {t: _table(A, t, o) for t, o in PRINT_TABLES} == EXPECTED["prints_tables"]
    keys = tuple(EXPECTED["prints_settings"])
    assert {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(keys)),
                                    keys)} == EXPECTED["prints_settings"]
    assert EXPECTED["prints_settings"]["plugin_on_prints"] == "1" and EXPECTED["prints_settings"]["prints_on"] == "1"
    assert _in_request(A, lambda: A.plugin_on("prints"))
    assert "Print orders" in _page(c, "/settings/plugins")
    states = {str(r[0]): r[1] for r in _q(A, "SELECT id, state FROM print_orders")}
    assert {states[str(oid)]: name for name, oid in ORDERS.items()} == {s: s for s in ORDERS}
    for oid in ORDERS.values():
        assert c.get(f"/prints/order/{oid}", environ_base=E).status_code == 200
    pays = {}
    for r in EXPECTED["prints_tables"]["print_payments"]:
        pays.setdefault(r["order_id"], []).append((r["kind"], r["amount_cents"]))
    total = {r[0]: json.loads(r[1])["total"] for r in _q(A, "SELECT id, invoice_data FROM print_orders WHERE invoice_data IS NOT NULL")}
    assert pays[ORDERS["paid"]] == [("payment", total[ORDERS["paid"]] + 500)]
    assert pays[ORDERS["invoiced"]] == [("payment", 1000)] and total[ORDERS["invoiced"]] > 1000
    assert pays[ORDERS["shipped"]] == [("payment", total[ORDERS["shipped"]])]
    assert [k for k, _ in pays[ORDERS["canceled"]]] == ["payment", "refund"]
    assert sum(a if k == "payment" else -a for k, a in pays[ORDERS["canceled"]]) == 0
    costs = EXPECTED["prints_tables"]["print_lab_costs"]
    assert [(r["order_id"], r["kind"], r["products"], r["shipping"], r["tax"]) for r in costs] == \
        [(ORDERS["shipped"], "order", 1240, 675, 80)]
    review = _page(c, "/prints/review")
    assert "Dale Rivers" in review
    assert "Print orders from people not on file (1)" in _page(c, "/")
    assert _page(c, "/settings/prints")

def test_the_print_offer_on_the_web_host_and_a_paid_order_goes_to_the_lab(upgraded):

    A, c, taken, tmp_path = upgraded
    G = A.app.config["gallery_module"]
    wren = BY["wren"]
    gid = EXPECTED["links"][str(wren)]["gallery_id"]
    key = _q(A, "SELECT key FROM galleries WHERE session_id=?", (wren,))[0][0]
    off = json.loads(_opened(G, key, hosted(tmp_path / HOST / gid, "offer.bin", key).read_bytes(), "offer.bin"))
    assert off["plv"] == int(EXPECTED["prints_settings"]["prints_plv"]) and "cost" not in json.dumps(off)
    want = {r["id"]: r["each_cents"] for r in EXPECTED["prints_tables"]["price_list_items"] if r["offered"]}
    assert {i["id"]: i["each"] for i in off["items"]} == want
    oid = ORDERS["paid"]
    r = c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.get_data()))
    inv = EXPECTED["print_invoices"][str(oid)]["number"]
    names = z.namelist()
    assert "order.txt" in names and any(n.startswith(f"{inv}-1-8x10") for n in names)
    assert _q(A, "SELECT state FROM print_orders WHERE id=?", (oid,))[0][0] == "prepared"

def test_the_web_host_certificate_pin_and_the_host_check_are_kept(upgraded, monkeypatch):

    A, c, taken, tmp_path = upgraded
    import webtls
    pin = EXPECTED["web_cert"]["pins"][ADDR]
    assert len(pin["spki"]) == 64 and pin["self_signed"] and webtls.pin_for(ADDR) == pin
    assert EXPECTED["web_cert"]["seen"] == {}
    assert not _q(A, "SELECT 1 FROM settings WHERE key LIKE 'web\\_cert\\_%' ESCAPE '\\'")
    page = _page(c, "/setup/galleries")
    assert f"The one you confirmed on {pin['at']}." in page and pin["fp"] in page
    caps = json.loads(_q(A, "SELECT value FROM settings WHERE key='gallery_host_caps'")[0][0])
    assert dict(fp=caps.get("fp"), setup_kind=caps["setup"]["kind"], now_kind=caps["now"]["kind"],
                features=caps["now"].get("features")) == EXPECTED["host_caps"]
    assert EXPECTED["host_caps"]["now_kind"] == "ok"
    from test_web_cert_pin import Front, make_cert
    d = tmp_path / "certs"
    d.mkdir()
    other = make_cert(d, "other", names=(webtls.host_of(ADDR),))
    f = Front(other[2], other[3])
    real = socket.create_connection

    def resolve(addr, *a, **kw):
        if f"{addr[0]}:{addr[1]}" == ADDR:
            addr = ("127.0.0.1", f.port)
        return real(addr, *a, **kw)
    monkeypatch.setattr(socket, "create_connection", resolve)
    try:
        state, seen = webtls.check(ADDR)
        assert state == "changed" and seen["spki"] != pin["spki"]
    finally:
        f.close()

def test_the_refund_choices_and_what_each_session_waits_for(upgraded):

    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, refund_release FROM sessions WHERE refund_release IS NOT NULL")} \
        == EXPECTED["refund_release"] == {str(BY["lane"]): "release", str(BY["grant"]): "release", str(BY["olsen"]): "hold"}
    for sid, want in EXPECTED["release_blocked"].items():
        assert round(_in_request(A, lambda: A.release_blocked(int(sid))), 2) == want, sid
    assert EXPECTED["release_blocked"][str(BY["olsen"])] == 30.0
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (BY["lane"],))[0][0]
    assert "After the refund, the gallery releases once nothing is owed." in _page(c, f"/c/{hid}?s={BY['lane']}")

def test_the_canceled_booking_header_says_what_billing_says_in_cents(upgraded):

    A, c, taken, _ = upgraded
    for sid, want in EXPECTED["headers"].items():

        page = _page(c, f"/session/{sid}/edit")
        summary = doc_text_any(page.split('<table class="ui-money" aria-label="Summary">', 1)[1].split("</table>", 1)[0])
        figs = re.findall(r"(Quote|Deposit kept|Balance) \$([\d,]+(?:\.\d+)?)", want)
        assert figs, want
        for label, amount in figs:
            assert f"{label} ${float(amount.replace(',', '')):,.2f}" in summary, (sid, label, amount, summary)
        assert all(re.fullmatch(r"\$[\d,]+\.\d\d", m) for m in re.findall(r"\$[\d,.]*\d", summary)), summary
    assert "Deposit kept $100" in EXPECTED["headers"][str(BY["grant"])]

def test_the_face_photo_is_kept_sealed_and_shown(upgraded):
    A, c, taken, tmp_path = upgraded
    f = EXPECTED["face"]
    r = c.get(f"/face/{f['household']}", environ_base=E)
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.headers["Cache-Control"] == "no-store"
    assert len(r.data) == f["size"] and hashlib.sha256(r.data).hexdigest() == f["sha256"]
    assert (tmp_path / "faces" / f"{f['household']}.jpg").read_bytes() == \
        (FIXTURE / "faces" / f"{f['household']}.jpg").read_bytes()

def test_the_s3_backup_location_and_its_pinned_certificate_are_kept(upgraded, monkeypatch):

    A, c, taken, tmp_path = upgraded
    from vault import backends as B
    from vault import s3 as S3mod
    from vault import web as VW
    addr = S3["address"]
    cfg = VW.config()
    assert {k: v for k, v in cfg["secondary"].items() if k != "secret_key"} == S3["secondary"]
    assert cfg["secondary"]["type"] == "s3" and cfg["secondary"]["bucket"] == S3["bucket"]
    pin = S3["pins"][addr]
    assert list(S3["pins"]) == [addr] and pin["self_signed"] and len(pin["spki"]) == 64
    assert S3mod.pin_for(addr) == pin
    assert not _q(A, "SELECT 1 FROM settings WHERE key LIKE 's3\\_cert\\_seen:%' ESCAPE '\\'")
    page = _page(c, "/setup/storage").replace("&#39;", "'")
    assert "The one you confirmed on" in page and pin["fp"] in page
    d = tmp_path / "s3certs"
    d.mkdir()
    other = FakeS3(buckets=(S3["bucket"],), tls=make_cert(d))
    real = socket.create_connection

    def resolve(where, *a, **kw):
        if f"{where[0]}:{where[1]}" == addr:
            where = ("127.0.0.1", other.port)
        return real(where, *a, **kw)
    monkeypatch.setattr(socket, "create_connection", resolve)
    monkeypatch.setattr(S3mod, "SLEEP", lambda s: None)
    try:
        with pytest.raises(Exception) as e:
            B.from_config(VW.config()["secondary"]).put_stream("x.age", io.BytesIO(b"1"))
        assert "certificate changed" in str(e.value)
        assert not other.log
    finally:
        other.close()
    assert S3mod.pin_for(addr) == pin

def test_codewords_on_by_default_and_off_for_the_type_turned_off(upgraded):

    A, c, taken, _ = upgraded
    import config as C
    assert EXPECTED["codeword_policy"] == {"family": True, "headshot": False}
    assert {t: C.POLICY[t].get("gallery_codeword") for t in sorted(C.SESSION_TYPES)} == EXPECTED["codeword_policy"]
    assert {k: v for k, v in C.POLICY["headshot"].items() if k != "gallery_codeword"} == EXPECTED["headshot_policy"]
    assert hashlib.sha256(_q(A, "SELECT value FROM settings WHERE key='rules'")[0][0].encode()).hexdigest() \
        == EXPECTED["rules_sha256"]
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (BY["upton"],))[0][0]
    assert 'name="gallery_codeword"' in _page(c, f"/inquiry?hid={hid}&type=family")
    assert 'name="gallery_codeword"' not in _page(c, f"/inquiry?hid={hid}&type=headshot")
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, gallery_codeword FROM sessions WHERE gallery_codeword != ''")} \
        == EXPECTED["codewords"]
    assert EXPECTED["codewords"][str(BY["upton"])] and EXPECTED["codewords"][str(BY["lane"])]

def test_the_replaced_link_is_kept_with_the_clients_picks_and_draft(upgraded):

    A, c, taken, tmp_path = upgraded
    import sealed
    G = A.app.config["gallery_module"]
    rep = EXPECTED["replaced"]
    sid = BY["yates"]
    gid = _q(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert gid == rep["gallery_id"] == EXPECTED["links"][str(sid)]["gallery_id"] != rep["old_gallery_id"]
    assert not (tmp_path / HOST / rep["old_gallery_id"]).exists() and not (tmp_path / EARLIER / rep["old_gallery_id"]).exists()
    assert EXPECTED["galleries_130"][str(sid)]["upload_state"] == "up"
    kept = G.KEPT_ROOT / gid
    assert sorted(p.name for p in kept.iterdir()) == rep["kept"] == ["draft.las", "picks.las"]
    key = _q(A, "SELECT key FROM galleries WHERE session_id=?", (sid,))[0][0]
    picks = json.loads(sealed.open_link("picks", gid, G.key_bytes(key), (kept / "picks.las").read_bytes()))
    draft = json.loads(sealed.open_link("draft", gid, G.key_bytes(key), (kept / "draft.las").read_bytes()))
    assert len(picks["picks"]) == 1 and len(draft["picks"]) == 2
    assert hosted(tmp_path / HOST / gid, "picks.las", key).read_bytes() == (kept / "picks.las").read_bytes()
    with pytest.raises(Exception):
        sealed.open_link("picks", rep["old_gallery_id"], G.key_bytes(key), (kept / "picks.las").read_bytes())
    assert [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? AND action LIKE 'Replaced the gallery%' ORDER BY id",
                             (sid,))] == rep["history"]
    assert [list(r) for r in _q(A, "SELECT title, done FROM tasks WHERE session_id=? AND title='Send the client the new gallery link'",
                                (sid,))] == rep["tasks"] == [["Send the client the new gallery link", None]]
    link = _in_request(A, lambda: A.app.config["gallery_link"](sid))
    assert link.startswith(EXPECTED["gallery_base_url"] + gid + "/") and rep["old_gallery_id"] not in link

def test_the_served_page_check_is_kept_and_still_warns(upgraded):

    A, c, taken, _ = upgraded
    want = EXPECTED["served_check"]
    gid = EXPECTED["links"][str(BY["ivers"])]["gallery_id"]
    assert list(want["bad"]) == [gid] and want["bad"][gid]["n"] == 2 and want["bad"][gid]["sid"] == BY["ivers"]
    assert json.loads(_q(A, "SELECT value FROM settings WHERE key='served_check'")[0][0]) == want
    assert {str(r[0]): r[1] for r in _q(A, "SELECT session_id, action FROM audit WHERE action LIKE "
                                        "'The gallery page or scripts on the web host%'")} == EXPECTED["served_history"]
    assert "The gallery of Ivers on your web host is not what Lumen Aureum put there" in _page(c, "/")

def test_receipts_name_the_agreement_only_when_one_is_signed(upgraded):

    A, c, taken, _ = upgraded
    for sid, want in EXPECTED["receipts"].items():
        assert _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='R'", (int(sid),))[0][0] == want["number"]
        page = _page(c, f"/session/{sid}/receipt")
        assert (LICENSE_LINE in page) is want["license_line"], sid
        assert want["number"] in EXPECTED["issued_text"] and doc_text(page) == EXPECTED["issued_text"][want["number"]]
    assert {v["license_line"] for v in EXPECTED["receipts"].values()} == {True, False}
    assert "copyright in your delivered photos" not in _page(c, f"/session/{BY['vance']}/receipt")

HIDS = EXPECTED["clients_150"]
S150 = EXPECTED["settings_150"]

def test_nothing_moves_again_under_the_stage_rules(tmp_path, monkeypatch):

    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch)
    want = {k: v["stage"] for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == want
    assert not _q(A, "SELECT 1 FROM audit WHERE action LIKE 'Updated to the 1.5 stage rules.%'")
    assert _q(A, "SELECT value FROM settings WHERE key='price_approvals'")[0][0] == S150["price_approvals"]
    n_audit = _q(A, "SELECT COUNT(*) FROM audit")[0][0]
    A2, c2, _ = _open(tmp_path, monkeypatch)
    assert _q(A2, "SELECT COUNT(*) FROM audit")[0][0] == n_audit
    assert {str(r[0]): r[1] for r in _q(A2, "SELECT id, stage FROM sessions")} == want

def test_the_approved_prices_stay_approved(upgraded):

    A, c, taken, _ = upgraded
    ap = json.loads(_q(A, "SELECT value FROM settings WHERE key='price_approvals'")[0][0])
    assert ap == json.loads(S150["price_approvals"])
    cur = _q(A, "SELECT MAX(id) FROM fee_schedules")[0][0]
    assert str(cur) in ap
    assert "Review your prices." not in _page(c, "/")

def test_idle_sign_out_check_in_wait_and_home_base_stay_on_general(upgraded):

    A, c, taken, _ = upgraded
    for k in ("idle_signout_hours", "check_in_snooze_months", "mileage_rate", "home_base"):
        assert _q(A, "SELECT value FROM settings WHERE key=?", (k,))[0][0] == S150[k], k
    assert (S150["idle_signout_hours"], S150["check_in_snooze_months"], S150["mileage_rate"]) == ("4", "9", "0.7")
    assert _in_request(A, A.app.config["idle_seconds"]) == 4 * 3600
    from make_fixture import form_fields

    assert form_fields(_page(c, "/security"), action="/settings")["idle_signout_hours"] == ["4"]
    assert form_fields(_page(c, "/settings?tab=clients"))["check_in_snooze_months"] == ["9"]
    page = _page(c, "/settings")
    general = form_fields(page)
    assert "home_base" not in general
    assert "mileage_rate" not in general and 'name="mileage_rate"' not in page
    assert 'data-idle="14400"' in page
    money = _page(c, "/setup/money")
    field = re.search(r'<input[^>]*id="f-mileage_rate"[^>]*>', money)
    assert field and 'value="0.70"' in field.group(0), field and field.group(0)

def test_the_gallery_look_is_on_its_own_settings_page(upgraded):

    A, c, taken, _ = upgraded
    look = ("gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength")
    got = {k: _q(A, "SELECT value FROM settings WHERE key=?", (k,))[0][0] for k in look}
    assert got == {k: EXPECTED["settings"][k] for k in look}
    page = _page(c, "/settings?tab=branding")
    assert 'id="gallery-look"' in page and 'value="elegant"' in page and EXPECTED["settings"]["proof_mark_text"] in page
    assert 'id="gallery-look"' not in _page(c, "/settings")

def test_the_locked_studio_notice_and_its_plain_file_are_kept(upgraded):

    A, c, taken, tmp_path = upgraded
    import locknotice
    for k in ("server", "topic", "token"):
        assert _q(A, "SELECT value FROM settings WHERE key=?", ("ntfy_" + k,))[0][0] == S150["ntfy_" + k]
    assert json.loads((tmp_path / "ntfy.json").read_text()) == EXPECTED["ntfy_json"]
    assert {k: v for k, v in locknotice.read_config().items()} == EXPECTED["ntfy_json"]

    page = _page(c, "/settings?tab=notifications")
    assert 'id="conn-ntfy"' in page and S150["ntfy_server"] in page and S150["ntfy_topic"][:3] + "..." in page
    assert S150["ntfy_token"] not in page and 'type="password"' not in page

def test_account_activity_lists_the_security_events(upgraded):

    A, c, taken, _ = upgraded
    want = EXPECTED["account_activity"]
    assert "Set up the locked-studio notice" in [r["text"] for r in want]
    got = [dict(text=r["text"], who=r["who"]) for r in _in_request(A, A.account_activity)]
    it = iter(got)
    assert all(w in it for w in want), (want, got)
    page = _page(c, "/security")
    for w in want:
        assert w["text"] in page, w

def test_the_release_wording_edited_on_the_document_is_kept(upgraded):

    A, c, taken, _ = upgraded
    text = _q(A, "SELECT value FROM settings WHERE key='release_text'")[0][0]
    assert hashlib.sha256(text.encode()).hexdigest() == EXPECTED["release_text_sha256"]
    assert EXPECTED["release_added"] in text
    assert _q(A, "SELECT value FROM settings WHERE key='doc_reviews'")[0][0] == S150["doc_reviews"]
    assert "release:*" in json.loads(S150["doc_reviews"])
    assert EXPECTED["release_added"] in _page(c, "/settings/wording/release")

def test_the_1_5_stage_flow_and_the_reasons_are_kept(upgraded):

    A, c, taken, _ = upgraded
    z, quinn, abbott = BY["zeller"], BY["quinn"], BY["abbott"]
    assert {str(r[0]): [r[1], r[2]] for r in _q(A, "SELECT id, stage, lost_reason FROM sessions WHERE id IN (?, ?)",
                                               (quinn, abbott))} == EXPECTED["lost_150"]
    assert EXPECTED["lost_150"] == {str(quinn): ["lost", "They went elsewhere"], str(abbott): ["lost", "Other"]}
    for sid, want in EXPECTED["history_150"].items():
        assert [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (int(sid),))] == want, sid
    hist = EXPECTED["history_150"]
    assert "Moved to Quoted: the quote was marked sent" in " ".join(hist[str(z)])
    assert any(h.startswith("Booked the new quote") for h in hist[str(z)])
    assert any(h.startswith("Moved to Shot: shoot done") for h in hist[str(z)])
    assert hist[str(quinn)] == ["Did not book: They went elsewhere"]
    assert any(h.startswith("Canceled the booking") and "Moved out of state" in h for h in hist[str(abbott)])
    got = [dict(session=r[0], old_total=r[1], old_vid=r[2], new_vid=r[3], booked=bool(r[4]))
           for r in _q(A, "SELECT session_id, old_total, old_vid, new_vid, booked_at FROM quote_replacements ORDER BY id")]
    assert got == EXPECTED["quote_replacements"] and got[0]["session"] == z and got[0]["booked"]
    assert _q(A, "SELECT stage FROM sessions WHERE id=?", (z,))[0][0] == "shot"
    for sid in (z, quinn, abbott):
        assert c.get(f"/session/{sid}/edit", environ_base=E).status_code == 200

def test_a_payment_in_trade_and_a_trip_at_the_rate_in_force(upgraded):

    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, note FROM payments WHERE note != ''")} == EXPECTED["payment_notes"]
    assert list(EXPECTED["payment_notes"].values()) == ["Two framed prints in trade"]
    assert [list(r) for r in _q(A, "SELECT session_id, date, label, amount, miles, rate FROM expenses ORDER BY id")] \
        == EXPECTED["expenses"]
    assert [e[4:] for e in EXPECTED["expenses"] if e[0] == BY["zeller"]] == [[18.0, 0.7]]
    ym = _in_request(A, lambda: A.year_money(YEAR))
    assert round(float(ym["exp"]["m"] or 0), 1) == 18.0

def test_labels_end_contact_check_in_and_the_business_client(upgraded):

    A, c, taken, _ = upgraded
    assert sorted([r[0], r[1]] for r in _q(A, "SELECT household_id, label FROM client_labels")) == EXPECTED["labels"]
    assert [list(r) for r in _q(A, "SELECT household_id, since FROM client_end_contact ORDER BY household_id")] \
        == EXPECTED["end_contact"]
    assert [list(r) for r in _q(A, "SELECT household_id, channel, summary FROM log WHERE summary='Checked in by email' ORDER BY id")] \
        == EXPECTED["check_ins"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, business FROM households WHERE business IS NOT NULL")} \
        == EXPECTED["business_clients"] == {str(HIDS["vance"]): 1}
    page = _page(c, f"/client/{HIDS['ellis']}")
    assert "Slow to pay" in page and "Prefers text" in page
    foster = HIDS["foster"]
    assert [foster] == [e[0] for e in EXPECTED["end_contact"]]
    links = _in_request(A, lambda: A.bulk_mail_links([int(h) for h in EXPECTED["clients"]]))
    foster_mail = [r[0] for r in _q(A, "SELECT email FROM people WHERE household_id=? AND COALESCE(email,'') != ''", (foster,))]
    others = [r[0] for r in _q(A, "SELECT email FROM people WHERE household_id != ? AND COALESCE(email,'') != ''", (foster,))]
    from urllib.parse import unquote
    bcc = unquote(" ".join(m for _l, m, _n in links))
    assert others and any(e in bcc for e in others)
    assert not any(e in bcc for e in foster_mail)
    assert _in_request(A, lambda: A.is_business_client(HIDS["vance"]))
    from make_fixture import form_fields
    assert form_fields(_page(c, f"/client/{HIDS['vance']}"), f"/client/{HIDS['vance']}").get("business") == ["1"]

def test_each_session_type_shows_its_icon(upgraded):

    A, c, taken, _ = upgraded
    import config as C
    for name in ("zeller", "abbott"):
        sid = BY[name]
        stype = _q(A, "SELECT type FROM sessions WHERE id=?", (sid,))[0][0]
        page = _page(c, f"/session/{sid}/edit")
        assert re.search(r'class="ms ui-type ui-type-kick"[^>]*>' + re.escape(C.type_icon(stype)) + "<", page), name
