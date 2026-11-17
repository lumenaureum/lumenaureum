import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers_tools import need_tool

E = {"REMOTE_ADDR": "127.0.0.1"}

def _serve(php, root):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return p, port

def _rewrite(monkeypatch, routes):
    import hostcheck
    real = hostcheck._http

    def http(method, url, headers=None, data=None, timeout=20):
        for pub, local in routes:
            if url.startswith(pub):
                url = local + url[len(pub):]
                break
        return real(method, url, headers=headers, data=data, timeout=timeout)
    monkeypatch.setattr(hostcheck, "HTTP", http)
    monkeypatch.setattr(hostcheck, "SLEEP", lambda s_: None)

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _legacy_inquiry(pem, fields):

    import base64
    import secrets
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    site = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    eph = ec.generate_private_key(ec.SECP256R1())
    epub = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    z = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), site))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epub + site, info=b"lumen-aureum inquiry v1").derive(z)
    magic = bytes.fromhex("4c415131")
    iv = secrets.token_bytes(12)
    del base64
    return magic + epub + iv + AESGCM(key).encrypt(iv, json.dumps(fields).encode(), magic)

def _check_galleries(A, G, tmp_path, hosts):

    import sealed
    rows = _q(A, """SELECT s.gallery_id, g.key, g.ekey, g.host_fp, g.sealed_gen, g.upload_state, g.removed_at
                    FROM galleries g JOIN sessions s ON s.id=g.session_id""")
    online = [r for r in rows if r[5] == "up" and not r[6]]
    assert online and all(r[4] == 1 for r in online)

    for gid, was, was_open in _q(A, """SELECT s.gallery_id, g.link_was, g.was_open FROM galleries g
                                       JOIN sessions s ON s.id=g.session_id"""):
        assert G.is_link(gid) and was and not G.is_link(was) and not was_open, (gid, was)
        if any(r[0] == gid for r in online) and (hosts[0] / gid / "index.html").is_file():
            assert sorted(p.name for p in (hosts[0] / was).iterdir()) == ["index.html"], was
            assert (hosts[0] / was / "index.html").read_bytes() == G.redirect_page(gid)
    for gid, key, ekey, _fp, _g, _u, _r in online:
        host = hosts[0] / gid
        if not (host / "index.html").is_file():
            continue
        page = (host / "index.html").read_text()
        assert G._script_hashes(page) == G.VIEWER_SCRIPT_HASHES, gid
        if (host / "picks.php").is_file():
            for n, src in G.HOST_SCRIPTS.items():
                assert (host / n).read_bytes() == (G.ASSETS / src).read_bytes(), (gid, n)
        for name in ("picks.las", "draft.las"):
            if (host / name).is_file():
                sealed.open_link(name[:-4], gid, G.key_bytes(ekey or key), (host / name).read_bytes())
    for p in hosts[0].rglob("*.bin"):
        assert p.name not in ("picks.bin", "draft.bin"), p

    if any(p.name in ("picks.bin", "draft.bin") for h in hosts[1:] for p in h.rglob("*.bin")):
        assert _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'What the client sent to the copy on %'")[0][0] >= 1
    kept = tmp_path / "galleries-kept"
    if kept.is_dir():
        assert not [p for p in kept.rglob("*") if p.name in ("picks.bin", "draft.bin")]

@pytest.mark.parametrize("version", ["1.3.0", "1.2.0"])
def test_the_upgrade_finishes_on_a_release_fixture(tmp_path, monkeypatch, version):
    php = need_tool("php")
    if version == "1.3.0":
        import test_upgrade_from_130 as T
        T._place(tmp_path)
        web, earlier = tmp_path / "web", tmp_path / "host"
        cur_root, galleries = web, web / "g"
    else:
        import test_upgrade_from_120 as T
        T._place(tmp_path)
        cur_root = galleries = tmp_path / "host"
        earlier = None
    base = T.EXPECTED.get("gallery_base_url") or "https://example.com/g/"
    from urllib.parse import urlsplit
    pub_root = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}/"
    before = {p: p.read_bytes() for p in (tmp_path / "galleries-kept").rglob("*") if p.is_file()} \
        if (tmp_path / "galleries-kept").is_dir() else {}
    srv, port = _serve(php, cur_root)
    try:
        prefix = "" if version == "1.3.0" else "g/"
        _rewrite(monkeypatch, [(pub_root + prefix, f"http://127.0.0.1:{port}/")] if prefix else
                 [(pub_root, f"http://127.0.0.1:{port}/")])
        legacy_left = []
        A, c, taken = T._open(tmp_path, monkeypatch)[:3]
        from plugins import gallery as G
        if version == "1.3.0":

            pem = _q(A, "SELECT value FROM settings WHERE key='site_inquiry_key'")[0][0]
            inq = web / "inquiries"
            good = dict(v=1, name="Grace Hopper", email="grace@example.org", phone="", type="family", date="",
                        message="Before the update", hp="", t=12000)
            for i in range(2):
                (inq / f"{i:016x}-{os.urandom(16).hex()}.bin").write_bytes(_legacy_inquiry(pem, dict(good, name=f"Upgrade Visitor {i}")))
            stranger = inq / f"{9:016x}-{os.urandom(16).hex()}.bin"
            stranger.write_bytes(os.urandom(300))
            legacy_left.append(stranger)
            con = A.connect()
            con.execute("DELETE FROM settings WHERE key='upgrade_140'")
            con.execute("UPDATE galleries SET sealed_gen=0 WHERE upload_state='up' AND removed_at IS NULL")
            con.commit(); con.close()
        n_before = _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0]
        if version == "1.3.0":
            import upgrade_140
            with monkeypatch.context() as m, A.app.app_context():
                m.setattr(upgrade_140, "finish", lambda env: False)
                A.app.config["gallery_upgrade_run"]()
            left = sorted(p.name for p in (web / "inquiries").glob("*.bin"))
            assert left == [legacy_left[0].name]
        with A.app.app_context():
            A.app.config["gallery_upgrade_run"]()
            A.app.config["gallery_upgrade_run"]()
        st = json.loads(_q(A, "SELECT value FROM settings WHERE key='upgrade_140'")[0][0])
        assert st.get("finished"), st
        hosts = [galleries] + ([earlier] if earlier else [])
        _check_galleries(A, G, tmp_path, hosts)

        import legacy_130
        import sealed

        keys = {r[3]: (r[0], r[2] or r[1]) for r in _q(A, """SELECT s.gallery_id, g.key, g.ekey, g.link_was FROM galleries g
                                                           JOIN sessions s ON s.id=g.session_id""")}
        assert all(G.is_link(r[0]) for r in _q(A, "SELECT gallery_id FROM sessions"))
        checked = 0
        for p, blob in before.items():
            if p.name in ("picks.bin", "draft.bin") and p.parent.name in keys:
                new, key = keys[p.parent.name]
                try:
                    want = json.loads(legacy_130.open_picks_payload(blob, key, p.name))
                except ValueError:
                    continue
                las = (tmp_path / "galleries-kept" / new / (p.name[:-4] + ".las")).read_bytes()
                got = json.loads(sealed.open_link(p.name[:-4], new, G.key_bytes(key), las))
                assert got.pop("id") and (got == want or got.get("at", "") >= want.get("at", "")), p
                checked += 1
        assert checked or not [p for p in before if p.name in ("picks.bin", "draft.bin")]

        for sid, want in (T.EXPECTED.get("picks") or {}).items():
            got = _q(A, "SELECT favorites, retouch_picks, veto FROM sessions WHERE id=?", (int(sid),))[0]
            assert dict(favorites=got[0] or "", retouch_picks=got[1] or "", veto=got[2] or "") == \
                {k: want[k] or "" for k in ("favorites", "retouch_picks", "veto")}, sid
        n_after = _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0]
        c.post("/gallery/sync", environ_base=E)
        assert _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0] == n_after
        assert n_after >= n_before
        if version == "1.3.0":
            names = [json.loads(r[0])["name"] for r in _q(A, "SELECT fields FROM studio_inquiry_review")]
            assert sorted(n for n in names if n.startswith("Upgrade Visitor")) == ["Upgrade Visitor 0", "Upgrade Visitor 1"]
            assert not list((web / "inquiries").glob("*.bin"))
            assert (web / "inquiry.php").read_text().count("X-Host-Scripts") == 1
            assert (web / "sealed.php").is_file()
            contact = (web / "contact" / "index.html").read_text()
            assert "assets/inquiry.js?v=" in contact
        assert "Updating galleries" not in c.get("/", environ_base=E).get_data(as_text=True)
    finally:
        srv.terminate()
        srv.wait(timeout=10)
