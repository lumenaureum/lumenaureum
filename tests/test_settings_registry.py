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
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1"}, environ_base=E)
    return A, c, tmp_path

def _page(c, url, follow=True):
    return c.get(url, environ_base=E, follow_redirects=follow).get_data(as_text=True)

def _sql(A, sql, args=()):
    con = A.connect()
    try:
        r = con.execute(sql, args).fetchall()
        con.commit()
        return r
    finally:
        con.close()

def _set(A, k, v):
    _sql(A, "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

def _url(A, p):
    with A.app.test_request_context():
        from flask import url_for
        return url_for("plugin_page", name=p["plugin"]) if p.get("plugin") else url_for(p["ep"], **p["args"])

def test_every_exported_setting_has_a_page_and_every_field_is_saved_by_a_form_on_it(env):
    A, c, _ = env
    import settings_io
    import settings_registry as R
    keys = [f_["key"] for f_ in R.fields() if f_["key"]]
    assert not [k for k in settings_io.SETTING_KEYS if k not in keys]
    ids = [p_["id"] for p_ in R.pages()]
    assert len(ids) == len(set(ids))
    pages = {p_["id"]: _page(c, _url(A, p_)) for p_ in R.pages()}
    for f_ in R.fields():
        if f_.get("optional"):
            continue
        html = pages[f_["page"]["id"]]
        inp = R.field_input(f_)
        if inp:
            assert f'name="{inp}' in html, (f_["page"]["id"], inp)
        if f_.get("anchor"):
            assert f'id="{f_["anchor"]}"' in html, (f_["page"]["id"], f_["anchor"])

def test_a_setting_lives_in_one_place(env):

    A, c, _ = env
    main = lambda url: _page(c, url).split('class="setmain', 1)[1]
    profile, clients, prices = main("/settings?tab=profile"), main("/settings?tab=clients"), main("/settings?tab=prices")
    for gone in ("f-pay_details", "f-bank_details", "f-age_majority", "f-idle_signout_hours", "f-credit", 'id="logo"',
                 "f-gallery_address"):
        assert gone not in profile, gone
    assert 'id="f-age_majority"' in clients and 'id="f-check_in_snooze_months"' in clients
    assert 'name="quote_term_days"' not in prices and 'name="sales_tax_pct"' not in prices
    money = main("/setup/money")
    for part in ('id="deposits"', 'id="ways-to-pay"', 'id="travel"', 'id="f-pay_details"', 'id="f-bank_details"'):
        assert part in money, part
    assert 'id="f-idle_signout_hours"' in main("/security") and 'id="notice"' not in main("/security")
    assert 'id="notice"' in main("/settings?tab=notifications")

def test_the_settings_list_is_the_registry_in_order(env):
    A, c, _ = env
    import settings_registry as R
    nav = _page(c, "/settings").split('<nav class="settabs', 1)[1].split("</nav>", 1)[0]
    titles = re.findall(r'<h2 class="ui-sec-title">([^<]+)</h2>', nav)
    assert titles == [g_["title"] for g_ in R.GROUPS]
    assert 'href="/settings/plugin/prints"' in nav
    assert "Print orders is off." in _page(c, "/settings/plugin/prints")
    assert 'href="/settings/type/family"' in nav and "sui-child" in nav
    assert "data-ui-find" not in nav

def test_old_addresses_land_on_the_new_pages(env):
    A, c, _ = env
    for old, new in (("/settings?tab=general", "/settings?tab=profile"), ("/settings?tab=gallery", "/settings?tab=branding"),
                     ("/settings?tab=fees", "/settings?tab=prices"), ("/settings?find=deposit", "/search?q=deposit"),
                     ("/setup/business", "/settings?tab=profile#location")):
        r = c.get(old, environ_base=E)
        assert r.status_code == 302 and r.headers["Location"].endswith(new), (old, r.headers.get("Location"))
    ui_js = open(os.path.join(ROOT, "static", "ui.js")).read()
    assert "data-settings-moved" in ui_js
    nav = _page(c, "/settings?tab=profile")
    assert "/settings?tab=clients#f-age_majority" in nav.replace("&amp;", "&").replace("\\u0026", "&")

def test_each_form_saves_only_its_own_settings(env):
    A, c, _ = env
    _set(A, "credit_off", "")
    c.post("/settings?tab=clients", data={"clients_posted": "1", "form_id": "clients-form", "check_in_snooze_months": "4",
                                          "age_majority": "19"}, environ_base=E)
    with A.app.test_request_context():
        assert A.setting("credit_off") == "" and A.check_in_snooze() == 4 and A.majority_age() == 19
    r = c.post("/settings?tab=branding", data={"credit_posted": "1", "form_id": "credit"}, environ_base=E)
    assert r.headers["Location"].endswith("saved=credit#save-credit")
    with A.app.test_request_context():
        assert A.setting("credit_off") == "1"
    r = c.post("/settings", data={"idle_posted": "1", "idle_signout_hours": "3", "back": "/security#idle"}, environ_base=E)
    assert r.headers["Location"].endswith("/security#idle")
    with A.app.test_request_context():
        assert A.idle_hours() == 3
    c.post("/settings?tab=profile", data={"form_id": "business", "business_website": "not a site"}, environ_base=E)
    page = _page(c, "/settings?tab=profile")
    assert 'class="sui-err"' in page and 'aria-invalid="true"' in page.split('id="f-business_website"', 1)[1][:200]

def test_labels_are_renamed_and_merged_on_every_client(env):
    A, c, _ = env
    for n in ("Lane", "Ortiz", "Kim", "Park"):
        _sql(A, "INSERT INTO households (name) VALUES (?)", (n,))
    ids = [r[0] for r in _sql(A, "SELECT id FROM households ORDER BY id")]
    for hid in ids[:3]:
        _sql(A, "INSERT INTO client_labels (household_id, label) VALUES (?, 'slow to pay')", (hid,))
    _sql(A, "INSERT INTO client_labels (household_id, label) VALUES (?, 'VIP')", (ids[0],))
    _sql(A, "INSERT INTO client_labels (household_id, label) VALUES (?, 'VIP')", (ids[3],))
    page = _page(c, "/settings?tab=clients")
    assert "slow to pay" in page and "3 clients" in page and "2 clients" in page
    c.post("/settings/labels", data={"label": "slow to pay", "new": "Slow to pay"}, environ_base=E)
    assert _sql(A, "SELECT COUNT(*) FROM client_labels WHERE label='Slow to pay'")[0][0] == 3
    c.post("/settings/labels", data={"label": "VIP", "act": "merge", "into": "Slow to pay"}, environ_base=E)
    got = _sql(A, "SELECT label, COUNT(*) FROM client_labels GROUP BY label")
    assert [tuple(r) for r in got] == [("Slow to pay", 4)]
    c.post("/settings/labels", data={"label": "Slow to pay", "new": "  "}, environ_base=E)
    assert _sql(A, "SELECT COUNT(*) FROM client_labels WHERE label='Slow to pay'")[0][0] == 4

def test_one_search_groups_answers_by_kind(env):
    A, c, _ = env
    page = _page(c, "/search?q=mileage")
    assert 'id="setting-hits"' in page and 'href="/setup/money#f-mileage_rate"' in page
    _sql(A, "INSERT INTO households (name) VALUES ('Harlow')")
    hid = _sql(A, "SELECT id FROM households WHERE name='Harlow'")[0][0]
    _sql(A, "INSERT INTO sessions (household_id, type, stage) VALUES (?, 'family', 'booked')", (hid,))
    sid = _sql(A, "SELECT MAX(id) FROM sessions")[0][0]
    _sql(A, "INSERT INTO sessions (household_id, type, stage) VALUES (?, 'family', 'inquiry')", (hid,))
    _sql(A, "INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (?, 'INV', 'INV-2026-0042', '2026-11-14')", (sid,))
    page = _page(c, "/search?q=harlow")
    assert page.index('id="hits"') < page.index('id="session-hits"')
    page = _page(c, "/search?q=INV-2026-0042", follow=False)
    assert "INV-2026-0042" in page or "/c/" in page or "/client" in page
    base = _page(c, "/")
    bar = base.split('<nav class="top', 1)[1].split("</nav>\n<main", 1)[0]
    assert 'class="ui-nav-search' in bar.split('class="ui-more ui-nav-more"', 1)[0]
    assert 'name="q"' not in bar.split('class="ui-more ui-nav-more"', 1)[1]
    clients = _page(c, "/clients")
    assert "Search everything" not in clients and 'placeholder="Filter by name, phone, email, or label"' in clients

def _two_destinations(A, c, tmp):
    for d in ("v1", "v2"):
        (tmp / d).mkdir()
    r = c.post("/setup/storage", data=dict(primary_type="local", primary_path=str(tmp / "v1"), primary_init="1",
                                           secondary_type="local", secondary_path=str(tmp / "v2"), secondary_init="1",
                                           one_disk_ok="1", same_disk_ok="1", backup_time="02:40"), environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)[-1500:]

def test_a_configured_destination_is_a_read_only_card_with_no_secret(env):
    A, c, tmp = env
    _two_destinations(A, c, tmp)
    for url in ("/setup/storage", "/vault"):
        page = _page(c, url)
        where = page.split('id="where"', 1)[1].split("</details>", 1)[0] if url == "/vault" else page
        assert 'class="sui-conn" id="f-primary_type"' in where and 'class="sui-conn" id="f-secondary_type"' in where
        assert str(tmp / "v1") in where and str(tmp / "v2") in where
        assert 'name="primary_path"' not in where and 'name="primary_type"' not in where and 'type="password"' not in where
        assert ">Test now<" in where and ">Replace<" in where and ">Remove<" in where
        assert 'name="backup_time"' in where

def test_replace_shows_the_form_blank_but_for_its_kind(env):
    A, c, tmp = env
    _two_destinations(A, c, tmp)
    page = _page(c, "/setup/storage?replace=primary")
    form = page.split('id="f"', 1)[1].split("</form>", 1)[0]
    assert 'name="only" value="primary"' in form and 'name="primary_type"' in form and 'name="secondary_type"' not in form
    assert 'value="local" selected' in form and str(tmp / "v1") not in form
    assert ">Cancel<" in page
    fold = _page(c, "/vault?replace=secondary").split('id="where"', 1)[1].split("</details>", 1)[0]
    assert 'action="/setup/storage?replace=secondary"' in fold and 'name="secondary_type"' in fold
    assert 'name="primary_type"' not in fold
    page = _page(c, "/setup/galleries")
    assert 'name="gallery_type"' in page

def test_a_failed_test_on_replace_leaves_the_old_destination_in_use(env):
    A, c, tmp = env
    from vault import web as VW
    _two_destinations(A, c, tmp)
    before = VW.config()
    r = c.post("/setup/storage?replace=primary", data=dict(only="primary", primary_type="local",
                                                           primary_path="/proc/no-such-place/vault", backup_time="02:40"),
               environ_base=E)
    assert r.status_code == 200 and "Primary location:" in r.get_data(as_text=True)
    assert VW.config()["primary"] == before["primary"] and VW.config()["secondary"] == before["secondary"]
    (tmp / "v3").mkdir()
    r = c.post("/setup/storage?replace=primary", data=dict(only="primary", primary_type="local", primary_path=str(tmp / "v3"),
                                                           primary_init="1", backup_time="02:40"), environ_base=E)
    assert r.status_code == 302
    assert VW.config()["primary"]["path"] == str(tmp / "v3") and VW.config()["secondary"] == before["secondary"]
    assert "it works" in _page(c, "/setup/storage")
    c.post("/setup/storage", data=dict(only="neither", remove="secondary", backup_time="02:40"), environ_base=E)
    assert "secondary" not in VW.config() and VW.config()["primary"]["path"] == str(tmp / "v3")

def test_test_now_records_the_result_on_the_card(env):
    A, c, tmp = env
    _two_destinations(A, c, tmp)
    r = c.post("/setup/connection/secondary/test", data={"back": "/vault#where"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/vault#where")
    import shutil
    shutil.rmtree(tmp / "v2")
    c.post("/setup/connection/secondary/test", environ_base=E)
    page = _page(c, "/setup/storage")
    card = page.split('id="f-secondary_type"', 1)[1].split('class="ui-btnrow"', 1)[0]
    assert "failed" in card

def test_the_web_host_and_ntfy_are_cards_once_set_up(env, monkeypatch):
    A, c, tmp = env
    import setup
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(tmp / "gal")}
    setup.write_storage(cfg)
    _set(A, "gallery_base_url", "https://example.com/g/")
    page = _page(c, "/setup/galleries")
    assert 'class="sui-conn" id="f-gallery_type"' in page and 'name="gallery_path"' not in page
    assert "https://example.com/g/" in page and 'name="gallery_days"' in page
    c.post("/setup/galleries", data=dict(keep_host="1", gallery_days="40", unopened_days="50"), environ_base=E)
    assert VW.config()["gallery_host"]["path"] == str(tmp / "gal")
    page = _page(c, "/setup/galleries?replace=gallery")
    assert 'name="gallery_type"' in page and "No online galleries for now" not in page
    import locknotice
    sent = []
    monkeypatch.setattr(locknotice, "_post", lambda url, body, headers: sent.append(url))
    c.post("/security/notice", data=dict(act="save", server="https://ntfy.sh", topic="studio_abc_123", token="tok-zzq9"),
           environ_base=E)
    page = _page(c, "/settings?tab=notifications")
    assert 'id="conn-ntfy"' in page and 'name="server"' not in page and 'type="password"' not in page and "tok-zzq9" not in page

    def refuse(*a):
        raise OSError("the server answered 403")
    monkeypatch.setattr(locknotice, "_post", refuse)
    c.post("/security/notice", data=dict(act="save", replace="1", server="https://ntfy.example.org", topic="other_topic_9"),
           environ_base=E)
    with A.app.test_request_context():
        assert A.setting("ntfy_server") == "https://ntfy.sh" and A.setting("ntfy_topic") == "studio_abc_123"
