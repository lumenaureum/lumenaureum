import csv
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

from helpers import fresh_modules, signed_in_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from make_fixture import doc_text, doc_text_any

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "db-1.3.0"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json")
E = {"REMOTE_ADDR": "127.0.0.1"}
V130_SCHEMA = 7
DIRS = ("galleries", "galleries-kept", "host", "web", "photos")
HOST, EARLIER = EXPECTED["hosts"]["current"], EXPECTED["hosts"]["earlier"][0]
BY = EXPECTED["sessions_by_name"]
YEAR = EXPECTED["year"]

def _place(tmp_path):

    shutil.copyfile(FIXTURE / "crm.db", tmp_path / "crm.db")
    for name in SIDE_FILES:
        shutil.copyfile(FIXTURE / name, tmp_path / name)
    for name in DIRS:
        shutil.copytree(FIXTURE / name, tmp_path / name)
    assert EXPECTED["storage_keys"] == ["gallery_host", "gallery_hosts_prev", "primary"]
    (tmp_path / "storage.json").write_text(json.dumps({
        "primary": {"type": "local", "path": str(tmp_path / "vault")},
        "gallery_host": dict(EXPECTED["gallery_host"], path=str(tmp_path / HOST)),
        "gallery_hosts_prev": [{"type": "local", "path": str(tmp_path / EARLIER)}]}))

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
                outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["outstanding"]},
                upcoming={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["upcoming"]})

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def _gallery_rows(A):
    cols = tuple(EXPECTED["galleries_130"]["1"])
    return {str(r[0]): dict(zip(cols, r[1:])) for r in _q(A, f"SELECT session_id, {', '.join(cols)} FROM galleries")}

def _folder(A, sid):

    gid, was = _q(A, "SELECT s.gallery_id, g.link_was FROM galleries g JOIN sessions s ON s.id=g.session_id WHERE s.id=?",
                  (sid,))[0]
    return was or gid

def _on_host(A, tmp_path):
    out = {}
    for (sid,) in _q(A, "SELECT session_id FROM galleries"):
        gid = _folder(A, sid)
        out[str(sid)] = {w: sorted(p.name for p in (tmp_path / w / gid).iterdir()) if (tmp_path / w / gid).is_dir() else None
                         for w in (HOST, EARLIER)}
    return out

@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    _place(tmp_path)
    return _open(tmp_path, monkeypatch) + (tmp_path,)

def test_the_fixture_is_a_1_3_0_database():
    assert EXPECTED["schema_version"] == V130_SCHEMA and EXPECTED["made_with"].startswith("1.3.0")
    assert EXPECTED["app_version"] == "1.3.0"
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

def test_the_schema_stays_at_7_and_no_backup_is_needed(upgraded):
    A, c, taken, _ = upgraded
    assert A.SCHEMA_VERSION >= V130_SCHEMA
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert taken == (["upgrade"] if A.SCHEMA_VERSION > V130_SCHEMA else [])

def test_today_says_once_that_it_was_updated(tmp_path, monkeypatch):
    import about
    now = about.version()
    if now == EXPECTED["app_version"]:
        now = "1.3.1"
    _place(tmp_path)
    A, c, taken = _open(tmp_path, monkeypatch, version=now)
    assert _q(A, "SELECT value FROM settings WHERE key='app_version'")[0][0] == now
    first, second = _page(c, "/"), _page(c, "/")
    assert f"Updated to Lumen Aureum {now}." in first
    if A.SCHEMA_VERSION == V130_SCHEMA:
        assert taken == [] and "Your data didn't need changes, so no backup was made." in first
    assert "Updated to Lumen Aureum" not in second

def test_every_record_is_still_there(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, name FROM households")} == EXPECTED["clients"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == {
        k: v["stage"] for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, lost_reason FROM sessions WHERE stage='lost'")} == EXPECTED["lost"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, tax_rate FROM sessions")} == EXPECTED["tax_rates"]
    for sid in EXPECTED["sessions"]:
        assert c.get(f"/session/{sid}", environ_base=E).status_code == 200
    for hid in EXPECTED["clients"]:
        assert c.get(f"/c/{hid}", environ_base=E).status_code == 200

def test_payments_tips_and_their_tax_are_as_1_3_0_kept_them(upgraded):

    A, c, taken, _ = upgraded
    got = {str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4], tax=r[5], taxable=r[6], voided=r[7])
           for r in _q(A, "SELECT id, session_id, kind, amount, received, tax, taxable, voided FROM payments")}
    assert got == EXPECTED["payments_kept"]
    tips = {k for k, v in EXPECTED["payments_kept"].items() if v["kind"] == "tip"}
    assert len(tips) == 2 and all(EXPECTED["payments_kept"][k]["tax"] == 0.0 for k in tips)
    assert {v["amount"] for k, v in EXPECTED["payments_kept"].items() if k in tips} == {50.0, -10.0}

    _in_request(A, lambda: A._fill_payment_taxes())
    _in_request(A, lambda: A._fill_payment_taxes(BY["ellis"]))
    assert {str(r[0]) for r in _q(A, "SELECT id FROM payments WHERE kind='tip'")} == tips
    assert [list(r) for r in _q(A, "SELECT id, received, amount, tax, type, kind FROM books ORDER BY id")] == EXPECTED["books"]
    assert [r[0] for r in _q(A, "SELECT kind FROM books ORDER BY id")].count("tip") == 1
    t = _in_request(A, lambda: A.totals(BY["ellis"]))
    assert round(t["tips"], 2) == EXPECTED["money"][str(BY["ellis"])]["tips"] == 40.0
    assert _in_request(A, lambda: A.year_money(YEAR))["tips"] == EXPECTED["year_money"]["tips"] == 60.0

def test_balances_match_1_3_0(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): dict(session=r[1], amount=r[2]) for r in
            _q(A, "SELECT payment_id, session_id, amount FROM refund_credits")} == EXPECTED["refund_credits"]
    for sid, want in EXPECTED["money"].items():
        t = _in_request(A, lambda: A.totals(int(sid)))
        assert {k: round(t[k] or 0.0, 2) for k in want} == want, sid
    grant, foster = EXPECTED["money"][str(BY["grant"])], EXPECTED["money"][str(BY["foster"])]
    assert grant["kept"] > 0 and foster["written_off"] == 162.0

def test_finances_and_the_export_match_1_3_0(upgraded):
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
    r = c.get(f"/money/accounts.zip?year={YEAR}", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert {n: list(csv.reader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in sorted(z.namelist())} \
        == EXPECTED["export"]

def test_statements_and_the_charges_they_are_made_of(upgraded):

    A, c, taken, _ = upgraded
    assert [list(r) for r in _q(A, "SELECT id, session_id, at, amount, number, label FROM charges ORDER BY id")] \
        == EXPECTED["charges"]
    assert [list(r) for r in _q(A, "SELECT id, session_id, amount, at, why FROM write_offs ORDER BY id")] == EXPECTED["write_offs"]
    assert [list(r) for r in _q(A, "SELECT id, session_id, at, tax, taxable, why FROM tax_adjustments ORDER BY id")] \
        == EXPECTED["tax_adjustments"]
    assert len([ch for ch in EXPECTED["charges"] if ch[1] == BY["grant"]]) == 3
    r2 = lambda v: round(v or 0.0, 2)
    for key, want in EXPECTED["statements"].items():
        hid, month = key.split()
        v = _in_request(A, lambda: A.statement_ctx(int(hid), month))
        got = dict(opening=r2(v["opening"]), closing=r2(v["closing"]),
                   invoices=[[i["date"], i["number"], r2(i["amount"])] for i in v["invoices"]],
                   payments=[[p["date"], p["kind"], r2(p["amount"])] for p in v["payments"]])
        assert got == want, key

def test_issued_documents_are_the_ones_1_3_0_kept(upgraded):
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

def test_drafts_stay_unnumbered_and_the_next_numbers_follow(upgraded):
    A, c, taken, _ = upgraded
    ortiz, bell = BY["ortiz"], BY["bell"]
    assert not _q(A, "SELECT 1 FROM doc_numbers WHERE session_id IN (?, ?)", (ortiz, bell))
    for sid, kind, pre in ((ortiz, "invoice", "INV"), (bell, "receipt", "R")):
        assert c.post(f"/session/{sid}/{kind}/issue", environ_base=E).status_code in (302, 303)
        new = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, pre))[0][0]
        same_year = [int(n[-4:]) for n, d in EXPECTED["doc_numbers"].items() if d["kind"] == pre and n[:-4] == new[:-4]]
        assert new not in EXPECTED["doc_numbers"] and int(new[-4:]) == max(same_year, default=0) + 1

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

def test_the_master_key_check_is_remembered(upgraded):
    A, c, taken, _ = upgraded
    assert EXPECTED["master_checked"]
    assert _q(A, "SELECT value FROM settings WHERE key='master_checked'")[0][0] == EXPECTED["master_checked"]
    assert c.get("/security/master-key", environ_base=E).status_code == 200

def _opened(G, key, blob, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + name).encode())

def _manifest(A, G, tmp_path, sid):
    gid = _folder(A, sid)
    key, ekey = _q(A, "SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))[0]
    blob = (tmp_path / HOST / gid / "manifest.bin").read_bytes()
    return gid, ekey or key, blob, json.loads(_opened(G, ekey or key, blob, "manifest.bin"))

def test_every_gallery_and_its_host_record_is_as_1_3_0_left_it(upgraded):

    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    assert G.host_fingerprint() == EXPECTED["hosts"]["current_fp"]
    assert [G.fingerprint_of(h) for h in G.earlier_host_cfgs()] == EXPECTED["hosts"]["earlier_fp"]
    assert _gallery_rows(A) == EXPECTED["galleries_130"]
    assert _on_host(A, tmp_path) == EXPECTED["on_host"]
    assert [list(r) for r in _q(A, "SELECT gallery_id, host_fp, added FROM gallery_leftovers ORDER BY gallery_id")] \
        == EXPECTED["leftovers"]
    g = EXPECTED["galleries_130"]
    lane, hale, ivers = str(BY["lane"]), str(BY["hale"]), str(BY["ivers"])
    assert g[lane]["moved_from"] == EXPECTED["hosts"]["earlier_fp"][0] and g[lane]["host_fp"] == EXPECTED["hosts"]["current_fp"]
    assert EXPECTED["on_host"][lane][EARLIER] and "picks.bin" in EXPECTED["on_host"][lane][HOST]
    assert g[hale]["dark_at"] and g[hale]["upload_state"] == "removed" and EXPECTED["on_host"][hale][HOST] == ["index.html"]
    assert g[ivers]["restored_at"] and g[ivers]["restore_days"] == 14 and g[ivers]["upload_state"] == "up"
    hale_gid = _folder(A, BY["hale"])
    page = (tmp_path / HOST / hale_gid / "index.html").read_bytes()
    assert hashlib.sha256(page).hexdigest() == EXPECTED["dark_page_sha256"] and b"This gallery has gone dark." in page
    assert {r[0]: r[1] for r in _q(A, "SELECT id, retouch_picks FROM sessions WHERE id=?", (BY["lane"],))} \
        == {BY["lane"]: EXPECTED["gallery"]["retouch_picks"]}
    move = _page(c, "/galleries/move")
    assert "Nothing: every gallery is on the web host set up now." in move
    assert "needs the new link" not in _page(c, "/") and "still on your earlier web host" not in _page(c, "/")
    for sid in (lane, ivers):
        link = _in_request(A, lambda: A.app.config["gallery_link"](int(sid)))
        assert link.startswith(EXPECTED["gallery_base_url"])
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (BY["hale"],))[0][0]
    assert "Put back online" in _page(c, f"/c/{hid}?s={BY['hale']}")

def test_the_1_2_galleries_picks_and_download_galleries_survive(upgraded):
    A, c, taken, tmp_path = upgraded
    from plugins import gallery as G
    want = EXPECTED["gallery"]
    sid = want["session"]
    cols = ("key", "ekey", "n_files", "built_at", "cw_fixed", "upload_state", "uploaded_at", "removed_at",
            "draft_n", "draft_at", "picks_hash", "limits_days")
    assert dict(zip(cols, _q(A, f"SELECT {', '.join(cols)} FROM galleries WHERE session_id=?", (sid,))[0])) \
        == {k: want[k] for k in cols}
    got = {str(r[0]): dict(proof=r[1], prune_due=r[2], upload_state=r[3], n_files=r[4], built_at=r[5]) for r in
           _q(A, "SELECT session_id, proof, prune_due, upload_state, n_files, built_at FROM galleries")}
    assert got == EXPECTED["galleries"]
    for sid_, want_ in EXPECTED["download_galleries"].items():
        gid, key, blob, m = _manifest(A, G, tmp_path, int(sid_))
        assert gid == want_["gallery_id"] and G.is_download(gid) and G.online_download(gid)
        assert hashlib.sha256(blob).hexdigest() == want_["manifest_sha256"]
        assert [dict(name=z["name"], size=z["size"], parts=z["parts"]) for z in m["zips"]] == want_["zips"]
        assert sorted(p.name for p in (tmp_path / HOST / gid).iterdir()) == want_["host_files"]
        fulls = {it["on"]: _opened(G, key, (tmp_path / HOST / gid / it["o"]).read_bytes(), it["o"]) for it in m["items"]}
        for z in m["zips"]:
            whole = b"".join(_opened(G, key, (tmp_path / HOST / gid / p).read_bytes(), p) for p in z["parts"])
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

def test_a_gallery_check_changes_nothing(upgraded, monkeypatch):

    A, c, taken, tmp_path = upgraded
    monkeypatch.setitem(A.app.config, "clock_ok", lambda: False)
    before = _on_host(A, tmp_path)
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    rows = _gallery_rows(A)
    keep = ("upload_state", "removed_at", "dark_at", "restored_at", "restore_days", "restore_once", "host_fp",
            "moved_from", "link_base", "n_files", "built_at")
    assert {s: {k: v[k] for k in keep} for s, v in rows.items()} == \
        {s: {k: v[k] for k in keep} for s, v in EXPECTED["galleries_130"].items()}
    after = _on_host(A, tmp_path)
    assert after == before == EXPECTED["on_host"]
    diaz = BY["diaz"]
    gid = EXPECTED["download_galleries"][str(diaz)]["gallery_id"]
    (tmp_path / HOST / gid / "seen.txt").write_text(date.today().isoformat() + "T12:00:00Z")
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    sd, fv = _q(A, "SELECT sd_shipped, first_visit FROM sessions WHERE id=?", (diaz,))[0]
    assert sd and fv and abs((date.fromisoformat(sd) - date.today()).days) <= 1

def test_the_studio_page_its_files_and_inquiries_are_kept(upgraded):
    A, c, taken, tmp_path = upgraded
    got = {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key LIKE 'site\\_%' ESCAPE '\\'"
                                     " AND key NOT IN ('site_inquiry_key')")}

    assert got.pop("site_left", "{}") == "{}" and got == EXPECTED["site_settings"]
    assert hashlib.sha256(_q(A, "SELECT value FROM settings WHERE key='site_inquiry_key'")[0][0].encode()).hexdigest() \
        == EXPECTED["site_key_sha256"]
    assert [dict(id=r[0], source=r[1], name=r[2], data_sha256=hashlib.sha256(r[3]).hexdigest() if r[3] else None)
            for r in _q(A, "SELECT id, source, name, data FROM studio_photos ORDER BY sort, id")] == EXPECTED["studio_photos"]
    files = [list(r) for r in _q(A, "SELECT name, host_fp, size, sha256 FROM studio_files ORDER BY name")]
    assert files == EXPECTED["studio_files"]
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
    page = _page(c, "/settings/studio-page")
    assert "Website inquiries to review" in page and "Grace Hopper" in page
    assert "1 website inquiry to review" in _page(c, "/")

    rid = _q(A, "SELECT id FROM studio_inquiry_review")[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": str(rid)}, environ_base=E)
    assert not _q(A, "SELECT 1 FROM studio_inquiry_review")
    assert _q(A, "SELECT source FROM households WHERE name='Grace Hopper'")[0][0] == "Website"
