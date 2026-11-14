import threading
import time

from flask import Flask

def test_jobs_run_in_the_background_once(monkeypatch):
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    import jobs
    app = Flask("t")
    gate, runs = threading.Event(), []

    def slow(job):
        runs.append(1)
        gate.wait(5)
        return "finished"
    t0 = time.time()
    j1 = jobs.start(app, "k1", "Slow thing", slow)
    j2 = jobs.start(app, "k1", "Slow thing", slow)
    assert time.time() - t0 < 1 and j1 is j2 and jobs.running("k1")
    gate.set()
    for _ in range(100):
        if not jobs.running("k1"):
            break
        time.sleep(0.02)
    assert runs == [1] and jobs.JOBS["k1"]["state"] == "done" and jobs.JOBS["k1"]["message"] == "finished"

    def bad(job):
        raise RuntimeError("vault unreachable")
    jobs.start(app, "k2", "Bad thing", bad)
    for _ in range(100):
        if not jobs.running("k2"):
            break
        time.sleep(0.02)
    assert jobs.JOBS["k2"]["state"] == "error" and "vault unreachable" in jobs.JOBS["k2"]["message"]
    assert {j["key"] for j in jobs.visible()} >= {"k1", "k2"}

def test_a_failed_save_changes_nothing(tmp_path, monkeypatch):

    import os
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json")):
        monkeypatch.setenv(k, str(tmp_path / v))
    from helpers import fresh_modules, signed_in_client
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Quinn')").lastrowid
    sid = con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (?, 'portrait', 'inquiry')", (hid,)).lastrowid
    con.commit(); con.close()
    r = c.post(f"/session/{sid}/quote/fee", data={"fee_key": "no-such-fee", "qty": "1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400
    con = A.connect()
    assert not con.execute("SELECT 1 FROM quotes WHERE session_id=?", (sid,)).fetchone()
    con.close()
