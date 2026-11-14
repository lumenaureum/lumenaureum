import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

def test_ages_in_brackets_make_minors_named_properly(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ada (7) and Obi (4)")
    rows = _q(A, """SELECT p.name, p.is_minor, p.birth_year FROM people p JOIN session_people sp ON sp.person_id=p.id
                    WHERE sp.session_id=? ORDER BY p.id""", (sid,))
    names = {r[0]: r for r in rows}
    assert set(names) == {"Ada", "Obi"}
    assert names["Ada"][1] == 1 and names["Obi"][1] == 1 and names["Obi"][2]

def test_names_typed_again_reuse_the_same_people(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _new(A, c, "family", subject_name="Ava, Ben and Cole")
    _new(A, c, "family", subject_name="Ava, Ben and Cole")
    _new(A, c, "family", subject_name="Avery Lane")
    assert _q(A, "SELECT COUNT(*) FROM people WHERE household_id=1")[0][0] == 4

def test_a_type_added_later_keeps_the_copyright(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    lic = {t: "license" for t in A.C.SESSION_TYPES}
    c.post("/setup/documents", data={**{f"rights_{t}": v for t, v in lic.items()}, "state": "OH"}, environ_base=E)
    c.post("/setup/shoot", data={"preset_families": "1", "preset_events": "1"}, environ_base=E)
    assert "event" in A.C.SESSION_TYPES and "event" in A.C.LICENSE_TYPES

def test_event_deposits_organizer_pct_family_none(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families", "events"))
    with A.app.test_request_context():
        A.db()
        assert A._deposit_for("event", 90.10, 0) == round(90.10 * A.C.EVENT_DEPOSIT_PCT / 100, 2)
        assert A._deposit_for("event", 37.54, 0, family=True) == 0.0

def test_deleting_a_person_takes_their_unsigned_release(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava, Ben")
    pid = _q(A, "SELECT id FROM people WHERE name='Ben'")[0][0]
    assert _q(A, "SELECT COUNT(*) FROM documents WHERE about_person=?", (pid,))[0][0] >= 0
    c.post(f"/person/{pid}", data={"delete": "1"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM documents WHERE about_person=? AND status != 'signed'", (pid,))[0][0] == 0
    assert c.get(f"/c/1?s={sid}", environ_base=E).status_code == 200

def test_a_note_dated_year_one_never_breaks_the_client_page(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post("/client/1/note", data={"text": "old", "when": "0001-01-01T09:00", "session_id": str(sid)}, environ_base=E)
    c.post("/client/1/note", data={"text": "bad", "when": "0000-13-45T99:99", "session_id": str(sid)}, environ_base=E)
    assert c.get(f"/c/1?s={sid}", environ_base=E).status_code == 200

def test_a_named_app_replaces_payment_app_and_details_print(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    A.C.PAYMENT_METHODS = ["cash", "payment app", "Venmo"]
    c.post("/settings", data={"pay_details": "Venmo @marcus-photo"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        t = A.C.payment_text()
    assert "payment app" not in t and "Venmo @marcus-photo." in t

def test_posts_to_a_deleted_session_are_404(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    r = c.post("/session/999/money", data={"what": "line", "label": "x", "amount": "1"}, environ_base=E)
    assert r.status_code == 404

def test_an_untaxed_line_is_not_taxed(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post("/settings", data={"sales_tax_pct": "8"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        A.x("UPDATE settings SET value='8' WHERE key='sales_tax_pct'")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "License", "amount": "600", "taxable_shown": "1"},
           environ_base=E)
    rows = _q(A, "SELECT label, taxable FROM lines WHERE session_id=? AND label='License'", (sid,))
    assert rows and rows[0][1] == 0

def test_a_changed_invoice_after_sending_gets_a_new_number(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "225"}, environ_base=E)
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    first = c.get(f"/session/{sid}/invoice", environ_base=E)
    num1 = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,))
    assert first.status_code == 200 and num1
    with A.app.test_request_context():
        A.db()
        A._invoice_marked_sent(sid)
        A._commit()
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Late fee", "amount": "25"}, environ_base=E)
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    page = c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True)
    num2 = _q(A, "SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,))
    assert num2 and num2[0][0] != num1[0][0] and "Replaces" in page and num1[0][0] in page
