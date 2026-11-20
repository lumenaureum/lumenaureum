import base64
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, form_d, hosted, seal_picks
from helpers_tools import need_tool

GID = "g-galSecGate14xAAAAAAAAAA"

def _work_exactly(env, bits, below):

    import hashlib
    inner, i = hashlib.sha256(env).digest(), 0
    while True:
        w = i.to_bytes(8, "big")
        v = int.from_bytes(hashlib.sha256(inner + w).digest()[:4], "big")
        if v >> (32 - bits) == 0 and (bits >= below or v >> (32 - below) != 0):
            return w.hex()
        i += 1

def _serve(php, docroot):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(docroot)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return p, f"http://127.0.0.1:{port}/"

def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

@pytest.fixture
def site(tmp_path):
    php = need_tool("php")
    os.environ["CRM_GALLERY_ROOT"] = str(tmp_path / "galleries")
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    import hostfiles
    from vault import backends as VB
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (40, 30), "red").save(src / "p0.jpg", "JPEG")
    key = G.new_key()
    out = G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True)).out_dir
    web = tmp_path / "web"
    web.mkdir()
    proof = hostfiles.proof_of(G.key_bytes(key))
    host = hostfiles.HostFiles(VB.LocalBackend(str(web), marked=False), lambda f: proof if f == GID else None)
    for p in sorted(out.iterdir(), key=G.upload_order):
        if p.is_file() and not p.name.startswith("."):
            host.put_stream(f"{GID}/{p.name}", io.BytesIO(p.read_bytes()))
    host.put_stream(f"{GID}/limits.txt", io.BytesIO(b"days=30\nunopened_until=2099-01-01\nproof=1\n"))
    srv, base = _serve(php, web)
    yield G, key, proof, host, web / GID, base + GID + "/"
    srv.terminate()
    srv.wait(timeout=10)
    os.environ.pop("CRM_GALLERY_ROOT", None)

def solve_gate(proof, hour, bits):
    inner = hashlib.sha256(b"lumen-aureum gate work\0" + proof.encode() + b"\0" + str(hour).encode()).digest()
    i = 0
    while True:
        w = i.to_bytes(8, "big")
        if int.from_bytes(hashlib.sha256(inner + w).digest()[:4], "big") >> (32 - bits) == 0:
            return f"{hour}-{w.hex()}"
        i += 1

def test_no_sealed_file_or_state_sits_at_a_name_anyone_can_guess(site):
    G, key, proof, host, d, base = site
    names = {p.name for p in d.iterdir()}
    assert not {"manifest.bin", "limits.txt", "seen.txt", "picks.las", "draft.las"} & names
    st, _h, body = call(base + "manifest.bin")
    assert st == 404 or body == (d / "index.html").read_bytes()
    st, _h, body = call(base + "limits.php")
    assert st == 200 and body == b""
    assert call(base + "seen-key.php")[2] == b""

    st, _h, body = call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    m = json.loads(AESGCM(G.key_bytes(key)).decrypt(body[:12], body[12:], b"lumen-aureum-gallery:manifest.bin"))
    assert st == 200 and m["items"]
    assert call(base + "gate.php?f=manifest.bin")[0] == 403

def test_picks_go_up_under_the_key_proofs_name_and_come_back_through_the_gate(site):
    G, key, proof, host, d, base = site
    form = {"Content-Type": "application/x-www-form-urlencoded", "X-Proof": proof}
    (d / "picks.las").write_bytes(b"LAS1 an older copy under the plain name")
    blob = seal_picks(key, GID, [{"i": 0, "name": "p0.jpg", "r": True}], "picks")
    assert call(base + "picks.php", data=form_d(blob), headers=form)[0] == 204
    import hostfiles
    assert (d / hostfiles.name_for(proof, "picks.las")).read_bytes() == blob
    assert not (d / "picks.las").exists()
    assert call(base + "gate.php?f=picks.las", headers={"X-Proof": proof})[2] == blob
    st, h, _b = call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})
    assert h.get("X-Saved") == "picks"

    buf = io.BytesIO()
    host.get_stream(f"{GID}/picks.las", buf)
    assert buf.getvalue() == blob and f"{GID}/picks.las" in host.list(f"{GID}/")

def test_a_folder_from_before_keeps_working_and_the_refit_moves_it(site):

    G, key, proof, host, d, base = site
    import hostfiles
    m = d / hostfiles.name_for(proof, "manifest.bin")
    shutil.move(m, d / "manifest.bin")
    lim = d / "limits.php"
    (d / "limits.txt").write_bytes(hostfiles.state_bytes(lim.read_bytes()))
    lim.unlink()
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})[0] == 200
    moved = hostfiles.relayout(host, GID)
    assert set(moved) == {"manifest.bin", "limits.txt"}
    assert m.is_file() and not (d / "manifest.bin").exists() and lim.read_bytes().startswith(hostfiles.EXIT)
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})[0] == 200

def test_the_host_check_finds_a_gate_anyone_can_walk_around(site, tmp_path, monkeypatch):

    G, key, proof, host, d, base = site
    import hostcheck
    root = base.rsplit(GID, 1)[0]
    assert hostcheck.gate_holds(host, root, (GID, G.key_bytes(key))) is False
    assert hostcheck.gate_holds(host, root) is False
    assert not [p for p in d.parent.iterdir() if p.name.startswith(".la-gate")]
    caps = dict(kind="ok", gate=False)
    assert hostcheck.gate_fails(caps) and hostcheck.gate_fails(dict(kind="none"))
    assert not hostcheck.gate_fails(dict(kind="ok", gate=True))
    monkeypatch.setattr(G, "host_check_now", lambda: caps)
    assert "can't go there" in G.word_refused()

def test_a_proxy_in_front_of_the_host_is_seen():
    import hostcheck
    assert hostcheck.proxied({"fwd": ["HTTP_X_FORWARDED_FOR"], "ra": "203.0.113.4"}, "https://ex.com/g/") == \
        "it passed on X-Forwarded-For"
    assert hostcheck.proxied({"fwd": [], "ra": "127.0.0.1"}, "https://ex.com/g/") == \
        "every request reaches it from its own machine"
    assert hostcheck.proxied({"fwd": [], "ra": "127.0.0.1"}, "http://127.0.0.1:8080/g/") == ""
    assert hostcheck.proxied({"fwd": [], "ra": "198.51.100.7"}, "https://ex.com/g/") == ""

def test_a_right_proof_is_never_refused_because_of_others_junk(site):
    G, key, proof, host, d, base = site
    host.put_stream(f"{GID}/limits.txt", io.BytesIO(b"days=30\nunopened_until=2099-01-01\nproof=1\ncodeword=0\n"))
    for _ in range(25):
        assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": "0" * 32})[0] == 403
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})[0] == 200
    assert not (d / "tries.php").exists()

def test_codeword_guessing_costs_work_and_locks_nobody_out(site):
    G, key, proof, host, d, base = site
    for _ in range(10):
        assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": "1" * 32})[0] == 403
    raw = (d / "tries.php").read_text()
    assert raw.startswith("<?php exit; ?>\n") and "127.0.0.1" not in raw
    st, h, _b = call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})
    assert st == 429
    bits, hour = (int(x) for x in h["X-Work"].split())
    assert 16 <= bits <= 20
    work = solve_gate(proof, hour, bits)
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof, "X-Work": work})[0] == 200
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": "2" * 32, "X-Work": work})[0] == 429
    assert call(base + "gate.php?f=manifest.bin", headers={"X-Proof": "2" * 32,
                                                            "X-Work": solve_gate("2" * 32, hour, bits)})[0] == 403

def test_inquiry_rate_list_prints_nothing_and_a_proxy_turns_nobody_away(tmp_path):
    php = need_tool("php")
    fresh_modules()
    from plugins import studio_page as S
    _priv, pub_b64 = S.new_inquiry_key()
    site = tmp_path / "site"
    (site / "inquiries").mkdir(parents=True)
    shutil.copyfile(ROOT / "plugins" / "sealed_assets" / "sealed.php", site / "sealed.php")
    src = S.inquiry_php_source("ab" * 16, 8, S.kid_hex(pub_b64), shared=True).replace("$WORK_SHARED = 20;", "$WORK_SHARED = 12;")
    (site / "inquiry.php").write_text(src)
    srv, base = _serve(php, site)
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        def short_of(env, have, need):

            inner, i = hashlib.sha256(env).digest(), 0
            while True:
                v = int.from_bytes(hashlib.sha256(inner + i.to_bytes(8, "big")).digest()[:4], "big")
                if v >> (32 - have) == 0 and v >> (32 - need) != 0:
                    return i.to_bytes(8, "big").hex()
                i += 1

        def post(i, bits, weak_of=0):
            env = S.seal_inquiry(pub_b64, {"v": 1, "id": f"{i:032x}", "name": "Ada", "email": "ada@example.com",
                                          "message": f"hello {i}", "t": 9000})
            d = base64.urlsafe_b64encode(env).rstrip(b"=").decode()
            w = short_of(env, bits, weak_of) if weak_of else S.solve_work(env, bits)
            return call(base + "inquiry.php", data=f"w={w}&d={d}".encode(), headers=form)
        st, h, _b = post(0, 8, weak_of=12)
        assert st == 429 and h.get("X-Work") == "12"
        assert [post(i, 12)[0] for i in range(7)] == [204] * 7
        assert call(base + "inquiries/.rate.php")[2] == b""
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def test_the_studio_page_puts_an_empty_index_in_the_inquiries_folder():
    from plugins import studio_page as S
    assert S.NAME_OK.match("inquiries/index.html") and S.INQ_RATE == ".rate.php"
    src = (ROOT / "plugins" / "studio_assets" / "inquiry.php").read_text()
    assert "/.rate.php" in src and "<?php exit; ?>" in src
    page = (ROOT / "plugins" / "studio_page.py").read_text()
    assert 'put(f"{INQ_DIR}/index.html", "")' in page

def test_orders_folder_holds_an_empty_index():
    src = (ROOT / "plugins" / "gallery_assets" / "orders.php").read_text()
    assert "index.html" in src
    from plugins import prints
    assert "orders/index.html" in (ROOT / "plugins" / "prints.py").read_text()

def _inquiry_site(tmp_path, shared):

    php = need_tool("php")
    from plugins import studio_page as S
    _priv, pub_b64 = S.new_inquiry_key()
    site = tmp_path / "site"
    (site / "inquiries").mkdir(parents=True)
    shutil.copyfile(ROOT / "plugins" / "sealed_assets" / "sealed.php", site / "sealed.php")
    src = S.inquiry_php_source("ab" * 16, 8, S.kid_hex(pub_b64), shared=shared)
    (site / "inquiry.php").write_text(src.replace("$WORK_SHARED = 20;", "$WORK_SHARED = 12;"))
    (site / "_t.php").write_text("<?php $_SERVER['REMOTE_ADDR'] = $_SERVER['HTTP_X_T_ADDR'] ?? $_SERVER['REMOTE_ADDR'];"
                                 " include __DIR__ . '/inquiry.php';\n")
    srv, base = _serve(php, site)

    def post(i, bits, addr="203.0.113.9"):
        env = S.seal_inquiry(pub_b64, {"v": 1, "id": f"{i:032x}", "name": "Ada", "email": "ada@example.com",
                                      "message": f"hello {i}", "t": 9000})
        d = base64.urlsafe_b64encode(env).rstrip(b"=").decode()
        return call(base + "_t.php", data=f"w={_work_exactly(env, bits, below=12)}&d={d}".encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "X-T-Addr": addr})
    return S, site, srv, post

def test_past_a_senders_limits_an_inquiry_asks_for_more_work_and_gets_in(tmp_path):

    S, site, srv, post = _inquiry_site(tmp_path, shared=False)
    try:
        assert [post(i, 8)[0] for i in range(5)] == [204] * 5
        st, h, _b = post(5, 8)
        assert st == 429 and h.get("X-Work") == "12"
        assert post(5, 12)[0] == 204
        assert post(6, 8, addr="198.51.100.4")[0] == 204
        assert len(list((site / "inquiries").glob("*.las"))) == 7
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def test_behind_a_proxy_a_full_inquiries_folder_drops_nothing_already_kept(tmp_path):
    S, site, srv, post = _inquiry_site(tmp_path, shared=True)
    try:
        inq = site / "inquiries"
        blob = b"LAS1" + b"\0" * 200
        for i in range(200):
            (inq / f"{'0' * 16}-{i:032x}.las").write_bytes(blob)
        before = sorted(p.name for p in inq.glob("*.las"))
        assert post(1, 12)[0] == 507
        assert sorted(p.name for p in inq.glob("*.las")) == before
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def test_inquiry_php_must_carry_the_proxy_line(tmp_path):
    from plugins import studio_page as S
    src = S.inquiry_php_source("ab" * 16, 8, "0" * 16, shared=True)
    assert "\n$SHARED = 1;" in src and "\n$SHARED = 0;" in S.inquiry_php_source("ab" * 16, 8, "0" * 16)
    real = S.ASSETS
    (tmp_path / "inquiry.php").write_text((real / "inquiry.php").read_text().replace("$SHARED = 0;", "$X = 0;"))
    S.ASSETS = tmp_path
    try:
        with pytest.raises(ValueError):
            S.inquiry_php_source("ab" * 16, 8, "0" * 16, shared=True)
    finally:
        S.ASSETS = real

def test_today_says_plainly_what_a_failing_host_means_for_codeword_galleries_already_there():
    import hostcheck
    was = dict(kind="ok", gate=True)
    msg = hostcheck.worse(was, dict(kind="ok", gate=False))
    assert "could now try codewords much faster" in msg and "take those galleries down" in msg
    assert "stay sealed" not in msg
    assert hostcheck.worse(was, dict(kind="ok", gate=None)) == ""

def _app_with_gallery(tmp_path, monkeypatch, word="spruce otter raven"):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    from helpers import signed_in_client
    c = signed_in_client(A)
    G = A.app.view_functions["build_gallery"].__globals__
    key = G["new_key"]()
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, gallery_id, gallery_codeword) VALUES (1, 1, 'family', ?, ?)",
                (GID, word))
    con.execute("INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, n_files) "
                "VALUES (1, ?, ?, '2026-11-17', 'built', 3)", (key, "e" + key[1:] if word else ""))
    con.commit()
    con.close()
    return A, c, G, key

def test_a_gallery_with_a_codeword_is_refused_on_a_host_that_hands_files_out(tmp_path, monkeypatch):
    A, c, G, key = _app_with_gallery(tmp_path, monkeypatch)
    started = []

    class _T:
        def __init__(self, *a, **kw):
            started.append(kw)

        def start(self):
            pass
    monkeypatch.setitem(G, "codeword_key", lambda k, i, w: "e" + key[1:] if w else k)
    monkeypatch.setitem(G, "host_check_now", lambda: dict(kind="ok", gate=False))
    monkeypatch.setattr(G["threading"], "Thread", _T)
    c.post("/session/1/gallery/upload", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    con = A.connect()
    st, err = con.execute("SELECT upload_state, upload_error FROM galleries WHERE session_id=1").fetchone()
    con.close()
    assert st == "error" and err == G["WORD_REFUSED"] and not started
    assert "try codewords as fast as their computer allows" in err

    monkeypatch.setitem(G, "host_check_now", lambda: dict(kind="ok", gate=True))
    c.post("/session/1/gallery/upload", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert started

def test_the_host_check_fetches_a_live_gallery_without_the_proof(tmp_path, monkeypatch):

    A, c, G, key = _app_with_gallery(tmp_path, monkeypatch, word="")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', ekey=? WHERE session_id=1", (key,))
    con.commit()
    con.close()
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".vault-root").write_text("")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(tmp_path / "web")}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', 'https://www.example.com/g/')")
    con.commit()
    con.close()
    import hostcheck
    seen = []
    monkeypatch.setattr(hostcheck, "probe", lambda h, b: hostcheck.Probe(kind="ok", version="8.2", wait=2, detail=""))
    monkeypatch.setattr(hostcheck, "htaccess_honored", lambda h, b: True)
    monkeypatch.setattr(hostcheck, "gate_holds", lambda h, b, g=None: seen.append(g) or True)
    c.post("/setup/host-check", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert seen == [(GID, G["key_bytes"](key))]
