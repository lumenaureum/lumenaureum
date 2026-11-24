import html
import os
import re
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def wenv(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_engagements": "1", "preset_families": "1"}, environ_base=E)
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        out = con.execute(q_, args).fetchall()
        con.commit()
        return out
    finally:
        con.close()

def _wedding(A):
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,'Avery Rowe','bride','adult','avery@example.com',0)")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,'Jordan Penn','partner','adult','jordan@example.com',0)")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    return sid

def test_removing_a_wedding_partner_leaves_a_phantom_signer_that_never_signs(wenv):

    A, c = wenv
    sid = _wedding(A)
    rows = sql(A, "SELECT about_person FROM documents WHERE session_id=? AND kind='agreement'", (sid,))
    assert len(rows) == 2
    jordan = sql(A, "SELECT id FROM people WHERE name='Jordan Penn'")[0][0]
    avery = sql(A, "SELECT id FROM people WHERE name='Avery Rowe'")[0][0]
    r = c.post(f"/person/{jordan}", data={"delete": "1"}, environ_base=E)
    assert r.status_code in (302, 303)
    did = sql(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement' AND about_person=?", (sid, avery))[0][0]
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement", "did": str(did)}, environ_base=E)
    page = _text(c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True))
    with A.app.test_request_context():
        w = A.workflow(sid)
        step = next(st for st in w["current"]["steps"] + w["carried"] if st["label"] == "Agreement signed")

    assert "Signer: Jordan Penn" not in page
    assert step["done"], "Agreement signed stays open: it waits on a partner who is no longer on the record"

def _wedding_from_planner(A):

    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    for name, rel, role in (("Quinn Planner", "planner", "adult"), ("Avery Rowe", "bride", "adult"),
                            ("Jordan Penn", "partner", "adult"), ("Morgan Rowe", "mother", "parent")):
        sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,?,?,?,?,0)",
            (name, rel, role, name.split()[0].lower() + "@example.com"))
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    return sid

def _text(page):
    import html
    import re
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def test_a_wedding_statement_bills_the_couple_not_the_planner(wenv):

    A, c = wenv
    sid = _wedding_from_planner(A)
    c.post(f"/session/{sid}/money", data=dict(what="line", label="Wedding", amount="3000", taxable_shown="1"),
           environ_base=E)
    inv = _text(c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True))
    assert "Bill to Avery Rowe and Jordan Penn" in inv
    month = date.today().strftime("%Y-%m")
    st = _text(c.get(f"/client/1/statement?month={month}", environ_base=E).get_data(as_text=True))
    bill_to = st[st.index("Bill to"):st.index("Period")]
    assert "Quinn Planner" not in bill_to, bill_to
    assert "Avery Rowe" in bill_to and "Jordan Penn" in bill_to, bill_to

def test_a_partner_added_after_the_inquiry_is_billed_but_never_asked_to_sign(wenv):

    A, c = wenv
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Avery Rowe')")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,'Avery Rowe','','adult','avery@example.com',0)")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="inquiry", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    r = c.post("/client/1/person", data=dict(name="Jordan Penn", relation="partner", email="jordan@example.com", role="adult"),
               environ_base=E)
    assert r.status_code in (302, 303)
    c.post(f"/session/{sid}/money", data=dict(what="line", label="Wedding", amount="3000", taxable_shown="1"),
           environ_base=E)
    inv = _text(c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True))
    assert "Bill to Avery Rowe and Jordan Penn" in inv
    signers = sorted(r_[0] for r_ in sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='agreement'", (sid,)))
    assert signers == ["Avery Rowe", "Jordan Penn"], signers

def test_a_partner_added_after_the_agreement_is_signed_does_not_change_it(wenv):

    A, c = wenv
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Avery Rowe')")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,'Avery Rowe','','adult','avery@example.com',0)")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    did = sql(A, "SELECT id FROM documents WHERE session_id=? AND kind='agreement'", (sid,))[0][0]
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement", "did": str(did)}, environ_base=E)
    c.post("/client/1/person", data=dict(name="Jordan Penn", relation="partner", email="jordan@example.com", role="adult"),
           environ_base=E)
    signers = [r_[0] for r_ in sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='agreement'", (sid,))]
    assert signers == ["Avery Rowe"], signers

@pytest.mark.parametrize("relation", ["best man", "friend"])
def test_adding_or_editing_someone_who_is_not_a_partner_never_makes_them_a_signer(wenv, relation):

    A, c = wenv
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Avery Rowe')")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (1,'Avery Rowe','','adult','avery@example.com',0)")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="inquiry", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    c.post("/client/1/person", data=dict(name="Sam Other", relation=relation, role="adult"), environ_base=E)
    sam = sql(A, "SELECT id FROM people WHERE name='Sam Other'")[0][0]
    c.post(f"/person/{sam}", data=dict(name="Sam Other", relation=relation, role="adult", phone="555-0100"), environ_base=E)
    signers = [r_[0] for r_ in sql(A, "SELECT signer FROM documents WHERE session_id=? AND kind='agreement'", (sid,))]
    assert signers == ["Avery Rowe"], signers

from test_money_audit_130 import env, household, one, post, session

def test_deleting_a_session_drops_the_explanation_of_its_open_year_correction(env):

    A, c, freeze = env
    freeze("2026-06-02")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    post(c, "/session/1/money", what="expense", label="Second shooter", amount="100", date="2026-06-01")
    freeze("2027-03-01")
    eid = one(A, "SELECT id FROM expenses")[0]
    post(c, f"/money/expense/{eid}/correct", should_be="60", why="the receipt said 60")
    with A.app.test_request_context():
        assert len(A.year_corrections("2027")) == 1
    c.post("/session/1/delete", environ_base=E)
    assert one(A, "SELECT COUNT(*) FROM sessions")[0] == 0
    with A.app.test_request_context():
        assert A.year_money(2027)["exp"]["a"] == -40.0
        fixes = A.year_corrections("2027")
    assert len(fixes) == 1 and "Correction of" in fixes[0]["words"], fixes

@pytest.fixture
def wedenv(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    p = lambda path, **d: c.post(path, data=d, environ_base=E)
    p("/setup/shoot", preset_weddings="1", preset_engagements="1")
    p("/contact/new", contact_name="Maya Chen", email="maya@example.com", phone="555-0100")
    p("/inquiry", household_id="1", type="wedding", title="Chen and Lee wedding", shoot_date="2027-10-09",
      subjects="1", subject_name="Jordan Lee", subject_relation="fiance", i_venue="Longwood Gardens")
    return A, c, p

def text(c, path):
    t = c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))

def test_the_venue_answered_on_the_inquiry_reaches_the_session_sheet(wedenv):

    A, c, p = wedenv
    p("/session/1/stage", stage="booked")
    assert "Longwood Gardens" in text(c, "/session/1/agreement")
    sheet = text(c, "/session/1/details")
    assert "place to be confirmed" not in sheet, sheet[sheet.find("Meet"):sheet.find("Meet") + 80]
    assert "Longwood Gardens" in sheet

def test_the_agreement_for_a_wedding_and_engagement_lists_every_quoted_line(wedenv):

    A, c, p = wedenv
    p("/session/1/money", what="line", label="Wedding day (8 hr)", amount="3000")
    p("/inquiry", household_id="1", type="engagement", title="Chen and Lee engagement", shoot_date="2027-03-13",
      subjects="1", plan_of="1")
    p("/session/2/money", what="line", label="Engagement session", amount="400")
    p("/session/1/stage", stage="booked")
    p("/session/2/stage", stage="booked")
    t = text(c, "/session/1/agreement")
    assert "$3,400.00 for every session" in t or "for every session" in t
    quoted = t.split("Quoted", 1)[1].split("Price", 1)[0]
    assert "Engagement session" in quoted, quoted
