import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_auth import E, PW, claim, client, finish_setup

def _pending(A, email="owner@example.com", pw=PW):

    other = A.app.test_client()
    other.post("/login", data=dict(email=email, password=pw), environ_base=E)
    import datakey
    assert len(datakey._state["pending"]) == 1
    return other

def _second_factor_fails(other, secret):
    import auth
    import datakey
    assert not datakey._state["pending"]
    r = other.post("/login/2fa", data=dict(code=auth.totp(secret, at=time.time() + 30)), environ_base=E)
    assert "/login" in (r.location or "")
    assert "/login" in other.get("/", environ_base=E).location

def test_a_password_change_drops_a_pending_sign_in(client):
    A, c = client
    secret, _, _ = claim(c, A)
    finish_setup(A)
    other = _pending(A)
    c.post("/security", data=dict(act="password", current=PW, password="another good one", password2="another good one"),
           environ_base=E)
    import datakey
    assert datakey.unwrap_password("another good one")
    _second_factor_fails(other, secret)

def test_new_recovery_codes_and_signing_out_others_drop_a_pending_sign_in(client):
    A, c = client
    secret, _, _ = claim(c, A)
    finish_setup(A)
    for act in ("codes", "others"):
        other = _pending(A)
        c.post("/security", data=dict(act=act, current=PW), environ_base=E)
        _second_factor_fails(other, secret)

def test_the_master_key_recovery_drops_a_pending_sign_in_while_unlocked(client):
    A, c = client
    secret, _, master = claim(c, A)
    finish_setup(A)
    import datakey
    import netsec
    assert datakey.is_unlocked()
    other = _pending(A)
    code = netsec.recovery_code()
    A.app.test_client().post("/unlock/master", data=dict(secret=master, password="a brand new password",
                                                         password2="a brand new password", code=code), environ_base=E)
    assert datakey.unwrap_password("a brand new password")
    _second_factor_fails(other, secret)

def test_a_new_master_key_drops_pending_sign_ins(client):
    A, c = client
    claim(c, A)
    finish_setup(A)
    import datakey
    _pending(A)
    datakey.replace_master(datakey.master_pub())
    assert not datakey._state["pending"]
