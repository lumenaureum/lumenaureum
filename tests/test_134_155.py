import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _files, _job

@pytest.fixture(autouse=True)
def _vault(site):

    v = site.tmp / "vault"
    v.mkdir(exist_ok=True)
    (v / ".vault-root").write_text("")
    return v

def _reset(site, **extra):
    r = site.c.post("/settings/reset", data=dict(confirm="RESET", **extra), environ_base=E)
    assert r.status_code == 302
    return _job("reset")

def _gone(folder):
    (folder / ".vault-root").rename(folder / ".vault-root-off")

def _back(folder):
    (folder / ".vault-root-off").rename(folder / ".vault-root")

def test_reset_page_says_how_many_galleries_are_online(site):
    site.gallery("Online One")
    site.gallery("Online Two")
    page = site.c.get("/settings?tab=system", environ_base=E).get_data(as_text=True)
    assert "2 galleries are online on your web host" in page
    assert "Take the 2 galleries down from the web host" in page and "removed 30 days later" in page

def test_reset_takes_the_galleries_down(site):
    sid = site.gallery("Comes Down")
    gid = site.gid(sid)
    assert _files(site.host, gid)
    job = _reset(site, take_down="1")
    assert job.get("state") == "done", job
    assert _files(site.host, gid) == []
    assert "1 gallery came down from the web host" in job.get("message", "")

def test_reset_can_leave_the_galleries_online_and_says_so(site):
    sid = site.gallery("Stays Up")
    gid = site.gid(sid)
    job = _reset(site)
    assert job.get("state") == "done", job
    assert _files(site.host, gid)
    assert "1 gallery stays online" in job.get("message", "")

def test_a_gallery_that_cannot_come_down_at_the_reset_stays_listed_and_comes_down_later(site):
    sid = site.gallery("Host Away")
    gid = site.gid(sid)
    _gone(site.host)
    job = _reset(site, take_down="1")
    assert job.get("state") == "done", job
    assert [r[0] for r in site.q("SELECT gallery_id FROM gallery_leftovers")] == [gid]
    assert "1 old gallery folder still has to come down from a web host" in \
        site.c.get("/", environ_base=E).get_data(as_text=True)
    _back(site.host)
    site.sync()
    assert _files(site.host, gid) == []
    assert site.q("SELECT COUNT(*) FROM gallery_leftovers")[0][0] == 0

def test_folders_already_waiting_to_come_down_survive_a_reset(site):
    site.q("INSERT INTO gallery_leftovers (gallery_id, host_fp, added) VALUES ('abcdefghijkl', 'fp-x', '2026-11-01')")
    assert _reset(site).get("state") == "done"
    assert site.q("SELECT gallery_id FROM gallery_leftovers")[0][0] == "abcdefghijkl"

def _one_photo(site, name="IMG_1.NEF", size=3 << 20, sent=1 << 20):
    import photostore
    con = site.A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Photo Client')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'family', 'shot')", (hid,)).lastrowid
    con.commit()
    row = photostore.start(con, sid, "raw", name, size)
    import io
    photostore.append(con, row, 0, io.BytesIO(b"x" * sent), sent)
    row = con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()
    con.close()
    return sid, row

def test_photo_files_after_a_reset_are_removed_30_days_later_without_a_restart(site):
    import photostore
    _, row = _one_photo(site, sent=3 << 20)
    assert photostore.path_of(row["id"]).exists()
    assert _reset(site).get("state") == "done"
    assert photostore.path_of(row["id"]).exists()
    first = site.q("SELECT first_seen FROM photo_orphans WHERE id=?", (row["id"],))
    assert first and first[0][0] == date.today().isoformat()
    site.q("UPDATE photo_orphans SET first_seen=?", ((date.today() - timedelta(days=31)).isoformat(),))
    site.A.sweep_photo_orphans()
    assert not photostore.path_of(row["id"]).exists()

def test_reset_page_says_what_happens_to_photos(site):
    page = site.c.get("/settings?tab=system", environ_base=E).get_data(as_text=True)
    assert "removed 30 days later" in page

def test_an_unfinished_upload_has_a_remove_action_on_it(site):
    sid, row = _one_photo(site, name="probe.NEF")
    hid = site.q("SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]
    page = site.c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Unfinished" in page and "probe.NEF" in page
    assert "Remove the unfinished upload" in page
    assert f'action="/photo/{row["id"]}/delete"' in page
    assert "data-confirm=\"Remove the unfinished upload of probe.NEF?" in page
    r = site.c.post(f"/photo/{row['id']}/delete", data={"back": f"/c/{hid}?s={sid}"}, environ_base=E)
    assert r.status_code == 302
    assert site.q("SELECT COUNT(*) FROM photo_files WHERE session_id=?", (sid,))[0][0] == 0
    page = site.c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Unfinished" not in page

def test_seal_now_says_an_unfinished_upload_holds_it_back(site):
    sid, row = _one_photo(site, name="probe.NEF")
    site.c.post(f"/session/{sid}/seal", environ_base=E)
    res = _job(f"seal:{sid}").get("message", "")
    assert "probe.NEF" in res and "unfinished" in res.lower()
