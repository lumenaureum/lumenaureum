import hashlib
import html
import json
import re

from test_studio_page import E, env, setting, switch_on, wait
from test_studio_site_release import upload

OLD = {"index.html": b"<h1>my old site</h1>", "robots.txt": b"User-agent: *\n", "404.html": b"old not found page"}

def q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def refused_with_old_files(env):
    A, c, S, tmp, web = env
    switch_on(c)
    for n, b in OLD.items():
        (web / n).write_bytes(b)
    (web / "notes.txt").write_text("the photographer's own file")
    upload(c, S)
    assert setting(A, "site_state") == "error"
    assert "weren't put there by Lumen Aureum" in setting(A, "site_error")
    return A, c, S, tmp, web

def publish_section(c):
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    return page[page.index('id="publish"'):]

def offered(c):

    sec = publish_section(c)
    m = re.search(r'<form method="post" action="/settings/studio-page/replace"[^>]*data-confirm="([^"]*)">(.*?)</form>', sec, re.S)
    assert m, "no Replace these files form"
    assert ">Replace these files</button>" in m.group(2)
    return re.findall(r'name="name" value="([^"]+)"', m.group(2)), html.unescape(m.group(1))

def replace(c, S, names):
    c.post("/settings/studio-page/replace", data={"name": names}, environ_base=E)
    wait(S)

def test_the_refusal_offers_replace_these_files_naming_the_count(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    names, ask = offered(c)
    assert sorted(names) == sorted(OLD)
    assert ask.startswith("Replace these 3 files on your web host") and "keeps a copy of each old file" in ask
    assert not (tmp / "site-kept").exists()

def test_replace_keeps_byte_identical_copies_then_the_files_are_its_own(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    names, _ = offered(c)
    replace(c, S, names)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    assert (web / "index.html").read_bytes() != OLD["index.html"]

    (row,) = q(A, "SELECT host_fp, folder, files, put_back FROM studio_kept")
    fp, folder, files, put_back = row
    assert put_back is None and folder.startswith(re.sub(r"[^A-Za-z0-9_-]", "_", fp) + "/")
    for f in json.loads(files):
        p = tmp / "site-kept" / folder / f["name"]
        assert p.read_bytes() == OLD[f["name"]] and f["sha256"] == hashlib.sha256(OLD[f["name"]]).hexdigest()
    assert sorted(f["name"] for f in json.loads(files)) == sorted(OLD)

    mine = {n for (n,) in q(A, "SELECT name FROM studio_files")}
    assert set(OLD) <= mine

    assert (web / "notes.txt").read_text() == "the photographer's own file"
    assert (web / "g" / "abc123xyz" / "index.html").read_text() == "a client gallery"

    sec = publish_section(c)
    assert "3 files your web host had before" in sec and "Lumen Aureum keeps a copy of each." in sec
    assert ">Put the old files back</button>" in sec and "/settings/studio-page/replace" not in sec
    log = " ".join(s for (s,) in q(A, "SELECT action FROM audit"))
    assert "replaced 3 files" in log and "after keeping a copy of each" in log

def test_a_set_that_changed_since_it_was_shown_is_refused(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    names, _ = offered(c)
    (web / "sitemap.xml").write_text("someone's sitemap")
    replace(c, S, names)
    err = setting(A, "site_error")
    assert setting(A, "site_state") == "error"
    assert "changed since you were asked, so nothing was replaced" in err and "now also there: sitemap.xml" in err
    for n, b in OLD.items():
        assert (web / n).read_bytes() == b
    assert not q(A, "SELECT 1 FROM studio_kept") and not (tmp / "site-kept").exists()
    assert sorted(offered(c)[0]) == sorted(list(OLD) + ["sitemap.xml"])
    (web / "404.html").unlink()
    replace(c, S, offered(c)[0])
    assert "no longer there: 404.html" in setting(A, "site_error")
    assert (web / "index.html").read_bytes() == OLD["index.html"]

def test_only_listed_files_and_only_ones_the_site_writes(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    replace(c, S, sorted(OLD) + ["notes.txt"])
    assert setting(A, "site_state") == "error" and "nothing was replaced" in setting(A, "site_error")
    replace(c, S, ["index.html"])
    assert "nothing was replaced" in setting(A, "site_error")
    assert (web / "notes.txt").read_text() == "the photographer's own file"
    for n, b in OLD.items():
        assert (web / n).read_bytes() == b
    assert not q(A, "SELECT 1 FROM studio_kept")

def test_a_file_too_large_to_keep_is_refused(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    names, _ = offered(c)
    with open(web / "index.html", "r+b") as f:
        f.truncate(S.KEEP_MAX + 1)
    replace(c, S, names)
    err = setting(A, "site_error")
    assert "index.html is larger than 20 MB" in err
    assert "too large for Lumen Aureum to keep a copy of, so nothing was replaced" in err and "index.html" in err
    assert (web / "index.html").stat().st_size == S.KEEP_MAX + 1 and (web / "robots.txt").read_bytes() == OLD["robots.txt"]
    assert not q(A, "SELECT 1 FROM studio_kept") and not (tmp / "site-kept").exists()

def test_put_the_old_files_back(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    replace(c, S, offered(c)[0])
    (kid,), = q(A, "SELECT id FROM studio_kept")
    sec = publish_section(c)
    ask = html.unescape(re.search(r'action="/settings/studio-page/put-back"[^>]*data-confirm="([^"]*)"', sec).group(1))
    assert ask.startswith("Put the 3 old files back on your web host?")
    c.post("/settings/studio-page/put-back", data={"kept": str(kid)}, environ_base=E)
    wait(S)
    for n, b in OLD.items():
        assert (web / n).read_bytes() == b
    mine = {n for (n,) in q(A, "SELECT name FROM studio_files")}
    assert not set(OLD) & mine
    assert q(A, "SELECT put_back FROM studio_kept")[0][0]
    assert setting(A, "site_note").startswith("3 files are back to the old site's (")
    assert "Uploads leave them alone, so an upload stops on them and nothing else goes up until you replace them again." \
        in setting(A, "site_note")
    assert "Put the old files back</button>" not in publish_section(c)
    assert (web / "notes.txt").read_text() == "the photographer's own file"
    log = " ".join(s for (s,) in q(A, "SELECT action FROM audit"))
    assert "put 3 old files back" in log

    upload(c, S)
    assert setting(A, "site_state") == "error" and sorted(offered(c)[0]) == sorted(OLD)
    err = setting(A, "site_error")
    assert err.startswith("Upload stopped: 3 files are back to the old site's (") and "weren't put there" not in err
    assert "nothing else went up: your other changes wait until you replace them again." in err
    assert setting(A, "site_note") == ""

def test_put_back_leaves_everything_if_a_file_changed_on_the_host_since(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    replace(c, S, offered(c)[0])
    (kid,), = q(A, "SELECT id FROM studio_kept")
    (web / "robots.txt").write_text("edited by hand on the host")
    c.post("/settings/studio-page/put-back", data={"kept": str(kid)}, environ_base=E)
    wait(S)
    assert "robots.txt was changed on your web host since" in setting(A, "site_error")
    assert (web / "robots.txt").read_text() == "edited by hand on the host"
    assert (web / "index.html").read_bytes() != OLD["index.html"]
    assert q(A, "SELECT put_back FROM studio_kept")[0][0] is None

def test_take_down_after_replace_removes_them_and_nothing_else(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    replace(c, S, offered(c)[0])
    c.post("/settings/studio-page/take-down", environ_base=E)
    wait(S)
    assert setting(A, "site_state") == "down"
    for n in OLD:
        assert not (web / n).exists()
    assert (web / "notes.txt").read_text() == "the photographer's own file"
    assert (web / "g" / "abc123xyz" / "index.html").read_text() == "a client gallery"

    (kid,), = q(A, "SELECT id FROM studio_kept")
    c.post("/settings/studio-page/put-back", data={"kept": str(kid)}, environ_base=E)
    wait(S)
    for n, b in OLD.items():
        assert (web / n).read_bytes() == b

def test_replace_waits_for_the_approval_and_asks_the_host_again(env):
    A, c, S, tmp, web = refused_with_old_files(env)
    names, _ = offered(c)
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **kw: held.append(proceed) or True)
    try:
        c.post("/settings/studio-page/replace", data={"name": names}, environ_base=E)
        assert held and setting(A, "site_state") == "awaiting"
        assert (web / "index.html").read_bytes() == OLD["index.html"]
        (web / "sitemap.xml").write_text("arrived while waiting")
        held[0]()
    finally:
        A.hooks.guards["gallery_upload"].pop()
    assert "now also there: sitemap.xml" in setting(A, "site_error")
    for n, b in OLD.items():
        assert (web / n).read_bytes() == b
    assert not q(A, "SELECT 1 FROM studio_kept")
