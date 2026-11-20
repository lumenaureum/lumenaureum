import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import E, _app

def _ids(C):
    import walkthrough as W
    t = next(t_ for t_ in C.TYPES if t_["key"] == "wedding")
    return [s["id"] for s in W.steps_for(dict(t))]

def test_the_preview_follows_unsaved_values_and_saves_nothing(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    import config as C
    n = _ids(C).index("portfolio")
    before = dict(C.PORTFOLIO_DISCOUNT)
    r = c.post(f"/settings/type/wedding/walk/{n}/preview", data={"portfolio_value": "7", "portfolio_mode": "pct"},
               environ_base=E).get_json()
    assert "takes 7% off the price, before tax." in r["preview"], r
    assert dict(C.PORTFOLIO_DISCOUNT) == before

def test_three_ways_on_and_accept_all_defaults_grays_out_after_a_change(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    import config as C
    ids = _ids(C)
    page = c.get("/settings/type/wedding/walk/1", environ_base=E).get_data(as_text=True)
    assert ">Next<" in page and "Accept all defaults" in page and "Keep as it is" not in page and "Skip for now" not in page
    assert 'id="walk-defaults" disabled' not in page
    n = ids.index("delivery_days")
    c.post(f"/settings/type/wedding/walk/{n}", data={"act": "save", "delivery_days": str(C.delivery_days("wedding") + 3)},
           environ_base=E)
    later = c.get(f"/settings/type/wedding/walk/{n + 1}", environ_base=E).get_data(as_text=True)
    assert 'id="walk-defaults" disabled' in later
    c.get(f"/settings/type/wedding/walk/{len(ids)}", environ_base=E)
    again = c.get("/settings/type/wedding/walk/1", environ_base=E).get_data(as_text=True)
    assert 'id="walk-defaults" disabled' not in again

def test_accept_all_defaults_saves_nothing(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    import config as C
    n = _ids(C).index("delivery_days")
    was = C.delivery_days("wedding")
    c.post(f"/settings/type/wedding/walk/{n}", data={"act": "finish", "delivery_days": str(was + 9)}, environ_base=E)
    assert C.delivery_days("wedding") == was

def test_the_walk_through_asks_about_every_setting_on_the_type_page(tmp_path, monkeypatch):

    import re
    A, c = _app(tmp_path, monkeypatch, presets=("portraits",))
    import config as C
    import walkthrough as W
    key = next(t_["key"] for t_ in C.TYPES)
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    form = page.split('method="post"', 1)[1]
    names = set(re.findall(r'name="([a-z_]+)"', form))
    names -= {"back", "type", "revert", "once"}
    names = {n for n in names if not re.match(r"(booked|inquiry|quoted|shot|delivered|closed)_", n)}
    t = next(t_ for t_ in C.TYPES if t_["key"] == key)
    asked = set()
    for st in W.steps_for(dict(t)):
        asked.add(st["field"][1])
        asked |= {"outfits", "wear"} if st["id"] == "wear" else set()
        asked |= {"free_reschedule", "notice_hours", "late_minutes"} if st["id"] == "reschedule" else set()
        asked |= {"deposit_value", "deposit_mode"} if st["id"] == "deposit" else set()
        asked |= {"portfolio_value", "portfolio_mode"} if st["id"] == "portfolio" else set()
        asked |= {"delivery_method"} if st["id"] == "method" else set()
    missing = sorted(n for n in names if n not in asked and not n.startswith("step"))
    assert not missing, f"type page settings the walk-through never asks about: {missing}"

def test_the_type_page_opens_after_the_walk_through_or_accepting_its_defaults(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch, presets=("weddings",))
    con = A.connect()
    con.execute("DELETE FROM settings WHERE key='walk'")
    con.commit()
    con.close()
    r = c.get("/settings/type/wedding", environ_base=E)
    assert r.status_code == 302 and "/settings/type/wedding/walk/0" in r.location
    c.post("/settings/type/wedding/walk/0", data={"act": "finish"}, environ_base=E)
    c.get(c.post("/settings/type/wedding/walk/0", data={"act": "finish"}, environ_base=E).location, environ_base=E)
    assert c.get("/settings/type/wedding", environ_base=E).status_code == 200
