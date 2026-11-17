import io
import json
import zipfile
from datetime import date

import pytest

import fake_lab
import prints_env as PE
from prints_env import E

@pytest.fixture
def e(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch)
    lab, fake = fake_lab.make(e.A.app.config["prints_api"])
    e.A.hooks.labs.append(lab)
    e.lab, e.fake = lab, fake
    yield e
    e.A.hooks.labs[:] = [l_ for l_ in e.A.hooks.labs if l_.get("id") != "testlab"]

def api(e):
    return e.A.app.config["prints_api"]

def orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def paid_order(e, confirm=True, pay_it=True):
    PE.send(e, PE.payload(e, "a" * 32))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    if confirm:
        con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    con = e.A.connect()
    con.execute("UPDATE print_orders SET flags=? WHERE id=?", (json.dumps({"flags_ok": True}), oid))
    con.commit(); con.close()
    if pay_it:
        total = json.loads(orders(e)[0]["invoice_data"])["total"]
        e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": f"{total / 100:.2f}"}, environ_base=E)
    return oid

def in_req(e, fn, *a, **kw):
    with e.A.app.test_request_context():
        try:
            out = fn(*a, **kw)
            e.A.db().commit()
            return out
        except Exception:
            e.A.db().rollback()
            raise

def test_the_lab_is_offered_and_its_functions_used(e):
    page = e.c.get("/settings/prints", environ_base=E).data.decode()
    assert "Test Lab" in page and "Test lab: nothing is printed" in page and "Fake product" in page
    e.c.post("/settings/prints/lab", data={"lab": "testlab"}, environ_base=E)
    assert PE.q(e, "SELECT value FROM settings WHERE key='prints_lab'")[0]["value"] == "testlab"
    PE.save_prices(e, **{"fake_pl-3": "P810"})
    assert json.loads(PE.q(e, "SELECT lab FROM price_list_items WHERE id='pl-3'")[0]["lab"]) == {"testlab": {"product": "P810"}}
    e.c.post("/settings/prints/lab/testlab/costs", environ_base=E)
    row = PE.q(e, "SELECT cost_cents, cost_from, cost_date, each_cents FROM price_list_items WHERE id='pl-1'")[0]
    assert row["cost_cents"] == 111 and row["cost_from"] == "Test Lab" and row["each_cents"] == 1500
    oid = paid_order(e, pay_it=False)
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    e.c.post(f"/prints/order/{oid}/quote", environ_base=E)
    assert orders(e)[0]["ship_lab_cents"] == 912

def test_the_starter_list_from_a_lab_replaces_only_the_sample(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, saved=False, on=False)
    lab, fake = fake_lab.make(None)
    e.A.hooks.labs.append(lab)
    try:
        e.c.get("/settings/prints", environ_base=E)
        e.c.post("/settings/prints/lab/testlab/starter", environ_base=E)
        rows = PE.q(e, "SELECT label, cost_cents FROM price_list_items ORDER BY sort")
        assert [r["label"] for r in rows] == ["5x7 print, Matte", "8x10 print, Matte"] and rows[0]["cost_cents"] == 300
    finally:
        e.A.hooks.labs.remove(lab)

def test_hand_off_is_refused_unless_paid_checked_and_unflagged(e):
    oid = paid_order(e, confirm=False, pay_it=False)
    ok, words = in_req(e, api(e).hand_off, oid, "testlab")
    assert not ok and "paid" in words
    con = e.A.connect(); con.execute("UPDATE print_orders SET before_payment=1"); con.commit(); con.close()
    ok, _w = in_req(e, api(e).hand_off, oid, "testlab", automatic=True)
    assert not ok
    total = json.loads(orders(e)[0]["invoice_data"])["total"]
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": f"{total / 100:.2f}"}, environ_base=E)
    ok, words = in_req(e, api(e).hand_off, oid, "testlab")
    assert not ok and "Looks right" in words
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1")
    con.execute("UPDATE print_orders SET flags='{}'"); con.commit(); con.close()
    ok, words = in_req(e, api(e).hand_off, oid, "testlab")
    assert not ok and "flags" in words
    assert "send" not in e.fake["calls"]
    con = e.A.connect(); con.execute("UPDATE print_orders SET flags=?", (json.dumps({"flags_ok": True}),)); con.commit(); con.close()
    ok, words = in_req(e, api(e).hand_off, oid, "testlab")
    assert ok and orders(e)[0]["state"] == "with_lab" and "3:00 pm" in orders(e)[0]["lab_words"]

def test_report_moves_only_by_the_table(e):
    oid = paid_order(e)
    in_req(e, api(e).hand_off, oid, "testlab")
    for bad in ("shipped", "delivered"):
        with pytest.raises(Exception):
            in_req(e, api(e).report, oid, "testlab", bad)
    assert orders(e)[0]["state"] == "with_lab"
    with pytest.raises(Exception):
        in_req(e, api(e).report, oid, "otherlab", "placed", numbers=["1"])
    in_req(e, api(e).report, oid, "testlab", "placed", numbers=["W-1"], cost=dict(products=1200, shipping=900, lab_tax=80))
    o = orders(e)[0]
    assert o["state"] == "ordered" and json.loads(o["lab_orders"])[0]["number"] == "W-1" and o["cost_tax"] == 80
    with pytest.raises(Exception):
        in_req(e, api(e).report, oid, "testlab", "given_back")
    with pytest.raises(Exception):
        in_req(e, api(e).report, oid, "testlab", "closed")
    in_req(e, api(e).report, oid, "testlab", "shipped", carrier="UPS", tracking="1Z", url="https://t.example/1Z")
    in_req(e, api(e).report, oid, "testlab", "delivered")
    assert orders(e)[0]["state"] == "delivered"

def test_a_plugin_that_raises_or_hangs_changes_nothing_and_says_so(e):
    oid = paid_order(e)
    e.A.hooks.labs.remove(e.lab)
    lab, fake = fake_lab.make(None, fail={"send"})
    e.A.hooks.labs.append(lab)
    ok, _w = in_req(e, api(e).hand_off, oid, "testlab")
    assert not ok and orders(e)[0]["state"] == "paid"
    import html
    assert "Test Lab had a problem with Avery Sample's order" in html.unescape(e.c.get("/", environ_base=E).data.decode())
    e.A.hooks.labs.remove(lab)
    lab, fake = fake_lab.make(None, hang={"send"})
    e.A.hooks.labs.append(lab)
    e.A.app.config["prints_lab_timeout"][0] = 0.5
    try:
        ok, _w = in_req(e, api(e).hand_off, oid, "testlab")
    finally:
        e.A.app.config["prints_lab_timeout"][0] = 30
    assert not ok and orders(e)[0]["state"] == "paid"
    e.A.hooks.labs.remove(lab)
    e.A.hooks.labs.append(e.lab)

def test_prepare_on_an_order_a_plugin_holds_calls_cancel(e):
    oid = paid_order(e)
    in_req(e, api(e).hand_off, oid, "testlab")
    page = e.c.get(f"/prints/order/{oid}/prepare", environ_base=E).data.decode()
    assert "confirm the lab order was canceled or never placed" in page
    r = e.c.get(f"/prints/order/{oid}/prepare.zip?confirm=1", environ_base=E)
    assert r.status_code == 200 and zipfile.ZipFile(io.BytesIO(r.get_data())).namelist()
    assert "cancel" in e.fake["calls"] and orders(e)[0]["state"] == "prepared" and orders(e)[0]["lab_id"] is None

def test_the_snapshot_never_holds_the_email_or_the_gallery_key(e):
    oid = paid_order(e)
    in_req(e, api(e).hand_off, oid, "testlab")
    snap = in_req(e, api(e).order, oid)
    text = json.dumps({k: (dict(v) if hasattr(v, "keys") else [dict(x) for x in v] if isinstance(v, tuple) else v)
                       for k, v in snap.items()})
    assert "avery@example.com" not in text.lower() and e.key not in text
    assert snap["paid_in_full"] is True and snap["ship"]["city"] == "Springfield"
    assert snap["lines"][0]["item"] and "email" not in text
    with pytest.raises(TypeError):
        snap["state"] = "closed"
    files = list(in_req(e, lambda: list(api(e).print_files(oid, "testlab"))))
    assert len(files) == 2 and all(f[3][:2] == b"\xff\xd8" for f in files)

def test_the_host_area_stays_in_its_folder(e):
    area = in_req(e, api(e).host, "testlab")
    area.put("a.lap", b"x")
    assert (e.host / "testlab.d" / "a.lap").read_bytes() == b"x" and area.list() == ["a.lap"]
    assert area.public_url("a.lap") == "https://gallery.test/g/testlab.d/a.lap"
    for bad in ("../x", "a/b", ".htaccess", "", "..", "x/../../y"):
        with pytest.raises(ValueError):
            area.put(bad, b"x")
    area.delete("a.lap")
    assert area.list() == []
    import plugins.prints as PR
    from importlib import import_module
    mod = import_module("plugins.prints")
    for bad in ("prints", "x.d/..", "PRINTS.d", "a b.d", "g-galPrints14aAAAAAAAAAAA"):
        with pytest.raises(ValueError):
            mod.check_host_folder(bad)
    assert mod.check_host_folder("prints.d") == "prints.d"
    assert date.today()
