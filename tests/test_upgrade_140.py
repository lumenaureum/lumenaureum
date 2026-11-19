import hashlib
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, form_d, legacy_picks, seal_picks, signed_in_client
from helpers import host_bytes, host_has, host_text, hosted
from helpers_tools import need_tool

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "galUpgrade140"
PUBLIC = "https://www.example.com/"
OLD = ROOT / "tests" / "compat"
T1, T2, T3 = "2026-11-01T10:00:00.000Z", "2026-11-05T10:00:00.000Z", "2026-11-09T10:00:00.000Z"

SEEN = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT09:00:00Z")
PICKS_1 = [{"i": 0, "name": "a.jpg", "k": "n:a.jpg", "r": True, "note": "soften", "m": False, "v": False}]
PICKS_2 = PICKS_1 + [{"i": 1, "name": "b.jpg", "k": "n:b.jpg", "r": False, "note": "", "m": True, "v": False}]
DRAFT = PICKS_2 + [{"i": 2, "name": "c.jpg", "k": "n:c.jpg", "r": False, "note": "", "m": False, "v": True}]

def _old_page(gid, gate=True):

    from plugins import gallery as G
    page = (OLD / "pages" / "1.3.1" / "viewer.html").read_text()
    page = page.replace('<html lang="en">', f'<html lang="en" data-gid="{gid}">', 1)
    if gate:
        page = page.replace('<html lang="en"', '<html lang="en" data-gate="1"', 1)
    return page.replace("<span hidden data-la-logo></span>", G.LA_LOGO, 1)

def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

class Site:
    pass

@pytest.fixture
def site(tmp_path, monkeypatch):
    php = need_tool("php")
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    web = tmp_path / "web"
    (web / "g").mkdir(parents=True)
    (web / "g" / ".vault-root").write_text("")
    from vault import web as VW
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(web / "g")}
    VW.write_config(cfg)
    port = _port()
    srv = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(web)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    import hostcheck
    real = hostcheck._http
    s = Site()
    s.down = False

    def http(method, url, headers=None, data=None, timeout=20):
        if s.down:
            raise OSError("the host can't be reached")
        return real(method, url.replace(PUBLIC, f"http://127.0.0.1:{port}/"), headers=headers, data=data, timeout=timeout)
    monkeypatch.setattr(hostcheck, "HTTP", http)
    monkeypatch.setattr(hostcheck, "SLEEP", lambda s_: None)
    from plugins import gallery as G
    from PIL import Image
    src = tmp_path / "photos" / "export" / "lane"
    src.mkdir(parents=True)
    for i, col in enumerate(("red", "blue", "green")):
        Image.new("RGB", (40, 30), col).save(src / f"{'abc'[i]}.jpg", "JPEG")
    key = G.new_key()
    G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True))
    out = G.OUT_ROOT / GID

    (out / "sealed.php").unlink()
    (out / "index.html").write_text(_old_page(GID))
    for n, src_n in (("gate.php", "gate.php"), ("picks.php", "picks.php"), ("seen.php", "seen.php"), (".htaccess", "htaccess")):
        shutil.copyfile(OLD / "scripts" / "1.3.1" / src_n, out / n)
    host = web / "g" / GID
    host.mkdir()
    for p in out.iterdir():
        if p.is_file() and not p.name.startswith(".online") and p.name != ".download":
            shutil.copyfile(p, host / p.name)
    (host / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\nunopened_ts=4070908799\nwarn_days=7\n"
                                     'contact="hello@studio.example"\nproof=1\n')
    (host / "seen.txt").write_text(SEEN)
    G.mark_online(GID)

    kept = legacy_picks(key, PICKS_1, "picks.bin", T1)
    (G.KEPT_ROOT / GID).mkdir(parents=True)
    (G.KEPT_ROOT / GID / "picks.bin").write_bytes(kept)
    con = A.connect()
    for k, v in (("gallery_base_url", PUBLIC + "g/"), ("business_email", "hello@studio.example")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id, first_visit, retouch_picks)
                   VALUES (1, 'family', 'delivered', '2026-10-01', ?, '2026-10-20', 'a.jpg: soften')""", (GID,))
    con.execute("""INSERT INTO galleries (session_id, key, built_at, n_files, upload_state, uploaded_at, host_fp, link_base,
                   picks_hash, sealed_gen) VALUES (1, ?, ?, 3, 'up', '2026-10-15', ?, ?, ?, 0)""",
                (key, date.today().isoformat(), G.host_fingerprint(), PUBLIC + "g/", hashlib.sha256(kept).hexdigest()))
    con.execute("DELETE FROM settings WHERE key IN ('upgrade_140', 'gallery_refit')")
    con.commit()
    con.close()
    s.A, s.c, s.G, s.key, s.host, s.web, s.port, s.tmp = A, c, G, key, host, web, port, tmp_path
    s.base = f"http://127.0.0.1:{port}/g/{GID}/"
    s.photos = sorted(p.name for p in host.iterdir() if p.name.endswith(".bin"))
    yield s
    srv.terminate()
    srv.wait(timeout=10)

def run(s):
    with s.A.app.app_context():
        return s.A.app.config["gallery_upgrade_run"]()

def q(s, sql, args=()):
    con = s.A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def state(s):
    r = q(s, "SELECT value FROM settings WHERE key='upgrade_140'")
    return json.loads(r[0][0]) if r else {}

def new_id(s):

    gid = q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0]
    return gid if gid != GID else (state(s).get("links") or {}).get("1")

def new_host(s):
    return s.web / "g" / new_id(s)

def send_legacy(s, name="picks.bin", picks=PICKS_2, at=T2):

    blob = legacy_picks(s.key, picks, name, at)
    (s.host / name).write_bytes(blob)
    return blob

def post(url, envelope, proof):
    req = urllib.request.Request(url, data=form_d(envelope), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "X-Proof": proof})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def proof(s):
    return hashlib.sha256(s.G.key_bytes(s.key)).hexdigest()[:32]

def payload(s, name, blob, gid=None):
    import sealed
    return sealed.open_link(name[:-4], gid or new_id(s), s.G.key_bytes(s.key), blob)

def same(a, b):

    x, y = json.loads(a), json.loads(b)
    x.pop("id", None)
    y.pop("id", None)
    return x == y

def legacy_payload(s, blob, name):
    import legacy_130
    return legacy_130.open_picks_payload(blob, s.key, name)

def assert_done(s, picks_payload, draft_payload=None):
    import sealed
    G = s.G
    assert state(s).get("finished"), state(s)
    new = new_id(s)
    assert sealed.LINK_ID.fullmatch(new) and new.startswith("g-") and len(new) == 25
    row = q(s, "SELECT s.gallery_id, g.link_was, g.was_open, g.sealed_gen, g.ekey, g.key FROM sessions s JOIN galleries g "
               "ON g.session_id=s.id WHERE s.id=1")[0]
    assert tuple(row[:4]) == (new, GID, 0, 1)
    nh = s.web / "g" / new
    page = (nh / "index.html").read_text()
    assert page == G.viewer_page(new, gate=True, credit=False, cws=GID)
    assert f'data-cws="{GID}"' in page[:400]
    for n, src in G.HOST_SCRIPTS.items():
        assert (nh / n).read_bytes() == (G.ASSETS / src).read_bytes(), n
    for p in s.photos:
        assert hosted(nh, p, row[4] or row[5]).read_bytes() == (G.OUT_ROOT / new / p).read_bytes(), p
    lim = host_bytes(nh, "limits.txt")
    assert b"proof=1" in lim and b"moved=" not in lim and b"days=30" in lim
    assert host_text(nh, "seen.txt") == SEEN
    assert (nh / "seen-key.php").read_bytes() == (G.OUT_ROOT / new / "seen-key.php").read_bytes()

    assert sorted(p.name for p in s.host.iterdir()) == ["index.html"]
    assert (s.host / "index.html").read_bytes() == G.redirect_page(new)

    assert not list(s.web.rglob("picks.bin")) and not list(s.web.rglob("draft.bin"))
    assert not (G.KEPT_ROOT / GID).exists() and not (G.OUT_ROOT / GID).exists()
    kept = G.KEPT_ROOT / new
    assert not (kept / "picks.bin").exists() and not (kept / "draft.bin").exists()
    got = payload(s, "picks.las", (kept / "picks.las").read_bytes())
    assert same(got, picks_payload) and json.loads(got)["id"]
    assert host_bytes(nh, "picks.las") == (kept / "picks.las").read_bytes()
    if draft_payload is not None:
        assert same(payload(s, "draft.las", host_bytes(nh, "draft.las")), draft_payload)

    with s.A.app.test_request_context():
        s.A.db()
        assert s.A.app.config["gallery_link"](1) == f"{PUBLIC}g/{new}/#k={s.key}"

    s.c.post("/gallery/sync", environ_base=E)
    n = q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0]
    assert n == 1, n
    assert q(s, "SELECT favorites FROM sessions WHERE id=1")[0][0] == "b.jpg"

def test_the_upgrade_moves_the_gallery_and_carries_everything_over(site):
    s = site
    picks = send_legacy(s)
    draft = send_legacy(s, "draft.bin", DRAFT, T3)
    run(s)
    assert_done(s, legacy_payload(s, picks, "picks.bin"), legacy_payload(s, draft, "draft.bin"))
    assert tuple(q(s, "SELECT retouch_picks, favorites FROM sessions WHERE id=1")[0]) == ("a.jpg: soften", "b.jpg")
    new = new_id(s)
    nbase = f"http://127.0.0.1:{s.port}/g/{new}/"

    newer = seal_picks(s.key, new, DRAFT, "picks", "2026-11-12T10:00:00.000Z")
    assert post(nbase + "picks.php", newer, proof(s)) == 204
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"

    assert post(nbase + "picks.php", seal_picks_old(s), proof(s)) == 400
    assert post(s.base + "picks.php", legacy_picks(s.key, PICKS_1), proof(s)) != 204
    assert sorted(p.name for p in s.host.iterdir()) == ["index.html"]
    today = s.c.get("/", environ_base=E).get_data(as_text=True)
    assert "Updating galleries" not in today
    assert q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'The gallery moved to its new address%'")[0][0] == 1

def seal_picks_old(s):

    import sealed
    body = json.dumps({"v": 1, "id": "0" * 32, "at": T3, "picks": DRAFT}).encode()
    g = GID.encode()
    binding = b"LAS1" + bytes([1, 1, len(g)]) + g
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = bytes(12)
    return binding + nonce + AESGCM(sealed._key(s.G.key_bytes(s.key), b"", binding)).encrypt(nonce, body, binding)

def test_the_build_folder_and_kept_copies_move_to_the_link_id(site):
    s = site
    run(s)
    new = new_id(s)
    out = s.G.OUT_ROOT / new
    assert (out / "index.html").read_text() == s.G.viewer_page(new, gate=True, credit=False, cws=GID)
    assert (out / "sealed.php").is_file() and (out / s.G.ONLINE_MARK).is_file()
    assert not (s.G.OUT_ROOT / GID).exists()
    assert q(s, "SELECT link_was FROM galleries WHERE session_id=1")[0][0] == GID

def test_picks_sealed_for_the_old_id_by_a_14_build_are_carried_over(site):

    s = site
    (s.host / "picks.las").write_bytes(seal_picks_old(s))
    run(s)
    new = new_id(s)
    got = json.loads(payload(s, "picks.las", (s.G.KEPT_ROOT / new / "picks.las").read_bytes()))
    assert got["picks"] == DRAFT and got["at"] == T3
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"
    assert sorted(p.name for p in s.host.iterdir()) == ["index.html"]

class Stop(BaseException):
    pass

STEPS = ["probe", "built", "committed", "door", "shut", "read", "redirect", "emptied"]

def _stop_after(monkeypatch, step):

    import hostcheck
    import upgrade_140
    if step == "probe":
        monkeypatch.setattr(hostcheck, "probe", lambda *a, **k: (_ for _ in ()).throw(Stop()))
        return
    if step == "shut":
        real_fc = upgrade_140.take_in

        def take_in(env, r, host, folder, put_host=None):
            if folder == GID and put_host is not None:
                raise Stop()
            return real_fc(env, r, host, folder, put_host)
        monkeypatch.setattr(upgrade_140, "take_in", take_in)
        return
    name = {"built": "build_new", "committed": "relink", "door": "door", "read": "take_in", "redirect": "put_redirect",
            "emptied": "empty_old"}[step]
    real = getattr(upgrade_140, name)

    def stop(*a, **k):
        out = real(*a, **k)
        if name != "take_in" or k.get("put_host") is not None or (len(a) > 4 and a[4] is not None):
            raise Stop()
        return out
    monkeypatch.setattr(upgrade_140, name, stop)

@pytest.mark.parametrize("step", STEPS)
def test_stopping_after_any_step_and_running_again_ends_the_same(site, monkeypatch, step):
    s = site
    picks = send_legacy(s)
    draft = send_legacy(s, "draft.bin", DRAFT, T3)
    with monkeypatch.context() as m:
        _stop_after(m, step)
        with pytest.raises(Stop):
            run(s)
    if step in ("door", "shut", "read", "redirect"):

        assert post(s.base + "picks.php", legacy_picks(s.key, PICKS_1), proof(s)) == 503
        assert b"moved=" not in host_bytes(s.host, "limits.txt")
        assert (s.host / "picks.bin").read_bytes() == picks
    if step in ("door", "shut", "read"):
        assert (s.host / "index.html").read_text() == _old_page(GID)
    if step in ("probe", "built"):
        assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
        assert (s.host / "index.html").read_text() == _old_page(GID)
    run(s)
    assert_done(s, legacy_payload(s, picks, "picks.bin"), legacy_payload(s, draft, "draft.bin"))

def test_an_unreachable_host_waits_and_today_says_so(site):
    s = site
    picks = send_legacy(s)
    s.down = True
    run(s)
    assert tuple(q(s, "SELECT sealed_gen, link_was FROM galleries WHERE session_id=1")[0]) == (0, None)
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    assert (s.host / "picks.php").read_bytes() == (OLD / "scripts" / "1.3.1" / "picks.php").read_bytes()
    assert (s.host / "picks.bin").read_bytes() == picks
    today = s.c.get("/", environ_base=E).get_data(as_text=True)
    assert "Updating galleries: 0 of 1 done." in today and "cannot be reached" in today
    assert "1 waits for" in today and "1 wait for" not in today
    assert "Saying it is gone for good gives up any picks clients sent there that the app has not read." in today
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT favorites FROM sessions WHERE id=1")[0][0] == "b.jpg"
    assert (s.host / "picks.bin").read_bytes() == picks
    s.down = False
    run(s)
    assert_done(s, legacy_payload(s, picks, "picks.bin"))
    assert "Updating galleries" not in s.c.get("/", environ_base=E).get_data(as_text=True)

def test_php_below_the_floor_leaves_the_gallery_on_13(site, monkeypatch):
    s = site
    import hostcheck
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="old", version="7.2", wait=0,
                        detail="Your web host runs PHP 7.2; Lumen Aureum needs 7.4 or later"))
    run(s)
    assert q(s, "SELECT sealed_gen FROM galleries WHERE session_id=1")[0][0] == 0
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    assert (s.host / "picks.php").read_bytes() == (OLD / "scripts" / "1.3.1" / "picks.php").read_bytes()
    assert "PHP 7.2; Lumen Aureum needs 7.4 or later" in s.c.get("/", environ_base=E).get_data(as_text=True)

def test_a_failed_canary_leaves_the_client_where_they_were(site, monkeypatch):
    s = site
    import refit
    real = refit.canary
    monkeypatch.setattr(refit, "canary", lambda *a, **k: "the photos did not open through the new gate (test)")
    run(s)
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    assert (s.host / "index.html").read_text() == _old_page(GID)
    assert "photos did not open" in s.c.get("/", environ_base=E).get_data(as_text=True)
    monkeypatch.setattr(refit, "canary", real)
    run(s)
    assert state(s).get("finished")

def test_picks_sent_to_the_new_folder_while_the_old_one_is_emptied_are_recorded_once(site, monkeypatch):
    s = site
    send_legacy(s)
    with monkeypatch.context() as m:
        _stop_after(m, "redirect")
        with pytest.raises(Stop):
            run(s)
    new = new_id(s)
    newer = seal_picks(s.key, new, DRAFT, "picks", "2026-11-12T10:00:00.000Z")
    assert post(f"http://127.0.0.1:{s.port}/g/{new}/picks.php", newer, proof(s)) == 204
    run(s)
    assert host_bytes(new_host(s), "picks.las") == newer
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"
    assert q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0] == 2
    assert state(s).get("finished") and not (s.host / "picks.bin").exists()
    assert payload(s, "picks.las", (s.G.KEPT_ROOT / new / "picks.las").read_bytes()) == payload(s, "picks.las", newer)

def test_the_old_folder_is_carried_over_before_it_forwards_anyone(site, monkeypatch):

    s = site
    send_legacy(s)
    import upgrade_140
    real = upgrade_140.take_in
    pages = []

    def take_in(env, r, host, folder, put_host=None):
        if folder == GID and put_host is not None:
            pages.append((s.host / "index.html").read_bytes())
        return real(env, r, host, folder, put_host)
    monkeypatch.setattr(upgrade_140, "take_in", take_in)
    run(s)
    assert pages and all(b"data-to=" not in p for p in pages)
    assert state(s).get("finished") and (s.host / "index.html").read_bytes() == s.G.redirect_page(new_id(s))

def test_a_carried_item_never_replaces_one_a_client_just_sent_to_the_new_folder(site, monkeypatch):

    s = site
    send_legacy(s)
    import upgrade_140
    G = s.A.app.config["gallery_upgrade_run"].__globals__
    real_read, real_take = G["_read"], upgrade_140.take_in
    box = {}

    def take_in(env, r, host, folder, put_host=None):
        box["armed"] = folder == GID and put_host is not None
        try:
            return real_take(env, r, host, folder, put_host)
        finally:
            box["armed"] = False

    def _read(host, path):
        out = real_read(host, path)
        if box.get("armed") and "newer" not in box and path.endswith("/picks.las") and path.startswith("g-"):
            new = path.split("/", 1)[0]
            box["newer"] = seal_picks(s.key, new, DRAFT, "picks", "2026-11-12T10:00:00.000Z")
            (s.web / "g" / new / "picks.las").write_bytes(box["newer"])
        return out
    monkeypatch.setitem(G, "_read", _read)
    monkeypatch.setattr(upgrade_140, "take_in", take_in)
    run(s)
    assert "newer" in box and state(s).get("finished")
    assert host_bytes(new_host(s), "picks.las") == box["newer"]
    assert payload(s, "picks.las", (s.G.KEPT_ROOT / new_id(s) / "picks.las").read_bytes()) == \
        payload(s, "picks.las", box["newer"])
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"

def test_pieces_of_a_one_time_download_fetched_at_the_old_folder_count_at_the_new_one(site, monkeypatch):

    s = site
    piece_a, piece_b = "a" * 24 + ".bin", "b" * 24 + ".bin"
    lim = host_text(s.host, "limits.txt")
    (s.host / "limits.txt").write_text(lim + f"once={piece_a},{piece_b}\n")
    (s.host / "once.txt").write_text(piece_a + "\n")
    import upgrade_140
    real = upgrade_140.build_new

    def build_new(*a, **k):
        out = real(*a, **k)
        (s.host / "once.txt").write_text(piece_a + "\n" + piece_b + "\n")
        return out
    monkeypatch.setattr(upgrade_140, "build_new", build_new)
    run(s)
    assert state(s).get("finished")
    assert host_text(new_host(s), "once.txt").split() == [piece_a, piece_b]

def _forward_to(s, to, manifest):

    d = s.web / "g" / to
    d.mkdir()
    (d / "index.html").write_text(s.G.viewer_page(to, gate=True, credit=False, cws=GID))
    (d / "manifest.bin").write_bytes(manifest)
    (s.host / "index.html").write_bytes(s.G.redirect_page(to))
    return d

def test_a_forward_to_a_folder_that_is_not_this_gallery_is_never_adopted(site):

    s = site
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    other = s.G.new_key()
    nonce = os.urandom(12)
    foreign = nonce + AESGCM(s.G.key_bytes(other)).encrypt(nonce, b"{}", b"lumen-aureum-gallery:manifest.bin")
    to = "g-someoneElsesFolder12345"
    d = _forward_to(s, to, foreign)
    before = sorted((p.name, p.read_bytes()) for p in d.iterdir())
    run(s)
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    assert (state(s).get("links") or {}).get("1") != to
    assert (state(s).get("waits") or {}).get(GID, {}).get("why") == "forward"
    assert sorted((p.name, p.read_bytes()) for p in d.iterdir()) == before
    assert "this gallery; check that folder on the host" in s.c.get("/", environ_base=E).get_data(as_text=True)

def test_a_forward_to_another_gallerys_link_id_is_never_adopted(site):

    s = site
    to = "g-anotherSessionsLink1234"
    d = _forward_to(s, to, (s.host / "manifest.bin").read_bytes())
    con = s.A.connect()
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id) VALUES (1, 'family', 'booked', "
                "'2026-12-01', ?)", (to,))
    con.commit(); con.close()
    before = sorted((p.name, p.read_bytes()) for p in d.iterdir())
    run(s)
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    assert q(s, "SELECT COUNT(*) FROM sessions WHERE gallery_id=?", (to,))[0][0] == 1
    assert (state(s).get("waits") or {}).get(GID, {}).get("why") == "forward"
    assert sorted((p.name, p.read_bytes()) for p in d.iterdir()) == before

def test_a_file_that_changed_after_it_was_read_is_never_deleted_unread(site, monkeypatch):

    s = site
    send_legacy(s)
    import upgrade_140
    real = upgrade_140.empty_old
    later = legacy_picks(s.key, DRAFT, "picks.bin", T3)

    def empty_old(env, r, host, folder, keep_page, read, deletes=True):
        (s.host / "picks.bin").write_bytes(later)
        return real(env, r, host, folder, keep_page, read, deletes)
    monkeypatch.setattr(upgrade_140, "empty_old", empty_old)
    run(s)
    assert not (s.host / "picks.bin").exists()
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"
    assert same(payload(s, "picks.las", (s.G.KEPT_ROOT / new_id(s) / "picks.las").read_bytes()),
                legacy_payload(s, later, "picks.bin"))

def test_a_file_that_is_not_ours_is_never_deleted_unread(site):

    s = site
    (s.host / "draft.bin").write_bytes(b"LAK1" + bytes(64))
    run(s)
    assert (s.host / "draft.bin").read_bytes() == b"LAK1" + bytes(64)
    assert sorted(p.name for p in s.host.iterdir()) == [".htaccess", "draft.bin", "index.html"]
    assert q(s, "SELECT was_open FROM galleries WHERE session_id=1")[0][0] == 0 and state(s).get("finished")

def test_an_upload_of_a_13_gallery_moves_it_first(site):
    s = site
    send_legacy(s)
    s.G.build(GID, s.tmp / "photos" / "export" / "lane", s.key, picks=dict(retouch=3, market=2, post=True))
    s.c.post("/session/1/gallery/upload", environ_base=E)
    for _ in range(200):
        gid = q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0]
        if gid != GID and s.G.online_is_current(gid):
            break
        time.sleep(0.05)
    new = q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0]
    assert new != GID and s.G.is_link(new) and s.G.online_is_current(new)
    assert q(s, "SELECT favorites FROM sessions WHERE id=1")[0][0] == "b.jpg"
    assert not (s.host / "picks.bin").exists()
    assert (s.host / "index.html").read_bytes() == s.G.redirect_page(new)
    built = sorted(p.name for p in (s.G.OUT_ROOT / new).iterdir() if p.name.endswith(".bin"))
    assert all(host_has(new_host(s), n, s.key) for n in built)

def test_a_page_from_before_11_never_gets_proof_in_its_old_folder(site, monkeypatch):

    s = site
    (s.host / "index.html").write_text(_old_page(GID).replace("keyProof", "keyQroof"))
    (s.host / "limits.txt").write_text("days=30\nunopened_until=2099-01-01\n")
    import upgrade_140
    real = upgrade_140.door
    seen = []

    def door(env, host, base, folder, check, scripts=True, mark=b"moved=1"):
        out = real(env, host, base, folder, check, scripts, mark)
        seen.append(host_text(s.host, "limits.txt"))
        return out
    monkeypatch.setattr(upgrade_140, "door", door)
    run(s)
    assert seen and "proof=1" not in seen[0] and "updating=1" in seen[0]
    assert "proof=1" in host_text(new_host(s), "limits.txt")

def test_a_restored_database_finds_its_moved_gallery_without_a_second_carry_over(site):
    s = site
    send_legacy(s)
    con = s.A.connect()
    con.execute("CREATE TABLE before_up AS SELECT * FROM galleries")
    con.commit(); con.close()
    run(s)
    new = new_id(s)
    las = (s.G.KEPT_ROOT / new / "picks.las").read_bytes()
    con = s.A.connect()
    con.execute("UPDATE sessions SET gallery_id=? WHERE id=1", (GID,))
    con.execute("UPDATE galleries SET sealed_gen=0, link_was=NULL, was_open=0, was_fp=NULL WHERE session_id=1")
    con.execute("DELETE FROM settings WHERE key IN ('upgrade_140', 'gallery_refit')")
    con.commit(); con.close()
    shutil.move(str(s.G.OUT_ROOT / new), str(s.G.OUT_ROOT / GID))
    shutil.move(str(s.G.KEPT_ROOT / new), str(s.G.KEPT_ROOT / GID))
    run(s)
    assert state(s).get("finished") and new_id(s) == new
    assert (s.G.KEPT_ROOT / new / "picks.las").read_bytes() == las
    assert sorted(p.name for p in s.host.iterdir()) == ["index.html"]
    assert q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0] == 1

def test_with_galleries_switched_off_the_form_part_still_finishes(site):
    s = site
    import upgrade_140

    class Ctx:
        app, q, x, audit, hooks = s.A.app, s.A.q, s.A.x, s.A.audit, s.A.hooks
    with s.A.app.app_context():
        assert upgrade_140.run(Ctx, None) == "The inquiry form is updated."
    assert state(s).get("finished")

def test_a_gallery_not_online_takes_its_link_id_with_its_picks(site):
    s = site
    con = s.A.connect()
    con.execute("UPDATE galleries SET upload_state='removed', removed_at='2026-11-01' WHERE session_id=1")
    con.commit(); con.close()
    legacy = (s.G.KEPT_ROOT / GID / "picks.bin").read_bytes()
    import upgrade_140

    class Ctx:
        app, q, x, audit, hooks, connect = s.A.app, s.A.q, s.A.x, s.A.audit, s.A.hooks, staticmethod(s.A.connect)
    with s.A.app.app_context():
        upgrade_140.ensure(Ctx, s.G._G(), 1)
    new = q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0]
    assert s.G.is_link(new) and tuple(q(s, "SELECT link_was, was_open FROM galleries WHERE session_id=1")[0]) == (GID, 0)
    kept = s.G.KEPT_ROOT / new / "picks.las"
    assert kept.is_file() and same(payload(s, "picks.las", kept.read_bytes()), legacy_payload(s, legacy, "picks.bin"))

def test_every_session_gets_a_link_id(site):
    s = site
    con = s.A.connect()
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id) VALUES
                   (1, 'family', 'inquiry', '2026-12-01', 'Ab3dEf6hIj9k')""")
    con.commit(); con.close()
    run(s)
    assert all(s.G.is_link(r[0]) for r in q(s, "SELECT gallery_id FROM sessions"))
    with s.A.app.test_request_context():
        s.A.db()
        sid = s.A.create_session(1, "family")
        s.A.g.db.commit()
    gid = q(s, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert s.G.is_link(gid) and gid.startswith("g-")

def _waiting(s, monkeypatch):

    import hostcheck
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="old", version="7.2", wait=0,
                        detail="Your web host runs PHP 7.2; Lumen Aureum needs 7.4 or later"))
    run(s)
    assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
    return send_legacy(s)

def _picks_in(s):
    return q(s, "SELECT favorites FROM sessions WHERE id=1")[0][0] == "b.jpg"

def test_each_check_reads_a_waiting_gallerys_picks_and_deletes_nothing(site, monkeypatch):
    s = site
    picks = _waiting(s, monkeypatch)
    s.c.post("/gallery/sync", environ_base=E)
    assert _picks_in(s)
    assert (s.host / "picks.bin").read_bytes() == picks
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0] == 1
    newer = send_legacy(s, picks=DRAFT, at=T3)
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"
    assert (s.host / "picks.bin").read_bytes() == newer

def _no_check_read(monkeypatch):

    import upgrade_140
    monkeypatch.setattr(upgrade_140, "read_waiting", lambda *a, **k: None)

def test_going_dark_reads_a_waiting_gallerys_picks_first(site, monkeypatch):
    s = site
    _waiting(s, monkeypatch)
    _no_check_read(monkeypatch)
    from datetime import timedelta
    old = (date.today() - timedelta(days=400)).isoformat()
    con = s.A.connect()
    con.execute("UPDATE sessions SET first_visit=?, sd_shipped=? WHERE id=1", (old, old))
    con.commit(); con.close()
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT upload_state FROM galleries WHERE session_id=1")[0][0] == "removed"
    assert not (s.host / "picks.bin").exists()
    assert _picks_in(s)

def test_a_take_down_reads_a_waiting_gallerys_picks_first(site, monkeypatch):
    s = site
    _waiting(s, monkeypatch)
    _no_check_read(monkeypatch)
    with s.A.app.test_request_context():
        s.A.app.config["gallery_take_down"](1)
        s.A.db().commit()
    assert not (s.host / "picks.bin").exists()
    assert _picks_in(s)

def test_removing_a_copy_reads_a_waiting_gallerys_picks_first(site, monkeypatch):

    s = site
    _waiting(s, monkeypatch)
    _no_check_read(monkeypatch)
    G = s.A.app.config["gallery_module"]
    assert G.remove_from(GID, G.gallery_host()) > 0
    assert not (s.host / "picks.bin").exists()
    assert _picks_in(s)

def test_a_legacy_read_that_fails_stops_the_delete(site, monkeypatch):
    s = site
    picks = _waiting(s, monkeypatch)
    _no_check_read(monkeypatch)
    from vault import backends as VB
    real = VB.LocalBackend.get_stream

    def broken(self, name, w):
        if name.endswith("/picks.bin"):
            raise OSError("the host dropped the connection")
        return real(self, name, w)
    monkeypatch.setattr(VB.LocalBackend, "get_stream", broken)
    with pytest.raises(Exception):
        s.A.app.config["gallery_module"].delete_gallery(GID)
    assert (s.host / "picks.bin").read_bytes() == picks and (s.host / "index.html").exists()

def test_picks_from_a_page_before_11_win_a_tie_with_an_older_kept_copy(site):

    s = site
    kept = legacy_picks(s.key, PICKS_1, "picks.bin", None)
    (s.G.KEPT_ROOT / GID / "picks.bin").write_bytes(kept)
    con = s.A.connect()
    con.execute("UPDATE galleries SET picks_hash=? WHERE session_id=1", (hashlib.sha256(kept).hexdigest(),))
    con.commit(); con.close()
    picks = send_legacy(s, picks=PICKS_2, at=None)
    run(s)
    assert_done(s, legacy_payload(s, picks, "picks.bin"))

def test_a_copy_moved_to_an_earlier_host_before_14_is_read_there(site):

    s = site
    G = s.A.app.config["gallery_module"]
    old_dir = s.tmp / "oldhost"
    shutil.copytree(s.host, old_dir / GID)
    (old_dir / ".vault-root").write_text("")
    (old_dir / GID / "limits.txt").write_text(host_text(old_dir / GID, "limits.txt") + "moved=1\n")
    later = legacy_picks(s.key, DRAFT, "picks.bin", T3)
    (old_dir / GID / "picks.bin").write_bytes(later)
    from vault import web as VW
    cfg = VW.config()
    old_cfg = {"type": "local", "path": str(old_dir)}
    cfg.setdefault("gallery_hosts_prev", []).append(old_cfg)
    VW.write_config(cfg)
    con = s.A.connect()
    con.execute("UPDATE galleries SET moved_from=? WHERE session_id=1", (G.fingerprint_of(old_cfg),))
    con.commit(); con.close()
    run(s)
    new = new_id(s)
    assert same(payload(s, "picks.las", (s.G.KEPT_ROOT / new / "picks.las").read_bytes()), legacy_payload(s, later, "picks.bin"))

    assert (old_dir / GID / "picks.bin").read_bytes() == later and (old_dir / GID / "index.html").exists()
    assert q(s, "SELECT COUNT(*) FROM audit WHERE action LIKE 'What the client sent to the copy on % stay there until "
                "that copy comes down%'")[0][0] == 1
    s.c.post("/gallery/sync", environ_base=E)
    assert q(s, "SELECT veto FROM sessions WHERE id=1")[0][0] == "c.jpg"
    assert state(s).get("finished")

    with s.A.app.test_request_context():
        s.A.db()
        G.remove_from(new, G.open_host(old_cfg))
    assert not (old_dir / GID).exists()

def test_a_host_without_php_now_still_has_its_legacy_picks_read(site, monkeypatch):

    s = site
    picks = send_legacy(s)
    import hostcheck
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="none", version="", wait=0, detail=""))
    run(s)
    new = new_id(s)
    assert tuple(q(s, "SELECT gallery_id, link_was FROM sessions s JOIN galleries g ON g.session_id=s.id")[0]) == (new, GID)
    assert _picks_in(s)
    assert same(payload(s, "picks.las", (s.G.KEPT_ROOT / new / "picks.las").read_bytes()), legacy_payload(s, picks, "picks.bin"))
    assert not (s.host / "picks.bin").exists()
    assert not (new_host(s) / "picks.php").exists()
    assert (s.host / "index.html").read_bytes() == s.G.redirect_page(new)

def test_the_old_folder_leads_to_the_dark_page_then_goes_with_it(site):
    s = site
    run(s)
    new = new_id(s)
    G = s.A.app.config["gallery_module"]
    with s.A.app.test_request_context():
        s.A.db()
        G.go_dark(new, "Sample Studio", "hello@studio.example")
    assert (s.host / "index.html").read_bytes() == G.redirect_page(new)
    assert b"gone dark" in (new_host(s) / "index.html").read_bytes()
    with s.A.app.test_request_context():
        s.A.db()
        G.clear_dark(new)
    assert not s.host.exists() and not new_host(s).exists()

def test_a_take_down_removes_the_old_folder_too(site):
    s = site
    run(s)
    with s.A.app.test_request_context():
        s.A.app.config["gallery_take_down"](1)
        s.A.db().commit()
    assert not s.host.exists() and not new_host(s).exists()

def test_put_back_online_the_old_link_leads_to_it_again(site):
    s = site
    run(s)
    new = new_id(s)
    G = s.A.app.config["gallery_module"]
    with s.A.app.test_request_context():
        s.A.db()
        G.delete_gallery(new)
    assert not s.host.exists()
    G.upload_gallery(new, 30, "2099-01-01")
    assert (s.host / "index.html").read_bytes() == G.redirect_page(new)

def test_a_codeword_gallery_keeps_its_key_and_a_rebuild_keeps_it_too(site):
    s = site
    G = s.G
    word = "amber birch cedar delta ember"
    ekey = G.codeword_key(s.key, GID, word)
    con = s.A.connect()
    con.execute("UPDATE galleries SET ekey=? WHERE session_id=1", (ekey,))
    con.execute("UPDATE sessions SET gallery_codeword=? WHERE id=1", (word,))
    con.commit(); con.close()
    run(s)
    new = new_id(s)
    page = (new_host(s) / "index.html").read_text()
    assert f'data-gid="{new}"' in page[:400] and f'data-cws="{GID}"' in page[:400]
    with s.A.app.test_request_context():
        s.A.db()
        assert s.A.app.config["gallery_link"](1).endswith("&c=1")
    hid = 1
    view = s.c.get(f"/c/{hid}?s=1", environ_base=E).get_data(as_text=True)
    assert "The codeword changed after this build" not in view
    assert q(s, "SELECT ekey FROM galleries WHERE session_id=1")[0][0] == ekey
    assert G.codeword_key(s.key, G.cw_id({"link_was": GID}, new), word) == ekey
