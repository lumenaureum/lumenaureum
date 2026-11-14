import os
import threading
import time
import traceback

_LOCK = threading.Lock()
JOBS = {}

def start(app, key, label, fn, link=""):

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
        except Exception as e:
            app.logger.error("job %s failed: %s", key, traceback.format_exc())
            j.update(state="error", message=str(e) or type(e).__name__)
        finally:
            j["ended"] = time.time()
    if os.environ.get("CRM_TESTING") and not os.environ.get("CRM_JOBS_ASYNC"):
        run()
    else:
        threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
    return j

def running(key):
    j = JOBS.get(key)
    return bool(j and j["state"] == "running")

def visible():

    now = time.time()
    with _LOCK:
        for k in [k for k, j in JOBS.items() if j["ended"] and now - j["ended"] > (600 if j["state"] == "error" else 30)]:
            JOBS.pop(k, None)
        return [dict(j) for j in JOBS.values()]
