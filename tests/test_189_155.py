import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _new_folder, _setup_host

OLD = "oldid1234abcd"

def _moved_on_13(site, was_on_new_host=True):
    sid = site.gallery("Moved On 1.3")
    gid = site.gid(sid)
    _setup_host(site, _new_folder(site, "host2"))
    site.c.post("/galleries/move", environ_base=E)
    os.rename(site.host / gid, site.host / OLD)
    cur = site.q("SELECT host_fp, moved_from FROM galleries WHERE session_id=?", (sid,))[0]
    site.q("UPDATE galleries SET link_was=?, was_fp=? WHERE session_id=?",
           (OLD, cur[0] if was_on_new_host else cur[1], sid))
    return gid

def test_the_old_copy_is_listed_under_the_folder_it_is_in(site):
    gid = _moved_on_13(site)
    page = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert f"the old copy, folder {OLD})" in page and f"folder {gid}" not in page

def test_relinked_on_the_earlier_host_it_may_be_either(site):
    gid = _moved_on_13(site, was_on_new_host=False)
    page = site.c.get("/galleries/move", environ_base=E).get_data(as_text=True)
    assert f"the old copy, folder {OLD} or {gid})" in page
