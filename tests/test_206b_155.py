import html
import re
import threading
import time

import pytest

import prints_env as PE
from prints_env import E

KEY = "client-check:1"

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def line(e):
    p = html.unescape(e.c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    m = re.search(r'<div class="ui-factrow" id="from-client"><p class="ui-factrow-text">(.*?)</p>', p, re.S)
    return m.group(1) if m else None, p

def test_check_now_is_a_job_and_the_line_says_what_came_in(e):
    import jobs
    jobs.JOBS.pop(KEY, None)
    r = e.c.post("/client/1/check", data={"back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302
    assert jobs.JOBS[KEY]["state"] == "done" and jobs.JOBS[KEY]["message"].startswith("Checked: ")
    text, p = line(e)
    assert text.startswith("From the client, checked just now: ") and "Check now" in p
    assert e.c.get(f"/jobs/state?key={KEY}", environ_base=E).get_json()["state"] == "done"
    assert e.c.get("/jobs/state?key=nothing-here", environ_base=E).get_json()["state"] == "idle"

def test_a_save_never_waits_behind_a_slow_check(e, monkeypatch):
    import jobs
    import locks
    A = e.A
    jobs.JOBS.pop(KEY, None)
    monkeypatch.setenv("CRM_JOBS_ASYNC", "1")
    go, inside, held = threading.Event(), threading.Event(), []

    def slow_host(sids):
        held.append(locks.WRITES._is_owned())
        inside.set()
        go.wait(30)
        return dict(got=["they opened the gallery"])
    A.hooks.client_checks.append(slow_host)
    try:
        t0 = time.monotonic()
        assert e.c.post("/client/1/check", data={"back": "/c/1?s=1"}, environ_base=E).status_code == 302
        assert time.monotonic() - t0 < 5
        with e.c.session_transaction() as s_:
            assert s_.get("notice") == "Checking now. What came in shows on the From the client line when it is done."
        assert inside.wait(20)
        assert held == [False]
        text, p = line(e)
        assert text.startswith("From the client: checking now") and f'data-ui-job="/jobs/state?key={KEY}"' in p
        assert 'action="/client/1/check"' not in p
        assert e.c.get(f"/jobs/state?key={KEY}", environ_base=E).get_json()["state"] == "running"
        today = e.c.get("/", environ_base=E).get_data(as_text=True)
        assert f'data-ui-job="/jobs/state?key={KEY}"' in today
        t0 = time.monotonic()
        r = e.c.post("/settings/reset", data={"confirm": "no"}, environ_base=E)
        assert r.status_code == 302 and time.monotonic() - t0 < 5
    finally:
        go.set()
        for _ in range(400):
            if not jobs.running(KEY):
                break
            time.sleep(0.05)
        A.hooks.client_checks.remove(slow_host)
    assert jobs.JOBS[KEY]["state"] == "done", jobs.JOBS[KEY]
    text, p = line(e)
    assert text == "From the client, checked just now: they opened the gallery." and 'action="/client/1/check"' in p

def test_the_regular_check_started_by_hand_is_a_job_too(e):
    import jobs
    G = e.A.app.config["gallery_module"]
    G.LAST_CHECK.clear()
    assert e.c.post("/gallery/sync", environ_base=E).status_code == 302
    assert jobs.JOBS["gallery-check"]["state"] == "done" and G.LAST_CHECK.get("at")

def test_a_job_a_job_starts_still_runs_in_tests(e):

    import jobs
    ran = []
    app = e.A.app
    with app.test_request_context("/", method="POST"):
        jobs.start(app, "t206b-outer", "Outer", lambda j: jobs.start(app, "t206b-inner", "Inner", lambda j2: ran.append(1)))
        assert ran == []
        from flask import request
        jobs.run_after(request.environ)
        assert ran == [1] and "crm.jobs_after" not in request.environ
    for k in ("t206b-outer", "t206b-inner"):
        jobs.JOBS.pop(k, None)
