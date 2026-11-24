import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, unlock_new_install

@pytest.fixture
def install(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    secret = unlock_new_install(A)
    con = A.connect(); con.execute("INSERT INTO households (name) VALUES ('The Harlows')"); con.commit(); con.close()
    from vault import cli
    import datakey
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TEST_PASSWORD)
    code, out = _run(cli, ["backup"])
    name = out.strip().splitlines()[0]
    datakey.lock()
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": secret)
    return A, cli, secret, store, name

def _run(cli, argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()

def test_a_recorded_backup_restores_checked_with_no_warning(install, tmp_path):
    A, cli, secret, store, name = install
    code, out = _run(cli, ["restore", name, str(tmp_path / "r")])
    assert code == 0 and "restored to" in out and "Not checked" not in out

def test_a_backup_this_database_never_recorded_restores_with_the_warning(install, tmp_path):
    A, cli, secret, store, name = install
    import datakey
    datakey.set_unlocked(datakey.unwrap_master(secret))
    con = A.connect(); con.execute("DELETE FROM vault_objects WHERE name=?", (name,)); con.commit(); con.close()
    datakey.lock()
    code, out = _run(cli, ["restore", name, str(tmp_path / "r")])
    assert code == 0 and "restored to" in out
    assert "Not checked: this machine has no record of this backup" in out
    assert out.index("Not checked") < out.index("restored to")

def test_a_restore_from_a_copied_vault_folder_warns(install, tmp_path):
    A, cli, secret, store, name = install
    code, out = _run(cli, ["restore", name, str(tmp_path / "r"), "--from", str(store)])
    assert code == 0 and "Not checked: this machine has no record of this backup" in out

def test_opening_an_orphan_archive_warns(install, tmp_path, monkeypatch):
    A, cli, secret, store, name = install
    from vault import seal as S
    monkeypatch.setattr(S, "open_orphan", lambda *a: {"label": "a session"})
    code, out = _run(cli, ["open-orphan", "s/x.age", str(tmp_path / "o"), "--from", str(store)])
    assert code == 0 and "Not checked: this machine has no record of this archive" in out

def test_shredding_is_described_as_final_only_once_older_backups_age_out():
    from vault import keys, seal
    for doc in (seal.destroy_session.__doc__, keys.shred.__doc__):
        if doc is None:
            continue
        assert "what makes it final" not in doc and "age out" in doc
    design = open(os.path.join(ROOT, "vault", "DESIGN.md")).read()
    assert "once the database backups made before the deletion" in design
    sec = open(os.path.join(ROOT, "docs", "security.md")).read()
    part = sec[sec.index("## Deleting a client"):sec.index("## Record numbers")]
    assert "key that opens them is destroyed" in part and "until those backups age out" in part

def _members(folder, **kw):
    import tarfile
    from vault import seal as S
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        left = S.add_tree(tf, str(folder), "shoot", **kw)
    buf.seek(0)
    tf = tarfile.open(fileobj=buf)
    return {m.name: (m, tf.extractfile(m).read() if m.isfile() else None) for m in tf.getmembers()}, left

def test_a_link_to_a_file_is_sealed_as_the_file(tmp_path):
    outside = tmp_path / "elsewhere.nef"; outside.write_bytes(b"raw bytes")
    shoot = tmp_path / "shoot"; (shoot / "sub").mkdir(parents=True)
    (shoot / "a.jpg").write_bytes(b"jpeg"); (shoot / "sub" / "b.jpg").write_bytes(b"b")
    os.symlink(outside, shoot / "linked.nef")
    os.symlink("a.jpg", shoot / "same.jpg")
    got, left = _members(shoot)
    assert left == [] and not any(m.issym() for m, _ in got.values())
    assert got["shoot/linked.nef"][1] == b"raw bytes" and got["shoot/a.jpg"][1] == b"jpeg"
    assert got["shoot/sub/b.jpg"][1] == b"b" and got["shoot/sub"][0].isdir()
    assert got["shoot/same.jpg"][0].isfile() or got["shoot/same.jpg"][0].islnk()

@pytest.mark.parametrize("bad", ["folder", "nowhere"])
def test_a_link_to_a_folder_or_nowhere_stops_the_seal_naming_it(tmp_path, bad):
    shoot = tmp_path / "shoot"; shoot.mkdir(); (shoot / "a.jpg").write_bytes(b"jpeg")
    os.symlink(tmp_path if bad == "folder" else tmp_path / "gone", shoot / "odd")
    with pytest.raises(ValueError, match=r"shoot/odd is a link .*nothing was removed"):
        _members(shoot)
    got, left = _members(shoot, skip_bad=True)
    assert left == ["odd"] and "shoot/odd" not in got and "shoot/a.jpg" in got

def test_a_folder_reached_through_a_link_is_sealed_from_where_it_is(tmp_path):
    real = tmp_path / "real"; real.mkdir(); (real / "a.jpg").write_bytes(b"jpeg")
    os.symlink(real, tmp_path / "shoot")
    got, _ = _members(tmp_path / "shoot")
    assert got["shoot"][0].isdir() and got["shoot/a.jpg"][1] == b"jpeg"

def test_a_session_with_a_bad_link_is_not_sealed_and_nothing_is_stored(tmp_path):
    import sqlite3
    from vault import backends as B, keys as K, seal as S
    bk = B.LocalBackend.init_root(str(tmp_path / "store"))
    con = sqlite3.connect(tmp_path / "t.db")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    K.init(con)
    K.set_master_pub(con, K.generate_master()[1])
    shoot = tmp_path / "shoot"; shoot.mkdir(); (shoot / "a.jpg").write_bytes(b"jpeg")
    os.symlink(tmp_path, shoot / "loop")
    with pytest.raises(ValueError, match="link to a folder"):
        S.seal_session(con, 1, [str(shoot)], [bk])
    assert not list((tmp_path / "store").rglob("*.age")) and (shoot / "a.jpg").exists()
    assert not con.execute("SELECT 1 FROM vault_objects").fetchone()

def test_opening_an_archive_still_refuses_a_link_that_leads_out(tmp_path):
    import tarfile
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode="w") as tf:
        ti = tarfile.TarInfo("shoot/x"); ti.type = tarfile.SYMTYPE; ti.linkname = "/etc/passwd"
        tf.addfile(ti)
    evil.seek(0)
    with tarfile.open(fileobj=evil) as tf, pytest.raises(tarfile.FilterError):
        tf.extractall(tmp_path / "out", filter="data")

@pytest.fixture
def G(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    from plugins import gallery
    return gallery

GID143 = "g-" + "a" * 23

def _old_page(G, gid=GID143):

    return G.viewer_page(gid, gate=True, credit=True).replace(G.VIEWER_POLICY, "script-src 'unsafe-inline';", 1)

def test_the_built_page_pins_its_own_script(G):
    page = G.viewer_page(GID143, gate=True, credit=True)
    import re
    policy = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert "unsafe-inline" not in policy.split("script-src")[1].split(";")[0] and "'sha256-'" not in policy
    assert "script-src" + G._script_hashes(page) + ";" in policy and G.current_page(page)
    assert "script-src 'sha256-';" in (G.ASSETS / "viewer.html").read_text()

def test_a_page_an_earlier_version_built_is_rewritten_before_upload(G):
    d = G.OUT_ROOT / GID143; d.mkdir(parents=True)
    (d / "index.html").write_text(_old_page(G))
    assert not G.current_page(_old_page(G))
    assert G.refresh_page(GID143)
    now = (d / "index.html").read_text()
    assert G.current_page(now) and 'data-gate="1"' in now[:400] and G.CREDIT_HTML in now
    assert not G.refresh_page(GID143)

def test_the_refit_replaces_an_online_page_that_differs_only_in_its_policy(G):
    import refit
    old = _old_page(G)
    want = refit.page_for(G, GID143, old)
    assert want != old.encode() and G.current_page(want.decode())

def test_the_page_runs_under_its_own_policy_and_an_injected_script_does_not(tmp_path, G):
    sync_api = pytest.importorskip("playwright.sync_api")
    page_text = G.viewer_page(GID143, gate=False, credit=False)
    injected = page_text.replace("</body>", "<script>window.__injected = 1</script></body>", 1)
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium is not available: {e}")

    def visit(body):
        ctx = b.new_context()
        ctx.route("https://gallery.test/**", lambda route, request=None: route.fulfill(
            status=200 if route.request.url.split("#")[0].endswith("/") else 404,
            body=body if route.request.url.split("#")[0].endswith("/") else "",
            headers={"content-type": "text/html; charset=utf-8"}))
        p = ctx.new_page()
        refused = []
        p.on("console", lambda m: refused.append(m.text) if "Content Security Policy" in m.text else None)
        p.goto(f"https://gallery.test/g/{GID143}/#k=" + "A" * 43)
        p.wait_for_timeout(1000)
        got = p.evaluate("[typeof window.__injected, document.body.innerText.trim()]")
        ctx.close()
        return got, refused
    try:
        (kind, text), refused = visit(page_text)
        assert not refused, refused
        (kind2, text2), refused2 = visit(injected)
        assert kind2 == "undefined" and refused2
        assert text2 == text and text
        broken = page_text.replace(G.VIEWER_SCRIPT_HASHES, " 'sha256-AAAA'", 1)
        (_, text3), refused3 = visit(broken)
        assert refused3 and text3 != text
    finally:
        b.close()
        pw.stop()
