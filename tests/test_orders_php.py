import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from helpers_tools import need_tool
from helpers import hosted, host_write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

GID = "g-galOrders14xAAAAAAAAAAA"

def studio_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    return k, k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

def payload(oid, n=1):
    return dict(v=1, oid=oid, at="2026-11-20T15:00:00.000Z", plv=1, cur="USD",
                who=dict(name="Avery Sample", email="avery@example.com", phone=""), ship=dict(how="pickup"),
                lines=[dict(p="a" * 24 + ".bin", n="Lane-034.jpg", item="pl-3", qty=n, crop=[0, 0, 1, 1], turn=0,
                            each=3000, ppi=300)], sub=3000 * n, ack=True, rel=dict(kind="new", prev=None), note="")

@pytest.fixture
def host(tmp_path, monkeypatch):
    php = need_tool("php")
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    import sealed
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (40, 30), "red").save(src / "a.jpg", "JPEG")
    key = G.new_key()
    d = G.build(GID, src, key, picks=dict(post=True), download=True).out_dir
    host_write(d, "limits.txt", 'days=30\nunopened_until=2099-01-01\nproof=1\ncontact="studio@example.com"\n')
    priv, spk = studio_key()
    (d / "offer-kid.php").write_text("<?php exit; ?>\n" + sealed.kid_of(spk).hex() + "\n")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([php, "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(d)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    proof = hashlib.sha256(G.key_bytes(key)).hexdigest()[:32]
    yield f"http://127.0.0.1:{port}/", d, proof, G, key, spk
    p.terminate(); p.wait()

def order(G, key, spk, oid, n=1, gid=GID):
    import printorders as P
    return P.seal_order(payload(oid, n), spk, gid, G.key_bytes(key))

def body(env, w=None, extra=""):
    import printorders as P
    w = w or P.solve_work(env)
    return ("w=" + w + "&d=" + base64.urlsafe_b64encode(env).decode().rstrip("=") + extra).encode()

def call(url, data=None, proof=None, ctype="application/x-www-form-urlencoded", method=None):
    h = {"X-Proof": proof} if proof else {}
    if data is not None and ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)

def kept(d):
    return sorted(p.name for p in (d / "orders").glob("*.las")) if (d / "orders").is_dir() else []

def test_every_step_in_order(host):
    base, d, proof, G, key, spk = host
    u = base + "orders.php"
    env = order(G, key, spk, "1" * 32)
    good = body(env)
    st, _b, h = call(u, method="GET")
    assert st == 405 and h.get("X-Host-Scripts") == "1.6.0"
    host_write(d, "limits.txt", "days=30\nunopened_until=2001-01-01\nproof=1\n")
    assert call(u, good, proof)[0] == 410
    host_write(d, "limits.txt", 'days=30\nunopened_until=2099-01-01\nproof=1\ncontact="studio@example.com"\n')
    host_write(d, "done.txt", str(int(time.time()) - 5))
    assert call(u, good, proof)[0] == 410
    hosted(d, "done.txt").unlink()
    assert call(u, good)[0] == 403
    assert call(u, good, "0" * 32)[0] == 403
    host_write(d, "limits.txt", 'days=30\nunopened_until=2099-01-01\nproof=1\nmoved=1\ncontact="studio@example.com"\n')
    st, b, _h = call(u, good, proof)
    assert st == 409 and json.loads(b) == {"moved": 1, "contact": "studio@example.com"}
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\n")
    assert call(u, good, proof, ctype="text/plain")[0] == 400
    assert call(u, body(env, extra="&x=1"), proof)[0] == 400
    assert call(u + "?x=1", good, proof)[0] == 400
    assert call(u, b"d=AAAA&w=0000000000000000", proof)[0] == 400
    other = __import__("sealed").seal_studio("print-order", spk, b'{"v":1}', gid="g-galOtherOneAAAAAAAAAAAA", link_key=G.key_bytes(key))
    assert call(u, body(other), proof)[0] == 400
    _priv, spk2 = studio_key()
    assert call(u, body(order(G, key, spk2, "2" * 32)), proof)[0] == 400
    (d / "offer-kid.php").rename(d / "offer-kid.off")
    assert call(u, good, proof)[0] == 410
    (d / "offer-kid.off").rename(d / "offer-kid.php")
    bad_w = next(w for w in (f"{i:016x}" for i in range(1000)) if not __import__("printorders").solve_work(env) == w)
    st = call(u, body(env, w=bad_w), proof)[0]
    assert st == 403
    assert kept(d) == []
    assert call(u, good, proof)[0] == 204
    assert call(u, good, proof)[0] == 204
    assert kept(d) == [hashlib.sha256(env).hexdigest()[:32] + ".las"]
    assert (d / "orders" / kept(d)[0]).read_bytes() == env
    raw = (d / "orders" / "tries.php").read_text()
    assert raw.startswith("<?php exit; ?>\n") and "127.0.0.1" not in raw
    assert call(base + "orders/tries.php")[1] == b""
    for f in ("orders/" + kept(d)[0], kept(d)[0]):
        st, b, _h = call(base + "gate.php?f=" + f, proof=proof)
        assert st == 404 and env not in b

def test_limits_per_sender_and_per_folder(host):
    base, d, proof, G, key, spk = host
    u = base + "orders.php"
    for i in range(5):
        assert call(u, body(order(G, key, spk, f"{i + 1:032x}")), proof)[0] == 204
    assert call(u, body(order(G, key, spk, "a" * 32)), proof)[0] == 429
    assert len(kept(d)) == 5

    t = d / "orders" / "tries.php"
    data = json.loads(t.read_text().split("\n", 1)[1])
    who = next(iter(data))
    fake = [f"{i:032x}.las" for i in range(100, 110)]
    for n in fake:
        (d / "orders" / n).write_bytes(b"x")
    data[who] = {"t": [], "w": fake}
    t.write_text("<?php exit; ?>\n" + json.dumps(data) + "\n")
    assert call(u, body(order(G, key, spk, "b" * 32)), proof)[0] == 429
    data[who] = {"t": [], "w": []}
    t.write_text("<?php exit; ?>\n" + json.dumps(data) + "\n")
    for i in range(200, 290):
        (d / "orders" / f"{i:032x}.las").write_bytes(b"x")
    assert len(kept(d)) >= 100
    assert call(u, body(order(G, key, spk, "c" * 32)), proof)[0] == 507

def test_the_offer_kid_file_and_orders_folder_are_denied_by_htaccess():
    from pathlib import Path
    ht = (Path(ROOT) / "plugins" / "gallery_assets" / "htaccess").read_text()
    assert "offer-kid\\.php" in ht and "RewriteRule ^orders(/|$) - [F,L]" in ht
