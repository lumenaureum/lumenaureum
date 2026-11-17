import base64
import json

import prints_env as PE

OLD = "galOldOrders"

def _order_for_old_id(e, monkeypatch, oid):

    import printorders as P
    import sealed
    off = PE.offer(e)
    spk = base64.urlsafe_b64decode(off["spk"] + "=" * (-len(off["spk"]) % 4))
    with monkeypatch.context() as m:
        m.setattr(sealed, "_gid_ok", lambda gid, mode: True)
        m.setattr(sealed, "_kind_ok", lambda p, gid: None)
        env = P.seal_order(PE.payload(e, oid), spk, OLD, e.G.key_bytes(e.key))
    assert env[6] == len(OLD) and env[7:7 + len(OLD)] == OLD.encode()
    return env

def test_an_order_sealed_for_the_old_id_is_collected_for_the_gallery_once(tmp_path, monkeypatch):
    e = PE.make(tmp_path, monkeypatch)
    import printorders as P
    env = _order_for_old_id(e, monkeypatch, "1" * 32)
    d = e.host / OLD / "orders"
    d.mkdir(parents=True)
    (d / P.host_name(env)).write_bytes(env)
    con = e.A.connect()
    con.execute("UPDATE galleries SET link_was=?, was_open=1, was_fp=host_fp WHERE session_id=1", (OLD,))
    con.commit(); con.close()
    with e.A.app.app_context():
        e.G.final_collect(OLD, e.G.gallery_host())
    rows = PE.q(e, "SELECT oid, gid, session_id, state FROM print_orders")
    assert [(r["oid"], r["gid"], r["session_id"]) for r in rows] == [("1" * 32, PE.GID, 1)]
    assert rows[0]["state"] in ("new", "unmatched")
    assert not list(d.glob("*.las"))

    (d / P.host_name(env)).write_bytes(env)
    with e.A.app.app_context():
        e.G.final_collect(OLD, e.G.gallery_host())
    assert len(PE.q(e, "SELECT oid FROM print_orders")) == 1 and not list(d.glob("*.las"))

def test_an_order_for_the_old_id_never_opens_as_the_new_ones(tmp_path, monkeypatch):

    e = PE.make(tmp_path, monkeypatch)
    import sealed
    from cryptography.hazmat.primitives import serialization
    env = _order_for_old_id(e, monkeypatch, "2" * 32)
    pem = PE.q(e, "SELECT value FROM settings WHERE key='site_inquiry_key'")[0]["value"]
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    kmap = {sealed.kid_of(k.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)): k}
    try:
        sealed.open_studio("print-order", kmap, env, gid=PE.GID, link_key=e.G.key_bytes(e.key))
        raise AssertionError("opened")
    except sealed.SealedError as x:
        assert x.code == "E_SCOPE"
    import upgrade_140
    assert json.loads(upgrade_140.open_order_before_links(kmap, env, OLD, e.G.key_bytes(e.key)))["oid"] == "2" * 32
