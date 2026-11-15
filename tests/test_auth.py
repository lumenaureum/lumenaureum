import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

E = {"REMOTE_ADDR": "127.0.0.1"}
PW = "correct horse battery"

def test_totp_rfc6238_vector():
    import auth
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert auth.totp(secret, at=59, digits=8) == "94287082"
    assert auth.totp(secret, at=1111111109, digits=8) == "07081804"
    assert auth.totp_ok(secret, auth.totp(secret, at=1000), at=1000 + 30)
    assert not auth.totp_ok(secret, auth.totp(secret, at=1000), at=1000 + 120)

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    return A, A.app.test_client()

def restart(c_old=None):

    fresh_modules()
    import app as A
    return A, A.app.test_client()

def unlocked():

    return sys.modules["datakey"].is_unlocked()

def claim(c, A, email="owner@example.com", pw=PW):

    import auth
    c.post("/setup/owner", data=dict(claim=sys.modules["netsec"].claim_code(), name="Owner", email=email, password=pw,
                                     password2=pw), environ_base=E)
    with c.session_transaction() as s:
        tok = s["setup_tok"]
        assert "totp" not in s and "pw" not in s
    secret = auth.SETUP[tok]["totp"]
    r = c.post("/setup/owner/verify", data=dict(code=auth.totp(secret)), environ_base=E)
    assert r.location.endswith("/setup/owner/key")
    page = c.get("/setup/owner/key", environ_base=E).get_data(as_text=True)
    master = re.search(r"AGE-SECRET-KEY-1[0-9A-Z]+", page).group(0)
    assert master in c.get("/setup/owner/key", environ_base=E).get_data(as_text=True)
    other = A.app.test_client()
    assert "AGE-SECRET-KEY" not in other.get("/setup/owner/key", environ_base=E).get_data(as_text=True)
    assert master in c.post("/setup/owner/key", data=dict(tail="XXXXXX"), environ_base=E).get_data(as_text=True)
    r = c.post("/setup/owner/key", data=dict(tail=master[-6:]), environ_base=E)
    assert not any(st.get("secret") for st in auth.SETUP.values())
    codes = re.findall(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", r.get_data(as_text=True))
    return secret, codes, master

def finish_setup(A):
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    con.commit()
    con.close()

def sign_in(c, secret, pw=PW, code=None):
    import auth
    c.post("/login", data=dict(email="owner@example.com", password=pw), environ_base=E)
    return c.post("/login/2fa", data=dict(code=code or auth.totp(secret)), environ_base=E)

def test_setup_then_everything_behind_login(client):
    A, c = client
    assert c.get("/", environ_base=E).location.endswith("/setup/owner")
    assert c.get("/healthz", environ_base=E).get_json() == {"ok": True, "locked": True}
    secret, codes, master = claim(c, A)
    assert len(codes) == 10
    assert c.get("/healthz", environ_base=E).get_json()["locked"] is False
    assert c.get("/", environ_base=E).location.endswith("/setup/business")
    assert c.get("/setup/owner", environ_base=E).status_code == 404
    assert not sys.modules["netsec"].code_file().exists()
    c.post("/logout", environ_base=E)
    for p in ("/", "/settings", "/vault", "/money", "/calendar", "/security"):
        assert "/login" in c.get(p, environ_base=E).location
    assert c.post("/settings/reset", data={"confirm": "RESET"}, environ_base=E).status_code == 302

def test_data_is_encrypted_on_disk(client, tmp_path):
    A, c = client
    secret, _, _ = claim(c, A)
    finish_setup(A)
    con = A.connect()
    con.execute("INSERT INTO households (name, address) VALUES ('Harlow Family', '12 Elm St 00000')")
    con.commit(); con.close()
    hid = 1
    from PIL import Image
    import io
    b = io.BytesIO(); Image.new("RGB", (400, 400), "red").save(b, "JPEG")
    c.post(f"/client/{hid}/face", data={"photo": (io.BytesIO(b.getvalue()), "f.jpg")}, environ_base=E,
           content_type="multipart/form-data")
    con = A.connect(); con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); con.close()
    for f in tmp_path.rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert b"Harlow" not in data and b"Elm St" not in data, f
            assert not data.startswith(b"SQLite format 3"), f
    face = (tmp_path / "faces" / f"{hid}.jpg").read_bytes()
    assert face.startswith(b"LAE1") and b"JFIF" not in face
    r = c.get(f"/face/{hid}", environ_base=E)
    assert r.data[:2] == b"\xff\xd8" and r.headers["Cache-Control"] == "no-store"
    assert c.get("/", environ_base=E).headers["Cache-Control"] == "no-store"
    assert not (tmp_path / "secret_key").exists()

def test_restart_locks_and_sign_in_unlocks(client):
    A, c = client
    secret, _, _ = claim(c, A)
    finish_setup(A)
    A, c = restart()
    import datakey
    assert not datakey.is_unlocked()
    assert c.get("/healthz", environ_base=E).get_json()["locked"] is True
    r = c.get("/", environ_base=E)
    assert "/login" in r.location
    assert b"Locked after a restart" in c.get("/login", environ_base=E).data
    c.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=E)
    assert not datakey.is_unlocked()
    assert "/login" in c.get("/", environ_base=E).location
    r = c.post("/login/2fa", data=dict(code="000000"), environ_base=E)
    assert not datakey.is_unlocked()
    import auth
    r = c.post("/login/2fa", data=dict(code=auth.totp(secret)), environ_base=E)
    assert datakey.is_unlocked() and c.get("/", environ_base=E).status_code == 200

def test_pending_key_expires_without_second_factor(client, monkeypatch):
    A, c = client
    secret, _, _ = claim(c, A)
    A, c = restart()
    import datakey, auth, time
    c.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=E)
    assert len(datakey._state["pending"]) == 1
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + datakey.PENDING_TTL + 5)
    r = c.post("/login/2fa", data=dict(code=auth.totp(secret, at=real() + datakey.PENDING_TTL + 5)), environ_base=E)
    assert "/login" in r.location and not datakey.is_unlocked()

def test_wrong_password_and_lockout(client):
    A, c = client
    claim(c, A)
    A, c = restart()
    r = c.post("/login", data=dict(email="owner@example.com", password="wrong password!!"), environ_base=E)
    assert "Wrong email or password" in r.get_data(as_text=True)
    for _ in range(5):
        c.post("/login", data=dict(email="owner@example.com", password="nope nope nope"), environ_base=E)
    r = c.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=E)
    assert "Wait 15 minutes" in r.get_data(as_text=True)

def test_recovery_code_replaces_two_factor_once_but_never_the_password(client):
    A, c = client
    secret, codes, _ = claim(c, A)
    finish_setup(A)
    for attempt in (True, False):
        A, c = restart()
        sign_in(c, secret, code=codes[0])
        assert unlocked() is attempt
    A, c = restart()
    r = c.post("/login", data=dict(email="owner@example.com", password=codes[1]), environ_base=E)
    assert not unlocked() and "Wrong email or password" in r.get_data(as_text=True)

def test_master_key_resets_a_lost_password(client):
    A, c = client
    secret, _, master = claim(c, A)
    finish_setup(A)
    A, c = restart()
    import datakey
    r = c.post("/unlock/master", data=dict(secret="AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ",
                                           password="a brand new password", password2="a brand new password"), environ_base=E)
    assert not datakey.is_unlocked()
    r = c.post("/unlock/master", data=dict(secret=master, password="a brand new password",
                                           password2="a brand new password", code="WRONG-CODE-0000"), environ_base=E)
    assert not datakey.is_unlocked()
    import netsec
    code = netsec.recovery_code()
    assert code in (netsec.tls_dir().parent / netsec.RECOVERY_FILE).read_text()
    r = c.post("/unlock/master", data=dict(secret=master, password="a brand new password",
                                           password2="a brand new password", code=code), environ_base=E)
    assert datakey.is_unlocked() and c.get("/", environ_base=E).status_code == 200
    assert not (netsec.tls_dir().parent / netsec.RECOVERY_FILE).exists() and not netsec.recovery_ok(code)
    A, c = restart()
    assert datakey.unwrap_password(PW) is None and datakey.unwrap_password("a brand new password")

def test_tampered_keyring_is_refused(client, tmp_path):
    A, c = client
    secret, _, _ = claim(c, A)
    import json
    ring = json.loads((tmp_path / "keyring.json").read_text())
    from vault import keys as K
    _, other = K.generate_master()
    ring["master_pub"] = other
    (tmp_path / "keyring.json").write_text(json.dumps(ring))
    A, c = restart()
    r = c.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=E)
    assert "changed outside" in r.get_data(as_text=True)
    import datakey
    assert not datakey.is_unlocked() and not datakey._state["pending"]

def test_change_password_and_new_codes(client):
    A, c = client
    secret, codes, _ = claim(c, A)
    finish_setup(A)
    r = c.post("/security", data=dict(act="password", current="wrong", password="x" * 14, password2="x" * 14), environ_base=E)
    assert b"current password" in r.data
    c.post("/security", data=dict(act="password", current=PW, password="another good one", password2="another good one"), environ_base=E)
    r = c.post("/security", data=dict(act="codes", current="another good one"), environ_base=E)
    new = re.findall(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", r.get_data(as_text=True))
    assert len(new) == 10
    A, c = restart()
    sign_in(c, secret, pw="another good one", code=codes[0])
    assert not unlocked()
    A, c = restart()
    sign_in(c, secret, pw="another good one", code=new[0])
    assert unlocked()

def test_no_open_redirect(client):
    A, c = client
    import auth
    secret, _, _ = claim(c, A)
    finish_setup(A)
    c.post("/logout", environ_base=E)
    c.post("/login", data=dict(email="owner@example.com", password=PW, next="//evil.example/x"), environ_base=E)
    r = c.post("/login/2fa", data=dict(code=auth.totp(secret)), environ_base=E)
    assert "evil" not in (r.location or "")

def test_short_password_refused_and_off_network_blocked(client):
    A, c = client
    r = c.post("/setup/owner", data=dict(claim=sys.modules["netsec"].claim_code(), name="O", email="o@example.com",
                                         password="short", password2="short"), environ_base=E)
    assert "Passwords must match" in r.get_data(as_text=True)
    assert c.get("/setup/owner", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403

def test_failed_unlock_work_leaves_the_app_locked(client):
    A, c = client
    secret, _, _ = claim(c, A)
    finish_setup(A)
    A, c = restart()
    A.app.testing = False

    def boom():
        raise RuntimeError("a plugin migration failed")
    A.UNLOCK_HOOKS.append(boom)
    r = sign_in(c, secret)
    assert r.status_code == 500 and not unlocked()
    assert c.get("/healthz", environ_base=E).get_json()["locked"] is True
    A.UNLOCK_HOOKS.remove(boom)
    import auth, time
    sign_in(c, secret, code=auth.totp(secret, at=time.time() + 30))
    assert unlocked() and c.get("/", environ_base=E).status_code == 200

def test_unlock_key_file_must_be_on_another_volume(client, tmp_path, monkeypatch):
    A, c = client
    claim(c, A)
    import datakey
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(tmp_path / "keys" / "unlock.key"))
    with pytest.raises(ValueError, match="different volume"):
        datakey.enable_auto()
    assert not datakey.auto_enabled()

def test_tampered_keyring_with_auto_unlock_starts_locked(client, tmp_path, monkeypatch):
    A, c = client
    claim(c, A)
    import datakey, json
    from pyrage import x25519
    import pyrage, base64
    ident = x25519.Identity.generate()
    (tmp_path / "unlock.key").write_text(str(ident))
    ring = json.loads((tmp_path / "keyring.json").read_text())
    ring["auto"] = base64.b64encode(pyrage.encrypt(datakey.dk(), [ident.to_public()])).decode()
    ring["auto_pub"] = str(ident.to_public())
    (tmp_path / "keyring.json").write_text(json.dumps(ring))
    monkeypatch.setenv("CRM_UNLOCK_KEY_FILE", str(tmp_path / "unlock.key"))
    A, c = restart()
    assert not unlocked()

def test_about_is_open_and_lists_what_it_includes(client):
    A, c = client
    r = c.get("/about", environ_base=E)
    t = r.get_data(as_text=True)
    assert r.status_code == 200 and "Walk Up Web LLC" in t and "Affero" in t and "github.com/lumenaureum" in t
    assert "Flask" in t and "sqlcipher3" in t and "gunicorn" in t
    import about
    v = __import__("importlib.metadata").metadata.version("flask")
    assert f"Flask {v}" not in t and "Version " not in t

def test_gateway_refusal_message_is_whole(monkeypatch):

    import inspect
    import auth
    src = inspect.getsource(auth)
    assert ("This connection arrived through Docker's port proxy, which hides where it came from, so it is refused. "
            "Connect from another device on your network, or list this address in CRM_ALLOW if you mean to allow it.").replace(" ", "") \
        in src.replace('"\n', "").replace('"', "").replace(" ", "").replace("\n", "")
