import base64
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, hosted, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "g-galPrints14aAAAAAAAAAAA"

def photo(path, size, color):
    from PIL import Image
    im = Image.new("RGB", size, color)
    for x_ in range(0, size[0], 37):
        im.putpixel((x_, x_ % size[1]), (255, 255, 255))
    im.save(path, "JPEG")

class Env:
    pass

def make(tmp_path, monkeypatch, caps="ok", saved=True, on=True, plugin=True, proofs=False):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    G = A.app.config["gallery_module"]
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    src = tmp_path / "src"
    src.mkdir()
    photo(src / "Lane-034.jpg", (3000, 2000), "red")
    photo(src / "Lane-035.jpg", (2000, 3000), "blue")
    photo(src / "Lane-036.jpg", (600, 400), "green")
    key = G.new_key()
    G.build(GID, src, key, picks=dict(post=True), download=not proofs, keep_full=proofs,
            mark=dict(style="tiled", text="PROOF", strength=40, logo=None) if proofs else None,
            brand=dict(client="The Sample Family", studio="Sample Studio"))
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://gallery.test/g/')")
    con.execute("INSERT INTO settings (key, value) VALUES ('business_email', 'studio@example.com')")
    con.execute("INSERT INTO settings (key, value) VALUES ('business_name', 'Sample Studio')")
    if plugin:
        con.execute("INSERT INTO settings (key, value) VALUES ('plugin_on_prints', '1')")
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_host_caps', ?)", (json.dumps({"now": {"kind": caps}}),))
    con.execute("INSERT INTO households (id, name, address) VALUES (1, 'The Sample Family', '12 Elm St\nSpringfield, IL 62704')")
    con.execute("INSERT INTO people (household_id, name, role, email) VALUES (1, 'Avery Sample', 'adult', 'Avery@Example.com')")
    con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_id) VALUES (1, 1, 'family', 'delivered', ?, ?)",
                ((date.today() - timedelta(days=20)).isoformat(), GID))
    con.execute("""INSERT INTO galleries (session_id, key, upload_state, uploaded_at, sealed_gen, host_fp, built_at)
                   VALUES (1, ?, 'up', ?, 1, ?, ?)""", (key, date.today().isoformat(), G.host_fingerprint(),
                                                       date.today().isoformat() if proofs else None))
    con.commit()
    con.close()
    G.upload_gallery(GID, 30, "2099-01-01")
    G.mark_online(GID)
    e = Env()
    e.A, e.c, e.G, e.host, e.key, e.tmp = A, c, G, host, key, tmp_path
    if saved:
        save_prices(e)
    if on:
        assert c.post("/settings/prints/switch", data={"offer": "1"}, environ_base=E).status_code == 302
        if proofs:
            assert c.post("/session/1/prints/switch", data={"offer": "1"}, environ_base=E).status_code == 302
        refresh(e)
    return e

def save_prices(e, **extra):

    A = e.A
    with A.app.app_context():
        rows = A.q("SELECT * FROM price_list_items ORDER BY sort")
    if not rows:
        e.c.get("/settings/prints", environ_base=E)
        with A.app.app_context():
            rows = A.q("SELECT * FROM price_list_items ORDER BY sort")
    data = {"id": [r["id"] for r in rows], "markup": "3", "invoice_days": "3", "large_n": "25", "large_c": "250",
            "arrive": "Prints come straight from our lab."}
    for r in rows:
        i = r["id"]
        data.update({f"label_{i}": r["label"], f"w_{i}": str(r["w"]).rstrip("0").rstrip("."), f"h_{i}": str(r["h"]).rstrip("0").rstrip("."),
                     f"finish_{i}": r["finish"], f"each_{i}": f"{r['each_cents'] / 100:.2f}" if r["each_cents"] is not None else "",
                     f"cost_{i}": f"{r['cost_cents'] / 100:.2f}" if r["cost_cents"] is not None else "",
                     f"hide_{i}": str(r["ppi_hide"]), f"mark_{i}": str(r["ppi_mark"]), f"on_{i}": "1" if r["offered"] else ""})
    for d in ("us", "akhi", "pickup"):
        data.update({f"ship_on_{d}": "1", f"ship_rule_{d}": "cost", f"ship_say_{d}": ""})
    data["ship_rule_ca"] = "cost"
    data.update(extra)
    data = {k: v for k, v in data.items() if v != "" or k.startswith(("each_", "cost_", "label_new", "w_new"))}
    r = e.c.post("/settings/prints", data=data, environ_base=E)
    assert r.status_code == 302, r.data
    return r

def refresh(e):
    with e.A.app.app_context():
        e.A.app.config["prints_refresh_offers"]()

def collect(e):
    with e.A.app.app_context():
        return e.A.app.config["prints_collect"]()

def offer(e):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = hosted(e.host / GID, "offer.bin", e.key).read_bytes()
    return json.loads(AESGCM(e.G.key_bytes(e.key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:offer.bin"))

def manifest(e):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = (e.G.OUT_ROOT / GID / "manifest.bin").read_bytes()
    return json.loads(AESGCM(e.G.key_bytes(e.key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))

def payload(e, oid, email="avery@example.com", name="Avery Sample", ship=None, lines=None, rel=None):
    import printorders as P
    off = offer(e)
    m = manifest(e)
    by_n = {it["n"]: it for it in m["items"]}
    item = {i["label"].split(" ")[0]: i for i in off["items"]}
    if lines is None:
        it = by_n["Lane-034.jpg"]
        crop = P.full_crop(it["ow"], it["oh"], 8, 10)
        lines = [dict(p=it["o"], n="Lane-034.jpg", item=item["8x10"]["id"], qty=2, crop=crop, turn=0, each=item["8x10"]["each"],
                      ppi=P.effective_ppi(crop, it["ow"], it["oh"], 8, 10, 0)),
                 dict(p=by_n["Lane-035.jpg"]["o"], n="Lane-035.jpg", item=item["4x6"]["id"], qty=1, crop=[0, 0, 1, 1], turn=0,
                      each=item["4x6"]["each"], ppi=333)]
    return dict(v=1, oid=oid, at="2026-11-20T15:00:00.000Z", plv=off["plv"], cur="USD",
                who=dict(name=name, email=email, phone=""),
                ship=ship or dict(how="me", name="", addr1="12 Elm St", addr2="", city="Springfield", state="IL", zip="62704",
                                  country="US", phone=""),
                lines=lines, sub=sum(l_["each"] * l_["qty"] for l_ in lines), ack=True,
                rel=rel or dict(kind="new", prev=None), note="")

def send(e, data, gid=GID):

    import printorders as P
    off = offer(e)
    spk = base64.urlsafe_b64decode(off["spk"] + "=" * (-len(off["spk"]) % 4))
    env = P.seal_order(data, spk, gid, e.G.key_bytes(e.key))
    d = e.host / gid / "orders"
    d.mkdir(exist_ok=True)
    (d / P.host_name(env)).write_bytes(env)
    return env

def q(e, sql, args=()):
    con = e.A.connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()
