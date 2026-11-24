import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    return A, signed_in_client(A, setup_done=False), tmp_path

def post(c, url, **data):
    return c.post(url, data=data, environ_base=E)

def gallery_folder(tmp):
    g = tmp / "galleries"; g.mkdir(); (g / ".vault-root").write_text("")
    return g

def test_galleries_refuse_an_http_address(env):
    A, c, tmp = env
    g = gallery_folder(tmp)
    r = post(c, "/setup/galleries", gallery_days="30", unopened_days="14", gallery_type="local",
             gallery_path=str(g), gallery_base_url="http://photos.example.com/g/")
    assert r.status_code == 200
    assert b"must start with https://" in r.data and b"needs a certificate" in r.data
    from vault import web as VW
    assert "gallery_host" not in VW.config()

def test_galleries_accept_an_https_address(env):
    A, c, tmp = env
    g = gallery_folder(tmp)
    r = post(c, "/setup/galleries", gallery_days="30", unopened_days="14", gallery_type="local",
             gallery_path=str(g), gallery_base_url="https://photos.example.com/g")
    assert r.status_code == 302, r.data
    from vault import web as VW
    host = VW.config()["gallery_host"]
    assert (host["type"], host["path"]) == ("local", str(g))
    con = A.connect()
    try:
        assert con.execute("SELECT value FROM settings WHERE key='gallery_base_url'").fetchone()[0] == \
            "https://photos.example.com/g/"
    finally:
        con.close()

def test_backend_form_blank_secret_keeps_the_saved_one():
    import setup
    old = {"type": "s3", "bucket": "b", "access_key": "AK", "secret_key": "saved-secret"}
    form = dict(primary_type="s3", primary_bucket="b", primary_access_key="AK", primary_secret_key="  ")
    assert setup.backend_from_form(form, "primary_", old)["secret_key"] == "saved-secret"
    form["primary_secret_key"] = "fresh-secret"
    assert setup.backend_from_form(form, "primary_", old)["secret_key"] == "fresh-secret"
    form["primary_secret_key"] = ""
    assert setup.backend_from_form(form, "primary_", None)["secret_key"] == ""

def test_storage_step_with_blank_s3_secret_keeps_the_saved_secret(env, monkeypatch):
    A, c, tmp = env
    import setup
    from vault import web as VW
    VW.write_config({"primary": {"type": "s3", "bucket": "old-bucket", "access_key": "AK",
                                 "secret_key": "saved-secret", "prefix": ""}})
    tested = []
    monkeypatch.setattr(setup, "test_backend", lambda cfg, **kw: tested.append(dict(cfg)) or "")
    r = post(c, "/setup/storage", primary_type="s3", primary_bucket="new-bucket", primary_access_key="AK",
             primary_secret_key="", backup_time="02:40")
    assert r.status_code == 302, r.data
    assert tested and tested[0]["secret_key"] == "saved-secret"
    prim = VW.config()["primary"]
    assert prim["bucket"] == "new-bucket" and prim["secret_key"] == "saved-secret"
