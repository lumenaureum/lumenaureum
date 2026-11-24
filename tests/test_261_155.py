import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
BASE = "https://app.test"

def _env(tmp_path, monkeypatch, setup_done=False):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    return A, signed_in_client(A, setup_done=setup_done)

@pytest.fixture
def env(tmp_path, monkeypatch):
    return _env(tmp_path, monkeypatch)

def _get(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _options(page, name):
    sel = re.search(r'<select id="f-%s"[^>]*>(.*?)</select>' % name, page, re.S).group(1)
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', sel), sel

def test_galleries_ask_where_they_are_hosted_by_name(env):
    A, c = env
    page = _get(c, "/setup/galleries")
    assert "Where are your galleries hosted?" in page
    opts, sel = _options(page, "gallery_type")
    assert [w for _, w in opts] == ["No online galleries for now", "DreamHost", "SiteGround", "Bluehost", "A web server at home",
                                    "Another web host: your web host&#39;s login", "Cloud storage (Backblaze, Wasabi, AWS...)"]
    assert '<optgroup label="Something else">' in sel
    assert "SFTP" not in sel and "S3" not in sel
    assert "Where your galleries live online" in page and "Web address of that folder" not in page

    for k in ("dreamhost", "siteground", "bluehost"):
        m = re.search(r'<div class="be hint" data-be="gallery_sftp\.%s">(.*?)</div>' % k, page, re.S)
        assert m and m.group(1).count("<br>") == 1, k
    assert "SFTP Users &amp; Files" in page and "SSH Keys Manager" in page and "cPanel" in page

def test_backups_offer_a_folder_first_and_the_second_copy_a_drive_or_the_nas_before_cloud(env):
    A, c = env
    page = _get(c, "/setup/storage")
    opts, sel = _options(page, "primary_type")
    assert opts[0] == ("local", "A folder on this machine or a mounted share") and 'value="local" selected' in sel
    opts, sel = _options(page, "secondary_type")
    vals = [v for v, _ in opts]
    assert vals[:3] == ["", "local.drive", "sftp.nas"]
    assert vals.index("s3.b2") > vals.index("sftp.nas")
    assert '<optgroup label="Cloud storage (Backblaze, Wasabi, AWS...)">' in sel
    names = dict(opts)
    assert names["s3.b2"] == "Backblaze B2" and names["s3.r2"] == "Cloudflare R2" and names["s3.wasabi"] == "Wasabi"
    assert names["s3.aws"] == "Amazon S3 (AWS)" and names["sftp.nas"] == "Your NAS" and names["local.drive"] == "A drive you plug in"
    assert "S3-compatible" not in page and "SFTP server" not in page
    for k in ("s3.b2", "s3.r2", "s3.wasabi", "s3.aws", "sftp.nas", "local.drive"):
        assert f'data-be="secondary_{k}"' in page, k
    assert "Application Keys" in page and "r2.cloudflarestorage.com" in page and "wasabisys.com" in page

def test_the_second_copy_links_offer_a_drive_and_the_nas(env, tmp_path, monkeypatch):
    A, c = env
    import setup
    from vault import web as VW
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    r = c.post("/setup/storage", data=dict(primary_type="local", primary_path=str(vault), primary_init="1",
                                           backup_time="02:40"), environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)[-1500:]
    assert VW.config()["primary"]["type"] == "local"
    A_ = A.connect()
    A_.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    A_.commit()
    A_.close()
    page = _get(c, "/setup/storage")
    links = re.search(r'id="f-secondary_type">(.*?)</p>', page, re.S).group(1)
    assert ">Plug in a drive &rsaquo;<" in links and ">Choose your NAS &rsaquo;<" in links
    assert "replace=secondary&amp;kind=local.drive" in links and "replace=secondary&amp;kind=sftp.nas" in links
    page = _get(c, "/setup/storage?replace=secondary&kind=sftp.nas")
    assert re.search(r'<option value="sftp.nas" selected', page)
    page = _get(c, "/setup/storage?replace=secondary&kind=nonsense")
    assert not re.search(r'<option value="[^"]*" selected[^>]*>[^<]*</option>', _options(page, "secondary_type")[1])

def _advanced(page, id_):
    m = re.search(r'<details class="ui-fold\s*" id="%s"( open)?>(.*?)</details>' % re.escape(id_), page, re.S)
    assert m, id_
    return bool(m.group(1)), m.group(2)

def test_port_key_file_region_prefix_and_addressing_wait_under_advanced(env):
    A, c = env
    page = _get(c, "/setup/galleries")
    opened, body = _advanced(page, "adv-gallery_sftp")
    assert not opened and 'name="gallery_port"' in body and 'value="file"' in body and 'name="gallery_key_file"' in body
    opened, body = _advanced(page, "adv-gallery_s3")
    assert not opened and 'name="gallery_region"' in body and 'name="gallery_s3prefix"' in body and 'name="gallery_addressing"' in body
    assert "authorized_keys" not in page.split('id="adv-gallery_sftp"')[0].split("data-show-if=\"gallery_keymode=app\"")[1].split("<span class=\"qh")[0]
    store = _get(c, "/setup/storage")
    for p in ("primary_", "secondary_"):
        opened, body = _advanced(store, f"adv-{p}sftp")
        assert not opened and f'name="{p}port"' in body and f'name="{p}keymode" value="file"' in body
        opened, body = _advanced(store, f"adv-{p}s3")
        assert not opened and f'name="{p}addressing"' in body

def test_advanced_opens_when_something_in_it_is_set(env, monkeypatch):
    A, c = env
    import setup
    monkeypatch.setattr(setup, "test_backend", lambda cfg, **kw: "Refused for the test.")
    page = c.post("/setup/galleries", data=dict(gallery_days="30", unopened_days="14", gallery_type="sftp.siteground",
                                                gallery_host="example.com", gallery_user="u", gallery_port="2200",
                                                gallery_trust="1", gallery_host_key="ssh-ed25519 AAAA",
                                                gallery_keymode="app", gallery_base_url="https://example.com/g/"),
                  environ_base=E).get_data(as_text=True)
    assert _advanced(page, "adv-gallery_sftp")[0]
    assert re.search(r'<option value="sftp.siteground" selected', page)

def test_presets_fill_what_is_certain_when_left_blank(env):
    import setup
    f = dict(gallery_type="sftp.siteground", gallery_host="example.com", gallery_user="u", gallery_keymode="file",
             gallery_key_file="/keys/id")
    assert setup.backend_from_form(f, "gallery_")["port"] == 18765
    f = dict(f, gallery_port="2222")
    assert setup.backend_from_form(f, "gallery_")["port"] == 2222
    c = setup.backend_from_form(dict(gallery_type="sftp.bluehost", gallery_host="example.com", gallery_user="u",
                                     gallery_keymode="password", gallery_password="pw"), "gallery_")
    assert c["root"] == "public_html/g" and "port" not in c and c["type"] == "sftp" and c["auth"] == "password"
    c = setup.backend_from_form(dict(gallery_type="sftp.dreamhost", gallery_host="example.com", gallery_user="u"), "gallery_")
    assert c["root"] == "" and c["type"] == "sftp"

    c = setup.backend_from_form(dict(gallery_type="sftp.retired", gallery_host="example.com", gallery_user="u",
                                     gallery_root="example.com/g"), "gallery_")
    assert c["type"] == "sftp" and c["root"] == "example.com/g" and c["host"] == "example.com" and "port" not in c
    c = setup.backend_from_form(dict(primary_type="local.drive", primary_path="/backup2"), "primary_")
    assert c == {"type": "local", "path": "/backup2"}

def test_a_cloud_address_pasted_as_the_provider_shows_it():
    fresh_modules()
    import setup
    c = setup.backend_from_form(dict(secondary_type="s3.b2", secondary_bucket="studio-backups",
                                     secondary_endpoint="s3.us-west-004.backblazeb2.com", secondary_access_key="k",
                                     secondary_secret_key="s"), "secondary_")
    assert c["type"] == "s3" and c["endpoint"] == "https://s3.us-west-004.backblazeb2.com"
    c = setup.backend_from_form(dict(secondary_type="s3.r2", secondary_bucket="studio-backups",
                                     secondary_endpoint="https://0123abcd.r2.cloudflarestorage.com/studio-backups",
                                     secondary_access_key="k", secondary_secret_key="s"), "secondary_")
    assert c["endpoint"] == "https://0123abcd.r2.cloudflarestorage.com"
    c = setup.backend_from_form(dict(secondary_type="s3.aws", secondary_bucket="b", secondary_access_key="k",
                                     secondary_secret_key="s"), "secondary_")
    assert "endpoint" not in c

def test_a_providers_address_names_its_region():
    fresh_modules()
    from vault import s3 as S3
    assert S3.region_of("s3.us-west-004.backblazeb2.com") == "us-west-004"
    assert S3.region_of("s3.eu-central-1.wasabisys.com") == "eu-central-1"
    assert S3.region_of("0123abcd.r2.cloudflarestorage.com") == "auto"
    assert S3.region_of("s3.example.com") is None and S3.region_of("") is None
    assert S3.Client("b", "k", "s", endpoint="https://s3.us-west-004.backblazeb2.com").region == "us-west-004"
    assert S3.Client("b", "k", "s", endpoint="https://s3.us-west-004.backblazeb2.com", region="x-1").region == "x-1"
    assert S3.Client("b", "k", "s", endpoint="https://nas.example:9000").region == "us-east-1"

def test_saved_connections_show_the_provider_in_plain_words():
    fresh_modules()
    import setup
    rows = dict(setup.conn_rows({"type": "s3", "bucket": "b", "endpoint": "https://s3.us-west-004.backblazeb2.com", "access_key": "k"}))
    assert rows["Kind"] == "Backblaze B2" and rows["Service address"].endswith("backblazeb2.com")
    rows = dict(setup.conn_rows({"type": "s3", "bucket": "b", "access_key": "k"}))
    assert rows["Kind"] == "Amazon S3 (AWS)" and "Service address" not in rows and "Endpoint" not in rows
    assert dict(setup.conn_rows({"type": "s3", "bucket": "b", "endpoint": "https://nas.example:9000"}))["Kind"] == "Cloud storage"
    assert dict(setup.conn_rows({"type": "sftp", "host": "h.example", "user": "u"}, True))["Kind"] == "Your web host's login"
    assert dict(setup.conn_rows({"type": "sftp", "host": "h.example", "user": "u"}))["Kind"] == "A server you sign in to"
    assert dict(setup.conn_rows({"type": "local", "path": "/g"}, True))["Kind"] == "A web server at home"
    assert setup.provider_of({"type": "s3", "endpoint": "https://s3.eu-central-1.wasabisys.com"}) == "s3.wasabi"
    assert setup.provider_of({"type": "sftp", "host": "iad1-shared-a1-23.dreamhost.com"}) == "sftp.dreamhost"
    assert setup.provider_of({"type": "local"}) == ""

def test_the_app_tries_the_other_addressing_style_and_keeps_the_one_that_works(monkeypatch):
    fresh_modules()
    import setup
    from vault import s3 as S3
    tried = []

    def trip(cfg):
        tried.append(cfg.get("addressing"))
        if cfg.get("addressing") != "virtual":
            raise S3.S3Error(404, "NoSuchBucket", "The specified bucket does not exist")
        return ""
    monkeypatch.setattr(setup, "_round_trip", trip)
    cfg = {"type": "s3", "bucket": "b", "endpoint": "https://s3.example.com", "access_key": "k", "secret_key": "s"}
    assert setup.test_backend(cfg) == "" and cfg["addressing"] == "virtual" and tried == [None, "virtual"]

    tried.clear()
    cfg = dict(cfg, addressing="path")
    assert setup.test_backend(cfg) != "" and tried == ["path"]

    tried.clear()
    assert setup.test_backend({"type": "s3", "bucket": "b", "endpoint": "https://192.168.1.5:9000", "access_key": "k",
                               "secret_key": "s"}) != "" and tried == [None]

    def down(cfg):
        tried.append(cfg.get("addressing"))
        raise ConnectionRefusedError()
    monkeypatch.setattr(setup, "_round_trip", down)
    tried.clear()
    assert setup.test_backend({"type": "s3", "bucket": "b", "endpoint": "https://s3.example.com", "access_key": "k",
                               "secret_key": "s"}) == "Can't reach that server." and tried == [None]

@pytest.fixture(scope="module")
def browser():
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")
    yield b
    b.close()
    pw.stop()

def _tab(browser, c, width=390):
    from urllib.parse import urlsplit

    def handle(route, request):
        u = urlsplit(request.url)
        r = c.get(u.path + ("?" + u.query if u.query else ""), environ_base=E)
        route.fulfill(status=r.status_code, body=r.data,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-security-policy")})
    ctx = browser.new_context(viewport={"width": width, "height": 844})
    ctx.route(BASE + "/**", handle)
    return ctx, ctx.new_page()

def test_choosing_a_web_host_fills_what_is_certain_and_says_where_to_find_the_rest(browser, env):
    A, c = env
    ctx, p = _tab(browser, c)
    try:
        p.goto(f"{BASE}/setup/galleries")
        p.select_option("#f-gallery_type", "sftp.siteground")
        assert p.locator('[data-be="gallery_sftp.siteground"]').is_visible()
        assert not p.locator('[data-be="gallery_sftp.bluehost"]').is_visible()
        assert p.locator("#f-gallery_host").is_visible() and not p.locator("#f-gallery_port").is_visible()
        assert p.input_value("#f-gallery_port") == "18765"
        assert "public_html" in p.get_attribute("#f-gallery_root", "placeholder")
        p.fill("#f-gallery_host", "example.com")
        assert p.input_value("#f-gallery_root") == ""
        assert p.input_value("#f-gallery_base_url") == "https://example.com/g/"
        p.select_option("#f-gallery_type", "sftp.bluehost")
        assert p.input_value("#f-gallery_port") == "" and p.input_value("#f-gallery_root") == "public_html/g"
        assert p.is_checked('input[name="gallery_keymode"][value="password"]')
        assert p.locator("#f-gallery_password").is_visible()
        p.select_option("#f-gallery_type", "sftp.dreamhost")
        assert p.input_value("#f-gallery_root") == "example.com/g"
        p.fill("#f-gallery_root", "mine/g")
        p.select_option("#f-gallery_type", "sftp.bluehost")
        assert p.input_value("#f-gallery_root") == "mine/g"
        p.goto(f"{BASE}/setup/storage")
        p.select_option("#f-secondary_type", "s3.b2")
        assert p.locator('[data-be="secondary_s3.b2"]').is_visible() and p.locator("#f-secondary_bucket").is_visible()
        assert "backblazeb2.com" in p.get_attribute("#f-secondary_endpoint", "placeholder")
        assert not p.locator("#f-secondary_region").is_visible()
        p.select_option("#f-secondary_type", "sftp.nas")
        assert p.locator('[data-be="secondary_sftp.nas"]').is_visible() and p.locator("#f-secondary_host").is_visible()
        assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        ctx.close()
