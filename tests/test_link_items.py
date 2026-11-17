import json
import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, seal_picks, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
GID = "g-galItems14AAAAAAAAAAAAA"
PICKS_A = [{"i": 0, "name": "a.jpg", "r": True, "note": "", "m": False, "v": False}]
PICKS_B = [{"i": 1, "name": "b.jpg", "r": False, "note": "", "m": True, "v": False}]

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
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
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id, first_visit)
                   VALUES (1, 'family', 'delivered', '2026-01-10', ?, ?)""", (GID, date.today().isoformat()))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files, sealed_gen) VALUES (1, ?, ?, 2, 1)",
                (key, date.today().isoformat()))
    con.commit(); con.close()
    G.upload_gallery(GID, A.C.GALLERY_DAYS, "2099-01-01")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', uploaded_at=? WHERE session_id=1", (date.today().isoformat(),))
    con.commit(); con.close()
    return A, c, G, host, key

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _received(A):
    return _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Picks received from the gallery%'")[0][0]

def test_new_sessions_and_galleries_get_g_link_ids(env):
    A, c, G, host, key = env
    import sealed
    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, "family")
        A.g.db.commit()
    gid = _q(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert sealed.LINK_ID.fullmatch(gid) and gid.startswith("g-") and G.is_link(gid)
    assert "." not in gid and len(gid) == 25
    assert not G.is_link("Ab3dEf6hIj9k") and not G.is_link("s" + gid[1:])

def test_picks_without_an_item_id_are_never_read(env):
    A, c, G, host, key = env
    (host / GID / "picks.las").write_bytes(seal_picks(key, GID, PICKS_A, item=""))
    c.post("/gallery/sync", environ_base=E)
    assert _received(A) == 0 and not _q(A, "SELECT retouch_picks FROM sessions WHERE id=1")[0][0]

def test_the_same_picks_item_is_taken_in_once_and_an_older_one_never_comes_back(env):
    A, c, G, host, key = env
    first = seal_picks(key, GID, PICKS_A, item="a" * 32, at="2026-11-10T10:00:00.000Z")
    second = seal_picks(key, GID, PICKS_B, item="b" * 32, at="2026-11-12T10:00:00.000Z")
    (host / GID / "picks.las").write_bytes(first)
    c.post("/gallery/sync", environ_base=E)
    assert _received(A) == 1
    again = seal_picks(key, GID, PICKS_A, item="a" * 32, at="2026-11-10T10:00:00.000Z")
    assert again != first
    (host / GID / "picks.las").write_bytes(again)
    c.post("/gallery/sync", environ_base=E)
    assert _received(A) == 1
    (host / GID / "picks.las").write_bytes(second)
    c.post("/gallery/sync", environ_base=E)
    assert _received(A) == 2 and _q(A, "SELECT favorites FROM sessions WHERE id=1")[0][0] == "b.jpg"

    (G.KEPT_ROOT / GID / "picks.las").unlink()
    (host / GID / "picks.las").write_bytes(first)
    c.post("/gallery/sync", environ_base=E)
    assert _received(A) == 2 and _q(A, "SELECT favorites, retouch_picks FROM sessions WHERE id=1")[0][0] == "b.jpg"

def test_an_older_draft_put_back_never_shows(env):
    A, c, G, host, key = env
    new = seal_picks(key, GID, PICKS_A + PICKS_B, "draft", item="d" * 32, at="2026-11-12T10:00:00.000Z")
    old = seal_picks(key, GID, PICKS_A, "draft", item="c" * 32, at="2026-11-10T10:00:00.000Z")
    (host / GID / "draft.las").write_bytes(old)
    c.post("/gallery/sync", environ_base=E)
    assert _q(A, "SELECT draft_n FROM galleries WHERE session_id=1")[0][0] == 1
    (host / GID / "draft.las").write_bytes(new)
    c.post("/gallery/sync", environ_base=E)
    assert _q(A, "SELECT draft_n FROM galleries WHERE session_id=1")[0][0] == 2
    (G.KEPT_ROOT / GID / "draft.las").unlink()
    (host / GID / "draft.las").write_bytes(old)
    c.post("/gallery/sync", environ_base=E)
    assert _q(A, "SELECT draft_n FROM galleries WHERE session_id=1")[0][0] == 2

def test_a_purpose_for_a_link_of_another_kind_is_refused(env):

    A, c, G, host, key = env
    import sealed
    env_ = seal_picks(key, GID, PICKS_A)
    other = "s" + GID[1:]
    relabeled = env_[:7] + other.encode() + env_[32:]
    with pytest.raises(sealed.SealedError) as e:
        sealed.check(relabeled, "picks", other)
    assert e.value.code == "E_KIND"
    with pytest.raises(Exception):
        G.open_picks(relabeled, key, other)
    with pytest.raises(sealed.SealedError) as e:
        sealed.seal_link("picks", other, G.key_bytes(key), b'{"v":1}')
    assert e.value.code == "E_KIND"

def test_the_host_refuses_a_purpose_for_a_link_of_another_kind(tmp_path):

    from helpers_tools import need_tool
    import subprocess
    php = need_tool("php")
    import sealed
    gid = GID
    env_ = sealed.seal_link("picks", gid, bytes(32), json.dumps({"v": 1, "id": "e" * 32, "picks": []}).encode())
    other = "e" + gid[1:]
    relabeled = env_[:7] + other.encode() + env_[32:]
    script = tmp_path / "t.php"
    script.write_text("<?php require '" + os.path.join(ROOT, "plugins", "sealed_assets", "sealed.php") + "';\n"
                      "echo sealed_host_check(hex2bin($argv[1]), 'picks', $argv[2], null), '|',"
                      " sealed_host_check(hex2bin($argv[3]), 'picks', $argv[4], null);\n")
    out = subprocess.run([php, str(script), relabeled.hex(), other, env_.hex(), gid], capture_output=True, text=True).stdout
    assert out == "E_KIND|"

def test_an_inquiry_is_taken_in_once_by_its_item_id(tmp_path, monkeypatch):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    signed_in_client(A)
    import items
    with A.app.app_context():
        assert items.state(A.q, "inquiry", "", "f" * 32) == "new"
        items.take(A.q, A.x, "inquiry", "", "f" * 32)
        A.db().commit()
        assert items.state(A.q, "inquiry", "", "f" * 32) == "latest"
        items.take(A.q, A.x, "inquiry", "", "1" * 32)
        A.db().commit()
        assert items.state(A.q, "inquiry", "", "f" * 32) == "older"
        assert not items.take(A.q, A.x, "inquiry", "", "f" * 32)
    assert items.item_of({"id": "F" * 32}) is None and items.item_of({"id": "f" * 31}) is None
    assert items.item_of({"oid": "f" * 32}) is None and items.item_of({"id": "f" * 32}) == "f" * 32

def test_an_item_is_known_per_link(env):

    A, c, G, host, key = env
    import items
    other = "g-otherLink14AAAAAAAAAAAA"
    with A.app.app_context():
        assert items.take(A.q, A.x, "picks", GID, "a" * 32)
        A.db().commit()
        assert items.state(A.q, "picks", GID, "a" * 32) == "latest"
        assert items.state(A.q, "picks", other, "a" * 32) == "new"
        assert items.take(A.q, A.x, "picks", other, "a" * 32)
        A.db().commit()
        assert items.state(A.q, "picks", other, "a" * 32) == "latest"
        assert items.state(A.q, "picks", GID, "a" * 32) == "latest"

def test_sealed_seen_of_an_earlier_14_build_gains_the_link_in_its_key(tmp_path):

    import sqlite3
    import items
    con = sqlite3.connect(str(tmp_path / "x.db"))
    con.executescript("""CREATE TABLE sealed_seen (purpose TEXT NOT NULL, item TEXT NOT NULL, link TEXT NOT NULL DEFAULT '',
        first_seen TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (purpose, item));
        INSERT INTO sealed_seen VALUES ('picks', 'aaaa', 'g-1', '2026-11-01', 1);
        INSERT INTO sealed_seen VALUES ('inquiry', 'bbbb', '', '2026-11-02', 2);""")
    assert items.migrate(con)
    pk = [r[1] for r in sorted(con.execute("PRAGMA table_info(sealed_seen)"), key=lambda r: r[5]) if r[5]]
    assert pk == ["purpose", "link", "item"]
    assert sorted(con.execute("SELECT * FROM sealed_seen").fetchall()) == [
        ("inquiry", "bbbb", "", "2026-11-02", 2), ("picks", "aaaa", "g-1", "2026-11-01", 1)]
    con.execute("INSERT INTO sealed_seen VALUES ('picks', 'aaaa', 'g-2', '2026-11-03', 3)")
    assert not items.migrate(con)
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='sealed_seen_before_link'").fetchone()

def test_two_sessions_never_share_a_link_id(env):

    A, c, G, host, key = env
    con = A.connect()
    try:
        with pytest.raises(Exception, match="UNIQUE"):
            con.execute("INSERT INTO sessions (household_id, type, stage, gallery_id) VALUES (1, 'family', 'inquiry', ?)",
                        (GID,))
        con.execute("INSERT INTO sessions (household_id, type, stage, gallery_id) VALUES (1, 'family', 'inquiry', '')")
        con.execute("INSERT INTO sessions (household_id, type, stage, gallery_id) VALUES (1, 'family', 'inquiry', '')")
        con.execute("DROP INDEX sessions_gallery_id")
        con.commit()
    finally:
        con.close()
    A.init_db()
    con = A.connect()
    try:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='sessions_gallery_id'").fetchone()
    finally:
        con.close()

def _seal_before_links(key, gid):

    import sealed
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    body = json.dumps({"v": 1, "id": "0" * 32, "at": "2026-11-09T10:00:00.000Z", "picks": PICKS_A}).encode()
    g = gid.encode()
    binding = b"LAS1" + bytes([1, 1, len(g)]) + g
    nonce = bytes(12)
    return binding + nonce + AESGCM(sealed._key(key, b"", binding)).encrypt(nonce, body, binding)

def test_the_fenced_reader_refuses_a_link_id():

    import legacy_130
    key = bytes(range(32))
    assert json.loads(legacy_130.open_las_before_links("picks", "Ab3dEf6hIj9k", key,
                                                      _seal_before_links(key, "Ab3dEf6hIj9k")))["picks"] == PICKS_A
    for gid in (GID, "s" + GID[1:]):
        with pytest.raises(ValueError):
            legacy_130.open_las_before_links("picks", gid, key, _seal_before_links(key, gid))

_REDIRECT_HARNESS = r"""
const [src, path] = [process.argv[1], process.argv[2]];
let went = "";
const location = { origin: "https://www.example.com", pathname: path, search: "?x=1", hash: "#k=KEY&c=1",
                   replace(u) { went = u; } };
const document = { documentElement: { getAttribute: () => process.argv[3] } };
new Function("location", "document", "URL", src)(location, document, URL);
process.stdout.write(went);
"""

def test_the_redirect_never_leaves_the_host(env):

    from helpers_tools import need_tool
    import re
    import subprocess
    node = need_tool("node")
    A, c, G, host, key = env
    page = G.redirect_page(GID).decode()
    src = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    assert "base-uri 'none'" in page and "form-action 'none'" in page

    def go(path, to=GID):
        return subprocess.run([node, "-e", _REDIRECT_HARNESS, src, path, to], capture_output=True, text=True, timeout=30,
                              check=True).stdout
    tail = "?x=1#k=KEY&c=1"
    want = f"https://www.example.com/g/{GID}/" + tail
    for path in ("/g/Ab3dEf6hIj9k/", "/g/Ab3dEf6hIj9k", "/g/Ab3dEf6hIj9k/index.html", "//g/Ab3dEf6hIj9k/",
                 "//g//Ab3dEf6hIj9k/index.html", "/g//Ab3dEf6hIj9k//"):
        assert go(path) == want, path
    assert go("//Ab3dEf6hIj9k/index.html") == f"https://www.example.com/{GID}/" + tail
    assert go("///Ab3dEf6hIj9k/") == f"https://www.example.com/{GID}/" + tail
    assert go("/g/Ab3dEf6hIj9k/", "//evil.example/x") == ""
