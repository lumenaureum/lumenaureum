import json

import pytest

import prints_env as PE
from prints_env import E

OID1, OID2 = "a" * 32, "b" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def item(e, iid):
    return PE.q(e, "SELECT * FROM price_list_items WHERE id=?", (iid,))[0]

def history(e):
    return [f"{r['action']} {r['note'] or ''}" for r in PE.q(e, "SELECT * FROM audit ORDER BY id")]

def test_a_cost_on_a_sample_line_never_changes_its_price(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, saved=False, on=False)
    e.c.get("/settings/prints", environ_base=E)
    was = item(e, "pl-3")["each_cents"]
    PE.save_prices(e, **{"cost_pl-3": "5.01"})
    row = item(e, "pl-3")
    assert row["each_cents"] == was and row["cost_cents"] == 501

def test_a_cost_on_a_blank_price_or_a_new_size_leaves_the_price_blank(e):
    PE.save_prices(e, **{"cost_pl-3": "5.01", "each_pl-3": ""})
    assert item(e, "pl-3")["each_cents"] is None
    PE.save_prices(e, **{"label_new": "5x5 print", "w_new": "5", "h_new": "5", "cost_new": "2.00"})
    new = PE.q(e, "SELECT * FROM price_list_items WHERE label='5x5 print'")[0]
    assert new["each_cents"] is None and new["cost_cents"] == 200

def test_the_margin_follows_a_typed_cost(e):
    PE.save_prices(e, **{"cost_pl-3": "10.00"})
    page = e.c.get("/settings/prints", environ_base=E).data.decode()
    assert "$20.00, 3.0x" in page

def test_fill_prices_from_costs_names_the_markup_says_what_changes_and_is_in_history(e):
    PE.save_prices(e, **{"cost_pl-3": "5.01", "markup": "3.5"})
    page = e.c.get("/settings/prints", environ_base=E).data.decode()
    assert "Fill prices from costs (3.5x)" in page
    assert "8x10" in page and "$30.00 to $18.00" in page
    assert item(e, "pl-3")["each_cents"] == 3000
    e.c.post("/settings/prints/fill", environ_base=E)
    assert item(e, "pl-3")["each_cents"] == 1800
    assert any("Filled print prices from lab costs" in h and "$30.00 to $18.00" in h for h in history(e))
    page = e.c.get("/settings/prints", environ_base=E).data.decode()
    assert "$12.99, 3.6x" in page
    assert "Nothing would change" in page

def _drafts(page):

    import html
    import re
    from urllib.parse import unquote
    out = []
    for href in re.findall(r'href="((?:mailto|sms):[^"]*)"', page):
        href = html.unescape(href)
        if "body=" in href:
            out.append(unquote(href.split("body=", 1)[1]))
    return out

@pytest.fixture
def demo(tmp_path, monkeypatch):
    import os
    import runpy
    from helpers import fresh_modules, signed_in_client
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(PE.ROOT, "seed_demo.py"), run_name="seed")
    return A, c

def test_session_document_drafts_carry_their_key_facts_and_promise_no_attachment(demo):
    A, c = demo
    con = A.connect()
    sid = con.execute("""SELECT s.id FROM sessions s WHERE s.id IN (SELECT session_id FROM lines) AND s.type='family'
                         ORDER BY s.id LIMIT 1""").fetchone()[0]
    vid = con.execute("SELECT id FROM quote_versions ORDER BY id LIMIT 1").fetchone()
    con.close()
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    page = c.get(f"/session/{sid}/invoice", environ_base=E).data.decode()
    with A.app.app_context():
        number = A.q("SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)["number"]
        total = A.money(A.totals(sid)["quoted"])
    mails = _drafts(page)
    assert mails and all("attached" not in m for m in mails)
    body = mails[0]
    assert f"Invoice {number} for Family session" in body and total in body
    assert "You can pay by" in body or "Ask me how to pay." in body or "It is paid in full" in body
    for url in [f"/session/{sid}/details", f"/session/{sid}/agreement", "/fees/sheet?sid=%d" % sid] + (
            [f"/quote/{vid[0]}"] if vid else []):
        page = c.get(url, environ_base=E, follow_redirects=True).data.decode()
        mails = _drafts(page)
        assert all("attached" not in m and "send you the PDF" not in m for m in mails), url
    if vid:
        body = _drafts(c.get(f"/quote/{vid[0]}", environ_base=E).data.decode())[0]
        assert "Your quote for" in body and "$" in body and "It is good through" in body
    body = _drafts(c.get(f"/session/{sid}/details", environ_base=E).data.decode())[0]
    assert "Your session details: Family session" in body
    body = _drafts(c.get("/fees/sheet?sid=%d" % sid, environ_base=E).data.decode())[0]
    assert "My prices" in body and "$" in body

def test_no_draft_in_the_code_says_attached():
    import re
    from pathlib import Path
    for name in ("app.py", "plugins/prints.py", "plugins/gallery.py"):
        text = (Path(PE.ROOT) / name).read_text()
        assert not re.search(r"\b(is|are) attached\b", text), name

def test_the_print_invoice_draft_gives_number_total_and_how_to_pay(e):
    PE.send(e, PE.payload(e, OID1))
    PE.collect(e)
    oid = PE.q(e, "SELECT id FROM print_orders")[0]["id"]
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    o = PE.q(e, "SELECT * FROM print_orders")[0]
    total = json.loads(o["invoice_data"])["total"]
    body = _drafts(e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode())[0]
    assert f"Invoice {o['invoice_number']} for 3 prints from the gallery of The Sample Family: ${total / 100:,.2f}" in body
    assert "shipping $8.00" in body and ("You can pay by" in body or "Ask me how to pay." in body)
    assert "attached" not in body

def new_order(e, oid=OID1, **kw):
    PE.send(e, PE.payload(e, oid, **kw))
    PE.collect(e)
    return PE.q(e, "SELECT id FROM print_orders WHERE oid=?", (oid,))[0]["id"]

def order_row(e, oid):
    return PE.q(e, "SELECT * FROM print_orders WHERE id=?", (oid,))[0]

def issued_ship(e, oid):
    return json.loads(order_row(e, oid)["invoice_data"])["ship_cents"]

def test_after_change_the_order_shipping_follows_the_rules_again(e):
    oid = new_order(e)
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert issued_ship(e, oid) == 800
    PE.save_prices(e, ship_rule_us="flat", ship_flat_us="12.00")
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "Shipping set by hand" not in page
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert issued_ship(e, oid) == 1200
    assert not order_row(e, oid)["ship_by_hand"]

def test_a_hand_set_shipping_charge_is_kept_and_flagged_beside_the_rules(e):
    oid = new_order(e)
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00", "ship": "15.00"}, environ_base=E)
    assert order_row(e, oid)["ship_by_hand"] == 1
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    PE.save_prices(e, ship_rule_us="flat", ship_flat_us="12.00")
    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "Shipping set by hand: $15.00 (rules now say $12.00)" in page
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert issued_ship(e, oid) == 1500

    e.c.post(f"/prints/order/{oid}/reissue", environ_base=E)
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00", "ship": ""}, environ_base=E)
    assert order_row(e, oid)["ship_by_hand"] == 0
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    assert issued_ship(e, oid) == 1200

def pay(e, oid, cents, kind="payment", day=None):
    from datetime import date
    return e.c.post(f"/prints/order/{oid}/payment", data={"kind": kind, "amount": f"{cents / 100:.2f}",
                                                         "received": day or date.today().isoformat()}, environ_base=E)

def paid_order(e, oid=OID1, **kw):
    oid_ = new_order(e, oid, **kw)
    e.c.post(f"/prints/order/{oid_}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid_}/invoice", environ_base=E)
    pay(e, oid_, json.loads(order_row(e, oid_)["invoice_data"])["total"])
    return oid_

def ordered(e, oid, number="77", products="20.00", shipping="8.00", tax="1.00"):
    r = e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": number, "cost_products": products,
                                                     "cost_shipping": shipping, "cost_tax": tax}, environ_base=E)
    assert r.status_code == 302
    return r

def year(e, y):
    with e.A.app.test_request_context():
        return e.A.app.config["prints_year"](y)

def this_year():
    from datetime import date
    return date.today().year

def test_canceling_keeps_the_lab_cost_unless_the_lab_order_was_canceled(e):
    a = paid_order(e, OID1)
    ordered(e, a, "77")
    e.c.post(f"/prints/order/{a}/cancel", data={"lab": "not"}, environ_base=E)
    assert order_row(e, a)["state"] == "canceled"
    y = year(e, this_year())
    assert y["cost"] == 2800 and y["lab_tax"] == 100
    b = paid_order(e, OID2, ship=dict(how="pickup"))
    ordered(e, b, "78", products="10.00", shipping="", tax="")
    e.c.post(f"/prints/order/{b}/cancel", data={"lab": "canceled"}, environ_base=E)
    y = year(e, this_year())
    assert y["cost"] == 2800
    assert [c["what"] for c in y["costs"]] == ["lab order", "lab order", "lab refund"]

def test_a_later_lab_refund_is_marked_on_its_own_day_and_past_years_never_change(e):
    a = paid_order(e)
    ordered(e, a, "77")
    last = this_year() - 1
    con = e.A.connect()
    con.execute("UPDATE print_lab_costs SET day=?", (f"{last}-12-20",))
    con.commit()
    con.close()
    e.c.post(f"/prints/order/{a}/cancel", data={"lab": "finishing"}, environ_base=E)
    before = year(e, last)["cost"]
    assert before == 2800 and year(e, this_year())["cost"] == 0
    cid = PE.q(e, "SELECT id FROM print_lab_costs WHERE kind='order'")[0]["id"]
    e.c.post(f"/prints/order/{a}/lab_refund", data={"cost": str(cid)}, environ_base=E)
    assert year(e, last)["cost"] == before
    assert year(e, this_year())["cost"] == -2800
    e.c.post(f"/prints/order/{a}/lab_refund", data={"cost": str(cid)}, environ_base=E)
    assert len(PE.q(e, "SELECT * FROM print_lab_costs WHERE kind='refund'")) == 1

def test_a_remake_is_its_own_cost_and_never_replaces_or_moves_the_original(e):
    a = paid_order(e)
    ordered(e, a, "77")
    last = this_year() - 1
    con = e.A.connect()
    con.execute("UPDATE print_lab_costs SET day=?", (f"{last}-12-20",))
    con.execute("UPDATE print_orders SET ordered_at=?", (f"{last}-12-20T10:00:00",))
    con.commit()
    con.close()
    e.c.post(f"/prints/order/{a}/shipped", data={"carrier": "UPS"}, environ_base=E)
    e.c.post(f"/prints/order/{a}/remake", data={"note": "damaged"}, environ_base=E)
    assert order_row(e, a)["state"] == "remake"
    e.c.post(f"/prints/order/{a}/ordered", data={"lab_name": "A Lab", "lab_number": "88"}, environ_base=E)
    o = order_row(e, a)
    assert o["state"] == "ordered" and o["cost_products"] == 2000 and o["ordered_at"].startswith(str(last))
    rows = PE.q(e, "SELECT * FROM print_lab_costs ORDER BY id")
    assert [(r["kind"], r["lab_number"], r["products"]) for r in rows] == [("order", "77", 2000), ("remake", "88", 0)]
    assert year(e, last)["cost"] == 2800 and year(e, this_year())["cost"] == 0
    page = e.c.get(f"/prints/order/{a}", environ_base=E).data.decode()
    assert "Remake 88 at A Lab: $0.00" in page and "Lab order 77 at A Lab" in page

def test_the_accountant_export_lists_each_lab_cost_as_paid(e):
    a = paid_order(e)
    ordered(e, a, "77")
    e.c.post(f"/prints/order/{a}/cancel", data={"lab": "canceled"}, environ_base=E)
    with e.A.app.test_request_context():
        files = {n: (h, rows) for n, h, rows in e.A.app.config["prints_accounts"](this_year())}
    h, rows = files["print-lab-costs.csv"]
    assert h[:6] == ["date paid", "invoice", "billed to", "lab", "what", "lab order"]
    assert [r_[4] for r_ in rows] == ["lab order", "lab refund"] and rows[1][6] == "-20.00"

def test_payments_in_parts_split_exactly_into_the_invoice_parts(e):
    oid = new_order(e)
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    inv = json.loads(order_row(e, oid)["invoice_data"])
    third = inv["total"] // 3
    for c_ in (third, third, inv["total"] - 2 * third):
        pay(e, oid, c_)
    assert order_row(e, oid)["state"] == "paid"
    y = year(e, this_year())
    rows = [r for r in y["rows"] if r["invoice"] == inv["number"]]
    assert len(rows) == 3
    assert sum(r["sales"] for r in rows) == inv["sub"]
    assert sum(r["ship"] for r in rows) == inv["ship_cents"]
    assert sum(r["tax"] for r in rows) == inv["tax_cents"]
    assert all(r["sales"] + r["ship"] + r["tax"] == r["amount"] for r in rows)

GRANDMA = dict(email="rose@example.org", name="Grandma Rose")
ROSE_HOME = dict(how="me", name="", addr1="9 Oak Ln", addr2="", city="Peoria", state="IL", zip="61602", country="US", phone="")

def statement(e, hid):
    from datetime import date
    return e.c.get(f"/client/{hid}/statement?m={date.today().strftime('%Y-%m')}", environ_base=E).data.decode()

def invoice_and_pay(e, oid):
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    o = order_row(e, oid)
    pay(e, oid, 1000)
    return o["invoice_number"]

def test_accepting_an_outside_orderer_makes_them_a_client_of_their_own(e):
    oid = new_order(e, ship=ROSE_HOME, **GRANDMA)
    assert order_row(e, oid)["state"] == "unmatched"
    e.c.post(f"/prints/order/{oid}/new_client", environ_base=E)
    o = order_row(e, oid)
    rose = PE.q(e, "SELECT h.* FROM households h JOIN people p ON p.household_id=h.id WHERE p.id=?", (o["contact_id"],))[0]
    assert rose["id"] != 1 and rose["name"] == "Grandma Rose" and rose["referred_by"] == 1
    assert "Ordered prints from the gallery of The Sample Family" in rose["notes"]
    assert "9 Oak Ln" in rose["address"] and "Peoria, IL 61602" in rose["address"]
    number = invoice_and_pay(e, oid)
    mine = statement(e, rose["id"])
    assert number in mine and "Prints from the gallery of The Sample Family" in mine and "$10" in mine
    assert number not in statement(e, 1)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "The ship-to address is not on file" not in page

def test_an_orderer_who_is_a_client_elsewhere_has_the_order_on_their_statement(e):
    con = e.A.connect()
    con.execute("INSERT INTO households (id, name, address) VALUES (7, 'The Rose Family', '9 Oak Ln\nPeoria, IL 61602')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (7, 'Grandma Rose', 'adult', 'rose@example.org')")
    con.commit()
    con.close()
    oid = new_order(e, ship=ROSE_HOME, **GRANDMA)
    assert order_row(e, oid)["state"] == "new"
    number = invoice_and_pay(e, oid)
    assert number in statement(e, 7)

def close_long_ago(e):
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    con = e.A.connect()
    con.execute("UPDATE print_orders SET state='closed', closed_at=?", (old,))
    con.commit()
    con.close()
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()

def ship_now(e, oid):
    return json.loads(order_row(e, oid)["payload"])["ship"]

def test_the_trim_applies_only_to_someone_elses_address_and_guests_never_accepted(e):
    client_me = new_order(e, OID1)
    client_other = new_order(e, OID2, ship=dict(ROSE_HOME, how="other", name="Grandma Rose"))
    accepted = new_order(e, "c" * 32, ship=ROSE_HOME, **GRANDMA)
    e.c.post(f"/prints/order/{accepted}/new_client", environ_base=E)
    guest = new_order(e, "d" * 32, ship=dict(ROSE_HOME, addr1="1 Pine St"), email="g@example.org", name="A Guest")
    e.c.post(f"/prints/order/{guest}/guest", environ_base=E)
    e.c.post(f"/prints/order/{guest}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{guest}/invoice", environ_base=E)
    html = order_row(e, guest)["invoice_html"]
    close_long_ago(e)
    assert ship_now(e, client_me)["addr1"] == "12 Elm St"
    assert ship_now(e, accepted)["addr1"] == "9 Oak Ln"
    assert ship_now(e, client_other) == dict(how="other", city="Peoria", state="IL", zip="61602", country="US", reduced=True)
    assert ship_now(e, guest)["reduced"] and "addr1" not in ship_now(e, guest)
    assert order_row(e, guest)["invoice_html"] == html and "1 Pine St" in html
    import printorders as P
    assert not P.trimmed(dict(how="pickup"), False)

def test_a_reopened_order_with_a_reduced_ship_to_is_flagged_and_held_from_the_lab(e):
    from datetime import datetime, timedelta
    oid = new_order(e, ship=dict(ROSE_HOME, how="other", name="Grandma Rose"))
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    con = e.A.connect()
    con.execute("UPDATE print_orders SET state='expired', closed_at=?", (old,))
    con.commit()
    con.close()
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()
    assert ship_now(e, oid)["reduced"]
    e.c.post(f"/prints/order/{oid}/reopen", environ_base=E)
    assert order_row(e, oid)["state"] == "new"
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "Shipping address incomplete" in page and "Save the full address" in page
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    pay(e, oid, json.loads(order_row(e, oid)["invoice_data"])["total"])
    assert order_row(e, oid)["state"] == "paid"
    prep = e.c.get(f"/prints/order/{oid}/prepare", environ_base=E).data.decode()
    assert "Shipping address incomplete: enter the full address on the order first." in prep
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E)
    assert r.status_code == 302 and "prepare.zip" not in r.headers["Location"]
    with e.A.app.test_request_context():
        ok, words = e.A.app.config["prints_api"].hand_off(oid, "nolab")
    assert not ok
    e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": "1"}, environ_base=E)
    assert order_row(e, oid)["state"] == "paid"
    e.c.get(f"/prints/order/{oid}", environ_base=E)
    r = e.c.post(f"/prints/order/{oid}/ship_to", data={"name": "Grandma Rose", "addr1": "9 Oak Ln", "city": "Peoria",
                                                      "state": "IL", "zip": "61602", "country": "US"}, environ_base=E)
    assert r.status_code == 302
    sh = ship_now(e, oid)
    assert sh["addr1"] == "9 Oak Ln" and not sh.get("reduced") and order_row(e, oid)["reduced_at"] is None
    prep = e.c.get(f"/prints/order/{oid}/prepare", environ_base=E).data.decode()
    assert "Shipping address incomplete" not in prep
    assert "Shipping address incomplete" not in e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()

def _settings(A, **kv):
    con = A.connect()
    for k, v in kv.items():
        con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    con.commit()
    con.close()

def _unlock_wording(A):
    with A.app.test_request_context():
        A.db()
        A._freeze_reviewed_wording()
        A.db().commit()
        return A.setting("agreement_text"), A.setting("wording_frozen_1_4")

def test_new_installs_get_my_web_host(e):
    import wording
    assert "(such as my web host, email, payment and print lab)" in wording.STANDARD["agreement"]
    assert "my web host" in wording.STANDARD["agreement"] and wording.AGREEMENT_1_3 != wording.STANDARD["agreement"]
    text, flag = _unlock_wording(e.A)
    assert flag == "1"
    _settings(e.A, doc_reviews=json.dumps({"agreement:*": "2026-11-17"}))
    text, _ = _unlock_wording(e.A)
    assert text == ""

def test_an_upgraded_install_keeps_the_agreement_it_approved(e):
    import wording
    con = e.A.connect()
    con.execute("DELETE FROM settings WHERE key='wording_frozen_1_4'")
    con.commit()
    con.close()
    _settings(e.A, wording_frozen_1_0="1", doc_reviews=json.dumps({"agreement:*": "2026-05-01"}))
    text, flag = _unlock_wording(e.A)
    assert text == wording.AGREEMENT_1_3 and "my web host" not in text and flag == "1"
    _settings(e.A, agreement_text="My own words.")
    assert _unlock_wording(e.A)[0] == "My own words."
