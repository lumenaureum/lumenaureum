import ipaddress
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "192.168.1.20"}
OTHER = {"REMOTE_ADDR": "192.168.1.66"}
PW = "correct horse battery"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    return A, tmp_path

def claim(c):
    import auth, netsec
    c.post("/setup/owner", data=dict(claim=netsec.claim_code(), name="Owner", email="owner@example.com", password=PW,
                                     password2=PW), environ_base=E)
    with c.session_transaction() as s:
        st = auth.SETUP[s["setup_tok"]]
    c.post("/setup/owner/verify", data=dict(code=auth.totp(st["totp"])), environ_base=E)
    page = c.get("/setup/owner/key", environ_base=E).get_data(as_text=True)
    master = re.search(r"AGE-SECRET-KEY-1[0-9A-Z]+", page).group(0)
    c.post("/setup/owner/key", data=dict(tail=master[-6:]), environ_base=E)
    return st["totp"]

def test_plain_http_always_refused(env):
    A, _ = env
    c = A.app.test_client()
    r = c.get("/setup/owner", environ_base=E, base_url="http://localhost")
    assert r.status_code == 403 and b"HTTPS" in r.data
    assert c.get("/healthz", environ_base=E, base_url="http://localhost").status_code == 200
    assert c.get("/setup/owner", environ_base=E).status_code == 200

def test_tls_off_only_behind_a_trusted_proxy(monkeypatch):
    monkeypatch.setenv("CRM_TLS", "off")
    monkeypatch.delenv("CRM_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("CRM_PLAIN_HTTP_OK", "1")
    fresh_modules()
    import netsec
    assert netsec.startup_problem()
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", "10.0.0.5")
    fresh_modules()
    import netsec as n2
    assert not n2.startup_problem()

def test_unknown_host_refused(env):
    A, _ = env
    c = A.app.test_client()
    assert c.get("/setup/owner", environ_base=E, headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/setup/owner", environ_base=E, headers={"Host": "192.168.1.5:8780"}).status_code == 200
    assert c.get("/setup/owner", environ_base=E, headers={"Host": "localhost:8780"}).status_code == 200

def test_forwarded_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", "10.9.9.9")
    monkeypatch.setenv("CRM_ALLOW_ONLY", "192.168.1.0/24")
    fresh_modules()
    import app as A
    c = A.app.test_client()

    assert c.get("/healthz", environ_base=E, headers={"X-Forwarded-For": "192.168.1.9"}).status_code == 400

    r = c.get("/setup/owner", environ_base={"REMOTE_ADDR": "10.9.9.9"},
              headers={"X-Forwarded-For": "192.168.1.9", "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    r = c.get("/setup/owner", environ_base={"REMOTE_ADDR": "10.9.9.9"},
              headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"})
    assert r.status_code == 403
    r = c.get("/setup/owner", environ_base={"REMOTE_ADDR": "10.9.9.9"}, base_url="http://localhost",
              headers={"X-Forwarded-For": "192.168.1.9", "X-Forwarded-Proto": "http"})
    assert r.status_code == 403

def test_docker_gateway_refused_unless_listed(env, monkeypatch):
    A, _ = env
    import netsec
    monkeypatch.setattr(netsec, "GATEWAY", ipaddress.ip_address("172.17.0.1"))
    c = A.app.test_client()
    r = c.get("/setup/owner", environ_base={"REMOTE_ADDR": "172.17.0.1"})
    assert r.status_code == 403 and b"Docker" in r.data
    monkeypatch.setenv("CRM_ALLOW", "172.17.0.1/32")
    assert c.get("/setup/owner", environ_base={"REMOTE_ADDR": "172.17.0.1"}).status_code == 200

def test_claim_code_required(env):
    A, _ = env
    c = A.app.test_client()
    r = c.post("/setup/owner", data=dict(claim="0000-0000-0000", name="Mallory", email="m@example.com", password=PW,
                                         password2=PW), environ_base=E)
    assert b"setup code" in r.data
    import netsec
    assert netsec.claim_code() in netsec.code_file().read_text()
    with c.session_transaction() as s:
        assert "setup_tok" not in s

def test_lockout_is_per_address_and_reported(env):
    A, _ = env
    c = A.app.test_client()
    secret = claim(c)
    c.post("/logout", environ_base=E)
    stranger = A.app.test_client()
    for _ in range(6):
        stranger.post("/login", data=dict(email="owner@example.com", password="wrong wrong wrong"), environ_base=OTHER)
    r = stranger.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=OTHER)
    assert "Wait 15 minutes" in r.get_data(as_text=True)
    import auth
    c.post("/login", data=dict(email="owner@example.com", password=PW), environ_base=E)
    c.post("/login/2fa", data=dict(code=auth.totp(secret, at=time.time() + 30)), environ_base=E)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')"); con.commit(); con.close()
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert "failed attempts since the last sign-in" in page and "192.168.1.66" in page

def test_logout_ends_the_session_everywhere(env):
    A, _ = env
    c = signed_in_client(A)
    cookie = c.get_cookie("session").value
    assert c.get("/", environ_base=E).status_code == 200
    c.post("/logout", environ_base=E)
    thief = A.app.test_client()
    thief.set_cookie("session", cookie)
    assert "/login" in thief.get("/", environ_base=E).location

def test_totp_code_works_once():
    import auth
    secret = auth.new_totp_secret()
    now = time.time()
    code = auth.totp(secret, at=now)
    step = auth.totp_ok(secret, code, at=now)
    assert step and not auth.totp_ok(secret, code, at=now, after=step)

def test_cross_site_post_refused(env):
    A, _ = env
    c = signed_in_client(A)
    r = c.post("/settings", data={"business_name": "x"}, environ_base=E,
               headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403

def test_security_headers_and_versions(env):
    A, _ = env
    c = A.app.test_client()
    r = c.get("/login", environ_base=E)
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    t = c.get("/about", environ_base=E).get_data(as_text=True)
    assert "Version " not in t

def test_back_links_stay_local_and_long_input_refused(env):
    A, _ = env
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Harlow Family')")
    con.execute("INSERT INTO sessions (household_id, type) VALUES (1, 'family')")
    con.execute("INSERT INTO tasks (session_id, title) VALUES (1, 'Call them')")
    con.commit(); con.close()
    r = c.post("/task/1", data={"back": "//evil.example/x"}, environ_base=E)
    assert "evil" not in (r.location or "")
    r = c.post("/contact/new", data={"contact_name": "x" * 201}, environ_base=E)
    assert r.status_code == 413

def test_client_delete_and_export(env):
    A, tmp = env
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name, address) VALUES ('Harlow Family', '12 Elm St')")
    con.execute("INSERT INTO people (household_id, name, role) VALUES (1, 'Ava Harlow', 'child')")
    con.execute("INSERT INTO sessions (household_id, type) VALUES (1, 'family')")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Session', 100, 'fee')")
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Sales tax (6%)', 6, 'tax')")
    con.execute("INSERT INTO payments (session_id, received, amount, kind, method) VALUES (1, '2026-03-02', 106, 'payment', 'cash')")
    con.commit(); con.close()
    c.post("/client/1/note", data={"summary": "Ava loves the river", "channel": "note"}, environ_base=E)
    data = c.get("/client/1/export.json", environ_base=E).get_json()
    assert data["households"][0]["name"] == "Harlow Family" and data["people"][0]["name"] == "Ava Harlow"
    assert "/client/1" in c.post("/client/1/delete", data={"confirm": "nope"}, environ_base=E).location
    c.post("/client/1/delete", data={"confirm": "DELETE"}, environ_base=E)
    con = A.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM households").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        left = [r[0] + " " + (r[1] or "") for r in con.execute("SELECT action, note FROM audit")]
        assert not any("Harlow" in x_ or "Ava" in x_ or "river" in x_ for x_ in left)
        assert any("Deleted a client record" in x_ for x_ in left)
        assert [tuple(r) for r in con.execute("SELECT received, amount, tax FROM books")] == [("2026-03-02", 106.0, 6.0)]
    finally:
        con.close()

def test_rereview_fixes(env):

    A, tmp = env
    import auth
    th = auth.Throttle()
    th.MAX_TRACKED = 50
    for i in range(300):
        th.ip_blocked(f"10.0.{i // 250}.{i % 250}")
        th.fail(f"10.1.{i // 250}.{i % 250}")
    assert len(th.ip) <= 51
    with pytest.raises(ValueError):
        A._check_filter("1=1; DROP TABLE households")
    c = signed_in_client(A)
    other = A.app.test_client()
    with other.session_transaction() as s:
        s["uid"] = 1
        s["sid"] = auth.start_session(1)
    assert other.get("/", environ_base=E).status_code == 200
    import datakey
    from vault import keys as K

    sec, pub = K.generate_master()
    datakey.replace_master(pub)
    import netsec as N_
    r = A.app.test_client().post("/unlock/master", data=dict(secret=sec, password="a whole new password",
                                                          password2="a whole new password", code=N_.recovery_code()),
                                 environ_base=E)
    assert "/login" in other.get("/", environ_base=E).location

    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Harlow Family')"); con.commit()
    snap = tmp / "before.db"
    import db
    db.snapshot(snap)
    con.close()
    c2 = signed_in_client(A)
    c2.post("/client/1/delete", data={"confirm": "DELETE"}, environ_base=E)
    import shutil
    shutil.copyfile(snap, A.DB_PATH)
    for s_ in ("-wal", "-shm"):
        __import__("pathlib").Path(str(A.DB_PATH) + s_).unlink(missing_ok=True)
    assert A.reapply_deletions() == 1
    con = A.connect()
    assert con.execute("SELECT COUNT(*) FROM households").fetchone()[0] == 0
    con.close()

def test_health_probe_from_loopback_passes_a_narrow_allow_list(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_ALLOW_ONLY", "100.64.0.0/10")
    fresh_modules()
    import app as A
    c = A.app.test_client()
    lo = {"REMOTE_ADDR": "127.0.0.1"}
    assert c.get("/healthz", environ_base=lo).status_code == 200
    assert c.get("/setup/owner", environ_base=lo).status_code == 403
    assert c.get("/healthz", environ_base=E).status_code == 403

def test_no_inline_script_anywhere(env):

    import pathlib
    A, _ = env
    c = A.app.test_client()
    csp = c.get("/login", environ_base=E).headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    root = pathlib.Path(A.__file__).parent
    handler = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.I)
    bare = re.compile(r"<script(?![^>]*\b(src|nonce)=)[^>]*>", re.I)
    for f in list((root / "templates").glob("*.html")) + list((root / "plugins").glob("*.py")):
        t = "\n".join(ln for ln in f.read_text().splitlines() if "re.findall" not in ln)
        assert not handler.search(t), f"inline handler in {f.name}"
        assert not bare.search(t), f"inline <script> in {f.name}"

def test_csp_nonce_for_plugin_scripts(env):
    A, _ = env
    with A.app.test_request_context("/login", environ_base=E, base_url="https://localhost"):
        n = A.csp_nonce()
        assert n == A.csp_nonce() and len(n) >= 20
        resp = A._no_store(A.app.response_class("x"))
        assert f"'nonce-{n}'" in resp.headers["Content-Security-Policy"]

def test_health_probe_ignores_forwarded_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_ALLOW_ONLY", "100.64.0.0/10")
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", "10.9.9.9")
    fresh_modules()
    import app as A
    c = A.app.test_client()
    r = c.get("/healthz", environ_base={"REMOTE_ADDR": "10.9.9.9"}, headers={"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"})
    assert r.status_code == 403
