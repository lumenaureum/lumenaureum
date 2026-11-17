import json
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
COMPAT = ROOT / "tests" / "compat"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
from helpers import fresh_modules, form_d, legacy_picks, seal_picks
from helpers import hosted, host_bytes, host_has
from helpers_tools import need_tool
import snapshot_compat

GID = "g-galCompat14xAAAAAAAAAAA"
MATRIX = json.loads((COMPAT / "matrix.json").read_text())

def _key(v):
    return (9999,) if v == "current" else tuple(int(x) for x in v.split("."))

def expected(page, scripts):
    for p in MATRIX["pairs"]:
        if p["page"] == page and p["scripts"] == scripts:
            return p
    floor = _key(MATRIX["floor"])
    if _key(page) < floor or _key(scripts) < floor:
        return {"result": "never", "why": "below the floor: the 1.4 upgrade replaces both"}
    return {"result": "works" if _key(scripts) >= _key(page) else "never", "why": "the default rule"}

def pages():
    return snapshot_compat.kept("pages") + ["current"]

def script_sets():
    return snapshot_compat.kept("scripts") + ["current"]

def _page_file(version):
    if version == "current":
        return ROOT / "plugins" / "gallery_assets" / "viewer.html"
    return snapshot_compat.resolve("pages", version) / "viewer.html"

def _script(version, name):
    if version == "current":
        return ROOT / snapshot_compat.SCRIPTS[name]
    return snapshot_compat.resolve("scripts", version) / name

def test_the_matrix_is_well_formed_and_every_kept_set_is_whole():
    assert MATRIX["floor"] == "1.4.0"
    for p in MATRIX["pairs"]:
        assert p["result"] in ("works", "refuses", "never") and p.get("why")
        assert p["page"] in pages() and p["scripts"] in script_sets()
    for v in snapshot_compat.kept("pages"):
        d = snapshot_compat.resolve("pages", v)
        assert (d / "viewer.html").is_file() and (d / "inquiry.js").is_file(), v
    for v in snapshot_compat.kept("scripts"):
        d = snapshot_compat.resolve("scripts", v)
        assert {"gate.php", "picks.php", "seen.php", "htaccess"} <= {p.name for p in d.iterdir()}, v

    below = [v for v in snapshot_compat.kept("pages") if _key(v) < _key(MATRIX["floor"])]
    assert below == ["1.3.1"]

@pytest.fixture(scope="module")
def gallery(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("compat")
    import os
    os.environ["CRM_GALLERY_ROOT"] = str(tmp / "galleries")
    fresh_modules()
    from PIL import Image
    from plugins import gallery as G
    src = tmp / "src"
    src.mkdir()
    for i, col in enumerate(("red", "blue")):
        Image.new("RGB", (40, 30), col).save(src / f"p{i}.jpg", "JPEG")
    key = G.new_key()
    out = G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True)).out_dir
    yield G, key, out, tmp
    os.environ.pop("CRM_GALLERY_ROOT", None)

def _serve(php, folder):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(folder.parent)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return p, f"http://127.0.0.1:{port}/{folder.name}/"

def _host(G, out, tmp, page, scripts, name):

    h = tmp / name / GID
    shutil.rmtree(h.parent, ignore_errors=True)
    shutil.copytree(out, h)
    for n in ("gate.php", "picks.php", "seen.php"):
        shutil.copyfile(_script(scripts, n), h / n)
    if _script(scripts, "sealed.php").is_file():
        shutil.copyfile(_script(scripts, "sealed.php"), h / "sealed.php")
    else:
        (h / "sealed.php").unlink(missing_ok=True)
    text = _page_file(page).read_text().replace('<html lang="en">', f'<html lang="en" data-gid="{GID}" data-gate="1">', 1)
    (h / "index.html").write_text(text)
    (h / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\nproof=1\n")
    return h

def _pairs():
    return [(p, s) for p in pages() for s in script_sets()]

@pytest.mark.parametrize("page,scripts", _pairs())
def test_each_pair_does_what_the_matrix_says(gallery, page, scripts):
    G, key, out, tmp = gallery
    want = expected(page, scripts)
    if want["result"] == "never":
        return
    php, node = need_tool("php"), need_tool("node")
    h = _host(G, out, tmp, page, scripts, f"host-{page}-{scripts}".replace(".", "_"))
    srv, base = _serve(php, h)
    try:
        r = subprocess.run([node, str(ROOT / "tests" / "compat_harness.js"), str(h / "index.html"), base, key, GID],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-2000:]
        res = json.loads(r.stdout)
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert res["opened"], (page, scripts, res)
    if want["result"] == "works":
        sent = [c for c in res["calls"] if c["u"] == "picks.php"]
        assert sent and sent[-1]["status"] == 204, res["calls"]
        assert res["sent"].startswith("Sent."), res
        pk = G.open_picks(host_bytes(h, "picks.las"), key, GID)
        assert pk["retouch"] == ["p0.jpg"]
        assert host_has(h, "draft.las")
        assert all(c["set"] for c in res["calls"] if c["u"] in ("picks.php", "seen.php", "gate.php")), res["calls"]
    else:
        assert res["sent"] == want["scenarios"]["send"], res
        assert want["scenarios"]["draft"] in res["tray"], res
        assert not [p.name for p in h.iterdir() if p.name in ("picks.las", "draft.las", "picks.bin", "draft.bin") or p.name.endswith(".tmp")]

def _call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)

def test_every_answer_of_the_current_scripts_carries_the_set(gallery):
    G, key, out, tmp = gallery
    php = need_tool("php")
    h = _host(G, out, tmp, "current", "current", "host-answers")
    srv, base = _serve(php, h)
    import hashlib
    proof = hashlib.sha256(G.key_bytes(key)).hexdigest()[:32]
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        answers = [
            _call(base + "picks.php"),
            _call(base + "picks.php?kind=probe", data=b"", headers=form),
            _call(base + "picks.php", data=form_d(b"x" * 64), headers=form),
            _call(base + "gate.php"),
            _call(base + "gate.php?f=manifest.bin"),
            _call(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof}),
            _call(base + "seen.php", data=b"p=" + proof.encode(), headers=form),
        ]
        assert [a[0] for a in answers] == [405, 400, 403, 404, 403, 200, 204]
        assert all(a[1].get("X-Host-Scripts") == G.HOST_SCRIPTS_SET for a in answers), answers

        from helpers import las_text
        for body, code in ((legacy_picks(key, [{"i": 0, "name": "p0.jpg", "r": True}]), 400),
                           (b"LAS1" + bytes(262144), 413), (las_text(GID), 400),
                           (seal_picks(key, "g-anotherGalleryAAAAAAAAA", [], "picks"), 400)):
            st, hd = _call(base + "picks.php", data=form_d(body), headers=dict(form, **{"X-Proof": proof}))
            assert st == code and hd.get("X-Host-Scripts") == G.HOST_SCRIPTS_SET, (st, code)
        assert not [p.name for p in h.iterdir() if p.name in ("picks.las", "draft.las", "picks.bin", "draft.bin") or p.name.endswith(".tmp")]

        hosted(h, "seen.txt").unlink(missing_ok=True)
        (h / "limits.txt").write_text("days=30\nunopened_until=2001-01-01\nproof=1\n")
        st, hd = _call(base + "picks.php", data=form_d(seal_picks(key, GID, [], "picks")), headers=dict(form, **{"X-Proof": proof}))
        assert st == 410 and hd.get("X-Host-Scripts") == G.HOST_SCRIPTS_SET
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def test_the_1_3_1_scripts_carry_no_set(gallery):

    G, key, out, tmp = gallery
    php = need_tool("php")
    h = _host(G, out, tmp, "1.3.1", "1.3.1", "host-old")
    srv, base = _serve(php, h)
    try:
        st, hd = _call(base + "picks.php?kind=probe", data=b"", headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert st == 400 and "X-Host-Scripts" not in hd
        st, hd = _call(base + "gate.php")
        assert st == 404 and "X-Host-Scripts" not in hd
    finally:
        srv.terminate()
        srv.wait(timeout=10)
