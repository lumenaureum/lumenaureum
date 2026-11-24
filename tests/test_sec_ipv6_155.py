import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, HttpsClient, fresh_modules, signed_in_client

SECRET = "JBSWY3DPEHPK3PXP"
OWNER = "owner@example.com"
NET = "fd00:1:2:3::"
MINE = {"REMOTE_ADDR": NET + "10"}
THEIRS = {"REMOTE_ADDR": NET + "66"}

@pytest.fixture
def A(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A_
    A_.master = signed_in_client(A_).master_secret
    A_.app.test_client_class = HttpsClient
    return A_

def _fresh_code(A):

    con = A.connect()
    con.execute("UPDATE users SET totp_last=0")
    con.commit()
    con.close()

def _full_sign_in(A, c, env=MINE):
    import auth
    _fresh_code(A)
    r = c.post("/login", data=dict(email=OWNER, password=TEST_PASSWORD), environ_base=env)
    assert "/login/2fa" in r.headers.get("Location", "")
    return c.post("/login/2fa", data=dict(code=auth.totp(SECRET)), environ_base=env)

def _token(c):
    import knownbrowser
    ck = c.get_cookie(knownbrowser.COOKIE, domain="localhost")
    return ck.value if ck else None

def _flood(A, n=None):

    import auth
    th = A.app.config["auth_throttle"]
    for i in range(n or auth.IP_FAILS_PER_HOUR):
        th.fail(f"{NET}{0x1000 + i:x}", "password")

def _gets_past_password(c, env=MINE):
    r = c.post("/login", data=dict(email=OWNER, password=TEST_PASSWORD), environ_base=env)
    return "/login/2fa" in r.headers.get("Location", "")

def test_the_throttle_lets_a_known_browser_past_the_64_ceiling_only():
    import auth
    th = auth.Throttle()
    for i in range(auth.IP_FAILS_PER_HOUR):
        th.fail(f"2001:db8:1:2::{i + 100:x}")
    assert th.ip_blocked("2001:db8:1:2::1") and not th.ip_blocked("2001:db8:1:2::1", known=True)
    for _ in range(auth.MAX_FAILS):
        th.fail("2001:db8:1:2::1")
    assert th.ip_blocked("2001:db8:1:2::1", known=True)
    assert "minutes" in th.wait_text("2001:db8:1:2::1", known=True)

def test_known_browser_gets_past_the_64_ceiling_but_not_the_lock_on_its_address(A):
    import auth
    c = A.app.test_client()
    r = _full_sign_in(A, c)
    assert r.status_code == 302 and _token(c)
    _flood(A)
    stranger = A.app.test_client()
    assert not _gets_past_password(stranger, THEIRS)
    assert not _gets_past_password(A.app.test_client(), MINE)
    assert _gets_past_password(c)
    r = _full_sign_in(A, c)
    assert "/login" not in r.headers.get("Location", "")

    for _ in range(auth.MAX_FAILS):
        c.post("/login", data=dict(email=OWNER, password="wrong wrong wrong"), environ_base=MINE)
    assert not _gets_past_password(c)

def test_the_cookie_is_locked_down_and_holds_nothing_but_a_random_token(A):
    import knownbrowser
    c = A.app.test_client()
    r = _full_sign_in(A, c)
    sc = [h for h in r.headers.getlist("Set-Cookie") if h.startswith(knownbrowser.COOKIE + "=")]
    assert len(sc) == 1
    low = sc[0].lower()
    assert "httponly" in low and "secure" in low and "samesite=strict" in low and "max-age=2592000" in low
    tok = _token(c)
    assert len(tok) >= 40 and OWNER not in tok
    kept = knownbrowser.path().read_text()
    assert tok not in kept and OWNER not in kept
    assert oct(knownbrowser.path().stat().st_mode & 0o777) == "0o600"

@pytest.mark.parametrize("how", ["forged", "expired", "revoked", "other_install"])
def test_a_marker_that_isnt_live_does_not_get_past_the_ceiling(A, how):
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    tok = _token(c)
    if how == "forged":
        c.set_cookie(knownbrowser.COOKIE, "A" * 43, domain="localhost")
    elif how == "expired":
        data = json.loads(knownbrowser.path().read_text())
        for rec in data["browsers"].values():
            rec["exp"] = time.time() - 1
        knownbrowser.path().write_text(json.dumps(data))
    elif how == "revoked":
        knownbrowser.revoke_all()
    else:
        knownbrowser.revoke_all()
        c.set_cookie(knownbrowser.COOKIE, tok + "x", domain="localhost")
    _flood(A)
    assert not knownbrowser.known(_token(c))
    assert not _gets_past_password(c)

def test_the_marker_is_issued_only_after_two_factor(A):
    import knownbrowser
    c = A.app.test_client()
    _fresh_code(A)
    r = c.post("/login", data=dict(email=OWNER, password=TEST_PASSWORD), environ_base=MINE)
    assert "/login/2fa" in r.headers.get("Location", "")
    assert _token(c) is None
    c.post("/login/2fa", data=dict(code="000000"), environ_base=MINE)
    assert _token(c) is None and not knownbrowser.path().exists()
    import auth
    c.post("/login/2fa", data=dict(code=auth.totp(SECRET)), environ_base=MINE)
    assert knownbrowser.known(_token(c))

def test_a_password_change_voids_every_other_marker_and_gives_this_browser_a_fresh_one(A):

    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    other = A.app.test_client()
    _full_sign_in(A, other, THEIRS)
    tok, theirs = _token(c), _token(other)
    assert knownbrowser.known(tok) and knownbrowser.known(theirs)
    new = "another password 456"
    r = c.post("/security", data=dict(act="password", current=TEST_PASSWORD, password=new, password2=new),
               environ_base=MINE)
    assert r.status_code == 200
    fresh = _token(c)
    assert fresh and fresh != tok and knownbrowser.known(fresh)
    assert not knownbrowser.known(tok) and not knownbrowser.known(theirs)
    _flood(A)
    assert not _gets_past_password(other, THEIRS)

def test_a_wrong_current_password_gives_no_fresh_marker(A):
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    tok = _token(c)
    c.post("/security", data=dict(act="others", current="wrong wrong wrong"), environ_base=MINE)
    assert _token(c) == tok and knownbrowser.known(tok)

def test_a_change_made_outside_the_app_voids_markers_too(A):

    import datakey
    import knownbrowser
    from vault import keys as K
    c = A.app.test_client()
    _full_sign_in(A, c)
    tok = _token(c)
    datakey.rewrap_password("another password 456")
    assert not knownbrowser.known(tok)
    import auth
    _fresh_code(A)
    c.post("/login", data=dict(email=OWNER, password="another password 456"), environ_base=MINE)
    c.post("/login/2fa", data=dict(code=auth.totp(SECRET)), environ_base=MINE)
    tok = _token(c)
    assert knownbrowser.known(tok)
    datakey.replace_master(K.generate_master()[1])
    assert not knownbrowser.known(tok)

def test_signing_out_other_devices_gives_this_browser_a_fresh_marker_only(A):
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    other = A.app.test_client()
    _full_sign_in(A, other, THEIRS)
    toks = [_token(c), _token(other)]
    assert all(knownbrowser.known(t) for t in toks)
    c.post("/security", data=dict(act="others", current=TEST_PASSWORD), environ_base=MINE)
    assert not any(knownbrowser.known(t) for t in toks)
    fresh = _token(c)
    assert fresh not in toks and knownbrowser.known(fresh)
    assert len(json.loads(knownbrowser.path().read_text())["browsers"]) == 1

def test_new_recovery_codes_void_every_marker(A):
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    other = A.app.test_client()
    _full_sign_in(A, other, THEIRS)
    toks = [_token(c), _token(other)]
    c.post("/security", data=dict(act="codes", current=TEST_PASSWORD), environ_base=MINE)
    assert not any(knownbrowser.known(t) for t in toks)
    assert _token(c) in toks

def test_a_stolen_marker_is_thrown_away_after_five_wrong_tries(A):

    import auth
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    tok = _token(c)
    _flood(A)
    th = A.app.config["auth_throttle"]
    for i in range(auth.MAX_FAILS):
        assert knownbrowser.known(tok)
        before = len(th.log)
        c.post("/login", data=dict(email=OWNER, password="wrong wrong wrong"),
               environ_base={"REMOTE_ADDR": f"{NET}{0x2000 + i:x}"})
        assert len(th.log) == before + 1
    assert not knownbrowser.known(tok)
    assert not _gets_past_password(c, {"REMOTE_ADDR": NET + "3000"})

def test_a_new_sign_in_replaces_the_old_marker(A):
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    first = _token(c)
    _full_sign_in(A, c)
    second = _token(c)
    assert first != second and knownbrowser.known(second) and not knownbrowser.known(first)

def test_docs_explain_the_known_browser():
    text = " ".join(open(os.path.join(ROOT, "docs", "security.md"), encoding="utf-8").read().split())
    assert "known browser" in text and "SameSite=Strict" in text and "known-browsers.json" in text
    assert "keep every other device on that network waiting" in text

def test_the_master_key_unlock_gives_no_marker_and_voids_the_others(A):

    import netsec
    import knownbrowser
    c = A.app.test_client()
    _full_sign_in(A, c)
    tok = _token(c)
    fresh = A.app.test_client()
    fresh.get("/unlock/master", environ_base=THEIRS)
    new = "another password 456"
    r = fresh.post("/unlock/master", data=dict(secret=A.master, password=new, password2=new,
                                               code=netsec.RECOVERY["code"]), environ_base=THEIRS)
    assert r.status_code == 302
    assert _token(fresh) is None
    assert not knownbrowser.known(tok)
