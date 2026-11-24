import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
SECRETS = ("hunter2", "AKIAEXAMPLEKEY", "deadbeefcafe", "tok123456", "s3cretvalue", "bucket/private/path")

def _clean(text):
    for s_ in SECRETS:
        assert s_ not in text, (s_, text)

@pytest.mark.parametrize("raw, kept", [
    ("Max retries exceeded with url: https://nas.example.com:9000/bucket/private/path?X-Amz-Credential=AKIAEXAMPLEKEY"
     "&X-Amz-Signature=deadbeefcafe (Caused by timeout)", "https://nas.example.com:9000/..."),
    ("failed for sftp://owner:hunter2@files.example.net/home", "sftp://files.example.net/..."),
    ("login owner:hunter2@files.example.net refused", "@files.example.net refused"),
    ("bad request /b/x?token=tok123456&x=1", "/b/x?..."),
    ("api_key=AKIAEXAMPLEKEY password = hunter2 secret=s3cretvalue", "api_key=[hidden]"),
    ("{'secret_key': 's3cretvalue', 'Authorization': 'AWS4-HMAC-SHA256 Credential=AKIAEXAMPLEKEY, Signature=deadbeefcafe'}",
     "'secret_key': [hidden]"),
    ("Authorization: Bearer tok123456\nmore", "Authorization: [hidden]"),
    ("sent Bearer tok123456 to the server", "Bearer [hidden]"),
])
def test_scrub_hides_addresses_and_credentials(raw, kept):
    import errtext
    out = errtext.scrub(raw)
    _clean(out)
    assert kept in out, out
    assert "\n" not in out

def test_scrub_leaves_ordinary_words_alone_and_caps_the_length():
    import errtext
    for plain in ("Errno 111 Connection refused", "AuthenticationException: Authentication failed.",
                  "No such file: /backup2/lumenaureum"):
        assert errtext.scrub(plain) == plain
    long_ = errtext.scrub("x" * 1000)
    assert len(long_) == errtext.LIMIT and long_.endswith("...")
    assert len(errtext.scrub("y " * 300, 50)) <= 50

def test_named_and_words_fall_back_to_the_kind():
    import errtext
    assert errtext.named(ValueError()) == "ValueError"
    assert errtext.words(ValueError()) == "ValueError"
    assert errtext.named(OSError("at https://owner:hunter2@h.example/x?token=tok123456")) == "OSError: at https://h.example/..."

class Odd(Exception):
    pass

def test_plain_error_fallback_is_scrubbed_and_short():
    fresh_modules()
    import setup
    cfg = dict(type="s3", bucket="b", endpoint="https://nas.example.com:9000")
    e = Odd("PUT https://nas.example.com:9000/bucket/private/path?X-Amz-Signature=deadbeefcafe failed; "
            "headers={'Authorization': 'AWS4-HMAC-SHA256 Credential=AKIAEXAMPLEKEY'} " + "z" * 500)
    msg = setup.plain_error(e, cfg)
    _clean(msg)
    assert msg.startswith("Odd: PUT https://nas.example.com:9000/...") and len(msg) <= 200

    assert setup.plain_error(ConnectionRefusedError(), cfg) == "Can't reach that server."

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    monkeypatch.setattr(A, "STARTED_AT", time.monotonic())
    yield A, c
    A.PLUGIN_ERRORS.clear()

def test_test_now_keeps_and_shows_only_scrubbed_text(env, monkeypatch):
    A, c = env
    import setup

    class Broken:
        def put_stream(self, *a, **k):
            raise Odd("upload to sftp://owner:hunter2@files.example.net/bucket/private/path?token=tok123456 failed")

    cfg = dict(type="sftp", host="files.example.net", user="owner", host_key="files.example.net ssh-ed25519 AAAA")
    monkeypatch.setattr(setup.VW, "config", lambda: {"primary": cfg})
    monkeypatch.setattr(setup.B, "from_config", lambda c_: Broken())
    r = c.post("/setup/connection/primary/test", data={"back": "/settings"}, environ_base=E)
    assert r.status_code == 302
    with c.session_transaction() as s:
        notice = s.get("notice", "")
    assert notice.startswith("The test failed: Odd: upload to sftp://files.example.net/...")
    _clean(notice)
    con = A.connect()
    stored = con.execute("SELECT value FROM settings WHERE key='conn_status'").fetchone()[0]
    con.close()
    why = json.loads(stored)["primary"]["why"]
    assert json.loads(stored)["primary"]["ok"] is False and "files.example.net" in why
    _clean(stored)

def test_every_raw_error_site_goes_through_the_helper():

    def src(name):
        return open(os.path.join(ROOT, name)).read()
    setup_ = src("setup.py")
    assert 'return f"{type(e).__name__}: {e}"' not in setup_ and "errtext.named(e)" in setup_
    assert 'detail=str(e)[:200]' not in setup_
    app_ = src("app.py")
    assert 'f"{type(e).__name__}: {e}"[:200]' not in app_
    gal = src("plugins/gallery.py")
    for old in ("(str(e)[:200], sid)", "(str(e)[:120] or e.__class__.__name__)", 'f"held upload failed: {e}"',
                "({str(e)[:120]})"):
        assert old not in gal, old
    site = src("plugins/studio_page.py")
    assert 'f"Taking the site down stopped: {e}"' not in site and "{e}\"[:600]" not in site
    assert "words=str(e)" not in src("upgrade_140.py")
    assert "({e})" not in src("rotate.py")
