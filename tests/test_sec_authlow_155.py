import os
import sqlite3
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import auth
import netsec

SECRET = "https://user:hunter2@bucket.example/obj?X-Amz-Signature=abc123&X-Amz-Credential=AKIAXXXX"

def test_a_neighbor_in_the_same_64_cannot_lock_the_owner_out():
    th = auth.Throttle()
    for _ in range(auth.MAX_FAILS):
        th.fail("2001:db8:1:2::bad")
    assert th.ip_blocked("2001:db8:1:2::bad")
    assert not th.ip_blocked("2001:db8:1:2::1")
    assert not th.ip_blocked("2001:db8:1:2::2")

def test_ipv4_lock_is_still_per_address_and_mapped_form_matches():
    th = auth.Throttle()
    for _ in range(auth.MAX_FAILS):
        th.fail("::ffff:192.0.2.7")
    assert th.ip_blocked("192.0.2.7") and th.ip_blocked("::ffff:192.0.2.7")
    assert not th.ip_blocked("192.0.2.8")

def test_fresh_address_every_try_is_still_held_to_the_hourly_ceiling():
    th = auth.Throttle()
    for i in range(1, auth.IP_FAILS_PER_HOUR + 1):
        th.fail(f"2001:db8:1:2::{i:x}")
    assert th.ip_blocked("2001:db8:1:2::ffff")
    assert not th.ip_blocked("2001:db8:1:3::1")

def test_tracking_stays_bounded():
    th = auth.Throttle()
    th.MAX_TRACKED = 20
    for i in range(200):
        th.fail(f"10.1.0.{i}")
    assert len(th.ip) <= 21 and len(th.near) <= 21

def test_docs_no_longer_claim_only_15_minutes():
    text = open(os.path.join(ROOT, "docs", "security.md"), encoding="utf-8").read()
    assert "only\n  delay that network's sign-ins by 15 minutes" not in text
    assert "delay that network's sign-ins by 15 minutes" not in " ".join(text.split())
    assert "lands on the exact address" in " ".join(text.split())

@pytest.mark.parametrize("url,at", [
    ("https://0.0.0.0/g/", "0.0.0.0"),
    ("https://[::]/g/", "::"),
    ("https://0/g/", "0.0.0.0"),
    ("https://0.1.2.3/g/", "0.1.2.3"),
    ("https://[::ffff:0.0.0.0]/g/", "::ffff:0.0.0.0"),
])
def test_this_host_addresses_are_flagged(url, at):
    assert netsec.only_here(url) == at
    assert "only works from this machine" in netsec.only_here_words(url)

def test_ordinary_addresses_still_pass():
    assert netsec.only_here("https://192.168.1.5/g/") == ""
    assert netsec.only_here("https://203.0.113.9/g/") == ""
    assert netsec.only_here("https://[2001:db8::1]/g/") == ""

def _db(tmp_path):
    path = tmp_path / "s.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()
    return lambda: sqlite3.connect(path)

def _kept(connect):
    con = connect()
    try:
        return (con.execute("SELECT value FROM settings WHERE key='backup_last_error'").fetchone() or ("",))[0]
    finally:
        con.close()

def test_scheduled_backup_failure_is_kept_scrubbed(tmp_path, monkeypatch):
    from vault import web as VW, keys as K, backup as BK
    connect = _db(tmp_path)
    monkeypatch.setattr(K, "master_pub", lambda con: "pub")

    def boom(*a, **k):
        raise OSError(f"PUT {SECRET} failed")
    monkeypatch.setattr(BK, "sealed_backup", boom)
    with pytest.raises(OSError):
        VW.run_if_due(connect, tmp_path / "crm.db", tmp_path / "faces", now=datetime(2026, 1, 2, 23, 0))
    kept = _kept(connect)
    assert kept.startswith("OSError:") and "bucket.example" in kept
    for leak in ("hunter2", "abc123", "AKIAXXXX", "/obj"):
        assert leak not in kept

def test_no_raw_exception_text_left_at_the_backup_error_sites():
    src = open(os.path.join(ROOT, "vault", "web.py"), encoding="utf-8").read()
    assert 'f"{type(e).__name__}: {e}"' not in src
    assert src.count("_record_error(connect, errtext.named(e))") == 3

def test_failed_job_message_is_scrubbed(monkeypatch):
    import time
    from flask import Flask
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    import jobs
    app = Flask("t")

    def bad(job):
        raise RuntimeError(f"vault unreachable at {SECRET}")
    jobs.start(app, "authlow-k", "Bad thing", bad)
    for _ in range(200):
        if not jobs.running("authlow-k"):
            break
        time.sleep(0.02)
    msg = jobs.JOBS["authlow-k"]["message"]
    assert "vault unreachable" in msg and "hunter2" not in msg and "abc123" not in msg
