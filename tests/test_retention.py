import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    assert "retention" in A.LOADED
    shoot = (date.today() - timedelta(days=400)).isoformat()
    raw = tmp_path / "photos" / "raw" / shoot[:4] / f"{shoot}_lane"
    raw.mkdir(parents=True)
    (raw / "a.nef").write_bytes(b"raw")
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date, sd_shipped) VALUES (1, 'family', 'delivered', ?, ?)",
                (shoot, shoot))
    con.commit(); con.close()
    return A, c, tmp_path, raw

def test_pick_raw_folder_only_from_the_shoot_date(env):
    A, c, tmp, raw = env
    c.post("/session/1/raw", data={"raw_dir": str(tmp / "photos" / "raw") + "/"}, environ_base=E)
    con = A.connect()
    assert not con.execute("SELECT raw_dir FROM retention WHERE session_id=1").fetchone()
    con.close()
    c.post("/session/1/raw", data={"raw_dir": str(raw) + "/", "back": "//evil.example/"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT raw_dir FROM retention WHERE session_id=1").fetchone()[0] == str(raw) + "/"
    con.close()

def test_daily_pass_deletes_a_year_after_delivery_unless_kept(env):
    A, c, tmp, raw = env
    c.post("/session/1/raw", data={"raw_dir": str(raw) + "/"}, environ_base=E)
    c.post("/session/1/raw/keep", environ_base=E)
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert raw.exists()
    con = A.connect()
    con.execute("UPDATE retention SET keep_until=? WHERE session_id=1", ((date.today() - timedelta(days=1)).isoformat(),))
    con.commit(); con.close()
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert raw.exists()
    con = A.connect()
    import json
    n = json.loads(con.execute("SELECT notice FROM retention WHERE session_id=1").fetchone()[0])
    n["purge"][0] = (date.today() - timedelta(days=8)).isoformat()
    con.execute("UPDATE retention SET notice=? WHERE session_id=1", (json.dumps(n),))
    con.commit(); con.close()
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert not raw.exists() and raw.parent.exists() and (tmp / "photos" / "raw").exists()
    con = A.connect()
    assert con.execute("SELECT purged_at FROM retention WHERE session_id=1").fetchone()[0] == date.today().isoformat()
    con.close()

def test_early_delete_needs_the_written_request(env):
    A, c, tmp, raw = env
    c.post("/session/1/raw", data={"raw_dir": str(raw) + "/"}, environ_base=E)
    c.post("/session/1/raw/early", data={"asked_on": "", "asked_by": "email"}, environ_base=E)
    assert raw.exists()
    c.post("/session/1/raw/early", data={"asked_on": "2026-01-02", "asked_by": "carrier pigeon"}, environ_base=E)
    assert raw.exists()
    c.post("/session/1/raw/early", data={"asked_on": "2026-01-02", "asked_by": "letter"}, environ_base=E)
    assert not raw.exists()
    con = A.connect()
    assert "written request by letter" in con.execute("SELECT early_note FROM retention WHERE session_id=1").fetchone()[0]
    con.close()

def test_safe_refuses_roots_years_and_escapes(env):
    A, c, tmp, raw = env
    from plugins import retention as R
    base = tmp / "photos" / "raw"
    assert R._safe(raw, base, 2) and not R._safe(raw.parent, base, 2) and not R._safe(base, base, 2)
    link = raw.parent / "escape"
    os.symlink(tmp, link)
    assert not R._safe(link, base, 2)

def test_failed_unseal_goes_back_locally(env):
    A, c, tmp, raw = env
    r = c.post("/session/1/unseal", data={"master": "wrong", "back": "https://evil.example/x?"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].startswith("/") and "evil" not in r.headers["Location"]
    assert r.headers["Location"].endswith("?unseal=failed")
    r = c.post("/session/1/unseal", data={"master": "wrong", "back": "/client/1?s=1"}, environ_base=E)
    assert r.headers["Location"].endswith("/client/1?s=1&unseal=failed")

def test_a_mistyped_date_or_clock_jump_never_deletes_at_once(env):

    A, c, tmp, raw = env
    c.post("/session/1/raw", data={"raw_dir": str(raw) + "/"}, environ_base=E)
    con = A.connect()
    con.execute("UPDATE sessions SET sd_shipped=? WHERE id=1", ((date.today() - timedelta(days=400)).isoformat(),))
    con.commit(); con.close()
    with A.app.app_context():
        A.app.config["retention_pass"]()
    assert raw.exists()
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('clock_last', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ((date.today() - timedelta(days=30)).isoformat(),))
    con.commit(); con.close()
    with A.app.app_context():
        assert not A.app.config["clock_ok"]()
    assert "Automatic deletions are paused" in c.get("/", environ_base=E).get_data(as_text=True)
    c.post("/clock/confirm", environ_base=E)
    with A.app.app_context():
        assert A.app.config["clock_ok"]()

def test_delivery_dates_are_checked(env):
    A, c, tmp, raw = env
    con = A.connect()
    con.execute("UPDATE sessions SET shoot_date=? WHERE id=1", ((date.today() - timedelta(days=20)).isoformat(),))
    con.commit(); con.close()
    con = A.connect()
    before = con.execute("SELECT sd_shipped FROM sessions WHERE id=1").fetchone()[0]
    con.close()
    for bad in ((date.today() + timedelta(days=3)).isoformat(), (date.today() - timedelta(days=400)).isoformat()):
        c.post("/session/1/delivery", data={"sd_shipped": bad}, environ_base=E)
        con = A.connect()
        assert con.execute("SELECT sd_shipped FROM sessions WHERE id=1").fetchone()[0] == before, bad
        con.close()
