import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
EXAMPLE = os.path.join(ROOT, "plugins", "examples", "city_tax.py")

def _app(tmp_path, monkeypatch, mounted):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    if mounted:
        mine = tmp_path / "my-plugins"
        mine.mkdir()
        shutil.copy(EXAMPLE, mine / "city_tax.py")
        monkeypatch.setenv("CRM_PLUGIN_DIRS", str(mine))
    else:
        monkeypatch.delenv("CRM_PLUGIN_DIRS", raising=False)
    fresh_modules()
    import app as A
    return A, signed_in_client(A, setup_done=False)

def test_not_loaded_where_it_ships(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, mounted=False)
    assert not A.hooks.tax_rules

def test_city_clients_pay_the_city_tax_too(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, mounted=True)
    assert len(A.hooks.tax_rules) == 1
    c.post("/setup/tax", data=dict(taxes_posted="1", new_tax_name="State tax", new_tax_rate="6", new_tax_default="1"),
           environ_base=E)
    c.post("/setup/tax", data=dict(taxes_posted="1", tax_key_0="t1", tax_name_0="State tax", tax_rate_0="6",
                                   tax_default_0="1", new_tax_name="Riverton tax", new_tax_rate="1.5"), environ_base=E)
    rule = A.hooks.tax_rules[0]
    with A.app.test_request_context():
        A.db()
        taxes = A.taxes_list()
        city = next(t for t in taxes if t["name"] == "Riverton tax")
        assert rule(dict(address="12 Main St, Riverton"), taxes) == {city["key"]: dict(on=True, note="")}
        assert rule(dict(address="4 Oak Ln, Lakeside 00501"), taxes)[city["key"]]["on"]
        assert not rule(dict(address="4 Oak Ln, Lakeside 99999"), taxes)[city["key"]]["on"]
        empty = rule(dict(address=""), taxes)[city["key"]]
        assert not empty["on"] and empty["note"]
        assert A.tax_pct_at("12 Main St, Riverton") == pytest.approx(7.5) and A.tax_pct_at("4 Oak Ln, Lakeside") == 6
