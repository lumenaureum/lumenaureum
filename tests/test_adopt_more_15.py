import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_adopt_gallery_b3 import BASE, GID, adopt, env, notice, nothing_adopted, row, snapshot

E = {"REMOTE_ADDR": "127.0.0.1"}

def _as_1_4_layout(host, key):

    import hostfiles
    from plugins import gallery as G
    d = host / GID
    (d / hostfiles.name_for(hostfiles.proof_of(G.key_bytes(key)), "manifest.bin")).write_bytes((d / "manifest.bin").read_bytes())
    (d / "manifest.bin").unlink()

def test_a_wrong_key_in_a_1_4_gallery_link_is_named_as_the_key(env):
    import base64
    import secrets
    A, c, G, host, key, photos, m = env
    _as_1_4_layout(host, key)
    before = snapshot(host / GID)
    wrong = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    adopt(c, f"{BASE}{GID}/#k={wrong}")
    assert "the key in that link doesn't open the gallery. Paste the whole link the client received." in notice(c)
    assert "holds no gallery" not in notice(c) and nothing_adopted(A) and snapshot(host / GID) == before
    adopt(c, f"{BASE}{GID}/#k={key}")
    assert row(A, "SELECT gallery_id FROM sessions WHERE id=1")[0] == GID

def test_an_empty_folder_still_says_it_holds_no_gallery(env):
    A, c, G, host, key, photos, m = env
    for p in (host / GID).iterdir():
        if p.suffix == ".bin":
            p.unlink()
    adopt(c, f"{BASE}{GID}/#k={key}")
    assert "holds no gallery the app can read" in notice(c) and nothing_adopted(A)

def test_a_gallery_adopted_before_the_shoot_shows_with_bring_up_to_date(env):
    A, c, G, host, key, photos, m = env
    for stage in ("inquiry", "booked"):
        con = A.connect()
        con.execute("UPDATE sessions SET stage=? WHERE id=1", (stage,))
        con.commit()
        con.close()
        if stage == "inquiry":
            adopt(c, f"{BASE}{GID}/#k={key}")
            assert row(A, "SELECT gallery_id FROM sessions WHERE id=1")[0] == GID
        page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
        assert 'id="gal-adopted"' in page and ">Bring this gallery up to date<" in page, stage

def test_a_current_format_gallery_from_another_install_is_not_called_an_earlier_release(env):
    A, c, G, host, key, photos, m = env
    adopt(c, f"{BASE}{GID}/#k={key}")
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Built in the format from before 1.4" in page
    con = A.connect()
    con.execute("UPDATE galleries SET adopted_fmt='1.0' WHERE session_id=1")
    con.commit()
    con.close()
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "an earlier release:" not in page and "the old page" not in page
    assert "Made outside this app (on another install, or by an earlier release)" in page
