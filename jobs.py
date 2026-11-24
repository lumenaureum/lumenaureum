import os
import threading
import time
import traceback

import errtext

_LOCK = threading.Lock()
JOBS = {}

def start(app, key, label, fn, link="", quiet=False):

    with _LOCK:
        j = JOBS.get(key)
        if j and j["state"] == "running":
            return j
        j = JOBS[key] = dict(key=key, label=label, state="running", done=0, total=0, message="", started=time.time(),
                             ended=None, link=link, quiet=bool(quiet))

    def run():
        try:
            with app.app_context():
                out = fn(j)
            j.update(state="done", message=out or j.get("message") or "Done.")
        except Exception as e:
            app.logger.error("job %s failed: %s", key, traceback.format_exc())
            j.update(state="error", message=errtext.words(e, 0))
        finally:
            j["ended"] = time.time()
    if os.environ.get("CRM_TESTING") and not os.environ.get("CRM_JOBS_ASYNC"):
        later = _after_request()
        if later is not None:
            later.append(run)
        else:
            run()
    else:
        threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
    return j

def _after_request():

    from flask import has_request_context, request
    if not has_request_context() or request.method != "POST":
        return None
    return request.environ.setdefault("crm.jobs_after", [])

def run_after(environ):

    while "crm.jobs_after" in environ:
        for fn in environ.pop("crm.jobs_after", None) or ():
            fn()

def running(key):
    j = JOBS.get(key)
    return bool(j and j["state"] == "running")

def status_text(j):

    out = "working..." + (f" ({j.get('done', 0)} of {j['total']} done)" if j.get("total") else "")
    fn = j.get("now_fn")
    if fn:
        try:
            now = (fn() or "").strip()
        except Exception:
            now = ""
        if now:
            out += " \u00b7 " + now
    return out

def live():

    return [dict(key=j["key"], text=j["text"]) for j in visible() if j["state"] == "running"]

def visible():

    now = time.time()
    with _LOCK:
        for k in [k for k, j in JOBS.items() if j["ended"] and now - j["ended"] > (600 if j["state"] == "error" else 30)]:
            JOBS.pop(k, None)
        out = [dict(j) for j in JOBS.values() if not j.get("quiet") or j["state"] == "error"]
    for j in out:
        if j["state"] == "running":
            j["text"] = status_text(j)
    return out
