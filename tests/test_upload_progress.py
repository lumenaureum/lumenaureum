import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_fixes_141 import _served_env

def test_the_upload_reports_file_n_of_m_and_the_row_carries_it(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("UPDATE galleries SET upload_state='uploading' WHERE session_id=1")
    con.commit()
    con.close()
    G = e.G
    G.UPLOADING[1] = dict(done=3, total=12)
    try:
        r = e.c.get("/session/1/gallery/status", environ_base=PE.E).get_json()
        assert r["upload"] == "uploading" and r["up_label"] == "Uploading: file 3 of 12"
        b = e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
        assert 'id="gal-up-count"' in b and 'data-label="gal-up-count"' in b
    finally:
        G.UPLOADING.pop(1, None)
    r = e.c.get("/session/1/gallery/status", environ_base=PE.E).get_json()
    assert "up_label" not in r
