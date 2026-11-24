import os
import threading
import time

import pytest

import prints_env as PE
from prints_env import E

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def test_a_gallery_check_by_hand_runs_its_update_job_after_the_check_and_waits_on_nothing(e):
    import db
    import jobs
    jobs.JOBS.pop("upgrade-140", None)
    for _ in range(3):
        t0 = time.monotonic()
        assert e.c.post("/gallery/sync", environ_base=E).status_code == 302
        assert time.monotonic() - t0 < 10
        j = jobs.JOBS.get("upgrade-140")
        assert j and j["state"] == "done", j
        assert "locked" not in (j["message"] or "")
    assert not db.SELF_WAITS

def test_a_job_started_outside_a_saving_request_still_runs_at_once(e):
    import jobs
    ran = []
    with e.A.app.test_request_context("/", method="GET"):
        jobs.start(e.A.app, "t206", "A test job", lambda j: ran.append(1))
        assert ran == [1]
    jobs.JOBS.pop("t206", None)

def test_a_write_waiting_on_its_own_thread_fails_at_once_and_is_noted(e):
    import db
    one, two = e.A.connect(), e.A.connect()
    try:
        one.execute("INSERT INTO settings (key, value) VALUES ('t206', '1')")
        t0 = time.monotonic()
        with pytest.raises(db.sqlite3.OperationalError, match="locked"):
            two.execute("INSERT INTO settings (key, value) VALUES ('t206b', '1')")
        assert time.monotonic() - t0 < 2
        assert len(db.SELF_WAITS) == 1 and "t206b" in db.SELF_WAITS[0]
        db.SELF_WAITS.clear()
        one.commit()
        two.execute("INSERT INTO settings (key, value) VALUES ('t206b', '1')")
        two.commit()
    finally:
        one.close()
        two.close()

def test_a_write_from_another_thread_still_waits_its_turn(e):

    import db
    one = e.A.connect()
    done = []

    def other():
        c = e.A.connect()
        try:
            c.execute("INSERT INTO settings (key, value) VALUES ('t206c', '1')")
            c.commit()
            done.append(time.monotonic())
        finally:
            c.close()
    try:
        one.execute("INSERT INTO settings (key, value) VALUES ('t206', '1')")
        t = threading.Thread(target=other)
        t.start()
        time.sleep(1.0)
        assert not done
        one.commit()
        t.join(10)
        assert done and not db.SELF_WAITS
    finally:
        one.close()

def test_the_owner_waits_only_as_long_as_a_background_writer_holds_the_lock(e, monkeypatch):

    import locks
    held, release = threading.Event(), threading.Event()

    def job():
        with locks.WRITES:
            held.set()
            release.wait(10)
    t = threading.Thread(target=job)
    t.start()
    held.wait(5)
    threading.Timer(1.0, release.set).start()
    t0 = time.monotonic()
    r = e.c.post("/settings/plugins", data={"name": "prints", "anyway": "1"}, environ_base=E)
    took = time.monotonic() - t0
    t.join(5)
    assert r.status_code == 302 and 0.8 < took < 8, (r.status_code, took)

def test_in_real_use_the_check_returns_and_its_job_finishes_right_after(e, monkeypatch):

    import jobs
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    jobs.JOBS.pop("upgrade-140", None)
    t0 = time.monotonic()
    assert e.c.post("/gallery/sync", environ_base=E).status_code == 302
    while jobs.running("gallery-check") and time.monotonic() - t0 < 25:
        time.sleep(0.1)
    j = jobs.JOBS.get("upgrade-140")
    assert j
    while j["state"] == "running" and time.monotonic() - t0 < 25:
        time.sleep(0.1)
    assert j["state"] == "done" and time.monotonic() - t0 < 10, (j, time.monotonic() - t0)
