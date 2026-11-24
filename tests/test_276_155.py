import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
WARN = "only works from this machine"

@pytest.mark.parametrize("url", [
    "https://127.0.0.1/g/", "https://127.9.8.7:8443/g/", "https://localhost/g/", "https://gallery.localhost/",
    "https://[::1]/g/", "https://169.254.169.254/latest/", "https://[fe80::1]/g/", "https://[::ffff:127.0.0.1]/",
])
def test_loopback_and_link_local_are_flagged(url):
    import netsec
    assert netsec.only_here(url)
    assert WARN in netsec.only_here_words(url)

@pytest.mark.parametrize("url", [
    "https://192.168.1.20/g/", "https://10.0.0.5/g/", "https://172.16.4.4/", "https://[fd00::5]/g/",
    "https://8.8.8.8/", "", "not a url", "https://", "https://[::1/",
])
def test_lan_internet_and_junk_are_not_flagged(url):
    import netsec
    assert netsec.only_here(url) == "" and netsec.only_here_words(url) == ""

def test_a_name_is_judged_by_what_it_resolves_to(monkeypatch):
    import netsec
    monkeypatch.setattr(netsec.socket, "getaddrinfo",
                        lambda h, p: [(2, 1, 6, "", ("169.254.169.254", 0))] if h == "meta.example" else
                        [(2, 1, 6, "", ("203.0.113.9", 0))])
    assert netsec.only_here("https://meta.example/g/") == "169.254.169.254"
    assert "meta.example (169.254.169.254)" in netsec.only_here_words("https://meta.example/g/")
    assert netsec.only_here("https://photos.example/g/") == ""

def test_a_name_that_does_not_resolve_is_not_flagged(monkeypatch):
    import netsec

    def boom(h, p):
        raise OSError("no such name")
    monkeypatch.setattr(netsec.socket, "getaddrinfo", boom)
    assert netsec.only_here("https://nowhere.example/") == ""

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

def _host(tmp_path, base):
    h = tmp_path / "host"
    h.mkdir(exist_ok=True)
    return dict(gallery_days="30", unopened_days="365", gallery_type="local", gallery_path=str(h), gallery_init="1",
                gallery_base_url=base)

def _setting(A, key):
    con = A.connect()
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()

def test_setup_step_saves_a_loopback_address_with_a_warning(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    r = c.post("/setup/galleries", data=_host(tmp, "https://127.0.0.1/g/"), environ_base=E)
    assert r.status_code == 302
    assert _setting(A, "gallery_base_url") == "https://127.0.0.1/g/"
    page = c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)
    assert WARN in page and "Saved." in page and "data-refused" not in page

def test_setup_step_says_nothing_for_a_lan_address(env):
    A, tmp = env
    c = signed_in_client(A, setup_done=False)
    r = c.post("/setup/galleries", data=_host(tmp, "https://192.168.1.20/g/"), environ_base=E)
    assert r.status_code == 302
    assert WARN not in c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)

def test_the_web_host_card_keeps_the_warning_with_its_help_bubble(env):
    A, tmp = env
    c = signed_in_client(A)
    assert c.post("/setup/galleries", data=_host(tmp, "https://[::1]/g/"), environ_base=E).status_code == 302
    c.get("/", environ_base=E)
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert 'class="sui-conn"' in page
    i = page.index("sui-conn-warn")
    line = page[i:page.index("</p>", i)]
    assert WARN in line and 'class="qh"' in line and "169.254" in line

def test_the_web_host_card_has_no_warning_for_a_lan_address(env):
    A, tmp = env
    c = signed_in_client(A)
    assert c.post("/setup/galleries", data=_host(tmp, "https://10.0.0.5/g/"), environ_base=E).status_code == 302
    assert "sui-conn-warn" not in c.get("/setup/galleries", environ_base=E).get_data(as_text=True)

def test_settings_save_warns_but_keeps_a_link_local_address(env):
    A, _ = env
    c = signed_in_client(A)
    c.post("/settings", data=dict(gallery_base_url="https://169.254.169.254/g/"), environ_base=E)
    assert _setting(A, "gallery_base_url") == "https://169.254.169.254/g/"
    assert WARN in c.get("/settings", environ_base=E, follow_redirects=True).get_data(as_text=True)

def test_s3_endpoint_on_loopback_gets_no_gallery_warning():
    import setup
    rows = setup.conn_rows({"type": "s3", "bucket": "b", "endpoint": "https://127.0.0.1:9000", "access_key": "k"})
    assert not any(WARN in v for _, v in rows)
    assert setup.gallery_only_here("https://203.0.113.9/g/") is None
