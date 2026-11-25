import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

import prints_env as PE
from prints_env import E

OID1 = "a" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def sql(e, s, args=()):
    con = e.A.connect()
    con.execute(s, args)
    con.commit()
    con.close()

def history(e):
    return [r["action"] for r in PE.q(e, "SELECT action FROM audit ORDER BY id")]

def one_print(e, **kw):
    p = PE.payload(e, OID1, **kw)
    p["lines"] = [dict(p["lines"][1], qty=1)]
    p["sub"] = p["lines"][0]["each"]
    return p

def test_history_says_1_print(e):
    PE.send(e, one_print(e))
    PE.collect(e)
    assert any(a.startswith("Print order received from Avery Sample: 1 print") and "1 prints" not in a for a in history(e))

def test_history_says_which_way_a_switch_went(e):
    e.c.post("/settings/prints/switch", data={}, environ_base=E)
    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    e.c.post("/session/1/prints/switch", data={"offer": "1"}, environ_base=E)
    h = history(e)
    assert "Turned print ordering off" in h and "Stopped offering prints from this gallery" in h
    assert "Offered prints from this gallery" in h
    assert not any("on or off" in a or "or stopped" in a for a in h)

def test_today_uses_the_first_name(e):
    PE.send(e, PE.payload(e, OID1))
    PE.collect(e)
    sql(e, "UPDATE print_orders SET state='shipped', shipped_at=?", (datetime.now().isoformat(timespec="seconds"),))
    page = e.c.get("/", environ_base=E).data.decode()
    assert "Avery Sample: shipped. Tell Avery" in page and "Tell Avery Sample" not in page

def test_the_clients_device_time_is_shown_in_the_studios_time_zone(e):
    PE.send(e, PE.payload(e, OID1))
    PE.collect(e)
    o = orders(e)[0]
    page = e.c.get(f"/prints/order/{o['id']}", environ_base=E).data.decode()
    local = datetime.fromisoformat(o["browser_at"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    assert f"the client&#39;s device said {local}" in page or f"the client's device said {local}" in page

def test_pick_up_needs_no_shipping_typed_and_reads_as_pick_up(e):
    PE.send(e, PE.payload(e, OID1, ship=dict(how="pickup")))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "Pick up from the studio" in page and "ship to the studio" not in page
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    o = orders(e)[0]
    assert o["state"] == "invoiced" and json.loads(o["invoice_data"])["ship_cents"] == 0

def test_the_invoice_draft_stands_alone_and_promises_no_attachment(e):
    PE.send(e, PE.payload(e, OID1))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).data.decode()
    assert "The email gives the invoice's number, total, and how to pay." in page
    assert "attach it" not in page
    assert "attached" not in page and "%20attached" not in page

def test_the_host_check_says_why_in_plain_words(monkeypatch):
    import hostcheck

    class Host:
        def put_stream(self, *a, **k):
            pass

        def delete(self, *a):
            pass

    def no_answer(*a, **k):
        raise urllib.error.URLError(OSError("connection refused"))
    monkeypatch.setattr(hostcheck, "HTTP", no_answer)
    p = hostcheck.probe(Host(), "https://www.example.com/g/")
    assert p.kind == "unreachable" and p["detail"] == "its web address didn't answer"
    import socket

    def slow(*a, **k):
        raise urllib.error.URLError(socket.timeout("timed out"))
    monkeypatch.setattr(hostcheck, "HTTP", slow)
    assert hostcheck.probe(Host(), "https://www.example.com/g/")["detail"] == "its web address didn't answer in time"

def test_a_session_kept_for_print_orders_shows_no_delete_date(e):
    old = (date.today() - timedelta(days=400)).isoformat()
    con = e.A.connect()
    con.execute("UPDATE sessions SET sd_shipped=?, stage='delivered' WHERE id=1", (old,))
    con.execute("INSERT INTO retention (session_id, keep_days) VALUES (1, 90)")
    con.commit(); con.close()
    page = e.c.get("/", environ_base=E).data.decode()
    assert "RAWs and outtakes delete" not in page
    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    PE.refresh(e)
    assert "RAWs and outtakes delete" in e.c.get("/", environ_base=E).data.decode()
