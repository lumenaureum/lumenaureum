#!/usr/bin/env python3
"""Lumen Aureum: a private back office for photographers. It runs on your own machine and only answers the
networks you allow (by default: this machine and private networks)."""
import importlib.util
import ipaddress
import json
import re
import os
import secrets
import threading
import datakey
import db as dbmod
from db import sqlite3   # SQLCipher, same API
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from markupsafe import Markup

import config as C
import sun

os.umask(0o077)       # everything the app creates (database, journal, faces, backups) is readable by its owner only

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CRM_DB", ROOT / "data" / "crm.db"))


def static_v(filename):
    """Cache-buster: the file's mtime as ?v=, so a CSS edit reaches the browser without a hard refresh."""
    try:
        return int((ROOT / "static" / filename).stat().st_mtime)
    except OSError:
        return 0
PORT = int(os.environ.get("CRM_PORT", "8780"))
# Networks allowed to reach the app at all (the login is the real lock; this keeps it off the open internet).
# CRM_ALLOW_ONLY replaces the default list, e.g. "127.0.0.0/8,100.64.0.0/10" to allow only a VPN.
_DEFAULT_NETS = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"]
ALLOW = [ipaddress.ip_network(n.strip()) for n in
         (os.environ.get("CRM_ALLOW_ONLY", "").split(",") if os.environ.get("CRM_ALLOW_ONLY") else _DEFAULT_NETS)
         + [n for n in os.environ.get("CRM_ALLOW", "").split(",") if n.strip()] if n.strip()]

import netsec
if netsec.startup_problem():
    raise SystemExit(netsec.startup_problem())
from flask import Request as _Request


class _MemRequest(_Request):
    """Uploaded files in a form (face photos, the logo, a settings file) stay in memory: never spooled to a plaintext
    temporary file on disk. Photos never come this way; they are read from the raw request and sealed as they arrive.
    MAX_CONTENT_LENGTH caps the size."""
    def _get_file_stream(self, total_content_length=None, content_type=None, filename=None, content_length=None):
        import io as _io
        return _io.BytesIO()


app = Flask(__name__)
app.request_class = _MemRequest
app.wsgi_app = netsec.ProxyGate(app.wsgi_app)          # forwarded headers believed only from CRM_TRUSTED_PROXIES
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024   # face photos and forms
app.jinja_env.globals["static_v"] = static_v
def _fee_short(label):
    """'Senior portrait session (1.5 hr, up to 2 locations), every edited image included' -> 'Senior 1.5 hr'."""
    import re as _re
    lab = label.split(", every edited image")[0]
    m = _re.search(r"\(([^)]*)\)", lab)
    dur = m.group(1).split(",")[0].strip() if m and m.group(1)[:1].isdigit() else ""
    lab = _re.sub(r"\s*\([^)]*\)", "", lab)
    for a, b in ((" portrait session", ""), (" session", ""), (", per hour", " /hr"), (", per image", " /image"),
                 (", per mile each way", ""), (", per round-trip mile", ""), (" minimum", " min"), ("Additional ", "Extra ")):
        lab = lab.replace(a, b)
    return " ".join(x for x in (lab.strip(" ,"), dur) if x)
app.jinja_env.filters["feeshort"] = _fee_short
def credit_on():
    return setting("credit_off") != "1"


app.jinja_env.globals["credit_on"] = credit_on
app.jinja_env.filters["oneline"] = lambda t: ", ".join(l_.strip() for l_ in (t or "").splitlines() if l_.strip())
app.jinja_env.filters["todo"] = lambda label: todo_word(label)   # open steps read as tasks, not states
app.jinja_env.globals["setting"] = lambda k, d="": setting(k, d)


def setup_done():
    try:
        return setting("setup_complete") == "1"
    except Exception:
        return False                        # locked: no database to ask


SETTINGS_ENDPOINTS = {"settings_view", "wording_edit", "type_edit", "type_walk", "vault_view", "security", "setup_step", "about"}
app.jinja_env.globals["setup_done"] = setup_done
app.jinja_env.globals["SETTINGS_ENDPOINTS"] = SETTINGS_ENDPOINTS
C.galleries_on = lambda: bool(setting("gallery_base_url"))
def _setting_anywhere(k):
    try:
        return setting(k)
    except RuntimeError:                                  # outside a request: read it in an app context, if unlocked
        try:
            with app.app_context():
                return setting(k)
        except Exception:  # noqa: BLE001
            return ""


C.pay_details = lambda: _setting_anywhere("pay_details")
C.mileage_rate = lambda: _setting_anywhere("mileage_rate")


def contact_bits():
    """How a client reaches the photographer, for the top of every document: email, phone, mailing address."""
    return [b for b in (setting("business_email").strip(), setting("business_phone").strip(),
                        ", ".join(l_.strip() for l_ in setting("business_address").splitlines() if l_.strip())) if b]


app.jinja_env.globals["contact_bits"] = contact_bits

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT DEFAULT '', source TEXT DEFAULT '',
  referred_by INTEGER REFERENCES households(id), notes TEXT DEFAULT '', demo INTEGER DEFAULT 0,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  name TEXT NOT NULL, role TEXT DEFAULT 'adult', is_minor INTEGER DEFAULT 0, grad_year INTEGER,
  email TEXT DEFAULT '', phone TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, date TEXT, venue TEXT DEFAULT '', notes TEXT DEFAULT '',
  demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  type TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'inquiry', title TEXT DEFAULT '',
  shoot_date TEXT, start_time TEXT DEFAULT '', stops TEXT DEFAULT '', intake TEXT DEFAULT '{}',
  notes TEXT DEFAULT '',
  gallery_id TEXT DEFAULT '', gallery_built TEXT, letter_mailed TEXT, first_visit TEXT,
  extension_used INTEGER DEFAULT 0, sd_shipped TEXT, favorites TEXT DEFAULT '', veto TEXT DEFAULT '',
  created TEXT DEFAULT CURRENT_TIMESTAMP, stage_changed TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS session_people (
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  person_id INTEGER REFERENCES people(id) ON DELETE CASCADE, PRIMARY KEY (session_id, person_id));
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  title TEXT NOT NULL, offset_days INTEGER, due TEXT, done TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, about_person INTEGER REFERENCES people(id) ON DELETE SET NULL,
  signer TEXT DEFAULT '', status TEXT DEFAULT 'needed', sent TEXT, signed TEXT, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS doc_snapshots (
  kind TEXT NOT NULL, ref INTEGER NOT NULL, at TEXT, data TEXT NOT NULL, PRIMARY KEY (kind, ref));
CREATE TABLE IF NOT EXISTS doc_snapshots_old (
  kind TEXT NOT NULL, ref INTEGER NOT NULL, at TEXT, data TEXT NOT NULL, moved TEXT);
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  label TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT DEFAULT 'deposit', amount REAL NOT NULL, received TEXT, method TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  date TEXT, label TEXT NOT NULL, amount REAL DEFAULT 0, miles REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY, household_id INTEGER REFERENCES households(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  at TEXT DEFAULT CURRENT_TIMESTAMP, channel TEXT DEFAULT 'note', summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY, received TEXT, amount REAL, tax REAL DEFAULT 0, type TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, household_id INTEGER, session_id INTEGER, event_id INTEGER,
  action TEXT NOT NULL, note TEXT DEFAULT '', who TEXT DEFAULT 'Owner', route TEXT DEFAULT '', data TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS audit_hh ON audit(household_id, at);
CREATE TABLE IF NOT EXISTS step_closes (
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, step TEXT NOT NULL, outcome TEXT NOT NULL,
  at TEXT DEFAULT (date('now','localtime')), PRIMARY KEY (session_id, step));
CREATE TABLE IF NOT EXISTS stage_log (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  stage TEXT NOT NULL, at TEXT DEFAULT (date('now','localtime')));
"""


# ---------------------------------------------------------------- db helpers

def db():
    if "db" not in g:
        g.db = connect()
    return g.db


def connect():
    """The encrypted database (db.py). Raises datakey.Locked until the owner has signed in since the last restart."""
    return dbmod.connect(DB_PATH)


UNLOCKED_POSTS = {"photos_upload", "photos_status"}     # uploads: one writer per file instead (photostore, locks.photo)


@app.before_request
def _one_write_at_a_time():
    """Saving requests take turns: two at once (a double click, two windows, an in-place save beside another) never
    interleave their reads and writes. Uploads use their own per-file lock and don't wait here."""
    if request.method == "POST" and request.endpoint not in UNLOCKED_POSTS:
        import locks
        if not locks.WRITES.acquire(timeout=45):
            return "Busy finishing another change. Try again in a moment.", 503
        g.write_lock = True
        g.tx = True                      # this request's changes commit together at the end, or not at all


@app.teardown_request
def _release_write(_exc):
    if g.pop("tx", None) and "db" in g:          # the request failed before it could commit: undo all of it
        try:
            g.db.rollback()
            if g.pop("applied", None):
                apply_settings()                  # the rules in memory follow the database again
        except Exception:
            pass
    if g.pop("write_lock", None):
        import locks
        locks.WRITES.release()


@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def _commit():
    """Inside a saving request the changes wait for the end of the request (all or nothing); elsewhere (background
    work, jobs, setup) each change commits at once."""
    if not g.get("tx"):
        db().commit()
    for k in ("wf_cache", "tot_cache", "needs_you"):
        g.pop(k, None)


def x(sql, args=()):
    cur = db().execute(sql, args)
    _commit()
    return cur.lastrowid


def setting(key, default=""):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def d(s):
    return date.fromisoformat(s) if s else None


def num(v, default=0.0, lo=-1e7, hi=1e7):
    """A number typed into a form: a bad, infinite or NaN value gives the default; the result is kept in range."""
    try:
        n = float(str(v).replace(",", "").replace("$", "").strip()) if v not in (None, "") else default
    except ValueError:
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, n))


def safe_back(target):
    """A "back" address from a form or link: local paths only, never another site (no open redirects)."""
    from urllib.parse import urlsplit
    t = (target or "").strip()
    if not t.startswith("/") or t.startswith("//") or "\\" in t or any(ord(c) < 32 or ord(c) == 127 for c in t):
        return None                          # a TAB or newline is dropped by browsers: "/\t/evil" would become "//evil"
    u = urlsplit(t)
    return t if not u.scheme and not u.netloc else None


def today():
    return datetime.now(TZ).date()


# ---------------------------------------------------------------- plugins

class Hooks:
    """What a plugin can attach to. See plugins/README.md."""

    def __init__(self):
        self.nav = []             # (label, endpoint)
        self.session_panels = []  # fn(session_row) -> html or None (shown under Details)
        self.session_tabs = []    # fn(session_row) -> (label, count_or_None, html) or None
        self.today = []           # fn() -> [dict(text=, link=, kind=)]
        self.steps = []           # fn(session_row) -> [(stage, label, done, tab)]  extra workflow steps
        self.step_actions = []    # fn(step_label, session_row) -> dict(verb=, href=) or None: dashboard verb for a step
        self.stage_blocks = []    # fn(session_row) -> [(title, html)]: sections in the client screen's right column
        self.on = {"session_created": [], "stage_changed": [], "gallery_window_changed": []}
        self.guards = {}          # action -> [fn(proceed, **details) -> bool]: True = the plugin holds the action and calls proceed() later
        self.detail_locations = []   # fn(session) -> [dict(time=, name=, town=, what=, light=)] or None: locations on the client details sheet
        self.search = []          # fn(term) -> [dict(title=, rows=[dict(text=, sub=, link=)])]: extra search results
        self.step_icons = []      # (words, Material Symbol) checked before the built-in icons
        self.step_labels = {}     # step title -> short label for tiles (TODO_WORDS for a plugin's own steps)
        # A regional sales-tax rule (Settings > Sales tax > Regional): dict(name=, areas=[(key, label)],
        # area_of=fn(client address) -> (area, warning), extra_pct=fn(area, local_pct_setting) -> % added to the base)
        self.tax_region = None

    def fire(self, name, *args):
        for fn in self.on.get(name, []):
            fn(*args)

    def guard(self, action, proceed, **details):
        """True when a plugin has taken the action over (it will call proceed() itself, or not at all)."""
        return any(fn(proceed, **details) for fn in self.guards.get(action, []))


hooks = Hooks()


def _stale_clients():
    """Retention for client records: anyone whose last session ended CLOSED_CLIENT_YEARS ago is offered for deletion."""
    cutoff = (today() - timedelta(days=365 * C.CLOSED_CLIENT_YEARS)).isoformat()
    rows = q("""SELECT h.id, h.name FROM households h WHERE COALESCE(h.demo, 0)=0
                AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.household_id=h.id AND (s.stage NOT IN ('closed','lost')
                                OR COALESCE(s.shoot_date, s.created, '') > ?))
                AND EXISTS (SELECT 1 FROM sessions s WHERE s.household_id=h.id)""", (cutoff,))
    return [dict(text=f"{r['name']}: nothing open for {C.CLOSED_CLIENT_YEARS} years. Review and delete their record?",
                 link=url_for("client_view", hid=r["id"]) + "#their-data") for r in rows]


hooks.today.append(_stale_clients)


UNLOCK_HOOKS = []       # run in order at unlock: core schema first, then plugins (see on_unlock at the end)


class PluginContext:
    def __init__(self, name):
        self.name = name
        self.app, self.hooks, self.q, self.x = app, hooks, q, x
        self.render = render_template
        self.C, self.nice = C, nice
        self.audit, self.advance, self.connect = audit, advance, connect
        self.add_column = dbmod.add_column        # ALTER TABLE ... ADD COLUMN with every part checked
        self.safe_back = safe_back                # a "back" address from a form: local paths only
        self.release_blocked, self.money = release_blocked, money
        self.html_pdf = lambda html_, name_: html_pdf(html_, name_)

    @staticmethod
    def csp(extra=""):
        """The Content-Security-Policy, with extra script sources (e.g. " 'sha256-...'") for a page a plugin serves."""
        return CSP.format(extra=extra)

    def migrate(self, sql):
        """Schema for the plugin. Runs at unlock (the database can't be opened before the owner signs in)."""
        def run():
            con = connect()
            con.executescript(sql)
            con.commit()
            con.close()
        UNLOCK_HOOKS.append(run)

    def on_unlock(self, fn):
        """Run fn once the database is open (after the first sign-in since a restart). Usable as a decorator."""
        UNLOCK_HOOKS.append(fn)
        return fn

    @staticmethod
    def unlocked():
        return datakey.is_unlocked()


PLUGIN_DIRS = [ROOT / "plugins"] + [Path(p_) for p_ in os.environ.get("CRM_PLUGIN_DIRS", "").split(os.pathsep) if p_.strip()]
# Core parts that ship as plugin files but can't be switched off: the documents promise what they do (unedited files
# sealed, then deleted on the agreed day). Retention is core; online galleries stay optional.
CORE_PLUGINS = {"retention"}
DISABLED_PLUGINS = {n.strip() for n in os.environ.get("CRM_PLUGINS_OFF", "").split(",") if n.strip()} - CORE_PLUGINS


def load_plugins():
    """Every *.py in plugins/ and in each folder listed in CRM_PLUGIN_DIRS (your own plugins, kept outside this code),
    in name order per folder; files starting with _ and names in CRM_PLUGINS_OFF are skipped."""
    loaded = []
    for path in (p_ for d_ in PLUGIN_DIRS if d_.is_dir() for p_ in sorted(d_.glob("*.py"))):
        if path.name.startswith("_") or path.stem in DISABLED_PLUGINS or path.stem in loaded:
            continue
        if path.stem in CORE_PLUGINS and path.parent != ROOT / "plugins":
            continue                                    # a core part is never replaced by a file of the same name
        spec = importlib.util.spec_from_file_location(f"plugins.{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            mod.register(PluginContext(path.stem))
        except Exception as e:  # noqa: BLE001 - a broken plugin of your own never stops the app from starting
            if path.parent == ROOT / "plugins":
                raise                                   # the built-in ones must work
            app.logger.exception("plugin %s failed to load", path)
            PLUGIN_ERRORS.append(f"{path.name}: {type(e).__name__}")
            continue
        loaded.append(path.stem)
        PLUGINS_LOADED.append(dict(name=path.stem, built_in=path.parent == ROOT / "plugins", folder=str(path.parent),
                                   label=f"{path.parent.name}/{path.stem}"))
    return loaded


PLUGIN_ERRORS = []      # your plugins that failed to load at this start (shown in Settings)
PLUGINS_LOADED = []     # every plugin loaded at this start: on About (signed in) and in the startup log


# ---------------------------------------------------------------- audit: every change, when, and why

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
TZ = ZoneInfo("UTC")   # replaced by the Settings time zone (apply_settings)


def audit(action, sid=None, hid=None, eid=None, note="", who="Owner", route="", data=""):
    if sid and not hid:
        r = q("SELECT household_id, event_id FROM sessions WHERE id=?", (sid,), one=True)
        if r:
            hid, eid = r["household_id"], eid or r["event_id"]
    x("INSERT INTO audit (at, household_id, session_id, event_id, action, note, who, route, data) VALUES (?,?,?,?,?,?,?,?,?)",
      (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), hid, sid, eid, action, note or "", who, route, data))


def _title(tid):
    r = q("SELECT title, session_id, event_id, done FROM tasks WHERE id=?", (tid,), one=True)
    return r


def _old_date(sid):
    r = q("SELECT shoot_date FROM sessions WHERE id=?", (sid,), one=True) if sid else None
    return r["shoot_date"] if r else None


def describe(ep, va, f):
    """A plain sentence for what this POST is about to do, plus who it concerns. Computed before the change."""
    sid, hid, eid = va.get("sid"), va.get("hid"), va.get("eid")
    act = None
    if ep == "task_toggle":
        t = _title(va["tid"])
        if t:
            sid, eid = t["session_id"], t["event_id"] or eid
            act = (f"Removed step '{t['title']}'" if f.get("delete") else
                   f"Reopened '{t['title']}'" if t["done"] else f"Marked '{t['title']}' done")
    elif ep == "close_step":
        outcome = OUTCOMES.get(f.get("outcome"), f.get("outcome"))
        said = (f.get("said_at") or "").replace("T", " ")
        if said:
            g.said_prefix = f"Said {nice(said[:10])}, {ampm(said[11:16])}. "
        if f.get("task"):
            t = _title(int(f["task"]))
            if t:
                sid, eid = t["session_id"], t["event_id"]
                act = f"Closed '{t['title']}' as {outcome}"
        else:
            sid = int(f["sid"]) if f.get("sid") else None
            act = f"Closed '{AUTO.get(f.get('step'), (f.get('step'),))[0]}' as {outcome}"
    elif ep == "quick":
        a = f.get("act")
        act = {"sign": f"Marked the {f.get('kind', 'release')} signed",
               "mark": f"Marked '{MARKABLE.get(f.get('field'), f.get('field'))}' for today",
               "pay": f"Recorded the balance paid ({money(totals(sid)['due']) if sid else ''})",
               "extend": "Gave the gallery its one extension",
               "stage": f"Moved to {C.STAGE_LABELS.get(f.get('stage'), f.get('stage'))}",
               "date": (f"Moved the shoot from {nice(_old_date(sid))} to {nice(f.get('shoot_date'))}" if _old_date(sid) else
                        f"Set the shoot date to {nice(f.get('shoot_date'))}") + (f", {ampm(f.get('start_time'))}" if f.get("start_time") else ""),
               "favorites": f"Recorded the client's portfolio suggestions: {f.get('favorites', '')}" + (f" (don't show: {f['veto']})" if f.get("veto") else "")}.get(a)
    elif ep == "session_stage":
        act = f"Moved to {C.STAGE_LABELS.get(f.get('stage'), f.get('stage'))}"
    elif ep == "event_stage":
        act = f"Moved every family to {C.STAGE_LABELS.get(f.get('stage'), f.get('stage'))}"
    elif ep == "event_releases":
        act = ("Marked this family's releases signed" if f.get("session_id") else "Marked every release signed") + f" on {nice(f.get('signed_on')) or 'the event day'}"
        if f.get("session_id"):
            sid = int(f["session_id"])
    elif ep == "event_mark":
        act = f"Marked '{MARKABLE.get(f.get('field'), f.get('field'))}' for every family"
    elif ep == "event_date":
        old = (q("SELECT date FROM events WHERE id=?", (eid,), one=True) or {"date": None})["date"]
        act = f"Moved the event from {nice(old)} to {nice(f.get('date'))}" if old else f"Moved the event to {nice(f.get('date'))}"
    elif ep == "event_task_done":
        act = f"Marked '{f.get('title')}' done for the whole event"
    elif ep == "doc_save":
        dd = q("SELECT d.*, p.name person FROM documents d LEFT JOIN people p ON p.id=d.about_person WHERE d.id=?", (va["did"],), one=True)
        if dd:
            sid = dd["session_id"]
            what = dd["kind"].capitalize() + (f" for {dd['person']}" if dd["person"] else "")
            if f.get("delete"):
                act = f"Removed {what}"
            elif f.get("cycle"):
                order = ["needed", "sent", "signed"]
                act = f"{what}: {dd['status']} -> {order[(order.index(dd['status']) + 1) % 3]}"
            elif f.get("signed_on"):
                act = f"{what}: signed on {nice(f['signed_on'])}"
            elif "note" in f:
                act = f"{what}: paper kept '{f.get('note')}'"
            elif f.get("status") in ("needed", "sent", "signed"):
                act = f"{what}: marked {f['status']}"
    elif ep == "money_add":
        w = f.get("what")
        act = {"line": f"Added quote line '{f.get('label')}' {money(num(f.get('amount')))}",
               "payment": f"Recorded {f.get('kind', 'payment')} {money(num(f.get('amount')))}" + (f" by {f['method']}" if f.get("method") else "")
                          + (f" #{f['ref'].strip()}" if (f.get("ref") or "").strip() else ""),
               "expense": f"Added expense '{f.get('label')}'" + (f" {f.get('miles')} mi" if f.get("miles") else "") + (f" {money(num(f['amount']))}" if f.get("amount") else "")}.get(w)
    elif ep == "money_delete":
        table = {"line": "lines", "payment": "payments", "expense": "expenses"}.get(va.get("what"))
        r = q("SELECT * FROM " + dbmod.ident(table) + " WHERE id=?", (va["rid"],), one=True) if table else None
        if r:
            sid = r["session_id"]
            act = f"Deleted {va['what']} " + (r["label"] if "label" in r.keys() else "") + f" {money(r['amount'])}"
    elif ep == "log_add":
        hid = int(f["household_id"]) if f.get("household_id") else None
        sid = int(f["session_id"]) if f.get("session_id") else None
        act = f"Logged {f.get('channel', 'note')}: {f.get('summary')}"
    elif ep == "task_add":
        act = f"Added step '{f.get('title')}'"
    elif ep == "session_intake":
        act = "Updated the intake answers"
    elif ep == "session_save":
        act = "Edited the session details"
    elif ep == "session_delivery":
        act = "Updated delivery dates"
    elif ep == "session_delete":
        r_ = q("SELECT s.household_id, s.event_id, s.type, s.shoot_date FROM sessions s WHERE s.id=?", (sid,), one=True)
        if r_:
            hid, eid = r_["household_id"], r_["event_id"]
            act = f"Deleted the {C.SESSION_TYPES.get(r_['type'], r_['type'])} session" + (f" ({nice(r_['shoot_date'])})" if r_["shoot_date"] else "")
        else:
            act = "Deleted a session"
    elif ep == "client_save":
        act = "Edited client details"
    elif ep == "person_add":
        act = f"Added {f.get('name')}"
    elif ep == "person_save":
        p_ = q("SELECT * FROM people WHERE id=?", (va["pid"],), one=True)
        if p_:
            hid = p_["household_id"]
            if f.get("delete"):
                act = f"Removed {p_['name']}"
            else:
                ch = []
                for k, label in (("name", "name"), ("relation", "relation"), ("gender", "gender"), ("email", "email"), ("phone", "phone")):
                    if k in f and (f.get(k) or "") != (p_[k] or "") and (f.get(k) or k in ("email", "phone")):
                        ch.append(f"{label} {p_[k] or '-'} -> {f.get(k) or '-'}")
                if f.get("age") and str(f["age"]).isdigit() and age_of(p_) != int(f["age"]):
                    ch.append(f"age ~{age_of(p_)} -> ~{f['age']}")
                if "grad_year" in f and str(f.get("grad_year") or "") != str(p_["grad_year"] or ""):
                    ch.append(f"class of {p_['grad_year'] or '-'} -> {f.get('grad_year') or '-'}")
                act = f"Edited {p_['name']}: " + "; ".join(ch) if ch else f"Saved {p_['name']} (no changes)"
    elif ep == "face_upload":
        act = "Removed the reference photo" if f.get("remove") else "Set a new reference photo"
    elif ep in ("client_new_flow", "client_new_session"):
        act = f"Started a {C.SESSION_TYPES.get(f.get('type'), f.get('type'))} workflow"
    elif ep == "inquiry":
        hh = (q("SELECT name FROM households WHERE id=?", (f.get("household_id"),), one=True)
              if str(f.get("household_id") or "").isdigit() else None)
        who_ = f.get("household_name") or f.get("contact_name") or (hh["name"] if hh else "") or "a new client"
        act = f"New inquiry: {who_} ({C.SESSION_TYPES.get(f.get('type'), '')})"
    elif ep == "event_add":
        act = f"Created event '{f.get('name')}'"
    elif ep == "event_family":
        act = f"Added {f.get('household_name')} to the event"
    elif ep == "event_edit":
        act = "Edited the event details"
    elif ep == "settings_view":
        act = "Changed settings"
    elif ep == "setup_step":
        import setup as _setup
        title = dict((s_, t_) for s_, t_, _r in _setup.STEPS).get(va.get("step"), va.get("step"))
        act = f"Setup: {'skipped' if f.get('skip') else 'saved'} {str(title).lower()}"
    elif ep == "client_note":
        act = ""
    elif ep == "note_delete":
        r_ = q("SELECT * FROM log WHERE id=?", (va.get("lid"),), one=True)
        if r_:
            hid, sid = r_["household_id"], r_["session_id"]
            act = f"Deleted a note from {nice(r_['at'][:10])}: {r_['summary'][:80]}"
    if act is None:
        act = EP_WORDS.get(ep) or app.config.get("ep_words", {}).get(ep) or ep.replace("_", " ").capitalize()
    return act, sid, hid, eid


# plain sentences for History
EP_WORDS = {"quote_send": "Made the quote to send", "quote_fee": "Added a fee to the quote", "quote_discount": "Set the discount", "type_walk": "Walked through a session type's settings", "walk_dismiss": "Dismissed the settings walkthrough", "portfolio_set": "Changed portfolio permission",
            "quote_requote": "Requoted at current fees", "quote_exempt": "Changed the sales tax setting",
            "upload_gallery_req": "Asked to upload the gallery", "build_gallery": "Started building the gallery",
            "gallery_sync_now": "Checked the gallery", "retention_raw": "Picked the RAW folder",
            "retention_keep": "Kept the RAWs another year", "vault_new": "Started creating the vault master key", "retention_seal": "Sealed the photos",
            "retention_unseal": "Opened the sealed photos for a reprint",
            "vault_confirm": "Confirmed the vault master key was saved", "vault_schedule": "Changed the daily backup time", "retention_early": "Deleted the photos early at the client's written request", "overhead_add": "Added a business expense",
            "quick": "Updated the session", "settings_import": "Imported settings from a file", "setup_hide_list": "Hid the finish-setting-up list", "contact_new": "Added a contact", "settings_reset": "Reset the database"}


QUIET_ENDPOINTS = {"gallery_status", "client_delete", "vault_check", "vault_backup_now", "wording_edit"}   # client_delete writes its own nameless line   # plugins add theirs to app.config["quiet_endpoints"]


@app.after_request
def _commit_request(resp):
    """Runs last (registered first): a saving request's changes, its History line and the stage moves it caused commit
    together. A failed request (an error page) commits nothing."""
    if g.pop("tx", None) and "db" in g:
        if resp.status_code < 400:
            try:
                _snapshot_signed()
            except Exception:
                app.logger.exception("signed-document snapshot")
            g.db.commit()
        else:
            g.db.rollback()
            if g.pop("applied", None):
                apply_settings()
    return resp


def _snapshot_signed():
    """A signed agreement or release keeps the words it was signed with: the first save that sees it signed
    stores the document as it read then, and every later view shows that copy. Unsigning moves the copy to
    doc_snapshots_old (kept, never deleted). One document that fails is logged and skipped, never blocking the rest."""
    def store(kind, ref, view):
        try:
            data = json.dumps(view(ref), default=str)
        except Exception:
            if (kind, ref) not in _SNAP_FAILED:
                _SNAP_FAILED.add((kind, ref))
                app.logger.exception("signed-document snapshot %s %s", kind, ref)
            return
        x("INSERT OR REPLACE INTO doc_snapshots (kind, ref, at, data) VALUES (?,?,?,?)", (kind, ref, today().isoformat(), data))
    for r_ in q("""SELECT DISTINCT session_id FROM documents d WHERE kind='agreement' AND status='signed'
                   AND NOT EXISTS (SELECT 1 FROM doc_snapshots WHERE kind='agreement' AND ref=d.session_id)"""):
        store("agreement", r_[0], agreement_view)
    for r_ in q("""SELECT id FROM documents d WHERE kind='release' AND status='signed'
                   AND NOT EXISTS (SELECT 1 FROM doc_snapshots WHERE kind='release' AND ref=d.id)"""):
        store("release", r_[0], release_view)
    gone = """(kind='agreement' AND ref NOT IN (SELECT session_id FROM documents WHERE kind='agreement' AND status='signed'))
              OR (kind='release' AND ref NOT IN (SELECT id FROM documents WHERE kind='release' AND status='signed'))"""
    x("INSERT INTO doc_snapshots_old (kind, ref, at, data, moved) SELECT kind, ref, at, data, ? FROM doc_snapshots WHERE " + gone,
      (today().isoformat(),))
    x("DELETE FROM doc_snapshots WHERE " + gone)


_SNAP_FAILED = set()

def legacy_signed(kind, ref, v):
    """A document signed before signed copies were kept shows today's wording: say so on screen."""
    r_ = q("SELECT data FROM doc_snapshots WHERE kind=? AND ref=?", (kind, ref), one=True)
    if r_ and '"legacy"' in r_["data"]:
        v = dict(v)
        v["legacy_signed"] = True
    return v


def signed_copy(kind, ref):
    """The stored copy of a signed agreement (ref = session) or release (ref = document), or None."""
    r_ = q("SELECT data FROM doc_snapshots WHERE kind=? AND ref=?", (kind, ref), one=True)
    if not r_:
        return None
    v = json.loads(r_["data"])
    if v.get("legacy"):                       # signed before copies were kept: the live document, as before
        return None
    v["custom"] = Markup(v.get("custom") or "")
    v["unsigned"] = False
    return v


DATE_FIELDS = ("shoot_date", "received", "signed_on", "on", "date", "gallery_built", "letter_mailed", "first_visit",
               "sd_shipped", "asked_on", "due", "event_date")


@app.before_request
def _session_exists():
    """Any page for one session (sid in its address) is a 404 once that session is gone, never a half-done save."""
    sid_ = (request.view_args or {}).get("sid")
    if sid_ is not None and datakey.is_unlocked() and request.method == "POST" \
            and not q("SELECT 1 FROM sessions WHERE id=?", (sid_,), one=True):
        abort(404)


@app.before_request
def _dates_are_real():
    """A typed date must be a real day between 1900 and 2100, or the save is refused with the form intact rather than
    failing part way (a year like 9999 overflowed the due-date arithmetic)."""
    if request.method != "POST" or not request.form or not _allowed(request.remote_addr) or not _same_origin():
        return None                                       # refused anyway, by the network and origin checks below
    for k_ in DATE_FIELDS:
        v_ = (request.form.get(k_) or "").strip()
        if not v_:
            continue
        try:
            dv_ = date.fromisoformat(v_[:10])
            ok_ = 1900 <= dv_.year <= 2100
        except ValueError:
            ok_ = False
        if not ok_:
            session["notice"] = f"{v_} isn't a date this can use. Nothing was changed."
            g.refused = True
            return redirect(safe_back(request.form.get("back")) or safe_back(request.referrer and urlparse_path(request.referrer)) or url_for("home"))
    return None


def urlparse_path(u):
    from urllib.parse import urlsplit
    p_ = urlsplit(u)
    return p_.path + (("?" + p_.query) if p_.query else "")


@app.before_request
def _audit_before():
    if request.method != "POST" or not request.endpoint or request.endpoint == "static" or not datakey.is_unlocked():
        return
    if request.endpoint in QUIET_ENDPOINTS | app.config.get("quiet_endpoints", set()) or not _allowed(request.remote_addr):
        return
    try:
        f = request.form if request.form else {}
        g.audit = describe(request.endpoint, request.view_args or {}, f) + (f.get("note_why", ""),)
        if g.get("said_prefix"):
            g.audit = g.audit[:4] + ((g.said_prefix + g.audit[4]).strip(),)
    except Exception:
        app.logger.exception("audit describe failed")
        g.audit = (request.endpoint, None, None, None, "")


@app.after_request
def _audit_after(resp):
    a = g.pop("audit", None)
    if a:
        g.touched_sid = a[1]                 # the session this action touched: only it is advanced afterwards
    if a and a[0] and resp.status_code < 400 and not g.get("refused"):     # a refused action leaves no History line
        act, sid, hid, eid, note = a
        note = g.pop("audit_note", None) or note          # a route may add detail to its own History line
        if request.endpoint == "inquiry" and not hid:
            r = q("SELECT id, household_id FROM sessions ORDER BY id DESC LIMIT 1", one=True)
            sid, hid = (r["id"], r["household_id"]) if r else (None, None)
        try:
            audit(act, sid, hid, eid, note=note, route=request.path)
        except Exception:
            app.logger.exception("audit write failed")
    return _advance_after_post(resp)


# ---------------------------------------------------------------- access

def _allowed(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except (ValueError, TypeError):
        return False
    return any(ip in n for n in ALLOW)


SHORT_FIELDS = {"name", "household_name", "contact_name", "email", "phone", "title", "label", "signer", "business_name",
                "photographer_name", "studio_name", "business_email", "business_phone", "contact_email", "venue", "school"}
SHORT_MAX, LONG_MAX = 200, 20000


@app.before_request
def input_limits():
    """Every form field has a length limit at the boundary: names and contact details 200 characters, anything else
    (notes, addresses, agreements) 20,000. Over the limit is refused, never silently cut."""
    if request.method != "POST" or not request.form:
        return None
    for k, v in request.form.items(multi=True):
        limit = SHORT_MAX if (k in SHORT_FIELDS or k.endswith(("_name", "_email", "_phone"))) else LONG_MAX
        if len(v) > limit:
            return f"'{k[:40]}' is too long (the limit is {limit} characters).\n", 413
    return None


@app.before_request
def allowed_networks_only():
    if not _allowed(request.remote_addr) and not netsec.health_probe(request):
        abort(403)
    if request.method == "POST" and not _same_origin():
        abort(403)


def _same_origin():
    """CSRF guard: the network gate alone let any page open on an allowed device post a hidden form here
    (reset, voids, uploads). Browsers say where a request came from; anything cross-site is refused."""
    sfs = request.headers.get("Sec-Fetch-Site")
    if sfs:
        return sfs in ("same-origin", "none")
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if src and src != "null":
        from urllib.parse import urlsplit
        return urlsplit(src).netloc == request.host
    return src != "null"     # no browser provenance at all (scripts, tests): the IP gate decides


@app.context_processor
def globals_():
    def take_notice():
        """The failed-attempt notice, shown once by whichever layout renders it (and kept until one does)."""
        from flask import session as _fs
        return _fs.pop("notice", None)

    def import_result():
        """What the last settings import did (kept until shown; a pending plugin list stays until answered)."""
        from flask import session as _fs
        r = _fs.get("import_result")
        if r and r.get("plugins") is None and not r.get("gallery"):
            _fs.pop("import_result", None)
        return r
    return dict(take_notice=take_notice, import_result=import_result, C=C, hooks=hooks, today=today(), stage_label=C.STAGE_LABELS,
                type_label=C.SESSION_TYPES, money=money, nice=nice, light=light, face=face, ampm=ampm, OUTCOMES=OUTCOMES, sym_group=sym_group, delta3=timedelta(days=3),
                who_is=who_is, age_of=age_of, age_label=age_label, RELATIONS=RELATIONS,
                DOC_DISCLAIMER=__import__("wording").DISCLAIMER)


def light(dstr, stype=None):
    """'golden 4:24 - sunset 5:04' pieces for a date string, or None (also for a type shot indoors)."""
    if not dstr or (stype and stype in C.INDOOR_TYPES):
        return None
    try:
        g_, s_ = sun.evening(d(dstr[:10]))
    except ValueError:
        return None
    return dict(golden=sun.fmt(g_), sunset=sun.fmt(s_))


FACES = DB_PATH.parent / "faces"
FACE_COLORS = ["#8a5a3c", "#5f7d5a", "#4f6b8a", "#7a5a86", "#8a6d2f", "#3f7a78", "#8a4b5b", "#5a6170"]


def face(hid):
    """Reference photo for a client, or initials on a steady color (worked out once per request per client)."""
    cache = g.setdefault("face_cache", {})
    if hid not in cache:
        cache[hid] = _face(hid)
    return cache[hid]


def _face(hid):
    if not hid:
        return dict(url=None, initials="?", color=FACE_COLORS[0])
    f = FACES / f"{hid}.jpg"
    if f.exists():
        return dict(url=url_for("face_img", hid=hid) + f"?v={int(f.stat().st_mtime)}", initials="", color="")
    p = q("""SELECT name FROM people WHERE household_id=? ORDER BY role='child' DESC, role='parent' ASC, id LIMIT 1""",
          (hid,), one=True)
    name = p["name"] if p else (q("SELECT name FROM households WHERE id=?", (hid,), one=True) or {"name": "?"})["name"]
    words = [w for w in name.replace("The ", "").split() if w[:1].isalpha()]
    if len(words) == 1:
        hh = (q("SELECT name FROM households WHERE id=?", (hid,), one=True) or {"name": ""})["name"]
        hw = [w for w in hh.replace("The ", "").split() if w[:1].isalpha()]
        if hw and hw[0][0].lower() != words[0][0].lower():
            words.append(hw[0])
    initials = "".join(w[0] for w in (words[:1] + words[-1:] if len(words) > 1 else words)).upper() or "?"
    return dict(url=None, initials=initials, color=FACE_COLORS[hid % len(FACE_COLORS)])


RELATIONS = ["mother", "father", "daughter", "son", "grandmother", "grandfather", "wife", "husband", "partner",
             "sister", "brother", "aunt", "uncle", "guardian", "self", "friend", "pet", "other"]
PARENT_REL = {"mother", "father", "grandmother", "grandfather", "guardian", "aunt", "uncle"}
CHILD_REL = {"daughter", "son"}
ANIMAL_REL = {"pet", "dog", "cat", "horse", "pony", "rabbit", "bird", "animal", "puppy", "kitten"}   # never a minor or a signer
GENDER_OF = {"mother": "F", "daughter": "F", "grandmother": "F", "wife": "F", "sister": "F", "aunt": "F",
             "father": "M", "son": "M", "grandfather": "M", "husband": "M", "brother": "M", "uncle": "M"}


def age_of(p):
    """Approximate age from the stored birth year, else from a graduation year (seniors finish at ~18)."""
    keys = p.keys() if hasattr(p, "keys") else p
    if "birth_date" in keys and p["birth_date"]:
        try:
            b_ = date.fromisoformat(p["birth_date"][:10])
            return max(0, today().year - b_.year - ((today().month, today().day) < (b_.month, b_.day)))
        except ValueError:
            pass
    if "birth_year" in keys and p["birth_year"]:
        return today().year - p["birth_year"]
    if p["grad_year"]:
        school_end = today().year + (1 if today().month >= 7 else 0)
        return 17 - (p["grad_year"] - school_end)
    return None


def age_label(p):
    """A baby's age in weeks or months from a birth date ('6 weeks', '4 months', 'due Mar 3'); else '~N' years."""
    keys = p.keys() if hasattr(p, "keys") else p
    bd = p["birth_date"] if "birth_date" in keys else None
    if bd:
        try:
            born = date.fromisoformat(bd[:10])
        except ValueError:
            born = None
        if born:
            days = (today() - born).days
            if days < 0:
                return f"due {nice(bd)}"
            if days < 7 * 13:
                return f"{days // 7} week{'' if days // 7 == 1 else 's'}" if days >= 7 else "newborn"
            if days < 730:
                m_ = days * 12 // 365
                return f"{m_} months"
    a_ = age_of(p)
    if a_ is None:
        return ""
    return f"about {a_}" if ("age_approx" in keys and p["age_approx"]) else str(a_)


def who_is(p):
    """'Ava, daughter, F, ~17' - the bits that are known."""
    keys = p.keys()
    bits = [p["name"].split()[0] if len(p["name"].split()) > 1 else p["name"]]
    rel = p["relation"] if "relation" in keys else ""
    if rel:
        bits.append(rel)
    g_ = (p["gender"] if "gender" in keys else "") or GENDER_OF.get(rel or "", "")
    if g_:
        bits.append(g_)
    a_ = age_label(p)
    if a_:
        bits.append(a_)
    return ", ".join(bits)


def majority_age():
    """Age of majority where the photographer works (Settings; 18 unless set): minor status decides who signs."""
    try:
        return max(14, min(25, int(setting("age_majority") or 18)))
    except ValueError:
        return 18


app.jinja_env.globals["majority_age"] = majority_age


def person_fields(f, cur=None):
    """Form -> (relation, gender, birth_year, role, is_minor, age_approx). Anything the form leaves blank keeps `cur`'s
    value. The Minor box, wherever the form shows it, always wins; a typed age only fills it in. An approximate age near
    the age of majority never decides minor status on its own."""
    rel = (f.get("relation") or "").strip().lower() or ((cur["relation"] or "") if cur else "")
    gender = f.get("gender") or GENDER_OF.get(rel, "") or ((cur["gender"] or "") if cur else "")
    age = str(f.get("age") or "").strip()
    if age.isdigit() and cur is not None and age_of(cur) == int(age):
        by = cur["birth_year"]             # the age shown, saved back unchanged: never shift the birth year (a January save)
    else:
        by = today().year - int(age) if age.isdigit() else (cur["birth_year"] if cur else None)
    if rel in ANIMAL_REL:
        role = "pet"
    elif rel in PARENT_REL:
        role = "parent"
    elif rel in CHILD_REL:
        role = "child"
    else:
        role = f.get("role") or (cur["role"] if cur else "adult")
    approx = (1 if f.get("age_approx") else 0) if age.isdigit() else ((cur["age_approx"] if cur and "age_approx" in cur.keys() else 0) or 0)
    maj = majority_age()
    if f.get("minor_shown"):
        minor = 1 if f.get("is_minor") else 0
    elif age.isdigit() and not (approx and abs(int(age) - maj) <= 2):
        minor = 1 if int(age) < maj else 0
    elif f.get("is_minor"):
        minor = 1
    else:
        minor = cur["is_minor"] if cur else 0
    if role == "pet":                     # a 12-year-old dog is not a child: no guardian clause, no "school"
        return rel, gender, by, role, 0, approx
    if age.isdigit() and approx and abs(int(age) - maj) <= 2 and not f.get("minor_shown"):
        session["notice"] = f"About {age}: tick Minor if they are under {maj}. An approximate age doesn't decide it."
    return rel, gender, by, role, minor, approx


def _past_day(s):
    """A real date, today or earlier."""
    try:
        return date.fromisoformat(str(s)[:10]) <= today()
    except ValueError:
        return False


def now_local():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def ampm(hhmm):
    """'16:10' -> '4:10 pm'."""
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        return hhmm or ""
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h < 12 else 'pm'}"


def money(v):
    """$1,250 or $12.50; a negative amount reads -$125 (never $-125)."""
    v = round(float(v or 0), 2)
    a = abs(v)
    return ("-" if v < 0 else "") + (f"${a:,.0f}" if a.is_integer() else f"${a:,.2f}")


def nice(s):
    if not s:
        return ""
    try:
        dt = d(s[:10])
        return dt.strftime("%a %b %-d") + ("" if dt.year == today().year else dt.strftime(", %Y"))
    except ValueError:
        return s


# ---------------------------------------------------------------- domain logic

def session_full(sid):
    s = q("""SELECT s.*, h.name AS household, h.address AS address, e.name AS event_name FROM sessions s
             JOIN households h ON h.id = s.household_id LEFT JOIN events e ON e.id = s.event_id
             WHERE s.id=?""", (sid,), one=True)
    if not s:
        abort(404)
    return s


# ---------------------------------------------------------------- quotes

def current_schedule():
    r = q("SELECT * FROM fee_schedules ORDER BY id DESC LIMIT 1", one=True)
    return dict(id=r["id"], created=r["created"], note=r["note"], items=json.loads(r["items"]))


def schedule(sched_id):
    r = q("SELECT * FROM fee_schedules WHERE id=?", (sched_id,), one=True)
    return dict(id=r["id"], created=r["created"], note=r["note"], items=json.loads(r["items"])) if r else None


def quote_term():
    v = setting("quote_term_days")
    return int(v) if str(v).isdigit() else C.QUOTE_TERM_DAYS


def quote_of(sid):
    r = q("SELECT * FROM quotes WHERE session_id=?", (sid,), one=True)
    if not r:
        return None
    d_ = dict(r)
    d_["expired"] = bool(d_["expires"]) and d_["expires"] < today().isoformat()
    d_["schedule"] = schedule(d_["schedule_id"])
    return d_


def ensure_quote(sid):
    if not q("SELECT 1 FROM quotes WHERE session_id=?", (sid,), one=True):
        x("INSERT INTO quotes (session_id, schedule_id, created, expires) VALUES (?,?,?,?)",
          (sid, current_schedule()["id"], now_local(), (today() + timedelta(days=quote_term())).isoformat()))


def fee_amount(item, qty):
    unit = float(item["amount"] or 0)
    if item.get("unit") == "mile" and not unit:
        unit = float(setting("mileage_rate") or 0)
    return unit, round(unit * qty, 2)


def reapply_discount(sid):
    """Discount then tax: both are generated lines, rebuilt whenever the quote changes, so totals stay a plain sum."""
    _reapply_portfolio(sid)
    _reapply_discount(sid)
    reapply_tax(sid)
    _invoice_still_sent(sid)


def _reapply_portfolio(sid, force=False):
    """The portfolio permission discount: a generated line while the client allows portfolio use and the session's type
    offers one. Declining it (or a type without one) takes the line off. force: the client changed the permission
    itself, which the agreement lets move the price until delivery."""
    s_ = q("SELECT type, portfolio_no FROM sessions WHERE id=?", (sid,), one=True)
    if s_ and not force and not q(UNAGREED_SQL + " AND id=?", (s_["type"], sid), one=True):
        return                                # agreed (quote sent, paid or signed): its price lines stay as agreed
    x("DELETE FROM lines WHERE session_id=? AND kind='portfolio'", (sid,))
    rule = C.PORTFOLIO_DISCOUNT.get(s_["type"]) if s_ else None
    if not rule or s_["portfolio_no"]:
        return
    base = q("""SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?
                AND kind NOT IN ('discount','passthrough','tax','credit','portfolio')""", (sid,), one=True)["v"]
    if base <= 0:
        return
    amt = round(min(base * rule[1] / 100 if rule[0] == "pct" else rule[1], base), 2)
    if amt > 0.005:
        x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'portfolio', 1)",
          (sid, f"Portfolio permission ({C.portfolio_discount_words(s_['type'])} off): I may show up to {C.MARKETING_PICKS}"
                f" photos; you may suggest favorites and say no to any before it is shown", -amt))
    g.pop("tot_cache", None)


@app.post("/session/<int:sid>/portfolio")
def portfolio_set(sid):
    """The client allows portfolio use (and keeps the discount, when the type offers one), or declines it."""
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    want = request.form.get("set")
    if want not in ("allowed", "declined"):
        abort(400)
    s_now = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    x("UPDATE sessions SET portfolio_no=? WHERE id=?", (1 if want == "declined" else 0, sid))
    if s_now["stage"] in ("delivered", "closed") or (totals(sid)["due"] <= 0.005 and totals(sid)["paid"] > 0):
        session["notice"] = "Recorded. The photos were already delivered (or paid in full), so the discount stays, as the agreement says."
        return redirect(safe_back(request.form.get("back")) or stage_url(sid) + "#billing")
    had_ = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind='portfolio'", (sid,), one=True)["v"]
    _reapply_portfolio(sid, force=True)                  # before delivery the agreement adds the discount back (or again)
    reapply_discount(sid)
    now_ = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind='portfolio'", (sid,), one=True)["v"]
    if abs(now_ - had_) > 0.005:
        session["notice"] = ("The portfolio discount was added back to the balance, as the agreement says." if want == "declined"
                             else "The portfolio discount is on the balance again.")
    return redirect(safe_back(request.form.get("back")) or stage_url(sid) + "#billing")


def _reapply_discount(sid):
    x("DELETE FROM lines WHERE session_id=? AND kind='discount'", (sid,))
    qt = q("SELECT * FROM quotes WHERE session_id=?", (sid,), one=True)
    if not qt or not qt["discount_pct"]:
        return
    # Pass-through costs (permits, mileage, travel) are never discounted: a free job still covers its permit.
    sub = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind NOT IN ('discount','passthrough','tax','credit')",
            (sid,), one=True)["v"]
    if sub <= 0:
        return
    pct = float(qt["discount_pct"])
    reason = next((lab for k, lab, _ in C.DISCOUNTS if k == qt["discount_reason"]), qt["discount_reason"]) or "Discount"
    x("INSERT INTO lines (session_id, label, amount, kind) VALUES (?,?,?, 'discount')",
      (sid, f"Discount: {reason} ({pct:g}%)", -round(sub * pct / 100, 2)))
    g.pop("tot_cache", None)


def _pct(key):
    try:
        return min(25.0, max(0.0, float(setting(key) or 0)))     # a typo (150%) never reaches a quote
    except ValueError:
        return 0.0


def tax_region():
    """A regional tax rule from a plugin (hooks.tax_region), when Settings chose it; else None."""
    return hooks.tax_region if setting("tax_mode") == "regional" and hooks.tax_region else None


def sales_tax_pct(area=None):
    """The rate for a sale: none, one flat rate for every client, or (regional) the base rate plus whatever the
    region plugin adds for the area the client takes delivery in."""
    if setting("tax_mode", "none") == "none":
        return 0.0
    base = _pct("sales_tax_pct")
    reg = tax_region()
    if not reg or not base:
        return base
    return base + float(reg["extra_pct"](area, _pct("local_tax_pct")) or 0)


def tax_area_of(sid):
    """Where a sale is taxed (regional mode only): a manual pick wins, else the plugin reads the client's address.
    Returns (area, warning or '')."""
    reg = tax_region()
    if not reg:
        return "flat", ""
    s = q("""SELECT s.tax_area, h.address FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""",
          (sid,), one=True)
    keys = [k for k, _ in reg["areas"]]
    if not s:
        return keys[0], ""
    if s["tax_area"] in keys:
        return s["tax_area"], ""
    return reg["area_of"](s["address"] or "")


def session_tax_rate(sid):
    """The rate a session is taxed at. Open quotes follow today's rate; once the client says yes (a payment, or the
    session is booked) the rate is kept, so a later change in Settings never re-taxes a past or booked job."""
    r_ = q("SELECT tax_rate FROM sessions WHERE id=?", (sid,), one=True)
    if r_ and r_["tax_rate"] is not None:
        return float(r_["tax_rate"])
    return sales_tax_pct(tax_area_of(sid)[0])


def lock_tax_rate(sid):
    x("UPDATE sessions SET tax_rate=? WHERE id=? AND tax_rate IS NULL", (sales_tax_pct(tax_area_of(sid)[0]), sid))


def kept_deposit_tax(sid):
    """A canceled booking whose deposit is kept: tax on it at the full rate, paid by the business."""
    s = q("SELECT stage, lost_reason, tax_exempt FROM sessions WHERE id=?", (sid,), one=True)
    if not s or s["stage"] != "lost" or s["lost_reason"] not in ("Canceled", "No-show") or s["tax_exempt"]:
        return 0.0
    paid = q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True)["v"]
    return round(max(paid, 0) * session_tax_rate(sid) / 100, 2)


def reapply_tax(sid):
    """Sales tax is one generated line on the TAXABLE lines only (each fee carries its own taxable flag), after the
    discount those lines received. Off when Settings has no rate or the client is exempt (certificate on file)."""
    x("DELETE FROM lines WHERE session_id=? AND kind='tax'", (sid,))
    s = q("SELECT tax_exempt FROM sessions WHERE id=?", (sid,), one=True)
    rate = session_tax_rate(sid) if s else 0.0
    if not rate or not s or s["tax_exempt"]:
        g.pop("tot_cache", None)
        return
    qt = q("SELECT discount_pct FROM quotes WHERE session_id=?", (sid,), one=True)
    pct = float(qt["discount_pct"] or 0) if qt else 0.0
    r = q("""SELECT COALESCE(SUM(CASE WHEN kind!='passthrough' THEN amount END),0) d,
                    COALESCE(SUM(CASE WHEN kind='passthrough' THEN amount END),0) p
             FROM lines WHERE session_id=? AND taxable=1 AND kind NOT IN ('discount','tax')""", (sid,), one=True)
    base = r["d"] * (1 - pct / 100) + r["p"]
    if base > 0:
        x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'tax', 0)",
          (sid, f"Sales tax ({rate:g}%)", round(base * rate / 100, 2)))
    g.pop("tot_cache", None)


def _lines_sig(sid):
    return json.dumps([[r_["label"], round(r_["amount"], 2)] for r_ in
                       q("SELECT label, amount FROM lines WHERE session_id=? ORDER BY id", (sid,))])


def _invoice_still_sent(sid):
    """A sent invoice never changes under its number: if its lines change afterwards, that number is retired, the next
    view issues a new one that says which it replaces, and "Send invoice" is open again."""
    r_ = q("SELECT number, sig FROM invoice_sent WHERE session_id=?", (sid,), one=True)
    if not r_ or r_["sig"] == _lines_sig(sid):
        return
    x("UPDATE doc_numbers SET kind='INV-replaced:' || number WHERE session_id=? AND kind='INV'", (sid,))
    x("DELETE FROM invoice_sent WHERE session_id=?", (sid,))
    x("UPDATE tasks SET done=NULL, outcome=NULL WHERE session_id=? AND title=?", (sid, SEND_TASKS["invoice"]))
    audit(f"Invoice {r_['number']} was already sent and its lines changed: the next invoice replaces it", sid=sid)


def _invoice_marked_sent(sid):
    inv_ = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    if inv_:
        x("INSERT OR REPLACE INTO invoice_sent (session_id, number, sig) VALUES (?,?,?)", (sid, inv_["number"], _lines_sig(sid)))


def totals(sid):
    """Money for one session. The first call in a request works out every session at once (four grouped queries), so
    pages that list many sessions never run a handful of queries per session; any write clears it (_commit)."""
    if "tot_cache" not in g:
        g.tot_cache = _all_totals()
    cache = g.tot_cache
    if sid not in cache:
        cache[sid] = _totals(sid)
    return cache[sid]


def _deposit_for(stype, quoted, has_event, family=False):
    if quoted <= 0 or family:                           # a family at a group event pays its share; no deposit is asked
        return 0.0
    own = C.DEPOSIT_RULE.get(stype)
    if own:                                             # this session type's own deposit
        return round(min(quoted * own[1] / 100 if own[0] == "pct" else own[1], quoted), 2)
    if stype and C.TYPE_KIND.get(stype) == "property" and not C.has_deposit_step(stype):   # real estate, products: none asked
        return 0.0
    if has_event or C.TYPE_KIND.get(stype) == "event":      # a family's share of a group event, too
        return round(quoted * C.EVENT_DEPOSIT_PCT / 100, 2)
    return round(min(C.DEPOSIT_AMOUNT, quoted), 2)


def _all_totals():
    rate = float(setting("mileage_rate") or 0)
    ln = {r[0]: r for r in q("""SELECT session_id, COALESCE(SUM(amount),0), COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0),
                                       MAX(fee_key='event') FROM lines GROUP BY session_id""")}
    pd = {r[0]: r[1] for r in q("SELECT session_id, COALESCE(SUM(amount),0) FROM payments WHERE voided IS NULL GROUP BY session_id")}
    ex = {r[0]: r for r in q("""SELECT session_id, COALESCE(SUM(amount),0), COALESCE(SUM(miles),0), COALESCE(SUM(miles * COALESCE(rate, ?)),0)
                                FROM expenses GROUP BY session_id""", (rate,))}
    out = {}
    for sid, stype, fam_ in q("SELECT id, type, event_id IS NOT NULL FROM sessions"):
        quoted, tax, ev = (ln[sid][1], ln[sid][2], ln[sid][3]) if sid in ln else (0.0, 0.0, 0)
        paid = pd.get(sid, 0.0)
        a, m, mc = (ex[sid][1], ex[sid][2], ex[sid][3]) if sid in ex else (0.0, 0.0, 0.0)
        dep = _deposit_for(stype, quoted, ev, family=bool(fam_))
        tax_paid = tax * min(1.0, paid / quoted) if quoted > 0.005 and paid > 0 else 0.0
        out[sid] = dict(quoted=quoted, paid=paid, due=quoted - paid, spent=a, miles=m, rate=rate, cost=a + mc,
                        profit=paid - tax_paid - a - mc, deposit_req=dep, deposit_short=max(0.0, dep - paid))
    return out


def _totals(sid):
    quoted = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?", (sid,), one=True)["v"]
    paid = q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True)["v"]
    rate = float(setting("mileage_rate") or 0)
    # each trip keeps the rate in force when it was logged, so a new IRS rate never re-prices old miles
    spent = q("""SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(miles),0) m, COALESCE(SUM(miles * COALESCE(rate, ?)),0) mc
                 FROM expenses WHERE session_id=?""", (rate, sid), one=True)
    cost = spent["a"] + spent["mc"]
    dep = deposit_required(sid, quoted)
    tax = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind='tax'", (sid,), one=True)["v"]
    tax_paid = tax * min(1.0, paid / quoted) if quoted > 0.005 and paid > 0 else 0.0   # tax is collected pro rata
    return dict(quoted=quoted, paid=paid, due=quoted - paid, spent=spent["a"], miles=spent["m"],
                rate=rate, cost=cost, profit=paid - tax_paid - cost, deposit_req=dep, deposit_short=max(0.0, dep - paid))


def deposit_required(sid, quoted=None):
    """Terms: $250 holds the date, 50% for events (any hourly event fee on the quote). Never more than the quote."""
    if quoted is None:
        quoted = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?", (sid,), one=True)["v"]
    if quoted <= 0:
        return 0.0
    st_ = q("SELECT type, event_id FROM sessions WHERE id=?", (sid,), one=True)
    return _deposit_for(st_["type"] if st_ else None,
                        quoted, bool(q("SELECT 1 FROM lines WHERE session_id=? AND fee_key='event'", (sid,), one=True)),
                        family=bool(st_ and st_["event_id"]))


OWED_STAGES = ("shot", "delivered", "closed")


def row_type(row):
    """The session type of a row that may or may not carry it."""
    try:
        return row["type"]
    except (IndexError, KeyError):
        return None


def owed_now(stage, t, shoot_date=None, stype=None):
    """Money is owed once the shoot has happened, or, for a type paid ahead, once its due date before the shoot has
    come. Quotes and other pre-shoot balances are not debts yet."""
    if stype in C.BEFORE_TYPES and shoot_date and stage == "booked":
        return t["due"] > 0.005 and (balance_due_date(shoot_date, stype) or "9999") <= today().isoformat()
    if shoot_date and shoot_date > today().isoformat() and stage not in ("delivered", "closed"):   # delivered: owed regardless
        return False
    return stage in OWED_STAGES and t["due"] > 0.005


def balance_due_date(shoot_date, stype=None, issued=None, sid=None):
    """Net-terms types: net 30 after the invoice date (nothing is late before an invoice exists).
    Everyone else pays at delivery, so the balance is due by the delivery date the Terms promise (14 days, 21 for events)."""
    if stype in C.NET_TERMS_TYPES and issued is None and sid is not None:
        inv_ = q("SELECT issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
        if not inv_ or not inv_["issued"]:
            return None
        issued = inv_["issued"]
    if issued and stype in C.NET_TERMS_TYPES:
        return (d(issued) + timedelta(days=C.BALANCE_NET_DAYS)).isoformat()   # net terms count from the invoice
    if not shoot_date:
        return None
    if stype in C.BEFORE_TYPES:
        return (d(shoot_date) - timedelta(days=C.BALANCE_BEFORE_DAYS)).isoformat()
    if stype in C.NET_TERMS_TYPES:
        return (d(shoot_date) + timedelta(days=C.BALANCE_NET_DAYS)).isoformat()
    days = C.delivery_days(stype)
    return (d(shoot_date) + timedelta(days=days)).isoformat()


def release_blocked(sid):
    """Terms: the SD card and gallery link are released on payment (net-30 real estate excepted). Returns the amount
    still owed when delivery must wait, else 0."""
    s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["type"] in C.NET_TERMS_TYPES:
        return 0.0
    due = totals(sid)["due"]
    return due if due > 0.005 else 0.0


def balance_rule(stype):
    return C.balance_text(stype)


def gallery_expires(s):
    if not s["first_visit"]:
        return None
    return d(s["first_visit"]) + timedelta(days=C.gallery_days(s["type"]) * (2 if s["extension_used"] else 1))


def _fair_due(base, t):
    """Due date from the shoot date. When the date is new or moved, nothing becomes late on the spot: a step
    whose date has already passed is due today instead. An unchanged date keeps its real lateness."""
    if not base:
        return None
    due = (base + timedelta(days=t["offset_days"])).isoformat()
    if not t["done"] and due != t["due"] and due < today().isoformat():
        return today().isoformat()
    return due


def schedule_tasks(sid):
    s = q("SELECT shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    base = d(s["shoot_date"])
    for t in q("SELECT id, offset_days, due, done FROM tasks WHERE session_id=? AND offset_days IS NOT NULL", (sid,)):
        due = _fair_due(base, t)
        db().execute("UPDATE tasks SET due=? WHERE id=?", (due, t["id"]))
    _commit()


def ensure_event_task(eid, stage, title, off, sort):
    if not q("SELECT 1 FROM tasks WHERE event_id=? AND stage=? AND title=?", (eid, stage, title), one=True):
        db().execute("INSERT INTO tasks (event_id, stage, title, offset_days, sort) VALUES (?,?,?,?,?)",
                     (eid, stage, title, off, sort))
        _commit()
    schedule_event(eid)


def schedule_event(eid):
    e = q("SELECT date FROM events WHERE id=?", (eid,), one=True)
    base = d(e["date"]) if e else None
    for t in q("SELECT id, offset_days, due, done FROM tasks WHERE event_id=? AND offset_days IS NOT NULL", (eid,)):
        due = _fair_due(base, t)
        db().execute("UPDATE tasks SET due=? WHERE id=?", (due, t["id"]))
    _commit()


def event_steps(eid):
    return q("SELECT * FROM tasks WHERE event_id=? ORDER BY sort, id", (eid,))


def flow_for(sid):
    """The workflow for a session: its type's, except group events, whose money steps follow the event's billing. With
    no online galleries set up, the steps that need one (gallery built, letter mailed, opened, picks) are left out:
    they could never be done, and the session would never finish."""
    f_ = _flow_for(sid)
    if C.galleries_on():
        return f_
    gal = {"gallery_built", "letter_mailed", "first_visit", "favorites"}
    return {st: [x_ for x_ in steps if not (x_[0] == "auto" and x_[1] in gal)] for st, steps in f_.items()}


def _flow_for(sid):
    """The workflow for a session: its type's, except group events, whose money steps follow the event's billing."""
    s = q("SELECT s.type, s.event_id, s.payer_event, e.billing FROM sessions s LEFT JOIN events e ON e.id=s.event_id WHERE s.id=?",
          (sid,), one=True)
    if not s:
        return {}
    if s["payer_event"]:
        return C.ORGANIZER_PAYER_FLOW
    if s["event_id"] and s["billing"] == "families":
        return C.with_family_money(C.WORKFLOW[s["type"]])
    return C.WORKFLOW[s["type"]]


def sync_workflow_tasks(sid):
    """Make the session's step-tasks match its type's workflow. Done steps and my own tasks are kept."""
    s = q("SELECT type, event_id FROM sessions WHERE id=?", (sid,), one=True)
    flow = flow_for(sid)
    want = {}
    for st in C.STAGES:
        for i, step in enumerate(flow.get(st, [])):
            if step[0] == "event" and s["event_id"]:
                ensure_event_task(s["event_id"], st, step[1], step[2], C.STAGES.index(st) * 100 + i)
            elif step[0] in ("do", "event"):
                want[(st, step[1])] = (step[2], C.STAGES.index(st) * 100 + i)
    have = {(r["stage"], r["title"]): r for r in q("SELECT * FROM tasks WHERE session_id=? AND own=0", (sid,))}
    for key, r in have.items():
        if key not in want and not r["done"]:
            db().execute("DELETE FROM tasks WHERE id=?", (r["id"],))
    for (st, title), (off, sort) in want.items():
        if (st, title) in have:
            db().execute("UPDATE tasks SET offset_days=?, sort=? WHERE id=?", (off, sort, have[(st, title)]["id"]))
        else:
            db().execute("INSERT INTO tasks (session_id, stage, title, offset_days, sort) VALUES (?,?,?,?,?)",
                         (sid, st, title, off, sort))
    _commit()
    schedule_tasks(sid)


OUTCOMES = {"done": "Done", "waived": "Waived", "na": "Not needed", "declined": "Client declined"}


PLAN_RESIGN = "Have the plan's agreement signed again (a session joined)"


def _sdocs(ctx, kind):
    docs = [dd for dd in ctx["docs"] if dd["kind"] == kind]
    s_ = ctx.get("s")
    if kind == "agreement" and s_ is not None and "plan_of" in s_.keys() and s_["plan_of"]:
        docs = q("SELECT kind, status FROM documents WHERE session_id=? AND kind='agreement'", (s_["plan_of"],))   # the plan's agreement
        if q("SELECT 1 FROM tasks WHERE session_id=? AND title=? AND done IS NULL",
             (s_["plan_of"], PLAN_RESIGN), one=True):
            return False                                # signed before this session joined: it doesn't cover it yet
    return bool(docs) and all(dd["status"] == "signed" for dd in docs)


def _quoted(c):
    return c["t"]["quoted"] > 0 or bool(q("SELECT 1 FROM lines WHERE session_id=? AND kind != 'discount'", (c["s"]["id"],), one=True))


# key -> (label, done(ctx), tab where you do it)
AUTO = {
    "logged": ("First contact logged", lambda c: c["n_log"] > 0 or _quoted(c), "log"),   # a built quote means you were in touch
    "date_set": ("Shoot date set", lambda c: bool(c["s"]["shoot_date"]), "shoot"),
    "quote_built": ("Quote built", lambda c: _quoted(c), "money"),
    "agreement_signed": ("Agreement signed", lambda c: _sdocs(c, "agreement"), "paperwork"),
    "releases_signed": ("Releases signed", lambda c: _sdocs(c, "release"), "paperwork"),
    "deposit_paid": ("Deposit received", lambda c: c["t"]["deposit_short"] <= 0.005 and
                     (c["t"]["paid"] > 0 or (c["t"]["deposit_req"] <= 0.005 and c["t"]["quoted"] > 0.005)), "money"),   # no deposit: done once quoted
    "intake_done": ("Intake answered", lambda c: sum(1 for v in c["intake"].values() if v) >=
                    max(1, len(C.INTAKE.get(c["s"]["type"], [])) // 2), "intake"),
    "gallery_built": ("Gallery built", lambda c: bool(c["s"]["gallery_built"]), "delivery"),
    "letter_mailed": ("QR letter mailed", lambda c: bool(c["s"]["letter_mailed"]), "delivery"),
    "first_visit": ("Client opened the gallery", lambda c: bool(c["s"]["first_visit"]) or gallery_removed(c["s"]["id"]), "delivery"),
    "balance_paid": ("Paid in full", lambda c: c["t"]["quoted"] > 0 and c["t"]["due"] <= 0.005, "money"),
    "sd_shipped": ("Files delivered", lambda c: bool(c["s"]["sd_shipped"]), "delivery"),
    "favorites": ("Picks received", lambda c: bool(c["s"]["favorites"] or c["s"]["retouch_picks"] or c["s"]["veto"]), "delivery"),
}


def workflow(sid):
    cache = g.setdefault("wf_cache", {})
    if sid not in cache:
        cache[sid] = _workflow(sid)
    return cache[sid]


BOOKING_HOLD_DAYS = 7   # deposit + agreement due a week after the client says yes


def booking_due(sid, s):
    r = q("SELECT MIN(at) a FROM stage_log WHERE session_id=? AND stage='booked'", (sid,), one=True)
    base = d((r["a"] or "")[:10]) if r and r["a"] else d((s["stage_changed"] or "")[:10]) if s["stage_changed"] else today()
    due = base + timedelta(days=BOOKING_HOLD_DAYS)
    if s["shoot_date"] and d(s["shoot_date"]) < due:
        due = d(s["shoot_date"])
    return due.isoformat()


def _workflow(sid):
    """Stages with their steps, current stage, progress and the next step."""
    s = session_full(sid)
    ctx = dict(s=s, t=totals(sid), intake=json.loads(s["intake"] or "{}"),
               docs=q("SELECT kind, status FROM documents WHERE session_id=?", (sid,)),
               n_log=q("SELECT COUNT(*) n FROM log WHERE session_id=?", (sid,), one=True)["n"])
    tasks = {(r["stage"], r["title"]): r for r in q("SELECT * FROM tasks WHERE session_id=? AND own=0", (sid,))}
    own = q("SELECT * FROM tasks WHERE session_id=? AND own=1 ORDER BY id", (sid,))
    flow = flow_for(sid)
    closes = {r["step"]: r["outcome"] for r in q("SELECT step, outcome FROM step_closes WHERE session_id=?", (sid,))}
    extra = {}
    for fn in hooks.steps:
        for st, label, done, tab in (fn(s) or []):
            extra.setdefault(st, []).append(dict(label=label, done=done, kind="auto", tab=tab))
    stages = []
    for st in C.STAGES:
        steps = []
        for step in flow.get(st, []):
            if step[0] == "auto":
                if step[1] in ("deposit_paid", "balance_paid") and ctx["t"]["quoted"] == 0:
                    continue
                if step[1] in ("releases_signed",) and not any(dd["kind"] == "release" for dd in ctx["docs"]):
                    continue
                label, fn, tab = AUTO[step[1]]
                ov = closes.get(step[1])
                due_ = booking_due(sid, s) if (st == "booked" and step[1] in ("deposit_paid", "agreement_signed", "releases_signed")) else \
                    balance_due_date(s["shoot_date"], s["type"], sid=sid) if step[1] == "balance_paid" else None
                steps.append(dict(label=label, done=bool(ov) or fn(ctx), kind="auto", tab=tab, key=step[1], due=due_,
                                  outcome=ov if ov and ov != "done" else None))
            elif step[0] == "event" and s["event_id"]:
                r = q("SELECT * FROM tasks WHERE event_id=? AND stage=? AND title=?", (s["event_id"], st, step[1]), one=True)
                if r:
                    steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"], outcome=r["outcome"] if r["outcome"] not in (None, "done") else None,
                                      shared=True))
            else:
                r = tasks.get((st, step[1]))
                if r:
                    steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"],
                                      outcome=r["outcome"] if r["outcome"] not in (None, "done") else None))
        steps += extra.get(st, [])
        for r in (r_ for r_ in own if (r_["stage"] or s["stage"]) == st):
            steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"], outcome=r["outcome"] if r["outcome"] not in (None, "done") else None, mine=True))
        stages.append(dict(stage=st, steps=steps, n_done=sum(x_["done"] for x_ in steps)))
    cur = next((g_ for g_ in stages if g_["stage"] == s["stage"]), None)
    nxt_stage = None
    if s["stage"] in C.STAGES and C.STAGES.index(s["stage"]) + 1 < len(C.STAGES):
        nxt_stage = C.STAGES[C.STAGES.index(s["stage"]) + 1]
    next_step = next((st_ for st_ in (cur["steps"] if cur else []) if not st_["done"]), None)
    ready = cur is not None and all(st_["done"] for st_ in cur["steps"])
    wait_close = None                                    # every step done, but the gallery is still up: say until when
    if ready and nxt_stage == "closed" and not can_enter("closed", s["shoot_date"], s["id"]):
        wait_close = gallery_expires(q("SELECT * FROM sessions WHERE id=?", (s["id"],), one=True)) or "open"
    return dict(stages=stages, current=cur, next_stage=nxt_stage, next_step=next_step, ready=ready, wait_close=wait_close)


def create_session(household_id, type_, stage="inquiry", title="", shoot_date=None, event_id=None,
                   subject_ids=()):
    sid = x("""INSERT INTO sessions (household_id, type, stage, title, shoot_date, event_id, gallery_id, created, stage_changed)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (household_id, type_, stage, title, shoot_date or None, event_id, secrets.token_urlsafe(9), now_local(), now_local()))
    for pid in subject_ids:
        x("INSERT OR IGNORE INTO session_people VALUES (?,?)", (sid, pid))
    signers = []
    if type_ in C.NEEDS_AGREEMENT:
        signers = default_signers(household_id, type_)
        for p_ in signers or [None]:
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'agreement', ?, ?)",
              (sid, p_["id"] if p_ else None, p_["name"] if p_ else ""))
    signer_ids = {p_["id"] for p_ in signers}
    if type_ in C.NEEDS_RELEASE:
        for pid in subject_ids:
            if pid in signer_ids:
                continue                                # signing the agreement covers them; no separate release
            p = q("SELECT * FROM people WHERE id=?", (pid,), one=True)
            signer = ""
            if p["is_minor"]:
                parent = q("SELECT name FROM people WHERE household_id=? AND role='parent' LIMIT 1",
                           (p["household_id"],), one=True)
                signer = f"Parent: {parent['name']}" if parent else "Parent (required)"
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'release', ?, ?)",
              (sid, pid, signer))
    sync_workflow_tasks(sid)
    hooks.fire("session_created", sid)
    return sid


def signer_choices(household_id):
    """The adults on a client record who could sign an agreement (parents first)."""
    return q("""SELECT id, name FROM people WHERE household_id=? AND role IN ('parent','adult') AND COALESCE(is_minor,0)=0
                ORDER BY role='parent' DESC, id""", (household_id,))


def default_signers(household_id, type_):
    """Who signs a new session's agreement: the first adult, or every adult when the type says everyone listed signs."""
    ch = signer_choices(household_id)
    return list(ch) if C.ALL_SIGN.get(type_) else list(ch[:1])


def agreement_signers(sid):
    """The people signing this session's agreement, in order; a row from before 'Who signs' existed counts as the
    first adult."""
    rows = q("""SELECT d.*, p.name AS pname FROM documents d LEFT JOIN people p ON p.id=d.about_person
                WHERE d.session_id=? AND d.kind='agreement' ORDER BY d.id""", (sid,))
    names = [r_["pname"] for r_ in rows if r_["pname"]]
    if not names:
        s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
        ch = signer_choices(s_["household_id"]) if s_ else []
        names = [ch[0]["name"]] if ch else []
    return rows, names


@app.post("/session/<int:sid>/signers")
def session_signers(sid):
    """Who signs the agreement: tick any adults on the client record. Nothing about who they are to each other."""
    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    allowed = {r_["id"]: r_["name"] for r_ in signer_choices(s_["household_id"])}
    want = [int(v_) for v_ in request.form.getlist("signer") if v_.isdigit() and int(v_) in allowed]
    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#paperwork"
    if not want:
        session["notice"] = "At least one person signs the agreement."
        g.refused = True
        return redirect(back)
    rows = q("SELECT * FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,))
    have = {r_["about_person"]: r_ for r_ in rows if r_["about_person"]}
    for r_ in rows:
        if r_["about_person"] is None:                   # a row from before 'Who signs': give it to the first chosen person
            if want[0] not in have:
                x("UPDATE documents SET about_person=?, signer=? WHERE id=?", (want[0], allowed[want[0]], r_["id"]))
                have[want[0]] = r_
            elif r_["status"] != "signed":
                x("DELETE FROM documents WHERE id=?", (r_["id"],))
    kept_signed = []
    for pid, r_ in list(have.items()):
        if pid not in want:
            if r_["status"] == "signed":
                kept_signed.append(allowed.get(pid) or "someone")
            else:
                x("DELETE FROM documents WHERE id=?", (r_["id"],))
    for pid in want:
        if pid not in have:
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'agreement', ?, ?)", (sid, pid, allowed[pid]))
            x("DELETE FROM documents WHERE session_id=? AND kind='release' AND about_person=? AND status != 'signed'", (sid, pid))
    if kept_signed:
        session["notice"] = f"{oxford(kept_signed)} already signed, so they stay on the agreement."
    audit("Who signs the agreement: " + oxford(allowed[p_] for p_ in want), sid=sid)
    return redirect(back)


def gallery_removed(sid):
    """A gallery taken down (window over, unopened for a year, early delete) no longer holds the session open."""
    if not q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True):
        return False
    r = q("SELECT removed_at FROM galleries WHERE session_id=?", (sid,), one=True)
    return bool(r and r["removed_at"])


def can_enter(stage, shoot_date, sid=None):
    """Shot is entered only once the shoot date has come; Closed only once a delivered gallery has run its days."""
    if stage == "shot":
        return bool(shoot_date and shoot_date <= today().isoformat())
    if stage == "closed" and sid:
        s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        if s_ and s_["gallery_built"]:
            g_ = q("SELECT * FROM galleries WHERE session_id=?", (sid,), one=True) \
                if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
            if g_ and g_["removed_at"]:
                return True      # taken down (window over, never opened in a year, or early delete)
            if not s_["first_visit"] and not (g_ and "uploaded_at" in g_.keys() and g_["uploaded_at"]):
                return True      # never put online: there is no gallery window to wait for
            exp = gallery_expires(s_)
            return bool(exp and exp < today())
    return True


def advance(sid):
    """A workflow moves itself on when every step of its stage is done."""
    s0 = q("SELECT stage, shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    if s0 and s0["stage"] == "shot" and s0["shoot_date"] and s0["shoot_date"] > today().isoformat():
        set_stage(sid, "booked")
        audit(f"Moved back to Booked (the shoot is {nice(s0['shoot_date'])}, still ahead)", sid=sid, who="Lumen Aureum")
        try:
            session["notice"] = f"Still Booked: the shoot is {nice(s0['shoot_date'])}."
        except RuntimeError:
            pass                                        # outside a request (background work)
    for _ in range(len(C.STAGES)):
        s_ = q("SELECT stage, shoot_date, hold_stage FROM sessions WHERE id=?", (sid,), one=True)
        if not s_ or s_["stage"] not in C.STAGES:
            return
        w = workflow(sid)
        if s_["hold_stage"] == s_["stage"]:     # moved back by hand: it stays until a step there is reopened
            if w["ready"]:
                return
            x("UPDATE sessions SET hold_stage=NULL WHERE id=?", (sid,))
        if not (w["ready"] and w["next_stage"] and w["current"] and w["current"]["steps"]):
            return
        if not can_enter(w["next_stage"], s_["shoot_date"], sid):
            return
        set_stage(sid, w["next_stage"])
        audit(f"Moved to {C.STAGE_LABELS[w['next_stage']]}: all {C.STAGE_LABELS[s_['stage']]} steps are done",
              sid=sid, who="Lumen Aureum")


_ADV = {"at": 0.0}


def advance_all(force=False):
    """Every open session moves itself on when its stage is done. Date-driven moves only change once a day, so a page
    view re-runs this at most every few minutes."""
    import time as _t
    if not force and _t.time() - _ADV["at"] < 300:
        return
    _ADV["at"] = _t.time()
    for r in q("SELECT id FROM sessions WHERE stage NOT IN ('closed','lost')"):
        advance(r["id"])


def _advance_after_post(resp):
    """Called from the audit hook, after the action's own line is written, so causes precede effects."""
    if (request.method == "POST" and resp.status_code < 400 and datakey.is_unlocked() and _unlocked_once
            and request.endpoint not in QUIET_ENDPOINTS | app.config.get("quiet_endpoints", set())):
        try:
            sid_ = g.get("touched_sid")
            if sid_:
                advance(sid_)            # the session this action touched (never every open session on every save)
        except Exception:
            app.logger.exception("advance failed")
    return resp


def set_stage(sid, stage):
    old = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)["stage"]
    if old == stage:
        return
    cur_ = db().execute("UPDATE sessions SET stage=?, stage_changed=? WHERE id=? AND stage=?", (stage, now_local(), sid, old))
    _commit()
    if cur_.rowcount != 1:
        return                           # someone else moved it at the same moment: their move stands, logged once
    if stage in ("booked", "shot", "delivered", "closed"):
        lock_tax_rate(sid)
    x("INSERT INTO stage_log (session_id, stage) VALUES (?,?)", (sid, stage))
    hooks.fire("stage_changed", sid, old, stage)


def needs_you():
    if "needs_you" in g:
        return g.needs_you
    g.needs_you = _needs_you()
    return g.needs_you


def _needs_you():
    t = today()
    soon = (t + timedelta(days=7)).isoformat()
    out = {}
    tasks, seen = [], {}
    for r in q("""SELECT t.*, s.title AS stitle, s.type, s.event_id, e.name AS event_name, h.name AS household
        FROM tasks t JOIN sessions s ON s.id=t.session_id JOIN households h ON h.id=s.household_id
        LEFT JOIN events e ON e.id=s.event_id
        WHERE t.done IS NULL AND t.due IS NOT NULL AND t.due <= ? AND s.stage NOT IN ('closed','lost')
        ORDER BY t.due, t.sort""", (soon,)):
        if r["event_id"]:
            key = (r["event_id"], r["title"])
            if key in seen:
                seen[key]["n"] += 1
                continue
            seen[key] = dict(r=r, n=1, event=True)
            tasks.append(seen[key])
        else:
            tasks.append(dict(r=r, n=1, event=False))
    out["tasks"] = tasks
    flow, seen_ev = [], {}
    for r in q("""SELECT s.*, h.name AS household, e.name AS event_name FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE s.stage NOT IN ('closed','lost')"""):
        if r["event_id"]:
            if r["event_id"] in seen_ev:
                seen_ev[r["event_id"]]["n"] += 1
                continue
        w = workflow(r["id"])
        cur = w["current"] or dict(steps=[], n_done=0)
        overdue = sum(1 for st in cur["steps"] if not st["done"] and st.get("due") and st["due"] < t.isoformat())
        nxt = w["next_step"]
        row = dict(s=r, wf=w, n=1, overdue=overdue, event=bool(r["event_id"]),
                   sortkey=(0 if overdue else 1, (nxt or {}).get("due") or r["shoot_date"] or "9999"))
        if r["event_id"]:
            seen_ev[r["event_id"]] = row
        flow.append(row)
    flow.sort(key=lambda z: z["sortkey"])
    out["flow"] = flow
    shoots, seen = [], {}
    for r in q("""SELECT s.*, h.name AS household, e.name AS event_name, e.venue AS event_venue FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE s.shoot_date BETWEEN ? AND ? AND s.stage NOT IN ('lost') ORDER BY s.shoot_date, s.start_time""",
               (t.isoformat(), (t + timedelta(days=14)).isoformat())):
        if r["event_id"]:
            if r["event_id"] in seen:
                seen[r["event_id"]]["n"] += 1
                continue
            seen[r["event_id"]] = dict(s=r, n=1, event=True)
            shoots.append(seen[r["event_id"]])
        else:
            shoots.append(dict(s=r, n=1, event=False))
    out["shoots"] = shoots
    out["paperwork"] = q("""SELECT doc.*, s.id AS sid, s.shoot_date, h.name AS household, p.name AS person
        FROM documents doc JOIN sessions s ON s.id=doc.session_id JOIN households h ON h.id=s.household_id
        LEFT JOIN people p ON p.id=doc.about_person
        WHERE doc.status != 'signed' AND s.stage NOT IN ('lost','closed')
          AND s.shoot_date IS NOT NULL AND s.shoot_date <= ? ORDER BY s.shoot_date""",
                         ((t + timedelta(days=14)).isoformat(),))
    owing = []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage IN ('shot','delivered','closed')"""):
        tt = totals(s["id"])
        if owed_now(s["stage"], tt, s["shoot_date"], row_type(s)):
            owing.append(dict(s=s, due=tt["due"]))
    out["owing"] = owing
    galleries = []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.first_visit IS NOT NULL"""):
        exp = gallery_expires(s)
        if exp and t <= exp <= t + timedelta(days=2):
            galleries.append(dict(s=s, expires=exp))
    out["galleries"] = galleries
    quiet = []
    cutoff = (t - timedelta(days=C.LEAD_QUIET_DAYS)).isoformat()
    for s in q("""SELECT s.*, h.name AS household,
                  (SELECT MAX(at) FROM log WHERE household_id=s.household_id) AS last_touch
                  FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage IN ('inquiry','quoted')"""):
        last = s["last_touch"] or s["created"]
        if last[:10] <= cutoff:
            quiet.append(dict(s=s, last=last[:10]))
    out["quiet"] = quiet
    out["future"] = future_work()
    out["plugins"] = [item for fn in hooks.today for item in (fn() or [])]
    return out


FUTURE_TYPE = "senior"


def future_work():
    """Minors who will be seniors next school year, with no senior session yet (only when a "senior" type exists)."""
    if FUTURE_TYPE not in C.SESSION_TYPES:
        return []
    t = today()
    school_year_end = t.year + (1 if t.month >= 7 else 0)
    rows = q("""SELECT p.*, h.name AS household FROM people p JOIN households h ON h.id=p.household_id
        WHERE p.grad_year IN (?, ?) AND NOT EXISTS (
          SELECT 1 FROM session_people sp JOIN sessions s ON s.id=sp.session_id
          WHERE sp.person_id=p.id AND s.type=?)""", (school_year_end, school_year_end + 1, FUTURE_TYPE))
    return rows


# ---------------------------------------------------------------- action dashboard

MARKABLE = {"gallery_built": "Mark built", "letter_mailed": "Mark mailed", "first_visit": "Mark opened",
            "sd_shipped": "Mark delivered"}
# Waiting on THEM: these steps are the client's move. Tiles show the client's face, dimmed, with
# how long you've been waiting; your own steps keep their task icon and button.
THEIR_STEPS = {"Intake answered", "Client said yes", "Agreement signed", "Releases signed", "Deposit received",
               "Client opened the gallery", "Picks received", "Paid in full"}
THEIR_WORDS = {"Intake answered": "Intake answers", "Client said yes": "Quote accepted", "Agreement signed": "Agreement signed",
               "Releases signed": "Releases signed", "Deposit received": "Deposit paid", "Client opened the gallery": "Gallery opened",
               "Picks received": "Favorites picked", "Paid in full": "Balance paid"}
def todo_word(label):
    """An open step read as a task: a plugin's own words first, then the built-in ones."""
    return hooks.step_labels.get(label) or TODO_WORDS.get(label, label)


TODO_WORDS = {"First contact logged": "Log the first contact", "Intake answered": "Get the intake answers",
              "Quote built": "Build the quote", "Agreement signed": "Get the agreement signed",
              "Releases signed": "Get the releases signed", "Deposit received": "Collect the deposit",
              "Shoot date set": "Pick a shoot date", "Gallery built": "Build gallery",
              "QR letter mailed": "Mail QR letter", "Client opened the gallery": "Client opens the gallery",
              "SD card shipped": "Ship SD card",
              "Picks received": "Get their picks",
              "Paid in full": "Collect the balance",
              # short forms of the "do" steps so tiles never wrap
              "Plan stops, check permits": "Plan stops", "Scout any new spot in person": "Scout new spots",
              "Weather call, confirm meeting spot": "Weather call", "Charge batteries, clear cards": "Charge batteries",
              "Scout the venue, dial in settings": "Scout the venue", "Confirm the venue's photo rules": "Venue photo rules",
              "Send session details": "Send session details"}
LINK_VERBS = {"logged": ("Log contact", "log"), "intake_done": ("Fill intake", "intake"),
              "quote_built": ("Build quote", "money"), "deposit_paid": ("Record deposit", "money"),
              "date_set": ("Set date", "shoot"), "favorites": ("Pick favorites", "delivery")}


def event_reps():
    """For each event, the family session furthest behind: it decides what the event still needs."""
    reps = {}
    for r in q("SELECT id, event_id, stage FROM sessions WHERE event_id IS NOT NULL AND stage NOT IN ('closed','lost')"):
        i = C.STAGES.index(r["stage"]) if r["stage"] in C.STAGES else 99
        if r["event_id"] not in reps or i < reps[r["event_id"]][1]:
            reps[r["event_id"]] = (r["id"], i)
    return {eid: v[0] for eid, v in reps.items()}


def session_minutes(sid, stype):
    r = q("SELECT duration_min FROM sessions WHERE id=?", (sid,), one=True)
    if r and r["duration_min"]:
        return r["duration_min"]
    mins = 0
    items = {it["key"]: it for it in current_schedule()["items"]}
    for ln in q("SELECT fee_key, qty FROM lines WHERE session_id=? AND fee_key != ''", (sid,)):
        it = items.get(ln["fee_key"], {})
        if it.get("unit") == "hour":
            mins += 60 * (ln["qty"] or 1)
        else:
            mins += int(it.get("minutes") or 0)
    return int(mins or C.TYPE_MINUTES[stype])


def _span(sid, stype, day, hm):
    try:
        h_, m_ = (int(v) for v in hm.split(":")[:2])
    except (AttributeError, ValueError):
        return None
    start = datetime.combine(d(day), datetime.min.time()) + timedelta(hours=h_, minutes=m_)
    return start, start + timedelta(minutes=session_minutes(sid, stype))


def clashes(sid):
    """Other live sessions the same day that overlap this one (with drive/setup buffer), or share the day with no
    start time to tell. Warn only - two minis back to back are fine; the photographer decides."""
    s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s or not s["shoot_date"] or s["stage"] in ("lost", "closed"):
        return []
    return clashes_at(sid, s["type"], s["shoot_date"], s["start_time"], s["event_id"] or s["payer_event"])


def clashes_at(sid, stype, day, hm, event_id=None):
    """What a session (sid 0 = not created yet) would collide with on `day` at `hm`, however far out."""
    mine = _span(sid, stype, day, hm)
    out = []
    for o in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.shoot_date=? AND s.id != ? AND s.stage NOT IN ('lost','closed')
                  AND (s.event_id IS NULL OR s.event_id IS NOT ?)
                  AND (s.payer_event IS NULL OR s.payer_event IS NOT ?)""", (day, sid or 0, event_id, event_id)):
        theirs = _span(o["id"], o["type"], o["shoot_date"], o["start_time"])
        if not mine or not theirs:
            when_ = f"same day at {clock12(o['start_time'])}" if theirs else "same day, no start time yet"
            out.append(dict(name=o["household"], hid=o["household_id"], sid=o["id"], when=when_))
            continue
        buf = timedelta(minutes=C.BOOKING_BUFFER_MIN)
        if mine[0] < theirs[1] + buf and theirs[0] < mine[1] + buf:
            out.append(dict(name=o["household"], hid=o["household_id"], sid=o["id"],
                            when=f"{clock12(o['start_time'])} to {theirs[1].strftime('%-I:%M %p')}"))
    return out


def build_actions(only_sid=None):
    """Every concrete thing to do, as verb + target, across open sessions and events (or, with only_sid, for one
    session and its event: the session page needs no other client's work)."""
    t = today()
    acts = []
    seen_events = set()
    rows = q("""SELECT s.*, h.name AS household, e.name AS event_name, e.date AS event_date FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE s.stage NOT IN ('closed','lost')""" + ("" if only_sid is None else
        " AND (s.id=? OR (s.event_id IS NOT NULL AND s.event_id=(SELECT event_id FROM sessions WHERE id=?)))"),
        () if only_sid is None else (only_sid, only_sid))
    reps = event_reps()
    for s in rows:
        if s["event_id"]:
            if s["event_id"] in seen_events or reps.get(s["event_id"]) != s["id"]:
                continue
            seen_events.add(s["event_id"])
            acts += event_actions(s)
            continue
        w = workflow(s["id"])
        cur = w["current"]
        who = dict(name=s["household"], sub=C.SESSION_TYPES.get(s["type"], s["type"]),
                   link=url_for("session_view", sid=s["id"]), type=s["type"], hid=s["household_id"])
        tt = totals(s["id"])
        for st in (cur["steps"] if cur else []):
            if st["done"]:
                continue
            when = st.get("due") or (s["shoot_date"] if s["stage"] == "booked" else None)
            theirs = st["label"] in THEIR_STEPS
            a = dict(who=who, what=(THEIR_WORDS if theirs else TODO_WORDS).get(st["label"], st["label"]), when=when,
                     stage=s["stage"], theirs=theirs, since=(s["stage_changed"] or "")[:10])
            if st["label"] == "Send retouched photos" and (s["retouch_picks"] or "").strip():
                a["detail"] = s["retouch_picks"].strip()        # the client's notes, right where the work is
            if st["label"] == "Send session details":
                a.update(doc=url_for("session_details", sid=s["id"]), doc_verb="Open details")
            elif st["label"] == "Send quote":
                a.update(doc=url_for("session_quote", sid=s["id"]), doc_verb="Open quote")
            elif st["label"] == "Send invoice":
                a.update(doc=url_for("invoice", sid=s["id"]), doc_verb="Open invoice")
            elif st["label"] == "Agreement signed":
                a.update(doc=url_for("agreement", sid=s["id"]), doc_verb="Open agreement")
            elif st["label"] == "Releases signed":
                rel_ = q("SELECT id FROM documents WHERE session_id=? AND kind='release' AND status!='signed' ORDER BY id", (s["id"],))
                if len(rel_) == 1:
                    a.update(doc=url_for("release_doc", did=rel_[0]["id"]), doc_verb="Open release")
                elif rel_:
                    a.update(doc=url_for("session_view", sid=s["id"]) + "#paperwork", doc_verb=f"Open {len(rel_)} releases", doc_here=True)
            custom = next((r_ for r_ in (fn(st["label"], s) for fn in hooks.step_actions) if r_), None)
            if custom:
                a.update(custom)
            elif st["kind"] == "do" and st["label"] == "Confirm time and place" and (not s["start_time"] or not has_place(s)):
                missing = " and ".join(w_ for w_, ok in (("a start time", s["start_time"]), ("a place", has_place(s))) if not ok)
                a.update(verb="Add time and place", href=url_for("session_view", sid=s["id"]) + "#shoot",
                         what=f"Confirm time and place: needs {missing}", close=dict(task=st["task_id"]))
            elif st["kind"] == "do":
                a.update(verb="Done", post=url_for("task_toggle", tid=st["task_id"]), fields={"set": "1"},
                         close=dict(task=st["task_id"]))
            else:
                key = next((k for k, v in AUTO.items() if v[0] == st["label"]), None)
                if key:
                    a["close"] = dict(sid=s["id"], step=key)
                if key in ("agreement_signed", "releases_signed"):
                    a.update(verb="Mark signed", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="sign", kind="agreement" if key == "agreement_signed" else "release"))
                elif key in MARKABLE:
                    a.update(verb=MARKABLE[key], post=url_for("quick", sid=s["id"]), fields=dict(act="mark", field=key))
                    if key == "first_visit":       # the gallery clock starts on the day the client opened it: ask which day
                        a.update(ask_date=True, date_tip=f"The day the client first opened it. The gallery comes down "
                                                         f"{C.gallery_days(s['type'])} days later.")
                elif key == "balance_paid":
                    a.update(verb=f"Record {money(tt['due'])}", href=url_for("session_view", sid=s["id"]) + "#money",
                             what=f"Balance {money(tt['due'])}",
                             when=balance_due_date(s["shoot_date"], s["type"], sid=s["id"]))
                elif key in LINK_VERBS:
                    verb, tab = LINK_VERBS[key]
                    a.update(verb=verb, href=who["link"] + "#" + tab, sid=s["id"], hid=s["household_id"],
                             form=key, intake=json.loads(s["intake"] or "{}"), type=s["type"], due_amt=tt["due"],
                     dep_amt=(tt["deposit_short"] if tt["deposit_short"] > 0.005 else tt["deposit_req"]))
                else:
                    a.update(verb="Open",
                             href=who["link"] + "#" + (st.get("tab") or ""))
            acts.append(a)
        if s["stage"] in ("inquiry", "quoted", "booked") and s["shoot_date"] and s["shoot_date"] >= t.isoformat():
            for c_ in clashes(s["id"]):
                acts.append(dict(who=who, what=f"Overlaps {c_['name']} ({c_['when']})", when=s["shoot_date"],
                                 stage=s["stage"], verb="Open", href=url_for("client_stage", hid=s["household_id"], s=s["id"])))
        has_bal_step = cur and any(st_.get("key") == "balance_paid" for st_ in cur["steps"])
        bdue = balance_due_date(s["shoot_date"], s["type"], sid=s["id"])
        paid_ahead = (s["stage"] == "booked" and row_type(s) in C.BEFORE_TYPES and bdue and tt["due"] > 0.005
                      and bdue <= (t + timedelta(days=14)).isoformat())            # balance due before the shoot: remind 2 weeks out
        if not has_bal_step and (paid_ahead or (s["stage"] in ("shot", "delivered") and owed_now(s["stage"], tt, s["shoot_date"], row_type(s)))):
            acts.append(dict(who=who, what=f"Balance {money(tt['due'])}", theirs=True, since=s["shoot_date"], when=bdue,
                             stage=s["stage"],
                             verb=f"Record {money(tt['due'])}", href=url_for("session_view", sid=s["id"]) + "#money"))
        if w["ready"] and w["next_stage"] and can_enter(w["next_stage"], s["shoot_date"], s["id"]):
            acts.append(dict(who=who, what=f"Everything in {C.STAGE_LABELS[s['stage']]} is done", when=None,
                             stage=s["stage"], verb=f"Move to {C.STAGE_LABELS[w['next_stage']]}",
                             post=url_for("quick", sid=s["id"]), fields=dict(act="stage", stage=w["next_stage"]),
                             primary=True))
        exp = gallery_expires(s)
        if exp and t <= exp <= t + timedelta(days=2) and not s["extension_used"]:
            acts.append(dict(who=who, what=f"Gallery deletes {nice(exp.isoformat())}", when=exp.isoformat(),
                             stage=s["stage"], verb="Give extension", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="extend")))
        if s["stage"] in ("inquiry", "quoted"):
            last = q("SELECT MAX(at) m FROM log WHERE household_id=?", (s["household_id"],), one=True)["m"] or s["created"]
            if last[:10] <= (t - timedelta(days=C.LEAD_QUIET_DAYS)).isoformat():
                acts.append(dict(who=who, what=f"Follow up (last contact {nice(last)})", when=None, stage=s["stage"],
                                 verb="Follow up", href=who["link"] + "#log", form="logged", sid=s["id"],
                                 hid=s["household_id"], alt=dict(
                                     verb="Mark lost", post=url_for("quick", sid=s["id"]),
                                     fields=dict(act="stage", stage="lost"))))
    for p in (future_work() if only_sid is None else []):
        acts.append(dict(who=dict(name=p["household"], sub="", link=url_for("client_view", hid=p["household_id"]), hid=p["household_id"]),
                         what=f"{p['name']} is class of {p['grad_year']} - offer senior photos", when=None,
                         stage=None, verb="Open client", href=url_for("client_view", hid=p["household_id"]),
                         later=True))
    for fn in hooks.today:
        for item in fn() or []:
            if not item.get("who"):
                continue                          # not tied to a client: shown in Today's "Also" list instead
            acts.append(dict(who=item.get("who") or dict(name=item.get("sub", ""), sub="", link=item["link"]), what=item["text"],
                             when=item.get("when"), stage=None, verb="Open", href=item["link"], later=not item.get("when")))
    soon = (t + timedelta(days=7)).isoformat()
    for a in acts:
        a["icon"], a["tone"] = action_icon(a)
        a["kind"] = KIND.get(a["icon"], "step")
        a["sym"] = icon_for(a.get("what"), a.get("verb"))
        a["status"] = status_of(a.get("when")) if a.get("when") else "next"
        a["tip"] = tip_for(a)
        a["type"] = a.get("type") or a.get("stype")
        v = a.get("verb", "")
        a["tags"] = " ".join(filter(None, [
            "money" if (v.startswith("Record") or "Balance" in a.get("what", "") or a.get("form") in ("deposit_paid", "quote_built")) else "",
            "lead" if a.get("stage") in ("inquiry", "quoted") else "",
            "shoot" if a.get("stage") == "booked" else ""]))
        w_ = a.get("when")
        a["group"] = ("overdue" if w_ and w_ < t.isoformat() else "week" if w_ and w_ <= soon
                      else "later" if w_ or a.get("later") else "now")
    order = {"overdue": 0, "now": 1, "week": 2, "later": 3}
    acts.sort(key=lambda a: (order[a["group"]], a.get("when") or "9999", 0 if a.get("primary") else 1))
    return acts


ICON_WORDS = [  # first match wins: (words in the task, Material Symbol)
    (("move to", "ready"), "arrow_forward"), (("deposit", "balance", "record $", "paid in full", "collect"), "payments"),
    (("sd card", "ship"), "local_shipping"),
    (("quote",), "request_quote"), (("signature", "signed", "release", "agreement", "unsigned"), "signature"), (("qr letter", "letter"), "mail"), (("prep guide", "session details"), "send"),
    (("deletes", "extension"), "timer"), (("opens the gallery", "opened"), "visibility"),
    (("gallery",), "photo_library"), (("favorite", "picks"), "star"),
    (("plan stops", "permit"), "route"), (("intake",), "assignment"), (("shoot date", "set date"), "event"),
    (("weather",), "partly_cloudy_day"), (("batter",), "battery_charging_full"), (("back up", "raws"), "backup"),
    (("cull",), "photo_filter"), (("edit",), "tune"), (("scout", "venue"), "location_on"),
    (("follow up", "contact", "call", "text", "email"), "chat"), (("class of",), "school"), (("deliver",), "inventory_2"),
]


MONEY_SYMS = {"payments", "request_quote"}
CLIENT_SYMS = {"chat", "signature", "assignment", "send", "mail", "event", "visibility", "school", "star"}


def sym_group(name):
    """Which kind of work an icon stands for: client, money or craft."""
    return "money" if name in MONEY_SYMS else "client" if name in CLIENT_SYMS else "craft"


def icon_for(*texts):
    t = " ".join(x or "" for x in texts).lower()
    return next((ic for words, ic in hooks.step_icons + ICON_WORDS if any(w in t for w in words)), "task_alt")


KIND = {"pen": "paper", "dollar": "money", "mail": "deliver", "box": "deliver", "image": "deliver", "eye": "deliver",
        "clock": "deliver", "chat": "talk", "arrow": "move"}


def when_words(when):
    """'Sat Nov 7, 2 days ago' / 'Thu Nov 12, in 3 days' / 'today'."""
    if not when:
        return "no date"
    n = (d(when[:10]) - today()).days
    rel = "today" if n == 0 else "tomorrow" if n == 1 else "yesterday" if n == -1 else f"in {n} days" if n > 0 else f"{-n} days ago"
    return f"{nice(when)}, {rel}"


def tip_for(a):
    t_ = _tip_for(a)
    if a.get("theirs"):
        who_ = (a.get("who") or {}).get("name") or "the client"
        since_ = f", since {nice(a['since'])}" if a.get("since") and not a.get("when") else ""
        return f"Waiting on {who_}{since_}. " + t_
    return t_


def _tip_for(a):
    st = a.get("status")
    w = a.get("when")
    if w and nice(w) in a["what"]:
        rel = when_words(w).split(", ", 1)[1]
        return f"{'Problem: ' if st == 'late' else 'Warning: ' if st == 'soon' else ''}{a['what']} ({rel})"
    if st == "late":
        return f"Problem: {a['what']} was due {when_words(w)}"
    if st == "soon":
        return f"Warning: {a['what']} is due {when_words(w)}"
    return f"{a['what']}: due {when_words(w)}" if w else a["what"]


def status_of(when):
    """late / soon (3 days) / next, for a dated item."""
    if not when:
        return "next"
    t = today().isoformat()
    if when < t:
        return "late"
    return "soon" if when <= (today() + timedelta(days=3)).isoformat() else "next"


def action_icon(a):
    """(icon, tone) for a dashboard action, from its verb and text."""
    v, w = a.get("verb", ""), a.get("what", "").lower()
    if v.startswith("Move"):
        return "arrow", "green"
    if v.startswith("Record") or a.get("form") in ("deposit_paid", "quote_built") or "balance" in w:
        return "dollar", "amber"
    if "signed" in v.lower() or "signature" in w or "unsigned" in w:
        return "pen", "red"
    if "permit" in w or "plan stops" in w:
        return "route", "gold"
    if "mail" in v.lower() or "letter" in w or "prep guide" in w or "session details" in w or "send quote" in w:
        return "mail", "blue"
    if "ship" in v.lower() or "sd card" in w:
        return "box", "blue"
    if "built" in v.lower() or "gallery" in w and "delete" not in w or w in ("cull", "edit"):
        return "image", "violet"
    if "extension" in v.lower() or "deletes" in w:
        return "clock", "red"
    if "opens" in w:
        return "eye", "violet"
    if a.get("form") == "logged" or "quiet" in w or "contact" in w:
        return "chat", "blue"
    if a.get("form") == "intake_done":
        return "clipboard", "gold"
    if a.get("form") == "date_set":
        return "calendar", "gold"
    if "favorite" in w or "rate" in w:
        return "star", "gold"
    if "back up" in w:
        return "drive", "violet"
    if "weather" in w:
        return "sun", "gold"
    if "batter" in w:
        return "battery", "gold"
    if "class of" in w:
        return "cap", "blue"
    if "scout" in w or "rules" in w:
        return "pin", "gold"
    return "check", "gold"


def event_actions(s):
    eid = s["event_id"]
    who = dict(name=s["event_name"], sub="Group event", link=url_for("event_view", eid=eid), type="event", eid=eid)
    acts = []
    fam = q("SELECT id, stage FROM sessions WHERE event_id=? AND stage NOT IN ('lost')", (eid,))
    stage = s["stage"]
    top = max((C.STAGES.index(f["stage"]) for f in fam if f["stage"] in C.STAGES), default=0)
    for t_ in event_steps(eid):
        if not t_["done"] and t_["stage"] in C.STAGES and C.STAGES.index(t_["stage"]) <= top:
            acts.append(dict(who=who, what=todo_word(t_["title"]), when=t_["due"], stage=t_["stage"], verb="Done",
                             post=url_for("task_toggle", tid=t_["id"]), fields={}, close=dict(task=t_["id"])))
    lag = q("""SELECT s.id, h.name FROM documents d JOIN sessions s ON s.id=d.session_id JOIN households h ON h.id=s.household_id
               WHERE d.kind='release' AND d.status!='signed' AND s.event_id=? GROUP BY s.id ORDER BY h.name""", (eid,))
    if 0 < len(lag) <= 3:
        for r_ in lag:
            acts.append(dict(who=who, what=f"{r_['name'].replace('The ', '')}: release unsigned", when=s["event_date"],
                             stage=stage, verb="Mark signed", post=url_for("event_releases", eid=eid), fields=dict(session_id=r_["id"])))
    elif lag:
        acts.append(dict(who=who, what=f"{len(lag)} families' releases unsigned", when=s["event_date"],
                         stage=stage, verb="Mark all signed", post=url_for("event_releases", eid=eid), fields={}))
    if stage in ("shot", "delivered"):
        for field, label in (("gallery_built", "galleries built"), ("letter_mailed", "QR letters mailed")):
            n = sum(1 for f in fam if q("SELECT " + dbmod.ident(field) + " v FROM sessions WHERE id=?", (f["id"],), one=True)["v"])
            if n < len(fam):
                acts.append(dict(who=who, what=f"{n} of {len(fam)} {label}", when=None, stage=stage,
                                 verb="Mark all " + ("built" if field == "gallery_built" else "mailed"),
                                 post=url_for("event_mark", eid=eid), fields=dict(field=field)))
    if not acts and stage in C.STAGES and C.STAGES.index(stage) + 1 < len(C.STAGES) and \
            can_enter(C.STAGES[C.STAGES.index(stage) + 1], s["shoot_date"]):
        nxt = C.STAGES[C.STAGES.index(stage) + 1]
        acts.append(dict(who=who, what=f"Everything in {C.STAGE_LABELS[stage]} is done", when=None, stage=stage,
                         verb=f"Move all to {C.STAGE_LABELS[nxt]}", post=url_for("event_stage", eid=eid),
                         fields=dict(stage=nxt), primary=True))
    return acts


def start_time_in(v, keep=""):
    """A start time from a form. Midnight is almost always noon typed on a 12-hour clock: it is not saved, and the
    owner is told why. Blank means no time yet."""
    v = (v or "").strip()[:5]
    if v in ("00:00", "0:00"):
        session["notice"] = "A midnight start looks like a slip, so no time was saved. For noon, pick 12:00 PM."
        return keep if keep not in ("00:00",) else ""
    return v if re.fullmatch(r"\d{1,2}:\d{2}", v) else ""


def has_place(s):
    intake_ = json.loads(s["intake"] or "{}")
    return bool((s["stops"] or "").strip() or (intake_.get("venue") or "").strip() or (intake_.get("address") or "").strip())


@app.post("/session/<int:sid>/quick")
def quick(sid):
    s = session_full(sid)
    f = request.form
    act = f.get("act")
    day = today().isoformat()
    if act == "sign":
        x("""UPDATE documents SET status='signed', signed=?, sent=COALESCE(sent, ?)
             WHERE session_id=? AND kind=? AND status!='signed'""", (day, day, sid, f.get("kind", "release")))
    elif act == "mark" and f.get("field") in MARKABLE:
        if f["field"] in ("letter_mailed", "sd_shipped") and release_blocked(sid):
            return redirect(safe_back(f.get("back")) or url_for("home"))   # released on payment
        on = f.get("on") or ""
        if on and not _past_day(on):
            session["notice"] = "That date is in the future. Nothing was changed."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
        x("UPDATE sessions SET " + dbmod.ident(f["field"]) + "=? WHERE id=?", (on[:10] if on else day, sid))
    elif act == "pay":
        due = totals(sid)["due"]
        if due > 0.005:
            x("INSERT INTO payments (session_id, kind, amount, received) VALUES (?,?,?,?)",
              (sid, "balance" if s["stage"] in OWED_STAGES else "deposit", round(due, 2), day))
            paying_means_yes(sid)
    elif act == "date":
        x("UPDATE sessions SET shoot_date=?, start_time=? WHERE id=?",
          (f.get("shoot_date") or None, start_time_in(f.get("start_time", s["start_time"])), sid))
        where = " ".join((f.get("where") or "").split())[:200]
        if where:                                   # the place, with the date: it goes first in the session's places
            rest = [l_ for l_ in (s["stops"] or "").splitlines() if l_.strip() and l_.strip() != where]
            x("UPDATE sessions SET stops=? WHERE id=?", ("\n".join([where] + rest), sid))
        schedule_tasks(sid)
    elif act == "favorites":
        x("UPDATE sessions SET favorites=?, veto=?, retouch_picks=? WHERE id=?",
          (f.get("favorites", ""), f.get("veto", ""), f.get("retouch_picks", ""), sid))
    elif act == "extend":
        x("UPDATE sessions SET extension_used=1 WHERE id=?", (sid,))
        hooks.fire("gallery_window_changed", sid)
    elif act == "stage" and f.get("stage") in C.STAGES + C.OFF_PIPELINE:
        set_stage(sid, f["stage"])
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(f.get("back")) or url_for("home"))


# ---------------------------------------------------------------- routes: overview

# ---------------------------------------------------------------- the clock check
# Automatic deletions (retention, expired galleries) trust today's date. A NAS clock that jumps (a dead battery, a bad
# time server) or a machine left off for a while would otherwise delete a year's worth at once. So each automatic pass
# asks clock_ok() first: the date must have moved forward by no more than CLOCK_JUMP_DAYS since the last check. If it
# jumped, or went backwards, automatic deletions pause and Today asks the owner to confirm the date.
CLOCK_JUMP_DAYS = 3


def _put_setting(k, v):
    x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))


def clock_ok():
    t = today()
    if setting("clock_paused"):
        return False
    last = setting("clock_last")
    if last:
        try:
            gap = (t - d(last)).days
        except ValueError:
            gap = 0
        if gap < 0 or gap > CLOCK_JUMP_DAYS:
            _put_setting("clock_paused", f"{last}|{t.isoformat()}")
            audit(f"Automatic deletions paused: the date moved from {nice(last)} to {nice(t.isoformat())} since the last check",
                  who="Lumen Aureum")
            return False
    _put_setting("clock_last", t.isoformat())
    return True


app.config["clock_ok"] = clock_ok


@app.get("/jobs/state")
def jobs_state():
    """For the job panel: whether anything is still running (the page reloads when it stops)."""
    import jobs
    js = jobs.visible()
    run = [j for j in js if j["state"] == "running"]
    return {"state": "running" if run else "idle", "done": sum(j["done"] for j in run), "total": sum(j["total"] for j in run)}


def jobs_visible():
    import jobs
    return jobs.visible()


app.jinja_env.globals["jobs_visible"] = jobs_visible


@app.post("/clock/confirm")
def clock_confirm():
    """The owner says today's date is right: automatic deletions resume from today."""
    _put_setting("clock_paused", "")
    _put_setting("clock_last", today().isoformat())
    audit(f"Confirmed today's date ({nice(today().isoformat())}); automatic deletions resume")
    return redirect(url_for("home"))


@app.get("/")
def home():
    acts = build_actions()                 # looking never writes: stages move after saves and in the background
    t = today()
    week = (t + timedelta(days=7)).isoformat()
    shoots = [sh for sh in needs_you_shoots() if sh["s"]["shoot_date"] <= week]
    owed = 0.0
    for s in q("SELECT id, stage, shoot_date FROM sessions WHERE stage IN ('shot','delivered','closed')"):
        tt = totals(s["id"])
        if owed_now(s["stage"], tt, s["shoot_date"], row_type(s)):
            owed += tt["due"]
    deposits = 0.0                            # deposits asked for and not yet in: its own figure, not mixed into "owed"
    for s in q("SELECT id FROM sessions WHERE stage IN ('quoted','booked')"):
        deposits += max(0.0, totals(s["id"])["deposit_short"])
    kpi = dict(deposits=round(deposits, 2),overdue=sum(a["group"] == "overdue" for a in acts),
               week=sum(a["group"] in ("week", "now") for a in acts),
               shoots=len(shoots), owed=owed,
               leads=q("SELECT COUNT(*) n FROM sessions WHERE stage IN ('inquiry','quoted')", one=True)["n"])
    y = str(t.year)
    year = dict(y=y,
                received=q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE substr(received,1,4)=?", (y,), one=True)["v"],
                spent=q("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE substr(date,1,4)=?", (y,), one=True)["v"],
                miles=q("SELECT COALESCE(SUM(miles),0) v FROM expenses WHERE substr(date,1,4)=?", (y,), one=True)["v"],
                shoots=q("SELECT COUNT(*) n FROM sessions WHERE substr(shoot_date,1,4)=? AND stage IN ('shot','delivered','closed')", (y,), one=True)["n"])
    focus = None
    if request.args.get("c"):
        focus = client_focus(int(request.args["c"]), acts)
    elif request.args.get("e"):
        focus = event_focus(int(request.args["e"]), acts)
    loose = [it for fn in hooks.today for it in (fn() or []) if not it.get("who")]
    paused = setting("clock_paused")
    rost = roster(acts)
    busy_ = [r_ for r_ in rost if r_["next"] or r_["shoot"] or r_.get("owed")]
    quiet_ = [r_ for r_ in rost if r_ not in busy_]          # nothing to do now: folded away, not a card each
    return render_template("today.html", clock_paused=paused.split("|") if paused else None, quiet=quiet_,
                           finish=app.config["finish_items"](), loose=loose, acts=acts, kpi=kpi, shoots=shoots, year=year,
                           days=two_weeks(acts), focus=focus, roster=busy_,
                           sel=("c:" + request.args["c"]) if request.args.get("c") else ("e:" + request.args["e"]) if request.args.get("e") else "")


def roster(acts):
    """One entry per client (and per group event) with open work: where they are, what is due, what is coming."""
    t = today().isoformat()
    soon = (today() + timedelta(days=7)).isoformat()
    by_link = {}
    for a in acts:
        by_link.setdefault(a["who"]["link"], []).append(a)
    out, seen_e = {}, set()
    reps = event_reps()
    rows = q("""SELECT s.*, h.name AS household, h.demo AS demo, e.name AS event_name, e.date AS event_date, e.venue AS venue FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE s.stage NOT IN ('closed','lost') ORDER BY COALESCE(s.shoot_date,'9999')""")
    for r in rows:
        if r["event_id"]:
            if r["event_id"] in seen_e or reps.get(r["event_id"]) != r["id"]:
                continue
            seen_e.add(r["event_id"])
            key, link = f"e:{r['event_id']}", url_for("event_view", eid=r["event_id"])
            ent = out.setdefault(key, dict(key=key, kind="event", eid=r["event_id"], hid=None, name=r["event_name"],
                                            people=f"{q('SELECT COUNT(*) n FROM sessions WHERE event_id=?', (r['event_id'],), one=True)['n']} families",
                                            flows=[], acts=[], owed=0))
        else:
            key, link = f"c:{r['household_id']}", url_for("session_view", sid=r["id"])
            ent = out.setdefault(key, dict(key=key, kind="client", hid=r["household_id"], eid=None, demo=r["demo"],
                                            name=r["household"], people=" · ".join(who_is(p) for p in q(
                                                "SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (r["household_id"],))),
                                            flows=[], acts=[], owed=0, dep_short=0))
            tt = totals(r["id"])
            if owed_now(r["stage"], tt, r["shoot_date"], row_type(r)):
                ent["owed"] += tt["due"]
            elif r["stage"] == "booked" and tt["deposit_short"] > 0.005:
                ent["dep_short"] += tt["deposit_short"]
        w = workflow(r["id"])
        cur = w["current"] or dict(steps=[], n_done=0)
        _dis = days_in_stage(r)
        ent["flows"].append(dict(days=_dis, since=(today() - timedelta(days=_dis)).isoformat(), type=C.SESSION_TYPES.get(r["type"], r["type"]) if not r["event_id"] else "Group event",
                                 stage=r["stage"], idx=C.STAGES.index(r["stage"]) if r["stage"] in C.STAGES else -1,
                                 date=r["shoot_date"], time=r["start_time"], stype=r["type"], tentative=r["date_tentative"] if "date_tentative" in r.keys() else 0, ready=w["ready"] and bool(w["next_stage"]),
                                 done=cur["n_done"], total=len(cur["steps"])))
        ent["acts"] += by_link.get(link, [])
    for a in acts:
        if a["who"]["link"].startswith("/client/") and a["who"].get("hid"):
            ent = out.get(f"c:{a['who']['hid']}")
            if ent is not None:
                ent["acts"].append(a)
    res = []
    for ent in out.values():
        acts_ = ent["acts"]
        ent["late"] = [a for a in acts_ if a.get("when") and a["when"] < t]
        ent["now"] = [a for a in acts_ if not a.get("when") and not a.get("later")]
        ent["coming"] = sorted([a for a in acts_ if a.get("when") and a["when"] >= t], key=lambda a: a["when"])
        ent["week"] = any(a["when"] <= soon for a in ent["coming"]) or bool(ent["late"]) or bool(ent["now"])
        ent["lead"] = any(f["stage"] in ("inquiry", "quoted") for f in ent["flows"])
        soon3 = (today() + timedelta(days=3)).isoformat()
        real_now = [a for a in ent["now"] if not a.get("primary")]
        soon_items = [a for a in ent["coming"] if a["when"] <= soon3]
        if ent["late"]:
            ent["corner"] = "Problem: " + "; ".join(f"{a['what']} was due {when_words(a['when'])}" for a in ent["late"][:3])
        elif soon_items:
            ent["corner"] = "Warning: " + "; ".join((f"{a['what']} ({when_words(a['when']).split(', ', 1)[1]})" if nice(a['when']) in a['what']
                                                     else f"{a['what']} is due {when_words(a['when'])}") for a in soon_items[:3])
        else:
            ent["corner"] = ""
        ent["health"] = "late" if ent["late"] else "soon" if any(a["when"] <= soon3 for a in ent["coming"]) else "normal"
        ent["momentum"] = ("down" if ent["late"] else "up" if any(f["ready"] for f in ent["flows"]) else "flat")
        marks = []
        for a in ent["late"]:
            marks.append(dict(x=1, kind=a.get("kind", "step"), status="late", size=13, tip=f"Late since {nice(a['when'])}: {a['what']}"))
        for a in ent["now"]:
            marks.append(dict(x=6, kind=a.get("kind", "step"), status="soon" if not a.get("primary") else "done", size=11, tip=f"Now: {a['what']}"))
        for a in ent["coming"]:
            days = (d(a["when"]) - today()).days
            if days <= 14:
                marks.append(dict(x=10 + days * 6.2, kind=a.get("kind", "step"), status=status_of(a["when"]),
                                  size=13 if days <= 3 else 10 if days <= 7 else 8, tip=f"{nice(a['when'])}: {a['what']}"))
        for f in ent["flows"]:
            if f["date"] and t <= f["date"] <= (today() + timedelta(days=14)).isoformat():
                marks.append(dict(x=10 + (d(f["date"]) - today()).days * 6.2, kind="shoot", status="shoot", size=14,
                                  tip=f"Shoot {nice(f['date'])}" + (f", {ampm(f['time'])}" if f["time"] else "")))
        # spread marks that land on the same spot
        seen_x = {}
        for m in sorted(marks, key=lambda m_: m_["x"]):
            k_ = round(m["x"])
            m["y"] = seen_x.get(k_, 0); seen_x[k_] = m["y"] + 1
        ent["marks"] = marks
        real_first = [a for a in ent["late"]] + [a for a in ent["now"] if not a.get("primary")] + ent["coming"] + [a for a in ent["now"] if a.get("primary")]
        # your move leads; the client's move leads only when nothing is on you
        mine_ = [a for a in real_first if not a.get("theirs")]
        if mine_ and real_first and real_first[0].get("theirs"):
            real_first.remove(mine_[0]); real_first.insert(0, mine_[0])
        for a in real_first:
            if a.get("theirs") and a.get("since"):
                a["waited"] = max(0, (today() - d(a["since"])).days)
        ent["next"] = real_first[0] if real_first else None
        ent["then"] = real_first[1:4]
        ent["more"] = max(0, len(real_first) - 4)
        upcoming_shoot = next((f for f in ent["flows"] if f["date"] and f["date"] >= t), None)
        ent["shoot"] = upcoming_shoot
        top = (ent["late"] or ent["now"] or ent["coming"] or [None])[0]
        ent["top"] = dict(what=top["what"], when=top.get("when"), status=("late" if ent["late"] else ("done" if top.get("primary") else "next") if ent["now"] else status_of(top.get("when"))),
                          kind=top.get("kind", "step")) if top else None
        nxt = min([f["date"] for f in ent["flows"] if f["date"] and f["date"] >= t] or ["9999"])
        ent["sort"] = (0 if ent["late"] else 1 if ent["now"] else 2, (ent["coming"][0]["when"] if ent["coming"] else nxt))
        res.append(ent)
    res.sort(key=lambda e_: e_["sort"])
    return res


def client_focus(hid, acts=None):
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True)
    if not h:
        return None
    acts = acts if acts is not None else build_actions()
    people = q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (hid,))
    flows, past = [], []
    for sr in q("SELECT * FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999')", (hid,)):
        link = url_for("session_view", sid=sr["id"])
        if sr["stage"] in ("closed", "lost"):
            past.append(dict(s=sr, link=link))
            continue
        w = workflow(sr["id"])
        cur = w["current"] or dict(steps=[], n_done=0)
        flows.append(dict(s=sr, link=link, wf=w, done=[st for st in cur["steps"] if st["done"]],
                          acts=[a for a in acts if a["who"]["link"] == link], t=totals(sr["id"]),
                          light=light(sr["shoot_date"], sr["type"])))
    return dict(kind="client", h=h, people=people, flows=flows, past=past,
                contact=household_contact(people),
                back=url_for("home", c=hid))


def event_focus(eid, acts=None):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    if not e:
        return None
    acts = acts if acts is not None else build_actions()
    link = url_for("event_view", eid=eid)
    fams = []
    for sr in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                   WHERE s.event_id=? ORDER BY h.name""", (eid,)):
        docs = q("SELECT status FROM documents WHERE session_id=? AND kind='release'", (sr["id"],))
        fams.append(dict(s=sr, signed=bool(docs) and all(dd["status"] == "signed" for dd in docs)))
    return dict(kind="event", e=e, fams=fams, acts=[a for a in acts if a["who"]["link"] == link],
                link=link, back=url_for("home", e=eid), light=light(e["date"]),
                stage=fams[0]["s"]["stage"] if fams else "booked")


def stage_dates(sr, docs, pays):
    """When each stage was reached: logged changes first, otherwise the best evidence on file."""
    got = {r["stage"]: r["at"][:10] for r in q("SELECT stage, MIN(at) at FROM stage_log WHERE session_id=? GROUP BY stage", (sr["id"],))}
    signed = sorted(d_["signed"] for d_ in docs if d_["signed"])
    paid = sorted(p_["received"] for p_ in pays if p_["received"])
    created = (sr["created"] or today().isoformat())[:10]
    first_log = q("SELECT MIN(at) m FROM log WHERE session_id=?", (sr["id"],), one=True)["m"]
    created = min([created] + ([first_log[:10]] if first_log else []) + signed[:1] + paid[:1])
    guess = {"inquiry": created,
             "booked": (signed[:1] or paid[:1] or [None])[0],
             "shot": sr["shoot_date"] if sr["shoot_date"] and sr["shoot_date"] <= today().isoformat() else None,
             "delivered": sr["letter_mailed"] or sr["gallery_built"],
             "closed": (sr["stage_changed"] or "")[:10] if sr["stage"] == "closed" else None}
    idx = C.STAGES.index(sr["stage"]) if sr["stage"] in C.STAGES else -1
    out = {}
    for i, st in enumerate(C.STAGES):
        if i <= idx:
            out[st] = got.get(st) or guess.get(st)
    if "quoted" in out and not out["quoted"]:
        out["quoted"] = out.get("inquiry")
    return out


def days_in_stage(sr):
    """Whole days since this session reached its current stage."""
    docs = q("SELECT kind, status, signed FROM documents WHERE session_id=?", (sr["id"],))
    pays = q("SELECT received FROM payments WHERE session_id=?", (sr["id"],))
    since = stage_dates(sr, docs, pays).get(sr["stage"]) or (sr["stage_changed"] or "")[:10] or today().isoformat()
    return max(0, (today() - d(since)).days)


def journey(sr, acts):
    """Everything that happened and everything planned for one session, placed on a time axis."""
    sid = sr["id"]
    t = today().isoformat()
    docs = q("""SELECT d.*, p.name AS person FROM documents d LEFT JOIN people p ON p.id=d.about_person WHERE session_id=?""", (sid,))
    pays = q("SELECT * FROM payments WHERE session_id=? ORDER BY received", (sid,))
    past, future = [], []
    for l in q("SELECT * FROM log WHERE session_id=? ORDER BY at", (sid,)):
        past.append(dict(d=l["at"][:10], kind="talk", label=l["summary"], sub=l["channel"], size=10))
    for p_ in pays:
        past.append(dict(d=p_["received"], kind="money", label=f"{money(p_['amount'])} {p_['kind']}", sub=p_["method"] or "",
                         size=int(min(18, 9 + p_["amount"] / 60)), show=True))
    for d_ in docs:
        if d_["signed"]:
            past.append(dict(d=d_["signed"], kind="paper", label=f"{d_['kind'].capitalize()} signed", sub=d_["person"] or "", size=11, show=True))
    for field, label in (("gallery_built", "Gallery built"), ("letter_mailed", "QR letter mailed"),
                         ("first_visit", "Gallery opened"), ("sd_shipped", "Files delivered")):
        if sr[field]:
            past.append(dict(d=sr[field], kind="deliver", label=label, sub="", size=11, show=True))
    for tk in q("SELECT * FROM tasks WHERE session_id=? AND done IS NOT NULL", (sid,)):
        past.append(dict(d=tk["done"], kind="step", label=tk["title"], sub="", size=8))
    # everything finished sits above the line: shared event steps, steps closed another way,
    # the quote built and each version sent
    if sr["event_id"]:
        for tk in q("SELECT * FROM tasks WHERE event_id=? AND session_id IS NULL AND done IS NOT NULL", (sr["event_id"],)):
            past.append(dict(d=tk["done"], kind="step", label=tk["title"], sub="whole event", size=8))
    for c_ in q("SELECT * FROM step_closes WHERE session_id=?", (sid,)):
        lab = AUTO[c_["step"]][0] if c_["step"] in AUTO else c_["step"]
        past.append(dict(d=(c_["at"] or t)[:10], kind="step", label=f"{lab} ({OUTCOMES.get(c_['outcome'], c_['outcome']).lower()})",
                         sub=c_["note"] or "", size=8))
    qt_ = q("SELECT created FROM quotes WHERE session_id=?", (sid,), one=True)
    if qt_ and qt_["created"]:
        past.append(dict(d=qt_["created"][:10], kind="money", label="Quote built", sub="", size=9))
    for v_ in q("SELECT id, at FROM quote_versions WHERE session_id=?", (sid,)):
        past.append(dict(d=v_["at"][:10], kind="money", label=f"Quote {v_['id']} sent", sub="", size=10))
    for a in acts:
        if a.get("when"):
            st_ = status_of(a["when"])
            days = (d(a["when"]) - today()).days
            future.append(dict(d=a["when"], kind=a.get("kind", "step"), label=a["what"], late=st_ == "late", status=st_,
                               size=16 if st_ == "late" else 14 if days <= 3 else 11 if days <= 7 else 9))
    shoot = sr["shoot_date"]
    ok_ = lambda v: bool(re.fullmatch(r"(19|20|21)\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", str(v)[:10]))
    past = [x_ for x_ in past if ok_(x_["d"])]            # a mistyped date (year 1, month 13) is left off the line
    future = [x_ for x_ in future if ok_(x_["d"])]
    dates = [x_["d"] for x_ in past + future if x_["d"]] + [t, (sr["created"] or t)[:10]] + ([shoot] if shoot and ok_(shoot) else [])
    start = d(min(dates)) - timedelta(days=2)
    end = max(d(max(dates)), today() + timedelta(days=10)) + timedelta(days=3)
    span = max(1, (end - start).days)
    pos = lambda ds: round((d(ds) - start).days / span * 100, 2)
    for x_ in past + future:
        x_["x"] = pos(x_["d"])
    past.sort(key=lambda x_: x_["d"]); future.sort(key=lambda x_: x_["d"])
    for x_ in past:
        x_["sym"] = {"talk": "chat", "money": "payments", "paper": "signature"}.get(x_["kind"]) or icon_for(x_["label"])
    for x_ in future:
        x_["sym"] = icon_for(x_["label"])
    first_next = next((x_ for x_ in future if x_["status"] != "late"), None)
    for x_ in future:
        x_["show"] = x_["status"] == "late" or x_ is first_next
    for lst in (past, future):
        lanes_end = [-99.0, -99.0]
        for x_ in lst:
            x_["lane"] = 0
            if not x_.get("show"):
                continue
            lane = next((i for i, e_ in enumerate(lanes_end) if x_["x"] - e_ > 14), None)
            if lane is None:
                x_["show"] = False
                continue
            x_["lane"] = lane + 1; lanes_end[lane] = x_["x"]
    sd = stage_dates(sr, docs, pays)
    bands = []
    keys = [st for st in C.STAGES if st in sd and sd[st]]
    for i, st in enumerate(keys):
        a_ = pos(sd[st]); b_ = pos(sd[keys[i + 1]]) if i + 1 < len(keys) else pos(t)
        bands.append(dict(stage=st, a=a_, w=max(0.8, b_ - a_)))
    ticks = []
    dd = start
    while dd <= end:
        if dd.weekday() == 0:
            ticks.append(dict(x=pos(dd.isoformat()), label=dd.strftime("%b %-d")))
        dd += timedelta(days=1)
    tpos, spos = pos(t), (pos(shoot) if shoot else None)
    ticks = [tk for tk in ticks if abs(tk["x"] - tpos) > 6 and (spos is None or abs(tk["x"] - spos) > 6)]
    for b in bands:
        b["label"] = b["w"] >= 8 and not (b["a"] - 2 < tpos < b["a"] + 10)
    return dict(past=past, future=future, today=tpos, shoot=spos, shoot_d=shoot, shoot_label=spos is None or abs(spos - tpos) > 7,
                bands=bands, ticks=ticks, docs=docs, pays=pays, stage_dates=sd)


HISTORY_AREAS = [   # first match wins: (words in the History line, Material Symbol for its part of the product)
    (("payment", "deposit", "refund", "void", "recorded ", "balance", "check #", "paid"), "payments"),
    (("quote", "fee", "discount", "line", "sales tax", "tax ", "price", "invoice", "receipt"), "request_quote"),
    (("agreement", "release", "signed", "wording", "document", "sample", "reviewed", "session details", "statement"), "description"),
    (("upload", "photo", "gallery", "sealed", "raw", "export", "letter", "download"), "photo_library"),
    (("shoot date", "start time", "time and place", "stops", "tentative", "reschedul"), "event"),
    (("moved to", "stage", "lost", "reopen", "booked", "inquiry"), "arrow_forward"),
    (("intake",), "assignment"),
    (("email", "phone", "person", "address", "name", "client", "household"), "person"),
    (("step", "marked", "done"), "task_alt"),
]


def history_icon(text):
    t_ = (text or "").lower()
    return next((sym_ for words, sym_ in HISTORY_AREAS if any(w in t_ for w in words)), "history")


def client_history(hid):
    rows = [dict(at=r["at"], kind="audit", who=r["who"], text=r["action"], note=r["note"], sid=r["session_id"], icon=history_icon(r["action"]), seq=r["id"])
            for r in q("SELECT * FROM audit WHERE household_id=?", (hid,))]
    rows += [dict(at=r["at"] if len(r["at"]) > 10 else r["at"] + " 00:00:00", kind=r["channel"], who=setting("photographer_name") or "You", text=r["summary"],
                  note="", sid=r["session_id"], lid=r["id"], written=r["written_at"])
             for r in q("SELECT * FROM log WHERE household_id=?", (hid,))]
    rows.sort(key=lambda r_: (r_["at"], r_.get("seq", 0)), reverse=True)   # same minute: the order it happened
    for r_ in rows:
        r_["day"] = r_["at"][:10]
        r_["time"] = ampm(r_["at"][11:16]) if r_["at"][11:16] != "00:00" else ""
    return rows


@app.get("/c/<int:hid>")
def client_stage(hid):
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    people = q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (hid,))
    sess = q("SELECT * FROM sessions WHERE household_id=? ORDER BY stage IN ('closed','lost'), COALESCE(shoot_date,'9999')", (hid,))
    if not sess:
        return redirect(url_for("client_view", hid=hid))
    sid = request.args.get("s", type=int) or sess[0]["id"]
    sr = next((x_ for x_ in sess if x_["id"] == sid), sess[0])
    acts = build_actions(only_sid=sr["id"])
    link = url_for("session_view", sid=sr["id"])
    my = [a for a in acts if a["who"]["link"] == link]
    w = workflow(sr["id"])
    full = session_full(sr["id"])
    panels = [Markup(x_) for x_ in (fn(full) for fn in hooks.session_panels) if x_]
    tabs = [(lab, cnt, Markup(html)) for lab, cnt, html in (t_ for t_ in (fn(full) for fn in hooks.session_tabs) if t_)]
    blocks = [(t_, Markup(h_)) for fn in hooks.stage_blocks for t_, h_ in (fn(full) or [])]
    can_move = bool(w["next_stage"]) and can_enter(w["next_stage"], sr["shoot_date"], sr["id"])
    return render_template("client_stage.html", h=h, people=people, sess=sess, s=full, acts=my, wf=w, days_in=days_in_stage(sr), blocks=blocks,
                           photos=photo_summary(sr["id"]) if sr["stage"] in ("booked", "shot", "delivered", "closed") else None,
                           versions=q("SELECT id, at FROM quote_versions WHERE session_id=? ORDER BY id DESC", (sr["id"],)),
                           clash=clashes(sr["id"]), minutes=session_minutes(sr["id"], sr["type"]),
                           tax_rate=sales_tax_pct(), tax_auto=tax_area_of(sr["id"]), kept_tax=kept_deposit_tax(sr["id"]), tax_areas=[] if not tax_region() else [("auto", "From address")] + [(k_, f"{lab_} {sales_tax_pct(k_):g}%") for k_, lab_ in tax_region()["areas"]],
                           tax_area_names=dict(tax_region()["areas"]) if tax_region() else {},
                           can_move=can_move, now_iso=datetime.now(TZ).strftime("%Y-%m-%dT%H:%M"),
                           j=journey(sr, my), t=totals(sr["id"]), intake=json.loads(sr["intake"] or "{}"),
                           panels=panels, tabs=tabs, light_=light(sr["shoot_date"], sr["type"]), expires=gallery_expires(sr),
                           contact=household_contact(people),
                           lines=q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sr["id"],)),
                           quote=quote_of(sr["id"]), fee_sched=(quote_of(sr["id"]) or {}).get("schedule") or current_schedule(),
                           history=client_history(hid),
                           back=url_for("client_stage", hid=hid, s=sr["id"]))


@app.get("/focus/client/<int:hid>")
def focus_client(hid):
    return render_template("_focus.html", focus=client_focus(hid))


@app.get("/focus/event/<int:eid>")
def focus_event(eid):
    return render_template("_focus.html", focus=event_focus(eid))


@app.post("/client/<int:hid>/new")
def client_new_flow(hid):
    t_ = request.form.get("type", "")
    if t_ not in C.SESSION_TYPES:
        abort(400)
    subj = [p["id"] for p in q("SELECT id, role FROM people WHERE household_id=?", (hid,)) if p["role"] != "parent"]
    new_sid = create_session(hid, t_, "inquiry", subject_ids=subj)
    return redirect(url_for("client_stage", hid=hid, s=new_sid))


@app.post("/event/<int:eid>/date")
def event_date(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    new_date = request.form.get("date") or None
    x("UPDATE events SET date=? WHERE id=?", (new_date, eid))
    for srow in q("SELECT id, shoot_date FROM sessions WHERE event_id=?", (eid,)):
        if srow["shoot_date"] in (None, e["date"]):
            x("UPDATE sessions SET shoot_date=? WHERE id=?", (new_date, srow["id"]))
            schedule_tasks(srow["id"])
    schedule_event(eid)
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(request.form.get("back")) or url_for("home"))


def two_weeks(acts):
    t = today()
    days = []
    for i in range(14):
        dd = t + timedelta(days=i)
        days.append(dict(date=dd, iso=dd.isoformat(), dow=dd.strftime("%a"), num=dd.day, mon=dd.strftime("%b"),
                         today=i == 0, weekend=dd.weekday() >= 5, first=i == 0 or dd.day == 1, shoots=[], due=[]))
    by = {x["iso"]: x for x in days}
    for sh in needs_you()["shoots"]:
        r = sh["s"]
        if r["shoot_date"] in by:
            by[r["shoot_date"]]["shoots"].append(dict(
                event=sh["event"], eid=r["event_id"], hid=r["household_id"], n=sh["n"], sid=r["id"],
                name=r["event_name"] if sh["event"] else r["household"].replace("The ", ""),
                time=r["start_time"], light=light(r["shoot_date"], r["type"]),
                link=url_for("event_view", eid=r["event_id"]) if sh["event"] else url_for("session_view", sid=r["id"])))
    for a in acts:
        w_ = a.get("when")
        if not w_ or a.get("later") and not w_:
            continue
        key = t.isoformat() if w_ < t.isoformat() else w_
        if key in by:
            by[key]["due"].append(dict(what=a["what"], theirs=a.get("theirs"), name=a["who"]["name"], hid=a["who"].get("hid"),
                                       eid=a["who"].get("eid"), late=w_ < t.isoformat(), who=a["who"]["link"]))
    return days


@app.get("/availability")
def availability():
    """Live check while picking a shoot date."""
    from flask import jsonify
    day, hm = request.args.get("d", ""), request.args.get("t", "")
    sid = request.args.get("sid", type=int) or 0
    try:
        d(day)
    except ValueError:
        return jsonify(clashes=[])
    s_ = q("SELECT type, event_id FROM sessions WHERE id=?", (sid,), one=True)
    stype = (s_["type"] if s_ else request.args.get("type")) or (C.NEW_SESSION_TYPES[0] if C.NEW_SESSION_TYPES else "portrait")
    return jsonify(clashes=[f"{c['name']}: {c['when']}" for c in clashes_at(sid, stype, day, hm, s_["event_id"] if s_ else None)],
                   day=nice(day))


@app.get("/calendar/year")
def year_view():
    """A year at a glance: which days are booked (Saturdays stand out), for answering "are you free on ...?" """
    import calendar as _cal
    t = today()
    try:
        y = int(request.args.get("y") or t.year)
    except ValueError:
        y = t.year
    booked = {}
    for r in q("""SELECT s.shoot_date d, COALESCE(e.name, h.name) n FROM sessions s JOIN households h ON h.id=s.household_id
                  LEFT JOIN events e ON e.id=s.event_id
                  WHERE substr(s.shoot_date,1,4)=? AND s.stage NOT IN ('lost') GROUP BY s.shoot_date, COALESCE(s.event_id, -s.id)""",
               (str(y),)):
        booked.setdefault(r["d"], []).append(r["n"])
    cal_ = _cal.Calendar(firstweekday=6)
    months = [dict(m=m, title=date(y, m, 1).strftime("%B"),
                   weeks=[[dict(iso=dd.isoformat(), num=dd.day, other=dd.month != m, sat=dd.weekday() == 5,
                                today=dd == t, who=booked.get(dd.isoformat(), [])) for dd in wk]
                          for wk in cal_.monthdatescalendar(y, m)]) for m in range(1, 13)]
    free_sats = sum(1 for m_ in months for wk in m_["weeks"] for c in wk
                    if c["sat"] and not c["other"] and not c["who"] and c["iso"] >= t.isoformat())
    return render_template("year.html", y=y, months=months, free_sats=free_sats)


@app.get("/calendar")
def month_view():
    """Month at a glance: shoots and every dated step, on its own day, Sunday-first grid."""
    import calendar as _cal
    t = today()
    try:
        y, m = (int(v) for v in (request.args.get("m") or t.strftime("%Y-%m")).split("-"))
        first = date(y, m, 1)
    except ValueError:
        first = t.replace(day=1)
    acts = build_actions()
    cal_ = _cal.Calendar(firstweekday=6)
    weeks = []
    for wk in cal_.monthdatescalendar(first.year, first.month):
        weeks.append([dict(date=dd, iso=dd.isoformat(), num=dd.day, other=dd.month != first.month, today=dd == t,
                           weekend=dd.weekday() >= 5, shoots=[], due=[]) for dd in wk])
    by = {c["iso"]: c for wk in weeks for c in wk}
    lo, hi = weeks[0][0]["iso"], weeks[-1][-1]["iso"]
    seen_ev = set()
    for r in q("""SELECT s.*, h.name AS household, e.name AS event_name FROM sessions s JOIN households h ON h.id=s.household_id
                  LEFT JOIN events e ON e.id=s.event_id WHERE s.shoot_date BETWEEN ? AND ? AND s.stage != 'lost'
                  ORDER BY s.start_time""", (lo, hi)):
        if r["event_id"]:
            if r["event_id"] in seen_ev:
                continue
            seen_ev.add(r["event_id"])
            n = q("SELECT COUNT(*) n FROM sessions WHERE event_id=? AND stage != 'lost'", (r["event_id"],), one=True)["n"]
            by[r["shoot_date"]]["shoots"].append(dict(event=True, eid=r["event_id"], name=r["event_name"], n=n, time=r["start_time"],
                                                      link=url_for("event_view", eid=r["event_id"]), done=r["stage"] in ("shot", "delivered", "closed")))
        else:
            where_ = (json.loads(r["intake"] or "{}").get("address") or "").strip().splitlines()
            by[r["shoot_date"]]["shoots"].append(dict(event=False, hid=r["household_id"], name=r["household"], time=r["start_time"],
                                                      where=where_[0] if where_ else "",
                                                      link=url_for("client_stage", hid=r["household_id"], s=r["id"]),
                                                      done=r["stage"] in ("shot", "delivered", "closed")))
    for a in acts:
        w_ = a.get("when")
        if w_ and w_ in by:
            by[w_]["due"].append(a)
    prev_ = (first - timedelta(days=1)).strftime("%Y-%m")
    next_ = (first + timedelta(days=32)).strftime("%Y-%m")
    return render_template("month.html", weeks=weeks, title=first.strftime("%B %Y"), prev_m=prev_, next_m=next_,
                           this_m=t.strftime("%Y-%m"), is_this=first.strftime("%Y-%m") == t.strftime("%Y-%m"))


def needs_you_shoots():
    return needs_you()["shoots"]


SESSION_FILTERS = None   # the only SQL fragments session_rows accepts (values are always bound parameters)


def _check_filter(where):
    """session_rows takes a WHERE built only from fixed fragments (VIEWS and the two search clauses); anything else is
    refused, so no request text can ever become SQL."""
    allowed = sorted({w for _l, w in VIEWS.values()} | {"1=1", " AND s.type=?", " AND (h.name LIKE ? OR s.title LIKE ? OR e.name LIKE ?)"},
                     key=len, reverse=True)
    rest = where
    for frag in allowed:
        rest = rest.replace(frag, "")
    if rest.strip():
        raise ValueError("unexpected SQL in a session filter")
    return where


def session_rows(where="1=1", args=()):
    rows = []
    for r in q("""SELECT s.*, h.name AS household, e.name AS event_name,
        (SELECT title FROM tasks WHERE session_id=s.id AND done IS NULL ORDER BY COALESCE(due,'9999'), sort LIMIT 1) AS next_task,
        (SELECT due FROM tasks WHERE session_id=s.id AND done IS NULL ORDER BY COALESCE(due,'9999'), sort LIMIT 1) AS next_due,
        (SELECT COUNT(*) FROM documents WHERE session_id=s.id) AS n_docs,
        (SELECT COUNT(*) FROM documents WHERE session_id=s.id AND status='signed') AS n_signed
        FROM sessions s JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE """ + _check_filter(where) + """ ORDER BY COALESCE(s.shoot_date,'9999'), s.id""", args):
        rows.append(dict(s=r, t=totals(r["id"]), wf=workflow(r["id"])))
    return rows


VIEWS = {
    "open": ("Open", "s.stage NOT IN ('closed','lost')"),
    "upcoming": ("Upcoming shoots", "s.shoot_date >= date('now','localtime') AND s.stage NOT IN ('lost')"),
    "leads": ("Leads", "s.stage IN ('inquiry','quoted')"),
    "post": ("Post-production", "s.stage IN ('shot','delivered')"),
    "all": ("All", "1=1"),
}


@app.get("/sessions")
def sessions():
    view = request.args.get("view", "open")
    label, where = VIEWS.get(view, VIEWS["open"])
    if request.args.get("owing"):
        where = "1=1"
    args = []
    if request.args.get("type"):
        where += " AND s.type=?"; args.append(request.args["type"])
    if request.args.get("q"):
        where += " AND (h.name LIKE ? OR s.title LIKE ? OR e.name LIKE ?)"; args += [f"%{request.args['q']}%"] * 3
    rows = session_rows(where, args)
    if request.args.get("owing"):
        rows = [r for r in rows if owed_now(r["s"]["stage"], r["t"], r["s"]["shoot_date"], row_type(r["s"]))]
    return render_template("sessions.html", rows=rows, view=view, views=VIEWS)


def _search_matches(hid, term):
    """A client's sessions, and the addresses that matched the search (they aren't otherwise shown): each session that
    matched on its address or venue carries it as 'hit'; the client's own address or company goes in matched."""
    t_ = term.lower()
    first = lambda v: next((ln.strip() for ln in (v or "").splitlines() if t_ in ln.lower()), (v or "").strip().splitlines()[0] if v else "")
    h = q("SELECT address, company FROM households WHERE id=?", (hid,), one=True)
    matched = [f"{lab}: {first(h[col])}" for col, lab in (("company", "Company"), ("address", "Address"))
               if h and t_ in (h[col] or "").lower()]
    ses = []
    for s_ in q("SELECT id, type, stage, shoot_date, intake FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999') DESC", (hid,)):
        d_ = dict(s_)
        intake = json.loads(d_.pop("intake") or "{}")
        d_["hit"] = next((first(str(intake[k])) for k in ("address", "venue") if t_ in str(intake.get(k) or "").lower()), "")
        ses.append(d_)
    return ses, matched


@app.get("/search")
def search():
    term = request.args.get("q", "").strip()
    like = f"%{term}%"
    hits = []
    if not term:
        for h in q("SELECT id, name FROM households ORDER BY name"):
            hits.append(dict(kind="client", h=h, people=q("SELECT name, phone, email FROM people WHERE household_id=?", (h["id"],)),
                             sessions=q("SELECT id, type, stage, shoot_date FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999') DESC", (h["id"],))))
        for e in q("SELECT * FROM events ORDER BY date DESC"):
            hits.append(dict(kind="event", e=e))
        return render_template("search.html", term=term, hits=hits, extra=[])
    if term:
        for h in q("""SELECT DISTINCT h.id, h.name FROM households h LEFT JOIN people p ON p.household_id=h.id
                      LEFT JOIN sessions s ON s.household_id=h.id
                      WHERE h.name LIKE ? OR h.company LIKE ? OR h.address LIKE ? OR p.name LIKE ? OR p.email LIKE ? OR p.phone LIKE ? OR
                      REPLACE(p.phone,'-','') LIKE ? OR json_extract(s.intake, '$.address') LIKE ?
                      OR json_extract(s.intake, '$.venue') LIKE ? ORDER BY h.name""",
                   (like, like, like, like, like, like, f"%{term.replace('-', '')}%", like, like)):
            ses, matched = _search_matches(h["id"], term)
            people = q("SELECT name, phone, email FROM people WHERE household_id=?", (h["id"],))
            hits.append(dict(kind="client", h=h, sessions=ses, people=people, matched=matched))
        for e in q("SELECT * FROM events WHERE name LIKE ? OR venue LIKE ? ORDER BY date DESC", (like, like)):
            hits.append(dict(kind="event", e=e))
    digits = "".join(ch for ch in term if ch.isdigit())
    if len(digits) >= 4 and not hits:
        for h in q("SELECT DISTINCT h.id, h.name FROM households h JOIN people p ON p.household_id=h.id"):
            phones = ["".join(ch for ch in (p["phone"] or "") if ch.isdigit()) for p in q("SELECT phone FROM people WHERE household_id=?", (h["id"],))]
            if any(digits in ph or (ph and ph in digits) for ph in phones):
                ses = q("SELECT id, type, stage, shoot_date FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999') DESC", (h["id"],))
                hits.append(dict(kind="client", h=h, sessions=ses, people=q("SELECT name, phone, email FROM people WHERE household_id=?", (h["id"],))))
    if term:                                     # beyond names and addresses: notes, titles, intake answers, numbers
        known = {x_["h"]["id"]: x_ for x_ in hits if x_["kind"] == "client"}
        more = {}

        def hit(hid_, line):
            more.setdefault(hid_, [])
            if line not in more[hid_] and len(more[hid_]) < 4:
                more[hid_].append(line)
        cut = lambda v: (lambda t_: t_ if len(t_) <= 90 else t_[:87] + "...")(" ".join(str(v or "").split()))
        for r_ in q("SELECT household_id, summary FROM log WHERE summary LIKE ? ORDER BY at DESC LIMIT 60", (like,)):
            hit(r_["household_id"], "History: " + cut(r_["summary"]))
        for r_ in q("SELECT id AS household_id, notes FROM households WHERE notes LIKE ?", (like,)):
            hit(r_["household_id"], "Notes: " + cut(r_["notes"]))
        for r_ in q("SELECT household_id, title, notes, po_number, intake FROM sessions WHERE title LIKE ? OR notes LIKE ? OR po_number LIKE ? OR intake LIKE ?",
                    (like, like, like, like)):
            for lab, v in (("Session", r_["title"]), ("Session notes", r_["notes"]), ("PO", r_["po_number"])):
                if term.lower() in str(v or "").lower():
                    hit(r_["household_id"], f"{lab}: {cut(v)}")
            for k_, v in json.loads(r_["intake"] or "{}").items():
                if k_ not in ("address", "venue") and term.lower() in str(v or "").lower():
                    hit(r_["household_id"], f"Intake: {cut(v)}")
        for r_ in q("""SELECT s.household_id, d.number FROM doc_numbers d JOIN sessions s ON s.id=d.session_id WHERE d.number LIKE ?""", (like,)):
            hit(r_["household_id"], ("Receipt " if r_["number"].startswith("R-") else "Invoice ") + r_["number"])
        if term.isdigit():
            for r_ in q("SELECT s.household_id, v.id FROM quote_versions v JOIN sessions s ON s.id=v.session_id WHERE v.id=?", (int(term),)):
                hit(r_["household_id"], f"Quote {r_['id']}")
        for r_ in q("""SELECT s.household_id, p.ref, p.amount FROM payments p JOIN sessions s ON s.id=p.session_id WHERE p.ref LIKE ?""", (like,)):
            hit(r_["household_id"], f"Check #{r_['ref']}, {money(r_['amount'])}")
        for r_ in q("SELECT household_id, name, notes FROM people WHERE notes LIKE ?", (like,)):
            hit(r_["household_id"], f"{r_['name']}: {cut(r_['notes'])}")
        for hid_, lines in more.items():
            if hid_ in known:
                known[hid_].setdefault("matched", []).extend(l_ for l_ in lines if l_ not in known[hid_]["matched"])
                continue
            h_ = q("SELECT id, name FROM households WHERE id=?", (hid_,), one=True)
            if h_:
                ses, _m = _search_matches(hid_, term)
                hits.append(dict(kind="client", h=h_, sessions=ses, matched=lines,
                                 people=q("SELECT name, phone, email FROM people WHERE household_id=?", (hid_,))))
    extra = []
    import settings_index
    found_settings = settings_index.find(term, C.SESSION_TYPES, url_for)
    if found_settings:
        extra.append(dict(title="Settings", rows=found_settings))
    for fn in hooks.search:                      # plugins may add their own results
        try:
            extra += [sec for sec in (fn(term) or []) if sec.get("rows")]
        except Exception:
            app.logger.exception("plugin search")
    if len(hits) == 1 and hits[0]["kind"] == "client" and len(hits[0]["sessions"]) == 1 and not extra:
        return redirect(stage_url(hits[0]["sessions"][0]["id"]))
    if len(hits) == 1 and not extra and hits[0]["kind"] == "event":
        return redirect(url_for("event_view", eid=hits[0]["e"]["id"]))
    return render_template("search.html", term=term, hits=hits, extra=extra)


@app.get("/pipeline")
def pipeline():
    rows = q("""SELECT s.*, h.name AS household, e.name AS event_name, e.date AS event_date FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        ORDER BY COALESCE(s.shoot_date, '9999'), s.id""")
    cols = {st: [] for st in C.STAGES + C.OFF_PIPELINE}
    grouped = {}
    for r in rows:
        if r["event_id"]:
            key = (r["event_id"], r["stage"])
            if key not in grouped:
                grouped[key] = dict(event=True, id=r["event_id"], name=r["event_name"], date=r["event_date"], n=0)
                cols[r["stage"]].append(grouped[key])
            grouped[key]["n"] += 1
        else:
            t = totals(r["id"])
            cols[r["stage"]].append(dict(event=False, s=r, quoted=t["quoted"]))
    show_lost = request.args.get("lost") == "1"
    return render_template("pipeline.html", cols=cols, show_lost=show_lost)


@app.post("/session/<int:sid>/stage")
def session_stage(sid):
    stage = request.form["stage"]
    if stage not in C.STAGES + C.OFF_PIPELINE:
        abort(400)
    old_ = (q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True) or abort(404))["stage"]
    back_ = old_ in C.STAGES and stage in C.STAGES and C.STAGES.index(stage) < C.STAGES.index(old_)
    set_stage(sid, stage)
    x("UPDATE sessions SET hold_stage=? WHERE id=?", (stage if back_ else None, sid))   # a step back by hand stays put
    if back_:
        session["notice"] = (f"Stays at {C.STAGE_LABELS[stage]} until you move it on, or reopen one of its steps"
                             " (it then moves on by itself once they are done again).")
    if stage == "lost" and request.form.get("lost_reason") in C.LOST_REASONS:
        x("UPDATE sessions SET lost_reason=? WHERE id=?", (request.form["lost_reason"], sid))
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid))


# ---------------------------------------------------------------- routes: clients

@app.get("/clients")
def clients():
    term = request.args.get("q", "").strip()
    like = f"%{term}%"
    rows = q("""SELECT h.*, (SELECT COUNT(*) FROM sessions WHERE household_id=h.id) AS n_sessions,
        (SELECT COUNT(*) FROM sessions WHERE household_id=h.id AND stage NOT IN ('closed','lost')) AS n_open,
        (SELECT GROUP_CONCAT(name, ', ') FROM people WHERE household_id=h.id) AS members,
        (SELECT COALESCE(NULLIF(phone,''), email) FROM people WHERE household_id=h.id AND role IN ('parent','adult')
           ORDER BY role='parent' DESC, id LIMIT 1) AS reach,
        (SELECT MAX(at) FROM log WHERE household_id=h.id) AS last_touch,
        (SELECT name FROM households r WHERE r.id=h.referred_by) AS referrer
        FROM households h WHERE ?='' OR h.name LIKE ? OR EXISTS (
          SELECT 1 FROM people p WHERE p.household_id=h.id AND (p.name LIKE ? OR p.email LIKE ? OR p.phone LIKE ?))
        ORDER BY h.name""", (term, like, like, like, like))
    return render_template("clients.html", rows=rows, term=term)


@app.route("/contact/new", methods=["GET", "POST"])
def contact_new():
    """Step 1: a contact is added on its own; an inquiry may or may not come with it."""
    if request.method == "GET":
        return render_template("contact_new.html", households=q("SELECT id, name FROM households ORDER BY name"))
    f = request.form
    name = f.get("contact_name", "").strip()
    if not name:
        abort(400)
    hid = x("INSERT INTO households (name, source, referred_by) VALUES (?,?,?)",
            (f.get("household_name", "").strip() or name, f.get("source", ""),
             int(f["referred_by"]) if f.get("referred_by") else None))
    rel, gender, by, role, minor, approx = person_fields(dict(relation=f.get("contact_relation"), gender=f.get("contact_gender"),
                                                              age=f.get("contact_age"), age_approx=f.get("contact_age_approx"), role="adult"))
    x("""INSERT INTO people (household_id, name, role, email, phone, relation, gender, birth_year, age_approx)
         VALUES (?,?,?,?,?,?,?,?,?)""", (hid, name, role, f.get("email", ""), f.get("phone", ""), rel, gender, by, approx))
    if f.get("note"):
        x("INSERT INTO log (household_id, channel, summary, at) VALUES (?,?,?,?)",
          (hid, f.get("channel", "email"), f["note"], now_local()))
    if f.get("then") == "inquiry":
        return redirect(url_for("inquiry", hid=hid))
    return redirect(url_for("client_view", hid=hid))


@app.route("/inquiry", methods=["GET", "POST"])
def inquiry():
    """Step 2: an inquiry belongs to a contact. Pick the contact, then the shoot type, then a form shaped by the type."""
    if request.method == "GET":
        hid = request.args.get("hid", type=int)
        t_ = request.args.get("type", "")
        h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) if hid else None
        if hid and not h:
            abort(404)
        return render_template("inquiry.html", h=h, type_=t_ if t_ in C.SESSION_TYPES else "",
                               households=sorted(q("SELECT id, name FROM households"), key=lambda r_: re.sub(r"^the\s+", "", r_["name"].lower())),
                               people=q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id",
                                        (hid,)) if h else [],
                               plans=q("""SELECT id, title, type, shoot_date FROM sessions WHERE household_id=? AND plan_of IS NULL
                                          AND stage NOT IN ('closed','lost') ORDER BY id""", (hid,)) if h else [])
    f = request.form
    t_ = f.get("type", "")
    hid = f.get("household_id", type=int)
    if t_ not in C.SESSION_TYPES or not hid or not q("SELECT 1 FROM households WHERE id=?", (hid,), one=True):
        abort(400)
    subject_ids = [int(v) for v in f.getlist("subjects") if v.isdigit()]
    if t_ in C.SUBJECT_TYPES and f.get("subject_name", "").strip():
        rel_ = f.get("subject_relation") or ("pet" if t_ in C.ANIMAL_TYPES else "")
        if t_ in C.BABY_TYPES and rel_ not in PARENT_REL and rel_ != "pet" and not f.get("subject_age"):
            f = f.copy(); f["subject_minor"] = "1"; f["minor_shown"] = "1"   # a newborn or baby session: the baby is a minor
        rel, gender, by, role, minor, approx = person_fields(dict(relation=rel_, gender=f.get("subject_gender"),
                                                                  age=f.get("subject_age"), age_approx=f.get("subject_age_approx"),
                                                                  is_minor=f.get("subject_minor"), minor_shown=f.get("minor_shown"),
                                                                  role="child" if f.get("subject_minor") else "adult"))
        # several people at once: "Ava, Ben and Cole". The details typed (who, age, minor) belong to the first; the
        # others are added by name, as minors only when the Minor box is ticked, and are filled in on the client page.
        names = []
        for n_ in (n_.strip() for n_ in re.split(r",|\band\b|&", f["subject_name"])):
            if not n_:
                continue
            if re.fullmatch(r"\(?\d{1,2}\)?", n_) and names:   # "Ada, 7": the number belongs to the name before it
                names[-1] += " " + n_
            elif n_.lower() not in [x_.lower() for x_ in names]:   # "Mia and Mia": one Mia
                names.append(n_)
        names = names[:12] or [f["subject_name"].strip()]
        minor_typed = minor                                # the Minor box, for names typed without an age
        have_ = {" ".join(p_["name"].lower().split()): p_["id"] for p_ in q("SELECT id, name FROM people WHERE household_id=?", (hid,))}
        for i_, nm in enumerate(names):
            first_ = i_ == 0
            bare_ = " ".join(re.sub(r"[\s,(]+\d{1,2}\s*\)?$", "", nm).lower().split())
            firsts_ = [pid_ for n_, pid_ in have_.items() if n_.split()[:1] == [bare_]] if " " not in bare_ else []
            if bare_ in have_ or len(firsts_) == 1:       # already in this family ("Priya" for Priya Shah): not a copy
                pid_ = have_.get(bare_) or firsts_[0]
                if pid_ not in subject_ids:
                    subject_ids.append(pid_)
                continue
            m_ = re.match(r"^(.*?)[\s,(]+(\d{1,2})\s*\)?$", nm)     # "Ada (7)", "Ada 7", "Ada, 7": the number is the age
            if m_ and m_.group(1).strip() and first_ and f.get("subject_age", "").strip():
                nm = m_.group(1).strip()                  # the age box wins; the "(7)" still leaves the name
            elif m_ and m_.group(1).strip():
                nm, age_ = m_.group(1).strip(), int(m_.group(2))
                kid_ = age_ < majority_age() and role != "pet"
                if first_:
                    by, approx, minor = today().year - age_, 1, 1 if kid_ else minor_typed
                    role = "child" if kid_ else role
                else:
                    subject_ids.append(x("""INSERT INTO people (household_id, name, role, is_minor, birth_year, age_approx)
                                            VALUES (?,?,?,?,?,1)""", (hid, nm[:200], "pet" if role == "pet" else ("child" if kid_ else "adult"),
                                                                      1 if kid_ else 0, today().year - age_)))
                    continue
            subject_ids.append(x("""INSERT INTO people (household_id, name, role, is_minor, grad_year, relation, gender, birth_year, age_approx)
                                    VALUES (?,?,?,?,?,?,?,?,?)""",
                                 (hid, nm[:200], role if first_ else ("pet" if role == "pet" else ("child" if minor_typed else "adult")),
                                  minor if first_ else (0 if role == "pet" else minor_typed),
                                  int(f["grad_year"]) if first_ and t_ in C.GRAD_TYPES and f.get("grad_year", "").isdigit() else None,
                                  rel if first_ else ("pet" if role == "pet" else ""), gender if first_ else "",
                                  by if first_ else None, approx if first_ else 0)))
    if t_ not in C.SUBJECT_TYPES:
        subject_ids = [p["id"] for p in q("SELECT id FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY id LIMIT 1", (hid,))]
    sid = create_session(hid, t_, "inquiry", f.get("title", ""), f.get("shoot_date") or None, subject_ids=subject_ids)
    head_ = q("SELECT * FROM sessions WHERE id=? AND household_id=? AND plan_of IS NULL", (f.get("plan_of", type=int) or 0, hid), one=True)
    if head_ and head_["id"] != sid:          # part of a plan from the start: one agreement covers both
        x("UPDATE sessions SET plan_of=? WHERE id=?", (head_["id"], sid))
        x("DELETE FROM documents WHERE session_id=? AND kind='agreement' AND status != 'signed'", (sid,))
        if q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (head_["id"],), one=True):
            # a bare inquiry never unsigns a booked agreement: a to-do asks for it to be signed again once this books
            x("INSERT INTO tasks (session_id, title, due, sort, stage, own) VALUES (?,?,?,999,?,1)",
              (head_["id"], PLAN_RESIGN, None, head_["stage"]))
    if (f.get("gallery_codeword") or "").strip():
        from plugins import gallery as _G
        cw_ = " ".join(f["gallery_codeword"].split())[:60]
        if _G.codeword_strong(cw_):
            x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (cw_, sid))
        else:
            session["notice"] = (f"The gallery codeword wasn't saved: use three words or 14 letters, such as "
                                 f"\"{_G.suggest_codeword()}\". Add it on the session page.")
    answers = {k: f.get("i_" + k, "").strip() for k, _ in C.INTAKE.get(t_, [])}
    if any(answers.values()):
        x("UPDATE sessions SET intake=? WHERE id=?", (json.dumps(answers), sid))
    # the inquiry IS the first contact: it is logged with or without a note, so "Log the first contact" never asks again
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (hid, sid, f.get("channel") or "note", f.get("note", "").strip() or f"Inquiry: {C.SESSION_TYPES[t_]}", now_local()))
    return redirect(stage_url(sid))


@app.get("/client/<int:hid>")
def client_view(hid):
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    people = q("SELECT * FROM people WHERE household_id=? ORDER BY role DESC, name", (hid,))
    types = {r_["type"] for r_ in q("SELECT type FROM sessions WHERE household_id=?", (hid,))}
    # grad year only for seniors and sports; gender only for people photographed in portraits (not a company's staff, not pet owners)
    show_grad = bool(types & set(C.GRAD_TYPES)) or any(p_["grad_year"] for p_ in people)
    show_born = bool(types & set(C.BABY_TYPES)) or any(p_["birth_date"] for p_ in people)
    show_gender = (not (h["company"] or "").strip() and not (types and types <= (set(C.ANIMAL_TYPES) | set(C.LICENSE_TYPES)))) \
        or any(p_["gender"] for p_ in people)
    return render_template(
        "client.html", h=h, show_grad=show_grad, show_gender=show_gender, show_born=show_born,
        people=people,
        sessions=q("SELECT * FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999') DESC", (hid,)),
        log=q("SELECT * FROM log WHERE household_id=? ORDER BY at DESC", (hid,)),
        referrer=q("SELECT id, name FROM households WHERE id=?", (h["referred_by"],), one=True),
        referred=q("SELECT id, name FROM households WHERE referred_by=?", (hid,)),
        households=q("SELECT id, name FROM households WHERE id != ? ORDER BY name", (hid,)))


@app.post("/client/<int:hid>")
def client_save(hid):
    f = request.form
    for r_ in q("SELECT id FROM sessions WHERE household_id=? AND stage IN ('inquiry','quoted')", (hid,)):
        g.setdefault("_retax", []).append(r_["id"])
    x("UPDATE households SET name=?, company=?, address=?, source=?, referred_by=?, notes=? WHERE id=?",
      (f["name"], f.get("company", "").strip()[:200], f.get("address", ""), f.get("source", ""),
       int(f["referred_by"]) if f.get("referred_by") else None, f.get("notes", ""), hid))
    for sid_ in g.pop("_retax", []):
        reapply_tax(sid_)
    return redirect(url_for("client_view", hid=hid) + "#details")


@app.get("/face/<int:hid>")
def face_img(hid):
    """Face photos are encrypted on disk (tied to their household); decrypted in memory, never cached by the browser."""
    f = FACES / f"{hid}.jpg"
    try:
        data = datakey.open_bytes(f.read_bytes(), f"face:{hid}", label="faces")
    except (OSError, ValueError):
        abort(404)                               # missing, or damaged / altered (the check failed)
    r = app.response_class(data, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.post("/client/<int:hid>/face")
def face_upload(hid):
    from PIL import Image, ImageOps
    FACES.mkdir(parents=True, exist_ok=True)
    if request.form.get("remove"):
        (FACES / f"{hid}.jpg").unlink(missing_ok=True)
    elif request.files.get("photo"):
        im = ImageOps.exif_transpose(Image.open(request.files["photo"].stream)).convert("RGB")
        side = min(im.size)
        left, top = (im.width - side) // 2, max(0, (im.height - side) // 3)
        import io as _io
        buf = _io.BytesIO()
        im.crop((left, top, left + side, top + side)).resize((320, 320), Image.LANCZOS).save(buf, "JPEG", quality=86)
        datakey.write_sealed(FACES / f"{hid}.jpg", buf.getvalue(), f"face:{hid}", label="faces")
    return redirect(safe_back(request.form.get("back")) or url_for("client_view", hid=hid))


@app.post("/client/<int:hid>/note")
def client_note(hid):
    f = request.form
    when = (f.get("when") or "").replace("T", " ")
    when = (when + ":00")[:19] if len(when) == 16 else now_local()
    try:                                          # a real date and time, this century or last: else now
        if datetime.strptime(when, "%Y-%m-%d %H:%M:%S").year < 1900:
            when = now_local()
    except ValueError:
        when = now_local()
    when = min(when, now_local())
    x("INSERT INTO log (household_id, session_id, channel, summary, at, written_at) VALUES (?,?,?,?,?,?)",
      (hid, int(f["session_id"]) if f.get("session_id") else None, f.get("channel", "note"), f["text"].strip(), when, now_local()))
    return redirect(safe_back(f.get("back")) or url_for("client_stage", hid=hid) + "#history")


@app.post("/note/<int:lid>/delete")
def note_delete(lid):
    r = q("SELECT * FROM log WHERE id=?", (lid,), one=True) or abort(404)
    x("DELETE FROM log WHERE id=?", (lid,))
    return redirect(safe_back(request.form.get("back")) or url_for("client_stage", hid=r["household_id"]) + "#history")


@app.post("/client/<int:hid>/person")
def person_add(hid):
    f = request.form
    rel, gender, by, role, minor, approx = person_fields(f)
    pid_ = x("""INSERT INTO people (household_id, name, role, is_minor, grad_year, email, phone, relation, gender, birth_year, age_approx)
         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
      (hid, f["name"], role, minor, int(f["grad_year"]) if f.get("grad_year", "").strip().isdigit() else None, f.get("email", ""),
       f.get("phone", ""), rel, gender, by, approx))
    _save_birth_date(pid_, f)
    _same_name_notice(f["name"], hid)
    return redirect(url_for("client_view", hid=hid) + "#people")


def _save_birth_date(pid, f):
    """Born (or due) date, for babies: optional; a due date may be ahead. A baby is a minor."""
    if "birth_date" not in f:
        return
    v = (f.get("birth_date") or "").strip()
    try:
        v = date.fromisoformat(v).isoformat() if v else None
    except ValueError:
        return
    x("UPDATE people SET birth_date=? WHERE id=?", (v, pid))
    if v:
        x("UPDATE people SET birth_year=?, is_minor=1 WHERE id=?", (int(v[:4]), pid))


def _same_name_notice(name, hid):
    """Two people with the same name are allowed (it happens), but say so, in case it's the same person twice."""
    other = q("""SELECT h.name FROM people p JOIN households h ON h.id=p.household_id
                 WHERE lower(trim(p.name))=lower(trim(?)) AND p.household_id != ? LIMIT 1""", (name, hid), one=True)
    if other:
        session["notice"] = f"{name.strip()} is also a person under {other['name']}. Check it isn't the same person twice."


@app.post("/person/<int:pid>")
def person_save(pid):
    f = request.form
    p = q("SELECT * FROM people WHERE id=?", (pid,), one=True) or abort(404)
    if f.get("delete"):
        # their unsigned releases go with them (a signed one is a record and stays, kept by its snapshot)
        x("DELETE FROM documents WHERE about_person=? AND kind='release' AND status != 'signed'", (pid,))
        x("DELETE FROM people WHERE id=?", (pid,))
    else:
        rel, gender, by, role, minor, approx = person_fields(f, cur=p)
        x("""UPDATE people SET name=?, role=?, is_minor=?, grad_year=?, email=?, phone=?, relation=?, gender=?, birth_year=?, age_approx=?
             WHERE id=?""",
          (f["name"], role, minor, int(f["grad_year"]) if f.get("grad_year", "").strip().isdigit() else None, f.get("email", ""),
           f.get("phone", ""), rel, gender, by, approx, pid))
        _save_birth_date(pid, f)
    return redirect(url_for("client_view", hid=p["household_id"]) + "#people")


@app.post("/log")
def log_add():
    f = request.form
    hid_, sid_ = f.get("household_id", type=int), f.get("session_id", type=int)
    if hid_ is None or not f.get("summary"):
        abort(400)
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (hid_, sid_,
       f.get("channel", "note"), f["summary"], datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    return redirect(safe_back(f.get("back")) or url_for("client_view", hid=f["household_id"]))


@app.post("/client/<int:hid>/session")
def client_new_session(hid):
    f = request.form
    if f.get("stage", "inquiry") not in C.STAGES or f["type"] not in C.SESSION_TYPES:
        abort(400)
    sid = create_session(hid, f["type"], f.get("stage", "inquiry"), f.get("title", ""),
                         f.get("shoot_date") or None, subject_ids=[int(v) for v in f.getlist("subjects")])
    return redirect(url_for("session_view", sid=sid))


# ---------------------------------------------------------------- routes: sessions

def stage_url(sid):
    r = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
    return url_for("client_stage", hid=r["household_id"], s=sid) if r else url_for("home")


@app.get("/session/<int:sid>")
def session_view(sid):
    s = session_full(sid)
    panels = [Markup(h) for h in (fn(s) for fn in hooks.session_panels) if h]
    tabs = [(lab, cnt, Markup(html)) for lab, cnt, html in (t for t in (fn(s) for fn in hooks.session_tabs) if t)]
    return render_template(
        "session.html", s=s, intake=json.loads(s["intake"] or "{}"),
        plan_opts=[r_ for r_ in q("SELECT * FROM sessions WHERE household_id=? AND id != ? AND plan_of IS NULL ORDER BY id", (s["household_id"], sid))]
                  if s["type"] in C.NEEDS_AGREEMENT and not q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True) else [],
        signer_opts=signer_choices(s["household_id"]) if s["type"] in C.NEEDS_AGREEMENT else [],
        signer_ids={r_["about_person"] for r_ in agreement_signers(sid)[0] if r_["about_person"]}
                   or {p_["id"] for p_ in signer_choices(s["household_id"])[:1]},
        subjects=q("""SELECT p.* FROM people p JOIN session_people sp ON sp.person_id=p.id
                      WHERE sp.session_id=?""", (sid,)),
        members=q("SELECT * FROM people WHERE household_id=?", (s["household_id"],)),
        tasks=q("SELECT * FROM tasks WHERE session_id=? ORDER BY COALESCE(due,'9999'), sort", (sid,)),
        docs=q("""SELECT doc.*, p.name AS person FROM documents doc LEFT JOIN people p ON p.id=doc.about_person
                  WHERE doc.session_id=?""", (sid,)),
        lines=q("SELECT * FROM lines WHERE session_id=?", (sid,)),
        payments=q("SELECT * FROM payments WHERE session_id=? ORDER BY received", (sid,)),
        expenses=q("SELECT * FROM expenses WHERE session_id=? ORDER BY date", (sid,)),
        log=q("SELECT * FROM log WHERE session_id=? ORDER BY at DESC", (sid,)),
        t=totals(sid), expires=gallery_expires(s), panels=panels, tabs=tabs, wf=workflow(sid),
        contact=household_contact(q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id",
                                    (s["household_id"],))),
        events=q("SELECT id, name FROM events ORDER BY date DESC"), minutes=session_minutes(sid, s["type"]))


@app.post("/session/<int:sid>")
def session_save(sid):
    f = request.form
    s = session_full(sid)
    x("""UPDATE sessions SET title=?, type=?, shoot_date=?, start_time=?, stops=?, notes=?, event_id=?
         WHERE id=?""",
      (f.get("title", ""), f.get("type", s["type"]), f.get("shoot_date") or None, start_time_in(f.get("start_time", "")),
       f.get("stops", ""), f.get("notes", ""), int(f["event_id"]) if f.get("event_id") else None, sid))
    if "duration_min" in f:
        x("UPDATE sessions SET duration_min=? WHERE id=?", (int(f["duration_min"]) if f["duration_min"].isdigit() else None, sid))
    if "po_number" in f:
        x("UPDATE sessions SET po_number=? WHERE id=?", (f.get("po_number", "").strip()[:80], sid))
    for k_ in ("lic_term", "lic_territory"):
        if k_ in f:
            x(f"UPDATE sessions SET {k_}=? WHERE id=?", (f.get(k_, "").strip()[:200], sid))
    if "lic_present" in f:
        x("UPDATE sessions SET lic_exclusive=? WHERE id=?", (1 if f.get("lic_exclusive") else 0, sid))
    if "date_tentative_present" in f:
        x("UPDATE sessions SET date_tentative=? WHERE id=?", (1 if f.get("date_tentative") else 0, sid))
    if f.get("subjects_present"):
        db().execute("DELETE FROM session_people WHERE session_id=?", (sid,))
        for pid in f.getlist("subjects"):
            db().execute("INSERT INTO session_people VALUES (?,?)", (sid, int(pid)))
        _commit()
    sync_workflow_tasks(sid)
    return redirect(url_for("session_view", sid=sid) + "#shoot")


@app.post("/session/<int:sid>/intake")
def session_intake(sid):
    s = session_full(sid)
    data = {k: request.form.get(k, "") for k, _ in C.INTAKE.get(s["type"], [])}
    x("UPDATE sessions SET intake=? WHERE id=?", (json.dumps(data), sid))
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#intake")


@app.post("/session/<int:sid>/delivery")
def session_delivery(sid):
    """The delivery dates. Each must be a real day, not in the future and not before the shoot: these dates start the
    clocks for sealing and deleting the session's photos, so a typo must not get in (a year off is the usual one)."""
    f = request.form
    s_ = session_full(sid)
    for k_, lab in (("gallery_built", "Gallery built"), ("letter_mailed", "Letter mailed"), ("first_visit", "First visit"),
                    ("sd_shipped", "Files delivered")):
        v = (f.get(k_) or "").strip()
        if not v or v == (s_[k_] or ""):
            continue
        try:
            dv = date.fromisoformat(v)
        except ValueError:
            dv = None
        if not dv or dv > today() or (s_["shoot_date"] and v < s_["shoot_date"][:10]):
            session["notice"] = (f"{lab}: {v} can't be right" + (f" (the shoot was {nice(s_['shoot_date'])})" if s_["shoot_date"] else "")
                                 + ". Nothing was changed.")
            g.refused = True
            return redirect(url_for("session_view", sid=sid) + "#delivery")
    if f.get("delivery_method") in C.DELIVERY_METHODS:
        x("UPDATE sessions SET delivery_method=? WHERE id=?", (f["delivery_method"], sid))
    if "retouch_picks" in f:
        x("UPDATE sessions SET retouch_picks=? WHERE id=?", ((f.get("retouch_picks") or "")[:5000], sid))
    if "gallery_codeword" in f and (f.get("gallery_codeword") or "").strip() != (s_["gallery_codeword"] or ""):
        g_ = q("SELECT cw_fixed FROM galleries WHERE session_id=?", (sid,), one=True) if q(
            "SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
        from plugins import gallery as _G
        new_cw = " ".join((f.get("gallery_codeword") or "").split())[:60]
        if g_ and g_["cw_fixed"]:
            session["notice"] = "The gallery is already online, so its codeword stays as it is: the link you sent keeps working."
        elif new_cw and not _G.codeword_strong(new_cw):
            session["notice"] = (f"That codeword is too easy to guess. Use three words or 14 letters, such as "
                                 f"\"{_G.suggest_codeword()}\". Nothing was changed.")
        else:
            x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (new_cw, sid))
    x("""UPDATE sessions SET gallery_built=?, letter_mailed=?, first_visit=?, extension_used=?, sd_shipped=?,
         favorites=?, veto=? WHERE id=?""",
      (f.get("gallery_built") or None, f.get("letter_mailed") or None, f.get("first_visit") or None,
       1 if f.get("extension_used") else 0, f.get("sd_shipped") or None, f.get("favorites", ""),
       f.get("veto", ""), sid))
    hooks.fire("gallery_window_changed", sid)
    return redirect(url_for("session_view", sid=sid) + "#delivery")


@app.post("/session/<int:sid>/delete")
def session_delete(sid):
    s = session_full(sid)
    # nothing of a deleted session may outlive it: web gallery down, RAWs/exports/local gallery purged
    if app.config.get("gallery_take_down"):
        try:
            app.config["gallery_take_down"](sid)
        except Exception:
            app.logger.exception("take-down on delete %s", sid)
    if app.config.get("retention_purge_now"):
        try:
            app.config["retention_purge_now"](sid)
        except Exception:
            app.logger.exception("purge on delete %s", sid)
            abort(500, "Could not delete this session's sealed photos; the session was kept. Check the vault storage.")
    _keep_books([sid], why="session deleted")         # money received is never erased
    for tbl_ in ("doc_snapshots", "doc_snapshots_old"):  # its signed copies go with it (nothing else could reach them)
        x("DELETE FROM " + tbl_ + " WHERE (kind='agreement' AND ref=?) OR (kind='release' AND ref IN "
          "(SELECT id FROM documents WHERE session_id=?))", (sid, sid))
    x("DELETE FROM sessions WHERE id=?", (sid,))
    left = q("SELECT id FROM sessions WHERE household_id=? LIMIT 1", (s["household_id"],), one=True)
    return redirect(url_for("client_stage", hid=s["household_id"]) if left else url_for("client_view", hid=s["household_id"]))


def household_rows(hid):
    """Every row about one client: tables keyed by the household, and by its sessions. {table: [dict]}"""
    sids = [r["id"] for r in q("SELECT id FROM sessions WHERE household_id=?", (hid,))]
    out = {"households": [dict(r) for r in q("SELECT * FROM households WHERE id=?", (hid,))]}
    tables = [r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t_ in tables:
        cols = dbmod.columns(db(), t_)
        if t_ == "households":
            continue
        rows = []
        if "household_id" in cols:
            rows += q("SELECT * FROM " + dbmod.ident(t_) + " WHERE household_id=?", (hid,))
        if "session_id" in cols and sids:
            marks = ",".join("?" * len(sids))
            rows += q("SELECT * FROM " + dbmod.ident(t_) + " WHERE session_id IN (" + marks + ")"
                      + (" AND (household_id IS NULL OR household_id != ?)" if "household_id" in cols else ""),
                      tuple(sids) + ((hid,) if "household_id" in cols else ()))
        if rows:
            out[t_] = [dict(r) for r in rows]
    return out


@app.get("/client/<int:hid>/export.json")
def client_export(hid):
    """Everything held about one client, as JSON (for a client who asks what you keep, or to take it elsewhere)."""
    h = q("SELECT name FROM households WHERE id=?", (hid,), one=True) or abort(404)
    data = household_rows(hid)
    secret_ = {"key", "ekey", "fkey", "gallery_codeword", "picks_hash"}      # keys and codewords are never handed out
    def _strip(v):
        if isinstance(v, dict):
            return {k_: _strip(x_) for k_, x_ in v.items() if k_ not in secret_}
        if isinstance(v, list):
            return [_strip(x_) for x_ in v]
        return v
    data = _strip(data)
    r = app.response_class(json.dumps(data, indent=1, default=str), mimetype="application/json")
    r.headers["Content-Disposition"] = f'attachment; filename="client-{hid}.json"'
    audit("Exported everything held about this client", hid=hid)
    return r


DELETIONS = DB_PATH.parent / "deletions.enc"   # older installs: sealed under the data key (read, never written again)
DELETIONS_JSON = DB_PATH.parent / "deletions.json"   # outside the database and outside any key: survives a restore and a
                                                     # key rotation (record ids and creation times only, no client details)
NUMBERS = DB_PATH.parent / "doc-numbers.enc"   # highest number issued per run, kept outside the database


def _deletions():
    out = []
    try:
        out = json.loads(DELETIONS_JSON.read_text())
    except (OSError, ValueError):
        pass
    try:
        old = json.loads(datakey.open_bytes(DELETIONS.read_bytes(), "deletions", label="deletions"))
        seen = {(d_["id"], d_["created"]) for d_ in out}
        out += [d_ for d_ in old if (d_["id"], d_["created"]) not in seen]
    except (OSError, ValueError, Exception):
        pass
    return out


def _record_deletion(hid, created):
    items = _deletions() + [dict(id=hid, created=created, at=now_local())]
    _write_plain(DELETIONS_JSON, json.dumps(items))


def reapply_deletions():
    """After a restore from an older backup, clients deleted since then are deleted again (matched by id AND creation
    time, so a newer client that reused an id is never touched)."""
    n = 0
    con = connect()
    try:
        ev_cols = {r_[1] for r_ in con.execute("PRAGMA table_info(events)")}
        for d_ in _deletions():
            r = con.execute("SELECT id FROM households WHERE id=? AND created=?", (d_["id"], d_["created"])).fetchone()
            if not r:
                continue
            try:                                          # one that fails is logged; it never keeps the app locked
                con.execute("SAVEPOINT redelete")
                sids = [x_[0] for x_ in con.execute("SELECT id FROM sessions WHERE household_id=?", (d_["id"],))]
                marks = ",".join("?" * len(sids)) or "NULL"
                con.execute("DELETE FROM audit WHERE household_id=? OR session_id IN (" + marks + ")", (d_["id"], *sids))
                if sids:
                    for tbl_ in ("doc_snapshots", "doc_snapshots_old"):
                        con.execute("DELETE FROM " + tbl_ + " WHERE (kind='agreement' AND ref IN (" + marks + ")) OR (kind='release' AND ref IN "
                                    "(SELECT id FROM documents WHERE session_id IN (" + marks + ")))", (*sids, *sids))
                    con.execute("DELETE FROM vault_keys WHERE session_id IN (" + marks + ")", tuple(sids))   # archives unreadable
                    import photostore as _ps                  # their photos, and the mirror copies on the next pass
                    if con.execute("SELECT 1 FROM sqlite_master WHERE name='photo_files'").fetchone():
                        for s_ in sids:
                            for pr_ in con.execute("SELECT * FROM photo_files WHERE session_id=?", (s_,)).fetchall():
                                _ps.delete(con, pr_, commit=False)
                con.execute("UPDATE households SET referred_by=NULL WHERE referred_by=?", (d_["id"],))
                for c_ in ("payer_hid", "contact_hid"):
                    if c_ in ev_cols:
                        con.execute(f"UPDATE events SET {c_}=NULL WHERE {c_}=?", (d_["id"],))
                con.execute("DELETE FROM households WHERE id=?", (d_["id"],))
                con.execute("RELEASE redelete")
            except Exception:  # noqa: BLE001
                con.execute("ROLLBACK TO redelete")
                con.execute("RELEASE redelete")
                app.logger.exception("re-deleting client %s after a restore", d_["id"])
                continue
            (FACES / f"{d_['id']}.jpg").unlink(missing_ok=True)
            n += 1
        if n:
            con.execute("INSERT INTO audit (at, action, who) VALUES (?,?,?)",
                        (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                         f"Deleted again {n} client record(s) that had been deleted after this backup was made", "Lumen Aureum"))
        con.commit()
    finally:
        con.close()
    return n


@app.post("/client/<int:hid>/delete")
def client_delete(hid):
    """Delete a client and everything about them: galleries off the web, photos and sealed archives destroyed, face
    photo, every record and every history line that names them. One line stays, without a name."""
    if request.form.get("confirm", "").strip() != "DELETE":
        return redirect(url_for("client_view", hid=hid) + "?delete=nope")
    h_ = q("SELECT id, name, created FROM households WHERE id=?", (hid,), one=True) or abort(404)
    import jobs

    def work(job):                     # a background job: galleries come down over the network, photos are purged
        _delete_client(hid, h_["created"])
        return f"Deleted the client record and everything about it."
    jobs.start(app, f"client-delete:{hid}", f"Deleting {h_['name']} and everything about them", work)
    session["notice"] = f"Deleting {h_['name']}: galleries come down and photos are destroyed first. The progress shows at the bottom of the page."
    return redirect(url_for("home"))


def _delete_client(hid, created):
    """The work of a client delete (run as a job). Galleries and photos go first; if either fails, nothing else is
    deleted and the job says why."""
    if not q("SELECT 1 FROM households WHERE id=?", (hid,), one=True):
        return                                           # already gone (a second click)
    sids = [r["id"] for r in q("SELECT id FROM sessions WHERE household_id=?", (hid,))]
    for sid in sids:
        if app.config.get("gallery_take_down"):
            try:
                app.config["gallery_take_down"](sid)
            except Exception:
                app.logger.exception("take-down on client delete %s", sid)
                raise RuntimeError("Could not take a gallery down; nothing was deleted. Check the gallery host.")
        if app.config.get("retention_purge_now"):
            try:
                app.config["retention_purge_now"](sid)
            except Exception:
                app.logger.exception("purge on client delete %s", sid)
                raise RuntimeError("Could not delete this client's photos; nothing else was deleted. Check the vault storage.")
    import locks
    with locks.WRITES:                                   # the record itself goes in one short, uninterrupted step
        _delete_client_rows(hid, created, sids)


def _delete_client_rows(hid, created, sids):
    (FACES / f"{hid}.jpg").unlink(missing_ok=True)
    _keep_books(sids)
    _record_deletion(hid, created)
    marks = ",".join("?" * len(sids)) or "NULL"
    if sids:
        x("DELETE FROM vault_keys WHERE session_id IN (" + marks + ")", tuple(sids))
        x("DELETE FROM vault_objects WHERE session_id IN (" + marks + ") AND deleted IS NOT NULL", tuple(sids))
    if sids:                                  # signed copies of their agreements and releases go with them
        for tbl_ in ("doc_snapshots", "doc_snapshots_old"):
            x("DELETE FROM " + tbl_ + " WHERE (kind='agreement' AND ref IN (" + marks + ")) OR (kind='release' AND ref IN "
              "(SELECT id FROM documents WHERE session_id IN (" + marks + ")))", (*sids, *sids))
    x("DELETE FROM audit WHERE household_id=? OR session_id IN (" + marks + ")", (hid, *sids))
    x("UPDATE households SET referred_by=NULL WHERE referred_by=?", (hid,))
    for c_ in ("payer_hid", "contact_hid"):
        if c_ in dbmod.columns(db(), "events"):
            x(f"UPDATE events SET {c_}=NULL WHERE {c_}=?", (hid,))
    x("DELETE FROM households WHERE id=?", (hid,))
    audit("Deleted a client record and everything about it")


def _keep_books(sids, why="client deleted"):
    """Money already received stays in the year's totals and its sales tax stays owed after a client is deleted:
    each payment is kept as a nameless line (date, amount, its share of the tax, session type), each expense as
    unnamed overhead."""
    for sid in sids:
        s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
        tot_ = q("SELECT COALESCE(SUM(amount),0) v, COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0) t FROM lines WHERE session_id=?",
                 (sid,), one=True)
        for p_ in q("SELECT received, amount, kind FROM payments WHERE session_id=? AND voided IS NULL", (sid,)):
            tax = round(p_["amount"] * tot_["t"] / tot_["v"], 2) if tot_["v"] > 0 and tot_["t"] else 0.0
            x("INSERT INTO books (received, amount, tax, type, kind) VALUES (?,?,?,?,?)",
              (p_["received"], p_["amount"], tax, s_["type"] if s_ else "", p_["kind"]))
        for e_ in q("SELECT date, amount, miles FROM expenses WHERE session_id=?", (sid,)):
            x("INSERT INTO expenses (session_id, date, label, amount, miles) VALUES (NULL,?,?,?,?)",
              (e_["date"], f"Session expense ({why})", e_["amount"], e_["miles"]))


@app.post("/task/<int:tid>")
def task_toggle(tid):
    t = q("SELECT * FROM tasks WHERE id=?", (tid,), one=True) or abort(404)
    if request.form.get("delete"):
        x("DELETE FROM tasks WHERE id=?", (tid,))
    elif not t["done"] and t["title"] == "Confirm time and place" and t["session_id"] and (
            not (s_ := session_full(t["session_id"]))["start_time"] or not has_place(s_)):
        missing = " or ".join(w_ for w_, ok in (("start time", s_["start_time"]), ("place", has_place(s_))) if not ok)
        session["notice"] = f"Not confirmed yet: the session has no {missing}. Set it on the session page first."
        g.refused = True
        return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=t["session_id"]))
    else:
        want = (request.form.getlist("set") or [None])[-1]   # what the button showed: "1" done, "0" not done (never a blind flip)
        done_now = (want == "1") if want in ("0", "1") else not t["done"]
        if bool(t["done"]) == done_now:
            g.refused = True                         # already that way (a stale page or a double click): nothing to do
        else:
            x("UPDATE tasks SET done=?, outcome=? WHERE id=?",
              (today().isoformat() if done_now else None, "done" if done_now else None, tid))
            if done_now and t["title"] == SEND_TASKS["invoice"] and t["session_id"]:
                _invoice_marked_sent(t["session_id"])
            if done_now and t["title"] == PLAN_RESIGN and t["session_id"]:
                # signed again for the whole plan: the old signed copy is kept aside, and the next save stores the
                # agreement as it reads now, with every session of the plan on it
                x("""INSERT INTO doc_snapshots_old (kind, ref, at, data, moved) SELECT kind, ref, at, data, ? FROM doc_snapshots
                     WHERE kind='agreement' AND ref=?""", (today().isoformat(), t["session_id"]))
                x("DELETE FROM doc_snapshots WHERE kind='agreement' AND ref=?", (t["session_id"],))
                x("UPDATE documents SET signed=? WHERE session_id=? AND kind='agreement' AND status='signed'",
                  (today().isoformat(), t["session_id"]))
    if safe_back(request.form.get("back")):
        return redirect(safe_back(request.form.get("back")))
    if t["session_id"]:
        return redirect(url_for("session_view", sid=t["session_id"]))
    return redirect(url_for("event_view", eid=t["event_id"]))


@app.post("/event/<int:eid>/task")
def event_task_done(eid):
    x("""UPDATE tasks SET done=? WHERE done IS NULL AND title=? AND session_id IN
         (SELECT id FROM sessions WHERE event_id=?)""", (today().isoformat(), request.form["title"], eid))
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))


@app.post("/close")
def close_step():
    f = request.form
    outcome = f.get("outcome", "done")
    if outcome not in OUTCOMES:
        abort(400)
    if f.get("task"):
        x("UPDATE tasks SET done=?, outcome=? WHERE id=?", (today().isoformat(), outcome, int(f["task"])))
        if f.get("note_why"):
            x("UPDATE tasks SET outcome_note=? WHERE id=?", (f["note_why"], int(f["task"])))
    elif f.get("sid") and f.get("step") in AUTO:
        x("""INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?,?,?,?)
             ON CONFLICT(session_id, step) DO UPDATE SET outcome=excluded.outcome, note=excluded.note, at=date('now','localtime')""",
          (int(f["sid"]), f["step"], outcome, f.get("note_why", "")))
    else:
        abort(400)
    return redirect(safe_back(f.get("back")) or url_for("home"))


@app.post("/session/<int:sid>/task")
def task_add(sid):
    f = request.form
    stage = f.get("stage") or session_full(sid)["stage"]
    x("INSERT INTO tasks (session_id, title, due, sort, stage, own) VALUES (?,?,?,999,?,1)",
      (sid, f["title"], f.get("due") or None, stage))
    return redirect(url_for("session_view", sid=sid))


@app.post("/doc/<int:did>")
def doc_save(did):
    doc = q("SELECT * FROM documents WHERE id=?", (did,), one=True) or abort(404)
    f = request.form
    if f.get("delete"):
        x("DELETE FROM documents WHERE id=?", (did,))
    else:
        status = f.get("status", doc["status"])
        if f.get("cycle"):
            order = ["needed", "sent", "signed"]
            status = order[(order.index(doc["status"]) + 1) % 3]
        if f.get("signed_on") and not _past_day(f["signed_on"]):
            session["notice"] = "A signing date can't be in the future. Nothing was changed."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("session_view", sid=doc["session_id"]) + "#paperwork")
        if f.get("signed_on"):
            status = "signed"
        elif "signed_on" in f:
            status = "sent" if doc["sent"] else "needed"
        sent = doc["sent"] or (today().isoformat() if status in ("sent", "signed") else None)
        signed = f.get("signed_on") or (today().isoformat() if status == "signed" and not doc["signed"] else (
            doc["signed"] if status == "signed" else None))
        if "signed_on" in f and not f.get("signed_on"):
            signed = None
        x("UPDATE documents SET status=?, signer=?, sent=?, signed=?, note=? WHERE id=?",
          (status, f.get("signer", doc["signer"]), sent, signed, f.get("note", doc["note"]), did))
    return redirect(safe_back(f.get("back")) or url_for("session_view", sid=doc["session_id"]) + "#paperwork")


@app.post("/session/<int:sid>/doc")
def doc_add(sid):
    f = request.form
    x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?,?,?,?)",
      (sid, f["kind"], f.get("about_person", type=int), f.get("signer", "")))
    return redirect(url_for("session_view", sid=sid) + "#paperwork")


@app.post("/session/<int:sid>/money")
def money_add(sid):
    f = request.form
    what = f["what"]
    if what == "line":
        ensure_quote(sid)
        x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'custom', ?)",
          (sid, f["label"], num(f["amount"]), 1 if f.get("taxable") == "1" or not f.get("taxable_shown") else 0))
        reapply_discount(sid)
    elif what == "payment":
        kind = f.get("kind", "deposit")
        try:
            amt = num(f["amount"])
        except ValueError:
            amt = 0
        if not amt or amt <= 0:                         # a refund is its own kind; a negative amount is a typo, not a refund
            session["notice"] = "Enter the amount as a positive number. For money going back, choose Refund."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("session_view", sid=sid) + "#money")
        t_ = totals(sid) if kind == "refund" else None
        if t_ is not None and amt > t_["paid"] + 0.005:     # never more back than was paid
            session["notice"] = f"A refund can't be more than what was paid ({money(t_['paid'])}). Nothing was recorded."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("session_view", sid=sid) + "#money")
        pid_ = x("INSERT INTO payments (session_id, kind, amount, received, method, ref) VALUES (?,?,?,?,?,?)",
                 (sid, kind, -amt if kind == "refund" else amt, f.get("received") or today().isoformat(), f.get("method", ""),
                  (f.get("ref") or "").strip()[:40]))
        if kind == "refund":
            # Money back never makes the client owe more: beyond giving back an overpayment, a refund lowers the price
            # by the same amount, as its own line (not taxed, not discounted), and voiding the refund removes it.
            credit = round(amt - max(0.0, t_["paid"] - t_["quoted"]), 2)
            if credit > 0.005:
                x("INSERT INTO lines (session_id, label, amount, kind, taxable, fee_key) VALUES (?,?,?, 'credit', 0, ?)",
                  (sid, "Refund", -credit, f"refund:{pid_}"))
                g.pop("tot_cache", None)
        if kind != "refund":
            paying_means_yes(sid)
    elif what == "expense":
        add_expense(sid, f)
    return redirect(safe_back(f.get("back")) or url_for("session_view", sid=sid) + "#money")


def paying_means_yes(sid):
    """A client who pays has said yes: the open "Client said yes" step ticks itself, and a session still at Inquiry
    moves on (a payment is past first contact), then on as far as its steps allow."""
    x("""UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND title='Client said yes' AND done IS NULL""",
      (today().isoformat(), sid))
    lock_tax_rate(sid)
    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    if s_ and s_["stage"] == "inquiry":
        set_stage(sid, "quoted")
        audit("Moved to Quoted (a payment came in)", sid=sid, who="Lumen Aureum")
    advance(sid)


def add_expense(sid, f):
    miles = num(f.get("miles"), lo=0, hi=100000)
    x("INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (?,?,?,?,?,?)",
      (sid, f.get("date") or today().isoformat(), f["label"], num(f.get("amount")), miles,
       float(setting("mileage_rate") or 0) if miles else None))


@app.post("/money/overhead")
def overhead_add():
    """Business expenses that belong to no session: gear, software, insurance, education."""
    add_expense(None, request.form)
    return redirect(url_for("money_view", year=(request.form.get("date") or today().isoformat())[:4]) + "#overhead")


@app.post("/money/<what>/<int:rid>/delete")
def money_delete(what, rid):
    table = {"line": "lines", "payment": "payments", "expense": "expenses"}.get(what) or abort(400)
    r = q("SELECT * FROM " + dbmod.ident(table) + " WHERE id=?", (rid,), one=True) or abort(404)
    if table == "payments":
        # money received is never erased: a mistaken entry is voided (kept, struck through, out of the totals)
        want = request.form.get("set")          # "void" or "restore", as the button said; never a blind flip
        void_now = (want == "void") if want in ("void", "restore") else not r["voided"]
        if bool(r["voided"]) == void_now:
            g.refused = True
        else:
            x("UPDATE payments SET voided=? WHERE id=?", (today().isoformat() if void_now else None, rid))
            if r["kind"] == "refund" and void_now:          # its price line goes with it
                x("DELETE FROM lines WHERE fee_key=?", (f"refund:{rid}",))
            elif r["kind"] == "refund":                     # restored: the price line comes back as it was
                t_ = totals(r["session_id"])
                credit = round(-r["amount"] - max(0.0, t_["paid"] - r["amount"] - t_["quoted"]), 2)
                if credit > 0.005 and not q("SELECT 1 FROM lines WHERE fee_key=?", (f"refund:{rid}",), one=True):
                    x("INSERT INTO lines (session_id, label, amount, kind, taxable, fee_key) VALUES (?,?,?, 'credit', 0, ?)",
                      (r["session_id"], "Refund", -credit, f"refund:{rid}"))
            g.pop("tot_cache", None)
    else:
        x("DELETE FROM " + dbmod.ident(table) + " WHERE id=?", (rid,))
    if table == "lines":
        reapply_discount(r["session_id"])
    if not r["session_id"]:
        return redirect(safe_back(request.form.get("back")) or url_for("money_view") + "#overhead")
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=r["session_id"]) + "#money")


@app.post("/session/<int:sid>/quote/fee")
def quote_fee(sid):
    """Add a standard fee from the quote's own fee schedule (the current one if the quote is new)."""
    ensure_quote(sid)
    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#money"
    try:
        qty = num(request.form.get("qty"), 1.0, lo=0, hi=100000) or 1.0
    except ValueError:
        abort(400)
    if request.form.get("fee_key") == "custom":           # a line not on the price list: what it is and the price each
        label = (request.form.get("label") or "").strip()[:200]
        try:
            each = num(request.form.get("amount"))
        except ValueError:
            each = None
        if not label or each is None:
            session["notice"] = "For a line not on the price list, say what it is and its price."
            g.refused = True
            return redirect(back)
        x("INSERT INTO lines (session_id, label, amount, kind, qty, unit, taxable) VALUES (?,?,?, 'custom', ?,?,?)",
          (sid, label if qty == 1 else f"{label} x {qty:g}", round(each * qty, 2), qty, each,
           1 if request.form.get("taxable") == "1" or not request.form.get("taxable_shown") else 0))
        reapply_discount(sid)
        return redirect(back)
    sch = schedule(q("SELECT schedule_id FROM quotes WHERE session_id=?", (sid,), one=True)["schedule_id"])
    item = next((i for i in sch["items"] if i["key"] == request.form.get("fee_key")), None) or abort(400)
    qty = max(qty, float(item.get("min") or 0))
    unit, amt = fee_amount(item, qty)
    label = item["label"] if qty == 1 else f"{item['label']} x {qty:g}"
    x("INSERT INTO lines (session_id, label, amount, kind, fee_key, qty, unit, taxable) VALUES (?,?,?,?,?,?,?,?)",
      (sid, label, amt, "passthrough" if item.get("passthrough") else "fee", item["key"], qty, unit, fee_taxable(item)))
    reapply_discount(sid)
    return redirect(back)


@app.post("/line/<int:lid>/qty")
def line_qty(lid):
    """Change how many of a quote line, as on any invoice: the price each stays, the line total follows."""
    l_ = q("SELECT * FROM lines WHERE id=?", (lid,), one=True) or abort(404)
    s_ = q("SELECT stage FROM sessions WHERE id=?", (l_["session_id"],), one=True)
    back = safe_back(request.form.get("back")) or stage_url(l_["session_id"]) + "#billing"
    if s_["stage"] not in ("inquiry", "quoted") or l_["kind"] in ("discount", "tax") or not l_["unit"]:
        return redirect(back)
    try:
        qty = num(request.form.get("qty"), 1.0, lo=0.5, hi=100000)
    except ValueError:
        return redirect(back)
    base = re.sub(r" x [0-9.]+$", "", l_["label"])
    x("UPDATE lines SET qty=?, amount=?, label=? WHERE id=?",
      (qty, round(l_["unit"] * qty, 2), base if qty == 1 else f"{base} x {qty:g}", lid))
    reapply_discount(l_["session_id"])
    return redirect(back)


def fee_taxable(item):
    # fees are taxable unless marked otherwise (many places tax the whole price, travel included)
    return 1 if item.get("taxable", True) else 0


@app.post("/session/<int:sid>/quote/exempt")
def quote_exempt(sid):
    area = request.form.get("tax_area")
    if area == "auto" or (tax_region() and area in [k_ for k_, _ in tax_region()["areas"]]):
        x("UPDATE sessions SET tax_area=? WHERE id=?", (None if area == "auto" else area, sid))
    else:
        x("UPDATE sessions SET tax_exempt=? WHERE id=?", (1 if request.form.get("tax_exempt") else 0, sid))
    reapply_tax(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#money")


@app.post("/session/<int:sid>/quote/discount")
def quote_discount(sid):
    ensure_quote(sid)
    try:
        pct = num(request.form.get("pct"), lo=0, hi=100)
    except ValueError:
        abort(400)
    x("UPDATE quotes SET discount_pct=?, discount_reason=? WHERE session_id=?",
      (pct, request.form.get("reason", ""), sid))
    reapply_discount(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#money")


@app.post("/session/<int:sid>/quote/requote")
def quote_requote(sid):
    """An expired quote is re-priced at TODAY's fee schedule with a fresh term; custom lines keep their amounts."""
    sch = current_schedule()
    by_key = {i["key"]: i for i in sch["items"]}
    for ln in q("SELECT * FROM lines WHERE session_id=? AND kind IN ('fee','passthrough') AND fee_key != ''", (sid,)):
        item = by_key.get(ln["fee_key"])
        if item:
            unit, amt = fee_amount(item, ln["qty"] or 1)
            x("UPDATE lines SET unit=?, amount=?, taxable=? WHERE id=?", (unit, amt, fee_taxable(item), ln["id"]))
    x("UPDATE quotes SET schedule_id=?, created=?, expires=? WHERE session_id=?",
      (sch["id"], now_local(), (today() + timedelta(days=quote_term())).isoformat(), sid))
    reapply_discount(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#money")


def html_pdf(html, name):
    """HTML document -> PDF with WeasyPrint (BSD): the same templates, no bundled browser."""
    from flask import Response
    import weasyprint
    page = weasyprint.CSS(string="@page { size: Letter; margin: 0.5in; }")
    pdf = weasyprint.HTML(string=html, base_url=str(ROOT)).write_pdf(stylesheets=[page])
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


def file_slug(*parts):
    """A plain file name from words and dates: quote-joe-smith-2026-11-12."""
    out = "-".join(filter(None, (re.sub(r"[^a-z0-9]+", "-", str(p_ or "").lower()).strip("-") for p_ in parts)))
    return out[:90] or "document"


def client_slug(sid):
    r_ = q("SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True)
    return r_["name"] if r_ else ""


def long_date(iso):
    return d(iso[:10]).strftime("%B %-d, %Y") if iso else ""


def subjects_of(sid):
    return [r["name"] for r in q("""SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id
                                   WHERE sp.session_id=? ORDER BY p.id""", (sid,))]


def clock12(hm):
    try:
        h_, m_ = (int(v) for v in hm.split(":")[:2])
    except (AttributeError, ValueError):
        return ""
    return f"{h_ % 12 or 12}:{m_:02d} {'AM' if h_ < 12 else 'PM'}"


def oxford(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else (" and ".join(xs) if len(xs) == 2 else (", ".join(xs[:-1]) + ", and " + xs[-1]) if xs else "")


@app.post("/session/<int:sid>/quote/send")
def quote_send(sid):
    """Freeze exactly what the client is being sent. The live quote can change later; this version never does.
    The quote's clock starts now, not when the first line was added."""
    s = session_full(sid)
    ensure_quote(sid)
    lines = [dict(label=l["label"], amount=l["amount"], kind=l["kind"])
             for l in q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sid,))]
    if not lines:
        abort(400)
    t = totals(sid)
    shown = request.form.get("total")
    if shown and abs(num(shown, 0) - t["quoted"]) > 0.005:   # the quote changed after the page showed it: look first
        session["notice"] = f"The quote changed a moment ago (it is now {money(t['quoted'])}). Check it, then send it."
        g.refused = True
        return redirect(stage_url(sid) + "#billing")
    sent = now_local()
    expires = (today() + timedelta(days=quote_term())).isoformat()
    qt = q("SELECT * FROM quotes WHERE session_id=?", (sid,), one=True)
    data = quote_data(sid, s, lines, t)
    vid = x("INSERT INTO quote_versions (session_id, at, schedule_id, expires, data) VALUES (?,?,?,?,?)",
            (sid, sent, qt["schedule_id"], expires, json.dumps(data)))
    x("UPDATE quotes SET sent=?, expires=? WHERE session_id=?", (sent, expires, sid))
    g.audit_note = f"quote {vid}, {money(t['quoted'])}"   # one History line (the route's); sent only on Mark sent
    return redirect(url_for("quote_doc", vid=vid))


def quote_data(sid, s, lines, t):
    """What a quote says, frozen when it is sent (and shown, unfrozen, by the preview)."""
    return dict(lines=lines, total=t["quoted"], deposit=t["deposit_req"], client=s["household"], type_label=C.doc_label(s["type"]),
                people=oxford(subjects_of(sid)) if s["type"] in C.SUBJECT_TYPES else "", type=s["type"], shoot_date=s["shoot_date"],
                photographer=setting("photographer_name"), business=setting("business_name"),
                terms=[list(x_) for x_ in C.build_terms(s["type"])], editing=[[h_, list(p_)] for h_, p_ in C.build_editing(s["type"])],
                client_company=company_of(s["household_id"]),
                net_days=C.BALANCE_NET_DAYS, balance_rule=balance_rule(s["type"]), license=s["type"] in C.LICENSE_TYPES)


@app.get("/session/<int:sid>/quote/preview")
def quote_preview(sid):
    """The quote exactly as it would go out, before anything is frozen or numbered. Looking never writes."""
    s = session_full(sid)
    lines = [dict(label=l["label"], amount=l["amount"], kind=l["kind"])
             for l in q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sid,))]
    if not lines:
        return redirect(stage_url(sid) + "#billing")
    t = totals(sid)
    qt = q("SELECT * FROM quotes WHERE session_id=?", (sid,), one=True)
    v = quote_data(sid, s, lines, t)
    v.setdefault("license", v["type"] in C.LICENSE_TYPES)
    v.update(id=None, sid=sid, schedule_id=qt["schedule_id"] if qt else None, sent_long=long_date(today().isoformat()),
             expires_long=long_date((today() + timedelta(days=quote_term())).isoformat()),
             type_label=C.doc_label(v["type"]), shoot_date_long=long_date(v.get("shoot_date")),
             back=stage_url(sid), reviewed=doc_reviewed("terms", v["type"]), review=review_url("terms", v["type"]))
    return render_template("quote_doc.html", v=v, preview=True, send=None, total_shown="%.2f" % t["quoted"])


SEND_TASKS = {"quote": "Send quote", "details": "Send session details", "invoice": "Send invoice"}


def send_ctx(sid, kind, subject, lines):
    """The 'send' row on a document page: an email draft in the photographer's own mail app (to the client, the PDF
    attached by hand: the app sends no mail itself) and Mark sent, which closes the matching step."""
    from urllib.parse import quote as _qu
    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
    who = q("""SELECT name, email FROM people WHERE household_id=? AND role IN ('parent','adult') AND COALESCE(email,'') != ''
               ORDER BY role='parent' DESC, id LIMIT 1""", (s_["household_id"],), one=True) if s_ else None
    ppl = q("SELECT * FROM people WHERE household_id=? ORDER BY id", (s_["household_id"],)) if s_ else []
    reach = household_contact(ppl)
    phone_ = re.sub(r"[^0-9+]", "", (reach["phone"] or "") if reach else "")
    first = (who["name"].split() or [""])[0] if who and who["name"] else ""
    sign = setting("photographer_name") or setting("business_name")
    body = "\n\n".join([f"Hi {first}," if first else "Hello,"] + lines + [f"Thank you,\n{sign}" if sign else "Thank you"])
    step_ = SEND_TASKS.get(kind, "")                     # documents with no send step (price list, agreement): no Mark sent
    task = q("SELECT id FROM tasks WHERE session_id=? AND title=? AND done IS NULL", (sid, step_), one=True) if step_ else None
    sent = q("SELECT done FROM tasks WHERE session_id=? AND title=? AND done IS NOT NULL ORDER BY done DESC", (sid, step_), one=True) if step_ else None
    sms_body = (re.sub(r"\b(is|are) attached\b", r"\1 ready", lines[0]) + " I'll send you the PDF.") if lines else subject
    return dict(mailto=f"mailto:{_qu(who['email'] if who else '', safe='@')}?subject={_qu(subject)}&body={_qu(body)}",
                to=who["email"] if who else "", sid=sid, phone=(reach["phone"] if reach else ""),
                sms=f"sms:{phone_}?&body={_qu(sms_body)}" if len(phone_) >= 7 else "", task=task["id"] if task else None, sent=nice(sent["done"]) if sent and not task else "", back=request.full_path.rstrip("?"))


# ---------------------------------------------------------------- photos: uploaded into the app, sealed on arrival

app.config.setdefault("quiet_endpoints", set()).update({"photos_status", "photos_upload"})   # one History line per file, not per piece


@app.get("/session/<int:sid>/photos/status")
def photos_status(sid):
    """Where an upload stands: how many bytes of this file are already sealed here (0 for a new one)."""
    import photostore
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    from urllib.parse import unquote
    blocked = photos_blocked(sid)
    if blocked:
        return {"ok": False, "message": blocked}, 409
    con = connect()
    try:
        from vault import keys as VK
        lab = q("""SELECT h.name, s.type, s.shoot_date FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""",
                (sid,), one=True)
        r = photostore.start(con, sid, request.args.get("kind", ""), unquote(request.headers.get("X-File-Name", "")),
                             request.args.get("size", type=int), master_pub=VK.master_pub(con),
                             label=" - ".join(str(v) for v in (lab["name"], C.SESSION_TYPES.get(lab["type"], lab["type"]), lab["shoot_date"]) if v) if lab else "")
    except photostore.UploadError as e:
        return {"ok": False, "message": str(e)}, 400
    except OSError as e:                                  # the photos folder is full or not writable
        app.logger.error("upload start failed: %s", e)
        return {"ok": False, "message": "The photos folder is full or can't be written to. Free some space (or check its "
                                        "permissions), then drop the file again."}, 507
    finally:
        con.close()
    return {"ok": True, "received": r["received"], "piece": photostore.PIECE}


def photos_blocked(sid):
    """Uploads wait while the session's photos are being sealed or deleted, and stop for good once purged."""
    import locks
    if q("SELECT 1 FROM sqlite_master WHERE name='retention'", one=True) and \
            q("SELECT 1 FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (sid,), one=True):
        return "This session's photos were deleted at the end of their keeping time; nothing more can be added."
    if int(sid) in locks.SEALING:
        return "This session's photos are being sealed or deleted right now. Try again in a few minutes."
    return ""


@app.post("/session/<int:sid>/photos/upload")
def photos_upload(sid):
    """One piece of one photo. The body is read straight from the request and encrypted before it is written: never
    request.form or request.files (they would spool a large body to a plaintext temporary file)."""
    import photostore
    from urllib.parse import unquote
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    import locks
    length = request.content_length or 0
    blocked = photos_blocked(sid)
    if blocked:
        return {"ok": False, "message": blocked}, 409
    con, row = connect(), None
    try:
        row = photostore.start(con, sid, request.args.get("kind", ""), unquote(request.headers.get("X-File-Name", "")),
                               request.args.get("size", type=int), create=False)
        lk = locks.photo(row["id"])
        if not lk.acquire(blocking=False):            # another request is writing this file: wait for it
            return {"ok": False, "received": row["received"]}, 409
        try:
            row = con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()   # re-read under the lock
            if not row:
                raise photostore.Gone("That file was deleted here; drop it again to upload it again.")
            got = photostore.append(con, row, request.args.get("offset", type=int, default=-1), request.stream, length)
        finally:
            lk.release()
        done = got == row["size"]
        if done:
            audit(f"Uploaded {photostore.KINDS[row['kind']].lower()[:-1]} {row['name']} (sealed)", sid=sid)
            if row["kind"] == "raw":                    # RAWs in the app are encrypted and mirrored: that step is done
                x("UPDATE tasks SET done=? WHERE session_id=? AND title='Back up photos' AND done IS NULL",
                  (today().isoformat(), sid))
            if not os.environ.get("CRM_TESTING"):
                threading.Thread(target=_mirror_now, name="photo-mirror", daemon=True).start()
        return {"ok": True, "received": got, "done": done}
    except photostore.Gone as e:
        return {"ok": False, "message": str(e)}, 410
    except photostore.UploadError as e:
        if str(e).startswith("resume at"):
            cur_ = con.execute("SELECT received FROM photo_files WHERE id=?", (row["id"],)).fetchone() if row else None
            return {"ok": False, "received": cur_[0] if cur_ else 0}, 409
        return {"ok": False, "message": str(e)}, 400
    except BlockingIOError:
        return {"ok": False, "received": row["received"]}, 409
    finally:
        con.close()


@app.get("/photo/<fid>")
def photo_download(fid):
    """A sealed photo, decrypted as it is sent (for re-editing). Nothing is written to disk on the way."""
    import photostore
    con = connect()
    try:
        row = con.execute("SELECT * FROM photo_files WHERE id=? AND state='done'", (fid,)).fetchone()
    finally:
        con.close()
    if not row:
        abort(404)
    gen = photostore.iter_plain(row)
    try:
        first = next(gen)                     # check the key before anything is sent
    except StopIteration:
        first = b""
    except FileNotFoundError:
        session["notice"] = (f"{row['name']} isn't in the photos folder yet. After a restore, the app copies photos back "
                             "from the backup location on its next pass (within the hour, while unlocked); try again then.")
        return redirect(stage_url(row["session_id"]))
    except Exception:
        session["notice"] = (f"{row['name']} won't open with the key this database has. If you restored a backup made "
                             "before a key rotation, run `python -m vault.cli relink-photos` with your master key (see "
                             "Operations in the docs), then try again.")
        return redirect(stage_url(row["session_id"]))
    audit(f"Downloaded {row['name']} for editing", sid=row["session_id"])
    from urllib.parse import quote as _qu
    import itertools
    return app.response_class(itertools.chain([first], gen), mimetype="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_qu(row['name'])}", "Content-Length": str(row["size"])})


@app.post("/photo/<fid>/delete")
def photo_delete(fid):
    import photostore
    con = connect()
    try:
        row = con.execute("SELECT * FROM photo_files WHERE id=?", (fid,)).fetchone() or abort(404)
        import locks
        lk = locks.photo(fid)
        if not lk.acquire(blocking=False):
            session["notice"] = f"{row['name']} is still uploading. Stop that upload first, then delete it."
            g.refused = True
            return redirect(safe_back(request.form.get("back")) or stage_url(row["session_id"]) + "#photos")
        try:
            photostore.delete(con, row)
        finally:
            lk.release()
    finally:
        con.close()
    audit(f"Deleted {row['name']}", sid=row["session_id"])
    return redirect(safe_back(request.form.get("back")) or stage_url(row["session_id"]) + "#photos")


_MIRROR_LOCK = threading.Lock()


def mirror_photos():
    """Keep the photo mirror in step (photostore.mirror_pass) on the second backup location, else the vault."""
    import photostore
    from vault import sync as vault_sync, web as vault_web_
    if not datakey.is_unlocked() or not _MIRROR_LOCK.acquire(blocking=False):
        return
    try:
        try:
            target = vault_sync.second_backend() or vault_web_.store_backends()[0]
        except Exception:
            return                                   # no backup location yet: nothing to mirror to
        con = connect()
        try:
            audit_ = setting("photo_mirror_audit") != today().isoformat()      # a full re-check once a day
            c_, f_, r_ = photostore.mirror_pass(con, target, audit=audit_)
            if audit_:
                con.execute("INSERT INTO settings (key, value) VALUES ('photo_mirror_audit',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (today().isoformat(),))
            x_err = ""
        except Exception as e:  # noqa: BLE001 - shown in Settings > Backups and retried
            c_ = f_ = r_ = 0
            x_err = f"{type(e).__name__}: {e}"[:200]
            app.logger.warning("photo mirror: %s", x_err)
        con.execute("INSERT INTO settings (key, value) VALUES ('photo_mirror_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (x_err,))
        con.commit()
        con.close()
        if f_:
            app.logger.info("photo mirror: fetched back %d photos missing here", f_)
    finally:
        _MIRROR_LOCK.release()


def _advance_loop():
    """Date-driven stage moves (a shoot date passing) happen here every few minutes, not when a page is looked at."""
    import time as _t
    _t.sleep(30)
    while True:
        try:
            if datakey.is_unlocked():
                with app.app_context():
                    advance_all(force=True)
        except Exception:
            app.logger.exception("advance loop")
        _t.sleep(300)


def _mirror_now():
    with app.app_context():
        mirror_photos()


def _mirror_loop():
    import time as _t
    _t.sleep(120)
    while True:
        try:
            with app.app_context():
                mirror_photos()
        except Exception:
            app.logger.exception("photo mirror loop")
        _t.sleep(900)


def photo_summary(sid):
    """Per kind: the finished files and the unfinished ones, for the Photos panel."""
    import photostore
    con = connect()
    try:
        rows = photostore.files(con, sid)
    finally:
        con.close()
    out = {}
    for k, label in photostore.KINDS.items():
        rs = sorted((r for r in rows if r["kind"] == k),        # the gallery's order: img2 before img10
                    key=lambda r: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", r["name"])])
        done = [r for r in rs if r["state"] == "done"]
        out[k] = dict(label=label, done=done, partial=[r for r in rs if r["state"] == "partial"],
                      sealed=[r for r in rs if r["state"] == "sealed"], bytes=sum(r["size"] for r in done))
    return out


def size_words(n):
    for unit, f in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= f:
            return f"{n / f:.1f} {unit}".replace(".0 ", " ")
    return f"{n} bytes"


app.jinja_env.filters["size"] = size_words


@app.post("/session/<int:sid>/contact-email")
def contact_email(sid):
    """Add an email for the client from a document's bar, without leaving it: it goes on the person you talk to."""
    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    em = (request.form.get("email") or "").strip()[:200]
    who = household_contact(q("SELECT * FROM people WHERE household_id=? ORDER BY id", (s_["household_id"],)))
    if not who or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
        session["notice"] = "That doesn't look like an email address." if who else "Add a grown-up to this client first."
    else:
        x("UPDATE people SET email=? WHERE id=?", (em, who["id"]))
        audit(f"Email for {who['name']} set to {em}", sid=sid, hid=s_["household_id"])
    return redirect(safe_back(request.form.get("back")) or stage_url(sid))


@app.get("/session/<int:sid>/quote")
def session_quote(sid):
    """The quote as last sent, or the session's Billing section when none has been sent yet."""
    r = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    if r:
        return redirect(url_for("quote_doc", vid=r["id"]))
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    return redirect(stage_url(sid) + "#billing")


def quote_version(vid):
    r = q("SELECT * FROM quote_versions WHERE id=?", (vid,), one=True) or abort(404)
    v = json.loads(r["data"])
    v.setdefault("license", v["type"] in C.LICENSE_TYPES)     # quotes frozen before this was recorded
    v.update(id=r["id"], sid=r["session_id"], schedule_id=r["schedule_id"], sent_long=long_date(r["at"]), at=r["at"],
             expires_long=long_date(r["expires"]), type_label=v.get("type_label") or C.SESSION_TYPES.get(v["type"], v["type"]),
             shoot_date_long=long_date(v.get("shoot_date")), back=stage_url(r["session_id"]),
             reviewed=doc_reviewed("terms", v["type"]),
             review=review_url("terms", v["type"]))
    return v


@app.get("/quote/<int:vid>")
def quote_doc(vid):
    v = quote_version(vid)
    who = v.get("business") or v.get("photographer") or "your photographer"
    return render_template("quote_doc.html", v=v, send=send_ctx(v["sid"], "quote", f"Your quote from {who}",
                           [f"Your quote is attached. It is good through {v['expires_long']}."]))


@app.get("/quote/<int:vid>.pdf")
def quote_doc_pdf(vid):
    v = quote_version(vid)
    return html_pdf(render_template("quote_doc.html", v=v, pdf=True), file_slug("quote", v["client"], v["at"][:10]) + ".pdf")


def agreement_ctx(sid):
    s = session_full(sid)
    last = q("SELECT * FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    t = totals(sid)
    ppl = q("""SELECT p.* FROM people p JOIN session_people sp ON sp.person_id=p.id WHERE sp.session_id=?""", (sid,))
    _, signers = agreement_signers(sid)
    lic_bits = []
    if (s["lic_term"] or "").strip():
        lic_bits.append(f"The license lasts {s['lic_term'].strip().rstrip('.')}.")
    if (s["lic_territory"] or "").strip():
        lic_bits.append(f"It covers use in {s['lic_territory'].strip().rstrip('.')}.")
    if s["lic_exclusive"]:
        lic_bits.append("It is exclusive: I license these photos to no one else while it lasts.")
    elif lic_bits:
        lic_bits.append("It is not exclusive.")
    scope = [f"{l_['label']}" for l_ in q("SELECT label FROM lines WHERE session_id=? AND kind NOT IN ('discount','tax','credit') ORDER BY id", (sid,))]
    def price_of(sid_):                               # the latest quote sent, else the live lines
        lq = q("SELECT * FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid_,), one=True)
        if lq:
            d_ = json.loads(lq["data"])
            return d_["total"], d_["deposit"], lq["id"]
        t_ = t if sid_ == sid else totals(sid_)
        return t_["quoted"], t_["deposit_req"], None
    plan = []
    if q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True):
        for r_ in plan_sessions(sid):                 # every session the agreement covers, each with its own price
            pt, pd, pq = price_of(r_["id"])
            plan.append(dict(label=(r_["title"] or C.SESSION_TYPES.get(r_["type"], r_["type"])), when=long_date(r_["shoot_date"]),
                             price=pt, deposit=pd, quote_id=pq))
    total, deposit, qid = price_of(sid)
    if plan:
        total, deposit, qid = round(sum(p_["price"] for p_ in plan), 2), round(sum(p_["deposit"] for p_ in plan), 2), None
    event = bool(q("SELECT 1 FROM lines WHERE session_id=? AND fee_key='event'", (sid,), one=True))
    intake_ = json.loads(s["intake"] or "{}")
    venue_ = (intake_.get("venue") or "").strip()
    return dict(sid=sid, client=company_of(s["household_id"]) or s["household"], signer=oxford(signers), signers=signers, plan=plan,
                lic_scope=(" " + " ".join(lic_bits)) if lic_bits else "", scope=scope if s["type"] in C.LICENSE_TYPES else [],
                products=s["type"] in C.PRODUCT_TYPES,
                portfolio_no=bool(s["portfolio_no"]),
                portfolio_discount=bool(q("SELECT 1 FROM lines WHERE session_id=? AND kind='portfolio'", (sid,), one=True)),
                company=company_of(s["household_id"]),
                people=oxford(p["name"] for p in ppl) if s["type"] in C.SUBJECT_TYPES else "",
                job_address=(intake_.get("address") or "").strip().splitlines()[0] if (intake_.get("address") or "").strip() else "",
                minor=any(p["is_minor"] for p in ppl),
                type_label=(lambda ev_: ev_["name"] if ev_ else C.doc_label(s["type"]))(
                    q("SELECT name FROM events WHERE id=?", (s["event_id"] or s["payer_event"],), one=True) if (s["event_id"] or s["payer_event"]) else None),
                shoot_date_long=long_date(s["shoot_date"]), start_time=clock12(s["start_time"]),
                stops=", ".join([venue_] * bool(venue_) + [l_.strip() for l_ in (s["stops"] or "").splitlines() if l_.strip() and l_.strip() != venue_]),
                total=total, deposit=deposit, quote_id=qid, net_days=C.BALANCE_NET_DAYS, balance_rule=balance_rule(s["type"]),
                gallery_days=C.gallery_days(s["type"]), delivery_days=C.EVENT_DELIVERY_DAYS if event and not C.POLICY[s["type"]].get("delivery_days") else C.delivery_days(s["type"]),
                reschedule_fee=C.RESCHEDULE_FEE, retouch_picks=C.RETOUCH_PICKS, marketing_picks=C.MARKETING_PICKS,
                license=s["type"] in C.LICENSE_TYPES, kind=C.TYPE_KIND[s["type"]], payment=C.payment_text(),
                cancel=C.cancel_text(s["type"]), late=C.late_text(s["type"]), outfits=C.POLICY[s["type"]]["outfits"], animals=s["type"] in C.ANIMAL_TYPES, stype=s["type"],
                delivery=C.delivery_text(s["type"], s["delivery_method"]), keep_raw=C.fill(s["type"], "keep_raw"),
                license_use=C.fill(s["type"], "license_use"),
                event_rights=event_rights_text(s["event_id"] or s["payer_event"], voice="organizer" if s["payer_event"] else "photographer"),
                photographer=setting("photographer_name") or "[YOUR NAME]", business=setting("business_name"),
                law=setting("governing_law") or "the place where I do business",
                reviewed=doc_reviewed("agreement", s["type"]), review=review_url("agreement", s["type"]), back=stage_url(sid),
                unsigned=bool(q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status!='signed'", (sid,), one=True)))


def agreement_view(sid):
    """The agreement context, with the owner's own wording rendered when they have written one."""
    import wording
    a = agreement_ctx(sid)
    a["custom"] = wording.render(own_wording("agreement", q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)["type"]),
                                 agreement_fields(a))   # one source: the type's own wording, the general one, or the standard
    return a


def agreement_fields(a):
    """The [FIELDS] of the agreement, worded the way the standard agreement words them."""
    if a["kind"] == "property":
        provide = "I photograph what we agreed on, at the place and time we agreed on."
    else:
        provide = ("I photograph your animals, and you with them if you like, in the style you have asked for, at the places"
                   " and times we have agreed on." if a["animals"] else
                   "I photograph you in the style you have asked for, at the places and times we have agreed on.")
        if a["outfits"]:
            provide += (f" The look of the session is yours to plan: {C.wear_text(a.get('stype'))}, and any props."
                        " You are welcome to change outfits as often as you like within your session time.")
        provide += (f" Every image I deliver is edited as described on my price list, with light retouching on up to"
                    f" {a['retouch_picks']} photos you pick.")
    if a["event_rights"]:
        photos = a["event_rights"]
    elif a["license"] and a["kind"] == "property":
        photos = (f"I keep the copyright. Your license covers {a['license_use']}. Anyone else who wants to use the photos"
                  " needs their own license from me.") + a["lic_scope"]
    elif a["license"] and C.TYPE_KIND.get(a.get("stype")) != "property" and not a["lic_scope"] \
            and a["license_use"] == C.DEFAULT_POLICY["license_use"]:     # the default personal license, spelled out
        photos = ("I keep the copyright. Once the balance is paid, you, and the people photographed, receive a permanent,"
                  " non-exclusive license to the photos I deliver for personal use: to print them anywhere, share them, post"
                  " them on social media and personal websites, and crop or apply filters for those uses. The license cannot be"
                  " taken back. It does not cover selling the photos, using them to advertise a business, entering them in"
                  " contests, or licensing them to others; those need my written permission. Your license letter is also a"
                  " print release, for any print lab.")
    elif a["license"]:
        photos = ((f"I keep the copyright. You receive a license; it covers {a['license_use']}." if a["lic_scope"] else
                   f"I keep the copyright. Once the balance is paid, you, and the people photographed, receive a permanent,"
                   f" non-exclusive license to the photos I deliver for {a['license_use']}. The license cannot be taken back.")
                  + " Anything else, such as selling the photos or licensing them to others, needs my written permission."
                  + a["lic_scope"])
    else:
        photos = ("When the balance is paid in full, I transfer to you all copyright in the photos I deliver. From then on"
                  " they are yours to use any way you like, with no permission needed from me.")
    if a["kind"] == "property":
        use = "I may show these photos in my portfolio and on my website. I never publish an owner's or client's private details with them."
    else:
        names = " I never use your names" + (" or your child's name, school or location" if a["minor"] else "") + " with the photos."
        if a.get("portfolio_no"):              # the client declined portfolio use
            use = ("I will not show any photo from your session in my portfolio, on my website, on social media, or anywhere"
                   " else." + names)
        else:
            use = (C.portfolio_choice(a["marketing_picks"])
                   + (" Your price includes my portfolio permission discount. If, before the photos are delivered, you"
                      " withdraw permission, or say no to so many photos that I cannot show any, the discount is added back"
                      " to your balance. After delivery, the discount stays, whatever you later ask me to take down."
                      if a.get("portfolio_discount") else "")
                   + ("" if a["license"] else " This permission continues after the copyright transfers to you.")
                   + (" When a child in these photos turns 18, they may ask me to take down any photo of them, and I will."
                      if a["minor"] else "")
                   + " I show no other photo from your session anywhere." + names)
    me = a["photographer"] + (f", working through {a['business']}" if a["business"] else "")
    deliver = a["delivery"] + (f" (the gallery stays up {a['gallery_days']} days after you first open it)" if a["kind"] != "property" and "gallery" in a["delivery"] else "")
    return {"CLIENT": a["company"] or a["signer"] or a["client"], "PHOTOGRAPHER": me, "SESSION": a["type_label"], "DATE": a["shoot_date_long"] or "",
            "PRICE": money(a["total"]), "DEPOSIT": money(a["deposit"]), "BALANCE DUE": a["balance_rule"],
            "PAYMENT": a["payment"], "CANCELLATION": a["cancel"] + (" " + a["late"] if a["late"] else ""),
            "IF I CANNOT": cannot_text(a.get("stype")),
            "WHAT I PROVIDE": provide, "HOW YOU DELIVER": deliver, "DELIVERY DAYS": str(a["delivery_days"]),
            "HOW LONG": a["keep_raw"], "THE PHOTOS": photos, "HOW I MAY USE THEM": use,
            "MINORS": "A parent or guardian signs this agreement and is present for the whole session." if a["minor"] else "",
            "PRODUCTS": ("I handle the products you lend me with care and return them as we agreed. While they are in my care,"
                         " my responsibility for loss or damage is limited to the price of this session.") if a["products"] else "",
            "LAW": a["law"], "RECORDS YEARS": str(C.CLOSED_CLIENT_YEARS)}


def cannot_text(stype):
    """Section 4: what the photographer offers if they can't do the session. A date that can't move (no free
    reschedule: weddings, events) lists every realistic option; the photographer keeps what they can really do."""
    if stype and not C.POLICY[stype]["free_reschedule"]:
        return ("I will send a qualified photographer in my place. If I cannot find one, I refund everything you have paid;"
                " or, if you prefer and your date can move, we pick a new date.")
    return "I will offer a new date first. If no new date works for you, I refund everything you have paid."


def company_of(hid):
    r = q("SELECT company FROM households WHERE id=?", (hid,), one=True)
    return (r["company"] or "").strip() if r else ""


NUMBERS_JSON = DB_PATH.parent / "doc-numbers.json"   # plain counters, outside any key: survive restores and rotations


_PLAIN_LOCK = threading.Lock()


def _write_plain(path, text):
    with _PLAIN_LOCK:
        _write_plain_locked(path, text)


def _write_plain_locked(path, text):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f_:
        f_.write(text)
        f_.flush()
        os.fsync(f_.fileno())
    os.replace(tmp, path)


def _number_marks():
    marks = {}
    try:
        raw_ = json.loads(NUMBERS_JSON.read_text())
        marks = {k_: int(v_) for k_, v_ in raw_.items()
                 if isinstance(k_, str) and re.fullmatch(r"(INV|R)-\d{4}-", k_) and str(v_).isdigit() and 0 <= int(v_) < 100000}
    except (OSError, ValueError, AttributeError):
        pass
    try:                                     # an older sealed copy still counts
        for k_, v_ in json.loads(datakey.open_bytes(NUMBERS.read_bytes(), "numbers", label="numbers")).items():
            marks[k_] = max(int(v_), int(marks.get(k_, 0)))
    except Exception:
        pass
    return marks


def _seal_missing_photo_keys():
    """Photos uploaded before each photo's key was also sealed beside it (to the master key) get that sealed key now,
    so the master key alone can always recover them, even after a restore or a key rotation."""
    import photostore
    from vault import keys as VK
    pub = VK.master_pub(db()) if hasattr(VK, "master_pub") else None
    if not pub:
        return
    con = connect()
    try:
        n = 0
        for r_ in con.execute("SELECT * FROM photo_files WHERE state='done'").fetchall():
            if not photostore.key_path(r_["id"]).exists() and photostore.path_of(r_["id"]).exists():
                photostore.write_key(r_, pub)
                n += 1
        if n:
            app.logger.info("sealed the keys of %d older photos beside them", n)
    finally:
        con.close()


def _fold_legacy_side_files():
    """Deletions and number marks from before 1.0 lived in files sealed under the data key; a key rotation followed by
    restoring an older keyring would make them unreadable. Fold them into the plain files once, while they open."""
    try:
        items = _deletions()                   # reads deletions.json and, while it opens, the sealed deletions.enc
        if items:
            _write_plain(DELETIONS_JSON, json.dumps(items))
    except Exception:
        app.logger.exception("folding the older deletions file")
    try:
        marks = _number_marks()
        if marks:
            _save_number_marks(marks)
    except Exception:
        app.logger.exception("folding the older number marks")


def _seed_number_marks():
    """At unlock: the marks never fall below what this database has issued (so numbers issued before marks existed,
    or before an upgrade, are never handed out again after restoring an older backup)."""
    marks = _number_marks()
    changed = False
    for kind_, num_ in q("SELECT kind, number FROM doc_numbers"):
        pre, _, n_ = (num_ or "").rpartition("-")
        if pre and n_.isdigit() and int(n_) > int(marks.get(pre + "-", 0)):
            marks[pre + "-"] = int(n_)
            changed = True
    if changed:
        _save_number_marks(marks)


def _save_number_marks(marks):
    """The highest invoice and receipt number issued in each run, kept beside the database rather than in it, so a
    restored older database carries on after the last number really issued (a gap is fine; a repeat is not)."""
    try:
        _write_plain(NUMBERS_JSON, json.dumps(marks))
    except Exception:
        app.logger.exception("saving the document number marks")


def doc_number(sid, kind):
    """Invoice (INV) or receipt (R) number, given once when first issued and kept: the year it was issued, then the next
    number in that year's run (INV-2026-0001, INV-2026-0002 ...)."""
    r = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind), one=True)
    if r:
        return r["number"]
    yr = today().isoformat()[:4]
    pre = f"{kind}-{yr}-"
    for _ in range(5):
        n = q("SELECT COALESCE(MAX(CAST(substr(number, ?) AS INTEGER)), 0) + 1 AS n FROM doc_numbers WHERE kind=? AND number LIKE ?",
              (len(pre) + 1, kind, pre + "%"), one=True)["n"]
        marks = _number_marks()
        n = max(n, int(marks.get(pre, 0)) + 1)     # after a restore from an older backup, never reuse a number already sent
        marks[pre] = n
        _save_number_marks(marks)
        try:
            x("INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (?,?,?,?)", (sid, kind, f"{pre}{n:04d}", today().isoformat()))
        except sqlite3.IntegrityError:
            r = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind), one=True)
            if r:
                return r["number"]
            continue                                    # someone else took that number a moment ago: take the next
        return f"{pre}{n:04d}"
    abort(503)


def bill_ctx(sid, receipt=False):
    """Invoice (what is owed) or receipt (paid in full) for one session, built from the live record."""
    s = session_full(sid)
    t = totals(sid)
    lines = [dict(label=l["label"], amount=l["amount"]) for l in
             q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sid,))]
    pays = [dict(date=nice(p_["received"]), kind=p_["kind"], method=p_["method"], ref=p_["ref"] or "", amount=p_["amount"]) for p_ in
            q("SELECT * FROM payments WHERE session_id=? AND voided IS NULL ORDER BY received", (sid,))]
    payer = q("""SELECT name FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY role='parent' DESC, id
                 LIMIT 1""", (s["household_id"],), one=True)
    last = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    inv_ = q("SELECT issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    bd = balance_due_date(s["shoot_date"], s["type"], issued=(inv_["issued"] if inv_ else (today().isoformat() if not receipt else None)))
    net = s["type"] in C.NET_TERMS_TYPES
    lic = s["type"] in C.LICENSE_TYPES
    when_paid = max((p_["received"] for p_ in q("SELECT received FROM payments WHERE session_id=? AND voided IS NULL", (sid,))), default=None)
    return dict(receipt=receipt, number=doc_number(sid, "R" if receipt else "INV"),
                date_long=long_date(when_paid if receipt and when_paid else today().isoformat()),
                client=s["household"], client_address=s["address"] or "",
                bill_to=(company_of(s["household_id"]) or (s["household"] if s["payer_event"] else None)
                         or (payer["name"] if payer else s["household"])),
                attn=(payer["name"] if payer and (company_of(s["household_id"]) or (s["payer_event"] and payer["name"] != s["household"])) else ""),
                job_address=(json.loads(s["intake"] or "{}").get("address") or "").strip(),
                type_label=s["event_name"] or (s["title"] if s["payer_event"] and s["title"] else C.doc_label(s["type"])),
                shoot_date_long=long_date(s["shoot_date"]),
                lines=lines, total=t["quoted"], payments=pays, paid=t["paid"], balance=t["due"],
                due_rule=(long_date(bd) if (net or s["type"] in C.BEFORE_TYPES) and bd else "On delivery"),
                due_sentence=(f"Due by {long_date(bd)}." if (net or s["type"] in C.BEFORE_TYPES) and bd else
                              "Due on delivery: your photos are released on payment."),
                license=lic, event_family=bool(s["event_id"]), checks="check" in C.PAYMENT_METHODS,
                pay_words=(lambda w: w[:1].upper() + w[1:])(C._or_list(C.payment_methods()) or "ask me how to pay"),
                pay_details=" ".join((C.pay_details() or "").split()),
                photographer=setting("photographer_name"), business=setting("business_name"), address=setting("business_address"),
                tax_id=setting("business_tax_id"), bank=setting("bank_details"), po=(s["po_number"] or "").strip(),
                quote_id=last["id"] if last else None, back=stage_url(sid),
                replaces=(lambda r_: r_["number"] if r_ else "")(q(
                    "SELECT number FROM doc_numbers WHERE session_id=? AND kind LIKE 'INV-replaced:%' ORDER BY issued DESC, number DESC",
                    (sid,), one=True)) if not receipt else "",
                reviewed=doc_reviewed("terms", s["type"]), review=review_url("terms", s["type"]),
                pdf_url=url_for("receipt_pdf" if receipt else "invoice_pdf", sid=sid))


def statement_ctx(hid, month):
    """Statement of account for one client and month: the balance at the start of the month, invoices issued
    that month, payments received that month, and the balance at the end. What a business's bookkeeper reconciles."""
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    try:
        y_, m_ = (int(v_) for v_ in month.split("-"))
        first = date(y_, m_, 1)
    except ValueError:
        abort(400)
    nxt = (first + timedelta(days=32)).replace(day=1)
    a_, b_ = first.isoformat(), nxt.isoformat()
    def label(r_):
        where_ = ((json.loads(r_["intake"] or "{}").get("address") or "").strip().splitlines() or [""])[0]
        return ((r_["title"] or C.SESSION_TYPES.get(r_["type"], r_["type"])) + (f", {nice(r_['shoot_date'])}" if r_["shoot_date"] else "")
                + (f", {where_}" if where_ else ""))
    invs = [dict(r_, amount=totals(r_["sid"])["quoted"]) for r_ in q(
        """SELECT d.number, d.issued, s.id AS sid, s.title, s.type, s.shoot_date, s.intake FROM doc_numbers d JOIN sessions s ON s.id=d.session_id
           WHERE d.kind='INV' AND s.household_id=? AND s.stage != 'lost' ORDER BY d.issued, d.number""", (hid,))]
    pays = q("""SELECT p.amount, p.received, p.kind, p.method, s.id AS sid, s.title, s.type, s.shoot_date, s.intake FROM payments p
                JOIN sessions s ON s.id=p.session_id WHERE s.household_id=? AND p.voided IS NULL ORDER BY p.received, p.id""", (hid,))
    opening = round(sum(i_["amount"] for i_ in invs if (i_["issued"] or "") < a_) - sum(p_["amount"] for p_ in pays if (p_["received"] or "") < a_), 2)
    inv_rows = [dict(date=nice(i_["issued"]), number=i_["number"], what=label(i_), amount=i_["amount"],
                     due_on=nice(balance_due_date(i_["shoot_date"], i_["type"], issued=i_["issued"])) or "")
                for i_ in invs if a_ <= (i_["issued"] or "") < b_]
    pay_rows = [dict(date=nice(p_["received"]), what=label(p_), kind=("refund" if p_["amount"] < 0 else p_["kind"]),
                     method=p_["method"], amount=p_["amount"]) for p_ in pays if a_ <= (p_["received"] or "") < b_]
    closing = round(opening + sum(r_["amount"] for r_ in inv_rows) - sum(r_["amount"] for r_ in pay_rows), 2)
    invoiced = {i_["sid"] for i_ in invs}
    not_invoiced = [dict(what=label(r_), total=totals(r_["id"])["quoted"]) for r_ in q(
        """SELECT * FROM sessions WHERE household_id=? AND stage NOT IN ('lost','closed') ORDER BY COALESCE(shoot_date,'9999')""", (hid,))
        if r_["id"] not in invoiced and totals(r_["id"])["quoted"] > 0.005]
    payer = q("""SELECT name FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY role='parent' DESC, id
                 LIMIT 1""", (hid,), one=True)
    company = (h["company"] or "").strip()
    return dict(hid=hid, month=month, title=first.strftime("%B %Y"), opening=opening, invoices=inv_rows, payments=pay_rows,
                closing=closing, not_invoiced=not_invoiced, start_long=long_date(a_), end_long=long_date((nxt - timedelta(days=1)).isoformat()),
                bill_to=company or (payer["name"] if payer else h["name"]), attn=payer["name"] if payer and company else "",
                client_address=h["address"] or "", photographer=setting("photographer_name"), business=setting("business_name"),
                address=setting("business_address"), pay_words=(lambda w: w[:1].upper() + w[1:])(C._or_list(C.payment_methods()) or "ask me how to pay"),
                pay_details=" ".join((C.pay_details() or "").split()),
                tax_id=setting("business_tax_id"), bank_details=setting("bank_details"),
                prev=(first - timedelta(days=1)).strftime("%Y-%m"), next=nxt.strftime("%Y-%m"),
                back=url_for("client_view", hid=hid), pdf_url=url_for("statement_pdf", hid=hid, m=month))


@app.get("/client/<int:hid>/statement")
def statement(hid):
    return render_template("statement.html", v=statement_ctx(hid, request.args.get("m") or today().strftime("%Y-%m")))


@app.get("/client/<int:hid>/statement.pdf")
def statement_pdf(hid):
    v = statement_ctx(hid, request.args.get("m") or today().strftime("%Y-%m"))
    return html_pdf(render_template("statement.html", v=v, pdf=True), file_slug("statement", v["bill_to"], v["month"]) + ".pdf")


@app.get("/session/<int:sid>/invoice")
def invoice(sid):
    v = bill_ctx(sid)
    return render_template("invoice.html", v=v, send=send_ctx(sid, "invoice", f"Invoice {v['number']}",
                           [f"Invoice {v['number']} is attached. {v['due_sentence']}"]))


@app.get("/session/<int:sid>/invoice.pdf")
def invoice_pdf(sid):
    v = bill_ctx(sid)
    return html_pdf(render_template("invoice.html", v=v, pdf=True), file_slug(v["number"], v["client"]) + ".pdf")


@app.get("/session/<int:sid>/receipt")
def receipt(sid):
    if totals(sid)["due"] > 0.005:                     # a receipt is for a session paid in full; until then, the invoice
        return redirect(url_for("invoice", sid=sid))
    v = bill_ctx(sid, receipt=True)
    return render_template("invoice.html", v=v, send=send_ctx(sid, "receipt", f"Receipt {v['number']}",
                           [f"Thank you. Your receipt, {v['number']}, is attached: the session is paid in full."]))


@app.get("/session/<int:sid>/receipt.pdf")
def receipt_pdf(sid):
    if totals(sid)["due"] > 0.005:
        return redirect(url_for("invoice_pdf", sid=sid))
    v = bill_ctx(sid, receipt=True)
    return html_pdf(render_template("invoice.html", v=v, pdf=True), file_slug(v["number"], v["client"]) + ".pdf")


def household_contact(people):
    """Who to reach for a client: the first grown-up with a phone or email; else the first grown-up. Adding a partner
    or a second parent without contact details never takes the place of the person you have been talking to."""
    adults = [p for p in people if p["role"] in ("parent", "adult")]
    return next((p for p in adults if (p["phone"] or "").strip() or (p["email"] or "").strip()), adults[0] if adults else None)


def details_ctx(sid):
    s = session_full(sid)
    stops = None
    for fn in hooks.detail_locations:            # a plugin may describe the locations (times, town, what is there)
        try:
            stops = fn(s)
        except Exception:
            app.logger.exception("detail locations")
        if stops:
            break
    if not stops:
        stops = [dict(time=clock12(s["start_time"]) if i == 0 else "", name=n, town="", what="", light="")
                 for i, n in enumerate(l_.strip() for l_ in (s["stops"] or "").splitlines() if l_.strip())]
    mins = session_minutes(sid, s["type"])
    meet = ((f"{clock12(s['start_time'])}" if s["start_time"] else "Time to be confirmed")
            + (f" at {stops[0]['name']}" if stops else ", place to be confirmed"))
    return dict(sid=sid, client=s["household"], people=oxford(subjects_of(sid)), type_label=C.doc_label(s["type"]),
                date_long=long_date(s["shoot_date"]), meet=meet.strip(), stops=stops,
                length=(f"{mins // 60} hr {mins % 60} min" if mins % 60 and mins >= 60 else f"{mins // 60} hr" if mins >= 60 else f"{mins} min").replace(" 0 min", ""),
                photographer=setting("photographer_name"), business=setting("business_name"), back=stage_url(sid),
                outfits=C.POLICY[s["type"]]["outfits"], wear=C.wear_text(s["type"]), weather=C.fill(s["type"], "weather"),
                animals=s["type"] in C.ANIMAL_TYPES,
                reviewed=doc_reviewed("terms", s["type"]), review=review_url("terms", s["type"]))


@app.get("/session/<int:sid>/details")
def session_details(sid):
    v = details_ctx(sid)
    return render_template("session_details.html", v=v, send=send_ctx(sid, "details", "Your session details",
                           [f"The details for your session{(' on ' + v['date_long']) if v.get('date_long') else ''} are attached."]))


@app.get("/session/<int:sid>/details.pdf")
def session_details_pdf(sid):
    return html_pdf(render_template("session_details.html", v=details_ctx(sid), pdf=True), file_slug("session-details", client_slug(sid), today().isoformat()) + ".pdf")


def event_rights_text(eid, voice="photographer", photographer="", person=""):
    """The event's ownership terms. In the photographer's voice for the organizer's agreement and the letters (the
    organizer option there is the transfer itself, with the license and permissions back); in the signer's voice for a
    family's release, where "I" is the parent."""
    e = q("SELECT rights, rights_note FROM events WHERE id=?", (eid,), one=True) if eid else None
    if not e:
        return ""
    r_ = e["rights"] or "families"
    if r_ == "custom":
        return e["rights_note"] or ""
    if voice == "release":
        return C.EVENT_RIGHTS_RELEASE[r_].format(photographer=photographer or "The photographer", person=person or "my child")
    if voice == "organizer":
        return C.EVENT_RIGHTS_ORGANIZER.get(r_) or C.EVENT_RIGHTS.get(r_, "")
    return C.EVENT_RIGHTS.get(r_, "")


def release_ctx(did):
    dc = q("""SELECT d.*, p.name AS person, p.is_minor FROM documents d LEFT JOIN people p ON p.id=d.about_person
              WHERE d.id=?""", (did,), one=True) or abort(404)
    s = session_full(dc["session_id"])
    ev = q("SELECT name, date FROM events WHERE id=?", (s["event_id"],), one=True) if s["event_id"] else None
    return dict(person=dc["person"] or s["household"], minor=bool(dc["is_minor"]), portfolio_no=bool(s["portfolio_no"]), signer=("" if (dc["signer"] or "").strip() == "Parent (required)" else re.sub(r"^Parent:\s*", "", dc["signer"] or "")),
                event_rights=event_rights_text(s["event_id"], voice="release", person=dc["person"] or s["household"],
                                               photographer=setting("photographer_name") or setting("business_name")),
                license=s["type"] in C.LICENSE_TYPES,
                event=bool(ev), what=ev["name"] if ev else C.doc_label(s["type"]),
                date_long=long_date(ev["date"] if ev else s["shoot_date"]),
                photographer=setting("photographer_name") or "[YOUR NAME]", business=setting("business_name"),
                reviewed=doc_reviewed("release", s["type"]), review=review_url("release", s["type"]),
                did=did, unsigned=dc["status"] != "signed",
                back=stage_url(s["id"]), pdf_url=url_for("release_pdf", did=did))


def _family_money(did):
    """At a group event where each family pays, the family's release carries its price terms: the family signs nothing
    else, and a deposit taken with no written terms is only a prepayment."""
    r_ = q("""SELECT s.id, e.billing FROM documents d JOIN sessions s ON s.id=d.session_id JOIN events e ON e.id=s.event_id
              WHERE d.id=?""", (did,), one=True)
    if not r_ or r_["billing"] != "families":
        return ""
    t_ = totals(r_["id"])
    if t_["quoted"] <= 0.005:
        return ""
    dep_ = t_.get("deposit_req") or 0
    return (f"My family's price is {money(t_['quoted'])}."
            + (f" {money(dep_)} is due to reserve our photos, and the rest is due when the photos are delivered."
               if 0.005 < dep_ < t_["quoted"] - 0.005 else " It is due before the photos are delivered.")
            + " If the event is canceled, or moved to a date we cannot come, or the photographer cannot photograph it,"
              " everything we paid is refunded." + (" If we do not come, the deposit is not refunded." if 0.005 < dep_ else ""))


def release_view(did):
    """The release: the owner's own wording, else the standard text (one source; the page and the PDF both use this)."""
    v = release_ctx(did)
    v["money"] = _family_money(did)
    import wording
    own = own_wording("release", q("SELECT s.type FROM documents d JOIN sessions s ON s.id=d.session_id WHERE d.id=?",
                                   (did,), one=True)["type"])
    who = "the family" if v["minor"] else "me"
    used = f"The edited photos are delivered to {who}"
    if v["event"]:                       # a group event: a personal license, never a copyright transfer (group photos are
        used += (" for personal use: to print, share, and post them, not to sell them. Group photos that include "  # shared)
                 f"{v['person']} also go to the other families in the group")
    else:
        used += (", with the license set out in the session agreement" if v["license"] else
                 f", and the copyright in them is transferred to {who} once the session is paid in full")
    if v.get("portfolio_no"):
        used += (". No photo from the session is shown in a portfolio, on a website, on social media, or anywhere else, and names"
                 f"{', school' if v['minor'] else ''} and location are never published with the photos.")
    else:
        used += (f". The photographer may show up to {C.MARKETING_PICKS} photos from the session in a portfolio, on a website, and on the"
                 f" business's social media accounts, chosen by the photographer; {'we' if v['minor'] else 'I'} may suggest favorites."
                 f" Before a photo is first shown, {'we' if v['minor'] else 'I'} may say no to it, and it will not be shown; after that, a"
                 " written request to take a photo down is honored within 14 days."
                 f" No other photo is shown anywhere, and names{', school' if v['minor'] else ''} and location are never published with the photos.")
    v["custom"] = wording.render(own, {
        "PERSON": v["person"], "PHOTOGRAPHER": v["photographer"] or "the photographer",
        "OCCASION": "the event above" if v["event"] else "the session above", "EVENT RIGHTS": v["event_rights"] or "",
        "HOW THE PHOTOS ARE USED": used,
        "GUARDIAN": f"I am the parent or legal guardian of {v['person']} and may sign this release on their behalf." if v["minor"] else ""})
    return v


@app.get("/doc/<int:did>/release")
def release_doc(did):
    v = signed_copy("release", did) or legacy_signed("release", did, release_view(did))
    sid_ = q("SELECT session_id FROM documents WHERE id=?", (did,), one=True)["session_id"]
    who = setting("photographer_name") or setting("business_name") or "your photographer"
    send = send_ctx(sid_, "release", f"Photo release from {who}",
                    ["The photo release is attached." + ("" if not v.get("unsigned") else " Please sign it and send it back.")])
    return render_template("release.html", v=v, send=send, signed_on=_signed_on(None, None, did))


@app.get("/doc/<int:did>/release.pdf")
def release_pdf(did):
    v = signed_copy("release", did) or legacy_signed("release", did, release_view(did))
    return html_pdf(render_template("release.html", v=v, pdf=True, signed_on=_signed_on(None, None, did)), file_slug("release", v["person"], today().isoformat()) + ".pdf")


@app.get("/session/<int:sid>/agreement")
def agreement(sid):
    head = plan_head(sid)
    if head != sid:
        return redirect(url_for("agreement", sid=head))           # a session in a plan signs the plan's one agreement
    a = signed_copy("agreement", sid) or legacy_signed("agreement", sid, agreement_view(sid))
    who = setting("photographer_name") or setting("business_name") or "your photographer"
    send = send_ctx(sid, "agreement", f"Your session agreement with {who}",
                    ["Your session agreement is attached." + ("" if not a.get("unsigned") else " Please read it, sign it and send it back.")])
    return render_template("agreement.html", a=a, send=send, signed_on=_signed_on(sid, "agreement"))


def _signed_on(sid, kind, did=None):
    """The day a document was marked signed, as a long date, for the signature block (blank while unsigned)."""
    r_ = q("SELECT MAX(signed) d FROM documents WHERE " + ("id=?" if did else "session_id=? AND kind=?") + " AND status='signed'",
           (did,) if did else (sid, kind), one=True)
    return long_date(r_["d"]) if r_ and r_["d"] else ""


def plan_head(sid):
    r_ = q("SELECT plan_of FROM sessions WHERE id=?", (sid,), one=True)
    return r_["plan_of"] if r_ and r_["plan_of"] else sid


def plan_sessions(head):
    """The sessions one agreement covers: the plan's first session and every session joined to it."""
    return q("SELECT * FROM sessions WHERE id=? OR plan_of=? ORDER BY COALESCE(shoot_date,'9999'), id", (head, head))


@app.post("/session/<int:sid>/plan")
def join_plan(sid):
    """Join this session to another session's plan (one agreement for several sessions, e.g. a baby's first year), or
    leave it. Only sessions of the same client; a plan is one level deep."""
    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    to = request.form.get("plan_of", "")
    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid)
    if not to:
        if s_["plan_of"]:
            x("UPDATE sessions SET plan_of=NULL WHERE id=?", (sid,))
            if s_["type"] in C.NEEDS_AGREEMENT and not q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (sid,), one=True):
                for p_ in default_signers(s_["household_id"], s_["type"]) or [None]:
                    x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'agreement', ?, ?)",
                      (sid, p_["id"] if p_ else None, p_["name"] if p_ else ""))
            audit("Left the plan; this session has its own agreement again", sid=sid)
        return redirect(back)
    head = q("SELECT * FROM sessions WHERE id=? AND household_id=?", (int(to) if to.isdigit() else 0, s_["household_id"]), one=True)
    if not head or head["id"] == sid or head["plan_of"] or q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True):
        session["notice"] = "Pick another session of this client that isn't itself part of a plan."
        g.refused = True
        return redirect(back)
    x("UPDATE sessions SET plan_of=? WHERE id=?", (head["id"], sid))
    x("DELETE FROM documents WHERE session_id=? AND kind='agreement' AND status != 'signed'", (sid,))
    resign = q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (head["id"],), one=True)
    if resign:                                   # the client agreed to the plan as it was, not to this added session
        x("""UPDATE documents SET status=CASE WHEN sent IS NOT NULL THEN 'sent' ELSE 'needed' END, signed=NULL
             WHERE session_id=? AND kind='agreement'""", (head["id"],))
    audit(f"Joined the plan of {head['title'] or C.SESSION_TYPES.get(head['type'], head['type'])}: one agreement covers both"
          + ("; the plan's agreement needs signing again" if resign else ""), sid=sid)
    return redirect(back)


@app.get("/session/<int:sid>/agreement.pdf")
def agreement_pdf(sid):
    return html_pdf(render_template("agreement.html", a=signed_copy("agreement", plan_head(sid))
                                    or legacy_signed("agreement", plan_head(sid), agreement_view(sid)), pdf=True,
                                    signed_on=_signed_on(plan_head(sid), "agreement")), file_slug("agreement", client_slug(sid), today().isoformat()) + ".pdf")


def fee_groups(only=None):
    """Price-list sections: one per session type (the prices tagged with it), then add-ons and travel. only: one type's
    section (the list a client of that type receives)."""
    groups = [(C.SESSION_TYPES[t], tuple(it["key"] for it in current_schedule()["items"] if it.get("type") == t))
              for t in C.SESSION_TYPES if not only or t == only]
    return [g_ for g_ in groups if g_[1]] + [("Add-ons", ("extra_hour", "extra_person", "extra_loc", "retouch", "rush")),
                                              ("Travel", ("travel",))]


def fee_sheet_ctx(pdf=False, stype=None):
    sch = current_schedule()
    stype = stype if stype in C.SESSION_TYPES else None
    rate = float(setting("mileage_rate") or 0)

    def price(it):
        if it.get("unit") == "mile":
            return f"{rate * 100:g} cents per mile" if not it["amount"] else f"{money(it['amount'])} per mile"
        tail = {"hour": " per hour", "each": " each"}.get(it.get("unit"), "")
        return money(it["amount"]) + tail

    def label(it):
        lab = it["label"].replace(", every edited image included", "")
        return lab
    groups, used = [], set()
    for title, keys in fee_groups(stype):
        rows = [(label(it), price(it)) for it in sch["items"]
                if any(it["key"] == k or (k.endswith("_") and it["key"].startswith(k)) for k in keys)
                and not (it.get("unit") == "mile" and not it["amount"] and not rate)]   # no rate yet: no "0 cents"
        used |= {it["key"] for it in sch["items"] if any(it["key"] == k or (k.endswith("_") and it["key"].startswith(k)) for k in keys)}
        if rows:
            groups.append((title, rows))
    other = [(label(it), price(it)) for it in sch["items"] if it["key"] not in used
             and not (stype and it.get("type") and it.get("type") != stype)]      # one type's list: not other types' prices
    if other:
        groups.append(("Other", other))
    return dict(sch=sch, groups=groups, as_of=today().strftime("%B %-d, %Y"), hold_days=quote_term(), pdf=pdf,
                stype=stype, type_label=C.SESSION_TYPES.get(stype, "") if stype else "",
                terms=C.build_terms(stype) if stype else C.build_terms(),
                editing=C.build_editing(stype) if stype else C.EDITING_POLICY,
                reviewed=doc_reviewed("terms", stype) if stype else doc_reviewed("terms"),
                who=setting("photographer_name"), studio=setting("studio_name"))


@app.get("/fees/sheet")
def fee_sheet():
    """Client-facing price list: current schedule, good-through date, editing definition."""
    stype, sid = request.args.get("type"), request.args.get("sid", type=int)
    ctx_ = fee_sheet_ctx(stype=stype)
    send = None
    if sid and q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True):   # opened from a session: send it to that client
        who = setting("photographer_name") or setting("business_name") or "your photographer"
        send = send_ctx(sid, "price", f"Price list from {who}", ["My price list is attached."])
    return render_template("fee_sheet.html", send=send, sid=sid, back_url=stage_url(sid) if send else url_for("settings_view", tab="fees"), **ctx_)


@app.get("/fees/sheet.pdf")
def fee_sheet_pdf():
    stype = request.args.get("type")
    return html_pdf(render_template("fee_sheet.html", **fee_sheet_ctx(pdf=True, stype=stype)),
                    f"price-list{'-' + stype if stype in C.SESSION_TYPES else ''}-{today().isoformat()}.pdf")


@app.get("/fees/<int:sched_id>")
def fees_view(sched_id):
    sch = schedule(sched_id) or abort(404)
    return render_template("fees.html", sch=sch, current=current_schedule()["id"], rate=setting("mileage_rate"))


# ---------------------------------------------------------------- routes: events

@app.get("/events")
def events():
    rows = q("""SELECT e.*, (SELECT COUNT(*) FROM sessions WHERE event_id=e.id) AS n
                FROM events e ORDER BY e.date DESC""")
    contact = q("SELECT id, name FROM households WHERE id=?", (request.args.get("contact", type=int) or 0,), one=True)
    return render_template("events.html", rows=rows, contact=contact)


@app.post("/events")
def event_add():
    f = request.form
    eid = x("INSERT INTO events (name, date, venue) VALUES (?,?,?)", (f["name"], f.get("date") or None, f.get("venue", "")))
    c_ = f.get("contact", type=int)
    if c_ and q("SELECT 1 FROM households WHERE id=?", (c_,), one=True):    # the person who asked: the organizer contact
        x("UPDATE events SET contact_hid=? WHERE id=?", (c_, eid))
    return redirect(url_for("event_view", eid=eid))


@app.get("/event/<int:eid>")
def event_view(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    fams = []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.event_id=? ORDER BY h.name""", (eid,)):
        docs = q("SELECT status FROM documents WHERE session_id=? AND kind='release'", (s["id"],))
        subj = q("""SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id
                    WHERE sp.session_id=?""", (s["id"],))
        parent = q("""SELECT * FROM people WHERE household_id=? AND role IN ('parent','adult')
                      ORDER BY role='parent' DESC, id LIMIT 1""", (s["household_id"],), one=True)
        fams.append(dict(s=s, subjects=", ".join(p["name"] for p in subj), parent=parent,
                         released=bool(docs) and all(dd["status"] == "signed" for dd in docs),
                         t=totals(s["id"]) if e["billing"] == "families" else None,
                         n_docs=len(docs), n_signed=sum(dd["status"] == "signed" for dd in docs)))
    payer = q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.payer_event=?""",
              (eid,), one=True)
    c_ = e["contact_hid"] if "contact_hid" in e.keys() else None
    hh = sorted(q("SELECT id, name FROM households"), key=lambda r_: (r_["id"] != c_, re.sub(r"^the\s+", "", r_["name"].lower())))
    contact = q("SELECT id, name FROM households WHERE id=?", (c_,), one=True) if c_ else None
    return render_template("event.html", e=e, fams=fams, steps=event_steps(eid), sign_day=event_sign_day(e),
                           payer=payer, households=hh, payer_total=totals(payer["id"]) if payer else None, contact=contact)


@app.post("/event/<int:eid>/family-price")
def event_family_price(eid):
    """Each family pays: one price for every family that has no price yet (a family's own quote is never changed)."""
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    try:
        amt = round(num(request.form.get("amount")), 2)
    except ValueError:
        amt = 0
    if amt > 0:
        x("UPDATE events SET family_price=? WHERE id=?", (amt, eid))
        e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
        n = 0
        for s in q("SELECT id FROM sessions WHERE event_id=?", (eid,)):
            if q("SELECT 1 FROM lines WHERE session_id=? AND kind!='tax'", (s["id"],), one=True):
                continue
            _price_family(s["id"], e)
            n += 1
        audit(f"Set {money(amt)} for {n} famil{'y' if n == 1 else 'ies'} at {e['name']}")
    return redirect(url_for("event_view", eid=eid) + "#families")


def event_sign_day(e):
    """Parents sign at the event: default to the event day once it has passed."""
    if e["date"] and e["date"] <= today().isoformat():
        return e["date"]
    return today().isoformat()


@app.post("/event/<int:eid>/edit")
def event_edit(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    new_date = f.get("date") or None
    x("UPDATE events SET name=?, date=?, venue=?, notes=? WHERE id=?",
      (f.get("name") or e["name"], new_date, f.get("venue", ""), f.get("notes", ""), eid))
    if new_date != e["date"]:
        for srow in q("SELECT id, shoot_date FROM sessions WHERE event_id=?", (eid,)):
            if srow["shoot_date"] in (None, e["date"]):
                x("UPDATE sessions SET shoot_date=? WHERE id=?", (new_date, srow["id"]))
                schedule_tasks(srow["id"])
        schedule_event(eid)
    return redirect(url_for("event_view", eid=eid))


@app.post("/event/<int:eid>/billing")
def event_billing(eid):
    """Who pays and who owns the photos are decided per event."""
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    if f.get("billing") in ("free", "organizer", "families"):
        x("UPDATE events SET billing=? WHERE id=?", (f["billing"], eid))
    if f.get("rights") in C.EVENT_RIGHTS and not (f.get("billing") and f.get("billing") != e["billing"] and f["rights"] == (e["rights"] or "families")):
        x("UPDATE events SET rights=? WHERE id=?", (f["rights"], eid))
    elif f.get("billing") and f["billing"] != e["billing"] and (e["rights"] or "families") != "custom":
        # who pays changed and ownership wasn't touched: it follows (organizer pays: the organizer owns, families get
        # their own photos for personal use)
        x("UPDATE events SET rights=? WHERE id=?", ("organizer" if f["billing"] == "organizer" else "families", eid))
    if "rights_note" in f:
        x("UPDATE events SET rights_note=? WHERE id=?", (f["rights_note"].strip(), eid))
    if f.get("payer_hid", "").isdigit() and q("SELECT 1 FROM households WHERE id=?", (int(f["payer_hid"]),), one=True):
        hid = int(f["payer_hid"])
        x("UPDATE events SET payer_hid=? WHERE id=?", (hid, eid))
        ps_ = q("SELECT id, household_id FROM sessions WHERE payer_event=?", (eid,), one=True)
        if ps_ and ps_["household_id"] != hid:
            # a new organizer takes over the billing session while nothing is paid or signed on it; after that it
            # stays with the one who paid or signed, and the change is noted
            done_ = q("""SELECT (SELECT COUNT(*) FROM payments WHERE session_id=? AND voided IS NULL)
                                + (SELECT COUNT(*) FROM documents WHERE session_id=? AND status='signed') n""",
                      (ps_["id"], ps_["id"]), one=True)["n"]
            if not done_:
                x("UPDATE sessions SET household_id=? WHERE id=?", (hid, ps_["id"]))
            else:
                audit("Organizer changed; the billing session stays with the previous organizer (payments or signatures recorded)",
                      sid=ps_["id"])
        if not ps_:
            sid = create_session(hid, C.EVENT_TYPE, "inquiry", e["name"], e["date"])
            x("UPDATE sessions SET payer_event=? WHERE id=?", (eid, sid))
            x("INSERT INTO documents (session_id, kind) VALUES (?, 'agreement')", (sid,))
            sync_workflow_tasks(sid)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    for r_ in q("SELECT id FROM sessions WHERE event_id=?", (eid,)):
        sync_workflow_tasks(r_["id"])            # families pay; paying is enough (no agreement per family)
    return redirect(url_for("event_view", eid=eid) + "#billing")


@app.post("/event/<int:eid>/mark")
def event_mark(eid):
    field = request.form.get("field")
    if field not in MARKABLE:
        abort(400)
    col = dbmod.ident(field)
    x("UPDATE sessions SET " + col + "=? WHERE event_id=? AND " + col + " IS NULL", (today().isoformat(), eid))
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))


@app.post("/event/<int:eid>/releases")
def event_releases(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    day = request.form.get("signed_on") or event_sign_day(e)
    if request.form.get("signed_on") and not _past_day(day):
        session["notice"] = "A signing date can't be in the future. Nothing was changed."
        g.refused = True
        return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))
    only = request.form.get("session_id")
    if only:
        x("""UPDATE documents SET status='signed', signed=?, sent=COALESCE(sent, ?)
             WHERE kind='release' AND status != 'signed' AND session_id IN (SELECT id FROM sessions WHERE event_id=? AND id=?)""",
          (day, day, eid, int(only)))
    else:
        x("""UPDATE documents SET status='signed', signed=?, sent=COALESCE(sent, ?)
             WHERE kind='release' AND status != 'signed' AND session_id IN (SELECT id FROM sessions WHERE event_id=?)""",
          (day, day, eid))
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))


@app.post("/event/<int:eid>/family")
def event_family(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    hid = x("INSERT INTO households (name, source, address) VALUES (?,?,?)", (f["household_name"], e["name"], f.get("address", "")))
    if f.get("parent_name"):
        x("INSERT INTO people (household_id, name, role, email, phone, relation) VALUES (?,?,?,?,?,?)",
          (hid, f["parent_name"], "parent", f.get("email", ""), f.get("phone", ""), "parent"))
    subjects = []
    for raw in [n.strip() for n in re.split(r",|\band\b|&", f.get("children", "")) if n.strip()]:   # "Mateo, Lucia and Diego"
        m = re.match(r"^(.*?)[\s(]+(\d{1,2})\)?$", raw)
        name, age = (m.group(1).strip(), int(m.group(2))) if m else (raw, None)
        subjects.append(x("INSERT INTO people (household_id, name, role, is_minor, birth_year) VALUES (?,?, 'child', 1, ?)",
                          (hid, name, (today().year - age) if age else None)))
    sid_ = create_session(hid, C.EVENT_TYPE, "booked", e["name"], e["date"], event_id=eid, subject_ids=subjects)
    if e["billing"] == "families" and e["family_price"] and sid_:
        _price_family(sid_, e)                              # a family added later gets the event's price too
    return redirect(url_for("event_view", eid=eid))


def _price_family(sid, e):
    ensure_quote(sid)
    x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'custom', 1)",
      (sid, f"{e['name']} - family session", e["family_price"]))
    reapply_discount(sid)


@app.post("/event/<int:eid>/stage")
def event_stage(eid):
    stage = request.form["stage"]
    if stage not in C.STAGES + C.OFF_PIPELINE:
        abort(400)
    e_ = q("SELECT date FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if stage in ("shot", "delivered", "closed") and e_["date"] and e_["date"] > today().isoformat():
        session["notice"] = f"Not yet: the event is {nice(e_['date'])}. Families move on from the day itself."
        g.refused = True
        if request.headers.get("X-Fetch"):
            return ("", 409)
        return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))
    for s in q("SELECT id FROM sessions WHERE event_id=?", (eid,)):
        set_stage(s["id"], stage)
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))


# ---------------------------------------------------------------- routes: money, settings

@app.get("/money")
def money_view():
    try:
        year = int(request.args.get("year", today().year))
    except ValueError:
        year = today().year
    year = min(max(year, 1900), 9999)
    y = str(year)
    rate = float(setting("mileage_rate") or 0)
    received = q("""SELECT type, SUM(v) v FROM (
                      SELECT s.type, p.amount v FROM payments p JOIN sessions s ON s.id=p.session_id
                      WHERE substr(p.received,1,4)=? AND p.voided IS NULL
                      UNION ALL SELECT type, amount FROM books WHERE substr(received,1,4)=?) GROUP BY type""", (y, y))
    exp = q("""SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(miles),0) m, COALESCE(SUM(miles * COALESCE(rate, ?)),0) mc
               FROM expenses WHERE substr(date,1,4)=?""", (rate, y), one=True)
    overhead = q("SELECT * FROM expenses WHERE session_id IS NULL AND substr(date,1,4)=? ORDER BY date DESC", (y,))
    refunds = q("""SELECT COALESCE(SUM(amount),0) v FROM (SELECT amount FROM payments WHERE kind='refund' AND voided IS NULL
                   AND substr(received,1,4)=? UNION ALL SELECT amount FROM books WHERE kind='refund' AND substr(received,1,4)=?)""",
                (y, y), one=True)["v"]
    lost = q("""SELECT COALESCE(NULLIF(lost_reason,''),'no reason') r, COUNT(*) n FROM sessions WHERE stage='lost'
                AND substr(stage_changed,1,4)=? GROUP BY r ORDER BY n DESC""", (y,))
    # sales tax is counted WHEN RECEIVED: each payment carries its share of the tax
    tax_lines = 0.0
    kept_ = {}                  # a kept deposit's tax is counted once, at the full rate, below (not pro rata here too)
    for p_ in q("""SELECT p.session_id, p.amount FROM payments p WHERE p.voided IS NULL AND substr(p.received,1,4)=?
                   AND p.session_id IS NOT NULL""", (y,)):
        if p_["session_id"] not in kept_:
            kept_[p_["session_id"]] = kept_deposit_tax(p_["session_id"]) > 0
        if kept_[p_["session_id"]]:
            continue
        tot_ = q("SELECT COALESCE(SUM(amount),0) v, COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0) t FROM lines WHERE session_id=?",
                 (p_["session_id"],), one=True)
        if tot_["v"] > 0 and tot_["t"]:
            tax_lines += p_["amount"] * tot_["t"] / tot_["v"]
    tax_lines += q("SELECT COALESCE(SUM(tax),0) v FROM books WHERE substr(received,1,4)=?", (y,), one=True)["v"]
    tax_lines = round(tax_lines, 2)
    tax_kept = sum(kept_deposit_tax(r_["id"]) for r_ in q("""SELECT id FROM sessions WHERE stage='lost' AND lost_reason IN ('Canceled','No-show')
                                                            AND substr(stage_changed,1,4)=?""", (y,)))
    by_month = q("""SELECT substr(received,1,7) mo, SUM(amount) v FROM (
                      SELECT received, amount FROM payments WHERE substr(received,1,4)=? AND voided IS NULL
                      UNION ALL SELECT received, amount FROM books WHERE substr(received,1,4)=?) GROUP BY mo ORDER BY mo""", (y, y))
    funnel = q("""SELECT stage, COUNT(*) n FROM sessions WHERE substr(created,1,4)=? GROUP BY stage""", (y,))
    outstanding, upcoming = [], []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage NOT IN ('lost') ORDER BY s.shoot_date"""):
        tt = totals(s["id"])
        if owed_now(s["stage"], tt, s["shoot_date"], row_type(s)):
            bd = balance_due_date(s["shoot_date"], s["type"], sid=s["id"])
            outstanding.append(dict(s=s, t=tt, due_on=bd, late=bool(bd and bd < today().isoformat())))
        elif s["stage"] == "booked" and tt["due"] > 0.005:
            upcoming.append(dict(s=s, t=tt))
    return render_template("money.html", net_days=C.BALANCE_NET_DAYS, year=year, received=received,
                           total=sum(r["v"] for r in received), exp=exp, rate=rate, by_month=by_month,
                           funnel={r["stage"]: r["n"] for r in funnel}, outstanding=outstanding,
                           upcoming=upcoming, overhead=overhead, refunds=refunds, lost=lost, today_iso=today().isoformat(),
                           tax_due=round(tax_lines + tax_kept, 2), tax_kept=tax_kept)


@app.route("/settings", methods=["GET", "POST"])
def settings_view():
    if request.method == "POST":
        for k in ("mileage_rate", "home_base", "photographer_name", "studio_name", "business_name", "business_address", "business_email", "business_phone", "gallery_base_url",
                  "business_tax_id", "bank_details", "pay_details"):
            if k not in request.form:
                continue                     # each tab saves only its own fields
            v = request.form.get(k, "")
            if k == "gallery_base_url" and v.strip() and not v.strip().startswith("https://"):
                continue                     # client galleries are only ever linked over https
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (k, v))
        term = request.form.get("quote_term_days", "")
        if term.isdigit():
            x("INSERT INTO settings (key, value) VALUES ('quote_term_days',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (term,))
        if request.form.get("main_posted"):
            x("INSERT INTO settings (key, value) VALUES ('credit_off',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              ("" if request.form.get("credit") else "1",))
            am = request.form.get("age_majority", "")
            if am.isdigit() and 14 <= int(am) <= 25:
                x("INSERT INTO settings (key, value) VALUES ('age_majority',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (am,))
        if "sales_tax_pct" in request.form:
            for k in ("sales_tax_pct", "local_tax_pct"):
                try:
                    v_ = float(request.form.get(k) or 0)
                except ValueError:
                    v_ = -1
                if not 0 <= v_ <= 25:
                    session["notice"] = "Sales tax rates are percentages between 0 and 25. That rate wasn't saved."
                    continue
                x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (k, request.form.get(k, "").strip()))
        if request.form.get("fees_posted"):
            cur = current_schedule()
            items = []
            for i, it in enumerate(cur["items"]):
                if request.form.get(f"fee_del_{i}"):
                    continue
                try:
                    amt = num(request.form.get(f"fee_amt_{i}"), it["amount"], lo=0)   # a typo keeps the old price
                except ValueError:
                    amt = it["amount"]
                items.append(dict(it, label=request.form.get(f"fee_label_{i}", it["label"]).strip() or it["label"], amount=amt,
                                  taxable=bool(request.form.get(f"fee_tax_{i}"))))
            if request.form.get("new_label", "").strip():
                lab = request.form["new_label"].strip()
                key = re.sub(r"[^a-z0-9]+", "_", lab.lower()).strip("_")[:30] or "fee"
                while any(i_["key"] == key for i_ in items):
                    key += "_"
                try:
                    amt = num(request.form.get("new_amount"), lo=0)
                except ValueError:
                    amt = 0
                items.append(dict(key=key, label=lab, amount=amt, unit=request.form.get("new_unit", "flat"),
                                  taxable=bool(request.form.get("new_taxable"))))
            if items != cur["items"]:
                x("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)", (now_local(), json.dumps(items), ""))
        if request.form.get("clear_demo"):
            # demo galleries on the web come down with their demo clients
            for r_ in q("SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.demo=1"):
                if app.config.get("gallery_take_down"):
                    app.config["gallery_take_down"](r_["id"])
                if app.config.get("retention_purge_now"):
                    app.config["retention_purge_now"](r_["id"])
            x("UPDATE households SET referred_by=NULL WHERE referred_by IN (SELECT id FROM households WHERE demo=1)")
            x("DELETE FROM households WHERE demo=1")
            x("DELETE FROM events WHERE demo=1")
        return redirect(url_for("settings_view", tab=request.args.get("tab") or None, saved=1))
    n_demo = q("SELECT COUNT(*) n FROM households WHERE demo=1", one=True)["n"]
    return render_template("settings.html", plugin_errors=PLUGIN_ERRORS, rate=setting("mileage_rate"), home_base=setting("home_base"),
                           photographer=setting("photographer_name"), studio=setting("studio_name"),
                           gallery_base=setting("gallery_base_url"),
                           n_demo=n_demo, plugins=LOADED, db_path=DB_PATH,
                           fees=current_schedule(), term=quote_term(), tax_pct=setting("sales_tax_pct"), local_pct=setting("local_tax_pct"),
                           region=bool(tax_region()), reviews=review_rows() if request.args.get("tab") == "documents" else [],
                           versions=q("SELECT id, created FROM fee_schedules ORDER BY id DESC LIMIT 8"))


def review_rows():
    """Every document a client can receive, per session type where its wording differs, and whether it is reviewed."""
    import wording
    rows = []
    for doc in ("agreement", "release", "transfer", "license", "terms"):
        seen = {}
        for t_ in C.TYPES:
            k_ = t_["key"]
            if doc == "agreement" and k_ not in C.NEEDS_AGREEMENT:
                continue
            if doc == "release" and k_ not in C.NEEDS_RELEASE:
                continue
            if doc == "transfer" and k_ in C.LICENSE_TYPES or doc == "license" and k_ not in C.LICENSE_TYPES:
                continue
            seen.setdefault(_review_key(doc, k_), []).append(k_)
        for key_, types_ in seen.items():
            st = review_state(doc, types_[0])
            rows.append(dict(label=REVIEW_DOCS[doc].split(" (")[0], for_=oxford([C.SESSION_TYPES[t] for t in types_]),
                             reviewed=st["reviewed"], when=st["when"], url=review_url(doc, types_[0] if doc == "terms" or len(seen) > 1 else None)))
    st = review_state("terms")
    rows.append(dict(label="Price list terms", for_="every type", reviewed=st["reviewed"], when=st["when"], url=url_for("settings_view", tab="fees") + "#terms"))
    return rows


def logo_src():
    """The owner's logo as a data address, for documents (works in the PDF too)."""
    v = setting("logo_png")
    return f"data:image/png;base64,{v}" if v else ""


app.jinja_env.globals["logo_src"] = logo_src
app.jinja_env.globals["iso_today"] = lambda: today().isoformat()


@app.post("/settings/logo")
def logo_upload():
    """A logo for the documents: PNG or JPEG only (no SVG: it can carry script), re-encoded and resized here."""
    from PIL import Image, ImageOps
    import base64
    import io as _io
    if request.form.get("remove"):
        x("DELETE FROM settings WHERE key='logo_png'")
        audit("Removed the logo")
        return redirect(url_for("settings_view"))
    f_ = request.files.get("logo")
    try:
        im = Image.open(f_.stream)
        if im.format not in ("PNG", "JPEG") or im.width * im.height > 40_000_000:
            raise ValueError
        im = ImageOps.exif_transpose(im).convert("RGBA")
        im.thumbnail((600, 240), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    except Exception:
        session["notice"] = "That file isn't a PNG or JPEG picture we can use."
        g.refused = True
        return redirect(url_for("settings_view"))
    x("INSERT INTO settings (key, value) VALUES ('logo_png',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (base64.b64encode(buf.getvalue()).decode(),))
    audit("Uploaded a logo for the documents")
    return redirect(url_for("settings_view"))


def _backup_extras():
    """For each backup: the settings file (the same as Settings > Export, to restore settings on their own) and the
    storage settings as stored (encrypted under the data key, which the backup's keyring carries)."""
    import settings_io
    from vault import web as vault_web_
    con = connect()
    try:
        get = lambda k: (lambda r_: r_[0] if r_ else None)(con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone())
        r_ = con.execute("SELECT items FROM fee_schedules ORDER BY id DESC LIMIT 1").fetchone()
        data = settings_io.export(get, json.loads(r_[0]) if r_ else [], vault_web_.config())
    finally:
        con.close()
    out = [("settings.json", json.dumps(data, indent=1).encode())]
    if vault_web_.CFG_PATH.exists():
        out.append(("storage.json", vault_web_.CFG_PATH.read_bytes()))
    for p_ in (DELETIONS_JSON, NUMBERS_JSON):      # so a lost disk doesn't lose them (merged, never shrunk, on restore)
        if p_.exists():
            out.append((p_.name, p_.read_bytes()))
    return out


vault_backup_mod = __import__("vault.backup", fromlist=["EXTRAS"])
vault_backup_mod.EXTRAS.append(_backup_extras)


@app.get("/settings/export.json")
def settings_export():
    """Everything that describes the business, as a file to import into another install. No client records, no secrets."""
    import settings_io
    from vault import web as vault_web_
    data = settings_io.export(setting, current_schedule()["items"], vault_web_.config())
    r = app.response_class(json.dumps(data, indent=1), mimetype="application/json")
    r.headers["Content-Disposition"] = f'attachment; filename="lumen-aureum-settings-{today().isoformat()}.json"'
    audit("Exported the settings")
    return r


@app.post("/settings/import")
def settings_import():
    """Apply a settings file from Export: only known settings, every value checked; prices become a new price list."""
    import settings_io
    from vault import web as vault_web_
    back = safe_back(request.form.get("back")) or url_for("settings_view")
    f = request.files.get("file")
    if not f:
        return redirect(back)
    try:
        cur = vault_web_.config()
        raw_text = f.read(1_000_000).decode("utf-8", "replace")
        new_settings, new_rules, fees, storage = settings_io.parse(raw_text, cur)
    except settings_io.ImportProblem as e:
        session["import_result"] = dict(error=str(e)[:200])
        return redirect(back)
    first_setup = setting("setup_complete") != "1"
    skipped = []
    if not first_setup:            # after setup, a file never moves backups or galleries: change those in their own steps
        if storage:
            skipped.append("backup and gallery locations")
        storage = {}
        if new_settings.pop("gallery_base_url", None):
            skipped.append("gallery address") if "backup and gallery locations" not in skipped else None
    gal = {}
    if first_setup:              # galleries are public: their host and address wait for the owner's OK, like plugins
        if storage.get("gallery_host"):
            gal["host"] = {k_: v_ for k_, v_ in storage.pop("gallery_host").items() if k_ not in settings_io.SECRET_FIELDS}
        if new_settings.get("gallery_base_url"):
            gal["url"] = new_settings.pop("gallery_base_url")
    notices = []
    own_words = [d_ for d_ in ("agreement", "release", "transfer", "license") if (new_settings.get(f"{d_}_text") or "").strip()
                 or any((t_.get(f"{d_}_text") or "").strip() for t_ in (new_rules.get("TYPES") or []))]
    if own_words:               # legal text from a file is read before it is used: its documents go back to SAMPLE
        r_ = {k_: v_ for k_, v_ in _reviews().items() if k_.split(":")[0] not in own_words}
        x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
        notices.append(f"This file brings its own {oxford(own_words)} wording. Those documents print as SAMPLE until you"
                       " read each one in Settings and press Reviewed.")
    for k, v in new_settings.items():
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    for t_ in new_rules.get("TYPES") or []:
        if t_.get("animals"):
            notices.append(f"{t_['label']}: no photo release is asked for (animal sessions).")
    if new_rules:
        try:
            merged = json.loads(setting("rules") or "{}")
        except ValueError:
            merged = {}
        merged.update(new_rules)
        x("INSERT INTO settings (key, value) VALUES ('rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
          (json.dumps(merged),))
    if fees:
        x("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)", (now_local(), json.dumps(fees), "Imported settings"))
    if storage:
        cur.update(storage)
        vault_web_.write_config(cur)
    result = dict(ok=True, skipped=skipped, notices=notices)
    if gal:
        h_ = gal.get("host", {})
        result["gallery"] = gal
        result["gallery_words"] = " ".join(x_ for x_ in (h_.get("type", "").upper(), h_.get("endpoint") or "",
                                                         h_.get("host") or h_.get("bucket") or h_.get("path") or "",
                                                         gal.get("url", "")) if x_)
    session["import_result"] = result
    apply_settings()
    return redirect(back)


def _save_rules(**kv):
    try:
        merged = json.loads(setting("rules") or "{}")
    except ValueError:
        merged = {}
    if "TYPES" in kv and "LICENSE_TYPES" not in kv and "LICENSE_TYPES" in merged:
        had = {t_.get("key") for t_ in C.TYPES}                # a type added later keeps the copyright with the
        new_ = [t_["key"] for t_ in kv["TYPES"] if t_.get("key") not in had]   # photographer, like every type at setup
        if new_:
            kv = dict(kv, LICENSE_TYPES=list(merged["LICENSE_TYPES"]) + [k_ for k_ in new_ if k_ not in merged["LICENSE_TYPES"]])
    merged.update(kv)
    x("INSERT INTO settings (key, value) VALUES ('rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (json.dumps(merged),))
    apply_settings()


def own_wording(doc, stype=None):
    """The wording a document uses: the session type's own, else the owner's general wording, else the standard."""
    import wording
    t_ = next((t for t in C.TYPES if t["key"] == stype), None) if stype else None
    return ((t_ or {}).get(f"{doc}_text") or "").strip() or setting(f"{doc}_text").strip() or wording.STANDARD[doc]


# ---------------------------------------------------------------- review: each supplied document prints SAMPLE until read
# Each document prints SAMPLE (supplied boilerplate) until the owner presses Reviewed on its own page. Once reviewed it stays reviewed
# (later edits never bring SAMPLE back); only "Revert to default" does, which also puts the shipped
# wording back. A session type with its own
# wording is a separate document with its own review. Saving never counts as a review, and a settings file never
# brings reviews with it.
REVIEW_DOCS = {"agreement": "Agreement", "release": "Release", "transfer": "Copyright transfer", "license": "Photo license",
               "terms": "Terms (quote, invoice, session details, price list)"}


def _review_key(doc, stype=None):
    """Which review a document falls under: the type's own (terms, or a type with its own wording), else the general."""
    if doc == "terms":
        return f"terms:{stype or '*'}"
    t_ = next((t for t in C.TYPES if t["key"] == stype), None) if stype else None
    return f"{doc}:{stype}" if t_ and (t_.get(f"{doc}_text") or "").strip() else f"{doc}:*"


def _freeze_reviewed_wording():
    """Once, at the first unlock of 1.0: a document whose sample wording was reviewed before 1.0, and that has no own
    wording, keeps the pre-1.0 words as its own. A release never changes a contract its photographer approved."""
    import wording_legacy
    t_ = setting("transfer_text")
    if wording_legacy.OLD_TRANSFER_PORTFOLIO in t_:      # frozen before the repair: the paragraph no longer fills
        x("UPDATE settings SET value=? WHERE key='transfer_text'",
          (t_.replace(wording_legacy.OLD_TRANSFER_PORTFOLIO, "[FAVORITES]"),))
        try:
            session["notice"] = "Your copyright transfer letter's portfolio paragraph now matches the portfolio promise in your agreement."
        except RuntimeError:
            pass
    if setting("wording_frozen_1_0") == "1":
        return
    r_ = _reviews()
    for doc, text in wording_legacy.STANDARD_RC.items():
        reviewed = any(k_.startswith(doc + ":") for k_ in r_) or (doc == "agreement" and setting("agreement_reviewed") == "1")
        if reviewed and not setting(f"{doc}_text").strip():
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"{doc}_text", text))
    x("INSERT INTO settings (key, value) VALUES ('wording_frozen_1_0','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")


def _reviews():
    try:
        return dict(json.loads(setting("doc_reviews") or "{}"))
    except (ValueError, TypeError):
        return {}


def doc_reviewed(doc, stype=None):
    """True once the owner has reviewed this document (for this session type)."""
    return _review_key(doc, stype) in _reviews()


def review_url(doc, stype=None):
    if doc == "terms":
        return url_for("type_edit", key=stype) + "#terms" if stype else url_for("settings_view", tab="fees") + "#terms"
    return url_for("wording_edit", doc=doc, type=stype or None)


@app.post("/settings/review/<doc>")
def doc_review(doc):
    """Reviewed - remove SAMPLE: the one way a document stops printing as a sample."""
    if doc not in REVIEW_DOCS:
        abort(404)
    stype = request.form.get("type") or None
    if stype and stype not in C.SESSION_TYPES:
        abort(404)
    r_ = _reviews()
    key = _review_key(doc, stype)
    if request.form.get("revert"):           # Revert to default: the shipped wording, and SAMPLE again
        r_.pop(key, None)
        if doc == "terms" and stype:
            types = [dict(t) for t in C.TYPES]
            t_ = next(t for t in types if t["key"] == stype)
            shipped = next((t for pr in C.PRESETS.values() for t in pr["types"] if t["key"] == stype), None)
            t_["policy"] = dict((shipped or {}).get("policy") or {})
            t_.pop("deposit_pct", None); t_.pop("deposit_amount", None)
            for k_ in ("deposit_pct", "deposit_amount"):
                if shipped and shipped.get(k_) is not None:
                    t_[k_] = shipped[k_]
            _save_rules(TYPES=types)
        elif doc != "terms":
            x("DELETE FROM settings WHERE key=?", (f"{doc}_text",))
        audit(f"Reverted the {REVIEW_DOCS[doc].split(' (')[0].lower()} to the shipped default" + (f" for {C.SESSION_TYPES[stype]}" if stype else ""))
    else:
        r_[key] = today().isoformat()
        if doc in ("agreement", "release", "transfer", "license") and key.endswith(":*") and not setting(f"{doc}_text").strip():
            # what was reviewed becomes the photographer's own wording: a later release of the app never changes it
            import wording
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (f"{doc}_text", wording.STANDARD[doc]))
        audit(f"Reviewed the {REVIEW_DOCS[doc].split(' (')[0].lower()} wording" + (f" for {C.SESSION_TYPES[stype]}" if stype else ""))
    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
    return redirect(safe_back(request.form.get("back")) or review_url(doc, stype))


def review_state(doc, stype=None):
    """For a wording page: reviewed (and when) or not."""
    when = _reviews().get(_review_key(doc, stype))
    return dict(doc=doc, stype=stype, reviewed=bool(when), when=nice(when) if when else "")


app.jinja_env.globals["review_state"] = review_state


@app.route("/settings/wording/<doc>", methods=["GET", "POST"])
def wording_edit(doc):
    """Your own wording for the agreement or the release: plain text with [FIELDS]; Reset goes back to the standard."""
    import wording
    if doc not in wording.STANDARD:
        abort(404)
    key = f"{doc}_text"
    stype = request.args.get("type") or ""
    types = [dict(t) for t in C.TYPES]
    t_ = next((t for t in types if t["key"] == stype), None)
    if stype and not t_:
        abort(404)
    general = setting(key).strip() or wording.STANDARD[doc]
    if request.method == "POST":
        text = "" if request.form.get("reset") else wording.clean(request.form.get("text", ""))
        if t_:                                          # one session type's own wording, kept with the type
            if text == general.strip():
                text = ""                              # unchanged: keep following the general wording
            if text:
                t_[key] = text
            else:
                t_.pop(key, None)
                r_ = _reviews()
                if r_.pop(f"{doc}:{stype}", None):     # back on the general wording: its own review goes with it
                    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
            _save_rules(TYPES=types)
        else:
            if text == wording.STANDARD[doc].strip():
                text = ""                              # unchanged: keep following the standard (and its updates)
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, text))
        audit(f"{'Reset' if request.form.get('reset') else 'Saved'} the {doc} wording" + (f" for {t_['label']}" if t_ else ""))
        return redirect(url_for("wording_edit", doc=doc, type=stype or None, saved=1))
    if t_:
        own = (t_.get(key) or "").strip()
        text = own or general
    else:
        own = setting(key).strip()
        text = own or wording.STANDARD[doc]
    return render_template("wording_edit.html", doc=doc, label=wording.LABELS.get(doc, doc.capitalize()), text=text, own=bool(own), stype=t_,
                           typed=[t for t in types if (t.get(key) or "").strip()], all_types=types,
                           fields=wording.FIELDS[doc], saved=request.args.get("saved"))


# Sessions whose price may still follow a settings change: not yet agreed (inquiry or quoted, no quote sent, nothing
# paid, nothing signed). A booked or paid session's price never moves on its own.
UNAGREED_SQL = """SELECT id FROM sessions WHERE type=? AND stage IN ('inquiry','quoted')
                  AND NOT EXISTS (SELECT 1 FROM quote_versions v WHERE v.session_id=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.session_id=sessions.id AND p.voided IS NULL)
                  AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.session_id=sessions.id AND d.status='signed')"""


def _walk_state():
    try:
        return json.loads(setting("walk") or "{}")
    except ValueError:
        return {}


@app.route("/settings/type/<key>/walk/<int:n>", methods=["GET", "POST"])
def type_walk(key, n):
    """Walk me through this: one setting per screen (walkthrough.py). Keep, change or skip; nothing is required."""
    import walkthrough as W
    types = [json.loads(json.dumps(t_)) for t_ in C.TYPES]
    t = next((t_ for t_ in types if t_["key"] == key), None) or abort(404)
    steps = W.steps_for(t)
    st = _walk_state()
    mine = st.setdefault(key, {"done": [], "skipped": []})
    if request.method == "POST" and n < len(steps):
        step, act = steps[n], request.form.get("act")
        if act == "save":
            step["put"](request.form)
            _save_rules(TYPES=types)
            if step["id"] in ("portfolio", "deposit"):           # quotes not yet sent follow the new amounts
                for r_ in q(UNAGREED_SQL, (key,)):
                    reapply_discount(r_["id"])
        if act in ("save", "keep"):
            mine["done"] = sorted(set(mine["done"]) | {step["id"]})
            mine["skipped"] = [i for i in mine["skipped"] if i != step["id"]]
        elif act == "skip":
            mine["skipped"] = sorted(set(mine["skipped"]) | {step["id"]})
        _put_setting("walk", json.dumps(st))
        nxt = len(steps) if act == "finish" else n + 1
        return redirect(url_for("type_walk", key=key, n=nxt))
    if n >= len(steps):
        return render_template("walk.html", t=t, steps=steps, n=n, mine=mine, summary=True)
    step = steps[n]
    return render_template("walk.html", t=t, steps=steps, n=n, step=step, value=step["get"](), preview=step["preview"](),
                           mine=mine, summary=False)


@app.post("/walk/dismiss")
def walk_dismiss():
    _put_setting("walk_dismissed", "1")
    return redirect(url_for("home"))


def walk_offer():
    """Today's card after setup: the session types still untouched by the walkthrough (until dismissed)."""
    if setting("walk_dismissed") == "1" or not setup_done():
        return []
    st = _walk_state()
    return [(k_, lab) for k_, lab in C.SESSION_TYPES.items() if k_ != C.PERSONAL_TYPE["key"] and not (st.get(k_) or {}).get("done")]


app.jinja_env.globals["walk_offer"] = walk_offer


@app.route("/settings/type/<key>", methods=["GET", "POST"])
def type_edit(key):
    """One session type: its steps per stage and its policy wording. Steps that match the kind's defaults are not
    stored, so a later default change still reaches them."""
    types = [dict(t) for t in C.TYPES]
    t = next((t_ for t_ in types if t_["key"] == key), None) or abort(404)
    kind_flow = C.KINDS[t["kind"]]["flow"]
    step_kind = "event" if t["kind"] == "event" else "do"
    if request.method == "POST":
        f = request.form
        steps = {}
        for stage in C.STEP_STAGES:
            rows = []
            for i in range(60):
                k_ = f"{stage}_{i}"
                if k_ + "_kind" not in f:
                    break
                if f.get(k_ + "_drop"):
                    continue
                if f[k_ + "_kind"] == "auto":
                    if f.get(k_ + "_key") in C.AUTO_KEYS:
                        rows.append(["auto", f[k_ + "_key"]])
                    continue
                label = (f.get(k_ + "_label") or "").strip()[:80]
                if label:
                    rows.append([step_kind, label, _days(f.get(k_ + "_days"))])
            if f.get(stage + "_new"):
                rows.append([step_kind, f[stage + "_new"].strip()[:80], _days(f.get(stage + "_new_days"))])
            if f.get(stage + "_auto") in C.AUTO_KEYS:
                rows.append(["auto", f[stage + "_auto"]])
            if [tuple(r) for r in rows] != [tuple(r) for r in kind_flow.get(stage, [])]:
                steps[stage] = rows
        t["steps"] = steps
        if not steps:
            t.pop("steps", None)
        for flag in ("all_sign", "indoors"):
            if f.get(flag):
                t[flag] = True
            else:
                t.pop(flag, None)
        t.pop("portfolio_pct", None); t.pop("portfolio_amount", None)
        pv_ = _int_or_none(f.get("portfolio_value"))
        if pv_ and f.get("portfolio_mode") in ("pct", "amt"):           # blank or 0: no portfolio discount
            t["portfolio_pct" if f["portfolio_mode"] == "pct" else "portfolio_amount"] = min(pv_, 100 if f["portfolio_mode"] == "pct" else 100000)
        t.pop("deposit_pct", None); t.pop("deposit_amount", None)
        dv = _int_or_none(f.get("deposit_value"))
        if dv is not None and f.get("deposit_mode") in ("pct", "amt"):        # blank = the general deposit rule
            t["deposit_pct" if f["deposit_mode"] == "pct" else "deposit_amount"] = max(0, min(dv, 100 if f["deposit_mode"] == "pct" else 100000))
        t["policy"] = dict(free_reschedule=bool(f.get("free_reschedule")), outfits=bool(f.get("outfits")),
                           wear=((f.get("wear") or "").strip().rstrip(".")[:200] or C.DEFAULT_WEAR),
                           **{k_: (f.get(k_) or "").strip()[:200] for k_, _, _ in C.FILL_INS},
                           notice_hours=max(0, min(2000, _int_or_none(f.get("notice_hours")) or 0)),
                           late_minutes=max(0, min(2000, _int_or_none(f.get("late_minutes")) or 0)),
                           keep_raw_days=max(1, min(3650, _int_or_none(f.get("keep_raw_days")) or C.raw_keep_days(key))))
        if f.get("delivery_method") in C.DELIVERY_METHODS:
            t["policy"]["delivery_method"] = f["delivery_method"]
        if f.get("gallery_codeword"):
            t["policy"]["gallery_codeword"] = True
        dd_ = _int_or_none(f.get("delivery_days"))
        general_ = C.EVENT_DELIVERY_DAYS if t["kind"] == "event" else C.DELIVERY_DAYS
        if dd_ and dd_ > 0 and dd_ != general_:              # the general number: follow it when it changes
            t["policy"]["delivery_days"] = min(dd_, 365)
        gd_ = _int_or_none(f.get("gallery_days"))
        if gd_ and gd_ > 0 and gd_ != C.GALLERY_DAYS:          # the general number: follow it when it changes
            t["policy"]["gallery_days"] = min(gd_, 3650)
        if "questions" in f:
            qs = _parse_questions(f["questions"], C.INTAKE[key])
            base = [tuple(q_) for q_ in t.get("intake", [])] + C.KINDS[t["kind"]]["intake"]
            t.pop("questions", None)
            if qs != base:
                t["questions"] = [list(q_) for q_ in qs]
        _save_rules(TYPES=types)
        for r_ in q("SELECT id FROM sessions WHERE type=? AND stage NOT IN ('closed','lost')", (key,)):
            sync_workflow_tasks(r_["id"])
            if q(UNAGREED_SQL + " AND id=?", (key, r_["id"]), one=True):
                reapply_discount(r_["id"])          # quotes not yet agreed follow the new portfolio discount
        return redirect(url_for("type_edit", key=key, saved=1))
    flow = C.WORKFLOW[key]
    return render_template("type_edit.html", t=t, flow=flow, stages=C.STEP_STAGES, policy=C.POLICY[key],
                           questions="\n".join(q_[1] for q_ in C.INTAKE[key]),
                           auto_label={k_: v_[0] for k_, v_ in AUTO.items()}, saved=request.args.get("saved"))


def _parse_questions(text, current):
    """Intake questions, one per line. A question kept word for word keeps its answers (same key); a new one gets a
    key from its words. "Address" and "Venue" keep the keys that search, invoices and the calendar read."""
    by_label = {lab.strip().lower(): k_ for k_, lab in current}
    out, used = [], set()
    for line in (text or "").splitlines()[:20]:
        lab = " ".join(line.split())[:120]
        if not lab:
            continue
        k_ = by_label.get(lab.lower())
        if not k_:
            low = lab.lower()
            k_ = next((w for w in ("address", "venue") if w in low and w not in used), None) or \
                "q_" + (re.sub(r"[^a-z0-9]+", "_", low).strip("_")[:24] or "x")
        base, n = k_, 2
        while k_ in used:
            k_, n = f"{base}_{n}", n + 1
        used.add(k_)
        out.append((k_, lab))
    return out


def _days(v):
    """Days from the shoot for a step: -365 to 365, or none."""
    n = _int_or_none(v)
    return None if n is None else max(-365, min(365, n))


def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


@app.post("/settings/import/gallery")
def settings_import_gallery():
    """Use the gallery host and address an imported file named, after the owner has seen them."""
    from vault import web as vault_web_
    res = session.get("import_result") or {}
    gal = res.get("gallery")
    if gal and request.form.get("use") == "1":
        if gal.get("host"):
            import settings_io
            cur = vault_web_.config()
            old = cur.get("gallery_host") or {}
            new = dict(gal["host"])
            if old.get("type") == new.get("type"):           # a saved secret stays on the server; it never rode in the cookie
                new.update({k_: old[k_] for k_ in settings_io.SECRET_FIELDS if k_ in old})
            cur["gallery_host"] = new
            vault_web_.write_config(cur)
        if gal.get("url"):
            x("INSERT INTO settings (key, value) VALUES ('gallery_base_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (gal["url"],))
        audit("Gallery host set from the imported file")
    res.pop("gallery", None)
    res.pop("gallery_words", None)
    session["import_result"] = res
    return redirect(safe_back(request.form.get("back")) or url_for("settings_view"))


@app.post("/settings/reset")
def settings_reset():
    """Wipe every client, session, event and log row; keep Settings. Typed confirmation, and a full copy of the database goes to
    the backups folder first (sealed in the vault when a master key exists); if that copy fails, nothing is deleted."""
    if request.form.get("confirm", "").strip() != "RESET":
        return redirect(url_for("settings_view") + "?reset=nope")
    import jobs

    def work(job):
        _reset_work()
        return "Reset done. The old database was sealed into the vault first (kept 30 days)."
    jobs.start(app, "reset", "Resetting the database (a sealed copy first)", work)
    return redirect(url_for("settings_view", tab="system"))


def _reset_work():
    from vault import backup as vault_backup
    # the pre-reset copy is sealed to the master key (db + faces + keyring); there is no unencrypted fallback any more
    vault_backup.sealed_backup(str(DB_PATH), str(FACES) if FACES.exists() else None, kind="reset")
    # sealed session archives are NOT destroyed by a reset: they are marked orphaned and
    # destroyed with the pre-reset snapshot 30 days later, so the reset stays undoable for the same 30 days
    _c = connect()
    try:
        _c.execute("UPDATE vault_keys SET orphaned=? WHERE orphaned IS NULL", (now_local(),))
        _c.commit()
    finally:
        _c.close()
    if FACES.exists():
        import shutil
        shutil.rmtree(FACES)
    import locks
    with locks.WRITES:
        _wipe_after_backup("sealed")


def _wipe_after_backup(saved):
    con = connect()
    con.execute("PRAGMA foreign_keys=OFF")
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t_ in tables:
        if t_ not in ("settings", "fee_schedules", "vault_objects", "vault_keys", "users"):   # the vault keeps its records; the owner stays
            con.execute("DELETE FROM " + dbmod.ident(t_))
    # The current fee schedule survives a reset as schedule #1. Older versions go: no quote refers to them any more.
    cur = con.execute("SELECT items, note FROM fee_schedules ORDER BY id DESC LIMIT 1").fetchone()
    con.execute("DELETE FROM fee_schedules")
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        con.execute("DELETE FROM sqlite_sequence")
    if cur:
        con.execute("INSERT INTO fee_schedules (id, created, items, note) VALUES (1, ?, ?, ?)",
                    (now_local(), cur[0], cur[1]))
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("VACUUM")    # deleted rows otherwise linger in free pages of crm.db
    return saved                # runs as a job: no request here, so nothing to redirect


# ---------------------------------------------------------------- start

# ---------------------------------------------------------------- schema versions
# The database records its schema version (PRAGMA user_version). 1 is the 1.0 baseline: everything init_db builds.
# A later change that is more than "add a table or a column" (a rename, a type change, rewriting data) goes in
# MIGRATIONS as the next number. At unlock, before any numbered step runs, a sealed "upgrade" backup is taken (when a
# master key exists); the steps then run in one transaction, so a failure leaves the database as it was. A database
# newer than this app is never opened: an older version could misread it.
SCHEMA_VERSION = 2
MIGRATIONS = {}          # n -> fn(con): moves the schema from n - 1 to n. Never edit a released step; add a new one.


def _migrate_2(con):
    """1.0: signed copies, hold stages, portfolio choice, delivery method, retention days, gallery picks. The tables and
    columns are made by init_db and the plugins (all additive); this step exists so the upgrade first takes a sealed
    backup and an older build refuses this database instead of opening it."""


MIGRATIONS[2] = _migrate_2


class SchemaTooNew(RuntimeError):
    pass


class UpgradeBlocked(RuntimeError):
    """The backup before an upgrade could not be made: nothing was changed."""


@app.errorhandler(UpgradeBlocked)
def _upgrade_blocked(e):
    return ("This version needs to update your database, and makes an encrypted backup first. That backup could not be "
            f"made ({e.args[0]}), so nothing was changed. Check that the vault storage is reachable, then sign in again. "
            "Or go back to the version you had.", 503, {"Content-Type": "text/plain; charset=utf-8"})


def _schema_version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def _upgrade_backup(con):
    """The sealed copy taken before an upgrade changes anything (the schema script, new columns, numbered steps)."""
    has_master = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone()) and bool(
        con.execute("SELECT 1 FROM settings WHERE key='vault_master_pub' AND COALESCE(value,'') != ''").fetchone())
    if has_master:
        from vault import backup as vault_backup
        try:
            vault_backup.sealed_backup(str(DB_PATH), str(FACES) if FACES.exists() else None, kind="upgrade")
        except Exception as e:
            raise UpgradeBlocked(f"{type(e).__name__}: {e}"[:200]) from e


def _migrate(con, have, backed_up=False):
    """Run the numbered steps after `have`, after a sealed backup, all in one transaction."""
    todo = [n for n in sorted(MIGRATIONS) if have < n <= SCHEMA_VERSION]
    if not todo:
        return
    if not backed_up:
        _upgrade_backup(con)
    con.execute("BEGIN IMMEDIATE")
    try:
        for n in todo:
            MIGRATIONS[n](con)
            dbmod.set_user_version(con, "main", n)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    app.logger.info("database schema upgraded from %s to %s", have, todo[-1])


@app.errorhandler(SchemaTooNew)
def _schema_too_new(e):
    return (f"This data was written by a newer version of Lumen Aureum (database version {e.args[0]}; this version reads "
            f"up to {SCHEMA_VERSION}). Run the newer version again, or restore the backup taken before the upgrade "
            "(docs/operations.md, Rolling back).", 409, {"Content-Type": "text/plain; charset=utf-8"})


def init_db():
    con = connect()
    have = _schema_version(con)
    if have > SCHEMA_VERSION:
        con.close()
        raise SchemaTooNew(have)
    had_snaps = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='doc_snapshots'").fetchone())
    had_docs = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone())
    backed_up = False
    if had_docs and have < SCHEMA_VERSION and any(n_ > have for n_ in MIGRATIONS):
        _upgrade_backup(con)                     # before anything changes: the copy is the old database exactly
        backed_up = True
    con.executescript(SCHEMA)
    if had_docs and not had_snaps:
        # upgrading from before signed copies were kept: documents already signed get a marker, not a copy, so they
        # never show today's words as "what was signed" (they read live, as they always did)
        legacy = json.dumps({"legacy": True})
        con.execute("""INSERT OR IGNORE INTO doc_snapshots (kind, ref, at, data)
                       SELECT DISTINCT 'agreement', session_id, NULL, ? FROM documents WHERE kind='agreement' AND status='signed'""", (legacy,))
        con.execute("""INSERT OR IGNORE INTO doc_snapshots (kind, ref, at, data)
                       SELECT 'release', id, NULL, ? FROM documents WHERE kind='release' AND status='signed'""", (legacy,))
    import photostore
    photostore.migrate(con)
    pcols = [r[1] for r in con.execute("PRAGMA table_info(people)")]
    for col, decl in (("relation", "TEXT DEFAULT ''"), ("gender", "TEXT DEFAULT ''"), ("birth_year", "INTEGER")):
        if col not in pcols:
            dbmod.add_column(con, "people", col, decl)
    if "written_at" not in [r[1] for r in con.execute("PRAGMA table_info(log)")]:
        con.execute("ALTER TABLE log ADD COLUMN written_at TEXT")
    if "note" not in [r[1] for r in con.execute("PRAGMA table_info(step_closes)")]:
        con.execute("ALTER TABLE step_closes ADD COLUMN note TEXT DEFAULT ''")
    cols = [r[1] for r in con.execute("PRAGMA table_info(tasks)")]
    if "stage" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN stage TEXT")
    if "own" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN own INTEGER DEFAULT 0")
    if "event_id" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN event_id INTEGER REFERENCES events(id) ON DELETE CASCADE")
    if "outcome" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN outcome TEXT")
    if "outcome_note" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN outcome_note TEXT")
    con.executescript("""
CREATE TABLE IF NOT EXISTS fee_schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, items TEXT NOT NULL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS quotes (session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  schedule_id INTEGER REFERENCES fee_schedules(id), discount_pct REAL DEFAULT 0, discount_reason TEXT DEFAULT '',
  created TEXT, expires TEXT);""")
    lcols = [r[1] for r in con.execute("PRAGMA table_info(lines)")]
    for col, decl in (("kind", "TEXT DEFAULT 'custom'"), ("fee_key", "TEXT DEFAULT ''"), ("qty", "REAL DEFAULT 1"), ("unit", "REAL")):
        if col not in lcols:
            dbmod.add_column(con, "lines", col, decl)
    # voided payments (no hard delete), mileage rate frozen per trip, durations, lost reasons,
    # quote versions (what was actually sent)
    for tbl, col, decl in (("payments", "voided", "TEXT"), ("expenses", "rate", "REAL"), ("sessions", "duration_min", "INTEGER"),
                           ("sessions", "lost_reason", "TEXT DEFAULT ''"), ("quotes", "sent", "TEXT"), ("sessions", "hold_stage", "TEXT"), ("sessions", "portfolio_no", "INTEGER DEFAULT 0"), ("sessions", "delivery_method", "TEXT"), ("sessions", "gallery_codeword", "TEXT DEFAULT ''"),
                           ("lines", "taxable", "INTEGER DEFAULT 1"), ("sessions", "tax_exempt", "INTEGER DEFAULT 0"),
                           ("sessions", "tax_area", "TEXT"), ("sessions", "retouch_picks", "TEXT DEFAULT ''"),
                           ("sessions", "payer_event", "INTEGER"), ("events", "billing", "TEXT DEFAULT 'free'"),
                           ("events", "rights", "TEXT DEFAULT 'families'"), ("events", "rights_note", "TEXT DEFAULT ''"),
                           ("events", "payer_hid", "INTEGER"), ("events", "contact_hid", "INTEGER"), ("households", "company", "TEXT DEFAULT ''"), ("events", "family_price", "REAL"),
                           ("people", "birth_date", "TEXT"), ("sessions", "date_tentative", "INTEGER DEFAULT 0"),
                           ("sessions", "plan_of", "INTEGER"), ("sessions", "po_number", "TEXT DEFAULT ''"),
                           ("sessions", "lic_term", "TEXT DEFAULT ''"), ("sessions", "lic_territory", "TEXT DEFAULT ''"),
                           ("sessions", "lic_exclusive", "INTEGER DEFAULT 0"), ("payments", "ref", "TEXT DEFAULT ''"),
                           ("people", "age_approx", "INTEGER DEFAULT 0"),
                           ("sessions", "tax_rate", "REAL")):
        if col not in dbmod.columns(con, tbl):
            dbmod.add_column(con, tbl, col, decl)
            if (tbl, col) == ("sessions", "tax_rate"):     # sessions past the quote keep the rate their tax line shows
                for sid_, lab in con.execute("""SELECT s.id, (SELECT label FROM lines l WHERE l.session_id=s.id AND l.kind='tax')
                                                FROM sessions s WHERE s.stage IN ('booked','shot','delivered','closed','lost')""").fetchall():
                    m_ = re.search(r"\(([0-9.]+)%\)", lab or "")
                    con.execute("UPDATE sessions SET tax_rate=? WHERE id=?", (float(m_.group(1)) if m_ else 0.0, sid_))
            if (tbl, col) == ("expenses", "rate"):
                cur_rate = con.execute("SELECT value FROM settings WHERE key='mileage_rate'").fetchone()
                con.execute("UPDATE expenses SET rate=? WHERE miles > 0", (float(cur_rate[0] or 0) if cur_rate else 0,))
    con.executescript("""
CREATE TABLE IF NOT EXISTS quote_versions (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  at TEXT, schedule_id INTEGER, expires TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doc_numbers (session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, kind TEXT NOT NULL,
  number TEXT NOT NULL UNIQUE, issued TEXT, PRIMARY KEY (session_id, kind));
CREATE TABLE IF NOT EXISTS invoice_sent (session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  number TEXT NOT NULL, sig TEXT NOT NULL);""")
    if not con.execute("SELECT 1 FROM fee_schedules").fetchone():
        con.execute("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)",
                    (now_local(), json.dumps(C.DEFAULT_FEES), "Starting estimates"))
    con.commit()
    con.executescript("""
CREATE INDEX IF NOT EXISTS tasks_sid ON tasks(session_id);
CREATE INDEX IF NOT EXISTS tasks_eid ON tasks(event_id);
CREATE INDEX IF NOT EXISTS documents_sid ON documents(session_id);
CREATE INDEX IF NOT EXISTS lines_sid ON lines(session_id);
CREATE INDEX IF NOT EXISTS payments_sid ON payments(session_id);
CREATE INDEX IF NOT EXISTS expenses_sid ON expenses(session_id);
CREATE INDEX IF NOT EXISTS log_sid ON log(session_id);
CREATE INDEX IF NOT EXISTS log_hh ON log(household_id, at);
CREATE INDEX IF NOT EXISTS step_closes_sid ON step_closes(session_id);
CREATE INDEX IF NOT EXISTS stage_log_sid ON stage_log(session_id);
CREATE INDEX IF NOT EXISTS sessions_hh ON sessions(household_id);
CREATE INDEX IF NOT EXISTS sessions_stage ON sessions(stage);
CREATE INDEX IF NOT EXISTS sessions_event ON sessions(event_id);
CREATE INDEX IF NOT EXISTS people_hh ON people(household_id);
CREATE INDEX IF NOT EXISTS audit_sid ON audit(session_id);""")     # foreign keys are not indexed by themselves
    con.commit()
    if have == 0:
        dbmod.set_user_version(con, "main", 1)          # new, or from before versions were recorded: the 1.0 baseline
        con.commit()
        have = 1
    _migrate(con, have, backed_up=backed_up)
    con.close()


from vault import keys as vault_keys, web as vault_web   # encrypted storage (vault/DESIGN.md)
vault_web.register(app, connect, lambda what: audit(what, who="Owner"))
import auth as owner_auth                     # owner login + two-factor; runs before every other request hook


def apply_settings():
    """Push saved settings into the running app: time zone (also the process TZ, so plugins and the backup scheduler
    agree on what 'today' is), the home location for sun times, and the business rules (config.apply)."""
    global TZ
    import time as _time
    from flask import has_app_context
    sql = "SELECT key, value FROM settings WHERE key IN ('timezone','home_lat','home_lon','rules')"
    if has_app_context():
        kv = dict((r[0], r[1]) for r in q(sql))           # the request's own connection: sees its uncommitted save
        if g.get("tx"):
            g.applied = True                               # if this request is rolled back, the rules are re-read
    else:
        con = connect()
        try:
            kv = dict(con.execute(sql).fetchall())
        finally:
            con.close()
    try:
        TZ = ZoneInfo(kv.get("timezone") or os.environ.get("TZ") or "UTC")   # Settings wins; else the container's TZ
    except (ZoneInfoNotFoundError, ValueError):
        TZ = ZoneInfo("UTC")
    sun.TZ = TZ
    os.environ["TZ"] = TZ.key
    _time.tzset()
    try:
        sun.HOME = (float(kv["home_lat"]), float(kv["home_lon"])) if kv.get("home_lat") else None
    except (KeyError, ValueError):
        sun.HOME = None
    try:
        C.apply(json.loads(kv.get("rules") or "{}"))
    except ValueError:
        C.apply({})


import threading as _threading
import atrest
_unlock_guard = _threading.Lock()
_unlocked_once = []


def _unlock_work():
    key = datakey.dk()
    for p_ in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm"), FACES):
        try:
            os.chmod(p_, 0o700 if p_ == FACES else 0o600)   # owner only
        except OSError:
            pass
    init_db()
    _vc = connect(); vault_keys.init(_vc)
    import photostore
    if photostore.sweep_orphans(_vc):
        app.logger.info("removed sealed photos no record points to (after a restore, or an interrupted write)")
    _vc.close()
    owner_auth.init_schema(connect)
    for fn in UNLOCK_HOOKS:
        fn()
    n_faces = atrest.encrypt_faces(FACES, key)
    atrest.encrypt_storage(vault_web.CFG_PATH, key)
    apply_settings()
    reapply_deletions()
    try:
        with app.test_request_context():
            db()
            _fold_legacy_side_files()
            _seed_number_marks()
            _freeze_reviewed_wording()
            _seal_missing_photo_keys()
            g.db.commit()
    except Exception:
        app.logger.exception("seeding document number marks / freezing reviewed wording")
    if n_faces:
        app.logger.info("encrypted %d face photos", n_faces)
    vault_web.PATHS.update(db=DB_PATH, faces=FACES)
    if not os.environ.get("CRM_TESTING"):       # tests never schedule real backups
        vault_web.start_scheduler(app, connect, DB_PATH, FACES)
        threading.Thread(target=_mirror_loop, name="photo-mirror-loop", daemon=True).start()
        threading.Thread(target=_advance_loop, name="advance-loop", daemon=True).start()


def on_unlock():
    """Everything that needs the database, run once per process right after the key is held (first sign-in, or the
    opt-in key file). Only when it all succeeds does the app count as unlocked;
    if anything fails, the key is dropped again and the next sign-in retries."""
    with _unlock_guard:
        if _unlocked_once:
            datakey.mark_ready()
            return
        try:
            _unlock_work()
        except BaseException:
            datakey.lock()
            raise
        _unlocked_once.append(True)
        datakey.mark_ready()


owner_auth.register(app, connect, _allowed, lambda what: audit(what, who="Owner"), on_unlock)


@app.errorhandler(datakey.Locked)
def _locked(_e):
    """Anything that reaches the database while locked goes to sign-in (the gate normally catches it first)."""
    if request.method == "GET":
        return redirect(url_for("login"))
    return "Locked: sign in again.", 503


CSP = ("default-src 'self'; script-src 'self'{extra}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def csp_nonce():
    """A per-response nonce for a plugin's own <script nonce="{{ csp_nonce() }}"> block. Inline handlers
    (onclick=...) are never allowed; the core has no inline script at all (static/*.js)."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(18)
    return g.csp_nonce


app.jinja_env.globals["csp_nonce"] = csp_nonce


@app.after_request
def _no_store(resp):
    """Client pages are never kept in the browser's disk cache (a lost laptop keeps nothing), and every response
    carries the browser protections: no framing, no inline or other-origin scripts, forms or connections, no sniffing.
    A route may set its own policy first (the gallery preview allows its one script by hash)."""
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("Content-Security-Policy",
                            CSP.format(extra=f" 'nonce-{g.csp_nonce}'" if "csp_nonce" in g else ""))
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.scheme == "https" and os.environ.get("CRM_HSTS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"   # only with a real certificate (opt-in)
    return resp


import rotate as _rotate
if _rotate.pending():
    raise SystemExit("A data key rotation was interrupted. Finish it first: python -m vault.cli rotate")
_run_lock = _rotate.hold_running()              # tells offline tools (rotation) that the app is running
__import__("atexit").register(_run_lock.close)   # released on a clean shutdown
import about as _about                          # /about: license, source, what it includes (open, no client data)
_about.register(app)
import setup as first_run                      # setup steps after the owner; until finished, signed-in pages go there
first_run.register(app, connect, apply_settings, lambda what: audit(what, who="Owner"), ALLOW)
LOADED = load_plugins()
if not os.environ.get("CRM_TESTING"):
    netsec.banner(unclaimed=not datakey.exists(),     # fingerprint (+ claim code) in the log
                  plugins=[(p_["name"], p_["built_in"], p_["folder"]) for p_ in PLUGINS_LOADED], plugin_errors=PLUGIN_ERRORS)
_auto = datakey.try_auto_unlock()               # opt-in unattended unlock (CRM_UNLOCK_KEY_FILE)
if _auto:
    datakey.set_unlocked(_auto)
    on_unlock()

if __name__ == "__main__":
    raise SystemExit("Start Lumen Aureum with: python serve.py")
