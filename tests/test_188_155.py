import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _new_folder, _setup_host

OLD = "oldid1234abcd"

def test_take_the_old_copies_down_reaches_the_folder_named_by_the_old_id(site):
    sid = site.gallery("Moved On 1.3")
    gid = site.gid(sid)
    first = site.host
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post("/galleries/move", environ_base=E)
    assert site.q("SELECT moved_from FROM galleries WHERE session_id=?", (sid,))[0][0]

    os.rename(first / gid, first / OLD)
    site.q("UPDATE galleries SET link_was=? WHERE session_id=?", (OLD, sid))
    site.c.post("/galleries/clear-old", environ_base=E)
    assert not (first / OLD).exists()
    assert not site.VW.config().get("gallery_hosts_prev")
    assert (site.tmp / "host2" / gid / "index.html").is_file() or any((site.tmp / "host2" / gid).iterdir())
