import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_fixes_141 import _served_env

def test_the_upload_reports_photo_n_of_m_and_the_row_carries_it(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("UPDATE galleries SET upload_state='uploading', n_files=COALESCE(NULLIF(n_files, 0), 7) WHERE session_id=1")
    n_photos = con.execute("SELECT n_files FROM galleries WHERE session_id=1").fetchone()[0]
    con.commit()
    con.close()
    assert n_photos
    G = e.G
    total = 4 * n_photos + 6
    G.UPLOADING[1] = dict(done=total // 2, total=total)
    try:
        r = e.c.get("/session/1/gallery/status", environ_base=PE.E).get_json()
        assert r["upload"] == "uploading" and r["up_label"].startswith("Putting the gallery online: ")
        m = __import__("re").fullmatch(r"Putting the gallery online: photo (\d+) of (\d+)", r["up_label"])
        assert m and int(m.group(2)) == n_photos == r["up_total"], r["up_label"]
        assert "file" not in r["up_label"]
        G.UPLOADING[1] = dict(done=1, total=total)
        r = e.c.get("/session/1/gallery/status", environ_base=PE.E).get_json()
        assert r["up_label"] == f"Putting the gallery online: {100 // total}%"
        G.UPLOADING[1] = dict(done=total // 2, total=total)
        b = e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
        assert 'id="gal-up-count"' in b and 'data-label="gal-up-count"' in b
    finally:
        G.UPLOADING.pop(1, None)
    r = e.c.get("/session/1/gallery/status", environ_base=PE.E).get_json()
    assert "up_label" not in r

def test_every_progress_count_is_in_photos():

    import importlib
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    G = importlib.import_module("plugins.gallery")
    assert G.by_photo("Putting the gallery online", 0, 0, 7) == ("Putting the gallery online", 0, 0)
    assert G.by_photo("Putting the gallery online", 20, 20, 7) == ("Putting the gallery online: photo 7 of 7", 7, 7)
    assert G.by_photo("Updating the copy online", 9, 20, 7) == ("Updating the copy online: photo 3 of 7", 3, 7)
    assert G.by_photo("Updating the copy online", 2, 20, 7) == ("Updating the copy online: 10%", 10, 100)
    assert G.by_photo("Putting the gallery online", 8, 20, 0) == ("Putting the gallery online: 40%", 40, 100)
