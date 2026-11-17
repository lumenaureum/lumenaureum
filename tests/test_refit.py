import json
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, signed_in_client
from helpers import host_text, host_write
from helpers_tools import need_tool

E = {"REMOTE_ADDR": "127.0.0.1"}
PUBLIC = "https://www.example.com/"

class S:
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
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    srv = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(web)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    import hostcheck
    real = hostcheck._http
    s = S()
    s.stale_page = None

    def http(method, url, headers=None, data=None, timeout=20):
        if s.stale_page is not None and url.endswith("/index.html"):
            return 200, {}, s.stale_page
        return real(method, url.replace(PUBLIC, f"http://127.0.0.1:{port}/"), headers=headers, data=data, timeout=timeout)
    monkeypatch.setattr(hostcheck, "HTTP", http)
    monkeypatch.setattr(hostcheck, "SLEEP", lambda s_: None)
    from PIL import Image
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', ?)", (PUBLIC + "g/",))
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO households (name) VALUES ('Reyes')")
    for hid in (1, 2):
        con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (?, 'family', 'delivered', '2026-10-01')", (hid,))
    con.commit()
    con.close()
    from plugins import gallery as G
    import refit
    s.gids = ["g-galRefitOne1AAAAAAAAAAA", "g-galRefitTwo2AAAAAAAAAAA"]
    for sid, gid in ((1, s.gids[0]), (2, s.gids[1])):
        src = tmp_path / "photos" / "export" / f"s{sid}"
        src.mkdir(parents=True)
        for i, col in enumerate(("red", "blue")):
            Image.new("RGB", (40, 30), col).save(src / f"p{i}.jpg", "JPEG")
        key = G.new_key()
        G.build(gid, src, key, picks=dict(retouch=3, market=2, post=True), credit="1")
        G.upload_gallery(gid, 30, "2099-01-01")
        G.mark_online(gid)
        _x(A, "UPDATE sessions SET gallery_id=? WHERE id=?", (gid, sid))
        _x(A, """INSERT INTO galleries (session_id, key, built_at, n_files, upload_state, uploaded_at, host_fp, sealed_gen,
                 page_set) VALUES (?, ?, ?, 2, 'up', ?, ?, 1, ?)""",
           (sid, key, date.today().isoformat(), date.today().isoformat(), G.host_fingerprint(), refit.fingerprint(G._G())))
    s.A, s.c, s.G, s.web, s.port = A, c, G, web, port
    yield s
    srv.terminate()
    srv.wait(timeout=10)

def _running(A, sid):
    j = A.app.view_functions["build_gallery"].__globals__["JOBS"].get(sid) or {}
    return j.get("state") == "running"

def _wait(cond, n=400):
    for _ in range(n):
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _x(A, sql, args=()):
    con = A.connect()
    con.execute(sql, args)
    con.commit()
    con.close()

def as_previous_release(s, gid):

    h = s.web / "g" / gid
    prev_page = (h / "index.html").read_text().replace("<title>", "<title>(previous release) ", 1)
    (h / "index.html").write_text(prev_page)
    (h / "picks.php").write_text((h / "picks.php").read_text() + "\n// previous release\n")
    _x(s.A, "UPDATE galleries SET page_set='previous' WHERE session_id=(SELECT id FROM sessions WHERE gallery_id=?)", (gid,))
    return prev_page

def refit_now(s, **kw):
    import refit
    with s.A.app.app_context():
        return refit.run_all(_Ctx(s.A), s.G._G(), **kw)

class _Ctx:

    def __init__(self, A):
        self.app, self.q, self.x, self.hooks = A.app, A.q, A.x, A.hooks
        self.audit = A.audit

def test_a_release_with_a_new_page_refits_every_gallery_and_tells_the_plugins(site):
    s = site
    told = []
    s.A.hooks.on.setdefault("host_refit", []).append(told.append)
    for gid in s.gids:
        as_previous_release(s, gid)
    assert refit_now(s) == 2
    G = s.G
    for gid in s.gids:
        h = s.web / "g" / gid
        assert (h / "index.html").read_text() == G.viewer_page(gid, gate=True, credit=True) or \
            G._script_hashes((h / "index.html").read_text()) == G.VIEWER_SCRIPT_HASHES
        assert "(previous release)" not in (h / "index.html").read_text()
        assert (h / "picks.php").read_bytes() == (G.ASSETS / "picks.php").read_bytes()
        assert (G.KEPT_ROOT / gid / "prev" / "picks.php").read_text().endswith("// previous release\n")
    import refit
    fp = refit.fingerprint(G._G())
    assert {r[0] for r in _q(s.A, "SELECT page_set FROM galleries")} == {fp}
    assert told == [G.host_fingerprint()]
    assert refit_now(s) == 0

def test_a_gallery_already_current_is_not_uploaded_again(site, monkeypatch):
    s = site
    _x(s.A, "UPDATE galleries SET page_set='previous'")
    import refit
    puts = []
    real = refit._put
    monkeypatch.setattr(refit, "_put", lambda *a: puts.append(a[3]) or real(*a))
    assert refit_now(s) == 2 and puts == []

def test_a_client_picking_now_is_left_until_quiet_and_do_it_now_goes_ahead(site):
    s = site
    gid = s.gids[0]
    prev = as_previous_release(s, gid)
    _x(s.A, "UPDATE galleries SET draft_at=? WHERE session_id=1", (datetime.now().isoformat(timespec="minutes"),))
    assert refit_now(s) == 0
    assert (s.web / "g" / gid / "index.html").read_text() == prev
    today = s.c.get("/", environ_base=E).get_data(as_text=True)
    assert "wait to be updated while the client is picking" in today or "waits to be updated while the client is picking" in today
    assert "ask them to reload the page" in today
    s.c.post("/session/1/gallery/refit-now", environ_base=E)
    assert "(previous release)" not in (s.web / "g" / gid / "index.html").read_text()

def test_quiet_waits_at_most_a_week(site):
    s = site
    gid = s.gids[0]
    as_previous_release(s, gid)
    _x(s.A, "UPDATE galleries SET draft_at=? WHERE session_id=1", (datetime.now().isoformat(timespec="minutes"),))
    import refit
    refit_now(s)
    con = s.A.connect()
    st = json.loads(con.execute("SELECT value FROM settings WHERE key='gallery_refit'").fetchone()[0])
    st["since"] = time.time() - 8 * 86400
    con.execute("UPDATE settings SET value=? WHERE key='gallery_refit'", (json.dumps(st),))
    con.commit(); con.close()
    assert refit_now(s) == 1

def test_a_failed_canary_puts_the_gallery_back_and_stops_the_host(site, monkeypatch):

    s = site
    prevs = {gid: as_previous_release(s, gid) for gid in s.gids}
    scripts_before = {gid: (s.web / "g" / gid / "picks.php").read_bytes() for gid in s.gids}
    import refit
    with monkeypatch.context() as m:
        m.setattr(refit, "canary", lambda *a: "the photos did not open through the new gate (test)")
        assert refit_now(s) == 0
        for gid in s.gids:
            assert (s.web / "g" / gid / "index.html").read_text() == prevs[gid]
            assert (s.web / "g" / gid / "picks.php").read_bytes() == scripts_before[gid]
        assert "failed its check" not in s.c.get("/", environ_base=E).get_data(as_text=True)
        for _ in range(refit.CANARY_TRIES - 1):
            assert refit_now(s) == 0
        today = s.c.get("/", environ_base=E).get_data(as_text=True)
        assert "The new gallery page failed its check on" in today and "stay on the previous version" in today
        assert "Check again" in today
    assert refit_now(s) == 0
    for gid in s.gids:
        assert (s.web / "g" / gid / "index.html").read_text() == prevs[gid]
    s.c.post("/galleries/refit-retry", data={"host": s.G.host_fingerprint()}, environ_base=E)
    refit_now(s)
    assert {r[0] for r in _q(s.A, "SELECT page_set FROM galleries")} == {refit.fingerprint(s.G._G())}
    assert "The new gallery page failed its check on" not in s.c.get("/", environ_base=E).get_data(as_text=True)

def test_a_passing_canary_failure_is_tried_again_at_the_next_check(site, monkeypatch):

    s = site
    for gid in s.gids:
        as_previous_release(s, gid)
    import refit
    real = refit.canary
    calls = []

    def once_bad(*a):
        calls.append(1)
        return "the photos did not open through the new gate (timed out)" if len(calls) == 1 else real(*a)
    monkeypatch.setattr(refit, "canary", once_bad)
    assert refit_now(s) == 0
    assert "The new gallery page failed its check on" not in s.c.get("/", environ_base=E).get_data(as_text=True)
    assert refit_now(s) == 2
    assert {r[0] for r in _q(s.A, "SELECT page_set FROM galleries")} == {refit.fingerprint(s.G._G())}

def test_a_gallery_its_gate_has_closed_is_never_the_canary(site):

    s = site
    for gid in s.gids:
        as_previous_release(s, gid)
    host_write(s.web / "g" / s.gids[0], "seen.txt", "2025-01-01T10:00:00Z")
    assert refit_now(s) == 2
    import refit
    assert {r[0] for r in _q(s.A, "SELECT page_set FROM galleries")} == {refit.fingerprint(s.G._G())}
    assert "failed its check" not in s.c.get("/", environ_base=E).get_data(as_text=True)

def test_a_rollback_never_puts_back_an_old_limits_txt(site, monkeypatch):

    s = site
    gid = s.gids[0]
    as_previous_release(s, gid)
    import refit
    d = s.web / "g" / gid
    newer = host_text(d, "limits.txt").replace("days=30", "days=60")
    assert newer != host_text(d, "limits.txt")

    def extended_then_failed(*a):
        host_write(d, "limits.txt", newer)
        return "the photos did not open through the new gate (test)"
    monkeypatch.setattr(refit, "canary", extended_then_failed)
    refit_now(s)
    assert host_text(d, "limits.txt") == newer

def test_a_rollback_takes_off_only_the_proof_line_the_refit_added(site, monkeypatch):
    s = site
    gid = s.gids[0]
    as_previous_release(s, gid)
    d = s.web / "g" / gid
    host_write(d, "limits.txt", "\n".join(l_ for l_ in host_text(d, "limits.txt").splitlines() if l_.strip() != "proof=1") + "\n")
    import refit
    monkeypatch.setattr(refit, "canary", lambda *a: "the photos did not open through the new gate (test)")
    refit_now(s)
    assert "proof=1" not in host_text(d, "limits.txt") and "days=" in host_text(d, "limits.txt")

def test_the_canary_checks_like_a_client(site):

    s = site
    as_previous_release(s, s.gids[0])
    import refit
    r = _q(s.A, "SELECT g.*, s.gallery_id, s.id AS sid FROM galleries g JOIN sessions s ON s.id=g.session_id WHERE s.id=1")[0]
    refit_now(s)
    page = (s.web / "g" / s.gids[0] / "index.html").read_bytes()
    with s.A.app.app_context():
        run = refit.Run(s.G._G(), s.A.q, s.A.x, s.A.audit, PUBLIC + "g/", {})
        assert refit.canary(run, r, None, PUBLIC + "g/", page) == ""
        assert "not the new one" in refit.canary(run, r, None, PUBLIC + "g/", page + b" ")

def test_a_cache_still_serving_the_old_page_is_retried_then_shown(site):
    s = site
    gid = s.gids[0]
    prev = as_previous_release(s, gid)
    s.stale_page = prev.encode()
    assert refit_now(s) == 0
    import refit
    con = s.A.connect()
    st = json.loads(con.execute("SELECT value FROM settings WHERE key='gallery_refit'").fetchone()[0])
    assert st["galleries"][gid]["cache_since"]
    assert "A cache in front of your host" not in s.c.get("/", environ_base=E).get_data(as_text=True)
    st["galleries"][gid]["cache_since"] = time.time() - 7 * 3600
    con.execute("UPDATE settings SET value=? WHERE key='gallery_refit'", (json.dumps(st),))
    con.commit(); con.close()
    refit_now(s)
    assert "A cache in front of your host still serves the old gallery page" in s.c.get("/", environ_base=E).get_data(as_text=True)
    s.stale_page = None
    assert refit_now(s) == 1
    assert _q(s.A, "SELECT page_set FROM galleries WHERE session_id=1")[0][0] == refit.fingerprint(s.G._G())
    del refit

def test_a_host_without_php_gets_the_page_only(site, monkeypatch):
    s = site
    import hostcheck
    import refit
    gid = s.gids[0]
    as_previous_release(s, gid)
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="none", version="", wait=0, detail=""))
    puts = []
    real = refit._put
    monkeypatch.setattr(refit, "_put", lambda *a: puts.append(a[3]) or real(*a))
    refit_now(s)
    assert puts == ["index.html"]

def test_php_below_the_floor_is_never_given_new_scripts(site, monkeypatch):
    s = site
    import hostcheck
    import refit
    for gid in s.gids:
        as_previous_release(s, gid)
    monkeypatch.setattr(hostcheck, "probe", lambda host, base: hostcheck.Probe(kind="old", version="7.3", wait=0,
                                                                              detail="Your web host runs PHP 7.3"))
    puts = []
    real = refit._put
    monkeypatch.setattr(refit, "_put", lambda *a: puts.append(a[3]) or real(*a))
    assert refit_now(s) == 0 and puts == []

def test_a_door_that_does_not_close_leaves_the_rest_of_the_host_for_the_next_pass(site, monkeypatch):

    s = site
    for gid in s.gids:
        as_previous_release(s, gid)
    import hostcheck
    asked = []
    monkeypatch.setattr(hostcheck, "door_closed", lambda *a, **k: asked.append(a) or False)
    assert refit_now(s) == 0
    assert len(asked) == 1

def test_a_rolled_back_gallery_gets_its_plain_names_back_for_the_previous_scripts(site, monkeypatch):

    import re
    s = site
    gid = s.gids[0]
    h = s.web / "g" / gid
    as_previous_release(s, gid)
    import hostfiles
    for plain, php in hostfiles.STATE.items():
        if (h / php).exists():
            (h / plain).write_bytes(hostfiles.state_bytes((h / php).read_bytes()))
            (h / php).unlink()
    before = {n: (h / n).read_bytes() for n in ("manifest.bin", "limits.txt")}
    import refit
    monkeypatch.setattr(refit, "canary", lambda *a: "the photos did not open through the new gate (test)")
    refit_now(s)
    assert (h / "manifest.bin").read_bytes() == before["manifest.bin"]
    assert b"days=30" in (h / "limits.txt").read_bytes()
    assert not (h / "limits.php").exists()
    assert not [p.name for p in h.iterdir() if re.fullmatch(r"[0-9a-f]{24}\.bin", p.name)
                and p.read_bytes() == before["manifest.bin"]]
