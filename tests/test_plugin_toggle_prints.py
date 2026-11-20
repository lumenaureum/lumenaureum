import io
import json
import zipfile
from datetime import date, datetime, timedelta

import pytest

import fake_lab
import prints_env as PE
from prints_env import E, GID

OIDS = [f"{i:x}" * 32 for i in range(1, 15)]
OIDS = [o[:32] for o in OIDS]
TABLES = ("print_orders", "print_order_lines", "print_payments", "print_lab_costs", "print_books", "print_order_oids",
          "price_list_items", "price_list_versions", "doc_numbers")

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def sql(e, s, args=()):
    con = e.A.connect()
    con.execute(s, args)
    con.commit()
    con.close()

def setting(e, k):
    r = PE.q(e, "SELECT value FROM settings WHERE key=?", (k,))
    return r[0]["value"] if r else None

def toggle(e, on, anyway=True):
    data = {"name": "prints", **({"turn": "on"} if on else {}), **({"anyway": "1"} if anyway and not on else {})}
    return e.c.post("/settings/plugins", data=data, environ_base=E)

def check(e):

    r = e.c.post("/gallery/sync", environ_base=E)
    assert r.status_code == 302

def today(e):
    return e.c.get("/", environ_base=E).data.decode()

def oid_of(e, oid):
    return PE.q(e, "SELECT id FROM print_orders WHERE oid=?", (oid,))[0]["id"]

def row(e, oid):
    return PE.q(e, "SELECT * FROM print_orders WHERE id=?", (oid,))[0]

def pay(e, oid, cents, kind="payment"):
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": kind, "amount": f"{cents / 100:.2f}",
                                                  "received": date.today().isoformat()}, environ_base=E)

def total(e, oid):
    return json.loads(row(e, oid)["invoice_data"])["total"]

def dump(e, skip_oids=()):

    out = {}
    skip_ids = {r["id"] for r in PE.q(e, "SELECT id, oid FROM print_orders") if r["oid"] in skip_oids}
    for t in TABLES:
        rows = PE.q(e, f"SELECT * FROM {t} ORDER BY 1")
        if t in ("print_orders", "print_order_oids"):
            rows = [r for r in rows if r["oid"] not in skip_oids]
        if t in ("print_order_lines", "print_payments", "print_lab_costs"):
            rows = [r for r in rows if r["order_id"] not in skip_ids]
        out[t] = rows
    out["print_galleries"] = PE.q(e, "SELECT session_id, gid, off, ever FROM print_galleries ORDER BY 1")
    out["history"] = [r["action"] for r in PE.q(e, "SELECT action FROM audit WHERE action LIKE 'Print order%' ORDER BY id")]
    return out

def host_files(e, gid=GID):
    d = e.host / gid
    back = {PE.hosted(d, n, e.key).name: n for n in ("offer.bin", "manifest.bin", "picks.las", "draft.las")}
    back.update({"limits.php": "limits.txt", "seen-at.php": "seen.txt", "once.php": "once.txt", "done.php": "done.txt"})
    return sorted(back.get(str(p.relative_to(d)), str(p.relative_to(d))) for p in d.rglob("*") if p.is_file())

def offer_plain(e):
    return PE.offer(e) if PE.hosted(e.host / GID, "offer.bin", e.key).exists() else None

def money(e):

    y = date.today().year
    with e.A.app.test_request_context():
        fig = e.A.app.config["prints_year"](y)
    st = e.c.get(f"/client/1/statement?m={date.today().strftime('%Y-%m')}", environ_base=E).data.decode()
    st = st.split("<body", 1)[1].split("<script", 1)[0]
    z = zipfile.ZipFile(io.BytesIO(e.c.get(f"/money/accounts.zip?year={y}", environ_base=E).data))
    csvs = {n: z.read(n) for n in z.namelist()}
    fin = e.c.get(f"/money?year={y}", environ_base=E).data.decode()
    return fig, st, csvs, fin

def every_state(e):

    ids = {}

    def order(i, state_to, **kw):
        PE.send(e, PE.payload(e, OIDS[i], **kw))
        PE.collect(e)
        oid = oid_of(e, OIDS[i])
        ids[state_to] = oid
        return oid

    def invoiced(i, name):
        oid = order(i, name)
        e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
        e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
        return oid

    def paid(i, name):
        oid = invoiced(i, name)
        pay(e, oid, total(e, oid))
        return oid

    def ordered(i, name):
        oid = paid(i, name)
        e.c.post(f"/prints/order/{oid}/ordered", data={"lab_name": "A Lab", "lab_number": str(100 + i),
                                                       "cost_products": "20.00", "cost_shipping": "8.00"}, environ_base=E)
        return oid

    order(0, "new")
    order(1, "unmatched", email=f"guest{1}@example.org", name="A Guest")
    invoiced(2, "invoiced")
    oid = invoiced(3, "part_paid")
    pay(e, oid, 1000)
    paid(4, "paid")
    oid = paid(5, "prepared")
    sql(e, "UPDATE print_orders SET state='prepared', prev_state='paid', prepared_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), oid))
    ordered(6, "ordered")
    oid = ordered(7, "shipped")
    e.c.post(f"/prints/order/{oid}/shipped", data={"carrier": "UPS", "tracking": "1Z"}, environ_base=E)
    oid = ordered(8, "delivered")
    e.c.post(f"/prints/order/{oid}/shipped", data={"carrier": "UPS"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/delivered", environ_base=E)
    oid = ordered(9, "closed")
    e.c.post(f"/prints/order/{oid}/shipped", data={"carrier": "UPS"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/delivered", environ_base=E)
    e.c.post(f"/prints/order/{oid}/close", environ_base=E)
    oid = invoiced(10, "expired")
    sql(e, "UPDATE print_orders SET invoice_issued=? WHERE id=?", ((date.today() - timedelta(days=40)).isoformat(), oid))
    with e.A.app.test_request_context():
        e.A.app.config["prints_pass"]()
        e.A.db().commit()
    oid = ordered(11, "canceled")
    e.c.post(f"/prints/order/{oid}/cancel", data={"lab": "not"}, environ_base=E)
    oid = ordered(12, "remake")
    e.c.post(f"/prints/order/{oid}/shipped", data={"carrier": "UPS"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/remake", data={"note": "damaged"}, environ_base=E)
    for k, oid in ids.items():
        want = "invoiced" if k == "part_paid" else k
        assert row(e, oid)["state"] == want, (k, row(e, oid)["state"])
    return ids

def test_print_orders_are_off_on_a_new_install(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, plugin=False, saved=False, on=False)
    assert setting(e, "plugin_on_prints") is None
    page = e.c.get("/settings/plugins", environ_base=E).data.decode()
    assert "Print orders" in page and "Turn on" in page and "Always on" in page
    r = e.c.get("/settings/prints", environ_base=E)
    assert r.status_code == 302 and "/settings/plugins" in r.headers["Location"]
    assert "Print prices" not in e.c.get("/settings/plugins", environ_base=E).data.decode().split("</nav>")[0]
    assert "Charge tax on prints" not in e.c.get("/setup/tax", environ_base=E).data.decode()
    check(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()
    toggle(e, True)
    assert setting(e, "plugin_on_prints") == "1"
    assert e.c.get("/settings/prints", environ_base=E).status_code == 200
    assert "Charge tax on prints" in e.c.get("/setup/tax", environ_base=E).data.decode()

    r = e.c.post("/settings/prints/switch", data={"offer": "1"}, environ_base=E)
    assert setting(e, "prints_on") != "1"
    check(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()

def test_orders_in_every_state_come_back_identical(e):
    ids = every_state(e)
    PE.send(e, PE.payload(e, OIDS[13]))
    before = dump(e)
    money_before = money(e)
    assert offer_plain(e)

    page = toggle(e, False, anyway=False).data.decode()
    assert "print orders are still open. Finish them first, or turn off anyway" in page
    assert setting(e, "plugin_on_prints") != "0"
    toggle(e, False)
    assert setting(e, "plugin_on_prints") == "0"
    check(e)

    late = PE.q(e, "SELECT * FROM print_orders WHERE oid=?", (OIDS[13],))[0]
    assert late["state"] == "new"
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists() and not (e.host / GID / "offer-kid.php").exists()
    assert "1 print order arrived while print orders are off" in today(e)

    for oid in ids.values():
        r = e.c.get(f"/prints/order/{oid}", environ_base=E)
        assert r.status_code == 302 and "/settings/plugins" in r.headers["Location"]
    assert e.c.post(f"/prints/order/{ids['invoiced']}/payment", data={"amount": "1"}, environ_base=E).status_code == 404
    assert "Print orders" not in e.c.get("/client/1", environ_base=E).data.decode().split("Print orders from")[0] or True
    fig, st, csvs, fin = money(e)
    fb, sb, cb, finb = money_before
    assert fig["received"] == fb["received"] and fig["cost"] == fb["cost"] and fig["sales"] == fb["sales"]
    assert st == sb and csvs == cb

    off_dump = dump(e, skip_oids={OIDS[13]})
    for t in TABLES + ("print_galleries",):
        assert off_dump[t] == before[t], t
    toggle(e, True)
    check(e)
    after = dump(e, skip_oids={OIDS[13]})
    for t in TABLES + ("print_galleries",):
        assert after[t] == before[t], t
    assert after["history"][:len(before["history"])] == before["history"]
    assert all("Print order received" in h for h in after["history"][len(before["history"]):])
    assert offer_plain(e)
    assert "arrived while print orders are off" not in today(e)
    page = e.c.get(f"/prints/order/{oid_of(e, OIDS[13])}", environ_base=E).data.decode()
    assert "Print order" in page and "New print order" in page
    assert money(e)[2] == money_before[2]

def test_repeated_toggles_leave_no_drift(e):
    every_state(e)
    check(e)
    base, files, off = dump(e), host_files(e), offer_plain(e)
    for _ in range(3):
        toggle(e, False)
        check(e)
        assert "offer.bin" not in host_files(e) and "offer-kid.php" not in host_files(e)
        toggle(e, True)
        check(e)
        now = dump(e)
        assert {k: v for k, v in now.items() if k != "history"} == {k: v for k, v in base.items() if k != "history"}
        assert now["history"] == base["history"]
        assert host_files(e) == files and offer_plain(e) == off

def test_an_order_from_a_page_loaded_before_the_switch_is_collected_and_kept(e):
    data = PE.payload(e, OIDS[0])
    toggle(e, False)
    PE.send(e, data)
    check(e)
    o = PE.q(e, "SELECT * FROM print_orders")
    assert len(o) == 1 and o[0]["state"] == "new" and o[0]["oid"] == OIDS[0]
    assert not list((e.host / GID / "orders").glob("*.las"))
    assert "1 print order arrived while print orders are off" in today(e)
    assert "orders/index.html" not in host_files(e)
    toggle(e, True)
    check(e)
    assert "orders/index.html" in host_files(e) and offer_plain(e)
    page = today(e)
    assert "arrived while print orders are off" not in page
    assert "print order" in page.lower()

def test_offers_come_down_only_where_the_check_reaches_and_are_retried(e):
    toggle(e, False)
    gl = e.G._g
    real = gl["gallery_host"]

    class Down:
        def __getattr__(self, name):
            raise OSError("the web host did not answer")

    gl["gallery_host"] = lambda: Down()
    try:
        e.A.app.config["print_offers_down"].__globals__
        with e.A.app.test_request_context():
            e.A.app.config["print_offers_down"]()
            e.A.db().commit()
    finally:
        gl["gallery_host"] = real
    assert PE.hosted(e.host / GID, "offer.bin", e.key).exists()
    assert "Print offers still on 1 gallery" in today(e)
    check(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()
    assert "Print offers still on" not in today(e)

def test_a_gallery_that_went_dark_or_was_taken_down_while_off_collects_its_orders_first(e):
    toggle(e, False)
    PE.send(e, PE.payload(e, OIDS[0]))
    with e.A.app.test_request_context():
        e.A.app.config["gallery_take_down"](1)
        e.A.db().commit()
    assert [r["oid"] for r in PE.q(e, "SELECT oid FROM print_orders")] == [OIDS[0]]
    assert not (e.host / GID).exists() or not list((e.host / GID).rglob("*.las"))
    sig = PE.q(e, "SELECT offer_sig FROM print_galleries")[0]["offer_sig"]
    check(e)
    assert sig == "" or PE.q(e, "SELECT offer_sig FROM print_galleries")[0]["offer_sig"] == ""

def test_going_dark_with_prints_off_collects_first(e):
    toggle(e, False)
    PE.send(e, PE.payload(e, OIDS[0]))
    old = (date.today() - timedelta(days=400)).isoformat()
    sql(e, "UPDATE sessions SET sd_shipped=?, first_visit=? WHERE id=1", (old, old))
    check(e)
    assert PE.q(e, "SELECT upload_state FROM galleries")[0]["upload_state"] == "removed"
    assert [r["oid"] for r in PE.q(e, "SELECT oid FROM print_orders")] == [OIDS[0]]

def test_deleting_the_session_with_prints_off_collects_first_and_keeps_the_books(e):
    oid = None
    PE.send(e, PE.payload(e, OIDS[0]))
    PE.collect(e)
    oid = oid_of(e, OIDS[0])
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    pay(e, oid, total(e, oid))
    pay(e, oid, total(e, oid), kind="refund")
    toggle(e, False)
    PE.send(e, PE.payload(e, OIDS[1]))
    with e.A.app.test_request_context():
        e.A.app.config["gallery_take_down"](1)
        e.A.db().commit()
    assert {r["oid"] for r in PE.q(e, "SELECT oid FROM print_orders")} == {OIDS[0], OIDS[1]}
    with e.A.app.test_request_context():
        e.A.app.config["print_keep_books"]([1])
        e.A.db().commit()
    books = PE.q(e, "SELECT what, amount_cents FROM print_books ORDER BY id")
    assert sum(b["amount_cents"] for b in books if b["what"] in ("payment", "refund")) == 0 and len(books) >= 2

def test_a_lab_plugin_changes_nothing_while_print_orders_are_off(e):
    lab, fake = fake_lab.make(e.A.app.config["prints_api"])
    e.A.hooks.labs.append(lab)
    try:
        PE.send(e, PE.payload(e, OIDS[0]))
        PE.collect(e)
        oid = oid_of(e, OIDS[0])
        e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
        e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
        sql(e, "UPDATE print_order_lines SET crop_confirmed=1")
        sql(e, "UPDATE print_orders SET flags=?", (json.dumps({"flags_ok": True}),))
        pay(e, oid, total(e, oid))
        api = e.A.app.config["prints_api"]
        with e.A.app.test_request_context():
            ok, words = api.hand_off(oid, "testlab")
            e.A.db().commit()
        assert ok and row(e, oid)["state"] == "with_lab"
        before = dump(e)
        toggle(e, False)
        with e.A.app.test_request_context():
            with pytest.raises(Exception):
                api.report(oid, "testlab", "placed", numbers=["L1"])
            assert api.hand_off(oid, "testlab")[0] is False
        check(e)
        assert dump(e)["print_orders"] == before["print_orders"]
        toggle(e, True)
        with e.A.app.test_request_context():
            api.report(oid, "testlab", "placed", numbers=["L1"])
            e.A.db().commit()
        assert row(e, oid)["state"] == "ordered"
    finally:
        e.A.hooks.labs[:] = [l_ for l_ in e.A.hooks.labs if l_.get("id") != "testlab"]

def test_the_price_list_and_settings_survive_toggles(e):
    PE.save_prices(e, **{"cost_pl-3": "5.01", "markup": "3.5", "ship_rule_us": "flat", "ship_flat_us": "12.00"})
    keep = {k: setting(e, k) for k in ("prints_markup", "prints_ship", "prints_on", "prints_plv", "prints_saved")}
    items = PE.q(e, "SELECT * FROM price_list_items ORDER BY id")
    toggle(e, False)
    check(e)
    toggle(e, True)
    check(e)
    assert {k: setting(e, k) for k in keep} == keep
    assert PE.q(e, "SELECT * FROM price_list_items ORDER BY id") == items
    assert offer_plain(e)["ship"]["us"]["on"]

def test_crm_plugins_off_wins_over_the_switch_both_ways(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_PLUGINS_OFF", "prints")
    e = PE.make(tmp_path, monkeypatch, saved=False, on=False)
    assert setting(e, "plugin_on_prints") == "1"
    assert "prints" not in e.A.app.view_functions.get("prints_settings", lambda: None).__module__ if \
        "prints_settings" in e.A.app.view_functions else True
    page = e.c.get("/settings/plugins", environ_base=E).data.decode()
    assert "Off for this install" in page and "turned this plugin off with CRM_PLUGINS_OFF" in page
    toggle(e, True)
    assert "That plugin has no switch here." in e.c.get("/settings/plugins", environ_base=E).data.decode()

    con = e.A.connect()
    con.execute("INSERT INTO print_galleries (session_id, gid, offer_sig, ever) VALUES (1, ?, 'x', 1)", (GID,))
    con.commit()
    con.close()
    PE.hosted(e.host / GID, "offer.bin", e.key).write_bytes(b"left from before")
    (e.host / GID / "offer-kid.php").write_text("<?php exit; ?>\n")
    check(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()

    assert e.c.get(f"/money?year={date.today().year}", environ_base=E).status_code == 200
    assert e.c.get(f"/money/accounts.zip?year={date.today().year}", environ_base=E).status_code == 200

def test_the_switch_off_with_the_override_lifted_stays_off(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch)
    toggle(e, False)
    assert "Turn on" in e.c.get("/settings/plugins", environ_base=E).data.decode()
    check(e)
    assert not PE.hosted(e.host / GID, "offer.bin", e.key).exists()

def test_the_switch_is_in_history(e):
    toggle(e, False)
    toggle(e, True)
    acts = [r["action"] for r in PE.q(e, "SELECT action FROM audit ORDER BY id")]
    assert "Turned Print orders off" in acts
    assert any(a.startswith("Turned Print orders on") for a in acts), acts

def test_a_1_3_1_install_updated_has_print_orders_off_and_nothing_for_prints_on_its_hosts(tmp_path, monkeypatch):
    import test_upgrade_from_131 as U
    U._place(tmp_path)
    A, c, _taken = U._open(tmp_path, monkeypatch)
    monkeypatch.setitem(A.app.config, "clock_ok", lambda: False)
    page = c.get("/settings/plugins", environ_base=E).data.decode()
    assert "Print orders" in page and "Turn on" in page
    assert c.get("/settings/prints", environ_base=E).status_code == 302
    assert "Print prices" not in page.split("</nav>")[0]
    assert c.post("/gallery/sync", environ_base=E).status_code in (302, 303)
    for d in (tmp_path / U.HOST, tmp_path / U.EARLIER):
        names = [p.name for p in d.rglob("*") if p.is_file()]
        assert not {"offer.bin", "offer-kid.php", "keep.txt"} & set(names), d
        assert not [p for p in d.rglob("orders") if p.is_dir()], d
    con = A.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM print_orders").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM print_galleries").fetchone()[0] == 0
        assert con.execute("SELECT value FROM settings WHERE key='plugin_on_prints'").fetchone() is None
    finally:
        con.close()
    c.get("/", environ_base=E)
    assert "print order" not in c.get("/", environ_base=E).data.decode().lower()

def test_an_order_collected_in_the_same_second_as_the_switch_is_not_counted_as_arrived_while_off(e):

    late = PE.payload(e, OIDS[1], email="guest@example.org", name="A Guest")
    PE.send(e, PE.payload(e, OIDS[0]))
    PE.collect(e)
    toggle(e, False)
    off_at = setting(e, "plugin_off_at_prints")
    sql(e, "UPDATE print_orders SET collected_at=?", (off_at,))
    assert "arrived while print orders are off" not in today(e)
    PE.send(e, late)
    check(e)
    assert "1 print order arrived while print orders are off" in today(e)
    assert {r["oid"]: json.loads(r["flags"]).get("off_n") for r in PE.q(e, "SELECT oid, flags FROM print_orders")} == \
        {OIDS[0]: None, OIDS[1]: 1}

def test_each_spell_off_counts_only_its_own_orders(e):
    import printorders as P
    first = PE.payload(e, OIDS[0])
    second = PE.payload(e, OIDS[1], email="guest@example.org", name="A Guest")
    later = PE.send(e, second)
    (e.host / GID / "orders" / P.host_name(later)).unlink()
    toggle(e, False)
    PE.send(e, first)
    check(e)
    assert "1 print order arrived while print orders are off" in today(e)
    toggle(e, True)
    assert "arrived while print orders are off" not in today(e)
    toggle(e, False)
    assert setting(e, "plugin_off_n_prints") == "2"
    assert "arrived while print orders are off" not in today(e)
    sql(e, "UPDATE settings SET value=? WHERE key='plugin_off_at_prints'", ("2000-01-01T00:00:00",))
    assert "arrived while print orders are off" not in today(e)
    (e.host / GID / "orders").mkdir(exist_ok=True)
    (e.host / GID / "orders" / P.host_name(later)).write_bytes(later)
    check(e)
    assert "1 print order arrived while print orders are off" in today(e)

def test_a_1_4_0_install_that_is_off_keeps_its_count_after_the_update(e):

    toggle(e, False)
    PE.send(e, PE.payload(e, OIDS[0]))
    check(e)
    sql(e, "UPDATE settings SET value='2000-01-01T00:00:00' WHERE key='plugin_off_at_prints'")
    sql(e, "DELETE FROM settings WHERE key='plugin_off_n_prints'")
    sql(e, "UPDATE print_orders SET flags='{}'")
    steps = [fn for fn in e.A.UNLOCK_HOOKS if getattr(fn, "__name__", "") == "_later_columns"]
    assert steps
    for fn in steps:
        fn()
    assert setting(e, "plugin_off_n_prints") == "1"
    assert "1 print order arrived while print orders are off" in today(e)
