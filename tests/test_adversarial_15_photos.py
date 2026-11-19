import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_photos_b2 as T
from test_photos_b2 import _page, _session, _sql, _task, env, fake_raw, jpeg, two_locations, upload

def _primary_only(tmp):
    from vault import backends as B
    prim = tmp / "data" / "vault"
    B.LocalBackend.init_root(str(prim))
    (tmp / "data" / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(prim)}}))

def test_h3_a_copy_in_the_main_vault_never_counts_once_a_second_location_is_added(env):
    A, c, tmp = env
    from vault import backends as B
    import photostore
    _primary_only(tmp)
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    with A.app.app_context():
        A.mirror_photos()
    assert _sql(A, "SELECT mirrored FROM photo_files")[0][0]
    assert not _task(A, "Back up photos")[0]
    sec = tmp / "second"
    B.LocalBackend.init_root(str(sec))
    two_locations(tmp)
    _page(c)
    assert not _task(A, "Back up photos")[0]
    con = A.connect()
    try:
        photostore.mirror_pass(con, B.LocalBackend(str(sec)))
    finally:
        con.close()
    assert list(sec.rglob("*.lap"))
    _page(c)
    assert _task(A, "Back up photos")[0]

def test_h3_a_copy_on_one_nas_never_counts_for_another_of_the_same_kind(env):
    A, c, tmp = env
    import photostore
    one = photostore.config_mark({"type": "local", "path": str(tmp / "nas1")})
    two = photostore.config_mark({"type": "local", "path": str(tmp / "nas2")})
    assert one != two and one.startswith("local:")
    from vault import backends as B
    B.LocalBackend.init_root(str(tmp / "nas1"))
    assert photostore.location_mark(B.LocalBackend(str(tmp / "nas1"))) == one

def test_m6_previews_are_dropped_on_lock_and_when_the_photo_goes(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "a.jpg", jpeg(), kind="export")
    upload(c, 1, "b.jpg", jpeg((1, 2, 3)), kind="export")
    fa, fb = [r[0] for r in _sql(A, "SELECT id FROM photo_files ORDER BY name")]
    for f_ in (fa, fb):
        assert c.get(f"/photo/{f_}/thumb", environ_base=T.E).status_code == 200
    assert {k[0] for k in A._THUMBS} == {fa, fb}
    import photostore
    photostore.destroy_files([fa])
    assert {k[0] for k in A._THUMBS} == {fb} and A._THUMB_BYTES[0] == sum(len(v) for v in A._THUMBS.values())
    import datakey
    datakey.lock()
    assert not A._THUMBS and A._THUMB_BYTES[0] == 0

def test_m7_raw_previews_are_read_without_copies_and_work_is_bounded(env):
    A, c, tmp = env
    _session(A, "shot")
    upload(c, 1, "IMG_0001.NEF", fake_raw())
    fid = _sql(A, "SELECT id FROM photo_files")[0][0]
    r = c.get(f"/photo/{fid}/thumb?size=l", environ_base=T.E)
    assert r.status_code == 200 and r.data[:3] == b"\xff\xd8\xff"
    im = A.raw_preview(fake_raw())
    assert im.size == (640, 480)
    assert A._THUMB_WORK._initial_value == 2
