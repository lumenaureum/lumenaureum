import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules

GID = "galPhp111x"

@pytest.fixture
def host(tmp_path, monkeypatch):
    php = shutil.which("php")
    if not php:
        pytest.skip("php is not installed")
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (40, 30), "red").save(src / "a.jpg", "JPEG")
    key = G.new_key()
    d = G.build(GID, src, key, picks=dict(retouch=2, market=2, post=True)).out_dir
    (d / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\nproof=1\n")
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

def sealed(G, key, aad):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    body = json.dumps({"v": 1, "at": "2026-11-14T15:00:00Z", "picks": [{"i": 0, "name": "a.jpg", "r": True}]}).encode()
    return b"LAK1" + iv + AESGCM(G.key_bytes(key)).encrypt(iv, body, aad)

def call(url, body=None, proof=None):
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"X-Proof": proof} if proof else {})
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), time.monotonic() - t
    except urllib.error.HTTPError as e:
        return e.code, b"", time.monotonic() - t

def test_picks_php_refuses_writes_once_the_window_has_closed(host):
    base, d, proof, G, key = host
    picks, draft = sealed(G, key, G.PICKS_AAD), sealed(G, key, G.DRAFT_AAD)
    assert call(base + "picks.php", picks, proof)[0] == 204
    (d / "picks.bin").unlink()
    (d / "seen.txt").write_text("2026-01-01T12:00:00Z")
    assert call(base + "picks.php", picks, proof)[0] == 410
    assert call(base + "picks.php?kind=draft", draft, proof)[0] == 410
    assert call(base + "gate.php?f=manifest.bin", proof=proof)[0] == 410
    (d / "seen.txt").unlink()
    (d / "limits.txt").write_text("days=30\nunopened_until=2001-01-01\nproof=1\n")
    assert call(base + "picks.php", picks, proof)[0] == 410
    (d / "limits.txt").write_text("days=30\nunopened_until=not a date\n")
    assert call(base + "picks.php", picks, proof)[0] == 410
    (d / "limits.txt").unlink()
    assert call(base + "picks.php?kind=draft", draft, proof)[0] == 410
    assert not (d / "picks.bin").exists() and not (d / "draft.bin").exists()

def test_gate_answers_at_once_without_a_proof_and_waits_on_a_wrong_one(host):
    base, d, proof, G, key = host
    (d / "draft.bin").write_bytes(sealed(G, key, G.DRAFT_AAD))
    for f in ("draft.bin", "picks.bin", "manifest.bin"):
        code, _, took = call(base + "gate.php?f=" + f)
        assert code == 403 and took < 0.5, f
    code, _, took = call(base + "gate.php?f=draft.bin", proof="0" * 32)
    assert code == 403 and took >= 0.9

def test_gate_serves_picks_bin_only_with_the_proof(host):
    base, d, proof, G, key = host
    assert call(base + "gate.php?f=picks.bin", proof=proof)[0] == 404
    sent = sealed(G, key, G.PICKS_AAD)
    assert call(base + "picks.php", sent, proof)[0] == 204
    assert call(base + "gate.php?f=picks.bin")[0] == 403
    assert call(base + "gate.php?f=picks.bin", proof="1" * 32)[0] == 403
    code, body, _ = call(base + "gate.php?f=picks.bin", proof=proof)
    assert (code, body) == (200, sent)

    (d / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\n")
    assert call(base + "gate.php?f=picks.bin")[0] == 403
    assert call(base + "gate.php?f=seen-key.php", proof=proof)[0] == 404
