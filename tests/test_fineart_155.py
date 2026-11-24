import io
import json
import zipfile
from datetime import date

import pytest

import prints_env as PE
from prints_env import E, GID
from helpers import hosted

OID = "f" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch, proofs=True)

def orders(e):
    return PE.q(e, "SELECT * FROM print_orders ORDER BY id")

def test_the_full_size_files_stay_home_and_only_proofs_go_up(e):
    G = e.G
    full = G.OUT_ROOT / GID / G.FULL_DIR
    m = PE.manifest(e)
    assert m.get("pr") == 1 and not m.get("dl") and not m.get("zips")
    o_names = {it["o"] for it in m["items"]}
    assert len(o_names) == 3 and {p.name for p in full.iterdir()} == o_names
    host_files = [p for p in (e.host / GID).rglob("*") if p.is_file()]
    host_names = {p.name for p in host_files}
    assert not (o_names & host_names) and G.FULL_DIR not in {p.name for p in (e.host / GID).rglob("*")}
    full_bytes = {(full / n).read_bytes() for n in o_names}
    assert not any(p.read_bytes() in full_bytes for p in host_files)

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from PIL import Image
    it = next(i for i in m["items"] if i["n"] == "Lane-034.jpg")
    assert (it["ow"], it["oh"]) == (3000, 2000) and max(it["w"], it["h"]) <= G.WEB_EDGE
    blob = hosted(e.host / GID, it["f"], e.key).read_bytes()
    web = AESGCM(G.key_bytes(e.key)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + it["f"]).encode())
    with Image.open(io.BytesIO(web)) as im:
        assert max(im.size) <= G.WEB_EDGE
        rgb = im.convert("RGB")
        marked = sum(1 for x_ in range(0, rgb.size[0], 4) for y_ in range(0, rgb.size[1], 4)
                     if not (rgb.getpixel((x_, y_))[1] < 60 and rgb.getpixel((x_, y_))[2] < 60))
    assert marked > 1000

    here, up = G.folder_sizes(GID)
    assert here - up >= sum(len(b) for b in full_bytes)

def test_the_page_offers_prints_from_the_proofs(e):
    off = PE.offer(e)
    assert off["items"] and off["spk"]
    page = (e.G.ASSETS / "viewer.html").read_text()
    assert "if (!(DL || m.pr === 1) || PREVIEW) return;" in page

def test_an_order_from_the_proofs_prints_the_full_size_file_unmarked(e):
    PE.send(e, PE.payload(e, OID))
    PE.collect(e)
    oid = orders(e)[0]["id"]
    e.c.post(f"/prints/order/{oid}/shipping", data={"lab_ship": "8.00"}, environ_base=E)
    e.c.post(f"/prints/order/{oid}/invoice", environ_base=E)
    total = json.loads(orders(e)[0]["invoice_data"])["total"]
    e.c.post(f"/prints/order/{oid}/payment", data={"kind": "payment", "amount": f"{total / 100:.2f}",
                                                  "received": date.today().isoformat()}, environ_base=E)
    con = e.A.connect(); con.execute("UPDATE print_order_lines SET crop_confirmed=1"); con.commit(); con.close()
    r = e.c.get(f"/prints/order/{oid}/prepare.zip", environ_base=E)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.get_data()))
    inv = orders(e)[0]["invoice_number"]
    from PIL import Image
    with Image.open(io.BytesIO(z.read(f"{inv}-1-8x10-Lustre-x2.jpg"))) as im:
        assert min(im.size) > 1900
        rgb = im.convert("RGB")
        w_, h_ = rgb.size
        px = [rgb.getpixel((x_, y_)) for x_ in range(5, w_, w_ // 9) for y_ in range(5, h_, h_ // 9)]
    red = sum(1 for p in px if p[0] > 200 and p[1] < 60 and p[2] < 60)
    assert red >= len(px) - 2
    assert orders(e)[0]["state"] == "prepared"

def test_the_switch_is_off_until_turned_on_and_apart_from_the_download_switch(e):
    keep = e.A.app.config["prints_keep_full"]
    with e.A.app.test_request_context():
        s = PE.q(e, "SELECT * FROM sessions WHERE id=1")[0]
        assert keep(s)
    e.c.post("/session/1/prints/switch", data={}, environ_base=E)
    row = PE.q(e, "SELECT off, proofs FROM print_galleries WHERE session_id=1")[0]
    assert row["proofs"] == 0 and row["off"] == 0
    with e.A.app.test_request_context():
        assert not keep(s)
    PE.refresh(e)
    with e.A.app.test_request_context():
        assert not e.A.app.config["print_hold"](1)
    page = e.c.get("/session/1", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "Offer prints from these proofs" in page and "Offer prints from this gallery" not in page

def test_a_proofing_gallery_built_without_the_full_files_asks_for_a_build(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, proofs=True)
    e.G.build(GID, e.tmp / "src", e.key, picks=dict(post=True), brand=dict(client="The Sample Family"))
    e.G.mark_online(GID)
    assert not e.G.is_print(GID) and not e.G.is_download(GID)
    with e.A.app.test_request_context():
        s = PE.q(e, "SELECT * FROM sessions WHERE id=1")[0]
        got = e.A.app.config["prints_offer"](s)
    assert got["proofs"] and got["switch"] and got["note"] == "Build the gallery again to offer prints from these proofs."

def test_a_plain_proofing_gallery_offers_no_prints_by_default(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch, on=False)
    e.G.build(GID, e.tmp / "src", e.key, picks=dict(post=True), brand=dict(client="The Sample Family"))
    e.G.mark_online(GID)
    e.c.post("/settings/prints/switch", data={"offer": "1"}, environ_base=E)
    con = e.A.connect(); con.execute("UPDATE galleries SET built_at=? WHERE session_id=1", (date.today().isoformat(),))
    con.commit(); con.close()
    PE.refresh(e)
    assert not hosted(e.host / GID, "offer.bin", e.key).exists()
    with e.A.app.test_request_context():
        s = PE.q(e, "SELECT * FROM sessions WHERE id=1")[0]
        assert not e.A.app.config["prints_keep_full"](s)
        got = e.A.app.config["prints_offer"](s)
    assert got["proofs"] and not got["switch"] and not got["note"]
