import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_s3 import ACCESS, SECRET, FakeS3, make_cert
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    from vault import s3 as S3
    monkeypatch.setattr(S3, "SLEEP", lambda s: None)
    return A, signed_in_client(A, setup_done=False), tmp_path

def s3_form(endpoint, secret=SECRET, **kw):
    return dict(dict(primary_type="s3", primary_bucket="test-bucket", primary_endpoint=endpoint,
                     primary_access_key=ACCESS, primary_secret_key=secret, backup_time="02:40"), **kw)

def history(A):
    con = A.connect()
    try:
        return " | ".join(r[0] for r in con.execute("SELECT action FROM audit").fetchall())
    except Exception:
        return ""
    finally:
        con.close()

def test_self_signed_s3_is_trusted_once_then_pinned_and_a_new_key_stops(env, tmp_path):
    A, c, tmp = env
    from vault import web as VW, backends as B
    f = FakeS3(tls=make_cert(tmp_path))
    port = f.port
    addr = f"127.0.0.1:{port}"
    try:
        r = c.post("/setup/storage", data=s3_form(f.endpoint), environ_base=E)
        page = r.get_data(as_text=True).replace('&#39;', "'")
        assert r.status_code == 200 and "Primary location: Its certificate isn't from a public authority" in page
        assert "The S3 storage's certificate isn't trusted" in page and "Trust this certificate" in page
        assert f'value="{f.endpoint}"' in page
        assert VW.config().get("primary", {}).get("type") != "s3" and not f.log
        seen = json.loads(A.connect().execute("SELECT value FROM settings WHERE key=?", ("s3_cert_seen:" + addr,)).fetchone()[0])
        sha = seen["cert"]["sha256"]
        assert seen["kind"] == "untrusted" and seen["cert"]["fp"] in page

        r = c.post("/setup/storage", data=s3_form(f.endpoint, secret="", s3cert_trust=f"{addr} {'0' * 64}"), environ_base=E)
        assert r.status_code == 200 and "changed again since it was shown" in r.get_data(as_text=True).replace('&#39;', "'")
        assert "s3_certs" not in VW.config()

        r = c.post("/setup/storage", data=s3_form(f.endpoint, secret="", s3cert_trust=f"{addr} {sha}"), environ_base=E)
        assert r.status_code == 302, r.get_data(as_text=True).replace('&#39;', "'")
        cfg = VW.config()
        assert cfg["s3_certs"][addr]["sha256"] == sha and cfg["primary"]["endpoint"] == f.endpoint
        assert cfg["primary"]["secret_key"] == SECRET
        assert any(m == "PUT" for m, *_ in f.log) and not f.objects["test-bucket"]
        assert A.connect().execute("SELECT 1 FROM settings WHERE key=?", ("s3_cert_seen:" + addr,)).fetchone() is None
        assert "Trusted the S3 storage's certificate at " + addr in history(A)
        page = c.get("/setup/storage", environ_base=E).get_data(as_text=True).replace('&#39;', "'")
        assert "The one you confirmed on" in page
    finally:
        f.close()

    g = FakeS3(tls=make_cert(tmp_path), port=port)
    try:
        with pytest.raises(Exception) as e:
            B.from_config(VW.config()["primary"]).put_stream("x.age", __import__("io").BytesIO(b"1"))
        assert "certificate changed" in str(e.value) and not g.log
        page = c.get("/setup/storage", environ_base=E).get_data(as_text=True).replace('&#39;', "'")
        assert "The S3 storage's certificate changed. Backups there are stopped until you decide." in page
        assert "Trust the new certificate" in page and "Keep the old one" in page
        r = c.post("/setup/storage", data=s3_form(g.endpoint, secret="", s3cert_refuse=addr), environ_base=E)
        page = r.get_data(as_text=True).replace('&#39;', "'")
        assert "You refused this one" in page and "Keep the old one" not in page
        assert VW.config()["s3_certs"][addr]["sha256"] == sha
    finally:
        g.close()

def test_plain_http_only_on_the_owners_network(env, monkeypatch):
    A, c, tmp = env
    r = c.post("/setup/storage", data=s3_form("http://93.184.215.14:9000"), environ_base=E)
    page = r.get_data(as_text=True).replace('&#39;', "'")
    assert r.status_code == 200 and "Plain http:// is accepted only for S3 storage on your own network" in page
    from vault import web as VW
    VW.write_config({"primary": {"type": "s3", "bucket": "b", "endpoint": "http://192.168.20.5:9000",
                                 "access_key": "AK", "secret_key": "s"}})
    page = c.get("/setup/storage", environ_base=E).get_data(as_text=True).replace('&#39;', "'")
    assert "This address uses plain http://. The app accepts it only for storage on your own network" in page
    assert "(plain http, accepted only on your own network)" in VW.describe(VW.config()["primary"])

def test_plain_http_s3_on_the_lan_works_end_to_end(env):
    A, c, tmp = env
    f = FakeS3()
    try:
        r = c.post("/setup/storage", data=s3_form(f.endpoint, primary_addressing="path"), environ_base=E)
        assert r.status_code == 302, r.get_data(as_text=True).replace('&#39;', "'")
        from vault import web as VW
        assert VW.config()["primary"]["addressing"] == "path"
    finally:
        f.close()

def test_settings_import_takes_lan_http_and_addressing():
    fresh_modules()
    import settings_io
    st = {"primary": {"type": "s3", "bucket": "b", "endpoint": "http://192.168.20.5:9000", "addressing": "path"}}
    out = settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "storage": st}), {})[3]
    assert out["primary"]["endpoint"] == "http://192.168.20.5:9000" and out["primary"]["addressing"] == "path"
    for bad in ({"endpoint": "http://93.184.215.14"}, {"addressing": "sideways"}):
        with pytest.raises(settings_io.ImportProblem):
            settings_io.parse(json.dumps({"lumen_aureum_settings": 1, "storage": {
                "primary": dict({"type": "s3", "bucket": "b"}, **bad)}}), {})

