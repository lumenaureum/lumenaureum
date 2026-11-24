import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_134_155 import _reset, _vault
from test_gallery_lifecycle_e2e import E, site
from test_gallery_round_two import _files

def _backup_module():
    return sys.modules["vault.backup"]

def test_the_box_is_ticked_by_default(site):
    site.gallery("Online One")
    page = site.c.get("/settings?tab=system", environ_base=E).get_data(as_text=True)
    box = re.search(r'<input type="checkbox" name="take_down"[^>]*>', page)
    assert box and "checked" in box.group(0)
    assert "If that fails, nothing is reset and no gallery comes down." in page

def test_the_sealed_copy_is_made_while_the_galleries_are_still_up(site, monkeypatch):
    sid = site.gallery("Copy First")
    gid = site.gid(sid)
    seen = {}
    real = _backup_module().sealed_backup

    def watched(*a, **k):
        seen["files_at_copy"] = len(_files(site.host, gid))
        return real(*a, **k)
    monkeypatch.setattr(_backup_module(), "sealed_backup", watched)
    job = _reset(site, take_down="1")
    assert job.get("state") == "done", job
    assert seen["files_at_copy"] > 0
    assert _files(site.host, gid) == []

def test_a_failed_copy_leaves_the_web_host_and_the_records_as_they_were(site, monkeypatch):
    sid = site.gallery("Copy Fails")
    gid = site.gid(sid)
    before = sorted(_files(site.host, gid))

    def broken(*a, **k):
        raise RuntimeError("the backup location is not there")
    monkeypatch.setattr(_backup_module(), "sealed_backup", broken)
    job = _reset(site, take_down="1")
    assert job.get("state") == "error", job
    assert sorted(_files(site.host, gid)) == before
    assert site.q("SELECT upload_state FROM galleries WHERE session_id=?", (sid,))[0][0] == "up"
    assert site.q("SELECT COUNT(*) FROM sessions")[0][0] >= 1
    assert site.q("SELECT COUNT(*) FROM gallery_leftovers")[0][0] == 0
