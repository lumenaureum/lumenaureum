import importlib.util
import ipaddress
import json
import math
import hashlib
import re
import os
import secrets
import io
import threading
import datakey
import db as dbmod
import sealed
from db import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, g, has_request_context, redirect, render_template, render_template_string, request, session, url_for
from markupsafe import Markup

import config as C
import sun
from formats import clock, drop_codewords, month_label, money

os.umask(0o077)
import nodump
nodump.harden()
import safe_media
safe_media.restrict_pillow()

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CRM_DB", ROOT / "data" / "crm.db"))

def static_v(filename):

    try:
        return int((ROOT / "static" / filename).stat().st_mtime)
    except OSError:
        return 0
PORT = int(os.environ.get("CRM_PORT", "8780"))

_DEFAULT_NETS = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"]
ALLOW = [ipaddress.ip_network(n.strip()) for n in
         (os.environ.get("CRM_ALLOW_ONLY", "").split(",") if os.environ.get("CRM_ALLOW_ONLY") else _DEFAULT_NETS)
         + [n for n in os.environ.get("CRM_ALLOW", "").split(",") if n.strip()] if n.strip()]

import netsec
if netsec.startup_problem():
    raise SystemExit(netsec.startup_problem())
from flask import Request as _Request

class _MemRequest(_Request):

    def _get_file_stream(self, total_content_length=None, content_type=None, filename=None, content_length=None):
        import io as _io
        return _io.BytesIO()

app = Flask(__name__)
app.request_class = _MemRequest
app.wsgi_app = netsec.ProxyGate(app.wsgi_app)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.jinja_env.globals["static_v"] = static_v
def _fee_short(label):

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
app.jinja_env.filters["dep"] = lambda t: C.dw(t)
app.jinja_env.filters["clock"] = clock
app.jinja_env.filters["month"] = month_label
def credit_on():
    return setting("credit_off") != "1"

app.jinja_env.globals["credit_on"] = credit_on
app.jinja_env.filters["oneline"] = lambda t: ", ".join(l_.strip() for l_ in (t or "").splitlines() if l_.strip())
app.jinja_env.filters["todo"] = lambda label: todo_word(label)
app.jinja_env.globals["plan_head_label"] = lambda sid: plan_head_label(sid)
app.jinja_env.filters["stage_index"] = lambda st: C.STAGES.index(st)
app.jinja_env.globals["setting"] = lambda k, d="": setting(k, d)
app.jinja_env.globals["books_open_from"] = lambda: books_open_from()
app.jinja_env.globals["year_closed"] = lambda y_: year_closed(y_)
app.jinja_env.globals["correction_words"] = lambda t_, r_: correction_words(t_, r_)

def settings_find_entries():

    import settings_index
    return settings_index.entries(C.SESSION_TYPES, url_for, settings_index.help_words())

def settings_find(term):
    import settings_index
    return settings_index.find(term or "", C.SESSION_TYPES, url_for, settings_index.help_words())

app.jinja_env.globals["settings_find_entries"] = settings_find_entries
app.jinja_env.globals["settings_find"] = settings_find

def _settings_page_href(p_):

    if p_.get("plugin"):
        if plugin_on(p_["plugin"]) and p_["ep"] in app.view_functions:
            return url_for(p_["ep"], **p_["args"])
        return url_for("plugin_page", name=p_["plugin"])
    return url_for(p_["ep"], **p_["args"])

def _settings_page_shown(p_, here):
    if p_.get("plugin"):

        return p_["plugin"] in DISABLED_PLUGINS or any(x_["name"] == p_["plugin"] for x_ in PLUGINS_LOADED)
    if p_.get("show") == "transfer":
        return bool([t_ for t_ in C.SESSION_TYPES if t_ not in C.LICENSE_TYPES]) or here
    return True

def settings_nav():

    import settings_registry as R_
    ep_, va_, args_ = request.endpoint, request.view_args or {}, _settings_args()
    out = []
    for g_ in R_.GROUPS:
        rows = []
        for p_ in g_["pages"]:
            on = R_.matches(p_, ep_, va_, args_)
            if not _settings_page_shown(p_, on):
                continue
            kids = []
            if p_["id"] == "shoot":
                kids = [dict(key=k_, label=l_, href=url_for("type_edit", key=k_),
                             on=ep_ in ("type_edit", "type_walk") and va_.get("key") == k_)
                        for k_, l_ in C.SESSION_TYPES.items()]
            rows.append(dict(label=p_["label"], sub=p_.get("sub", ""), href=_settings_page_href(p_), on=on, children=kids))
        if g_["id"] == "app":
            known = {p_["ep"] for p_ in R_.pages()}
            for l_, e_, s_ in hooks.settings_tabs:
                if e_ not in known:
                    rows.insert(1, dict(label=l_, sub=s_, href=url_for(e_), on=ep_ == e_, children=[]))
        out.append(dict(title=g_["title"], pages=rows))
    return out

def settings_here():

    import settings_registry as R_
    try:
        return R_.current(request.endpoint, request.view_args or {}, _settings_args())
    except RuntimeError:
        return None

def _settings_args():

    a_ = request.args.to_dict()
    if request.endpoint == "settings_view" and not a_.get("tab"):
        a_["tab"] = "profile"
    return a_

def field_error(name):

    if "field_errors" not in g:
        g.field_errors = session.pop("field_errors", None) or {}
    return g.field_errors.get(name)

def _field_refused(name, words):
    session.setdefault("field_errors", {})[name] = words
    session.modified = True

def settings_moved():

    import settings_registry as R_
    out = {}
    for f_ in R_.fields():
        p_ = f_["page"]
        if f_.get("anchor") and f_["anchor"] not in out and not p_.get("plugin"):
            try:
                out[f_["anchor"]] = url_for(p_["ep"], **p_["args"]) + "#" + f_["anchor"]
            except Exception:
                continue
    return out

app.jinja_env.globals["settings_moved"] = settings_moved
app.jinja_env.globals["has_endpoint"] = lambda ep_: ep_ in app.view_functions
app.jinja_env.globals["settings_nav"] = settings_nav
app.jinja_env.globals["settings_here"] = settings_here
app.jinja_env.globals["field_error"] = field_error

def setup_done():
    try:
        return setting("setup_complete") == "1"
    except Exception:
        return False

SETTINGS_ENDPOINTS = {"settings_view", "wording_edit", "type_edit", "type_walk", "vault_view", "security", "master_key_check", "setup_step", "about",
                      "plugins_settings", "plugin_page"}
app.jinja_env.globals["setup_done"] = setup_done
app.jinja_env.globals["SETTINGS_ENDPOINTS"] = SETTINGS_ENDPOINTS
app.jinja_env.globals["plugin_on"] = lambda name_: plugin_on(name_)

NAV = [("today", "Today", "home"), ("clients", "Clients", "clients"), ("sessions", "Sessions", "sessions"),
       ("calendar", "Calendar", "month_view"), ("finances", "Finances", "money_view")]
NAV_SECTION = {"home": "today",
               "clients": "clients", "client_view": "clients", "client_stage": "clients", "contact_new": "clients",
               "sessions": "sessions", "pipeline": "sessions", "session_view": "sessions", "session_edit": "sessions", "inquiry": "sessions",
               "events": "sessions", "event_view": "sessions",
               "month_view": "calendar", "year_view": "calendar",
               "money_view": "finances", "fees_view": "finances"}
NAV_PHONE = ("today", "clients", "sessions")
NAV_MORE = []

def add_nav(key, label, endpoint, after, shown, pages=()):

    NAV_MORE.append(dict(key=key, label=label, endpoint=endpoint, after=after, shown=shown))
    NAV_SECTION.update({p_: key for p_ in (endpoint, *pages)})

def nav_items():

    out = list(NAV)
    for n_ in NAV_MORE:
        try:
            if not n_["shown"]():
                continue
        except Exception:
            continue
        at = next((i_ + 1 for i_, (k_, _, _) in enumerate(out) if k_ == n_["after"]), len(out))
        out.insert(at, (n_["key"], n_["label"], n_["endpoint"]))
    return out

app.config["add_nav"] = add_nav
app.jinja_env.globals["NAV"] = NAV
app.jinja_env.globals["nav_items"] = nav_items
app.jinja_env.globals["NAV_PHONE"] = NAV_PHONE
app.jinja_env.globals["NAV_SECTION"] = NAV_SECTION
C.galleries_on = lambda: bool(setting("gallery_base_url"))
def _setting_anywhere(k):
    try:
        return setting(k)
    except RuntimeError:
        try:
            with app.app_context():
                return setting(k)
        except Exception:
            return ""

C.pay_details = lambda: _setting_anywhere("pay_details")
C.deposit_word = lambda: _setting_anywhere("deposit_word")
C.mileage_rate = lambda: _setting_anywhere("mileage_rate")

def contact_bits():

    return [b for b in (setting("business_email").strip(), setting("business_phone").strip(),
                        ", ".join(l_.strip() for l_ in setting("business_address").splitlines() if l_.strip())) if b]

app.jinja_env.globals["contact_bits"] = contact_bits

_SIDE_KEYS = "CREATE TABLE IF NOT EXISTS side_keys (name TEXT PRIMARY KEY, key TEXT NOT NULL)"
_ID_TABLE = "CREATE TABLE IF NOT EXISTS id_marks (name TEXT PRIMARY KEY, n INTEGER NOT NULL)"

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
  stage TEXT NOT NULL, at TEXT DEFAULT (date('now','localtime')), era TEXT);
CREATE TABLE IF NOT EXISTS bill_snapshots (number TEXT PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, at TEXT, sig TEXT, data TEXT NOT NULL, html TEXT);
CREATE INDEX IF NOT EXISTS bill_snapshots_sid ON bill_snapshots(session_id);
CREATE TABLE IF NOT EXISTS refund_credits (payment_id INTEGER PRIMARY KEY REFERENCES payments(id) ON DELETE CASCADE,
  session_id INTEGER, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS quote_marks (vid INTEGER PRIMARY KEY REFERENCES quote_versions(id) ON DELETE CASCADE,
  session_id INTEGER, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS write_offs (id INTEGER PRIMARY KEY, session_id INTEGER, amount REAL NOT NULL, at TEXT NOT NULL,
  why TEXT NOT NULL DEFAULT 'waived');
CREATE TABLE IF NOT EXISTS deposit_credits (id INTEGER PRIMARY KEY,
  household_id INTEGER REFERENCES households(id) ON DELETE CASCADE,
  from_session INTEGER REFERENCES sessions(id) ON DELETE CASCADE, amount REAL NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credit_uses (id INTEGER PRIMARY KEY, credit_id INTEGER REFERENCES deposit_credits(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, amount REAL NOT NULL, at TEXT NOT NULL,
  refund_id INTEGER, payment_id INTEGER);
CREATE TABLE IF NOT EXISTS tax_adjustments (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  at TEXT NOT NULL, tax REAL NOT NULL, taxable REAL NOT NULL DEFAULT 0, why TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS charges (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  at TEXT NOT NULL, amount REAL NOT NULL, number TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS client_labels (household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  label TEXT NOT NULL, PRIMARY KEY (household_id, label));
CREATE TABLE IF NOT EXISTS session_taxes (session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  key TEXT NOT NULL, name TEXT, rate REAL, included INTEGER DEFAULT 0, pick INTEGER, PRIMARY KEY (session_id, key));
CREATE TABLE IF NOT EXISTS client_end_contact (household_id INTEGER PRIMARY KEY REFERENCES households(id) ON DELETE CASCADE,
  since TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quote_replacements (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  at TEXT NOT NULL, old_vid INTEGER, old_total REAL NOT NULL DEFAULT 0, old_lines TEXT NOT NULL DEFAULT '[]',
  agreement TEXT NOT NULL DEFAULT '[]', new_vid INTEGER, booked_at TEXT);
""" + _SIDE_KEYS + ";\n" + _ID_TABLE + ";\n"

def db():
    if "db" not in g:
        g.db = connect()
    return g.db

def connect():

    return dbmod.connect(DB_PATH)

UNLOCKED_POSTS = {"photos_upload", "photos_status"}

@app.before_request
def _one_write_at_a_time():

    if request.method == "POST" and request.endpoint not in UNLOCKED_POSTS:
        import locks
        if not locks.WRITES.acquire(timeout=45):
            return "Busy finishing another change. Try again in a moment.", 503
        g.write_lock = True
        g.tx = True

@app.teardown_request
def _release_write(_exc):
    if g.pop("tx", None) and "db" in g:
        try:
            g.db.rollback()
            if g.pop("applied", None):
                apply_settings()
        except Exception:
            pass
    if g.pop("write_lock", None):
        import locks
        locks.WRITES.release()
    if "crm.jobs_after" in request.environ:
        import jobs
        jobs.run_after(request.environ)

@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()

def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows

def _commit():

    if not g.get("tx"):
        db().commit()
    for k in ("wf_cache", "tot_cache", "needs_you", "wf_pre", "wf_now"):
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

    try:
        n = float(str(v).replace(",", "").replace("$", "").strip()) if v not in (None, "") else default
    except ValueError:
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, n))

def safe_back(target):

    from urllib.parse import urlsplit
    t = (target or "").strip()
    if not t.startswith("/") or t.startswith("//") or "\\" in t or any(ord(c) < 32 or ord(c) == 127 for c in t):
        return None
    u = urlsplit(t)
    return t if not u.scheme and not u.netloc else None

def trailing_age(s, commas=True, newline_ok=False):

    sep = (lambda c: c.isspace() or c in ",(") if commas else (lambda c: c.isspace() or c == "(")
    t = s[:-1] if s.endswith("\n") else s
    t = t[:-1] if t.endswith(")") else t
    if commas:
        t = t.rstrip()
    k = len(t)
    while k > 0 and t[k - 1].isdecimal():
        k -= 1
    if not 1 <= len(t) - k <= 2:
        return None
    j = k
    while j > 0 and sep(t[j - 1]):
        j -= 1
    return (t[:j], t[k:]) if j < k and (newline_ok or "\n" not in t[:j]) else None

def today():
    return datetime.now(TZ).date()

class Hooks:

    def __init__(self):
        self.nav = []
        self.session_panels = []
        self.session_tabs = []
        self.today = []
        self.alerts = []
        self.steps = []
        self.step_actions = []
        self.stage_blocks = []
        self.step_notes = []
        self.session_actions = []

        self.client_checks = []

        self.client_online = []
        self.on = {"session_created": [], "stage_changed": [], "gallery_window_changed": []}
        self.guards = {}
        self.detail_locations = []
        self.search = []
        self.step_icons = []
        self.step_labels = {}
        self.settings_tabs = []
        self.type_panels = []

        self.settings_parts = {}

        self.tax_rules = []

        self.labs = []

    def fire(self, name, *args):
        for fn in self.on.get(name, []):
            fn(*args)

    def guard(self, action, proceed, **details):

        return next((r for r in (fn(proceed, **details) for fn in self.guards.get(action, [])) if r), False)

hooks = Hooks()

def _stale_clients():

    cutoff = (today() - timedelta(days=365 * C.CLOSED_CLIENT_YEARS)).isoformat()
    rows = q("""SELECT h.id, h.name FROM households h WHERE COALESCE(h.demo, 0)=0
                AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.household_id=h.id AND (s.stage NOT IN ('closed','lost')
                                OR COALESCE(s.shoot_date, s.created, '') > ?))
                AND EXISTS (SELECT 1 FROM sessions s WHERE s.household_id=h.id)""", (cutoff,))
    return [dict(text=f"{r['name']}: nothing open for {C.CLOSED_CLIENT_YEARS} years. Review and delete their record?",
                 link=url_for("client_view", hid=r["id"]) + "#their-data") for r in rows]

hooks.today.append(_stale_clients)

def _year_closing():

    t_ = today()
    last = t_.year - 1
    if t_.month != 1 or t_.day < 25 or year_closed(last):
        return []
    return [dict(text=f"{last} closes on or before January 31: enter any {last} money first.",
                 link=url_for("money_view", year=last) + "#year", when=f"{t_.year}-01-31")]

hooks.today.append(_year_closing)

UNLOCK_HOOKS = []
AFTER_UNLOCK_HOOKS = []

class PluginContext:
    def __init__(self, name):
        self.name = name
        self.app, self.hooks, self.q, self.x = app, hooks, q, x
        self.render = render_template
        self.C, self.nice = C, nice
        self.audit, self.advance, self.connect = audit, advance, connect

        self.inbox_arrived = lambda kind, n=1: inboxnotice.arrived(db(), kind, n)
        self.add_column = dbmod.add_column
        self.safe_back = safe_back
        self.release_blocked, self.money = release_blocked, money
        self.refund_hold_state = lambda sid: refund_hold_state(sid)
        self.deposit_held = lambda sid: deposit_held(sid)
        self.licensed = licensed
        self.html_pdf = lambda html_, name_: html_pdf(html_, name_)
        self.next_id = lambda table_: next_id(table_)
        self.create_session = lambda *a_, **k_: create_session(*a_, **k_)
        self.now_local = lambda: now_local()
        self.setting = lambda k_, d_="": setting(k_, d_)
        self.closed_year_refusal = lambda day_: closed_year_refusal(day_)
        self.year_closed = lambda y_: year_closed(y_)
        self.closed_year_change_refusal = lambda day_, what_="payment": closed_year_change_refusal(day_, what_)
        self.correction_words = lambda t_, r_: correction_words(t_, r_)
        self.correction_in = lambda f_: correction_in(f_)
        self.taxes = lambda: taxes_list()
        self.tax_pct_at = lambda address_="", included_=False: tax_pct_at(address_, included_)
        self.doc_number = lambda sid_, kind_, prefix_="INV": doc_number(sid_, kind_, prefix_)
        self.long_date = lambda iso_: long_date(iso_)
        self.detail_stops = lambda s_: detail_stops(s_)
        self.couple_of = lambda hid_, type_: couple_of(hid_, type_)
        self.client_people = lambda hid_, type_, email=False: client_people(hid_, type_, email)

    @staticmethod
    def csp(extra=""):

        return CSP.format(extra=extra)

    def migrate(self, sql):

        def run():
            con = connect()
            con.executescript(sql)
            con.commit()
            con.close()
        UNLOCK_HOOKS.append(run)

    def on_unlock(self, fn):

        UNLOCK_HOOKS.append(fn)
        return fn

    def switchable(self, default=False, label="", about="", before_off=None, when_on=None, links=()):

        SWITCHES[self.name] = dict(default=default, label=label or self.name, about=about, before_off=before_off,
                                   when_on=when_on, links=list(links))

    def active(self):
        return plugin_on(self.name)

    @staticmethod
    def plugin_on(name):
        return plugin_on(name)

    def after_unlock(self, fn):

        AFTER_UNLOCK_HOOKS.append(fn)
        return fn

    @staticmethod
    def unlocked():
        return datakey.is_unlocked()

PLUGIN_DIRS = [ROOT / "plugins"] + [Path(p_) for p_ in os.environ.get("CRM_PLUGIN_DIRS", "").split(os.pathsep) if p_.strip()]

CORE_PLUGINS = {"retention", "print_core"}
DISABLED_PLUGINS = {n.strip() for n in os.environ.get("CRM_PLUGINS_OFF", "").split(",") if n.strip()} - CORE_PLUGINS

SWITCHES = {}

def plugin_on(name):

    if name in DISABLED_PLUGINS or not any(p_["name"] == name for p_ in PLUGINS_LOADED):
        return False
    sw = SWITCHES.get(name)
    if not sw:
        return True
    try:
        v = setting(f"plugin_on_{name}")
    except Exception:
        v = ""
    return v == "1" if v in ("0", "1") else bool(sw["default"])

def load_plugins():

    loaded = []
    for path in (p_ for d_ in PLUGIN_DIRS if d_.is_dir() for p_ in sorted(d_.glob("*.py"))):
        if path.name.startswith("_") or path.stem in DISABLED_PLUGINS or path.stem in loaded:
            continue
        if path.stem in CORE_PLUGINS and path.parent != ROOT / "plugins":
            continue
        spec = importlib.util.spec_from_file_location(f"plugins.{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            mod.register(PluginContext(path.stem))
        except Exception as e:
            if path.parent == ROOT / "plugins":
                raise
            app.logger.exception("plugin %s failed to load", path)
            PLUGIN_ERRORS.append(f"{path.name}: {type(e).__name__}")
            continue
        loaded.append(path.stem)
        PLUGINS_LOADED.append(dict(name=path.stem, built_in=path.parent == ROOT / "plugins", folder=str(path.parent),
                                   label=f"{path.parent.name}/{path.stem}"))
    return loaded

PLUGIN_ERRORS = []
PLUGINS_LOADED = []

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
TZ = ZoneInfo("UTC")
HOST_TZ = os.environ.get("TZ", "")

def _audit_zone_note():

    try:
        was = setting("audit_zone_now")
        if was == TZ.key:
            return
        last = q("SELECT COALESCE(MAX(id), 0) n FROM audit", one=True)["n"]
        if not was:
            _put_setting("audit_zone_start", str(last))
        elif last:
            marks = json.loads(setting("audit_zones") or "[]")
            marks.append([last, was])
            _put_setting("audit_zones", json.dumps(marks))
        _put_setting("audit_zone_now", TZ.key)
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        pass

def audit(action, sid=None, hid=None, eid=None, note="", who="Owner", route="", data=""):
    _audit_zone_note()
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

def _stage_of(sid):
    r = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True) if sid else None
    return r["stage"] if r else None

def is_id(v):

    return isinstance(v, str) and v.isascii() and v.isdigit()

def describe(ep, va, f):

    sid, hid, eid = va.get("sid"), va.get("hid"), va.get("eid")
    act = None
    if ep == "task_toggle":
        t = _title(va["tid"])
        if t:
            sid, eid = t["session_id"], t["event_id"] or eid
            title_ = step_says(t["title"])
            act = (f"Removed step '{title_}'" if f.get("delete") else
                   f"Reopened '{title_}'" if t["done"] else f"Marked '{title_}' done")
    elif ep == "quote_mark_sent":
        r_ = q("SELECT session_id FROM quote_versions WHERE id=?", (va["vid"],), one=True)
        sid, act = (r_["session_id"] if r_ else None), "Marked the quote sent"
    elif ep == "close_step":
        outcome = OUTCOMES.get(f.get("outcome"), f.get("outcome"))
        said = (f.get("said_at") or "").replace("T", " ")
        if said:
            g.said_prefix = f"Said {nice(said[:10])}, {clock(said[11:16])}. "
        if f.get("task"):
            t = _title(int(f["task"])) if is_id(f["task"]) else None
            if t:
                sid, eid = t["session_id"], t["event_id"]
                act = f"Closed '{t['title']}' as {outcome}"
        elif is_id(f.get("sid")):
            sid = int(f["sid"])
            act = f"Closed '{AUTO.get(f.get('step'), (f.get('step'),))[0]}' as {outcome}"
    elif ep == "quick":
        a = f.get("act")
        signer_ = (q("""SELECT p.name FROM documents d JOIN people p ON p.id=d.about_person WHERE d.id=?""",
                     (int(f["did"]),), one=True) if str(f.get("did") or "").isdigit() else None)
        act = {"sign": f"Marked the {f.get('kind', 'release')} signed" + (f" by {signer_['name']}" if signer_ else ""),
               "mark": f"Marked '{MARKABLE.get(f.get('field'), f.get('field'))}' for today",
               "pay": f"Recorded the balance paid ({money(totals(sid)['due']) if sid else ''})",
               "extend": "Gave the gallery its one extension",
               "firm": "Confirmed the shoot happened on its tentative date",
               "stage": None if _forward(_stage_of(sid), f.get("stage")) else f"Moved to {C.STAGE_LABELS.get(f.get('stage'), f.get('stage'))}",
               "book": None, "shot": None,
               "rebook": "",
               "date": (f"Moved the shoot from {nice(_old_date(sid))} to {nice(f.get('shoot_date'))}" if _old_date(sid) else
                        f"Set the shoot date to {nice(f.get('shoot_date'))}") + (f", {clock(f.get('start_time'))}" if f.get("start_time") else ""),
               "favorites": f"Recorded the client's portfolio suggestions: {f.get('favorites', '')}" + (f" (don't show: {f['veto']})" if f.get("veto") else "")}.get(a)
    elif ep == "docs_mark_sent":
        act = f"Marked the {'agreement' if request.values.get('kind') == 'agreement' else 'release'} sent"
    elif ep == "deposit_invoice_sent":
        act = "Marked the deposit invoice sent"
    elif ep in ("quote_fee", "line_qty", "quote_discount"):
        if ep == "line_qty":
            l_ = q("SELECT * FROM lines WHERE id=?", (va["lid"],), one=True)
            sid = l_["session_id"] if l_ else None
            if l_:
                act = (f"Changed the quantity of '{re.sub(r' x [0-9.]+$', '', l_['label'])}' from {(l_['qty'] or 1):g} to "
                       f"{num(f.get('qty'), 1.0):g}")
        elif ep == "quote_fee":
            fk_ = f.get("fee_key") or ""
            if fk_ == "custom":
                lab_ = (f.get("label") or "").strip()
            else:
                qt_ = q("SELECT schedule_id FROM quotes WHERE session_id=?", (sid,), one=True)
                sch_ = (schedule(qt_["schedule_id"]) if qt_ else None) or current_schedule()
                lab_ = next((i_["label"] for i_ in sch_["items"] if i_["key"] == fk_), fk_)
            qn_ = f.get("qty") or "1"
            act = f"Added '{lab_}'" + (f" x {qn_}" if qn_ not in ("1", "1.0", "") else "") + " to the price" if lab_ else None
        else:
            act = f"Set the discount to {f.get('pct') or 0}%" + (f" ({f['reason']})" if f.get("reason") else "")
        st_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True) if sid else None
        if act and st_ and st_["stage"] not in ("inquiry", "quoted"):
            act += f", after booking (at {C.STAGE_LABELS.get(st_['stage'], st_['stage'])})"
    elif ep == "session_stage":
        act = None if _forward(_stage_of(sid), f.get("stage")) else f"Moved to {C.STAGE_LABELS.get(f.get('stage'), f.get('stage'))}"
        if f.get("stage") == "lost" and _stage_of(sid) in ("inquiry", "quoted"):
            why_ = f.get("lost_reason") if f.get("lost_reason") in C.NOT_BOOKED_REASONS else ""
            words_ = (f.get("lost_note") or "").strip()[:200]
            act = "Did not book" + (f": {why_}" if why_ else "") + (f" ({words_})" if words_ else "")
        elif f.get("stage") == "lost" and f.get("need_reason") and _stage_of(sid) not in ("lost", "closed"):
            lab_ = dict(C.CANCEL_REASONS).get(f.get("lost_reason"), "")
            words_ = (f.get("lost_note") or "").strip()[:200]
            act = "Canceled the booking" + (f": {lab_}" if lab_ else "") + (f" ({words_})" if words_ else "")
        elif f.get("stage") == "lost" and _stage_of(sid) == "lost" and f.get("lost_reason") in C.NOT_BOOKED_REASONS:
            act = f"Why it was not booked: {f['lost_reason']}"
        elif _stage_of(sid) == "lost" and f.get("stage") in ("inquiry", "quoted"):
            act = f"Undid Do not book: back at {C.STAGE_LABELS[f['stage']]}"
    elif ep == "session_pencil":
        s_ = q("SELECT shoot_date, pencil_until FROM sessions WHERE id=?", (sid,), one=True)
        day_ = nice((s_["shoot_date"] or "")[:10]) if s_ else ""
        a_ = f.get("act")
        if a_ == "release":
            act = f"Released the penciled date, {day_}" if s_ and s_["pencil_until"] else None
        elif a_ == "extend" and s_ and s_["pencil_until"]:
            from_ = max(d(s_["pencil_until"][:10]), today())
            act = f"Extended the hold on {day_} to {nice((from_ + timedelta(days=C.PENCIL_DAYS)).isoformat())}"
        elif a_ in ("pencil", "until"):
            until_ = f.get("until") or (today() + timedelta(days=C.PENCIL_DAYS)).isoformat()
            act = (f"Penciled in {day_}" if a_ == "pencil" else f"Changed the hold on {day_}") + f", held until {nice(until_)}"
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
        dd = q("SELECT d.*, COALESCE(NULLIF(d.covers, ''), p.name) person FROM documents d LEFT JOIN people p ON p.id=d.about_person WHERE d.id=?", (va["did"],), one=True)
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

               "payment": f"Recorded a {money(num(f.get('amount')))} "
                          + (f"{f['method']} " if f.get("method") and f.get("method") != "other" else "")
                          + {"deposit": "deposit", "refund": "refund"}.get(f.get("kind"), "payment") + (
                   f", other: {' '.join((f.get('note') or '').split())[:80]}, at that value" if f.get("method") == "other" else "")
                          + (f" #{f['ref'].strip()}" if (f.get("ref") or "").strip() else ""),
               "expense": f"Added expense '{f.get('label')}'" + (f" {f.get('miles')} mi" if f.get("miles") else "") + (f" {money(num(f['amount']))}" if f.get("amount") else "")}.get(w)
    elif ep == "session_after_refund":
        act = {"release": "Released the gallery held after a refund: it releases once nothing is owed",
               "hold": "Held the gallery again after a refund: it stays held until paid again"}.get(f.get("choice"))
    elif ep == "money_delete":
        table = {"line": "lines", "payment": "payments", "expense": "expenses"}.get(va.get("what"))
        r = q("SELECT * FROM " + dbmod.ident(table) + " WHERE id=?", (va["rid"],), one=True) if table else None
        if r:
            sid = r["session_id"]
            if table == "payments":
                want_ = f.get("set")
                void_ = (want_ == "void") if want_ in ("void", "restore") else not r["voided"]
                act = f"{'Voided' if void_ else 'Restored'} the {money(abs(r['amount']))} {r['kind'] or 'payment'}" + (
                    f" from {nice(r['received'])}" if r["received"] else "")
            else:
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
    elif ep == "bill_issue":
        act = f"Issued the {va.get('kind', 'invoice')}"
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
    elif ep == "session_subject_add":
        p_ = q("SELECT name FROM people WHERE id=?", (int(f["person_id"]),), one=True) if str(f.get("person_id") or "").isdigit() else None
        act = f"Added {p_['name']} to the session" if p_ else "Added a person to the session"
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
        w_ = app.config.get("ep_words", {}).get(ep)
        act = EP_WORDS.get(ep) or (w_(f) if callable(w_) else w_) or ep.replace("_", " ").capitalize()
    return act, sid, hid, eid

EP_WORDS = {"quote_license": "Changed the license terms on the quote", "quote_send": "Prepared the quote to send", "quote_fee": "Added a fee to the quote", "quote_discount": "Set the discount", "type_walk": "Walked through a session type's settings", "type_steps_reset": "Reset a session type's steps to the defaults", "type_walk_approve": "Approved a session type's terms at the end of the walk-through", "portfolio_set": "Changed portfolio permission",
            "quote_requote": "Requoted at current fees", "quote_exempt": "Changed the taxes on the quote",
            "upload_gallery_req": "Asked to upload the gallery", "build_gallery": "Started building the gallery",
            "gallery_sync_now": "Checked the gallery", "retention_raw": "Picked the RAW folder",
            "retention_keep": "Kept the RAWs another year", "vault_new": "Started creating the vault master key", "retention_seal": "Encrypted the readable copies for editing",
            "retention_unseal": "Opened the sealed photos for a reprint",
            "vault_confirm": "Confirmed the vault master key was saved", "vault_schedule": "Changed the daily backup time", "retention_early": "Deleted the photos early at the client's written request", "overhead_add": "Added a business expense",
            "quick": "Updated the session", "settings_import": "Imported settings from a file", "setup_hide_list": "Hid the finish-setting-up list", "contact_new": "Added a contact", "settings_reset": "Reset the database",
            "unconfirmed_keep": "Kept clients named in deletions that could not be confirmed",
            "master_key_later": "Put off checking the master key for a week", "client_check_in": "Checked in by email (a draft)",
            "session_codeword": "Added the gallery codeword"}

QUIET_ENDPOINTS = {"client_check_now", "login", "login_2fa", "dev_components_post", "idle_stay", "gallery_status", "client_delete", "session_delete", "unconfirmed_apply", "vault_check", "vault_backup_now", "wording_edit",
                   "master_key_check", "calendar_link_settings"}

@app.after_request
def _commit_request(resp):

    if g.pop("tx", None) and "db" in g:
        if resp.status_code < 400:
            try:
                _snapshot_signed()
            except Exception:
                app.logger.exception("signed-document snapshot")
            g.db.commit()
            _once_used()
        else:
            g.db.rollback()
            if g.pop("applied", None):
                apply_settings()
    if g.pop("sync_open_after", None):
        g.pop("tx", None)
        try:
            sync_open_sessions()
            db().commit()
        except Exception:
            db().rollback()
            app.logger.exception("bringing open sessions in line with new rules")
    return resp

def _snapshot_signed():

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

    r_ = q("SELECT data FROM doc_snapshots WHERE kind=? AND ref=?", (kind, ref), one=True)
    if r_ and '"legacy"' in r_["data"]:
        v = dict(v)
        v["legacy_signed"] = True
    return v

def signed_copy(kind, ref):

    r_ = q("SELECT data FROM doc_snapshots WHERE kind=? AND ref=?", (kind, ref), one=True)
    if not r_:
        return None
    v = json.loads(r_["data"])
    if v.get("legacy"):
        return None
    v["custom"] = Markup(v.get("custom") or "")
    v["unsigned"] = False
    return v

DATE_FIELDS = ("shoot_date", "received", "signed_on", "on", "date", "gallery_built", "letter_mailed", "first_visit",
               "sd_shipped", "asked_on", "due", "event_date")

@app.before_request
def _session_exists():

    if not datakey.is_unlocked() or request.endpoint in (None, "static"):
        return None
    va_, f_ = request.view_args or {}, (request.form if request.method == "POST" else {})
    asks = [("sessions", va_.get("sid")), ("households", va_.get("hid")), ("tasks", va_.get("tid")), ("events", va_.get("eid"))]
    if request.method == "POST":
        asks += [("sessions", f_.get("sid")), ("sessions", f_.get("session_id")), ("households", f_.get("household_id"))]
        if request.endpoint == "close_step":
            asks.append(("tasks", f_.get("task")))
    elif request.endpoint not in ("session_view", "session_edit", "client_view", "client_stage", "event_view"):
        return None
    for table_, id_ in asks:
        if id_ is None or not is_id(str(id_)):
            continue
        if not q("SELECT 1 FROM " + dbmod.ident(table_) + " WHERE id=?", (int(id_),), one=True):
            if 0 < int(id_) <= id_mark(table_):
                g.refused = True
                return render_template("gone.html", what={"sessions": "session", "households": "client", "tasks": "step",
                                                          "events": "event"}[table_]), 410
            abort(404)
    return None

@app.before_request
def _dates_are_real():

    if request.method != "POST" or not request.form or not _allowed(request.remote_addr) or not _same_origin():
        return None
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
def _fill_taxes_first():

    if request.method != "POST" or not datakey.is_unlocked() or request.endpoint in (None, "static"):
        return None
    try:
        if q("SELECT 1 FROM payments WHERE tax IS NULL AND voided IS NULL LIMIT 1", one=True):
            _fill_all_payment_taxes()
            db().commit()

    except sqlite3.Error:
        pass
    return None

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
def _mark_refused_notice(resp):

    if g.get("refused"):
        from flask import session as _fs
        if _fs.get("notice"):
            _fs["notice_refused"] = True
    return resp

@app.after_request
def _audit_after(resp):
    a = g.pop("audit", None)
    if a:
        g.touched_sid = a[1]
    if a and a[0] and resp.status_code < 400 and not g.get("refused"):
        act, sid, hid, eid, note = a
        note = g.pop("audit_note", None) or note
        if request.endpoint == "inquiry" and not hid:
            r = q("SELECT id, household_id FROM sessions ORDER BY id DESC LIMIT 1", one=True)
            sid, hid = (r["id"], r["household_id"]) if r else (None, None)
        try:
            audit(act, sid, hid, eid, note=note, route=request.path, data=g.pop("audit_data", "") or "")
        except Exception:
            app.logger.exception("audit write failed")
    for line_ in g.pop("after_audit", []):
        if resp.status_code < 400:
            try:
                audit(line_["action"], line_["sid"], note=line_["note"], who=line_["who"], route=request.path)
            except Exception:
                app.logger.exception("audit write failed")
    return _advance_after_post(resp)

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
    if request.method == "GET" and request.endpoint in WRITING_GETS | app.config.get("writing_gets", set()) and not _same_origin():
        abort(403)

WRITING_GETS = {"client_export", "photo_download", "money_accounts", "settings_export", "letter_qr"}

def _same_origin():

    sfs = request.headers.get("Sec-Fetch-Site")
    if sfs:
        return sfs in ("same-origin", "none")
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if src and src != "null":
        from urllib.parse import urlsplit
        return urlsplit(src).netloc == request.host
    return False

@app.context_processor
def globals_():
    def take_notice():

        from flask import session as _fs
        g.refill = _fs.pop("refill", None)
        g.notice_refused = _fs.pop("notice_refused", None)
        return _fs.pop("notice", None)

    def take_refill():

        return g.get("refill")

    def import_result():

        from flask import session as _fs
        r = _fs.get("import_result")
        if r and r.get("plugins") is None and not r.get("gallery") and not r.get("storage"):
            _fs.pop("import_result", None)
        return r
    return dict(take_notice=take_notice, take_refill=take_refill, import_result=import_result, C=C, hooks=hooks, today=today(), stage_label=C.STAGE_LABELS,
                type_label=C.SESSION_TYPES, type_icon=C.type_icon, money=money, nice=nice, light=light, face=face, clock=clock, month_label=month_label, OUTCOMES=OUTCOMES, sym_group=sym_group, delta3=timedelta(days=3),
                who_is=who_is, age_of=age_of, age_label=age_label, RELATIONS=RELATIONS, edit_stamp=edit_stamp,
                taxes_list=taxes_list, DOC_DISCLAIMER=__import__("wording").DISCLAIMER)

def light(dstr, stype=None, place=None):

    if not dstr or (stype and stype in C.INDOOR_TYPES):
        return None
    if not sun.HOME:
        return None
    spot = sun.place_of(place) if place is not None else None
    try:
        day_ = d(dstr[:10])
        g_, s_ = sun.evening(day_, spot[0], spot[1], spot[2]) if spot else sun.evening(day_)
    except ValueError:
        return None
    if not g_ or not s_:
        return None
    text = f"Golden hour {clock(g_)}, sunset {clock(s_)}"
    if spot and spot[2] and s_.utcoffset() != s_.astimezone(sun.TZ).utcoffset():
        text += " (local time at the shoot)"
    elif place is not None and not spot:
        z_ = (setting("home_zip") or "").strip()
        text += f" (times for home, ZIP {z_})" if z_ else " (times for home)"
    return dict(golden=clock(g_), sunset=clock(s_), text=text)

def shoot_place(s):

    keys = s.keys() if hasattr(s, "keys") else s
    try:
        intake_ = json.loads(s["intake"] or "{}") if "intake" in keys else {}
    except ValueError:
        intake_ = {}
    parts = [s["stops"] if "stops" in keys else "", str(intake_.get("venue") or ""), str(intake_.get("address") or "")]
    parts += [s[k_] for k_ in ("event_venue",) if k_ in keys]
    return "\n".join(p_ for p_ in parts if p_ and p_.strip())

FACES = DB_PATH.parent / "faces"
FACE_COLORS = ["#8a5a3c", "#536d4f", "#4d6887", "#7a5a86", "#7c622a", "#386d6b", "#8a4b5b", "#5a6170"]

def face(hid):

    cache = g.setdefault("face_cache", {})
    if hid not in cache:
        cache[hid] = _face(hid)
    return cache[hid]

def prefetch_faces():

    try:
        files = set(os.listdir(FACES))
    except OSError:
        files = set()
    people = {}
    for p_ in q("SELECT * FROM people ORDER BY household_id, role='parent' DESC, id"):
        people.setdefault(p_["household_id"], []).append(p_)
    g.face_pre = dict(files=files, people=people, names={r_[0]: r_[1] for r_ in q("SELECT id, name FROM households")})

def _face(hid):
    if not hid:
        return dict(url=None, initials="?", color=FACE_COLORS[0])
    pre = g.get("face_pre")
    f = FACES / f"{hid}.jpg"
    if (f"{hid}.jpg" in pre["files"]) if pre else f.exists():
        return dict(url=url_for("face_img", hid=hid) + f"?v={int(f.stat().st_mtime)}", initials="", color="")

    people_ = pre["people"].get(hid, []) if pre else \
        q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (hid,))
    hh_name = (lambda: pre["names"].get(hid)) if pre else \
        (lambda: (q("SELECT name FROM households WHERE id=?", (hid,), one=True) or {"name": None})["name"])
    p = household_contact(people_) or next((p_ for p_ in people_ if p_["role"] != "pet"), None)
    name = p["name"] if p else (hh_name() or "?")
    words = [w for w in name.replace("The ", "").split() if w[:1].isalpha()]
    if len(words) == 1:
        hh = hh_name() or ""
        hw = [w for w in hh.replace("The ", "").split() if w[:1].isalpha()]
        if hw and hw[0][0].lower() != words[0][0].lower():
            words.append(hw[0])
    initials = "".join(w[0] for w in (words[:1] + words[-1:] if len(words) > 1 else words)).upper() or "?"
    return dict(url=None, initials=initials, color=FACE_COLORS[hid % len(FACE_COLORS)])

RELATIONS = ["mother", "father", "daughter", "son", "grandmother", "grandfather", "wife", "husband", "partner",
             "sister", "brother", "aunt", "uncle", "guardian", "self", "friend", "planner", "pet", "other"]
def relations_in_use():

    try:
        own = [r_["v"] for r_ in q("""SELECT DISTINCT LOWER(TRIM(relation)) v FROM people
                                      WHERE TRIM(COALESCE(relation, '')) != '' ORDER BY v""")]
    except Exception:
        own = []
    return RELATIONS + [v_ for v_ in own if v_ not in RELATIONS]

app.jinja_env.globals["relations_in_use"] = relations_in_use

PARENT_REL = {"mother", "father", "grandmother", "grandfather", "guardian", "aunt", "uncle"}
CHILD_REL = {"daughter", "son"}
ANIMAL_REL = {"pet", "dog", "cat", "horse", "pony", "rabbit", "bird", "animal", "puppy", "kitten"}
GENDER_OF = {"mother": "F", "daughter": "F", "grandmother": "F", "wife": "F", "sister": "F", "aunt": "F",
             "father": "M", "son": "M", "grandfather": "M", "husband": "M", "brother": "M", "uncle": "M"}

def born_on(p):

    keys = p.keys() if hasattr(p, "keys") else p
    if "birth_date" in keys and p["birth_date"]:
        try:
            return date.fromisoformat(p["birth_date"][:10])
        except ValueError:
            return None
    return None

def age_on(born, day):

    return max(0, day.year - born.year - ((day.month, day.day) < (born.month, born.day)))

def as_day(v):

    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None

def minor_on(p, day=None):

    keys = p.keys() if hasattr(p, "keys") else p
    if ("role" in keys and p["role"] == "pet") or ("relation" in keys and (p["relation"] or "") in ANIMAL_REL):
        return False
    born = born_on(p)
    if born and ("minor_box" not in keys or p["minor_box"] is None):
        return age_on(born, as_day(day) or today()) < majority_age()
    return bool(p["is_minor"])

def age_of(p):

    keys = p.keys() if hasattr(p, "keys") else p
    if born_on(p):
        return age_on(born_on(p), today())
    if "birth_year" in keys and p["birth_year"]:
        return today().year - p["birth_year"]
    if p["grad_year"]:
        school_end = today().year + (1 if today().month >= 7 else 0)
        return 17 - (p["grad_year"] - school_end)
    return None

def age_label(p):

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

    try:
        return max(14, min(25, int(setting("age_majority") or 18)))
    except ValueError:
        return 18

app.jinja_env.globals["majority_age"] = majority_age
app.jinja_env.globals["minor_on"] = minor_on
app.jinja_env.globals["check_in_snooze"] = lambda: check_in_snooze()
app.jinja_env.globals["idle_hours"] = lambda: idle_hours()

def person_fields(f, cur=None):

    rel = (f.get("relation") or "").strip().lower() or ((cur["relation"] or "") if cur else "")
    gender = f.get("gender") or GENDER_OF.get(rel, "") or ((cur["gender"] or "") if cur else "")
    age = str(f.get("age") or "").strip()
    if age.isdigit() and cur is not None and age_of(cur) == int(age):
        by = cur["birth_year"]
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
    if role == "pet":
        return rel, gender, by, role, 0, approx
    if age.isdigit() and approx and abs(int(age) - maj) <= 2 and not f.get("minor_shown"):
        session["notice"] = f"About {age}: tick Minor if they are under {maj}. An approximate age doesn't decide it."
    return rel, gender, by, role, minor, approx

def _past_day(s):

    try:
        return date.fromisoformat(str(s)[:10]) <= today()
    except ValueError:
        return False

def now_local():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def nice(s):
    if not s:
        return ""
    try:
        dt = d(s[:10])
        return dt.strftime("%a %b %-d") + ("" if dt.year == today().year else dt.strftime(", %Y"))
    except ValueError:
        return s

def session_full(sid):
    s = _pre("sess", sid, None)
    if s is not None and s is not _NONE:
        return s
    s = q("""SELECT s.*, h.name AS household, h.address AS address, e.name AS event_name FROM sessions s
             JOIN households h ON h.id = s.household_id LEFT JOIN events e ON e.id = s.event_id
             WHERE s.id=?""", (sid,), one=True)
    if not s:
        abort(404)
    return s

def current_schedule():
    r = q("SELECT * FROM fee_schedules ORDER BY id DESC LIMIT 1", one=True)
    return dict(id=r["id"], created=r["created"], note=r["note"], items=json.loads(r["items"]))

def schedule(sched_id):
    r = q("SELECT * FROM fee_schedules WHERE id=?", (sched_id,), one=True)
    return dict(id=r["id"], created=r["created"], note=r["note"], items=json.loads(r["items"])) if r else None

PRICE_APPROVALS = "price_approvals"

def _price_approvals():
    try:
        v_ = json.loads(setting(PRICE_APPROVALS) or "{}")
        return v_ if isinstance(v_, dict) else {}
    except ValueError:
        return {}

def prices_approved(sched_id):
    return str(sched_id) in _price_approvals()

def sched_name(sched_id):

    a_ = _price_approvals().get(str(sched_id))
    if not a_:
        return "Sample prices"
    try:
        return "Prices as of " + d(a_["on"]).strftime("%b %-d, %Y")
    except (ValueError, KeyError, TypeError):
        return "Prices"

def sched_code(sched_id):

    return (_price_approvals().get(str(sched_id)) or {}).get("code", "")

app.jinja_env.globals.update(sched_name=sched_name, sched_code=sched_code, prices_approved=prices_approved)

def approve_prices(sched_id, on=None, quiet=False):

    ap_ = _price_approvals()
    if str(sched_id) in ap_:
        return False
    day = on or today().isoformat()
    day_ = day.replace("-", "")
    taken_ = {(v_ or {}).get("code", "") for v_ in ap_.values()}
    n_ = 1 + sum(1 for c_ in taken_ if c_.startswith(day_))
    while f"{day_}{n_:02d}" in taken_:
        n_ += 1
    ap_[str(sched_id)] = dict(on=day, code=f"{day_}{n_:02d}")
    _put_setting(PRICE_APPROVALS, json.dumps(ap_, sort_keys=True))
    if not quiet:
        audit(f"Approved the prices: {sched_name(sched_id)} ({ap_[str(sched_id)]['code']}); they print as yours from now on")
    return True

@app.post("/fees/<int:sched_id>/approve")
def fees_approve(sched_id):

    schedule(sched_id) or abort(404)
    if approve_prices(sched_id):
        session["notice"] = f"Approved: {sched_name(sched_id)}. They print as your prices from now on."
    return redirect(safe_back(request.form.get("back")) or url_for("fees_view", sched_id=sched_id))

app.config.setdefault("quiet_endpoints", set()).add("fees_approve")

def price_approval_upgrade():

    if setting(PRICE_APPROVALS) != "":
        return None

    used = [r_["sid_"] for r_ in q("""SELECT DISTINCT v.schedule_id AS sid_ FROM quote_versions v JOIN quote_marks m ON m.vid=v.id
                                     WHERE v.schedule_id IS NOT NULL
                                     UNION SELECT DISTINCT qt.schedule_id FROM quotes qt JOIN sessions s ON s.id=qt.session_id
                                     WHERE qt.schedule_id IS NOT NULL AND (s.stage IN ('booked','shot','delivered','closed')
                                       OR EXISTS (SELECT 1 FROM tasks t WHERE t.session_id=s.id AND t.title='Send quote'
                                                  AND t.done IS NOT NULL AND COALESCE(t.outcome, 'done')='done'))""") if r_["sid_"]]
    ap_ = {}
    if used:
        for r_ in q("SELECT id, created FROM fee_schedules WHERE id >= ? ORDER BY id", (min(used),)):
            day = (r_["created"] or today().isoformat())[:10]
            n_ = 1 + sum(1 for v_ in ap_.values() if v_["code"].startswith(day.replace("-", "")))
            ap_[str(r_["id"])] = dict(on=day, code=f"{day.replace('-', '')}{n_:02d}")
    _put_setting(PRICE_APPROVALS, json.dumps(ap_, sort_keys=True))
    if ap_:
        audit("Updated to 1.5: your prices were already used on a sent quote, so they count as approved", who="Lumen Aureum")
    return sorted(int(k_) for k_ in ap_)

def quote_term():

    v = setting("quote_term_days")
    return int(v) if str(v).isdigit() and 1 <= int(v) <= 365 else C.QUOTE_TERM_DAYS

app.jinja_env.globals["quote_term"] = quote_term

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

    _reapply_portfolio(sid)
    _reapply_discount(sid)
    reapply_tax(sid)
    _invoice_still_sent(sid)
    sync_charges(sid)

def _reapply_portfolio(sid, force=False):

    s_ = q("SELECT type, portfolio_no FROM sessions WHERE id=?", (sid,), one=True)
    if s_ and not force and not q(UNAGREED_SQL + " AND id=?", (s_["type"], sid), one=True):
        return
    x("DELETE FROM lines WHERE session_id=? AND kind='portfolio'", (sid,))
    rule = C.PORTFOLIO_DISCOUNT.get(s_["type"]) if s_ else None
    if not rule or s_["portfolio_no"]:
        return
    base = q("""SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?
                AND kind NOT IN ('discount','passthrough','tax','credit','portfolio') AND COALESCE(fee_key, '') != 'reschedule'""",
             (sid,), one=True)["v"]
    if base <= 0:
        return
    amt = round(min(base * rule[1] / 100 if rule[0] == "pct" else rule[1], base), 2)
    if amt > 0.005:
        x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'portfolio', 1)",
          (sid, f"Portfolio permission ({C.portfolio_discount_words(s_['type'])} off, before tax): I may show up to {C.MARKETING_PICKS}"
                f" photos; you may suggest favorites and say no to any before it is shown", -amt))
    g.pop("tot_cache", None)

@app.post("/session/<int:sid>/portfolio")
def portfolio_set(sid):

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
    _reapply_portfolio(sid, force=True)
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

    sub = q("""SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind NOT IN ('discount','passthrough','tax','credit')
               AND COALESCE(fee_key, '') != 'reschedule'""", (sid,), one=True)["v"]
    if sub <= 0:
        return
    pct = float(qt["discount_pct"])

    reason = next((lab for k, lab, _ in C.DISCOUNTS if k and k == qt["discount_reason"]), qt["discount_reason"])
    x("INSERT INTO lines (session_id, label, amount, kind) VALUES (?,?,?, 'discount')",
      (sid, f"Discount: {reason} ({pct:g}%)" if reason else f"Discount ({pct:g}%)", -round(sub * pct / 100, 2)))
    g.pop("tot_cache", None)

LEGACY_TAX_KEY, LEGACY_TAX_NAME = "sales", "Sales tax"
TAX_MAX_PCT = 25.0

def _rate_of(v):
    try:
        r_ = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(TAX_MAX_PCT, max(0.0, r_)) if r_ == r_ else 0.0

def taxes_list():

    raw = setting("taxes", None)
    if raw is None:
        mode = setting("tax_mode", "none") or "none"
        if mode == "none" or not _rate_of(setting("sales_tax_pct")):
            return []
        out = [dict(key=LEGACY_TAX_KEY, name=LEGACY_TAX_NAME, rate=_rate_of(setting("sales_tax_pct")), included=False,
                    default=True)]
        if mode == "regional" and _rate_of(setting("local_tax_pct")):
            out.append(dict(key="local", name="Local tax", rate=_rate_of(setting("local_tax_pct")), included=False,
                            default=False))
        return out
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for t_ in items if isinstance(items, list) else []:
        if isinstance(t_, dict) and str(t_.get("key") or "").strip() and str(t_.get("name") or "").strip():
            out.append(dict(key=str(t_["key"])[:20], name=str(t_["name"]).strip()[:40], rate=_rate_of(t_.get("rate")),
                            included=bool(t_.get("included")), default=bool(t_.get("default", True))))
    return out

def save_taxes(items):

    x("INSERT INTO settings (key, value) VALUES ('taxes', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (json.dumps(items),))
    on_top = round(sum(t_["rate"] for t_ in items if t_["default"] and not t_["included"]), 4)
    for k_, v_ in (("tax_mode", "flat" if items else "none"), ("sales_tax_pct", f"{on_top:g}" if items else "")):
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k_, v_))

def _tax_rules(info, items):

    out = {}
    for fn in hooks.tax_rules:
        try:
            got = fn(dict(info), [dict(t_) for t_ in items]) or {}
        except Exception:
            app.logger.exception("a plugin's tax rule")
            continue
        for k_, v_ in got.items():
            if isinstance(v_, bool):
                v_ = dict(on=v_)
            if isinstance(v_, dict):
                out.setdefault(k_, {}).update(v_)
    return out

def tax_pct_at(address="", included=False):

    items = taxes_list()
    rules = _tax_rules(dict(session_id=None, address=address or "", type=""), items)
    tot = 0.0
    for t_ in items:
        r_ = rules.get(t_["key"], {})
        if bool(r_.get("on", t_["default"])) and t_["included"] == included:
            tot += _rate_of(r_.get("rate", t_["rate"]))
    return round(tot, 4)

def session_taxes(sid):

    s_ = q("""SELECT s.id, s.type, s.tax_rate, s.tax_exempt, h.address, h.tax_exempt AS h_exempt, h.tax_cert
              FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""", (sid,), one=True)
    if not s_:
        return [], False, ""
    rows = {r_["key"]: r_ for r_ in q("SELECT * FROM session_taxes WHERE session_id=?", (sid,))}
    out = []
    if s_["tax_rate"] is not None:
        kept = [r_ for r_ in rows.values() if r_["rate"] is not None]
        if not kept:
            out = [dict(key=LEGACY_TAX_KEY, name=LEGACY_TAX_NAME, rate=float(s_["tax_rate"]), included=False, on=True,
                        manual=False, note="")]
        order = {t_["key"]: i_ for i_, t_ in enumerate(taxes_list())}
        for r_ in sorted(kept, key=lambda r_: (order.get(r_["key"], 99), r_["key"])):
            out.append(dict(key=r_["key"], name=r_["name"] or LEGACY_TAX_NAME, rate=float(r_["rate"]),
                            included=bool(r_["included"]), on=bool(r_["pick"]), manual=False, note=""))
    else:
        items = taxes_list()
        rules = _tax_rules(dict(session_id=sid, address=s_["address"] or "", type=s_["type"]), items)
        for t_ in items:
            r_ = rules.get(t_["key"], {})
            pick = rows[t_["key"]]["pick"] if t_["key"] in rows else None
            out.append(dict(key=t_["key"], name=t_["name"], rate=_rate_of(r_.get("rate", t_["rate"])), included=t_["included"],
                            on=bool(r_.get("on", t_["default"])) if pick is None else bool(pick), manual=pick is not None,
                            note=str(r_.get("note") or "")[:200]))
    if s_["h_exempt"] and s_["tax_rate"] is None:
        return out, True, "client"
    return out, bool(s_["tax_exempt"]), "session" if s_["tax_exempt"] else ""

def session_tax_rate(sid):

    r_ = q("SELECT tax_rate FROM sessions WHERE id=?", (sid,), one=True)
    if r_ and r_["tax_rate"] is not None:
        return float(r_["tax_rate"])
    return round(sum(t_["rate"] for t_ in session_taxes(sid)[0] if t_["on"] and not t_["included"]), 4)

def lock_tax_rate(sid):

    if not q("SELECT 1 FROM sessions WHERE id=? AND tax_rate IS NULL", (sid,), one=True):
        return
    items = session_taxes(sid)[0]
    for t_ in items:
        x("""INSERT INTO session_taxes (session_id, key, name, rate, included, pick) VALUES (?,?,?,?,?,?)
             ON CONFLICT(session_id, key) DO UPDATE SET name=excluded.name, rate=excluded.rate,
             included=excluded.included, pick=excluded.pick""",
          (sid, t_["key"], t_["name"], t_["rate"], int(t_["included"]), int(t_["on"])))
    if session_taxes(sid)[2] == "client":
        x("UPDATE sessions SET tax_exempt=1 WHERE id=?", (sid,))
    x("UPDATE sessions SET tax_rate=? WHERE id=? AND tax_rate IS NULL",
      (round(sum(t_["rate"] for t_ in items if t_["on"] and not t_["included"]), 4), sid))

def set_session_tax(sid, key, on):

    known = {t_["key"] for t_ in session_taxes(sid)[0]}
    if key not in known:
        return False
    locked = q("SELECT 1 FROM sessions WHERE id=? AND tax_rate IS NOT NULL", (sid,), one=True)
    if locked and not q("SELECT 1 FROM session_taxes WHERE session_id=? AND key=?", (sid, key), one=True):

        x("INSERT INTO session_taxes (session_id, key, name, rate, included, pick) VALUES (?,?,?,?,0,1)",
          (sid, key, LEGACY_TAX_NAME, float(q("SELECT tax_rate FROM sessions WHERE id=?", (sid,), one=True)["tax_rate"])))
    x("""INSERT INTO session_taxes (session_id, key, pick) VALUES (?,?,?)
         ON CONFLICT(session_id, key) DO UPDATE SET pick=excluded.pick""", (sid, key, 1 if on else 0))
    return True

KEPT_REASONS_1_2 = ("Canceled", "No-show")

def kept_split(paid, quoted, deposit):

    paid = max(paid, 0.0)
    if quoted <= 0.005:
        return round(paid, 2), 0.0
    kept = min(paid, max(deposit, 0.0))
    return round(kept, 2), round(max(0.0, paid - kept), 2)

def _kept_less_credit(paid, quoted, deposit, moved):

    if moved <= 0.005:
        return kept_split(paid, quoted, deposit)
    kept, back = kept_split(paid + moved, quoted, deposit)
    return round(max(0.0, kept - moved), 2), back

def _kept_case(s_):

    return bool(s_) and s_["stage"] == "lost"

def _lost_label(why):

    return {"No-show": "No-show", "Canceled": "Canceled"}.get(why or "", "Lost") + ", deposit kept"

def kept_deposit_tax(sid):

    s = q("SELECT stage, lost_reason FROM sessions WHERE id=?", (sid,), one=True)
    if not _kept_case(s):
        return 0.0
    return round(sum(e_["tax"] for e_ in tax_entries(sid)), 2)

def _legacy_1_2(sids):

    pays, kept, years = {}, [], {}
    for sid in sids:
        s_ = q("SELECT stage, lost_reason, stage_changed, tax_exempt FROM sessions WHERE id=?", (sid,), one=True)

        rows = q("""SELECT * FROM payments WHERE session_id=? AND voided IS NULL AND (kind != 'tip' OR tax IS NULL)
                    ORDER BY received, id""", (sid,))
        todo = [p_ for p_ in rows if p_["tax"] is None]
        if not s_ or not todo:
            continue
        paid = sum(p_["amount"] for p_ in rows)
        rate = session_tax_rate(sid)
        if (s_["stage"] == "lost" and s_["lost_reason"] in KEPT_REASONS_1_2 and not s_["tax_exempt"] and paid > 0
                and round(paid * rate / 100, 2) > 0):
            for p_ in todo:
                pays[p_["id"]] = (0.0, 0.0)
            if len(todo) == len(rows) and (s_["stage_changed"] or "")[:4].isdigit() and not q(
                    "SELECT 1 FROM tax_adjustments WHERE session_id=? AND why='kept_1_2'", (sid,), one=True):
                kept.append((sid, s_["stage_changed"][:10], round(paid * rate / 100, 2), round(paid, 2)))
            continue
        tot = q("""SELECT COALESCE(SUM(amount),0) v, COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0) t
                   FROM lines WHERE session_id=?""", (sid,), one=True)
        v_, t_, base = tot["v"], tot["t"], taxable_base(sid)
        for p_ in todo:
            taxed = v_ > 0 and t_
            years.setdefault((p_["received"] or "")[:4], []).append(
                (p_["received"] or "", p_["id"], p_["amount"] * t_ / v_ if taxed else 0.0,
                 p_["amount"] * base / v_ if taxed else 0.0))
    for y_, items in years.items():

        start = q("SELECT COALESCE(SUM(tax),0) v FROM books WHERE substr(received,1,4)=?", (y_,), one=True)["v"] if y_ else 0.0
        cum_t, cum_b = start, 0.0
        for _day, pid_, t_, b_ in sorted(items):
            tax_ = round(round(cum_t + t_, 2) - round(cum_t, 2), 2)
            taxable_ = round(round(cum_b + b_, 2) - round(cum_b, 2), 2) if abs(t_) > 0.000001 else 0.0
            cum_t, cum_b = cum_t + t_, cum_b + b_
            pays[pid_] = (tax_, taxable_)
    return pays, kept

def _fill_payment_taxes(sid=None):

    if sid is None:
        sids = [r_["session_id"] for r_ in q("""SELECT DISTINCT session_id FROM payments WHERE tax IS NULL AND voided IS NULL
                                                AND session_id IS NOT NULL""")]
    elif q("SELECT 1 FROM payments WHERE session_id=? AND tax IS NULL AND voided IS NULL LIMIT 1", (sid,), one=True):
        sids = [sid]
    else:
        return
    pays, kept = _legacy_1_2(sids)

    if sid is None:
        x("UPDATE payments SET kind='other' WHERE kind='tip' AND tax IS NULL AND voided IS NULL AND session_id IS NOT NULL")
    else:
        x("UPDATE payments SET kind='other' WHERE kind='tip' AND tax IS NULL AND voided IS NULL AND session_id=?", (sid,))
    for pid_, (tax_, taxable_) in pays.items():
        x("UPDATE payments SET tax=?, taxable=? WHERE id=? AND tax IS NULL", (tax_, taxable_, pid_))
    for sid_, day_, tax_, taxable_ in kept:
        x("INSERT INTO tax_adjustments (session_id, at, tax, taxable, why) VALUES (?,?,?,?, 'kept_1_2')",
          (sid_, day_, tax_, taxable_))
    g.pop("tot_cache", None)

TAX_WHY = {"canceled": "called off: only what is kept stays taxed", "reopened": "reopened after it was called off",
           "kept_1_2": "called off before 1.3: all paid taxed at the full rate, as then",
           "reason": "lost reason changed", "changed": "price or tax changed after payment",
           "voided": "a payment voided or restored", "payment": "payments received",
           "kept": "kept deposit"}

LIVE_ADJ = "COALESCE(why,'') NOT LIKE 'void:%'"

def tax_entries(sid):

    legacy, out = None, []
    for p_ in q("SELECT * FROM payments WHERE session_id=? AND voided IS NULL ORDER BY received, id", (sid,)):
        tax_, taxable_ = p_["tax"], p_["taxable"]
        if tax_ is None:
            if legacy is None:
                legacy = _legacy_1_2([sid])
                for sid_, day_, t_, b_ in legacy[1]:
                    out.append(dict(sid=sid, pid=None, day=day_, amount=0.0, tax=t_, taxable=b_, what="kept_1_2",
                                    kind="adjustment", parts=_parts_of(t_, b_, None)))
            tax_, taxable_ = legacy[0].get(p_["id"], (0.0, 0.0))
        out.append(dict(sid=sid, pid=p_["id"], day=p_["received"], amount=p_["amount"], tax=tax_ or 0.0,
                        taxable=taxable_ or 0.0, what="payment", kind=p_["kind"],
                        parts=_parts_of(tax_, taxable_, p_["tax_parts"] if p_["tax"] is not None else None)))
    for a_ in q(f"SELECT * FROM tax_adjustments WHERE session_id=? AND {LIVE_ADJ} ORDER BY at, id", (sid,)):
        out.append(dict(sid=sid, pid=None, day=a_["at"], amount=0.0, tax=a_["tax"], taxable=a_["taxable"],
                        what=a_["why"] or "changed", kind="adjustment", parts=_parts_of(a_["tax"], a_["taxable"], a_["tax_parts"])))
    out.sort(key=lambda e_: e_["day"] or "")
    return out

def payment_taxes(sid):

    return [(dict(e_, received=e_["day"]), e_["taxable"], e_["tax"]) for e_ in tax_entries(sid)]

def _parts_of(tax, taxable, raw):

    if raw:
        try:
            got = json.loads(raw)
            return {str(k_): [float(v_[0]), float(v_[1])] for k_, v_ in got.items()}
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
    if abs(tax or 0.0) < 0.000001 and abs(taxable or 0.0) < 0.000001:
        return {}
    return {LEGACY_TAX_NAME: [tax or 0.0, taxable or 0.0]}

def _tax_names(sid):

    names = {t_["key"]: t_["name"] for t_ in taxes_list()}
    names.update({t_["key"]: t_["name"] for t_ in session_taxes(sid)[0]})
    return names

def _tax_target(sid, by_name=False):

    tot = q("""SELECT COALESCE(SUM(amount),0) v, COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0) t
               FROM lines WHERE session_id=?""", (sid,), one=True)
    v_, t_ = tot["v"], tot["t"]
    figs = tax_figures(sid) if v_ > 0.005 else []
    held = [f_ for f_ in figs if f_["included"] and f_["amount"]]
    t_ += sum(f_["amount"] for f_ in held)
    if v_ <= 0.005 or not t_:
        return ((0.0, 0.0), {}) if by_name else (0.0, 0.0)
    s_ = q("SELECT stage, lost_reason FROM sessions WHERE id=?", (sid,), one=True)
    t2_ = totals(sid)
    covered = t2_["kept"] if _kept_case(s_) else min(max(t2_["paid"], 0.0), v_)
    bases = {f_["key"]: f_["base"] for f_ in figs}
    names = _tax_names(sid) if by_name or len(figs) > 1 or held else {}
    parts, keys = {}, set()
    for l_ in q("SELECT tax_key, label, amount FROM lines WHERE session_id=? AND kind='tax' ORDER BY id", (sid,)):
        keys.add(l_["tax_key"])
        k_ = l_["tax_key"] or LEGACY_TAX_KEY
        n_ = names.get(k_) or (re.sub(r" \([0-9.]+%\)$", "", l_["label"]) if l_["tax_key"] else LEGACY_TAX_NAME)
        b_ = bases[k_] if k_ in bases else taxable_base(sid, l_["tax_key"])
        p_ = parts.setdefault(n_, [0.0, 0.0])
        p_[0] += covered * l_["amount"] / v_
        p_[1] = max(p_[1], covered * b_ / v_)
    for f_ in held:
        p_ = parts.setdefault(names.get(f_["key"], f_["name"]), [0.0, 0.0])
        p_[0] += covered * f_["amount"] / v_
        p_[1] = max(p_[1], covered * f_["base"] / v_)
    if len(parts) == 1 and len(keys) == 1 and not held:
        base = taxable_base(sid, next(iter(keys)))
        whole = (covered * t_ / v_, covered * base / v_)
        parts = {n_: list(whole) for n_ in parts}
    else:
        whole = (covered * t_ / v_, max([p_[1] for p_ in parts.values()] or [0.0]))
    return (whole, parts) if by_name else whole

def settle_tax(sid, why="changed", pid=None):

    _fill_payment_taxes(sid)
    if pid is not None:
        x("UPDATE payments SET tax=0, taxable=0 WHERE id=? AND tax IS NULL", (pid,))
    (want_t, want_b), want_p = _tax_target(sid, by_name=True)
    have = tax_entries(sid)
    have_p = {}
    for e_ in have:
        for n_, (t_, b_) in e_["parts"].items():
            h_ = have_p.setdefault(n_, [0.0, 0.0])
            h_[0] += t_
            h_[1] += b_
    names = list(want_p) + [n_ for n_ in have_p if n_ not in want_p]

    dt = round(want_t - sum(e_["tax"] for e_ in have), 2)
    db_ = round(want_b - sum(e_["taxable"] for e_ in have), 2)
    if len(names) <= 1:
        delta = {n_: [dt, db_] for n_ in names}
    else:
        delta = {n_: [round(want_p.get(n_, [0.0, 0.0])[0] - have_p.get(n_, [0.0, 0.0])[0], 2),
                      round(want_p.get(n_, [0.0, 0.0])[1] - have_p.get(n_, [0.0, 0.0])[1], 2)] for n_ in names}
        dt = round(sum(d_[0] for d_ in delta.values()), 2)
    delta = {n_: d_ for n_, d_ in delta.items() if abs(d_[0]) >= 0.005 or abs(d_[1]) >= 0.005}
    if pid is not None:
        p_ = q("SELECT tax, taxable, tax_parts FROM payments WHERE id=?", (pid,), one=True)
        mine = _parts_of(p_["tax"], p_["taxable"], p_["tax_parts"])
        for n_, (t_, b_) in delta.items():
            m_ = mine.setdefault(n_, [0.0, 0.0])
            mine[n_] = [round(m_[0] + t_, 2), round(m_[1] + b_, 2)]
        x("UPDATE payments SET tax=ROUND(tax+?, 2), taxable=ROUND(taxable+?, 2), tax_parts=? WHERE id=?",
          (dt, db_, json.dumps(mine) if mine else None, pid))
    elif abs(dt) >= 0.005 or abs(db_) >= 0.005 or delta:
        x("INSERT INTO tax_adjustments (session_id, at, tax, taxable, why, tax_parts) VALUES (?,?,?,?,?,?)",
          (sid, today().isoformat(), dt, db_, why, json.dumps(delta) if delta else None))
    g.pop("tot_cache", None)

def _void_corrections(pid, void_now):

    p_ = q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    if not p_ or not p_["session_id"]:
        return
    if not void_now:
        for a_ in q("SELECT id, why FROM tax_adjustments WHERE why LIKE ?", (f"void:{pid}:%",)):
            x("UPDATE tax_adjustments SET why=? WHERE id=?", (a_["why"].split(":", 2)[2], a_["id"]))
        return
    own = p_["tax"] or 0.0
    left = round(abs(own), 2)
    if left < 0.005:
        return
    y_ = (p_["received"] or "")[:4]
    for a_ in q(f"""SELECT * FROM tax_adjustments WHERE session_id=? AND substr(at,1,4)=? AND at >= ? AND {LIVE_ADJ}
                    AND tax * ? < 0 ORDER BY at DESC, id DESC""", (p_["session_id"], y_, p_["received"][:10], own)):
        if left < 0.005:
            break
        take = round(min(abs(a_["tax"]), left), 2)
        left = round(left - take, 2)
        why_ = f"void:{pid}:{a_['why'] or 'changed'}"
        if take >= abs(a_["tax"]) - 0.000001:
            x("UPDATE tax_adjustments SET why=? WHERE id=?", (why_, a_["id"]))
            continue
        part = take / abs(a_["tax"])
        sign = 1 if a_["tax"] > 0 else -1
        own_p = _parts_of(a_["tax"], a_["taxable"], a_["tax_parts"]) if a_["tax_parts"] else None
        out_p = {n_: [round(v_[0] * part, 2), round(v_[1] * part, 2)] for n_, v_ in own_p.items()} if own_p else None
        x("UPDATE tax_adjustments SET tax=ROUND(tax-?, 2), taxable=ROUND(taxable-?, 2), tax_parts=? WHERE id=?",
          (sign * take, round(a_["taxable"] * part, 2),
           json.dumps({n_: [round(v_[0] - out_p[n_][0], 2), round(v_[1] - out_p[n_][1], 2)] for n_, v_ in own_p.items()})
           if own_p else None, a_["id"]))
        x("INSERT INTO tax_adjustments (session_id, at, tax, taxable, why, tax_parts) VALUES (?,?,?,?,?,?)",
          (p_["session_id"], a_["at"], sign * take, round(a_["taxable"] * part, 2), why_, json.dumps(out_p) if out_p else None))
    g.pop("tot_cache", None)

def stored_tax(sid):

    return sum(e_["tax"] for e_ in tax_entries(sid))

def _charge_target(sid, legacy=False):

    s_ = q("""SELECT stage, lost_reason, stage_changed, shoot_date, (SELECT MIN(received) FROM payments p WHERE
              p.session_id=s.id AND p.voided IS NULL AND p.kind != 'tip') AS first_paid FROM sessions s WHERE id=?""",
           (sid,), one=True)
    if not s_:
        return 0.0, "", "", ""
    t_ = totals(sid)
    if s_["stage"] == "lost":
        if _kept_case(s_) and t_["kept"] > 0.005 and not legacy:
            return (t_["kept"], "", _lost_label(s_["lost_reason"]),
                    (s_["stage_changed"] or "")[:10])
        return 0.0, "", "", ""
    inv_ = q("""SELECT d.number, d.issued, b.data FROM doc_numbers d LEFT JOIN bill_snapshots b ON b.number=d.number
                WHERE d.session_id=? AND d.kind='INV'""", (sid,), one=True)
    if inv_:
        amt = t_["quoted"]
        if inv_["data"] and not legacy:
            try:
                d_ = json.loads(inv_["data"])
                amt = float(d_.get("total", amt)) + float(d_.get("refund_lines_1_0_0") or 0)
            except (ValueError, TypeError, AttributeError):
                pass
        return amt, inv_["number"], "", (inv_["issued"] or "")[:10]
    if legacy:
        return 0.0, "", "", ""
    if t_["quoted"] > 0.005 and ((s_["stage"] in OWED_STAGES and t_["paid"] > 0.005) or t_["written_off"] > 0.005):
        return t_["quoted"], "", "Work done", (s_["shoot_date"] or s_["first_paid"] or "")[:10]
    return 0.0, "", "", ""

def _upgrade_floor(sid):

    on_ = setting("upgraded_on") or ""
    try:
        return on_ if on_ and sid <= int(setting("upgraded_sids") or 0) else ""
    except ValueError:
        return ""

def _work_done_day(sid, pid, shoot):

    t_ = today().isoformat()
    pays = q("""SELECT id, received FROM payments WHERE session_id=? AND voided IS NULL AND kind != 'tip' AND amount > 0
                ORDER BY received, id""", (sid,))
    if not pid or not shoot or shoot > t_ or not pays or pays[0]["id"] != pid:
        return t_
    first = (pays[0]["received"] or "")[:10]
    if not first or shoot[:10] > first or any(p_["id"] != pid and (p_["received"] or "") < t_[:8] + "01" for p_ in pays):
        return t_
    day = shoot[:10] if shoot[:7] == first[:7] else first
    return min(max(day, _upgrade_floor(sid)), t_)

def sync_charges(sid, entered=None):

    amt, number, label, natural = _charge_target(sid)
    rows = q("SELECT * FROM charges WHERE session_id=? ORDER BY id", (sid,))
    standing = round(sum(r_["amount"] for r_ in rows), 2)
    cur = next((r_ for r_ in reversed(rows) if r_["amount"] > 0), None) if abs(standing) > 0.005 else None
    if cur and abs(standing - amt) <= 0.005 and (cur["number"], cur["label"]) == (number, label):
        return
    if not cur and abs(amt) <= 0.005:
        return
    day = today().isoformat()
    if cur:
        x("INSERT INTO charges (session_id, at, amount, number, label) VALUES (?,?,?,'',?)",
          (sid, day, -standing, f"{cur['number'] or cur['label']} {'replaced' if abs(amt) > 0.005 else 'withdrawn'}"))
    elif entered and label == "Work done":
        day = _work_done_day(sid, entered, natural)
    if abs(amt) > 0.005:
        x("INSERT INTO charges (session_id, at, amount, number, label) VALUES (?,?,?,?,?)", (sid, day, round(amt, 2), number, label))

def _seed_charges():

    if setting("charges_seeded"):
        return

    x("UPDATE books SET kind='other' WHERE kind='tip'")
    sids = [r_["id"] for r_ in q("SELECT id FROM sessions WHERE id NOT IN (SELECT session_id FROM charges WHERE session_id IS NOT NULL)")]
    for sid_ in sids:
        amt, number, label, day = _charge_target(sid_, legacy=True)
        if abs(amt) > 0.005:
            x("INSERT INTO charges (session_id, at, amount, number, label) VALUES (?,?,?,?,?)",
              (sid_, day or today().isoformat(), round(amt, 2), number, label))
    if sids:
        for k_, v_ in (("upgraded_on", today().isoformat()), ("upgraded_sids", str(max(sids)))):
            x("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k_, v_))
        g.pop("tot_cache", None)
        for sid_ in sids:
            sync_charges(sid_)
    x("INSERT OR REPLACE INTO settings (key, value) VALUES ('charges_seeded', '1')")

def _fill_all_payment_taxes():

    if q("SELECT 1 FROM payments WHERE tax IS NULL AND voided IS NULL LIMIT 1", one=True):
        _fill_payment_taxes()

def money_changed(sid, why="changed"):

    settle_tax(sid, why)
    sync_charges(sid)

def reapply_tax(sid):

    _tax_line(sid)
    settle_tax(sid, "changed")
    sync_charges(sid)

def _tax_line(sid):

    x("DELETE FROM lines WHERE session_id=? AND kind='tax'", (sid,))
    for f_ in tax_figures(sid):
        if not f_["included"] and f_["base"] > 0:
            x("INSERT INTO lines (session_id, label, amount, kind, taxable, tax_key) VALUES (?,?,?, 'tax', 0, ?)",
              (sid, f"{f_['name']} ({f_['rate']:g}%)", f_["amount"], f_["key"]))
    g.pop("tot_cache", None)

UNDISCOUNTED_SQL = "(kind='passthrough' OR COALESCE(fee_key,'')='reschedule')"

def _undiscounted(l_):
    return l_["kind"] == "passthrough" or (l_["fee_key"] or "") == "reschedule"

def _untaxed_sql(key):
    return ("" if key is None else
            " AND instr(',' || COALESCE(untaxed,'') || ',', ',' || ? || ',') = 0")

def taxable_base(sid, key=None, without_fee=False):

    qt = q("SELECT discount_pct FROM quotes WHERE session_id=?", (sid,), one=True)
    pct = float(qt["discount_pct"] or 0) if qt else 0.0
    r = q(f"""SELECT COALESCE(SUM(CASE WHEN NOT ({UNDISCOUNTED_SQL}) THEN amount END),0) d,
                     COALESCE(SUM(CASE WHEN {UNDISCOUNTED_SQL} THEN amount END),0) p
              FROM lines WHERE session_id=? AND taxable=1 AND kind NOT IN ('discount','tax')"""
          + (" AND COALESCE(fee_key,'') != 'reschedule'" if without_fee else "") + _untaxed_sql(key),
          (sid,) + (() if key is None else (key,)), one=True)
    return r["d"] * (1 - pct / 100) + r["p"]

def _line_has_tax(l_, key):
    return bool(l_["taxable"]) and key not in (l_["untaxed"] or "").split(",")

def tax_figures(sid, without_fee=False):

    items, exempt, _w = session_taxes(sid)
    on = [t_ for t_ in items if t_["on"] and t_["rate"] > 0]
    if exempt or not on:
        return []
    ins = [t_ for t_ in on if t_["included"]]
    out = []
    if not ins:
        for t_ in on:
            base = taxable_base(sid, t_["key"], without_fee)
            out.append(dict(t_, base=base, amount=round(base * t_["rate"] / 100, 2)))
        return out
    qt = q("SELECT discount_pct FROM quotes WHERE session_id=?", (sid,), one=True)
    keep = 1 - (float(qt["discount_pct"] or 0) if qt else 0.0) / 100
    lines = q("""SELECT kind, amount, taxable, untaxed, fee_key FROM lines WHERE session_id=? AND kind NOT IN ('discount','tax')
                 ORDER BY id""", (sid,))
    if without_fee:
        lines = [l_ for l_ in lines if (l_["fee_key"] or "") != "reschedule"]
    for t_ in on:
        held = 0.0
        parts = []
        for l_ in lines:
            if not _line_has_tax(l_, t_["key"]):
                continue
            amt = l_["amount"] * (1 if _undiscounted(l_) else keep)
            r_in = sum(i_["rate"] for i_ in ins if _line_has_tax(l_, i_["key"]))
            parts.append(amt * 100 / (100 + r_in))
            if t_["included"]:
                held += amt * t_["rate"] / (100 + r_in)
        base = math.fsum(parts)
        out.append(dict(t_, base=base, amount=round(held if t_["included"] else base * t_["rate"] / 100, 2)))
    return out

def included_taxes(sid):

    return [dict(name=f_["name"], rate=f_["rate"], amount=f_["amount"]) for f_ in tax_figures(sid)
            if f_["included"] and f_["amount"] > 0.005]

def _lines_sig(sid):
    return json.dumps([[r_["label"], round(r_["amount"], 2)] for r_ in
                       q("SELECT label, amount FROM lines WHERE session_id=? ORDER BY id", (sid,))])

def _invoice_still_sent(sid):

    sig = _lines_sig(sid)
    r_ = q("SELECT number, sig FROM invoice_sent WHERE session_id=?", (sid,), one=True)
    for kind_ in ("INV", "R"):
        n_ = q("""SELECT d.number, b.sig FROM doc_numbers d LEFT JOIN bill_snapshots b ON b.number=d.number
                  WHERE d.session_id=? AND d.kind=?""", (sid, kind_), one=True)
        if not n_:
            continue
        sent_ = kind_ == "INV" and r_ is not None and r_["number"] == n_["number"]
        if not ((n_["sig"] and n_["sig"] != sig) or (sent_ and r_["sig"] != sig)):
            continue
        x("UPDATE doc_numbers SET kind=? || number WHERE session_id=? AND kind=?", (kind_ + "-replaced:", sid, kind_))
        if kind_ == "R":
            audit(f"Receipt {n_['number']} was already issued and the session's lines changed: the next receipt replaces it", sid=sid)
            continue
        x("DELETE FROM invoice_sent WHERE session_id=?", (sid,))
        x("UPDATE tasks SET done=NULL, outcome=NULL WHERE session_id=? AND title=?", (sid, SEND_TASKS["invoice"]))
        audit(f"Invoice {n_['number']} was already {'sent' if sent_ else 'issued'} and its lines changed: the next invoice "
              "replaces it", sid=sid)

def _invoice_marked_sent(sid):
    if totals(sid)["quoted"] > 0.005:
        issue_bill(sid)
    inv_ = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    if inv_:
        x("INSERT OR REPLACE INTO invoice_sent (session_id, number, sig) VALUES (?,?,?)", (sid, inv_["number"], _lines_sig(sid)))

def totals(sid):

    if "tot_cache" not in g:
        g.tot_cache = _all_totals()
    cache = g.tot_cache
    if sid not in cache:
        cache[sid] = _totals(sid)
    return cache[sid]

def _deposit_for(stype, quoted, has_event, family=False):
    if quoted <= 0 or family:
        return 0.0
    own = C.DEPOSIT_RULE.get(stype)
    if own:
        return round(min(quoted * own[1] / 100 if own[0] == "pct" else own[1], quoted), 2)
    if stype and C.TYPE_KIND.get(stype) == "property" and not C.has_deposit_step(stype):
        return 0.0
    if has_event or C.TYPE_KIND.get(stype) == "event":
        return round(quoted * C.EVENT_DEPOSIT_PCT / 100, 2)
    return round(min(C.DEPOSIT_AMOUNT, quoted), 2)

def _quoted_deposit(data, quoted, booked=False):

    try:
        v_ = json.loads(data) if data else None
    except ValueError:
        return None
    if not isinstance(v_, dict) or not isinstance(v_.get("deposit"), (int, float)) or not isinstance(v_.get("total"), (int, float)):
        return None
    if quoted <= 0 or (abs(v_["total"] - quoted) > 0.005 and not booked):
        return None
    return round(min(max(v_["deposit"], 0.0), quoted), 2)

def _all_totals():
    rate = float(setting("mileage_rate") or 0)
    lq_ = {r[0]: r[1] for r in q("""SELECT session_id, data FROM quote_versions WHERE id IN
                                    (SELECT MAX(id) FROM quote_versions GROUP BY session_id)""")}
    ln = {r[0]: r for r in q("""SELECT session_id, COALESCE(SUM(amount),0), COALESCE(SUM(CASE WHEN kind='tax' THEN amount END),0),
                                       MAX(fee_key='event') FROM lines GROUP BY session_id""")}
    pd = {r[0]: r[1] for r in q("""SELECT session_id, COALESCE(SUM(amount),0) FROM payments WHERE voided IS NULL AND kind != 'tip'
                                   GROUP BY session_id""")}
    tp = {r[0]: r[1] for r in q("""SELECT session_id, COALESCE(SUM(amount),0) FROM payments WHERE voided IS NULL AND kind='tip'
                                   GROUP BY session_id""")}
    rc = {r[0]: r[1] for r in q("""SELECT p.session_id, COALESCE(SUM(c.amount),0) FROM refund_credits c JOIN payments p ON p.id=c.payment_id
                                   WHERE p.voided IS NULL GROUP BY p.session_id""")}
    ex = {r[0]: r for r in q("""SELECT session_id, COALESCE(SUM(amount),0), COALESCE(SUM(miles),0), COALESCE(SUM(ROUND(miles * COALESCE(rate, ?), 2)),0)
                                FROM expenses GROUP BY session_id""", (rate,))}
    wo = {r[0]: r[1] for r in q("SELECT session_id, SUM(amount) FROM write_offs GROUP BY session_id")}
    tx = {r[0]: (r[1], r[2]) for r in q("""SELECT session_id, COALESCE(SUM(tax),0), MAX(tax IS NULL) FROM payments
                                          WHERE voided IS NULL AND kind != 'tip' GROUP BY session_id""")}
    for r in q(f"SELECT session_id, SUM(tax) FROM tax_adjustments WHERE {LIVE_ADJ} GROUP BY session_id"):
        tx[r[0]] = ((tx.get(r[0]) or (0.0, 0))[0] + r[1], (tx.get(r[0]) or (0.0, 0))[1])
    co_ = {r[0]: -r[1] for r in q("""SELECT session_id, COALESCE(SUM(amount),0) FROM payments WHERE voided IS NULL
                                     AND kind='refund' AND method=? GROUP BY session_id""", (CREDIT_OUT,))}
    out = {}
    rp_ = {r[0] for r in q("SELECT session_id FROM quote_replacements WHERE booked_at IS NULL")}
    for sid, stype, fam_, stage_, why_, dep_b in q("SELECT id, type, event_id IS NOT NULL, stage, lost_reason, deposit_booked FROM sessions"):
        quoted, tax, ev = (ln[sid][1], ln[sid][2], ln[sid][3]) if sid in ln else (0.0, 0.0, 0)
        paid = pd.get(sid, 0.0)
        a, m, mc = (ex[sid][1], ex[sid][2], ex[sid][3]) if sid in ex else (0.0, 0.0, 0.0)
        dep = _deposit_for(stype, quoted, ev, family=bool(fam_))
        agreed_ = None
        if not fam_ and sid in lq_:
            agreed_ = _quoted_deposit(lq_[sid], quoted, booked=stage_ not in ("inquiry", "quoted") and sid not in rp_)
            dep = dep if agreed_ is None else agreed_
        if not fam_ and agreed_ is None and dep_b is not None and stage_ not in ("inquiry", "quoted") and quoted > 0 and sid not in rp_:
            dep = round(min(dep_b, quoted), 2)
        lost_ = stage_ == "lost"
        kept, back = _kept_less_credit(paid, quoted, dep, co_.get(sid, 0.0)) if lost_ else (0.0, 0.0)
        income = kept if lost_ else paid
        tax_paid = _tax_carried(tx.get(sid), tax, quoted, income)
        out[sid] = dict(kept=kept, refund_due=back, tips=tp.get(sid, 0.0), quoted=quoted, paid=paid, due=quoted - rc.get(sid, 0.0) - wo.get(sid, 0.0) - paid, refund_credit=rc.get(sid, 0.0),
                        written_off=wo.get(sid, 0.0), spent=a, miles=m, rate=rate, cost=a + mc,
                        profit=income + tp.get(sid, 0.0) - tax_paid - a - mc, deposit_req=dep, deposit_short=max(0.0, dep - paid))
    return out

def _totals(sid):
    quoted = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?", (sid,), one=True)["v"]
    paid = q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE session_id=? AND voided IS NULL AND kind != 'tip'",
             (sid,), one=True)["v"]
    tips = q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE session_id=? AND voided IS NULL AND kind='tip'",
             (sid,), one=True)["v"]
    rate = float(setting("mileage_rate") or 0)

    spent = q("""SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(miles),0) m, COALESCE(SUM(ROUND(miles * COALESCE(rate, ?), 2)),0) mc
                 FROM expenses WHERE session_id=?""", (rate, sid), one=True)
    cost = spent["a"] + spent["mc"]
    dep = deposit_required(sid, quoted)
    tax = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=? AND kind='tax'", (sid,), one=True)["v"]
    rc = refund_credit(sid)
    wo = written_off(sid)
    st_ = q("SELECT stage, lost_reason FROM sessions WHERE id=?", (sid,), one=True)
    lost_ = _kept_case(st_)
    co_ = -q("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE session_id=? AND voided IS NULL AND kind='refund' AND method=?",
             (sid, CREDIT_OUT), one=True)["v"]
    kept, back = _kept_less_credit(paid, quoted, dep, co_) if lost_ else (0.0, 0.0)
    income = kept if lost_ else paid
    tx_ = q(f"""SELECT COALESCE(SUM(tax),0) + (SELECT COALESCE(SUM(tax),0) FROM tax_adjustments WHERE session_id=? AND {LIVE_ADJ}) t,
                      COALESCE(MAX(tax IS NULL),0) n FROM payments WHERE session_id=? AND voided IS NULL AND kind != 'tip'""",
            (sid, sid), one=True)
    tax_paid = _tax_carried((tx_["t"], tx_["n"]), tax, quoted, income)
    return dict(kept=kept, refund_due=back, tips=tips, quoted=quoted, paid=paid, due=quoted - rc - wo - paid, refund_credit=rc, written_off=wo, spent=spent["a"], miles=spent["m"],
                rate=rate, cost=cost, profit=income + tips - tax_paid - cost, deposit_req=dep, deposit_short=max(0.0, dep - paid))

def _tax_carried(rec, tax, quoted, income):

    if rec and not rec[1]:
        return rec[0]
    return tax * min(1.0, income / quoted) if quoted > 0.005 and income > 0 else 0.0

def refund_credit(sid):

    return q("""SELECT COALESCE(SUM(c.amount),0) v FROM refund_credits c JOIN payments p ON p.id=c.payment_id
                WHERE p.session_id=? AND p.voided IS NULL""", (sid,), one=True)["v"]

def written_off(sid):

    return q("SELECT COALESCE(SUM(amount),0) v FROM write_offs WHERE session_id=?", (sid,), one=True)["v"]

def write_off_balance(sid, note=""):

    due = totals(sid)["due"]
    if due <= 0.005:
        return
    note = " ".join((note or "").split())[:200]
    x("INSERT INTO write_offs (session_id, amount, at, why, note) VALUES (?,?,?, 'waived', ?)",
      (sid, round(due, 2), today().isoformat(), note))
    g.pop("tot_cache", None)
    sync_charges(sid)
    audit(f"Wrote off the balance of {money(due)} (waived{': ' + note if note else ''}). A write-off, not a payment", sid=sid)

def lock_deposit(sid):

    if q("SELECT 1 FROM sessions WHERE id=? AND deposit_booked IS NULL AND event_id IS NULL", (sid,), one=True):
        quoted = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?", (sid,), one=True)["v"]
        if quoted > 0.005:
            x("UPDATE sessions SET deposit_booked=? WHERE id=?", (deposit_required(sid, quoted), sid))

def unwaive_balance(sid):

    wo = written_off(sid)
    if not q("SELECT 1 FROM write_offs WHERE session_id=?", (sid,), one=True):
        return
    if wo <= 0.005:

        return

    x("DELETE FROM write_offs WHERE session_id=? AND at >= ?", (sid, books_open_from()))
    held = written_off(sid)
    if abs(held) > 0.005:
        x("INSERT INTO write_offs (session_id, amount, at, why) VALUES (?,?,?, 'taken back')",
          (sid, -round(held, 2), today().isoformat()))
    g.pop("tot_cache", None)
    sync_charges(sid)
    audit(f"Took back the write-off of {money(wo)} (the balance step is no longer Waived): the balance is owed again", sid=sid)

def deposit_required(sid, quoted=None):

    if quoted is None:
        quoted = q("SELECT COALESCE(SUM(amount),0) v FROM lines WHERE session_id=?", (sid,), one=True)["v"]
    if quoted <= 0:
        return 0.0
    st_ = q("SELECT type, event_id, stage, deposit_booked FROM sessions WHERE id=?", (sid,), one=True)
    if st_ and not st_["event_id"]:
        new_ = bool(q("SELECT 1 FROM quote_replacements WHERE session_id=? AND booked_at IS NULL", (sid,), one=True))
        lq_ = q("SELECT data FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
        agreed_ = _quoted_deposit(lq_["data"], quoted, booked=st_["stage"] not in ("inquiry", "quoted") and not new_) if lq_ else None
        if agreed_ is not None:
            return agreed_
        if st_["deposit_booked"] is not None and st_["stage"] not in ("inquiry", "quoted") and not new_:
            return round(min(st_["deposit_booked"], quoted), 2)
    return _deposit_for(st_["type"] if st_ else None,
                        quoted, bool(q("SELECT 1 FROM lines WHERE session_id=? AND fee_key='event'", (sid,), one=True)),
                        family=bool(st_ and st_["event_id"]))

def resched_fees_due(sid):

    fee = q("""SELECT COALESCE(SUM(amount),0) v, COUNT(*) n FROM lines WHERE session_id=? AND fee_key='reschedule'
               AND kind NOT IN ('tax','discount')""", (sid,), one=True)
    if not fee["n"]:
        return 0.0
    on_top = [sum(f_["amount"] for f_ in tax_figures(sid, w_) if not f_["included"] and f_["base"] > 0) for w_ in (False, True)]
    return round(fee["v"] + on_top[0] - on_top[1], 2)

def installments_of(sid):

    s_ = q("SELECT type, installments FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return 0
    n_ = s_["installments"] if s_["installments"] is not None else C.INSTALLMENTS.get(s_["type"], 0)
    return max(0, min(int(n_ or 0), C.INSTALLMENTS_KEPT_MAX))

def _booked_on(sid):

    r_ = q("SELECT MIN(at) a FROM stage_log WHERE session_id=? AND stage='booked'", (sid,), one=True)
    return (r_["a"] or "")[:10] if r_ else ""

def months_after(day, k):

    import calendar as _cal
    y_, m_ = divmod(day.month - 1 + k, 12)
    y_, m_ = day.year + y_, m_ + 1
    return day.replace(year=y_, month=m_, day=min(day.day, _cal.monthrange(y_, m_)[1]))

def plan_dates(sid):

    r_ = q("SELECT plan_dates FROM sessions WHERE id=?", (sid,), one=True)
    try:
        got = json.loads(r_["plan_dates"]) if r_ and r_["plan_dates"] else None
    except ValueError:
        return None
    return got if isinstance(got, list) and got and all(isinstance(v_, str) for v_ in got) else None

def keep_plan_dates(sid, again=False):

    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["stage"] in ("inquiry", "quoted", "lost"):
        return None
    n_, have = installments_of(sid), plan_dates(sid)
    if n_ < 2:
        if have:
            x("UPDATE sessions SET plan_dates=NULL WHERE id=?", (sid,))
        return None
    if have and len(have) == n_ and not again:
        return None
    rows = installment_schedule(sid, live=True)
    if not rows or any(not r_["due"] for r_ in rows):
        return None
    x("UPDATE sessions SET plan_dates=? WHERE id=?", (json.dumps([r_["due"] for r_ in rows]), sid))
    return [r_["due"] for r_ in rows]

def plan_dates_moved(sid):

    have = plan_dates(sid)
    if not have or len(have) != installments_of(sid):
        return False
    rows = installment_schedule(sid, live=True)
    return bool(rows) and all(r_["due"] for r_ in rows) and [r_["due"] for r_ in rows] != have

app.jinja_env.globals["plan_dates_moved"] = plan_dates_moved

def _seed_plan_dates():

    if setting("plan_dates_seeded"):
        return
    for r_ in q("SELECT id FROM sessions WHERE stage IN ('booked','shot','delivered') AND plan_dates IS NULL AND event_id IS NULL"):
        keep_plan_dates(r_["id"])
    x("INSERT OR REPLACE INTO settings (key, value) VALUES ('plan_dates_seeded', '1')")

def installment_schedule(sid, t_=None, live=False):

    n_ = installments_of(sid)
    s_ = q("SELECT stage, type, shoot_date, event_id FROM sessions WHERE id=?", (sid,), one=True)
    if n_ < 2 or not s_ or s_["event_id"] or s_["stage"] == "lost":
        return []
    t_ = t_ or totals(sid)
    after = round(t_["quoted"] - t_["deposit_req"], 2)
    if after <= 0.005:
        return []
    end = balance_due_date(s_["shoot_date"], s_["type"], sid=sid)
    start = _booked_on(sid) or today().isoformat()
    cents = int(round(after * 100))
    plain = cents - int(round(min(max(resched_fees_due(sid), 0.0), after) * 100))
    each = (plain if plain > 0 else cents) // n_
    covered = round(t_["quoted"] - t_["due"] - t_["deposit_req"], 2)
    now_, rows = today().isoformat(), []
    monthly = not end or months_after(d(start), n_) <= d(end)
    kept = None if live or s_["stage"] in ("inquiry", "quoted") else plan_dates(sid)
    kept = kept if kept and len(kept) == n_ else None
    for k_ in range(1, n_ + 1):
        amt = (each if k_ < n_ else cents - each * (n_ - 1)) / 100
        due = None
        if end and monthly:
            due = end if k_ == n_ else months_after(d(start), k_).isoformat()
        elif end:
            span = (d(end) - d(start)).days
            due = end if span <= 0 else (d(start) + timedelta(days=round(span * k_ / n_))).isoformat()
        if kept:
            due = kept[k_ - 1]
        got = max(0.0, min(amt, covered))
        covered = round(covered - amt, 2)
        left = round(amt - got, 2)
        late = left > 0.005 and bool(due) and due < now_ and s_["stage"] not in ("inquiry", "quoted")
        rows.append(dict(n=k_, of=n_, amount=amt, due=due, left=left, part=got > 0.005 and left > 0.005,
                         spacing="monthly" if monthly else "even",
                         status="paid" if left <= 0.005 else "late" if late else "due"))
    return rows

def payment_schedule(sid, t_=None, booked_only=False, paid=False):

    rows_ = installment_schedule(sid, t_)
    if not rows_:
        return []
    pre_ = booked_only and not _booked_on(sid)
    return [dict(n=r_["n"], of=r_["of"], amount=r_["amount"], **({"paid": r_["status"] == "paid"} if paid else {}),
                 due=("set by the day you book" if pre_ and r_["n"] < r_["of"] else
                      long_date(r_["due"]) if r_["due"] else "once the shoot date is set")) for r_ in rows_]

def next_installment(sid, t_=None):

    return next((r_ for r_ in installment_schedule(sid, t_) if r_["left"] > 0.005), None)

def set_installments(sid, n_):

    x("UPDATE sessions SET installments=? WHERE id=?", (n_, sid))
    g.pop("tot_cache", None)

@app.post("/session/<int:sid>/installments")
def session_installments(sid):

    back = safe_back(request.form.get("back")) or stage_url(sid) + "#billing"
    s_ = q("SELECT stage, type, installments FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    if s_["stage"] in ("lost", "closed"):
        return _refuse("This session is closed or canceled, so its payments don't change. Nothing was changed.", back)
    raw = (request.form.get("n") or "").strip()
    if request.form.get("redate") == "1":
        got = keep_plan_dates(sid, again=True)
        if not got:
            return _refuse("There are no dates to set again on this session. Nothing was changed.", back)
        audit("The payment plan's dates were set again from the shoot date: " + ", ".join(nice(d_) for d_ in got), sid=sid)
        session["notice"] = "The plan's dates now follow the shoot date: " + ", ".join(nice(d_) for d_ in got) + "."
        return redirect(back)
    if raw == "type":
        n_ = None
    else:
        n_ = _int_or_none(raw)
        if n_ is not None and n_ == installments_of(sid):
            return redirect(back)
        if n_ is None or not 1 <= n_ <= C.INSTALLMENTS_MAX:
            return _refuse(f"Pick from 1 to {C.INSTALLMENTS_MAX} payments. Nothing was changed.", back)
    had = installments_of(sid)
    set_installments(sid, n_)
    now_ = installments_of(sid)
    keep_plan_dates(sid, again=max(had, 1) != max(now_, 1))
    if max(had, 1) != max(now_, 1):
        audit(f"The balance is now paid in {now_} installments" if now_ >= 2 else "The balance is now one payment", sid=sid)
    return redirect(back)

app.config.setdefault("quiet_endpoints", set()).update({"session_installments", "session_reschedule_fee",
                                                         "session_credit_use", "session_credit_choice"})

def resched_fees_open(sid):

    s_ = q("SELECT type, stage, resched_settled FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["stage"] not in ("booked", "shot", "delivered") or (C.RESCHEDULE_FEE or 0) <= 0:
        return 0
    if not C.POLICY[s_["type"]]["free_reschedule"] or nothing_to_pay(sid):
        return 0
    return max(0, reschedules(sid) - 1 - (s_["resched_settled"] or 0))

RESCHED_FEE_LABEL = "Reschedule fee"

@app.post("/session/<int:sid>/reschedule-fee")
def session_reschedule_fee(sid):

    back = safe_back(request.form.get("back")) or stage_url(sid) + "#billing"
    if sent_twice():
        return _refuse_twice(back)
    if resched_fees_open(sid) <= 0:
        return _refuse("There is no reschedule fee waiting on this session. Nothing was changed.", back)
    fee = round(float(C.RESCHEDULE_FEE), 2)
    n_ = reschedules(sid)
    if request.form.get("act") == "add":
        ensure_quote(sid)

        qt_ = q("SELECT schedule_id FROM quotes WHERE session_id=?", (sid,), one=True)
        sch_ = (schedule(qt_["schedule_id"]) if qt_ else None) or current_schedule()
        it_ = next((i_ for i_ in sch_["items"] if i_.get("key") == "reschedule"), None)
        lock_tax_rate(sid)
        x("""INSERT INTO lines (session_id, label, amount, kind, fee_key, taxable, untaxed) VALUES (?,?,?, 'custom', 'reschedule', ?, ?)""",
          (sid, RESCHED_FEE_LABEL, fee, fee_taxable(it_) if it_ else 1, fee_untaxed(it_) if it_ else ""))
        reapply_discount(sid)
        audit(f"Added the reschedule fee, {money(fee)}, for reschedule {n_} (the Terms charge each one after the free one)", sid=sid)
        session["notice"] = f"Added the {money(fee)} reschedule fee to the balance."
    elif request.form.get("act") == "waive":
        audit(f"Waived the reschedule fee, {money(fee)}, for reschedule {n_}", sid=sid)
        session["notice"] = "Waived. Nothing was added."
    else:
        abort(400)
    x("UPDATE sessions SET resched_settled=COALESCE(resched_settled, 0) + 1 WHERE id=?", (sid,))
    return redirect(back)

CREDIT_OUT = "credit toward a later session"
CREDIT_IN = "credit from an earlier session"

def credit_used(cid):
    return q("""SELECT COALESCE(SUM(u.amount),0) v FROM credit_uses u JOIN payments p ON p.id=u.payment_id
                WHERE u.credit_id=? AND p.voided IS NULL""", (cid,), one=True)["v"]

def _credit_left(c_, used=None):

    used = credit_used(c_["id"]) if used is None else used
    return round(max(0.0, min(c_["amount"] - used, totals(c_["from_session"])["kept"])), 2)

def credit_of(sid):

    c_ = q("SELECT * FROM deposit_credits WHERE from_session=?", (sid,), one=True)
    if not c_:
        return None
    used = credit_used(c_["id"])
    return dict(id=c_["id"], amount=c_["amount"], at=c_["at"], used=used, left=_credit_left(c_, used))

def credits_for(sid):

    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return []
    out = []
    for c_ in q("""SELECT c.*, s.title, s.type, s.shoot_date FROM deposit_credits c JOIN sessions s ON s.id=c.from_session
                   WHERE c.household_id=? AND c.from_session != ? ORDER BY c.id""", (s_["household_id"], sid)):
        left = _credit_left(c_)
        if left > 0.005:
            out.append(dict(id=c_["id"], left=left, at=c_["at"], from_sid=c_["from_session"],
                            what=(c_["title"] or C.SESSION_TYPES.get(c_["type"], "session")) +
                            (f", {nice(c_['shoot_date'])}" if c_["shoot_date"] else "")))
    return out

def make_credit(sid):

    s_ = q("SELECT household_id, stage, lost_reason FROM sessions WHERE id=?", (sid,), one=True)
    if not _kept_case(s_) or credit_of(sid):
        return None
    kept = totals(sid)["kept"]
    if kept <= 0.005:
        return None
    x("INSERT INTO deposit_credits (household_id, from_session, amount, at) VALUES (?,?,?,?)",
      (s_["household_id"], sid, round(kept, 2), today().isoformat()))
    audit(f"The deposit, {money(kept)}, is held as a credit toward a future session", sid=sid)
    return kept

def drop_credit(sid, why="kept under the Terms instead"):

    c_ = credit_of(sid)
    if not c_ or c_["used"] > 0.005:
        return False
    x("DELETE FROM deposit_credits WHERE id=?", (c_["id"],))
    audit(f"The credit of {money(c_['amount'])} is withdrawn: the deposit is {why}", sid=sid)
    return True

@app.post("/session/<int:sid>/credit-choice")
def session_credit_choice(sid):

    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid)
    want = request.form.get("deposit")
    if want == "credit":
        if not make_credit(sid) and not credit_of(sid):
            return _refuse("There is no kept deposit on this session to hold as a credit. Nothing was changed.", back)
    elif want == "keep":
        c_ = credit_of(sid)
        if c_ and c_["used"] > 0.005:
            return _refuse("Part of this credit was already used toward another session, so it stays a credit. Nothing was changed.", back)
        drop_credit(sid)
    else:
        abort(400)
    return redirect(back)

@app.post("/session/<int:sid>/credit-use")
def session_credit_use(sid):

    back = safe_back(request.form.get("back")) or stage_url(sid) + "#billing"
    if sent_twice():
        return _refuse_twice(back)
    c_ = next((c_ for c_ in credits_for(sid) if str(c_["id"]) == (request.form.get("credit") or "")), None)
    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    t_ = totals(sid)
    if not c_ or s_["stage"] in ("lost", "closed") or t_["quoted"] <= 0.005 or t_["due"] <= 0.005:
        return _refuse("There is no credit to use here, or nothing is owed on this session. Nothing was recorded.", back)
    day = today().isoformat()
    why_ = closed_year_refusal(day)
    if why_:
        return _refuse(why_, back)
    amt = round(min(c_["left"], t_["due"]), 2)
    kind = "deposit" if t_["deposit_short"] > 0.005 else "balance"
    rid = record_payment(c_["from_sid"], "refund", amt, day, CREDIT_OUT)
    pid = record_payment(sid, kind, amt, day, CREDIT_IN)
    x("INSERT INTO credit_uses (credit_id, session_id, amount, at, refund_id, payment_id) VALUES (?,?,?,?,?,?)",
      (c_["id"], sid, amt, day, rid, pid))
    audit(f"Used {money(amt)} of the credit from the canceled {c_['what']} session", sid=sid)
    audit(f"{money(amt)} of the credit was used toward another session", sid=c_["from_sid"])
    paying_means_yes(sid)
    session["notice"] = f"Used {money(amt)} of the credit." + (f" {money(c_['left'] - amt)} of it is left." if c_["left"] - amt > 0.005 else "")
    return redirect(back)

OWED_STAGES = ("shot", "delivered", "closed")

def row_type(row):

    try:
        return row["type"]
    except (IndexError, KeyError):
        return None

def owed_now(stage, t, shoot_date=None, stype=None):

    if stype in C.BEFORE_TYPES and shoot_date and stage == "booked":
        return t["due"] > 0.005 and (balance_due_date(shoot_date, stype) or "9999") <= today().isoformat()
    if shoot_date and shoot_date > today().isoformat() and stage not in ("delivered", "closed"):
        return False
    return stage in OWED_STAGES and t["due"] > 0.005

def owed_billed(s, t):

    return owed_now(s["stage"], t, s["shoot_date"], row_type(s)) and bool(invoice_went(s["id"]))

def balance_due_date(shoot_date, stype=None, issued=None, sid=None):

    if stype in C.NET_TERMS_TYPES and issued is None and sid is not None:
        inv_ = q("SELECT issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
        if not inv_ or not inv_["issued"]:
            return None
        issued = inv_["issued"]
    if issued and stype in C.NET_TERMS_TYPES:
        return (d(issued) + timedelta(days=C.BALANCE_NET_DAYS)).isoformat()
    if not shoot_date:
        return None
    if stype in C.BEFORE_TYPES:
        return (d(shoot_date) - timedelta(days=C.BALANCE_BEFORE_DAYS)).isoformat()
    if stype in C.NET_TERMS_TYPES:
        return (d(shoot_date) + timedelta(days=C.BALANCE_NET_DAYS)).isoformat()
    days = C.delivery_days(stype)
    return (d(shoot_date) + timedelta(days=days)).isoformat()

AFTER_REFUND = {"release": "releases once nothing is owed", "hold": "stays held until paid again"}

def after_refund_default(paid_after):

    return "release" if paid_after > 0.005 else "hold"

def has_refund(sid):

    return bool(q("SELECT 1 FROM payments WHERE session_id=? AND kind='refund' AND voided IS NULL LIMIT 1", (sid,), one=True))

def after_refund(sid):

    if not has_refund(sid):
        return None
    r_ = q("SELECT refund_release FROM sessions WHERE id=?", (sid,), one=True)
    v_ = r_["refund_release"] if r_ else None
    if v_ not in AFTER_REFUND:
        v_ = after_refund_default(totals(sid)["paid"])
    return v_

def _blocked_for(t_, choice):

    if choice == "hold":
        owed = t_["quoted"] - t_["written_off"] - max(t_["paid"], 0.0)
    else:
        owed = t_["due"]
    return round(owed, 2) if owed > 0.005 else 0.0

def release_blocked(sid):

    s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["type"] in C.NET_TERMS_TYPES:
        return 0.0
    return _blocked_for(totals(sid), after_refund(sid))

def after_refund_words(sid):
    r_ = q("SELECT refund_release FROM sessions WHERE id=?", (sid,), one=True)
    v_ = r_["refund_release"] if r_ else None
    return f"After this refund, the gallery {AFTER_REFUND[v_]}" if v_ in AFTER_REFUND else ""

def refund_hold_state(sid):

    s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
    choice = after_refund(sid) if s_ and s_["type"] not in C.NET_TERMS_TYPES else None
    if not choice:
        return None
    t_ = totals(sid)
    return dict(choice=choice, owed=_blocked_for(t_, choice), if_released=_blocked_for(t_, "release"))

def balance_rule(stype, sid=None, doc="agreement"):

    return C.balance_text(stype, installments_of(sid) if sid else None, doc)

def gallery_expires(s):

    co_ = app.config.get("gallery_close_on")
    set_ = co_(s["id"]) if callable(co_) else None
    if set_:
        return set_
    back_ = gallery_restored(s["id"])
    if back_:
        return back_
    ad_ = app.config.get("gallery_adopted_end")
    adopted_ = ad_(s["id"]) if callable(ad_) else None
    if adopted_:
        return adopted_
    dl_ = app.config.get("gallery_download_online")
    opened = s["sd_shipped"] if callable(dl_) and dl_(s["id"]) else s["first_visit"]
    if not opened:
        return None
    return d(opened) + timedelta(days=C.gallery_days(s["type"]) * (2 if s["extension_used"] else 1))

def gallery_restored(sid):

    fn_ = app.config.get("gallery_restored_end")
    return fn_(sid) if callable(fn_) else None

def _fair_due(base, t, floor=None, fair=False):

    if not base:
        return None
    due = (base + timedelta(days=t["offset_days"])).isoformat()
    if floor and due < floor:
        due = floor
    if fair and not t["done"] and due != t["due"] and due < today().isoformat():
        return today().isoformat()
    return due

def stage_floors(sid, stage=None):

    s_ = q("SELECT stage, stage_changed FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return {}
    stage = stage or s_["stage"]
    if stage not in C.STAGES:
        return {}
    ci = C.STAGES.index(stage)
    out = {r_["stage"]: r_["a"][:10] for r_ in q("SELECT stage, MAX(at) a FROM stage_log WHERE session_id=? GROUP BY stage", (sid,))
           if r_["stage"] in C.STAGES and C.STAGES.index(r_["stage"]) <= ci and r_["a"]}
    if stage not in out and s_["stage_changed"]:
        out[stage] = s_["stage_changed"][:10]
    return out

def schedule_tasks(sid, fair=False, only=()):

    s = q("SELECT shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    base = d(s["shoot_date"])
    floors = stage_floors(sid)
    only = set(only or ())
    for t in q("SELECT id, stage, offset_days, due, done FROM tasks WHERE session_id=? AND offset_days IS NOT NULL", (sid,)):
        if not fair and t["due"] and t["id"] not in only:
            continue
        due = _fair_due(base, t, floors.get(t["stage"]), fair)
        if due != t["due"]:
            db().execute("UPDATE tasks SET due=? WHERE id=?", (due, t["id"]))
    _commit()

def ensure_event_task(eid, stage, title, off, sort):
    if not q("SELECT 1 FROM tasks WHERE event_id=? AND stage=? AND title=?", (eid, stage, title), one=True):
        db().execute("INSERT INTO tasks (id, event_id, stage, title, offset_days, sort) VALUES (?,?,?,?,?,?)",
                     (next_id("tasks"), eid, stage, title, off, sort))
        _commit()
    schedule_event(eid)

def schedule_event(eid, fair=False):

    e = q("SELECT date FROM events WHERE id=?", (eid,), one=True)
    base = d(e["date"]) if e else None
    for t in q("SELECT id, offset_days, due, done FROM tasks WHERE event_id=? AND offset_days IS NOT NULL", (eid,)):
        if not fair and t["due"]:
            continue
        due = _fair_due(base, t, fair=fair)
        if due != t["due"]:
            db().execute("UPDATE tasks SET due=? WHERE id=?", (due, t["id"]))
    _commit()

def event_steps(eid):
    return q("SELECT * FROM tasks WHERE event_id=? ORDER BY sort, id", (eid,))

def flow_for(sid, full=False):

    f_ = _flow_for(sid, full)
    if _pre_setting("galleries_on", C.galleries_on):
        return f_
    gal = {"gallery_built", "letter_mailed", "first_visit", "favorites"}
    return {st: [x_ for x_ in steps if not (x_[0] == "auto" and x_[1] in gal)] for st, steps in f_.items()}

def _flow_for(sid, full=False):

    s = _pre("sess", sid, None)
    s = None if s is _NONE else s
    billing = s["ev_billing"] if s is not None else None
    if s is None:
        s = q("SELECT s.type, s.event_id, s.payer_event, s.plan_of, e.billing FROM sessions s LEFT JOIN events e ON e.id=s.event_id WHERE s.id=?",
              (sid,), one=True)
        billing = s["billing"] if s else None
    if not s:
        return {}
    if s["payer_event"]:
        return C.ORGANIZER_PAYER_FLOW
    if s["event_id"] and billing == "families":
        return C.with_family_money(C.WORKFLOW[s["type"]])
    if not full and "plan_of" in s.keys() and s["plan_of"] and billed_on_plan(sid, s["plan_of"]):
        return C.without_money(C.WORKFLOW[s["type"]])
    return C.WORKFLOW[s["type"]]

def sync_workflow_tasks(sid):

    s = q("SELECT type, event_id FROM sessions WHERE id=?", (sid,), one=True)
    flow = flow_for(sid, full=True)
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
    now_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    ci = C.STAGES.index(now_["stage"]) if now_ and now_["stage"] in C.STAGES else None
    redo = []
    for (st, title), (off, sort) in want.items():
        if (st, title) in have:
            r = have[(st, title)]
            if r["offset_days"] != off:
                redo.append(r["id"])
            db().execute("UPDATE tasks SET offset_days=?, sort=? WHERE id=?", (off, sort, r["id"]))
        else:

            passed = ci is not None and C.STAGES.index(st) < ci
            tid_ = next_id("tasks")
            db().execute("INSERT INTO tasks (id, session_id, stage, title, offset_days, sort, done, outcome) VALUES (?,?,?,?,?,?,?,?)",
                         (tid_, sid, st, title, off, sort, today().isoformat() if passed else None, "na" if passed else None))
            redo.append(tid_)
    _commit()
    schedule_tasks(sid, only=redo)

def _sync_after_rules():

    if has_request_context() and g.get("tx"):
        g.sync_open_after = True
    else:
        sync_open_sessions()

def sync_open_sessions():

    for r_ in q("SELECT id FROM sessions WHERE stage NOT IN ('closed','lost')"):
        sync_workflow_tasks(r_["id"])

OUTCOMES = {"done": "Done", "waived": "Waived", "na": "Not needed", "declined": "Client declined"}

PLAN_RESIGN = "Have the plan's agreement signed again (a session joined)"

def plan_head_label(sid):

    h_ = q("SELECT h.title, h.type, h.shoot_date FROM sessions s JOIN sessions h ON h.id=s.plan_of WHERE s.id=?", (sid,), one=True)
    if not h_:
        return ""
    return (h_["title"] or C.SESSION_TYPES.get(h_["type"], h_["type"])) + (f", {nice(h_['shoot_date'])}" if h_["shoot_date"] else "")

def plan_covers(head_id, stype):

    items = {it["key"]: it for it in current_schedule()["items"]}
    label = (C.SESSION_TYPES.get(stype) or "").lower()
    names = [n for n in (label, (C.doc_label(stype) or "").lower()) if n]
    word = label.split(" ")[0] if label else ""
    stem = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
    for ln in q("SELECT fee_key, label FROM lines WHERE session_id=? AND kind NOT IN ('tax','discount')", (head_id,)):
        if (items.get(ln["fee_key"] or "") or {}).get("type") == stype:
            return True
        lab = (ln["label"] or "").lower()
        if any(n in lab for n in names) or (len(stem) >= 5 and re.search(r"\b" + re.escape(stem) + r"s?\b", lab)):
            return True
    return False

def _sdocs(ctx, kind):
    docs = [dd for dd in ctx["docs"] if dd["kind"] == kind]
    s_ = ctx.get("s")
    if kind == "agreement" and s_ is not None and "plan_of" in s_.keys() and s_["plan_of"]:
        docs = q("SELECT kind, status FROM documents WHERE session_id=? AND kind='agreement'", (s_["plan_of"],))
        if q("SELECT 1 FROM tasks WHERE session_id=? AND title=? AND done IS NULL",
             (s_["plan_of"], PLAN_RESIGN), one=True):
            return False
    return bool(docs) and all(dd["status"] == "signed" for dd in docs)

def _quoted(c):
    return c["t"]["quoted"] > 0 or bool(q("SELECT 1 FROM lines WHERE session_id=? AND kind != 'discount'", (c["s"]["id"],), one=True))

AUTO = {
    "logged": ("First contact logged", lambda c: c["n_log"] > 0 or _quoted(c), "log"),
    "date_set": ("Shoot date set", lambda c: bool(c["s"]["shoot_date"]), "shoot"),
    "quote_built": ("Quote built", lambda c: _quoted(c), "money"),
    "agreement_signed": ("Agreement signed", lambda c: _sdocs(c, "agreement"), "paperwork"),
    "releases_signed": ("Releases signed", lambda c: _sdocs(c, "release"), "paperwork"),
    "deposit_paid": ("Deposit received", lambda c: c["t"]["deposit_short"] <= 0.005 and
                     (c["t"]["paid"] > 0 or (c["t"]["deposit_req"] <= 0.005 and c["t"]["quoted"] > 0.005)), "money"),
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

_NONE = object()

def prefetch_workflows(sids):

    sids = sorted({int(s_) for s_ in sids if s_ is not None})
    if len(sids) < 10:
        return
    ids_ = json.dumps(sids)
    IN = "session_id IN (SELECT value FROM json_each(?))"
    pre = dict(ids=set(sids), sess={}, docs={}, nlog={}, tasks={}, own={}, flow={}, closes={}, slog={})
    for r_ in q("""SELECT s.*, h.name AS household, h.address AS address, e.name AS event_name, e.billing AS ev_billing
                   FROM sessions s JOIN households h ON h.id = s.household_id LEFT JOIN events e ON e.id = s.event_id
                   WHERE s.id IN (SELECT value FROM json_each(?))""", (ids_,)):
        pre["sess"][r_["id"]] = r_
    for r_ in q(f"SELECT session_id, kind, status, signed FROM documents WHERE {IN}", (ids_,)):
        pre["docs"].setdefault(r_["session_id"], []).append(r_)
    pre["nlog"] = {r_[0]: r_[1] for r_ in q(f"SELECT session_id, COUNT(*) FROM log WHERE {IN} GROUP BY session_id", (ids_,))}

    pre["now_only"] = {sid_ for sid_, r_ in pre["sess"].items() if r_["stage"] in ("closed", "lost")}
    for r_ in q(f"""SELECT t.* FROM tasks t JOIN sessions s ON s.id=t.session_id WHERE t.{IN}
                    AND (s.stage NOT IN ('closed', 'lost') OR t.own=1 OR t.stage=s.stage) ORDER BY t.id""", (ids_,)):
        pre["own" if r_["own"] else "tasks"].setdefault(r_["session_id"], []).append(r_)
    for r_ in q(f"SELECT session_id, step, outcome, note FROM step_closes WHERE {IN}", (ids_,)):
        pre["closes"].setdefault(r_["session_id"], []).append(r_)
    for r_ in q(f"SELECT id, session_id, stage, era, at FROM stage_log WHERE {IN} ORDER BY id", (ids_,)):
        pre["slog"].setdefault(r_["session_id"], []).append(r_)
    pre["galleries_on"], pre["rules_mark"] = C.galleries_on(), rules_mark()
    g.wf_pre = pre

def _pre_setting(name, read):

    pre = g.get("wf_pre")
    return pre[name] if pre else read()

def _pre(part, sid, empty=_NONE):

    pre = g.get("wf_pre")
    if not pre or sid not in pre["ids"]:
        return _NONE
    return pre[part].get(sid, empty)

def licensed(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return False
    refs = [s_["id"]] + ([s_["plan_of"]] if "plan_of" in s_.keys() and s_["plan_of"] else [])
    for ref_ in refs:
        r_ = q("SELECT data FROM doc_snapshots WHERE kind='agreement' AND ref=?", (ref_,), one=True)
        try:
            v_ = json.loads(r_["data"]) if r_ else {}
        except ValueError:
            v_ = {}
        if isinstance(v_, dict) and isinstance(v_.get("license"), bool):
            return v_["license"]
    r_ = q("SELECT data FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    try:
        v_ = json.loads(r_["data"]) if r_ else {}
    except ValueError:
        v_ = {}
    if isinstance(v_, dict) and isinstance(v_.get("license"), bool):
        return v_["license"]
    return s_["type"] in C.LICENSE_TYPES

def booked_hint(sid):

    w = workflow(sid)
    cur = w["current"]
    if not cur or cur["stage"] != "booked":
        return ""
    open_ = {st_["key"] for st_ in cur["steps"] if st_.get("key") in ("deposit_paid", "agreement_signed") and not st_["done"]}
    if open_ == {"deposit_paid", "agreement_signed"}:
        return C.dw("deposit and agreement still open")
    return C.dw("deposit still open") if "deposit_paid" in open_ else "agreement still open" if open_ else ""

app.jinja_env.globals["booked_hint"] = booked_hint

BOOKING_HOLD_DAYS = 7

def _booked_day(sid, s):
    got = _pre("slog", sid, [])
    if got is not _NONE:
        r = dict(a=min((r_["at"] for r_ in got if r_["stage"] == "booked" and r_["at"] is not None), default=None))
    else:
        r = q("SELECT MIN(at) a FROM stage_log WHERE session_id=? AND stage='booked'", (sid,), one=True)
    return d((r["a"] or "")[:10]) if r and r["a"] else d((s["stage_changed"] or "")[:10]) if s["stage_changed"] else today()

def booking_due(sid, s):
    due = _booked_day(sid, s) + timedelta(days=BOOKING_HOLD_DAYS)
    if s["shoot_date"] and d(s["shoot_date"]) < due:
        due = d(s["shoot_date"])
    return due.isoformat()

def priced_on(sid, s, quoted):

    if "priced_on" in s.keys() and s["priced_on"]:
        return d(s["priced_on"][:10])
    if quoted <= 0.005:
        return None
    r_ = q("SELECT MIN(at) a FROM quote_versions WHERE session_id=? AND at IS NOT NULL", (sid,), one=True)
    day = d(r_["a"][:10]) if r_ and r_["a"] else today()

    db().execute("UPDATE sessions SET priced_on=? WHERE id=? AND priced_on IS NULL", (day.isoformat(), sid))
    if not g.get("tx"):
        db().commit()
    return day

def deposit_due(sid, s, priced=None):

    due = d(_deposit_due(sid, s))
    return max(due, priced or due).isoformat()

def _deposit_due(sid, s):
    if s["type"] not in C.NEEDS_AGREEMENT:
        return booking_due(sid, s)
    head = s["plan_of"] if "plan_of" in s.keys() and s["plan_of"] else sid
    docs = _pre("docs", head, [])
    docs = q("SELECT status, signed FROM documents WHERE session_id=? AND kind='agreement'", (head,)) if docs is _NONE \
        else [r_ for r_ in docs if r_["kind"] == "agreement"]
    if not docs or any(r_["status"] != "signed" for r_ in docs) or (head != sid and q(
            "SELECT 1 FROM tasks WHERE session_id=? AND title=? AND done IS NULL", (head, PLAN_RESIGN), one=True)):
        return booking_due(sid, s)
    signed = max((d(r_["signed"][:10]) for r_ in docs if r_["signed"]), default=None)
    booked = today() if s["stage"] in ("inquiry", "quoted") else _booked_day(sid, s)
    return max(signed or booked, booked).isoformat()

def workflow_now(sid):

    full = g.get("wf_cache", {}).get(sid)
    if full is not None:
        return full
    cache = g.setdefault("wf_now", {})
    if sid not in cache:
        cache[sid] = _workflow(sid, only_now=True)
    return cache[sid]

def _workflow(sid, only_now=False):

    s = session_full(sid)
    docs_, n_log_, tasks_, own_, close_rows = (_pre(k_, sid, e_) for k_, e_ in
                                               (("docs", []), ("nlog", 0), ("tasks", []), ("own", []), ("closes", [])))
    if not only_now and sid in (g.get("wf_pre") or {}).get("now_only", ()):
        tasks_ = own_ = _NONE
    ctx = dict(s=s, t=totals(sid), intake=json.loads(s["intake"] or "{}"),
               docs=q("SELECT kind, status FROM documents WHERE session_id=?", (sid,)) if docs_ is _NONE else docs_,
               n_log=q("SELECT COUNT(*) n FROM log WHERE session_id=?", (sid,), one=True)["n"] if n_log_ is _NONE else n_log_)
    tasks = {(r["stage"], r["title"]): r for r in (q("SELECT * FROM tasks WHERE session_id=? AND own=0", (sid,))
                                                   if tasks_ is _NONE else tasks_)}
    own = q("SELECT * FROM tasks WHERE session_id=? AND own=1 ORDER BY id", (sid,)) if own_ is _NONE else own_
    flow = flow_for(sid)
    priced = priced_on(sid, s, ctx["t"]["quoted"])
    if close_rows is _NONE:
        close_rows = q("SELECT step, outcome, note FROM step_closes WHERE session_id=?", (sid,))
    closes = {r["step"]: r["outcome"] for r in close_rows}
    close_how = {r["step"]: r["note"] for r in close_rows if r["outcome"] == "done" and r["note"]}
    extra = {}
    for fn in hooks.steps:
        for st, label, done, tab in (fn(s) or []):
            extra.setdefault(st, []).append(dict(label=label, done=done, kind="auto", tab=tab))
    stages = []
    free_ = nothing_to_pay(sid, ctx["t"])
    for st in C.STAGES:
        steps = []
        if only_now and st != s["stage"]:
            stages.append(dict(stage=st, steps=steps, n_done=0))
            continue
        for step in flow.get(st, []):
            if step[0] == "auto":
                if step[1] in ("deposit_paid", "balance_paid") and ctx["t"]["quoted"] == 0:
                    continue
                if step[1] in ("releases_signed",) and not any(dd["kind"] == "release" for dd in ctx["docs"]):
                    continue
                label, fn, tab = AUTO[step[1]]
                ov = closes.get(step[1])
                due_ = deposit_due(sid, s, priced) if (st == "booked" and step[1] == "deposit_paid") else \
                    booking_due(sid, s) if (st == "booked" and step[1] in ("agreement_signed", "releases_signed")) else \
                    balance_due_date(s["shoot_date"], s["type"], sid=sid) if step[1] == "balance_paid" else None
                done_, out_ = bool(ov) or fn(ctx), ov if ov and ov != "done" else None
                if step[1] == "letter_mailed" and not done_ and (s["first_visit"] or s["favorites"] or s["retouch_picks"] or s["veto"]):
                    done_, out_ = True, "na"
                steps.append(dict(label=label, done=done_, kind="auto", tab=tab, key=step[1], due=due_,
                                  outcome=out_, how=close_how.get(step[1], "")))
            elif step[0] == "event" and s["event_id"]:
                r = q("SELECT * FROM tasks WHERE event_id=? AND stage=? AND title=?", (s["event_id"], st, step[1]), one=True)
                if r:
                    steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"], outcome=r["outcome"] if r["outcome"] not in (None, "done") else None,
                                      shared=True))
            else:
                if step[1] == SEND_TASKS["invoice"] and free_:
                    continue
                r = tasks.get((st, step[1]))
                if r:

                    why_ = (r["outcome_note"] or "")[len("Settled: "):] if r["done"] and r["outcome"] == "done" \
                        and (r["outcome_note"] or "").startswith("Settled: ") else ""
                    steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"],
                                      outcome=r["outcome"] if r["outcome"] not in (None, "done") else None, settled=why_))
        steps += extra.get(st, [])
        for r in (r_ for r_ in own if (r_["stage"] or s["stage"]) == st):
            steps.append(dict(label=r["title"], done=bool(r["done"]), kind="do", task_id=r["id"], due=r["due"], outcome=r["outcome"] if r["outcome"] not in (None, "done") else None, mine=True))
        stages.append(dict(stage=st, steps=steps, n_done=sum(x_["done"] for x_ in steps)))
    cur = next((g_ for g_ in stages if g_["stage"] == s["stage"]), None)
    carry_ = set() if only_now else carried_stages(sid, s["stage"])
    carried = []
    for g_ in stages:
        if g_["stage"] in carry_:
            for st_ in g_["steps"]:
                st_["carried"] = True
                if not st_["done"]:
                    carried.append(dict(st_, from_stage=g_["stage"]))
    nxt_stage = None
    if s["stage"] in C.STAGES and C.STAGES.index(s["stage"]) + 1 < len(C.STAGES):
        nxt_stage = C.STAGES[C.STAGES.index(s["stage"]) + 1]
    next_step = next((st_ for st_ in (cur["steps"] if cur else []) if not st_["done"]), None)
    ready = cur is not None and all(st_["done"] for st_ in cur["steps"])

    return dict(stages=stages, current=cur, next_stage=nxt_stage, next_step=next_step, ready=ready, wait_close=None,
                carried=carried)

def create_session(household_id, type_, stage="inquiry", title="", shoot_date=None, event_id=None,
                   subject_ids=()):
    sid = x("""INSERT INTO sessions (id, household_id, type, stage, title, shoot_date, event_id, gallery_id, created, stage_changed)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (next_id("sessions"), household_id, type_, stage, title, shoot_date or None, event_id, sealed.link_id("g"), now_local(), now_local()))
    for pid in subject_ids:
        x("INSERT OR IGNORE INTO session_people VALUES (?,?)", (sid, pid))
    x("INSERT INTO stage_log (session_id, stage) VALUES (?,?)", (sid, stage))
    signers = []
    if type_ in C.NEEDS_AGREEMENT:
        signers = default_signers(household_id, type_, [shoot_date])
        for p_ in signers or [None]:
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'agreement', ?, ?)",
              (sid, p_["id"] if p_ else None, p_["name"] if p_ else ""))
    signer_ids = {p_["id"] for p_ in signers}
    if type_ in C.NEEDS_RELEASE:
        for pid in subject_ids:
            if pid in signer_ids:
                continue
            p = q("SELECT * FROM people WHERE id=?", (pid,), one=True)
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'release', ?, ?)",
              (sid, pid, release_signer(p, shoot_date)))
    sync_workflow_tasks(sid)
    hooks.fire("session_created", sid)
    return sid

def _ensure_release(sid, type_, pid):

    releases_follow_subjects(sid, {pid})

def add_subjects(sid, pids):

    s_ = q("SELECT id, type, household_id FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return []
    mine = {r_["id"] for r_ in q("SELECT id FROM people WHERE household_id=?", (s_["household_id"],))}
    have = {r_["person_id"] for r_ in q("SELECT person_id FROM session_people WHERE session_id=?", (sid,))}
    added = []
    for pid in pids:
        if pid in mine and pid not in have:
            x("INSERT OR IGNORE INTO session_people VALUES (?,?)", (sid, pid))
            _ensure_release(sid, s_["type"], pid)
            have.add(pid)
            added.append(pid)
    return added

def open_sessions_without(pid):

    p = q("SELECT household_id FROM people WHERE id=?", (pid,), one=True)
    if not p:
        return []
    return q("""SELECT * FROM sessions s WHERE household_id=? AND stage NOT IN ('closed','lost')
                AND NOT EXISTS (SELECT 1 FROM session_people sp WHERE sp.session_id=s.id AND sp.person_id=?)
                ORDER BY COALESCE(shoot_date,'9999'), id""", (p["household_id"], pid))

def agreement_days(sid):

    if not sid:
        return []
    return [r_["shoot_date"] for r_ in plan_sessions(plan_head(sid)) if r_["shoot_date"]]

COUPLE_REL = {"partner", "wife", "husband", "spouse", "fiance", "fiancee", "fianc\u00e9", "fianc\u00e9e", "bride", "groom"}
CONTACT_ONLY_REL = {"planner", "wedding planner", "coordinator", "day-of coordinator"}

def contact_only(p_, type_):

    return type_ in C.COUPLE_TYPES and (p_["relation"] or "").strip().lower() in CONTACT_ONLY_REL

def booker(household_id, type_=None, days=()):

    days = [d_ for d_ in days if d_] or [None]
    return next((p_ for p_ in q("SELECT * FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY id",
                                (household_id,))
                 if not contact_only(p_, type_) and not any(minor_on(p_, d_) for d_ in days)), None)

BOOKER_FIRST = """id = (SELECT MIN(b_.id) FROM people b_ WHERE b_.household_id=people.household_id
                         AND b_.role IN ('parent','adult') AND COALESCE(b_.is_minor, 0)=0) DESC, role='parent' DESC"""

def client_people(household_id, type_, email=False):

    ppl = q("""SELECT * FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY role='parent' DESC, id""",
            (household_id,))
    b_ = booker(household_id, type_)
    ppl = [p_ for p_ in ppl if not contact_only(p_, type_) and (not email or (p_["email"] or "").strip())]
    if type_ in C.COUPLE_TYPES:
        ppl = [p_ for p_ in ppl if p_["role"] == "adult"] + [p_ for p_ in ppl if p_["role"] != "adult"]
    elif b_ is not None:
        ppl = [p_ for p_ in ppl if p_["id"] == b_["id"]] + [p_ for p_ in ppl if p_["id"] != b_["id"]]
    return ppl

def couple_of(household_id, type_, days=()):

    if type_ not in C.COUPLE_TYPES:
        return []
    days = [d_ for d_ in days if d_] or [None]
    rel = lambda p_: (p_["relation"] or "").strip().lower()
    everyone = q("SELECT * FROM people WHERE household_id=? ORDER BY id", (household_id,))
    ppl = [p_ for p_ in everyone if p_["role"] == "adult" and not any(minor_on(p_, d_) for d_ in days)]
    named = [p_ for p_ in ppl if rel(p_) in COUPLE_REL]
    if len(named) == 1:
        b_ = next((p_ for p_ in everyone if p_["role"] in ("parent", "adult") and not contact_only(p_, type_)
                   and not any(minor_on(p_, d_) for d_ in days)), None)
        if b_ is not None and b_["role"] == "adult" and not rel(b_) and b_["id"] != named[0]["id"]:
            named = sorted(named + [b_], key=lambda p_: p_["id"])
    if named:
        return named[:2]
    return ppl[:2] if not any(rel(p_) for p_ in everyone) else []

def couple_names(couple):

    return " and ".join(p_["name"] for p_ in couple if (p_["name"] or "").strip())

def signer_choices(household_id, days=(), type_=None):

    days = [d_ for d_ in days if d_] or [None]
    ppl = q("""SELECT * FROM people WHERE household_id=? AND role IN ('parent','adult','child')
               ORDER BY role='parent' DESC, role='adult' DESC, id""", (household_id,))
    out = [p_ for p_ in ppl if (p_["role"] != "child" or born_on(p_)) and not any(minor_on(p_, d_) for d_ in days)]
    first = [p_["id"] for p_ in couple_of(household_id, type_, days)] if type_ else []
    b_ = booker(household_id, type_, days)
    if b_ is not None and b_["id"] not in first:
        first.append(b_["id"])
    return [p_ for i_ in first for p_ in out if p_["id"] == i_] + [p_ for p_ in out if p_["id"] not in first]

def default_signers(household_id, type_, days=()):

    couple = couple_of(household_id, type_, days)
    if couple:
        return list(couple) if C.ALL_SIGN.get(type_) else list(couple[:1])
    ch = [p_ for p_ in signer_choices(household_id, days, type_) if not contact_only(p_, type_)]
    grown = [p_ for p_ in ch if p_["role"] != "child"] or ch
    if type_ in C.COUPLE_TYPES:
        grown = [p_ for p_ in grown if p_["role"] == "adult"] or grown
    return list(grown) if C.ALL_SIGN.get(type_) else list(grown[:1])

def agreement_signers(sid):

    rows = q("""SELECT d.*, p.name AS pname FROM documents d LEFT JOIN people p ON p.id=d.about_person
                WHERE d.session_id=? AND d.kind='agreement' ORDER BY d.id""", (sid,))
    names = [r_["pname"] for r_ in rows if r_["pname"]]
    if not names:
        s_ = q("SELECT household_id, type FROM sessions WHERE id=?", (sid,), one=True)
        ch = signer_choices(s_["household_id"], agreement_days(sid), s_["type"]) if s_ else []
        names = [ch[0]["name"]] if ch else []
    return rows, names

@app.post("/session/<int:sid>/signers")
def session_signers(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    allowed = {r_["id"]: r_["name"] for r_ in signer_choices(s_["household_id"], agreement_days(sid))}
    want = [int(v_) for v_ in request.form.getlist("signer") if v_.isdigit() and int(v_) in allowed]
    back = safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#paperwork"
    if not want:
        session["notice"] = "At least one person signs the agreement."
        g.refused = True
        return redirect(back)
    rows = q("SELECT * FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (sid,))
    have = {r_["about_person"]: r_ for r_ in rows if r_["about_person"]}
    for r_ in rows:
        if r_["about_person"] is None:
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

    if not q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True):
        return False
    r = q("SELECT removed_at FROM galleries WHERE session_id=?", (sid,), one=True)
    return bool(r and r["removed_at"])

def _gallery_to_close(sid, s_, f):

    g_ = q("SELECT * FROM galleries WHERE session_id=?", (sid,), one=True) \
        if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
    if g_ and g_["removed_at"]:
        return False
    return bool(s_["gallery_built"] or (f.get("gallery_built") or "").strip()
                or (g_ and (g_["built_at"] or g_["uploaded_at"])))

def can_enter(stage, shoot_date, sid=None):

    if stage == "shot":
        if sid and (q("SELECT date_tentative FROM sessions WHERE id=?", (sid,), one=True) or {"date_tentative": 0})["date_tentative"]:
            return False
        return bool(shoot_date and shoot_date <= today().isoformat())
    if stage == "closed" and sid:
        s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        if s_ and s_["gallery_built"]:
            g_ = q("SELECT * FROM galleries WHERE session_id=?", (sid,), one=True) \
                if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
            if g_ and g_["removed_at"]:
                return True
            if not s_["first_visit"] and not (g_ and "uploaded_at" in g_.keys() and g_["uploaded_at"]):
                return True
            exp = gallery_expires(s_)
            return bool(exp and exp < today())
    return True

def advance(sid, log=True):

    moves = []
    settle_steps(sid)
    s0 = q("SELECT stage, shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    if s0 and s0["stage"] == "shot" and s0["shoot_date"] and s0["shoot_date"] > today().isoformat():
        set_stage(sid, "booked")
        audit(f"Moved back to Booked (the shoot is {nice(s0['shoot_date'])}, still ahead)", sid=sid, who="Lumen Aureum")
        try:
            session["notice"] = f"Still Booked: the shoot is {nice(s0['shoot_date'])}."
        except RuntimeError:
            pass
    for _ in range(len(C.STAGES) + 1):
        s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        if not s_ or s_["stage"] not in C.STAGES:
            return moves
        w = workflow(sid)
        if s_["hold_stage"] == s_["stage"]:
            if w["ready"]:
                return moves
            x("UPDATE sessions SET hold_stage=NULL WHERE id=?", (sid,))
            return moves
        picks = bool((s_["favorites"] or "").strip() or (s_["retouch_picks"] or "").strip() or (s_["veto"] or "").strip())
        if picks and s_["stage"] in ("booked", "shot") and not been_delivered(sid):

            to = "shot" if s_["stage"] == "booked" else "delivered"
            if to == "shot" and not can_enter("shot", s_["shoot_date"], sid):
                return moves

            why_ = "the files were delivered" if to == "delivered" and s_["sd_shipped"] else "the client's picks came in"
            if move_on(sid, to, why_, who="Lumen Aureum", log=log):
                moves.append((to, why_))
                continue
            return moves
        if (s_["stage"] == "delivered" and w["ready"] and not w["carried"] and nothing_owed(sid) and not owed_back(sid)
                and not replacing(sid) and not retouch_unsent(sid)

                and not (app.config.get("gallery_holds_open") or (lambda s__: False))(s_)):
            if move_on(sid, "closed", "delivered and paid in full", who="Lumen Aureum", log=log):
                moves.append(("closed", "delivered and paid in full"))
            return moves
        if not (w["ready"] and w["next_stage"] and w["current"] and w["current"]["steps"]):
            return moves
        if w["next_stage"] in ("quoted", "shot", "closed"):
            return moves

        if not can_enter(w["next_stage"], s_["shoot_date"], sid):
            return moves
        why_ = f"all {C.STAGE_LABELS[s_['stage']]} steps are done"
        if not move_on(sid, w["next_stage"], why_, who="Lumen Aureum", log=log):
            return moves
        moves.append((w["next_stage"], why_))
    return moves

def been_delivered(sid):
    return bool(q("SELECT 1 FROM stage_log WHERE session_id=? AND stage IN ('delivered','closed')", (sid,), one=True))

def nothing_owed(sid):

    return totals(sid)["due"] <= 0.005

def retouch_unsent(sid):

    s_ = q("SELECT id, retouch_picks FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or not (s_["retouch_picks"] or "").strip():
        return False
    t_ = q("SELECT outcome FROM tasks WHERE session_id=? AND title='Send retouched photos' AND own=0", (sid,), one=True)
    if t_ and t_["outcome"] in ("na", "waived", "declined"):
        return False
    return retouch_state(s_)["n_fin"] == 0

def owed_back(sid):

    return totals(sid)["due"] < -0.005

STAGE_RULES_MARK = "stage_rules_mark"
STAGE_RULES_TODO = "stage_rules_todo"

def rules_mark():

    try:
        return int(setting(STAGE_RULES_MARK))
    except (TypeError, ValueError):
        return None

def carried_stages(sid, stage):

    mark = _pre_setting("rules_mark", rules_mark)
    if mark is None or stage not in C.STAGES:
        return set()
    got = _pre("slog", sid, [])
    if got is _NONE:
        got = q("SELECT id, stage, era FROM stage_log WHERE session_id=? ORDER BY id", (sid,))
    rows = [(r_["id"], C.STAGES.index(r_["stage"])) for r_ in got if r_["stage"] in C.STAGES]
    legacy = {r_["id"] for r_ in got if r_["era"] == "1.4"}
    out = set()
    for i, st in enumerate(C.STAGES[:C.STAGES.index(stage)]):
        last_in = max((id_ for id_, j in rows if j <= i), default=0)
        if not last_in:
            continue
        left = min((id_ for id_, j in rows if id_ > last_in and j > i), default=None)
        if left is not None and left not in legacy:
            out.add(st)
    return out

def _forward(old, to):
    return old in C.STAGES and to in C.STAGES and C.STAGES.index(to) > C.STAGES.index(old)

KEEP_OPEN_ON_DELIVERY = ("deposit_paid", "balance_paid", "agreement_signed", "releases_signed")

def _past_its_moment(s, st_):

    return (s["stage"] in ("delivered", "closed") and st_.get("from_stage") in ("inquiry", "quoted", "booked")
            and not st_.get("shared") and not st_.get("mine") and st_.get("key") not in KEEP_OPEN_ON_DELIVERY)

def move_on(sid, to, why, who="Owner", log=True):

    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["stage"] == to:
        return False
    old = s_["stage"]
    forward = _forward(old, to)
    set_stage(sid, to)
    if (q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True) or {"stage": None})["stage"] != to:
        return False
    not_needed = []
    if forward and to == "shot":
        for t_ in q("""SELECT id, title FROM tasks WHERE session_id=? AND stage='booked' AND own=0 AND done IS NULL
                       AND offset_days IS NOT NULL AND offset_days < 0 ORDER BY sort, id""", (sid,)):
            x("UPDATE tasks SET done=?, outcome='na' WHERE id=? AND done IS NULL", (today().isoformat(), t_["id"]))
            not_needed.append(t_["title"])
    if forward and to == "delivered":

        for g_ in workflow(sid)["stages"]:
            if g_["stage"] not in ("inquiry", "quoted", "booked"):
                continue
            for st_ in g_["steps"]:
                if st_["done"]:
                    continue
                if st_["kind"] == "auto" and st_.get("key") and st_["key"] not in KEEP_OPEN_ON_DELIVERY:
                    x("""INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?,?, 'na', ?)
                         ON CONFLICT(session_id, step) DO NOTHING""", (sid, st_["key"], "Closed on delivery"))
                    not_needed.append(st_["label"])
                elif st_["kind"] == "do" and st_.get("task_id") and not st_.get("shared") and not st_.get("mine"):
                    x("UPDATE tasks SET done=?, outcome='na' WHERE id=? AND done IS NULL", (today().isoformat(), st_["task_id"]))
                    not_needed.append(st_["label"])
    if forward:
        x("UPDATE sessions SET hold_stage=NULL WHERE id=?", (sid,))
        schedule_tasks(sid, only=[r_["id"] for r_ in q("SELECT id FROM tasks WHERE session_id=? AND stage=? AND done IS NULL",
                                                         (sid, to))])
    if log:
        still = [st_["label"] for st_ in workflow(sid)["carried"]] if forward else []
        line = f"Moved to {C.STAGE_LABELS.get(to, to)}: {why}." + (f" Still open: {', '.join(map(step_says, still))}." if still else "") + (
            f" Not needed: {', '.join(map(step_says, not_needed))}." if not_needed else "")
        audit_after(line, sid=sid, who=who)
    return True

def audit_after(action, sid=None, who="Owner", note=""):

    if has_request_context() and "audit" in g:
        g.setdefault("after_audit", []).append(dict(action=action, sid=sid, who=who, note=note))
    else:
        audit(action, sid=sid, who=who, note=note)

_ADV = {"at": 0.0}

NO_ADVANCE_ENDPOINTS = {"session_pencil"}

def advance_all(force=False):

    import time as _t
    if not force and _t.time() - _ADV["at"] < 300:
        return
    _ADV["at"] = _t.time()
    for r in q("SELECT id FROM sessions WHERE stage NOT IN ('closed','lost')"):
        advance(r["id"])

def _advance_after_post(resp):

    if (request.method == "POST" and resp.status_code < 400 and datakey.is_unlocked() and _unlocked_once
            and request.endpoint not in QUIET_ENDPOINTS | NO_ADVANCE_ENDPOINTS | app.config.get("quiet_endpoints", set())):
        try:
            sid_ = g.get("touched_sid")
            if sid_:
                advance(sid_)
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
        return
    if stage in ("booked", "shot", "delivered", "closed"):
        lock_tax_rate(sid)
        lock_deposit(sid)
    if stage not in LEAD_STAGES:
        pen_ = q("SELECT pencil_until, shoot_date FROM sessions WHERE id=?", (sid,), one=True)
        if pen_ and pen_["pencil_until"]:
            x("UPDATE sessions SET pencil_until=NULL WHERE id=?", (sid,))
            if stage == "booked" and pen_["shoot_date"]:
                audit_after(f"The penciled date, {nice(pen_['shoot_date'][:10])}, is now the booked date", sid)
    x("INSERT INTO stage_log (session_id, stage) VALUES (?,?)", (sid, stage))
    if stage in ("booked", "shot", "delivered"):
        keep_plan_dates(sid)
    money_changed(sid, "reopened" if old == "lost" else "canceled" if stage == "lost" else "changed")
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
    out["paperwork"] = q("""SELECT doc.*, s.id AS sid, s.shoot_date, h.name AS household, COALESCE(NULLIF(doc.covers, ''), p.name) AS person
        FROM documents doc JOIN sessions s ON s.id=doc.session_id JOIN households h ON h.id=s.household_id
        LEFT JOIN people p ON p.id=doc.about_person
        WHERE doc.status != 'signed' AND s.stage NOT IN ('lost','closed')
          AND s.shoot_date IS NOT NULL AND s.shoot_date <= ? ORDER BY s.shoot_date""",
                         ((t + timedelta(days=14)).isoformat(),))
    owing = []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage IN ('shot','delivered','closed')"""):
        tt = totals(s["id"])
        if owed_billed(s, tt):
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

    if FUTURE_TYPE not in C.SESSION_TYPES:
        return []
    t = today()
    school_year_end = t.year + (1 if t.month >= 7 else 0)
    rows = q("""SELECT p.*, h.name AS household FROM people p JOIN households h ON h.id=p.household_id
        WHERE p.grad_year IN (?, ?) AND NOT EXISTS (
          SELECT 1 FROM session_people sp JOIN sessions s ON s.id=sp.session_id
          WHERE sp.person_id=p.id AND s.type=?)""", (school_year_end, school_year_end + 1, FUTURE_TYPE))
    return rows

MARKABLE = {"gallery_built": "Mark built", "letter_mailed": "Mark mailed", "first_visit": "Mark opened",
            "sd_shipped": "Mark delivered"}

THEIR_STEPS = {"Intake answered", "Client said yes", "Agreement signed", "Releases signed", "Deposit received",
               "Client opened the gallery", "Picks received", "Paid in full"}
THEIR_WORDS = {"Intake answered": "Intake answers", "Client said yes": "Quote accepted", "Agreement signed": "Agreement signed",
               "Releases signed": "Releases signed", "Deposit received": "Collect the deposit", "Client opened the gallery": "Gallery opened",
               "Picks received": "Picks sent", "Paid in full": "Balance paid"}
def todo_word(label):

    return C.dw(hooks.step_labels.get(label) or TODO_WORDS.get(label, label))

TODO_WORDS = {"First contact logged": "Log the first contact", "Intake answered": "Get the intake answers",
              "Quote built": "Build the quote", "Agreement signed": "Get the agreement signed",
              "Releases signed": "Get the releases signed", "Deposit received": "Collect the deposit",
              "Shoot date set": "Pick a shoot date", "Gallery built": "Build gallery",
              "QR letter mailed": "Mail QR letter", "Client opened the gallery": "Client opens the gallery",
              "SD card shipped": "Ship SD card",
              "Picks received": "Get their picks",
              "Paid in full": "Collect the balance",

              "Scout the venue, dial in settings": "Scout the venue", "Confirm the venue's photo rules": "Venue photo rules",
              "Send session details": "Send the session sheet",
              "Back up photos": "Upload original photos", "Edit": "Upload edited photos"}

STEP_SAYS = {"Send session details": "Send the session sheet", "Back up photos": "Upload original photos",
             "Edit": "Upload edited photos"}

def step_says(label):
    return C.dw(STEP_SAYS.get(label, label))
LINK_VERBS = {"logged": ("Log contact", "log"), "intake_done": ("Fill in their answers", "intake"),
              "quote_built": ("Build quote", "money"), "deposit_paid": ("Record deposit", "money"),
              "date_set": ("Set date", "shoot"), "favorites": ("Record picks", "delivery")}

INTAKE_SENT = "Sent the intake questions"

def client_step_sent(s, key):

    sid = s["id"]
    if key == "intake_done":
        return bool(q("SELECT 1 FROM log WHERE session_id=? AND summary=?", (sid, INTAKE_SENT), one=True)) or any(
            (v_ or "").strip() for v_ in json.loads(s["intake"] or "{}").values() if isinstance(v_, str))
    if key in ("agreement_signed", "releases_signed"):
        kind_ = "agreement" if key == "agreement_signed" else "release"
        ref_ = plan_head(sid) if kind_ == "agreement" else sid
        docs_ = q("SELECT status, sent FROM documents WHERE session_id=? AND kind=?", (ref_, kind_))
        return bool(docs_) and not any(r_["status"] == "needed" and not r_["sent"] for r_ in docs_)
    if key == "deposit_paid":
        return bool(q("SELECT 1 FROM invoice_sent WHERE session_id=?", (sid,), one=True)
                    or q("SELECT 1 FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True))
    return True

def intake_draft(sid):

    s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
    qs_ = C.INTAKE.get(s_["type"] if s_ else "", []) or []
    lines = ["Before your session, could you answer a few questions? Just reply to this email with your answers.",
             "\n".join(f"{i}. {label}" for i, (_k, label) in enumerate(qs_, 1))]
    mail_ = _send_ctx(sid, "intake", "A few questions before your session", [l_ for l_ in lines if l_])
    return mail_["mailto"] if mail_["to"] else ""

app.jinja_env.globals["intake_draft"] = intake_draft

def mail_preview(mailto):

    from urllib.parse import unquote, urlsplit, parse_qs
    parts_ = urlsplit(mailto or "")
    qs_ = parse_qs(parts_.query, keep_blank_values=True)
    return dict(to=unquote(parts_.path), subject=(qs_.get("subject") or [""])[0], body=(qs_.get("body") or [""])[0])

app.jinja_env.globals["mail_preview"] = mail_preview
app.jinja_env.globals["length_words"] = lambda m_: length_words(m_)

def _theirs_after_send(a, s, key, tt):

    if client_step_sent(s, key):
        return
    sid = s["id"]
    a.update(theirs=False, close=a.get("close"))
    if key == "intake_done":
        draft_ = intake_draft(sid)
        if draft_:
            for k_ in ("form", "href"):
                a.pop(k_, None)
            a.update(what="Send the intake questions", verb="Send the intake questions", mail=draft_,
                     post=url_for("session_intake_send", sid=sid), fields={}, sid=sid,
                     preview=True)
        else:
            a.update(what="Ask the intake questions (no email on file)")
    elif key == "agreement_signed":
        a.update(what="Send the agreement", doc=url_for("agreement", sid=sid), doc_verb="Send the agreement", doc_here=False)
    elif key == "releases_signed":
        rel_ = q("SELECT id FROM documents WHERE session_id=? AND kind='release' AND status='needed' AND sent IS NULL ORDER BY id", (sid,))
        if len(rel_) == 1:
            a.update(what="Send the release", doc=url_for("release_doc", did=rel_[0]["id"]), doc_verb="Send the release", doc_here=False)
        else:
            a.update(what=f"Send the {len(rel_)} releases", doc=url_for("session_edit", sid=sid) + "#paperwork",
                     doc_verb=f"Send the {len(rel_)} releases", doc_here=True)
    elif key == "deposit_paid":
        for k_ in ("form", "href"):
            a.pop(k_, None)
        a.update(what=C.dw("Send the deposit invoice"), doc=url_for("invoice", sid=sid), doc_verb=C.dw("Send the deposit invoice"),
                 doc_here=False)

app.config.setdefault("quiet_endpoints", set()).add("session_intake_send")

@app.post("/session/<int:sid>/intake/send")
def session_intake_send(sid):

    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (s_["household_id"], sid, "email", INTAKE_SENT, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    mail_ = intake_draft(sid)
    if request.form.get("mail") and mail_:
        return redirect(mail_, code=303)
    return redirect(safe_back(request.form.get("back")) or url_for("home"))

@app.post("/session/<int:sid>/docs/sent")
def docs_mark_sent(sid):

    kind_ = request.values.get("kind")
    if kind_ not in ("agreement", "release"):
        abort(400)
    ref_ = plan_head(sid) if kind_ == "agreement" else sid
    day = today().isoformat()
    did_ = request.values.get("did", "")
    x("UPDATE documents SET sent=?, status=CASE WHEN status='needed' THEN 'sent' ELSE status END WHERE session_id=? AND kind=?"
      " AND sent IS NULL" + (" AND id=?" if did_.isdigit() else ""), (day, ref_, kind_) + ((int(did_),) if did_.isdigit() else ()))
    return redirect(safe_back(request.form.get("back")) or stage_url(sid))

@app.post("/session/<int:sid>/invoice/deposit-sent")
def deposit_invoice_sent(sid):

    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    _invoice_marked_sent(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("invoice", sid=sid))

def event_reps():

    reps = {}
    for r in q("SELECT id, event_id, stage FROM sessions WHERE event_id IS NOT NULL AND stage NOT IN ('closed','lost')"):
        i = C.STAGES.index(r["stage"]) if r["stage"] in C.STAGES else 99
        if r["event_id"] not in reps or i < reps[r["event_id"]][1]:
            reps[r["event_id"]] = (r["id"], i)
    return {eid: v[0] for eid, v in reps.items()}

def session_minutes(sid, stype, default=True):

    r = q("SELECT duration_min FROM sessions WHERE id=?", (sid,), one=True)
    if r and r["duration_min"]:
        return r["duration_min"]
    mins = 0
    items = {it["key"]: it for it in current_schedule()["items"]}
    for ln in q("SELECT fee_key, qty FROM lines WHERE session_id=? AND fee_key != ''", (sid,)):
        it = items.get(ln["fee_key"], {})
        if it.get("type") and it["type"] != stype and it["type"] in C.SESSION_TYPES:
            continue
        if it.get("unit") == "hour":
            mins += 60 * (ln["qty"] or 1)
        else:
            mins += int(it.get("minutes") or 0)
    return int(mins or (C.TYPE_MINUTES[stype] if default else 0))

def _span(sid, stype, day, hm, default=True):
    try:
        h_, m_ = (int(v) for v in hm.split(":")[:2])
    except (AttributeError, ValueError):
        return None
    start = datetime.combine(d(day), datetime.min.time()) + timedelta(hours=h_, minutes=m_)
    return start, start + timedelta(minutes=session_minutes(sid, stype, default))

def clashes(sid):

    s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s or not s["shoot_date"] or s["stage"] in ("lost", "closed"):
        return []
    return clashes_at(sid, s["type"], s["shoot_date"], s["start_time"], s["event_id"] or s["payer_event"])

def clashes_at(sid, stype, day, hm, event_id=None):

    mine = _span(sid, stype, day, hm)
    own_ = q("SELECT event_id, payer_event FROM sessions WHERE id=?", (sid,), one=True) if sid else None
    billing_ = bool(own_ and not own_["event_id"] and own_["payer_event"])
    out = []
    for o in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.shoot_date=? AND s.id != ? AND s.stage NOT IN ('lost','closed')
                  AND (s.event_id IS NULL OR s.event_id IS NOT ? OR ?)
                  AND (s.payer_event IS NULL OR s.payer_event IS NOT ?)""",
               (day, sid or 0, event_id, 0 if billing_ else 1, event_id)):
        theirs = _span(o["id"], o["type"], o["shoot_date"], o["start_time"])
        pen_ = penciled(o)
        if event_id is not None and o["event_id"] == event_id:

            a_, b_ = _span(sid, stype, day, hm, default=False), _span(o["id"], o["type"], o["shoot_date"], o["start_time"], default=False)
            if a_ and b_ and (a_[0] == b_[0] or (a_[0] < b_[1] and b_[0] < a_[1])):
                out.append(dict(name=o["household"], hid=o["household_id"], sid=o["id"], penciled=pen_,
                                when=f"{clock(o['start_time'])}, same event" + (", penciled" if pen_ else "")))
            continue
        if not mine or not theirs:
            when_ = f"same day at {clock(o['start_time'])}" if theirs else "same day, no start time yet"
            out.append(dict(name=o["household"], hid=o["household_id"], sid=o["id"], penciled=pen_,
                            when=when_ + (", penciled" if pen_ else "")))
            continue
        buf = timedelta(minutes=C.BOOKING_BUFFER_MIN)
        if mine[0] < theirs[1] + buf and theirs[0] < mine[1] + buf:
            out.append(dict(name=o["household"], hid=o["household_id"], sid=o["id"], penciled=pen_,
                            when=f"{clock(o['start_time'])} to {clock(theirs[1])}" + (", penciled" if pen_ else "")))
    return out

LEAD_STAGES = ("inquiry", "quoted")

def penciled(s):

    keys_ = s.keys()
    return bool("pencil_until" in keys_ and s["pencil_until"] and s["stage"] in LEAD_STAGES and s["shoot_date"])

def pencil_lapsed(s):

    return penciled(s) and s["pencil_until"][:10] < today().isoformat()

def date_holds(day, sid=0):

    try:
        day = d(str(day or "")[:10]).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None
    booked_, pencils_ = 0, 0
    seen_ev = set()
    for o in q("""SELECT id, stage, shoot_date, pencil_until, event_id FROM sessions WHERE shoot_date=? AND id != ?
                  AND stage NOT IN ('lost','closed')""", (day, sid or 0)):
        if o["stage"] in LEAD_STAGES:
            pencils_ += 1 if penciled(o) else 0
        elif not o["event_id"] or o["event_id"] not in seen_ev:
            booked_ += 1
            if o["event_id"]:
                seen_ev.add(o["event_id"])
    if not booked_ and not pencils_:
        return None
    parts_ = ([("a booking" if booked_ == 1 else f"{booked_} bookings")] if booked_ else []) + \
             ([("1 pencil" if pencils_ == 1 else f"{pencils_} pencils")] if pencils_ else [])
    return dict(booked=booked_, pencils=pencils_, words=f"{nice(day)} already has {' and '.join(parts_)}.")

app.jinja_env.globals.update(penciled=penciled, pencil_lapsed=pencil_lapsed, date_holds=date_holds,
                             PENCIL_DAYS=C.PENCIL_DAYS)

def _pencil_until_in(raw, floor):

    try:
        until_ = d(str(raw or "")[:10]).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None
    return until_ if until_ >= floor else None

@app.post("/session/<int:sid>/pencil")
def session_pencil(sid):

    s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    f = request.form
    act = f.get("act")
    t_ = today().isoformat()
    back_ = safe_back(f.get("back")) or url_for("client_stage", hid=s["household_id"], s=sid)
    refuse_ = None
    if act == "release":
        x("UPDATE sessions SET pencil_until=NULL WHERE id=?", (sid,))
    elif s["stage"] not in LEAD_STAGES:
        refuse_ = f"Not penciled: this session is already {C.STAGE_LABELS.get(s['stage'], s['stage'])}."
    elif not s["shoot_date"]:
        refuse_ = "Not penciled: set the date first."
    elif act == "pencil" or act == "until":
        until_ = (_pencil_until_in(f.get("until"), t_) if f.get("until")
                  else (today() + timedelta(days=C.PENCIL_DAYS)).isoformat())
        if not until_:
            refuse_ = "Not saved: pick a day from today on."
        else:
            x("UPDATE sessions SET pencil_until=? WHERE id=?", (until_, sid))
    elif act == "extend":
        from_ = max(d(s["pencil_until"][:10]) if s["pencil_until"] else today(), today())
        x("UPDATE sessions SET pencil_until=? WHERE id=?", ((from_ + timedelta(days=C.PENCIL_DAYS)).isoformat(), sid))
    else:
        abort(400)
    if refuse_:
        session["notice"] = refuse_
        g.refused = True
    return redirect(back_)

OPEN_WORDS = (
    ("/prints/order/", "Open the print order"), ("/prints/review", "Review the print orders"),
    ("/settings/prints", "Open print prices"), ("/settings/plugins", "Open plugins"), ("/galleries/move", "Open moving galleries"),
    ("/session/", "Open the session"), (("/client", "/c/"), "Open the client page"), ("/event", "Open the event"),
    (("/settings", "/setup/"), "Open settings"),
)

def _open_words(link):

    seg_ = (link or "").strip("/").split("/")[0].split("?")[0].split("#")[0].replace("-", " ").replace("_", " ")
    return next((w_ for pre, w_ in OPEN_WORDS if (link or "").startswith(pre)), f"Open {seg_}" if seg_ else "Open Today")

def shoot_started(s):

    if not s["shoot_date"]:
        return False
    now_ = datetime.now(TZ)
    sd_ = s["shoot_date"][:10]
    if sd_ < now_.date().isoformat():
        return True
    return sd_ == now_.date().isoformat() and (not s["start_time"] or s["start_time"][:5] <= now_.strftime("%H:%M"))

def follow_up(s):

    if not q("SELECT 1 FROM tasks WHERE session_id=? AND title='Client said yes' AND done IS NULL", (s["id"],), one=True):
        return None
    m_ = q("""SELECT m.vid, m.at, v.data FROM quote_marks m JOIN quote_versions v ON v.id=m.vid
              WHERE m.session_id=? ORDER BY m.at DESC, m.vid DESC LIMIT 1""", (s["id"],), one=True)
    if not m_:
        return None
    last_log = q("SELECT MAX(at) m FROM log WHERE household_id=?", (s["household_id"],), one=True)["m"] or ""
    last = max(m_["at"][:10], last_log[:10])
    if last > (today() - timedelta(days=C.LEAD_QUIET_DAYS)).isoformat():
        return None
    try:
        total_ = float((json.loads(m_["data"] or "{}") or {}).get("total") or 0)
    except (ValueError, TypeError, AttributeError):
        total_ = 0.0
    qo_ = quote_of(s["id"]) or {}
    what_ = (s["title"] or "").strip() or C.SESSION_TYPES.get(s["type"], "session") + " session"
    lines = [f"I wanted to follow up on the quote I sent on {nice(m_['at'][:10])} for your {what_.lower() if not s['title'] else what_}"
             + (f": {money(total_)}" if total_ > 0.005 else "") + "."
             + (f" It is good through {nice(qo_['expires'])}." if qo_.get("expires") else ""),
             "Would you like to go ahead? I'm happy to answer any questions, or to change anything that doesn't fit."]
    mail_ = _send_ctx(s["id"], "followup", "Following up on your quote", lines)
    return dict(what=f"Follow up on quote {quote_no(m_['vid'])} (sent {nice(m_['at'][:10])})", when=None, verb="I sent it",
                doc=mail_["mailto"], doc_verb="Follow up", doc_here=True, post=url_for("session_followup", sid=s["id"]),
                fields=dict(vid=m_["vid"]), followup=True, sid=s["id"], hid=s["household_id"],
                alt=dict(verb="Do not book", post=url_for("session_stage", sid=s["id"]), fields=dict(stage="lost")))

@app.post("/session/<int:sid>/followup")
def session_followup(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    vid_ = request.form.get("vid", type=int)
    draft_ = (follow_up(s_) or {}).get("doc") if request.form.get("mail") else None
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (s_["household_id"], sid, "email", f"Followed up on quote {quote_no(vid_)}" if vid_ else "Followed up on the quote",
       datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    if request.headers.get("X-Fetch"):
        return ("", 204)
    if draft_:
        return redirect(draft_, code=303)
    return redirect(safe_back(request.form.get("back")) or url_for("home"))

@app.post("/session/<int:sid>/reply")
def session_reply(sid):

    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    how_ = request.form.get("how") if request.form.get("how") in REPLY_HOW else "email"
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (s_["household_id"], sid, how_, "Replied to their message", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    mail_ = reply_draft(sid)
    if request.form.get("mail") and mail_:
        return redirect(mail_, code=303)
    return redirect(safe_back(request.form.get("back")) or url_for("home"))

def message_sender(sid):

    fn_ = app.config.get("inquiry_sender")
    return fn_(sid) if callable(fn_) else None

def reply_draft(sid):

    mail_ = _send_ctx(sid, "reply", "Re: your inquiry", [], to=message_sender(sid))
    return mail_["mailto"] if mail_["to"] else ""

app.jinja_env.globals["reply_draft"] = reply_draft

def reply_ways(sid):

    sender_ = message_sender(sid)
    mail_ = _send_ctx(sid, "reply", "Re: your inquiry", [], to=sender_)
    phone_raw = ((sender_ or {}).get("phone") if sender_ else "") or mail_["phone"] or ""
    phone_ = re.sub(r"[^0-9+]", "", phone_raw)
    return dict(email=dict(href=mail_["mailto"], to=mail_["to"] or ""),
                text=dict(href=f"sms:{phone_}" if len(phone_) >= 7 else "sms:", to=phone_raw if len(phone_) >= 7 else ""))

app.jinja_env.globals["reply_ways"] = reply_ways
REPLY_HOW = {"email": "Email", "text": "Text", "call": "Call", "in person": "In person"}
app.jinja_env.globals["REPLY_HOW"] = REPLY_HOW

def replied_on(sid):

    r_ = q("SELECT MAX(at) m FROM log WHERE session_id=? AND summary='Replied to their message'", (sid,), one=True)
    return (r_["m"] or "")[:10] if r_ else ""

app.jinja_env.globals["replied_on"] = replied_on

def refund_words(name, reason, owed, kept):

    why_ = {None: "canceled", "": "canceled", "Canceled": "canceled", "No-show": "did not show"}.get(
        reason, f"was not booked ({(reason or '').lower()})")
    return (f"{name} {why_}. Refund owed: ${owed:,.2f}."
            + (f" Deposit of ${kept:,.2f} is retained." if kept > 0.005 else ""))

def what_for(s):

    title_ = ((s["title"] if "title" in s.keys() else "") or "").strip()
    return title_ or f"{C.SESSION_TYPES.get(s['type'], 'Photo')} session"

def invoice_went(sid):

    sent_ = q("SELECT number FROM invoice_sent WHERE session_id=?", (sid,), one=True)
    task_ = q("""SELECT done FROM tasks WHERE session_id=? AND title=? AND done IS NOT NULL AND COALESCE(outcome, 'done')='done'
                 ORDER BY done DESC LIMIT 1""", (sid, SEND_TASKS["invoice"]), one=True)
    inv_ = q("SELECT number, issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    if not sent_ and not task_ and not invoice_issued_before_15(sid, inv_):
        return None
    return dict(number=(sent_["number"] if sent_ else (inv_["number"] if inv_ else "")),
                day=(task_["done"] if task_ else (inv_["issued"] if inv_ and inv_["issued"] else ""))[:10])

def invoice_issued_before_15(sid, inv_=None):

    from_ = setting("stage_rules_from")
    if inv_ is None:
        inv_ = q("SELECT number, issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    if not from_ or not inv_ or not inv_["issued"] or inv_["issued"][:10] > from_:
        return False
    return bool(q("SELECT 1 FROM stage_log WHERE session_id=? AND era='1.4'", (sid,), one=True)
                or q("SELECT 1 FROM sessions WHERE id=? AND COALESCE(created, '') != '' AND substr(created, 1, 10) < ?",
                     (sid, from_), one=True))

def invoice_words(sid, s, t_):

    return f"Send the invoice ({money(t_['due'])} balance, {what_for(s)})"

def owed_detail(sid, s, t_, inv_, unlocks=""):

    for_ = what_for(s) + (f", invoice {inv_['number']}" if inv_ and inv_.get("number") else "")
    return f"Balance {money(t_['due'])} of {money(t_['quoted'])} ({for_})." + (f" {unlocks}" if unlocks else "")

def deposit_held(sid):

    t_ = totals(sid)
    if t_["deposit_req"] <= 0.005 or t_["deposit_short"] <= 0.005:
        return 0.0
    if q("SELECT 1 FROM step_closes WHERE session_id=? AND step='deposit_paid' AND outcome != 'done'", (sid,), one=True):
        return 0.0
    return round(t_["deposit_short"], 2)

def replacing(sid):

    return q("SELECT * FROM quote_replacements WHERE session_id=? AND booked_at IS NULL ORDER BY id DESC LIMIT 1", (sid,), one=True)

def quote_locked(sid):

    s_ = q("SELECT stage, priced_on FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["stage"] in ("inquiry", "quoted"):
        return False
    if replacing(sid):
        return s_["stage"] in ("closed", "lost")
    if q("SELECT 1 FROM quote_versions WHERE session_id=?", (sid,), one=True):
        return True
    b_ = q("""SELECT MIN(at) a FROM stage_log WHERE session_id=? AND stage='booked'
              AND id > (SELECT MIN(id) FROM stage_log WHERE session_id=?)""", (sid, sid), one=True)
    return bool(b_ and b_["a"] and s_["priced_on"] and s_["priced_on"][:10] <= b_["a"][:10])

QUOTE_LOCKED = "The quote was booked, so it stays as agreed. To change it, use Replace the quote (under Details and history). Nothing was changed."

def length_words(mins):

    h_, m_ = divmod(int(mins or 0), 60)
    parts = ([f"{h_} hour{'' if h_ == 1 else 's'}"] if h_ else []) + ([f"{m_} minutes"] if m_ else [])
    return " ".join(parts) or "not set"

def shoot_missing(s):

    out = []
    if not s["shoot_date"]:
        out.append("no date")
    elif s["date_tentative"]:
        out.append("date not final yet")
    if not s["start_time"]:
        out.append("no start time")
    if not has_place(s):
        out.append("no place")
    return out

def owes_words(s, t_, inv_):

    sent_ = " ".join(filter(None, ["invoice", (inv_ or {}).get("number") or "", "sent", nice((inv_ or {}).get("day") or "")]))
    return f"{s['household']} owes {money(t_['due'])}" + (f" · {sent_}" if inv_ else "")

def letter_gate(s):

    if s["letter_mailed"] or s["stage"] not in OWED_STAGES:
        return None
    sid = s["id"]
    close_ = dict(sid=sid, step="letter_mailed")
    proofs_ = app.config.get("gallery_proofs")
    if proofs_ and proofs_(sid):
        dep = deposit_held(sid)
        if dep > 0.005:
            return dict(what=f"Collect the {money(dep)} deposit first", verb=f"Record payment ({money(dep)})", form="deposit_paid", sid=sid,
                        dep_amt=dep, deposit_first=True, close=close_,
                        detail=C.dw(f"Deposit {money(dep)} ({what_for(s)}). The proof gallery link goes out once it is in."))
        return None
    owed = release_blocked(sid)
    if owed > 0.005:
        return dict(what="Mail QR letter once paid", wait="After payment", close=close_,
                    detail=f"The letter carries the gallery link: it goes out once the {money(owed)} balance is paid.")
    return None

def waive_item(sid):

    return dict(verb="Waive the balance", post=url_for("close_step"), fields=dict(sid=sid, step="balance_paid", outcome="waived"),
                ask="Why (required)", ask_name="note_why",
                confirm="Waive the balance? It is written off, not paid: Finances count it as a write-off, not income.")

REMINDER_SENT = "Sent a payment reminder"

def deposit_step_due(sid):

    w_ = workflow(sid)
    return next((st_.get("due") for st_ in ((w_["current"]["steps"] if w_["current"] else []) + w_["carried"])
                 if st_.get("key") == "deposit_paid" and not st_["done"]), None)

def overdue(s, tt=None):

    sid = s["id"]
    tt = tt or totals(sid)
    none_ = dict(amount=0.0, since=None)
    if tt["due"] <= 0.005 or s["stage"] in ("inquiry", "quoted", "lost"):
        return none_
    now_ = today().isoformat()
    bd = balance_due_date(s["shoot_date"], row_type(s), sid=sid)
    rows_ = installment_schedule(sid, tt)
    if rows_ and rows_[-1]["due"]:
        bd = rows_[-1]["due"]
    late_, since_ = 0.0, []
    held_ = deposit_held(sid)
    dd = deposit_step_due(sid) if held_ > 0.005 else None
    if dd and str(dd)[:10] < now_:
        late_ += held_
        since_.append(str(dd)[:10])
    if bd and bd < now_:
        return dict(amount=round(tt["due"], 2), since=min(since_ + [bd]))
    for r_ in rows_:
        if r_["status"] == "late":
            late_ += r_["left"]
            since_.append(r_["due"])
    late_ = round(min(late_, tt["due"]), 2)
    return dict(amount=late_, since=min(since_)) if late_ > 0.005 else none_

def balance_when(s, tt=None):

    rows_ = [r_ for r_ in installment_schedule(s["id"], tt) if r_["left"] > 0.005 and r_["due"]]
    if rows_:
        return rows_[0]["due"]
    return balance_due_date(s["shoot_date"], row_type(s), sid=s["id"])

def payment_reminder(s, tt=None, due=None, kind=None):

    sid = s["id"]
    tt = tt or totals(sid)
    if kind is None:
        shot_ = bool(s["shoot_date"] and str(s["shoot_date"])[:10] < today().isoformat())
        kind = "deposit" if (s["stage"] in ("inquiry", "quoted", "booked") and not shot_ and tt["deposit_short"] > 0.005
                             and deposit_held(sid) > 0.005) else "balance"
    amount = round(tt["deposit_short"] if kind == "deposit" else tt["due"], 2)
    if amount <= 0.005:
        return None
    what_ = C.dw(kind)

    rows_ = installment_schedule(sid, tt) if kind == "balance" and due is None else []
    late_ = [r_ for r_ in rows_ if r_["status"] == "late"]
    open_ = [r_ for r_ in rows_ if r_["left"] > 0.005]
    if late_:
        amount, due = round(sum(r_["left"] for r_ in late_), 2), late_[0]["due"]
        what_ = (f"installment ({late_[0]['n']} of {late_[0]['of']})" if len(late_) == 1 else
                 f"installments ({late_[0]['n']} to {late_[-1]['n']} of {late_[0]['of']})")
    elif open_:
        amount, due, what_ = open_[0]["left"], open_[0]["due"], f"installment ({open_[0]['n']} of {open_[0]['of']})"
    elif due is None:
        due = deposit_step_due(sid) if kind == "deposit" else balance_due_date(s["shoot_date"], s["type"], sid=sid)
    due = str(due)[:10] if due else None
    late = bool(due and due < today().isoformat())
    ov_ = overdue(s, tt)

    dep_late = (round(ov_["amount"], 2) if kind == "balance" and not late and not rows_ and ov_["amount"] > 0.005
                and ov_["amount"] < amount - 0.005 else 0.0)
    for_ = what_for(s)
    title_ = ((s["title"] if "title" in s.keys() else "") or "").strip()
    when_ = (f" It was due {nice(due)}." if late else f" It is due {nice(due)}.") if due else ""
    if dep_late:
        when_ += C.dw(f" The {money(dep_late)} deposit in it was due {nice(ov_['since'])}.")
    lines = [f"A quick reminder: the {money(amount)} {what_} for your {for_ if title_ else for_.lower()} is still open.{when_}",
             how_to_pay_line()]
    mail_ = _send_ctx(sid, "reminder", f"Reminder: {what_.split(' (')[0]} of {money(amount)}", lines)
    words = (f"{what_.capitalize()} {money(amount)}" + (((", was due " if late else ", due ") + nice(due)) if due else "")
             + (C.dw(f"; the {money(dep_late)} deposit was due {nice(ov_['since'])}") if dep_late else ""))
    mailto_ = mail_["mailto"] if mail_["to"] else ""
    ways_ = [w_ for w_ in (("email", mailto_), ("text", mail_["sms"])) if w_[1]]
    both_ = len(ways_) > 1
    return dict(kind=kind, amount=amount, due=due, late=late, overdue=ov_["amount"], mailto=mailto_, sms=mail_["sms"], words=words,
                ways=[dict(how=h_, href=u_, verb=REMIND_VERB[h_] if both_ or h_ == "text" else "Remind them")
                      for h_, u_ in ways_])

REMIND_VERB = {"email": "Remind them by email", "text": "Remind them by text"}

def remind_items(s, tt=None, due=None, kind=None):

    r_ = payment_reminder(s, tt, due, kind)
    return [dict(verb=w_["verb"], post=url_for("session_remind", sid=s["id"]),
                 fields=dict(kind=r_["kind"], how=w_["how"]), mail=w_["href"]) for w_ in (r_["ways"] if r_ else [])]

app.config.setdefault("quiet_endpoints", set()).add("session_remind")

@app.post("/session/<int:sid>/remind")
def session_remind(sid):

    s_ = q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?",
           (sid,), one=True) or abort(404)
    kind_ = request.form.get("kind") if request.form.get("kind") in ("deposit", "balance") else None
    r_ = payment_reminder(s_, kind=kind_)
    text_ = request.form.get("how") == "text" and bool(r_ and r_["sms"])
    x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
      (s_["household_id"], sid, "text" if text_ else "email", REMINDER_SENT + (f": {r_['words']}" if r_ else ""),
       datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")))
    if request.form.get("mail") and r_ and (r_["sms"] if text_ else r_["mailto"]):
        return redirect(r_["sms"] if text_ else r_["mailto"], code=303)
    return redirect(safe_back(request.form.get("back")) or url_for("home"))

SETTLED_BY = {"Cull": "gallery_built", "Edit": "gallery_built"}

FREE_NOTE = "Settled: there is nothing to pay"
PLAN_NOTE = "Settled: billed on the plan's quote"
PLAN_MONEY_TASKS = tuple(st_[1] for st_ in C.MONEY_STEPS if st_[0] == "do")

def settle_steps(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["stage"] in ("closed", "lost") or s_["event_id"]:
        return
    day = today().isoformat()
    later = bool(s_["gallery_built"] or s_["letter_mailed"] or s_["first_visit"] or s_["sd_shipped"])
    free_ = nothing_to_pay(sid)
    on_plan_ = billed_on_plan(sid, s_["plan_of"])
    marks_ = ",".join("?" * len(PLAN_MONEY_TASKS))
    if on_plan_:
        x(f"""UPDATE tasks SET done=?, outcome='na', outcome_note=? WHERE session_id=? AND own=0 AND done IS NULL
              AND title IN ({marks_})""", (day, PLAN_NOTE, sid, *PLAN_MONEY_TASKS))
    else:
        x(f"""UPDATE tasks SET done=NULL, outcome=NULL, outcome_note=NULL WHERE session_id=? AND own=0 AND outcome='na'
              AND outcome_note=? AND title IN ({marks_})""", (sid, PLAN_NOTE, *PLAN_MONEY_TASKS))
    if not free_:
        for t_ in q("SELECT id, title FROM tasks WHERE session_id=? AND own=0 AND title=? AND outcome='na' AND outcome_note=?",
                    (sid, SEND_TASKS["invoice"], FREE_NOTE)):
            x("UPDATE tasks SET done=NULL, outcome=NULL, outcome_note=NULL WHERE id=?", (t_["id"],))
            audit_after(f"Reopened: {step_says(t_['title'])} (the session has something to pay again)", sid=sid, who="Lumen Aureum")
    settled = []
    for t_ in q("SELECT id, title FROM tasks WHERE session_id=? AND own=0 AND done IS NULL", (sid,)):
        if t_["title"] == SEND_TASKS["invoice"] and free_:

            x("UPDATE tasks SET done=?, outcome='na', outcome_note=? WHERE id=? AND done IS NULL", (day, FREE_NOTE, t_["id"]))
            continue
        why_ = None
        if t_["title"] == SEND_TASKS["invoice"] and q("SELECT 1 FROM invoice_sent WHERE session_id=?", (sid,), one=True):
            why_ = "the invoice was marked sent"
        elif t_["title"] == SEND_TASKS["invoice"] and invoice_issued_before_15(sid):
            why_ = "the invoice was issued before the update"
        elif t_["title"] in SETTLED_BY and later:
            why_ = "the gallery is built" if s_["gallery_built"] else "the photos were delivered"
        if why_:
            x("UPDATE tasks SET done=?, outcome='done', outcome_note=? WHERE id=? AND done IS NULL",
              (day, f"Settled: {why_}", t_["id"]))
            settled.append((t_["title"], why_))
    for why_ in dict.fromkeys(w_ for _t, w_ in settled):
        audit_after(f"Done: {', '.join(step_says(t_) for t_, w_ in settled if w_ == why_)} ({why_})", sid=sid, who="Lumen Aureum")

def _photo_row(a, st, s):

    lab, sid = st["label"], s["id"]
    here = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}"
    carried = a.get("note") or ""
    _photo_row_notes(a, st, s, lab, sid, here)
    if carried and carried not in (a.get("note") or ""):
        a["note"] = " · ".join(filter(None, [carried, a.get("note") or ""]))

def _photo_row_notes(a, st, s, lab, sid, here):
    if lab == "Back up photos":
        b = backup_state(sid)
        a.update(drop="raw", drop_verb="Browse RAW files", note="",
                 alt=dict(verb="I backed it up another way", post=url_for("backup_elsewhere", sid=sid), fields={},
                          confirm=BACKUP_ELSEWHERE_ASK, confirm_sets="sure"))
        if b["n"] and not b["two"]:

            a.update(note=f"{b['n']} RAW file{'' if b['n'] == 1 else 's'} in · Only one copy so far", verb="Add a backup location", href=url_for("setup_step", step="storage"),
                     post=None, fields=None)
        else:
            a.update(verb="Upload the RAWs", href=here + "#row-t" + str(st["task_id"]), post=None, fields=None, drop_main=True)
            if b["n"]:
                a["note"] = f"On both storage locations: {b['both']} of {b['n']} RAWs"
    elif lab == "Edit":
        a.update(drop="export", drop_verb="Browse edited photos")
        n_ = q("SELECT COUNT(*) n FROM photo_files WHERE session_id=? AND kind='export' AND state='done'", (sid,), one=True)["n"]
        if n_:
            a["note"] = f"{n_} edited photo{'' if n_ == 1 else 's'} in"
    elif lab == "Send retouched photos":
        rs = retouch_state(s)
        if rs["n"] and not rs["all_in"]:

            a.update(what=f"Retouch {rs['n']} photo{'' if rs['n'] == 1 else 's'}", retouch=rs["picks"], detail="",
                     note=f"{rs['n_in']} of {rs['n']} retouched photos in" if rs["n_in"] else "",
                     drop="final", drop_verb="Browse retouched photos", verb="Upload the retouched photos",
                     href=here + "#row-t" + str(st["task_id"]), post=None, fields=None, drop_main=True, primary=True)
        elif rs["n"]:
            a.update(detail="", note=f"{rs['n']} retouched photo{' is' if rs['n'] == 1 else 's are'} in", drop="final",
                     drop_verb="Browse retouched photos")

def build_actions(only_sid=None):

    t = today()
    acts = []
    crowd = {}
    seen_events = set()
    rows = q("""SELECT s.*, h.name AS household, e.name AS event_name, e.date AS event_date FROM sessions s
        JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE s.stage NOT IN ('closed','lost')""" + ("" if only_sid is None else
        " AND (s.id=? OR (s.event_id IS NOT NULL AND s.event_id=(SELECT event_id FROM sessions WHERE id=?)))"),
        () if only_sid is None else (only_sid, only_sid))
    if only_sid is None and "wf_pre" not in g:
        prefetch_workflows(s["id"] for s in rows)
    reps = event_reps()
    for s in rows:

        if s["event_id"] and s["id"] != only_sid:
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

        owed_rel = release_blocked(s["id"]) if s["stage"] in ("shot", "delivered") else 0.0
        collect_ = owed_rel > 0.005 and any(st_.get("key") == "sd_shipped" and not st_["done"] for st_ in (cur["steps"] if cur else []))
        inv_ = invoice_went(s["id"])
        open_ = [st_ for st_ in (cur["steps"] if cur else []) + w["carried"] if not st_["done"]
                 and not _past_its_moment(s, st_) and not (s["event_id"] and st_.get("shared"))]
        send_inv_ = any(st_["label"] == SEND_TASKS["invoice"] for st_ in open_)
        missing_ = shoot_missing(s) if s["stage"] == "booked" and not s["event_id"] else []
        if missing_ and s["date_tentative"] and s["shoot_date"] and s["shoot_date"] <= t.isoformat():
            missing_ = []
        if missing_:
            acts.append(dict(who=who, what="Set the shoot date, time and place", detail=", ".join(missing_).capitalize(),
                             when=None, stage=s["stage"], verb="Set date, time and place", form="date_set", sid=s["id"],
                             prefill=True, where=focus_place(s), shoot_facts=True,

                             shoot_date=s["shoot_date"], start_time=s["start_time"], date_tentative=s["date_tentative"],
                             seq=(0, 2)))
        rp_ = replacing(s["id"]) if s["stage"] not in ("inquiry", "quoted") else None
        if rp_:
            acts.append(dict(who=who, what="New quote waiting for their OK", when=None, stage=s["stage"],
                             detail=(f"Replaces the booked quote of {money(rp_['old_total'])}, canceled {nice(rp_['at'][:10])}."
                                     if rp_["old_total"] or json.loads(rp_["old_lines"] or "[]") else "Booked without a quote."),
                             verb="Book it", post=url_for("quick", sid=s["id"]), fields=dict(act="rebook"), primary=True,
                             replacing=True, seq=(0, 0)))
        for st in open_:
            if collect_ and st.get("key") == "balance_paid":
                continue
            if st.get("from_stage") and st.get("key") == "deposit_paid" and s["stage"] in OWED_STAGES:
                continue
            if missing_ and (st.get("key") == "date_set" or st["label"] == "Confirm time and place"):
                continue
            if st.get("key") == "balance_paid" and not inv_ and send_inv_:
                continue
            if st["label"] == SEND_TASKS["invoice"] and collect_ and not inv_:
                continue
            from_ = st.get("from_stage")
            when = st.get("due") or (s["shoot_date"] if s["stage"] == "booked" and not from_ else None)
            theirs = st["label"] in THEIR_STEPS
            a = dict(who=who, what=C.dw(THEIR_WORDS.get(st["label"], st["label"]) if theirs else todo_word(st["label"])), when=when,
                     stage=s["stage"], theirs=theirs, since=(s["stage_changed"] or "")[:10],
                     seq=(1, C.STAGES.index(from_), len(acts)) if from_ else (2, 0, len(acts)))
            if from_:
                a.update(note=f"From {C.STAGE_LABELS[from_]}", carried=from_)
            if st["label"] == "Send retouched photos" and (s["retouch_picks"] or "").strip():
                a["detail"] = s["retouch_picks"].strip()
            note_ = next((n_ for n_ in (fn(st["label"], s) for fn in hooks.step_notes) if n_), None)
            if note_:
                a["note"] = note_
            if st["label"] == "Send session details":
                a.update(doc=url_for("session_details", sid=s["id"]), doc_verb="Send the session sheet")
            elif st["label"] == "Send quote":
                a.update(doc=url_for("session_quote", sid=s["id"]), doc_verb="Send the quote")
            elif st["label"] == "Send invoice":
                a.update(doc=url_for("invoice", sid=s["id"]), doc_verb="Send the invoice")
                if tt["quoted"] > 0.005 and tt["due"] > 0.005:
                    a["what"] = invoice_words(s["id"], s, tt)
            elif st["label"] == "Releases signed":
                rel_ = q("SELECT id FROM documents WHERE session_id=? AND kind='release' AND status!='signed' ORDER BY id", (s["id"],))
                if len(rel_) > 1:
                    a.update(doc=url_for("session_edit", sid=s["id"]) + "#paperwork", doc_verb="Mark who signed", doc_here=True)
            custom = next((r_ for r_ in (fn(st["label"], s) for fn in hooks.step_actions) if r_), None)
            gate_ = letter_gate(s) if st.get("key") == "letter_mailed" else None
            if collect_ and st.get("key") == "sd_shipped" and not inv_:
                a.update(what=invoice_words(s["id"], s, tt), doc=url_for("invoice", sid=s["id"]), doc_verb="Send the invoice",
                         doc_here=False, theirs=False, collect=True, detail="The photos are released on payment.",
                         close=dict(sid=s["id"], step="sd_shipped"), when=None)
                custom = None
            elif collect_ and st.get("key") == "sd_shipped":
                a.update(what=f"Collect the {money(owed_rel)} balance first", verb=f"Record payment ({money(owed_rel)})",
                         href=url_for("session_edit", sid=s["id"]) + "#money", theirs=False, collect=True,
                         detail=owed_detail(s["id"], s, tt, inv_, "The photos are released on payment."),
                         close=dict(sid=s["id"], step="sd_shipped"),
                         when=balance_when(s, tt))
            elif gate_:
                a.update(gate_, theirs=False)
                custom = None
            elif custom:
                a.update(custom)
            elif st["kind"] == "do" and st["label"] == "Confirm time and place" and (not s["start_time"] or not has_place(s)):
                missing = " and ".join(w_ for w_, ok in (("a start time", s["start_time"]), ("a place", has_place(s))) if not ok)
                a.update(verb="Add time and place", href=url_for("session_edit", sid=s["id"]) + "#shoot",
                         what=f"Confirm time and place: needs {missing}", close=dict(task=st["task_id"]))
            elif st["kind"] == "do":
                a.update(verb="Mark done", post=url_for("task_toggle", tid=st["task_id"]), fields={"set": "1"},
                         close=dict(task=st["task_id"]))
            else:
                key = next((k for k, v in AUTO.items() if v[0] == st["label"]), None)
                if key:
                    a["close"] = dict(sid=s["id"], step=key)
                if key in ("agreement_signed", "releases_signed"):
                    kind_ = "agreement" if key == "agreement_signed" else "release"
                    if len(pending_docs(s["id"], kind_)) > 1:
                        a.update(verb="Mark who signed", doc=url_for("session_edit", sid=s["id"]) + "#paperwork",
                                 doc_verb="Mark who signed", doc_here=True)
                    else:
                        a.update(verb="Mark signed", post=url_for("quick", sid=s["id"]), fields=dict(act="sign", kind=kind_))
                elif key in MARKABLE:
                    a.update(verb=MARKABLE[key], post=url_for("quick", sid=s["id"]), fields=dict(act="mark", field=key))
                    if key == "first_visit":
                        a.update(ask_date=True, date_tip=f"The day the client first opened it. The gallery comes down "
                                                         f"{C.gallery_days(s['type'])} days later.")
                elif key == "balance_paid" and not inv_:
                    a.update(what=invoice_words(s["id"], s, tt), doc=url_for("invoice", sid=s["id"]),
                             doc_verb="Send the invoice", doc_here=False, theirs=False, when=None,
                             more_items=[waive_item(s["id"])])
                elif key == "balance_paid":
                    held_ = release_blocked(s["id"]) > 0.005
                    a.update(verb=f"Record payment ({money(tt['due'])})", href=url_for("session_edit", sid=s["id"]) + "#money",
                             what=owes_words(s, tt, inv_),
                             detail=owed_detail(s["id"], s, tt, inv_, "The photos are released on payment." if held_ else ""),
                             when=balance_when(s, tt), more_items=[waive_item(s["id"])])
                elif key in LINK_VERBS:
                    verb, tab = LINK_VERBS[key]
                    a.update(verb=verb, href=who["link"] + "#" + tab, sid=s["id"], hid=s["household_id"],
                             form=key, intake=json.loads(s["intake"] or "{}"), type=s["type"], due_amt=tt["due"],
                     dep_amt=(tt["deposit_short"] if tt["deposit_short"] > 0.005 else tt["deposit_req"]))
                    if key == "deposit_paid" and a["dep_amt"] > 0.005:
                        a["verb"] = f"Record payment ({money(a['dep_amt'])})"
                        if tt["paid"] > 0.005 and tt["deposit_short"] > 0.005 and not a.get("detail"):

                            a["detail"] = (f"{money(tt['deposit_short'])} left of the {money(tt['deposit_req'])} deposit; "
                                           f"{money(tt['paid'])} paid so far.")
                elif key:
                    a.update(verb="Mark done", post=url_for("close_step"), fields=dict(sid=s["id"], step=key, outcome="done"))
                else:
                    a.update(verb="Open session page", href=who["link"] + "#" + (st.get("tab") or ""))
            if theirs and not custom and not (collect_ and st.get("key") == "sd_shipped") and not gate_ \
                    and not (st.get("key") == "balance_paid"):
                _theirs_after_send(a, s, st.get("key"), tt)
            if st["kind"] == "do" and not st.get("shared") and not custom:
                _photo_row(a, st, s)
            if a.get("collect") and not a.get("more_items"):
                a["more_items"] = [waive_item(s["id"])]
            if not custom and ((st.get("key") == "deposit_paid" and a.get("theirs"))
                               or ((st.get("key") == "balance_paid" or a.get("collect")) and inv_)):
                rem_ = remind_items(s, tt, st.get("due") if st.get("key") == "deposit_paid" else None,
                                    "deposit" if st.get("key") == "deposit_paid" else "balance")
                if rem_:
                    a["more_items"] = rem_ + (a.get("more_items") or [])
            acts.append(a)
        for fn in hooks.session_actions:
            for a_ in fn(s) or []:
                acts.append(dict(dict(who=who, when=None, stage=s["stage"]), **a_))
        if s["stage"] in ("inquiry", "quoted", "booked") and s["shoot_date"] and s["shoot_date"] >= t.isoformat():
            cl_ = clashes(s["id"])
            if cl_:
                day_ = crowd.setdefault(s["shoot_date"], dict(ids=set(), first=None))
                day_["ids"].update([s["id"]] + [c_["sid"] for c_ in cl_])
                if day_["first"] is None:
                    day_["first"] = (who, s, cl_)
        has_bal_step = collect_ or (cur and any(st_.get("key") == "balance_paid" for st_ in cur["steps"] + w["carried"]))
        bdue = balance_due_date(s["shoot_date"], s["type"], sid=s["id"])
        paid_ahead = (s["stage"] == "booked" and row_type(s) in C.BEFORE_TYPES and bdue and tt["due"] > 0.005
                      and bdue <= (t + timedelta(days=14)).isoformat())
        if not has_bal_step and (paid_ahead or (s["stage"] in ("shot", "delivered") and owed_now(s["stage"], tt, s["shoot_date"], row_type(s)))):
            if not inv_:
                acts.append(dict(who=who, what=invoice_words(s["id"], s, tt), when=None, stage=s["stage"],
                                 more_items=[waive_item(s["id"])],
                                 doc=url_for("invoice", sid=s["id"]), doc_verb="Send the invoice", doc_here=False))
            else:
                acts.append(dict(who=who, what=owes_words(s, tt, inv_), theirs=True, since=s["shoot_date"],
                                 more_items=[m_ for m_ in remind_items(s, tt, None, "balance") + [waive_item(s["id"])] if m_],
                                 when=balance_when(s, tt), stage=s["stage"], detail=owed_detail(s["id"], s, tt, inv_),
                                 verb=f"Record payment ({money(tt['due'])})", href=url_for("session_edit", sid=s["id"]) + "#money"))
        if s["stage"] == "booked" and not s["event_id"] and not s["date_tentative"] and shoot_started(s):
            acts.append(dict(who=who, what=f"The shoot, {nice(s['shoot_date'])}" + (f", {clock(s['start_time'])}" if s["start_time"] else ""),
                             when=None, stage=s["stage"], verb="Shoot done", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="shot"), primary=True, shoot_done=True, seq=(0, 1),

                             confirm=(lambda o_: (", ".join(o_) + (" is" if len(o_) == 1 else " are") + " still open. Move to Shot?")
                                      if o_ else None)([step_says(st_["label"]) for st_ in open_])))
        if w["ready"] and w["next_stage"] not in (None, "shot", "closed") and can_enter(w["next_stage"], s["shoot_date"], s["id"]):
            acts.append(dict(who=who, what=f"Everything in {C.STAGE_LABELS[s['stage']]} is done", when=None,
                             stage=s["stage"], verb=f"Move to {C.STAGE_LABELS[w['next_stage']]}",
                             post=url_for("quick", sid=s["id"]), fields=dict(act="stage", stage=w["next_stage"]),
                             primary=True))
        if s["stage"] == "booked" and s["date_tentative"] and s["shoot_date"] and s["shoot_date"] <= t.isoformat():
            acts.append(dict(who=who, what=f"The tentative date ({nice(s['shoot_date'])}) has come: set the real date, or confirm "
                                          "the shoot happened", when=s["shoot_date"], stage=s["stage"], verb="Set the date",
                             href=url_for("session_edit", sid=s["id"]) + "#shoot", form="date_set", sid=s["id"], firm=True,
                             alt=dict(verb="It happened on that day", post=url_for("quick", sid=s["id"]), fields=dict(act="firm"))))
        if pencil_lapsed(s):
            acts.append(dict(who=who, what=f"The hold on {nice(s['shoot_date'][:10])} lapsed {nice(s['pencil_until'][:10])}",
                             when=None, stage=s["stage"], verb="Release the date", pencil_lapse=True,
                             post=url_for("session_pencil", sid=s["id"]), fields=dict(act="release"),
                             also=dict(verb=f"Extend the hold {C.PENCIL_DAYS} days", post=url_for("session_pencil", sid=s["id"]),
                                      fields=dict(act="extend"))))
        exp = gallery_expires(s)
        if exp and t <= exp <= t + timedelta(days=2) and not s["extension_used"] and not gallery_restored(s["id"]):
            acts.append(dict(who=who, what=f"Gallery deletes {nice(exp.isoformat())}", when=exp.isoformat(),
                             stage=s["stage"], verb="Give extension", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="extend")))
        if s["stage"] == "quoted":
            fu_ = follow_up(s)
            if fu_:
                acts.append(dict(fu_, who=who, stage=s["stage"]))
        elif s["stage"] == "inquiry":
            last = q("SELECT MAX(at) m FROM log WHERE household_id=?", (s["household_id"],), one=True)["m"] or s["created"]
            if last[:10] <= (t - timedelta(days=C.LEAD_QUIET_DAYS)).isoformat():
                acts.append(dict(who=who, what=f"Follow up (last contact {nice(last)})", when=None, stage=s["stage"],
                                 verb="Follow up", href=who["link"] + "#log", form="logged", sid=s["id"],
                                 hid=s["household_id"], alt=dict(
                                     verb="Do not book", post=url_for("session_stage", sid=s["id"]),
                                     fields=dict(stage="lost"))))
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage='closed' AND s.event_id IS NULL AND (s.first_visit IS NOT NULL OR s.sd_shipped IS NOT NULL)"""
               + ("" if only_sid is None else " AND s.id=?"), () if only_sid is None else (only_sid,)):

        exp = gallery_expires(s)
        if exp and t <= exp <= t + timedelta(days=2) and not s["extension_used"] and not gallery_restored(s["id"]):
            acts.append(dict(who=dict(name=s["household"], sub=C.SESSION_TYPES.get(s["type"], s["type"]),
                                      link=url_for("session_view", sid=s["id"]), type=s["type"], hid=s["household_id"]),
                             what=f"Gallery deletes {nice(exp.isoformat())}", when=exp.isoformat(),
                             stage=s["stage"], verb="Give extension", post=url_for("quick", sid=s["id"]),
                             fields=dict(act="extend")))
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage='lost'""" + ("" if only_sid is None else " AND s.id=?"),
               () if only_sid is None else (only_sid,)):
        back_, kept_ = totals(s["id"])["refund_due"], totals(s["id"])["kept"]
        if back_ > 0.005:
            acts.append(dict(who=dict(name=s["household"], sub=C.SESSION_TYPES.get(s["type"], s["type"]),
                                      link=url_for("session_view", sid=s["id"]), type=s["type"], hid=s["household_id"]),
                             what=refund_words(s["household"], s["lost_reason"], back_, kept_), when=(s["stage_changed"] or "")[:10] or None,
                             stage="lost", verb="Record refund issued", href=url_for("session_edit", sid=s["id"]) + "#money"))
    for p in (future_work() if only_sid is None else []):
        acts.append(dict(who=dict(name=p["household"], sub="", link=url_for("client_stage", hid=p["household_id"]), hid=p["household_id"]),
                         what=f"{p['name']} is class of {p['grad_year']} - offer senior photos", when=None,
                         stage=None, verb="Open client", href=url_for("client_stage", hid=p["household_id"]),
                         later=True))
    for fn in hooks.today:
        for item in fn() or []:
            if not item.get("who"):
                continue
            acts.append(dict(who=item.get("who") or dict(name=item.get("sub", ""), sub="", link=item["link"]), what=item["text"],
                             when=item.get("when"), stage=None, verb=item.get("verb") or _open_words(item["link"]), href=item["link"],
                             later=not item.get("when"), notice=True, help=item.get("help") or ""))
    for day_iso, day_ in sorted(crowd.items()):
        who_, s_, cl_ = day_["first"]
        if len(day_["ids"]) <= 2:
            c_ = cl_[0]
            acts.append(dict(who=who_, what=f"Overlaps {c_['name']} ({c_['when']})", when=day_iso, stage=s_["stage"],
                             verb=f"Open {c_['name']}", href=url_for("client_stage", hid=c_["hid"], s=c_["sid"])))
        else:
            acts.append(dict(who=who_, what=f"{len(day_['ids'])} sessions overlap on {nice(day_iso)}", when=day_iso,
                             stage=s_["stage"], verb="Open the day", href=url_for("month_view", m=day_iso[:7], d=day_iso)))
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
            "money" if (v.startswith("Record") or a.get("collect") or "Balance" in a.get("what", "") or a.get("form") in ("deposit_paid", "quote_built")) else "",
            "lead" if a.get("stage") in ("inquiry", "quoted") else "",
            "shoot" if a.get("stage") == "booked" else ""]))
        w_ = a.get("when")
        a["group"] = ("overdue" if w_ and w_ < t.isoformat() else "week" if w_ and w_ <= soon
                      else "later" if w_ or a.get("later") else "now")
    order = {"overdue": 0, "now": 1, "week": 2, "later": 3}
    acts.sort(key=lambda a: (order[a["group"]], a.get("when") or "9999", 0 if a.get("primary") else 1))
    return acts

ICON_WORDS = [
    (("move to", "ready"), "arrow_forward"), (("deposit", "balance", "record $", "paid in full", "collect"), "payments"),
    (("sd card", "ship"), "local_shipping"),
    (("quote",), "request_quote"), (("signature", "signed", "release", "agreement", "unsigned"), "signature"), (("qr letter", "letter"), "mail"), (("prep guide", "session details", "session sheet"), "send"),
    (("deletes", "extension"), "timer"), (("opens the gallery", "opened"), "visibility"),
    (("gallery",), "photo_library"), (("favorite", "picks"), "star"),
    (("intake",), "assignment"), (("shoot date", "set date"), "event"),
    (("weather",), "partly_cloudy_day"), (("batter",), "battery_charging_full"), (("back up", "raws", "original photos"), "backup"),
    (("cull",), "photo_filter"), (("edit",), "tune"), (("scout", "venue"), "location_on"),
    (("follow up", "contact", "call", "text", "email"), "chat"), (("class of",), "school"), (("deliver",), "inventory_2"),
]

MONEY_SYMS = {"payments", "request_quote"}
CLIENT_SYMS = {"chat", "signature", "assignment", "send", "mail", "event", "visibility", "school", "star"}

def sym_group(name):

    return "money" if name in MONEY_SYMS else "client" if name in CLIENT_SYMS else "craft"

def icon_for(*texts):
    t = " ".join(x or "" for x in texts).lower()
    return next((ic for words, ic in hooks.step_icons + ICON_WORDS if any(w in t for w in words)), "task_alt")

KIND = {"pen": "paper", "dollar": "money", "mail": "deliver", "box": "deliver", "image": "deliver", "eye": "deliver",
        "clock": "deliver", "chat": "talk", "arrow": "move"}

def when_words(when):

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

    if not when:
        return "next"
    t = today().isoformat()
    if when < t:
        return "late"
    return "soon" if when <= (today() + timedelta(days=3)).isoformat() else "next"

def action_icon(a):

    v, w = a.get("verb", ""), a.get("what", "").lower()
    if v.startswith("Move"):
        return "arrow", "green"
    if v.startswith("Record") or a.get("form") in ("deposit_paid", "quote_built") or "balance" in w:
        return "dollar", "amber"
    if "signed" in v.lower() or "signature" in w or "unsigned" in w:
        return "pen", "red"
    if "mail" in v.lower() or "letter" in w or "prep guide" in w or "session details" in w or "session sheet" in w or "send quote" in w:
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
            acts.append(dict(who=who, what=todo_word(t_["title"]), when=t_["due"], stage=t_["stage"], verb="Mark done",
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

    open_ = [o_ for o_ in event_family_open(eid) if o_["key"] != "releases_signed"]
    can_move = stage in C.STAGES and C.STAGES.index(stage) + 1 < len(C.STAGES) and \
        can_enter(C.STAGES[C.STAGES.index(stage) + 1], s["shoot_date"])
    nxt = C.STAGES[C.STAGES.index(stage) + 1] if can_move else None
    if open_:
        what_ = family_open_words(open_)
        when_ = min((o_["due"] for o_ in open_ if o_["due"]), default=None)
        if can_move and not acts:
            acts.append(dict(who=who, what=what_, when=when_, stage=stage, verb=f"Move all to {C.STAGE_LABELS[nxt]}",
                             post=url_for("event_stage", eid=eid), fields=dict(stage=nxt), primary=True,
                             confirm=f"{what_}. Move all to {C.STAGE_LABELS[nxt]} anyway?"))
        else:
            acts.append(dict(who=who, what=what_, when=when_, stage=stage, verb="See the families",
                             href=url_for("event_view", eid=eid) + "#families"))
    elif not acts and can_move:
        acts.append(dict(who=who, what=f"Everything in {C.STAGE_LABELS[stage]} is done", when=None, stage=stage,
                         verb=f"Move all to {C.STAGE_LABELS[nxt]}", post=url_for("event_stage", eid=eid),
                         fields=dict(stage=nxt), primary=True))
    return acts

FAMILY_OPEN_WORDS = {"agreement_signed": ("agreement still unsigned", "agreements still unsigned"),
                     "releases_signed": ("family's releases still unsigned", "families' releases still unsigned"),
                     "deposit_paid": ("deposit still unpaid", "deposits still unpaid"),
                     "balance_paid": ("balance still owed", "balances still owed")}

def event_family_open(eid):

    fam = q("SELECT * FROM sessions WHERE event_id=? AND stage NOT IN ('lost','closed')", (eid,))
    idx = [C.STAGES.index(f["stage"]) for f in fam if f["stage"] in C.STAGES]
    if not idx:
        return []
    low = C.STAGES[min(idx)]
    out = {}
    for f in fam:
        if f["stage"] != low:
            continue
        w = workflow(f["id"])
        for st_ in (w["current"]["steps"] if w["current"] else []) + w["carried"]:
            if st_["done"] or st_.get("shared") or _past_its_moment(f, st_):
                continue
            if st_.get("key") == "balance_paid" and f["shoot_date"] and f["shoot_date"] > today().isoformat():
                continue
            k_ = st_.get("key") or st_["label"]
            o_ = out.setdefault(k_, dict(key=k_, label=st_["label"], n=0, due=None))
            o_["n"] += 1
            if st_.get("due") and (not o_["due"] or st_["due"] < o_["due"]):
                o_["due"] = st_["due"]
    for o_ in out.values():
        n = o_["n"]
        if o_["key"] in FAMILY_OPEN_WORDS:
            o_["words"] = f"{n} {FAMILY_OPEN_WORDS[o_['key']][0 if n == 1 else 1]}"
        else:
            o_["words"] = f"{todo_word(o_['label'])}, {n} famil{'y' if n == 1 else 'ies'}"
    return list(out.values())

def family_open_words(open_):

    s_ = ", ".join(o_["words"] for o_ in open_)
    return s_[:1].upper() + s_[1:]

def start_time_in(v, keep=""):

    v = (v or "").strip()[:5]
    if v in ("00:00", "0:00"):
        session["notice"] = "A midnight start looks like a slip, so no time was saved. For noon, pick 12:00 PM."
        return keep if keep not in ("00:00",) else ""
    return v if re.fullmatch(r"\d{1,2}:\d{2}", v) else ""

def has_place(s):
    intake_ = json.loads(s["intake"] or "{}")
    return bool((s["stops"] or "").strip() or (intake_.get("venue") or "").strip() or (intake_.get("address") or "").strip())

def pending_docs(sid, kind):

    ref_ = plan_head(sid) if kind == "agreement" else sid
    return q("SELECT id FROM documents WHERE session_id=? AND kind=? AND status!='signed' ORDER BY id", (ref_, kind))

@app.post("/session/<int:sid>/quick")
def quick(sid):
    s = session_full(sid)
    f = request.form
    act = f.get("act")
    day = today().isoformat()
    if act == "sign" and not str(f.get("did") or "").isdigit() and len(pending_docs(sid, f.get("kind", "release"))) > 1:
        session["notice"] = "Several people still have to sign. Tick off the ones who signed, on the paperwork."
        g.refused = True
        return redirect(url_for("session_edit", sid=sid) + "#paperwork")
    doc_ref = plan_head(sid) if f.get("kind") == "agreement" else sid
    if act == "sign" and str(f.get("did") or "").isdigit():
        x("""UPDATE documents SET status='signed', signed=?, sent=COALESCE(sent, ?)
             WHERE id=? AND session_id=? AND kind=? AND status!='signed'""", (day, day, int(f["did"]), doc_ref, f.get("kind", "release")))
    elif act == "sign":
        x("""UPDATE documents SET status='signed', signed=?, sent=COALESCE(sent, ?)
             WHERE session_id=? AND kind=? AND status!='signed'""", (day, day, doc_ref, f.get("kind", "release")))
    elif act == "mark" and f.get("field") in MARKABLE:
        proofs_ = app.config.get("gallery_proofs")
        if f["field"] in ("letter_mailed", "sd_shipped") and release_blocked(sid) and not (
                f["field"] == "letter_mailed" and proofs_ and proofs_(sid)):
            session["notice"] = (f"Not marked: {money(release_blocked(sid))} is still due, and the photos are released on "
                                 "payment. Record the payment, or waive the balance from its step.")
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
        link_ = app.config.get("gallery_link")
        if f["field"] == "letter_mailed" and callable(link_) and not link_(sid):

            session["notice"] = "Not marked: put the gallery online first: the letter's QR code is its link."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
        if f["field"] == "letter_mailed" and proofs_ and proofs_(sid) and deposit_held(sid):
            session["notice"] = (f"Not marked: the {money(deposit_held(sid))} deposit is still unpaid, and the proof gallery "
                                 "link goes out once it is in. Record the payment first.")
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
        on = f.get("on") or ""
        if on and not _past_day(on):
            session["notice"] = "That date is in the future. Nothing was changed."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
        if f["field"] == "first_visit" and (on[:10] if on else day) != (s["first_visit"] or "") and not f.get("confirm_close"):
            page_ = _first_visit_confirm(sid, s, on[:10] if on else day, bool(s["extension_used"]),
                                         url_for("quick", sid=sid), safe_back(f.get("back")) or url_for("home"))
            if page_:
                return page_
        x("UPDATE sessions SET " + dbmod.ident(f["field"]) + "=? WHERE id=?", (on[:10] if on else day, sid))
    elif act == "pay":
        due = totals(sid)["due"]
        if due > 0.005:
            record_payment(sid, "balance" if s["stage"] in OWED_STAGES else "deposit", round(due, 2), day)
            paying_means_yes(sid)
    elif act in ("date", "favorites") and _stale_edit("when" if act == "date" else "picks", sid,
                                                      WHEN_FORM_FIELDS if act == "date" else PICKS_FORM_FIELDS, s):
        return _clash_page(sid, "todo")
    elif act == "date":
        x("UPDATE sessions SET shoot_date=?, start_time=? WHERE id=?",
          (f.get("shoot_date") or None, start_time_in(f.get("start_time", s["start_time"])), sid))
        where = " ".join((f.get("where") or "").split())[:200]
        if where:
            rest = [l_ for l_ in (s["stops"] or "").splitlines() if l_.strip() and l_.strip() != where]
            x("UPDATE sessions SET stops=? WHERE id=?", ("\n".join([where] + rest), sid))
        if f.get("firm"):
            x("UPDATE sessions SET date_tentative=0 WHERE id=?", (sid,))
        elif f.get("tentative_shown"):
            x("UPDATE sessions SET date_tentative=? WHERE id=?", (1 if f.get("tentative") else 0, sid))

        date_moved(sid, s["shoot_date"], was_tentative=s["date_tentative"] or not s["start_time"])
        schedule_tasks(sid, fair=True)
    elif act == "favorites":
        x("UPDATE sessions SET favorites=?, veto=?, retouch_picks=? WHERE id=?",
          (f.get("favorites", ""), f.get("veto", ""), f.get("retouch_picks", ""), sid))
    elif act == "firm":
        x("UPDATE sessions SET date_tentative=0 WHERE id=?", (sid,))
        if s["stage"] == "booked" and can_enter("shot", s["shoot_date"], sid):
            move_on(sid, "shot", "the shoot happened on its tentative date")
    elif act == "extend":
        x("UPDATE sessions SET extension_used=1 WHERE id=?", (sid,))
        hooks.fire("gallery_window_changed", sid)
    elif act == "rebook":
        if not quote_rebook(sid, s):
            g.refused = True
            if not session.get("notice"):
                session["notice"] = "Nothing to book: the quote is not being replaced."
    elif act in ("book", "shot") and s["stage"] not in (("inquiry", "quoted") if act == "book" else ("booked",)):
        g.refused = True
        session["notice"] = f"Already {C.STAGE_LABELS.get(s['stage'], s['stage'])}: nothing changed."
    elif act == "book":
        x("UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND title='Client said yes' AND done IS NULL", (day, sid))
        if q("SELECT 1 FROM tasks WHERE session_id=? AND title=? AND done IS NULL", (sid, SEND_TASKS["quote"]), one=True):
            lq_ = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
            if lq_:
                _mark_quote_sent(lq_["id"], sid)
            x("UPDATE tasks SET done=?, outcome=? WHERE session_id=? AND title=? AND done IS NULL",
              (day, "done" if lq_ else "na", sid, SEND_TASKS["quote"]))
        no_quote_ = s["stage"] == "inquiry" and not q("SELECT 1 FROM lines WHERE session_id=?", (sid,), one=True)
        if no_quote_:
            x("""INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?, 'quote_built', 'na', 'Booked without a quote')
                 ON CONFLICT(session_id, step) DO NOTHING""", (sid,))
        if f.get("final_shown") and s["shoot_date"]:
            x("UPDATE sessions SET date_tentative=? WHERE id=?", (0 if f.get("final") else 1, sid))
            s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        move_on(sid, "booked", "booked without a quote" if s["stage"] == "inquiry" else "the client said yes")
        if s["shoot_date"] and s["shoot_date"][:10] < day and not s["date_tentative"]:
            session["notice"] = (f"Booked. The shoot date, {nice(s['shoot_date'][:10])}, has already passed: set the real date, "
                                 "or press Shoot done if it happened then.")
    elif act == "shot":
        if not can_enter("shot", s["shoot_date"], sid):
            session["notice"] = "Not yet: the shoot date hasn't come, or is still tentative. Set the real date first."
            g.refused = True
            if request.headers.get("X-Fetch"):
                return ("", 409)
            return redirect(safe_back(f.get("back")) or url_for("home"))
        move_on(sid, "shot", "shoot done")
    elif act == "stage" and f.get("stage") in C.STAGES + C.OFF_PIPELINE:
        if f["stage"] == "shot" and s["stage"] != f["stage"] and not can_enter(f["stage"], s["shoot_date"], sid):
            session["notice"] = "Not yet: the shoot date hasn't come, or is still tentative. Set the real date first."
            g.refused = True
            if request.headers.get("X-Fetch"):
                return ("", 409)
            return redirect(safe_back(f.get("back")) or url_for("home"))
        if _forward(s["stage"], f["stage"]):
            move_on(sid, f["stage"], "moved by hand" if f["stage"] != "shot" else "shoot done")
        else:
            set_stage(sid, f["stage"])
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(f.get("back")) or url_for("home"))

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

def clock_rezone(write=None):

    last = setting("clock_last")
    if not last or setting("clock_paused"):
        return
    try:
        gap = (today() - d(last)).days
    except ValueError:
        return
    if abs(gap) <= 1:
        (write or _put_setting)("clock_last", today().isoformat())

app.config["clock_ok"] = clock_ok
app.config["clock_rezone"] = clock_rezone
app.config["today"] = today
app.config["now"] = lambda: datetime.now(TZ).replace(tzinfo=None)

@app.get("/jobs/state")
def jobs_state():

    import jobs
    key_ = request.args.get("key")
    if key_:
        j_ = jobs.JOBS.get(key_) or {}
        return {"state": j_.get("state") or "idle", "done": j_.get("done", 0), "total": j_.get("total", 0)}
    js = jobs.visible()
    run = [j for j in js if j["state"] == "running"]
    return {"state": "running" if run else "idle", "done": sum(j["done"] for j in run), "total": sum(j["total"] for j in run)}

def jobs_visible():
    import jobs
    return jobs.visible()

app.jinja_env.globals["jobs_visible"] = jobs_visible

@app.post("/clock/confirm")
def clock_confirm():

    _put_setting("clock_paused", "")
    _put_setting("clock_last", today().isoformat())
    audit(f"Confirmed today's date ({nice(today().isoformat())}); automatic deletions resume")
    return redirect(url_for("home"))

@app.get("/")
def home():

    if request.args.get("c", "").isdigit():
        return redirect(url_for("client_stage", hid=int(request.args["c"])))
    if request.args.get("e", "").isdigit():
        return redirect(url_for("event_view", eid=int(request.args["e"])))
    acts = build_actions()
    t = today()
    paused = setting("clock_paused")
    upd_ = update_notice()
    waited_, suggest_ = setting("locked_waited"), setting("ntfy_suggest") == "1"
    if waited_ or suggest_:
        x("DELETE FROM settings WHERE key IN ('locked_waited', 'ntfy_suggest')")
        g.tx = True
        try:
            waited_ = locknotice.when(datetime.fromisoformat(waited_), TZ.key) if waited_ else None
        except ValueError:
            waited_ = None
    rv_ = app.config.get("inquiry_review")
    days_ = [d_ for d_ in two_weeks(acts)[1:] if d_["shoots"] or d_["due"]]
    board_ = today_board(acts)
    import jobs as _jobs
    watch_ = app.config.get("today_watch_jobs") or ()
    checking_ = [k_ for k_, j_ in list(_jobs.JOBS.items())
                 if (k_.startswith("client-check:") or k_ in watch_) and j_["state"] == "running"]
    return render_template("today.html", clock_paused=paused.split("|") if paused else None,
                           master_pending=owner_auth.master_pending(db()), updated=upd_, checking=checking_,
                           aside_expiring=_restore.expiring(t),
                           locked_waited=waited_, ntfy_suggest=suggest_ and not ntfy_settings(),
                           kinds=today_filter_kinds(board_), today_iso=t.isoformat(), unconfirmed=unconfirmed_deletions(),
                           loose=[it for fn in hooks.today for it in (fn() or []) if not it.get("who")],
                           raw_held=app.config["raw_held_list"]() if "raw_held_list" in app.config else [],
                           board=board_, days=days_, review=rv_["rows"]() if rv_ else [],
                           review_types=rv_["types"]() if rv_ else [], review_cut=rv_["cut"] if rv_ else 0,
                           review_clients=rv_["clients"]() if rv_ and rv_.get("clients") else [])

def _first_words(text, n=140):

    t_ = " ".join((text or "").replace("Inquiry from your website:", "").split())
    return t_ if len(t_) <= n else t_[:n].rsplit(" ", 1)[0] + "..."

def today_action(s, rows, contact=None):

    stage, sid = s["stage"], s["id"]
    mine = [r for r in rows if not r.get("theirs")]
    lead = next((r for r in mine if r.get("primary")), None) or (mine[0] if mine else None)
    collect_ = next((r for r in mine if r.get("collect")), None)
    book_ = dict(verb="Book it", post=url_for("quick", sid=sid), fields=dict(act="book"), sid=sid,
                 ask_final=(s["shoot_date"] or "") if "shoot_date" in s.keys() else "")
    build_ = dict(verb="Build a quote", href=url_for("client_stage", hid=s["household_id"], s=sid) + "#billing", sid=sid)
    if on_shoot_day(s) and not s["event_id"]:

        if shoot_time_passed(s) and can_enter("shot", s["shoot_date"], sid):
            open_ = [r.get("step_words") or r["what"] for r in rows]
            return dict(what="The shoot time has passed", late=False, when=None, detail="",
                        a=dict(verb="Shoot done", post=url_for("quick", sid=sid), fields=dict(act="shot"), sid=sid,
                               confirm=(", ".join(open_) + (" is" if len(open_) == 1 else " are") + " still open. Move to Shot?") if open_ else None))
        return None
    lapse_ = next((r for r in mine if r.get("pencil_lapse")), None)
    if lapse_ and stage in LEAD_STAGES and not s["event_id"]:
        return dict(what=lapse_["what"], late=False, when=None, detail="", a=lapse_)
    if stage == "inquiry" and not s["event_id"]:
        first = message_view(q("SELECT summary, at, channel FROM log WHERE session_id=? ORDER BY at, id LIMIT 1", (sid,), one=True), s)
        what = (_first_words(first["body"]) or "New inquiry, no message, only the inquiry") if first else "New inquiry, no message on file"
        draft_ = reply_draft(sid) if contact and (contact["email"] or "").strip() else ""
        if draft_:
            return dict(what=what, late=False, when=None, detail="",
                        a=dict(verb="Reply", post=url_for("session_reply", sid=sid), mail=draft_, sid=sid))
        return dict(what=what, late=False, when=None, detail="", a=build_)
    if stage == "quoted" and not s["event_id"]:
        fu_ = next((r for r in mine if r.get("followup")), None)
        if fu_:
            return dict(what=fu_["what"], late=False, when=None, detail="",
                        a=dict(verb="Follow up", post=fu_["post"], fields=fu_.get("fields") or {}, mail=fu_["doc"], sid=sid))
        return dict(what="Quote out, waiting on their answer", late=False, when=None, detail="", a=book_)
    retouch_ = next((r for r in mine if r.get("retouch")), None)
    if retouch_:
        return dict(what=retouch_["what"], late=retouch_.get("group") == "overdue", when=retouch_.get("when"), detail="",
                    a=retouch_)
    if collect_:
        return dict(what=collect_.get("what") or collect_["verb"], late=collect_.get("group") == "overdue",
                    when=collect_.get("when"), detail="", a=collect_)
    if not lead:
        return None
    late = lead.get("group") == "overdue"
    return dict(what=lead.get("what") or "", late=late, when=lead.get("when"), detail=lead.get("detail") or "", a=lead)

def session_nexts(sessions_):

    open_ = [s_ for s_ in sessions_ if s_["stage"] not in ("closed", "lost") and not s_["event_id"]]
    if not open_:
        return {}
    by_link = {}
    for a in build_actions():
        by_link.setdefault(a["who"]["link"], []).append(a)
    out = {}
    for s_ in open_:
        rows = focus_rows(by_link.get(url_for("session_view", sid=s_["id"]), []), s_, totals(s_["id"]))
        out[s_["id"]] = today_action(s_, rows)
    return out

def on_shoot_day(s):

    return s["stage"] == "booked" and (s["shoot_date"] or "")[:10] == today().isoformat()

def shoot_time_passed(s):

    now_ = datetime.now(TZ)
    sd_ = (s["shoot_date"] or "")[:10]
    if not sd_:
        return False
    return sd_ < now_.date().isoformat() or (sd_ == now_.date().isoformat() and bool(s["start_time"])
                                             and s["start_time"][:5] <= now_.strftime("%H:%M"))

TASK_KINDS = (("Retouch", ("retouch",)), ("Back up", ("back up", "backup", "original photos")), ("Cull", ("cull",)), ("Edit", ("edit",)),
              ("Refunds", ("refund",)), ("Invoices", ("invoice",)), ("Payments", ("collect", "balance", "deposit", "payment", "owed")),
              ("Quotes", ("quote", "book it", "said yes")), ("Galleries", ("gallery",)), ("Letters", ("letter",)),
              ("Paperwork", ("agreement", "release", "sign", "photo rules")), ("Questions", ("intake", "questions")),
              ("Follow up", ("follow up", "reply", "picks")), ("Date and place", ("date", "time and place", "venue", "location", "scout")),
              ("Shoot", ("shoot",)), ("Delivery", ("deliver", "send", "photos", "copies")))

def task_kind(what):
    w_ = (what or "").lower()
    return next((k_ for k_, words in TASK_KINDS if any(x_ in w_ for x_ in words)), "Other") if w_ else ""

def today_filter_kinds(board):

    lines_ = [l_ for k_ in ("money", "work", "other", "quiet") for l_ in board[k_]]
    return dict(kind=[l_["f"]["kind"] for l_ in lines_ if l_["f"]["kind"]],
                label=[x_ for l_ in lines_ for x_ in l_["f"]["labels"]])

def today_board(acts):

    t = today().isoformat()
    by_link = {}
    for a in acts:
        by_link.setdefault(a["who"]["link"], []).append(a)
    out = dict(shoots=[], wrote=[], money=[], work=[], quiet=[], other=[])
    reps = event_reps()
    fresh = new_wrote()
    contacts = {}
    for s in q("""SELECT s.*, h.name AS household, e.name AS event_name, e.venue AS venue FROM sessions s
                  JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
                  WHERE s.stage NOT IN ('closed','lost') ORDER BY COALESCE(s.shoot_date,'9999'), s.start_time, s.id"""):
        ev = bool(s["event_id"])
        if ev and reps.get(s["event_id"]) != s["id"]:
            continue
        link = url_for("event_view", eid=s["event_id"]) if ev else url_for("session_view", sid=s["id"])
        mine = by_link.pop(link, [])
        if ev:
            rows, contact = mine, None
        else:
            rows = focus_rows(mine, s, totals(s["id"]))
            if s["household_id"] not in contacts:
                contacts[s["household_id"]] = household_contact(q(
                    "SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (s["household_id"],)))
            contact = contacts[s["household_id"]]
        line = dict(key=f"e{s['event_id']}" if ev else f"s{s['id']}", event=ev, sid=s["id"], hid=None if ev else s["household_id"],
                    eid=s["event_id"], name=(s["event_name"] if ev else s["household"]),
                    href=link if ev else url_for("client_stage", hid=s["household_id"], s=s["id"]),
                    kind="Group event" if ev else C.SESSION_TYPES.get(s["type"], s["type"]), stage=s["stage"], s=s)
        do = today_action(s, rows, contact)
        line["do"] = do

        line["due_any"] = min((str(r_["when"])[:10] for r_ in rows if r_.get("when") and not r_.get("theirs")), default="")
        line["note"] = next((r["what"] for r in rows if r.get("theirs") and r.get("what") not in {C.dw(w_) for w_ in THEIR_WORDS.values()}
                             and "money" not in (r.get("tags") or "")), "")
        if on_shoot_day(s):
            line.update(shoot_block(s, rows, contact))
            out["shoots"].append(line)
            continue
        if s["stage"] == "inquiry" and not ev:

            line.update(why="wrote", new=f"s{s['id']}" in fresh)
            if any(a_.get("notice") and a_.get("what") == "New inquiry from the website" for a_ in mine):
                line["note"] = "Inquiry from your website"
            out["work"].append(line)
            continue
        picks_ = not ev and s["stage"] in ("shot", "delivered") and any((s[k_] or "").strip() for k_ in ("favorites", "retouch_picks", "veto"))
        retouch_ = next((r for r in rows if r.get("retouch") and not r.get("theirs")), None)
        if picks_ and (retouch_ or f"p{s['id']}" in fresh):

            line.update(why="picks", new=f"p{s['id']}" in fresh)
            line["do"] = (dict(do, what=do["what"] if retouch_ else "Sent their picks: " + do["what"],
                               detail=do["detail"] or (s["retouch_picks"] or "").strip())
                          if do else dict(what="Sent their picks", a=None, late=False, when=None, detail=(s["retouch_picks"] or "").strip()))
            out["work"].append(line)
            continue
        late_money = next((r for r in rows if "money" in (r.get("tags") or "") and r.get("when") and r["when"] < t
                           and r.get("stage") != "lost"), None)
        if late_money:
            line["do"] = dict(what=late_money["what"], a=late_money, late=True,
                              when=late_money["when"], detail="")
            out["money"].append(line)
            continue
        (out["work"] if do else out["quiet"]).append(line)
    for link, rest in by_link.items():
        for a in rest:
            who = a["who"]
            out["other"].append(dict(key="o" + str(len(out["other"])), event=False, sid=a.get("sid"), hid=who.get("hid"),
                                     eid=who.get("eid"), name=who.get("name") or "", kind=who.get("sub") or "",
                                     href=url_for("client_stage", hid=who["hid"]) if who.get("hid") else who["link"],
                                     stage=a.get("stage") or "", s=None,
                                     do=dict(what=a.get("what") or "", a=a, late=a.get("group") == "overdue", when=a.get("when"), detail="",
                                             help=a.get("help") or "")))
    labels_ = client_labels()
    for sec_, lines_ in out.items():
        for l_ in lines_:
            do_ = l_.get("do") or {}
            kind_ = ("Shoot" if sec_ == "shoots" else "Inquiries" if l_.get("why") == "wrote"
                     else task_kind(do_.get("what") or ""))
            l_["f"] = dict(status="waiting" if sec_ == "quiet" or not do_.get("a") else "mine",
                           due=t if sec_ == "shoots" else min([w_ for w_ in ((do_.get("when") or "")[:10], l_.get("due_any") or "") if w_]
                                                              or [""]), kind=kind_ if sec_ != "quiet" else "",
                           labels=labels_.get(l_.get("hid"), []))
    out["wrote"].sort(key=lambda l_: 0 if l_.get("new") else 1)
    out["money"].sort(key=lambda l_: l_["do"]["when"] or "")
    out["work"].sort(key=lambda l_: (0 if l_["do"]["late"] else 1, 0 if l_.get("new") else 1, l_["do"]["when"] or "9999"))
    return out

def shoot_block(s, rows, contact):

    if s["event_id"]:
        place, intake_ = (s["venue"] or "").strip(), []
    else:
        x_ = focus_extra(s, session_full(s["id"]), None)
        place = x_["place"]
        ans = json.loads(s["intake"] or "{}")
        intake_ = [(label, ans[k_]) for k_, label in (C.INTAKE.get(s["type"]) or []) if (ans.get(k_) or "").strip()]
    return dict(time=clock(s["start_time"]) if s["start_time"] else "", place=place,
                light=light(s["shoot_date"], None if s["event_id"] else s["type"],
                            place if s["event_id"] else shoot_place(s)),
                phone=(contact["phone"] or "").strip() if contact else "", who=(contact["name"] if contact else ""),
                intake=intake_, open_items=[r.get("step_words") or r["what"] for r in rows if not r.get("primary")])

TODAY_SEEN = "today_seen"

def wrote_items():

    out = []
    for r_ in q("""SELECT s.id, s.stage, s.favorites, s.retouch_picks, s.veto, s.created,
                          (SELECT MIN(l.id) FROM log l WHERE l.session_id=s.id) AS first_log
                   FROM sessions s WHERE s.event_id IS NULL AND s.stage IN ('inquiry', 'shot', 'delivered')"""):
        if r_["stage"] == "inquiry":
            out.append(dict(key=f"s{r_['id']}", sid=r_["id"], kind="inquiry", sig=f"{r_['first_log'] or r_['created'] or ''}"))
            continue
        picks_ = "\n".join((r_[k_] or "").strip() for k_ in ("favorites", "retouch_picks", "veto"))
        if picks_.strip():
            out.append(dict(key=f"p{r_['id']}", sid=r_["id"], kind="picks", sig=hashlib.sha256(picks_.encode()).hexdigest()[:16]))
    return out

def _seen():
    try:
        v_ = json.loads(setting(TODAY_SEEN) or "{}")
        return v_ if isinstance(v_, dict) else {}
    except ValueError:
        return {}

def new_wrote():

    seen = _seen()
    return {it["key"] for it in wrote_items() if seen.get(it["key"]) != it["sig"]}

def mark_seen(sid):

    items = wrote_items()
    seen, mine = _seen(), [it for it in items if it["sid"] == sid]
    if not mine or all(seen.get(it["key"]) == it["sig"] for it in mine):
        return
    live = {it["key"] for it in items}
    seen = {k_: v_ for k_, v_ in seen.items() if k_ in live}
    seen.update({it["key"]: it["sig"] for it in mine})
    _put_setting(TODAY_SEEN, json.dumps(seen, sort_keys=True))

def inbox_count():

    try:
        rv_ = app.config.get("inquiry_review")
        return rv_["count"]() if rv_ else 0
    except Exception:
        return 0

app.jinja_env.globals["inbox_count"] = inbox_count

def stage_dates(sr, docs, pays):

    got = {r["stage"]: r["at"][:10] for r in q("""SELECT stage, MIN(at) at FROM stage_log WHERE session_id=?
                                                    AND COALESCE(era, '') != 'upgrade' GROUP BY stage""", (sr["id"],))}
    upg = {r["stage"]: r["at"][:10] for r in q("""SELECT stage, MIN(at) at FROM stage_log WHERE session_id=?
                                                    AND era='upgrade' GROUP BY stage""", (sr["id"],))}
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
            out[st] = got.get(st) or guess.get(st) or upg.get(st)
    if "quoted" in out and not out["quoted"]:
        out["quoted"] = out.get("inquiry")
    return out

def days_in_stage(sr):

    sr = q("SELECT * FROM sessions WHERE id=?", (sr["id"],), one=True) or sr
    last_ = q("SELECT at, era FROM stage_log WHERE session_id=? AND stage=? ORDER BY at DESC, id DESC LIMIT 1",
              (sr["id"], sr["stage"]), one=True)
    if last_ and last_["at"] and last_["era"] != "upgrade":
        return max(0, (today() - d(last_["at"][:10])).days)
    docs = q("SELECT kind, status, signed FROM documents WHERE session_id=?", (sr["id"],))
    pays = q("SELECT received FROM payments WHERE session_id=?", (sr["id"],))
    since = stage_dates(sr, docs, pays).get(sr["stage"])
    if since and last_ and since >= last_["at"][:10]:
        since = None
    if not since and not last_:
        since = (sr["stage_changed"] or "")[:10] or None
    if not since:
        return None
    return max(0, (today() - d(since)).days)

def journey(sr, acts):

    sid = sr["id"]
    t = today().isoformat()
    docs = q("""SELECT d.*, COALESCE(NULLIF(d.covers, ''), p.name) AS person FROM documents d LEFT JOIN people p ON p.id=d.about_person WHERE session_id=?""", (sid,))
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
        past.append(dict(d=tk["done"], kind="step", label=step_says(tk["title"]), sub="", size=8))

    if sr["event_id"]:
        for tk in q("SELECT * FROM tasks WHERE event_id=? AND session_id IS NULL AND done IS NOT NULL", (sr["event_id"],)):
            past.append(dict(d=tk["done"], kind="step", label=step_says(tk["title"]), sub="whole event", size=8))
    for c_ in q("SELECT * FROM step_closes WHERE session_id=?", (sid,)):
        lab = AUTO[c_["step"]][0] if c_["step"] in AUTO else c_["step"]
        wo_ = written_off(sid) if c_["step"] == "balance_paid" and c_["outcome"] == "waived" else 0
        past.append(dict(d=(c_["at"] or t)[:10], kind="step", label=f"{money(wo_)} waived" if wo_ > 0.005 else
                         f"{lab} ({OUTCOMES.get(c_['outcome'], c_['outcome']).lower()})",
                         sub=c_["note"] or "", size=8))
    qt_ = q("SELECT created FROM quotes WHERE session_id=?", (sid,), one=True)
    if qt_ and qt_["created"]:
        past.append(dict(d=qt_["created"][:10], kind="money", label="Quote built", sub="", size=9))
    for v_ in q("SELECT id, at, number FROM quote_versions WHERE session_id=?", (sid,)):
        past.append(dict(d=v_["at"][:10], kind="money", label=f"Quote {v_['number'] or v_['id']} sent", sub="", size=10))
    for a in acts:
        if a.get("when"):
            st_ = status_of(a["when"])
            days = (d(a["when"]) - today()).days
            future.append(dict(d=a["when"], kind=a.get("kind", "step"), label=a["what"], late=st_ == "late", status=st_,
                               size=16 if st_ == "late" else 14 if days <= 3 else 11 if days <= 7 else 9))
    shoot = sr["shoot_date"]
    def ok_(v):
        if not re.fullmatch(r"(19|20|21)\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", str(v)):
            return False
        try:
            return bool(date.fromisoformat(str(v)))
        except ValueError:
            return False
    past = [x_ for x_ in past if ok_(x_["d"])]
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
    for x_ in past:
        x_["tip"] = f"{nice(x_['d'])}: {x_['label']}" + (f" ({x_['sub']})" if x_["sub"] else "")
    for x_ in future:
        x_["tip"] = (("Problem: " if x_["status"] == "late" else "Warning: " if x_["status"] == "soon" else "")
                     + f"{x_['label']}, {'was due ' if x_['status'] == 'late' else 'due '}{nice(x_['d'])}")
    past, future = _same_day(past, future=False), _same_day(future, future=True)
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
    keys = [st for st in C.STAGES if st in sd and sd[st] and ok_(sd[st])]

    at_, prev_ = {}, None
    for st in keys:
        at_[st] = max(sd[st][:10], prev_) if prev_ else sd[st][:10]
        prev_ = at_[st]
    for i, st in enumerate(keys):
        a_ = pos(at_[st]); b_ = pos(at_[keys[i + 1]]) if i + 1 < len(keys) else pos(t)
        a_, b_ = min(max(a_, 0.0), 100.0), min(max(b_, 0.0), 100.0)
        bands.append(dict(stage=st, a=a_, w=max(0.8, b_ - a_)))
    ticks = []
    dd = start
    while dd <= end:
        if dd.weekday() == 0:
            ticks.append(dict(x=pos(dd.isoformat()), label=dd.strftime("%b %-d")))
        dd += timedelta(days=1)

    if len(ticks) > 1:
        gap_ = max(ticks[1]["x"] - ticks[0]["x"], 0.01)
        wide_, phone_ = max(1, math.ceil(5 / gap_)), max(1, math.ceil(12 / gap_))
        phone_ = math.ceil(phone_ / wide_) * wide_
        ticks = [dict(tk, minor=i % phone_ != 0) for i, tk in enumerate(ticks) if i % wide_ == 0]
    tpos, spos = pos(t), (pos(shoot) if shoot else None)
    ticks = [tk for tk in ticks if abs(tk["x"] - tpos) > 6 and (spos is None or abs(tk["x"] - spos) > 6)]
    last_lab_ = -99.0
    for b in bands:
        b["label"] = b["w"] >= 8 and not (b["a"] - 2 < tpos < b["a"] + 10) and b["a"] - last_lab_ >= 8
        if b["label"]:
            last_lab_ = b["a"]
    return dict(past=past, future=future, today=tpos, shoot=spos, shoot_d=shoot, shoot_label=spos is None or abs(spos - tpos) > 14,
                bands=bands, ticks=ticks, docs=docs, pays=pays, stage_dates=sd)

def _same_day(marks, future):

    days = {}
    for m_ in marks:
        days.setdefault(m_["d"], []).append(m_)
    out = []
    for day, ms in days.items():
        if len(ms) == 1:
            ms[0]["n"] = 1
            out.append(ms[0])
            continue
        if future:
            rank = {"late": 0, "soon": 1}
            ms = sorted(ms, key=lambda m_: rank.get(m_["status"], 2))
            head = dict(ms[0], n=len(ms), late=any(m_["late"] for m_ in ms),
                        tip=f"{len(ms)} due {nice(day)}:\n" + "\n".join(m_["tip"].rsplit(", ", 1)[0] for m_ in ms))
        else:
            ms = sorted(ms, key=lambda m_: not m_.get("show"))
            head = dict(ms[0], n=len(ms), show=any(m_.get("show") for m_ in ms),
                        tip=f"{nice(day)}, {len(ms)} things:\n" + "\n".join(m_["tip"].split(": ", 1)[1] for m_ in ms))
        out.append(head)
    return out

HISTORY_AREAS = [
    (("signed in from", "signed in and unlocked"), "lock"),
    (("exported", "downloaded"), "download"),
    (("printed the",), "print"),
    (("sealed",), "lock"),
    (("written off", "wrote off", "write-off", "waived"), "money_off"),
    (("print order", "print invoice", "prints"), "print"),
    (("picks",), "star"),
    (("studio page", "web host"), "web"),
    (("website inquir",), "inbox"),
    (("uploaded",), "cloud_upload"),
    (("deleted", "removed"), "delete"),
    (("letter",), "mail"),
    (("payment", "deposit", "refund", "void", "recorded ", "balance", "check #", "paid"), "payments"),
    (("quote", "fee", "discount", "line", "sales tax", "tax ", "price", "invoice", "receipt"), "request_quote"),
    (("agreement", "release", "signed", "wording", "document", "sample", "reviewed", "session details", "session sheet", "statement"), "description"),
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

SECURITY_LINES = ("Security:", "Exported everything held about this client", "Exported the settings", "Exported accounts for",
                  "Created the owner account", "Unlocked with the master key", "Checked the master key",
                  "Put off checking the master key", "Changed the password", "Made new recovery codes",
                  "Signed out every other device", "Started creating the vault master key", "Created the vault master key",
                  "Confirmed the vault master key", "Checked the newest backup opens", "Set up the locked-studio notice",
                  "Changed the locked-studio notice", "Turned off the locked-studio notice", "Signed in")

def is_security_line(action):
    return (action or "").startswith(SECURITY_LINES)

def account_activity(limit=300):

    names_ = {r["id"]: r["name"] for r in q("SELECT id, name FROM households")}
    rows = []
    for r in q("SELECT * FROM audit WHERE " + " OR ".join("action LIKE ?" for _ in SECURITY_LINES)
               + " ORDER BY at DESC, id DESC LIMIT ?", tuple(p_.replace("%", "") + "%" for p_ in SECURITY_LINES) + (limit,)):
        text = r["action"]
        if text.startswith("Exported everything held about this client"):
            who_ = names_.get(r["household_id"])
            text = (f"Downloaded a file of everything kept about {who_} (from Edit client details)" if who_ else
                    "Downloaded a file of everything kept about a client since deleted (from Edit client details)")
        elif text.startswith("Security: "):
            text = text[len("Security: "):][:1].upper() + text[len("Security: ") + 1:]
        at_ = audit_local(r["id"], r["at"], r["action"])
        rows.append(dict(at=at_, kind="audit", who=r["who"], text=text, note=r["note"], sid=None, seq=r["id"], icon=history_icon(text),
                         day=at_[:10], time=clock(at_[11:16])))
    rows.sort(key=lambda r_: (r_["at"], r_["seq"]), reverse=True)
    return history_collapse(rows)

app.jinja_env.globals["account_activity"] = account_activity

def client_history(hid):
    rows = [dict(at=r["at"], kind="audit", who=r["who"], text=r["action"], note=r["note"], sid=r["session_id"], icon=history_icon(r["action"]), seq=r["id"])
            for r in q("SELECT * FROM audit WHERE household_id=?", (hid,)) if not is_security_line(r["action"])]
    rows += [dict(at=r["at"] if len(r["at"]) > 10 else r["at"] + " 00:00:00", kind=r["channel"], who=setting("photographer_name") or "You", text=r["summary"],
                  note="", sid=r["session_id"], lid=r["id"], written=r["written_at"])
             for r in q("SELECT * FROM log WHERE household_id=?", (hid,))]
    rows.sort(key=lambda r_: (r_["at"], r_.get("seq", 0)), reverse=True)
    sids_ = sorted({r_["sid"] for r_ in rows if r_["sid"]})
    here_ = {r_["id"] for r_ in q("SELECT id FROM sessions WHERE id IN (%s)" % ",".join("?" * len(sids_)), sids_)} if sids_ else set()
    for r_ in rows:
        r_["gone"] = bool(r_["sid"]) and r_["sid"] not in here_
        r_["day"] = r_["at"][:10]
        r_["time"] = clock(r_["at"][11:16]) if r_["at"][11:16] != "00:00" else ""
    return history_collapse(rows)

def history_collapse(rows):

    out, seen = [], {}
    t_ = today().isoformat()
    for r_ in rows:
        if r_["kind"] != "audit":
            out.append(r_)
            continue
        k_ = (r_["day"], r_["text"], r_["note"], r_["who"], r_["sid"], r_.get("hid"))
        if k_ in seen:
            seen[k_]["n"] += 1
            continue
        r_ = dict(r_, n=1)
        seen[k_] = r_
        out.append(r_)
    for r_ in out:
        if r_.get("n", 1) > 1:
            r_["text"] = f"{r_['text']} \u00b7 {r_['n']} times" + (" today" if r_["day"] == t_ else "")
    return out

HISTORY_KINDS = [
    (("did not verify", "unsigned entry", "changed outside"), "Checks"),
    (("after this backup was made",), "Restores"),
    (("imported file", "imported clients", "removed an import"), "Imports"),
    (("deleted", "removed"), "Deletions"),
]
HISTORY_KIND_OF_ICON = {"payments": "Payments", "money_off": "Payments", "request_quote": "Quotes and bills",
                        "description": "Paperwork", "photo_library": "Photos and galleries", "cloud_upload": "Photos and galleries",
                        "star": "Photos and galleries", "lock": "Photos and galleries", "print": "Print orders",
                        "event": "Dates", "arrow_forward": "Stages", "task_alt": "Steps", "person": "Client details",
                        "assignment": "Client details", "mail": "Letters and email", "download": "Exports",
                        "inbox": "Inquiries", "web": "Studio page"}
STUDIO_HISTORY_PAGE = 400

HISTORY_CHIP_ORDER = ["Payments", "Paperwork", "Quotes and bills", "Photos and galleries", "Letters and email",
                      "Notes and contact", "Dates", "Stages", "Steps", "Client details", "Inquiries", "Print orders",
                      "Studio page", "Imports", "Exports", "Deletions", "Restores", "Checks", "Other"]

def history_kind(text, kind="audit"):
    if kind != "audit":
        return "Notes and contact"
    t_ = (text or "").lower()
    return next((k_ for words, k_ in HISTORY_KINDS if any(w in t_ for w in words)),
                HISTORY_KIND_OF_ICON.get(history_icon(text), "Other"))

def studio_history(hid=None, start="", end="", term="", page=1):

    where_a, args_a, where_l, args_l = ["NOT (" + " OR ".join("a.action LIKE ?" for _ in SECURITY_LINES) + ")"], \
        [p_ + "%" for p_ in SECURITY_LINES], [], []
    if hid == 0:
        where_a.append("a.household_id IS NULL AND a.session_id IS NULL AND a.event_id IS NULL")
        where_l.append("0")
    elif hid:
        where_a.append("(a.household_id=? OR a.session_id IN (SELECT id FROM sessions WHERE household_id=?))")
        args_a += [hid, hid]
        where_l.append("l.household_id=?")
        args_l.append(hid)
    for op_, v_ in ((">=", start), ("<", end + "~" if end else "")):
        if v_:
            where_a.append(f"a.at {op_} ?")
            args_a.append(v_)
            where_l.append(f"l.at {op_} ?")
            args_l.append(v_)
    if term:
        like_ = f"%{term}%"
        where_a.append("(a.action LIKE ? OR a.note LIKE ? OR h.name LIKE ?)")
        args_a += [like_] * 3
        where_l.append("(l.summary LIKE ? OR h.name LIKE ?)")
        args_l += [like_] * 2
    sql_ = ("SELECT 'audit' AS src, a.id AS id, a.at AS at, COALESCE(a.household_id, s.household_id) AS hid, a.session_id AS sid, a.event_id AS eid,"
            " a.action AS text, a.note, a.who, NULL AS written FROM audit a LEFT JOIN sessions s ON s.id=a.session_id"
            " LEFT JOIN households h ON h.id=COALESCE(a.household_id, s.household_id) WHERE " + " AND ".join(where_a)
            + " UNION ALL SELECT l.channel, l.id, CASE WHEN length(l.at) > 10 THEN l.at ELSE l.at || ' 00:00:00' END, l.household_id,"
            " l.session_id, NULL, l.summary, '', '', l.written_at FROM log l LEFT JOIN households h ON h.id=l.household_id"
            + (" WHERE " + " AND ".join(where_l) if where_l else "")
            + " ORDER BY at DESC, id DESC LIMIT ? OFFSET ?")
    page = max(1, page)
    got = q(sql_, (*args_a, *args_l, STUDIO_HISTORY_PAGE + 1, (page - 1) * STUDIO_HISTORY_PAGE))
    more = len(got) > STUDIO_HISTORY_PAGE
    names_ = {r_["id"]: r_["name"] for r_ in q("SELECT id, name FROM households")}
    sess_ = {r_["id"]: r_ for r_ in q("SELECT id, household_id, type, shoot_date FROM sessions")}
    events_ = {r_["id"]: r_["name"] for r_ in q("SELECT id, name FROM events")}
    me_ = setting("photographer_name") or "You"
    rows = []
    for r_ in got[:STUDIO_HISTORY_PAGE]:
        audit_ = r_["src"] == "audit"
        hid_, sid_ = r_["hid"], r_["sid"]
        if sid_ in sess_:
            hid_ = hid_ or sess_[sid_]["household_id"]
        who_ = names_.get(hid_) or (events_.get(r_["eid"]) if r_["eid"] else None)
        link_ = (url_for("client_stage", hid=hid_, s=sid_) if hid_ in names_ and sid_ in sess_ else
                 url_for("client_stage", hid=hid_) if hid_ in names_ else
                 url_for("event_view", eid=r_["eid"]) if r_["eid"] in events_ else "")
        s_ = sess_.get(sid_)
        rows.append(dict(at=r_["at"], kind="audit" if audit_ else r_["src"], who=r_["who"] if audit_ else me_, text=r_["text"],
                         note=r_["note"] or "", sid=sid_, seq=r_["id"], icon=history_icon(r_["text"]),
                         written=r_["written"], client=who_ or "", link=link_, hid=hid_,
                         session=(f"{C.SESSION_TYPES.get(s_['type'], s_['type'])}" + (f", {nice(s_['shoot_date'])}" if s_["shoot_date"] else ""))
                         if s_ else "",
                         gone=bool(sid_) and sid_ not in sess_, day=r_["at"][:10],
                         time=clock(r_["at"][11:16]) if r_["at"][11:16] != "00:00" else "",
                         what=history_kind(r_["text"], "audit" if audit_ else r_["src"])))
    return history_collapse(rows), more

@app.get("/history")
def studio_history_view():

    args_ = request.args
    hid_raw = args_.get("client", "")
    hid = 0 if hid_raw == "none" else int(hid_raw) if is_id(hid_raw) else None
    day_ = lambda v: v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or "") else ""
    start, end = day_(args_.get("from")), day_(args_.get("to"))
    if start and end and start > end:
        start, end = end, start
    term = (args_.get("q") or "").strip()[:100]
    page = int(args_["page"]) if is_id(args_.get("page", "")) and int(args_["page"]) > 0 else 1
    rows, more = studio_history(hid, start, end, term, page)
    months = []
    for r_ in rows:
        m_ = r_["day"][:7]
        if not months or months[-1]["key"] != m_:
            months.append(dict(key=m_, label=d(m_ + "-01").strftime("%B %Y"), rows=[]))
        months[-1]["rows"].append(r_)
    keep = {k_: v_ for k_, v_ in (("client", hid_raw if hid is not None else ""), ("from", start), ("to", end), ("q", term)) if v_}
    return render_template("history.html", months=months, rows=rows, more=more, page=page, keep=keep, hid=hid,
                           start=start, end=end, term=term, today_iso=today().isoformat(), kind_order=HISTORY_CHIP_ORDER,
                           clients=q("SELECT id, name FROM households ORDER BY name COLLATE NOCASE"))

UI_BLOCKS = {"div", "p", "li", "tr", "section", "ul", "ol", "table", "form", "h2", "h3", "h4", "details", "label",
             "header", "footer", "pre", "blockquote", "dl", "dt", "dd"}
UI_VOID = {"input", "br", "img", "hr", "meta", "link", "source", "wbr", "col", "area"}

def ui_shape(html):

    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.leaves, self.loose, self.first = [], 0, 0, None

        def handle_starttag(self, tag, attrs):
            if tag in UI_VOID:
                if not self.stack and tag == "input" and dict(attrs).get("type") != "hidden":
                    self.loose += 1
                return
            block = tag in UI_BLOCKS
            if block and self.stack:
                for fr in self.stack:
                    if fr["block"]:
                        fr["has_block"] = True
            if not self.stack and not block:
                self.loose += 1
            fr = dict(tag=tag, block=block, has_block=False, cls=dict(attrs).get("class") or "")
            if self.first is None:
                self.first = fr
            self.stack.append(fr)

        def handle_endtag(self, tag):
            while self.stack:
                fr = self.stack.pop()
                if fr["block"] and not fr["has_block"]:
                    self.leaves += 1
                if fr["tag"] == tag:
                    break

        def handle_data(self, data):
            if data.strip() and not self.stack:
                self.loose += 1

    p_ = P()
    p_.feed(str(html or ""))
    p_.close()
    n = p_.leaves + p_.loose
    f = p_.first or {}
    lone = n <= 1 and (f.get("tag") in ("form", "a", "button") or "ui-fact" in f.get("cls", "")
                       or "ui-btnrow" in f.get("cls", ""))
    return dict(lines=n, lone=lone)

app.jinja_env.filters["ui_shape"] = ui_shape

FOCUS_TIERS = {

    "message":      (1, 3, 4, 4, 4, 4, 4),
    "quote":        (1, 2, 2, 2, 2, 2, 2),
    "shoot":        (3, 3, 1, 1, 2, 4, 4),
    "light":        (0, 0, 3, 1, 0, 0, 0),
    "intake":       (3, 3, 3, 1, 4, 4, 4),
    "paperwork":    (0, 3, 2, 2, 4, 4, 4),
    "photos":       (0, 0, 0, 3, 1, 3, 4),
    "gallery":      (0, 0, 0, 0, 1, 2, 2),
    "picks":        (0, 0, 0, 0, 2, 2, 4),
    "delivery":     (0, 0, 0, 0, 3, 1, 4),
    "people":       (3, 3, 3, 3, 4, 4, 4),
    "timeline":     (0, 0, 1, 1, 1, 1, 4),
    "storage":      (0, 0, 0, 0, 3, 3, 4),
}
FOCUS_STAGES = ("inquiry", "quoted", "booked", "shoot_day", "shot", "delivered", "closed")
FOCUS_BLOCK_KEY = {"Gallery": "gallery", "Letters": "delivery", "Photo storage": "storage"}

def focus_stage(s):

    if s["stage"] == "booked" and s["shoot_date"] and s["shoot_date"][:10] == today().isoformat():
        return "shoot_day"
    return s["stage"] if s["stage"] in FOCUS_STAGES else "closed"

def button_words(a):

    if a.get("wait"):
        return ""
    if a.get("drop_main"):
        return a.get("drop_verb") or ""
    if a.get("doc") and not (a.get("mail") and a.get("post")) and not a.get("form"):
        return a.get("doc_verb") or ""
    return a.get("verb") or ""

def object_title(what, button):

    w_, b_ = (what or "").strip(), (button or "").strip()
    if not w_ or not b_:
        return what
    verb = next((v_ for v_ in ("Follow up", b_.split(" ", 1)[0]) if b_.startswith(v_ + " ") or b_ == v_), "")
    if not verb or not w_.startswith(verb + " "):
        return what
    rest = w_[len(verb) + 1:].strip()
    for art in ("the ", "a ", "an "):
        if rest.lower().startswith(art):
            rest = rest[len(art):]
            break
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1]
    return rest[:1].upper() + rest[1:] if rest else what

def focus_rows(acts, s, t):

    out = []
    owed = release_blocked(s["id"]) if s["stage"] in ("shot", "delivered") else 0
    for a in acts:
        if a.get("notice") and a.get("what") == "New inquiry from the website":
            continue
        if s["stage"] in ("inquiry", "quoted") and a.get("what") == THEIR_WORDS["Client said yes"]:
            continue
        if a.get("shoot_done") and focus_stage(s) == "shoot_day":
            continue
        a = dict(a)
        if a.get("client_check"):
            alt_ = a.pop("alt", None)
            for k_ in ("verb", "post", "fields"):
                a.pop(k_, None)
            if alt_:
                a.update(verb=alt_["verb"], post=alt_["post"], fields=alt_.get("fields") or {})
            else:
                a["wait"] = ("Since " + nice(a["since"])) if a.get("since") else "Waiting"
        verb = a.get("verb") or ""
        fld = (a.get("fields") or {}).get("field")
        if (a.get("href") or "").endswith("#money") and verb.startswith("Record payment") and not a.get("form"):

            a.update(form="pay_balance", pay_amt=t["due"], sid=s["id"])
            if a.get("collect"):
                a["deliver"] = None
        elif owed > 0.005 and not (a.get("wait") or a.get("form") or a.get("doc")) and (
                fld in ("sd_shipped", "letter_mailed") or a.get("collect")):

            a.update(form="pay_balance", pay_amt=t["due"], sid=s["id"], verb=f"Record payment ({money(owed)})", post=None, doc=None,
                     what=f"Collect the {money(owed)} balance first", collect=True, deliver=None,
                     detail=a.get("detail") or "Your terms hold the final photos until the balance is paid. "
                                                "Record the payment, then choose how to deliver.")
        elif a.get("verb") == "Add time and place" and (a.get("href") or "").endswith("#shoot"):
            a.update(form="date_set", sid=s["id"], prefill=True)
        if not a.get("theirs"):
            a["step_words"] = a.get("what")
            a["what"] = object_title(a.get("what"), button_words(a))
        out.append(a)

    t_ = today().isoformat()
    due_ = lambda a: a.get("notice") and a.get("when") and a["when"] <= t_
    return sorted(out, key=lambda a: (0 if a.get("retouch") else 1, a.get("seq") or (
        (0, 3) if a.get("primary") else (1, 99, 0) if due_(a) else (3, 0, 0))))

def focus_place(s):

    intake_ = json.loads(s["intake"] or "{}")
    place = next((p_.strip() for p_ in ((s["stops"] or "").splitlines() + [intake_.get("venue") or "", intake_.get("address") or ""])
                  if p_.strip()), "")
    return place.splitlines()[0] if place else ""

def focus_extra(sr, full, t):

    first = q("""SELECT summary, at, channel FROM log WHERE session_id=? ORDER BY at, id LIMIT 1""", (sr["id"],), one=True)
    intake_ = json.loads(sr["intake"] or "{}")
    place = next((p_.strip() for p_ in ((full["stops"] or "").splitlines() + [intake_.get("venue") or "", intake_.get("address") or ""])
                  if p_.strip()), "")
    now_ = datetime.now(TZ)
    passed = False
    if sr["shoot_date"]:
        sd_ = sr["shoot_date"][:10]
        passed = sd_ < now_.date().isoformat() or (sd_ == now_.date().isoformat() and bool(sr["start_time"])
                                                   and sr["start_time"][:5] <= now_.strftime("%H:%M"))
    pays = q("SELECT kind, amount, received, method, note FROM payments WHERE session_id=? AND voided IS NULL ORDER BY received, id",
             (sr["id"],))
    return dict(message=message_view(first, sr), place=place.splitlines()[0] if place else "", shoot_passed=passed, payments=pays)

_WEBSITE_MSG = re.compile(r"Inquiry from your website(?: \([^)]*\))?(?: from (?P<who>[^,:\n]+?))?"
                          r"(?:, preferred date (?P<date>\d{4}-\d{2}-\d{2}))?(?::\n?(?P<body>.*))?", re.S)
_CUT_NOTE = re.compile(r"\n?\(The message was longer; only its first \d+ characters were kept\.\)\s*$")
MESSAGE_HOW = {"email": "by email", "call": "by phone", "text": "by text", "in person": "in person", "note": "noted by you"}

def message_view(row, sr=None):

    if not row:
        return None
    text = row["summary"] or ""
    m = _WEBSITE_MSG.fullmatch(text.strip())
    cut = bool(_CUT_NOTE.search(text))
    pref = None
    if m:
        how, body, pref = "from your website", (m.group("body") or ""), m.group("date")
    else:
        how, body = MESSAGE_HOW.get(row["channel"] or "", row["channel"] or ""), text
        if sr is not None and body.strip() == f"Inquiry: {C.SESSION_TYPES.get(sr['type'], '')}":
            body = ""
    body = _CUT_NOTE.sub("", body).strip()
    at = row["at"] or ""
    when = nice(at[:10]) + (f", {clock(at[11:16])}" if at[11:16] and at[11:16] != "00:00" else "")
    try:
        pref_words = d(pref).strftime("%a %b %-d, %Y") if pref else ""
    except ValueError:
        pref_words = pref or ""
    who = (m.group("who") or "").strip() if m else ""
    return dict(how=how, when=when, preferred=pref_words, body=body, cut=cut, at=at, summary=text, who=who)

@app.get("/dev/components")
def dev_components():

    if os.environ.get("CRM_DEV") != "1":
        abort(404)
    return render_template("dev_components.html")

@app.post("/dev/components/try")
def dev_components_post():

    if os.environ.get("CRM_DEV") != "1":
        abort(404)
    what = request.form.get("what")
    if what == "error":
        return "Not saved: a sample error, as the app shows any failure.", 400
    if what == "refuse":
        session["notice"] = "Not saved: a sample refusal, shown beside the button."
        g.refused = True
    return redirect(url_for("dev_components"))

@app.get("/c/<int:hid>")
def client_stage(hid):
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    people = q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (hid,))
    sess = q("SELECT * FROM sessions WHERE household_id=? ORDER BY stage IN ('closed','lost'), COALESCE(shoot_date,'9999')", (hid,))
    if not sess:
        return redirect(url_for("client_view", hid=hid))
    sid = g.get("stage_sid") or request.args.get("s", type=int) or sess[0]["id"]
    sr = next((x_ for x_ in sess if x_["id"] == sid), sess[0])
    mark_seen(sr["id"])
    if sr["stage"] in ("shot", "delivered"):
        settle_backup(sr["id"])
    acts = build_actions(only_sid=sr["id"])
    link = url_for("session_view", sid=sr["id"])
    my = [a for a in acts if a["who"]["link"] == link and not a.get("pencil_lapse")]
    w = workflow(sr["id"])
    full = session_full(sr["id"])
    panels = [Markup(x_) for x_ in (fn(full) for fn in hooks.session_panels) if x_]
    tabs = [(lab, cnt, Markup(html)) for lab, cnt, html in (t_ for t_ in (fn(full) for fn in hooks.session_tabs) if t_)]
    blocks = [(t_, Markup(h_)) for fn in hooks.stage_blocks for t_, h_ in (fn(full) or [])]
    can_move = bool(w["next_stage"]) and (w["next_stage"] == "closed" or can_enter(w["next_stage"], sr["shoot_date"], sr["id"]))
    t_ = totals(sr["id"])
    tiers_ = {k_: v_[FOCUS_STAGES.index(focus_stage(sr))] for k_, v_ in FOCUS_TIERS.items()}
    if not tiers_["gallery"] and any(t_b == "Gallery" for t_b, _h in blocks):
        tiers_["gallery"] = 1
    return render_template("client_stage.html", h=h, people=people, sess=sess, s=full, acts=focus_rows(my, sr, t_), wf=w, from_client=client_online(hid), days_in=days_in_stage(sr), blocks=blocks,
                           fst=focus_stage(sr), tiers=tiers_,
                           block_key=FOCUS_BLOCK_KEY, x_=focus_extra(sr, full, t_),
                           can_shot=sr["stage"] == "booked" and can_enter("shot", sr["shoot_date"], sr["id"]),
                           photos=photo_summary(sr["id"]) if sr["stage"] in ("booked", "shot", "delivered", "closed") else None,
                           versions=q("SELECT id, at, number FROM quote_versions WHERE session_id=? ORDER BY id DESC", (sr["id"],)),
                           quote_same=_quote_unchanged(sr["id"]) if sr["stage"] in ("inquiry", "quoted") or replacing(sr["id"]) else None,
                           replacing=replacing(sr["id"]), q_locked=quote_locked(sr["id"]), canceled_vids={r_["old_vid"] for r_ in q(
                               "SELECT old_vid FROM quote_replacements WHERE session_id=? AND old_vid IS NOT NULL", (sr["id"],))},
                           clash=clashes(sr["id"]), minutes=session_minutes(sr["id"], sr["type"]),
                           kept_tax=kept_deposit_tax(sr["id"]), **_tax_view(sr["id"]),
                           can_move=can_move, now_iso=datetime.now(TZ).strftime("%Y-%m-%dT%H:%M"),
                           j=journey(sr, my), t=totals(sr["id"]), intake=json.loads(sr["intake"] or "{}"),
                           panels=panels, tabs=tabs, light_=light(sr["shoot_date"], sr["type"], shoot_place(full)), expires=gallery_expires(sr),
                           contact=household_contact(people),
                           lines=q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sr["id"],)),
                           quote=quote_of(sr["id"]), fee_sched=(quote_of(sr["id"]) or {}).get("schedule") or current_schedule(),
                           history=client_history(hid), moved=reschedules(sr["id"]),
                           inst=installment_schedule(sr["id"]), inst_n=installments_of(sr["id"]),
                           inst_type=C.INSTALLMENTS.get(sr["type"], 0), resched_open=resched_fees_open(sr["id"]),
                           credits=credits_for(sr["id"]), own_credit=credit_of(sr["id"]),
                           before_lost=stage_before_lost(sr["id"]) if sr["stage"] == "lost" else None,
                           back=url_for("client_stage", hid=hid, s=sr["id"]))

def stage_before_lost(sid):

    r_ = q("SELECT stage FROM stage_log WHERE session_id=? AND stage!='lost' ORDER BY id DESC LIMIT 1", (sid,), one=True)
    return r_["stage"] if r_ else None

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
            releases_follow(sid=srow["id"])
            schedule_tasks(srow["id"], fair=True)
    schedule_event(eid, fair=True)
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
                time=r["start_time"], light=light(r["shoot_date"], r["type"], shoot_place(r)),
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

    import calendar as _cal
    t = today()
    try:
        y = int(request.args.get("y") or t.year)
    except ValueError:
        y = t.year
    booked = {}
    for r in q("""SELECT s.shoot_date d, COALESCE(e.name, h.name) n, s.stage, s.shoot_date, s.pencil_until FROM sessions s
                  JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
                  WHERE substr(s.shoot_date,1,4)=? AND s.stage NOT IN ('lost') GROUP BY s.shoot_date, COALESCE(s.event_id, -s.id)""",
               (str(y),)):
        booked.setdefault(r["d"], []).append(r["n"] + (" (penciled)" if penciled(r) else ""))
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
                           weekend=dd.weekday() >= 5, shoots=[], due=[], long=dd.strftime("%A, %B %-d")) for dd in wk])
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
                                                      done=r["stage"] in ("shot", "delivered", "closed"),
                                                      penciled=penciled(r)))
    for a in acts:
        w_ = a.get("when")
        if w_ and w_ in by:
            by[w_]["due"].append(a)
    prev_ = (first - timedelta(days=1)).strftime("%Y-%m")
    next_ = (first + timedelta(days=32)).strftime("%Y-%m")
    day_ = request.args.get("d") or ""
    day_ = day_ if day_ in by else ""
    return render_template("month.html", weeks=weeks, title=first.strftime("%B %Y"), prev_m=prev_, next_m=next_,
                           this_m=t.strftime("%Y-%m"), is_this=first.strftime("%Y-%m") == t.strftime("%Y-%m"),
                           ym=first.strftime("%Y-%m"), day=day_)

def needs_you_shoots():
    return needs_you()["shoots"]

SESSION_FILTERS = None

def _check_filter(where):

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
    found = q("""SELECT s.*, h.name AS household, e.name AS event_name
        FROM sessions s JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
        WHERE """ + _check_filter(where) + """ ORDER BY COALESCE(s.shoot_date,'9999'), s.id""", args)
    prefetch_workflows(r["id"] for r in found)
    for r in found:
        done_ = r["stage"] in ("closed", "lost")
        rows.append(dict(s=r, t=totals(r["id"]), wf=workflow_now(r["id"]) if done_ else workflow(r["id"])))
    return rows

VIEWS = {
    "open": ("Open", "s.stage NOT IN ('closed','lost')"),
    "upcoming": ("Upcoming shoots", "s.shoot_date >= date('now','localtime') AND s.stage NOT IN ('lost')"),
    "leads": ("Leads", "s.stage IN ('inquiry','quoted')"),
    "post": ("Post-production", "s.stage IN ('shot','delivered')"),
    "all": ("All", "1=1"),
}

def soonest_first(rows):

    t_ = today().isoformat()
    day_ = lambda r_: (r_["s"]["shoot_date"] or "")[:10]
    return ([r_ for r_ in rows if day_(r_) >= t_] + [r_ for r_ in rows if not day_(r_)]
            + [r_ for r_ in rows if day_(r_) and day_(r_) < t_][::-1])

def session_views(s):

    stage_, day_, t_ = s["stage"], (s["shoot_date"] or "")[:10], today().isoformat()
    return [k_ for k_, ok_ in (("open", stage_ not in ("closed", "lost")), ("upcoming", bool(day_) and day_ >= t_ and stage_ != "lost"),
                               ("leads", stage_ in ("inquiry", "quoted")), ("post", stage_ in ("shot", "delivered"))) if ok_]

@app.get("/sessions")
def sessions():

    view = request.args.get("view", "open")
    if view not in VIEWS:
        view = "open"
    if request.args.get("owing"):
        view = "all"
    where = "1=1"
    args = []
    if request.args.get("type"):
        where += " AND s.type=?"; args.append(request.args["type"])

    q_words = (request.args.get("q") or "").split()
    label_q = [w_[6:].lower() for w_ in q_words if w_.lower().startswith("label:")]
    text_q = " ".join(w_ for w_ in q_words if not w_.lower().startswith("label:"))
    if text_q:
        where += " AND (h.name LIKE ? OR s.title LIKE ? OR e.name LIKE ?)"; args += [f"%{text_q}%"] * 3
    rows = session_rows(where, args)
    if request.args.get("owing"):
        rows = [r for r in rows if owed_billed(r["s"], r["t"])]
    rows = soonest_first(rows)
    labels_ = client_labels()
    nexts_ = session_nexts([r["s"] for r in rows])
    for r in rows:
        r["next"] = nexts_.get(r["s"]["id"])
        s_, nx_ = r["s"], r["wf"]["next_step"]
        open_ = s_["stage"] not in ("closed", "lost")
        theirs_ = bool(nx_) and nx_["label"] in THEIR_STEPS
        r["f"] = dict(status=("waiting" if theirs_ or not (nx_ or r["wf"]["next_stage"]) else "mine") if open_ else "",
                      due=(nx_ or {}).get("due") or "" if open_ else "",
                      owing=owed_billed(s_, r["t"]),
                      labels=labels_.get(s_["household_id"], []),
                      show=session_views(s_))
        r["in_view"] = view == "all" or view in r["f"]["show"]
    if label_q:
        rows = [r for r in rows if all(any(lq_ in l_.lower() for l_ in r["f"]["labels"]) for lq_ in label_q)]
    return render_template("sessions.html", rows=rows, view=view, views=VIEWS, today_iso=today().isoformat())

def _search_matches(hid, term):

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
        return render_template("search.html", term=term, hits=hits, docs=[], extra=search_history(""))
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
    docs = []
    if term:
        known = {x_["h"]["id"]: x_ for x_ in hits if x_["kind"] == "client"}
        more = {}

        def doc_hit(hid_, sid_, text_):
            h_ = q("SELECT name FROM households WHERE id=?", (hid_,), one=True)
            if h_ and not any(d_["text"] == text_ for d_ in docs):
                docs.append(dict(text=text_, sub=h_["name"], link=stage_url(sid_), hid=hid_))

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
        for r_ in q("""SELECT s.household_id, s.id AS sid, d.number FROM doc_numbers d JOIN sessions s ON s.id=d.session_id WHERE d.number LIKE ?""", (like,)):
            doc_hit(r_["household_id"], r_["sid"], ("Receipt " if r_["number"].startswith("R-") else "Invoice ") + r_["number"])
        if term.isdigit():

            for r_ in q("""SELECT s.household_id, s.id AS sid, v.id, v.number FROM quote_versions v JOIN sessions s ON s.id=v.session_id
                           WHERE v.number=? OR (v.number IS NULL AND v.id=?)""", (term, int(term))):
                doc_hit(r_["household_id"], r_["sid"], f"Quote {r_['number'] or r_['id']}")
        for r_ in q("""SELECT s.household_id, s.id AS sid, p.ref, p.amount FROM payments p JOIN sessions s ON s.id=p.session_id WHERE p.ref LIKE ?""", (like,)):
            doc_hit(r_["household_id"], r_["sid"], f"Check #{r_['ref']}, {money(r_['amount'])}")
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
    found_settings = settings_index.find(term, C.SESSION_TYPES, url_for, settings_index.help_words())
    for fn in hooks.search:
        try:
            extra += [sec for sec in (fn(term) or []) if sec.get("rows")]
        except Exception:
            app.logger.exception("plugin search")
    only_ = not extra and not found_settings
    if only_ and len(hits) == 1 and hits[0]["kind"] == "client" and len(hits[0]["sessions"]) == 1 and all(
            d_["hid"] == hits[0]["h"]["id"] for d_ in docs):
        return redirect(stage_url(hits[0]["sessions"][0]["id"]))
    if only_ and len(hits) == 1 and hits[0]["kind"] == "event" and not docs:
        return redirect(url_for("event_view", eid=hits[0]["e"]["id"]))
    if only_ and not hits and len(docs) == 1:
        return redirect(docs[0]["link"])

    if found_settings:
        extra.insert(0, dict(title="Settings", rows=found_settings))
    extra += search_history(term)
    return render_template("search.html", term=term, hits=hits, docs=docs, extra=extra)

def search_history(term):

    if not term:
        return [dict(title="History", rows=[dict(text="Studio history", sub="Every change recorded, all clients",
                                                  link=url_for("studio_history_view"))])]
    rows_, more_ = studio_history(term=term)
    if not rows_:
        return []
    n_ = f"{len(rows_)}+" if more_ else str(len(rows_))
    return [dict(title="History", rows=[dict(text=f"{n_} History line{'' if n_ == '1' else 's'} with \u201c{term}\u201d",
                                             sub="Studio history, all clients", link=url_for("studio_history_view", q=term))])]

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
    reopen_ = old_ in C.OFF_PIPELINE and stage in C.STAGES
    if stage == "lost" and old_ != "lost" and request.form.get("need_reason"):

        why_, words_ = request.form.get("lost_reason") or "", (request.form.get("lost_note") or "").strip()
        ok_ = C.NOT_BOOKED_REASONS if old_ in ("inquiry", "quoted") else [k_ for k_, _l in C.CANCEL_REASONS]
        if why_ not in ok_ or (why_ == "Other" and not words_):
            session["notice"] = ("Say why in a few words." if why_ == "Other" else
                                 "Pick why first." if old_ in ("inquiry", "quoted") else "Pick why the booking is canceled first.")
            g.refused = True
            return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid))
    if stage == "lost" and old_ != "lost" and request.form.get("lost_reason") in C.LOST_REASONS + C.NOT_BOOKED_REASONS:

        x("UPDATE sessions SET lost_reason=? WHERE id=?", (request.form["lost_reason"], sid))
    if _forward(old_, stage):
        move_on(sid, stage, "shoot done" if stage == "shot" else "moved by hand")
    else:
        set_stage(sid, stage)
    x("UPDATE sessions SET hold_stage=? WHERE id=?", (stage if back_ or reopen_ else None, sid))
    if reopen_:
        drop_credit(sid, "the booking's deposit again (reopened)")
    elif stage == "lost" and old_ not in ("lost", "inquiry", "quoted") and request.form.get("deposit") == "credit":
        make_credit(sid)
    if back_ or reopen_:
        old_date_ = (q("SELECT shoot_date FROM sessions WHERE id=?", (sid,), one=True) or {"shoot_date": None})["shoot_date"]
        session["notice"] = ((f"Reopened at {C.STAGE_LABELS[stage]}. " if reopen_ else "") +
                             f"Stays at {C.STAGE_LABELS[stage]} until you move it on, or reopen one of its steps"
                             " (it then moves on by itself once they are done again)." +
                             (f" The old shoot date, {nice(old_date_)}, has passed: set the new one."
                              if reopen_ and old_date_ and old_date_ < today().isoformat() else ""))
    if stage == "lost" and request.form.get("lost_reason") in C.LOST_REASONS + C.NOT_BOOKED_REASONS:
        why_ = request.form["lost_reason"]
        if (q("SELECT lost_reason FROM sessions WHERE id=?", (sid,), one=True)["lost_reason"] or "") != why_:
            x("UPDATE sessions SET lost_reason=? WHERE id=?", (why_, sid))

            money_changed(sid, "reason")
    if request.headers.get("X-Fetch"):
        return ("", 204)
    return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=sid))

ROLODEX_COLS = [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("stand", "Where they stand"), ("owed", "Owes"),
                ("toinv", "To invoice")]

def _digits(t):
    return re.sub(r"\D", "", t or "")

def rolodex_match(row, term):

    text_ = " ".join([row["name"], row["phone"], row["email"], row["stand"], row["owes"], row.get("to_invoice", ""),
                      row["find"]]).lower()
    for w_ in (term or "").lower().split():
        if w_ in text_:
            continue
        d_ = _digits(w_)
        if d_ and not re.search(r"[a-z]", w_) and d_ in _digits(row["phone"]):
            continue
        return False
    return True

def rolodex_why(row, term):

    name_, out = row["name"].lower(), []
    for w_ in (term or "").lower().split():
        if w_ in name_:
            continue
        d_ = _digits(w_) if not re.search(r"[a-z]", w_) else ""
        for lab_, v_ in row["why_src"]:
            if w_ in v_.lower() or (d_ and lab_ == "Phone" and d_ in _digits(v_)):
                line_ = f"{lab_}: {v_}"
                if line_ not in out:
                    out.append(line_)
                break
    return out

def where_they_stand(sessions_, t):

    open_ = [s_ for s_ in sessions_ if s_["stage"] not in ("closed", "lost")]
    if open_:
        ahead_ = sorted((s_ for s_ in open_ if (s_["shoot_date"] or "")[:10] >= t), key=lambda s_: s_["shoot_date"])
        rest_ = sorted((s_ for s_ in open_ if s_ not in ahead_), key=lambda s_: (s_["shoot_date"] or "9999", s_["id"]))
        s_ = (ahead_ or rest_)[0]
        when_ = (s_["shoot_date"] or "")[:10]
        text_ = C.STAGE_LABELS.get(s_["stage"], s_["stage"]) + (f" \u00b7 shoot {nice(when_)}" if when_ else "") \
            + (f" (+{len(open_) - 1} more)" if len(open_) > 1 else "")
        return text_, f"{C.STAGES.index(s_['stage']) if s_['stage'] in C.STAGES else 0}|{when_ or '9999'}"
    closed_ = sorted((s_["shoot_date"] or "")[:10] for s_ in sessions_ if s_["stage"] == "closed")
    if closed_:
        return "Closed" + (f" \u00b7 last session {nice(closed_[-1])}" if closed_[-1] else ""), f"6|{closed_[-1]}"
    if sessions_:
        return "Not booked", "7|"
    return "No sessions yet", "8|"

@app.get("/clients")
def clients():

    term = request.args.get("q", "").strip()
    sort = request.args.get("sort", "name")
    sort = sort if sort in dict(ROLODEX_COLS) else "name"
    desc = request.args.get("dir") == "desc"
    t = today().isoformat()
    labels = client_labels()
    owed, late, toinv = {}, set(), {}
    for s_ in q("SELECT * FROM sessions WHERE stage NOT IN ('lost') AND event_id IS NULL"):
        t_ = totals(s_["id"])
        if not owed_now(s_["stage"], t_, s_["shoot_date"], row_type(s_)):
            continue
        h_ = s_["household_id"]
        if invoice_went(s_["id"]):
            owed[h_] = owed.get(h_, 0.0) + t_["due"]
            if overdue(s_, t_)["amount"] > 0.005:
                late.add(h_)
        else:
            toinv[h_] = toinv.get(h_, 0.0) + t_["due"]
    by_h, types_ = {}, {}
    for s_ in q("SELECT id, household_id, stage, shoot_date, type FROM sessions"):
        types_.setdefault(s_["household_id"], set()).add(s_["type"])
        by_h.setdefault(s_["household_id"], []).append(s_)

    def business_(h):
        if h["business"] is not None:
            return bool(h["business"])
        return bool((h["company"] or "").strip()) or any(
            C.TYPE_KIND.get(t_) == "property" or t_ in C.NET_TERMS_TYPES for t_ in types_.get(h["id"], ()))
    prefetch_faces()
    people_ = {}
    for p_ in q("SELECT household_id, name, role, email, phone FROM people ORDER BY role='parent' DESC, role='adult' DESC, id"):
        people_.setdefault(p_["household_id"], []).append(p_)
    out = []
    for h in q("SELECT * FROM households ORDER BY name"):
        ppl = people_.get(h["id"], [])
        phone_ = next(((p_["phone"] or "").strip() for p_ in ppl if (p_["phone"] or "").strip()), "")
        email_ = next(((p_["email"] or "").strip() for p_ in ppl if (p_["email"] or "").strip()), "")
        stand_, stand_key = where_they_stand(by_h.get(h["id"], []), t)
        due_ = round(owed.get(h["id"], 0.0), 2)
        ti_ = round(toinv.get(h["id"], 0.0), 2)
        out.append(dict(hid=h["id"], name=h["name"], phone=phone_, email=email_, stand=stand_, owed=due_,
                        owes=money(due_) if due_ > 0.005 else "", late=due_ > 0.005 and h["id"] in late,
                        to_invoice=money(ti_) if ti_ > 0.005 else "",
                        find=" ".join(labels.get(h["id"], []) + [p_["name"] or "" for p_ in ppl]),
                        why_src=[("Person", p_["name"]) for p_ in ppl if p_["name"]] + [("Phone", phone_), ("Email", email_)]
                        + [("Label", l_) for l_ in labels.get(h["id"], [])]
                        + [("Where they stand", stand_), ("Owes", money(due_) if due_ > 0.005 else ""),
                           ("To invoice", money(ti_) if ti_ > 0.005 else "")],
                        sort=dict(name=client_sort_name(h["name"], h["company"] if "company" in h.keys() else "",
                                                        [p_["name"] or "" for p_ in ppl], business_(h)),
                                  phone=_digits(phone_), email=email_.lower(), stand=stand_key, owed="%.2f" % due_,
                                  toinv="%.2f" % ti_)))
    shown = [r_ for r_ in out if rolodex_match(r_, term)] if term else out
    for r_ in out:
        r_["text"] = " ".join([r_["name"], r_["phone"], r_["email"], r_["stand"], r_["owes"], r_["to_invoice"], r_["find"]]).lower()
        r_["why"] = rolodex_why(r_, term) if term else []
    num_ = sort in ("owed", "toinv")
    shown.sort(key=lambda r_: r_["sort"]["name"])
    shown.sort(key=lambda r_: float(r_["sort"][sort]) if num_ else r_["sort"][sort], reverse=desc)
    if not num_:
        shown.sort(key=lambda r_: not r_["sort"][sort])
    return render_template("clients.html", rows=shown, n_all=len(out), term=term, sort=sort, desc=desc, cols=ROLODEX_COLS,
                           today_iso=t, add_name=term if re.search(r"[^\d\s()+.\-]", term) else "")

CHECK_IN_MONTHS = 10
CHECK_IN_SNOOZE = 6

def check_in_due():

    t = today()
    cut = months_ago(t, CHECK_IN_MONTHS).isoformat()
    snooze_cut = months_ago(t, check_in_snooze()).isoformat()
    ended = ended_contact()
    out = set()
    for h in q("""SELECT h.id,
            (SELECT COUNT(*) FROM sessions WHERE household_id=h.id AND stage NOT IN ('closed','lost')) AS n_open,
            (SELECT MAX(shoot_date) FROM sessions WHERE household_id=h.id AND stage != 'lost') AS last_session,
            (SELECT MAX(shoot_date) FROM sessions WHERE household_id=h.id AND stage != 'lost' AND shoot_date <= ?) AS last_shot,
            (SELECT MAX(at) FROM log WHERE household_id=h.id AND summary='Checked in by email') AS last_check_in
            FROM households h""", (t.isoformat(),)):
        if (h["id"] not in ended and not h["n_open"] and not (h["last_session"] or "") > t.isoformat() and h["last_shot"]
                and h["last_shot"] <= cut and (h["last_check_in"] or "")[:10] <= snooze_cut):
            out.add(h["id"])
    return out

def check_in_snooze():

    v_ = setting("check_in_snooze_months")
    return int(v_) if v_.isdigit() and 1 <= int(v_) <= 36 else CHECK_IN_SNOOZE

def idle_hours():

    try:
        v_ = setting("idle_signout_hours")
    except Exception:
        return IDLE_HOURS
    return int(v_) if v_.isdigit() and 1 <= int(v_) <= 72 else IDLE_HOURS

IDLE_HOURS = 8

def client_sort_name(name, company="", members=None, business=False):

    n_ = " ".join((name or "").split())
    if company and n_.lower() == " ".join(company.split()).lower():
        return n_.lower()
    if business and members is not None and n_.lower() not in {" ".join(m_.split()).lower() for m_ in members}:
        return re.sub(r"^the\s+", "", n_, flags=re.I).lower()
    core = re.sub(r"^(the)\s+", "", n_, flags=re.I)
    core = re.sub(r"\s+(family|household|wedding|and family)$", "", core, flags=re.I)
    core = re.split(r"\s+(?:and|&)\s+", core)[-1]
    last = (core.split() or [""])[-1].strip(".,")
    return f"{last} {n_}".lower()

def label_clean(text):

    t_ = " ".join((text or "").split())[:40].strip()
    if not t_:
        return ""
    have = q("SELECT label FROM client_labels WHERE lower(label)=lower(?) LIMIT 1", (t_,), one=True)
    return have["label"] if have else t_

def client_labels(hid=None):

    if hid is not None:
        return [r_["label"] for r_ in q("SELECT label FROM client_labels WHERE household_id=? ORDER BY lower(label)", (hid,))]
    out = {}
    for r_ in q("SELECT household_id, label FROM client_labels ORDER BY lower(label)"):
        out.setdefault(r_["household_id"], []).append(r_["label"])
    return out

def ended_contact():

    return {r_["household_id"]: r_["since"] for r_ in q("SELECT household_id, since FROM client_end_contact")}

app.jinja_env.globals["client_labels"] = client_labels
app.jinja_env.globals["ended_contact"] = ended_contact
app.jinja_env.globals["all_labels"] = lambda: [r_["label"] for r_ in q("SELECT DISTINCT label FROM client_labels ORDER BY lower(label)")]
app.jinja_env.globals["check_in_draft"] = lambda hid: check_in_draft(hid)

def months_ago(d, n):
    import calendar as _cal
    y_, m_ = divmod(d.year * 12 + d.month - 1 - n, 12)
    return date(y_, m_ + 1, min(d.day, _cal.monthrange(y_, m_ + 1)[1]))

def check_in_draft(hid):

    from urllib.parse import quote as _qu
    who = q("""SELECT name, email FROM people WHERE household_id=? AND role IN ('parent','adult') AND COALESCE(email,'') != ''
               ORDER BY """ + BOOKER_FIRST + """, id LIMIT 1""", (hid,), one=True)
    if not who:
        return ""
    last = q("""SELECT type FROM sessions WHERE household_id=? AND shoot_date IS NOT NULL AND stage != 'lost'
                ORDER BY shoot_date DESC LIMIT 1""", (hid,), one=True)
    first = (who["name"].split() or [""])[0]
    sign = setting("photographer_name") or setting("business_name")
    what_ = C.doc_label(last["type"]).lower() if last else "session"
    if not re.search(r"\b(session|shoot|day|wedding|event)s?$", what_):
        what_ += " session"
    body = "\n\n".join([f"Hi {first}," if first else "Hello,",
                         f"It has been about a year since your {what_}, and I was thinking of you. If you would "
                         "like to plan another, I would be glad to find a date that works for you.",
                         f"Thank you,\n{sign}" if sign else "Thank you"])
    return f"mailto:{_qu(who['email'], safe='@')}?subject={_qu('Checking in')}&body={_qu(body)}"

def bulk_mail_links(hids, size=40):

    from urllib.parse import quote as _qu
    ended, seen, addrs = ended_contact(), set(), []
    for hid in hids:
        if hid in ended:
            continue
        for r_ in q("""SELECT email FROM people WHERE household_id=? AND role IN ('parent','adult') AND COALESCE(email,'') != ''
                       ORDER BY """ + BOOKER_FIRST + """, id LIMIT 1""", (hid,)):
            e_ = r_["email"].strip()
            if e_ and "@" in e_ and e_.lower() not in seen:
                seen.add(e_.lower())
                addrs.append(e_)
    own = (setting("business_email") or "").strip()
    groups = [addrs[i:i + size] for i in range(0, len(addrs), size)]
    out = []
    for i, grp in enumerate(groups):
        lab = (f"Email all {len(addrs)} clients" if len(groups) == 1 else f"Email clients {i * size + 1} to {i * size + len(grp)}")
        out.append((lab, f"mailto:{_qu(own, safe='@')}?bcc={_qu(','.join(grp), safe='@,')}", len(grp)))
    return out

@app.post("/client/<int:hid>/labels")
def client_label(hid):

    q("SELECT 1 FROM households WHERE id=?", (hid,), one=True) or abort(404)
    if request.form.get("remove"):
        x("DELETE FROM client_labels WHERE household_id=? AND label=?", (hid, request.form["remove"]))
        session["notice"] = f'Took the label "{request.form["remove"]}" off.'
    else:
        lab_ = label_clean(request.form.get("label"))
        if lab_:
            x("INSERT OR IGNORE INTO client_labels (household_id, label) VALUES (?,?)", (hid, lab_))
            session["notice"] = f'Labeled "{lab_}".'
    return redirect(safe_back(request.form.get("back")) or url_for("client_view", hid=hid) + "#labels")

@app.post("/settings/labels")
def settings_labels():

    old = request.form.get("label", "")
    back_ = url_for("settings_view", tab="clients") + "#labels"
    n_ = q("SELECT COUNT(*) n FROM client_labels WHERE label=?", (old,), one=True)["n"]
    if not n_:
        session["notice"] = "No client carries that label now. Nothing was changed."
        g.refused = True
        return redirect(back_)
    if request.form.get("act") == "merge":
        into = request.form.get("into", "")
        if not q("SELECT 1 FROM client_labels WHERE label=? LIMIT 1", (into,), one=True) or into == old:
            session["notice"] = "Pick another label to merge it into. Nothing was changed."
            g.refused = True
            return redirect(back_)
    else:
        typed = " ".join((request.form.get("new") or "").split())[:40].strip()
        if not typed:
            session["notice"] = "Type the label's new name. Nothing was changed."
            g.refused = True
            return redirect(back_)

        into = typed if typed.lower() == old.lower() else label_clean(typed)
    if into == old:
        g.refused = True
        return redirect(back_)
    joins = bool(q("SELECT 1 FROM client_labels WHERE label=? LIMIT 1", (into,), one=True))
    x("INSERT OR IGNORE INTO client_labels (household_id, label) SELECT household_id, ? FROM client_labels WHERE label=?",
      (into, old))
    x("DELETE FROM client_labels WHERE label=?", (old,))
    who = f"{n_} client{'' if n_ == 1 else 's'}"
    g.audit_note = f'"{old}" to "{into}", {who}'
    session["notice"] = (f'Merged "{old}" into "{into}" on {who}.' if joins else f'Renamed "{old}" to "{into}" on {who}.')
    return redirect(back_)

app.config.setdefault("ep_words", {})["settings_labels"] = lambda f: (
    "Merged a client label" if f.get("act") == "merge" else "Renamed a client label")

@app.post("/client/<int:hid>/end-contact")
def client_end_contact(hid):

    q("SELECT 1 FROM households WHERE id=?", (hid,), one=True) or abort(404)
    if request.form.get("end") == "0":
        x("DELETE FROM client_end_contact WHERE household_id=?", (hid,))
        session["notice"] = "Back in mailings and check-ins."
    else:
        x("INSERT OR IGNORE INTO client_end_contact (household_id, since) VALUES (?,?)", (hid, today().isoformat()))
        session["notice"] = "Ended contact: left out of mailings and check-ins."
    return redirect(safe_back(request.form.get("back")) or url_for("client_view", hid=hid) + "#labels")

@app.post("/client/<int:hid>/check-in")
def client_check_in(hid):

    q("SELECT 1 FROM households WHERE id=?", (hid,), one=True) or abort(404)
    x("INSERT INTO log (household_id, channel, summary, at) VALUES (?,?,?,?)",
      (hid, "email", "Checked in by email", now_local()))
    mail_ = check_in_draft(hid)
    if request.form.get("mail") and mail_:
        return redirect(mail_, code=303)
    return redirect(safe_back(request.form.get("back")) or url_for("clients"))

def client_check(hid):

    sids = [r_["id"] for r_ in q("SELECT id FROM sessions WHERE household_id=? ORDER BY id", (hid,))]
    got, failed = [], False
    for fn in hooks.client_checks:
        try:
            r_ = fn(sids) or {}
            got += [p_ for p_ in r_.get("got") or [] if p_]
            failed = failed or bool(r_.get("failed"))
        except Exception:
            app.logger.exception("Check now: %s", getattr(fn, "__name__", "collector"))
            failed = True
    said = ("Checked: " + "; ".join(got) + ".") if got else "Checked: nothing new from the client."
    if failed:
        said += " The web host could not be reached for part of it, so the app tries again at its next regular check."
    return said

def client_online(hid):

    sids = [r_["id"] for r_ in q("SELECT id FROM sessions WHERE household_id=?", (hid,))]
    n, ats = 0, []
    for fn in hooks.client_online:
        try:
            r_ = fn(sids) or {}
        except Exception:
            app.logger.exception("From the client: %s", getattr(fn, "__name__", "plugin"))
            continue
        n += r_.get("n") or 0
        if r_.get("at"):
            ats.append(r_["at"])
    if not n:
        return None
    import time as _t
    import jobs
    j_ = jobs.JOBS.get(client_check_key(hid)) or {}
    said = (j_.get("message") or "") if j_.get("state") in ("done", "error") and _t.time() - (j_.get("ended") or 0) < 60 else ""
    if said.startswith("Checked: "):
        said = "checked just now: " + said[len("Checked: "):]
    return dict(checked=int((_t.time() - max(ats)) // 60) if ats else None, checking=j_.get("state") == "running",
                said=said, key=client_check_key(hid))

def client_check_key(hid):
    return f"client-check:{int(hid)}"

@app.post("/client/<int:hid>/check", endpoint="client_check_now")
def client_check_now(hid):

    q("SELECT 1 FROM households WHERE id=?", (hid,), one=True) or abort(404)
    import jobs
    back = safe_back(request.form.get("back")) or url_for("client_view", hid=hid)
    j_ = jobs.start(app, client_check_key(hid), "Checking for anything new from the client", lambda job: client_check(hid),
                    link=back)
    session["notice"] = (j_.get("message") if j_["state"] != "running" else
                         "Checking now. What came in shows on the From the client line when it is done."
                         if urlparse_path(back).startswith("/c/") else "Checking now. This page updates when it is done.")
    return redirect(back)

import client_csv as CC

_IMPORT_DRAFTS = {}
_IMPORT_TTL = 3600
_IMPORT_LOCK = __import__("threading").Lock()

def _import_draft(token):
    import time
    with _IMPORT_LOCK:
        for k_ in [k_ for k_, d_ in _IMPORT_DRAFTS.items() if time.time() - d_["at"] > _IMPORT_TTL]:
            _IMPORT_DRAFTS.pop(k_, None)
        return _IMPORT_DRAFTS.get(token or "")

def _import_batches():
    try:
        v_ = json.loads(setting("client_imports") or "[]")
    except ValueError:
        v_ = []
    return v_ if isinstance(v_, list) else []

def _save_import_batches(items):
    x("INSERT INTO settings (key, value) VALUES ('client_imports',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (json.dumps(items[-20:]),))

def _existing_index():

    em, ph = {}, {}
    for p_ in q("SELECT household_id, email, phone FROM people"):
        e_ = (p_["email"] or "").strip().lower()
        if e_:
            em.setdefault(e_, set()).add(p_["household_id"])
        k_ = CC.phone_key(p_["phone"])
        if k_:
            ph.setdefault(k_, set()).add(p_["household_id"])
    return em, ph, {r_["id"]: r_["name"] for r_ in q("SELECT id, name FROM households")}

def _update_ops(hid, gr):

    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True)
    c_ = gr["client"]
    ops = []
    for k_, words in (("company", "company"), ("address", "address")):
        if c_[k_] and not CC.same_text(c_[k_], h[k_]):
            ops.append(("household", (k_, c_[k_]), words))
    if c_["notes"] and not CC.has_text(c_["notes"], h["notes"]):
        ops.append(("household", ("notes", ((h["notes"] or "").rstrip() + "\n\n" + c_["notes"]).strip()), "notes added"))
    here = [dict(p_) for p_ in q("SELECT * FROM people WHERE household_id=? ORDER BY id", (hid,))]
    for p in gr["people"]:
        same = next((x_ for x_ in here if p["email"] and (x_["email"] or "").strip().lower() == p["email"].lower()), None) \
            or next((x_ for x_ in here if CC.phone_key(p["phone"]) and CC.phone_key(x_["phone"]) == CC.phone_key(p["phone"])), None) \
            or next((x_ for x_ in here if (x_["name"] or "").strip().lower() == p["name"].lower()), None)
        if same is None:
            ops.append(("person_add", p, f"adds {p['name']}"))
            continue
        for k_, col_, words in (("email", "email", "email"), ("phone", "phone", "phone"),
                                ("relationship", "relation", "relationship"), ("birth_date", "birth_date", "birth date")):
            if not p[k_] or CC.same_text(p[k_], same[col_]):
                continue
            if k_ == "phone" and CC.phone_key(p[k_]) and CC.phone_key(p[k_]) == CC.phone_key(same[col_]):
                continue
            ops.append(("person", (same["id"], col_, p[k_]), f"{same['name']}'s {words}"))

        now_ = dict(same, birth_date=p["birth_date"], minor_box=None) \
            if p["birth_date"] and not CC.same_text(p["birth_date"], same["birth_date"]) else same
        if p["minor"] is not None and same["role"] != "pet" and int(p["minor"]) != int(minor_on(now_)):
            ops.append(("person", (same["id"], "is_minor", int(p["minor"])), f"{same['name']}'s minor mark"))
        if p["notes"] and not CC.has_text(p["notes"], same["notes"]):
            ops.append(("person", (same["id"], "notes", ((same["notes"] or "").rstrip() + "\n\n" + p["notes"]).strip()),
                        f"{same['name']}'s notes added"))
    return ops

def import_plan(d_):

    header, rows, mapping = d_["header"], d_["rows"], d_["mapping"]
    groups, problems = CC.groups(CC.records(header, rows, mapping))
    em, ph, names = _existing_index()
    add, match, notes, claimed = [], [], [], {}
    for gr in groups:
        notes += gr["notes"]
        keys_ = [("e", p["email"].lower()) for p in gr["people"] if p["email"]] + \
                [("p", CC.phone_key(p["phone"])) for p in gr["people"] if CC.phone_key(p["phone"])]
        hits, by = set(), []
        for kind_, k_ in keys_:
            found_ = (em if kind_ == "e" else ph).get(k_, set())
            if found_ - hits:
                by.append("email" if kind_ == "e" else "phone")
            hits |= found_
        num_ = gr["client"]["number"]
        if num_ and int(num_) in names and CC.same_text(names[int(num_)], gr["client"]["name"]):
            hits.add(int(num_))
            by.append("client number")
        first_row = gr["rows"][0]
        if len(hits) > 1:
            problems.append((first_row, f"{gr['client']['name']} matches {len(hits)} clients here ("
                             + ", ".join(sorted(names[h_] for h_ in hits)) + "): left out. Add or update them by hand."))
            continue
        twin = next((claimed[k_] for k_ in keys_ if k_ in claimed), None)
        if twin and not hits:
            problems.append((first_row, f"{gr['client']['name']} has the same email or phone as row {twin}: left out."))
            continue
        for k_ in keys_:
            claimed.setdefault(k_, first_row)
        gr["id"] = "g%d" % first_row
        if hits:
            gr["hid"] = hid_ = next(iter(hits))
            gr["here"] = names[hid_]
            gr["by"] = " and ".join(dict.fromkeys(by))
            gr["ops"] = _update_ops(hid_, gr)
            match.append(gr)
        else:
            add.append(gr)
    return dict(add=add, match=match, problems=sorted(problems), notes=sorted(notes))

def _plan_sig(plan):
    import hashlib
    body = json.dumps([[g_["id"], g_["client"], g_["people"]] for g_ in plan["add"]]
                      + [[g_["id"], g_["hid"], [list(map(str, o_[1:])) for o_ in g_["ops"]]] for g_ in plan["match"]],
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:24]

def _add_person(hid, p):
    rel, gender, by, role, minor, approx = person_fields(dict(relation=p["relationship"], role="adult"))
    if p["minor"] is not None and role != "pet":
        minor = int(p["minor"])
    pid_ = x("""INSERT INTO people (household_id, name, role, is_minor, email, phone, relation, gender, birth_year,
                age_approx, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
             (hid, p["name"], role, minor, p["email"], p["phone"], rel, gender, by, approx, p["notes"]))
    if p["birth_date"]:
        _save_birth_date(pid_, dict(birth_date=p["birth_date"], **_minor_said(p["minor"], role)))
    return pid_

def _minor_said(minor, role="adult"):

    if minor is None or role == "pet":
        return {"minor_shown": ""}
    return {"minor_shown": "1", "is_minor": "1" if int(minor) else ""}

def _set_minor_mark(pid, minor):

    p_ = q("SELECT * FROM people WHERE id=?", (pid,), one=True)
    if not p_ or p_["role"] == "pet":
        return
    if born_on(p_):
        _save_birth_date(pid, dict(birth_date=p_["birth_date"][:10], **_minor_said(minor)))
    else:
        x("UPDATE people SET is_minor=?, minor_box=NULL WHERE id=?", (int(minor), pid))

IMPORT_ADDED = "Added from an imported file"

def _apply_import(plan, picks, file_name):

    added, updated = [], 0
    for gr in plan["add"]:
        c_ = gr["client"]
        hid = x("INSERT INTO households (id, name, company, address, notes, source) VALUES (?,?,?,?,?,?)",
                (next_id("households"), c_["name"], c_["company"], c_["address"], c_["notes"], ""))
        for p in gr["people"]:
            _add_person(hid, p)
        audit(IMPORT_ADDED, hid=hid, note=file_name, route=request.path)
        added.append([hid, q("SELECT created FROM households WHERE id=?", (hid,), one=True)["created"]])
    for gr in plan["match"]:
        if picks.get(gr["id"]) != "update" or not gr["ops"]:
            continue
        hid = gr["hid"]
        for op, a_, _w in gr["ops"]:
            if op == "household":
                x(f"UPDATE households SET {dbmod.ident(a_[0])}=? WHERE id=?", (a_[1], hid))
            elif op == "person_add":
                _add_person(hid, a_)
            elif op == "person":
                pid_, col_, v_ = a_
                if col_ == "birth_date":
                    _save_birth_date(pid_, {"birth_date": v_, "minor_shown": ""})
                elif col_ == "relation":
                    p_ = q("SELECT * FROM people WHERE id=?", (pid_,), one=True)
                    rel, gender, by, role, minor, approx = person_fields(dict(relation=v_), cur=p_)
                    x("UPDATE people SET relation=?, gender=?, role=? WHERE id=?", (rel, gender, role, pid_))
                elif col_ == "is_minor":
                    _set_minor_mark(pid_, v_)
                else:
                    x(f"UPDATE people SET {dbmod.ident(col_)}=? WHERE id=?", (v_, pid_))
        if any(o_[0] == "household" and o_[1][0] == "address" for o_ in gr["ops"]):
            for r_ in q("SELECT id FROM sessions WHERE household_id=? AND stage IN ('inquiry','quoted')", (hid,)):
                reapply_tax(r_["id"])
        audit("Updated from an imported file: " + "; ".join(w_ for _o, _a, w_ in gr["ops"]), hid=hid, note=file_name,
              route=request.path)
        updated += 1
    return added, updated

@app.get("/clients/import")
def clients_import():

    batches = []
    alive = {r_["id"]: r_["created"] for r_ in q("SELECT id, created FROM households")}
    for b_ in reversed(_import_batches()):
        left_ = [a_ for a_ in b_.get("added", []) if alive.get(a_[0]) == a_[1]]
        batches.append(dict(b_, left=len(left_)))
    return render_template("clients_import.html", d=None, batches=batches, fields=CC.FIELDS,
                           max_mb=CC.MAX_BYTES // (1024 * 1024), max_rows=CC.MAX_ROWS)

@app.post("/clients/import")
def clients_import_upload():

    import secrets as _sec
    import time
    f_ = request.files.get("file")
    if not f_ or not f_.filename:
        g.refused = True
        session["notice"] = "Pick a CSV file to import."
        return redirect(url_for("clients_import"))
    try:
        header, rows, enc = CC.read(f_.read(CC.MAX_BYTES + 1))
    except CC.ImportProblem as e:
        g.refused = True
        session["notice"] = str(e)
        return redirect(url_for("clients_import"))
    name_ = CC.clean(os.path.basename(f_.filename.replace("\\", "/")), 80) or "file.csv"
    token = _sec.token_urlsafe(16)
    _import_draft("")
    with _IMPORT_LOCK:
        while len(_IMPORT_DRAFTS) >= 5:
            _IMPORT_DRAFTS.pop(min(_IMPORT_DRAFTS, key=lambda k_: _IMPORT_DRAFTS[k_]["at"]))
        _IMPORT_DRAFTS[token] = dict(at=time.time(), file=name_, enc=enc, header=header, rows=rows,
                                     mapping=CC.guess(header))
    return redirect(url_for("clients_import_check", token=token))

def _draft_or_gone(token):
    d_ = _import_draft(token)
    if d_ is None:
        session["notice"] = "That file is no longer open here (an import waits an hour at most). Pick it again."
    return d_

@app.get("/clients/import/<token>")
def clients_import_check(token):

    d_ = _draft_or_gone(token)
    if d_ is None:
        return redirect(url_for("clients_import"))
    plan = import_plan(d_)
    samples = [next((r_[i] for r_ in d_["rows"] if r_[i].strip()), "")[:60] for i in range(len(d_["header"]))]
    return render_template("clients_import.html", d=d_, token=token, plan=plan, sig=_plan_sig(plan), fields=CC.FIELDS,
                           samples=samples, batches=[], max_mb=0, max_rows=0)

@app.post("/clients/import/<token>/columns")
def clients_import_columns(token):
    d_ = _draft_or_gone(token)
    if d_ is None:
        return redirect(url_for("clients_import"))
    keys_ = set(CC.FIELD_LABELS)
    d_["mapping"] = [v_ if v_ in keys_ else CC.LEAVE_OUT
                     for v_ in (request.form.get(f"col{i}", CC.LEAVE_OUT) for i in range(len(d_["header"])))]
    session["notice"] = "Preview updated."
    return redirect(url_for("clients_import_check", token=token) + "#preview")

@app.post("/clients/import/<token>/cancel")
def clients_import_cancel(token):
    with _IMPORT_LOCK:
        _IMPORT_DRAFTS.pop(token, None)
    session["notice"] = "Import canceled. Nothing was added or changed."
    return redirect(url_for("clients"))

@app.post("/clients/import/<token>/confirm")
def clients_import_confirm(token):

    d_ = _draft_or_gone(token)
    if d_ is None:
        g.refused = True
        return redirect(url_for("clients_import"))
    plan = import_plan(d_)
    if request.form.get("sig") != _plan_sig(plan):
        g.refused = True
        session["notice"] = "The clients here changed since this preview. Check it again, then Import."
        return redirect(url_for("clients_import_check", token=token) + "#preview")
    picks = {gr["id"]: request.form.get("pick_" + gr["id"], "skip") for gr in plan["match"]}
    added, updated = _apply_import(plan, picks, d_["file"])
    if not added and not updated:
        g.refused = True
        session["notice"] = "Nothing to import: no new clients, and no matches picked for Update."
        return redirect(url_for("clients_import_check", token=token) + "#preview")
    skipped = len(plan["match"]) - updated
    words = (f"{len(added)} added" + (f", {updated} updated" if updated else "") + (f", {skipped} skipped" if skipped else ""))
    audit(f"Imported clients from a file: {words}", note=d_["file"], route=request.path)
    items = _import_batches()
    items.append(dict(id=max([b_.get("id", 0) for b_ in items] or [0]) + 1, at=now_local(), file=d_["file"],
                      added=added, updated=updated, removed=None))
    _save_import_batches(items)
    with _IMPORT_LOCK:
        _IMPORT_DRAFTS.pop(token, None)
    session["notice"] = f"Imported {d_['file']}: {words}."
    return redirect(url_for("clients"))

def _untouched_since_import(hid):

    return not (q("SELECT 1 FROM sessions WHERE household_id=? LIMIT 1", (hid,), one=True)
                or q("SELECT 1 FROM log WHERE household_id=? LIMIT 1", (hid,), one=True)
                or q("SELECT 1 FROM audit WHERE household_id=? AND action != ? LIMIT 1", (hid, IMPORT_ADDED), one=True)
                or q("SELECT 1 FROM client_labels WHERE household_id=? LIMIT 1", (hid,), one=True)
                or q("SELECT 1 FROM client_end_contact WHERE household_id=? LIMIT 1", (hid,), one=True)
                or q("SELECT 1 FROM households WHERE referred_by=? LIMIT 1", (hid,), one=True)
                or (FACES / f"{hid}.jpg").exists())

@app.post("/clients/import/batch/<int:bid>/remove")
def clients_import_remove(bid):

    items = _import_batches()
    b_ = next((b_ for b_ in items if b_.get("id") == bid and not b_.get("removed")), None)
    if b_ is None:
        g.refused = True
        session["notice"] = "That import was already removed."
        return redirect(url_for("clients_import"))
    gone, kept = [], 0
    for hid, created in b_.get("added", []):
        if not q("SELECT 1 FROM households WHERE id=? AND created=?", (hid, created), one=True):
            continue
        if _untouched_since_import(hid):
            x("DELETE FROM households WHERE id=?", (hid,))
            gone.append((hid, created))
        else:
            kept += 1
    b_["removed"] = now_local()
    b_["kept"] = kept
    _save_import_batches(items)
    audit(f"Removed an import: {len(gone)} client{'' if len(gone) == 1 else 's'} removed"
          + (f", {kept} kept (worked with since)" if kept else ""), note=b_.get("file", ""), route=request.path)
    g.pop("tx", None)
    db().commit()
    for hid, created in gone:
        _record_deletion(hid, created)
    session["notice"] = (f"Removed {len(gone)} client{'' if len(gone) == 1 else 's'} added by {b_.get('file', 'the import')}."
                         + (f" {kept} you have worked with since stayed." if kept else ""))
    return redirect(url_for("clients_import"))

app.config.setdefault("quiet_endpoints", set()).update(
    {"clients_import_upload", "clients_import_columns", "clients_import_cancel", "clients_import_confirm",
     "clients_import_remove"})

@app.route("/contact/new", methods=["GET", "POST"])
def contact_new():

    hh_ = q("""SELECT h.id, h.name, (SELECT COALESCE(NULLIF(p.email,''), NULLIF(p.phone,''), '') FROM people p
               WHERE p.household_id=h.id AND p.role IN ('parent','adult') ORDER BY p.id LIMIT 1) AS reach
               FROM households h ORDER BY h.name""")
    if request.method == "GET":

        name_ = " ".join(request.args.get("name", "").split())[:200]
        return render_template("contact_new.html", households=hh_, vals=dict(contact_name=name_) if name_ else {}, dupes=[])
    f = request.form
    name = f.get("contact_name", "").strip()
    if not name:
        abort(400)
    if not f.get("same_ok"):

        dupes = q("""SELECT p.name, p.email, p.phone, h.id AS hid, h.name AS household FROM people p
                     JOIN households h ON h.id=p.household_id WHERE lower(trim(p.name))=lower(?) ORDER BY h.id LIMIT 10""",
                  (" ".join(name.split()),))
        if dupes:
            g.refused = True
            return render_template("contact_new.html", households=hh_, vals=f, dupes=dupes)
    company_ = " ".join(f.get("company", "").split())[:200]
    hid = x("INSERT INTO households (id, name, company, source, referred_by) VALUES (?,?,?,?,?)",
            (next_id("households"), company_ or f.get("household_name", "").strip() or name, company_, f.get("source", ""),
             int(f["referred_by"]) if f.get("referred_by") else None))
    rel, gender, by, role, minor, approx = person_fields(dict(relation=f.get("contact_relation"), gender=f.get("contact_gender"),
                                                              age=f.get("contact_age"), age_approx=f.get("contact_age_approx"), role="adult"))
    x("""INSERT INTO people (household_id, name, role, email, phone, relation, gender, birth_year, age_approx)
         VALUES (?,?,?,?,?,?,?,?,?)""", (hid, name, role, f.get("email", ""), f.get("phone", ""), rel, gender, by, approx))
    msg_ = None
    if f.get("note", "").strip():
        msg_ = x("INSERT INTO log (household_id, channel, summary, at) VALUES (?,?,?,?)",
                 (hid, f.get("channel", "email"), f["note"].strip(), now_local()))
    if f.get("then") == "inquiry":
        return redirect(url_for("inquiry", hid=hid, msg=msg_) if msg_ else url_for("inquiry", hid=hid))
    return redirect(url_for("client_view", hid=hid))

def contact_message(hid, lid):

    if not hid or not lid:
        return None
    return q("SELECT id, summary, channel, at FROM log WHERE id=? AND household_id=? AND session_id IS NULL", (lid, hid), one=True)

def contact_standing():

    by_ = {}
    for s_ in q("SELECT household_id, stage FROM sessions"):
        by_.setdefault(s_["household_id"], set()).add(s_["stage"])
    out = {}
    for hid_, st_ in by_.items():
        on_ = [x_ for x_ in C.STAGES if x_ in st_ and x_ != "closed"]
        out[hid_] = (C.STAGE_LABELS[next(x_ for x_ in reversed(on_) if x_ not in ("inquiry", "quoted"))]
                     if any(x_ not in ("inquiry", "quoted") for x_ in on_) else "Lead" if on_
                     else "Past client" if "closed" in st_ else "Not booked")
    return out

@app.route("/inquiry", methods=["GET", "POST"])
def inquiry():

    if request.method == "GET":
        hid = request.args.get("hid", type=int)
        t_ = request.args.get("type", "")
        h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) if hid else None
        if hid and not h:
            abort(404)
        last_ = q("SELECT type FROM sessions ORDER BY id DESC LIMIT 1", one=True)
        return render_template("inquiry.html", h=h, type_=t_ if t_ in C.SESSION_TYPES else "", last_type=last_["type"] if last_ else "",
                               msg=contact_message(hid, request.args.get("msg", type=int)),
                               households=sorted(q("SELECT id, name FROM households"), key=lambda r_: re.sub(r"^the\s+", "", r_["name"].lower())),
                               standing=contact_standing() if not h else {},
                               people=q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id",
                                        (hid,)) if h else [],
                               plans=q("""SELECT id, title, type, shoot_date FROM sessions WHERE household_id=? AND plan_of IS NULL
                                          AND stage NOT IN ('closed','lost') ORDER BY id""", (hid,)) if h else [])
    f = request.form
    ask_ = _weak_codeword_ask(" ".join((f.get("gallery_codeword") or "").split())[:60], url_for("inquiry"))
    if ask_:
        return ask_
    t_ = f.get("type", "")
    hid = f.get("household_id", type=int)
    if t_ not in C.SESSION_TYPES or not hid or not q("SELECT 1 FROM households WHERE id=?", (hid,), one=True):
        abort(400)
    subject_ids = [int(v) for v in f.getlist("subjects") if v.isdigit()]
    if t_ in C.SUBJECT_TYPES and f.get("subject_name", "").strip():
        rel_ = f.get("subject_relation") or ("pet" if t_ in C.ANIMAL_TYPES else "")
        if t_ in C.BABY_TYPES and rel_ not in PARENT_REL and rel_ != "pet" and not f.get("subject_age"):
            f = f.copy(); f["subject_minor"] = "1"; f["minor_shown"] = "1"
        rel, gender, by, role, minor, approx = person_fields(dict(relation=rel_, gender=f.get("subject_gender"),
                                                                  age=f.get("subject_age"), age_approx=f.get("subject_age_approx"),
                                                                  is_minor=f.get("subject_minor"), minor_shown=f.get("minor_shown"),
                                                                  role="child" if f.get("subject_minor") else "adult"))

        names = []
        for n_ in (n_.strip() for n_ in re.split(r",|\band\b|&", f["subject_name"])):
            if not n_:
                continue
            if len(names) > 12:
                break
            if re.fullmatch(r"\(?\d{1,2}\)?", n_) and names:
                names[-1] += " " + n_
            elif n_.lower() not in [x_.lower() for x_ in names]:
                names.append(n_)
        names = names[:12] or [f["subject_name"].strip()]
        minor_typed = minor
        have_ = {" ".join(p_["name"].lower().split()): p_["id"] for p_ in q("SELECT id, name FROM people WHERE household_id=?", (hid,))}
        for i_, nm in enumerate(names):
            first_ = i_ == 0
            ta_ = trailing_age(nm, newline_ok=True)
            bare_ = " ".join((ta_[0] if ta_ else nm).lower().split())
            firsts_ = [pid_ for n_, pid_ in have_.items() if n_.split()[:1] == [bare_]] if " " not in bare_ else []
            if bare_ in have_ or len(firsts_) == 1:
                pid_ = have_.get(bare_) or firsts_[0]
                if pid_ not in subject_ids:
                    subject_ids.append(pid_)
                continue
            m_ = trailing_age(nm)
            if m_ and m_[0].strip() and first_ and f.get("subject_age", "").strip():
                nm = m_[0].strip()
            elif m_ and m_[0].strip():
                nm, age_ = m_[0].strip(), int(m_[1])
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
    if head_ and head_["id"] != sid:
        x("UPDATE sessions SET plan_of=? WHERE id=?", (head_["id"], sid))
        x("DELETE FROM documents WHERE session_id=? AND kind='agreement' AND status != 'signed'", (sid,))
        if q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (head_["id"],), one=True) \
                and not plan_covers(head_["id"], t_):

            x("INSERT INTO tasks (id, session_id, title, due, sort, stage, own) VALUES (?,?,?,?,999,?,1)",
              (next_id("tasks"), head_["id"], PLAN_RESIGN, None, head_["stage"]))
    if f.get("cw_make"):
        from plugins import gallery as _G
        x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (_G.suggest_codeword(), sid))
    elif (f.get("gallery_codeword") or "").strip():
        x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (" ".join(f["gallery_codeword"].split())[:60], sid))
    answers = {k: f.get("i_" + k, "").strip() for k, _ in C.INTAKE.get(t_, [])}
    if any(answers.values()):
        x("UPDATE sessions SET intake=? WHERE id=?", (json.dumps(answers), sid))

    msg_ = contact_message(hid, f.get("msg", type=int))
    if msg_:
        x("UPDATE log SET session_id=? WHERE id=? AND session_id IS NULL", (sid, msg_["id"]))
    if not msg_ or f.get("note", "").strip():
        x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
          (hid, sid, f.get("channel") or "note", f.get("note", "").strip() or f"Inquiry: {C.SESSION_TYPES[t_]}", now_local()))
    return redirect(stage_url(sid))

@app.get("/client/<int:hid>")
def client_view(hid):
    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    people = q("SELECT * FROM people WHERE household_id=? ORDER BY role DESC, name", (hid,))
    types = {r_["type"] for r_ in q("SELECT type FROM sessions WHERE household_id=?", (hid,))}

    show_grad = bool(types & set(C.GRAD_TYPES)) or any(p_["grad_year"] for p_ in people)
    show_born = bool(types & set(C.BABY_TYPES)) or any(p_["birth_date"] for p_ in people)
    show_gender = (not (h["company"] or "").strip() and not (types and types <= (set(C.ANIMAL_TYPES) | set(C.LICENSE_TYPES)))) \
        or any(p_["gender"] for p_ in people)
    offer = None
    added_ = request.args.get("added", type=int)
    if added_:
        p_ = next((p_ for p_ in people if p_["id"] == added_), None)
        open_ = open_sessions_without(added_) if p_ else []
        if open_:
            offer = dict(p=p_, sessions=[dict(id=r_["id"], what=(r_["title"] or "").strip() or C.SESSION_TYPES.get(r_["type"], r_["type"]),
                                               when=nice(r_["shoot_date"]) if r_["shoot_date"] else "") for r_ in open_])
    return render_template(
        "client.html", h=h, show_grad=show_grad, show_gender=show_gender, show_born=show_born,
        people=people, offer=offer,
        sessions=q("SELECT * FROM sessions WHERE household_id=? ORDER BY COALESCE(shoot_date,'9999') DESC", (hid,)),
        log=q("SELECT * FROM log WHERE household_id=? ORDER BY at DESC", (hid,)),
        referrer=q("SELECT id, name FROM households WHERE id=?", (h["referred_by"],), one=True),
        referred=q("SELECT id, name FROM households WHERE referred_by=?", (hid,)),
        households=q("SELECT id, name FROM households WHERE id != ? ORDER BY name", (hid,)))

@app.post("/client/<int:hid>")
def client_save(hid):
    f = request.form
    h_ = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    if _stale_edit("households", hid, CLIENT_FORM_FIELDS, h_):
        g.edit_clash_what = "client"
        return client_view(hid), 409
    for r_ in q("SELECT id FROM sessions WHERE household_id=? AND stage IN ('inquiry','quoted')", (hid,)):
        g.setdefault("_retax", []).append(r_["id"])
    x("UPDATE households SET name=?, company=?, address=?, source=?, referred_by=?, notes=? WHERE id=?",
      (f["name"], f.get("company", "").strip()[:200], f.get("address", ""), f.get("source", ""),
       int(f["referred_by"]) if f.get("referred_by") else None, f.get("notes", ""), hid))
    if f.get("tax_posted"):
        x("UPDATE households SET tax_exempt=?, tax_cert=? WHERE id=?",
          (1 if f.get("tax_exempt") else 0, (f.get("tax_cert") or "").strip()[:100], hid))
    if f.get("business_posted"):
        want_ = bool(f.get("business"))
        x("UPDATE households SET business=? WHERE id=?", (None if want_ == business_rule(hid) else int(want_), hid))
    for sid_ in g.pop("_retax", []):
        reapply_tax(sid_)
    return redirect(url_for("client_view", hid=hid) + "#details")

@app.get("/face/<int:hid>")
def face_img(hid):

    f = FACES / f"{hid}.jpg"
    try:
        data = datakey.open_bytes(f.read_bytes(), f"face:{hid}", label="faces")
    except (OSError, ValueError):
        abort(404)
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
    try:
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
    _save_birth_date(pid_, f, new=True)
    _same_name_notice(f["name"], hid)

    return redirect(url_for("client_view", hid=hid, added=pid_) + "#people")

@app.post("/session/<int:sid>/subject")
def session_subject_add(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    pid = request.form.get("person_id", type=int)
    p = q("SELECT name FROM people WHERE id=? AND household_id=?", (pid, s_["household_id"]), one=True) or abort(400)
    if add_subjects(sid, [pid]):
        rel_ = q("SELECT 1 FROM documents WHERE session_id=? AND about_person=? AND kind='release'", (sid, pid), one=True)
        what_ = (s_["title"] or "").strip() or C.SESSION_TYPES.get(s_["type"], s_["type"])
        session["notice"] = f"{p['name']} is now in the {what_} session" + (": their release is on its paperwork." if rel_ else ".")
    sync_workflow_tasks(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("client_view", hid=s_["household_id"]) + "#people")

def _save_birth_date(pid, f, new=False):

    if "birth_date" not in f:
        return
    v = (f.get("birth_date") or "").strip()
    try:
        v = date.fromisoformat(v).isoformat() if v else None
    except ValueError:
        return
    x("UPDATE people SET birth_date=? WHERE id=?", (v, pid))
    if not v:
        x("UPDATE people SET minor_box=NULL WHERE id=?", (pid,))
        return
    x("UPDATE people SET birth_year=? WHERE id=?", (int(v[:4]), pid))
    p_ = q("SELECT * FROM people WHERE id=?", (pid,), one=True)
    if not p_ or p_["role"] == "pet":
        x("UPDATE people SET minor_box=NULL WHERE id=?", (pid,))
        return
    by_date = 1 if age_on(born_on(p_), today()) < majority_age() else 0
    said = None
    if f.get("minor_shown"):
        box = 1 if f.get("is_minor") else 0
        if box != by_date and (box or not new):
            said = box
    x("UPDATE people SET is_minor=?, minor_box=? WHERE id=?", (by_date if said is None else said, said, pid))

def _same_name_notice(name, hid):

    other = q("""SELECT h.name FROM people p JOIN households h ON h.id=p.household_id
                 WHERE lower(trim(p.name))=lower(trim(?)) AND p.household_id != ? LIMIT 1""", (name, hid), one=True)
    if other:
        session["notice"] = f"{name.strip()} is also a person under {other['name']}. Check it isn't the same person twice."

@app.post("/person/<int:pid>")
def person_save(pid):
    f = request.form
    p = q("SELECT * FROM people WHERE id=?", (pid,), one=True) or abort(404)
    if not f.get("delete") and _stale_edit("people", pid, PERSON_FORM_FIELDS, p):
        g.edit_clash_form, g.edit_clash_what = "person", "person"
        g.edit_clash_reload = url_for("client_view", hid=p["household_id"]) + "#people"
        return client_view(p["household_id"]), 409
    if f.get("delete"):

        x("DELETE FROM documents WHERE about_person=? AND kind='release' AND status != 'signed'", (pid,))
        x("DELETE FROM people WHERE id=?", (pid,))
    else:
        rel, gender, by, role, minor, approx = person_fields(f, cur=p)
        x("""UPDATE people SET name=?, role=?, is_minor=?, grad_year=?, email=?, phone=?, relation=?, gender=?, birth_year=?, age_approx=?
             WHERE id=?""",
          (f["name"], role, minor, int(f["grad_year"]) if f.get("grad_year", "").strip().isdigit() else None, f.get("email", ""),
           f.get("phone", ""), rel, gender, by, approx, pid))
        _save_birth_date(pid, f)
        releases_follow(pid=pid)
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
    return redirect(stage_url(sid))

def client_link(link):

    import re as _re
    m_ = _re.match(r"^/session/(\d+)(?:#.*)?$", link or "")
    return stage_url(int(m_.group(1))) if m_ else link

app.jinja_env.globals["client_link"] = client_link

def stage_url(sid):
    r = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
    return url_for("client_stage", hid=r["household_id"], s=sid) if r else url_for("home")

def _session_gone(sid):

    import jobs
    hid_ = (q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) or {"household_id": None})["household_id"]
    if jobs.running(f"session-delete:{sid}") or (hid_ is not None and jobs.running(f"client-delete:{hid_}")):
        return render_template("gone.html", what="session", deleting=True)
    if hid_ is None:
        if 0 < sid <= id_mark("sessions"):
            g.refused = True
            return render_template("gone.html", what="session"), 410
        abort(404)
    return None

@app.get("/session/<int:sid>")
def session_view(sid):

    return _session_gone(sid) or redirect(stage_url(sid))

@app.get("/session/<int:sid>/edit")
def session_edit(sid):

    gone_ = _session_gone(sid)
    if gone_:
        return gone_
    s = session_full(sid)
    panels = [Markup(h) for h in (fn(s) for fn in hooks.session_panels) if h]
    tabs = [(lab, cnt, Markup(html)) for lab, cnt, html in (t for t in (fn(s) for fn in hooks.session_tabs) if t)]
    return render_template(
        "session.html", s=s, intake=json.loads(s["intake"] or "{}"),
        plan_opts=[r_ for r_ in q("SELECT * FROM sessions WHERE household_id=? AND id != ? AND plan_of IS NULL ORDER BY id", (s["household_id"], sid))]
                  if s["type"] in C.NEEDS_AGREEMENT and not q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True) else [],
        signer_opts=signer_choices(s["household_id"], agreement_days(sid), s["type"]) if s["type"] in C.NEEDS_AGREEMENT else [],
        signer_ids={r_["about_person"] for r_ in agreement_signers(sid)[0] if r_["about_person"]}
                   or {p_["id"] for p_ in default_signers(s["household_id"], s["type"] if s["type"] in C.COUPLE_TYPES else "",
                                                          agreement_days(sid))},
        subjects=q("""SELECT p.* FROM people p JOIN session_people sp ON sp.person_id=p.id
                      WHERE sp.session_id=? ORDER BY p.name COLLATE NOCASE""", (sid,)),
        members=q("SELECT * FROM people WHERE household_id=? ORDER BY name COLLATE NOCASE", (s["household_id"],)),
        tasks=q("SELECT * FROM tasks WHERE session_id=? ORDER BY COALESCE(due,'9999'), sort", (sid,)),
        docs=q("""SELECT doc.*, COALESCE(NULLIF(doc.covers, ''), p.name) AS person FROM documents doc LEFT JOIN people p ON p.id=doc.about_person
                  WHERE doc.session_id=?""", (sid,)),
        lines=q("SELECT * FROM lines WHERE session_id=?", (sid,)),
        payments=q("SELECT * FROM payments WHERE session_id=? ORDER BY received", (sid,)), issued_docs=issued_numbers(sid),
        print_orders_n=(app.config.get("print_orders_of") or (lambda s_: 0))(sid),
        receipt_no=(q("SELECT number FROM doc_numbers WHERE session_id=? AND kind='R'", (sid,), one=True) or {"number": ""})["number"],
        expenses=q("SELECT * FROM expenses WHERE session_id=? ORDER BY date", (sid,)),
        log=q("SELECT * FROM log WHERE session_id=? ORDER BY at DESC", (sid,)),
        t=totals(sid), expires=gallery_expires(s), restored=gallery_restored(sid), panels=panels, tabs=tabs, wf=workflow(sid),
        contact=household_contact(q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id",
                                    (s["household_id"],))),
        events=q("SELECT id, name FROM events ORDER BY date DESC"), minutes=session_minutes(sid, s["type"]),
        quote_len=length_from_quote(sid), q_locked=quote_locked(sid),

        gal_block=bool((app.config.get("gallery_for") or (lambda s_: False))(s)),
        gal_next=(app.config.get("gallery_next") or (lambda s_: None))(s))

SESSION_FORM_FIELDS = (("title", "Title"), ("type", "Type"), ("shoot_date", "Date"), ("start_time", "Start"),
                       ("duration_min", "Minutes"), ("po_number", "PO / reference"), ("lic_term", "License term"),
                       ("lic_territory", "Territory"), ("stops", "Where"), ("notes", "Notes"), ("delivery_method", "Files delivered by"))
CLIENT_FORM_FIELDS = (("name", "Name"), ("company", "Company"), ("address", "Mailing address"), ("source", "Source"),
                      ("notes", "Notes"))

STAMPED = {"sessions": ("title", "type", "shoot_date", "start_time", "duration_min", "po_number", "lic_term", "lic_territory",
                        "lic_exclusive", "date_tentative", "stops", "notes", "event_id", "delivery_method"),
           "households": ("name", "company", "address", "source", "referred_by", "notes"),

           "intake": ("intake",),
           "delivery": ("gallery_built", "letter_mailed", "first_visit", "extension_used", "sd_shipped", "delivery_method",
                        "favorites", "veto", "retouch_picks", "gallery_codeword"),
           "picks": ("favorites", "veto", "retouch_picks"),
           "when": ("shoot_date", "start_time", "stops"),
           "people": ("name", "relation", "gender", "role", "is_minor", "grad_year", "email", "phone", "birth_year",
                      "birth_date", "age_approx"),
           "events": ("name", "date", "venue", "notes"),
           "event_rights": ("billing", "rights", "rights_note")}
STAMP_TABLE = {"intake": "sessions", "delivery": "sessions", "picks": "sessions", "when": "sessions", "event_rights": "events"}
DELIVERY_FORM_FIELDS = (("gallery_built", "Gallery built"), ("letter_mailed", "QR letter mailed"), ("first_visit", "First visit"),
                        ("sd_shipped", "Files delivered"), ("favorites", "Portfolio suggestions"), ("veto", "Don't show"),
                        ("gallery_codeword", "Gallery codeword"), ("retouch_picks", "Retouch picks"))
PICKS_FORM_FIELDS = (("retouch_picks", "Retouch"), ("favorites", "Portfolio suggestions"), ("veto", "Don't show"))
WHEN_FORM_FIELDS = (("shoot_date", "Date"), ("start_time", "Start"), ("where", "Where"))
PERSON_FORM_FIELDS = (("name", "Name"), ("relation", "Who"), ("email", "Email"), ("phone", "Phone"), ("birth_date", "Born or due"),
                      ("grad_year", "Grad year"))
EVENT_FORM_FIELDS = (("name", "Name"), ("date", "Date"), ("venue", "Venue"), ("notes", "Notes"))

def edit_stamp(table, rid):

    import hashlib
    r_ = q("SELECT * FROM " + dbmod.ident(STAMP_TABLE.get(table, table)) + " WHERE id=?", (rid,), one=True)
    if not r_:
        return ""
    data = {k_: r_[k_] for k_ in STAMPED[table] if k_ in r_.keys()}
    if table == "sessions":
        data["_subjects"] = [r2["person_id"] for r2 in q("SELECT person_id FROM session_people WHERE session_id=? ORDER BY person_id", (rid,))]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:20]

def _stale_edit(table, rid, fields, current):

    seen = request.form.get("seen")
    if not seen or seen == edit_stamp(table, rid):
        return None
    g.refused = True
    cur_ = dict(current) if not isinstance(current, dict) else current
    g.edit_clash = [(lab, request.form.get(k_, ""), k_) for k_, lab in fields
                    if k_ in request.form and (request.form.get(k_) or "").strip() != str(cur_.get(k_) if cur_.get(k_) is not None else "").strip()]
    return True

def _clash_page(sid, form, what="session"):

    g.edit_clash_form, g.edit_clash_what = form, what
    back = (safe_back(request.form.get("back")) or "").split("#")[0].split("?")[0]
    if not back or back == url_for("session_edit", sid=sid):
        g.edit_clash_reload = url_for("session_edit", sid=sid) + "#" + form
        return session_edit(sid), 409
    g.stage_sid = sid
    g.edit_clash_reload = stage_url(sid)
    return client_stage(session_full(sid)["household_id"]), 409

def set_delivery_method(sid, new):

    s_ = q("SELECT type, delivery_method FROM sessions WHERE id=?", (sid,), one=True)
    old = C.delivery_method(s_["type"], s_["delivery_method"])
    if new == old:
        return
    x("UPDATE sessions SET delivery_method=? WHERE id=?", (new, sid))
    audit(f"Delivery changed from {C.DELIVERY_METHODS[old][0]} to {C.DELIVERY_METHODS[new][0]}", sid=sid)
    said = signed_delivery_differs(sid)
    if said:
        session["notice"] = f"The signed agreement says you deliver the photos {said}. Tell the client, or have it signed again."

def signed_delivery_differs(sid):

    s_ = q("SELECT type, delivery_method, plan_of FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return None
    for ref_ in [sid] + ([s_["plan_of"]] if s_["plan_of"] else []):
        copy_ = signed_copy("agreement", ref_)
        if copy_ and copy_.get("delivery"):
            now_ = C.delivery_text(s_["type"], s_["delivery_method"])
            return copy_["delivery"] if copy_["delivery"] != now_ else None
    return None

app.jinja_env.globals["signed_delivery_differs"] = lambda sid_: signed_delivery_differs(sid_)

@app.post("/session/<int:sid>")
def session_save(sid):
    f = request.form
    s = session_full(sid)
    if _stale_edit("sessions", sid, SESSION_FORM_FIELDS, s):
        g.edit_clash_what = "session"
        g.edit_clash_reload = url_for("session_edit", sid=sid) + "#shoot"
        return session_edit(sid), 409
    old_date, old_tentative = s["shoot_date"], s["date_tentative"]
    x("""UPDATE sessions SET title=?, type=?, shoot_date=?, start_time=?, stops=?, notes=?, event_id=?
         WHERE id=?""",
      (f.get("title", ""), f.get("type", s["type"]), f.get("shoot_date") or None, start_time_in(f.get("start_time", "")),
       f.get("stops", ""), f.get("notes", ""), int(f["event_id"]) if f.get("event_id") else None, sid))
    if "duration_min" in f and not length_from_quote(sid):
        x("UPDATE sessions SET duration_min=? WHERE id=?", (int(f["duration_min"]) if f["duration_min"].isdigit() else None, sid))
    if "po_number" in f:
        x("UPDATE sessions SET po_number=? WHERE id=?", (f.get("po_number", "").strip()[:80], sid))
    if not quote_locked(sid):
        _save_license(sid, f)
    if "date_tentative_present" in f:
        x("UPDATE sessions SET date_tentative=? WHERE id=?", (1 if f.get("date_tentative") else 0, sid))
    if f.get("delivery_method") in C.DELIVERY_METHODS:
        set_delivery_method(sid, f["delivery_method"])
    if f.get("subjects_present"):
        before = {r_[0] for r_ in q("SELECT person_id FROM session_people WHERE session_id=?", (sid,))}
        db().execute("DELETE FROM session_people WHERE session_id=?", (sid,))
        for pid in {int(v_) for v_ in f.getlist("subjects") if v_.isdigit()}:
            db().execute("INSERT INTO session_people VALUES (?,?)", (sid, pid))
        _commit()
        after = {r_[0] for r_ in q("SELECT person_id FROM session_people WHERE session_id=?", (sid,))}
        releases_follow_subjects(sid, after - before, before - after)
    date_moved(sid, old_date, was_tentative=old_tentative)
    sync_workflow_tasks(sid)
    if (old_date or None) != (f.get("shoot_date") or None):
        schedule_tasks(sid, fair=True)
    return redirect(url_for("session_edit", sid=sid) + "#shoot")

def business_rule(hid):

    h_ = q("SELECT company FROM households WHERE id=?", (hid,), one=True)
    if h_ and (h_["company"] or "").strip():
        return True
    return any(C.TYPE_KIND.get(r_["type"]) == "property" or r_["type"] in C.NET_TERMS_TYPES
               for r_ in q("SELECT DISTINCT type FROM sessions WHERE household_id=?", (hid,)))

def business_choice(hid):

    r_ = q("SELECT business FROM households WHERE id=?", (hid,), one=True)
    return None if not r_ or r_["business"] is None else bool(r_["business"])

def is_business_client(hid):

    c_ = business_choice(hid)
    return business_rule(hid) if c_ is None else c_

app.jinja_env.globals["is_business_client"] = lambda hid_: is_business_client(hid_)

def business_client(s):

    c_ = business_choice(s["household_id"])
    if c_ is not None:
        return c_
    if C.TYPE_KIND.get(s["type"]) == "property" or s["type"] in C.NET_TERMS_TYPES:
        return True
    return s["type"] in C.LICENSE_TYPES and bool((company_of(s["household_id"]) or "").strip())

app.jinja_env.globals["business_client"] = lambda s_: business_client(s_)

def length_from_quote(sid):

    return bool(q("SELECT 1 FROM lines WHERE session_id=? AND kind NOT IN ('discount','tax','portfolio')", (sid,), one=True))

def _save_license(sid, f):
    for k_ in ("lic_term", "lic_territory"):
        if k_ in f:
            x(f"UPDATE sessions SET {k_}=? WHERE id=?", (f.get(k_, "").strip()[:200], sid))
    if "lic_present" in f:
        x("UPDATE sessions SET lic_exclusive=? WHERE id=?", (1 if f.get("lic_exclusive") else 0, sid))

@app.post("/session/<int:sid>/quote/license")
def quote_license(sid):

    back = safe_back(request.form.get("back")) or stage_url(sid) + "#billing"
    s_ = q("SELECT type, household_id FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    if s_["type"] not in C.LICENSE_TYPES or not business_client(s_):
        abort(400)
    if quote_locked(sid):
        return _refuse(QUOTE_LOCKED, back)
    _save_license(sid, request.form)
    session["notice"] = "License terms saved"
    return redirect(back)

RESCHEDULE_REOPENS = ("Send session details", "Confirm time and place")

def reschedules(sid):

    return q("SELECT COUNT(*) n FROM audit WHERE session_id=? AND action LIKE 'Rescheduled from %'", (sid,), one=True)["n"]

def date_moved(sid, old, was_tentative=False):

    releases_follow(sid=sid)

    keep_plan_dates(sid, again=bool(was_tentative or not old))
    s_ = q("SELECT stage, shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    new = s_["shoot_date"] if s_ else None
    if not s_ or was_tentative or not old or not new or old[:10] == new[:10] or s_["stage"] not in ("booked", "shot"):
        return
    marks = ",".join("?" * len(RESCHEDULE_REOPENS))
    reopened = [r_["title"] for r_ in q(f"SELECT title FROM tasks WHERE session_id=? AND own=0 AND done IS NOT NULL AND title IN ({marks})",
                                        (sid, *RESCHEDULE_REOPENS))]
    x(f"UPDATE tasks SET done=NULL, outcome=NULL WHERE session_id=? AND own=0 AND title IN ({marks})", (sid, *RESCHEDULE_REOPENS))
    n_ = reschedules(sid) + 1
    audit(f"Rescheduled from {nice(old)} to {nice(new)} ({'once' if n_ == 1 else f'{n_} times'} so far)"
          + (f"; open again: {oxford(reopened)}" if reopened else ""), sid=sid)
    if resched_fees_open(sid) and has_request_context():
        session["notice"] = ((session.get("notice") or "") + " The Terms charge a reschedule fee for this one: add it "
                             "or waive it under Billing.").strip()

def releases_follow_subjects(sid, added, removed=()):

    for pid in removed:
        x("DELETE FROM documents WHERE session_id=? AND kind='release' AND about_person=? AND status != 'signed'", (sid, pid))
    s_ = q("SELECT type, shoot_date FROM sessions WHERE id=?", (sid,), one=True)
    if not s_ or s_["type"] not in C.NEEDS_RELEASE or not added:
        return
    signs = {r_[0] for r_ in q("SELECT about_person FROM documents WHERE session_id=? AND kind='agreement'", (plan_head(sid),))}
    has = {r_[0] for r_ in q("SELECT about_person FROM documents WHERE session_id=? AND kind='release'", (sid,))}
    for pid in sorted(added - signs - has):
        p = q("SELECT * FROM people WHERE id=?", (pid,), one=True)
        if p:
            x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'release', ?, ?)",
              (sid, pid, release_signer(p, s_["shoot_date"])))

def releases_follow(pid=None, sid=None):

    where, args = ("d.about_person=?", (pid,)) if pid else ("d.session_id=?", (sid,))
    for d_ in q(f"""SELECT d.id, d.signer, d.about_person, s.shoot_date FROM documents d JOIN sessions s ON s.id=d.session_id
                    WHERE {where} AND d.kind='release' AND d.status != 'signed' AND d.about_person IS NOT NULL
                    AND COALESCE(d.covers, '')=''""", args):
        p = q("SELECT * FROM people WHERE id=?", (d_["about_person"],), one=True)
        if not p:
            continue
        parents = [r_["name"] for r_ in q("SELECT name FROM people WHERE household_id=? AND role='parent'", (p["household_id"],))]
        ours = {"", "Parent or guardian (required)", "Parent (required)"} | {f"{w_}: {n_}" for n_ in parents
                                                                             for w_ in ("Parent or guardian", "Parent")}
        now_ = release_signer(p, d_["shoot_date"])
        if (d_["signer"] or "").strip() in ours and (d_["signer"] or "").strip() != now_:
            x("UPDATE documents SET signer=? WHERE id=?", (now_, d_["id"]))

NOT_GUARDIAN_REL = {"grandmother", "grandfather", "aunt", "uncle"}

def release_signer(p, day=None):

    if not minor_on(p, day):
        return ""
    parent = next((r_ for r_ in q("SELECT name, relation FROM people WHERE household_id=? AND role='parent' ORDER BY id",
                                  (p["household_id"],))
                   if (r_["relation"] or "").strip().lower() not in NOT_GUARDIAN_REL), None)
    return f"Parent or guardian: {parent['name']}" if parent else "Parent or guardian (required)"

@app.post("/session/<int:sid>/intake")
def session_intake(sid):
    s = session_full(sid)
    if _stale_edit("intake", sid, tuple(C.INTAKE.get(s["type"], [])), json.loads(s["intake"] or "{}")):
        return _clash_page(sid, "intake")
    data = {k: request.form.get(k, "") for k, _ in C.INTAKE.get(s["type"], [])}
    x("UPDATE sessions SET intake=? WHERE id=?", (json.dumps(data), sid))
    return redirect(safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#intake")

def _first_visit_confirm(sid, s_, fv_, extension_used, action, cancel):

    try:
        ends_ = date.fromisoformat(fv_[:10]) + timedelta(days=C.gallery_days(s_["type"]) * (2 if extension_used else 1))
    except ValueError:
        return None
    dl_ = app.config.get("gallery_download_online")
    if ends_ >= today() or (callable(dl_) and dl_(sid)):
        return None
    if not _gallery_to_close(sid, s_, request.form):
        return None
    g.refused = True
    return render_template("confirm_post.html", question="This closes the gallery now. Save anyway?",
                           detail=(f"With a first visit on {nice(fv_[:10])}, the gallery's days ran out on {nice(ends_.isoformat())}: "
                                   "the next gallery check takes it off the web. If the date is a slip, cancel and "
                                   "type the right one."),
                           action=action, fields=list(request.form.items(multi=True)),
                           confirm_name="confirm_close", yes="Save, and close the gallery", cancel=cancel)

def _weak_codeword_ask(word, action, cancel=None):

    from plugins import gallery as _G
    if not word or request.form.get("cw_ok") or request.form.get("cw_make") or _G.codeword_strong(word):
        return None
    g.refused = True
    return render_template("confirm_codeword.html", word=word, action=action, cancel=cancel,
                           fields=[(k_, v_) for k_, v_ in request.form.items(multi=True) if k_ not in ("cw_ok", "cw_make")])

@app.post("/session/<int:sid>/codeword")
def session_codeword(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    back_ = safe_back(request.form.get("back")) or url_for("client_stage", hid=s_["household_id"], s=sid)
    g_ = q("SELECT cw_fixed FROM galleries WHERE session_id=?", (sid,), one=True) if q(
        "SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
    from plugins import gallery as _G
    made_ = bool(request.form.get("cw_make"))
    new_cw = _G.suggest_codeword() if made_ else " ".join((request.form.get("gallery_codeword") or "").split())[:60]
    if g_ and g_["cw_fixed"]:
        return _refuse("The gallery is already online, so its codeword stays as it is: the link you sent keeps working.", back_)
    if not new_cw:
        return _refuse("Type a codeword, or press Make one for me. Nothing was saved.", back_)
    ask_ = _weak_codeword_ask(new_cw, url_for("session_codeword", sid=sid), back_)
    if ask_:
        return ask_
    x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (new_cw, sid))
    g.touched_sid = sid
    session["notice"] = f"Made the codeword \"{new_cw}\"" if made_ else "Saved the gallery codeword"
    return redirect(back_)

@app.post("/session/<int:sid>/delivery")
def session_delivery(sid):

    f = request.form
    s_ = session_full(sid)
    if _stale_edit("delivery", sid, DELIVERY_FORM_FIELDS, s_):
        return _clash_page(sid, "delivery")
    cw_typed_ = " ".join((f.get("gallery_codeword") or "").split())[:60]
    fixed_ = q("SELECT cw_fixed FROM galleries WHERE session_id=?", (sid,), one=True) if q(
        "SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
    if cw_typed_ != (s_["gallery_codeword"] or "") and not (fixed_ and fixed_["cw_fixed"]):
        ask_ = _weak_codeword_ask(cw_typed_, url_for("session_delivery", sid=sid), url_for("session_edit", sid=sid) + "#delivery")
        if ask_:
            return ask_
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
            return redirect(url_for("session_edit", sid=sid) + "#delivery")
    sd_ = (f.get("sd_shipped") or "").strip()
    owed_ = release_blocked(sid) if sd_ and sd_ != (s_["sd_shipped"] or "") else 0.0
    if owed_ > 0.005 and not f.get("confirm_owed"):

        g.refused = True
        return render_template("confirm_post.html", question=f"{money(owed_)} is still owed. Save the delivery anyway?",
                               detail=("The photos are released on payment. Saving this date records that you delivered "
                                       "them before the balance was paid; History notes it. To collect first, cancel and "
                                       "record the payment."),
                               action=url_for("session_delivery", sid=sid), fields=list(request.form.items(multi=True)),
                               confirm_name="confirm_owed", yes="Save, delivered before payment",
                               cancel=url_for("session_edit", sid=sid) + "#delivery")
    if owed_ > 0.005:
        audit_after(f"Delivered on {nice(sd_)} with {money(owed_)} still owed: chosen over the hold until payment", sid=sid)
    fv_ = (f.get("first_visit") or "").strip()
    if fv_ and fv_ != (s_["first_visit"] or "") and not f.get("confirm_close"):
        page_ = _first_visit_confirm(sid, s_, fv_, bool(f.get("extension_used")), url_for("session_delivery", sid=sid),
                                     url_for("session_edit", sid=sid) + "#delivery")
        if page_:
            return page_
    if f.get("delivery_method") in C.DELIVERY_METHODS:
        set_delivery_method(sid, f["delivery_method"])
    if "retouch_picks" in f:
        x("UPDATE sessions SET retouch_picks=? WHERE id=?", ((f.get("retouch_picks") or "")[:5000], sid))
    if f.get("cw_make"):
        from plugins import gallery as _Gm
        f = f.copy(); f["gallery_codeword"] = _Gm.suggest_codeword()
    if "gallery_codeword" in f and (f.get("gallery_codeword") or "").strip() != (s_["gallery_codeword"] or ""):
        g_ = q("SELECT cw_fixed FROM galleries WHERE session_id=?", (sid,), one=True) if q(
            "SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
        from plugins import gallery as _G
        new_cw = " ".join((f.get("gallery_codeword") or "").split())[:60]
        if g_ and g_["cw_fixed"]:
            session["notice"] = "The gallery is already online, so its codeword stays as it is: the link you sent keeps working."
        else:
            x("UPDATE sessions SET gallery_codeword=? WHERE id=?", (new_cw, sid))
    x("""UPDATE sessions SET gallery_built=?, letter_mailed=?, first_visit=?, extension_used=?, sd_shipped=?,
         favorites=?, veto=? WHERE id=?""",
      (f.get("gallery_built") or None, f.get("letter_mailed") or None, f.get("first_visit") or None,
       1 if f.get("extension_used") else 0, f.get("sd_shipped") or None, f.get("favorites", ""),
       f.get("veto", ""), sid))
    hooks.fire("gallery_window_changed", sid)
    return redirect(url_for("session_edit", sid=sid) + "#delivery")

@app.post("/session/<int:sid>/delete")
def session_delete(sid):
    s = session_full(sid)

    held = q("SELECT COALESCE(SUM(amount), 0) v FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True)["v"]
    held += (app.config.get("print_money_held") or (lambda s_: 0))(sid)
    if abs(held) > 0.005:
        session["notice"] = (f"This session still holds {money(held)} in recorded payments, so it wasn't deleted. If a "
                             "payment was entered by mistake, void it. If you gave the money back, record a refund. "
                             "Then you can delete the session.")
        g.refused = True
        return redirect(url_for("session_edit", sid=sid) + "#money")

    _start_session_delete(sid, s)
    session["notice"] = "Deleting this session: its gallery comes down and its photos are destroyed first. The progress shows at the bottom of the page."
    left = q("SELECT id FROM sessions WHERE household_id=? AND id != ? LIMIT 1", (s["household_id"], sid), one=True)
    return redirect(url_for("client_stage", hid=s["household_id"]) if left else url_for("client_view", hid=s["household_id"]))

def _gallery_down_again(sids):

    for sid in sids:
        if app.config.get("gallery_online") and app.config["gallery_online"](sid):
            try:
                app.config["gallery_take_down"](sid)
            except Exception:
                app.logger.exception("take-down at the end of a delete %s", sid)
                raise RuntimeError("A gallery went online again during the delete and could not be taken down; the "
                                   "records were kept. Check the web host, then delete again.") from None

def _gallery_still_down(sids):

    if app.config.get("gallery_online") and any(app.config["gallery_online"](sid) for sid in sids):
        raise RuntimeError("A gallery went online again during the delete; the records were kept. Delete again.")

def _start_session_delete(sid, s):
    import jobs
    act = f"Deleted the {C.SESSION_TYPES.get(s['type'], s['type'])} session" + (f" ({nice(s['shoot_date'])})" if s["shoot_date"] else "")
    hid, created, eid = s["household_id"], s["created"], s["event_id"]

    def work(job):
        _delete_session(sid, hid, created, act, eid)
        return "Deleted the session and everything on it."
    jobs.start(app, f"session-delete:{sid}", "Deleting a session and everything on it", work)

def _delete_session(sid, hid, created, act, eid=None):

    if not q("SELECT 1 FROM sessions WHERE id=? AND created=?", (sid, created), one=True):
        return
    n_orders = app.config.get("print_orders_of") or (lambda s_: 0)
    orders_before = n_orders(sid)
    if app.config.get("gallery_take_down"):
        try:
            app.config["gallery_take_down"](sid)
        except Exception:
            app.logger.exception("take-down on delete %s", sid)
            raise RuntimeError("Could not take the gallery down; no photos or records were deleted and the session was "
                               "kept. Check the web host, then delete the session again.")
    if n_orders(sid) > orders_before:
        raise RuntimeError("A print order the client sent was collected as the gallery came down, so the session and its "
                           "photos were kept (the gallery is down). Look at the order, then delete the session again.")
    if app.config.get("retention_purge_now"):
        try:
            app.config["retention_purge_now"](sid)
        except Exception:
            app.logger.exception("purge on delete %s", sid)
            raise RuntimeError("Could not delete this session's sealed photos; the session was kept. Check the vault storage.")
    _gallery_down_again([sid])
    import locks
    with locks.WRITES:
        if not q("SELECT 1 FROM sessions WHERE id=? AND created=?", (sid, created), one=True):
            return
        _gallery_still_down([sid])
        held = q("SELECT COALESCE(SUM(amount), 0) v FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True)["v"]
        held += (app.config.get("print_money_held") or (lambda s_: 0))(sid)
        if abs(held) > 0.005:
            raise RuntimeError(f"The session now holds {money(held)} in recorded payments, so its records were kept "
                               "(its gallery and photos are gone). Void or refund the payment, then delete it again.")
        kept_ = issued_numbers(sid)
        g.tx = True
        try:
            _delete_session_rows(sid)
            audit(act, sid=sid, hid=hid, eid=eid, note=("Issued documents kept in History: " + ", ".join(kept_)) if kept_ else "",
                  route=f"/session/{sid}/delete")
            g.pop("tx", None)
            db().commit()
        except BaseException:
            g.pop("tx", None)
            db().rollback()
            raise
        _record_deletion(sid, created, kind="session")

def _pass_plan_on(sid):

    rest = q("SELECT id, title, type FROM sessions WHERE plan_of=? ORDER BY COALESCE(shoot_date,'9999'), id", (sid,))
    if not rest:
        return
    new = rest[0]["id"]
    x("UPDATE sessions SET plan_of=NULL WHERE id=?", (new,))
    x("UPDATE sessions SET plan_of=? WHERE plan_of=?", (new, sid))
    x("UPDATE documents SET session_id=? WHERE session_id=? AND kind='agreement'", (new, sid))
    for tbl_ in ("doc_snapshots", "doc_snapshots_old"):
        x("UPDATE OR IGNORE " + tbl_ + " SET ref=? WHERE kind='agreement' AND ref=?", (new, sid))
    x("UPDATE tasks SET session_id=? WHERE session_id=? AND title=?", (new, sid, PLAN_RESIGN))
    audit("The plan's first session was deleted: this session now holds the plan's agreement" +
          (f" for {len(rest) - 1} other session{'' if len(rest) == 2 else 's'}" if len(rest) > 1 else ""), sid=new)

def _delete_session_rows(sid):

    _pass_plan_on(sid)
    _keep_books([sid], why="session deleted")
    for tbl_ in ("doc_snapshots", "doc_snapshots_old"):
        x("DELETE FROM " + tbl_ + " WHERE (kind='agreement' AND ref=?) OR (kind='release' AND ref IN "
          "(SELECT id FROM documents WHERE session_id=?))", (sid, sid))
    x("DELETE FROM sessions WHERE id=?", (sid,))

def household_rows(hid):

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

    h = q("SELECT name FROM households WHERE id=?", (hid,), one=True) or abort(404)
    data = household_rows(hid)
    secret_ = {"key", "ekey", "fkey", "key_trail", "gallery_codeword", "picks_hash"}
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

DELETIONS = DB_PATH.parent / "deletions.enc"
DELETIONS_JSON = DB_PATH.parent / "deletions.json"

DELETIONS_ASIDE = DB_PATH.parent / "deletions.unverified.json"
NUMBERS = DB_PATH.parent / "doc-numbers.enc"

def _deletion_key(con):
    con.execute(_SIDE_KEYS)
    r = con.execute("SELECT key FROM side_keys WHERE name='deletions'").fetchone()
    if r:
        return bytes.fromhex(r[0])
    k = datakey.subkey("deletions-mac")
    con.execute("INSERT OR IGNORE INTO side_keys (name, key) VALUES ('deletions', ?)", (k.hex(),))
    return k

def _deletion_mac(key, d_):

    import hashlib
    import hmac
    body = "lumen-aureum deletion|" + json.dumps((["session"] if d_.get("kind") == "session" else []) + [d_["id"], d_["created"]])
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()

def _signed(key, d_):
    out = dict(id=d_["id"], created=d_["created"], at=d_.get("at"), mac=_deletion_mac(key, d_))
    if d_.get("kind") == "session":
        out = dict(kind="session", **out)
    return out

def _is_entry(d_):

    return (isinstance(d_, dict) and isinstance(d_.get("id"), int) and isinstance(d_.get("created"), str)
            and d_.get("kind", "client") in ("client", "session"))

def _is_client_entry(d_):
    return _is_entry(d_) and d_.get("kind", "client") == "client"

def _read_deletions():

    plain, sealed = [], []
    try:
        plain = json.loads(DELETIONS_JSON.read_text())
    except (OSError, ValueError):
        pass
    try:
        sealed = json.loads(datakey.open_bytes(DELETIONS.read_bytes(), "deletions", label="deletions"))
    except Exception:
        pass
    return (plain if isinstance(plain, list) else []), [d_ for d_ in (sealed if isinstance(sealed, list) else []) if _is_entry(d_)]

def _sort_deletions(con):

    import hmac
    key = _deletion_key(con)
    plain, sealed = _read_deletions()
    good, legacy, forged = [], [], []
    for d_ in plain:
        if not _is_entry(d_):
            forged.append(d_)
        elif "mac" not in d_:
            (legacy if _is_client_entry(d_) else forged).append(d_)
        elif isinstance(d_["mac"], str) and hmac.compare_digest(d_["mac"], _deletion_mac(key, d_)):
            good.append(d_)
        else:
            forged.append(d_)
    seen = {(d_["id"], d_["created"]) for d_ in good if _is_client_entry(d_)}
    for d_ in sealed:
        if _is_client_entry(d_) and (d_["id"], d_["created"]) not in seen:
            seen.add((d_["id"], d_["created"]))
            good.append(_signed(key, d_))
    legacy = [d_ for d_ in legacy if (d_["id"], d_["created"]) not in seen]
    return key, good, legacy, forged

def _set_aside(items):

    try:
        old = json.loads(DELETIONS_ASIDE.read_text())
    except (OSError, ValueError):
        old = []
    _write_plain(DELETIONS_ASIDE, json.dumps((old if isinstance(old, list) else []) + items))

def _record_deletion(hid, created, kind="client"):

    with _ID_LOCK:
        key, good, legacy, forged = _sort_deletions(db())
        _commit()
        if any(d_["id"] == hid and d_["created"] == created and d_.get("kind", "client") == kind for d_ in good):
            return
        items = good + legacy + forged + [_signed(key, dict(id=hid, created=created, at=now_local(), kind=kind))]
        _write_plain(DELETIONS_JSON, json.dumps(items))

def reapply_deletions():

    n = 0
    con = connect()
    try:
        key, good, legacy, forged = _sort_deletions(con)
        con.commit()
        adopted, held = [], []
        for d_ in legacy:
            here = con.execute("SELECT 1 FROM households WHERE id=? AND created=?", (d_["id"], d_["created"])).fetchone()
            (held if here else adopted).append(d_)
        stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        ev_cols = {r_[1] for r_ in con.execute("PRAGMA table_info(events)")}
        destroy_ = []
        sessions_ = [d_ for d_ in good if not _is_client_entry(d_)]
        for d_ in good:
            if not _is_client_entry(d_):
                continue
            r = con.execute("SELECT id FROM households WHERE id=? AND created=?", (d_["id"], d_["created"])).fetchone()
            if not r:
                continue
            these_ = []
            try:
                con.execute("SAVEPOINT redelete")
                sids = [x_[0] for x_ in con.execute("SELECT id FROM sessions WHERE household_id=?", (d_["id"],))]
                with app.app_context():
                    g.db, g.tx = con, True
                    try:
                        _keep_books(sids)
                    finally:
                        g.pop("tx", None)
                        g.pop("db", None)
                marks = ",".join("?" * len(sids)) or "NULL"
                con.execute("DELETE FROM audit WHERE household_id=? OR session_id IN (" + marks + ")", (d_["id"], *sids))
                if sids:
                    for tbl_ in ("doc_snapshots", "doc_snapshots_old"):
                        con.execute("DELETE FROM " + tbl_ + " WHERE (kind='agreement' AND ref IN (" + marks + ")) OR (kind='release' AND ref IN "
                                    "(SELECT id FROM documents WHERE session_id IN (" + marks + ")))", (*sids, *sids))
                    con.execute("DELETE FROM vault_keys WHERE session_id IN (" + marks + ")", tuple(sids))
                    import photostore as _ps
                    if con.execute("SELECT 1 FROM sqlite_master WHERE name='photo_files'").fetchone():
                        for s_ in sids:
                            for pr_ in con.execute("SELECT * FROM photo_files WHERE session_id=?", (s_,)).fetchall():
                                these_ += _ps.delete(con, pr_, commit=False)
                con.execute("UPDATE households SET referred_by=NULL WHERE referred_by=?", (d_["id"],))
                for c_ in ("payer_hid", "contact_hid"):
                    if c_ in ev_cols:
                        con.execute(f"UPDATE events SET {c_}=NULL WHERE {c_}=?", (d_["id"],))
                con.execute("DELETE FROM households WHERE id=?", (d_["id"],))
                con.execute("RELEASE redelete")
                destroy_.extend(these_)
            except Exception:
                con.execute("ROLLBACK TO redelete")
                con.execute("RELEASE redelete")
                app.logger.exception("re-deleting client %s after a restore", d_["id"])
                continue
            (FACES / f"{d_['id']}.jpg").unlink(missing_ok=True)
            n += 1
        if n:
            con.execute("INSERT INTO audit (at, action, who) VALUES (?,?,?)",
                        (stamp, f"Deleted again {n} client record(s) that had been deleted after this backup was made", "Lumen Aureum"))
        for d_ in held:
            con.execute("INSERT INTO audit (at, household_id, action, who) VALUES (?,?,?,?)",
                        (stamp, d_["id"], "Not deleted: an unsigned entry in deletions.json (from before 1.0.1) names this "
                         "client, and the file alone can't prove you asked for it. Today lists this client: if you did "
                         "delete them, apply the deletion there.", "Lumen Aureum"))
        if forged:
            con.execute("INSERT INTO audit (at, action, who) VALUES (?,?,?)",
                        (stamp, f"Ignored {len(forged)} entry(ies) in deletions.json that did not verify (changed outside "
                         "Lumen Aureum, or signed under a key this database does not have); no client was deleted for "
                         "them. They were moved to deletions.unverified.json.", "Lumen Aureum"))
            app.logger.warning("deletions.json: %d entry(ies) did not verify; set aside, nothing deleted", len(forged))
        con.commit()
        if destroy_:
            import photostore as _ps
            _ps.destroy_files(destroy_)
        if held or forged:
            _set_aside(held + forged)
        items = good + [_signed(key, d_) for d_ in adopted]
        if items != _read_deletions()[0]:
            _write_plain(DELETIONS_JSON, json.dumps(items))
    finally:
        con.close()
    return n + _redelete_sessions(sessions_)

def _redelete_sessions(entries):

    if not entries:
        return 0
    n = 0
    with app.test_request_context():
        g.tx = True
        for d_ in entries:
            s_ = q("SELECT id, household_id FROM sessions WHERE id=? AND created=?", (d_["id"], d_["created"]), one=True)
            if not s_:
                continue
            try:
                import photostore as _ps
                gone_ = []
                x("DELETE FROM vault_keys WHERE session_id=?", (s_["id"],))
                if q("SELECT 1 FROM sqlite_master WHERE name='photo_files'", one=True):
                    for pr_ in q("SELECT * FROM photo_files WHERE session_id=?", (s_["id"],)):
                        gone_ += _ps.delete(db(), pr_, commit=False)
                _delete_session_rows(s_["id"])
                audit("Deleted again a session that had been deleted after this backup was made", hid=s_["household_id"],
                      who="Lumen Aureum")
                db().commit()
                _ps.destroy_files(gone_)
                n += 1
            except Exception:
                db().rollback()
                app.logger.exception("re-deleting session %s after a restore", d_["id"])
    return n

@app.post("/client/<int:hid>/delete")
def client_delete(hid):

    if request.form.get("confirm", "").strip() != "DELETE":
        return redirect(url_for("client_view", hid=hid) + "?delete=nope")
    h_ = q("SELECT id, name, created FROM households WHERE id=?", (hid,), one=True) or abort(404)
    _start_client_delete(h_)
    session["notice"] = f"Deleting {h_['name']}: galleries come down and photos are destroyed first. The progress shows at the bottom of the page."
    return redirect(url_for("home"))

def _start_client_delete(h_):

    import jobs
    hid, created = h_["id"], h_["created"]

    def work(job):
        _delete_client(hid, created)
        return "Deleted the client record and everything about it."
    jobs.start(app, f"client-delete:{hid}", f"Deleting {h_['name']} and everything about them", work)

def _aside_items():
    try:
        items = json.loads(DELETIONS_ASIDE.read_text())
    except (OSError, ValueError):
        return []
    return items if isinstance(items, list) else []

def unconfirmed_deletions():

    out, seen, dismissed = [], set(), set()
    items = _aside_items()
    for d_ in items:
        if _is_client_entry(d_) and d_.get("dismissed"):
            dismissed.add((d_["id"], d_["created"]))
    for d_ in items:
        k_ = (d_["id"], d_["created"]) if _is_client_entry(d_) else None
        if not k_ or k_ in seen or k_ in dismissed:
            continue
        seen.add(k_)
        h_ = q("SELECT id, name, created FROM households WHERE id=? AND created=?", k_, one=True)
        if h_:
            at_ = d_.get("at") if isinstance(d_.get("at"), str) else None
            out.append(dict(id=h_["id"], created=h_["created"], name=h_["name"], at=at_, key=f"{h_['id']}|{h_['created']}"))
    return sorted(out, key=lambda r_: (r_["name"] or "").lower())

def _picked_unconfirmed():

    picks = set(request.form.getlist("pick"))
    return [r_ for r_ in unconfirmed_deletions() if r_["key"] in picks]

@app.get("/deletions/unconfirmed")
def unconfirmed_view():
    return render_template("unconfirmed.html", rows=unconfirmed_deletions())

@app.post("/deletions/unconfirmed/apply")
def unconfirmed_apply():
    rows = _picked_unconfirmed()
    for r_ in rows:
        _start_client_delete(r_)
    if rows:
        session["notice"] = (f"Deleting {len(rows)} client{'' if len(rows) == 1 else 's'}: galleries come down and photos "
                             "are destroyed first. The progress shows at the bottom of the page.")
    else:
        g.refused = True
        session["notice"] = "Those clients are no longer on the list, so nothing was deleted."
    return redirect(url_for("home"))

@app.post("/deletions/unconfirmed/keep")
def unconfirmed_keep():
    keys = {(r_["id"], r_["created"]) for r_ in _picked_unconfirmed()}
    if not keys:
        g.refused = True
        return redirect(url_for("home"))
    stamp = now_local()
    items = _aside_items()
    for d_ in items:
        if _is_client_entry(d_) and (d_["id"], d_["created"]) in keys:
            d_["dismissed"] = stamp
    _write_plain(DELETIONS_ASIDE, json.dumps(items))
    g.audit_note = f"{len(keys)} client{'' if len(keys) == 1 else 's'}"
    session["notice"] = "Kept. Those clients stay, and this won't ask about them again."
    return redirect(url_for("home"))

def _delete_client(hid, created):

    if not q("SELECT 1 FROM households WHERE id=?", (hid,), one=True):
        return
    sids = [r["id"] for r in q("SELECT id FROM sessions WHERE household_id=?", (hid,))]
    n_orders = app.config.get("print_orders_of") or (lambda s_: 0)
    orders_before = sum(n_orders(sid) for sid in sids)
    for sid in sids:
        if app.config.get("gallery_take_down"):
            try:
                app.config["gallery_take_down"](sid)
            except Exception:
                app.logger.exception("take-down on client delete %s", sid)
                raise RuntimeError("Could not take a gallery down; no photos or records were deleted. Check the web host.")
    if sum(n_orders(sid) for sid in sids) > orders_before:
        raise RuntimeError("A print order the client sent was collected as a gallery came down, so the client and their "
                           "photos were kept (their galleries are down). Look at the order, then delete the client again.")
    for n_, sid in enumerate(sids):
        if app.config.get("retention_purge_now"):
            try:
                app.config["retention_purge_now"](sid)
            except Exception:
                app.logger.exception("purge on client delete %s", sid)
                raise RuntimeError("Could not delete all of this client's photos" + (
                    f" (those of {n_} of their {len(sids)} sessions are gone)" if n_ else "")
                    + "; the client record was kept. Check the vault storage, then delete the client again.")
    _gallery_down_again(sids)
    import locks
    with locks.WRITES:
        _gallery_still_down(sids)
        _delete_client_records(hid, created, sids)

def _delete_client_records(hid, created, sids):

    g.tx = True
    try:
        _delete_client_rows(hid, created, sids)
        g.pop("tx", None)
        db().commit()
    except BaseException:
        g.pop("tx", None)
        db().rollback()
        raise
    (FACES / f"{hid}.jpg").unlink(missing_ok=True)
    _record_deletion(hid, created)

def _delete_client_rows(hid, created, sids):
    _keep_books(sids)
    marks = ",".join("?" * len(sids)) or "NULL"
    if sids:
        x("DELETE FROM vault_keys WHERE session_id IN (" + marks + ")", tuple(sids))
        x("DELETE FROM vault_objects WHERE session_id IN (" + marks + ") AND deleted IS NOT NULL", tuple(sids))
    if sids:
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

    if app.config.get("print_keep_books"):
        app.config["print_keep_books"](sids)
    for sid in sids:
        s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
        for e_ in tax_entries(sid):
            if e_["kind"] == "adjustment" and abs(round(e_["tax"], 2)) < 0.005:
                continue
            x("INSERT INTO books (received, amount, tax, type, kind, tax_parts) VALUES (?,?,?,?,?,?)",
              (e_["day"], e_["amount"], round(e_["tax"], 2), s_["type"] if s_ else "", e_["kind"],
               json.dumps(e_["parts"]) if e_["parts"] and set(e_["parts"]) != {LEGACY_TAX_NAME} else None))
        for e_ in q("SELECT date, amount, miles, rate FROM expenses WHERE session_id=?", (sid,)):

            x("INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (NULL,?,?,?,?,?)",
              (e_["date"], f"Session expense ({why})", e_["amount"], e_["miles"], e_["rate"]))

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
    elif not t["done"] and t["title"] == "Send retouched photos" and t["session_id"] and (
            (request.form.getlist("set") or ["1"])[-1] == "1") and (why_ := retouch_refused(t["session_id"])):
        session["notice"] = why_
        g.refused = True
        return redirect(safe_back(request.form.get("back")) or url_for("session_view", sid=t["session_id"]))
    else:
        want = (request.form.getlist("set") or [None])[-1]
        done_now = (want == "1") if want in ("0", "1") else not t["done"]
        if bool(t["done"]) == done_now:
            g.refused = True
        else:
            x("UPDATE tasks SET done=?, outcome=? WHERE id=?",
              (today().isoformat() if done_now else None, "done" if done_now else None, tid))
            if done_now and t["title"] == SEND_TASKS["invoice"] and t["session_id"]:
                _invoice_marked_sent(t["session_id"])
            if done_now and t["title"] == SEND_TASKS["quote"] and t["session_id"]:
                lq_ = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (t["session_id"],), one=True)
                if lq_:
                    _mark_quote_sent(lq_["id"], t["session_id"])
                quote_went(t["session_id"])
            if done_now and t["title"] == PLAN_RESIGN and t["session_id"]:

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
         (SELECT id FROM sessions WHERE event_id=? AND stage != 'lost')""", (today().isoformat(), request.form["title"], eid))
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))

@app.post("/close")
def close_step():
    f = request.form
    outcome = f.get("outcome", "done")
    if outcome not in OUTCOMES:
        abort(400)
    if (f.get("task") and not is_id(f["task"])) or (not f.get("task") and f.get("sid") and not is_id(f["sid"])):
        abort(400)
    if f.get("task") and outcome == "done":
        t_ = q("SELECT title, session_id FROM tasks WHERE id=?", (int(f["task"]),), one=True)
        why_ = retouch_refused(t_["session_id"]) if t_ and t_["title"] == "Send retouched photos" and t_["session_id"] else ""
        if why_:
            session["notice"] = why_
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("home"))
    if f.get("task"):
        x("UPDATE tasks SET done=?, outcome=? WHERE id=?", (today().isoformat(), outcome, int(f["task"])))
        if f.get("note_why"):
            x("UPDATE tasks SET outcome_note=? WHERE id=?", (f["note_why"], int(f["task"])))
    elif f.get("sid") and f.get("step") in AUTO:
        if f["step"] == "balance_paid" and outcome in ("na", "declined") and totals(int(f["sid"]))["due"] > 0.005:

            return _refuse(f"{money(totals(int(f['sid']))['due'])} is still owed. To stop asking for it, waive it "
                           "(a write-off). Nothing was changed.", safe_back(f.get("back")) or url_for("home"))
        x("""INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?,?,?,?)
             ON CONFLICT(session_id, step) DO UPDATE SET outcome=excluded.outcome, note=excluded.note, at=date('now','localtime')""",
          (int(f["sid"]), f["step"], outcome, f.get("note_why", "")))
        if f["step"] == "balance_paid" and outcome == "waived":
            write_off_balance(int(f["sid"]), f.get("note_why", ""))
        elif f["step"] == "balance_paid":
            unwaive_balance(int(f["sid"]))
    else:
        abort(400)
    return redirect(safe_back(f.get("back")) or url_for("home"))

@app.post("/session/<int:sid>/task")
def task_add(sid):
    f = request.form
    stage = f.get("stage") or session_full(sid)["stage"]
    x("INSERT INTO tasks (id, session_id, title, due, sort, stage, own) VALUES (?,?,?,?,999,?,1)",
      (next_id("tasks"), sid, f["title"], f.get("due") or None, stage))
    return redirect(safe_back(f.get("back")) or url_for("session_view", sid=sid))

@app.post("/doc/<int:did>")
def doc_save(did):
    doc = q("SELECT * FROM documents WHERE id=?", (did,), one=True) or abort(404)
    f = request.form
    if f.get("delete"):
        x("DELETE FROM documents WHERE id=?", (did,))
    else:
        status = f.get("status", doc["status"])
        if f.get("cycle") and f.get("from") and f["from"] != doc["status"]:
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("session_edit", sid=doc["session_id"]) + "#paperwork")
        if f.get("cycle"):
            order = ["needed", "sent", "signed"]
            status = order[(order.index(doc["status"]) + 1) % 3]
        if f.get("signed_on") and not _past_day(f["signed_on"]):
            session["notice"] = "A signing date can't be in the future. Nothing was changed."
            g.refused = True
            return redirect(safe_back(f.get("back")) or url_for("session_edit", sid=doc["session_id"]) + "#paperwork")
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
    return redirect(safe_back(f.get("back")) or url_for("session_edit", sid=doc["session_id"]) + "#paperwork")

@app.post("/session/<int:sid>/doc")
def doc_add(sid):

    f = request.form
    kind_ = f["kind"]
    if kind_ not in ("release", "agreement", "property release", "other"):
        abort(400)
    covers = " ".join((f.get("covers") or "").split())[:200]
    ck = ""
    if kind_ == "property release":
        kind_, ck = "release", "property"
        covers = covers or " ".join((f.get("signer") or "").split())[:200]
    elif kind_ == "release" and covers:
        ck = "minor" if f.get("covers_minor") else "guest"
    else:
        covers = ""
    about = None if ck else f.get("about_person", type=int)
    x("INSERT INTO documents (session_id, kind, about_person, signer, covers, covers_kind) VALUES (?,?,?,?,?,?)",
      (sid, kind_, about, f.get("signer", ""), covers, ck))
    return redirect(url_for("session_edit", sid=sid) + "#paperwork")

_ONCE = {}
_ONCE_LOCK = threading.Lock()
_ONCE_KEEP = 5000

def form_once():
    return secrets.token_urlsafe(12)

app.jinja_env.globals["form_once"] = form_once

def sent_twice():

    tok = (request.form.get("once") or "")[:64]
    if not tok:
        return False
    f = request.form

    key = json.dumps([tok] + sorted([k_, "\n".join(v_.strip()[:200] for v_ in f.getlist(k_))] for k_ in f
                                    if k_ not in ("once", "back")))
    with _ONCE_LOCK:
        if key in _ONCE:
            return True
    g.once_key = key
    return False

def _once_used():

    key = g.pop("once_key", None)
    if not key or g.get("refused"):
        return
    with _ONCE_LOCK:
        _ONCE[key] = True
        if len(_ONCE) > _ONCE_KEEP:
            for k_ in list(_ONCE)[:len(_ONCE) - _ONCE_KEEP]:
                del _ONCE[k_]

def _refuse_twice(back):
    session["notice"] = "That was sent twice. It was saved once."
    g.refused = True
    return redirect(back)

PAYMENT_KINDS = ("deposit", "balance", "tip", "refund", "other")
MONEY_CAP = 10_000_000

class TooMuch(ValueError):
    pass

def amount_in(v, default=0.0):

    from decimal import Decimal, ROUND_HALF_UP
    n = num(v, default, lo=float("-inf"), hi=float("inf"))
    if n is None:
        return None
    if abs(n) > MONEY_CAP:
        raise TooMuch(n)
    return float(Decimal(repr(float(n))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def day_in(v):

    s_ = str(v or "").strip()
    if not s_:
        return ""
    if not re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", s_):
        return None
    try:
        d_ = date.fromisoformat(s_)
    except ValueError:
        return None
    return d_.isoformat() if 1900 <= d_.year <= 2100 else None

def _refuse(notice, back):

    session["notice"] = notice
    session["refill"] = request.path
    g.refused = True
    return redirect(back)

MONEY_DAY_MIN = "2000-01-01"
TOO_EARLY = "That date is before 2000. Check the year. Nothing was recorded."
FUTURE_PAY = "A payment is recorded once it arrives: that date is in the future. Nothing was recorded."

BOOKS_CLOSED_KEY = "books_closed_by_hand"

def books_open_from():

    t_ = today()
    last = t_.year - 1
    if t_.month == 1 and str(last) != setting(BOOKS_CLOSED_KEY):
        return f"{last}-01-01"
    return f"{t_.year}-01-01"

def year_closed(year):

    return int(year) < int(books_open_from()[:4])

def closed_year_refusal(received, what="payment"):

    if received and received < books_open_from():
        y_, now_ = received[:4], books_open_from()[:4]
        if what == "expense":
            return (f"That date is in {y_}, and {y_} is closed. To add a {y_} expense found late, use Add a late "
                    f"expense on Finances for {y_}: it is recorded in {now_}, marked as from {y_}. Nothing was recorded.")
        return f"That date is in {y_}, and {y_} is closed: the app records nothing more in a closed year. Nothing was recorded."
    return ""

def closed_year_change_refusal(day, what="payment"):

    if day and year_closed(day[:4]):
        return (f"That {what} is in {day[:4]}, which is closed, so it stays as it is. Use Correct on it to record the "
                f"change in {books_open_from()[:4]}. Nothing was changed.")
    return ""

def correction_words(table, r_):

    keys_ = r_.keys()
    if "late_from" in keys_ and r_["late_from"]:
        return f"Late, from {r_['late_from'][:4]} (spent {long_date(r_['late_from'])})" + (f": {r_['note']}" if r_["note"] else "")
    if "corrects" not in keys_ or not r_["corrects"]:
        return ""
    o_ = q(f"SELECT * FROM {dbmod.ident(table)} WHERE id=?", (r_["corrects"],), one=True)
    if not o_:
        return "Correction" + (f": {r_['note']}" if r_["note"] else "")
    if table == "expenses":
        what_ = f"the {long_date(o_['date'])} expense {o_['label']}"
    elif table == "print_payments":
        what_ = f"the {long_date(o_['received'])} print order {o_['kind']}"
    else:
        what_ = f"the {long_date(o_['received'])} {'tip back' if o_['kind'] == 'tip' and o_['amount'] < 0 else o_['kind'] or 'payment'}"
    return f"Correction of {what_}" + (f": {r_['note']}" if r_["note"] else "")

CORRECT_WHY = "Say why, in a few words. Nothing was recorded."

def correction_in(f, back_day=None):

    day, bad_ = money_day_in(f.get("day"))
    day = day or today().isoformat()
    why_ = " ".join((f.get("why") or "").split())[:120]
    refused_ = bad_ or closed_year_refusal(day) or ("" if why_ else CORRECT_WHY)
    return day, why_, refused_

def money_day_in(v, what="payment"):

    d_ = day_in(v)
    if d_ is None:
        return None, BAD_DAY
    if d_ and d_ < MONEY_DAY_MIN:
        return None, TOO_EARLY
    if d_ and d_ > (today() + timedelta(days=1)).isoformat():
        return None, (FUTURE_PAY if what == "payment" else
                      "An expense is recorded once it is spent: that date is in the future. Nothing was recorded.")
    return d_, None

TOO_MUCH = f"Amounts over ${MONEY_CAP:,} aren't taken. Nothing was recorded."
BAD_DAY = "That date isn't a real date (year-month-day). Nothing was recorded."

@app.post("/session/<int:sid>/money")
def money_add(sid):
    f = request.form
    what = f["what"]
    back = safe_back(f.get("back")) or url_for("session_edit", sid=sid) + "#money"
    if sent_twice():
        return _refuse_twice(back)
    if what == "line" and quote_locked(sid):
        return _refuse(QUOTE_LOCKED, back)
    if what == "line":
        try:
            amt = amount_in(f["amount"])
        except TooMuch:
            return _refuse(TOO_MUCH, back)
        ensure_quote(sid)
        x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'custom', ?)",
          (sid, f["label"], amt, 1 if f.get("taxable") == "1" or not f.get("taxable_shown") else 0))
        reapply_discount(sid)
    elif what == "payment":
        kind = f.get("kind", "deposit")
        if kind == "tip_back":
            return _tip_back(sid, f, back)
        if kind not in PAYMENT_KINDS:
            return _refuse("Choose what the payment is: deposit, balance, tip, refund or other. Nothing was recorded.", back)
        try:
            amt = amount_in(f["amount"])
        except TooMuch:
            return _refuse(TOO_MUCH, back)
        if not amt or amt <= 0:
            return _refuse("Enter the amount as a positive number. For money going back, choose Refund.", back)
        received, why_ = money_day_in(f.get("received"))
        why_ = why_ or closed_year_refusal(received)
        if why_:
            return _refuse(why_, back)
        other_ = " ".join((f.get("note") or "").split())[:80] if f.get("method") == "other" else ""
        if f.get("method") == "other" and not other_:
            return _refuse("For Other, say what it was (a trade, for instance). Nothing was recorded.", back)
        t_ = totals(sid) if kind == "refund" else None
        if t_ is not None and amt > t_["paid"] + 0.005:
            return _refuse(f"A refund can't be more than what was paid toward the price ({money(max(t_['paid'], 0))})."
                           + (" To give a tip back, choose Tip back." if t_["tips"] > 0.005 else "")
                           + " Nothing was recorded.", back)
        if t_ is not None and t_["kept"] > 0.005 and amt > t_["refund_due"] + 0.005:
            session["notice"] = (f"Recorded. That's {money(amt - t_['refund_due'])} more than the refund due, so less of "
                                 f"the deposit is kept: {money(max(0.0, t_['kept'] - (amt - t_['refund_due'])))}.")
        pid_ = record_payment(sid, kind, amt, received or today().isoformat(), f.get("method", ""), (f.get("ref") or "").strip()[:40],
                              after=f.get("after_refund"))
        if other_:
            x("UPDATE payments SET note=? WHERE id=?", (other_, pid_))
        a_ = g.get("audit")
        if kind == "refund" and a_ and a_[0]:
            g.audit = (f"{a_[0]}. {after_refund_words(sid)}",) + tuple(a_[1:])
        if kind not in ("refund", "tip"):
            t2_ = totals(sid)
            if t2_["quoted"] > 0.005 and t2_["due"] < -0.005:
                session["notice"] = (f"Recorded. That's {money(-t2_['due'])} more than the balance, so the session now "
                                     f"shows a {money(-t2_['due'])} credit to give back or put toward something else. If the client said to keep "
                                     f"it, void it and record the balance and the extra as a Tip.")
            paying_means_yes(sid)
    elif what == "expense":
        refused_ = add_expense(sid, f)
        if refused_:
            return _refuse(refused_, back)
    return redirect(back)

@app.post("/session/<int:sid>/after-refund")
def session_after_refund(sid):

    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid)
    want = request.form.get("choice")
    if want not in AFTER_REFUND or not has_refund(sid):
        return _refuse("There is no refund on this session to change that for. Nothing was changed.", back)
    x("UPDATE sessions SET refund_release=? WHERE id=?", (want, sid))
    g.pop("tot_cache", None)
    return redirect(back)

def _tip_back(sid, f, back):

    try:
        amt = amount_in(f.get("amount"))
    except TooMuch:
        return _refuse(TOO_MUCH, back)
    if not amt or amt <= 0:
        return _refuse("Enter the amount as a positive number. Nothing was recorded.", back)
    received, why_ = money_day_in(f.get("received"))
    why_ = why_ or closed_year_refusal(received)
    if why_:
        return _refuse(why_, back)
    tips = totals(sid)["tips"]
    if amt > tips + 0.005:
        return _refuse(f"Only {money(max(tips, 0))} in tips was received, so no more than that can go back. For money "
                       "paid toward the price, choose Refund. Nothing was recorded.", back)
    record_payment(sid, "tip", -amt, received or today().isoformat(), f.get("method", ""), (f.get("ref") or "").strip()[:40])
    return redirect(back)

def record_payment(sid, kind, amt, received, method="", ref="", after=None):

    t_ = totals(sid)
    if kind == "refund":
        after = after if after in AFTER_REFUND else after_refund_default(t_["paid"] - amt)
        x("UPDATE sessions SET refund_release=? WHERE id=?", (after, sid))
    st_ = q("SELECT stage, lost_reason FROM sessions WHERE id=?", (sid,), one=True)
    pid_ = x("""INSERT INTO payments (session_id, kind, amount, received, method, ref, tax, taxable)
                VALUES (?,?,?,?,?,?,0,0)""", (sid, kind, -amt if kind == "refund" else amt, received, method, ref))
    if kind == "refund" and not _kept_case(st_):

        credit = round(amt - max(0.0, -t_["due"]), 2)
        if credit > 0.005:
            x("INSERT INTO refund_credits (payment_id, session_id, amount) VALUES (?,?,?)", (pid_, sid, credit))
    g.pop("tot_cache", None)
    if kind != "tip":
        settle_tax(sid, pid=pid_)
    if kind not in ("tip", "refund"):
        _follow_write_off(sid)
    sync_charges(sid, entered=pid_)
    return pid_

def _follow_write_off(sid):

    wo = written_off(sid)
    due = totals(sid)["due"]
    day = today().isoformat()
    if due < -0.005 and wo > 0.005:
        back_ = round(min(wo, -due), 2)
        x("INSERT INTO write_offs (session_id, amount, at, why) VALUES (?,?,?, 'paid')", (sid, -back_, day))
        audit(f"Reversed {money(back_)} of the write-off: the balance was paid after all", sid=sid)
    elif due > 0.005:
        undone = -q("""SELECT COALESCE(SUM(amount),0) v FROM write_offs WHERE session_id=? AND why IN ('paid','unpaid')""",
                    (sid,), one=True)["v"]
        if undone > 0.005:
            again = round(min(undone, due), 2)
            x("INSERT INTO write_offs (session_id, amount, at, why) VALUES (?,?,?, 'unpaid')", (sid, again, day))
            audit(f"Wrote off {money(again)} again: the payment that settled it was voided", sid=sid)
    g.pop("tot_cache", None)

def paying_means_yes(sid):

    x("""UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND title='Client said yes' AND done IS NULL""",
      (today().isoformat(), sid))
    t_ = totals(sid)
    if t_["quoted"] > 0.005 and t_["due"] <= 0.005:
        x("UPDATE tasks SET done=?, outcome='na' WHERE session_id=? AND title=? AND done IS NULL",
          (today().isoformat(), sid, SEND_TASKS["invoice"]))
    lock_tax_rate(sid)
    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    if s_ and s_["stage"] == "inquiry":
        set_stage(sid, "quoted")
        audit("Moved to Quoted (a payment came in)", sid=sid, who="Lumen Aureum")
    advance(sid)

def add_expense(sid, f):

    miles_typed = str(f.get("miles") or "").strip()
    miles = num(miles_typed, None, lo=float("-inf"), hi=float("inf")) if miles_typed else None
    if miles_typed and (miles is None or not 0 < miles <= 100000):
        return "Miles are a number above 0 (up to 100,000). Nothing was recorded."
    try:
        amt = amount_in(f.get("amount"))
    except TooMuch:
        return TOO_MUCH
    if amt is not None and amt < 0:
        return ("An expense is a positive amount. A purchase returned is recorded as money in, or by removing the "
                "expense. Nothing was recorded.")
    day, why_ = money_day_in(f.get("date"), "expense")
    late = None
    if not why_ and day and closed_year_refusal(day) and (f.get("late") == "1" or (sid and f.get("from_shoot") == "1")):

        late, day = day, today().isoformat()
        session["notice"] = f"Recorded in {day[:4]}, marked as from {late[:4]} ({late[:4]} is closed)."
    why_ = why_ or closed_year_refusal(day, "expense")
    if why_:
        return why_
    if not miles and not amt:
        return "Enter the amount, the miles, or both. Nothing was recorded."
    x("INSERT INTO expenses (session_id, date, label, amount, miles, rate, late_from) VALUES (?,?,?,?,?,?,?)",
      (sid, day or today().isoformat(), f["label"], amt, miles or 0, float(setting("mileage_rate") or 0) if miles else None,
       late))
    return None

@app.post("/money/close-year")
def money_close_year():

    t_ = today()
    last = t_.year - 1
    back = safe_back(request.form.get("back")) or url_for("money_view") + "#year"
    if str(last) != (request.form.get("year") or "") or year_closed(last):
        return _refuse(f"Only {last} can be closed, and only in January while it is still open. Nothing was changed.",
                       back)
    x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (BOOKS_CLOSED_KEY, str(last)))
    g.audit = (f"Closed {last} on Finances: nothing more can be dated in it", None, None, None, "")
    session["notice"] = f"{last} is closed."
    return redirect(back)

@app.post("/money/overhead")
def overhead_add():

    day = day_in(request.form.get("date")) or today().isoformat()
    back = safe_back(request.form.get("back")) or url_for("money_view", year=day[:4]) + "#overhead"
    if sent_twice():
        return _refuse_twice(back)
    refused_ = add_expense(None, request.form)
    if refused_:
        return _refuse(refused_, back)
    return redirect(back)

@app.post("/money/<what>/<int:rid>/correct")
def money_correct(what, rid):

    table = {"payment": "payments", "expense": "expenses"}.get(what) or abort(400)
    r = q("SELECT * FROM " + dbmod.ident(table) + " WHERE id=?", (rid,), one=True) or abort(404)
    f = request.form
    sid = r["session_id"]
    back = safe_back(f.get("back")) or (url_for("session_edit", sid=sid) + "#money" if sid else url_for("money_view") + "#overhead")
    if sent_twice():
        return _refuse_twice(back)
    day0 = r["received"] if table == "payments" else r["date"]
    if (table == "payments" and (r["voided"] or not sid)) or not day0 or not year_closed(day0[:4]):
        return _refuse(f"Only a {what} in a closed year is corrected this way; in an open year, "
                       f"{'void' if table == 'payments' else 'remove'} it and enter it again. Nothing was recorded.", back)
    fixes = q(f"SELECT * FROM {dbmod.ident(table)} WHERE corrects=?" + (" AND voided IS NULL" if table == "payments" else ""), (rid,))
    if table == "payments":
        net, net_m = round(r["amount"] + sum(c_["amount"] for c_ in fixes), 2), 0.0
    else:
        net = round((r["amount"] or 0) + sum(c_["amount"] or 0 for c_ in fixes), 2)
        net_m = round((r["miles"] or 0) + sum(c_["miles"] or 0 for c_ in fixes), 2)
    typed_ = str(f.get("should_be") or "").strip()
    try:
        should = amount_in(typed_, None) if typed_ or table == "payments" else abs(net)
    except TooMuch:
        return _refuse(TOO_MUCH, back)
    if should is None or should < 0:
        return _refuse("Enter what it should have been, as a positive amount (0 if it never happened). Nothing was recorded.", back)
    miles_typed = str(f.get("miles_should_be") or "").strip()
    m_should = num(miles_typed, None, lo=float("-inf"), hi=float("inf")) if miles_typed else net_m
    if m_should is None or not 0 <= m_should <= 100000:
        return _refuse("Miles are a number, 0 or more. Nothing was recorded.", back)
    day, why_, refused_ = correction_in(f)
    if refused_:
        return _refuse(refused_, back)
    if table == "payments":
        delta = round((-should if r["amount"] < 0 else should) - net, 2)
        if abs(delta) < 0.005:
            return _refuse(f"It already stands at {money(should)}. Nothing was recorded.", back)

        use_ = q("SELECT * FROM credit_uses WHERE refund_id=? OR payment_id=?", (rid, rid), one=True)
        other_ = q("SELECT * FROM payments WHERE id=?", (use_["payment_id"] if use_["refund_id"] == rid else use_["refund_id"],),
                   one=True) if use_ else None
        if r["kind"] != "tip" and totals(sid)["paid"] + delta < -0.005:

            return _refuse(f"That would leave less than nothing paid on this session: {money(max(0.0, -(totals(sid)['paid'] + delta)))} "
                           "of this money already went back as a refund or moved on as a credit toward another session. "
                           "Correct that refund or credit first, then this. Nothing was recorded.", back)
        if other_ and should > abs(r["amount"]) + 0.005:
            return _refuse(f"This is a credit that was used, {money(abs(r['amount']))} of it: it can be corrected down "
                           "(0 if it was never used), not up. To use more of a credit, use it on the session's Billing. "
                           "Nothing was recorded.", back)
        pid_ = _correction_payment(r, delta, day, why_)
        if other_:
            oid_ = _correction_payment(other_, -delta, day, why_)
            in_, out_ = (pid_, oid_) if use_["payment_id"] == rid else (oid_, pid_)
            x("INSERT INTO credit_uses (credit_id, session_id, amount, at, refund_id, payment_id) VALUES (?,?,?,?,?,?)",
              (use_["credit_id"], use_["session_id"],
               q("SELECT amount FROM payments WHERE id=?", (in_,), one=True)["amount"], day, out_, in_))
            audit(f"Corrected with it: the other half of the credit, on the {'canceled' if other_['kind'] == 'refund' else 'new'} "
                  f"session. Why: {why_}", sid=other_["session_id"])
        was = money(abs(net))
    else:
        da, dm = round(should - net, 2), round(m_should - net_m, 2)
        if abs(da) < 0.005 and abs(dm) < 0.005:
            return _refuse("That is what it already stands at. Nothing was recorded.", back)
        x("""INSERT INTO expenses (session_id, date, label, amount, miles, rate, corrects, note) VALUES (?,?,?,?,?,?,?,?)""",
          (sid, day, r["label"], da, dm, r["rate"], rid, why_))
        g.pop("tot_cache", None)
        was = money(net) + (f", {net_m:g} mi" if net_m else "")
    g.audit = (f"Corrected the {was} {what} from {nice(day0)} ({day0[:4]} is closed): recorded the change on "
               f"{nice(day)}. Why: {why_}", sid, None, None, "")
    session["notice"] = f"Recorded as a correction on {nice(day)}. {day0[:4]} stays as it was."
    return redirect(back)

def _correction_payment(r, delta, day, why_):

    sid = r["session_id"]
    pid_ = x("""INSERT INTO payments (session_id, kind, amount, received, method, ref, tax, taxable, note, corrects)
                VALUES (?,?,?,?,?, '', 0, 0, ?, ?)""", (sid, r["kind"], delta, day, r["method"] or "", why_, r["id"]))
    cr_ = q("SELECT amount FROM refund_credits WHERE payment_id=?", (r["id"],), one=True)
    if r["kind"] == "refund" and cr_ and r["amount"]:
        part_ = round(cr_["amount"] * delta / r["amount"], 2)
        if abs(part_) >= 0.005:
            x("INSERT INTO refund_credits (payment_id, session_id, amount) VALUES (?,?,?)", (pid_, sid, part_))
    g.pop("tot_cache", None)
    if r["kind"] != "tip":
        settle_tax(sid, pid=pid_)
    if r["kind"] not in ("tip", "refund"):
        _follow_write_off(sid)
    sync_charges(sid)
    return pid_

def _void_payment(r, void_now):

    rid = r["id"]

    x("UPDATE payments SET voided=? WHERE id=?", (today().isoformat() if void_now else None, rid))
    g.pop("tot_cache", None)
    _void_corrections(rid, void_now)

    if r["session_id"]:
        if not void_now and r["tax"] is None and r["kind"] != "tip":
            settle_tax(r["session_id"], pid=rid)
        money_changed(r["session_id"], "voided")
        if r["kind"] not in ("tip", "refund"):
            _follow_write_off(r["session_id"])
            sync_charges(r["session_id"])

@app.post("/money/<what>/<int:rid>/delete")
def money_delete(what, rid):
    table = {"line": "lines", "payment": "payments", "expense": "expenses"}.get(what) or abort(400)
    r = q("SELECT * FROM " + dbmod.ident(table) + " WHERE id=?", (rid,), one=True) or abort(404)
    back_ = safe_back(request.form.get("back")) or (stage_url(r["session_id"]) + "#money" if r["session_id"]
                                                    else url_for("money_view") + "#overhead")
    if table in ("payments", "expenses"):
        why_ = closed_year_change_refusal(r["received"] if table == "payments" else r["date"], what)
        if why_:
            return _refuse(why_, back_)
    if table == "payments":

        want = request.form.get("set")
        void_now = (want == "void") if want in ("void", "restore") else not r["voided"]

        pair_ = q("""SELECT p.* FROM credit_uses u JOIN payments p ON p.id = CASE WHEN u.refund_id=? THEN u.payment_id
                     ELSE u.refund_id END WHERE u.refund_id=? OR u.payment_id=?""", (rid, rid, rid), one=True)
        if pair_ and bool(pair_["voided"]) != void_now:
            why_ = closed_year_change_refusal(pair_["received"], what)
            if why_:
                return _refuse(why_, back_)
        if bool(r["voided"]) == void_now:
            g.refused = True
        else:
            for r_ in [r] + ([pair_] if pair_ and bool(pair_["voided"]) != void_now else []):
                _void_payment(r_, void_now)
    else:
        if table == "lines" and r["session_id"] and quote_locked(r["session_id"]):
            return _refuse(QUOTE_LOCKED, safe_back(request.form.get("back")) or stage_url(r["session_id"]) + "#billing")
        x("DELETE FROM " + dbmod.ident(table) + " WHERE id=?", (rid,))
    if table == "lines":
        reapply_discount(r["session_id"])
    if not r["session_id"]:
        return redirect(safe_back(request.form.get("back")) or url_for("money_view") + "#overhead")
    return redirect(safe_back(request.form.get("back")) or url_for("session_edit", sid=r["session_id"]) + "#money")

def qty_in(v):

    typed = str(v or "").strip()
    return num(typed, None, lo=float("-inf"), hi=float("inf")) if typed else 1.0

@app.post("/session/<int:sid>/quote/fee")
def quote_fee(sid):

    back = safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#money"
    if sent_twice():
        return _refuse_twice(back)
    if quote_locked(sid):
        return _refuse(QUOTE_LOCKED, back)
    qty = qty_in(request.form.get("qty"))
    if qty is None or not 0.5 <= qty <= 100000:
        return _refuse("A quantity is a number from 0.5 to 100,000. Nothing was added.", back)
    ensure_quote(sid)
    if not request.form.get("fee_key"):
        session["notice"] = "Pick a line from the list first, then press Add."
        g.refused = True
        return redirect(back)
    if request.form.get("fee_key") == "custom":
        label = (request.form.get("label") or "").strip()[:200]
        try:
            each = amount_in(request.form.get("amount"), None)
            if each is not None and abs(each * qty) > MONEY_CAP:
                raise TooMuch(each)
        except TooMuch:
            return _refuse(TOO_MUCH, back)
        if not label or each is None:
            session["notice"] = "For a line not on the price list, say what it is and its price."
            g.refused = True
            return redirect(back)
        x("INSERT INTO lines (session_id, label, amount, kind, qty, unit, taxable) VALUES (?,?,?, 'custom', ?,?,?)",
          (sid, label if qty == 1 else f"{label} x {qty:g}", round(each * qty, 2), qty, each,
           1 if request.form.get("taxable") == "1" or not request.form.get("taxable_shown") else 0))
        reapply_discount(sid)
        session["notice"] = f"Added: {label if qty == 1 else f'{label} x {qty:g}'}"
        return redirect(back)
    sch = schedule(q("SELECT schedule_id FROM quotes WHERE session_id=?", (sid,), one=True)["schedule_id"])
    item = next((i for i in sch["items"] if i["key"] == request.form.get("fee_key")), None) or abort(400)
    qty = max(qty, float(item.get("min") or 0))
    unit, amt = fee_amount(item, qty)
    label = item["label"] if qty == 1 else f"{item['label']} x {qty:g}"
    x("INSERT INTO lines (session_id, label, amount, kind, fee_key, qty, unit, taxable, untaxed) VALUES (?,?,?,?,?,?,?,?,?)",
      (sid, label, amt, "passthrough" if item.get("passthrough") else "fee", item["key"], qty, unit, fee_taxable(item),
       fee_untaxed(item)))
    reapply_discount(sid)
    session["notice"] = f"Added: {label}"
    return redirect(back)

PRICE_STAGES = ("inquiry", "quoted", "booked", "shot", "delivered")

@app.post("/line/<int:lid>/qty")
def line_qty(lid):

    l_ = q("SELECT * FROM lines WHERE id=?", (lid,), one=True) or abort(404)
    s_ = q("SELECT stage FROM sessions WHERE id=?", (l_["session_id"],), one=True)
    back = safe_back(request.form.get("back")) or stage_url(l_["session_id"]) + "#billing"
    if s_["stage"] not in PRICE_STAGES or l_["kind"] in ("discount", "tax") or not l_["unit"]:
        return redirect(back)
    if quote_locked(l_["session_id"]):
        return _refuse(QUOTE_LOCKED, back)
    qty = qty_in(request.form.get("qty"))
    if qty is None or not 0.5 <= qty <= 100000:
        return _refuse("A quantity is a number from 0.5 to 100,000. To drop the line, remove it (the x beside it). Nothing was "
                       "changed.", back)
    base = re.sub(r" x [0-9.]+$", "", l_["label"])
    x("UPDATE lines SET qty=?, amount=?, label=? WHERE id=?",
      (qty, round(l_["unit"] * qty, 2), base if qty == 1 else f"{base} x {qty:g}", lid))
    reapply_discount(l_["session_id"])
    return redirect(back)

def fee_taxable(item):

    return 1 if item.get("taxable", True) else 0

def _fee_taxes_posted(name, it, single=None):

    keys = [t_["key"] for t_ in taxes_list()]
    if len(keys) <= 1:
        return dict(taxable=bool(request.form.get(single or name)), untaxed=list(it.get("untaxed") or []))
    on = [k_ for k_ in keys if request.form.get(f"{name}_{k_}")]
    return dict(taxable=bool(on), untaxed=[k_ for k_ in keys if k_ not in on] if on else [])

def fee_untaxed(item):

    return ",".join(str(k_) for k_ in (item.get("untaxed") or []) if k_)

@app.post("/session/<int:sid>/quote/exempt")
def quote_exempt(sid):
    if quote_locked(sid):
        return _refuse(QUOTE_LOCKED, safe_back(request.form.get("back")) or stage_url(sid) + "#billing")
    if request.form.get("tax_key"):
        if not set_session_tax(sid, request.form["tax_key"], bool(request.form.get("tax_on"))):
            abort(400)
    else:
        x("UPDATE sessions SET tax_exempt=? WHERE id=?", (1 if request.form.get("tax_exempt") else 0, sid))
    reapply_tax(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#money")

@app.post("/session/<int:sid>/trade-copies")
def trade_copies_set(sid):

    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    x("UPDATE sessions SET trade_copies=? WHERE id=?", (1 if request.form.get("trade_copies") else 0, sid))
    return redirect(safe_back(request.form.get("back")) or stage_url(sid) + "#paperwork")

@app.post("/session/<int:sid>/quote/discount")
def quote_discount(sid):
    if quote_locked(sid):
        return _refuse(QUOTE_LOCKED, safe_back(request.form.get("back")) or stage_url(sid) + "#billing")
    ensure_quote(sid)
    try:
        pct = num(request.form.get("pct"), lo=0, hi=100)
    except ValueError:
        abort(400)
    x("UPDATE quotes SET discount_pct=?, discount_reason=? WHERE session_id=?",
      (pct, request.form.get("reason", ""), sid))
    reapply_discount(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#money")

@app.post("/session/<int:sid>/quote/requote")
def quote_requote(sid):

    if quote_locked(sid):
        return _refuse(QUOTE_LOCKED, safe_back(request.form.get("back")) or stage_url(sid) + "#billing")
    sch = current_schedule()
    by_key = {i["key"]: i for i in sch["items"]}
    for ln in q("SELECT * FROM lines WHERE session_id=? AND kind IN ('fee','passthrough') AND fee_key != ''", (sid,)):
        item = by_key.get(ln["fee_key"])
        if item:
            unit, amt = fee_amount(item, ln["qty"] or 1)
            x("UPDATE lines SET unit=?, amount=?, taxable=?, untaxed=? WHERE id=?",
              (unit, amt, fee_taxable(item), fee_untaxed(item), ln["id"]))
    x("UPDATE quotes SET schedule_id=?, created=?, expires=? WHERE session_id=?",
      (sch["id"], now_local(), (today() + timedelta(days=quote_term())).isoformat(), sid))
    reapply_discount(sid)
    return redirect(safe_back(request.form.get("back")) or url_for("session_edit", sid=sid) + "#money")

def html_pdf(html, name):

    from flask import Response
    import weasyprint
    page = weasyprint.CSS(string="@page { size: Letter; margin: 0.5in; }")
    import safe_media
    pdf = weasyprint.HTML(string=html, base_url=str(ROOT), url_fetcher=safe_media.pdf_fetcher()).write_pdf(stylesheets=[page])
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

def file_slug(*parts):

    out = "-".join(filter(None, (re.sub(r"[^a-z0-9]+", "-", str(p_ or "").lower()).strip("-") for p_ in parts)))
    return out[:90] or "document"

def client_slug(sid):
    r_ = q("SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True)
    return r_["name"] if r_ else ""

def long_date(iso):
    return d(iso[:10]).strftime("%B %-d, %Y") if iso else ""

def subjects_of(sid):
    return [r["name"] for r in q("""SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id
                                   WHERE sp.session_id=? ORDER BY p.name COLLATE NOCASE""", (sid,))]

def oxford(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else (" and ".join(xs) if len(xs) == 2 else (", ".join(xs[:-1]) + ", and " + xs[-1]) if xs else "")

def _live_quote(sid):

    s = session_full(sid)
    lines = [dict(label=l["label"], amount=l["amount"], kind=l["kind"])
             for l in q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sid,))]
    t = totals(sid)
    return s, lines, t, quote_data(sid, s, lines, t)

def _quote_unchanged(sid):

    last = q("SELECT * FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    if not last or (last["expires"] or "") < today().isoformat():
        return None
    _s, lines, _t, data = _live_quote(sid)
    try:
        return last if lines and json.loads(last["data"]) == json.loads(json.dumps(data)) else None
    except ValueError:
        return None

@app.post("/session/<int:sid>/quote/send")
def quote_send(sid):

    ensure_quote(sid)
    s, lines, t, data = _live_quote(sid)
    if not lines:
        abort(400)
    shown = request.form.get("total")
    by_script = request.headers.get("X-Quote-Fetch") == "1"
    if shown and abs(num(shown, 0) - t["quoted"]) > 0.005:
        session["notice"] = f"The quote changed a moment ago (it is now {money(t['quoted'])}). Check it, then prepare it again."
        g.refused = True
        return ({"ok": False}, 409) if by_script else redirect(stage_url(sid) + "#billing")
    same = _quote_unchanged(sid)
    if same:
        g.refused = True
        return {"ok": True, "url": url_for("quote_doc", vid=same["id"])} if by_script else redirect(url_for("quote_doc", vid=same["id"]))
    vid = _number_quote(sid, data)
    g.audit_note = f"quote {quote_no(vid)}, {money(t['quoted'])}"
    g.audit_data = json.dumps({"quote_id": vid})
    return {"ok": True, "url": url_for("quote_doc", vid=vid)} if by_script else redirect(url_for("quote_doc", vid=vid))

def _number_quote(sid, data):

    sent = now_local()
    expires = (today() + timedelta(days=quote_term())).isoformat()
    qt = q("SELECT * FROM quotes WHERE session_id=?", (sid,), one=True)
    for _ in range(5):
        try:
            vid = x("INSERT INTO quote_versions (id, session_id, at, schedule_id, expires, data, number) VALUES (?,?,?,?,?,?,?)",
                    (next_quote_number(), sid, sent, qt["schedule_id"], expires, json.dumps(data), next_quote_doc_number(sent)))
            break
        except sqlite3.IntegrityError:
            continue
    else:
        abort(503)
    x("UPDATE quotes SET sent=?, expires=? WHERE session_id=?", (sent, expires, sid))
    return vid

@app.post("/session/<int:sid>/quote/replace")
def quote_replace(sid):

    s_ = session_full(sid)
    back = safe_back(request.form.get("back")) or stage_url(sid) + "#billing"
    if s_["stage"] in ("inquiry", "quoted", "closed", "lost") or s_["event_id"]:
        return _refuse("Only a booked quote is replaced; before booking, change the quote itself. Nothing was changed.", back)
    if replacing(sid):
        g.refused = True
        return redirect(back)
    t_ = totals(sid)
    lq_ = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    lines_ = [dict(label=l_["label"], amount=l_["amount"], kind=l_["kind"])
              for l_ in q("SELECT * FROM lines WHERE session_id=? ORDER BY id", (sid,))]
    head_ = plan_head(sid)
    docs_ = [dict(r_) for r_ in q("SELECT id, status, sent, signed FROM documents WHERE session_id=? AND kind='agreement'", (head_,))]
    x("""INSERT INTO quote_replacements (session_id, at, old_vid, old_total, old_lines, agreement) VALUES (?,?,?,?,?,?)""",
      (sid, now_local(), lq_["id"] if lq_ else None, round(t_["quoted"], 2), json.dumps(lines_), json.dumps(docs_)))
    signed_ = [d_ for d_ in docs_ if d_["status"] == "signed"]
    if docs_:
        x("UPDATE documents SET status='needed', sent=NULL, signed=NULL WHERE session_id=? AND kind='agreement'", (head_,))
    audit((f"Canceled the booked quote{' ' + quote_no(lq_['id']) if lq_ else ''} ({money(t_['quoted'])}) to replace it: kept in History"
           if lines_ else "Started a quote for a session booked without one")
          + (f"; the agreement signed {nice(signed_[0]['signed'])} stays on file, and the new quote needs a fresh signature"
             if signed_ and signed_[0]["signed"] else "; the new quote needs the agreement signed" if docs_ else "")
          + (f"; {money(t_['paid'])} paid carries over" if t_["paid"] > 0.005 else ""), sid=sid)
    session["notice"] = "The quote can be changed now. When they say yes to the new one, press Book it."
    return redirect(back)

app.config.setdefault("quiet_endpoints", set()).add("quote_replace")

def quote_rebook(sid, s_):

    rp_ = replacing(sid)
    if not rp_:
        return False
    if s_["stage"] in ("closed", "lost"):
        session["notice"] = (f"This session is {C.STAGE_LABELS.get(s_['stage'], s_['stage'])}: reopen it before booking a "
                             "new quote. Nothing was changed.")
        g.refused = True
        return False
    ensure_quote(sid)
    _s, lines, t_, data = _live_quote(sid)
    if not lines:
        session["notice"] = "The new quote has no lines yet. Add them first, then press Book it."
        g.refused = True
        return False
    same = _quote_unchanged(sid)
    vid = same["id"] if same and same["id"] != rp_["old_vid"] and (same["at"] or "") >= rp_["at"] else _number_quote(sid, data)
    _mark_quote_sent(vid, sid)
    x("UPDATE quote_replacements SET new_vid=?, booked_at=? WHERE id=?", (vid, now_local(), rp_["id"]))
    x("UPDATE sessions SET deposit_booked=NULL WHERE id=?", (sid,))
    g.pop("tot_cache", None)
    lock_deposit(sid)
    _rebook_write_off(sid)
    t2_ = totals(sid)
    audit(f"Booked the new quote {quote_no(vid)} ({money(t2_['quoted'])})"
          + (f", replacing the canceled one ({money(rp_['old_total'])})" if json.loads(rp_["old_lines"] or "[]") else "")
          + (f"; deposit {money(t2_['deposit_req'])}, {money(t2_['deposit_short'])} still to pay" if t2_["deposit_short"] > 0.005 else ""),
          sid=sid)
    session["notice"] = f"Booked the new quote ({money(t2_['quoted'])})"
    return True

def _rebook_write_off(sid):

    wo, due = written_off(sid), totals(sid)["due"]
    if due < -0.005 and wo > 0.005:
        back_ = round(min(wo, -due), 2)
        x("INSERT INTO write_offs (session_id, amount, at, why) VALUES (?,?,?, 'repriced')", (sid, -back_, today().isoformat()))
        g.pop("tot_cache", None)
        sync_charges(sid)
        audit(f"Reversed {money(back_)} of the write-off: the new quote needs less than was waived", sid=sid)
    if totals(sid)["due"] > 0.005 and q("SELECT 1 FROM step_closes WHERE session_id=? AND step='balance_paid' AND outcome != 'done'",
                                        (sid,), one=True):
        x("DELETE FROM step_closes WHERE session_id=? AND step='balance_paid'", (sid,))
        audit(f"Paid in full is open again: the new quote leaves {money(totals(sid)['due'])} to pay", sid=sid)

def nothing_to_pay(sid, t=None):

    t = t or totals(sid)
    return (t["quoted"] <= 0.005 and abs(t["paid"]) <= 0.005 and t["refund_due"] <= 0.005 and t["written_off"] <= 0.005
            and bool(q("SELECT 1 FROM lines WHERE session_id=?", (sid,), one=True) or free_by_nature(sid)))

def free_by_nature(sid):

    s_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
    return bool(s_) and C.TYPE_KIND.get(s_["type"]) == "personal"

def billed_on_plan(sid, plan_of=_NONE):

    if plan_of is _NONE:
        r_ = q("SELECT plan_of FROM sessions WHERE id=?", (sid,), one=True)
        plan_of = r_["plan_of"] if r_ else None
    return bool(plan_of) and not q("SELECT 1 FROM lines WHERE session_id=?", (sid,), one=True) and not q(
        "SELECT 1 FROM payments WHERE session_id=? AND voided IS NULL", (sid,), one=True)

app.jinja_env.globals["billed_on_plan"] = lambda sid: billed_on_plan(sid)
app.jinja_env.globals["nothing_to_pay"] = lambda sid, t=None: nothing_to_pay(sid, t)
app.jinja_env.globals["invoice_number"] = lambda sid: (lambda r_: r_["number"] if r_ else "")(
    q("SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True))

def quote_terms(s, t):

    terms = [list(x_) for x_ in C.build_terms(s["type"], setting("business_name"))]
    if t["quoted"] > 0.005:
        return terms
    return nothing_to_pay_terms(terms)

MONEY_WORDS = re.compile(r"\$\s?\d|\bdeposit|\bbalance\b|\brefund|\bpaid\b|\bpay\b|\bfees?\b|\bcharged?\b|\bat cost\b|\bper reschedule\b|\bmileage rate\b", re.I)
NO_COST = re.compile(r"\bno charge\b|\bfree (?:reschedule|of charge)\b", re.I)

def nothing_to_pay_terms(terms):

    out = []
    for h_, v_ in terms:
        if h_ == "Payment":
            continue
        if h_ == "Booking":
            out.append(["Booking", C.dw("There is nothing to pay for this session: no deposit and no balance.")])
            continue
        v_ = re.sub(r"\s*,?\s*once (?:the balance is|you have) paid(?: in full)?", "", v_)
        dep_ = re.escape(C.clean_deposit_word(C.deposit_word() or "") or "deposit")
        kept = [x_ for x_ in re.split(r"(?<=[.!?])\s+", v_.strip()) if x_ and (not (MONEY_WORDS.search(x_) or re.search(r"\b" + dep_, x_, re.I)) or (
                    NO_COST.search(x_) and not re.search(r"\$\s?\d|\bdeposit|\b" + dep_, x_, re.I)))]
        if kept:
            out.append([h_, " ".join(kept)])
    return out

def quote_data(sid, s, lines, t):

    lic_ = license_words(s) if s["type"] in C.LICENSE_TYPES and business_client(s) else []
    inc_ = included_taxes(sid)
    return dict(**({"license_terms": " ".join(lic_)} if lic_ else {}), **({"included": inc_} if inc_ else {}), lines=lines, total=t["quoted"], deposit=t["deposit_req"], client=s["household"], type_label=C.doc_label(s["type"]),
                people=oxford(subjects_of(sid)) if s["type"] in C.SUBJECT_TYPES else "", type=s["type"], shoot_date=s["shoot_date"],
                photographer=setting("photographer_name"), business=setting("business_name"),
                terms=quote_terms(s, t), editing=[[h_, list(p_)] for h_, p_ in C.build_editing(s["type"])],
                client_company=company_of(s["household_id"]),
                net_days=C.BALANCE_NET_DAYS, balance_rule=balance_rule(s["type"], sid, "quote"), license=s["type"] in C.LICENSE_TYPES,
                **({"schedule": sch_} if (sch_ := payment_schedule(sid, t, booked_only=True)) else {}))

@app.get("/session/<int:sid>/quote/preview")
def quote_preview(sid):

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
             back=stage_url(sid), reviewed=doc_reviewed("terms", v["type"]), approve=dict(doc="terms", type=v["type"]), review=review_url("terms", v["type"]))
    return render_template("quote_doc.html", v=v, preview=True, send=None, total_shown="%.2f" % t["quoted"])

SEND_TASKS = {"quote": "Send quote", "details": "Send session details", "invoice": "Send invoice"}

def send_ctx(sid, kind, subject, lines, vid=None):

    out_ = _send_ctx(sid, kind, subject, lines)
    if vid is not None:
        on_ = quote_sent_on(vid)
        out_.update(task=None, sent=nice(on_) if on_ else "", mark_url="" if on_ else url_for("quote_mark_sent", vid=vid))
    return out_

def _send_ctx(sid, kind, subject, lines, to=None):

    from urllib.parse import quote as _qu
    s_ = q("SELECT household_id, type FROM sessions WHERE id=?", (sid,), one=True)
    couple = [] if (to and to.get("email")) or not s_ else couple_of(s_["household_id"], s_["type"])
    both = [p_ for p_ in couple if (p_["email"] or "").strip()]
    who = to if to and to.get("email") else next(iter(client_people(s_["household_id"], s_["type"], email=True)), None) if s_ else None
    ppl = [p_ for p_ in q("SELECT * FROM people WHERE household_id=? ORDER BY id", (s_["household_id"],))
           if not contact_only(p_, s_["type"])] if s_ else []
    reach = household_contact(ppl)
    if both:
        who = dict(name=" and ".join((p_["name"].split() or [""])[0] for p_ in both if p_["name"]),
                   email=",".join(p_["email"].strip() for p_ in both))
        reach = household_contact(couple) or reach
    phone_ = re.sub(r"[^0-9+]", "", (reach["phone"] or "") if reach else "")
    first = (who["name"] if both else (who["name"].split() or [""])[0]) if who and who["name"] else ""
    sign = setting("photographer_name") or setting("business_name")
    body = "\n\n".join([f"Hi {first}," if first else "Hello,"] + lines + [f"Thank you,\n{sign}" if sign else "Thank you"])
    step_ = SEND_TASKS.get(kind, "")
    task = q("SELECT id FROM tasks WHERE session_id=? AND title=? AND done IS NULL", (sid, step_), one=True) if step_ else None
    sent = q("""SELECT done FROM tasks WHERE session_id=? AND title=? AND done IS NOT NULL
                AND COALESCE(outcome, 'done') = 'done' ORDER BY done DESC""", (sid, step_), one=True) if step_ else None
    sms_body = lines[0] if lines else subject
    words_ = [r_["gallery_codeword"] for r_ in q("SELECT gallery_codeword FROM sessions WHERE household_id=?",
                                                 (s_["household_id"],))] if s_ else []
    subject, body, sms_body = (drop_codewords(t_, words_) for t_ in (subject, body, sms_body))
    return dict(mailto=f"mailto:{_qu(who['email'] if who else '', safe='@,')}?subject={_qu(subject)}&body={_qu(body)}",
                to=(who["email"] or "").replace(",", ", ") if who else "", sid=sid, phone=(reach["phone"] if reach else ""),
                sms=f"sms:{phone_}?&body={_qu(sms_body)}" if len(phone_) >= 7 else "", task=task["id"] if task else None, sent=nice(sent["done"]) if sent and not task else "", back=request.full_path.rstrip("?"))

def how_to_pay_line():

    methods = C._or_list(C.payment_methods())
    details = " ".join((C.pay_details() or "").split()).rstrip(".")
    return ((f"You can pay by {methods}." if methods else "Ask me how to pay.")
            + (f" {details}." if details else ""))

def _for_what(label, when=""):
    return (label or "your session") + (f", {when}" if when else "")

app.config.setdefault("quiet_endpoints", set()).update({"photos_status", "photos_upload"})

_AUDIT_UPLOAD = threading.Lock()

def _audit_upload(sid, row):

    with _AUDIT_UPLOAD:
        _audit_upload_locked(sid, row)

def file_name_order(name):

    return [(0, int(p_), "") if p_.isdigit() else (1, 0, p_.casefold()) for p_ in re.split(r"(\d+)", name or "") if p_]

def _audit_upload_locked(sid, row):
    import photostore
    kind = row["kind"]
    what = photostore.KINDS[kind].lower()
    one = what[:-1]
    route_ = f"photos-batch:{kind}"
    last = q("SELECT id, at, route, data FROM audit WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    batch = None
    if last and last["route"] == route_:
        try:
            batch = json.loads(last["data"] or "{}")
            fresh = datetime.now(TZ).replace(tzinfo=None) - datetime.strptime(last["at"], "%Y-%m-%d %H:%M:%S") <= timedelta(minutes=10)
        except (ValueError, TypeError):
            batch, fresh = None, False
        if not fresh or not isinstance(batch, dict):
            batch = None
    names = (batch or {}).get("names", []) + [row["name"]]
    n, size = len(names), int((batch or {}).get("bytes", 0)) + int(row["size"] or 0)
    mb = f"{size / 1048576:.1f} MB"
    label = ("RAWs" if kind == "raw" else what) if n > 1 else one.replace("raw", "RAW")
    text = (f"Uploaded {n} {label} ({mb}, sealed)" if n > 1 else f"Uploaded {label} {row['name']} (sealed)")
    named_ = sorted(names, key=file_name_order)
    note = "" if n == 1 else ", ".join(named_[:3]) + (f" and {n - 3} more" if n > 3 else "")
    data = json.dumps(dict(names=names[-500:], bytes=size))
    if batch is not None:
        x("UPDATE audit SET action=?, note=?, data=?, at=? WHERE id=?",
          (text, note, data, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), last["id"]))
    else:
        audit(text, sid=sid, note=note, route=route_, data=data)

@app.get("/session/<int:sid>/photos/status")
def photos_status(sid):

    import photostore
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    from urllib.parse import unquote
    blocked = photos_blocked(sid)
    if blocked:
        return {"ok": False, "message": blocked}, 409
    con = connect()
    try:
        from vault import keys as VK
        r = photostore.start(con, sid, request.args.get("kind", ""), unquote(request.headers.get("X-File-Name", "")),
                             request.args.get("size", type=int), master_pub=VK.master_pub(con), label=_photo_label(sid))
    except photostore.UploadError as e:
        return {"ok": False, "message": str(e)}, 400
    except OSError as e:
        app.logger.error("upload start failed: %s", e)
        return {"ok": False, "message": "The photos folder is full or can't be written to. Free some space (or check its "
                                        "permissions), then drop the file again."}, 507
    finally:
        con.close()
    return {"ok": True, "received": r["received"], "piece": photostore.PIECE}

def photos_blocked(sid):

    import locks
    if q("SELECT 1 FROM sqlite_master WHERE name='retention'", one=True) and \
            q("SELECT 1 FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (sid,), one=True):
        return "This session's photos were deleted at the end of their keeping time; nothing more can be added."
    if int(sid) in locks.SEALING:
        return "This session's photos are being sealed or deleted right now. Try again in a few minutes."
    return ""

IMAGE_KINDS = ("export", "final")
_NOT_AN_IMAGE = "{name} isn't a JPEG or PNG image that opens, so it wasn't kept. {what} take the finished images."

def _image_head(b):
    return b.startswith(b"\xff\xd8\xff") or b.startswith(b"\x89PNG\r\n\x1a\n")

class _Prefixed:

    def __init__(self, head, rest):
        self.head, self.rest = head, rest

    def read(self, n=-1):
        if self.head:
            out = self.head if n < 0 or n >= len(self.head) else self.head[:n]
            self.head = self.head[len(out):]
            return out
        return self.rest.read(n)

def _decodes(photostore, con, fid):

    import io
    from PIL import Image
    row = con.execute("SELECT * FROM photo_files WHERE id=?", (fid,)).fetchone()
    if row["size"] > 512 << 20:
        return True
    try:
        with Image.open(io.BytesIO(photostore.plain_bytes(row))) as im:
            if im.format not in ("JPEG", "PNG"):
                return False
            if im.format == "JPEG":
                im.draft("RGB", (256, 256))
            im.load()
        return True
    except Image.DecompressionBombError:
        return True
    except Exception:
        return False

def store_photo(sid, kind, name, data):

    import io as _io
    import photostore
    from vault import keys as VK
    if not data or not _image_head(data[:8]):
        raise photostore.UploadError(_NOT_AN_IMAGE.format(name=name, what=photostore.KINDS[kind]))
    con = connect()
    try:
        row = photostore.start(con, sid, kind, name, len(data), create=True, master_pub=VK.master_pub(con),
                               label=_photo_label(sid))
        if row["state"] == "done":
            return "had"
        photostore.append(con, row, row["received"], _io.BytesIO(data[row["received"]:]), len(data) - row["received"],
                          master_pub=VK.master_pub(con), label=_photo_label(sid))
        return "added"
    finally:
        con.close()

app.config["store_photo"] = store_photo

def _photo_label(sid):

    lab = q("""SELECT h.name, s.type, s.shoot_date FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""",
            (sid,), one=True)
    return " - ".join(str(v) for v in (lab["name"], C.SESSION_TYPES.get(lab["type"], lab["type"]), lab["shoot_date"]) if v) if lab else ""

@app.post("/session/<int:sid>/photos/upload")
def photos_upload(sid):

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
        if not lk.acquire(blocking=False):
            return {"ok": False, "received": row["received"]}, 409
        try:
            row = con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone()
            if not row:
                raise photostore.Gone("That file was deleted here; drop it again to upload it again.")
            body = request.stream
            if row["kind"] in IMAGE_KINDS and row["received"] == 0 and length > 0:
                head = body.read(min(8, length))
                if not _image_head(head):
                    photostore.delete(con, row)
                    raise photostore.UploadError(_NOT_AN_IMAGE.format(name=row["name"], what=photostore.KINDS[row["kind"]]))
                body = _Prefixed(head, body)
            from vault import keys as VK
            got = photostore.append(con, row, request.args.get("offset", type=int, default=-1), body, length,
                                    master_pub=VK.master_pub(con), label=_photo_label(sid))
            if got == row["size"] and row["kind"] in IMAGE_KINDS and not _decodes(photostore, con, row["id"]):
                photostore.delete(con, con.execute("SELECT * FROM photo_files WHERE id=?", (row["id"],)).fetchone())
                raise photostore.UploadError(_NOT_AN_IMAGE.format(name=row["name"], what=photostore.KINDS[row["kind"]]))
        finally:
            lk.release()
        done = got == row["size"]
        note = ""
        if done:
            _audit_upload(sid, row)

            if row["kind"] == "export":
                note = settle_by_exports(sid)
            if not os.environ.get("CRM_TESTING"):
                threading.Thread(target=_mirror_now, name="photo-mirror", daemon=True).start()
        return {"ok": True, "received": got, "done": done, "note": note}
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

DOWNLOAD_VERIFY_MAX = 512 << 20

@app.get("/photo/<fid>")
def photo_download(fid):

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
        first = next(gen)
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
    if row["size"] <= DOWNLOAD_VERIFY_MAX:
        try:
            photostore.verify(row)
        except Exception as e:
            app.logger.error("Photo %s did not check out: %s", fid, e or type(e).__name__)
            session["notice"] = (f"{row['name']} is damaged in the photos folder and can't be downloaded. Upload it "
                                 "again from your own copy, or restore it from a backup.")
            return redirect(stage_url(row["session_id"]))
    audit(f"Downloaded {row['name']} for editing", sid=row["session_id"])
    from urllib.parse import quote as _qu

    def body():
        yield first
        try:
            yield from gen
        except Exception as e:
            app.logger.error("Download of photo %s stopped part way: %s", fid, e or type(e).__name__)
    return app.response_class(body(), mimetype="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_qu(row['name'])}", "Content-Length": str(row["size"])})

@app.post("/photo/<fid>/delete")
def photo_delete(fid):

    import photostore
    fetch_ = bool(request.headers.get("X-Fetch"))
    con = connect()
    try:
        row = con.execute("SELECT * FROM photo_files WHERE id=?", (fid,)).fetchone() or abort(404)
        import locks
        lk = locks.photo(fid)
        if not lk.acquire(blocking=False):
            msg_ = f"{row['name']} is still uploading. Stop that upload first, then delete it."
            if fetch_:
                return {"ok": False, "message": msg_}, 409
            session["notice"] = msg_
            g.refused = True
            return redirect(safe_back(request.form.get("back")) or stage_url(row["session_id"]) + "#photos")
        try:
            photostore.delete(con, row)
        finally:
            lk.release()
    finally:
        con.close()
    audit(f"Deleted {row['name']}", sid=row["session_id"])
    if fetch_:
        return {"ok": True, "message": f"Deleted {row['name']}."}
    return redirect(safe_back(request.form.get("back")) or stage_url(row["session_id"]) + "#photos")

app.config.setdefault("quiet_endpoints", set()).add("photos_delete_many")

def _bulk_label(rows):

    import photostore
    kinds = {r_["kind"] for r_ in rows}
    if len(kinds) != 1:
        return "files" if len(rows) != 1 else "file"
    k_ = kinds.pop()
    if len(rows) == 1:
        return "RAW" if k_ == "raw" else photostore.KINDS[k_].lower()[:-1]
    return "RAWs" if k_ == "raw" else photostore.KINDS[k_].lower()

def bulk_photo_rows(sid, what, find="", picked=()):

    photos = photo_summary(sid)
    if what == "upload":
        return latest_upload(photos)
    if what == "pick":
        want = set(picked)
        return [r for g_ in photos.values() for r in g_["done"] if r["id"] in want]
    words = (find or "").lower().split()
    if what == "find" and words:
        return [r for g_ in photos.values() for r in g_["done"] if all(w_ in r["name"].lower() for w_ in words)]
    return []

@app.post("/session/<int:sid>/photos/delete")
def photos_delete_many(sid):

    import locks
    import photostore
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    back = safe_back(request.form.get("back")) or stage_url(sid) + "#photos"
    what, find = request.form.get("what", ""), request.form.get("find", "")
    picked = list(dict.fromkeys(request.form.getlist("pick")))
    rows = bulk_photo_rows(sid, what, find, picked)

    def refuse(msg_):
        session["notice"] = msg_
        g.refused = True
        return redirect(back)
    if what == "pick" and not picked:
        return refuse("Tick the photos to delete first; nothing was deleted.")
    if not rows:
        return refuse("There are no files like that here now; nothing was deleted.")
    if what == "pick" and (len(rows) != len(picked) or request.form.get("n", "") not in ("", str(len(picked)))):
        return refuse("Some of the files ticked changed since the page was shown (deleted, or uploaded again), so "
                      "nothing was deleted. Look at them again, then delete.")
    if what != "pick" and str(len(rows)) != request.form.get("n", "") or (what == "upload" and rows[0]["id"] != request.form.get("first")):
        return refuse("The files changed since the page was shown (an upload finished, or some were deleted), so "
                      "nothing was deleted. Look at them again, then delete.")
    if int(sid) in locks.SEALING:
        return refuse("This session's photos are being sealed right now. Try again in a few minutes.")
    held, gone, done = [], [], []
    con = connect()
    try:
        for r_ in rows:
            lk = locks.photo(r_["id"])
            if not lk.acquire(blocking=False):
                continue
            held.append(lk)
            row = con.execute("SELECT * FROM photo_files WHERE id=? AND session_id=?", (r_["id"], sid)).fetchone()
            if row:
                gone += photostore.delete(con, row, commit=False)
                done.append(row)
        con.commit()
    finally:
        con.close()
        for lk in held:
            lk.release()
    photostore.destroy_files(gone)
    if not done:
        return refuse("Those files are still uploading. Stop the upload first, then delete them.")
    n_, label = len(done), _bulk_label(done)
    names = sorted((r_["name"] for r_ in done), key=file_name_order)
    note = ", ".join(names[:3]) + (f" and {n_ - 3} more" if n_ > 3 else "")
    audit(f"Deleted {n_:,} {label}" if n_ > 1 else f"Deleted {names[0]}", sid=sid, note=note if n_ > 1 else "",
          route="photos-delete", data=json.dumps(dict(names=names[:500])))
    left = len(rows) - n_
    session["notice"] = (f"Deleted {n_:,} {label}." if n_ > 1 else f"Deleted {names[0]}.") + (
        f" {left} still uploading {'was' if left == 1 else 'were'} left as {'it is' if left == 1 else 'they are'}." if left else "")
    return redirect(back)

_MIRROR_LOCK = threading.Lock()

def mirror_photos():

    import photostore
    from vault import sync as vault_sync, web as vault_web_
    if not datakey.is_unlocked() or not _MIRROR_LOCK.acquire(blocking=False):
        return
    try:
        try:
            target = vault_sync.second_backend() or vault_web_.store_backends()[0]
        except Exception:
            return
        con = connect()
        try:
            audit_ = setting("photo_mirror_audit") != today().isoformat()
            c_, f_, r_ = photostore.mirror_pass(con, target, audit=audit_)
            if audit_:
                con.execute("INSERT INTO settings (key, value) VALUES ('photo_mirror_audit',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (today().isoformat(),))
            x_err = ""
        except Exception as e:
            c_ = f_ = r_ = 0
            import errtext
            x_err = errtext.named(e)
            app.logger.warning("photo mirror: %s", x_err)
        con.execute("INSERT INTO settings (key, value) VALUES ('photo_mirror_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (x_err,))
        con.commit()
        con.close()
        if not x_err:
            settle_backups()
        if f_:
            app.logger.info("photo mirror: fetched back %d photos missing here", f_)
    finally:
        _MIRROR_LOCK.release()

def _advance_loop():

    import time as _t
    _t.sleep(30)
    while True:
        try:
            if datakey.is_unlocked():
                with app.app_context():
                    advance_all(force=True)
                    app.config["master_tick"](force=True)
        except Exception:
            app.logger.exception("advance loop")
        _t.sleep(300)

def _mirror_now():
    with app.app_context():
        mirror_photos()

def _mirror_loop():
    import time as _t
    _t.sleep(120)
    swept_on = None
    while True:
        try:
            with app.app_context():
                mirror_photos()
        except Exception:
            app.logger.exception("photo mirror loop")
        try:
            if datakey.is_unlocked() and swept_on != date.today():
                if sweep_photo_orphans():
                    app.logger.info("removed sealed photos no record points to (30 days after a reset or a restore)")
                with app.app_context():
                    if clock_ok() and _restore.expire(today()):
                        app.logger.info("removed data set aside at a restore 30 days ago")
                swept_on = date.today()
        except Exception:
            app.logger.exception("photo orphan sweep")
        _t.sleep(900)

def photo_summary(sid):

    import photostore
    con = connect()
    try:
        rows = photostore.files(con, sid)
    finally:
        con.close()
    out = {}
    wants_ = app.config.get("gallery_wants_finals")
    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    for k, label in photostore.KINDS.items():
        if k == "final" and not any(r["kind"] == k for r in rows) and not (callable(wants_) and s_ and wants_(s_)):
            continue
        rs = sorted((r for r in rows if r["kind"] == k),
                    key=lambda r: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", r["name"])])
        done = [r for r in rs if r["state"] == "done"]
        out[k] = dict(label=label, done=done, partial=[r for r in rs if r["state"] == "partial"],
                      sealed=[r for r in rs if r["state"] == "sealed"], bytes=sum(r["size"] for r in done))
    return out

PHOTO_BATCH_GAP = 30 * 60
PHOTO_SHOWN = 12
PHOTO_FIND_MAX = 48

def _photo_when(r):
    try:
        return datetime.fromisoformat((r["created"] or "").replace(" ", "T")[:19]).timestamp()
    except (ValueError, TypeError, IndexError):
        return 0.0

def latest_upload(photos):

    done = sorted(((k, r) for k, g_ in (photos or {}).items() for r in g_["done"]),
                  key=lambda kr: (_photo_when(kr[1]), kr[1]["name"]), reverse=True)
    if not done:
        return []
    kind, out, last = done[0][0], [], None
    for k, r in done:
        t_ = _photo_when(r)
        if k != kind or (last is not None and last - t_ > PHOTO_BATCH_GAP):
            break
        out.append(r)
        last = t_
    return out

def find_files(photos, term):

    words = (term or "").lower().split()
    if not words:
        return [], 0
    hits = [r for g_ in (photos or {}).values() for r in g_["done"] if all(w_ in r["name"].lower() for w_ in words)]
    return hits[:PHOTO_FIND_MAX], len(hits)

app.jinja_env.globals.update(latest_upload=latest_upload, find_files=find_files, PHOTO_SHOWN=PHOTO_SHOWN)

def size_words(n):
    for unit, f in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= f:
            return f"{n / f:.1f} {unit}".replace(".0 ", " ")
    return f"{n} bytes"

app.jinja_env.filters["size"] = size_words

THUMB_SIZES = {"s": 360, "l": 1600}
_THUMBS = {}
_THUMB_CAP = 48 << 20
_THUMB_BYTES = [0]
_THUMB_LOCK = threading.Lock()
_THUMB_WORK = threading.BoundedSemaphore(2)

def _thumbs_forget(fid=None):

    with _THUMB_LOCK:
        if fid is None:
            _THUMBS.clear()
            _THUMB_BYTES[0] = 0
            return
        for k_ in [k_ for k_ in _THUMBS if k_[0] == fid]:
            _THUMB_BYTES[0] -= len(_THUMBS.pop(k_))

datakey.ON_LOCK.append(_thumbs_forget)

def _photostore_hooks():
    import photostore
    photostore.ON_GONE.append(_thumbs_forget)

_photostore_hooks()

class _From(io.RawIOBase):

    def __init__(self, mv, start):
        super().__init__()
        self.mv, self.start, self.p = mv, start, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def readinto(self, b):
        n = max(0, min(len(b), len(self.mv) - self.start - self.p))
        b[:n] = self.mv[self.start + self.p:self.start + self.p + n]
        self.p += n
        return n

    def seek(self, off, whence=0):
        end = len(self.mv) - self.start
        self.p = max(0, off if whence == 0 else self.p + off if whence == 1 else end + off)
        return self.p

    def tell(self):
        return self.p

def raw_preview(data):

    from PIL import Image
    mv = memoryview(data)
    best, pos, seen = None, 0, 0
    while seen < 24:
        pos = data.find(b"\xff\xd8\xff", pos)
        if pos < 0:
            break
        seen += 1
        try:
            im = Image.open(io.BufferedReader(_From(mv, pos)))
            if im.format == "JPEG" and (best is None or im.size[0] * im.size[1] > best[1]):
                best = (pos, im.size[0] * im.size[1])
        except Exception:
            pass
        pos += 3
    if best is None:
        return None
    return Image.open(io.BufferedReader(_From(mv, best[0])))

PLAIN_IMAGE_HEADS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")

PREVIEW_EXTS = {"jpg", "jpeg", "png", "gif", "tif", "tiff", "heic", "webp", "nef", "nrw", "cr2", "cr3", "crw", "arw",
                "srf", "sr2", "raf", "orf", "rw2", "pef", "dng", "srw", "3fr", "iiq", "erf", "kdc", "mrw", "x3f", "raw", "rwl"}

def previewable(name):
    return (name or "").rsplit(".", 1)[-1].lower() in PREVIEW_EXTS if "." in (name or "") else False

app.jinja_env.globals["previewable"] = previewable

def photo_thumb(row, size):

    import photostore
    from PIL import Image, ImageOps
    key = (row["id"], size)
    with _THUMB_LOCK:
        if key in _THUMBS:
            return _THUMBS[key]
    if row["size"] > 512 << 20:
        return None
    with _THUMB_WORK:
        with _THUMB_LOCK:
            if key in _THUMBS:
                return _THUMBS[key]
        data = photostore.plain_bytes(row)
        edge = THUMB_SIZES[size]
        try:

            im = raw_preview(data) if row["kind"] == "raw" and not data[:8].startswith(PLAIN_IMAGE_HEADS) \
                else Image.open(io.BytesIO(data))
            if im is None:
                return None
            if im.format == "JPEG":
                im.draft("RGB", (edge, edge))
            im = ImageOps.exif_transpose(im)
            im.thumbnail((edge, edge))
            out = io.BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=82)
        except Exception:
            return None
        finally:
            del data
    blob = out.getvalue()
    if not datakey.is_unlocked():
        return blob
    with _THUMB_LOCK:
        if key not in _THUMBS:
            _THUMBS[key] = blob
            _THUMB_BYTES[0] += len(blob)
        while _THUMB_BYTES[0] > _THUMB_CAP and _THUMBS:
            _THUMB_BYTES[0] -= len(_THUMBS.pop(next(iter(_THUMBS))))
    return blob

@app.get("/photo/<fid>/thumb")
def photo_thumb_view(fid):

    size = request.args.get("size", "s")
    if size not in THUMB_SIZES:
        abort(404)
    con = connect()
    try:
        row = con.execute("SELECT * FROM photo_files WHERE id=? AND state='done'", (fid,)).fetchone()
    finally:
        con.close()
    if not row:
        abort(404)
    try:
        blob = photo_thumb(row, size)
    except Exception:
        blob = None
    if not blob:
        abort(404)
    return app.response_class(blob, mimetype="image/jpeg", headers={"Cache-Control": "no-store, private"})

def has_backup_location():

    try:
        from vault import web as vault_web_
        return bool(vault_web_.config().get("secondary"))
    except Exception:
        return False

def backup_state(sid):

    rows = q("SELECT mirrored FROM photo_files WHERE session_id=? AND kind='raw' AND state='done'", (sid,))
    two = has_backup_location()
    mark = None
    if two:
        try:
            import photostore
            from vault import web as vault_web_
            mark = photostore.config_mark(vault_web_.config().get("secondary"))
        except Exception:
            mark = None
    return dict(n=len(rows), both=sum(1 for r_ in rows if mark and r_["mirrored"] == mark) if two else 0, two=two)

def settle_backup(sid):

    st = backup_state(sid)
    if not (st["two"] and st["n"] and st["both"] == st["n"]):
        return False
    open_ = q("SELECT id FROM tasks WHERE session_id=? AND own=0 AND title='Back up photos' AND done IS NULL", (sid,))
    for r_ in open_:
        x("UPDATE tasks SET done=?, outcome='done' WHERE id=? AND done IS NULL", (today().isoformat(), r_["id"]))
    if open_:
        audit(f"Upload original photos done: the {st['n']} RAW{'' if st['n'] == 1 else 's'} are on both storage locations", sid=sid,
              who="Lumen Aureum")
    return bool(open_)

def settle_backups():

    if not has_backup_location():
        return
    for r_ in q("""SELECT DISTINCT t.session_id FROM tasks t JOIN photo_files p ON p.session_id=t.session_id AND p.kind='raw'
                   WHERE t.own=0 AND t.title='Back up photos' AND t.done IS NULL"""):
        settle_backup(r_["session_id"])

def settle_by_exports(sid):

    open_ = q("SELECT id, title FROM tasks WHERE session_id=? AND own=0 AND title IN ('Cull','Edit') AND done IS NULL ORDER BY sort",
              (sid,))
    if not open_:
        return ""
    for r_ in open_:
        x("UPDATE tasks SET done=?, outcome='done', outcome_note=? WHERE id=? AND done IS NULL",
          (today().isoformat(), "Settled: the edited photos were uploaded", r_["id"]))
    names = " and ".join(step_says(r_["title"]) for r_ in open_)
    note = f"{names} marked done: the edited photos arrived."
    audit(note, sid=sid, who="Lumen Aureum")
    return note

_PICK_NAME = re.compile(r"^\s*(?P<name>[^:\n]*?\.(?:jpe?g|png|tiff?|heic|webp|nef|cr2|cr3|arw|dng|raf|orf|rw2))\s*(?::\s*(?P<note>.*))?$", re.I)

def retouch_state(s):

    lines = [l_.strip() for l_ in (s["retouch_picks"] or "").splitlines() if l_.strip()]
    if not lines:
        return dict(picks=[], n=0, n_in=0, all_in=True, n_fin=0)
    finals = [r_["name"].lower() for r_ in q("SELECT name FROM photo_files WHERE session_id=? AND kind='final' AND state='done'",
                                             (s["id"],))]
    picks, loose = [], 0
    for l_ in lines:
        m_ = _PICK_NAME.match(l_)
        if m_:
            stem = m_.group("name").rsplit(".", 1)[0].lower()
            picks.append(dict(name=m_.group("name"), note=(m_.group("note") or "").strip(),
                              done=any(f_.rsplit(".", 1)[0].startswith(stem) for f_ in finals)))
        else:
            picks.append(dict(name="", note=l_, done=False))
            loose += 1
    if loose:
        spare = len(finals) - sum(1 for p_ in picks if p_["done"])
        for p_ in picks:
            if not p_["name"] and spare > 0:
                p_["done"], spare = True, spare - 1
    n_in = sum(1 for p_ in picks if p_["done"])
    return dict(picks=picks, n=len(picks), n_in=n_in, all_in=n_in == len(picks), n_fin=len(finals))

def retouch_refused(sid):

    s_ = q("SELECT id, retouch_picks FROM sessions WHERE id=?", (sid,), one=True)
    if not s_:
        return ""
    st = retouch_state(s_)
    if st["all_in"]:
        return ""
    return (f"Not done yet: {st['n_in']} of {st['n']} retouched photos are in. Drop the retouched photos on the Retouch row "
            "first.")

app.jinja_env.globals["retouch_state"] = retouch_state
app.config["retouch_refused"] = retouch_refused

BACKUP_ELSEWHERE_ASK = ("Mark Upload original photos done? A copy you made outside the app isn't encrypted by it. The app keeps its "
                        "own copies encrypted; keeping yours safe is up to you.")

@app.post("/session/<int:sid>/backup/elsewhere")
def backup_elsewhere(sid):

    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    back = safe_back(request.form.get("back")) or stage_url(sid)
    if not request.form.get("sure"):
        g.refused = True
        return render_template("confirm_post.html", question="Mark Upload original photos done?",
                               detail=("A copy you made outside the app isn't encrypted by it. The app keeps its own "
                                       "copies encrypted; keeping yours safe is up to you."),
                               action=url_for("backup_elsewhere", sid=sid), fields=list(request.form.items(multi=True)),
                               confirm_name="sure", yes="I backed it up another way", cancel=back)
    x("UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND own=0 AND title='Back up photos' AND done IS NULL",
      (today().isoformat(), sid))
    return redirect(back)

app.config.setdefault("ep_words", {})["backup_elsewhere"] = "Marked Upload original photos done: backed up outside the app, not encrypted by it"

DELIVERY_HOW = {"email": "the gallery link by email instead", "usb": "handed over in person on a USB drive",
                "sd": "handed over in person on an SD card", "disc": "handed over in person on a CD or DVD",
                "card": "handed over on a sealed card (SD or USB)"}
PHYSICAL_HOW = ("usb", "sd", "disc", "card")

def record_delivery(sid, step, words, on=None, physical=True):

    x("""INSERT INTO step_closes (session_id, step, outcome, note) VALUES (?,?,'done',?)
         ON CONFLICT(session_id, step) DO UPDATE SET outcome='done', note=excluded.note, at=date('now','localtime')""",
      (sid, step, words))
    if step == "sd_shipped" and physical:
        x("UPDATE sessions SET sd_shipped=COALESCE(sd_shipped, ?) WHERE id=?", (on or today().isoformat(), sid))

app.config["record_delivery"] = record_delivery

def _delivery_words(f):
    how_ = DELIVERY_HOW.get(f.get("how"), "another way")
    on_ = f.get("on") if f.get("how") in PHYSICAL_HOW else ""
    try:
        on_ = nice(date.fromisoformat(on_).isoformat()) if on_ else ""
    except ValueError:
        on_ = ""
    return f"Got the photos to the client: {how_}" + (f", {on_}" if on_ else "") + (f". {f['note'][:200]}" if f.get("note") else "")

@app.post("/session/<int:sid>/deliver/another")
def delivery_another(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    f = request.form
    how, step = f.get("how"), f.get("step", "letter_mailed")
    back = safe_back(f.get("back")) or stage_url(sid)
    if how not in DELIVERY_HOW or step not in ("letter_mailed", "sd_shipped"):
        abort(400)
    owed = release_blocked(sid)
    if owed > 0.005:
        session["notice"] = f"{money(owed)} is still owed. Your terms release the photos on payment."
        g.refused = True
        return redirect(back)
    on = f.get("on") or today().isoformat()
    try:
        on = min(date.fromisoformat(on), today()).isoformat()
    except ValueError:
        on = today().isoformat()
    note = (f.get("note") or "").strip()[:200]
    draft = ""
    if how == "email":
        mail_ = app.config.get("gallery_link_mail")
        draft = mail_(s_) if callable(mail_) else ""
        if not draft:
            session["notice"] = "No gallery link to email yet: put the gallery online first."
            g.refused = True
            return redirect(back)
    record_delivery(sid, step, DELIVERY_HOW[how] + (f" ({note})" if note else ""), on,
                    physical=how in PHYSICAL_HOW and not s_["sd_shipped"])
    session["notice"] = "Recorded: " + DELIVERY_HOW[how] + "."
    if how == "email" and f.get("mail"):
        return redirect(draft)
    return redirect(back)

app.config.setdefault("ep_words", {})["delivery_another"] = _delivery_words

@app.post("/session/<int:sid>/contact-email")
def contact_email(sid):

    s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    em = (request.form.get("email") or "").strip()[:200]
    who = household_contact(q("SELECT * FROM people WHERE household_id=? ORDER BY id", (s_["household_id"],)))
    if not who or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
        session["notice"] = "That doesn't look like an email address." if who else "Add a grown-up to this client first."
    elif (who["email"] or "").strip():
        session["notice"] = (f"{who['name']} already has an email on file ({who['email']}), added since this page was "
                             "opened, so it wasn't changed. To change it, edit it on the client page.")
        g.refused = True
    else:
        x("UPDATE people SET email=? WHERE id=?", (em, who["id"]))
        audit(f"Email for {who['name']} set to {em}", sid=sid, hid=s_["household_id"])
    return redirect(safe_back(request.form.get("back")) or stage_url(sid))

@app.get("/session/<int:sid>/quote")
def session_quote(sid):

    r = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    if r:
        return redirect(url_for("quote_doc", vid=r["id"]))
    q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    return redirect(stage_url(sid) + "#billing")

def quote_version(vid):
    r = q("SELECT * FROM quote_versions WHERE id=?", (vid,), one=True) or abort(404)
    v = json.loads(r["data"])
    v.setdefault("license", v["type"] in C.LICENSE_TYPES)
    v.update(id=r["id"], no=r["number"] or str(r["id"]), sid=r["session_id"], schedule_id=r["schedule_id"], sent_long=long_date(r["at"]), at=r["at"],
             expires_long=long_date(r["expires"]), type_label=v.get("type_label") or C.SESSION_TYPES.get(v["type"], v["type"]),
             shoot_date_long=long_date(v.get("shoot_date")), back=stage_url(r["session_id"]),

             reviewed=doc_reviewed("terms", v["type"]), approve=None,
             review=review_url("terms", v["type"]))
    newer_ = q("SELECT id FROM quote_versions WHERE session_id=? AND id>? ORDER BY id LIMIT 1", (r["session_id"], r["id"]), one=True)
    v["replaced_by"] = dict(id=newer_["id"], no=quote_no(newer_["id"])) if newer_ else None
    return v

@app.get("/quote/<int:vid>")
def quote_doc(vid):
    v = quote_version(vid)
    who = v.get("business") or v.get("photographer") or "your photographer"
    total_ = v.get("total") or 0
    facts = [f"Your quote for {_for_what(v.get('type_label'), v.get('shoot_date_long'))}: "
             + (money(total_) if total_ > 0.005 else "no charge")
             + (f", with a {money(v['deposit'])} deposit to hold the date." if v.get("deposit") else "."),
             f"It is good through {v['expires_long']}."]
    return render_template("quote_doc.html", v=v, send=send_ctx(v["sid"], "quote", f"Your quote from {who}", facts, vid=vid))

def quote_sent_on(vid):

    v_ = q("SELECT id, session_id, at FROM quote_versions WHERE id=?", (vid,), one=True)
    if not v_:
        return ""
    r_ = q("SELECT at FROM quote_marks WHERE vid=?", (vid,), one=True)
    if r_:
        return r_["at"]
    if q("SELECT 1 FROM quote_marks WHERE session_id=?", (v_["session_id"],), one=True):
        return ""
    t_ = q("SELECT done FROM tasks WHERE session_id=? AND title=? AND done IS NOT NULL ORDER BY done DESC",
           (v_["session_id"], SEND_TASKS["quote"]), one=True)
    return t_["done"] if t_ and (v_["at"] or "")[:10] <= t_["done"] else ""

def quote_went(sid):

    s_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
    if s_ and s_["stage"] == "inquiry":
        move_on(sid, "quoted", "the quote was marked sent")

def _mark_quote_sent(vid, sid):
    x("INSERT OR IGNORE INTO quote_marks (vid, session_id, at) VALUES (?,?,?)", (vid, sid, today().isoformat()))

@app.post("/quote/<int:vid>/sent")
def quote_mark_sent(vid):

    v_ = q("SELECT id, session_id FROM quote_versions WHERE id=?", (vid,), one=True) or abort(404)
    if quote_sent_on(vid):
        g.refused = True
    else:
        _mark_quote_sent(vid, v_["session_id"])
        x("UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND title=? AND done IS NULL",
          (today().isoformat(), v_["session_id"], SEND_TASKS["quote"]))
        g.audit_note = f"quote {quote_no(vid)}"
        quote_went(v_["session_id"])
    return redirect(safe_back(request.form.get("back")) or url_for("quote_doc", vid=vid))

@app.get("/quote/<int:vid>.pdf")
def quote_doc_pdf(vid):
    v = quote_version(vid)
    return html_pdf(render_template("quote_doc.html", v=v, pdf=True), file_slug("quote", v["no"], v["client"]) + ".pdf")

def license_words(s):

    lic_bits = []
    if (s["lic_term"] or "").strip():
        lic_bits.append(f"The license lasts {s['lic_term'].strip().rstrip('.')}.")
    if (s["lic_territory"] or "").strip():
        lic_bits.append(f"It covers use in {s['lic_territory'].strip().rstrip('.')}.")
    if s["lic_exclusive"]:
        lic_bits.append("It is exclusive: I license these photos to no one else while it lasts.")
    elif lic_bits:
        lic_bits.append("It is not exclusive.")
    return lic_bits

def agreement_ctx(sid):
    s = session_full(sid)
    last = q("SELECT * FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    t = totals(sid)
    ppl = q("""SELECT p.* FROM people p JOIN session_people sp ON sp.person_id=p.id WHERE sp.session_id=?
               ORDER BY p.name COLLATE NOCASE""", (sid,))
    _, signers = agreement_signers(sid)
    lic_bits = license_words(s)
    scope = [f"{l_['label']}" for l_ in q("SELECT label FROM lines WHERE session_id=? AND kind NOT IN ('discount','tax','credit') ORDER BY id", (sid,))]
    def price_of(sid_):
        lq = q("SELECT * FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid_,), one=True)
        if lq:
            d_ = json.loads(lq["data"])
            return d_["total"], d_["deposit"], lq["id"]
        t_ = t if sid_ == sid else totals(sid_)
        return t_["quoted"], t_["deposit_req"], None
    plan = []
    if q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True):
        for r_ in plan_sessions(sid):
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
                minor=any(minor_on(p, d_) for p in ppl for d_ in ([s["shoot_date"]] + [r_["shoot_date"] for r_ in plan_sessions(sid)]
                                                                     if plan else [s["shoot_date"]])),
                type_label=(lambda ev_: ev_["name"] if ev_ else C.doc_label(s["type"]))(
                    q("SELECT name FROM events WHERE id=?", (s["event_id"] or s["payer_event"],), one=True) if (s["event_id"] or s["payer_event"]) else None),
                shoot_date_long=long_date(s["shoot_date"]), start_time=clock(s["start_time"]),
                stops=", ".join([venue_] * bool(venue_) + [l_.strip() for l_ in (s["stops"] or "").splitlines() if l_.strip() and l_.strip() != venue_]),
                total=total, deposit=deposit, quote_id=qid, net_days=C.BALANCE_NET_DAYS, balance_rule=balance_rule(s["type"], sid),
                schedule=[] if plan else payment_schedule(sid),
                gallery_days=C.gallery_days(s["type"]), delivery_days=C.EVENT_DELIVERY_DAYS if event and not C.POLICY[s["type"]].get("delivery_days") else C.delivery_days(s["type"]),
                reschedule_fee=C.RESCHEDULE_FEE, retouch_picks=C.RETOUCH_PICKS, marketing_picks=C.MARKETING_PICKS,
                license=s["type"] in C.LICENSE_TYPES, kind=C.TYPE_KIND[s["type"]], payment=C.payment_text(setting("business_name")),
                free=total <= 0.005 and not plan,
                cancel=C.cancel_text(s["type"], total <= 0.005 and not plan), late=C.late_text(s["type"], total <= 0.005 and not plan), outfits=C.POLICY[s["type"]]["outfits"], animals=s["type"] in C.ANIMAL_TYPES, stype=s["type"],
                delivery=C.delivery_text(s["type"], s["delivery_method"]), keep_raw=C.fill(s["type"], "keep_raw"),
                link_days=C.gallery_days(s["type"]) if C.delivery_method(s["type"], s["delivery_method"]) == "link"
                and C.galleries_on() else None,
                license_use=C.fill(s["type"], "license_use"),
                event_rights=event_rights_text(s["event_id"] or s["payer_event"], voice="organizer" if s["payer_event"] else "photographer"),
                photographer=setting("photographer_name") or "[YOUR NAME]", business=setting("business_name"),
                law=setting("governing_law") or "the place where I do business",
                reviewed=doc_reviewed("agreement", s["type"]), review=review_url("agreement", s["type"]), back=stage_url(sid),

                approve=None if q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (sid,), one=True)
                else dict(doc="agreement", type=s["type"]),
                unsigned=bool(q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status!='signed'", (sid,), one=True)))

def agreement_view(sid):

    import wording
    a = agreement_ctx(sid)
    text_ = own_wording("agreement", q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)["type"])
    if a.get("free"):
        text_ = wording.free_text(text_)
    a["custom"] = C.dw(wording.render(text_, agreement_fields(a)))

    return a

def agreement_fields(a):

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
        photos = (f"I keep the copyright. Your license covers {a['license_use']}."
                  + (" " + C.MLS_SENTENCE if C.is_place(a.get("stype")) else "")
                  + " Anyone else who wants to use the photos needs their own license from me.") + a["lic_scope"]
    elif a["license"] and C.TYPE_KIND.get(a.get("stype")) != "property" and not a["lic_scope"] \
            and a["license_use"] == C.DEFAULT_POLICY["license_use"]:
        photos = ("I keep the copyright. Once the balance is paid, you, and the people photographed, receive a permanent,"
                  " non-exclusive license to the final edited photos I deliver for personal use: to print them anywhere, share them,"
                  " post them on social media and personal websites, and crop or apply filters for those uses. Proofs, unedited"
                  " files and photos I do not deliver are not included. It does not cover selling the photos, using them to advertise a business, entering them in"
                  " contests, or licensing them to others; those need my written permission. Your license letter is also a"
                  " print release, for any print lab.")
    elif a["license"]:
        photos = ((f"I keep the copyright. You receive a license; it covers {a['license_use']}." if a["lic_scope"] else
                   f"I keep the copyright. Once the balance is paid, you, and the people photographed, receive a permanent,"
                   f" non-exclusive license to the final edited photos I deliver for {a['license_use']}. Proofs, unedited files"
                   " and photos I do not deliver are not included.")
                  + " Anything else, such as selling the photos or licensing them to others, needs my written permission."
                  + a["lic_scope"])
    else:
        photos = ("When the balance is paid in full, I transfer to you all copyright in the final edited photos I deliver."
                  " Proofs, unedited files and photos I do not deliver are not included. From then on they are yours to use"
                  " any way you like, with no permission needed from me.")
    if a["kind"] == "property":
        use = "I may show these photos in my portfolio and on my website. I never publish an owner's or client's private details with them."
    else:
        names = " I never use your names" + (" or your child's name, school or location" if a["minor"] else "") + " with the photos."
        if a.get("portfolio_no"):
            use = ("I will not show any photo from your session in my portfolio, on my website, on social media, or anywhere"
                   " else." + names)
        else:
            use = (C.portfolio_choice(a["marketing_picks"])
                   + (" Your price includes my portfolio permission discount. If, before the photos are delivered, you"
                      " withdraw permission, or say no to so many photos that I cannot show any, the discount is added back"
                      " to your balance. After delivery, the discount stays, whatever you later ask me to take down."
                      if a.get("portfolio_discount") else "")
                   + ("" if a["license"] else " This permission continues after the copyright transfers to you.")
                   + (" When a child in these photos becomes an adult, they may ask me to take down any photo of them, and I will."
                      if a["minor"] else "")
                   + " I show no other photo from your session anywhere." + names)
    if a.get("free"):
        photos = photos.replace("Once the balance is paid, you, and", "You, and")
        photos = photos.replace("When the balance is paid in full, I transfer to you all copyright in the final edited photos I deliver.",
                                "When I deliver the final edited photos, I transfer to you all copyright in them.")
    me = a["photographer"] + (f", working through {a['business']}" if a["business"] else "")
    deliver = a["delivery"] + (f" (the gallery stays up {a['gallery_days']} days after you first open it)" if a["kind"] != "property" and "gallery" in a["delivery"] else "")
    return {"CLIENT": a["company"] or a["signer"] or a["client"], "PHOTOGRAPHER": me, "SESSION": a["type_label"], "DATE": a["shoot_date_long"] or "",
            "PRICE": money(a["total"]), "DEPOSIT": money(a["deposit"]), "BALANCE DUE": a["balance_rule"],
            "PAYMENT": a["payment"], "CANCELLATION": a["cancel"] + (" " + a["late"] if a["late"] else ""),
            "IF I CANNOT": cannot_text(a.get("stype"), a.get("free")),
            "WHAT I PROVIDE": provide, "HOW YOU DELIVER": deliver, "DELIVERY DAYS": str(a["delivery_days"]),

            "THE LINK": (f"The link stays open for {a['link_days']} days after you first open it; ask once and I'll extend"
                         " it.") if a.get("link_days") else "",
            "HOW LONG": a["keep_raw"], "THE PHOTOS": photos, "HOW I MAY USE THEM": use,
            "MINORS": "A parent or guardian signs this agreement and is present for the whole session." if a["minor"] else "",
            "PRODUCTS": ("I handle the products you lend me with care and return them as we agreed. While they are in my care,"
                         " my responsibility for loss or damage is limited to the price of this session.") if a["products"] else "",
            "LAW": a["law"], "RECORDS YEARS": str(C.CLOSED_CLIENT_YEARS)}

def cannot_text(stype, free=False):

    if stype and not C.POLICY[stype]["free_reschedule"]:
        if free:
            return "I will send a qualified photographer in my place. If I cannot find one, and your date can move, we pick a new date."
        return ("I will send a qualified photographer in my place. If I cannot find one, I refund everything you have paid;"
                " or, if you prefer and your date can move, we pick a new date.")
    if free:
        return "I will offer a new date."
    return "I will offer a new date first. If no new date works for you, I refund everything you have paid."

def company_of(hid):
    r = q("SELECT company FROM households WHERE id=?", (hid,), one=True)
    return (r["company"] or "").strip() if r else ""

NUMBERS_JSON = DB_PATH.parent / "doc-numbers.json"

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
                 if isinstance(k_, str) and re.fullmatch(r"(INV|R)-\d{4}-|Q|Q-\d{8}|S|H|T|E", k_) and str(v_).isdigit() and 0 <= int(v_) < 10**9}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        for k_, v_ in json.loads(datakey.open_bytes(NUMBERS.read_bytes(), "numbers", label="numbers")).items():
            marks[k_] = max(int(v_), int(marks.get(k_, 0)))
    except Exception:
        pass
    return marks

DELETIONS_RESTORED = DB_PATH.parent / "deletions.restored.json"
NUMBERS_RESTORED = DB_PATH.parent / "doc-numbers.restored.json"

def _merge_restored_side_files():

    with _ID_LOCK:
        try:
            incoming = json.loads(DELETIONS_RESTORED.read_text())
        except OSError:
            incoming = None
        except ValueError:
            incoming = []
        if incoming is not None:
            try:
                live = json.loads(DELETIONS_JSON.read_text())
            except (OSError, ValueError):
                live = []
            live = live if isinstance(live, list) else []
            seen = {json.dumps(d_, sort_keys=True) for d_ in live}
            for d_ in incoming if isinstance(incoming, list) else []:
                if isinstance(d_, dict) and json.dumps(d_, sort_keys=True) not in seen:
                    seen.add(json.dumps(d_, sort_keys=True))
                    live.append(d_)
            _write_plain(DELETIONS_JSON, json.dumps(live))
            DELETIONS_RESTORED.unlink(missing_ok=True)
        if NUMBERS_RESTORED.exists():
            marks = _number_marks()
            try:
                raw_ = json.loads(NUMBERS_RESTORED.read_text())
            except (OSError, ValueError):
                raw_ = {}
            for k_, v_ in (raw_.items() if isinstance(raw_, dict) else []):
                if isinstance(k_, str) and re.fullmatch(r"(INV|R)-\d{4}-|Q|Q-\d{8}|S|H|T|E", k_) and str(v_).isdigit() and 0 <= int(v_) < 10**9:
                    marks[k_] = max(int(v_), int(marks.get(k_, 0)))
            _save_number_marks(marks)
            NUMBERS_RESTORED.unlink(missing_ok=True)

def _seal_missing_photo_keys():

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

    try:
        with _ID_LOCK:
            marks = _number_marks()
            if marks:
                _save_number_marks(marks)
    except Exception:
        app.logger.exception("folding the older number marks")

def _seed_number_marks():

    with _ID_LOCK:
        _seed_number_marks_locked()

def _seed_number_marks_locked():
    marks = _number_marks()
    changed = False
    for kind_, num_ in q("SELECT kind, number FROM doc_numbers"):
        pre, _, n_ = (num_ or "").rpartition("-")
        if pre and n_.isdigit() and int(n_) > int(marks.get(pre + "-", 0)):
            marks[pre + "-"] = int(n_)
            changed = True
    sent_ = q("SELECT note, data FROM audit WHERE route LIKE '/session/%/quote/send'")
    qn = max([q("SELECT COALESCE(MAX(id), 0) n FROM quote_versions", one=True)["n"]] +
             [int(m_.group(1)) for r_ in sent_
              if (m_ := re.match(r"quote (\d{1,9}),", r_["note"] or ""))] +
             [_audit_quote_id(r_["data"]) for r_ in sent_])
    if qn > int(marks.get("Q", 0)):
        marks["Q"] = qn
        changed = True
    if changed:
        _save_number_marks(marks)

def _audit_quote_id(data):

    try:
        d_ = json.loads(data or "{}")
        return int(d_.get("quote_id") or 0) if isinstance(d_, dict) else 0
    except (ValueError, TypeError):
        return 0

def _save_number_marks(marks):

    try:
        _write_plain(NUMBERS_JSON, json.dumps(marks))
    except Exception:
        app.logger.exception("saving the document number marks")

def quote_no(vid):

    if not vid:
        return ""
    r_ = q("SELECT number FROM quote_versions WHERE id=?", (vid,), one=True)
    return (r_["number"] if r_ and r_["number"] else str(vid))

app.jinja_env.globals["quote_no"] = quote_no

def next_quote_doc_number(sent):

    day = sent[:10].replace("-", "")
    with _ID_LOCK:
        marks = _number_marks()
        n = int(marks.get("Q-" + day, 0))
        for r_ in q("SELECT number FROM quote_versions WHERE number LIKE ?", (day + "%",)):
            tail_ = (r_["number"] or "")[8:]
            n = max(n, int(tail_) if tail_.isdigit() else 0)
        for r_ in q("SELECT note FROM audit WHERE route LIKE '/session/%/quote/send' AND note LIKE ?", (f"quote {day}%",)):
            m_ = re.match(r"quote " + day + r"(\d{2,}),", r_["note"] or "")
            n = max(n, int(m_.group(1)) if m_ else 0)
        n += 1
        marks = {k_: v_ for k_, v_ in marks.items() if not (k_.startswith("Q-") and k_ < "Q-" + day)}
        marks["Q-" + day] = n
        _save_number_marks(marks)
    return f"{day}{n:02d}"

def next_quote_number():

    with _ID_LOCK:
        marks = _number_marks()
        n = max(q("SELECT COALESCE(MAX(id), 0) n FROM quote_versions", one=True)["n"], int(marks.get("Q", 0))) + 1
        marks["Q"] = n
        _save_number_marks(marks)
    return n

ID_MARKS = {"sessions": "S", "households": "H", "tasks": "T", "events": "E"}
_ID_LOCK = threading.RLock()

def _db_id_mark(con, table):
    con.execute(_ID_TABLE)
    r = con.execute("SELECT n FROM id_marks WHERE name=?", (table,)).fetchone()
    return int(r[0]) if r else 0

def id_mark(table):

    con = db()
    return max(int(_number_marks().get(ID_MARKS[table], 0)), _db_id_mark(con, table),
               con.execute("SELECT COALESCE(MAX(id), 0) FROM " + dbmod.ident(table)).fetchone()[0])

def _raise_id_mark(table, n):
    con = db()
    with _ID_LOCK:
        marks = _number_marks()
        if n > int(marks.get(ID_MARKS[table], 0)):
            marks[ID_MARKS[table]] = n
            _save_number_marks(marks)
        if n > _db_id_mark(con, table):
            con.execute("INSERT INTO id_marks (name, n) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET n=excluded.n", (table, n))

def next_id(table):

    with _ID_LOCK:
        n = id_mark(table) + 1
        _raise_id_mark(table, n)
    return n

def _seed_id_marks():

    con = db()
    top_s = con.execute("SELECT MAX(COALESCE((SELECT MAX(id) FROM sessions), 0), "
                        "COALESCE((SELECT MAX(session_id) FROM audit), 0))").fetchone()[0]
    top_h = con.execute("SELECT MAX(COALESCE((SELECT MAX(id) FROM households), 0), "
                        "COALESCE((SELECT MAX(household_id) FROM audit), 0))").fetchone()[0]

    _k, good_, legacy_, _f = _sort_deletions(con)
    for d_ in good_ + legacy_:
        if 0 < d_["id"] < 10**9:
            if _is_client_entry(d_):
                top_h = max(top_h, d_["id"])
            else:
                top_s = max(top_s, d_["id"])
    _raise_id_mark("sessions", int(top_s))
    _raise_id_mark("households", int(top_h))

    for table_, pat_ in (("tasks", r"/task/(\d+)"), ("events", r"/event/(\d+)")):
        top_ = con.execute("SELECT COALESCE(MAX(id), 0) FROM " + dbmod.ident(table_)).fetchone()[0]
        for (route_,) in con.execute("SELECT route FROM audit WHERE route LIKE ?", (pat_.split("(")[0] + "%",)).fetchall():
            m_ = re.match(pat_ + r"(/|$)", route_ or "")
            if m_ and int(m_.group(1)) < 10**9:
                top_ = max(top_, int(m_.group(1)))
        if table_ == "events":
            top_ = max(top_, con.execute("SELECT COALESCE(MAX(event_id), 0) FROM audit").fetchone()[0])
        _raise_id_mark(table_, int(top_))

def doc_number(sid, kind, prefix=None):

    r = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind), one=True)
    if r:
        return r["number"]
    yr = today().isoformat()[:4]
    pre = f"{prefix or kind}-{yr}-"
    for _ in range(5):

        n = q("SELECT COALESCE(MAX(CAST(substr(number, ?) AS INTEGER)), 0) + 1 AS n FROM doc_numbers WHERE number LIKE ?",
              (len(pre) + 1, pre + "%"), one=True)["n"]
        with _ID_LOCK:
            marks = _number_marks()
            n = max(n, int(marks.get(pre, 0)) + 1)
            marks[pre] = n
            _save_number_marks(marks)
        try:
            x("INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (?,?,?,?)", (sid, kind, f"{pre}{n:04d}", today().isoformat()))
        except sqlite3.IntegrityError:
            r = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind), one=True)
            if r:
                return r["number"]
            continue
        return f"{pre}{n:04d}"
    abort(503)

BILL_LIVE_KEYS = ("issue_url", "back", "review", "reviewed", "approve", "pdf_url")

def issued_numbers(sid):

    return [r_["number"] for r_ in q("SELECT number FROM doc_numbers WHERE session_id=? ORDER BY issued, number", (sid,))]

def _bill_nav(sid, receipt):
    t_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)["type"]
    return dict(issue_url=url_for("bill_issue", sid=sid, kind="receipt" if receipt else "invoice"), back=stage_url(sid),
                reviewed=doc_reviewed("terms", t_), approve=dict(doc="terms", type=t_), review=review_url("terms", t_),
                pdf_url=url_for("receipt_pdf" if receipt else "invoice_pdf", sid=sid))

def bill_doc_html(data, legacy=False):

    if legacy:
        import formats as _fm
        return render_template("_bill_doc.html", v=data, money=_fm.money_before_15)
    return render_template("_bill_doc.html", v=data)

def _as_1_0_0(sid, data):

    credits = [r_["amount"] for r_ in q("""SELECT c.amount FROM refund_credits c JOIN payments p ON p.id=c.payment_id
                                           WHERE p.session_id=? AND p.voided IS NULL ORDER BY p.id""", (sid,))
               if r_["amount"] > 0.005]
    if not credits:
        return data
    at = q("""SELECT COUNT(*) n FROM lines WHERE session_id=? AND NOT COALESCE(kind IN ('discount','tax'), 0)""",
           (sid,), one=True)["n"]
    lines = list(data["lines"])
    lines[at:at] = [dict(label="Refund", amount=-round(c_, 2)) for c_ in credits]
    off = round(sum(credits), 2)
    return dict(data, lines=lines, total=round(data["total"] - off, 2), refund_lines_1_0_0=off, refund_credit=0.0)

def freeze_bill(sid, receipt, number, fresh=False):

    r_ = q("SELECT data, html FROM bill_snapshots WHERE number=?", (number,), one=True)
    if r_:
        data = json.loads(r_["data"])
        if r_["html"] is None:
            html = bill_doc_html(data, legacy=True)
            x("UPDATE bill_snapshots SET html=? WHERE number=? AND html IS NULL", (html, number))
        else:
            html = r_["html"]
        return dict(data, frozen_html=Markup(html))
    v = bill_ctx(sid, receipt, live=True)
    data = {k_: v_ for k_, v_ in v.items() if k_ not in BILL_LIVE_KEYS}
    data["terms_reviewed"] = bool(v.get("reviewed"))
    if not fresh:
        data = _as_1_0_0(sid, data)
    html = bill_doc_html(data)
    x("INSERT OR IGNORE INTO bill_snapshots (number, session_id, kind, at, sig, data, html) VALUES (?,?,?,?,?,?,?)",
      (number, sid, "R" if receipt else "INV", now_local(), _lines_sig(sid), json.dumps(data), html))
    return dict(data, frozen_html=Markup(html))

def _freeze_bill_pages():

    for r_ in q("SELECT number FROM bill_snapshots WHERE html IS NULL ORDER BY number"):
        try:
            row = q("SELECT data FROM bill_snapshots WHERE number=?", (r_["number"],), one=True)
            x("UPDATE bill_snapshots SET html=? WHERE number=? AND html IS NULL",
              (bill_doc_html(json.loads(row["data"]), legacy=True), r_["number"]))
        except Exception:
            app.logger.exception("freezing issued document %s", r_["number"])

def bill_ctx(sid, receipt=False, live=False):

    kind_ = "R" if receipt else "INV"
    num_ = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, kind_), one=True)
    if num_ and not live:
        frozen_ = freeze_bill(sid, receipt, num_["number"])
        v = dict(frozen_, **_bill_nav(sid, receipt))

        v["approve"] = None
        if "terms_reviewed" in frozen_:
            v["reviewed"] = frozen_["terms_reviewed"]
        t_ = totals(sid)
        v["since"] = ""
        if not receipt and abs(t_["paid"] - v["paid"]) > 0.005:
            v["since"] = ("This is the invoice as issued. It has since been paid in full." if t_["due"] <= 0.005 else
                          f"This is the invoice as issued. Payments since then leave {money(t_['due'])} to pay.")
        return v
    s = session_full(sid)
    t = totals(sid)
    lines = [dict(label=l["label"], amount=l["amount"]) for l in
             q("SELECT * FROM lines WHERE session_id=? ORDER BY kind IN ('discount','tax'), kind='tax', id", (sid,))]
    pays = [dict(date=long_date(p_["received"]), kind=p_["kind"], method=p_["method"], ref=p_["ref"] or "", amount=p_["amount"]) for p_ in
            q("SELECT * FROM payments WHERE session_id=? AND voided IS NULL AND kind != 'tip' ORDER BY received", (sid,))]
    payer = next(iter(client_people(s["household_id"], s["type"])), None)
    couple = couple_names(couple_of(s["household_id"], s["type"], [s["shoot_date"]]))
    if couple:
        payer = dict(name=couple)
    last = q("SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,), one=True)
    inv_ = q("SELECT issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
    bd = balance_due_date(s["shoot_date"], s["type"], issued=(inv_["issued"] if inv_ else (today().isoformat() if not receipt else None)))
    net = s["type"] in C.NET_TERMS_TYPES
    lic = s["type"] in C.LICENSE_TYPES
    when_paid = max((p_["received"] for p_ in q("SELECT received FROM payments WHERE session_id=? AND voided IS NULL", (sid,))), default=None)

    dep_ = round(t["deposit_short"], 2) if (not receipt and s["stage"] in ("inquiry", "quoted", "booked")
                                           and t["deposit_short"] > 0.005 and t["due"] > 0.005) else 0.0
    dep_ = min(dep_, round(t["due"], 2))
    rest_ = round(max(t["due"], 0.0) - dep_, 2)
    rest_when = (f"by {long_date(bd)}" if (net or s["type"] in C.BEFORE_TYPES) and bd else "on delivery")
    dep_words = C.dw(f"The {money(dep_)} deposit is due now and holds your date."
                     + (f" The balance of {money(rest_)} is due {rest_when}." if rest_ > 0.005 else "")) if dep_ else ""
    return dict(receipt=receipt, number=num_["number"] if num_ else "", since="",
                deposit_due=dep_, after_deposit=rest_ if dep_ else 0.0, after_deposit_when=rest_when if dep_ else "",
                credit=round(-t["due"], 2) if t["due"] < -0.005 else 0.0, written_off=round(t["written_off"], 2),
                refund_credit=round(t["refund_credit"], 2),

                issue_url=url_for("bill_issue", sid=sid, kind="receipt" if receipt else "invoice"),
                date_long=long_date(when_paid if receipt and when_paid else today().isoformat()),
                client=s["household"], client_address=s["address"] or "",
                bill_to=(company_of(s["household_id"]) or (s["household"] if s["payer_event"] else None)
                         or (payer["name"] if payer else s["household"])),
                attn=(payer["name"] if payer and (company_of(s["household_id"]) or (s["payer_event"] and payer["name"] != s["household"])) else ""),
                job_address=(json.loads(s["intake"] or "{}").get("address") or "").strip(),
                type_label=s["event_name"] or (s["title"] if s["payer_event"] and s["title"] else C.doc_label(s["type"])),
                shoot_date_long=long_date(s["shoot_date"]),
                lines=lines, total=t["quoted"], payments=pays, paid=t["paid"], balance=max(t["due"], 0.0),
                **({"included": included_taxes(sid)} if included_taxes(sid) else {}),

                **({"schedule": sch_} if not receipt and (sch_ := payment_schedule(sid, t, paid=True)) else {}),
                no_charge=nothing_to_pay(sid, t),
                due_rule=(long_date(bd) if (net or s["type"] in C.BEFORE_TYPES) and bd else "On delivery"),
                due_sentence=dep_words or (f"Due by {long_date(bd)}." if (net or s["type"] in C.BEFORE_TYPES) and bd else
                                           "Due on delivery: your photos are released on payment."),
                license=lic, event_family=bool(s["event_id"]), checks="check" in C.PAYMENT_METHODS,

                agreement_signed=bool(_signed_on(plan_head(sid), "agreement")),
                pay_words=(lambda w: w[:1].upper() + w[1:])(C._or_list(C.payment_methods()) or "ask me how to pay"),
                pay_details=" ".join((C.pay_details() or "").split()),
                photographer=setting("photographer_name"), business=setting("business_name"), address=setting("business_address"),
                tax_id=setting("business_tax_id"), bank=setting("bank_details"), po=(s["po_number"] or "").strip(),
                quote_id=last["id"] if last else None, back=stage_url(sid),
                replaces=(lambda r_: r_["number"] if r_ else "")(q(
                    "SELECT number FROM doc_numbers WHERE session_id=? AND kind LIKE ? ORDER BY issued DESC, number DESC",
                    (sid, kind_ + "-replaced:%"), one=True)),
                reviewed=doc_reviewed("terms", s["type"]), approve=dict(doc="terms", type=s["type"]), review=review_url("terms", s["type"]),
                pdf_url=url_for("receipt_pdf" if receipt else "invoice_pdf", sid=sid))

def statement_ctx(hid, month):

    h = q("SELECT * FROM households WHERE id=?", (hid,), one=True) or abort(404)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month or ""):
        abort(400)
    try:
        y_, m_ = (int(v_) for v_ in month.split("-"))
        first = date(y_, m_, 1)
    except ValueError:
        abort(400)
    if not 1900 <= y_ <= 2100:
        abort(400)
    nxt = (first + timedelta(days=32)).replace(day=1)
    a_, b_ = first.isoformat(), nxt.isoformat()
    def label(r_):
        where_ = ((json.loads(r_["intake"] or "{}").get("address") or "").strip().splitlines() or [""])[0]
        return ((r_["title"] or C.SESSION_TYPES.get(r_["type"], r_["type"])) + (f", {nice(r_['shoot_date'])}" if r_["shoot_date"] else "")
                + (f", {where_}" if where_ else ""))

    info = {r_["sid"]: r_ for r_ in q("""SELECT id AS sid, title, type, shoot_date, intake FROM sessions WHERE household_id=?""", (hid,))}
    invs, has = [], set()
    for r_ in q("""SELECT c.* FROM charges c JOIN sessions s ON s.id=c.session_id WHERE s.household_id=? ORDER BY c.at, c.id""",
                (hid,)):
        has.add(r_["session_id"])
        invs.append(dict(info[r_["session_id"]], number=r_["number"], issued=r_["at"], amount=r_["amount"], kind=r_["label"]))
    for sid_ in info:
        if sid_ not in has:
            amt_, number_, label_, day_ = _charge_target(sid_)
            if abs(amt_) > 0.005:
                invs.append(dict(info[sid_], number=number_, issued=day_, amount=amt_, kind=label_))
    p_invs, p_pays = (app.config.get("prints_statement") or (lambda h_: ([], [])))(hid)

    p_info = dict(info)
    for sid_ in {i_["sid"] for i_ in p_invs} | {p_["sid"] for p_ in p_pays}:
        if sid_ not in p_info:
            r_ = q("""SELECT s.id AS sid, s.title, s.type, s.shoot_date, '{}' AS intake, h.name AS household FROM sessions s
                      JOIN households h ON h.id=s.household_id WHERE s.id=?""", (sid_,), one=True)
            if r_:
                p_info[sid_] = dict(r_, title=f"Prints from the gallery of {r_['household']}", shoot_date=None)
    for i_ in p_invs:
        if i_["sid"] in p_info:
            invs.append(dict(p_info[i_["sid"]], number=i_["number"], issued=i_["issued"], amount=i_["amount"], kind=i_["kind"]))
    invs.sort(key=lambda i_: i_["issued"] or "")
    pays = q("""SELECT p.amount, p.received, p.kind, p.method, s.id AS sid, s.title, s.type, s.shoot_date, s.intake FROM payments p
                JOIN sessions s ON s.id=p.session_id WHERE s.household_id=? AND p.voided IS NULL AND p.kind != 'tip'
                ORDER BY p.received, p.id""", (hid,))

    pays = sorted([dict(p_) for p_ in pays] + [dict(c_) for c_ in q(
        """SELECT c.amount, p.received, 'credit (refund, not owed)' AS kind, '' AS method, s.id AS sid, s.title, s.type,
                  s.shoot_date, s.intake FROM refund_credits c JOIN payments p ON p.id=c.payment_id JOIN sessions s ON s.id=p.session_id
           WHERE s.household_id=? AND p.voided IS NULL""", (hid,))] + [dict(w_) for w_ in q(
        """SELECT w.amount, w.at AS received, CASE w.why WHEN 'paid' THEN 'write-off reversed (paid after all)'
                  WHEN 'unpaid' THEN 'written off again (the payment was voided)'
                  WHEN 'repriced' THEN 'write-off reversed (new quote)'
                  ELSE 'written off (not owed)' END AS kind,
                  '' AS method, s.id AS sid, s.title, s.type, s.shoot_date, s.intake FROM write_offs w
           JOIN sessions s ON s.id=w.session_id WHERE s.household_id=? ORDER BY w.id""", (hid,))],
        key=lambda p_: p_["received"] or "")
    pays = sorted(pays + [dict(p_info[p_["sid"]], amount=p_["amount"], received=p_["received"], kind=p_["kind"], method=p_["method"])
                          for p_ in p_pays if p_["sid"] in p_info], key=lambda p_: p_["received"] or "")
    opening = round(sum(i_["amount"] for i_ in invs if (i_["issued"] or "") < a_) - sum(p_["amount"] for p_ in pays if (p_["received"] or "") < a_), 2)
    inv_rows = [dict(date=long_date(i_["issued"]), number=i_["number"] or i_.get("kind", ""), what=label(i_), amount=i_["amount"],
                     due_on="" if not i_["number"] else long_date(balance_due_date(i_["shoot_date"], i_["type"], issued=i_["issued"])) or "")
                for i_ in invs if a_ <= (i_["issued"] or "") < b_]
    pay_rows = [dict(date=long_date(p_["received"]), what=label(p_), kind=("refund" if p_["amount"] < 0 and p_["kind"] in PAYMENT_KINDS else p_["kind"]),
                     method=p_["method"], amount=p_["amount"]) for p_ in pays if a_ <= (p_["received"] or "") < b_]
    closing = round(opening + sum(r_["amount"] for r_ in inv_rows) - sum(r_["amount"] for r_ in pay_rows), 2)
    standing = {}
    for i_ in invs:
        standing[i_["sid"]] = standing.get(i_["sid"], 0.0) + i_["amount"]
    invoiced = {k_ for k_, v_ in standing.items() if v_ > 0.005}
    not_invoiced = [dict(what=label(r_), total=totals(r_["id"])["quoted"]) for r_ in q(
        """SELECT * FROM sessions WHERE household_id=? AND stage NOT IN ('lost','closed') ORDER BY COALESCE(shoot_date,'9999')""", (hid,))
        if r_["id"] not in invoiced and totals(r_["id"])["quoted"] > 0.005]
    payer = q("""SELECT name FROM people WHERE household_id=? AND role IN ('parent','adult') ORDER BY """ + BOOKER_FIRST + """, id
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

PLUGINS_PAGE = """{% extends "base.html" %}{% from "_macros.html" import help with context %}{% import "_settings_ui.html" as sui with context %}{% block title %}Plugins - Lumen Aureum{% endblock %}{% block body %}
{{ sui.head('Plugins') }}
<p class="sui-tip" style="max-width:760px">Turning one off keeps everything it recorded; turning it on again brings it all back.{{ help("An administrator can keep a plugin off for this whole install with the CRM_PLUGINS_OFF setting. Then the switch here can't turn the plugin on.") }}</p>
{% if plugin_errors %}<div class="err" id="plugin-errors" style="max-width:760px">Plugins that didn't load at the last start: {{ plugin_errors|join(', ') }}. Fix or remove each one, then restart Lumen Aureum.</div>{% endif %}
{% if ask %}<div class="card f" style="max-width:760px;border-left:3px solid var(--warn)" role="alert"><p style="margin:0">{{ ask.words }}</p>
  <div class="row" style="gap:8px;margin-top:8px"><a class="go" href="{{ ask.finish }}">Finish them first &rsaquo;</a>
  <form method="post" action="{{ url_for('plugins_settings') }}" class="inline"><input type="hidden" name="name" value="{{ ask.name }}"><input type="hidden" name="anyway" value="1"><button class="danger">Turn off anyway</button></form></div></div>{% endif %}
{% for p in rows %}<div class="card f" style="max-width:760px" id="plg-{{ p.name }}">
  <div class="row" style="gap:10px;align-items:center"><span class="grow"><b>{{ p.label }}</b> <span class="mute" style="font-size:13px">{{ p.status }}</span>{{ help(p.tech) }}</span>
  {% if p.switch %}<form method="post" action="{{ url_for('plugins_settings') }}" class="inline" data-busy-says="{{ 'Turning off...' if p.on else 'Turning on...' }}"><input type="hidden" name="name" value="{{ p.name }}">{% if p.on %}<button class="quiet">Turn off</button>{% else %}<input type="hidden" name="turn" value="on"><button class="primary">Turn on</button>{% endif %}</form>{% endif %}</div>
  {% if p.about %}<p class="sub" style="margin:4px 0 0">{{ p.about }}</p>{% endif %}
  {% if p.next %}<p style="margin:8px 0 0;border-left:3px solid var(--gold);padding:2px 10px">{{ p.next }}{% for w_, href_ in p.links %} <a class="go" href="{{ href_ }}">{{ w_ }} &rsaquo;</a>{% endfor %}</p>{% endif %}
</div>{% endfor %}
{% endblock %}"""

PLUGIN_WORDS = {
    "gallery": ("Online galleries", "Builds each client's private gallery and puts it on your web host."),
    "letters": ("Delivery letters", "Prints the gallery letter with its QR code, the copyright transfer, and SD card labels."),
    "print_core": ("Print order safekeeping", "Collects every print order a client sends and keeps print money in "
                                              "Finances, even while Print orders is off."),
    "retention": ("Keeping and deleting shoot files", "Deletes a shoot's unedited files on the day your agreement names, "
                                                      "with a reminder on Today a week before."),
    "studio_page": ("Studio page", "A small public website for your studio, with home, about, and contact pages."),
}

def _plugin_rows():
    names = [p_["name"] for p_ in PLUGINS_LOADED] + sorted(n_ for n_ in DISABLED_PLUGINS
                                                           if n_ not in {p_["name"] for p_ in PLUGINS_LOADED})
    rows = []
    for n_ in names:
        sw = SWITCHES.get(n_)
        got = next((p_ for p_ in PLUGINS_LOADED if p_["name"] == n_), None)
        where = f"File: {got['label'] if got else n_}.py."
        if n_ in CORE_PLUGINS:
            status, switch, tech = "Always on", False, f"Part of the core of Lumen Aureum: it can't be turned off. {where}"
        elif n_ in DISABLED_PLUGINS:
            status, switch = "Off for this install", False
            tech = (f"An administrator turned this plugin off with CRM_PLUGINS_OFF, so the switch here can't turn the "
                    f"plugin on. {where}")
        else:
            status, switch = (("On" if plugin_on(n_) else "Off"), True) if sw else ("On", False)
            tech = f"An administrator can keep this plugin off for the whole install with CRM_PLUGINS_OFF. {where}"
        label, about = PLUGIN_WORDS.get(n_, (n_, ""))
        if got and not got["built_in"]:
            about = about or f"Your own plugin, from {got['folder']}."
        on = plugin_on(n_)
        nxt, links = "", []
        if sw and on and sw.get("when_on"):
            try:
                nxt = sw["when_on"]() or ""
                links = [(w_, url_for(e_)) for w_, e_ in sw.get("links") or []] if nxt else []
            except Exception:
                app.logger.exception("plugin %s: when_on failed", n_)
                nxt, links = "", []
        rows.append(dict(name=n_, label=(sw or {}).get("label") or label, about=(sw or {}).get("about") or about,
                         status=status, tech=tech, switch=switch, on=on, next=nxt, links=links))
    return rows

@app.route("/settings/plugins", methods=["GET", "POST"])
def plugins_settings():
    ask = None
    if request.method == "POST":
        name = request.form.get("name", "")
        sw = SWITCHES.get(name)
        if not sw or name in DISABLED_PLUGINS or not any(p_["name"] == name for p_ in PLUGINS_LOADED):
            session["notice"] = "That plugin has no switch here."
            g.refused = True
            return redirect(url_for("plugins_settings"))
        on = request.form.get("turn") == "on"
        if on == plugin_on(name):
            g.refused = True
            return redirect(url_for("plugins_settings"))
        if not on and sw.get("before_off") and request.form.get("anyway") != "1":
            words = sw["before_off"]() or ""
            if words:
                g.refused = True
                ask = dict(name=name, words=words, finish=url_for("home"))
                return render_template_string(PLUGINS_PAGE, rows=_plugin_rows(), ask=ask, plugin_errors=PLUGIN_ERRORS)
        if not on and sw.get("before_off") and request.form.get("anyway") == "1":
            words = sw["before_off"]() or ""
            if words:
                g.audit_note = "turned off anyway: " + words.split(".")[0].lower()
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
          (f"plugin_on_{name}", "1" if on else "0"))
        if not on:
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (f"plugin_off_at_{name}", datetime.now().isoformat(timespec="seconds")))

            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (f"plugin_off_n_{name}", str(int(setting(f"plugin_off_n_{name}") or 0) + 1)))
        session["notice"] = f"{sw['label']} turned {'on' if on else 'off'}."
        return redirect(safe_back(request.form.get("back")) or url_for("plugins_settings"))
    return render_template_string(PLUGINS_PAGE, rows=_plugin_rows(), ask=ask, plugin_errors=PLUGIN_ERRORS)

@app.get("/settings/plugin/<name>")
def plugin_page(name):

    import settings_registry as R_
    p_ = next((p_ for p_ in R_.pages() if p_.get("plugin") == name), None)
    loaded = any(x_["name"] == name for x_ in PLUGINS_LOADED)
    if not p_ or not (loaded or name in DISABLED_PLUGINS):
        abort(404)
    if plugin_on(name) and p_["ep"] in app.view_functions:
        return redirect(url_for(p_["ep"], **p_["args"]))
    sw = SWITCHES.get(name) or {}
    label = sw.get("label") or PLUGIN_WORDS.get(name, (p_["label"], ""))[0]
    about = sw.get("about") or PLUGIN_WORDS.get(name, ("", ""))[1]
    can = bool(sw) and loaded and name not in DISABLED_PLUGINS
    return render_template("plugin_off.html", page=p_, label=label, about=about, name=name, can=can,
                           back=url_for("plugin_page", name=name))

app.config.setdefault("ep_words", {})["plugins_settings"] = lambda f: (
    f"Turned {(SWITCHES.get(f.get('name')) or {}).get('label') or f.get('name')} {'on' if f.get('turn') == 'on' else 'off'}")

def issue_bill(sid, receipt=False):

    had = q("SELECT 1 FROM doc_numbers WHERE session_id=? AND kind=?", (sid, "R" if receipt else "INV"), one=True)
    number = doc_number(sid, "R" if receipt else "INV")
    freeze_bill(sid, receipt, number, fresh=not had)
    if not receipt:
        sync_charges(sid)
    return number

def _receipt_waits(sid):

    t_ = totals(sid)
    return (t_["due"] > 0.005 or t_["paid"] <= 0.005) and not q(
        "SELECT 1 FROM doc_numbers WHERE session_id=? AND kind='R'", (sid,), one=True)

BILL_THEN = ("download", "print", "email")

@app.post("/session/<int:sid>/<any(invoice, receipt):kind>/issue")
def bill_issue(sid, kind):

    t_ = totals(sid)
    word = "Receipt" if kind == "receipt" else "Invoice"
    had = q("SELECT number FROM doc_numbers WHERE session_id=? AND kind=?", (sid, "R" if kind == "receipt" else "INV"), one=True)
    if kind == "receipt" and _receipt_waits(sid):
        session["notice"] = ("There is nothing to pay for this session, so it needs no receipt. Nothing was issued."
                             if nothing_to_pay(sid, t_) else "A receipt is for a session paid in full. Nothing was issued.")
        g.refused = True
    elif kind == "invoice" and not had and request.form.get("no_charge") == "1" and nothing_to_pay(sid, t_):

        g.audit_note = issue_bill(sid)
        session["notice"] = f"No-charge invoice {g.audit_note} issued."
        return redirect(safe_back(request.form.get("back")) or url_for("invoice", sid=sid))
    elif kind == "invoice" and t_["quoted"] <= 0.005 and not had:
        session["notice"] = ("There is nothing to pay for this session, so it needs no invoice. Nothing was issued."
                             if nothing_to_pay(sid, t_) else
                             "There's nothing to invoice yet. Add the session's prices first, then issue the invoice.")
        g.refused = True
    else:
        g.audit_note = issue_bill(sid, kind == "receipt")
        if had:
            g.refused = True
        else:
            session["notice"] = f"{word} {g.audit_note} issued."
        then = request.form.get("then")
        if then in BILL_THEN:
            session["doc_then"] = then
    return redirect(url_for(kind, sid=sid))

def _on_plan_v(sid, v):

    return dict(v, waiting=True, on_plan=plan_head_label(sid), plan_quote=url_for("session_quote", sid=plan_head(sid)))

def _bill_page(sid, receipt):
    v = bill_ctx(sid, receipt=receipt)
    if not receipt and not v["number"] and v.get("no_charge"):
        return render_template("invoice.html", v=dict(v, waiting=True, free=True), send=None, notice=session.pop("notice", None))
    if not v["number"] and billed_on_plan(sid):
        return render_template("invoice.html", v=_on_plan_v(sid, v), send=None, notice=session.pop("notice", None))
    if not receipt and not v["number"] and not v.get("lines"):
        return render_template("invoice.html", v=dict(v, waiting=True, unpriced=True), send=None, notice=session.pop("notice", None))
    word = "Receipt" if receipt else "Invoice"
    what_ = _for_what(v.get("type_label"), v.get("shoot_date_long"))
    if receipt:
        lines_ = [f"Receipt {v['number']} for {what_}: {money(v.get('total') or 0)}, paid in full. Thank you."]
    else:
        t_ = totals(sid)
        due_ = max(t_["due"], 0.0)
        free_ = nothing_to_pay(sid, t_)
        lines_ = [f"Invoice {v['number']} for {what_}: " + ("no charge." if free_ else f"{money(v.get('total') or 0)}.")
                  + (f" Paid so far: {money(t_['paid'])}. Balance: {money(due_)}." if t_["paid"] > 0.005 else "")]
        lines_ += ([v.get("due_sentence") or "", how_to_pay_line()] if due_ > 0.005 else
                   ["There is nothing to pay for this session."] if free_ else ["It is paid in full. Thank you."])
        lines_ = [l_ for l_ in lines_ if l_]
    then = session.pop("doc_then", None) if v["number"] else None
    send_ = send_ctx(sid, "receipt" if receipt else "invoice", f"{word} {v['number']}" if v["number"] else word, lines_)
    if not receipt and not send_.get("task"):
        st_ = q("SELECT stage FROM sessions WHERE id=?", (sid,), one=True)
        if st_ and st_["stage"] in ("quoted", "booked"):
            if q("SELECT 1 FROM invoice_sent WHERE session_id=?", (sid,), one=True):
                n_ = q("SELECT issued FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,), one=True)
                send_["sent"] = send_.get("sent") or (nice(n_["issued"]) if n_ and n_["issued"] else "")
            elif totals(sid)["deposit_short"] > 0.005:
                send_["mark_url"] = url_for("deposit_invoice_sent", sid=sid)
    return render_template("invoice.html", v=v, then=then, notice=session.pop("notice", None), send=send_)

@app.get("/session/<int:sid>/invoice")
def invoice(sid):
    return _bill_page(sid, False)

@app.get("/session/<int:sid>/invoice.pdf")
def invoice_pdf(sid):
    v = bill_ctx(sid)
    if not v["number"] and (billed_on_plan(sid) or not (v.get("lines") or v.get("no_charge"))):
        return redirect(url_for("invoice", sid=sid))
    return html_pdf(render_template("invoice.html", v=v, pdf=True), file_slug(v["number"] or "invoice-draft", v["client"]) + ".pdf")

@app.get("/session/<int:sid>/receipt")
def receipt(sid):
    if _receipt_waits(sid) and billed_on_plan(sid):
        return render_template("invoice.html", v=_on_plan_v(sid, bill_ctx(sid, receipt=True)), send=None,
                               notice=session.pop("notice", None))
    if _receipt_waits(sid):
        t_ = totals(sid)
        v = dict(bill_ctx(sid, receipt=True), waiting=True, due_now=t_["due"], inv_url=url_for("invoice", sid=sid))
        v["free"] = v.get("no_charge")
        return render_template("invoice.html", v=v, send=None, notice=session.pop("notice", None))
    return _bill_page(sid, True)

@app.get("/session/<int:sid>/receipt.pdf")
def receipt_pdf(sid):
    if _receipt_waits(sid):
        return redirect(url_for("invoice_pdf", sid=sid))
    v = bill_ctx(sid, receipt=True)
    return html_pdf(render_template("invoice.html", v=v, pdf=True), file_slug(v["number"] or "receipt-draft", v["client"]) + ".pdf")

def household_contact(people):

    adults = [p for p in people if p["role"] in ("parent", "adult")]
    b_ = min((p for p in adults if not p["is_minor"]), key=lambda p: p["id"], default=None)
    adults = ([b_] if b_ is not None else []) + [p for p in adults if p is not b_]
    return next((p for p in adults if (p["phone"] or "").strip() or (p["email"] or "").strip()), adults[0] if adults else None)

def detail_stops(s):

    stops = None
    for fn in hooks.detail_locations:
        try:
            stops = fn(s)
        except Exception:
            app.logger.exception("detail locations")
        if stops:
            break
    return stops or [dict(time=clock(s["start_time"]) if i == 0 else "", name=n, note="")
                     for i, n in enumerate(l_.strip() for l_ in (s["stops"] or "").splitlines() if l_.strip())]

def details_ctx(sid):
    s = session_full(sid)
    stops = detail_stops(s)
    mins = session_minutes(sid, s["type"])
    meet = ((f"{clock(s['start_time'])}" if s["start_time"] else "Time to be confirmed")
            + (f" at {stops[0]['name']}" if stops else ", place to be confirmed"))
    return dict(sid=sid, client=s["household"], people=oxford(subjects_of(sid)), type_label=C.doc_label(s["type"]),
                date_long=long_date(s["shoot_date"]), meet=meet.strip(), stops=stops,
                length=(f"{mins // 60} hr {mins % 60} min" if mins % 60 and mins >= 60 else f"{mins // 60} hr" if mins >= 60 else f"{mins} min").replace(" 0 min", ""),
                photographer=setting("photographer_name"), business=setting("business_name"), back=stage_url(sid),
                outfits=C.POLICY[s["type"]]["outfits"], wear=C.wear_text(s["type"]), weather=C.fill(s["type"], "weather"),
                animals=s["type"] in C.ANIMAL_TYPES,
                reviewed=doc_reviewed("terms", s["type"]), approve=dict(doc="terms", type=s["type"]), review=review_url("terms", s["type"]))

@app.get("/session/<int:sid>/details")
def session_details(sid):
    v = details_ctx(sid)
    facts = [f"Your session sheet: {_for_what(v.get('type_label'), v.get('date_long'))}."
             + (f" Meet: {v['meet']}." if v.get("meet") else "") + (f" About {v['length']}." if v.get("length") else "")]
    return render_template("session_details.html", v=v, send=send_ctx(sid, "details", "Your session sheet", facts))

@app.get("/session/<int:sid>/details.pdf")
def session_details_pdf(sid):
    return html_pdf(render_template("session_details.html", v=details_ctx(sid), pdf=True), file_slug("session-details", client_slug(sid), today().isoformat()) + ".pdf")

def event_rights_text(eid, voice="photographer", photographer="", person="", minor=False):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) if eid else None
    if not e:
        return ""
    r_ = e["rights"] or "families"
    people_ = event_nouns(e)[0] == "person"
    if r_ == "custom":
        note_ = (e["rights_note"] or "").strip()
        if voice == "release" and note_ and _first_person(note_):

            return f'{photographer or "The photographer"}\'s terms for the event photos: "{note_}"'
        return note_
    if voice == "release":
        if people_:
            return C.EVENT_RIGHTS_RELEASE_PEOPLE[r_].format(
                photographer=photographer or "The photographer",
                receiver=f"{person} receives" if minor and person else "I receive",
                own="their own" if minor and person else "my own",
                whose=_possessive(person) if minor and person else "my")
        return C.EVENT_RIGHTS_RELEASE[r_].format(photographer=photographer or "The photographer", person=person or "my child")
    if people_:
        if voice == "organizer":
            return C.EVENT_RIGHTS_ORGANIZER_PEOPLE.get(r_) or C.EVENT_RIGHTS_PEOPLE.get(r_, "")
        return C.EVENT_RIGHTS_PEOPLE.get(r_, "")
    if voice == "organizer":
        return C.EVENT_RIGHTS_ORGANIZER.get(r_) or C.EVENT_RIGHTS.get(r_, "")
    return C.EVENT_RIGHTS.get(r_, "")

def _first_person(words):

    return bool(re.search(r"\bI\b|\b(?:me|my|mine|myself)\b", words) or re.search(r"\b(?:Me|My|Mine|Myself)\b", words))

def release_ctx(did):
    dc = q("""SELECT d.*, COALESCE(NULLIF(d.covers, ''), p.name) AS person FROM documents d
              LEFT JOIN people p ON p.id=d.about_person WHERE d.id=?""", (did,), one=True) or abort(404)
    s = session_full(dc["session_id"])
    ev = q("SELECT * FROM events WHERE id=?", (s["event_id"],), one=True) if s["event_id"] else None
    ck = (dc["covers_kind"] or "") if (dc["covers"] or "").strip() else ""
    who_ = q("SELECT * FROM people WHERE id=?", (dc["about_person"],), one=True) if dc["about_person"] else None
    minor_ = minor_on(who_, s["shoot_date"] or (ev["date"] if ev else None)) if who_ else False
    return dict(person=dc["person"] or s["household"], minor=minor_ if not ck else ck == "minor",
                guest=ck in ("guest", "minor"), property=ck == "property" or dc["kind"] == "property release",
                client=s["household"], portfolio_no=bool(s["portfolio_no"]), signer=("" if (dc["signer"] or "").strip() in ("Parent (required)", "Parent or guardian (required)") else re.sub(r"^Parent(?: or guardian)?:\s*", "", dc["signer"] or "")),
                event_rights=event_rights_text(s["event_id"], voice="release", person=dc["person"] or s["household"],
                                               photographer=setting("photographer_name") or setting("business_name"),
                                               minor=minor_ if not ck else ck == "minor"),
                people_event=bool(ev) and event_nouns(ev)[0] == "person",
                license=s["type"] in C.LICENSE_TYPES, stype=s["type"], company=company_of(s["household_id"]),
                trade_copies=bool(s["trade_copies"]),
                event=bool(ev), what=ev["name"] if ev else C.doc_label(s["type"]),
                date_long=long_date(ev["date"] if ev else s["shoot_date"]),
                photographer=setting("photographer_name") or "[YOUR NAME]", business=setting("business_name"),
                reviewed=doc_reviewed("release", s["type"]), review=review_url("release", s["type"]),
                approve=dict(doc="release", type=s["type"]) if dc["status"] != "signed" else None,
                did=did, unsigned=dc["status"] != "signed",
                back=stage_url(s["id"]), pdf_url=url_for("release_pdf", did=did))

def _family_money(did):

    r_ = q("""SELECT s.id, e.* FROM documents d JOIN sessions s ON s.id=d.session_id JOIN events e ON e.id=s.event_id
              WHERE d.id=?""", (did,), one=True)
    if not r_ or r_["billing"] != "families":
        return ""
    t_ = totals(r_["id"])
    if t_["quoted"] <= 0.005:
        return ""
    dep_ = t_.get("deposit_req") or 0
    if event_nouns(r_)[0] == "person":
        return (f"My price is {money(t_['quoted'])}."
                + (f" {money(dep_)} is due to reserve my photos, and the rest is due when the photos are delivered."
                   if 0.005 < dep_ < t_["quoted"] - 0.005 else " It is due before the photos are delivered.")
                + " If the event is canceled, or moved to a date I cannot come, or the photographer cannot photograph it,"
                  " everything I paid is refunded." + (" The deposit reserves my place: the photographer turns away other work"
                                                     " to hold it. If I do not come, the photographer keeps the deposit for"
                                                     " that reason." if 0.005 < dep_ else ""))
    return (f"My family's price is {money(t_['quoted'])}."
            + (f" {money(dep_)} is due to reserve our photos, and the rest is due when the photos are delivered."
               if 0.005 < dep_ < t_["quoted"] - 0.005 else " It is due before the photos are delivered.")
            + " If the event is canceled, or moved to a date we cannot come, or the photographer cannot photograph it,"
              " everything we paid is refunded." + (" The deposit reserves our place: the photographer turns away other work"
                                                 " to hold it. If we do not come, the photographer keeps the deposit for"
                                                 " that reason." if 0.005 < dep_ else ""))

def _possessive(name):
    return name + ("'" if name.endswith("s") else "'s")

def _business_release(v):

    if v["event"] or not v["license"] or C.TYPE_KIND.get(v.get("stype")) != "property":
        return ""
    client_ = v.get("company") or v["client"]
    use_ = C.fill(v["stype"], "license_use")
    if v["property"]:
        return (f"The edited photos are delivered to {client_}, who booked the session, not to the owner. I am the owner of"
                f" the property, or I may give this permission for the owner. I agree that {client_} may use the photos of the"
                f" property under its license from the photographer, which covers {use_}. The photographer may show the"
                " photos in a portfolio and on a website, without the owner's name or contact details. "
                + ("The property's address may be published as part of that use." if C.is_place(v["stype"]) else
                   "The photographer does not publish the property's address with the photos."))
    if v["minor"]:
        p_ = v["person"]
        return (f"The edited photos are delivered to {client_}, who booked the session, not to us. We agree that {client_}"
                f" may use the photos {p_} appears in under its license from the photographer, which covers {use_}."
                f" {_possessive(p_)} name and school are used with the photos only if we agree in writing. The photographer"
                f" may show these photos in a portfolio and on a website, without {_possessive(p_)} name or school. If we"
                f" ask in writing, the photographer takes a photo {p_} appears in down from the portfolio and website within"
                f" 14 days; that does not change {_possessive(client_)} use.")
    return (f"The edited photos are delivered to {client_}, who booked the session, not to me. I agree that {client_} may"
            f" use the photos I appear in under its license from the photographer, which covers {use_}. My name is used"
            " with the photos only if I agree in writing. The photographer may show these photos in a portfolio and on a"
            " website, without my name. If I ask in writing, the photographer takes a photo I appear in down from the"
            f" portfolio and website within 14 days; that does not change {_possessive(client_)} use.")

def _personal_release(v):

    if v["event"] or C.TYPE_KIND.get(v.get("stype")) != "personal":
        return ""
    p_ = v["photographer"] or "The photographer"
    uses_ = ("to show them in a portfolio, on a website and on social media, to exhibit and publish them, to enter them in"
             f" contests, and to sell prints of them. {p_} keeps the copyright.")
    if v["property"]:
        return (f"I am the owner of the property, or I may give this permission for the owner. I agree that {p_} may use the"
                f" photos of the property: {uses_} The owner's name and the property's address are published only if I"
                " agree in writing.")
    if v["minor"]:
        k_ = v["person"]
        we, We, appear, name_, our = "we", "We", f"{k_} appears", f"{_possessive(k_)} name and school are", "our"
    else:
        we, We, appear, name_, our = "I", "I", "I appear", "My name is", "my"
    text_ = (f"{We} agree that {p_} may use the photos {appear} in: {uses_} The photos are not used to advertise another"
             f" business's products or services unless {we} agree in writing. {name_} published with the photos only if"
             f" {we} agree in writing. If {we} ask in writing, {p_} takes a photo {appear} in down from the portfolio,"
             " website and social media within 14 days and does not post it again; prints already sold and copies already"
             " published or shared by others cannot be recalled.")
    if v.get("trade_copies"):
        text_ += (f" In return {we} receive copies of the edited photos {appear} in, for {our} own personal use: to print,"
                  " share and post them, not to sell them.")
    return text_

def release_view(did):

    v = release_ctx(did)
    v["money"] = C.dw(_family_money(did))
    import wording
    own = own_wording("release", q("SELECT s.type FROM documents d JOIN sessions s ON s.id=d.session_id WHERE d.id=?",
                                   (did,), one=True)["type"])
    who = "the family" if v["minor"] else "me"
    used = f"The edited photos are delivered to {who}"
    if v["guest"] or v["property"]:
        used = f"The edited photos are delivered to {v['client']}, who booked the session, not to " + (
            "the owner" if v["property"] else "us" if v["minor"] else "me")
    if v["event"]:
        used += " for personal use: to print, share, and post them, not to sell them"
        if not v.get("people_event"):
            used += f". Group photos that include {v['person']} also go to the other families in the group"
    elif not (v["guest"] or v["property"]):
        used += (", with the license set out in the session agreement" if v["license"] else
                 ", and the copyright in them is transferred as the session agreement says")
    if (not v["event"] and not v["property"] and v["license"] and v.get("company")
            and C.TYPE_KIND.get(v.get("stype")) == "portrait"):

        co_, use_ = v["company"], C.fill(v["stype"], "license_use")
        if v["minor"]:
            p_ = v["person"]
            used = (f"The edited photos are delivered to {co_}, who booked the session. {co_} and we may each use"
                    f" {_possessive(p_)} photos under the license in the session agreement, which covers {use_}. {co_} may"
                    f" use {_possessive(p_)} name and job title with them")
        else:
            used = (f"The edited photos are delivered to {co_}, who booked the session. {co_} and I may each use my photos"
                    f" under the license in the session agreement, which covers {use_}. {co_} may use my name and job title"
                    " with them")
    names_ = f"names{', school' if v['minor'] else ''} or location"
    if v.get("portfolio_no"):
        used += (". The photographer shows no photo from the session in a portfolio, on a website, on social media, or anywhere"
                 f" else, and does not publish {names_} with them.")
    else:
        used += (f". The photographer may show up to {C.MARKETING_PICKS} photos from the session in a portfolio, on a website, and on the"
                 f" business's social media accounts, chosen by the photographer; {'we' if v['minor'] else 'I'} may suggest favorites."
                 f" Before a photo is first shown, {'we' if v['minor'] else 'I'} may say no to it, and it will not be shown; after that, a"
                 " written request to take a photo down is honored within 14 days."
                 f" The photographer shows no other photo from the session, and does not publish {names_} with the photos"
                 " the photographer shows.")
    business_ = _business_release(v) or _personal_release(v)
    if business_:
        used = business_
    v["custom"] = wording.render(own, {
        "PERSON": ("the property " + v["person"]) if v["property"] else v["person"], "PHOTOGRAPHER": v["photographer"] or "the photographer",
        "OCCASION": "the event above" if v["event"] else "the session above", "EVENT RIGHTS": v["event_rights"] or "",
        "HOW THE PHOTOS ARE USED": used,
        "GUARDIAN": f"I am the parent or legal guardian of {v['person']} and may sign this release on their behalf." if v["minor"] else ""})
    v["custom"] = C.dw(v["custom"])
    return v

@app.get("/doc/<int:did>/release")
def release_doc(did):
    v = signed_copy("release", did) or legacy_signed("release", did, release_view(did))
    sid_ = q("SELECT session_id FROM documents WHERE id=?", (did,), one=True)["session_id"]
    who = setting("photographer_name") or setting("business_name") or "your photographer"
    send = send_ctx(sid_, "release", f"Photo release from {who}",
                    [f"The photo release for {v.get('person') or 'your session'}"
                     + (f", {v['date_long']}" if v.get("date_long") else "") + "."
                     + ("" if not v.get("unsigned") else " Please sign it and send it back.")])
    _doc_sent_state(send, sid_, "release", did)
    return render_template("release.html", v=v, send=send, signed_on=_signed_on(None, None, did))

def _doc_sent_state(send, sid, kind, did=None):

    ref_ = plan_head(sid) if kind == "agreement" else sid
    rows_ = q("SELECT status, sent FROM documents WHERE session_id=? AND kind=?" + (" AND id=?" if did else ""),
              (ref_, kind) + ((did,) if did else ()))
    if any(r_["status"] == "needed" and not r_["sent"] for r_ in rows_):
        send.update(mark_url=url_for("docs_mark_sent", sid=sid, kind=kind, did=did), sent="")
    elif rows_ and not send.get("sent"):
        on_ = min((r_["sent"] for r_ in rows_ if r_["sent"]), default="")
        send.update(sent=nice(on_) if on_ else "")

@app.get("/doc/<int:did>/release.pdf")
def release_pdf(did):
    v = signed_copy("release", did) or legacy_signed("release", did, release_view(did))
    return html_pdf(render_template("release.html", v=v, pdf=True, signed_on=_signed_on(None, None, did)), file_slug("release", v["person"], today().isoformat()) + ".pdf")

@app.get("/session/<int:sid>/agreement")
def agreement(sid):
    head = plan_head(sid)
    if head != sid:
        return redirect(url_for("agreement", sid=head))
    a = signed_copy("agreement", sid) or legacy_signed("agreement", sid, agreement_view(sid))
    who = setting("photographer_name") or setting("business_name") or "your photographer"
    send = send_ctx(sid, "agreement", f"Your session agreement with {who}",
                    [f"Your session agreement for {_for_what(a.get('type_label'), a.get('shoot_date_long'))}"
                     + (f": {money(a['total'])}" if a.get("total") else "")
                     + (f", with a {money(a['deposit'])} deposit to hold the date" if a.get("deposit") else "") + "."
                     + ("" if not a.get("unsigned") else " Please read it, sign it and send it back.")])
    _doc_sent_state(send, sid, "agreement")
    rows_, _ = agreement_signers(sid)
    pending = [dict(did=r_["id"], name=r_["pname"] or "") for r_ in rows_ if r_["status"] != "signed"]
    return render_template("agreement.html", a=a, send=send, signed_on=_signed_on(sid, "agreement"), pending=pending,
                           signed_n=sum(1 for r_ in rows_ if r_["status"] == "signed"))

def _signed_on(sid, kind, did=None):

    r_ = q("SELECT MAX(signed) d FROM documents WHERE " + ("id=?" if did else "session_id=? AND kind=?") + " AND status='signed'",
           (did,) if did else (sid, kind), one=True)
    return long_date(r_["d"]) if r_ and r_["d"] else ""

def plan_head(sid):
    r_ = q("SELECT plan_of FROM sessions WHERE id=?", (sid,), one=True)
    return r_["plan_of"] if r_ and r_["plan_of"] else sid

def plan_sessions(head):

    return q("SELECT * FROM sessions WHERE id=? OR plan_of=? ORDER BY COALESCE(shoot_date,'9999'), id", (head, head))

@app.post("/session/<int:sid>/plan")
def join_plan(sid):

    s_ = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)
    to = request.form.get("plan_of", "")
    back = safe_back(request.form.get("back")) or url_for("session_view", sid=sid)
    if not to:
        if s_["plan_of"]:
            x("UPDATE sessions SET plan_of=NULL WHERE id=?", (sid,))
            if s_["type"] in C.NEEDS_AGREEMENT and not q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (sid,), one=True):
                for p_ in default_signers(s_["household_id"], s_["type"], agreement_days(sid)) or [None]:
                    x("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'agreement', ?, ?)",
                      (sid, p_["id"] if p_ else None, p_["name"] if p_ else ""))
            audit("Left the plan; this session has its own agreement again", sid=sid)
            settle_steps(sid)
        return redirect(back)
    head = q("SELECT * FROM sessions WHERE id=? AND household_id=?", (int(to) if to.isdigit() else 0, s_["household_id"]), one=True)
    if not head or head["id"] == sid or head["plan_of"] or q("SELECT 1 FROM sessions WHERE plan_of=?", (sid,), one=True):
        session["notice"] = "Pick another session of this client that isn't itself part of a plan."
        g.refused = True
        return redirect(back)
    x("UPDATE sessions SET plan_of=? WHERE id=?", (head["id"], sid))
    x("DELETE FROM documents WHERE session_id=? AND kind='agreement' AND status != 'signed'", (sid,))
    resign = q("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (head["id"],), one=True) \
        and not plan_covers(head["id"], s_["type"])
    if resign:
        x("""UPDATE documents SET status=CASE WHEN sent IS NOT NULL THEN 'sent' ELSE 'needed' END, signed=NULL
             WHERE session_id=? AND kind='agreement'""", (head["id"],))
    audit(f"Joined the plan of {head['title'] or C.SESSION_TYPES.get(head['type'], head['type'])}: one agreement covers both"
          + ("; the plan's agreement needs signing again" if resign else ""), sid=sid)
    settle_steps(sid)
    return redirect(back)

@app.get("/session/<int:sid>/agreement.pdf")
def agreement_pdf(sid):
    return html_pdf(render_template("agreement.html", a=signed_copy("agreement", plan_head(sid))
                                    or legacy_signed("agreement", plan_head(sid), agreement_view(sid)), pdf=True,
                                    signed_on=_signed_on(plan_head(sid), "agreement")), file_slug("agreement", client_slug(sid), today().isoformat()) + ".pdf")

def fee_groups(only=None):

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
                and not (it.get("unit") == "mile" and not it["amount"] and not rate)]
        used |= {it["key"] for it in sch["items"] if any(it["key"] == k or (k.endswith("_") and it["key"].startswith(k)) for k in keys)}
        if rows:
            groups.append((title, rows))
    other = [(label(it), price(it)) for it in sch["items"] if it["key"] not in used
             and not (stype and it.get("type") and it.get("type") != stype)]
    if other:
        groups.append(("Other", other))
    return dict(sch=sch, groups=groups, as_of=today().strftime("%B %-d, %Y"), hold_days=quote_term(), pdf=pdf,
                stype=stype, type_label=C.SESSION_TYPES.get(stype, "") if stype else "",
                terms=C.build_terms(stype, setting("business_name")) if stype else C.build_terms(business=setting("business_name")),
                editing=C.build_editing(stype) if stype else C.EDITING_POLICY,
                reviewed=doc_reviewed("terms", stype) if stype else doc_reviewed("terms"),
                who=setting("photographer_name"), studio=setting("studio_name"))

@app.get("/fees/sheet")
def fee_sheet():

    stype, sid = request.args.get("type"), request.args.get("sid", type=int)
    ctx_ = fee_sheet_ctx(stype=stype)
    send = None
    if sid and q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True):
        who = setting("photographer_name") or setting("business_name") or "your photographer"
        rows_ = [f"{lab_}: {price_}" for _t, g_ in ctx_["groups"] for lab_, price_ in g_]
        send = send_ctx(sid, "price", f"Price list from {who}", [
            f"My prices{(' (' + ctx_['type_label'] + ')') if ctx_.get('type_label') else ''}, as of {ctx_['as_of']}:",
            "\n".join(rows_[:20] + ([f"and {len(rows_) - 20} more on the full price list"] if len(rows_) > 20 else []))])
    return render_template("fee_sheet.html", send=send, sid=sid, back_url=stage_url(sid) if send else url_for("settings_view", tab="prices"), **ctx_)

@app.get("/fees/sheet.pdf")
def fee_sheet_pdf():
    stype = request.args.get("type")
    return html_pdf(render_template("fee_sheet.html", **fee_sheet_ctx(pdf=True, stype=stype)),
                    f"price-list{'-' + stype if stype in C.SESSION_TYPES else ''}-{today().isoformat()}.pdf")

@app.get("/fees/<int:sched_id>")
def fees_view(sched_id):
    sch = schedule(sched_id) or abort(404)
    return render_template("fees.html", sch=sch, current=current_schedule()["id"], rate=setting("mileage_rate"))

@app.get("/events")
def events():
    rows = q("""SELECT e.*, (SELECT COUNT(*) FROM sessions WHERE event_id=e.id) AS n
                FROM events e ORDER BY e.date DESC""")
    contact = q("SELECT id, name, company FROM households WHERE id=?", (request.args.get("contact", type=int) or 0,), one=True)
    return render_template("events.html", rows=rows, contact=contact)

@app.post("/events")
def event_add():
    f = request.form
    eid = x("INSERT INTO events (id, name, date, venue) VALUES (?,?,?,?)", (next_id("events"), f["name"], f.get("date") or None, f.get("venue", "")))
    c_ = f.get("contact", type=int)
    h_ = q("SELECT company FROM households WHERE id=?", (c_,), one=True) if c_ else None
    if h_:
        x("UPDATE events SET contact_hid=? WHERE id=?", (c_, eid))
        if (h_["company"] or "").strip():

            x("UPDATE events SET billing='organizer' WHERE id=?", (eid,))
            _event_payer(eid, c_)
    return redirect(url_for("event_view", eid=eid))

@app.get("/event/<int:eid>")
def event_view(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    fams = []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.event_id=? ORDER BY h.name""", (eid,)):
        docs = q("SELECT status FROM documents WHERE session_id=? AND kind='release'", (s["id"],))
        subj = q("""SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id
                    WHERE sp.session_id=? ORDER BY p.name COLLATE NOCASE""", (s["id"],))
        parent = q("""SELECT * FROM people WHERE household_id=? AND role IN ('parent','adult')
                      ORDER BY """ + BOOKER_FIRST + """, id LIMIT 1""", (s["household_id"],), one=True)
        fams.append(dict(s=s, subjects=", ".join(p["name"] for p in subj), parent=parent,
                         released=all(dd["status"] == "signed" for dd in docs),
                         t=totals(s["id"]) if e["billing"] == "families" else None,
                         n_docs=len(docs), n_signed=sum(dd["status"] == "signed" for dd in docs)))
    payer = q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.payer_event=?""",
              (eid,), one=True)
    c_ = e["contact_hid"] if "contact_hid" in e.keys() else None
    hh = sorted(q("SELECT id, name FROM households"), key=lambda r_: (r_["id"] != c_, re.sub(r"^the\s+", "", r_["name"].lower())))
    contact = q("SELECT id, name FROM households WHERE id=?", (c_,), one=True) if c_ else None
    return render_template("event.html", e=e, fams=fams, steps=event_steps(eid), sign_day=event_sign_day(e), nouns=event_nouns(e),
                           owing=[f_["s"]["id"] for f_ in fams if f_["t"] and f_["t"]["due"] > 0.005 and f_["s"]["stage"] != "lost"],
                           taxed=bool(taxes_list()),
                           open_from=books_open_from(),
                           payer=payer, households=hh, payer_total=totals(payer["id"]) if payer else None, contact=contact,
                           fam_open=family_open_words(event_family_open(eid)))

@app.post("/event/<int:eid>/family-price")
def event_family_price(eid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    try:
        amt = round(num(request.form.get("amount")), 2)
    except ValueError:
        amt = 0
    if amt > 0:
        x("UPDATE events SET family_price=?, family_tax_in=? WHERE id=?", (amt, 1 if request.form.get("tax_in") else 0, eid))
        e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
        n = 0
        for s in q("SELECT id FROM sessions WHERE event_id=? AND stage != 'lost'", (eid,)):
            if q("SELECT 1 FROM lines WHERE session_id=? AND kind!='tax'", (s["id"],), one=True):
                continue
            _price_family(s["id"], e)
            n += 1
        audit(f"Set {money(amt)} for {n} famil{'y' if n == 1 else 'ies'} at {e['name']}")
    return redirect(url_for("event_view", eid=eid) + "#families")

@app.post("/event/<int:eid>/payments")
def event_payments(eid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    back = url_for("event_view", eid=eid) + "#families"
    if sent_twice():
        return _refuse_twice(back)
    one_, many_ = event_nouns(e)
    picked = [int(v) for v in request.form.getlist("pick") if v.isdigit()]
    rows = q(f"SELECT s.id FROM sessions s WHERE s.event_id=? AND s.stage != 'lost' AND s.id IN ({','.join('?' * len(picked)) or 'NULL'})",
             (eid, *picked))
    if not rows:
        return _refuse(f"Select the {many_} who paid first. Nothing was recorded.", back)
    received, why_ = money_day_in(request.form.get("received"))
    if why_:
        return _refuse(why_, back)
    if closed_year_refusal(received):
        return _refuse(closed_year_refusal(received), back)
    method = request.form.get("method", "")
    if method not in C.PAYMENT_METHODS:
        return _refuse("Choose how they paid. Nothing was recorded.", back)
    n, total = 0, 0.0
    for r_ in rows:
        due = round(totals(r_["id"])["due"], 2)
        if due <= 0.005:
            continue
        record_payment(r_["id"], "balance", due, received or today().isoformat(), method)
        paying_means_yes(r_["id"])
        n, total = n + 1, total + due
    if not n:
        session["notice"] = f"Nothing was recorded: the {many_} ticked owe nothing."
        g.refused = True
        return redirect(back)
    session["notice"] = f"Recorded {n} payment{'' if n == 1 else 's'} in full, {money(total)} in all."
    audit(f"Recorded {n} {one_ if n == 1 else many_} as paid in full at {e['name']} ({money(total)}, {method})")
    return redirect(back)

def event_sign_day(e):

    if e["date"] and e["date"] <= today().isoformat():
        return e["date"]
    return today().isoformat()

@app.post("/event/<int:eid>/edit")
def event_edit(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    if _stale_edit("events", eid, EVENT_FORM_FIELDS, e):
        g.edit_clash_form, g.edit_clash_what, g.edit_clash_reload = "event", "event", url_for("event_view", eid=eid) + "#details"
        return event_view(eid), 409
    new_date = f.get("date") or None
    x("UPDATE events SET name=?, date=?, venue=?, notes=? WHERE id=?",
      (f.get("name") or e["name"], new_date, f.get("venue", ""), f.get("notes", ""), eid))
    if new_date != e["date"]:
        for srow in q("SELECT id, shoot_date, date_tentative FROM sessions WHERE event_id=?", (eid,)):
            if srow["shoot_date"] in (None, e["date"]):
                x("UPDATE sessions SET shoot_date=? WHERE id=?", (new_date, srow["id"]))
                date_moved(srow["id"], srow["shoot_date"], was_tentative=srow["date_tentative"])
                schedule_tasks(srow["id"], fair=True)
        if e["date"] and new_date:
            x(f"UPDATE tasks SET done=NULL, outcome=NULL WHERE event_id=? AND title IN ({','.join('?' * len(RESCHEDULE_REOPENS))})",
              (eid, *RESCHEDULE_REOPENS))
        schedule_event(eid, fair=True)
    return redirect(url_for("event_view", eid=eid))

def _event_payer(eid, hid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    x("UPDATE events SET payer_hid=? WHERE id=?", (hid, eid))
    sid = create_session(hid, C.EVENT_TYPE, "inquiry", e["name"], e["date"])
    x("UPDATE sessions SET payer_event=? WHERE id=?", (eid, sid))
    x("INSERT INTO documents (session_id, kind) VALUES (?, 'agreement')", (sid,))
    sync_workflow_tasks(sid)
    return sid

@app.post("/event/<int:eid>/billing")
def event_billing(eid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    if _stale_edit("event_rights", eid, (("rights_note", "Rights"),), e):
        g.edit_clash_form, g.edit_clash_what, g.edit_clash_reload = "event_rights", "event", url_for("event_view", eid=eid) + "#billing"
        return event_view(eid), 409
    if f.get("billing") in ("free", "organizer", "families"):
        x("UPDATE events SET billing=? WHERE id=?", (f["billing"], eid))

    if f.get("rights") in C.EVENT_RIGHTS:
        x("UPDATE events SET rights=? WHERE id=?", (f["rights"], eid))
    if "rights_note" in f:
        x("UPDATE events SET rights_note=? WHERE id=?", (f["rights_note"].strip(), eid))
    new_hid = None
    if (f.get("org_name") or "").strip():

        name_ = " ".join(f["org_name"].split())[:200]
        company_ = " ".join((f.get("org_company") or "").split())[:200]
        same_ = q("""SELECT h.name FROM people p JOIN households h ON h.id=p.household_id
                     WHERE lower(trim(p.name))=lower(?) OR (?!='' AND lower(trim(h.company))=lower(?)) LIMIT 1""",
                  (name_, company_, company_), one=True)
        if same_ and not f.get("same_ok"):
            session["notice"] = (f"{same_['name']} is already a contact. Pick them under Who is the organizer, or add "
                                 "the new one again and tick Add anyway. Nothing was changed.")
            g.refused = True
            return redirect(url_for("event_view", eid=eid) + "#billing")
        new_hid = x("INSERT INTO households (id, name, company, source) VALUES (?,?,?,?)",
                    (next_id("households"), company_ or name_, company_, e["name"]))
        x("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?, 'adult', ?, ?)",
          (new_hid, name_, (f.get("org_email") or "").strip()[:200], (f.get("org_phone") or "").strip()[:200]))
        audit(f"Added {company_ or name_} as the organizer of {e['name']}")
    if new_hid or (f.get("payer_hid", "").isdigit() and q("SELECT 1 FROM households WHERE id=?", (int(f["payer_hid"]),), one=True)):
        hid = new_hid or int(f["payer_hid"])
        x("UPDATE events SET payer_hid=? WHERE id=?", (hid, eid))
        ps_ = q("SELECT id, household_id FROM sessions WHERE payer_event=?", (eid,), one=True)
        if ps_ and ps_["household_id"] != hid:

            done_ = q("""SELECT (SELECT COUNT(*) FROM payments WHERE session_id=? AND voided IS NULL)
                                + (SELECT COUNT(*) FROM documents WHERE session_id=? AND status='signed')
                                + (SELECT COUNT(*) FROM doc_numbers WHERE session_id=? AND kind LIKE 'INV%')
                                + (SELECT COUNT(*) FROM charges WHERE session_id=?) n""",
                      (ps_["id"],) * 4, one=True)["n"]
            if not done_:
                x("UPDATE sessions SET household_id=? WHERE id=?", (hid, ps_["id"]))
            else:
                audit("Organizer changed; the billing session stays with the previous organizer (payments, signatures, "
                      "an invoice or charges recorded)", sid=ps_["id"])
                session["notice"] = ("The billing session stays with the previous organizer: it already has payments, a "
                                     "signature or an invoice issued to them, and their statements keep it. To bill the new "
                                     "organizer instead, void and reissue there, or bill them separately.")
        if not ps_:
            _event_payer(eid, hid)
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    _org_line_said(eid)
    for r_ in q("SELECT id FROM sessions WHERE event_id=?", (eid,)):
        sync_workflow_tasks(r_["id"])
    return redirect(url_for("event_view", eid=eid) + "#billing")

@app.post("/event/<int:eid>/mark")
def event_mark(eid):
    field = request.form.get("field")
    if field not in MARKABLE:
        abort(400)
    col = dbmod.ident(field)
    x("UPDATE sessions SET " + col + "=? WHERE event_id=? AND stage != 'lost' AND " + col + " IS NULL", (today().isoformat(), eid))
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
             WHERE kind='release' AND status != 'signed' AND session_id IN (SELECT id FROM sessions WHERE event_id=? AND stage != 'lost')""",
          (day, day, eid))
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))

@app.post("/event/<int:eid>/family")
def event_family(eid):
    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    f = request.form
    hid = x("INSERT INTO households (id, name, source, address) VALUES (?,?,?,?)", (next_id("households"), f["household_name"], e["name"], f.get("address", "")))
    if event_nouns(e)[0] == "person":
        pid_ = x("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?, 'adult', ?, ?)",
                 (hid, f["household_name"], f.get("email", ""), f.get("phone", "")))
        sid_ = create_session(hid, C.EVENT_TYPE, "booked", e["name"], e["date"], event_id=eid, subject_ids=[pid_])
        if e["billing"] == "families" and e["family_price"] and sid_:
            _price_family(sid_, e)
        _org_line_said(eid)
        return redirect(url_for("event_view", eid=eid) + "#families")
    if f.get("parent_name"):
        x("INSERT INTO people (household_id, name, role, email, phone, relation) VALUES (?,?,?,?,?,?)",
          (hid, f["parent_name"], "parent", f.get("email", ""), f.get("phone", ""), "parent"))
    subjects = []
    for raw in [n.strip() for n in re.split(r",|\band\b|&", f.get("children", "")) if n.strip()]:
        m = trailing_age(raw, commas=False)
        name, age = (m[0].strip(), int(m[1])) if m else (raw, None)

        minor_ = 1 if age is None or age < majority_age() else 0
        subjects.append(x("INSERT INTO people (household_id, name, role, is_minor, birth_year, age_approx) VALUES (?,?, 'child', ?, ?, ?)",
                          (hid, name, minor_, (today().year - age) if age is not None else None, 1 if age is not None else 0)))
    sid_ = create_session(hid, C.EVENT_TYPE, "booked", e["name"], e["date"], event_id=eid, subject_ids=subjects)
    if e["billing"] == "families" and e["family_price"] and sid_:
        _price_family(sid_, e)
    _org_line_said(eid)
    return redirect(url_for("event_view", eid=eid))

def _price_family(sid, e):

    ensure_quote(sid)
    price = e["family_price"]
    lid = x("INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (?,?,?, 'custom', 1)",
            (sid, f"{e['name']} - family session", price))
    reapply_discount(sid)
    if not (e["family_tax_in"] if "family_tax_in" in e.keys() else 0):
        return
    amt = price
    for _ in range(8):
        off = round(totals(sid)["quoted"] - price, 2)
        if abs(off) < 0.005:
            return
        rate = session_tax_rate(sid) / 100
        step = round(off / (1 + rate), 2) or (0.01 if off > 0 else -0.01)
        amt = round(amt - step, 2)
        x("UPDATE lines SET amount=? WHERE id=?", (amt, lid))
        reapply_discount(sid)

def event_nouns(e):

    return ("person", "people") if (e["attendees"] if "attendees" in e.keys() else None) == "people" else ("family", "families")

def _org_line(eid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    ps_ = q("SELECT id FROM sessions WHERE payer_event=?", (eid,), one=True)
    if not e or not ps_ or e["billing"] != "organizer" or not e["org_price"]:
        return True
    sid = ps_["id"]
    if per_person := (e["org_per"] or "person") == "person":
        n = q("SELECT COUNT(*) n FROM sessions WHERE event_id=? AND stage != 'lost'", (eid,), one=True)["n"]
        want = (f"{e['name']}, per {event_nouns(e)[0]} x {n}", round(n * e["org_price"], 2), n)
    else:
        want = (f"{e['name']}: event fee", round(e["org_price"], 2), 1)
    have = q("SELECT label, amount, qty FROM lines WHERE session_id=? AND fee_key='event_org'", (sid,))
    if len(have) == 1 and (have[0]["label"], round(have[0]["amount"], 2), have[0]["qty"] or 1) == want:
        return True
    if quote_locked(sid):
        return False
    x("DELETE FROM lines WHERE session_id=? AND fee_key='event_org'", (sid,))
    if not per_person or want[2]:
        ensure_quote(sid)
        x("INSERT INTO lines (session_id, label, amount, kind, taxable, fee_key, qty) VALUES (?,?,?, 'custom', 1, 'event_org', ?)",
          (sid, *want))
    reapply_discount(sid)
    return True

def _org_line_said(eid):

    if not _org_line(eid):
        session["notice"] = ("The organizer's quote was already booked, so its price stays as agreed. To bill for the "
                             "change, use Replace the quote on the organizer's session.")

app.jinja_env.globals["event_nouns"] = event_nouns

@app.post("/event/<int:eid>/attendees")
def event_attendees(eid):

    q("SELECT 1 FROM events WHERE id=?", (eid,), one=True) or abort(404)
    if request.form.get("attendees") in ("families", "people"):
        x("UPDATE events SET attendees=? WHERE id=?", (request.form["attendees"], eid))
        _org_line_said(eid)
    return redirect(url_for("event_view", eid=eid) + "#families")

@app.post("/event/<int:eid>/organizer-price")
def event_org_price(eid):

    e = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    try:
        amt = round(num(request.form.get("amount")), 2)
    except ValueError:
        amt = 0
    per = request.form.get("per") if request.form.get("per") in ("person", "event") else (e["org_per"] or "person")
    if amt <= 0 or amt > MONEY_CAP:
        return _refuse("Enter the price as a positive amount. Nothing was changed.", url_for("event_view", eid=eid) + "#billing")
    x("UPDATE events SET org_price=?, org_per=? WHERE id=?", (amt, per, eid))
    audit(f"Organizer's price for {e['name']}: {money(amt)} {'per ' + event_nouns(e)[0] if per == 'person' else 'for the event'}")
    _org_line_said(eid)
    return redirect(url_for("event_view", eid=eid) + "#billing")

@app.post("/event/<int:eid>/stage")
def event_stage(eid):
    stage = request.form["stage"]
    if stage not in C.STAGES + C.OFF_PIPELINE:
        abort(400)
    e_ = q("SELECT * FROM events WHERE id=?", (eid,), one=True) or abort(404)
    one_, many_ = event_nouns(e_)
    if stage in ("shot", "delivered", "closed") and e_["date"] and e_["date"] > today().isoformat():
        msg_ = f"Not yet: the event is {nice(e_['date'])}. {many_.capitalize()} move on from the day itself."
        if request.headers.get("X-Fetch"):
            return (msg_, 409, {"Content-Type": "text/plain; charset=utf-8"})
        session["notice"] = msg_
        g.refused = True
        return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))
    n_ = 0
    fam_ = q("SELECT id, stage FROM sessions WHERE event_id=? AND stage != 'lost'", (eid,))
    for s in fam_:
        if stage in C.STAGES and s["stage"] in C.STAGES and C.STAGES.index(s["stage"]) >= C.STAGES.index(stage):
            continue
        set_stage(s["id"], stage)
        n_ += 1

    ps_ = q("SELECT id, stage FROM sessions WHERE payer_event=? AND stage NOT IN ('lost','closed')", (eid,), one=True)
    org_ = bool(ps_ and n_ and stage in C.STAGES and ps_["stage"] in C.STAGES and C.STAGES.index(ps_["stage"]) < C.STAGES.index(stage))
    if org_:
        set_stage(ps_["id"], stage)
        audit(f"Moved to {C.STAGE_LABELS[stage]} with the event", sid=ps_["id"])

    label_ = C.STAGE_LABELS.get(stage, stage)
    why_ = (f"Nothing moved: every {one_} is already at " + label_ + " or further along.") if fam_ else \
        f"Nothing moved: the event has no {many_} yet."
    if request.headers.get("X-Fetch"):
        return (why_, 409, {"Content-Type": "text/plain; charset=utf-8"}) if not n_ else ("", 204)
    if n_:
        session["notice"] = f"Moved {n_} {one_ if n_ == 1 else many_}{' and the organizer' if org_ else ''} to {label_}."
    else:
        session["notice"] = why_
        g.refused = True
    return redirect(safe_back(request.form.get("back")) or url_for("event_view", eid=eid))

def money_year(arg):

    try:
        year = int(arg if arg is not None else today().year)
    except ValueError:
        year = today().year
    return min(max(year, 1900), 9999)

def year_sales_tax(y):

    rows = []
    for r_ in q(f"""SELECT session_id FROM payments WHERE voided IS NULL AND substr(received,1,4)=? AND session_id IS NOT NULL
                   UNION SELECT session_id FROM tax_adjustments WHERE substr(at,1,4)=? AND {LIVE_ADJ}""", (y, y)):
        st_ = q("SELECT stage, lost_reason, stage_changed FROM sessions WHERE id=?", (r_["session_id"],), one=True)

        moved_ = _kept_case(st_) and q("SELECT 1 FROM payments WHERE session_id=? AND voided IS NULL AND kind='refund' AND method=?",
                                       (r_["session_id"], CREDIT_OUT), one=True)
        lost_y = (st_["stage_changed"] or "")[:4] if _kept_case(st_) and (totals(r_["session_id"])["kept"] > 0.005 or moved_) else None
        if not _kept_case(st_):
            lost_y = _lost_through(r_["session_id"], y)
        for e_ in tax_entries(r_["session_id"]):
            if (e_["day"] or "")[:4] == y:
                if lost_y and e_["what"] == "payment" and e_["kind"] not in ("refund", "tip") and y >= lost_y:
                    e_ = dict(e_, what="kept")
                rows.append(e_)
    rows.sort(key=lambda r_: (r_["day"] or ""))
    for b_ in q("SELECT received, amount, tax, type, kind, tax_parts FROM books WHERE substr(received,1,4)=? ORDER BY received, id", (y,)):
        rows.append(dict(what="books", kind=b_["kind"], sid=None, day=b_["received"], amount=b_["amount"],
                         tax=b_["tax"] or 0.0, taxable=0.0, type=b_["type"],
                         parts={n_: [v_[0], 0.0] for n_, v_ in _parts_of(b_["tax"], 0.0, b_["tax_parts"]).items()}))
    return rows

def _lost_through(sid, y):

    rows = q("SELECT stage, at FROM stage_log WHERE session_id=? AND substr(at,1,4) <= ? ORDER BY at, id", (sid, y))
    if not rows or rows[-1]["stage"] != "lost":
        return None
    i_ = len(rows) - 1
    while i_ > 0 and rows[i_ - 1]["stage"] == "lost":
        i_ -= 1
    return (rows[i_]["at"] or "")[:4] or None

def _tax_view(sid):

    items, exempt, why = session_taxes(sid)
    items = [t_ for t_ in items if t_["rate"] > 0]
    cert = q("SELECT h.tax_cert FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True)
    return dict(taxed=bool(items), tax_items=items, tax_exempt_why=why if exempt else "",
                tax_cert=(cert["tax_cert"] or "").strip() if cert else "", included=included_taxes(sid),
                tax_word=tax_word([t_["name"] for t_ in items]))

def tax_word(names=None):

    names = list(dict.fromkeys(names or [])) or [t_["name"] for t_ in taxes_list()] or [LEGACY_TAX_NAME]
    return names[0] if len(names) == 1 else "Taxes"

def tax_by_name(rows):

    tot, seen = {}, set()
    for r_ in rows:
        for n_, v_ in (r_.get("parts") or {}).items():
            tot[n_] = tot.get(n_, 0.0) + v_[0]
            if abs(v_[0]) >= 0.005:
                seen.add(n_)
    order = {t_["name"]: i_ for i_, t_ in enumerate(taxes_list())}
    return [(n_, round(v_, 2) or 0.0) for n_, v_ in sorted(tot.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
            if n_ in seen]

def year_money(year):

    y = str(year)
    rate = float(setting("mileage_rate") or 0)

    received = q("""SELECT type, SUM(v) v FROM (
                      SELECT s.type, p.amount v FROM payments p JOIN sessions s ON s.id=p.session_id
                      WHERE substr(p.received,1,4)=? AND p.voided IS NULL
                      UNION ALL SELECT type, amount FROM books WHERE substr(received,1,4)=? AND kind IS NOT 'adjustment')
                    GROUP BY type""", (y, y))
    exp = q("""SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(miles),0) m, COALESCE(SUM(ROUND(miles * COALESCE(rate, ?), 2)),0) mc
               FROM expenses WHERE substr(date,1,4)=?""", (rate, y), one=True)
    refunds = q("""SELECT COALESCE(SUM(amount),0) v FROM (SELECT amount FROM payments WHERE kind='refund' AND voided IS NULL
                   AND substr(received,1,4)=? UNION ALL SELECT amount FROM books WHERE kind='refund' AND substr(received,1,4)=?)""",
                (y, y), one=True)["v"]
    written_off = q("SELECT COALESCE(SUM(amount),0) v FROM write_offs WHERE substr(at,1,4)=?", (y,), one=True)["v"]
    tips = q("""SELECT COALESCE(SUM(amount),0) v FROM (SELECT amount FROM payments WHERE kind='tip' AND voided IS NULL
                AND substr(received,1,4)=? UNION ALL SELECT amount FROM books WHERE kind='tip' AND substr(received,1,4)=?)""",
             (y, y), one=True)["v"]
    tax_rows = year_sales_tax(y)
    tax_adjusted = round(sum(r_["tax"] for r_ in tax_rows if r_["kind"] == "adjustment"), 2)
    by_month = q("""SELECT substr(received,1,7) mo, SUM(amount) v FROM (
                      SELECT received, amount FROM payments WHERE substr(received,1,4)=? AND voided IS NULL
                      UNION ALL SELECT received, amount FROM books WHERE substr(received,1,4)=? AND kind IS NOT 'adjustment')
                    GROUP BY mo ORDER BY mo""", (y, y))
    return dict(rate=rate, received=received, total=sum(r["v"] for r in received), exp=exp, refunds=refunds,
                tax_rows=tax_rows, tax_due=round(sum(r_["tax"] for r_ in tax_rows), 2), tax_adjusted=tax_adjusted, by_month=by_month,
                tax_names=tax_by_name(tax_rows),
                written_off=written_off, tips=tips)

@app.get("/money")
def money_view():
    year = money_year(request.args.get("year"))
    y = str(year)
    ym = year_money(year)

    who_ = """SELECT x_.*, h.name AS client, h.id AS hid, s.type AS stype, s.title AS stitle,
                     ROUND(x_.miles * COALESCE(x_.rate, ?), 2) AS mc_
              FROM expenses x_ LEFT JOIN sessions s ON s.id=x_.session_id LEFT JOIN households h ON h.id=s.household_id
              WHERE substr(x_.date,1,4)=? AND """

    overhead = q(who_ + "(x_.session_id IS NULL OR x_.amount != 0) ORDER BY x_.date DESC, x_.id DESC", (ym["rate"], y))
    trips = q(who_ + "x_.session_id IS NOT NULL AND x_.miles != 0 ORDER BY x_.date DESC, x_.id DESC", (ym["rate"], y))
    travel = q("""SELECT COALESCE(SUM(l.amount),0) v, COUNT(DISTINCT l.session_id) n FROM lines l JOIN sessions s ON s.id=l.session_id
                  WHERE l.fee_key='travel' AND substr(s.shoot_date,1,4)=? AND s.stage IN ('booked','shot','delivered','closed')""",
               (y,), one=True)
    lost = q("""SELECT COALESCE(NULLIF(lost_reason,''),'No reason given') r, COUNT(*) n FROM sessions WHERE stage='lost'
                AND substr(stage_changed,1,4)=? GROUP BY r ORDER BY n DESC""", (y,))
    funnel = q("""SELECT stage, COUNT(*) n FROM sessions WHERE substr(created,1,4)=? GROUP BY stage""", (y,))
    outstanding, upcoming, to_invoice = [], [], []
    for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.stage NOT IN ('lost') ORDER BY s.shoot_date"""):
        tt = totals(s["id"])
        if owed_now(s["stage"], tt, s["shoot_date"], row_type(s)) and not invoice_went(s["id"]):
            to_invoice.append(dict(s=s, t=tt))
        elif owed_now(s["stage"], tt, s["shoot_date"], row_type(s)):
            ov_ = overdue(s, tt)
            outstanding.append(dict(s=s, t=tt, due_on=ov_["since"] or balance_when(s, tt), late=ov_["amount"] > 0.005,
                                    late_amt=ov_["amount"]))
        elif s["stage"] == "booked" and tt["due"] > 0.005:

            rem_ = payment_reminder(s, tt)
            ov_ = overdue(s, tt)
            upcoming.append(dict(s=s, t=tt, rem=rem_, late=ov_["amount"] > 0.005, late_amt=ov_["amount"]))
    return render_template("money.html", net_days=C.BALANCE_NET_DAYS, year=year, received=ym["received"],
                           total=ym["total"], exp=ym["exp"], rate=ym["rate"], by_month=ym["by_month"],
                           funnel={r["stage"]: r["n"] for r in funnel}, outstanding=outstanding, to_invoice=to_invoice,
                           upcoming=upcoming, overhead=overhead, refunds=ym["refunds"], lost=lost, today_iso=today().isoformat(),
                           tax_due=ym["tax_due"], tax_adjusted=ym["tax_adjusted"], written_off=ym["written_off"],
                           tax_names=ym["tax_names"], tax_word=tax_word([n_ for n_, _v in ym["tax_names"]]),
                           tips=ym["tips"], export_years=accounts_years(year), trips=trips, travel=travel,
                           prints=(app.config.get("prints_year") or (lambda y_: None))(year),
                           books=books_state(year), fixes=year_corrections(y))

def year_corrections(y):

    out = []
    for r_ in q("SELECT * FROM payments WHERE corrects IS NOT NULL AND voided IS NULL AND substr(received,1,4)=?", (y,)):
        out.append(dict(day=r_["received"], words=correction_words("payments", r_), amount=r_["amount"]))
    rate = float(setting("mileage_rate") or 0)
    for r_ in q("SELECT * FROM expenses WHERE (corrects IS NOT NULL OR late_from IS NOT NULL) AND substr(date,1,4)=?", (y,)):
        cost_ = (r_["amount"] or 0) + round((r_["miles"] or 0) * (r_["rate"] if r_["rate"] is not None else rate), 2)
        out.append(dict(day=r_["date"], words=f"{r_['label']}: " + correction_words("expenses", r_), amount=-cost_))
    out += (app.config.get("prints_corrections") or (lambda y_: []))(y)
    return sorted(out, key=lambda r_: r_["day"])

def books_state(year):

    t_ = today()
    last = t_.year - 1
    return dict(closed=year_closed(year), last=last,
                can_close=t_.month == 1 and not year_closed(last) and year in (last, t_.year))

def accounts_years(year):

    ys = {r_["y"] for r_ in q("""SELECT substr(received,1,4) y FROM payments UNION SELECT substr(received,1,4) FROM books
                                 UNION SELECT substr(date,1,4) FROM expenses""") if (r_["y"] or "").isdigit()}
    return sorted({int(y_) for y_ in ys} | {today().year, year}, reverse=True)

class _Num(str):
    pass

def _money_cell(v):
    return _Num(f"{round(float(v or 0), 2) or 0.0:.2f}")

def _qty_cell(v, places=4):
    return _Num(f"{float(v or 0):.{places}f}".rstrip("0").rstrip(".") or "0")

def _text_cell(v):

    s_ = "" if v is None else str(v)
    return "'" + s_ if s_[:1] in ("=", "+", "-", "@", "\t", "\r") else s_

def _csv(header, rows):
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    for r_ in [header] + rows:
        w.writerow([c_ if isinstance(c_, _Num) else _text_cell(c_) for c_ in r_])
    return buf.getvalue().encode("utf-8-sig")

def _client_of(r_):
    return ((r_["company"] or "").strip() or r_["client"] or "") if r_["client"] is not None else ""

def _session_of(r_):
    if r_["type"] is None:
        return ""
    return r_["title"] or C.SESSION_TYPES.get(r_["type"], r_["type"])

def accounts_files(year):

    y = str(year)
    ym = year_money(year)
    who = """LEFT JOIN sessions s ON s.id=x_.session_id LEFT JOIN households h ON h.id=s.household_id"""
    cols = "s.title, s.type, s.shoot_date, s.tax_exempt, h.name AS client, h.company"
    nums = {(r_["session_id"], r_["kind"]): r_["number"] for r_ in q(
        "SELECT session_id, kind, number FROM doc_numbers WHERE kind IN ('INV','R')")}

    pay = []
    for r_ in q(f"""SELECT x_.*, {cols} FROM payments x_ {who} WHERE substr(x_.received,1,4)=? ORDER BY x_.received, x_.id""", (y,)):
        pay.append([r_["received"], _client_of(r_), _session_of(r_), r_["shoot_date"] or "", nums.get((r_["session_id"], "INV"), ""),
                    nums.get((r_["session_id"], "R"), ""), r_["kind"] or "", r_["method"] or "", r_["ref"] or "",
                    _money_cell(r_["amount"]), "yes" if r_["voided"] else "no", correction_words("payments", r_)])
    for b_ in q("SELECT * FROM books WHERE substr(received,1,4)=? AND kind IS NOT 'adjustment' ORDER BY received, id", (y,)):
        pay.append([b_["received"], "Deleted client", C.SESSION_TYPES.get(b_["type"], b_["type"]) if b_["type"] else "", "", "", "",
                    b_["kind"] or "", "", "", _money_cell(b_["amount"]), "no", ""])
    pay.sort(key=lambda r_: r_[0] or "")

    exp, mil = [], []
    for r_ in q(f"""SELECT x_.*, {cols}, ROUND(x_.miles * COALESCE(x_.rate, ?), 2) AS mc_ FROM expenses x_ {who}
                    WHERE substr(x_.date,1,4)=? ORDER BY x_.date, x_.id""", (ym["rate"], y)):
        if r_["amount"]:
            exp.append([r_["date"], r_["label"], _client_of(r_), _session_of(r_), _money_cell(r_["amount"]),
                        correction_words("expenses", r_)])
        if r_["miles"]:
            rate_ = r_["rate"] if r_["rate"] is not None else ym["rate"]

            mil.append([r_["date"], r_["label"], _client_of(r_), _session_of(r_), _qty_cell(r_["miles"], 2),
                        _qty_cell(rate_) if rate_ else "", _money_cell(r_["mc_"]) if rate_ else "", correction_words("expenses", r_)])

    wof = [[r_["at"], _client_of(r_), _session_of(r_), r_["shoot_date"] or "", nums.get((r_["session_id"], "INV"), ""),
            _money_cell(r_["amount"])] for r_ in q(f"""SELECT x_.*, {cols} FROM write_offs x_ {who}
                                                      WHERE substr(x_.at,1,4)=? ORDER BY x_.at, x_.session_id""", (y,))]

    names_ = [n_ for n_, _v in ym["tax_names"]]
    named = len(names_) > 1 or (names_ and names_[0] != LEGACY_TAX_NAME)
    tax, by_sid, books_ = [], {}, {}
    for t_ in ym["tax_rows"]:
        if t_["what"] == "books":
            for n_, (pt_, _b) in ((t_["parts"] or {names_[0]: [t_["tax"], 0.0]}).items() if named else [(None, (t_["tax"], 0))]):
                b_ = books_.setdefault((t_["type"] or "", "adjustment" if t_["kind"] == "adjustment" else "payment", n_),
                                       dict(received=0.0, tax=0.0))
                b_["tax"] += pt_
            b_ = books_[next(k_ for k_ in books_ if k_[:2] == (t_["type"] or "", "adjustment" if t_["kind"] == "adjustment" else "payment"))]
            b_["received"] += t_["amount"]
            continue
        for n_, (pt_, pb_) in ((t_["parts"] or {names_[0]: [0.0, 0.0]}).items() if named else [(None, (t_["tax"], t_["taxable"]))]):
            k_ = by_sid.setdefault((t_["sid"], t_["what"], n_), dict(received=0.0, tax=0.0, taxable=0.0))
            k_["tax"] += pt_
            k_["taxable"] += pb_
        k_ = by_sid[next(k_ for k_ in by_sid if k_[:2] == (t_["sid"], t_["what"]))]
        k_["received"] += t_["amount"]
    for (sid_, what_, name_), k_ in by_sid.items():
        r_ = q("""SELECT s.title, s.type, s.shoot_date, s.tax_exempt, h.name AS client, h.company, h.tax_exempt AS h_exempt
                  FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""", (sid_,), one=True)
        taxed_ = abs(k_["tax"]) > 0.000001
        if named:
            rate_ = next((t_["rate"] for t_ in session_taxes(sid_)[0] if t_["name"] == name_), 0.0) if taxed_ else 0.0
        else:
            rate_ = session_tax_rate(sid_) if taxed_ else 0.0
        row_ = [_client_of(r_), _session_of(r_), r_["shoot_date"] or "", nums.get((sid_, "INV"), "")] + ([name_] if named else [])
        tax.append(row_ + [TAX_WHY.get(what_, what_), _money_cell(k_["received"]),
                           _money_cell(k_["taxable"] if taxed_ else 0.0), _qty_cell(rate_) if rate_ else "", _money_cell(k_["tax"]),
                           "yes" if r_["tax_exempt"] or r_["h_exempt"] else "no"])
    for (type_, what_, name_), b_ in books_.items():
        row_ = ["Deleted client", C.SESSION_TYPES.get(type_, type_) if type_ else "", "", ""] + ([name_] if named else [])
        tax.append(row_ + ["corrections" if what_ == "adjustment" else "payments received", _money_cell(b_["received"]), "", "",
                           _money_cell(b_["tax"]), ""])

    mo = {f"{y}-{m_:02d}": dict(received=0.0, refunds=0.0, a=0.0, m=0.0, mc=0.0, tax=0.0, wo=0.0, tips=0.0) for m_ in range(1, 13)}
    for r_ in q("""SELECT substr(received,1,7) mo, SUM(amount) v FROM (SELECT received, amount FROM payments WHERE kind='tip'
                   AND voided IS NULL AND substr(received,1,4)=? UNION ALL SELECT received, amount FROM books WHERE kind='tip'
                   AND substr(received,1,4)=?) GROUP BY mo""", (y, y)):
        if r_["mo"] in mo:
            mo[r_["mo"]]["tips"] = r_["v"]
    for r_ in q("SELECT substr(at,1,7) mo, SUM(amount) v FROM write_offs WHERE substr(at,1,4)=? GROUP BY mo", (y,)):
        if r_["mo"] in mo:
            mo[r_["mo"]]["wo"] = r_["v"]
    for r_ in ym["by_month"]:
        if r_["mo"] in mo:
            mo[r_["mo"]]["received"] = r_["v"]
    for r_ in q("""SELECT substr(received,1,7) mo, SUM(amount) v FROM (SELECT received, amount FROM payments WHERE kind='refund'
                   AND voided IS NULL AND substr(received,1,4)=? UNION ALL SELECT received, amount FROM books WHERE kind='refund'
                   AND substr(received,1,4)=?) GROUP BY mo""", (y, y)):
        if r_["mo"] in mo:
            mo[r_["mo"]]["refunds"] = r_["v"]
    for r_ in q("""SELECT substr(date,1,7) mo, COALESCE(SUM(amount),0) a, COALESCE(SUM(miles),0) m,
                          COALESCE(SUM(ROUND(miles * COALESCE(rate, ?), 2)),0) mc FROM expenses WHERE substr(date,1,4)=? GROUP BY mo""",
                (ym["rate"], y)):
        if r_["mo"] in mo:
            mo[r_["mo"]].update(a=r_["a"], m=r_["m"], mc=r_["mc"])
    for t_ in ym["tax_rows"]:
        if (t_["day"] or "")[:7] in mo:
            mo[t_["day"][:7]]["tax"] += t_["tax"]
            for n_, v_ in (t_.get("parts") or {}).items():
                mo[t_["day"][:7]].setdefault("by", {}).setdefault(n_, 0.0)
                mo[t_["day"][:7]]["by"][n_] += v_[0]
    each_ = names_ if named else []
    summary = [[k_, _money_cell(v_["received"]), _money_cell(v_["refunds"]), _money_cell(v_["tips"]), _money_cell(v_["a"]), _qty_cell(v_["m"], 2),
                _money_cell(v_["mc"]), _money_cell(v_["tax"])] + [_money_cell(v_.get("by", {}).get(n_, 0.0)) for n_ in each_]
               + [_money_cell(v_["received"] - v_["a"] - v_["mc"] - v_["tax"]), _money_cell(v_["wo"])]
               for k_, v_ in mo.items()]
    e_ = ym["exp"]
    summary.append(["Total", _money_cell(ym["total"]), _money_cell(ym["refunds"]), _money_cell(ym["tips"]), _money_cell(e_["a"]), _qty_cell(e_["m"], 2),
                    _money_cell(e_["mc"]), _money_cell(ym["tax_due"])] + [_money_cell(dict(ym["tax_names"]).get(n_, 0.0)) for n_ in each_]
                   + [_money_cell(ym["total"] - e_["a"] - e_["mc"] - ym["tax_due"]), _money_cell(ym["written_off"])])

    return [
        ("payments.csv", _csv(["date received", "client", "session", "session date", "invoice", "receipt", "kind", "method",
                               "check number", "amount", "voided", "correction of"], pay)),
        ("expenses.csv", _csv(["date", "what", "client", "session", "amount", "correction of"], exp)),
        ("mileage.csv", _csv(["date", "purpose", "client", "session", "miles", "rate", "amount", "correction of"], mil)),
        ("sales-tax.csv", _csv(["client", "session", "session date", "invoice"] + (["tax"] if named else [])
                               + ["counted as", "received", "taxable amount", "rate (%)", "tax amount" if named else "sales tax",
                                  "exempt"], tax)),
        ("write-offs.csv", _csv(["date", "client", "session", "session date", "invoice", "amount written off"], wof)),
    ] + [(n_, _csv(h_, [[_Num(c_) if re.fullmatch(r"-?\d+\.\d\d", str(c_)) else c_ for c_ in r_] for r_ in rows_]))
         for n_, h_, rows_ in (app.config.get("prints_accounts") or (lambda y_: []))(year)] + [
        ("summary.csv", _csv(["month", "received", "refunds (in received)", "tips (in received, not taxed)", "expenses", "miles", "mileage",
                              "taxes" if named else "sales tax"] + each_ + ["net, after taxes" if named else "net, after sales tax",
                              "written off (not received)"], summary)),
    ]

def year_summary(year):

    y = str(year)
    ym = year_money(year)
    pr = (app.config.get("prints_year") or (lambda y_: None))(year) or {}
    pay_ = q("""SELECT kind, LOWER(COALESCE(method,'')) m, amount FROM payments WHERE voided IS NULL AND substr(received,1,4)=?
                UNION ALL SELECT kind, '', amount FROM books WHERE substr(received,1,4)=? AND kind IS NOT 'adjustment'""", (y, y))
    other = round(sum(r_["amount"] for r_ in pay_ if r_["kind"] not in ("refund", "tip") and r_["m"].startswith(("other", "trade"))), 2)
    tips = round(ym["tips"], 2)
    s_refunds = round(-ym["refunds"], 2)
    sessions = round(ym["total"] + s_refunds - tips - other, 2)
    p_refunds = (pr.get("refunds") or 0) / 100
    prints = round((pr.get("received") or 0) / 100 + p_refunds, 2)
    gross = round(sessions + tips + other + prints, 2)
    refunds = round(s_refunds + p_refunds, 2)
    tax = round(ym["tax_due"] + (pr.get("tax") or 0) / 100, 2)
    kinds = {}
    for r_ in q("SELECT label, amount, session_id FROM expenses WHERE substr(date,1,4)=? AND amount > 0", (y,)):
        k_ = " ".join((r_["label"] or "").split()).strip() or "Expense"
        k_ = k_[:1].upper() + k_[1:]
        name_, v_ = kinds.get(k_.lower(), (k_, 0.0))
        kinds[k_.lower()] = (name_, v_ + r_["amount"])
    exp = sorted(kinds.values(), key=lambda kv: -kv[1])
    if len(exp) > 10:
        exp = exp[:9] + [("Everything else", sum(v_ for _, v_ in exp[9:]))]
    exp = [(k_, round(v_, 2)) for k_, v_ in exp]
    if pr.get("cost"):
        exp.append(("Print lab cost", round(pr["cost"] / 100, 2)))
    if pr.get("lab_tax"):
        exp.append(("Print lab sales tax", round(pr["lab_tax"] / 100, 2)))
    t_ = today()
    end_ = date(year, 12, 31)
    now_ = datetime.now(TZ)
    import about as _ab
    return dict(year=year, business=setting("business_name").strip() or setting("studio_name").strip(),
                photographer=setting("photographer_name").strip(), studio=setting("studio_name").strip(),
                address=setting("business_address").strip(), contact=[c_ for c_ in (setting("business_email").strip(),
                setting("business_phone").strip(), setting("business_website").strip()) if c_],
                tax_id=setting("business_tax_id").strip(),
                start=long_date(f"{year}-01-01"), end=long_date((min(end_, t_) if year == t_.year else end_).isoformat()),
                to_date=year == t_.year and t_ < end_,
                generated=f"{now_.strftime('%B %-d, %Y')}, {clock(now_.strftime('%H:%M'))}", version=_ab.version(),
                sessions=sessions, tips=tips, other=other, prints=prints, gross=gross, refunds=refunds,
                net=round(gross - refunds, 2), tax=tax, expenses=exp, exp_total=round(sum(v_ for _, v_ in exp), 2),
                tax_names=(ym["tax_names"] + ([("On print invoices", round(pr["tax"] / 100, 2))] if pr.get("tax") else []))
                if len(ym["tax_names"]) > 1 else [],
                tax_word=tax_word([n_ for n_, _v in ym["tax_names"]]),
                miles=round(float(ym["exp"]["m"] or 0), 1), mileage=round(float(ym["exp"]["mc"] or 0), 2),
                rate=ym["rate"], waived=round(ym["written_off"] or 0, 2), any_prints=bool(pr.get("any")))

@app.get("/money/summary.pdf")
def money_summary_pdf():

    year = money_year(request.args.get("year"))
    v = year_summary(year)
    audit(f"Made the year summary for {year}")

    r = html_pdf(render_template("year_summary.html", v=v, pdf=True), f"year-summary-{year}.pdf")
    r.headers["Cache-Control"] = "no-store"
    return r

@app.get("/money/accounts.zip")
def money_accounts():

    import io as _io
    import zipfile
    year = money_year(request.args.get("year"))
    buf = _io.BytesIO()
    stamp = datetime.now(TZ).timetuple()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z_:
        for name_, data_ in accounts_files(year):
            z_.writestr(zipfile.ZipInfo(name_, date_time=stamp), data_, compress_type=zipfile.ZIP_DEFLATED)
    r = app.response_class(buf.getvalue(), mimetype="application/zip")
    r.headers["Content-Disposition"] = f'attachment; filename="lumen-aureum-{year}-accounts.zip"'
    audit(f"Exported accounts for {year}")
    return r

@app.route("/settings", methods=["GET", "POST"])
def settings_view():
    import settings_registry as _reg
    tab_ = request.args.get("tab") or ""
    if request.method == "GET" and request.args.get("find") is not None:

        return redirect(url_for("search", q=request.args.get("find") or None))
    if request.method == "GET" and tab_ in _reg.OLD_TABS:

        more_ = {k_: v_ for k_, v_ in request.args.items() if k_ != "tab"}
        return redirect(url_for("settings_view", tab=_reg.OLD_TABS[tab_], **more_))
    if request.method == "POST":
        for k in ("mileage_rate", "home_base", "photographer_name", "studio_name", "business_name", "business_address", "business_email", "business_phone", "gallery_base_url",
                  "business_tax_id", "bank_details", "pay_details", "business_website"):
            if k not in request.form:
                continue
            v = request.form.get(k, "")
            if k == "business_website":
                import settings_io as _sio
                raw_, v = v, _sio.clean_site(v)
                if v is None:
                    session["notice"] = f"The website wasn't saved: {_sio.site_problem(raw_)}. Everything else was saved."
                    _field_refused(k, f"Not saved: {_sio.site_problem(raw_)}.")
                    continue
            if k == "gallery_base_url" and v.strip() and not v.strip().startswith("https://"):
                session["notice"] = ("The gallery address wasn't saved: it must start with https://. Clients' browsers"
                                     " open the encrypted photos only over https.")
                continue
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (k, v))
            if k == "gallery_base_url" and v.strip():
                import netsec as _ns
                warn_ = _ns.only_here_words(v)
                if warn_:
                    session["notice"] = "Saved. " + warn_
        term = request.form.get("quote_term_days", "")
        if term.isdigit() and 1 <= int(term) <= 365:
            x("INSERT INTO settings (key, value) VALUES ('quote_term_days',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (term,))
        elif term:
            session["notice"] = "Quotes good for wasn't saved: it is a number of days from 1 to 365."

        main_ = request.form.get("main_posted")
        if main_ or request.form.get("credit_posted"):
            x("INSERT INTO settings (key, value) VALUES ('credit_off',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              ("" if request.form.get("credit") else "1",))
        nums_ = ((("check_in_snooze_months", 1, 36),) if main_ or request.form.get("clients_posted") else ()) + \
                ((("idle_signout_hours", 1, 72),) if main_ or request.form.get("idle_posted") else ())
        for k_, lo_, hi_ in nums_:
            v_ = request.form.get(k_, "").strip()
            if v_.isdigit() and lo_ <= int(v_) <= hi_:
                x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k_, v_))
            elif v_:
                session["notice"] = f"That number wasn't saved: it must be a whole number from {lo_} to {hi_}."
                _field_refused(k_, f"Not saved: a whole number from {lo_} to {hi_}.")
        if main_ or request.form.get("clients_posted"):
            am = request.form.get("age_majority", "")
            if am.isdigit() and 14 <= int(am) <= 25:
                x("INSERT INTO settings (key, value) VALUES ('age_majority',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (am,))
        if request.form.get("look_posted"):
            save_look(request.form)
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
                    amt = num(request.form.get(f"fee_amt_{i}"), it["amount"], lo=0)
                except ValueError:
                    amt = it["amount"]
                items.append(dict(it, label=request.form.get(f"fee_label_{i}", it["label"]).strip() or it["label"], amount=amt,
                                  **_fee_taxes_posted(f"fee_tax_{i}", it)))
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
                                  **_fee_taxes_posted("new_tax", {}, single="new_taxable")))
            for it_ in items:
                if not it_.get("untaxed"):
                    it_.pop("untaxed", None)
            if items != cur["items"]:
                was_ = prices_approved(cur["id"])
                new_id = x("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)", (now_local(), json.dumps(items), ""))
                if was_:
                    approve_prices(new_id, quiet=True)
        back_ = safe_back(request.form.get("back"))
        if back_:
            if not session.get("notice"):
                session["notice"] = "Saved."
            return redirect(back_)
        form_ = re.sub(r"[^a-z0-9-]", "", request.form.get("form_id", ""))[:30]
        tab_ = _reg.OLD_TABS.get(tab_, tab_)
        return redirect(url_for("settings_view", tab=tab_ or None,
                                saved=None if session.get("notice") else (form_ or 1))
                        + (f"#save-{form_}" if form_ and not session.get("notice") else ""))
    n_demo = q("SELECT COUNT(*) n FROM households WHERE demo=1", one=True)["n"]
    import settings_io
    look = settings_io.clean_look(setting("gallery_font"), setting("gallery_bg"), setting("proof_mark"),
                                  setting("proof_mark_text"), setting("proof_mark_strength"))
    logo_, dlogo_ = setting("logo_png"), setting("logo_dark_png")
    look_logos = dict(light=logo_, dark=dlogo_ or (settings_io.white_logo(logo_) if logo_ and tab_ == "branding" else ""))
    own_font_ = dict(name=setting("brand_font_name") or "Your font", light=bool(setting("brand_font_300"))) if setting("brand_font_400") else None
    emb_ = app.config.get("setup_embed")
    where_home_ = Markup(emb_("business", "/settings?tab=profile#location")) if (
        callable(emb_) and (tab_ or "profile") == "profile" and setting("setup_complete") == "1") else ""
    labels_ = q("SELECT label, COUNT(*) n FROM client_labels GROUP BY label ORDER BY lower(label)") if tab_ == "clients" else []
    ron_ = app.config.get("gallery_reset_online")
    reset_online_ = ron_() if tab_ == "system" and callable(ron_) else 0
    return render_template("settings.html", plugin_errors=PLUGIN_ERRORS, look=look, look_logos=look_logos, own_font=own_font_,
                           where_home_form=where_home_, labels=labels_, reset_online=reset_online_,
                           ntfy={r_["key"]: r_["value"] for r_ in q("SELECT key, value FROM settings WHERE key IN ('ntfy_server','ntfy_topic','ntfy_token')")}
                           if tab_ == "notifications" else None,
                           look_pal=settings_io.palette(look["gallery_bg"]), palettes=settings_io.PALETTES, rate=setting("mileage_rate"), home_base=setting("home_base"),
                           photographer=setting("photographer_name"), studio=setting("studio_name"),
                           gallery_base=setting("gallery_base_url"),
                           n_demo=n_demo, plugins=LOADED, db_path=DB_PATH,
                           fees=current_schedule(), term=quote_term(), taxes_=taxes_list(), reviews=review_rows() if request.args.get("tab") == "documents" else [],
                           versions=q("SELECT id, created FROM fee_schedules ORDER BY id DESC LIMIT 8"))

GALLERY_FONTS_DIR = ROOT / "plugins" / "gallery_assets" / "fonts"

@app.get("/settings/gallery-font/<name>.woff2")
def gallery_font_file(name):

    from flask import send_from_directory
    if name in ("own", "own-light"):
        import base64
        v_ = setting("brand_font_400" if name == "own" else "brand_font_300")
        if not v_:
            abort(404)
        r_ = app.response_class(base64.b64decode(v_), mimetype="font/woff2")
        r_.headers["Cache-Control"] = "no-store"
        return r_
    if name not in ("cormorant-garamond", "great-vibes"):
        abort(404)
    return send_from_directory(GALLERY_FONTS_DIR, name + ".woff2", mimetype="font/woff2", max_age=86400)

@app.post("/settings/font")
def brand_font_upload():

    import base64
    import settings_io as _sio
    back_ = url_for("settings_view", tab="branding") + "#gallery-look"
    if request.form.get("look_posted"):
        save_look(request.form)
    if request.form.get("remove"):
        x("DELETE FROM settings WHERE key IN ('brand_font_400', 'brand_font_300', 'brand_font_name')")
        if setting("gallery_font") == "own":
            x("UPDATE settings SET value='classic' WHERE key='gallery_font'")
        audit("Removed the studio's own font")
        return redirect(back_)
    got = {}
    for key_, field_ in (("brand_font_400", "regular"), ("brand_font_300", "light")):
        f_ = request.files.get(field_)
        if f_ and f_.filename:
            try:
                got[key_] = _sio.clean_font(f_.stream.read(_sio.FONT_MAX + 1))
            except Exception:
                session["notice"] = "That file isn't a font that can be used (WOFF2, WOFF, TTF or OTF, up to 2 MB)."
                g.refused = True
                return redirect(back_)
    if not got:
        return redirect(back_)
    if "brand_font_400" not in got and not setting("brand_font_400"):
        session["notice"] = "Add the regular weight first; the light one is optional."
        g.refused = True
        return redirect(back_)
    put_ = "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    for key_, (data_, fam_) in got.items():
        x(put_, (key_, base64.b64encode(data_).decode()))
        if key_ == "brand_font_400":
            x(put_, ("brand_font_name", fam_ or "Your font"))
            x(put_, ("gallery_font", "own"))
    audit("Uploaded the studio's own font" + (" (light)" if list(got) == ["brand_font_300"] else ""))
    session["notice"] = "Your font is added. Galleries you build or rebuild from now on, and the studio page, use it."
    return redirect(back_)

def _demo_words(n_):
    return f"{n_} demo client{'' if n_ == 1 else 's'}"

@app.post("/settings/demo/delete")
def settings_clear_demo():

    n_ = q("SELECT COUNT(*) n FROM households WHERE demo=1", one=True)["n"]
    if not n_:
        g.refused = True
        session["notice"] = "There are no demo clients left to delete."
        return redirect(url_for("settings_view", tab="clients"))
    import jobs
    jobs.start(app, "demo-delete", f"Deleting the {_demo_words(n_)}", _delete_demo_clients)
    session["notice"] = (f"Deleting the {_demo_words(n_)}: their galleries and photos go first. The progress shows at "
                         "the bottom of the page.")
    return redirect(url_for("settings_view", tab="clients"))

def _delete_demo_clients(job):

    sids = [r_["id"] for r_ in q("SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.demo=1")]
    n_ = q("SELECT COUNT(*) n FROM households WHERE demo=1", one=True)["n"]
    job.update(total=len(sids) + 1, done=0)
    for sid in sids:
        for what_, hook_ in (("a demo gallery down", "gallery_take_down"), ("away the demo photos", "retention_purge_now")):
            if app.config.get(hook_):
                try:
                    app.config[hook_](sid)
                except Exception:
                    app.logger.exception("demo delete: %s %s", hook_, sid)
                    raise RuntimeError(f"Could not take {what_}, so the demo clients were kept. Check your backup and "
                                       "gallery storage, then try again.") from None
        job["done"] += 1
    _gallery_down_again(sids)
    import locks
    with locks.WRITES:
        _gallery_still_down(sids)
        x("UPDATE households SET referred_by=NULL WHERE referred_by IN (SELECT id FROM households WHERE demo=1)")
        x("DELETE FROM households WHERE demo=1")
        x("DELETE FROM events WHERE demo=1")
        audit(f"Deleted the {_demo_words(n_)}")
    job["done"] = job["total"]
    return f"Deleted the {_demo_words(n_)} and their photos."

def review_rows():

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
    rows.append(dict(label="Price list terms", for_="every type", reviewed=st["reviewed"], when=st["when"], url=url_for("settings_view", tab="prices") + "#terms"))
    return rows

def logo_src():

    v = setting("logo_png")
    return f"data:image/png;base64,{v}" if v else ""

app.jinja_env.globals["logo_src"] = logo_src
app.jinja_env.globals["iso_today"] = lambda: today().isoformat()

def save_look(form):

    import settings_io
    bg_ = form.get("gallery_bg", "")
    look_ = settings_io.clean_look(form.get("gallery_font"), form.get("gallery_bg_custom", "") if bg_ == "custom" else bg_,
                                   form.get("proof_mark"), form.get("proof_mark_text"), form.get("proof_mark_strength"))
    for k, v in look_.items():
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

@app.post("/settings/logo")
def logo_upload():

    import base64
    import io as _io
    dark = request.form.get("which") == "dark"
    key_, what_ = ("logo_dark_png", "the logo for dark gallery backgrounds") if dark else ("logo_png", "the logo")
    back_ = url_for("settings_view", tab="branding") + "#gallery-look" if dark else url_for("settings_view", tab="branding") + "#logo"
    if dark and request.form.get("look_posted"):
        save_look(request.form)
    if request.form.get("remove"):
        x("DELETE FROM settings WHERE key=?", (key_,))
        audit("Removed " + what_)
        return redirect(back_)
    f_ = request.files.get("logo")
    import settings_io as _sio
    try:
        buf = _io.BytesIO(_sio.clean_logo(f_.stream.read(64 * 1024 * 1024)))
    except Exception:
        session["notice"] = "That file isn't a PNG or JPEG picture we can use."
        g.refused = True
        return redirect(back_)
    x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key_, base64.b64encode(buf.getvalue()).decode()))
    audit("Uploaded " + ("the logo for dark gallery backgrounds" if dark else "a logo for the documents"))
    if dark:
        session["notice"] = "Logo for dark backgrounds uploaded."
    return redirect(back_)

def _plugin_settings(con):

    out = {}
    for name_, part_ in hooks.settings_parts.items():
        try:
            v_ = part_["export"](con)
        except Exception:
            app.logger.exception("settings file: the %s part", name_)
            continue
        if v_ is not None:
            out[name_] = v_
    return out

def _backup_extras():

    import settings_io
    from vault import web as vault_web_
    con = connect()
    try:
        get = lambda k: (lambda r_: r_[0] if r_ else None)(con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone())
        r_ = con.execute("SELECT items FROM fee_schedules ORDER BY id DESC LIMIT 1").fetchone()
        data = settings_io.export(get, json.loads(r_[0]) if r_ else [], vault_web_.config(), _plugin_settings(con))
    finally:
        con.close()
    out = [("settings.json", json.dumps(data, indent=1).encode())]
    if vault_web_.CFG_PATH.exists():
        out.append(("storage.json", vault_web_.CFG_PATH.read_bytes()))
    for p_ in (DELETIONS_JSON, NUMBERS_JSON):

        if p_.exists():
            out.append((p_.name, p_.read_bytes()))
    return out

vault_backup_mod = __import__("vault.backup", fromlist=["EXTRAS"])
vault_backup_mod.EXTRAS.append(_backup_extras)

@app.get("/settings/export.json")
def settings_export():

    import settings_io
    from vault import web as vault_web_
    data = settings_io.export(setting, current_schedule()["items"], vault_web_.config(), _plugin_settings(db()))
    r = app.response_class(json.dumps(data, indent=1), mimetype="application/json")
    r.headers["Content-Disposition"] = f'attachment; filename="lumen-aureum-settings-{today().isoformat()}.json"'
    audit("Exported the settings")
    return r

@app.post("/settings/import")
def settings_import():

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
        parts = {}
        for name_, raw_ in settings_io.plugin_parts(raw_text).items():
            if name_ in hooks.settings_parts:
                try:
                    parts[name_] = hooks.settings_parts[name_]["check"](raw_)
                except ValueError as e_:
                    raise settings_io.ImportProblem(str(e_)) from None
    except settings_io.ImportProblem as e:
        session["import_result"] = dict(error=str(e)[:200])
        return redirect(back)
    first_setup = setting("setup_complete") != "1"
    skipped = []
    if not first_setup:
        if storage:
            skipped.append("backup and gallery locations")
        storage = {}
        if new_settings.pop("gallery_base_url", None):
            skipped.append("gallery address") if "backup and gallery locations" not in skipped else None
    gal, held_ = {}, {}
    if first_setup:
        for part_ in ("primary", "secondary"):
            if storage.get(part_):
                held_[part_] = {k_: v_ for k_, v_ in storage.pop(part_).items() if k_ not in settings_io.SECRET_FIELDS}
        if storage.get("gallery_host"):
            gal["host"] = {k_: v_ for k_, v_ in storage.pop("gallery_host").items() if k_ not in settings_io.SECRET_FIELDS}
        if new_settings.get("gallery_base_url"):
            gal["url"] = new_settings.pop("gallery_base_url")
    notices = []
    own_words = [d_ for d_ in ("agreement", "release", "transfer", "license") if (new_settings.get(f"{d_}_text") or "").strip()
                 or any((t_.get(f"{d_}_text") or "").strip() for t_ in (new_rules.get("TYPES") or []))]
    if own_words:
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
        was_ = prices_approved(current_schedule()["id"])
        new_id = x("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)", (now_local(), json.dumps(fees), "Imported settings"))
        if was_:
            approve_prices(new_id, quiet=True)
    if storage:
        cur.update(storage)
        vault_web_.write_config(cur)
    result = dict(ok=True, skipped=skipped, notices=notices)
    if held_:
        result["storage"] = held_
        result["storage_words"] = "; ".join(("Second location: " if p_ == "secondary" else "Backups: ") + vault_web_.describe(c_)
                                           for p_, c_ in held_.items())
    if gal:
        h_ = gal.get("host", {})
        result["gallery"] = gal
        result["gallery_words"] = " ".join(x_ for x_ in (h_.get("type", "").upper(), h_.get("endpoint") or "",
                                                         h_.get("host") or h_.get("bucket") or h_.get("path") or "",
                                                         gal.get("url", "")) if x_)
    apply_settings()
    for name_, v_ in parts.items():
        notices.extend(hooks.settings_parts[name_]["apply"](v_) or [])
    session["import_result"] = result
    if new_rules:
        sync_open_sessions()
    return redirect(back)

def _save_rules(**kv):
    try:
        merged = json.loads(setting("rules") or "{}")
    except ValueError:
        merged = {}
    if "TYPES" in kv and "LICENSE_TYPES" not in kv and "LICENSE_TYPES" in merged:
        had = {t_.get("key") for t_ in C.TYPES}
        new_ = [t_["key"] for t_ in kv["TYPES"] if t_.get("key") not in had]
        if new_:
            kv = dict(kv, LICENSE_TYPES=list(merged["LICENSE_TYPES"]) + [k_ for k_ in new_ if k_ not in merged["LICENSE_TYPES"]])
    merged.update(kv)
    x("INSERT INTO settings (key, value) VALUES ('rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (json.dumps(merged),))
    apply_settings()

def own_wording(doc, stype=None):

    import wording
    t_ = next((t for t in C.TYPES if t["key"] == stype), None) if stype else None
    return ((t_ or {}).get(f"{doc}_text") or "").strip() or setting(f"{doc}_text").strip() or wording.STANDARD[doc]

REVIEW_DOCS = {"agreement": "Agreement", "release": "Release", "transfer": "Copyright transfer", "license": "Photo license",
               "terms": "Terms (quote, invoice, session sheet, price list)"}

def _review_key(doc, stype=None):

    if doc == "terms":
        return f"terms:{stype or '*'}"
    t_ = next((t for t in C.TYPES if t["key"] == stype), None) if stype else None
    return f"{doc}:{stype}" if t_ and (t_.get(f"{doc}_text") or "").strip() else f"{doc}:*"

def _freeze_reviewed_wording():

    import wording_legacy
    t_ = setting("transfer_text")
    if wording_legacy.OLD_TRANSFER_PORTFOLIO in t_:
        x("UPDATE settings SET value=? WHERE key='transfer_text'",
          (t_.replace(wording_legacy.OLD_TRANSFER_PORTFOLIO, "[FAVORITES]"),))
        try:
            session["notice"] = "Your copyright transfer letter's portfolio paragraph now matches the portfolio promise in your agreement."
        except RuntimeError:
            pass
    if setting("wording_frozen_1_4") != "1":

        import wording
        r_ = _reviews()
        reviewed = "agreement:*" in r_ or setting("agreement_reviewed") == "1"
        if setting("wording_frozen_1_0") == "1" and reviewed and not setting("agreement_text").strip():
            x("INSERT INTO settings (key, value) VALUES ('agreement_text', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (wording.AGREEMENT_1_3,))
        x("INSERT INTO settings (key, value) VALUES ('wording_frozen_1_4','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
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

    return _review_key(doc, stype) in _reviews()

def review_url(doc, stype=None):
    if stype and stype not in {t_["key"] for t_ in C.TYPES}:
        stype = None
    if doc == "terms":
        return url_for("type_edit", key=stype) + "#terms" if stype else url_for("settings_view", tab="prices") + "#terms"
    return url_for("wording_edit", doc=doc, type=stype or None)

@app.post("/settings/review/<doc>")
def doc_review(doc):

    if doc not in REVIEW_DOCS:
        abort(404)
    stype = request.form.get("type") or None
    if stype and stype not in C.SESSION_TYPES:
        abort(404)
    r_ = _reviews()
    key = _review_key(doc, stype)
    if request.form.get("revert"):
        r_.pop(key, None)
        if doc == "terms" and stype:
            types = [dict(t) for t in C.TYPES]
            t_ = next(t for t in types if t["key"] == stype)
            shipped = next((t for pr in C.PRESETS.values() for t in pr["types"] if t["key"] == stype), None)
            t_["policy"] = dict((shipped or {}).get("policy") or {})
            t_.pop("deposit_pct", None); t_.pop("deposit_amount", None); t_.pop("deposit_own", None)
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

            import wording
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (f"{doc}_text", wording.STANDARD[doc]))
        audit(f"Reviewed the {REVIEW_DOCS[doc].split(' (')[0].lower()} wording" + (f" for {C.SESSION_TYPES[stype]}" if stype else ""))
    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
    return redirect(safe_back(request.form.get("back")) or review_url(doc, stype))

def review_state(doc, stype=None):

    when = _reviews().get(_review_key(doc, stype))
    return dict(doc=doc, stype=stype, reviewed=bool(when), when=nice(when) if when else "")

app.jinja_env.globals["review_state"] = review_state

@app.route("/settings/wording/<doc>", methods=["GET", "POST"])
def wording_edit(doc):

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
        if t_:
            if text == general.strip():
                text = ""
            if text:
                t_[key] = text
            else:
                t_.pop(key, None)
                r_ = _reviews()
                if r_.pop(f"{doc}:{stype}", None):
                    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
            _save_rules(TYPES=types)
        else:
            if text == wording.STANDARD[doc].strip():
                text = ""
            x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, text))
        audit(f"{'Reset' if request.form.get('reset') else 'Saved'} the {doc} wording" + (f" for {t_['label']}" if t_ else ""))
        return redirect(url_for("wording_edit", doc=doc, type=stype or None, saved=1))
    if t_:
        own = (t_.get(key) or "").strip()
        text = own or general
        own = own if own.split() != (general or "").split() else ""
    else:
        own = setting(key).strip()
        text = own or wording.STANDARD[doc]
        own = own if own.split() != wording.STANDARD[doc].split() else ""
    return render_template("wording_edit.html", doc=doc, label=wording.LABELS.get(doc, doc.capitalize()), text=text, own=bool(own), stype=t_,
                           typed=[t for t in types if (t.get(key) or "").strip()], all_types=types,
                           fields=wording.FIELDS[doc], saved=request.args.get("saved"))

def wording_here(doc, stype=None):

    import wording
    if doc not in wording.STANDARD:
        return None
    t_ = next((t for t in C.TYPES if t["key"] == stype), None) if stype else None
    own_type = bool(t_ and (t_.get(f"{doc}_text") or "").strip())
    return dict(doc=doc, type=stype if own_type else "", text=own_wording(doc, stype),
                scope=("For " + t_["label"] + " only") if own_type else "For every session type without its own wording")

app.jinja_env.globals["wording_here"] = wording_here

@app.post("/settings/wording/<doc>/here")
def wording_edit_here(doc):

    import wording
    if doc not in wording.STANDARD:
        abort(404)
    stype = request.form.get("type") or ""
    types = [dict(t) for t in C.TYPES]
    t_ = next((t for t in types if t["key"] == stype), None)
    if stype and not t_:
        abort(404)
    text = wording.clean(request.form.get("text", ""))
    key = f"{doc}_text"
    if not text.strip():
        session["notice"] = "The wording can't be empty: nothing was saved."
        return redirect(safe_back(request.form.get("back")) or url_for("wording_edit", doc=doc, type=stype or None))
    if t_:
        t_[key] = text
        _save_rules(TYPES=types)
    else:
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, text))
    r_ = _reviews()
    r_[_review_key(doc, stype or None)] = today().isoformat()
    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
    what = REVIEW_DOCS[doc].split(" (")[0].lower()
    audit(f"Edited and reviewed the {what} wording on the document" + (f" for {t_['label']}" if t_ else ""))
    return redirect(safe_back(request.form.get("back")) or url_for("wording_edit", doc=doc, type=stype or None))

UNAGREED_SQL = """SELECT id FROM sessions WHERE type=? AND stage IN ('inquiry','quoted')
                  AND NOT EXISTS (SELECT 1 FROM quote_versions v WHERE v.session_id=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.session_id=sessions.id AND p.voided IS NULL)
                  AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.session_id=sessions.id AND d.status='signed')"""

def _walk_state():
    try:
        return json.loads(setting("walk") or "{}")
    except ValueError:
        return {}

def _no_cw_check():

    from plugins import gallery as _G
    if not _G.static_host():
        return None
    con = connect()
    try:
        n_ = len(_G.codewords_waiting(con))
    finally:
        con.close()
    return dict(words=_G.NO_CODEWORD_CHECK, confirm=_G.codewords_off_confirm(n_),
                choose=url_for("setup_step", step="galleries", replace="gallery", _anchor="f"))

@app.route("/settings/type/<key>/walk/<int:n>", methods=["GET", "POST"])
def type_walk(key, n):

    import walkthrough as W
    types = [json.loads(json.dumps(t_)) for t_ in C.TYPES]
    t = next((t_ for t_ in types if t_["key"] == key), None) or abort(404)
    steps = W.steps_for(t)
    st = _walk_state()
    mine = st.setdefault(key, {"done": [], "skipped": []})
    if request.method == "POST" and n < len(steps):
        step, act = steps[n], request.form.get("act") or ("save" if request.form.get("cw_all_off") else None)
        if act == "save":
            before_ = json.dumps(t, sort_keys=True)
            step["put"](request.form)
            if json.dumps(t, sort_keys=True) != before_:
                mine["changed"] = True
            _save_rules(TYPES=types)
            if step["id"] in ("portfolio", "deposit"):
                for r_ in q(UNAGREED_SQL, (key,)):
                    reapply_discount(r_["id"])
            if step["id"] == "steps":
                _sync_type_sessions(key)
            if step["id"] == "codeword" and request.form.get("cw_all_off") and _no_cw_check():
                app.config["codewords_off"](con=db(), save=_save_rules)
        if act in ("save", "keep", "finish"):
            mine["done"] = sorted(set(mine["done"]) | {step["id"]})
            mine["skipped"] = [i for i in mine["skipped"] if i != step["id"]]
        elif act == "skip":
            mine["skipped"] = sorted(set(mine["skipped"]) | {step["id"]})
        _put_setting("walk", json.dumps(st))
        nxt = len(steps) if act == "finish" else n + 1
        return redirect(url_for("type_walk", key=key, n=nxt))
    if n >= len(steps):

        was_ = (mine.get("changed"), mine.get("walked"))
        mine.pop("changed", None)
        mine["walked"] = mine.get("walked") or today().isoformat()
        if was_ != (None, mine["walked"]):
            _put_setting("walk", json.dumps(st))
        return render_template("walk.html", t=t, steps=steps, n=len(steps), mine=mine, summary=True,
                               terms=C.build_terms(key, setting("business_name")), rs=review_state("terms", key))
    step = steps[n]
    auto_label = {k_: v_[0] for k_, v_ in AUTO.items()}
    return render_template("walk.html", t=t, steps=steps, n=n, step=step, value=step["get"](), preview=step["preview"](),
                           no_cw=_no_cw_check() if step["id"] == "codeword" else None, mine=mine, summary=False, reuse=W.reuse(key, step.get("reuse", step["id"])),
                           step_rows=C.step_rows(t, auto_label) if step["id"] == "steps" else None)

@app.post("/settings/type/<key>/walk/approve")
def type_walk_approve(key):

    t = next((t_ for t_ in C.TYPES if t_["key"] == key), None) or abort(404)
    r_ = _reviews()
    r_[_review_key("terms", key)] = today().isoformat()
    x("INSERT INTO settings (key, value) VALUES ('doc_reviews',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(r_),))
    audit(f"Reviewed the {REVIEW_DOCS['terms'].split(' (')[0].lower()} wording for {t['label']}")
    st = _walk_state()
    mine = st.setdefault(key, {"done": [], "skipped": []})
    mine.pop("changed", None)
    mine["finished"] = today().isoformat()
    mine["walked"] = mine.get("walked") or mine["finished"]
    _put_setting("walk", json.dumps(st))
    session["notice"] = f"{t['label']}'s terms are approved. They no longer print as SAMPLE."
    return redirect(url_for("type_edit", key=key) + "#terms")

_WALK_PV = {}
_WALK_PV_LOCK = threading.Lock()

@app.post("/settings/type/<key>/walk/<int:n>/preview")
def type_walk_preview(key, n):

    import importlib.util
    with _WALK_PV_LOCK:
        if not _WALK_PV:
            for name_, mod_ in (("config", "_pv_config"), ("walkthrough", "_pv_walkthrough")):
                spec_ = importlib.util.spec_from_file_location(mod_, os.path.join(os.path.dirname(__file__), name_ + ".py"))
                m_ = importlib.util.module_from_spec(spec_)
                spec_.loader.exec_module(m_)
                _WALK_PV[name_] = m_
        C2, W2 = _WALK_PV["config"], _WALK_PV["walkthrough"]
        W2.C = C2
        try:
            rules_ = json.loads(setting("rules") or "{}")
        except ValueError:
            rules_ = {}
        C2.apply(rules_)
        types_ = [json.loads(json.dumps(t_)) for t_ in C.TYPES]
        t_ = next((x_ for x_ in types_ if x_["key"] == key), None)
        if t_ is None:
            abort(404)
        steps_ = W2.steps_for(t_)
        if not 0 <= n < len(steps_):
            abort(404)
        try:
            steps_[n]["put"](request.form)
            C2.apply(dict(rules_, TYPES=types_))
            text_ = steps_[n]["preview"]()
        except Exception:
            app.logger.exception("walk preview")
            text_ = None
    return {"preview": text_}

def update_notice():

    raw_ = setting("update_notice")
    if not raw_:
        return None
    try:
        upd_ = json.loads(raw_)
    except ValueError:
        upd_ = None
    if not isinstance(upd_, dict) or (upd_.get("shown") and upd_["shown"] < today().isoformat()):
        x("DELETE FROM settings WHERE key='update_notice'")
        g.tx = True
        return None
    if not upd_.get("shown"):
        upd_["shown"] = today().isoformat()
        _put_setting("update_notice", json.dumps(upd_))
        g.tx = True
    return upd_

@app.post("/today/update-notice-hide")
def update_notice_hide():

    x("DELETE FROM settings WHERE key='update_notice'")
    return redirect(safe_back(request.form.get("back")) or url_for("home"))

import time as _time
STARTED_AT = _time.monotonic()
BACKUP_STALE_DAYS = 2
BACKUP_GRACE = 2 * 3600

def _alert(text, link, level="look", **kw):
    return dict(text=text, link=link, level=level, **kw)

def _backup_alerts():

    out = []
    con = db()
    has_key = bool(vault_keys.master_pub(con))
    to_vault = url_for("vault_view")
    if not has_key:
        return [_alert("Backups are not running: there is no master key yet.", to_vault, "fix")]
    try:
        cfg = vault_web.config()
    except Exception:
        return [_alert("The backup settings could not be read.", to_vault, "fix")]
    conn = (app.config.get("conn_status") or (lambda: {}))()
    for which, part, words, card in (("primary", "primary", "backup location", "f-primary_type"),
                                     ("secondary", "secondary", "second backup location", "f-secondary_type")):
        if not cfg.get(part):
            continue
        st_ = conn.get(which)
        link_ = url_for("setup_step", step="storage") + "#" + card
        if not st_:
            out.append(_alert(f"Your {words} has never been tested.", link_, "fix"))
        elif not st_.get("ok"):
            out.append(_alert(f"The last test of your {words} failed" + (f": {st_['why']}" if st_.get("why") else "."), link_, "fix"))
    err_ = setting("backup_last_error")
    if err_:
        out.append(_alert(f"The last backup failed: {err_}", to_vault, "fix"))
    last_ = (q("""SELECT MAX(created) m FROM vault_objects WHERE kind IN ('db', 'manual', 'upgrade') AND deleted IS NULL""",
               one=True) or {"m": None})["m"]
    if _time.monotonic() - STARTED_AT >= BACKUP_GRACE:
        try:
            at_ = datetime.fromisoformat(last_) if last_ else None
        except ValueError:
            at_ = None
        if not at_:
            out.append(_alert("No backup has been made yet.", to_vault, "fix"))
        elif datetime.now() - at_ > timedelta(days=BACKUP_STALE_DAYS):
            out.append(_alert(f"The last backup was on {nice(at_.date().isoformat())}, more than {BACKUP_STALE_DAYS} days ago.",
                              to_vault, "fix"))
    if cfg.get("secondary") and setting("backup_second_error"):
        out.append(_alert("Your second backup copy could not be checked, so it is not counted yet.", to_vault, "fix"))
    if setting("photo_mirror_error"):
        out.append(_alert("The last copy of your photos to the backup failed.", to_vault, "fix"))
    return out

BACKUP_LATE = timedelta(hours=26)

def backup_problem():

    if setting(inboxnotice.KEY_BACKUP_ON) != "1" or not vault_keys.master_pub(db()):
        return ""
    if setting("backup_last_error"):
        return "failed"
    if _time.monotonic() - STARTED_AT < BACKUP_GRACE:
        return ""
    last_ = (q("""SELECT MAX(created) m FROM vault_objects WHERE kind IN ('db', 'manual', 'upgrade') AND deleted IS NULL""",
               one=True) or {"m": None})["m"]
    try:
        at_ = datetime.fromisoformat(last_) if last_ else None
    except ValueError:
        at_ = None
    return "late" if not at_ or datetime.now() - at_ > BACKUP_LATE else ""

def storage_problem():

    import shutil
    import photostore as _ps
    for p_ in (DB_PATH.parent, _ps.PHOTOS_ROOT):
        try:
            du_ = shutil.disk_usage(p_)
            if du_.total and du_.free * 100 < du_.total * inboxnotice.STORAGE_LOW_PCT:
                return True
        except OSError:
            continue
    return False

def galleries_closing_tomorrow():

    if not q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True):
        return 0
    tomorrow_ = today() + timedelta(days=1)
    n_ = 0
    for s_ in q("""SELECT s.* FROM sessions s JOIN galleries g ON g.session_id=s.id
                   WHERE g.upload_state='up' AND g.removed_at IS NULL"""):
        exp_ = gallery_expires(s_)
        if isinstance(exp_, str):
            exp_ = d(exp_[:10])
        if exp_ == tomorrow_:
            n_ += 1
    return n_

def update_installed():

    raw_ = setting("update_notice")
    if not raw_:
        return ""
    try:
        v_ = json.loads(raw_)
    except ValueError:
        return ""
    if not isinstance(v_, dict):
        return ""
    return "|".join(str(v_.get(k_) or "") for k_ in ("version", "was", "backup_at"))

def served_page_changed():

    fn_ = app.config.get("gallery_served_changed")
    return sorted((fn_() or {}).keys()) if callable(fn_) else []

STATUS_PROBLEMS = {"storage": storage_problem, "closing": galleries_closing_tomorrow, "update": update_installed,
                   "served": served_page_changed, "cred": lambda: setting(inboxnotice.KEY_CRED_AT)}

def _connection_alerts():

    out = []
    conn = (app.config.get("conn_status") or (lambda: {}))()
    try:
        has_host_ = bool(vault_web.config().get("gallery_host"))
    except Exception:
        has_host_ = False
    if has_host_:
        st_ = conn.get("gallery")
        link_ = url_for("setup_step", step="galleries") + "#f-gallery_type"
        if not st_:
            out.append(_alert("Your web host has never been tested.", link_))
        elif not st_.get("ok"):
            out.append(_alert("The last test of your web host failed" + (f": {st_['why']}" if st_.get("why") else "."), link_, "fix"))
    st_ = conn.get("ntfy")
    if ntfy_settings() and st_ and not st_.get("ok"):
        out.append(_alert("The last test notice to your phone could not be sent" + (f": {st_['why']}" if st_.get("why") else "."),
                          url_for("settings_view", tab="notifications") + "#notice", "fix"))
    return out

def _setup_alerts():

    out = []
    urgent_ = app.config["urgent_items"]() if "urgent_items" in app.config else []
    out += [_alert(t_ if t_.endswith(".") else t_ + ".", l_, "fix") for t_, l_ in urgent_]
    pend_ = owner_auth.master_pending(db())
    if pend_:
        out.append(_alert(f"A new master key takes effect on {pend_['when']}. If you didn't ask for it, cancel it.",
                          url_for("security") + "#master-pending", "fix", post=url_for("master_pending_cancel"),
                          verb="Cancel the new master key"))
    if setting(owner_auth.ROTATE_DUE):
        out.append(_alert("Your master key was replaced. Rotate the data key, so the old key can't open your data "
                          "through an earlier backup.", url_for("security") + "#rotate-due", "fix"))
    if owner_auth.master_check_due(db(), today()):
        out.append(_alert("Check you still have your master key. It is the only way back in if your password is lost.",
                          url_for("master_key_check"), post=url_for("master_key_later"), verb="Ask next week"))
    sched_ = current_schedule()["id"]
    if not prices_approved(sched_):
        out.append(_alert("Your prices are still the starter prices (SAMPLE). Review and approve them.",
                          url_for("fees_view", sched_id=sched_)))
    for k_, lab_ in C.SESSION_TYPES.items():
        if k_ != C.PERSONAL_TYPE["key"] and not type_walked(k_):
            out.append(_alert(f"{lab_}: this session type has not been walked through yet.", url_for("type_walk", key=k_, n=0)))
    said_ = {t_ for t_, _ in urgent_}
    first_ = url_for("contact_new")
    out += [_alert(t_ if t_.endswith(".") else t_ + ".", l_) for t_, l_ in (app.config["finish_items"]() if "finish_items" in app.config else [])
            if t_ not in said_ and l_ != first_]
    return out

def system_alerts():

    if "alerts" in g:
        return g.alerts
    out = []
    try:
        if setup_done():
            out += _backup_alerts() + _connection_alerts()
            out += [_alert(f"A plugin of yours did not load at the last start: {e_}.", url_for("plugins_settings"), "fix")
                    for e_ in PLUGIN_ERRORS]
            for fn in hooks.alerts:
                try:
                    out += [dict(it_, level=it_.get("level") or "look") for it_ in (fn() or [])]
                except Exception:
                    app.logger.exception("alert source %s failed", getattr(fn, "__name__", fn))
            out += _setup_alerts()
    except Exception:
        app.logger.debug("alerts unavailable", exc_info=True)
        out = []
    out.sort(key=lambda a_: a_["level"] != "fix")
    if has_request_context():
        g.alerts = out
    return out

def alert_counts():

    a_ = system_alerts()
    return sum(1 for it_ in a_ if it_["level"] == "fix"), len(a_)

app.jinja_env.globals["alert_counts"] = alert_counts

@app.get("/alerts")
def alerts_view():

    return render_template("alerts.html", alerts=system_alerts())

def type_walked(key, t=None):

    st_ = _walk_state()
    mine = st_.get(key) or {}
    return bool(mine.get("walked") or mine.get("finished") or mine.get("done") or (st_.get("*") or {}).get("walked"))

app.jinja_env.globals["type_walked"] = type_walked

@app.route("/settings/type/<key>", methods=["GET", "POST"])
def type_edit(key):

    types = [dict(t) for t in C.TYPES]
    t = next((t_ for t_ in types if t_["key"] == key), None) or abort(404)
    if request.method == "GET" and not type_walked(key, t):

        return redirect(url_for("type_walk", key=key, n=0))
    kind_flow = C.KINDS[t["kind"]]["flow"]
    step_kind = "event" if t["kind"] == "event" else "do"
    if request.method == "POST":
        f = request.form
        steps, off = {}, {}
        for stage in C.STEP_STAGES:
            rows, off_ = [], []
            for i in range(60):
                k_ = f"{stage}_{i}"
                if k_ + "_kind" not in f:
                    break
                if f.get(k_ + "_drop") and not (f[k_ + "_kind"] == "auto" and f.get(k_ + "_key") == C.GALLERY_STEP[1]):
                    continue
                if f[k_ + "_kind"] == "auto":
                    if f.get(k_ + "_key") not in C.AUTO_KEYS:
                        continue
                    row = ["auto", f[k_ + "_key"]]
                else:
                    label = (f.get(k_ + "_label") or "").strip()[:80]
                    if not label:
                        continue
                    row = [step_kind, label, _days(f.get(k_ + "_days"))]
                rows.append(row)

                if f.get(k_ + "_on") == "0" and C.step_id(row) != C.GALLERY_STEP:
                    off_.append(list(C.step_id(row)))
            if f.get(stage + "_new"):
                rows.append([step_kind, f[stage + "_new"].strip()[:80], _days(f.get(stage + "_new_days"))])
            if f.get(stage + "_auto") in C.AUTO_KEYS:
                rows.append(["auto", f[stage + "_auto"]])
            if [tuple(r) for r in rows] != [tuple(r) for r in kind_flow.get(stage, [])]:
                steps[stage] = rows
            if off_:
                off[stage] = off_
        _put_steps(t, steps, off)
        for flag in ("all_sign", "indoors"):
            if f.get(flag):
                t[flag] = True
            else:
                t.pop(flag, None)
        t.pop("portfolio_pct", None); t.pop("portfolio_amount", None)
        pv_ = _int_or_none(f.get("portfolio_value"))
        if pv_ and f.get("portfolio_mode") in ("pct", "amt"):
            t["portfolio_pct" if f["portfolio_mode"] == "pct" else "portfolio_amount"] = min(pv_, 100 if f["portfolio_mode"] == "pct" else 100000)
        dv = _int_or_none(f.get("deposit_value"))
        t.pop("deposit_pct", None); t.pop("deposit_amount", None); t.pop("deposit_own", None)
        if dv is not None and f.get("deposit_mode") in ("pct", "amt"):
            t["deposit_pct" if f["deposit_mode"] == "pct" else "deposit_amount"] = max(0, min(dv, 100 if f["deposit_mode"] == "pct" else 100000))
            t["deposit_own"] = True
        else:
            shipped = C.shipped_type(key)
            for k_ in ("deposit_pct", "deposit_amount"):
                if shipped and shipped.get(k_) is not None:
                    t[k_] = shipped[k_]
        if "installments" in f:
            n_ = _int_or_none(f.get("installments"))
            had_ = int(t.pop("installments", None) or 0)
            if n_ and n_ >= 2:
                t["installments"] = n_ if n_ == had_ else min(n_, C.INSTALLMENTS_MAX)
        t["policy"] = dict(free_reschedule=bool(f.get("free_reschedule")), outfits=bool(f.get("outfits")),
                           wear=((f.get("wear") or "").strip().rstrip(".")[:200] or C.DEFAULT_WEAR),
                           **{k_: (f.get(k_) or "").strip()[:200] for k_, _, _ in C.FILL_INS},
                           notice_hours=max(0, min(2000, _int_or_none(f.get("notice_hours")) or 0)),
                           late_minutes=max(0, min(2000, _int_or_none(f.get("late_minutes")) or 0)),
                           keep_raw_days=max(1, min(3650, _int_or_none(f.get("keep_raw_days")) or C.raw_keep_days(key))))
        if f.get("delivery_method") in C.DELIVERY_METHODS:
            t["policy"]["delivery_method"] = f["delivery_method"]
        t["policy"]["gallery_codeword"] = bool(f.get("gallery_codeword"))
        dd_ = _int_or_none(f.get("delivery_days"))
        general_ = C.EVENT_DELIVERY_DAYS if t["kind"] == "event" else C.DELIVERY_DAYS
        if dd_ and dd_ > 0 and dd_ != general_:
            t["policy"]["delivery_days"] = min(dd_, 365)
        gd_ = _int_or_none(f.get("gallery_days"))
        if gd_ and gd_ > 0 and gd_ != C.GALLERY_DAYS:
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
                reapply_discount(r_["id"])
        if f.get("cw_all_off") and _no_cw_check():
            app.config["codewords_off"](con=db(), save=_save_rules)
        return redirect(url_for("type_edit", key=key, saved=1))
    import walkthrough as W
    auto_label = {k_: v_[0] for k_, v_ in AUTO.items()}
    return render_template("type_edit.html", t=t, step_rows=C.step_rows(t, auto_label), stages=C.STEP_STAGES, policy=C.POLICY[key],
                           dep_setup=C.deposit_follows_setup(t) or C.DEPOSIT_RULE.get(key) is None, dep_rule=C.DEPOSIT_RULE.get(key),
                           questions="\n".join(q_[1] for q_ in C.INTAKE[key]), reuse=lambda fid: W.reuse(key, fid),
                           auto_label=auto_label, saved=request.args.get("saved"), no_cw=_no_cw_check(),
                           type_panels=[Markup(h_) for h_ in (fn(t) for fn in hooks.type_panels) if h_])

def _put_steps(t, steps, off):

    t.pop("steps", None)
    t.pop("steps_off", None)
    if steps:
        t["steps"] = steps
    if off:
        t["steps_off"] = off

def _sync_type_sessions(key):

    for r_ in q("SELECT id FROM sessions WHERE type=? AND stage NOT IN ('closed','lost')", (key,)):
        sync_workflow_tasks(r_["id"])

@app.post("/settings/type/<key>/steps-reset")
def type_steps_reset(key):

    types = [dict(t_) for t_ in C.TYPES]
    t = next((t_ for t_ in types if t_["key"] == key), None) or abort(404)
    _put_steps(t, {}, {})
    _save_rules(TYPES=types)
    _sync_type_sessions(key)
    audit(f"Reset the steps for {t['label']} to the defaults")
    session["notice"] = f"{t['label']} has the default steps again."
    return redirect(safe_back(request.form.get("back")) or url_for("type_edit", key=key))

def _parse_questions(text, current):

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

    n = _int_or_none(v)
    return None if n is None else max(-365, min(365, n))

def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None

@app.post("/settings/import/gallery")
def settings_import_gallery():

    from vault import backends as vault_backends_, web as vault_web_
    res = session.get("import_result") or {}
    gal = res.get("gallery")
    held_ = res.get("storage")
    if held_ and request.form.get("use") == "1" and setting("setup_complete") != "1":
        import settings_io
        cur = vault_web_.config()
        for part_, new in held_.items():
            if part_ not in ("primary", "secondary") or not isinstance(new, dict):
                continue
            new = dict(new)
            for k_ in ("path", "key_file", "known_hosts"):
                if new.get(k_) and settings_io._inside_private(str(new[k_])):
                    new = None
                    break
            if new is None:
                continue
            cur[part_] = vault_backends_.carry_secrets(cur.get(part_) or {}, new, settings_io.SECRET_FIELDS)
        vault_web_.write_config(cur)
        audit("Backup locations set from the imported file")
    if gal and request.form.get("use") == "1":
        if gal.get("host"):
            import settings_io
            cur = vault_web_.config()
            old = cur.get("gallery_host") or {}

            cur["gallery_host"] = vault_backends_.carry_secrets(old, dict(gal["host"]), settings_io.SECRET_FIELDS)
            vault_web_.write_config(cur)
        if gal.get("url"):
            x("INSERT INTO settings (key, value) VALUES ('gallery_base_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (gal["url"],))
        audit("Web host set from the imported file")
    for k_ in ("gallery", "gallery_words", "storage", "storage_words"):
        res.pop(k_, None)
    session["import_result"] = res
    return redirect(safe_back(request.form.get("back")) or url_for("settings_view"))

@app.post("/settings/reset")
def settings_reset():

    if request.form.get("confirm", "").strip() != "RESET":
        return redirect(url_for("settings_view") + "?reset=nope")
    import jobs
    take_down_ = request.form.get("take_down") == "1"

    def work(job):
        down, noted, left = _reset_work(job, take_down=take_down_)
        out = "Reset done. The old database was sealed into the vault first (kept 30 days)."
        if down:
            out += f" {down} galler{'y' if down == 1 else 'ies'} came down from the web host."
        if noted:
            out += (f" {noted} galler{'y' if noted == 1 else 'ies'} could not come down yet; "
                    f"{'it is' if noted == 1 else 'they are'} tried again at the next check and listed on Today.")
        if left:
            out += (f" {left} galler{'y stays' if left == 1 else 'ies stay'} online; the app no longer manages "
                    f"{'it' if left == 1 else 'them'}.")
        return out
    jobs.start(app, "reset", "Resetting the database (a sealed copy first)", work)
    return redirect(url_for("settings_view", tab="system"))

def _reset_work(job=None, take_down=False):

    from vault import backup as vault_backup
    down = noted = left = 0

    vault_backup.sealed_backup(str(DB_PATH), str(FACES) if FACES.exists() else None, kind="reset")
    ron_, rtd_ = app.config.get("gallery_reset_online"), app.config.get("gallery_reset_take_down")
    if take_down and callable(rtd_):
        down, noted = rtd_(job)
    elif callable(ron_):
        left = ron_()

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
    sweep_photo_orphans()
    return down, noted, left

def sweep_photo_orphans():

    import photostore
    con_ = connect()
    try:
        return photostore.sweep_orphans(con_)
    finally:
        con_.close()

def _wipe_after_backup(saved):
    con = connect()
    con.execute("PRAGMA foreign_keys=OFF")
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t_ in tables:
        if t_ not in ("settings", "fee_schedules", "vault_objects", "vault_keys", "users",
                      "gallery_leftovers"):
            con.execute("DELETE FROM " + dbmod.ident(t_))
    con.execute("DELETE FROM settings WHERE key IN ('upgraded_on', 'upgraded_sids')")

    cur = con.execute("SELECT items, note FROM fee_schedules ORDER BY id DESC LIMIT 1").fetchone()
    cur_id = con.execute("SELECT MAX(id) FROM fee_schedules").fetchone()[0] if cur else None
    con.execute("DELETE FROM fee_schedules")
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        con.execute("DELETE FROM sqlite_sequence")
    if cur:
        con.execute("INSERT INTO fee_schedules (id, created, items, note) VALUES (1, ?, ?, ?)",
                    (now_local(), cur[0], cur[1]))
    ap_ = con.execute("SELECT value FROM settings WHERE key=?", (PRICE_APPROVALS,)).fetchone()
    if ap_ and ap_[0]:
        try:
            kept_ = json.loads(ap_[0]).get(str(cur_id)) if cur_id else None
        except (ValueError, AttributeError):
            kept_ = None
        con.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps({"1": kept_} if kept_ else {}), PRICE_APPROVALS))
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("VACUUM")
    return saved

SCHEMA_VERSION = 9
MIGRATIONS = {}

def _migrate_2(con):
    pass

MIGRATIONS[2] = _migrate_2

def _migrate_3(con):

    con.execute("""CREATE TABLE IF NOT EXISTS refund_credits (payment_id INTEGER PRIMARY KEY REFERENCES payments(id)
                   ON DELETE CASCADE, session_id INTEGER, amount REAL NOT NULL)""")

    def sig(sid):
        return json.dumps([[r_[0], round(r_[1], 2)] for r_ in
                           con.execute("SELECT label, amount FROM lines WHERE session_id=? ORDER BY id", (sid,))])
    for lid, sid, amount, fee_key in con.execute(
            "SELECT id, session_id, amount, fee_key FROM lines WHERE fee_key LIKE 'refund:%' ORDER BY id").fetchall():
        try:
            pid = int(fee_key.split(":", 1)[1])
        except ValueError:
            continue
        if not con.execute("SELECT 1 FROM payments WHERE id=?", (pid,)).fetchone():
            continue
        sent = con.execute("SELECT sig FROM invoice_sent WHERE session_id=?", (sid,)).fetchone()
        was_sent = bool(sent) and sent[0] == sig(sid)
        con.execute("INSERT OR IGNORE INTO refund_credits (payment_id, session_id, amount) VALUES (?,?,?)",
                    (pid, sid, round(-amount, 2)))
        con.execute("DELETE FROM lines WHERE id=?", (lid,))
        if was_sent:
            con.execute("UPDATE invoice_sent SET sig=? WHERE session_id=?", (sig(sid), sid))

MIGRATIONS[3] = _migrate_3

def _migrate_4(con):
    pass

MIGRATIONS[4] = _migrate_4

def _migrate_5(con):

    if "html" not in dbmod.columns(con, "bill_snapshots"):
        dbmod.add_column(con, "bill_snapshots", "html", "TEXT")

MIGRATIONS[5] = _migrate_5

def _migrate_6(con):
    pass

MIGRATIONS[6] = _migrate_6

def _migrate_7(con):
    pass

MIGRATIONS[7] = _migrate_7

def _migrate_8(con):
    pass

MIGRATIONS[8] = _migrate_8

def _migrate_9(con):
    pass

MIGRATIONS[9] = _migrate_9

class SchemaTooNew(RuntimeError):
    pass

class UpgradeBlocked(RuntimeError):
    pass

@app.errorhandler(UpgradeBlocked)
def _upgrade_blocked(e):
    return ("This version needs to update your database, and makes an encrypted backup first. That backup could not be "
            f"made ({e.args[0]}), so nothing was changed. Check that the vault storage is reachable, then sign in again. "
            "Or go back to the version you had.", 503, {"Content-Type": "text/plain; charset=utf-8"})

def _schema_version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]

def _upgrade_backup(con, was=""):

    has_master = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone()) and bool(
        con.execute("SELECT 1 FROM settings WHERE key='vault_master_pub' AND COALESCE(value,'') != ''").fetchone())
    if has_master:
        from vault import backup as vault_backup
        _tz_from_settings(con)
        try:
            names_ = vault_backup.sealed_backup(str(DB_PATH), str(FACES) if FACES.exists() else None, kind="upgrade")
        except Exception as e:
            import errtext
            raise UpgradeBlocked(errtext.named(e)) from e
        if was and names_:
            try:
                r_ = con.execute("SELECT value FROM settings WHERE key='upgrade_from'").fetchone()
                from_ = json.loads(r_[0]) if r_ and r_[0] else {}
                from_[names_[0]] = was
                kept_ = {r[0] for r in con.execute("SELECT name FROM vault_objects WHERE kind='upgrade' AND deleted IS NULL")}
                from_ = {k: v for k, v in from_.items() if k in kept_ or k == names_[0]}
                con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('upgrade_from', ?)", (json.dumps(from_),))
                con.commit()
            except (sqlite3.Error, ValueError, TypeError, AttributeError):
                pass
        _UPGRADE_BACKED.append(datetime.now().isoformat(timespec="minutes"))

_UPGRADE_BACKED = []
_MIGRATED = []

def _tz_from_settings(con):

    import time as _time
    try:
        r_ = con.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
        zone = ZoneInfo((r_[0] if r_ and r_[0] else "") or HOST_TZ or "UTC")
    except (sqlite3.Error, ZoneInfoNotFoundError, ValueError):
        return
    os.environ["TZ"] = zone.key
    _time.tzset()

def _note_update():

    import about as _ab
    now_v = _ab.version()
    was = setting("app_version")

    if was == now_v and not _MIGRATED and not _UPGRADE_BACKED:
        return
    x("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_version', ?)", (now_v,))
    if was or _UPGRADE_BACKED:
        _put_update_notice(now_v, was)

def _put_update_notice(now_v, was=""):
    x("INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
      (json.dumps(dict(version=now_v, backup=bool(_UPGRADE_BACKED), changed=bool(_MIGRATED),
                       backup_at=_UPGRADE_BACKED[-1] if _UPGRADE_BACKED and isinstance(_UPGRADE_BACKED[-1], str) else "",
                       was=was or "")),))

RENAMED_STEPS = {"Back up photos": ("Back up RAWs",)}

def _stage_rules_one(sid):

    s_ = q("""SELECT s.id, s.stage, s.shoot_date, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
              WHERE s.id=? AND s.stage NOT IN ('closed','lost')""", (sid,), one=True)
    if not s_:
        return None
    notes, n_dated, n_renamed = [], 0, 0
    for new_, olds_ in RENAMED_STEPS.items():
        open_ = q("SELECT id, stage FROM tasks WHERE session_id=? AND own=0 AND title=? AND done IS NULL", (sid, new_), one=True)
        if not open_:
            continue
        was_ = q(f"""SELECT title, done FROM tasks WHERE session_id=? AND stage=? AND done IS NOT NULL
                     AND title IN ({','.join('?' * len(olds_))}) ORDER BY done LIMIT 1""", (sid, open_["stage"], *olds_), one=True)
        if was_:
            x("UPDATE tasks SET done=?, outcome='done' WHERE id=?", (was_["done"], open_["id"]))
            notes.append(f"'{new_}' counts as done on {nice(was_['done'][:10])}, as '{was_['title']}' (the step was renamed).")
            n_renamed += 1
    rt_ = q("""SELECT id FROM tasks WHERE session_id=? AND own=0 AND title='Send retouched photos' AND done IS NOT NULL
               AND COALESCE(outcome, 'done')='done'""", (sid,), one=True)
    if rt_ and retouch_unsent(sid):
        st_ = retouch_state(q("SELECT id, retouch_picks FROM sessions WHERE id=?", (sid,), one=True))
        x("UPDATE tasks SET done=NULL, outcome=NULL WHERE id=?", (rt_["id"],))
        notes.append(f"'Send retouched photos' is open again: {st_['n_in']} of {st_['n']} retouched photos are in (an "
                     "earlier version marked it done when the download gallery was opened).")
    floors, base = stage_floors(sid), d(s_["shoot_date"])
    dated = []
    for t_ in q("""SELECT id, title, stage, offset_days, due, done FROM tasks WHERE session_id=? AND stage=? AND done IS NULL
                   AND offset_days IS NOT NULL ORDER BY sort, id""", (sid, s_["stage"])):
        due = _fair_due(base, t_, floors.get(t_["stage"]))
        if due != t_["due"]:
            x("UPDATE tasks SET due=? WHERE id=?", (due, t_["id"]))
            dated.append(f"{t_['title']} {nice(due) if due else 'no date'}" + (f" (was {nice(t_['due'])})" if t_["due"] else ""))
    if dated:
        notes.append("Step dates now count from the shoot: " + "; ".join(dated) + ".")
        n_dated += len(dated)
    before = q("SELECT COALESCE(MAX(id), 0) m FROM stage_log", one=True)["m"]
    try:
        moves = advance(sid, log=False)
    finally:
        x("UPDATE stage_log SET era='upgrade' WHERE session_id=? AND id > ? AND era IS NULL", (sid, before))
    for st_, why_ in moves:
        notes.append(f"Moved to {C.STAGE_LABELS[st_]}: {why_}.")
    if notes:
        audit("Updated to the 1.5 stage rules. " + " ".join(notes), sid=sid, who="Lumen Aureum")
    return dict(dated=n_dated, renamed=n_renamed, moved=bool(moves), household=s_["household"])

def stage_rules_upgrade():

    if setting(STAGE_RULES_MARK) == "":
        x("UPDATE stage_log SET era='1.4' WHERE era IS NULL")
        todo_ = [r_["id"] for r_ in q("SELECT id FROM sessions WHERE stage NOT IN ('closed','lost') ORDER BY id")]
        _put_setting(STAGE_RULES_TODO, json.dumps(todo_))
        _put_setting("stage_rules_from", today().isoformat())
        _put_setting(STAGE_RULES_MARK, str(q("SELECT COALESCE(MAX(id), 0) m FROM stage_log", one=True)["m"]))
    try:
        todo = [int(i_) for i_ in json.loads(setting(STAGE_RULES_TODO) or "[]")]
    except (TypeError, ValueError):
        todo = []
    if not todo:
        return None
    n_dated, n_renamed, moved, left = 0, 0, [], list(todo)
    for sid in todo:
        try:
            got = _stage_rules_one(sid)
        except Exception:
            app.logger.exception("the 1.5 stage rules for session %s (tried again at the next sign-in)", sid)
            try:
                db().rollback()
            except sqlite3.Error:
                pass
            continue
        left.remove(sid)
        _put_setting(STAGE_RULES_TODO, json.dumps(left))
        if got:
            n_dated += got["dated"]
            n_renamed += got["renamed"]
            if got["moved"] and got["household"] not in moved:
                moved.append(got["household"])
    words = []
    if n_dated:
        words.append(f"Step dates now count from the shoot: {n_dated} step{'' if n_dated == 1 else 's'} re-dated.")
    if n_renamed:
        words.append(f"{n_renamed} renamed step{'' if n_renamed == 1 else 's'} kept as done.")
    if moved:
        words.append("Moved on under the new stage rules: " + oxford(moved) + ". Each one's History says what changed.")
    out = dict(dated=n_dated, renamed=n_renamed, moved=moved, words=" ".join(words))
    upd_ = setting("update_notice")
    if words and not upd_:
        import about as _ab
        _put_update_notice(_ab.version(), setting("app_version") or "")
        upd_ = setting("update_notice")
    if upd_ and words:
        try:
            v_ = json.loads(upd_)
            v_["stages"] = out["words"]
            _put_setting("update_notice", json.dumps(v_))
        except ValueError:
            pass
    return out

def _last_app_version(con):

    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone():
            return None
        r_ = con.execute("SELECT value FROM settings WHERE key='app_version'").fetchone()
        return (r_[0] or None) if r_ else None
    except sqlite3.Error:
        return None

def _migrate(con, have, backed_up=False):

    todo = [n for n in sorted(MIGRATIONS) if have < n <= SCHEMA_VERSION]
    if not todo:
        return
    if not backed_up:
        _upgrade_backup(con, _last_app_version(con) or "")
    _MIGRATED.append(True)
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
    import about as _ab
    return render_template("schema_too_new.html", have=e.args[0], reads=SCHEMA_VERSION, version=_ab.version()), 409

SCHEMA_MARK = DB_PATH.parent / "schema-version"

def _schema_mark():

    try:
        v_, ino_ = SCHEMA_MARK.read_text().split()[:2]
        return int(v_) if int(ino_) == DB_PATH.stat().st_ino else 0
    except (OSError, ValueError):
        return 0

def _write_schema_mark(v):
    if _schema_mark() != v:
        try:
            tmp_ = SCHEMA_MARK.with_name(SCHEMA_MARK.name + ".tmp")
            tmp_.write_text(f"{int(v)} {DB_PATH.stat().st_ino}\n")
            os.replace(tmp_, SCHEMA_MARK)
        except OSError:
            app.logger.warning("could not write %s", SCHEMA_MARK)

@app.before_request
def _refuse_newer_data_early():
    if request.endpoint in ("login", "login_2fa", "unlock_master") and _schema_mark() > SCHEMA_VERSION:
        import about as _ab
        return render_template("schema_too_new.html", have=_schema_mark(), reads=SCHEMA_VERSION, version=_ab.version()), 409

def _fill_refund_release(con):

    for (sid_,) in con.execute("""SELECT DISTINCT session_id FROM payments WHERE kind='refund' AND voided IS NULL
                                  AND session_id IS NOT NULL""").fetchall():
        paid = con.execute("""SELECT COALESCE(SUM(amount),0) FROM payments WHERE session_id=? AND voided IS NULL
                              AND kind != 'tip'""", (sid_,)).fetchone()[0]
        con.execute("UPDATE sessions SET refund_release=? WHERE id=? AND refund_release IS NULL",
                    (after_refund_default(paid), sid_))

def init_db():
    con = connect()
    have = _schema_version(con)
    if have > SCHEMA_VERSION:
        con.close()
        _write_schema_mark(have)
        raise SchemaTooNew(have)
    had_snaps = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='doc_snapshots'").fetchone())
    had_docs = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone())
    backed_up = False
    import about as _ab
    was_ = _last_app_version(con)

    if had_docs and (was_ != _ab.version() or (have < SCHEMA_VERSION and any(n_ > have for n_ in MIGRATIONS))):
        _upgrade_backup(con, was_ or "")
        backed_up = True
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='write_offs'").fetchone() and \
            "id" not in dbmod.columns(con, "write_offs"):

        con.executescript("""ALTER TABLE write_offs RENAME TO write_offs_one;
CREATE TABLE write_offs (id INTEGER PRIMARY KEY, session_id INTEGER, amount REAL NOT NULL, at TEXT NOT NULL,
  why TEXT NOT NULL DEFAULT 'waived');
INSERT INTO write_offs (session_id, amount, at) SELECT session_id, amount, at FROM write_offs_one ORDER BY at;
DROP TABLE write_offs_one;""")
    con.executescript(SCHEMA)
    from vault import sync as vault_sync
    con.executescript(vault_sync.SCHEMA)
    if had_docs and not had_snaps:

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

    for tbl, col, decl in (("payments", "voided", "TEXT"), ("expenses", "rate", "REAL"), ("sessions", "duration_min", "INTEGER"),
                           ("sessions", "lost_reason", "TEXT DEFAULT ''"), ("quotes", "sent", "TEXT"), ("sessions", "hold_stage", "TEXT"), ("sessions", "portfolio_no", "INTEGER DEFAULT 0"), ("sessions", "delivery_method", "TEXT"), ("sessions", "gallery_codeword", "TEXT DEFAULT ''"),
                           ("lines", "taxable", "INTEGER DEFAULT 1"), ("sessions", "tax_exempt", "INTEGER DEFAULT 0"),
                           ("sessions", "tax_area", "TEXT"), ("sessions", "retouch_picks", "TEXT DEFAULT ''"),
                           ("sessions", "payer_event", "INTEGER"), ("events", "billing", "TEXT DEFAULT 'free'"),
                           ("events", "rights", "TEXT DEFAULT 'families'"), ("events", "rights_note", "TEXT DEFAULT ''"),
                           ("events", "payer_hid", "INTEGER"), ("events", "contact_hid", "INTEGER"), ("households", "company", "TEXT DEFAULT ''"), ("events", "family_price", "REAL"),

                           ("events", "attendees", "TEXT DEFAULT 'families'"), ("events", "org_price", "REAL"),
                           ("events", "org_per", "TEXT DEFAULT 'person'"),

                           ("events", "family_tax_in", "INTEGER DEFAULT 0"),
                           ("people", "birth_date", "TEXT"), ("sessions", "date_tentative", "INTEGER DEFAULT 0"),
                           ("sessions", "plan_of", "INTEGER"), ("sessions", "po_number", "TEXT DEFAULT ''"),
                           ("sessions", "lic_term", "TEXT DEFAULT ''"), ("sessions", "lic_territory", "TEXT DEFAULT ''"),
                           ("sessions", "lic_exclusive", "INTEGER DEFAULT 0"), ("sessions", "trade_copies", "INTEGER DEFAULT 0"), ("payments", "ref", "TEXT DEFAULT ''"),
                           ("people", "age_approx", "INTEGER DEFAULT 0"), ("sessions", "priced_on", "TEXT"),
                           ("sessions", "tax_rate", "REAL"), ("photo_files", "key_trail", "TEXT DEFAULT '[]'"),
                           ("bill_snapshots", "html", "TEXT"), ("payments", "tax", "REAL"), ("payments", "taxable", "REAL"),
                           ("sessions", "tax_area_locked", "TEXT"), ("sessions", "deposit_booked", "REAL"),

                           ("payments", "note", "TEXT DEFAULT ''"), ("write_offs", "note", "TEXT DEFAULT ''"),

                           ("households", "business", "INTEGER"),

                           ("documents", "covers", "TEXT DEFAULT ''"), ("documents", "covers_kind", "TEXT DEFAULT ''"),

                           ("people", "minor_box", "INTEGER"),

                           ("sessions", "pencil_until", "TEXT"),

                           ("payments", "corrects", "INTEGER"), ("expenses", "corrects", "INTEGER"),
                           ("expenses", "late_from", "TEXT"), ("expenses", "note", "TEXT DEFAULT ''"),

                           ("lines", "tax_key", "TEXT"), ("lines", "untaxed", "TEXT DEFAULT ''"),
                           ("payments", "tax_parts", "TEXT"), ("tax_adjustments", "tax_parts", "TEXT"),
                           ("books", "tax_parts", "TEXT"), ("households", "tax_exempt", "INTEGER DEFAULT 0"),
                           ("households", "tax_cert", "TEXT DEFAULT ''"),

                           ("sessions", "installments", "INTEGER"), ("sessions", "resched_settled", "INTEGER DEFAULT 0"),

                           ("sessions", "plan_dates", "TEXT")):
        if col not in dbmod.columns(con, tbl):
            dbmod.add_column(con, tbl, col, decl)
            if (tbl, col) == ("sessions", "tax_rate"):
                for sid_, lab in con.execute("""SELECT s.id, (SELECT label FROM lines l WHERE l.session_id=s.id AND l.kind='tax')
                                                FROM sessions s WHERE s.stage IN ('booked','shot','delivered','closed','lost')""").fetchall():
                    m_ = re.search(r"\(([0-9.]+)%\)", lab or "")
                    con.execute("UPDATE sessions SET tax_rate=? WHERE id=?", (float(m_.group(1)) if m_ else 0.0, sid_))
            if (tbl, col) == ("expenses", "rate"):
                cur_rate = con.execute("SELECT value FROM settings WHERE key='mileage_rate'").fetchone()
                con.execute("UPDATE expenses SET rate=? WHERE miles > 0", (float(cur_rate[0] or 0) if cur_rate else 0,))
    if "era" not in dbmod.columns(con, "stage_log"):

        dbmod.add_column(con, "stage_log", "era", "TEXT")
        mark_ = con.execute("SELECT value FROM settings WHERE key='stage_rules_mark'").fetchone()
        if mark_ and str(mark_[0] or "").isdigit():
            con.execute("UPDATE stage_log SET era='1.4' WHERE id <= ?", (int(mark_[0]),))

            from_ = con.execute("SELECT value FROM settings WHERE key='stage_rules_from'").fetchone()
            if from_ and from_[0]:
                con.execute("""UPDATE stage_log SET era='upgrade' WHERE id > ? AND substr(at, 1, 10) = ? AND session_id IN
                               (SELECT session_id FROM audit WHERE action LIKE 'Updated to the 1.5 stage rules.%Moved to %')""",
                            (int(mark_[0]), from_[0][:10]))
    if "refund_release" not in dbmod.columns(con, "sessions"):

        dbmod.add_column(con, "sessions", "refund_release", "TEXT")
        _fill_refund_release(con)
    con.executescript("""
CREATE TABLE IF NOT EXISTS quote_versions (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  at TEXT, schedule_id INTEGER, expires TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doc_numbers (session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, kind TEXT NOT NULL,
  number TEXT NOT NULL UNIQUE, issued TEXT, PRIMARY KEY (session_id, kind));
CREATE TABLE IF NOT EXISTS invoice_sent (session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  number TEXT NOT NULL, sig TEXT NOT NULL);""")
    if "number" not in dbmod.columns(con, "quote_versions"):

        dbmod.add_column(con, "quote_versions", "number", "TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS quote_versions_number ON quote_versions(number)")
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
CREATE INDEX IF NOT EXISTS write_offs_sid ON write_offs(session_id);
CREATE INDEX IF NOT EXISTS tax_adjustments_sid ON tax_adjustments(session_id);
CREATE INDEX IF NOT EXISTS charges_sid ON charges(session_id);
CREATE INDEX IF NOT EXISTS expenses_sid ON expenses(session_id);
CREATE INDEX IF NOT EXISTS log_sid ON log(session_id);
CREATE INDEX IF NOT EXISTS log_hh ON log(household_id, at);
CREATE INDEX IF NOT EXISTS step_closes_sid ON step_closes(session_id);
CREATE INDEX IF NOT EXISTS stage_log_sid ON stage_log(session_id);
CREATE INDEX IF NOT EXISTS sessions_hh ON sessions(household_id);
CREATE INDEX IF NOT EXISTS sessions_stage ON sessions(stage);
CREATE INDEX IF NOT EXISTS sessions_event ON sessions(event_id);
CREATE INDEX IF NOT EXISTS people_hh ON people(household_id);
CREATE INDEX IF NOT EXISTS audit_sid ON audit(session_id);""")

    try:
        con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS sessions_gallery_id ON sessions(gallery_id)
                       WHERE gallery_id IS NOT NULL AND gallery_id != ''""")
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        app.logger.warning("two sessions share a gallery ID: the unique index on sessions.gallery_id was not made")
    con.commit()
    if have == 0:
        dbmod.set_user_version(con, "main", 1)
        con.commit()
        have = 1
    _migrate(con, have, backed_up=backed_up)
    _write_schema_mark(_schema_version(con))
    con.close()

from vault import keys as vault_keys, web as vault_web
vault_web.register(app, connect, lambda what: audit(what, who="Owner"))
import auth as owner_auth

def audit_local(row_id, at, action=""):

    try:
        marks = json.loads(setting("audit_zones") or "[]")

        start = int(setting("audit_zone_start") or 0) if setting("audit_zone_now") else float("inf")
    except (ValueError, TypeError):
        marks, start = [], 0
    src = next((z_ for last_, z_ in marks if row_id <= last_), None)
    if src is None and row_id <= start and action.startswith("Created the owner account"):
        src = HOST_TZ or "UTC"
    if not src or src == TZ.key or not at or len(at) < 16:
        return at
    try:
        t_ = datetime.strptime(at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(src))
    except (ValueError, ZoneInfoNotFoundError):
        return at
    return t_.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")

def apply_settings():

    global TZ
    import time as _time
    from flask import has_app_context
    sql = "SELECT key, value FROM settings WHERE key IN ('timezone','home_lat','home_lon','rules')"
    if has_app_context():
        kv = dict((r[0], r[1]) for r in q(sql))
        if g.get("tx"):
            g.applied = True
    else:
        con = connect()
        try:
            kv = dict(con.execute(sql).fetchall())
        finally:
            con.close()
    try:
        TZ = ZoneInfo(kv.get("timezone") or HOST_TZ or "UTC")
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
    cfg_ = locknotice.read_config()
    if cfg_ and cfg_.get("tz") != TZ.key:
        locknotice.write_config(cfg_["server"], cfg_["topic"], cfg_.get("token", ""), TZ.key)

import threading as _threading
import atrest
import locknotice
import inboxnotice
_unlock_guard = _threading.Lock()
_unlocked_once = []

def _unlock_work():
    key = datakey.dk()
    for p_ in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm"), FACES):
        try:
            os.chmod(p_, 0o700 if p_ == FACES else 0o600)
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
    try:
        _merge_restored_side_files()
    except Exception:
        app.logger.exception("merging the restored deletions and number marks")
    reapply_deletions()
    try:
        with app.test_request_context():
            db()
            _fold_legacy_side_files()
            _seed_number_marks()
            _seed_id_marks()
            _freeze_reviewed_wording()
            _freeze_bill_pages()
            _fill_all_payment_taxes()
            _seed_charges()
            _seed_plan_dates()
            _note_update()
            _seal_missing_photo_keys()
            g.db.commit()
    except Exception:
        app.logger.exception("seeding document number marks / freezing reviewed wording")
    try:
        with app.test_request_context():
            db()
            stage_rules_upgrade()
            g.db.commit()
    except Exception:
        app.logger.exception("the 1.5 stage rules for existing sessions")
    try:
        with app.test_request_context():
            db()
            price_approval_upgrade()
            g.db.commit()
    except Exception:
        app.logger.exception("the 1.5 price approval for existing prices")
    if n_faces:
        app.logger.info("encrypted %d face photos", n_faces)
    vault_web.PATHS.update(db=DB_PATH, faces=FACES)
    for fn in AFTER_UNLOCK_HOOKS:
        try:
            fn()
        except Exception:
            app.logger.exception("after unlock")
    if not os.environ.get("CRM_TESTING"):
        vault_web.start_scheduler(app, connect, DB_PATH, FACES)
        threading.Thread(target=_mirror_loop, name="photo-mirror-loop", daemon=True).start()
        threading.Thread(target=_advance_loop, name="advance-loop", daemon=True).start()

def on_unlock():

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
        try:
            _after_locked_start()
        except Exception:
            app.logger.exception("after a locked start")

NTFY_KEYS = ("ntfy_server", "ntfy_topic", "ntfy_token")

def ntfy_settings():

    v = {k_[5:]: setting(k_) for k_ in NTFY_KEYS}
    return v if v["server"] and v["topic"] else None

app.jinja_env.globals["ntfy_public"] = locknotice.public_server
app.jinja_env.globals["inbox_notice_on"] = lambda: setting(inboxnotice.KEY_ON) == "1"
app.jinja_env.globals["backup_notice_on"] = lambda: setting(inboxnotice.KEY_BACKUP_ON) == "1"
app.jinja_env.globals["storage_low_pct"] = inboxnotice.STORAGE_LOW_PCT
app.jinja_env.globals["status_notice_on"] = lambda kind: inboxnotice.status_on(db(), kind)
app.config["credential_changed"] = lambda: inboxnotice.credential_changed(db())

def _with_app(fn):

    with app.app_context():
        return fn()

def ntfy_mirror():

    v = ntfy_settings()
    if v:
        locknotice.write_config(v["server"], v["topic"], v["token"], TZ.key)
    else:
        locknotice.clear_config()

STATUS_SWITCHES = {
    "storage": ("storage", f"You'll get a notice when your studio's disk has less than {inboxnotice.STORAGE_LOW_PCT}% "
                           "free: at most one a day while it lasts."),
    "closing": ("gallery closing", "You'll get a notice in the morning when a gallery closes the next day: never "
                                   "which, or how many."),
    "update": ("update", "You'll get a notice when your studio is updated to a new version."),
    "served": ("gallery page", "You'll get a notice when a gallery page on your web host is not the one your studio "
                               "put there: at most one a day while it lasts."),
    "cred": ("sign-in change", "You'll get a notice when your password, master key or recovery codes change."),
}

def _public_ntfy_note(server):

    return (" This is the public ntfy server, where anyone who learns the topic can read the notices: a private server "
            "of your own is safer." if locknotice.public_server(server) else "")

@app.post("/security/notice")
def security_notice():

    f, act = request.form, request.form.get("act")
    if act == "save":
        server, topic, token = (f.get("server") or "").strip().rstrip("/"), (f.get("topic") or "").strip(), (f.get("token") or "").strip()
        why = locknotice.check(server, topic)
        if not why and f.get("replace") and ntfy_settings():

            sent_ = locknotice.send(dict(server=server, topic=topic, token=token), locknotice.TEST_TEXT)
            if sent_:
                why = f"The test notice couldn't be sent to the new server ({sent_}), so the one in use now stays."
        if why:
            session["notice"] = why
            g.notice_refused = True
            if f.get("replace"):
                return redirect(url_for("settings_view", tab="notifications", replace="ntfy") + "#notice")
        else:
            if f.get("replace"):
                mark_ = app.config.get("conn_mark")
                if mark_:
                    mark_("ntfy", True)
            had = ntfy_settings()
            _put_setting("ntfy_server", server)
            _put_setting("ntfy_topic", topic)
            if token or f.get("drop_token") or f.get("replace"):
                _put_setting("ntfy_token", token)
            ntfy_mirror()
            audit("Changed the locked-studio notice" if had else "Set up the locked-studio notice")
            session["notice"] = "Saved. Send a test notice to check it reaches your phone." + _public_ntfy_note(server)
    elif act == "test":

        v = ntfy_settings()
        server, topic = (f.get("server") or "").strip().rstrip("/"), (f.get("topic") or "").strip()
        if server or topic:
            why = locknotice.check(server, topic)
            if why:
                session["notice"] = why
                g.notice_refused = True
                return redirect(url_for("settings_view", tab="notifications") + "#notice")
            token = "" if f.get("drop_token") else ((f.get("token") or "").strip() or (v or {}).get("token", ""))
            v = dict(server=server, topic=topic, token=token)
        why = locknotice.send(v, locknotice.TEST_TEXT) if v else "set up a server and topic first"
        if v and not (server or topic) and app.config.get("conn_mark"):
            app.config["conn_mark"]("ntfy", not why, why or "")
        if why:
            app.logger.warning("The test notice could not be sent: %s", why)
            if server or topic:
                session["ntfy_typed"] = dict(server=server, topic=topic)
            session["notice"] = f"The test notice couldn't be sent: {why}."
        elif (server or topic) and ntfy_settings() != v:

            mark_ = app.config.get("conn_mark")
            if mark_:
                mark_("ntfy", True)
            had = ntfy_settings()
            _put_setting("ntfy_server", v["server"])
            _put_setting("ntfy_topic", v["topic"])
            _put_setting("ntfy_token", v.get("token", ""))
            ntfy_mirror()
            audit("Changed the locked-studio notice" if had else "Set up the locked-studio notice")
            session["notice"] = ("Test notice sent, and these settings are saved. If it doesn't arrive, check the topic in "
                                 "the ntfy app." + _public_ntfy_note(v["server"]))
        else:
            session["notice"] = "Test notice sent. If it doesn't arrive, check the topic in the ntfy app on your phone."
    elif act == "inbox":

        on_ = bool(f.get("inbox_on")) and bool(ntfy_settings())
        if on_ != (setting(inboxnotice.KEY_ON) == "1"):
            _put_setting(inboxnotice.KEY_ON, "1" if on_ else "0")
            x("DELETE FROM settings WHERE key=?", (inboxnotice.KEY_STATE,))
            audit("Turned on the studio inbox notice" if on_ else "Turned off the studio inbox notice")
        session["notice"] = ("You'll get a notice when something new comes in: never what or how many, at most one "
                             f"every {inboxnotice.QUIET.seconds // 60} minutes." if on_ else "The studio inbox notice is off.")
    elif act == "backup":

        on_ = bool(f.get("backup_on")) and bool(ntfy_settings())
        if on_ != (setting(inboxnotice.KEY_BACKUP_ON) == "1"):
            _put_setting(inboxnotice.KEY_BACKUP_ON, "1" if on_ else "0")
            x("DELETE FROM settings WHERE key=?", (inboxnotice.KEY_BACKUP_STATE,))
            audit("Turned on the backup notice" if on_ else "Turned off the backup notice")
        session["notice"] = ("You'll get a notice when a backup fails or none has worked for over a day: at most one a "
                             "day while it lasts." if on_ else "The backup notice is off.")
    elif act in STATUS_SWITCHES:

        name_, said_ = STATUS_SWITCHES[act]
        on_ = bool(f.get(act + "_on")) and bool(ntfy_settings())
        key_on_, key_state_ = inboxnotice.STATUS[act][:2]
        if on_ != inboxnotice.status_on(db(), act):
            _put_setting(key_on_, "1" if on_ else "0")
            x("DELETE FROM settings WHERE key=?", (key_state_,))
            seed_ = (inboxnotice.seeded("update", update_installed()) if on_ and act == "update" else
                     inboxnotice.seeded("cred", setting(inboxnotice.KEY_CRED_AT)) if on_ and act == "cred" else None)
            if seed_:
                _put_setting(key_state_, seed_)
            audit(f"Turned on the {name_} notice" if on_ else f"Turned off the {name_} notice")
        session["notice"] = said_ if on_ else f"The {name_} notice is off."
    elif act == "remove":
        for k_ in NTFY_KEYS + (inboxnotice.KEY_ON, inboxnotice.KEY_STATE, inboxnotice.KEY_CRED_AT) + tuple(
                k2_ for v_ in inboxnotice.STATUS.values() for k2_ in v_[:2] if k2_):
            x("DELETE FROM settings WHERE key=?", (k_,))
        ntfy_mirror()
        audit("Turned off the locked-studio notice")
        session["notice"] = "The locked-studio notice is off, and its plain file is deleted."
    return redirect(url_for("settings_view", tab="notifications") + "#notice")

import calfeed

FEED_THROTTLE = owner_auth.Throttle()

app.config["feed_throttle"] = FEED_THROTTLE

def calendar_feed_entries():

    since = (today() - timedelta(days=calfeed.PAST_DAYS)).isoformat()
    out, events = [], {}
    for s in q("""SELECT s.id, s.type, s.stage, s.shoot_date, s.start_time, s.event_id, s.pencil_until, s.date_tentative
                  FROM sessions s JOIN households h ON h.id=s.household_id LEFT JOIN events e ON e.id=s.event_id
                  WHERE s.shoot_date >= ? AND s.stage != 'lost' AND COALESCE(h.demo, 0) = 0 AND COALESCE(e.demo, 0) = 0
                  AND NOT (s.event_id IS NULL AND s.payer_event IS NOT NULL)
                  ORDER BY s.shoot_date, s.start_time, s.id""", (since,)):
        lead_ = s["stage"] in LEAD_STAGES
        if lead_ and not penciled(s):
            continue
        tentative_ = lead_ or bool(s["date_tentative"])
        label_ = C.SESSION_TYPES.get(s["type"], "Session") + (" (penciled)" if lead_ else
                                                               " (date not final)" if s["date_tentative"] else "")
        e_ = calfeed.entry(("s", s["id"]), label_, s["shoot_date"], s["start_time"], session_minutes(s["id"], s["type"]),
                           TZ, tentative=tentative_)
        if not s["event_id"]:
            out.append(e_)
            continue
        k_ = ("e", s["event_id"], s["shoot_date"][:10])
        had_ = events.get(k_)
        if not had_:
            events[k_] = dict(e_, key=k_)
        elif isinstance(had_["start"], datetime) and isinstance(e_["start"], datetime):
            had_["start"], had_["end"] = min(had_["start"], e_["start"]), max(had_["end"], e_["end"])
            had_["tentative"] = had_["tentative"] and e_["tentative"]
        elif isinstance(e_["start"], datetime):
            events[k_] = dict(e_, key=k_)
    return out + list(events.values())

@app.get("/calendar-feed/<token>.ics", endpoint="calendar_feed")
def calendar_feed(token):

    ip_ = request.remote_addr
    plain_ = {"Content-Type": "text/plain; charset=utf-8"}
    if FEED_THROTTLE.ip_blocked(ip_):
        return "Too many wrong tries from here. Try again later.\n", 429, dict(plain_, **{"Retry-After": "900"})
    if not datakey.is_unlocked():

        return ("The studio is locked after a restart. The calendar fills in again once you sign in.\n", 503,
                dict(plain_, **{"Retry-After": str(calfeed.RETRY_LOCKED)}))
    try:
        if not calfeed.token_matches(token, setting(calfeed.KEY_TOKEN)):
            FEED_THROTTLE.fail(ip_, "calendar link")
            return "Not found.\n", 404, plain_
        body_ = calfeed.build(calendar_feed_entries(), setting(calfeed.KEY_TOKEN), studio=setting("studio_name"))
    except Exception as e_:
        app.logger.error("The calendar link could not be answered: %s", type(e_).__name__)
        return "Something went wrong.\n", 500, plain_
    return body_, 200, {"Content-Type": "text/calendar; charset=utf-8",
                        "Content-Disposition": 'inline; filename="studio-sessions.ics"'}

def calendar_feed_url(scheme="https"):

    tok_ = setting(calfeed.KEY_TOKEN)
    if not tok_:
        return ""
    return f"{scheme}://{request.host}" + url_for("calendar_feed", token=tok_)

app.jinja_env.globals["calendar_feed_url"] = calendar_feed_url

@app.post("/settings/calendar-link", endpoint="calendar_link_settings")
def calendar_link_settings():

    act_, had_ = request.form.get("act"), bool(setting(calfeed.KEY_TOKEN))
    if act_ in ("on", "new"):
        _put_setting(calfeed.KEY_TOKEN, calfeed.new_token())
        if act_ == "new" and had_:
            audit("Made a new calendar link (the old one stopped working)")
            session["notice"] = "New link made. The old one stopped working: subscribe again on each phone."
        else:
            audit("Turned on the calendar link")
            session["notice"] = "The calendar link is on. Subscribe to it on your phone."
    elif act_ == "off":
        if had_:
            x("DELETE FROM settings WHERE key=?", (calfeed.KEY_TOKEN,))
            audit("Turned off the calendar link")
        session["notice"] = "The calendar link is off. Phones subscribed to it get nothing new."
    return redirect(url_for("settings_view", tab="calendar") + "#calendar-link")

def _after_locked_start():

    datakey.forget_legacy_auto()
    since = locknotice.unlocked()
    with app.test_request_context():
        db()
        ntfy_mirror()
        if since is not None:
            if datetime.now(since.tzinfo) - since >= locknotice.WAITED_MIN:
                _put_setting("locked_waited", since.isoformat())
            if not ntfy_settings() and not setting("ntfy_suggested"):
                _put_setting("ntfy_suggested", "1")
                _put_setting("ntfy_suggest", "1")
        g.db.commit()

owner_auth.register(app, connect, _allowed, lambda what: audit(what, who="Owner"), on_unlock)
app.config["idle_seconds"] = lambda: idle_hours() * 3600

def _restart_after_restore():

    datakey.lock()
    _unlocked_once.clear()

def _end_all_sessions():
    owner_auth.SESSIONS.clear()
    owner_auth.LAST_SEEN.clear()

import restore as _restore
_restore.register(app, connect, lambda what: audit(what, who="Owner"), schema_now=SCHEMA_VERSION,
                  soft_restart=_restart_after_restore, end_sessions=_end_all_sessions)

@app.errorhandler(datakey.Locked)
def _locked(_e):

    if request.method == "GET":
        return redirect(url_for("login"))
    return "Locked: sign in again.", 503

CSP = ("default-src 'self'; script-src 'self'{extra}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

def csp_nonce():

    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(18)
    return g.csp_nonce

app.jinja_env.globals["csp_nonce"] = csp_nonce

@app.after_request
def _no_store(resp):

    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("Content-Security-Policy",
                            CSP.format(extra=f" 'nonce-{g.csp_nonce}'" if "csp_nonce" in g else ""))
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.scheme == "https" and os.environ.get("CRM_HSTS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp

import rotate as _rotate
if _rotate.pending():
    raise SystemExit("A data key rotation was interrupted. Finish it first: python -m vault.cli rotate")
_run_lock = _rotate.hold_running()
__import__("atexit").register(_run_lock.close)
import about as _about
_about.register(app)
import setup as first_run
first_run.register(app, connect, apply_settings, lambda what: audit(what, who="Owner"), ALLOW,
                   after_rules=_sync_after_rules)
LOADED = load_plugins()
if not os.environ.get("CRM_TESTING"):
    netsec.banner(unclaimed=not datakey.exists(),
                  plugins=[(p_["name"], p_["built_in"], p_["folder"]) for p_ in PLUGINS_LOADED], plugin_errors=PLUGIN_ERRORS)
if datakey.exists():
    locknotice.started_locked()
    if not os.environ.get("CRM_TESTING"):
        locknotice.start_loop(datakey.is_unlocked, app.logger)
if not os.environ.get("CRM_TESTING"):
    inboxnotice.start_loop(datakey.is_unlocked, connect, lambda: _with_app(ntfy_settings), app.logger,
                           backup_problem=lambda: _with_app(backup_problem),
                           problems={k_: (lambda fn_=fn_: _with_app(fn_)) for k_, fn_ in STATUS_PROBLEMS.items()})

if __name__ == "__main__":
    raise SystemExit("Start Lumen Aureum with: python serve.py")
