import secrets

from flask.testing import FlaskClient

class HttpsClient(FlaskClient):

    def open(self, *args, **kw):
        kw.setdefault("base_url", "https://localhost")
        return super().open(*args, **kw)

TEST_PASSWORD = "test password 123"
MODULES = ("app", "auth", "config", "setup", "sun", "datakey", "db", "atrest", "rotate", "netsec", "about", "settings_io", "serve", "seed_demo")

def fresh_modules():

    import sys
    for m in [m for m in sys.modules if m in MODULES or m.startswith(("vault", "plugins"))]:
        del sys.modules[m]

def unlock_new_install(A, password=TEST_PASSWORD):

    import datakey
    from vault import keys as K
    if datakey.exists():
        datakey.set_unlocked(datakey.unwrap_password(password))
        A.on_unlock()
        return None
    secret, pub = K.generate_master()
    key = secrets.token_bytes(32)
    datakey.create_from(key, datakey.password_wrap(key, password), pub)
    datakey.set_unlocked(key)
    A.on_unlock()
    con = A.connect()
    K.set_master_pub(con, pub)
    con.close()
    return secret

def signed_in_client(A, setup_done=True):
    import datakey
    secret = None
    if not datakey.is_unlocked():
        secret = unlock_new_install(A)
    con = A.connect()
    try:
        con.execute("""INSERT OR IGNORE INTO users (id, name, email, pw_hash, totp_secret) VALUES
                       (1, 'Owner', 'owner@example.com', '', 'JBSWY3DPEHPK3PXP')""")
        if setup_done:
            con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
        con.commit()
    finally:
        con.close()
    A.app.test_client_class = HttpsClient
    c = A.app.test_client()
    import auth
    with c.session_transaction() as s:
        s["uid"] = 1
        s["sid"] = auth.start_session(1)
    c.master_secret = secret
    return c

def posted_envelope(buf):

    import base64
    from urllib.parse import parse_qs
    if buf and buf.startswith(b"d="):
        d = parse_qs(buf.decode())["d"][0]
        return base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    return buf
