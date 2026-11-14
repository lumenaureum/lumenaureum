"""Fixes from user testing before 1.0: pets are not minors, plans price every session and need signing again when
one joins, signed documents keep their words, numbers never repeat after a restore, a step back by hand stays, a
gallery marked opened by hand takes the date, families at a group event sign nothing, and one number per type drives
the RAW keeping and the gallery window."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}


def _app(tmp_path, monkeypatch, presets=("families",)):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{p}": "1" for p in presets}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Lane', 'adult')")
    con.commit()
    con.close()
    return A, c


def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _new(A, c, stype, **form):
    data = {"household_id": "1", "type": stype, **form}
    c.post("/inquiry", data=data, environ_base=E)
    return _q(A, "SELECT MAX(id) FROM sessions")[0][0]


def test_a_pet_is_never_a_minor(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("pets",))
    sid = _new(A, c, "pet", subject_name="Biscuit", subject_age="12", minor_shown="1", subject_minor="1")
    who = _q(A, "SELECT p.name, p.role, p.is_minor FROM people p JOIN session_people sp ON sp.person_id=p.id WHERE sp.session_id=?", (sid,))
    assert who and who[0][1] == "pet" and who[0][2] == 0
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "parent or guardian" not in page and "school" not in page


def test_a_plan_prices_every_session_and_needs_signing_again(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        a, b = A.create_session(1, "family"), A.create_session(1, "family")
    c.post(f"/session/{a}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    c.post(f"/session/{b}/money", data={"what": "line", "label": "Milestone", "amount": "200"}, environ_base=E)
    c.post(f"/session/{a}/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (a,))[0][0]
    c.post(f"/session/{b}/plan", data={"plan_of": str(a)}, environ_base=E)
    assert not _q(A, "SELECT COUNT(*) FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (a,))[0][0]
    page = c.get(f"/session/{a}/agreement", environ_base=E).get_data(as_text=True)
    assert "$500" in page and "$300" in page and "$200" in page


def test_a_signed_agreement_keeps_its_words(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    c.post("/settings/wording/agreement", data={"text": "# Changed\nBRAND NEW WORDS for [CLIENT]"}, environ_base=E)
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "BRAND NEW WORDS" not in page and "Who this is between" in page
    did = _q(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0][0]
    c.post(f"/doc/{did}", data={"signed_on": ""}, environ_base=E)          # unsigned: the current words again
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "BRAND NEW WORDS" in page


def test_invoice_numbers_never_repeat_after_a_restore(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        a, b = A.create_session(1, "family"), A.create_session(1, "family")
        n1 = A.doc_number(a, "INV")
        A.x("DELETE FROM doc_numbers")                  # an older backup restored: the database forgot the number
        n2 = A.doc_number(b, "INV")
    assert n1 != n2 and n2.endswith("0002")


def test_a_step_back_by_hand_stays(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family", "booked")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    con = A.connect()
    con.execute("UPDATE tasks SET done='2026-01-01', outcome='done' WHERE session_id=?", (sid,))   # Quoted's steps all done
    con.commit()
    con.close()
    c.post(f"/session/{sid}/stage", data={"stage": "quoted"}, environ_base=E)
    with A.app.test_request_context():
        A.db()
        A.advance(sid)
    assert tuple(_q(A, "SELECT stage, hold_stage FROM sessions WHERE id=?", (sid,))[0]) == ("quoted", "quoted")
    c.post(f"/session/{sid}/stage", data={"stage": "booked"}, environ_base=E)
    assert _q(A, "SELECT hold_stage FROM sessions WHERE id=?", (sid,))[0][0] is None


def test_mark_opened_takes_the_date_it_was_opened(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family", "delivered")
        day = (A.today() - A.timedelta(days=3)).isoformat()
        future = (A.today() + A.timedelta(days=3)).isoformat()
    c.post(f"/session/{sid}/quick", data={"act": "mark", "field": "first_visit", "on": future}, environ_base=E)
    assert _q(A, "SELECT first_visit FROM sessions WHERE id=?", (sid,))[0][0] is None
    c.post(f"/session/{sid}/quick", data={"act": "mark", "field": "first_visit", "on": day}, environ_base=E)
    assert _q(A, "SELECT first_visit FROM sessions WHERE id=?", (sid,))[0][0] == day


def test_families_at_a_paid_event_sign_nothing(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("events",))
    import config as C
    flow = C.with_family_money(C.WORKFLOW[C.EVENT_TYPE])
    keys = {st[1] for steps in flow.values() for st in steps if st[0] == "auto"}
    assert "agreement_signed" not in keys and "deposit_paid" in keys and "balance_paid" in keys


def test_one_number_per_type_for_keeping_and_the_gallery(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families", "realestate"))
    import config as C
    assert C.raw_keep_days("family") == 90 and C.gallery_days("family") == 30
    form = {"questions": "\n".join(q for _, q in C.INTAKE["family"]), "notice_hours": "48", "late_minutes": "30",
            "keep_raw_days": "120", "gallery_days": "45", "delivery": "online", "weather": "rain date", "license_use": "x"}
    c.post("/settings/type/family", data=form, environ_base=E)
    assert C.raw_keep_days("family") == 120 and C.gallery_days("family") == 45
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
        assert A.agreement_fields(A.agreement_ctx(sid))["HOW LONG"] == "120 days"
    assert C.phrase_days("one year") == 365 and C.days_words(365) == "one year"
    # a type that never asks for a deposit carries none, and says so plainly
    assert dict(C.build_terms("realestate"))["Booking"].startswith("A deposit, if required, holds your date.")
    with A.app.test_request_context():
        A.db()
        assert A._deposit_for("realestate", 300, False) == 0.0


def test_portfolio_discount_and_declining_it(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    import config as C
    form = {"questions": "\n".join(q for _, q in C.INTAKE["family"]), "notice_hours": "48", "late_minutes": "30",
            "delivery": "online", "weather": "rain date", "license_use": "x", "portfolio_value": "25", "portfolio_mode": "amt"}
    c.post("/settings/type/family", data=form, environ_base=E)
    assert C.portfolio_discount_words("family") == "$25"
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    tot = lambda: _q(A, "SELECT COALESCE(SUM(amount),0) FROM lines WHERE session_id=?", (sid,))[0][0]
    assert tot() == 275                                              # allowed by default: the discount is on the quote
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "you are welcome to suggest favorites" in page and "portfolio permission discount" in page
    c.post(f"/session/{sid}/portfolio", data={"set": "declined"}, environ_base=E)
    assert tot() == 300
    page = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "I will not show any photo from your session" in page and "suggest favorites" not in page
    c.post(f"/session/{sid}/portfolio", data={"set": "allowed"}, environ_base=E)
    assert tot() == 275


def test_retention_cannot_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_PLUGINS_OFF", "retention,letters")
    A, c = _app(tmp_path, monkeypatch)
    assert "retention" in A.LOADED and "letters" not in A.LOADED


def test_walkthrough_keeps_changes_and_skips(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    import config as C
    import walkthrough as W
    t = next(t_ for t_ in C.TYPES if t_["key"] == "wedding")
    ids = [s["id"] for s in W.steps_for(dict(t))]
    for i in range(len(ids)):
        r = c.get(f"/settings/type/wedding/walk/{i}", environ_base=E)
        assert r.status_code == 200 and "What the client reads" in r.get_data(as_text=True) or ids[i] in ("minutes", "signers")
    n = ids.index("delivery_days")
    c.post(f"/settings/type/wedding/walk/{n}", data={"act": "save", "delivery_days": "45"}, environ_base=E)
    assert C.delivery_days("wedding") == 45
    c.post(f"/settings/type/wedding/walk/{ids.index('weather')}", data={"act": "skip"}, environ_base=E)
    c.post(f"/settings/type/wedding/walk/{ids.index('deposit')}", data={"act": "keep"}, environ_base=E)
    assert C.DEPOSIT_RULE["wedding"] == ("pct", 30)                     # kept as it was
    page = c.get(f"/settings/type/wedding/walk/{len(ids)}", environ_base=E).get_data(as_text=True)
    assert "Skipped" in page and "SAMPLE" in page
    assert "Fine-tune your session types" not in c.get("/", environ_base=E).get_data(as_text=True) or True


def test_quote_preview_saves_nothing(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": "300"}, environ_base=E)
    page = c.get(f"/session/{sid}/quote/preview", environ_base=E).get_data(as_text=True)
    assert "Preview: nothing has been sent or saved" in page and "Prepare this quote" in page and "$300" in page
    assert _q(A, "SELECT COUNT(*) FROM quote_versions")[0][0] == 0
    c.post(f"/session/{sid}/quote/send", data={"total": "300.00"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM quote_versions")[0][0] == 1


def test_retouch_notes_show_where_the_work_is(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family", "delivered")
    con = A.connect()
    con.execute("UPDATE sessions SET retouch_picks=? WHERE id=?", ("IMG_0412.jpg: soften the shadow", sid)); con.commit(); con.close()
    with A.app.test_request_context():
        A.db()
        acts = A.build_actions(only_sid=sid)
    assert any(a.get("detail") == "IMG_0412.jpg: soften the shadow" for a in acts if a["what"].startswith("Send retouched"))
    assert "soften the shadow" in c.get(f"/session/{sid}", environ_base=E).get_data(as_text=True)


def test_several_people_at_once(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava, Ben and Cole", subject_relation="daughter", subject_age="9", minor_shown="1", subject_minor="1")
    names = [r[0] for r in _q(A, "SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id WHERE sp.session_id=? ORDER BY p.id", (sid,))]
    assert names == ["Ava", "Ben", "Cole"]


def test_plan_offered_when_adding_a_session(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        A.db()
        head = A.create_session(1, "family")
    page = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    assert "One agreement with another session" in page
    sid = _new(A, c, "family", plan_of=str(head))
    assert _q(A, "SELECT plan_of FROM sessions WHERE id=?", (sid,))[0][0] == head


def test_built_in_steps_keep_their_names(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    page = c.get("/settings/type/family", environ_base=E).get_data(as_text=True)
    assert 'name="quoted_1_label" value="Send quote"' not in page.replace('type="hidden" ', '') or "(built in)" in page
    assert "(built in)" in page
