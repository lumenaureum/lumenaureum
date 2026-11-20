import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

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

def test_signed_in_pages_go_to_setup_until_finished(env):
    A, c, _ = env
    r = c.get("/", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup/business")
    assert c.get("/clients", environ_base=E).headers["Location"].endswith("/setup/business")
    assert c.get("/setup/tax", environ_base=E).status_code == 200
    assert c.get("/healthz", environ_base=E).status_code == 200

def test_business_step_sets_time_zone_and_sun(env):
    A, c, _ = env
    r = post(c, "/setup/business", business_name="North Light Photo", photographer_name="Sam Rivera",
             timezone="America/Chicago", home_zip="66952")
    assert r.headers["Location"].endswith("/setup/storage")
    import sun
    assert A.TZ.key == "America/Chicago" and sun.TZ.key == "America/Chicago"
    assert sun.HOME and abs(sun.HOME[0] - 39.8) < 0.3
    r = post(c, "/setup/business", business_name="x", photographer_name="y", timezone="Not/AZone")
    assert b"time zone" in r.data
    r = post(c, "/setup/business", business_name="x", photographer_name="y", timezone="UTC", home_zip="00000")
    assert b"isn&#39;t in the US ZIP table" in r.data or b"isn't in the US ZIP table" in r.data
    post(c, "/setup/business", business_name="x", photographer_name="y", timezone="Europe/Dublin", home_lat="53.35", home_lon="-6.26")
    assert sun.HOME == (53.35, -6.26)

def test_storage_refuses_a_nonempty_unmarked_folder_and_marks_an_empty_one(env):
    A, c, tmp = env
    busy = tmp / "busy"; busy.mkdir(); (busy / "x").write_text("x")
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(busy), primary_init="1", backup_time="03:15")
    assert b"already has files" in r.data
    vault = tmp / "vault"; vault.mkdir()
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="03:15")
    assert r.status_code == 302, r.data
    assert (vault / ".vault-root").exists()
    from vault import web as VW
    assert VW.config()["primary"] == {"type": "local", "path": str(vault)}
    assert oct((tmp / "storage.json").stat().st_mode)[-3:] == "600"
    t = post(c, "/setup/test/primary", primary_type="local", primary_path=str(tmp / "nope")).get_json()
    assert not t["ok"] and "doesn't exist" in t["message"]

def test_money_types_documents_tax_feed_the_app(env):
    A, c, _ = env
    import config as C
    post(c, "/setup/money", deposit="150", event_pct="40", reschedule_fee="25", delivery_days="10", event_delivery_days="20",
         quote_term_days="21", mileage_rate="0.7", net_days="15", net_family="1", pay_cash="1", pay_other="Venmo")
    assert C.DEPOSIT_AMOUNT == 150 and "$150.00 deposit" in C.TERMS[0][1]
    assert C.NET_TERMS_TYPES == ("family",) and C.PAYMENT_METHODS == ["cash", "Venmo"]
    r = post(c, "/setup/money", deposit="150", event_pct="40", reschedule_fee="25", delivery_days="10",
             event_delivery_days="20", net_days="15")
    assert b"at least one way" in r.data
    post(c, "/setup/shoot", preset_families="1", preset_realestate="1")
    assert set(C.SESSION_TYPES) == {"family", "realestate"}
    assert C.TYPE_KIND["realestate"] == "property" and C.EVENT_TYPE == "event"
    with A.app.test_request_context():
        keys = [it["key"] for it in A.current_schedule()["items"]]
    assert "family" in keys and "re_small" in keys and "wedding" not in keys
    post(c, "/setup/documents", rights_family="transfer", rights_realestate="license")
    assert C.LICENSE_TYPES == ("realestate",)
    with A.app.test_request_context():
        assert A.sales_tax_pct() == 0.0
    post(c, "/setup/tax", tax_mode="flat", sales_tax_pct="7.25")
    with A.app.test_request_context():
        assert A.sales_tax_pct("any") == 7.25
    r = post(c, "/setup/tax", tax_mode="flat", sales_tax_pct="")
    assert b"Enter your rate" in r.data

def test_rules_survive_a_restart(env, monkeypatch):
    A, c, _ = env
    post(c, "/setup/money", deposit="175", event_pct="50", reschedule_fee="0", delivery_days="14", event_delivery_days="21",
         net_days="30", pay_check="1")
    fresh_modules()
    import app as A2
    import config as C2
    import datakey
    assert not datakey.is_unlocked() and C2.DEPOSIT_AMOUNT != 175
    datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD))
    A2.on_unlock()
    assert C2.DEPOSIT_AMOUNT == 175 and C2.PAYMENT_METHODS == ["check"]

def test_cannot_finish_without_required_steps_then_finishes(env):
    A, c, tmp = env
    r = post(c, "/setup/network")
    assert b"Still needed" in r.data and b"Your business" in r.data
    post(c, "/setup/business", business_name="B", photographer_name="P", timezone="UTC")
    vault = tmp / "vault"; vault.mkdir()
    post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40")
    r = post(c, "/setup/network")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert c.get("/", environ_base=E).status_code == 200
    assert b'aria-label="Settings"' in c.get("/setup/money", environ_base=E).data

def test_storage_settings_are_encrypted_on_disk(env):
    A, c, tmp = env
    vault = tmp / "vault"; vault.mkdir()
    post(c, "/setup/storage", primary_type="s3", primary_bucket="b", primary_access_key="AKIAEXAMPLE",
         primary_secret_key="very-secret-value", backup_time="02:40")
    post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40")
    raw = (tmp / "storage.json").read_bytes()
    assert raw.startswith(b"LAE1") and str(vault).encode() not in raw

def test_optional_step_can_use_defaults(env):
    A, c, _ = env
    r = post(c, "/setup/money", skip="1")
    assert r.headers["Location"].endswith("/setup/tax")
    r = post(c, "/setup/business", skip="1")
    assert r.status_code == 200 and b"business name" in r.data

def test_finish_setting_up_list_is_in_the_alert_bell(env):

    A, c, tmp = env
    post(c, "/setup/business", business_name="B", photographer_name="P", timezone="UTC")
    vault = tmp / "vault"; vault.mkdir()
    post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40")
    post(c, "/setup/network")
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Setting up" not in today and "starter prices" not in today and "business email" not in today
    page = c.get("/alerts", environ_base=E).get_data(as_text=True)
    for line in ("approve each where you use it", "starter prices", "business email", "second backup location",
                 "online galleries"):
        assert line in page, line
    assert "first client" not in page
    post(c, "/settings/review/agreement")
    page = c.get("/alerts", environ_base=E).get_data(as_text=True)
    assert "approve each where you use it" not in page

def test_backups_on_the_database_disk_are_refused_without_a_second_location(env, monkeypatch):
    A, c, tmp = env
    import setup
    vault = tmp / "vault"; vault.mkdir()
    second = tmp / "second"
    monkeypatch.setattr(setup, "same_disk", lambda a, b: str(second) not in (str(a), str(b)))
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40")
    assert b"same disk as your data" in r.data and b"Continue anyway" in r.data
    page = r.data.decode()
    assert '<button name="one_disk_ok" value="1">' in page
    assert page.index("data-enter-default") < page.index('name="one_disk_ok"')
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40",
             one_disk_ok="1")
    assert r.status_code == 302
    second.mkdir()
    r = post(c, "/setup/storage", primary_type="local", primary_path=str(vault), primary_init="1", backup_time="02:40",
             secondary_type="local", secondary_path=str(second), secondary_init="1")
    assert r.status_code == 302

def test_custom_types_regional_tax_and_demo(env, tmp_path, monkeypatch):
    A, c, tmp = env
    import config as C
    custom = [dict(key="boats", label="Boats", kind="portrait", minutes=60, intake=[["vessel", "Vessel"]]),
              dict(key="gala", label="Galas", kind="event", minutes=180)]
    from settings_io import parse
    _s, rules, _f, _st = parse(json.dumps({"lumen_aureum_settings": 1, "rules": {"TYPES": custom}}), {})
    with A.app.test_request_context():
        A.x("INSERT OR REPLACE INTO settings (key, value) VALUES ('rules', ?)", (json.dumps(rules),))
    A.apply_settings()
    assert C.SESSION_TYPES["boats"] == "Boats" and C.EVENT_TYPE == "gala" and ("vessel", "Vessel") in C.INTAKE["boats"]
    page = c.get("/setup/shoot", environ_base=E).get_data(as_text=True)
    assert "Boats" in page and "Galas" in page
    post(c, "/setup/shoot", preset_seniors="1", keep_boats="1", keep_gala="1")
    assert {"senior", "boats", "gala"} == set(C.SESSION_TYPES)
    post(c, "/setup/shoot", preset_seniors="1", preset_personal="1", keep_boats="1", keep_gala="1")
    assert {"senior", "boats", "gala", "portfolio"} == set(C.SESSION_TYPES)
    assert C.WORKFLOW["no-such-type"] is C.PORTRAIT_FLOW

    A.hooks.tax_region = dict(name="Testland", areas=[("in", "Inside"), ("out", "Outside")],
                              area_of=lambda addr: ("in", "") if "Inside" in addr else ("out", ""),
                              extra_pct=lambda area, local: local if area == "in" else 0)
    assert b"Testland" in c.get("/setup/tax", environ_base=E).data
    post(c, "/setup/tax", tax_mode="regional", sales_tax_pct="5", local_tax_pct="2")
    with A.app.test_request_context():
        assert A.sales_tax_pct("in") == 7 and A.sales_tax_pct("out") == 5
    A.hooks.tax_region = None
    with A.app.test_request_context():
        import seed_demo
        try:
            seed_demo.main()
        except AttributeError:
            pass
        assert A.q("SELECT COUNT(*) n FROM sessions WHERE type='gala'", one=True)["n"] == 4

def test_storage_errors_in_plain_words():
    import setup
    cfg = dict(type="sftp", host="files.example.com", key_file="/keys/id")
    msg = setup.plain_error(FileNotFoundError(2, "No such file", "/data/.ssh/known_hosts"), cfg)
    assert "hasn't connected to files.example.com" in msg and "known_hosts" not in msg
    assert setup.plain_error(FileNotFoundError(2, "No such file", "/keys/id"), cfg) == "The private key file isn't there."
    assert setup.plain_error(ValueError("odd"), cfg) == "ValueError: odd"

def test_balance_due_before_the_session():
    import config as C
    C.apply({"BEFORE_TYPES": ["portrait"], "BALANCE_BEFORE_DAYS": 10})
    try:
        assert C.balance_text("portrait") == "due 10 days before the session"
        assert "due 10 days before the session" in dict(C.build_terms("portrait"))["Booking"]
    finally:
        C.apply({})

def test_type_steps_and_terms_are_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    import config as C
    key = next(iter(C.SESSION_TYPES))
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    assert "Steps" in page and "Add an automatic step" in page
    form = {}
    for stage in C.STEP_STAGES:
        for i, st in enumerate(C.WORKFLOW[key].get(stage, [])):
            form[f"{stage}_{i}_kind"] = st[0]
            if st[0] == "auto":
                form[f"{stage}_{i}_key"] = st[1]
            else:
                form[f"{stage}_{i}_label"] = st[1]
                form[f"{stage}_{i}_days"] = "" if st[2] is None else str(st[2])
    form.update({"booked_new": "Charge batteries", "booked_new_days": "-1", "delivered_auto": "sd_shipped",
                 "free_reschedule": "1", "notice_hours": "72", "late_minutes": "0"})
    c.post(f"/settings/type/{key}", data=form, environ_base=E)
    assert ("do", "Charge batteries", -1) in C.WORKFLOW[key]["booked"]
    assert ("auto", "sd_shipped") in C.WORKFLOW[key]["delivered"]
    assert "3 days' notice" in C.cancel_text(key) and not C.late_text(key) and not C.POLICY[key]["outfits"]
    data = c.get("/settings/export.json", environ_base=E).get_json()
    t = next(t_ for t_ in data["rules"]["TYPES"] if t_["key"] == key)
    assert ["do", "Charge batteries", -1] in t["steps"]["booked"] and "inquiry" not in t["steps"]

def test_delivery_fill_in_reaches_the_documents_and_a_blank_shows():
    import config as C
    C.apply({"TYPES": [dict(C.PRESETS["portraits"]["types"][0], policy={"delivery": "on a USB drive, with an online gallery"})]})
    try:
        assert "on a USB drive" in dict(C.build_terms("portrait"))["Delivery"]
        C.apply({"TYPES": [dict(C.PRESETS["portraits"]["types"][0], policy={"delivery": ""})]})
        assert C.delivery_text("portrait") == "[HOW YOU DELIVER]"
    finally:
        C.apply({})

def test_every_preset_loads_with_its_own_questions_and_prices():

    import config as C
    fees = [f["key"] for pr in C.PRESETS.values() for f in pr["fees"]] + [f["key"] for f in C._ADDONS]
    assert len(fees) == len(set(fees))
    types = [t["key"] for pr in C.PRESETS.values() for t in pr["types"]]
    assert len(types) == len(set(types))
    C.apply({"TYPES": C.preset_types(list(C.PRESETS))})
    try:
        for pr in C.PRESETS.values():
            for t in pr["types"]:
                qs = [k for k, _ in C.INTAKE[t["key"]]]
                assert qs and len(qs) == len(set(qs)), t["key"]
                assert any(f.get("type") == t["key"] for f in pr["fees"]) or t is C.PERSONAL_TYPE, t["key"]
        assert "outfits" not in dict(C.INTAKE["newborn"]) and "address" not in dict(C.INTAKE["product"])
        assert "address" in dict(C.INTAKE["food"]) and "food" in C.LICENSE_TYPES
        assert "promoting this business" in C.fill("food", "license_use")
    finally:
        C.apply({})

def test_intake_questions_are_editable_per_type(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    import config as C
    key = next(iter(C.SESSION_TYPES))
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    assert "Intake questions" in page and "Who is coming" in page
    before = dict((v, k) for k, v in C.INTAKE[key])
    form = {"questions": "Who is coming\nVenue for the shoot\n  Favorite <b>color</b>  \n\nFavorite <b>color</b>",
            "notice_hours": "48", "late_minutes": "30", "free_reschedule": "1", "outfits": "1"}
    form.update({f"{st}_{i}_kind": s_[0] for st in C.STEP_STAGES for i, s_ in enumerate(C.WORKFLOW[key].get(st, []))})
    form.update({f"{st}_{i}_key": s_[1] for st in C.STEP_STAGES for i, s_ in enumerate(C.WORKFLOW[key].get(st, []))
                 if s_[0] == "auto"})
    form.update({f"{st}_{i}_label": s_[1] for st in C.STEP_STAGES for i, s_ in enumerate(C.WORKFLOW[key].get(st, []))
                 if s_[0] != "auto"})
    c.post(f"/settings/type/{key}", data=form, environ_base=E)
    qs = C.INTAKE[key]
    assert qs[0] == (before["Who is coming"], "Who is coming")
    assert qs[1][0] == "venue"
    assert len(qs) == 4 and qs[2][0] != qs[3][0] and qs[2][1] == "Favorite <b>color</b>"
    assert "Favorite &lt;b&gt;color" in c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    data = c.get("/settings/export.json", environ_base=E).get_json()
    t = next(t_ for t_ in data["rules"]["TYPES"] if t_["key"] == key)
    assert [q[1] for q in t["questions"]][:2] == ["Who is coming", "Venue for the shoot"]
    import settings_io
    with pytest.raises(settings_io.ImportProblem):
        settings_io._questions([["a", "x"], ["a", "y"]], "q")
    with pytest.raises(settings_io.ImportProblem):
        settings_io._questions([["Bad Key", "x"]], "q")

def test_container_time_zone_is_the_starting_choice(tmp_path, monkeypatch):

    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("TZ", "America/Chicago")
    fresh_modules()
    import app as A
    from helpers import unlock_new_install
    unlock_new_install(A)
    A.apply_settings()
    assert A.TZ.key == "America/Chicago"

def test_second_copy_on_the_same_disk_is_a_warning_you_can_accept(env, monkeypatch):
    A, c, tmp = env
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda x_, y_: "va" in str(x_) + str(y_) and "vb" in str(x_) + str(y_))
    post(c, "/setup/business", business_name="B", photographer_name="P", timezone="UTC")
    a, b = tmp / "va", tmp / "vb"
    a.mkdir(); b.mkdir()
    form = dict(primary_type="local", primary_path=str(a), primary_init="1", secondary_type="local",
                secondary_path=str(b), secondary_init="1", backup_time="02:40")
    r = post(c, "/setup/storage", **form)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "same disk as the first" in page and "Keep it anyway" in page and str(b) in page
    r = post(c, "/setup/storage", same_disk_ok="1", **form)
    assert r.status_code == 302
    r = post(c, "/setup/storage", **form)
    assert r.status_code == 302

def test_fresh_install_price_list_starts_at_one(env):

    A, c, tmp = env
    post(c, "/setup/business", business_name="B", photographer_name="P", timezone="UTC")
    post(c, "/setup/shoot", preset_portraits="1")
    post(c, "/setup/shoot", preset_weddings="1")
    con = A.connect()
    assert tuple(con.execute("SELECT COUNT(*), MAX(id) FROM fee_schedules").fetchone()) == (1, 1)
    con.close()
