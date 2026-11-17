import json
from datetime import date, datetime, timedelta

import pytest

import prints_env as PE
from prints_env import E, GID

OID1, OID2 = "a" * 32, "b" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def hold(e):
    with e.A.app.test_request_context():
        return e.A.app.config["print_hold"](1)

def daily(e):
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()

def sql(e, s, args=()):
    con = e.A.connect()
    con.execute(s, args)
    con.commit()
    con.close()

def invoiced(e):
    PE.send(e, PE.payload(e, OID1))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    return oid, json.loads(orders(e)[0]["invoice_data"])["total"]

def pay(e, oid, cents, kind="payment"):
    return e.c.post(f"/prints/order/{oid}/payment", data={"kind": kind, "amount": f"{cents / 100:.2f}",
                                                         "received": date.today().isoformat()}, environ_base=E)

def test_the_hold_ends_when_the_gallery_goes_dark_with_no_open_order(e):
    assert hold(e)
    old = (date.today() - timedelta(days=400)).isoformat()
    sql(e, "UPDATE sessions SET sd_shipped=?, first_visit=? WHERE id=1", (old, old))
    e.c.post("/gallery/sync", environ_base=E)
    assert PE.q(e, "SELECT upload_state FROM galleries")[0]["upload_state"] == "removed"
    assert not hold(e)
    assert "Kept for print orders" not in e.c.get("/", environ_base=E).data.decode()

def test_the_hold_ends_when_the_gallery_is_taken_down(e):
    assert hold(e)
    with e.A.app.test_request_context():
        e.A.app.config["gallery_take_down"](1)
        e.A.db().commit()
    assert not hold(e)

def test_an_open_order_still_holds_after_the_gallery_comes_down(e):
    PE.send(e, PE.payload(e, OID1))
    with e.A.app.test_request_context():
        e.A.app.config["gallery_take_down"](1)
        e.A.db().commit()
    assert orders(e)[0]["state"] == "new" and hold(e)

def test_a_line_is_priced_from_the_list_and_a_different_shown_price_is_flagged(e):
    p = PE.payload(e, OID1)
    listed = p["lines"][0]["each"]
    p["lines"][0]["each"] = 100
    p["sub"] = sum(l_["each"] * l_["qty"] for l_ in p["lines"])
    PE.send(e, p)
    assert PE.collect(e) == 1
    ln = PE.q(e, "SELECT each_cents, shown_cents FROM print_order_lines ORDER BY n")
    assert ln[0]["each_cents"] == listed and ln[0]["shown_cents"] == 100
    assert ln[1]["each_cents"] == ln[1]["shown_cents"] == p["lines"][1]["each"]
    page = e.c.get(f"/prints/order/{orders(e)[0]['id']}", environ_base=E).data.decode()
    assert "the client&#39;s page said $1.00 each" in page or "the client's page said $1.00 each" in page

def test_an_item_not_on_the_named_list_is_flagged(e):
    p = PE.payload(e, OID1)
    p["lines"][0]["item"] = "pl-999"
    PE.send(e, p)
    PE.collect(e)
    page = e.c.get(f"/prints/order/{orders(e)[0]['id']}", environ_base=E).data.decode()
    assert "Line 1: not on the price list the client ordered from" in page

def test_a_refunded_prepared_order_goes_back_to_invoiced_and_cannot_be_ordered(e):
    oid, total = invoiced(e)
    pay(e, oid, total)
    assert orders(e)[0]["state"] == "paid"
    sql(e, "UPDATE print_orders SET state='prepared', prev_state='paid'")
    pay(e, oid, total, kind="refund")
    assert orders(e)[0]["state"] == "invoiced"
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "9"}, environ_base=E)
    assert orders(e)[0]["state"] == "invoiced"

def test_ordered_at_the_lab_refuses_a_prepared_order_not_paid_in_full(e):
    oid, total = invoiced(e)
    sql(e, "UPDATE print_orders SET state='prepared', prev_state='paid'")
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "9"}, environ_base=E)
    assert orders(e)[0]["state"] == "prepared"

def test_a_part_paid_invoice_never_expires_and_today_names_it(e):
    oid, total = invoiced(e)
    pay(e, oid, 500)
    sql(e, "UPDATE print_orders SET invoice_issued=?", ((date.today() - timedelta(days=31)).isoformat(),))
    daily(e)
    o = orders(e)[0]
    assert o["state"] == "invoiced" and o["invoice_number"]
    assert "part paid, the rest unpaid after 30 days" in e.c.get("/", environ_base=E).data.decode()

def test_a_part_refund_after_day_30_does_not_void_the_invoice(e):
    oid, total = invoiced(e)
    pay(e, oid, total)
    sql(e, "UPDATE print_orders SET invoice_issued=?", ((date.today() - timedelta(days=40)).isoformat(),))
    pay(e, oid, 300, kind="refund")
    assert orders(e)[0]["state"] == "invoiced"
    daily(e)
    assert orders(e)[0]["state"] == "invoiced" and orders(e)[0]["invoice_number"]

def _shipped_long_ago(e, state):
    old = (datetime.now() - timedelta(days=46)).isoformat(timespec="seconds")
    sql(e, "UPDATE print_orders SET state=?, shipped_at=?", (state, old))

def test_a_delivered_order_closes_45_days_after_it_shipped(e):
    oid, total = invoiced(e)
    pay(e, oid, total)
    _shipped_long_ago(e, "delivered")
    daily(e)
    assert orders(e)[0]["state"] == "closed"

def test_an_order_made_before_payment_and_still_owed_stays_open_and_on_today(e):
    oid, total = invoiced(e)
    e.c.post(f"/prints/order/{oid}/before_payment", environ_base=E)
    _shipped_long_ago(e, "shipped")
    daily(e)
    assert orders(e)[0]["state"] == "shipped"
    page = e.c.get("/", environ_base=E).data.decode()
    assert f"ordered before payment, ${total / 100:,.2f} still owed" in page
    pay(e, oid, total)
    daily(e)
    assert orders(e)[0]["state"] == "closed"

def test_an_order_collected_as_the_session_is_deleted_stops_the_delete(e):
    PE.send(e, PE.payload(e, OID1))
    e.c.post("/session/1/delete", environ_base=E)
    assert PE.q(e, "SELECT 1 FROM sessions WHERE id=1")
    assert [o["oid"] for o in orders(e)] == [OID1]
    page = e.c.get("/session/1", environ_base=E).data.decode()
    assert "1 print order from its gallery is deleted with it." in page

def test_the_orders_folder_is_made_by_the_app_when_the_offer_goes_up(e):

    assert (e.host / GID / "orders").is_dir() and (e.host / GID / "orders" / "index.html").read_bytes() == b""
    PE.send(e, PE.payload(e, OID1))
    assert PE.collect(e) == 1
    assert [p.name for p in (e.host / GID / "orders").iterdir()] == ["index.html"]

def test_an_order_sent_during_the_final_collection_is_collected_before_the_delete(e):

    PE.send(e, PE.payload(e, OID1))
    late = PE.payload(e, OID2)
    sent = []

    def arrives(gid, host):
        if not sent:
            sent.append(PE.send(e, late))
    e.G.BEFORE_DELETE.append(arrives)
    try:
        with e.A.app.test_request_context():
            e.A.app.config["gallery_take_down"](1)
            e.A.db().commit()
    finally:
        e.G.BEFORE_DELETE.remove(arrives)
    assert sorted(o["oid"] for o in orders(e)) == [OID1, OID2]

def test_a_file_that_fails_for_another_reason_stays_on_the_host(e, monkeypatch):

    PE.send(e, PE.payload(e, OID1))
    import sealed

    def broken(*a, **k):
        raise RuntimeError("the studio key could not be read")
    monkeypatch.setattr(sealed, "open_studio", broken)
    PE.collect(e)
    assert list((e.host / GID / "orders").glob("*.las")) and orders(e) == []
    assert not (e.G.KEPT_ROOT / GID / "orders-unopened").exists()

def test_a_file_set_aside_is_named_on_today(e):
    d = e.host / GID / "orders"
    (d / ("1" * 32 + ".las")).write_bytes(b"LAS1" + bytes(400))
    PE.collect(e)
    page = e.c.get("/", environ_base=E).data.decode().replace("&#39;", "'")
    assert "1 file from a gallery's orders folder could not be opened as a print order" in page

def test_prepare_never_brings_back_an_order_canceled_while_the_zip_went_out(e):

    oid, total = invoiced(e)
    pay(e, oid, total)
    sql(e, "UPDATE print_order_lines SET crop_confirmed=1")
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E, buffered=False)
    it = iter(r.response)
    next(it)
    sql(e, "UPDATE print_orders SET state='canceled'")
    for _ in it:
        pass
    r.close()
    assert orders(e)[0]["state"] == "canceled"

def test_lines_of_an_invoiced_order_waiting_for_its_files_wait_for_change_the_order(e):

    oid, total = invoiced(e)
    sql(e, "UPDATE print_orders SET state='needs_files', prev_state='invoiced'")
    lid = PE.q(e, "SELECT id FROM print_order_lines ORDER BY n")[0]["id"]
    e.c.post(f"/prints/order/{oid}/line/{lid}", data={"do": "save", "qty": "9", "each": "1.00"}, environ_base=E)
    assert PE.q(e, "SELECT qty FROM print_order_lines WHERE id=?", (lid,))[0]["qty"] == 2
