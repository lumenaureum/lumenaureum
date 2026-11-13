"""Test helpers: an unlocked install (keyring + encrypted database) and a client signed in as its owner."""
import secrets

from flask.testing import FlaskClient


class HttpsClient(FlaskClient):
    """The Flask test client, speaking HTTPS (the app refuses anything else). Pass base_url="http://..." to test that."""

    def open(self, *args, **kw):
        kw.setdefault("base_url", "https://localhost")
        return super().open(*args, **kw)

TEST_PASSWORD = "test password 123"
MODULES = ("app", "auth", "config", "setup", "sun", "datakey", "db", "atrest", "rotate", "netsec", "about", "settings_io", "serve", "seed_demo")


def fresh_modules():
    """Forget the app's modules so the next import reads the test's environment (CRM_DB etc.)."""
    import sys
    for m in [m for m in sys.modules if m in MODULES or m.startswith(("vault", "plugins"))]:
        del sys.modules[m]


def unlock_new_install(A, password=TEST_PASSWORD):
    """A keyring wrapped to `password` and a fresh master key; the app unlocked and its schema created.
    Returns the master secret (tests that restore need it)."""
    import datakey
    from vault import keys as K
    if datakey.exists():                       # a later process on the same test install: sign in again
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
        if setup_done:   # most tests are about the app, not first-run setup (tests/test_setup.py covers that)
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
