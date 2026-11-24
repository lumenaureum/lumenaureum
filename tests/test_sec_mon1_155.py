import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, one, run
from test_37_155 import _confirm, _plan, _token, _upload

HEAD = b"Client,Person,Relationship,Email,Birth date,Minor\n"

def _person(A, name):
    con = A.connect()
    try:
        return con.execute("SELECT * FROM people WHERE name=?", (name,)).fetchone()
    finally:
        con.close()

def _minor(A, name, day=None):
    with A.app.test_request_context():
        A.db()
        return A.minor_on(A.q("SELECT * FROM people WHERE name=?", (name,), one=True), day)

def _import(A, c, data, picks=None):
    tok = _token(_upload(c, data))
    plan = _plan(A, tok)
    if picks == "update":
        picks = {"pick_" + g_["id"]: "update" for g_ in plan["match"]}
    assert _confirm(A, c, tok, picks=picks).status_code == 302
    return plan

def test_a_child_minor_by_birth_date_stays_a_minor_and_moves_with_the_date(env):
    A, c = env
    _import(A, c, HEAD + b"Lane Family,Robin Lane,mother,robin@example.com,,no\n"
                         b"Lane Family,Kit Lane,son,,2015-04-09,yes\n")
    kit = _person(A, "Kit Lane")
    assert kit["is_minor"] == 1 and kit["minor_box"] is None
    assert _minor(A, "Kit Lane") is True
    assert _minor(A, "Kit Lane", "2033-04-09") is False
    with A.app.test_request_context():
        A.db()
        hid = kit["household_id"]
        assert [p_["name"] for p_ in A.signer_choices(hid)] == ["Robin Lane"]
        assert A.release_signer(A.q("SELECT * FROM people WHERE id=?", (kit["id"],), one=True)) \
            == "Parent or guardian: Robin Lane"

def test_minor_no_against_a_minor_birth_date_is_kept_as_said(env):
    A, c = env
    _import(A, c, HEAD + b"Ray Family,Jo Ray,daughter,,2015-04-09,no\n")
    jo = _person(A, "Jo Ray")
    assert jo["is_minor"] == 0 and jo["minor_box"] == 0
    assert _minor(A, "Jo Ray") is False

def test_minor_yes_against_an_adult_birth_date_is_kept_as_said(env):
    A, c = env
    _import(A, c, HEAD + b"Ray Family,Sam Ray,son,,1990-01-01,yes\n")
    sam = _person(A, "Sam Ray")
    assert sam["is_minor"] == 1 and sam["minor_box"] == 1 and _minor(A, "Sam Ray") is True

def test_no_birth_date_uses_the_minor_value_and_no_minor_value_uses_the_birth_date(env):
    A, c = env
    _import(A, c, HEAD + b"Fox Family,Ann Fox,daughter,,,yes\nFox Family,Ben Fox,son,,2016-02-02,\n")
    assert _person(A, "Ann Fox")["is_minor"] == 1 and _minor(A, "Ann Fox") is True
    ben = _person(A, "Ben Fox")
    assert ben["is_minor"] == 1 and ben["minor_box"] is None

def test_update_sets_the_minor_mark_so_it_takes_effect(env):
    A, c = env
    run(A, """UPDATE households SET name='Lane Family' WHERE id=1;
              INSERT INTO people (id, household_id, name, role, relation, email, birth_date, is_minor)
                VALUES (1, 1, 'Robin Lane', 'parent', 'mother', 'robin@example.com', NULL, 0);
              INSERT INTO people (id, household_id, name, role, relation, email, birth_date, is_minor)
                VALUES (2, 1, 'Kit Lane', 'child', 'son', 'kit@example.com', '2015-04-09', 1);
              INSERT INTO people (id, household_id, name, role, relation, email, birth_date, is_minor)
                VALUES (3, 1, 'Lee Lane', 'child', 'son', 'lee@example.com', NULL, 0);""")
    plan = _import(A, c, HEAD + b"Lane Family,Kit Lane,son,kit@example.com,,no\n"
                                b"Lane Family,Lee Lane,son,lee@example.com,,yes\n", picks="update")
    assert {w_ for _o, _a, w_ in plan["match"][0]["ops"]} == {"Kit Lane's minor mark", "Lee Lane's minor mark"}
    kit = _person(A, "Kit Lane")
    assert kit["minor_box"] == 0 and kit["is_minor"] == 0 and _minor(A, "Kit Lane") is False
    assert _person(A, "Lee Lane")["is_minor"] == 1 and _minor(A, "Lee Lane") is True

    plan = _import(A, c, HEAD + b"Lane Family,Kit Lane,son,kit@example.com,,yes\n", picks="update")
    assert [w_ for _o, _a, w_ in plan["match"][0]["ops"]] == ["Kit Lane's minor mark"]
    kit = _person(A, "Kit Lane")
    assert kit["minor_box"] is None and kit["is_minor"] == 1 and _minor(A, "Kit Lane") is True

def test_update_promises_nothing_when_the_file_agrees_with_what_the_app_reads(env):
    A, c = env
    run(A, """UPDATE households SET name='Lane Family' WHERE id=1;
              INSERT INTO people (id, household_id, name, role, relation, email, birth_date, is_minor)
                VALUES (2, 1, 'Kit Lane', 'child', 'son', 'kit@example.com', '2015-04-09', 0);""")

    plan = _import(A, c, HEAD + b"Lane Family,Kit Lane,son,kit@example.com,,yes\n", picks="update")
    assert not plan["match"][0]["ops"]
