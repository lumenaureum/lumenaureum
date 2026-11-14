import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

@pytest.fixture
def S(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import settings_io
    return settings_io

def good_type(key="family"):
    return {"key": key, "label": "Family", "kind": "portrait", "minutes": 60,
            "steps": {"booked": [["do", "Plan", -3], ["auto", "deposit_paid"]]},
            "deposit_pct": 25}

def good_file():
    return {"lumen_aureum_settings": 1,
            "settings": {"business_name": "Studio"},
            "rules": {"TYPES": [good_type()]},
            "fees": [{"key": "session", "label": "Session", "amount": 200, "unit": "flat"}]}

def _with_type(**changes):
    def make(d):
        d["rules"]["TYPES"][0].update(changes)
    return make

def _set(path, value):
    def make(d):
        cur = d
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value
    return make

CASES = {
    "types not a list": _set(["rules", "TYPES"], {"key": "family"}),
    "types empty": _set(["rules", "TYPES"], []),
    "types too long": _set(["rules", "TYPES"], [good_type(f"t{i}") for i in range(41)]),
    "type not an object": _set(["rules", "TYPES"], ["family"]),
    "key with capitals": _with_type(key="Family"),
    "key with spaces": _with_type(key="my type"),
    "key empty": _with_type(key=""),
    "key too long": _with_type(key="a" * 31),
    "key not plain": _with_type(key=["family"]),
    "duplicate keys": _set(["rules", "TYPES"], [good_type("family"), good_type("family")]),
    "unknown kind": _with_type(kind="wedding_party"),
    "missing kind": _with_type(kind=None),
    "minutes too small": _with_type(minutes=4),
    "minutes too large": _with_type(minutes=1441),
    "minutes a bool": _with_type(minutes=True),
    "minutes a float": _with_type(minutes=60.5),
    "minutes a string": _with_type(minutes="60"),
    "deposit percent over 100": _with_type(deposit_pct=101),
    "deposit percent negative": _with_type(deposit_pct=-1),
    "deposit percent a bool": _with_type(deposit_pct=True),
    "deposit amount too large": _with_type(deposit_amount=100001),
    "unknown stage": _with_type(steps={"archived": [["do", "x", 0]]}),
    "steps not an object": _with_type(steps=[["do", "x", 0]]),
    "too many steps": _with_type(steps={"booked": [["do", f"s{i}", 0] for i in range(31)]}),
    "step of unknown sort": _with_type(steps={"booked": [["remind", "x", 0]]}),
    "unknown auto key": _with_type(steps={"booked": [["auto", "coffee_made"]]}),
    "auto with extra fields": _with_type(steps={"booked": [["auto", "deposit_paid", 3]]}),
    "days too early": _with_type(steps={"booked": [["do", "x", -366]]}),
    "days too late": _with_type(steps={"booked": [["do", "x", 366]]}),
    "days a bool": _with_type(steps={"booked": [["do", "x", True]]}),
    "days a string": _with_type(steps={"booked": [["do", "x", "3"]]}),
    "fees not a list": _set(["fees"], {"key": "session"}),
    "too many fees": _set(["fees"], [{"key": f"f{i}", "amount": 1} for i in range(201)]),
    "fee not an object": _set(["fees"], ["session"]),
    "duplicate fee keys": _set(["fees"], [{"key": "a", "amount": 1}, {"key": "a", "amount": 2}]),
    "bad fee key": _set(["fees"], [{"key": "Session Fee", "amount": 1}]),
    "fee amount negative": _set(["fees"], [{"key": "a", "amount": -1}]),
    "fee amount a bool": _set(["fees"], [{"key": "a", "amount": True}]),
    "bad unit": _set(["fees"], [{"key": "a", "amount": 1, "unit": "day"}]),
    "rules not an object": _set(["rules"], ["TYPES"]),
    "rule number a bool": _set(["rules", "DEPOSIT_AMOUNT"], True),
    "rule number out of range": _set(["rules", "DEPOSIT_AMOUNT"], 100001),
    "setting not plain": _set(["settings", "business_name"], {"x": 1}),
    "setting too long": _set(["settings", "business_name"], "x" * 2001),
    "gallery url over http": _set(["settings", "gallery_base_url"], "http://gallery.example.com"),
    "storage of unknown kind": _set(["storage"], {"primary": {"type": "ftp", "host": "x"}}),
    "s3 endpoint over http": _set(["storage"], {"primary": {"type": "s3", "bucket": "b",
                                                            "endpoint": "http://s3.example.com"}}),
}

def test_a_well_formed_file_parses(S):
    settings, rules, fees, storage = S.parse(json.dumps(good_file()), {})
    assert settings == {"business_name": "Studio"}
    t = rules["TYPES"][0]
    assert t["key"] == "family" and t["minutes"] == 60 and t["deposit_pct"] == 25
    assert t["steps"]["booked"] == [["do", "Plan", -3], ["auto", "deposit_paid"]]
    assert fees[0]["unit"] == "flat"

@pytest.mark.parametrize("name", list(CASES))
def test_malformed_field_is_refused(S, name):
    d = copy.deepcopy(good_file())
    CASES[name](d)
    with pytest.raises(S.ImportProblem):
        S.parse(json.dumps(d), {})

@pytest.mark.parametrize("text", ["not json", "[]", json.dumps({"lumen_aureum_settings": 2}),
                                  json.dumps({"settings": {}})])
def test_not_a_settings_file_is_refused(S, text):
    with pytest.raises(S.ImportProblem):
        S.parse(text, {})

def test_boundary_values_are_accepted(S):
    d = good_file()
    d["rules"]["TYPES"] = [dict(good_type(f"t{i}"), minutes=5 if i % 2 else 1440) for i in range(40)]
    d["rules"]["TYPES"][0]["steps"] = {"booked": [["do", f"s{i}", 365 if i % 2 else -365] for i in range(30)]}
    d["rules"]["TYPES"][0]["deposit_pct"] = 100
    d["fees"] = [{"key": f"f{i}", "amount": 1, "unit": u} for i, u in
                 enumerate(["flat", "hour", "each", "mile", "person"] * 40)]
    _, rules, fees, _ = S.parse(json.dumps(d), {})
    assert len(rules["TYPES"]) == 40 and len(fees) == 200
