import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import http.client
import urllib.request
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "galDraft01"
VIEWER = os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    Image.new("RGB", (32, 24), "red").save(src / "a.jpg", "JPEG")
    Image.new("RGB", (32, 24), "blue").save(src / "b.jpg", "JPEG")
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    from plugins import gallery as G
    key = G.new_key()
    G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True))
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', 'https://example.com/g/')")
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id)
                   VALUES (1, 'family', 'delivered', '2026-01-10', ?)""", (GID,))
    con.execute("UPDATE sessions SET first_visit=? WHERE id=1", (date.today().isoformat(),))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files) VALUES (1, ?, ?, 2)", (key, date.today().isoformat()))
    con.commit(); con.close()
    G.upload_gallery(GID, A.C.GALLERY_DAYS, "2099-01-01")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', uploaded_at=? WHERE session_id=1", (date.today().isoformat(),))
    con.commit(); con.close()
    return A, c, G, host, key

def sealed(G, key, picks, aad, at="2026-11-14T15:00:00.000Z"):

    import secrets as _s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = _s.token_bytes(12)
    return b"LAK1" + iv + AESGCM(G.key_bytes(key)).encrypt(iv, json.dumps({"v": 1, "at": at, "picks": picks}).encode(), aad)

DRAFT = [{"i": 0, "name": "a.jpg", "r": True, "note": "soften", "m": False, "v": False},
         {"i": 1, "name": "b.jpg", "r": False, "note": "", "m": True, "v": False}]

def status(A):
    con = A.connect()
    r = con.execute("SELECT draft_n, draft_at FROM galleries WHERE session_id=1").fetchone()
    con.close()
    return tuple(r)

def test_draft_shows_still_picking_until_matching_picks_arrive(env):
    A, c, G, host, key = env
    c.post("/gallery/sync", environ_base=E)
    assert status(A) == (None, None)
    (host / GID / "draft.bin").write_bytes(sealed(G, key, DRAFT, G.DRAFT_AAD))
    c.post("/gallery/sync", environ_base=E)
    n, at = status(A)
    assert n == 2 and at
    assert "soften" not in json.dumps(status(A))
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Client is still picking (2 photos marked)" in page
    assert "Client is still picking (2 photos marked)" in c.get("/", environ_base=E).get_data(as_text=True)

    (host / GID / "picks.bin").write_bytes(sealed(G, key, DRAFT[:1], G.PICKS_AAD))
    c.post("/gallery/sync", environ_base=E)
    assert status(A)[0] == 2

    (host / GID / "picks.bin").write_bytes(sealed(G, key, DRAFT, G.PICKS_AAD))
    c.post("/gallery/sync", environ_base=E)
    assert status(A) == (None, None)
    assert "Client is still picking" not in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    con = A.connect()
    assert tuple(con.execute("SELECT retouch_picks, favorites FROM sessions WHERE id=1").fetchone()) == ("a.jpg: soften", "b.jpg")
    con.close()

@pytest.mark.parametrize("stage", ["shot", "delivered"])
def test_still_picking_shows_once_at_any_stage(env, stage):
    A, c, G, host, key = env
    con = A.connect()
    con.execute("UPDATE sessions SET stage=? WHERE id=1", (stage,))
    con.commit(); con.close()
    (host / GID / "draft.bin").write_bytes(sealed(G, key, DRAFT[:1], G.DRAFT_AAD))
    c.post("/gallery/sync", environ_base=E)
    for url in ("/c/1?s=1", "/"):
        page = c.get(url, environ_base=E).get_data(as_text=True)
        text = re.sub(r"<[^>]+>", " ", page)
        assert text.count("Client is still picking (1 photo marked)") == 1, (stage, url)

def test_a_draft_is_not_taken_for_picks_or_the_other_way(env):
    A, c, G, host, key = env

    (host / GID / "picks.bin").write_bytes(sealed(G, key, DRAFT, G.DRAFT_AAD))
    (host / GID / "draft.bin").write_bytes(sealed(G, G.new_key(), DRAFT, G.DRAFT_AAD))
    c.post("/gallery/sync", environ_base=E)
    assert status(A) == (None, None)
    con = A.connect()
    assert con.execute("SELECT retouch_picks FROM sessions WHERE id=1").fetchone()[0] in ("", None)
    con.close()
    with pytest.raises(Exception):
        G.open_draft(sealed(G, key, DRAFT, G.PICKS_AAD), key)

def test_draft_goes_with_the_gallery(env):
    A, c, G, host, key = env
    (host / GID / "draft.bin").write_bytes(sealed(G, key, DRAFT, G.DRAFT_AAD))
    G.delete_gallery(GID)
    assert not (host / GID).exists()

@pytest.fixture
def php_host(env):
    php = shutil.which("php")
    if not php:
        pytest.skip("php is not installed")
    A, c, G, host, key = env
    d = host / GID
    (d / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\nproof=1\n")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([php, "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    import hashlib
    proof = hashlib.sha256(G.key_bytes(key)).hexdigest()[:32]
    yield base, d, proof, G, key
    p.terminate(); p.wait()

def call(url, body=None, proof=None):

    h = {"X-Proof": proof} if proof else {}
    if body is not None:
        h["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""

def test_picks_php_writes_a_draft_only_with_the_proof_and_the_envelope(php_host):
    base, d, proof, G, key = php_host
    good = sealed(G, key, DRAFT, G.DRAFT_AAD)
    assert call(base + "picks.php?kind=draft", good)[0] == 403
    assert call(base + "picks.php?kind=draft", good, "0" * 32)[0] == 403
    assert call(base + "picks.php?kind=draft", b"LAK1" + b"hello there, plain words " * 4, proof)[0] == 400
    assert call(base + "picks.php?kind=other", good, proof)[0] == 400
    assert not (d / "draft.bin").exists()
    assert call(base + "picks.php?kind=draft", good, proof)[0] == 204
    assert (d / "draft.bin").read_bytes() == good and not (d / "picks.bin").exists()

    assert call(base + "gate.php?f=draft.bin")[0] == 403
    assert call(base + "gate.php?f=draft.bin", proof=proof) == (200, good)

    sent = sealed(G, key, DRAFT, G.PICKS_AAD)
    assert call(base + "picks.php", sent, proof)[0] == 204
    assert (d / "picks.bin").read_bytes() == sent and (d / "draft.bin").read_bytes() == good

    try:
        assert call(base + "picks.php?kind=draft", b"LAK1" + os.urandom(262144), proof)[0] == 413
    except (ConnectionError, http.client.RemoteDisconnected, urllib.error.URLError):
        pass
    assert (d / "draft.bin").read_bytes() == good

def test_picks_and_the_visit_stamp_arrive_as_forms(php_host):

    import base64
    from urllib.parse import urlencode
    base, d, proof, G, key = php_host
    good = sealed(G, key, DRAFT, G.DRAFT_AAD)

    def form(url, fields, proof_=None):
        req = urllib.request.Request(url, data=urlencode(fields).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              **({"X-Proof": proof_} if proof_ else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    d64 = base64.urlsafe_b64encode(good).decode().rstrip("=")
    assert form(base + "picks.php?kind=draft", {"d": d64}) == 403
    assert form(base + "picks.php?kind=draft", {"d": base64.urlsafe_b64encode(b"LAK1" + b"plain words here " * 6).decode()},
                proof) == 400
    assert form(base + "picks.php?kind=draft", {"d": "not base64!"}, proof) == 413
    assert form(base + "picks.php?kind=draft", {"d": d64}, proof) == 204
    assert (d / "draft.bin").read_bytes() == good
    (d / "seen.txt").unlink(missing_ok=True)
    assert form(base + "seen.php", {"p": "0" * 32}) == 204 and not (d / "seen.txt").exists()
    assert form(base + "seen.php", {"p": proof}) == 204 and (d / "seen.txt").exists()

def test_gate_says_missing_draft_plainly(php_host):
    base, d, proof, G, key = php_host
    assert call(base + "gate.php?f=draft.bin", proof=proof)[0] == 404

@pytest.fixture(scope="module")
def runs():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "viewer_draft_harness.js"), VIEWER], capture_output=True,
                         text=True, timeout=120, check=True).stdout
    return json.loads(out)

def test_viewer_takes_the_newer_host_draft(runs):
    r = runs["host_newer"]
    assert r["kept"]["picks"] == {"n:c.jpg": {"m": True}, "n:b.jpg": {"r": True, "n": "brighter"}}
    assert r["tray"].endswith("Saved  ·  Not sent yet")

def test_viewer_keeps_newer_local_picks_and_saves_them_up(runs):
    r = runs["local_newer"]
    assert r["kept"]["picks"] == {"n:a.jpg": {"r": True, "n": "soften"}}
    assert [d["u"] for d in r["drafts"]] == ["picks.php?kind=draft"] and r["drafts"][0]["proof"] == 32
    assert r["drafts"][0]["payload"]["picks"][0]["name"] == "a.jpg"

def test_viewer_keeps_local_picks_when_the_host_fails(runs):
    for case in ("host_unreachable", "host_garbage"):
        r = runs[case]
        assert r["kept"]["picks"] == {"n:a.jpg": {"r": True, "n": "soften"}}, case
        assert r["tray"].endswith("Saved on this device  ·  Not sent yet"), case
    assert runs["host_missing"]["kept"]["picks"] == {"n:a.jpg": {"r": True, "n": "soften"}}

def test_a_pick_saves_a_draft_after_a_moment(runs):
    r = runs["pick_saves_draft"]
    assert len(r["drafts"]) == 1
    p = r["drafts"][0]["payload"]
    assert p["v"] == 1 and p["at"] == r["kept"]["at"]
    assert p["picks"] == [{"i": 1, "name": "b.jpg", "k": "n:b.jpg", "r": True, "note": "less glare", "m": False, "v": False}]
    assert r["tray"].endswith("Saved  ·  Not sent yet")
