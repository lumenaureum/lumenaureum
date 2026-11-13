"""Settings export and import: a round trip into a blank install carries business details, rules, prices and storage;
never secrets or client records; a bad file changes nothing; only known settings are taken."""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}


def install(tmp, name, monkeypatch, setup_done=True):
    d = tmp / name
    d.mkdir()
    monkeypatch.setenv("CRM_DB", str(d / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(d / "storage.json"))
    fresh_modules()
    import app as A
    return A, signed_in_client(A, setup_done=setup_done)


def upload(c, data, back="/settings"):
    body = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return c.post("/settings/import", data={"back": back, "file": (io.BytesIO(body), "s.json")},
                  content_type="multipart/form-data", environ_base=E)


def test_round_trip_into_a_blank_install(tmp_path, monkeypatch):
    A, c = install(tmp_path, "one", monkeypatch)
    with A.app.test_request_context():
        for k, v in (("business_name", "North Light"), ("photographer_name", "Sam Rivera"), ("timezone", "America/Chicago"),
                     ("gallery_base_url", "https://example.com/g/")):
            A.x("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
        A.x("INSERT OR REPLACE INTO settings (key, value) VALUES ('rules', ?)", (json.dumps({"DEPOSIT_AMOUNT": 175}),))
        A.x("INSERT INTO fee_schedules (created, items, note) VALUES ('now', ?, 'mine')",
            (json.dumps([{"key": "family", "label": "Family hour", "amount": 333, "unit": "flat"}]),))
    from vault import web as VW
    VW.write_config({"primary": {"type": "local", "path": "/vault"},
                     "gallery_host": {"type": "s3", "bucket": "b", "access_key": "AK", "secret_key": "TOPSECRET"}})
    A.apply_settings()
    r = c.get("/settings/export.json", environ_base=E)
    text = r.get_data(as_text=True)
    assert "TOPSECRET" not in text and "Harlow" not in text
    data = json.loads(text)
    assert data["settings"]["business_name"] == "North Light" and data["rules"]["DEPOSIT_AMOUNT"] == 175

    A2, c2 = install(tmp_path, "two", monkeypatch, setup_done=False)        # a blank install, mid-setup
    r = upload(c2, data, back="/setup/business")
    with c2.session_transaction() as ss:
        assert ss["import_result"]["ok"]
    import config as C2
    assert C2.DEPOSIT_AMOUNT == 175
    with A2.app.test_request_context():
        assert A2.setting("business_name") == "North Light" and A2.current_schedule()["items"][0]["amount"] == 333
    from vault import web as VW2
    assert "gallery_host" not in VW2.config()                          # galleries wait for the owner's OK
    assert b"This file sets your online galleries" in c2.get("/setup/business", environ_base=E).data
    c2.post("/settings/import/gallery", data={"use": "1", "back": "/setup/business"}, environ_base=E)
    cfg = VW2.config()
    assert cfg["gallery_host"]["bucket"] == "b" and "secret_key" not in cfg["gallery_host"]
    assert b"Sam Rivera" in c2.get("/setup/business", environ_base=E).data


def test_bad_files_change_nothing(tmp_path, monkeypatch):
    A, c = install(tmp_path, "one", monkeypatch)
    for bad in (b"not json", {"hello": 1}, {"lumen_aureum_settings": 1, "settings": {"gallery_base_url": "http://x/"}},
                {"lumen_aureum_settings": 1, "fees": [{"key": "a b", "amount": 1}]},
                {"lumen_aureum_settings": 1, "rules": {"DEPOSIT_AMOUNT": "lots"}}):
        r = upload(c, bad)
        with c.session_transaction() as ss:
            assert ss.pop("import_result")["error"]
    with A.app.test_request_context():
        assert A.setting("gallery_base_url") == ""
    r = upload(c, {"lumen_aureum_settings": 1, "settings": {"business_name": "X", "setup_complete": "0", "vault_master_pub": "evil"}})
    with A.app.test_request_context():
        assert A.setting("business_name") == "X" and A.setting("setup_complete") == "1"   # unknown keys ignored
        assert A.setting("vault_master_pub") != "evil"


def test_settings_files_never_load_plugins(tmp_path, monkeypatch):
    """A settings file that names plugin folders loads nothing, and an export never carries any."""
    A, c = install(tmp_path, "one", monkeypatch)
    mine = tmp_path / "my-plugins"; mine.mkdir()
    (mine / "hello.py").write_text("def register(ctx):\n    ctx.hooks.nav.append(('Hello', 'home'))\n")
    upload(c, {"lumen_aureum_settings": 1, "plugins": [str(mine)]})
    assert "plugins" not in json.loads(c.get("/settings/export.json", environ_base=E).data)
    assert b"Load them" not in c.get("/settings", environ_base=E).data
    fresh_modules()
    import app as A2                                                   # "after a restart"
    assert "hello" not in A2.LOADED

def test_a_broken_plugin_never_stops_the_app(tmp_path, monkeypatch):
    A, c = install(tmp_path, "one", monkeypatch)
    mine = tmp_path / "broken"; mine.mkdir()
    (mine / "oops.py").write_text("raise RuntimeError('nope')\n")
    monkeypatch.setenv("CRM_PLUGIN_DIRS", str(mine))
    fresh_modules()
    import app as A2                                                   # "after the restart"
    assert any("oops.py" in e for e in A2.PLUGIN_ERRORS) and "oops" not in A2.LOADED


def test_shoot_step_keeps_imported_types_as_they_are(tmp_path, monkeypatch):
    A, c = install(tmp_path, "one", monkeypatch, setup_done=False)
    import config as C
    t = dict(C.PRESETS["families"]["types"][0], label="Families (mine)", steps={"booked": [["do", "Charge batteries", -1]]})
    upload(c, {"lumen_aureum_settings": 1, "rules": {"TYPES": [t, dict(C.PERSONAL_TYPE, label="Portfolio (mine)")]}}, back="/setup/shoot")
    c.post("/setup/shoot", data={"preset_families": "1"}, environ_base=E)
    import config as C2
    fam = next(x for x in C2.TYPES if x["key"] == "family")
    assert fam["label"] == "Families (mine)" and fam["steps"]["booked"] == [["do", "Charge batteries", -1]]
    assert C2.SESSION_TYPES["portfolio"] == "Portfolio (mine)"
