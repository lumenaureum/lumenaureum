import json
from datetime import date, timedelta

import pytest

import prints_env as PE
from prints_env import E, GID

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def watch_deletes(e, monkeypatch):

    from vault import backends as VB
    seen = []
    real = VB.LocalBackend.delete

    def spy(self, name):
        if name.startswith(GID + "/") and "/orders/" not in name:
            seen.append((name, len(PE.q(e, "SELECT 1 FROM print_orders"))))
        return real(self, name)
    monkeypatch.setattr(VB.LocalBackend, "delete", spy)
    return seen

def test_go_dark_collects_first(e, monkeypatch):
    PE.send(e, PE.payload(e, "a" * 32))
    con = e.A.connect()
    con.execute("UPDATE sessions SET sd_shipped=?, first_visit=? WHERE id=1", ((date.today() - timedelta(days=400)).isoformat(),) * 2)
    con.commit(); con.close()
    seen = watch_deletes(e, monkeypatch)
    e.c.post("/gallery/sync", environ_base=E)
    assert PE.q(e, "SELECT oid FROM print_orders") == [{"oid": "a" * 32}]
    assert seen and all(n >= 1 for _name, n in seen)
    assert PE.q(e, "SELECT upload_state FROM galleries")[0]["upload_state"] == "removed"
    assert not (e.host / GID / "orders").exists()

def test_take_down_collects_first(e, monkeypatch):
    PE.send(e, PE.payload(e, "b" * 32))
    seen = watch_deletes(e, monkeypatch)
    with e.A.app.test_request_context():
        e.A.app.config["gallery_take_down"](1)
        e.A.db().commit()
    assert PE.q(e, "SELECT oid FROM print_orders") == [{"oid": "b" * 32}]
    assert seen and all(n == 1 for _name, n in seen)

def test_a_failed_collection_stops_the_delete(e, monkeypatch):
    PE.send(e, PE.payload(e, "c" * 32))
    from vault import backends as VB
    real = VB.LocalBackend.get_stream

    def broken(self, name, w):
        if "/orders/" in name:
            raise OSError("the host dropped the connection")
        return real(self, name, w)
    monkeypatch.setattr(VB.LocalBackend, "get_stream", broken)
    with e.A.app.test_request_context():
        with pytest.raises(Exception):
            e.G.delete_gallery(GID)
    assert (e.host / GID / "index.html").exists() and list((e.host / GID / "orders").glob("*.las"))
    assert PE.q(e, "SELECT 1 FROM print_orders") == []

def test_update_and_prune_never_touch_orders(e):
    PE.send(e, PE.payload(e, "d" * 32))
    e.G.upload_gallery(GID)
    e.G.prune_host(GID)
    assert list((e.host / GID / "orders").glob("*.las"))
    assert PE.hosted(e.host / GID, "offer.bin", e.key).exists() and (e.host / GID / "offer-kid.php").exists()
    assert PE.collect(e) == 1

def test_a_move_collects_from_the_earlier_host_first(e, tmp_path):
    from vault import backends as VB
    PE.send(e, PE.payload(e, "e" * 32))
    new_dir = tmp_path / "newhost"
    new_dir.mkdir()
    (new_dir / ".vault-root").write_text("")
    old, new = VB.LocalBackend(str(e.host), public=True), VB.LocalBackend(str(new_dir), public=True)
    with e.A.app.test_request_context():
        e.G.hand_over(old, new, GID, e.key)
        e.A.db().commit()
    assert PE.q(e, "SELECT oid FROM print_orders") == [{"oid": "e" * 32}]
    assert not list((e.host / GID / "orders").glob("*.las"))

def test_the_offline_week(e, monkeypatch):

    for i, who in enumerate(("Avery Sample", "Grandma Rose", "Pat Doe")):
        PE.send(e, PE.payload(e, f"{i + 1:032x}", name=who, email="avery@example.com" if i == 0 else f"p{i}@example.org"))
    con = e.A.connect()
    first = (date.today() - timedelta(days=e.A.C.GALLERY_DAYS + 3)).isoformat()
    con.execute("UPDATE sessions SET sd_shipped=?, first_visit=? WHERE id=1", (first, first))
    con.commit(); con.close()
    seen = watch_deletes(e, monkeypatch)
    e.c.post("/gallery/sync", environ_base=E)
    assert len(PE.q(e, "SELECT 1 FROM print_orders")) == 3
    assert seen and all(n == 3 for _name, n in seen)
    page = e.c.get("/", environ_base=E).data.decode()
    assert "New print orders (1)" in page and "Print orders from people not on file (2)" in page

def test_host_gone_for_good_warns_about_uncollected_orders(e):
    from vault import web as VW
    cfg = VW.config()
    old = dict(cfg["gallery_host"])
    cfg.setdefault("gallery_hosts_prev", []).append(old)
    newdir = e.tmp / "host2"
    newdir.mkdir()
    (newdir / ".vault-root").write_text("")
    cfg["gallery_host"] = {"type": "local", "path": str(newdir)}
    VW.write_config(cfg)
    con = e.A.connect()
    con.execute("UPDATE galleries SET host_fp=?", (e.G.fingerprint_of(old),))
    con.commit(); con.close()
    page = e.c.get("/galleries/move", environ_base=E).data.decode()
    assert "Orders clients sent to galleries on this host that the app has not collected yet may be lost" in page
    assert json.dumps(1)
