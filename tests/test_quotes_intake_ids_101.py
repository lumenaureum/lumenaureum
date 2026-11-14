"""1.0.1 follow-ups from user testing: the intake form and the other several-field forms refuse a stale tab;
Prepare quote is named for what it does, never numbers an unchanged quote twice, and answers a stale tab in that tab; a revised quote is not "Sent" until marked; Add with no line picked says so; task and
event ids are never reused, and a deleted one gets the "gone" page (N12 follow-up); a deleted session's History is
labeled on its replacement."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E  # noqa: E402

CLASH = "was changed in another tab or window. Your edits were not saved; reload to see the latest."
INTAKE = {"who": "", "interests": "", "looks": "", "outfits": "", "must": "", "avoid": "", "props": ""}


def _stamp(page, action):
    """The fingerprint carried by the form posting to `action` on this page."""
    i = page.index(f'action="{action}"')
    form = page[i:page.index("</form>", i)]
    return re.search(r'name="seen" value="([0-9a-f]{20})"', form).group(1)


def _line(c, sid, amount="300"):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": amount}, environ_base=E)


def _stage(c, sid):
    return c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)


# ---------------------------------------------------------------- the intake form, and the other forms

def test_a_stale_intake_tab_is_refused_on_the_stage_page(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    act = f"/session/{sid}/intake"
    tab_a, tab_b = _stamp(_stage(c, sid), act), _stamp(_stage(c, sid), act)
    back = f"/c/1?s={sid}"
    r = c.post(act, data={**INTAKE, "seen": tab_a, "who": "Ava and Ben", "must": "Vineyard 44 - terrace", "back": back},
               environ_base=E)
    assert r.status_code == 302
    r = c.post(act, data={**INTAKE, "seen": tab_b, "who": "Ava and Ben", "must": "Vineyard 44", "looks": "candid",
                          "back": back}, environ_base=E)
    page = r.get_data(as_text=True)
    assert r.status_code == 409 and "This session " + CLASH in page
    assert "candid" in page                                         # what was typed is listed, not lost
    saved = json.loads(_q(A, "SELECT intake FROM sessions WHERE id=?", (sid,))[0][0])
    assert saved["must"] == "Vineyard 44 - terrace" and saved["looks"] == ""   # tab A's save stands
    assert 'value="Vineyard 44 - terrace"' in page                  # the form shows the latest
    assert f'href="/c/1?s={sid}"' in page                           # Reload goes to a page, not to the save address
    r = c.post(act, data={**INTAKE, "seen": _stamp(page, act), "must": "Vineyard 44 - terrace", "looks": "candid",
                          "back": back}, environ_base=E)
    assert r.status_code == 302


def test_a_stale_intake_tab_on_the_session_page_and_a_fresh_one(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    act = f"/session/{sid}/intake"
    old = _stamp(c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True), act)
    assert c.post(act, data={**INTAKE, "seen": old, "who": "Ava"}, environ_base=E).status_code == 302
    r = c.post(act, data={**INTAKE, "seen": old, "who": "Ava, Ben"}, environ_base=E)
    assert r.status_code == 409 and "This session " + CLASH in r.get_data(as_text=True)
    assert c.post(act, data={**INTAKE, "who": "No stamp: an older page"}, environ_base=E).status_code == 302


def test_the_delivery_form_refuses_a_stale_tab(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    con = A.connect()
    con.execute("UPDATE sessions SET stage='shot', shoot_date='2026-01-10' WHERE id=?", (sid,))
    con.commit()
    con.close()
    act = f"/session/{sid}/delivery"
    old = _stamp(c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True), act)
    r = c.post(act, data={"seen": old, "retouch_picks": "12 brighten, 40 crop"}, environ_base=E)
    assert r.status_code == 302
    r = c.post(act, data={"seen": old, "retouch_picks": "", "favorites": "7"}, environ_base=E)
    assert r.status_code == 409 and "This session " + CLASH in r.get_data(as_text=True)
    assert _q(A, "SELECT retouch_picks, favorites FROM sessions WHERE id=?", (sid,))[0][:] == ("12 brighten, 40 crop", "")


def test_a_person_row_and_event_details_refuse_a_stale_tab(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    pid = _q(A, "SELECT id FROM people WHERE household_id=1")[0][0]
    page = c.get("/client/1", environ_base=E).get_data(as_text=True)
    old = re.search(r'value="([0-9a-f]{20})" name="seen"', page).group(1)
    row = {"name": "Avery Lane", "relation": "", "email": "", "phone": "", "minor_shown": "1"}
    assert c.post(f"/person/{pid}", data={**row, "seen": old, "email": "avery@example.com"}, environ_base=E).status_code == 302
    r = c.post(f"/person/{pid}", data={**row, "seen": old, "phone": "555-0100"}, environ_base=E)
    assert r.status_code == 409 and "This person " + CLASH in r.get_data(as_text=True)
    assert tuple(_q(A, "SELECT email, phone FROM people WHERE id=?", (pid,))[0]) == ("avery@example.com", "")

    c.post("/events", data={"name": "Team day", "date": "2027-05-01", "venue": "Field 2"}, environ_base=E)
    eid = _q(A, "SELECT MAX(id) FROM events")[0][0]
    old = _stamp(c.get(f"/event/{eid}", environ_base=E).get_data(as_text=True), f"/event/{eid}/edit")
    ev = {"name": "Team day", "date": "2027-05-01", "venue": "Field 2", "notes": ""}
    assert c.post(f"/event/{eid}/edit", data={**ev, "seen": old, "venue": "Field 2, north gate"}, environ_base=E).status_code == 302
    r = c.post(f"/event/{eid}/edit", data={**ev, "seen": old, "notes": "bring the ladder"}, environ_base=E)
    assert r.status_code == 409 and "This event " + CLASH in r.get_data(as_text=True)
    assert tuple(_q(A, "SELECT venue, notes FROM events WHERE id=?", (eid,))[0]) == ("Field 2, north gate", "")


def test_the_to_do_picks_form_and_the_email_from_a_document_never_overwrite(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    with A.app.test_request_context():
        A.db()
        old = A.edit_stamp("picks", sid)
    pick = {"act": "favorites", "back": f"/c/1?s={sid}", "veto": ""}
    assert c.post(f"/session/{sid}/quick", data={**pick, "seen": old, "favorites": "3, 9"}, environ_base=E).status_code == 302
    r = c.post(f"/session/{sid}/quick", data={**pick, "seen": old, "favorites": "4"}, environ_base=E)
    assert r.status_code == 409 and "4" in r.get_data(as_text=True)
    assert _q(A, "SELECT favorites FROM sessions WHERE id=?", (sid,))[0][0] == "3, 9"
    # "No email on file: add one" on a document left open: an email added since is not replaced
    con = A.connect()
    con.execute("UPDATE people SET email='avery@example.com' WHERE household_id=1")
    con.commit()
    con.close()
    c.post(f"/session/{sid}/contact-email", data={"email": "typo@example.com"}, environ_base=E)
    assert _q(A, "SELECT email FROM people WHERE household_id=1")[0][0] == "avery@example.com"


# ---------------------------------------------------------------- Prepare quote

def test_prepare_quote_is_named_for_what_it_does_and_never_numbers_twice(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid)
    page = _stage(c, sid)
    assert ">Prepare quote<" in page and "Send quote<" not in page and "Send updated quote" not in page
    r1 = c.post(f"/session/{sid}/quote/send", data={"total": "300.00"}, environ_base=E)
    r2 = c.post(f"/session/{sid}/quote/send", data={"total": "300.00"}, environ_base=E)    # a second press
    vids = [v[0] for v in _q(A, "SELECT id FROM quote_versions WHERE session_id=?", (sid,))]
    assert len(vids) == 1 and r1.location == r2.location and r2.location.endswith(f"/quote/{vids[0]}")
    assert len(_q(A, "SELECT 1 FROM audit WHERE route=?", (f"/session/{sid}/quote/send",))) == 1   # one History line
    page = _stage(c, sid)
    assert f">Open quote {vids[0]}<" in page and ">Prepare quote<" not in page
    _line(c, sid, "50")                                             # the quote changed: a new number, named as such
    assert ">Prepare updated quote<" in _stage(c, sid)
    c.post(f"/session/{sid}/quote/send", data={"total": "350.00"}, environ_base=E)
    assert len(_q(A, "SELECT id FROM quote_versions WHERE session_id=?", (sid,))) == 2
    assert json.loads((tmp_path / "doc-numbers.json").read_text())["Q"] == vids[0] + 1     # the Q mark still holds


def test_prepare_quote_from_the_page_answers_in_that_same_tab(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid)
    page = _stage(c, sid)
    i = page.index(f'action="/session/{sid}/quote/send"')
    assert 'target="_blank"' not in page[page.rindex("<form", 0, i):i]    # a refusal never opens a new tab
    H = {"X-Quote-Fetch": "1"}
    _line(c, sid, "50")                                                     # another tab added a line
    r = c.post(f"/session/{sid}/quote/send", data={"total": "300.00"}, headers=H, environ_base=E)
    assert r.status_code == 409 and r.get_json() == {"ok": False}
    assert not _q(A, "SELECT 1 FROM quote_versions WHERE session_id=?", (sid,))
    assert "The quote changed a moment ago (it is now $350" in _stage(c, sid)     # shown when the tab reloads
    r = c.post(f"/session/{sid}/quote/send", data={"total": "350.00"}, headers=H, environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions WHERE session_id=?", (sid,))[0][0]
    assert r.status_code == 200 and r.get_json() == {"ok": True, "url": f"/quote/{vid}"}


# ---------------------------------------------------------------- Sent belongs to each quote

def test_a_revised_quote_is_not_sent_until_marked(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid)
    c.post(f"/session/{sid}/quote/send", data={"total": "300.00"}, environ_base=E)
    v1 = _q(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    page = c.get(f"/quote/{v1}", environ_base=E).get_data(as_text=True)
    assert "Not marked sent yet." in page and f'action="/quote/{v1}/sent"' in page
    assert c.post(f"/quote/{v1}/sent", environ_base=E).status_code == 302
    assert _q(A, "SELECT done FROM tasks WHERE session_id=? AND title='Send quote'", (sid,))[0][0]   # the step closed
    assert ">Sent " in c.get(f"/quote/{v1}", environ_base=E).get_data(as_text=True)
    _line(c, sid, "50")
    c.post(f"/session/{sid}/quote/send", data={"total": "350.00"}, environ_base=E)
    v2 = _q(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    page = c.get(f"/quote/{v2}", environ_base=E).get_data(as_text=True)
    assert v2 != v1 and ">Sent " not in page and f'action="/quote/{v2}/sent"' in page
    c.post(f"/quote/{v2}/sent", environ_base=E)
    assert ">Sent " in c.get(f"/quote/{v2}", environ_base=E).get_data(as_text=True)
    assert len(_q(A, "SELECT 1 FROM audit WHERE action='Marked the quote sent'")) == 2


# ---------------------------------------------------------------- Add with nothing picked

def test_add_with_no_line_picked_says_so(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    page = _stage(c, sid)
    assert "Pick a line from the list first." in page                 # the inline message, shown by the page script
    r = c.post(f"/session/{sid}/quote/fee", data={"fee_key": "", "qty": "1", "back": f"/c/1?s={sid}"}, environ_base=E)
    assert r.status_code == 302 and not _q(A, "SELECT 1 FROM lines WHERE session_id=?", (sid,))
    assert "Pick a line from the list first, then press Add." in _stage(c, sid)


# ---------------------------------------------------------------- ids: tasks and events

def test_task_and_event_ids_are_never_reused_and_a_deleted_one_is_gone(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{sid}/task", data={"title": "Call the venue"}, environ_base=E)
    tid = _q(A, "SELECT MAX(id) FROM tasks")[0][0]
    assert c.post(f"/task/{tid}", data={"delete": "1"}, environ_base=E).status_code == 302
    c.post(f"/session/{sid}/task", data={"title": "Confirm parking"}, environ_base=E)
    assert _q(A, "SELECT MAX(id) FROM tasks")[0][0] == tid + 1
    r = c.post(f"/task/{tid}", data={"set": "1"}, environ_base=E)               # a stale tab ticks the deleted step
    assert r.status_code == 410 and "This step was deleted" in r.get_data(as_text=True)
    r = c.post("/close", data={"task": str(tid), "outcome": "done"}, environ_base=E)
    assert r.status_code == 410
    assert c.post("/task/999999", data={"set": "1"}, environ_base=E).status_code == 404   # never issued: plain 404

    c.post("/events", data={"name": "Team day", "date": "2027-05-01"}, environ_base=E)
    eid = _q(A, "SELECT MAX(id) FROM events")[0][0]
    con = A.connect()
    con.execute("DELETE FROM events WHERE id=?", (eid,))
    con.commit()
    con.close()
    r = c.post(f"/event/{eid}/edit", data={"name": "Old tab", "date": "2027-05-02"}, environ_base=E)
    assert r.status_code == 410 and "This event was deleted" in r.get_data(as_text=True)
    assert c.get(f"/event/{eid}", environ_base=E).status_code == 410
    c.post("/events", data={"name": "Spring day", "date": "2027-04-01"}, environ_base=E)
    assert _q(A, "SELECT MAX(id) FROM events")[0][0] == eid + 1
    marks = json.loads((tmp_path / "doc-numbers.json").read_text())
    assert marks["T"] >= tid + 1 and marks["E"] == eid + 1


def test_task_and_event_marks_are_seeded_at_unlock(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{sid}/task", data={"title": "Call the venue"}, environ_base=E)
    tid = _q(A, "SELECT MAX(id) FROM tasks")[0][0]
    c.post(f"/task/{tid}", data={"set": "1"}, environ_base=E)           # History names it by its address
    c.post(f"/task/{tid}", data={"delete": "1"}, environ_base=E)
    (tmp_path / "doc-numbers.json").write_text("{}")
    con = A.connect()
    con.execute("DELETE FROM id_marks")
    con.commit()
    con.close()
    with A.app.test_request_context():
        A.db()
        A._seed_id_marks()
        A.g.db.commit()
        assert A.next_id("tasks") == tid + 1


# ---------------------------------------------------------------- a deleted session's History

def test_a_deleted_sessions_history_is_labeled_on_its_replacement(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    gone = _new(A, c, "family", subject_name="Ava")
    _line(c, gone)
    c.post(f"/session/{gone}/quote/send", data={"total": "300.00"}, environ_base=E)
    old_q = _q(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    assert c.post(f"/session/{gone}/delete", environ_base=E).status_code == 302
    new = _new(A, c, "family", subject_name="Ava")
    assert new == gone + 1                                          # 835bfdd: the number is never handed out again
    assert not _q(A, "SELECT 1 FROM audit WHERE session_id=? AND note LIKE ?", (new, f"quote {old_q},%"))
    page = _stage(c, new)
    hist = page[page.index('id="history"'):]
    rows = re.findall(r'<div class="hrow.*?</div>\s*</div>', hist, re.S)
    quote_row = next(r_ for r_ in rows if f"quote {old_q}," in r_)
    assert "From a deleted session" in quote_row                   # the old session's lines say whose they were
    assert "From a deleted session" not in rows[0]                  # the newest line is the new session's own
    r = c.post(f"/session/{gone}", data={"title": "stale tab"}, environ_base=E)
    assert r.status_code == 410 and _q(A, "SELECT title FROM sessions WHERE id=?", (new,))[0][0] != "stale tab"
