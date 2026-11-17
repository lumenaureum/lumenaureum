import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from helpers_tools import need_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)
from test_studio_page import E, env, setting, switch_on, wait

ASSETS = Path(ROOT) / "plugins" / "studio_assets"
GOOD = dict(v=1, name="Ada Lovelace", email="ada@example.org", phone="", type="family", date="2027-05-01",
            message="Family photos in the spring?\nThree of us.", hp="", t=16000)

def S_():
    from plugins import studio_page as S
    return S

def test_envelope_round_trip_in_python():
    S = S_()
    pem, pub = S.new_inquiry_key()
    blob = S.seal_inquiry(pub, GOOD)
    assert blob[:7] == b"LAS1\x02\x03\x00" and b"Ada" not in blob and b"spring" not in blob
    assert blob[7:15].hex() == S.kid_hex(pub)
    got = S.open_inquiry(pem, blob)
    assert len(got.pop("id")) == 32 and got == GOOD
    other, _ = S.new_inquiry_key()
    with pytest.raises(ValueError):
        S.open_inquiry(other, blob)
    with pytest.raises(ValueError):
        S.open_inquiry(pem, blob[:-1] + bytes([blob[-1] ^ 1]))
    types = [("family", "Family session")]
    f, why = S.clean_inquiry(GOOD, types)
    assert why == "" and f["date"] == "2027-05-01" and f["message"].endswith("Three of us.")
    assert S.clean_inquiry(dict(GOOD, hp="http://spam"), types)[1] == "spam"
    assert S.clean_inquiry(dict(GOOD, t=800), types)[1] == "spam"
    assert S.clean_inquiry(dict(GOOD, email="", phone=""), types)[1] == "incomplete"
    f, why = S.clean_inquiry(dict(GOOD, type="<b>", date="9999-01-01", email="not an email", phone="555"), types)
    assert why == "" and (f["type"], f["date"], f["email"]) == ("", "", "")

def test_the_page_script_seals_what_the_app_opens():
    need_tool("node")
    S = S_()
    pem, pub = S.new_inquiry_key()
    form = dict(name="Ada Lovelace", email="ada@example.org", phone="", type="family", date="", message="Hello there",
                website="")
    run = lambda f: json.loads(subprocess.run(["node", os.path.join(TESTS, "inquiry_harness.js"), str(ASSETS / "inquiry.js"),
                                                pub, json.dumps(f)], capture_output=True, text=True, timeout=60, check=True).stdout)
    out = run(form)
    assert out["msg"] == "Thanks." and len(out["posted"]) == 1
    p = out["posted"][0]
    assert p["url"] == "../inquiry.php" and p["type"] == "application/x-www-form-urlencoded" and p["credentials"] == "omit"
    assert re.fullmatch(r"w=[0-9a-f]{16}&d=[A-Za-z0-9_-]+", p["body"]) and "Ada" not in p["body"] and "Hello" not in p["body"]
    w, d = p["body"][2:18], p["body"][21:]
    body = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    assert S.work_ok(body, w, 12) and not S.work_ok(body, w, 32)
    data = S.open_inquiry(pem, body)
    assert data["name"] == "Ada Lovelace" and data["message"] == "Hello there" and data["t"] >= 3000 and data["hp"] == ""
    assert run(dict(form, email="", phone=""))["posted"] == []
    assert out["busy"] == dict(text="Sending...", aria="true", disabled=True)
    fast = run(dict(form, wait_ms=500))
    assert fast["msg"] == "Thanks." and len(fast["posted"]) == 1
    d = fast["posted"][0]["body"][21:]
    assert S.open_inquiry(pem, base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)))["t"] >= 3000
    trap = run(dict(form, website="http://spam.example"))
    d = trap["posted"][0]["body"][21:]
    assert S.open_inquiry(pem, base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)))["hp"] == "http://spam.example"

def test_an_edited_inquiry_sent_again_is_another_item():

    need_tool("node")
    S = S_()
    pem, pub = S.new_inquiry_key()
    form = dict(name="Ada Lovelace", email="ada@example.org", phone="", type="family", date="", message="Hello there",
                website="", work=0)

    def ids(tries):
        out = json.loads(subprocess.run(["node", os.path.join(TESTS, "inquiry_harness.js"), str(ASSETS / "inquiry.js"), pub,
                                         json.dumps(dict(form, tries=tries))], capture_output=True, text=True, timeout=60,
                                        check=True).stdout)
        got = []
        for p in out["posted"]:
            d = p["body"][21:]
            data = S.open_inquiry(pem, base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)))
            got.append((data["id"], data["message"]))
        return got
    same = ids([{"status": 500}, {"status": 204}])
    assert len(same) == 2 and same[0][0] == same[1][0]
    edited = ids([{"status": 500}, {"set": {"message": "Hello there, and a date"}, "status": 204}])
    assert len(edited) == 2 and edited[0][0] != edited[1][0] and edited[1][1] == "Hello there, and a date"
    after = ids([{"status": 204}, {"set": {"name": "Ada", "email": "ada@example.org", "message": "Hello there"}, "status": 204}])
    assert len(after) == 2 and after[0][0] != after[1][0]

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

def test_inquiry_php_keeps_only_the_envelope(tmp_path):
    need_tool("php")
    S = S_()
    _, pub = S.new_inquiry_key()
    site = tmp_path / "site"
    (site / "inquiries").mkdir(parents=True)
    (site / "inquiry.php").write_text(S.inquiry_php_source("ab" * 16, 8, S.kid_hex(pub)))
    shutil.copyfile(Path(ROOT) / "plugins" / "sealed_assets" / "sealed.php", site / "sealed.php")
    port = _free_port()
    srv = subprocess.Popen(["php", "-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-S", f"127.0.0.1:{port}", "-t", str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/inquiry.php"
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        def post(body, ctype="application/x-www-form-urlencoded", method="POST"):
            req = urllib.request.Request(url, data=body if method == "POST" else None, method=method,
                                         headers={"Content-Type": ctype} if ctype else {})
            try:
                return urllib.request.urlopen(req, timeout=10).status
            except urllib.error.HTTPError as e:
                return e.code
        b64 = lambda b: b"w=" + S.solve_work(b, 8).encode() + b"&d=" + base64.urlsafe_b64encode(b).rstrip(b"=")
        blob = S.seal_inquiry(pub, GOOD)
        assert post(None, None, "GET") == 405
        assert post(b64(blob), "text/plain") == 415
        assert post(b'{"d": "x"}', "application/json") == 415
        assert post(b64(blob) + b"&x=1") == 400
        assert post(b64(b"LAS2" + blob[4:])) == 400
        assert post(b64(blob[:60])) == 400
        assert post(b64(blob[:92] + b"readable words " * 30)) == 400
        assert post(b64(S.seal_inquiry(S.new_inquiry_key()[1], GOOD))) == 400
        legacy = b"LAQ1" + blob[15:]
        assert post(b64(legacy)) == 400
        assert post(b"w=0000000000000000&d=" + b"A" * 30000) == 413
        assert not list((site / "inquiries").iterdir())
        assert post(b64(blob)) == 204
        kept = list((site / "inquiries").glob("*.las"))
        assert len(kept) == 1 and kept[0].read_bytes() == blob and re.fullmatch(r"[0-9a-f]{16}-[0-9a-f]{32}", kept[0].stem)
        old = site / "inquiries" / ("0" * 16 + "-" + "1" * 32 + ".bin")
        old.write_bytes(b"x" * 300)
        for _ in range(4):
            assert post(b64(S.seal_inquiry(pub, GOOD))) == 204
        assert post(b64(S.seal_inquiry(pub, GOOD))) == 429
        assert len(list((site / "inquiries").glob("*.las"))) == 5 and old.read_bytes() == b"x" * 300
        rate = (site / "inquiries" / ".rate.php").read_text()
        assert "127.0.0.1" not in rate
        for f in (site / "inquiries").iterdir():
            assert b"Ada" not in f.read_bytes() and b"spring" not in f.read_bytes()
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def put_inquiry(web, blob, name=None):
    d = web / "inquiries"
    d.mkdir(exist_ok=True)
    n = name or os.urandom(8).hex() + "-" + os.urandom(16).hex() + ".las"
    (d / n).write_bytes(blob)
    return d / n

def test_the_app_collects_inquiries(env, monkeypatch):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert (web / "inquiry.php").is_file() and "Require all denied" in (web / "inquiries" / ".htaccess").read_text()
    contact = (web / "contact" / "index.html").read_text()
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    assert f'data-key="{pub}"' in contact and 'data-post="../inquiry.php"' in contact and "data-off" not in contact
    assert "BEGIN PRIVATE KEY" not in "".join(p.read_text(errors="replace") for p in web.rglob("*") if p.is_file())
    good = put_inquiry(web, S.seal_inquiry(pub, GOOD))

    import locks
    monkeypatch.setattr(locks, "WRITES", type("Busy", (), {"acquire": lambda *a, **k: False})())
    assert A.app.config["studio_page_collect"]() == dict(added=0, dropped=0, bad=0)
    assert good.is_file()
    monkeypatch.undo()
    spam = put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, hp="buy now")))
    junk = put_inquiry(web, b"LAS1" + os.urandom(200))
    old = put_inquiry(web, os.urandom(300), name=os.urandom(8).hex() + "-" + os.urandom(16).hex() + ".bin")
    other = put_inquiry(web, S.seal_inquiry(S.new_inquiry_key()[1], GOOD))
    stranger = put_inquiry(web, b"not ours", name="notes.txt")
    res = A.app.config["studio_page_collect"]()
    assert res == dict(added=1, dropped=1, bad=2), res
    assert not good.exists() and not spam.exists() and not junk.exists() and not other.exists()
    assert stranger.read_bytes() == b"not ours"
    assert old.is_file()
    con = A.connect()
    assert not con.execute("SELECT 1 FROM households WHERE source='Website'").fetchone()
    con.close()
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "Website inquiries to review" in page and "Ada Lovelace" in page and "Three of us." in page
    assert "1 website inquiry to review: Add or Spam" in c.get("/", environ_base=E).get_data(as_text=True)

    import app as A_
    monkeypatch.setattr(A_, "create_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    rid = str(A.connect().execute("SELECT id FROM studio_inquiry_review").fetchone()[0])
    assert c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": rid}, environ_base=E).status_code == 500
    monkeypatch.undo()
    con = A.connect()
    assert not con.execute("SELECT 1 FROM households WHERE source='Website'").fetchone()
    assert con.execute("SELECT COUNT(*) FROM studio_inquiry_review").fetchone()[0] == 1
    con.close()
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": rid}, environ_base=E)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM studio_inquiry_review").fetchone()
    h = con.execute("SELECT id, name FROM households WHERE source='Website'").fetchall()
    assert [r[1] for r in h] == ["Ada Lovelace"]
    s = con.execute("SELECT id, type, stage, shoot_date, date_tentative FROM sessions WHERE household_id=?", (h[0][0],)).fetchone()
    assert (s[1], s[2], s[3], s[4]) == ("family", "inquiry", "2027-05-01", 1)
    p = con.execute("SELECT email FROM people WHERE household_id=?", (h[0][0],)).fetchone()
    assert p[0] == "ada@example.org"
    log = con.execute("SELECT summary FROM log WHERE session_id=?", (s[0],)).fetchone()[0]
    assert log.startswith("Inquiry from your website") and "Three of us." in log
    hist = [r[0] for r in con.execute("SELECT action FROM audit")]
    con.close()
    assert "Inquiry from your website" in hist
    assert any("1 dropped" in a and "2 unreadable files removed" in a for a in hist)
    assert "buy now" not in json.dumps(hist)
    assert "New inquiry from the website" in c.get("/", environ_base=E).get_data(as_text=True)

    again = put_inquiry(web, (S.seal_inquiry(pub, GOOD)))
    blob = again.read_bytes()
    con = A.connect()
    import hashlib
    con.execute("INSERT INTO studio_inquiries (sha256, outcome) VALUES (?, 'added')", (hashlib.sha256(blob).hexdigest(),))
    con.commit(); con.close()
    assert A.app.config["studio_page_collect"]() == dict(added=0, dropped=0, bad=0) and not again.exists()

def test_draft_build_is_relative_and_sends_nothing(env, tmp_path):
    A, c, S, tmp, web = env
    pem, pub = S.new_inquiry_key()
    conf = dict(name="Pat Lens", owner="Pat Lens", email="hello@example.com", about="Words.", site_url="https://www.example.com/",
                site_path="/", palette=__import__("settings_io").palette("#0d0c0b"), dark=True, galleries=True,
                year=2026, htaccess=True, php=True, draft=True, gallery_folder="g",
                inquiry=dict(key=pub, types=[("family", "Family session")]))
    out = tmp_path / "draft"
    S.build_site(out, conf, [])
    files = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert not {"robots.txt", "sitemap.xml", ".htaccess", "inquiry.php", "inquiries/.htaccess"} & files
    for f in files:
        if f.endswith(".html"):
            t = (out / f).read_text()
            assert '<meta name="robots" content="noindex, nofollow">' in t, f
            assert not __import__("re").search(r'(?:href|src)="/', t), f
            assert "canonical" not in t
    contact = (out / "contact" / "index.html").read_text()
    assert 'data-off="1"' in contact and "sending is off" in contact

    S.build_site(out, dict(conf, draft=False, php=False, htaccess=False), [])
    contact = (out / "contact" / "index.html").read_text()
    assert 'id="inq"' not in contact and "mailto:hello@example.com" in contact and not (out / "inquiry.php").exists()
