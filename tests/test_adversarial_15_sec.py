import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD
from test_client_page_141 import E, env
from test_walk_15 import _booked

SECRET = "JBSWY3DPEHPK3PXP"

def _actions(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit ORDER BY id").fetchall()]
    finally:
        con.close()

def test_m4_every_sign_in_is_in_account_activity_and_the_unlocking_one_says_so(env):
    A, c = env
    import time
    import auth
    import datakey
    con = A.connect()
    con.execute("UPDATE users SET email='owner@example.com'")
    con.commit()
    con.close()
    before = len(_actions(A))
    datakey.lock()
    fresh = A.app.test_client()
    fresh.post("/login", data=dict(email="owner@example.com", password=TEST_PASSWORD), environ_base=E)
    fresh.post("/login/2fa", data=dict(code=auth.totp(SECRET)), environ_base=E)
    assert datakey.is_unlocked()
    fresh.post("/logout", environ_base=E)
    fresh.post("/login", data=dict(email="owner@example.com", password=TEST_PASSWORD), environ_base=E)
    fresh.post("/login/2fa", data=dict(code=auth.totp(SECRET, time.time() + 30)), environ_base=E)
    new = _actions(A)[before:]
    assert "Signed in and unlocked the data from 127.0.0.1" in new and "Signed in from 127.0.0.1" in new
    assert "Login" not in new and "Login 2fa" not in new

def test_m4_a_wrong_password_or_an_empty_code_is_never_logged_as_a_sign_in(env):
    A, c = env
    before = len(_actions(A))
    fresh = A.app.test_client()
    fresh.post("/login", data=dict(email="owner@example.com", password="wrong wrong wrong"), environ_base=E)
    fresh.post("/login/2fa", data=dict(code="000000"), environ_base=E)
    assert not [a for a in _actions(A)[before:] if a.startswith(("Login", "Signed in"))]

def test_m5_an_issued_invoice_offers_no_approve_and_keeps_its_sample_banner(env):
    A, c = env
    _booked(A)
    c.post("/session/1/invoice/issue", data={}, environ_base=E)
    with A.app.test_request_context():
        assert A.q("SELECT number FROM doc_numbers WHERE session_id=1", one=True) and not A.doc_reviewed("terms", "family")
    page = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert ">Approve this wording<" not in page and "wording-here-text" not in page
    assert "SAMPLE wording" in page
    c.post("/settings/review/terms", data={"type": "family", "back": "/"}, environ_base=E)
    with A.app.test_request_context():
        assert A.doc_reviewed("terms", "family")
    page = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert "SAMPLE wording" in page

def test_m5_a_numbered_quote_offers_no_approve(env):
    A, c = env
    _booked(A)
    c.post("/session/1/quote/send", environ_base=E)
    con = A.connect()
    try:
        vid = con.execute("SELECT id FROM quote_versions WHERE session_id=1").fetchone()[0]
    finally:
        con.close()
    page = c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True)
    assert ">Approve this wording<" not in page and "wording-here-text" not in page
    assert re.search(r"SAMPLE", page)
