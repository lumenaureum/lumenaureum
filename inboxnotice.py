import json
import logging
import threading
import time
from datetime import datetime, timedelta

import locknotice

KEY_ON = "inbox_notice"
KEY_STATE = "inbox_notice_state"
TITLE = "Studio inbox"
GATHER = timedelta(minutes=2)
QUIET = timedelta(minutes=30)
RETRY = timedelta(minutes=10)
TICK = 60
KINDS = {"inquiry": ("inquiry", "inquiries"), "picks": ("set of picks", "sets of picks"),
         "print": ("print order", "print orders")}
LOCK = threading.RLock()
log = logging.getLogger(__name__)

def _now():
    return datetime.now()

def _get(con, key):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else None

def _put(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

def load(con):
    try:
        v = json.loads(_get(con, KEY_STATE) or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}

def is_on(con):
    return _get(con, KEY_ON) == "1"

def arrived(con, kind, n=1):

    if kind not in KINDS or n <= 0:
        return
    try:
        if not is_on(con):
            return
        st = load(con)
        p = st.get("pending") if isinstance(st.get("pending"), dict) else {}
        p[kind] = int(p.get(kind) or 0) + int(n)
        st["pending"] = p
        st.setdefault("first_at", _now().isoformat(timespec="seconds"))
        _put(con, KEY_STATE, json.dumps(st, sort_keys=True))
    except Exception:
        log.warning("studio inbox notice: a new %s was not counted", kind, exc_info=True)

def text(pending):

    parts = [(k, int(pending.get(k) or 0)) for k in KINDS if int(pending.get(k) or 0) > 0]
    if not parts:
        return ""
    words = [f"{n} {KINDS[k][0] if n == 1 else KINDS[k][1]}" for k, n in parts]
    if len(parts) == 1:
        return f"{parts[0][1]} new {words[0].split(' ', 1)[1]}"
    return f"{sum(n for _, n in parts)} new: " + ", ".join(words)

def _at(st, key):
    try:
        return datetime.fromisoformat(st[key]) if st.get(key) else None
    except (TypeError, ValueError):
        return None

def due(st, now):

    if not text(st.get("pending") or {}):
        return False
    first, sent, tried = _at(st, "first_at"), _at(st, "sent_at"), _at(st, "tried_at")
    return (not first or now - first >= GATHER) and (not sent or now - sent >= QUIET) and (not tried or now - tried >= RETRY)

def tick(connect, cfg, now=None):

    now = now or _now()
    with LOCK:
        con = connect()
        try:
            if not is_on(con) or not cfg:
                return None
            st = load(con)
            if not due(st, now):
                return None
            told = {k: int(v or 0) for k, v in (st.get("pending") or {}).items() if k in KINDS}
            why = locknotice.send(cfg, text(told), title=TITLE)
            con.execute("BEGIN IMMEDIATE")
            st = load(con)
            if why is None:
                left = {k: int(v or 0) - told.get(k, 0) for k, v in (st.get("pending") or {}).items()}
                st["pending"] = {k: v for k, v in left.items() if v > 0}
                st["sent_at"] = now.isoformat(timespec="seconds")
                st.pop("tried_at", None)
                st.pop("first_at", None)
                if st["pending"]:
                    st["first_at"] = now.isoformat(timespec="seconds")
            else:
                st["tried_at"] = now.isoformat(timespec="seconds")
            _put(con, KEY_STATE, json.dumps(st, sort_keys=True))
            con.execute("COMMIT")
        finally:
            con.close()
    if why is not None:
        log.warning("The studio inbox notice could not be sent (tried again in %d minutes): %s",
                    RETRY.seconds // 60, why)
    return "sent" if why is None else "failed"

def step(is_unlocked, connect, settings, now=None, backup_problem=None):

    if not is_unlocked():
        return None
    cfg = settings()
    told = tick(connect, cfg, now)
    if backup_problem:
        backup_tick(connect, cfg, backup_problem(), now)
    return told

def start_loop(is_unlocked, connect, settings, applog, backup_problem=None):

    def loop():
        while True:
            try:
                step(is_unlocked, connect, settings, backup_problem=backup_problem)
            except Exception:
                applog.exception("studio inbox notice")
            time.sleep(TICK)
    threading.Thread(target=loop, name="inbox-notice", daemon=True).start()

KEY_BACKUP_ON = "backup_notice"
KEY_BACKUP_STATE = "backup_notice_state"
BACKUP_TITLE = "Studio backup"
BACKUP_AGAIN = timedelta(days=1)
BACKUP_TEXT = {"failed": "The last backup failed. Sign in to your studio to see why.",
               "late": "No backup has been made in over a day. Sign in to your studio to check backups."}

def backup_tick(connect, cfg, problem, now=None):

    now = now or _now()
    with LOCK:
        con = connect()
        try:
            if _get(con, KEY_BACKUP_ON) != "1" or not cfg:
                return None
            try:
                st = json.loads(_get(con, KEY_BACKUP_STATE) or "{}")
                st = st if isinstance(st, dict) else {}
            except ValueError:
                st = {}
            if problem not in BACKUP_TEXT:
                if st:
                    _put(con, KEY_BACKUP_STATE, "{}")
                return None
            sent, tried = _at(st, "sent_at"), _at(st, "tried_at")
            if sent and now - sent < BACKUP_AGAIN and st.get("what") == problem:
                return None
            if tried and now - tried < RETRY:
                return None
            why = locknotice.send(cfg, BACKUP_TEXT[problem], title=BACKUP_TITLE)
            if why is None:
                st = dict(sent_at=now.isoformat(timespec="seconds"), what=problem)
            else:
                st["tried_at"] = now.isoformat(timespec="seconds")
            _put(con, KEY_BACKUP_STATE, json.dumps(st, sort_keys=True))
        finally:
            con.close()
    if why is not None:
        log.warning("The backup notice could not be sent (tried again in %d minutes): %s", RETRY.seconds // 60, why)
    return "sent" if why is None else "failed"
