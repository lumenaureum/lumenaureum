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

INBOX_TEXT = "Something new came in. Sign in to your studio to see it."

def text(pending):

    return INBOX_TEXT if any(int(pending.get(k) or 0) > 0 for k in KINDS) else ""

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

def step(is_unlocked, connect, settings, now=None, backup_problem=None, problems=None):

    if not is_unlocked():
        return None
    cfg = settings()
    told = tick(connect, cfg, now)
    asks = dict(problems or {})
    asks.setdefault("cred", lambda: credential_fact(connect))
    asks.setdefault("newmaster", lambda: newmaster_fact(connect))
    if backup_problem:
        asks["backup"] = backup_problem
    for kind, ask in asks.items():
        if kind not in STATUS:
            continue
        try:
            con = connect()
            try:
                on = status_on(con, kind)
            finally:
                con.close()
            if on and cfg:
                status_tick(connect, cfg, kind, ask(), now)
        except Exception:
            log.warning("the %s notice check failed", kind, exc_info=True)
    try:
        also = newmaster_also(connect)
        if also and (not cfg or (also.get("server"), also.get("topic")) != (cfg.get("server"), cfg.get("topic"))):
            status_tick(connect, also, "newmaster", newmaster_fact(connect), now, state=KEY_NEWMASTER_ALSO_STATE)
    except Exception:
        log.warning("the newmaster notice check failed", exc_info=True)
    return told

def start_loop(is_unlocked, connect, settings, applog, backup_problem=None, problems=None):

    def loop():
        while True:
            try:
                step(is_unlocked, connect, settings, backup_problem=backup_problem, problems=problems)
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

    return status_tick(connect, cfg, "backup", problem, now)

STORAGE_LOW_PCT = 10

def _backup(fact, now):
    return (fact, BACKUP_TEXT[fact]) if fact in BACKUP_TEXT else None

def _storage(fact, now):
    return ("low", f"Your studio's disk has less than {STORAGE_LOW_PCT}% free. Sign in to your studio to make room "
                   "before uploads or backups stop.") if fact else None

CLOSING_HOURS = (9, 21)

def _closing(fact, now):
    n = int(fact or 0)
    if n <= 0:
        return None
    if not CLOSING_HOURS[0] <= now.hour < CLOSING_HOURS[1]:
        return False
    return ((now.date() + timedelta(days=1)).isoformat(),
            "One or more galleries close tomorrow. Sign in to your studio to see which, or to keep one open longer.")

def _update(fact, now):
    return (str(fact), "Your studio was updated to a new version. Today says what changed.") if fact else None

def _served(fact, now):
    ids = sorted(str(v) for v in (fact or ()))
    if not ids:
        return None
    return (",".join(ids), "One or more gallery pages on your web host are not the ones your studio put there. Sign in "
                           "to your studio to check them.")

def _calendar(fact, now):

    return ("failing", "Your studio couldn't send sessions to your calendar. Sign in to your studio to see why.") \
        if fact else None

STATUS = {
    "backup": (KEY_BACKUP_ON, KEY_BACKUP_STATE, BACKUP_TITLE, _backup, BACKUP_AGAIN),
    "storage": ("storage_notice", "storage_notice_state", "Studio storage", _storage, timedelta(days=1)),
    "closing": ("closing_notice", "closing_notice_state", "Gallery closing", _closing, None),
    "update": ("updated_notice", "updated_notice_state", "Studio updated", _update, None),
    "served": ("served_notice", "served_notice_state", "Gallery page changed", _served, timedelta(days=1)),
    "calendar": ("calendar_notice", "calendar_notice_state", "Studio calendar", _calendar, timedelta(days=1)),
}
TITLES = tuple(v[2] for v in STATUS.values())

COVERED = {"served": lambda was, now: set(now.split(",")) <= set(str(was or "").split(","))}

DEFAULT_ON = {"cred"}

def status_on(con, kind):

    key = STATUS[kind][0]
    if key is None:
        return True
    v = _get(con, key)
    return v == "1" or (v is None and kind in DEFAULT_ON)

def status_tick(connect, cfg, kind, fact, now=None, state=None):

    key_on, key_state, title, words, again = STATUS[kind]
    key_state = state or key_state
    now = now or _now()
    with LOCK:
        con = connect()
        try:
            if not status_on(con, kind) or not cfg:
                return None
            try:
                st = json.loads(_get(con, key_state) or "{}")
                st = st if isinstance(st, dict) else {}
            except ValueError:
                st = {}
            said = words(fact, now)
            if said is False:
                return None
            if not said:
                if st:
                    _put(con, key_state, "{}")
                    con.commit()
                return None
            what, body = said
            sent, tried = _at(st, "sent_at"), _at(st, "tried_at")
            same = st.get("what") == what or (kind in COVERED and COVERED[kind](st.get("what"), what))
            if sent and same and (again is None or now - sent < again):
                return None
            if tried and now - tried < RETRY:
                return None
            why = locknotice.send(cfg, body, title=title)
            if why is None:
                st = dict(sent_at=now.isoformat(timespec="seconds"), what=what)
            else:
                st["tried_at"] = now.isoformat(timespec="seconds")
            _put(con, key_state, json.dumps(st, sort_keys=True))
            con.commit()
        finally:
            con.close()
    if why is not None:
        log.warning("The %s notice could not be sent (tried again in %d minutes): %s", title.lower(),
                    RETRY.seconds // 60, why)
    return "sent" if why is None else "failed"

def seeded(kind, fact, now=None):

    now = now or _now()
    said = STATUS[kind][3](fact, now)
    return json.dumps(dict(sent_at=now.isoformat(timespec="seconds"), what=said[0]), sort_keys=True) if said else None

KEY_CRED_ON = "credential_notice"
KEY_CRED_STATE = "credential_notice_state"
KEY_CRED_AT = "credential_changed_at"
CRED_TITLE = "Studio sign-in changed"
CRED_TEXT = "A way into your studio was changed. If it wasn't you, sign in and look at Account activity."

def _cred(fact, now):
    return (str(fact), CRED_TEXT) if fact else None

STATUS["cred"] = (KEY_CRED_ON, KEY_CRED_STATE, CRED_TITLE, _cred, None)
TITLES = tuple(v[2] for v in STATUS.values())

def credential_changed(con):

    try:
        if not status_on(con, "cred") or not (_get(con, "ntfy_server") and _get(con, "ntfy_topic")):
            return
        _put(con, KEY_CRED_AT, _now().isoformat(timespec="microseconds"))
    except Exception:
        log.warning("sign-in change notice: a change was not noted", exc_info=True)

KEY_NEWMASTER_STATE = "newmaster_notice_state"
NEWMASTER_TITLE = "Studio master key"
NEWMASTER_TEXT = ("A new master key was asked for. It takes effect in 48 hours. If it wasn't you, sign in and cancel "
                  "it in Settings, Security.")
KEY_MASTER_PENDING = "master_pending"

KEY_NEWMASTER_ALSO = "newmaster_notice_also"
NEWMASTER_ALSO_DAYS = 30
KEY_NEWMASTER_ALSO_STATE = "newmaster_notice_also_state"

def _newmaster(fact, now):
    return (str(fact), NEWMASTER_TEXT) if fact else None

STATUS["newmaster"] = (None, KEY_NEWMASTER_STATE, NEWMASTER_TITLE, _newmaster, None)
TITLES = tuple(v[2] for v in STATUS.values())

def newmaster_fact(connect):

    con = connect()
    try:
        raw = _get(con, KEY_MASTER_PENDING)
    finally:
        con.close()
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return None
    return v.get("asked") if isinstance(v, dict) else None

def _epoch():

    return time.time()

def _kept(raw):

    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not (isinstance(v, dict) and v.get("server") and v.get("topic")):
        return None
    kept = v.get("kept")
    if not isinstance(kept, (int, float)) or not 0 <= _epoch() - kept < NEWMASTER_ALSO_DAYS * 86400:
        return None
    return v

def keep_for_newmaster(con, had, new=None):

    if not had or (new and (new.get("server"), new.get("topic")) == (had.get("server"), had.get("topic"))):
        return
    if not _kept(_get(con, KEY_NEWMASTER_ALSO)):
        _put(con, KEY_NEWMASTER_ALSO, json.dumps(dict(server=had.get("server"), topic=had.get("topic"),
                                                      token=had.get("token") or "", kept=int(_epoch())),
                                                 sort_keys=True))

def newmaster_also(connect):

    con = connect()
    try:
        raw = _get(con, KEY_NEWMASTER_ALSO)
    finally:
        con.close()
    return _kept(raw)

def credential_fact(connect):

    con = connect()
    try:
        return _get(con, KEY_CRED_AT)
    finally:
        con.close()

def cred_tick(connect, cfg, now=None):

    return status_tick(connect, cfg, "cred", credential_fact(connect), now)
