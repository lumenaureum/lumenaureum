import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_expiry import E, GID, env, go_live, days_ago
from test_gallery_sunset import stamp

def test_the_same_place_typed_another_way_is_one_identity():
    from plugins import gallery as G
    a = {"type": "sftp", "host": "Gal.Example.com", "user": "ann", "root": "/srv/g/", "private_key": "x"}
    b = {"type": "sftp", "host": "gal.example.com", "user": "ann", "port": 22, "root": "/srv/g", "key_file": "/k"}
    assert G.host_identity(a) == G.host_identity(b) and G.fingerprint_of(a) == G.fingerprint_of(b)

    assert G.host_identity(a) != G.host_identity(dict(b, user="bob"))
    assert G.host_identity(a) != G.host_identity(dict(b, root="/srv/other"))
    assert G.host_identity(a) != G.host_identity(dict(b, port=2222))
    s1 = {"type": "s3", "bucket": "Pics", "access_key": "A", "secret_key": "s", "prefix": "/g/"}
    s2 = {"type": "s3", "bucket": "pics", "access_key": "B", "secret_key": "t", "prefix": "g",
          "endpoint": "https://s3.us-east-1.amazonaws.com/"}
    assert G.host_identity(s1) == G.host_identity(s2)
    assert G.host_identity(s1) != G.host_identity(dict(s2, endpoint="https://files.example.com"))
    assert G.host_identity({"type": "local", "path": "/tmp/x/"}) == G.host_identity({"type": "local", "path": "/tmp/x"})

def test_a_probe_file_tells_one_place_reached_two_ways(env, tmp_path, monkeypatch):
    A, c, G, host = env
    other = tmp_path / "other"
    other.mkdir()
    (other / ".vault-root").write_text("")
    real = G.host_identity
    monkeypatch.setattr(G, "host_identity", lambda cfg: ("x", id(cfg)))
    assert G.same_place({"type": "local", "path": str(host)}, {"type": "local", "path": str(host)})
    assert not G.same_place({"type": "local", "path": str(host)}, {"type": "local", "path": str(other)})
    assert not [p for p in os.listdir(host) if p.startswith(".la-probe")]
    monkeypatch.setattr(G, "host_identity", real)

def test_take_the_old_copies_down_refuses_the_place_in_use(env, tmp_path, monkeypatch):
    A, c, G, host = env
    from vault import web as VW
    go_live(A, G, days_ago(1))
    stamp(A, G)
    link = tmp_path / "link"
    link.symlink_to(host)
    cfg = VW.config()
    cfg["gallery_host_prev"] = dict(cfg["gallery_host"])
    cfg["gallery_host"] = {"type": "local", "path": str(link)}
    VW.write_config(cfg)
    assert "Take the old copies down" not in c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    c.post("/galleries/clear-old", environ_base=E)
    assert (host / GID / "manifest.bin").exists() and not VW.config().get("gallery_host_prev")

    cfg = VW.config()
    cfg["gallery_host_prev"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    monkeypatch.setattr(G, "host_identity", lambda cfg_: ("x", cfg_.get("path")))
    c.post("/galleries/clear-old", environ_base=E)
    assert (host / GID / "manifest.bin").exists() and not VW.config().get("gallery_host_prev")

def test_a_plugin_may_hold_take_the_old_copies_down(env, tmp_path):
    A, c, G, host = env
    from vault import web as VW
    go_live(A, G, days_ago(1))
    stamp(A, G)
    new = tmp_path / "host2"
    new.mkdir()
    (new / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host_prev"], cfg["gallery_host"] = cfg["gallery_host"], {"type": "local", "path": str(new)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("UPDATE galleries SET host_fp=? WHERE session_id=1", (G.host_fingerprint(),))
    con.commit(); con.close()
    held = []
    A.hooks.guards.setdefault("gallery_upload", []).append(lambda proceed, **d: d.get("remove") and held.append(proceed) or bool(d.get("remove")))
    try:
        c.post("/galleries/clear-old", environ_base=E)
        assert len(held) == 1 and (host / GID / "manifest.bin").exists()
        with A.app.test_request_context():
            held[0]()
        assert not (host / GID).exists()
    finally:
        A.hooks.guards["gallery_upload"].pop()

def test_a_move_never_writes_older_picks_over_newer_ones(env, tmp_path):
    A, c, G, host = env
    from test_gallery_php_111 import sealed
    from test_gallery_sunset import switch_host
    go_live(A, G, days_ago(3))
    stamp(A, G)
    con = A.connect()
    key = con.execute("SELECT key FROM galleries WHERE session_id=1").fetchone()[0]
    con.close()
    older = sealed(G, key, G.PICKS_AAD)
    (host / GID / "picks.bin").write_bytes(older)
    c.post("/gallery/sync", environ_base=E)
    assert G.kept_copy(GID, "picks.bin") == older
    newer = sealed(G, key, G.PICKS_AAD)
    assert newer != older
    (host / GID / "picks.bin").write_bytes(newer)
    draft_new = sealed(G, key, G.DRAFT_AAD)
    new = switch_host(A, tmp_path)
    (new / GID).mkdir()
    (new / GID / "draft.bin").write_bytes(draft_new)
    c.post("/galleries/move", environ_base=E)
    assert (new / GID / "picks.bin").read_bytes() == newer
    assert (new / GID / "draft.bin").read_bytes() == draft_new
