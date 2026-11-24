import base64
import json
import os
import re
import subprocess
import sys

import pytest

from helpers_tools import need_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
VIEWER = os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")
GID = "g-galPrints14aAAAAAAAAAAA"

def keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, raw

def run(cases, spk):
    node = need_tool("node")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "viewer_prints_harness.js"), VIEWER,
                          base64.urlsafe_b64encode(spk).decode().rstrip("="), json.dumps(cases)],
                         capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)

def opened(priv, d):
    import sealed
    env = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    return env

@pytest.fixture(scope="module")
def res():
    priv, spk = keys()
    r = run({
        "pickup": {},
        "shipped": {"how": "me"},
        "refused_then_again": {"statuses": [507, 204], "again": True},
        "closed": {"statuses": [410]},
        "second": {"store": {"orders:" + GID: json.dumps([{"oid": "a" * 32, "at": "2026-11-20T12:00:00Z", "n": 3, "total": 4500}])},
                   "rel": "A change to that one"},
        "email_only": {"email_only": True},
        "ack_first": {"how": "me", "ack_first": True},
    }, spk)
    return r, priv, spk

def payload_of(priv, spk, post):
    import sealed
    from urllib.parse import parse_qs
    f = parse_qs(post["body"])
    d, w = f["d"][0], f["w"][0]
    env = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    return env, w

def test_sizes_fit_first_and_too_large_hidden(res):
    r, _priv, _spk = res
    sizes = r["pickup"]["sizes"]
    assert sizes[0].startswith("4x6") and "fits without cropping" in sizes[0]
    assert sizes[1].startswith("8x10") and "cropped to fit" in sizes[1]
    assert not any(s.startswith("40x60") for s in sizes)

def test_the_order_is_sealed_padded_and_passes_the_app_rules(res, tmp_path):
    import hashlib
    import printorders as P
    import sealed
    r, priv, spk = res
    out = r["pickup"]
    assert len(out["posts"]) == 1 and len(out["posts"][0]["proof"]) == 32
    env, w = payload_of(priv, spk, out["posts"][0])
    assert len(env) in P.PAD_SIZES
    inner = hashlib.sha256(env).digest()
    assert int.from_bytes(hashlib.sha256(inner + bytes.fromhex(w)).digest()[:4], "big") >> (32 - 17) == 0
    key = base64.urlsafe_b64decode(json.loads("{}") or b"")
    del key

    assert env[:7] == b"LAS1\x03\x04" + bytes([len(GID)]) and env[7:7 + len(GID)] == GID.encode()
    assert env[7 + len(GID):15 + len(GID)] == sealed.kid_of(spk)
    assert b"Avery" not in env and b"avery@" not in env
    said = out["said"]
    assert "8x10 print, Lustre" in said[0] and "Drag the frame" in said[0]
    assert "Your order is put together on this device." in said[1] and "plus shipping and tax, on your invoice" in said[1]
    assert "Please add your name." in said[2] and out["err_in_view"]
    assert "Shipping at cost" not in said[2]
    assert said[3].count("Pick up from Sample Studio") == 1 and "hipping" not in said[3]
    assert "Plus tax, on your invoice." in said[3]
    assert "Is this right?" in said[3] and "Pick up from Sample Studio" in said[3] and "Invoice to: Avery Sample, avery@example.com" in said[3]
    assert "Sent to Sample Studio. You will get an invoice by email, usually within 3 days. Nothing is printed until it is paid." in said[-1]
    assert "To change anything, write to studio@example.com." in said[-1]
    store = out["store"]
    assert json.loads(store["cart:" + GID]) == {"v": 1, "lines": []}
    marks = json.loads(store["orders:" + GID])
    assert len(marks) == 1 and marks[0]["n"] == 2 and marks[0]["total"] == 6000 and set(marks[0]) == {"oid", "at", "n", "total"}
    assert out["session"] == {}
    cart = out["cart_after_add"]["lines"][0]
    assert set(cart) == {"p", "item", "qty", "crop", "turn"} and cart["qty"] == 2 and cart["item"] == "pl-3"

def test_the_app_opens_what_the_page_sealed(tmp_path):

    need_tool("node")
    import printorders as P
    import sealed
    priv, spk = keys()
    harness = open(os.path.join(ROOT, "tests", "viewer_prints_harness.js")).read()
    fixed = harness.replace('const key = globalThis.crypto.getRandomValues(new Uint8Array(32));',
                            'const key = new Uint8Array(32).fill(7);').replace(
        '"./ls_open.js"', json.dumps(os.path.join(ROOT, "tests", "ls_open.js")))
    p = tmp_path / "h.js"
    p.write_text(fixed)
    out = subprocess.run(["node", str(p), VIEWER, base64.urlsafe_b64encode(spk).decode().rstrip("="), json.dumps({"x": {"how": "me"}})],
                         capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-2000:]
    post = json.loads(out.stdout)["x"]["posts"][0]
    from urllib.parse import parse_qs
    d = parse_qs(post["body"])["d"][0]
    env = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    data = json.loads(sealed.open_studio("print-order", {sealed.kid_of(spk): priv}, env, gid=GID, link_key=bytes([7]) * 32))
    clean = P.check_payload(data)
    assert clean["ship"] == dict(how="me", name="", addr1="12 Elm St", addr2="", city="Springfield", state="IL", zip="62704",
                                 country="US", phone="")
    ln = clean["lines"][0]
    assert ln["item"] == "pl-3" and ln["qty"] == 2 and ln["each"] == 3000 and ln["p"] == "0" * 23 + "3.bin"
    assert P.shape_ok(ln["crop"], 6000, 4000, 8, 10, ln["turn"]) and ln["ppi"] == P.effective_ppi(ln["crop"], 6000, 4000, 8, 10, ln["turn"])
    assert clean["plv"] == 4 and clean["sub"] == 6000 and clean["rel"] == {"kind": "new", "prev": None}

def test_refused_sends_keep_the_order_and_try_again_sends_the_same_bytes(res):
    r, _p, _s = res
    a = r["refused_then_again"]
    assert "We could not take your order right now. Please email studio@example.com." in a["said"][-2]
    assert "Sent to Sample Studio." in a["said"][-1]
    assert len(a["posts"]) == 2 and a["posts"][0]["body"] == a["posts"][1]["body"]
    mid = a["session_mid"]
    assert len(mid) == 1 and "Avery" not in json.dumps(mid) and "avery@" not in json.dumps(mid)
    assert a["session"] == {}
    c = r["closed"]
    assert "Please email studio@example.com." in c["said"][-1] and c["said"][-1].count("was not sent") == 1
    assert c["session"] == {}
    assert json.loads(c["store"]["cart:" + GID])["lines"]
    assert "orders:" + GID not in c["store"]

def test_a_second_order_asks_new_or_change(res):
    r, priv, spk = res
    s = r["second"]
    assert "You sent an order on Nov 20. Is this a new order, or a change to that one?" in s["said"][-2]
    assert len(json.loads(s["store"]["orders:" + GID])) == 2

def test_a_host_without_php_says_email(res):
    r, _p, _s = res
    assert r["email_only"]["prline"] == "For prints, email studio@example.com"
    assert r["email_only"]["prbar_hidden"] is True

def test_the_page_rules_agree_with_the_app():
    node = need_tool("node")
    import printorders as P
    html = open(VIEWER).read()
    block = html[html.index("// prints.js begin"):html.index("// prints.js end")]
    cases = [(6000, 4000, 8, 10, 0), (6000, 4000, 8, 10, 1), (4000, 6000, 5, 7, 0), (3000, 3000, 11, 14, 1), (5184, 3456, 4, 6, 0)]
    js = block + "\nconsole.log(JSON.stringify({c: " + json.dumps(cases) + ".map(([a,b,w,h,t]) => { const c = PRINTS.fullCrop(a,b,w,h,t); " \
        "return [c, PRINTS.ppi(c,a,b,w,h,t), PRINTS.fits(a,b,w,h)]; }), lab: [PRINTS.labelOk('Zo\\u00eb M\\u00fcller'), PRINTS.labelOk('\\u674e')], " \
        "pad: [100, 5000, 70000].map(PRINTS.padTo)}));"
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60, check=True).stdout)
    for (ow, oh, w, h, t), (c, ppi, fits) in zip(cases, out["c"]):
        pc = P.full_crop(ow, oh, w, h, t)
        assert [round(v, 4) for v in c] == [round(v, 4) for v in pc]
        assert ppi == P.effective_ppi(pc, ow, oh, w, h, t) and fits == P.fits_without_crop(ow, oh, w, h)
    assert out["lab"] == [True, False] and out["pad"] == [4096, 8192, 0]

def test_the_review_says_shipping_once(res):
    r, _p, _s = res
    review = r["shipped"]["said"][3]
    assert review.count("Shipping at cost, on your invoice") == 1 and "Shipping: " not in review
    assert "Plus tax, on your invoice." in review and "Plus shipping and tax" not in review

def test_the_cart_and_order_markers_are_sealed_on_the_device(res):

    r, _p, _s = res
    for case in ("pickup", "closed", "second"):
        out = r[case]
        stored = dict(out["stored"], **out["session_stored"])
        assert stored or case == "pickup"
        for k, v in stored.items():

            assert re.fullmatch(r"la:[0-9a-f]{32}", k), k
            assert re.fullmatch(r"[A-Za-z0-9+/]+=*", v) and len(base64.b64decode(v)) >= 12 + 16, (case, v[:40])
            for word in (GID, "cart:", "orders:", "Lane-034", "pl-3", '"qty"'):
                assert word not in k and word not in v, (case, word)
    assert r["closed"]["stored"]
    mid = r["refused_then_again"]["session_mid_stored"]
    assert len(mid) == 1 and all(k.startswith("la:") for k in mid)

def test_the_invoice_checkbox_refusal_sits_at_the_checkbox(res):

    r, _, _ = res
    a = r["ack_first"]["ack"]
    assert a["heading"] == "Ship to"
    assert a["label"].strip() == "I\u2019ll get an invoice. Nothing is printed until it\u2019s paid."
    assert a["err"] == ["Tick \u2018I\u2019ll get an invoice\u2019 to continue."] and a["err_after_box"]
    assert a["focused"] and a["want"] and a["invalid"]
    assert "Sent to Sample Studio." in r["ack_first"]["said"][-1]
