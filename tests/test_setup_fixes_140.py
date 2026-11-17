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
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    return A, signed_in_client(A, setup_done=False), tmp_path

def post(c, url, **data):
    return c.post(url, data=data, environ_base=E)

def key_line():
    import paramiko
    k = paramiko.ECDSAKey.generate()
    return k, f"{k.get_name()} {k.get_base64()}"

@pytest.fixture
def server(monkeypatch):

    import setup
    _, line = key_line()
    fp = setup.B.host_key_fingerprint(line)
    fetched, tested = [], []
    monkeypatch.setattr(setup.B, "fetch_host_key", lambda host, port=22, **kw: fetched.append(host) or (line, fp))
    monkeypatch.setattr(setup, "test_backend", lambda cfg, **kw: tested.append(dict(cfg)) or "")
    return dict(line=line, fp=fp, fetched=fetched, tested=tested)

def trust_box(page, p):
    m = re.search(r'<label class="choice trustbox" data-trust="%s"([^>]*)><input type="checkbox" name="%s_trust" value="1"([^>]*)>'
                  % (p, p), page)
    assert m, page
    return dict(hidden="hidden" in m.group(1), ticked="checked" in m.group(2))

def hidden_key(page, p):
    return re.search(r'name="%s_host_key" value="([^"]*)"' % p, page).group(1)

SFTP = dict(type="sftp", host="files.example.com", user="studio", root="backups", keymode="app")

def form(prefix, d):
    return {prefix + k: v for k, v in d.items()}

def test_test_and_continue_shows_the_fingerprint_for_a_new_web_host(env, server):
    A, c, tmp = env
    f = dict(gallery_days="30", unopened_days="14", gallery_base_url="https://example.com/g/", **form("gallery_", SFTP))
    r = post(c, "/setup/galleries", **f)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "First connection to files.example.com" in page
    assert server["fp"] in page and hidden_key(page, "gallery") == server["line"]
    assert trust_box(page, "gallery") == dict(hidden=False, ticked=False)
    assert server["tested"] == []
    assert "known_hosts" not in page
    r = post(c, "/setup/galleries", gallery_trust="1", gallery_host_key=server["line"], **f)
    assert r.status_code == 302, r.get_data(as_text=True)
    assert server["tested"][-1]["host_key"] == server["line"]
    from vault import web as VW
    assert VW.config()["gallery_host"]["host_key"] == server["line"]

def test_every_sftp_location_on_the_backups_step_gets_its_fingerprint(env, server):
    A, c, tmp = env
    f = dict(backup_time="02:40", **form("primary_", SFTP), **form("secondary_", dict(SFTP, host="offsite.example.net")))
    page = post(c, "/setup/storage", **f).get_data(as_text=True)
    assert "Primary location: First connection to files.example.com" in page
    assert "Second location: First connection to offsite.example.net" in page
    assert server["fetched"] == ["files.example.com", "offsite.example.net"] and server["tested"] == []
    for p in ("primary", "secondary"):
        assert trust_box(page, p)["hidden"] is False and hidden_key(page, p) == server["line"]
    r = post(c, "/setup/storage", primary_trust="1", primary_host_key=server["line"], secondary_trust="1",
             secondary_host_key=server["line"], **f)
    assert r.status_code == 302, r.get_data(as_text=True)
    assert [t["host_key"] for t in server["tested"]] == [server["line"]] * 2

def test_a_changed_server_key_is_offered_in_plain_words(env, monkeypatch):
    A, c, tmp = env
    import paramiko
    import setup
    from vault import web as VW
    old_k, old = key_line()
    new_k, new = key_line()
    VW.write_config({"primary": dict(SFTP, host_key=old)})

    class Refused:
        def put_stream(self, *a):
            raise paramiko.BadHostKeyException("files.example.com", new_k, old_k)
    monkeypatch.setattr(setup.B, "from_config", lambda cfg: Refused())
    page = post(c, "/setup/storage", backup_time="02:40", **form("primary_", SFTP)).get_data(as_text=True)
    assert "files.example.com&#39;s key changed since you trusted it" in page
    assert "known_hosts" not in page and "BadHostKey" not in page
    assert hidden_key(page, "primary") == new and setup.B.host_key_fingerprint(new) in page
    assert trust_box(page, "primary")["hidden"] is False

def test_no_separate_test_buttons_on_the_setup_steps(env):
    A, c, tmp = env
    for step in ("storage", "galleries"):
        page = c.get(f"/setup/{step}", environ_base=E).get_data(as_text=True)
        assert "data-test=" not in page and "Test upload" not in page and "Test connection" not in page
        assert "Test and continue" in page

def test_no_known_hosts_in_any_plain_error():
    import paramiko
    import setup
    cfg = dict(SFTP)
    for e in (FileNotFoundError(2, "No such file", "/data/.ssh/known_hosts"),
              paramiko.SSHException("Server 'files.example.com' not found in known_hosts")):
        msg = setup.plain_error(e, cfg)
        assert "known_hosts" not in msg and "files.example.com" in msg, msg
    for name in ("templates/setup.html", "setup.py", "static/setup.js"):
        text = open(os.path.join(ROOT, name)).read()
        assert "known_hosts first" not in text and "isn't in known_hosts" not in text

def test_a_failed_try_keeps_what_was_typed_on_the_web_host_step(env, server, monkeypatch):
    A, c, tmp = env
    f = dict(gallery_days="45", unopened_days="21", gallery_base_url="http://typed.example.com/g/",
             gallery_trust="1", gallery_host_key=server["line"], **form("gallery_", dict(SFTP, port="2222", keymode="file",
                                                                                      key_file="/keys/id")))
    page = post(c, "/setup/galleries", **f).get_data(as_text=True)
    assert "must start with https://" in page
    assert 'value="http://typed.example.com/g/"' in page and 'value="2222"' in page and 'value="/keys/id"' in page
    assert 'value="45"' in page and 'value="21"' in page and 'value="files.example.com"' in page
    assert re.search(r'name="gallery_keymode" value="file" checked', page)
    assert re.search(r'<option value="sftp" selected>', page)
    assert trust_box(page, "gallery") == dict(hidden=False, ticked=True) and hidden_key(page, "gallery") == server["line"]

def test_a_failed_try_keeps_what_was_typed_on_other_steps(env):
    A, c, tmp = env
    page = post(c, "/setup/business", business_name="North Light", photographer_name="Sam", business_email="sam@example.com",
                timezone="America/Denver", home_zip="00000").get_data(as_text=True)
    assert "ZIP table" in page and 'value="North Light"' in page and 'value="sam@example.com"' in page
    assert "<option selected>America/Denver</option>" in page and 'value="00000"' in page
    page = post(c, "/setup/money", deposit="-5", event_pct="30", net_days="14", reschedule_fee="25", delivery_days="21",
                event_delivery_days="30", bal_portrait="after", pay_check="1", pay_other="Zelle").get_data(as_text=True)
    assert "can&#39;t be negative" in page and 'value="-5"' in page and 'value="Zelle"' in page
    assert re.search(r'name="bal_portrait" value="after" checked', page)
    assert re.search(r'name="pay_check" value="1" checked', page) and not re.search(r'name="pay_cash" value="1" checked', page)

def test_a_typed_secret_waits_on_the_server_for_the_retry(env, monkeypatch):
    A, c, tmp = env
    import setup
    answers, tested = ["The server refused the key.", ""], []
    monkeypatch.setattr(setup, "test_backend", lambda cfg, **kw: tested.append(dict(cfg)) or answers.pop(0))
    s3 = form("primary_", dict(type="s3", bucket="studio", endpoint="https://s3.example.com", access_key="AK"))
    page = post(c, "/setup/storage", backup_time="02:40", primary_secret_key="typed-secret-123", **s3).get_data(as_text=True)
    assert "refused" in page and "typed-secret-123" not in page
    assert "kept from your last try" in page and 'value="studio"' in page and 'value="https://s3.example.com"' in page
    r = post(c, "/setup/storage", backup_time="02:40", primary_secret_key="", **s3)
    assert r.status_code == 302 and tested[-1]["secret_key"] == "typed-secret-123"
    assert setup._PENDING == {}

def test_a_mounted_vault_is_filled_in_as_a_value(env, monkeypatch):
    A, c, tmp = env
    import setup
    mnt = tmp / "vaultmount"; mnt.mkdir()
    monkeypatch.setattr(setup, "VAULT_MOUNT", str(mnt))
    page = c.get("/setup/storage", environ_base=E).get_data(as_text=True)
    assert f'name="primary_path" placeholder="e.g. /vault" value="{mnt}"' in page
    assert "has a /vault folder mounted for backups" in page

def test_placeholders_read_as_examples(env):
    A, c, tmp = env
    pages = "".join(c.get(f"/setup/{s}", environ_base=E).get_data(as_text=True) for s in ("storage", "galleries"))
    for ph in ("e.g. /vault", "e.g. /backup2", "e.g. https://example.com/g/", "e.g. /galleries", "Port (blank for 22)"):
        assert f'placeholder="{ph}"' in pages, ph
    for old in ('placeholder="/vault"', 'placeholder="https://example.com/g/"', 'placeholder="Port (22)"'):
        assert old not in pages

def test_s3_tls_errors_in_plain_words():
    import setup
    cfg = dict(type="s3", bucket="b", endpoint="https://nas.example.com:9000")
    wrong = Exception("SSL validation failed for https://nas.example.com:9000/b [SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1006)")
    assert setup.plain_error(wrong, cfg) == "This address doesn't answer with HTTPS. Check the port, or whether the server uses https."
    selfs = Exception("SSL validation failed for https://nas.example.com:9000/b [SSL: CERTIFICATE_VERIFY_FAILED] certificate "
                      "verify failed: self-signed certificate (_ssl.c:1006)")
    assert setup.plain_error(selfs, cfg) == ("Its certificate isn't trusted (self-signed). Lumen Aureum can't use a "
                                             "self-signed S3 certificate yet.")
    other = Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")
    assert "SSL" not in setup.plain_error(other, cfg)

@pytest.mark.parametrize("container", [True, False])
def test_who_can_reach_this_names_the_request_and_both_install_kinds(env, monkeypatch, container):
    A, c, tmp = env
    import setup
    monkeypatch.setattr(setup, "in_container", lambda: container)
    page = c.get("/setup/network", environ_base=E).get_data(as_text=True)
    assert "This request came from 127.0.0.1" in page and "This device:" not in page
    assert "router or VPN can share one address" in page
    if container:
        assert "compose.yaml" in page and "Additional Environment Variables" in page
    else:
        assert "compose.yaml" not in page and "environment the app starts with" in page
