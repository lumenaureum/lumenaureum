import json
from datetime import date

import pytest

import prints_env as PE
from prints_env import E, GID
from test_129_155 import env

OID = "a" * 32
OID2 = "b" * 32

def _orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def _set(e, k, v):
    con = e.A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    con.commit()
    con.close()

def _invoice(e, oid):
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    return json.loads(PE.q(e, "SELECT invoice_data FROM print_orders WHERE id=?", (oid,))[0]["invoice_data"])

@pytest.fixture
def dl(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def test_tax_exempt_client_is_not_charged_sales_tax_on_their_print_order(dl):

    e = dl
    _set(e, "taxes", json.dumps([dict(key="sales", name="Sales tax", rate=6, included=False, default=True)]))
    con = e.A.connect()
    con.execute("UPDATE households SET tax_exempt=1, tax_cert='EX-12345' WHERE id=1")
    con.commit(); con.close()
    PE.send(e, PE.payload(e, OID))
    PE.collect(e)
    o = _orders(e)[0]
    assert o["contact_id"], "the order is matched to the exempt client"
    inv = _invoice(e, o["id"])
    assert inv["tax_cents"] == 0, f"an exempt client was charged {inv['tax_cents']} cents of sales tax on prints"

def _proofs(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    e = PE.make(tmp_path, monkeypatch, proofs=True)
    import shutil
    lane = tmp_path / "photos" / "export" / "lane"
    lane.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tmp_path / "src", lane)
    con = e.A.connect()
    con.execute("UPDATE sessions SET stage='proofing' WHERE id=1")
    con.execute("UPDATE galleries SET source_dir=? WHERE session_id=1", (str(lane) + "/",))
    con.commit(); con.close()
    e.lane = lane
    return e

def _paid_order(e, oid=OID):
    PE.send(e, PE.payload(e, oid))
    PE.collect(e)
    o = [r for r in _orders(e) if r["oid"] == oid][0]
    inv = _invoice(e, o["id"])
    e.c.post(f"/prints/order/{o['id']}/payment", data={"kind": "payment", "amount": f"{inv['total'] / 100:.2f}",
                                                      "received": date.today().isoformat()}, environ_base=E)
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    return PE.q(e, "SELECT * FROM print_orders WHERE id=?", (o["id"],))[0]

def _rebuild(e):
    import time
    G = e.G
    G.JOBS.pop(1, None)
    r = e.c.post("/session/1/gallery/build", data={"source_dir": str(e.lane)}, environ_base=E)
    assert r.status_code == 302
    for _ in range(300):
        if G.JOBS.get(1, {}).get("state") in ("done", "error"):
            break
        time.sleep(0.1)
    assert G.JOBS.get(1, {}).get("state") == "done", G.JOBS.get(1)

def test_a_paid_print_order_from_the_proofs_survives_a_rebuild(tmp_path, monkeypatch):

    e = _proofs(tmp_path, monkeypatch)
    o = _paid_order(e)
    assert o["state"] == "paid"
    assert e.c.get(f"/prints/order/{o['id']}/prepare.zip", environ_base=E).status_code == 200
    con = e.A.connect(); con.execute("UPDATE print_orders SET state='paid', prepared_at=NULL WHERE id=?", (o["id"],)); con.commit(); con.close()
    _rebuild(e)
    assert e.G.is_print(GID), "still a proofing gallery that sells prints"
    page = e.c.get(f"/prints/order/{o['id']}/prepare", environ_base=E).get_data(as_text=True)
    assert "Photo changed since the order" not in page, "the paid order's photos were orphaned by a rebuild"

def test_turning_the_proofs_switch_off_keeps_an_open_orders_full_size_files(tmp_path, monkeypatch):

    e = _proofs(tmp_path, monkeypatch)
    o = _paid_order(e)
    r = e.c.post("/session/1/prints/switch", data={}, environ_base=E, follow_redirects=True)
    assert "still open" in r.get_data(as_text=True), "turning the switch off is refused while the order is open"
    _rebuild(e)
    page = e.c.get(f"/prints/order/{o['id']}/prepare", environ_base=E).get_data(as_text=True)
    assert e.G.is_print(GID) or e.G.is_download(GID), "the full-size files the paid order needs are gone"
    assert e.c.get(f"/prints/order/{o['id']}/prepare.zip", environ_base=E).status_code == 200, page[-1500:]

def test_a_proofs_switch_off_by_another_way_still_keeps_an_open_orders_full_size_files(tmp_path, monkeypatch):

    e = _proofs(tmp_path, monkeypatch)
    o = _paid_order(e)
    con = e.A.connect(); con.execute("UPDATE print_galleries SET proofs=0 WHERE session_id=1"); con.commit(); con.close()
    _rebuild(e)
    assert e.G.is_print(GID)
    assert e.c.get(f"/prints/order/{o['id']}/prepare.zip", environ_base=E).status_code == 200

def test_rebuild_all_with_new_studio_details_leaves_open_print_orders_printable(tmp_path, monkeypatch):

    import time
    import jobs
    e = _proofs(tmp_path, monkeypatch)
    o = _paid_order(e)
    con = e.A.connect()
    con.execute("UPDATE galleries SET brand_fp='2.old' WHERE session_id=1")
    con.commit(); con.close()
    _set(e, "business_name", "Sample Studio Renamed")
    page = e.c.get("/galleries", environ_base=E).get_data(as_text=True)
    assert "gal-rebuild" in page
    assert e.c.post("/galleries/rebuild-details", environ_base=E).status_code == 302
    for _ in range(300):
        if not jobs.running("gallery-rebuild-details"):
            break
        time.sleep(0.1)
    assert e.G.JOBS.get(1, {}).get("state") == "done", e.G.JOBS.get(1)
    page = e.c.get(f"/prints/order/{o['id']}/prepare", environ_base=E).get_data(as_text=True)
    assert "Photo changed since the order" not in page, "Rebuild all orphaned a paid print order's photos"

def test_a_second_sessions_card_written_to_the_same_drive_keeps_the_first(env):

    import test_129_155 as T
    A, c, G, tmp, src = env
    one = T.session(A, delivery_method="card")
    two = T.session(A, delivery_method="card")
    mount = T.write_card(c, one, src, tmp / "drive")
    code_one = T.row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (one,))[0]
    first = {p_.name for p_ in (mount / G.CARD_DIR).iterdir()}
    assert T.row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (one,))[0]
    r = c.post(f"/session/{two}/gallery/card", data={"to": "folder", "folder": str(mount), "source_dir": str(src)},
               environ_base=E, follow_redirects=True)
    assert "already holds another session" in r.get_data(as_text=True), "the second write is refused, and says why"
    assert not T.row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (two,))[0], "the refused card is not delivered"
    T.write_card(c, one, src, mount)
    assert T.row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (one,))[0] == code_one
    left = {p_.name for p_ in mount.rglob("*") if p_.is_file()}
    opens_one = False
    for box in [p_ for p_ in mount.rglob("manifest.bin")]:
        try:
            T.opened(G, G.card_key(code_one), box.read_bytes(), "manifest.bin")
            opens_one = True
        except Exception:
            pass
    assert opens_one, ("the first session's card was overwritten by the second session's: the engagement letter's code "
                       f"opens nothing on the drive any more (first card's files still there: {len(first & left)} of {len(first)})")

