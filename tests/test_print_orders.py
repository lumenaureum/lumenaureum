import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta

import pytest

import prints_env as PE
from prints_env import E, GID

OID1, OID2, OID3 = "a" * 32, "b" * 32, "c" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def test_prints_cannot_be_turned_on_while_the_list_is_sample(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, saved=False, on=False)
    r = e.c.get("/settings/prints", environ_base=E)
    assert b"SAMPLE" in r.data and b"4x6 print, Lustre" in r.data
    e.c.post("/settings/prints/switch", data={"offer": "1"}, environ_base=E)
    assert PE.q(e, "SELECT value FROM settings WHERE key='prints_on'") == []
    PE.refresh(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()
    PE.save_prices(e)
    e.c.post("/settings/prints/switch", data={"offer": "1"}, environ_base=E)
    PE.refresh(e)
    assert PE.hosted(e.host / GID, "offer.bin", e.key).exists() and (e.host / GID / "offer-kid.php").exists()

def test_the_offer_carries_prices_and_the_studio_key_never_a_cost(e):
    PE.save_prices(e, **{"cost_pl-3": "5.01"})
    e.c.post("/settings/prints/fill", environ_base=E)
    PE.refresh(e)
    off = PE.offer(e)
    assert off["cur"] == "USD" and off["kid"] == (e.host / GID / "offer-kid.php").read_text().split("\n")[1]
    by = {i["id"]: i for i in off["items"]}
    assert by["pl-3"]["each"] == 1600
    assert "cost" not in json.dumps(off) and "501" not in json.dumps(by["pl-3"])
    assert off["ship"]["pickup"]["say"] == "Pick up from Sample Studio" and off["contact"] == "studio@example.com"

    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    PE.refresh(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists() and not (e.host / GID / "offer-kid.php").exists()

def test_a_later_cost_never_moves_a_price_and_margin_is_empty_without_cost(e):
    PE.save_prices(e, **{"cost_pl-3": "5.01"})
    e.c.post("/settings/prints/fill", environ_base=E)
    PE.save_prices(e, **{"cost_pl-3": "9.00"})
    row = PE.q(e, "SELECT each_cents, cost_cents, price_set FROM price_list_items WHERE id='pl-3'")[0]
    assert row["each_cents"] == 1600 and row["cost_cents"] == 900
    page = e.c.get("/settings/prints", environ_base=E).data.decode()
    assert "$7.00, 1.8x" in page
    assert page.count("x</span>") == 1
    e.c.post("/settings/prints/fill", environ_base=E)
    assert PE.q(e, "SELECT each_cents FROM price_list_items WHERE id='pl-3'")[0]["each_cents"] == 2700
    vs = PE.q(e, "SELECT plv FROM price_list_versions ORDER BY plv")
    assert len(vs) >= 3 and vs[-1]["plv"] > vs[0]["plv"]

def test_an_email_only_offer_on_a_host_without_php(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, caps="none")
    off = PE.offer(e)
    assert off == {"v": 1, "email_only": True, "contact": "studio@example.com", "studio": "Sample Studio"}
    assert not (e.host / GID / "offer-kid.php").exists()

def test_collection_matches_records_once_and_deletes_after(e):
    env = PE.send(e, PE.payload(e, OID1))
    assert PE.collect(e) == 1
    o = orders(e)
    assert len(o) == 1 and o[0]["state"] == "new" and o[0]["contact_id"] and o[0]["oid"] == OID1
    assert not list((e.host / GID / "orders").glob("*.las"))
    lines = PE.q(e, "SELECT * FROM print_order_lines ORDER BY n")
    assert [l["qty"] for l in lines] == [2, 1] and lines[1]["crop_confirmed"] == 1 and lines[0]["crop_confirmed"] == 0

    (e.host / GID / "orders" / ("f" * 32 + ".las")).write_bytes(env)
    assert PE.collect(e) == 0 and len(orders(e)) == 1

    e.c.post(f"/prints/order/{o[0]['id']}/cancel", data={}, environ_base=E)
    PE.q(e, "DELETE FROM print_orders")
    con = e.A.connect(); con.execute("DELETE FROM print_orders"); con.commit(); con.close()
    (e.host / GID / "orders" / ("f" * 32 + ".las")).write_bytes(env)
    assert PE.collect(e) == 0 and orders(e) == []

def test_unknown_orderers_go_to_the_review_list_never_a_client(e):
    PE.send(e, PE.payload(e, OID1, email="grandma@example.org", name="Grandma Rose"))
    PE.collect(e)
    o = orders(e)[0]
    assert o["state"] == "unmatched" and o["contact_id"] is None
    n_people = len(PE.q(e, "SELECT * FROM people"))
    page = e.c.get("/prints/review", environ_base=E).data.decode()
    assert "Grandma Rose" in page and "Bill Grandma Rose as a guest" in page and "Make a new client" in page
    assert len(PE.q(e, "SELECT * FROM people")) == n_people
    e.c.post(f"/prints/order/{o['id']}/add_contact", environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "new" and o["contact_id"]
    assert PE.q(e, "SELECT household_id FROM people WHERE id=?", (o["contact_id"],))[0]["household_id"] == 1

def test_a_file_that_does_not_open_is_set_aside_and_a_bad_payload_is_unreadable(e):
    d = e.host / GID / "orders"
    d.mkdir(exist_ok=True)
    (d / ("1" * 32 + ".las")).write_bytes(b"LAS1" + os.urandom(400))
    p = PE.payload(e, OID2)
    p["sub"] += 1
    PE.send(e, p)
    PE.collect(e)
    assert (e.G.KEPT_ROOT / GID / "orders-unopened" / ("1" * 32 + ".las")).is_file()
    o = orders(e)
    assert len(o) == 1 and o[0]["state"] == "unreadable" and "total" in json.loads(o[0]["flags"])["why"]
    assert not list(d.glob("*.las"))

def flow_to_paid(e, **ship):
    PE.send(e, PE.payload(e, OID1, **ship))
    PE.collect(e)
    o = orders(e)[0]
    oid = o["id"]
    return oid

def test_the_invoice_waits_for_shipping_then_takes_the_inv_run(e):
    oid = flow_to_paid(e)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert orders(e)[0]["state"] == "new"
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.12"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "invoiced" and o["invoice_number"] == f"INV-{date.today().year}-0001"
    inv = json.loads(o["invoice_data"])
    assert inv["ship_cents"] == 812 and inv["total"] == inv["sub"] + 812
    html = e.c.get(f"/prints/order/{oid}/invoice", environ_base=E).data.decode()
    assert "Prints are made by our lab after this invoice is paid in full." in html and "a size or address you chose" not in html
    assert "a crop, size or address you chose is a new order" in html

    with e.A.app.test_request_context():
        with e.A.app.app_context():
            n = e.A.doc_number(1, "INV")
            e.A.db().commit()
    assert n == f"INV-{date.today().year}-0002"

def pay(e, oid, amount):
    return e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": amount, "received": date.today().isoformat()},
                    environ_base=E)

def invoiced(e):
    oid = flow_to_paid(e)
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    return oid, json.loads(orders(e)[0]["invoice_data"])["total"]

def test_partial_payment_never_makes_it_paid_and_the_session_balance_is_untouched(e):
    oid, total = invoiced(e)
    with e.A.app.app_context():
        before = e.A.totals(1)
    pay(e, oid, f"{(total - 100) / 100:.2f}")
    assert orders(e)[0]["state"] == "invoiced"
    pay(e, oid, "1.00")
    assert orders(e)[0]["state"] == "paid"
    with e.A.app.app_context():
        assert e.A.totals(1) == before
    pid = PE.q(e, "SELECT id FROM print_payments ORDER BY id DESC")[0]["id"]
    e.c.post(f"/prints/order/{oid}/void_payment", data={"pid": pid}, environ_base=E)
    assert orders(e)[0]["state"] == "invoiced"

def test_every_hand_edge_and_the_refused_ones(e):
    oid, total = invoiced(e)
    lid = PE.q(e, "SELECT id FROM print_order_lines WHERE crop_confirmed=0")[0]["id"]
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "77"}, environ_base=E)
    assert orders(e)[0]["state"] == "invoiced"
    pay(e, oid, f"{total / 100:.2f}")
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E)
    assert r.status_code == 302
    e.c.post(f"/prints/order/{oid}/line/{lid}", data={"do": "confirm"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "77", "cost_products": "20",
                                                    "cost_shipping": "8.50"}, environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "ordered" and o["lab_name"] == "A Lab" and o["cost_products"] == 2000
    e.c.post(f"/prints/order/{oid}/delivered", environ_base=E)
    assert orders(e)[0]["state"] == "ordered"
    e.c.post(f"/prints/order/{oid}/shipped", data={"carrier": "UPS", "tracking": "1Z", "url": "https://t.example/1Z"}, environ_base=E)
    assert orders(e)[0]["state"] == "shipped"
    e.c.post(f"/prints/order/{oid}/remake", data={"note": "arrived bent"}, environ_base=E)
    assert orders(e)[0]["state"] == "remake"
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "78"}, environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "ordered" and [n["number"] for n in json.loads(o["lab_orders"])] == ["77", "78"]
    e.c.post(f"/prints/order/{oid}/shipped", data={}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/delivered", environ_base=E)
    e.c.post(f"/prints/order/{oid}/close", environ_base=E)
    assert orders(e)[0]["state"] == "closed"
    head = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode().split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert " at A Lab, no. 77, 78" in head
    e.c.post(f"/prints/order/{oid}/cancel", environ_base=E)
    assert orders(e)[0]["state"] == "closed"
    hist = [r["action"] for r in PE.q(e, "SELECT action FROM audit WHERE session_id=1 AND action LIKE 'Print order from%'")]
    assert any("Ordered at the lab" in h for h in hist) and any(": Done" in h for h in hist)

def test_cancel_after_ordering_asks_about_the_lab(e):
    oid, total = invoiced(e)
    pay(e, oid, f"{total / 100:.2f}")
    PE.q(e, "SELECT 1")
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "77"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/cancel", data={}, environ_base=E)
    assert orders(e)[0]["state"] == "ordered"
    e.c.post(f"/prints/order/{oid}/cancel", data={"lab": "finishing"}, environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "canceled" and o["cancel_lab"] == "The lab is finishing the cancellation"

def test_prepare_streams_the_files_and_the_sheet_writing_nothing_readable(e, monkeypatch):
    oid, total = invoiced(e)
    pay(e, oid, f"{total / 100:.2f}")
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    written = []
    real_open = open

    def watch(f, mode="r", *a, **kw):
        if any(m in mode for m in "wax") and str(f).startswith(str(e.tmp)):
            written.append(str(f))
        return real_open(f, mode, *a, **kw)
    import builtins
    monkeypatch.setattr(builtins, "open", watch)
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E)
    data = r.get_data()
    monkeypatch.setattr(builtins, "open", real_open)
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('-prints.zip"')
    assert not [w for w in written if "crm.db" not in w]
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    inv = orders(e)[0]["invoice_number"]
    assert f"{inv}-1-8x10-Lustre-x2.jpg" in names and f"{inv}-2-4x6-Lustre-x1.jpg" in names
    assert "order.txt" in names and "order-sheet.pdf" in names
    sheet = z.read("order.txt").decode()
    assert "Lane-034.jpg" in sheet and "Lane-035.jpg" in sheet and "Number of prints: 3" in sheet
    assert "$" not in sheet and "avery@example.com" not in sheet.lower()
    from PIL import Image
    with Image.open(io.BytesIO(z.read(f"{inv}-1-8x10-Lustre-x2.jpg"))) as im:
        assert im.size[0] * 5 == im.size[1] * 4 or im.size[0] * 4 == im.size[1] * 5

        want = round(min(im.size) / 8)
        assert im.info.get("dpi") and round(im.info["dpi"][0]) == want and want != 72
        ex = im.getexif()
        assert not ex.get(282) or round(float(ex[282])) == want
    assert orders(e)[0]["state"] == "prepared"

def test_a_broken_stream_leaves_the_state(e):
    oid, total = invoiced(e)
    pay(e, oid, f"{total / 100:.2f}")
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E, buffered=False)
    it = iter(r.response)
    next(it)
    r.close()
    assert orders(e)[0]["state"] == "paid"

def test_a_line_below_the_floor_is_held_out(e):
    import printorders as P
    m = PE.manifest(e)
    small = next(it for it in m["items"] if it["n"] == "Lane-036.jpg")
    off = PE.offer(e)
    big = next(i for i in off["items"] if i["label"].startswith("20x30"))
    line = dict(p=small["o"], n="Lane-036.jpg", item=big["id"], qty=1, crop=[0, 0, 1, 1], turn=0, each=big["each"], ppi=20)
    PE.send(e, PE.payload(e, OID3, lines=[line]))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    with e.A.app.test_request_context():
        with pytest.raises(Exception):
            list(e.A.app.config["prints_api"].print_files(oid))
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "change the crop or the size" in page
    del P

def test_order_before_payment_is_a_danger_override_with_history(e):
    oid, total = invoiced(e)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "Order before payment" in page and "You carry the risk" in page
    e.c.post(f"/prints/order/{oid}/before_payment", environ_base=E)
    assert orders(e)[0]["before_payment"] == 1
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "1"}, environ_base=E)
    assert orders(e)[0]["state"] == "ordered"
    assert PE.q(e, "SELECT 1 FROM audit WHERE action LIKE '%Order before payment turned on%'")

def test_unpaid_expires_at_thirty_days_and_reopens(e):
    oid, total = invoiced(e)
    old = (date.today() - timedelta(days=31)).isoformat()
    con = e.A.connect(); con.execute("UPDATE print_orders SET invoice_issued=?", (old,)); con.commit(); con.close()
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()
    o = orders(e)[0]
    assert o["state"] == "expired" and o["invoice_number"] is None
    assert json.loads(o["flags"])["voided"][0]["why"] == "expired unpaid"
    assert PE.q(e, "SELECT kind FROM doc_numbers WHERE kind LIKE 'PRINT-void:%'")
    e.c.post(f"/prints/order/{oid}/reopen", environ_base=E)
    assert orders(e)[0]["state"] == "new"

def test_today_lines(e):
    oid, total = invoiced(e)
    con = e.A.connect()
    con.execute("UPDATE print_orders SET invoice_issued=?", ((date.today() - timedelta(days=8)).isoformat(),))
    con.commit(); con.close()
    page = e.c.get("/", environ_base=E).data.decode()
    assert "waiting for payment since" in page
    PE.send(e, PE.payload(e, OID2, email="x@example.org", name="Pat Doe"))
    PE.collect(e)
    page = e.c.get("/", environ_base=E).data.decode()
    assert "Print orders from people not on file (1)" in page

def test_the_ship_to_of_a_non_client_is_reduced_a_year_after_closing(e):
    PE.send(e, PE.payload(e, OID1, email="g@example.org", name="Grandma Rose",
                          ship=dict(how="other", name="Grandma Rose", addr1="9 Oak Ln", addr2="", city="Peoria", state="IL",
                                    zip="61602", country="US", phone="")))
    PE.collect(e)
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    con = e.A.connect()
    con.execute("UPDATE print_orders SET state='closed', closed_at=?, invoice_data='{\"ship_lines\": [\"9 Oak Ln\"]}'", (old,))
    con.commit(); con.close()
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()
    o = orders(e)[0]
    ship = json.loads(o["payload"])["ship"]
    assert ship == dict(how="other", city="Peoria", state="IL", zip="61602", country="US", reduced=True)
    assert "9 Oak Ln" in o["invoice_data"]

def test_retention_holds_the_build_while_prints_are_offered(e):
    with e.A.app.test_request_context():
        assert e.A.app.config["print_hold"](1)
    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    PE.refresh(e)
    with e.A.app.test_request_context():
        assert not e.A.app.config["print_hold"](1)
    PE.send
    e.c.post("/session/1/prints/switch", data={"offer": "1"}, environ_base=E)

def test_tax_on_prints_and_shipping_by_setting(e):
    con = e.A.connect()
    for k, v in (("tax_mode", "flat"), ("sales_tax_pct", "6"), ("tax_prints", "1"), ("tax_print_shipping", "0")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit(); con.close()
    oid, total = invoiced(e)
    inv = json.loads(orders(e)[0]["invoice_data"])
    assert inv["tax_cents"] == round(inv["sub"] * 0.06) and inv["rate"] == 6
    con = e.A.connect(); con.execute("UPDATE settings SET value='1' WHERE key='tax_print_shipping'"); con.commit(); con.close()
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    inv = json.loads(orders(e)[0]["invoice_data"])
    assert inv["tax_cents"] == round((inv["sub"] + 800) * 0.06)
    con = e.A.connect(); con.execute("UPDATE settings SET value='none' WHERE key='tax_mode'"); con.commit(); con.close()
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert json.loads(orders(e)[0]["invoice_data"])["tax_cents"] == 0

def test_finances_keep_prints_apart_and_the_export_has_them(e):
    oid, total = invoiced(e)
    pay(e, oid, f"{total / 100:.2f}")
    page = e.c.get("/money", environ_base=E).data.decode()
    assert "Print orders" in page and "Profit on prints" in page
    r = e.c.get(f"/money/accounts.zip?year={date.today().year}", environ_base=E, headers={"Sec-Fetch-Site": "same-origin"})
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert "prints.csv" in z.namelist() and "print-lab-costs.csv" in z.namelist()
    assert f"{total / 100:.2f}" in z.read("prints.csv").decode("utf-8-sig")

def test_a_session_holding_print_money_is_not_deleted(e):
    oid, total = invoiced(e)
    pay(e, oid, "5.00")
    e.c.post("/session/1/delete", environ_base=E)
    assert PE.q(e, "SELECT 1 FROM sessions WHERE id=1")

def test_pages_render(e):
    oid, total = invoiced(e)
    for u in ("/settings/prints", f"/prints/order/{oid}", "/prints/review", f"/prints/order/{oid}/prepare", "/session/1",
              f"/prints/order/{oid}/line/{PE.q(e, 'SELECT id FROM print_order_lines')[0]['id']}/crop"):
        r = e.c.get(u, environ_base=E, follow_redirects=True)
        assert r.status_code == 200, u
    lid = PE.q(e, "SELECT id FROM print_order_lines")[0]["id"]
    r = e.c.get(f"/prints/order/{oid}/line/{lid}.jpg", environ_base=E)
    assert r.mimetype == "image/jpeg" and r.headers["Cache-Control"] == "no-store"

def test_retention_waits_while_prints_are_offered_and_gives_fresh_notice(e):

    old = (date.today() - timedelta(days=400)).isoformat()
    con = e.A.connect()
    con.execute("UPDATE sessions SET sd_shipped=?, stage='delivered' WHERE id=1", (old,))
    con.execute("INSERT INTO retention (session_id, keep_days, notice) VALUES (1, 90, ?)",
                (json.dumps({"purge": [(date.today() - timedelta(days=30)).isoformat(), "2025-01-01"]}),))
    con.commit(); con.close()
    with e.A.app.test_request_context():
        e.A.app.config["retention_pass"]()
        e.A.db().commit()
    assert (e.G.OUT_ROOT / GID / "manifest.bin").is_file()
    n = json.loads(PE.q(e, "SELECT notice FROM retention WHERE session_id=1")[0]["notice"])
    assert n["print_hold"] == date.today().isoformat() and n["purge"][0] == date.today().isoformat()
    assert "Kept for print orders: The Sample Family" in e.c.get("/", environ_base=E).data.decode()
    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    PE.refresh(e)
    with e.A.app.test_request_context():
        e.A.app.config["retention_pass"]()
        e.A.db().commit()
    assert (e.G.OUT_ROOT / GID / "manifest.bin").is_file()
    assert "print_hold" not in json.loads(PE.q(e, "SELECT notice FROM retention WHERE session_id=1")[0]["notice"])

def test_the_statement_lists_print_invoices_and_payments(e):
    oid, total = invoiced(e)
    pay(e, oid, f"{total / 100:.2f}")
    inv = orders(e)[0]["invoice_number"]
    page = e.c.get(f"/client/1/statement?m={date.today().strftime('%Y-%m')}", environ_base=E).data.decode()
    assert inv in page and "print order" in page

def test_an_order_whose_files_went_needs_the_files(e):
    import shutil
    oid, total = invoiced(e)
    shutil.rmtree(e.G.OUT_ROOT / GID)
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()
    o = orders(e)[0]
    assert o["state"] == "needs_files" and o["prev_state"] == "invoiced"
    assert "needs the photo files" in e.c.get("/", environ_base=E).data.decode()
    e.c.post(f"/prints/order/{oid}/files_back", environ_base=E)
    assert orders(e)[0]["state"] == "needs_files"

def test_a_low_price_is_named_on_today_once(e):
    PE.save_prices(e, **{"each_pl-1": "1.00", "cost_pl-1": "0.90"})
    assert "4x6 print, Lustre priced below 1.5 times cost" in e.c.get("/", environ_base=E).data.decode()
    con = e.A.connect()
    seen = json.loads(con.execute("SELECT value FROM settings WHERE key='prints_low_named'").fetchone()[0])
    seen["pl-1"][1] = (date.today() - timedelta(days=1)).isoformat()
    con.execute("UPDATE settings SET value=? WHERE key='prints_low_named'", (json.dumps(seen),))
    con.commit(); con.close()
    assert "4x6 print, Lustre priced below 1.5 times cost" not in e.c.get("/", environ_base=E).data.decode()
    assert "data-tip=\"Below 1.5 times your cost\"" in e.c.get("/settings/prints", environ_base=E).data.decode()

def test_the_offer_goes_up_with_the_gallery_before_its_page(e, monkeypatch):
    from vault import backends as VB
    on = PE.hosted(e.host / GID, "offer.bin", e.key)
    on.unlink()
    on = on.name
    (e.host / GID / "offer-kid.php").unlink()
    put = []
    real = VB.LocalBackend.put_stream

    def spy(self, name, *a, **kw):
        put.append(name.rsplit("/", 1)[-1])
        return real(self, name, *a, **kw)
    monkeypatch.setattr(VB.LocalBackend, "put_stream", spy)
    e.G.upload_gallery(GID, 30, "2099-01-01")
    assert on in put and put.index(on) < put.index("index.html")
    assert put.index("orders.php") < put.index(on)
