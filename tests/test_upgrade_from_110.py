import hashlib
import hmac
import json
import shutil
import sys
from pathlib import Path

import pytest

from helpers import fresh_modules, signed_in_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from make_fixture import doc_text

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "db-1.1.0"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json")
E = {"REMOTE_ADDR": "127.0.0.1"}
V110_SCHEMA = 4

def _place(tmp_path, side_files=SIDE_FILES):

    shutil.copyfile(FIXTURE / "crm.db", tmp_path / "crm.db")
    for name in side_files:
        if (FIXTURE / name).exists():
            shutil.copyfile(FIXTURE / name, tmp_path / name)

def _open(tmp_path, monkeypatch):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
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
                outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["outstanding"]},
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

def test_the_fixture_is_a_1_1_0_database():
    assert EXPECTED["schema_version"] == V110_SCHEMA and EXPECTED["made_with"].startswith("1.1.0")

def test_the_upgrade_takes_a_backup_and_moves_to_schema_5(upgraded):
    A, c, taken, _ = upgraded
    assert A.SCHEMA_VERSION >= 5
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert taken == ["upgrade"]
    assert "html" in [r[1] for r in _q(A, "PRAGMA table_info(bill_snapshots)")]
    assert _q(A, "PRAGMA integrity_check")[0][0] == "ok"
    assert not _q(A, "PRAGMA foreign_key_check")

def test_every_record_is_still_there(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, name FROM households")} == EXPECTED["clients"]
    assert {str(r[0]): r[1] for r in _q(A, "SELECT id, stage FROM sessions")} == {
        k: v["stage"] for k, v in EXPECTED["sessions"].items()}
    assert {str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4]) for r in
            _q(A, "SELECT id, session_id, kind, amount, received FROM payments")} == EXPECTED["payments"]
    ev = EXPECTED["event"]
    assert [r[0] for r in _q(A, """SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id
                                   WHERE s.event_id=? ORDER BY h.name""", (ev["id"],))] == ev["families"]
    for sid in EXPECTED["sessions"]:
        assert c.get(f"/session/{sid}", environ_base=E).status_code == 200

def test_balances_and_refund_credits_match_1_1_0(upgraded):
    A, c, taken, _ = upgraded
    assert {str(r[0]): dict(session=r[1], amount=r[2]) for r in
            _q(A, "SELECT payment_id, session_id, amount FROM refund_credits")} == EXPECTED["refund_credits"]
    for sid, want in EXPECTED["sessions"].items():
        t = _in_request(A, lambda: A.totals(int(sid)))
        assert round(t["quoted"], 2) == want["quoted"], sid
        assert round(t["paid"], 2) == want["paid"], sid
        assert round(t["due"], 2) == want["due"], sid
        assert round(t.get("refund_credit", 0.0), 2) == want["refund_credit"], sid

def test_finances_match_1_1_0(upgraded):
    A, c, taken, _ = upgraded
    assert _finances(A, c, EXPECTED["year"]) == EXPECTED["finances"]

def test_issued_documents_are_frozen_as_1_1_0_showed_them(upgraded):

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
    assert not _q(A, "SELECT number FROM bill_snapshots WHERE html IS NULL")
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

def test_frozen_documents_keep_their_words_when_the_wording_changes(upgraded):
    A, c, taken, _ = upgraded
    number, want = next(iter(sorted(EXPECTED["issued_text"].items())))
    sid, kind = _q(A, "SELECT session_id, kind FROM bill_snapshots WHERE number=?", (number,))[0]
    tpl = Path(A.__file__).resolve().parent / "templates" / "_bill_doc.html"
    real = tpl.read_text()
    try:
        tpl.write_text(real.replace("Questions about this", "Any questions on this"))
        if A.app.jinja_env.cache is not None:
            A.app.jinja_env.cache.clear()
        assert doc_text(_page(c, f"/session/{sid}/{'receipt' if kind == 'R' else 'invoice'}")) == want
    finally:
        tpl.write_text(real)
        if A.app.jinja_env.cache is not None:
            A.app.jinja_env.cache.clear()

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

def test_the_gallery_its_picks_and_its_draft_survive(upgraded):
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
    assert want["still_picking"][0] in _page(c, "/")

def test_the_gallery_look_logos_and_time_zone_are_kept(upgraded):
    A, c, taken, _ = upgraded
    keys = tuple(EXPECTED["settings"])
    assert {r[0]: r[1] for r in _q(A, "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(keys)),
                                    keys)} == EXPECTED["settings"]
    for k, digest in EXPECTED["logos"].items():
        v = _q(A, "SELECT value FROM settings WHERE key=?", (k,))
        assert v and hashlib.sha256(v[0][0].encode()).hexdigest() == digest, k
    import settings_io
    look = settings_io.clean_look(*(EXPECTED["settings"][k] for k in ("gallery_font", "gallery_bg", "proof_mark",
                                                                     "proof_mark_text", "proof_mark_strength")))
    assert {k: look[k] for k in look} == {k: EXPECTED["settings"][k] for k in look}
    page = _page(c, "/settings")
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

def test_older_code_refuses_a_newer_database(upgraded, monkeypatch):
    A, c, taken, _ = upgraded
    monkeypatch.setattr(A, "SCHEMA_VERSION", A.SCHEMA_VERSION - 1)
    with pytest.raises(A.SchemaTooNew):
        A.init_db()
