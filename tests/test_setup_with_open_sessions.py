import os
import sys
import time

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
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    c = signed_in_client(A, setup_done=False)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'booked', '2026-12-01')")
    con.commit()
    con.close()
    return A, c, tmp_path

def _host(tmp_path):
    h = tmp_path / "host"
    h.mkdir(exist_ok=True)
    return dict(gallery_days="30", unopened_days="365", gallery_type="local", gallery_path=str(h), gallery_init="1",
                gallery_base_url="https://photos.example/g/")

STEPS = {
    "shoot": lambda t: dict(preset_families="1", preset_realestate="1"),
    "money": lambda t: dict(deposit="150", event_pct="40", reschedule_fee="25", delivery_days="10", event_delivery_days="20",
                            quote_term_days="21", mileage_rate="0.7", net_days="15", pay_cash="1"),
    "tax": lambda t: dict(tax_mode="flat", sales_tax_pct="7.25"),
    "documents": lambda t: dict(rights_family="transfer"),
    "galleries": _host,
}

@pytest.mark.parametrize("step", sorted(STEPS))
def test_a_setup_step_saves_with_an_open_session(env, step):
    A, c, tmp = env
    t0 = time.time()
    r = c.post(f"/setup/{step}", data=STEPS[step](tmp), environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    assert time.time() - t0 < 4
    con = A.connect()
    try:
        assert step in con.execute("SELECT value FROM settings WHERE key='setup_done'").fetchone()[0]
        if step != "tax":
            assert con.execute("SELECT COUNT(*) FROM tasks WHERE session_id=1").fetchone()[0] > 0
    finally:
        con.close()

def test_turning_galleries_off_then_on_with_the_same_host_is_not_a_host_change(env):
    A, c, tmp = env
    from vault import web as VW
    assert c.post("/setup/galleries", data=_host(tmp), environ_base=E).status_code == 302
    assert c.post("/setup/galleries", data=dict(gallery_days="30", unopened_days="365"), environ_base=E).status_code == 302
    assert c.post("/setup/galleries", data=_host(tmp), environ_base=E).status_code == 302
    assert not VW.config().get("gallery_host_prev")
    d = dict(_host(tmp), gallery_path=str(tmp / "host") + "/")
    assert c.post("/setup/galleries", data=d, environ_base=E).status_code == 302
    assert not VW.config().get("gallery_host_prev")
