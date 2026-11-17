import json
import re
import time
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import gallery_key, host_names, host_bytes, host_has, host_text, host_write
from test_gallery_expiry import E, GID, env, go_live, days_ago
from test_gallery_php_111 import host as php_host

def settings(A, **kv):
    con = A.connect()
    for k, v in kv.items():
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    con.commit(); con.close()

def one(A, sql):
    con = A.connect()
    r = con.execute(sql).fetchone()
    con.close()
    return r

def one_all(A, sql):
    con = A.connect()
    r = con.execute(sql).fetchall()
    con.close()
    return r

def test_the_warning_is_a_week_or_a_quarter_of_a_short_window():
    from plugins import gallery as G
    assert [G.sunset_days(d) for d in (60, 30, 14, 7, 1)] == [7, 7, 4, 2, 1]

def test_the_host_deadline_carries_the_warning_and_the_photographers_email(env):
    A, c, G, host = env
    settings(A, business_email="hello@studio.example")
    go_live(A, G, days_ago(1))
    c.post("/gallery/sync", environ_base=E)
    lim = host_text(host / GID, "limits.txt")
    assert "warn_days=7\n" in lim and 'contact="hello@studio.example"\n' in lim
    settings(A, business_email='bad"address@x.y')
    c.post("/gallery/sync", environ_base=E)
    lim = host_text(host / GID, "limits.txt")
    assert "contact" not in lim and "warn_days=7\n" in lim

def test_an_email_changed_in_settings_reaches_the_host(env):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    settings(A, business_email="new@studio.example")
    c.post("/gallery/sync", environ_base=E)
    assert 'contact="new@studio.example"' in host_text(host / GID, "limits.txt")
    assert one(A, "SELECT limits_contact FROM galleries WHERE session_id=1")[0] == "new@studio.example"

def test_an_expired_gallery_goes_dark_with_the_studio_and_its_email_only(env):
    A, c, G, host = env
    settings(A, business_email="hello@studio.example", business_name="Lark & Pine Studio")
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    page = (host / GID / "index.html").read_text()
    assert [p.name for p in (host / GID).iterdir()] == ["index.html"]
    assert "This gallery has gone dark." in page and "Your photographer, Lark &amp; Pine Studio, may still be able to help" in page
    assert 'href="mailto:hello@studio.example"' in page and 'aria-label="Lumen Aureum"' in page
    assert "Lane" not in page and "<script" not in page
    assert one(A, "SELECT dark_at FROM galleries WHERE session_id=1")[0] == date.today().isoformat()
    assert "its link now says the gallery has gone dark" in one(A, "SELECT GROUP_CONCAT(action, ' | ') FROM audit")[0]

def test_the_dark_page_goes_after_a_year(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    assert (host / GID / "index.html").exists()
    con = A.connect()
    con.execute("UPDATE galleries SET dark_at=? WHERE session_id=1", (days_ago(G.DARK_KEEP_DAYS - 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert (host / GID / "index.html").exists()
    con = A.connect()
    con.execute("UPDATE galleries SET dark_at=? WHERE session_id=1", (days_ago(G.DARK_KEEP_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert not (host / GID).exists() and one(A, "SELECT dark_at FROM galleries WHERE session_id=1")[0] is None

def test_the_dark_page_escapes_the_studio_and_drops_an_odd_email():
    from plugins import gallery as G
    page = G.dark_page("<b>Studio</b>", "x\"><script>@a.b").decode()
    assert "&lt;b&gt;Studio&lt;/b&gt;" in page and "mailto" not in page and "<script>" not in page

def get(url, proof=None):
    req = urllib.request.Request(url, headers={"X-Proof": proof} if proof else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def test_gate_says_when_it_sunsets_only_in_the_last_days_and_only_to_the_key_holder(php_host):
    base, d, proof, G, key = php_host
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\nwarn_days=7\n")
    opened = datetime.now(timezone.utc) - timedelta(days=10)
    host_write(d, "seen.txt", opened.strftime("%Y-%m-%dT%H:%M:%SZ"))
    code, h, _ = get(base + "gate.php?f=manifest.bin", proof)
    assert code == 200 and "X-Sunset" not in h
    opened = datetime.now(timezone.utc) - timedelta(days=25)
    host_write(d, "seen.txt", opened.strftime("%Y-%m-%dT%H:%M:%SZ"))
    code, h, _ = get(base + "gate.php?f=manifest.bin", proof)
    assert code == 200 and h["X-Sunset"] == (opened + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    code, h, _ = get(base + "gate.php?f=manifest.bin")
    assert code == 403 and "X-Sunset" not in h

def test_gate_names_the_photographers_email_once_the_gallery_has_gone_dark(php_host):
    base, d, proof, G, key = php_host
    host_write(d, "seen.txt", "2026-01-01T12:00:00Z")
    host_write(d, "limits.txt", 'days=30\nunopened_until=2099-01-01\nproof=1\ncontact="hello@studio.example"\n')
    code, h, body = get(base + "gate.php?f=manifest.bin")
    assert code == 410 and json.loads(body) == {"contact": "hello@studio.example"}
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\n")
    code, h, body = get(base + "gate.php?f=manifest.bin")
    assert code == 410 and body == b""

def test_a_gallery_that_went_dark_can_be_put_back_online_for_its_days_from_today(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    assert [p.name for p in (host / GID).iterdir()] == ["index.html"]
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Put back online" in page and 'aria-label="More actions for this gallery"' in page
    assert "For 7 days" in page and "For 14 days" in page and f"For {A.C.GALLERY_DAYS} days" in page
    assert "For one download" not in page
    c.post("/session/1/gallery/back-online", data={"days": str(A.C.GALLERY_DAYS)}, environ_base=E)
    for _ in range(100):
        r = one(A, "SELECT upload_state, restored_at, removed_at, dark_at FROM galleries WHERE session_id=1")
        if r[0] in ("up", "error"):
            break
        time.sleep(0.05)
    until = (date.today() + timedelta(days=A.C.GALLERY_DAYS)).isoformat()
    assert tuple(r) == ("up", date.today().isoformat(), None, None)
    assert "manifest.bin" in host_names(host / GID, gallery_key(A))
    assert f"until={until}\n" in host_text(host / GID, "limits.txt")
    assert "<title>This gallery has gone dark</title>" not in (host / GID / "index.html").read_text()
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    con = A.connect()
    con.execute("UPDATE galleries SET restored_at=? WHERE session_id=1", (days_ago(A.C.GALLERY_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    assert [p.name for p in (host / GID).iterdir()] == ["index.html"]

def test_only_a_gallery_that_came_down_can_be_put_back(env):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    assert "Put back online" not in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    c.post("/session/1/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert one(A, "SELECT restored_at FROM galleries WHERE session_id=1")[0] is None

def test_gate_keeps_a_fixed_last_day_for_a_gallery_put_back(php_host):
    base, d, proof, G, key = php_host
    host_write(d, "seen.txt", "2026-01-01T12:00:00Z")
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nproof=1\nwarn_days=7\nuntil={soon}\n")
    code, h, _ = get(base + "gate.php?f=manifest.bin", proof)
    assert code == 200 and h["X-Sunset"] == soon + "T23:59:59Z"
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\nuntil=2026-01-02\n")
    assert get(base + "gate.php?f=manifest.bin", proof)[0] == 410

def wait_up(A):
    for _ in range(100):
        r = one(A, "SELECT upload_state FROM galleries WHERE session_id=1")
        if r[0] in ("up", "error"):
            return r[0]
        time.sleep(0.05)

def test_put_back_for_seven_days(env):
    A, c, G, host = env
    go_live(A, G, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    c.post("/session/1/gallery/back-online", data={"days": "7"}, environ_base=E)
    assert wait_up(A) == "up"
    until = (date.today() + timedelta(days=7)).isoformat()
    lim = host_text(host / GID, "limits.txt")
    assert f"until={until}\n" in lim and "once=" not in lim
    con = A.connect()
    con.execute("UPDATE galleries SET restored_at=? WHERE session_id=1", (days_ago(8),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"

def test_a_one_time_download_goes_dark_once_it_has_been_made(env, tmp_path):
    A, c, G, host = env
    from plugins import gallery as G2
    from PIL import Image
    src = tmp_path / "dlsrc"; src.mkdir()
    Image.new("RGB", (32, 24), "blue").save(src / "b.jpg", "JPEG")
    key = one(A, "SELECT key FROM galleries WHERE session_id=1")[0]
    G2.build(GID, src, key, download=True, picks=dict(post=True))
    pieces = G2.zip_pieces(GID, key)
    assert pieces and G2.gate_built(GID)
    go_live(A, G2, days_ago(A.C.GALLERY_DAYS + 1))
    c.post("/gallery/sync", environ_base=E)
    assert "For one download" in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    c.post("/session/1/gallery/back-online", data={"mode": "once"}, environ_base=E)
    assert wait_up(A) == "up"
    lim = host_text(host / GID, "limits.txt")
    assert f"once={','.join(pieces)}\n" in lim
    assert f"until={(date.today() + timedelta(days=G2.ONCE_DAYS)).isoformat()}\n" in lim
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    host_write(host / GID, "done.txt", str(int(time.time()) + 3000))
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "up"
    host_write(host / GID, "done.txt", str(int(time.time()) - 5))
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    assert [p.name for p in (host / GID).iterdir()] == ["index.html"]
    assert "the one-time download was made" in one(A, "SELECT GROUP_CONCAT(action, ' | ') FROM audit")[0]

def test_gate_closes_a_one_time_download_an_hour_after_its_last_piece(php_host):
    base, d, proof, G, key = php_host
    names = sorted(p.name for p in d.iterdir() if re.fullmatch(r"[0-9a-f]{24}\.bin", p.name))[:2]
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nproof=1\nonce={','.join(names)}\n")
    code, h, _ = get(base + "gate.php?f=manifest.bin", proof)
    assert code == 200 and "X-Once" in h
    assert get(base + "gate.php?f=" + names[0], proof)[0] == 200
    assert not host_has(d, "done.txt")
    assert get(base + "gate.php?f=" + names[1], proof)[0] == 200
    left = int(host_text(d, "done.txt")) - time.time()
    assert 3500 < left <= 3600
    assert get(base + "gate.php?f=manifest.bin", proof)[0] == 200
    host_write(d, "done.txt", str(int(time.time()) - 1))
    assert get(base + "gate.php?f=manifest.bin", proof)[0] == 410

def stamp(A, G, base=None):

    con = A.connect()
    con.execute("UPDATE galleries SET host_fp=?, link_base=COALESCE(?, link_base) WHERE session_id=1", (G.host_fingerprint(), base))
    con.commit(); con.close()

def switch_host(A, tmp_path, base="https://photos.newhost.example/g/"):

    from vault import web as VW
    new = tmp_path / "host2"
    new.mkdir()
    (new / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host_prev"] = cfg["gallery_host"]
    cfg["gallery_host"] = {"type": "local", "path": str(new)}
    VW.write_config(cfg)
    settings(A, gallery_base_url=base)
    return new

def test_galleries_move_whole_to_a_new_host(env, tmp_path):
    A, c, G, host = env
    from test_gallery_php_111 import sealed
    settings(A, gallery_base_url="https://photos.example/g/", business_email="hello@studio.example")
    go_live(A, G, days_ago(3))
    stamp(A, G, "https://photos.example/g/")
    key = one(A, "SELECT key FROM galleries WHERE session_id=1")[0]
    picks = sealed(G, key, "picks", GID)
    (host / GID / "picks.las").write_bytes(picks)
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT host_fp FROM galleries WHERE session_id=1")[0]

    new = switch_host(A, tmp_path)
    c.post("/gallery/sync", environ_base=E)
    assert not (new / GID).exists()
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "1 gallery is still on your earlier web host" in today
    assert "still on your earlier web host" in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    page = c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert "Lane: online" in page and "Put 1 gallery on the new host" in page

    c.post("/galleries/move", environ_base=E)
    names = host_names(new / GID, gallery_key(A))
    assert {"index.html", "manifest.bin", "limits.txt", "picks.las", "gate.php"} <= names
    assert host_bytes(new / GID, "picks.las") == picks
    until = (date.today() - timedelta(days=3) + timedelta(days=A.C.GALLERY_DAYS)).isoformat()
    assert f"until={until}\n" in host_text(new / GID, "limits.txt")
    assert "still on your earlier web host" not in c.get("/", environ_base=E).get_data(as_text=True)

    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "The gallery address changed: 1 client needs the new link" in today
    page = c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert "https://photos.newhost.example/g/" + GID + "/#k=" in page
    c.post("/session/1/gallery/link-sent", environ_base=E)
    assert "needs the new link" not in c.get("/", environ_base=E).get_data(as_text=True)

    c.post("/galleries/clear-old", environ_base=E)
    assert not (host / GID).exists() and host_has(new / GID, "manifest.bin", gallery_key(A))
    from vault import web as VW
    assert "gallery_host_prev" not in VW.config()

def test_a_gallery_that_expires_on_a_dead_host_goes_dark_on_the_new_one(env, tmp_path):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    stamp(A, G)
    new = switch_host(A, tmp_path, base="https://photos.example/g/")
    con = A.connect()
    con.execute("UPDATE sessions SET first_visit=? WHERE id=1", (days_ago(A.C.GALLERY_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    assert [p.name for p in (new / GID).iterdir()] == ["index.html"]
    assert not (host / GID).exists()
    assert "earlier web host" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_an_expired_gallery_on_an_earlier_host_that_cant_be_reached_stays_listed(env, tmp_path):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    stamp(A, G)
    new = switch_host(A, tmp_path, base="https://photos.example/g/")
    (host / ".vault-root").unlink()
    con = A.connect()
    con.execute("UPDATE sessions SET first_visit=? WHERE id=1", (days_ago(A.C.GALLERY_DAYS + 1),))
    con.commit(); con.close()
    c.post("/gallery/sync", environ_base=E)
    assert one(A, "SELECT upload_state FROM galleries WHERE session_id=1")[0] == "removed"
    assert [p.name for p in (new / GID).iterdir()] == ["index.html"]
    assert host_has(host / GID, "manifest.bin", gallery_key(A))
    assert "earlier web host" in c.get("/", environ_base=E).get_data(as_text=True)
    assert any("could not be removed" in a for (a,) in one_all(A, "SELECT action FROM audit WHERE session_id=1"))

def test_the_earlier_host_is_never_cleared_before_the_move(env, tmp_path):
    A, c, G, host = env
    go_live(A, G, days_ago(1))
    stamp(A, G)
    switch_host(A, tmp_path, base="https://photos.example/g/")
    c.post("/galleries/clear-old", environ_base=E)
    assert host_has(host / GID, "manifest.bin", gallery_key(A))
