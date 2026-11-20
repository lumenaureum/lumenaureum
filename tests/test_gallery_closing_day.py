import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _q

def _env(tmp_path, monkeypatch):
    from test_fixes_141 import _served_env
    return _served_env(tmp_path, monkeypatch)

def _block(e, PE):
    return e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)

def _limits(e, PE):
    return (e.host / PE.GID / "limits.php").read_text()

def test_change_closing_day_sets_the_last_day_here_and_on_the_web_host(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    b = _block(e, PE)
    assert "Change closing day" in b and "Close now" in b
    day = date.today() + timedelta(days=7)
    r = e.c.post("/session/1/gallery/closing-day", data={"day": day.isoformat()}, environ_base=PE.E)
    assert r.status_code == 302
    assert _q(e.A, "SELECT close_on FROM galleries WHERE session_id=1")[0][0] == day.isoformat()
    assert f"until={day.isoformat()}" in _limits(e, PE)
    b = _block(e, PE)
    assert "Extend to" not in b
    hist = [r[0] for r in _q(e.A, "SELECT action FROM audit WHERE session_id=1")]
    assert any(h.startswith("Gallery closing day set to") for h in hist)

def test_a_closing_day_today_or_over_a_year_out_is_refused(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    for bad in (date.today(), date.today() + timedelta(days=366), "not a date"):
        e.c.post("/session/1/gallery/closing-day", data={"day": str(bad)}, environ_base=PE.E)
        assert _q(e.A, "SELECT close_on FROM galleries WHERE session_id=1")[0][0] is None, bad

def test_the_check_takes_the_gallery_down_after_the_day_set(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    e.c.post("/session/1/gallery/closing-day", data={"day": (date.today() + timedelta(days=3)).isoformat()},
             environ_base=PE.E)
    con = e.A.connect()
    con.execute("UPDATE galleries SET close_on=? WHERE session_id=1", ((date.today() - timedelta(days=1)).isoformat(),))
    con.commit()
    con.close()
    e.c.post("/gallery/sync", environ_base=PE.E)
    assert tuple(_q(e.A, "SELECT upload_state, close_on FROM galleries WHERE session_id=1")[0]) == ("removed", None)
    hist = [r[0] for r in _q(e.A, "SELECT action FROM audit WHERE session_id=1")]
    assert any("closed on the day you set" in h for h in hist), hist

def test_close_now_closes_it_and_put_back_online_starts_afresh(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    photos = {p.name for p in (e.host / PE.GID).iterdir()}
    r = e.c.post("/session/1/gallery/close-now", environ_base=PE.E)
    assert r.status_code == 302
    assert tuple(_q(e.A, "SELECT upload_state, close_on FROM galleries WHERE session_id=1")[0]) == ("removed", None)
    left = {p.name for p in (e.host / PE.GID).iterdir()} if (e.host / PE.GID).exists() else set()
    assert not any(n.endswith(".bin") for n in left) and left != photos
    b = _block(e, PE)
    assert "Put back online" in b and "Close now" not in b
    e.c.post("/session/1/gallery/back-online", data={"days": "7"}, environ_base=PE.E)
    assert _q(e.A, "SELECT close_on FROM galleries WHERE session_id=1")[0][0] is None

def test_search_finds_adopt_and_closing_a_gallery(tmp_path, monkeypatch):

    e, PE = _env(tmp_path, monkeypatch)
    with e.A.app.test_request_context():
        adopt = e.A.settings_find("adopt")
        close = e.A.settings_find("sunset")
    assert any(r["text"] == "Adopt a gallery already online" and r["link"].startswith("/sessions") for r in adopt)
    assert any(r["text"].startswith("Close a gallery now") and "Gallery" in r["sub"] for r in close)
