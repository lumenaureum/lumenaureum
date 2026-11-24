import os
import re
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
    return A, tmp_path

def folder(tmp, name):
    g = tmp / "srv" / name
    g.mkdir(parents=True)
    (g / ".vault-root").write_text("")
    return g

def save(c, path, url):
    return c.post("/setup/galleries", data=dict(gallery_days="30", unopened_days="14", gallery_type="local",
                                                gallery_path=str(path), gallery_base_url=url), environ_base=E)

def notice(c):
    with c.session_transaction() as s:
        return s.get("notice") or ""

@pytest.mark.parametrize("host,url,top", [
    ({"type": "sftp", "host": "example.com", "root": "example.com/g"}, "https://example.com/g/", False),
    ({"type": "sftp", "host": "example.com", "root": "public_html/g"}, "https://example.com/g/", False),
    ({"type": "sftp", "host": "example.com", "root": "public_html"}, "https://example.com/g/", True),
    ({"type": "sftp", "host": "example.com", "root": "example.com"}, "https://example.com/g/", True),
    ({"type": "sftp", "host": "example.com", "root": ""}, "https://example.com/g/", True),
    ({"type": "sftp", "host": "example.com", "root": "/var/www/"}, "https://example.com/g/", True),
    ({"type": "local", "path": "/galleries"}, "https://example.com/g/", False),
    ({"type": "local", "path": "/srv/htdocs"}, "https://example.com/g/", True),
    ({"type": "local", "path": "/galleries"}, "https://example.com/", True),
    ({"type": "local", "path": "/galleries"}, "https://example.com", True),
    ({"type": "s3", "bucket": "b", "prefix": ""}, "https://b.example.com/g/", False),
    ({"type": "s3", "bucket": "b", "prefix": ""}, "https://b.example.com/", True),
    (None, "", False),
])
def test_at_site_top(env, host, url, top):
    from plugins.gallery import at_site_top
    assert at_site_top(host, url) is top

def test_a_folder_of_its_own_saves_with_no_warning(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    r = save(c, folder(tmp, "g"), "https://example.com/g/")
    assert r.status_code == 302, r.data
    assert "top of your website" not in notice(c)

def test_the_top_of_a_website_is_saved_with_a_warning(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    g = folder(tmp, "www")
    r = save(c, g, "https://example.com/")
    assert r.status_code == 302, r.data
    from vault import web as VW
    host = VW.config()["gallery_host"]
    assert (host["type"], host["path"]) == ("local", str(g))
    n = notice(c)
    assert n.startswith("Saved.") and "top of your website" in n and "/g/" in n

def test_the_web_host_card_keeps_the_warning_with_its_bubble(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=True)
    assert save(c, folder(tmp, "public_html"), "https://example.com/g/").status_code == 302
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    m = re.search(r'<div class="hint" id="site-top-saved"[^>]*>(.*?)</div>', page, re.S)
    assert m, "the saved web host's card shows the warning"
    assert "top of your website" in m.group(1) and 'class="qh"' in m.group(1)
    assert "delete folders" in m.group(1) and "studio page" in m.group(1)

def test_the_form_carries_a_hidden_warning_for_the_live_check(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    m = re.search(r'<div class="hint" id="site-top"([^>]*)>(.*?)</div>', page, re.S)
    assert m and " hidden" in m.group(1) and "public_html" in m.group(1)
    assert 'class="qh"' in m.group(2)
    assert "site-top-saved" not in page

def test_a_refused_save_at_the_top_shows_the_warning_beside_the_refusal(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    r = save(c, folder(tmp, "g"), "http://example.com/")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    m = re.search(r'<div class="hint" id="site-top"([^>]*)>', page)
    assert m and " hidden" not in m.group(1)

def test_setup_script_fills_g_from_the_host():
    js = open(os.path.join(ROOT, "static", "setup.js"), encoding="utf-8").read()
    assert "'{d}/g'" in js and "tpl.replace('{d}', d)" in js and "'https://' + d + '/g/'" in js
    assert "getElementById('site-top')" in js
