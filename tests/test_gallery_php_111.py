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
from helpers import hosted, host_has, host_write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

GID = "g-galPhp111xAAAAAAAAAAAAA"

@pytest.fixture
def host(tmp_path, monkeypatch):
    php = need_tool("php")
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (40, 30), "red").save(src / "a.jpg", "JPEG")
    key = G.new_key()
    d = G.build(GID, src, key, picks=dict(retouch=2, market=2, post=True)).out_dir
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\n")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([php, "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    proof = hashlib.sha256(G.key_bytes(key)).hexdigest()[:32]
    yield f"http://127.0.0.1:{port}/", d, proof, G, key
    p.terminate(); p.wait()

def sealed(G, key, purpose, gid=GID):
    from helpers import seal_picks
    return seal_picks(key, gid, [{"i": 0, "name": "a.jpg", "r": True}], purpose, "2026-11-14T15:00:00Z")

def call(url, body=None, proof=None):
    from helpers import form_d
    h = {"X-Proof": proof} if proof else {}
    if body is not None:
        body, h["Content-Type"] = form_d(body), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET", headers=h)
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), time.monotonic() - t
    except urllib.error.HTTPError as e:
        return e.code, b"", time.monotonic() - t

def test_picks_php_refuses_writes_once_the_window_has_closed(host):
    base, d, proof, G, key = host
    picks, draft = sealed(G, key, "picks"), sealed(G, key, "draft")
    assert call(base + "picks.php", picks, proof)[0] == 204
    hosted(d, "picks.las").unlink()
    host_write(d, "seen.txt", "2026-01-01T12:00:00Z")
    assert call(base + "picks.php", picks, proof)[0] == 410
    assert call(base + "picks.php?kind=draft", draft, proof)[0] == 410
    assert call(base + "gate.php?f=manifest.bin", proof=proof)[0] == 410
    hosted(d, "seen.txt").unlink()
    host_write(d, "limits.txt", "days=30\nunopened_until=2001-01-01\nproof=1\n")
    assert call(base + "picks.php", picks, proof)[0] == 410
    host_write(d, "limits.txt", "days=30\nunopened_until=not a date\n")
    assert call(base + "picks.php", picks, proof)[0] == 410
    hosted(d, "limits.txt").unlink()
    assert call(base + "picks.php?kind=draft", draft, proof)[0] == 410
    assert not host_has(d, "picks.las") and not host_has(d, "draft.las")

def test_gate_answers_at_once_without_a_proof_and_on_a_wrong_one(host):
    base, d, proof, G, key = host
    (d / "draft.las").write_bytes(sealed(G, key, "draft"))
    for f in ("draft.las", "picks.las", "manifest.bin"):
        code, _, took = call(base + "gate.php?f=" + f)
        assert code == 403 and took < 0.5, f
    assert not (d / "tries.php").exists()
    code, _, took = call(base + "gate.php?f=draft.las", proof="0" * 32)
    assert code == 403 and took < 0.5
    assert (d / "tries.php").is_file()

def test_wrong_proofs_lock_the_sender_out_for_an_hour(host):
    base, d, proof, G, key = host
    assert call(base + "gate.php?f=manifest.bin", proof=proof)[0] == 200
    for i in range(6):
        assert call(base + "gate.php?f=manifest.bin", proof=f"{i:032x}")[0] == 403
    for i in range(4):
        assert call(base + "picks.php", sealed(G, key, "picks"), proof=f"{i + 6:032x}")[0] == 403
    t = time.monotonic()
    for _ in range(3):
        assert call(base + "gate.php?f=manifest.bin", proof=proof)[0] == 429
        assert call(base + "picks.php", sealed(G, key, "picks"), proof)[0] == 429
    assert time.monotonic() - t < 3
    assert not host_has(d, "picks.las")
    raw = (d / "tries.php").read_text()
    assert raw.startswith("<?php exit; ?>\n") and "127.0.0.1" not in raw
    assert call(base + "tries.php")[1] == b""

    head, body = raw.split("\n", 1)
    old = {k: [t_ - 3601 for t_ in v] for k, v in json.loads(body).items()}
    (d / "tries.php").write_text(head + "\n" + json.dumps(old) + "\n")
    assert call(base + "gate.php?f=manifest.bin", proof=proof)[0] == 200
    assert call(base + "picks.php", sealed(G, key, "picks"), proof)[0] == 204
    assert call(base + "gate.php?f=manifest.bin", proof="f" * 32)[0] == 403
    assert [len(v) for v in json.loads((d / "tries.php").read_text().split("\n", 1)[1]).values()] == [1]

def test_a_burst_of_wrong_proofs_gets_no_more_than_ten_answers(host):
    base, d, proof, G, key = host
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda i: call(base + "gate.php?f=manifest.bin", proof=f"{i:032x}")[0], range(30)))
    assert codes.count(403) == 10 and codes.count(429) == 20

def test_gate_serves_picks_bin_only_with_the_proof(host):
    base, d, proof, G, key = host
    assert call(base + "gate.php?f=picks.las", proof=proof)[0] == 404
    sent = sealed(G, key, "picks")
    assert call(base + "picks.php", sent, proof)[0] == 204
    assert call(base + "gate.php?f=picks.las")[0] == 403
    assert call(base + "gate.php?f=picks.las", proof="1" * 32)[0] == 403
    code, body, _ = call(base + "gate.php?f=picks.las", proof=proof)
    assert (code, body) == (200, sent)

    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\n")
    assert call(base + "gate.php?f=picks.las")[0] == 403
    assert call(base + "gate.php?f=seen-key.php", proof=proof)[0] == 404
    (d / "picks.bin").write_bytes(b"x" * 64)
    assert call(base + "gate.php?f=picks.bin", proof=proof)[0] == 404
    assert call(base + "gate.php?f=sealed.php", proof=proof)[0] == 404
