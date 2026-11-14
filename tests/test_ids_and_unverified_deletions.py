"""1.0.1: session and client numbers are never handed out again after a delete (N12), so a tab left open on a deleted
session or client can't save into a new, unrelated one; it gets a "This session was deleted" page instead. And
deletions that could not be confirmed are listed on Today for the owner to apply or keep,
never applied on their own."""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E  # noqa: E402

NOTICE = "These clients were deleted earlier, but this copy of your data could not confirm it. If you deleted them, apply the deletions."


def _restore(A, snap):
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)


def _contact(A, c, name):
    c.post("/contact/new", data={"contact_name": name}, environ_base=E)
    return _q(A, "SELECT id, created FROM households WHERE name=?", (name,))[0]


# ---------------------------------------------------------------- N12: ids never reused

def test_a_deleted_top_session_id_is_not_reused(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _new(A, c, "family", subject_name="Ava")
    top = _new(A, c, "family", subject_name="Ben")
    c.post(f"/session/{top}/delete", environ_base=E)
    assert not _q(A, "SELECT 1 FROM sessions WHERE id=?", (top,))
    fresh = _new(A, c, "family", subject_name="Cal")
    assert fresh == top + 1


def test_a_stale_tab_on_a_deleted_session_cannot_post_into_a_new_one(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    old = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{old}/delete", environ_base=E)
    new = _new(A, c, "family", subject_name="Ben")
    assert new != old
    before = _q(A, "SELECT title, notes FROM sessions WHERE id=?", (new,))[0]
    r = c.post(f"/session/{old}", data={"title": "From the old tab", "type": "family", "notes": "stale"}, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 410 and "This session was deleted" in page
    assert tuple(_q(A, "SELECT title, notes FROM sessions WHERE id=?", (new,))[0]) == tuple(before)
    r = c.post(f"/session/{old}/money", data={"what": "line", "label": "x", "amount": "5"}, environ_base=E)
    assert r.status_code == 410 and not _q(A, "SELECT 1 FROM lines WHERE session_id=?", (old,))
    r = c.post(f"/session/{old}/task", data={"title": "Call back"}, environ_base=E)
    assert r.status_code == 410
    r = c.post("/client/1/note", data={"text": "from a stale tab", "session_id": str(old)}, environ_base=E)
    assert r.status_code == 410 and not _q(A, "SELECT 1 FROM log WHERE summary='from a stale tab'")
    assert c.get(f"/session/{old}", environ_base=E).status_code == 410
    assert c.post("/session/99999/money", data={"what": "line"}, environ_base=E).status_code == 404   # never issued


def test_a_refused_post_to_a_deleted_session_leaves_no_history(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    old = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{old}/delete", environ_base=E)
    n = _q(A, "SELECT COUNT(*) FROM audit")[0][0]
    c.post(f"/session/{old}/delivery", data={"favorites": "1"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM audit")[0][0] == n


def test_client_ids_are_not_reused_and_stale_client_tabs_are_refused(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, _ = _contact(A, c, "Harlow")
    c.post(f"/client/{hid}/delete", data={"confirm": "DELETE"}, environ_base=E)
    assert not _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    hid2, _ = _contact(A, c, "Okafor")
    assert hid2 == hid + 1
    r = c.post(f"/client/{hid}", data={"name": "Stale"}, environ_base=E)
    assert r.status_code == 410 and "This client was deleted" in r.get_data(as_text=True)
    r = c.post(f"/client/{hid}/note", data={"text": "stale note"}, environ_base=E)
    assert r.status_code == 410 and not _q(A, "SELECT 1 FROM log WHERE summary='stale note'")
    assert _q(A, "SELECT name FROM households WHERE id=?", (hid2,))[0][0] == "Okafor"


def test_ids_stay_unique_after_restoring_an_older_backup(tmp_path, monkeypatch):
    """The mark beside the database outlives a restore: a session made after the backup is not reissued."""
    A, c = _app(tmp_path, monkeypatch)
    import db
    _new(A, c, "family", subject_name="Ava")
    snap = tmp_path / "before.db"
    db.snapshot(snap)
    later = _new(A, c, "family", subject_name="Ben")
    _restore(A, snap)
    assert not _q(A, "SELECT 1 FROM sessions WHERE id=?", (later,))
    assert _new(A, c, "family", subject_name="Cal") == later + 1


def test_ids_stay_unique_without_the_side_file(tmp_path, monkeypatch):
    """The mark kept inside the database is enough on its own (a data folder rebuilt without doc-numbers.json)."""
    A, c = _app(tmp_path, monkeypatch)
    top = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{top}/delete", environ_base=E)
    A.NUMBERS_JSON.unlink()
    assert _new(A, c, "family", subject_name="Ben") == top + 1


def test_marks_are_seeded_at_unlock_from_history_and_deletions(tmp_path, monkeypatch):
    """An install upgraded from 1.0.0 has no marks: they start above every id the History and deletions list name."""
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("INSERT INTO audit (at, session_id, action) VALUES ('2026-01-01 00:00:00', 40, 'Deleted the session')")
    con.commit()
    con.close()
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=70, created="2025-01-01 00:00:00", at="2025-02-01 00:00:00")]))
    A.NUMBERS_JSON.unlink(missing_ok=True)
    with A.app.test_request_context():
        A.db().execute("DELETE FROM id_marks")
        A._seed_id_marks()
        A.db().commit()
        assert A.id_mark("sessions") == 40 and A.id_mark("households") == 70
        assert A.next_id("sessions") == 41 and A.next_id("households") == 71


def test_marks_are_part_of_the_schema(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _new(A, c, "family", subject_name="Ava")
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION
    assert _q(A, "SELECT 1 FROM sqlite_master WHERE name='id_marks'")       # made by init_db, not on first use


# ---------------------------------------------------------------- unconfirmed deletions

def _aside(A, items):
    A.DELETIONS_ASIDE.write_text(json.dumps(items))


def test_unconfirmed_deletions_are_listed_on_today_and_never_applied(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=hid, created=created, at="2026-03-02 10:00:00", mac="0" * 64)]))
    assert A.reapply_deletions() == 0                       # signed under a key this copy doesn't have: set aside
    assert _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Harlow Family" in today and "Review and decide" in today
    page = c.get("/deletions/unconfirmed", environ_base=E).get_data(as_text=True)
    assert NOTICE in page and "Harlow Family" in page and "Apply these deletions" in page and "Keep them" in page
    assert "deleted on" in page
    for _ in range(3):                                      # looking, and signing in again, never applies them
        A.reapply_deletions()
        c.get("/", environ_base=E)
    assert _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))


def test_apply_goes_through_the_normal_delete_and_signs_it(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    other, oc = _contact(A, c, "Okafor Family")
    _aside(A, [dict(id=hid, created=created, at="2026-03-02 10:00:00", mac="0" * 64),
               dict(id=other, created=oc, at=None)])          # a forged MAC, and an unsigned 1.0.0 entry
    r = c.post("/deletions/unconfirmed/apply", data={"pick": [f"{hid}|{created}", f"{other}|{oc}"]}, environ_base=E)
    assert r.status_code == 302
    assert not _q(A, "SELECT 1 FROM households WHERE id IN (?,?)", (hid, other))
    live = json.loads(A.DELETIONS_JSON.read_text())
    assert {d_["id"] for d_ in live} == {hid, other} and all(len(d_["mac"]) == 64 for d_ in live)
    with A.app.test_request_context():
        key, good, legacy, forged = A._sort_deletions(A.db())
        assert {d_["id"] for d_ in good} == {hid, other} and not forged
    assert any("Deleted a client record" in a for (a,) in _q(A, "SELECT action FROM audit"))
    assert "Review and decide" not in c.get("/", environ_base=E).get_data(as_text=True)


def test_apply_only_touches_what_the_page_listed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    bystander, bc = _contact(A, c, "Okafor Family")
    _aside(A, [dict(id=hid, created=created, mac="0" * 64)])
    c.post("/deletions/unconfirmed/apply", data={"pick": [f"{bystander}|{bc}", f"{hid}|wrong"]}, environ_base=E)
    assert _q(A, "SELECT 1 FROM households WHERE id=?", (hid,)) and _q(A, "SELECT 1 FROM households WHERE id=?", (bystander,))


def test_keep_dismisses_without_deleting(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    _aside(A, [dict(id=hid, created=created, at="2026-03-02 10:00:00", mac="0" * 64)])
    r = c.post("/deletions/unconfirmed/keep", data={"pick": f"{hid}|{created}"}, environ_base=E)
    assert r.status_code == 302 and _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    assert json.loads(A.DELETIONS_ASIDE.read_text())[0]["dismissed"]
    assert "Review and decide" not in c.get("/", environ_base=E).get_data(as_text=True)
    assert "Nothing to confirm" in c.get("/deletions/unconfirmed", environ_base=E).get_data(as_text=True)
    A.reapply_deletions()                                   # the next sign-in doesn't bring it back
    assert "Review and decide" not in c.get("/", environ_base=E).get_data(as_text=True)
    assert any("Kept clients named in deletions" in a for (a,) in _q(A, "SELECT action FROM audit"))


def test_entries_for_clients_already_gone_or_reused_ids_are_not_listed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    _aside(A, [dict(id=hid, created="1999-01-01 00:00:00", mac="0" * 64),     # same number, a different client
               dict(id=hid + 50, created=created, mac="0" * 64), "junk", {"id": "x"}])
    assert "Review and decide" not in c.get("/", environ_base=E).get_data(as_text=True)
    with A.app.test_request_context():
        A.db()
        assert A.unconfirmed_deletions() == []


def test_unsigned_legacy_entries_flow_into_the_notice(tmp_path, monkeypatch):
    """A 1.0.0 entry whose client is still here is set aside by reapply_deletions, and shows on Today the same way."""
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    A.DELETIONS_JSON.write_text(json.dumps([dict(id=hid, created=created, at="2026-01-05 09:00:00")]))
    assert A.reapply_deletions() == 0
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Harlow Family" in today and "Review and decide" in today
    c.post("/deletions/unconfirmed/apply", data={"pick": f"{hid}|{created}"}, environ_base=E)
    assert not _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))


def test_the_actions_need_a_signed_in_same_origin_post(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, created = _contact(A, c, "Harlow Family")
    _aside(A, [dict(id=hid, created=created, mac="0" * 64)])
    r = c.post("/deletions/unconfirmed/apply", data={"pick": f"{hid}|{created}"}, environ_base=E,
               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    with c.session_transaction() as s:
        s.clear()
    r = c.post("/deletions/unconfirmed/apply", data={"pick": f"{hid}|{created}"}, environ_base=E)
    assert r.status_code in (302, 401, 403) and _q(A, "SELECT 1 FROM households WHERE id=?", (hid,))
    assert c.get("/deletions/unconfirmed", environ_base=E).status_code != 200
