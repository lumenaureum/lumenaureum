import pytest

import prints_env as PE
from prints_env import E

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def test_close_now_reads_only_the_gallery_it_closes(e):
    G = e.G
    G.LAST_CHECK.pop("at", None)
    G.LAST_CHECK.pop("sid", None)
    r = e.c.post("/session/1/gallery/close-now", environ_base=E)
    assert r.status_code == 302
    assert PE.q(e, "SELECT upload_state FROM galleries WHERE session_id=1")[0]["upload_state"] == "removed"
    assert "at" not in G.LAST_CHECK
    assert list(G.LAST_CHECK.get("sid", {})) == [1]

