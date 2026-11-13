"""Long work runs as a job: a background thread with a name, a progress count and an outcome, shown in a small panel
on every page until it finishes. The request that starts a job only checks it may start, then returns at once, so a
seal of 100 GB or a client delete that takes galleries off the web never holds up anything else.

One job per key (e.g. "seal:12"): starting a job whose key is already running returns the running one, so a double
click starts nothing twice."""
import os
import threading
import time
import traceback

_LOCK = threading.Lock()
JOBS = {}          # key -> dict(key, label, state running|done|error, done, total, message, started, ended, link)


def start(app, key, label, fn, link=""):
    """Run fn(job) in a thread inside an app context. fn may set job["done"], job["total"], job["message"]; what it
    returns (a string) becomes the finished message. Returns the job (the running one, if already started)."""
    with _LOCK:
        j = JOBS.get(key)
        if j and j["state"] == "running":
            return j
        j = JOBS[key] = dict(key=key, label=label, state="running", done=0, total=0, message="", started=time.time(),
                             ended=None, link=link)

    def run():
        try:
            with app.app_context():
                out = fn(j)
            j.update(state="done", message=out or j.get("message") or "Done.")
        except Exception as e:  # noqa: BLE001 - shown in the panel and the log
            app.logger.error("job %s failed: %s", key, traceback.format_exc())
            j.update(state="error", message=str(e) or type(e).__name__)
        finally:
            j["ended"] = time.time()
    if os.environ.get("CRM_TESTING") and not os.environ.get("CRM_JOBS_ASYNC"):
        run()                                    # tests: the job finishes before the request returns
    else:
        threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
    return j


def running(key):
    j = JOBS.get(key)
    return bool(j and j["state"] == "running")


def visible():
    """Jobs to show: every running one, a finished one for half a minute (long enough to see it worked), and a failed
    one for ten minutes."""
    now = time.time()
    with _LOCK:
        for k in [k for k, j in JOBS.items() if j["ended"] and now - j["ended"] > (600 if j["state"] == "error" else 30)]:
            JOBS.pop(k, None)
        return [dict(j) for j in JOBS.values()]
