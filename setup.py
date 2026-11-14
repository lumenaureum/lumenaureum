"""First-run setup: the steps after the owner account (auth.py is step 1). Until setup is finished, every signed-in page
sends the owner here. Each step saves as it goes, so leaving halfway loses nothing; after setup the same pages open
from Settings for changes.

What is saved where:
  settings table   business details, time zone, home location, tax mode, gallery address, rules (JSON, see config.apply)
  storage.json     where the vault and galleries live (vault/web.py reads it); written 0600 because it can hold keys
"""
import gzip
import json
import os
import re
import secrets
import time
import zoneinfo
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

import config as C
from vault import backends as B, web as VW

ROOT = Path(__file__).resolve().parent
STEPS = [  # (slug, title, required)
    ("business", "Your business", True),
    ("storage", "Backups", True),
    ("shoot", "What you shoot", False),
    ("money", "Payments and terms", False),
    ("tax", "Sales tax", False),
    ("documents", "Documents", False),
    ("galleries", "Online galleries", False),
    ("network", "Who can reach this", False),
]
SLUGS = [s for s, _, _ in STEPS]
OPEN_DURING_SETUP = {"static", "logout", "healthz", "setup_step", "setup_test", "security", "settings_import", "settings_import_gallery", "settings_export", "about"}
PAYMENT_CHOICES = ["cash", "check", "card", "bank transfer", "payment app"]
_ZIPS = None


def zip_latlon(z):
    global _ZIPS
    if _ZIPS is None:
        with gzip.open(ROOT / "zip_latlon.json.gz", "rt") as f:
            _ZIPS = json.load(f)["zips"]
    return _ZIPS.get((z or "").strip()[:5])


def timezones():
    zs = sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/")))
    us = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
          "America/Anchorage", "Pacific/Honolulu"]
    return [z for z in us if z in zs] + [z for z in zs if z not in us]


def host_tz():
    """The machine's or container's own TZ, as it was when the app started (apply_settings overwrites the variable),
    when it names a real zone; else ''."""
    import sys
    tz = getattr(sys.modules.get("app") or sys.modules.get("__main__"), "HOST_TZ", "")
    return tz if tz in zoneinfo.available_timezones() else ""


def _data_and_program():
    data = os.path.dirname(os.path.realpath(os.environ.get("CRM_DB", str(ROOT / "data" / "crm.db"))))
    return data, os.path.realpath(str(ROOT))


def suggested_vault():
    """A backup folder the storage step can offer that its own checks accept: /vault when the container mounts one,
    else a vault folder beside the database when that is outside the program, else one beside the program folder."""
    import settings_io
    data, prog = _data_and_program()
    for p_ in ("/vault", os.path.join(data, "vault"), os.path.join(os.path.dirname(prog), "lumen-aureum-vault")):
        if (p_ != "/vault" or os.path.isdir(p_)) and not settings_io._inside_private(p_):
            return p_
    return ""


def inside_private_message(key):
    """Why a path inside the data or program folder is refused, and what would work instead."""
    data, prog = _data_and_program()
    if key != "path":
        return (f"That file is inside Lumen Aureum's own data folder ({data}) or program folder ({prog}). Keep keys"
                " somewhere else, for example a folder mounted only for them.")
    ex = suggested_vault()
    return (f"That folder is inside Lumen Aureum's own data folder ({data}) or program folder ({prog}), where a backup"
            " would be lost along with the app. Choose a folder outside both, ideally on another disk"
            + (f", for example {ex}." if ex else "."))


def same_disk(path, other):
    """True when two folders are on the same filesystem (a backup there dies with the database)."""
    try:
        return os.stat(path).st_dev == os.stat(other).st_dev
    except OSError:
        return False


def backups_share_disk(cfg=None):
    """The vault is a folder on the database's own disk and there is no second location."""
    cfg = cfg if cfg is not None else VW.config()
    prim = cfg.get("primary") or {}
    return prim.get("type") == "local" and same_disk(prim.get("path", ""), str(VW.CFG_PATH.parent)) and not cfg.get("secondary")


def write_storage(cfg):
    VW.write_config(cfg)          # encrypted under the data key (it can hold SFTP/S3 credentials)


def backend_from_form(f, prefix, old=None):
    """A storage choice from the form. Secrets left blank keep what was saved before (they are never shown back)."""
    old = old or {}
    t = f.get(prefix + "type", "")
    if t == "local":
        return {"type": "local", "path": f.get(prefix + "path", "").strip()}
    if t == "sftp":
        c = {"type": "sftp", "host": f.get(prefix + "host", "").strip(), "user": f.get(prefix + "user", "").strip(),
             "key_file": f.get(prefix + "key_file", "").strip(), "root": f.get(prefix + "root", "").strip()}
        port = f.get(prefix + "port", "").strip()
        if port.isdigit() and port != "22":
            c["port"] = int(port)
        if f.get(prefix + "keymode", "app") == "app":     # the app's own key (no file to place in the container)
            c["key_file"] = ""
            c["private_key"] = app_ssh_key()[0]
        same_server = old.get("host") == c["host"] and int(old.get("port", 22)) == int(c.get("port", 22))
        pin = (f.get(prefix + "host_key") or "").strip() if f.get(prefix + "trust") else ""
        if pin or (same_server and old.get("host_key")):
            c["host_key"] = pin or old["host_key"]        # trusted once, by the owner, after checking the fingerprint
        return c
    if t == "s3":
        c = {"type": "s3", "bucket": f.get(prefix + "bucket", "").strip(), "endpoint": f.get(prefix + "endpoint", "").strip() or None,
             "region": f.get(prefix + "region", "").strip() or None, "access_key": f.get(prefix + "access_key", "").strip(),
             "secret_key": f.get(prefix + "secret_key", "").strip() or old.get("secret_key", ""),
             "prefix": f.get(prefix + "s3prefix", "").strip()}
        return {k: v for k, v in c.items() if v is not None}
    return None


def app_ssh_key():
    """This install's own SSH key pair for SFTP backups and galleries, made once and kept in the encrypted database.
    Returns (private, public line)."""
    priv = _get("ssh_app_key")
    if not priv:
        priv, _ = B.new_ssh_key()
        _put(ssh_app_key=priv)
    return priv, B.public_line(priv)


_DB = {}                       # register() keeps the app's connect() here


def _get(key, default=""):
    con = _DB["connect"]()
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default
    finally:
        con.close()


def _put(**kv):
    con = _DB["connect"]()
    try:
        for k, v in kv.items():
            con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        con.commit()
    finally:
        con.close()


def test_backend(cfg, init_local=False, keep=True):
    """Write, read back and delete a small file. Returns '' when it worked, else a plain-language reason.
    keep=False (the Test button, before anything is saved): a folder or marker made only for the test is removed again."""
    made = []
    try:
        return _test_backend(cfg, init_local, made)
    finally:
        if not keep:
            for path in reversed(made):         # the marker first, then the folder (only if the test left it empty)
                try:
                    (os.rmdir if os.path.isdir(path) else os.remove)(path)
                except OSError:
                    pass


def _test_backend(cfg, init_local, made):
    """test_backend's body; made collects the folder and marker it created."""
    if not cfg:
        return "Nothing chosen."
    import settings_io
    for k in ("path", "key_file", "known_hosts"):
        if cfg.get(k) and settings_io._inside_private(cfg[k]):
            return inside_private_message(k)
    ep = (cfg.get("endpoint") or "") if cfg.get("type") == "s3" else ""
    if ep and not (ep.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", ep)):
        return "The S3 endpoint must start with https:// (credentials and files would otherwise cross the network unencrypted)."
    try:
        if cfg["type"] == "local":
            p = cfg.get("path", "")
            if not p or not os.path.isabs(p):
                return "Give the full path of a folder, starting with /."
            if init_local and not os.path.exists(p) and os.path.isdir(os.path.dirname(p.rstrip("/"))):
                os.mkdir(p, 0o700)            # a new vault folder inside one that exists (never a whole missing path)
                made.append(p)
            if init_local and os.path.isdir(p) and not os.path.isfile(os.path.join(p, B.LocalBackend.MARKER)):
                if any(os.scandir(p)):
                    return "That folder already has files in it. Pick an empty folder, or one used by Lumen Aureum before."
                B.LocalBackend.init_root(p)
                made.append(os.path.join(p, B.LocalBackend.MARKER))
            elif not os.path.isdir(p):
                return "That folder doesn't exist. Create it (and mount the share, if it is one) and try again."
        be = B.from_config(cfg)
        name = f"setup-test/{secrets.token_hex(6)}.txt"
        body = b"Lumen Aureum storage test\n"
        import io
        be.put_stream(name, io.BytesIO(body), "text/plain")
        out = io.BytesIO()
        be.get_stream(name, out)
        be.delete(name)
        try:
            be.remove_dir("setup-test")
        except Exception:
            pass
        return "" if out.getvalue() == body else "The file read back differently from what was written."
    except Exception as e:  # shown to the owner: plain words for the usual cases, else the backend's own words
        return plain_error(e, cfg)


def plain_error(e, cfg):
    text = str(e)
    if "is not initialised" in text or "is not initialized" in text:
        return ("That folder hasn't been set up for Lumen Aureum yet. If it is a new empty folder, tick \"New empty folder:"
                " set it up\". If it should already be set up, check that the share is mounted.")
    if isinstance(e, FileNotFoundError) and "known_hosts" in text:
        return f"This machine has never connected to {cfg.get('host', 'that server')}. Add it to known_hosts first."
    if isinstance(e, FileNotFoundError) and cfg.get("key_file") and cfg["key_file"] in text:
        return "The private key file isn't there."
    if "not found in known_hosts" in text or "known_hosts" in text.lower() and "server" in text.lower():
        return f"{cfg.get('host', 'That server')} isn't in known_hosts (or its key changed)."
    if type(e).__name__ == "AuthenticationException":
        return "The server refused the key."
    if isinstance(e, (TimeoutError, ConnectionRefusedError)) or type(e).__name__ in ("NoValidConnectionsError", "gaierror"):
        return "Can't reach that server."
    return f"{type(e).__name__}: {e}"


def app_hooks():
    import sys
    return sys.modules["app"].hooks if "app" in sys.modules else sys.modules["__main__"].hooks


def register(app, connect, apply_settings, audit, allow_nets):
    _DB["connect"] = connect
    def get(key, default=""):
        con = connect()
        try:
            r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        return r[0] if r and r[0] is not None else default

    def put(**kv):
        con = connect()
        try:
            for k, v in kv.items():
                con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (k, "" if v is None else str(v)))
            con.commit()
        finally:
            con.close()

    def rules():
        try:
            return json.loads(get("rules", "{}"))
        except ValueError:
            return {}

    def save_rules(**kv):
        r = rules()
        if "TYPES" in kv and "LICENSE_TYPES" not in kv and "LICENSE_TYPES" in r:
            had = {t_.get("key") for t_ in C.TYPES}            # a type added later keeps the copyright with the
            new_ = [t_["key"] for t_ in kv["TYPES"] if t_.get("key") not in had]   # photographer, like every type
            if new_:
                kv = dict(kv, LICENSE_TYPES=list(r["LICENSE_TYPES"]) + [k_ for k_ in new_ if k_ not in r["LICENSE_TYPES"]])
        r.update(kv)
        put(rules=json.dumps(r))
        apply_settings()

    def done():
        try:
            return set(json.loads(get("setup_done", "[]")))
        except ValueError:
            return set()

    def mark(step):
        put(setup_done=json.dumps(sorted(done() | {step})))

    def complete():
        return get("setup_complete") == "1"

    def first_open():
        d = done()
        return next((s for s in SLUGS if s not in d), SLUGS[-1])

    def gate():
        """After the owner account: until setup is finished, signed-in pages go to the next setup step."""
        ep = request.endpoint or ""
        if ep in OPEN_DURING_SETUP or ep.startswith("setup_owner") or ep.startswith("login") or ep == "unlock_master":
            return None
        import datakey
        if not datakey.is_unlocked() or complete():
            return None        # locked: the sign-in gate (which runs first) has it
        from flask import session as fs
        if not fs.get("uid"):
            return None        # the login gate (which runs first) has already sent them to log in
        return redirect(url_for("setup_step", step=first_open()))

    app.before_request_funcs.setdefault(None, []).insert(1, gate)
    app.config.setdefault("quiet_endpoints", set()).add("setup_test")
    app.config.setdefault("ep_words", {})["setup_test"] = "Tested a storage connection"

    def nxt(step):
        i = SLUGS.index(step)
        if not complete() and i + 1 < len(SLUGS):
            return redirect(url_for("setup_step", step=SLUGS[i + 1]))
        return redirect(url_for("setup_step", step=step, saved=1) if complete() else url_for("setup_step", step=step))

    @app.route("/setup/<step>", methods=["GET", "POST"], endpoint="setup_step")
    def setup_step(step):
        if step not in SLUGS:
            abort(404)
        err = None
        f = request.form
        if request.method == "POST":
            skip = f.get("skip") and not dict((s, r) for s, _, r in STEPS)[step]
            if skip and (step != SLUGS[-1] or complete()):
                mark(step)
                return nxt(step)
            err = None if skip else save(step, f)    # "Use the defaults" on the last step finishes, like Finish
            if not err:
                mark(step)
                if step == SLUGS[-1] and not complete():
                    missing = [t for s, t, r in STEPS if r and s not in done()]
                    if missing:
                        err = "Still needed before you can start: " + ", ".join(missing) + "."
                    else:
                        put(setup_complete="1")
                        audit("Setup finished")
                        return redirect(url_for("home"))
                else:
                    return nxt(step)
        return render_template("setup.html", step=step, steps=STEPS, done=done(),
                               complete=complete(), err=err, required=dict((s, r) for s, _, r in STEPS)[step], saved=request.args.get("saved"), **context(step))

    @app.post("/setup/test/<which>", endpoint="setup_test")
    def setup_test(which):
        """Connection test from the storage and gallery steps, before anything is saved."""
        cfg = VW.config()
        old = cfg.get({"primary": "primary", "secondary": "secondary", "gallery": "gallery_host"}.get(which, ""), {})
        c = backend_from_form(request.form, which + "_", old)
        if c and c.get("type") == "sftp" and c.get("host") and not c.get("host_key") and not os.path.isfile(
                os.path.expanduser(c.get("known_hosts", "~/.ssh/known_hosts"))):
            try:                                  # first contact: show the server's fingerprint for the owner to check
                line, fp = B.fetch_host_key(c["host"], c.get("port", 22))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "message": plain_error(e, c)}
            return {"ok": False, "host_key": line, "fingerprint": fp,
                    "message": f"First connection to {c['host']}. Its key fingerprint is {fp}. Check it matches the one your "
                               "host shows, tick Trust this server, and test again."}
        msg = test_backend(c, init_local=which in ("primary", "secondary", "gallery") and request.form.get(which + "_init") == "1",
                           keep=False)
        if not msg and which == "primary" and c.get("type") == "local" and same_disk(c.get("path", ""), str(VW.CFG_PATH.parent)):
            return {"ok": False, "message": "Works, but it's on the same disk as your data: one failure loses both."}
        return {"ok": not msg, "message": msg or "Connected: a test file was written, read back and removed."}

    # ------------------------------------------------------------ "finish setting up" on Today

    def finish_items():
        """What is still open after setup: each line goes away by itself once done. [] when hidden or nothing left."""
        if not complete() or get("finish_hidden") == "1":
            return []
        con = connect()
        try:
            one = lambda sql: con.execute(sql).fetchone()[0]
            clients = one("SELECT COUNT(*) FROM households WHERE COALESCE(demo, 0)=0")
            versions = one("SELECT COUNT(*) FROM fee_schedules")
            starter = one("SELECT note FROM fee_schedules ORDER BY id LIMIT 1") if versions else ""
        finally:
            con.close()
        cfg = VW.config()
        items = []
        if not get("timezone"):
            items.append(("Set your time zone, so evening work gets today's date", url_for("settings_view") + "#tz"))
        if get("atrest_upgraded"):
            items.append(("Delete the unencrypted snapshots and copies made before the upgrade", url_for("security")))
        if get("doc_reviews") in (None, "", "{}"):
            items.append(("Read your documents and mark each one reviewed: until then they print as SAMPLE",
                          url_for("settings_view", tab="documents")))
        if versions <= 1 and starter == "Starting estimates":
            items.append(("Set your own prices: the starter prices are placeholders", url_for("settings_view") + "#fees"))
        if not get("business_email"):
            items.append(("Add your business email (clients' gallery picks are sent there)", url_for("setup_step", step="business")))
        if backups_share_disk(cfg):
            items.append(("Your backups are on the same disk as your data: move the vault or add a second location",
                          url_for("setup_step", step="storage")))
        elif not cfg.get("secondary"):
            items.append(("Add a second backup location, ideally outside your house", url_for("setup_step", step="storage")))
        if not cfg.get("gallery_host"):
            items.append(("Set up online galleries", url_for("setup_step", step="galleries")))
        if not clients:
            items.append(("Add your first client", url_for("contact_new")))
        return items

    app.config["finish_items"] = finish_items

    # ------------------------------------------------------------ time zone, for installs set up without one

    def tz_prompt():
        """For Settings: shown once, until a time zone is saved. None when there is one (or setup isn't finished)."""
        if not complete() or get("timezone"):
            return None
        import sys
        cur = getattr(sys.modules.get("app") or sys.modules.get("__main__"), "TZ", None)
        return dict(zones=timezones(), host=host_tz(), now=getattr(cur, "key", "UTC"))

    app.jinja_env.globals["tz_prompt"] = tz_prompt

    @app.post("/settings/timezone", endpoint="settings_timezone")
    def settings_timezone():
        """Save the time zone from the Settings prompt. Only dates made from now on follow it: documents already
        issued keep the dates stored on them."""
        from flask import g, session as fs
        tz = request.form.get("timezone", "") or request.form.get("browser_tz", "")
        if tz not in zoneinfo.available_timezones():
            g.refused = True
            fs["notice"] = "Pick your time zone from the list. Nothing was changed."
            return redirect(url_for("settings_view") + "#tz")
        put(timezone=tz)
        apply_settings()
        audit(f"Set the time zone to {tz}")
        fs["notice"] = f"Time zone set to {tz}. Documents already issued keep their dates."
        return redirect(url_for("settings_view"))

    @app.post("/setup/hide-list", endpoint="setup_hide_list")
    def setup_hide_list():
        put(finish_hidden="1")
        return redirect(url_for("home"))

    # ------------------------------------------------------------ per-step save and context

    def save(step, f):
        if step == "business":
            if not f.get("business_name", "").strip() or not f.get("photographer_name", "").strip():
                return "Your business name and your own name, please."
            tz = f.get("timezone", "") or f.get("browser_tz", "")    # the browser's own zone, sent by the page
            if tz not in zoneinfo.available_timezones():
                return "Pick your time zone."
            lat = lon = ""
            z = f.get("home_zip", "").strip()
            if z:
                ll = zip_latlon(z)
                if not ll:
                    return f"ZIP {z} isn't in the US ZIP table. Leave it blank and enter latitude and longitude instead."
                lat, lon = ll
            elif f.get("home_lat", "").strip():
                try:
                    lat, lon = float(f["home_lat"]), float(f.get("home_lon", ""))
                    assert -90 <= lat <= 90 and -180 <= lon <= 180
                except (ValueError, AssertionError):
                    return "Latitude and longitude should be numbers, like 39.83 and -98.58."
            put(business_name=f["business_name"].strip(), photographer_name=f["photographer_name"].strip(),
                studio_name=f.get("studio_name", "").strip(), business_email=f.get("business_email", "").strip(),
                business_phone=f.get("business_phone", "").strip()[:40], business_address=f.get("business_address", "").strip(), timezone=tz, home_zip=z, home_lat=lat, home_lon=lon)
            apply_settings()
            return None
        if step == "storage":
            cfg = VW.config()
            prim = backend_from_form(f, "primary_", cfg.get("primary"))
            msg = test_backend(prim, init_local=f.get("primary_init") == "1")
            if msg:
                return "Primary location: " + msg
            cfg["primary"] = prim
            if f.get("secondary_type"):
                sec = backend_from_form(f, "secondary_", cfg.get("secondary"))
                msg = test_backend(sec, init_local=f.get("secondary_init") == "1")
                if msg:
                    return "Second location: " + msg
                if (prim.get("type") == "local" and sec.get("type") == "local" and not f.get("same_disk_ok")
                        and same_disk(prim.get("path", ""), sec.get("path", ""))):
                    return ("SAME-DISK: The second copy is on the same disk as the first: one failed disk would take both. "
                            "Pick a folder on another disk or an SFTP or S3 location, or tick Keep it anyway below.")
                cfg["secondary"] = sec
            else:
                cfg.pop("secondary", None)
            if f.get("one_disk_ok"):
                cfg["one_disk_ok"] = True                 # remembered: later saves don't ask again
            if backups_share_disk(cfg) and not cfg.get("one_disk_ok"):
                return ("ONE-DISK: Your backups would be on the same disk as your data. They protect against mistakes, but"
                        " not against that disk failing. Pick a folder on another disk or dataset, add a second copy below"
                        " (a USB drive or an online account), or tick Continue anyway and add one later.")
            write_storage(cfg)
            t = f.get("backup_time", "")
            if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                put(backup_time=t)
            return None
        if step == "money":
            try:
                kv = dict(DEPOSIT_AMOUNT=int(f["deposit"]), EVENT_DEPOSIT_PCT=int(f["event_pct"]),
                          BALANCE_NET_DAYS=int(f["net_days"]), RESCHEDULE_FEE=int(f["reschedule_fee"]),
                          BALANCE_BEFORE_DAYS=int(f.get("before_days") or C.BALANCE_BEFORE_DAYS),
                          DELIVERY_DAYS=int(f["delivery_days"]), EVENT_DELIVERY_DAYS=int(f["event_delivery_days"]),
                          TRAVEL_FREE_MILES=int(f.get("travel_free") or C.TRAVEL_FREE_MILES))
                rate = float(f.get("mileage_rate") or 0)
                term = int(f.get("quote_term_days") or C.QUOTE_TERM_DAYS)
            except (KeyError, ValueError):
                return "Whole numbers, please (the mileage rate may have cents)."
            if any(v < 0 for v in kv.values()) or kv["EVENT_DEPOSIT_PCT"] > 100:
                return "Amounts can't be negative, and the event deposit is a percentage up to 100."
            methods = [m for m in PAYMENT_CHOICES if f.get("pay_" + m)] + \
                      [m.strip() for m in f.get("pay_other", "").split(",") if m.strip()]
            if not methods:
                return "Pick at least one way clients can pay."
            kv["PAYMENT_METHODS"] = methods
            kv["NET_TERMS_TYPES"] = [t for t in C.SESSION_TYPES if f.get("bal_" + t) == "after" or f.get("net_" + t)]
            kv["BEFORE_TYPES"] = [t for t in C.SESSION_TYPES if f.get("bal_" + t) == "before"]
            save_rules(**kv)
            put(mileage_rate=rate, quote_term_days=term)
            return None
        if step == "tax":
            mode = f.get("tax_mode", "none")
            if mode not in ("none", "flat") + (("regional",) if app_hooks().tax_region else ()):
                return "Pick how sales tax works for you."
            try:
                st = float(f.get("sales_tax_pct") or 0)
                lo = float(f.get("local_tax_pct") or 0)
            except ValueError:
                return "Rates are percentages, like 6 or 7.25."
            if mode != "none" and not st:
                return "Enter your rate, or choose No sales tax for now."
            if not (0 <= st <= 25 and 0 <= lo <= 10):
                return "Sales tax rates are between 0 and 25 percent (local between 0 and 10)."
            put(tax_mode=mode, sales_tax_pct=(st if mode != "none" else ""), local_tax_pct=(lo if mode == "regional" else ""))
            return None
        if step == "shoot":
            chosen = [k for k in C.PRESETS if f.get("preset_" + k)]
            keep = [t for t in C.TYPES if f.get("keep_" + t["key"])]
            if not chosen and not keep and rules().get("TYPES"):
                return None                               # types already set (an imported settings file): kept as they are
            have = {t["key"]: t for t in C.TYPES}       # a type you already have keeps its own label, steps and terms
            types = [have.get(t["key"], t) for t in C.preset_types(chosen or list(C.BASIC_PRESETS))]
            types = keep + [t for t in types if t["key"] not in {k["key"] for k in keep}]
            save_rules(TYPES=types)
            con = connect()
            try:
                starter = con.execute("SELECT COUNT(*), MIN(note), MIN(id) FROM fee_schedules").fetchone()
                if starter[0] <= 1 and (starter[1] or "") in ("Starting estimates", ""):
                    from datetime import datetime as _dt
                    items_ = json.dumps(C.preset_fees(chosen or list(C.BASIC_PRESETS)))
                    now_ = _dt.now().isoformat(timespec="seconds")
                    used = starter[2] and con.execute("SELECT 1 FROM quotes WHERE schedule_id=? LIMIT 1", (starter[2],)).fetchone()
                    if starter[0] == 1 and not used:   # the placeholder nothing has used yet: replaced, not versioned
                        con.execute("UPDATE fee_schedules SET created=?, items=? WHERE id=?", (now_, items_, starter[2]))
                    else:
                        con.execute("INSERT INTO fee_schedules (created, items, note) VALUES (?,?,?)", (now_, items_, "Starting estimates"))
                    con.commit()
            finally:
                con.close()
            return None
        if step == "documents":
            save_rules(LICENSE_TYPES=[t for t in C.SESSION_TYPES if f.get("rights_" + t) == "license"])
            put(governing_law=f.get("governing_law", "").strip()[:200])
            return None
        if step == "galleries":
            try:
                kv = dict(GALLERY_DAYS=int(f["gallery_days"]), GALLERY_UNOPENED_DAYS=int(f["unopened_days"]))
            except (KeyError, ValueError):
                return "Days are whole numbers."
            cfg = VW.config()
            if f.get("gallery_type"):
                gh = backend_from_form(f, "gallery_", cfg.get("gallery_host"))
                msg = test_backend(gh, init_local=f.get("gallery_init") == "1")
                if msg:
                    return "Gallery host: " + msg
                base = f.get("gallery_base_url", "").strip()
                if not base.startswith("https://"):
                    return "The web address galleries are served from should start with https://."
                cfg["gallery_host"] = gh
                put(gallery_base_url=base.rstrip("/") + "/")
            else:
                cfg.pop("gallery_host", None)
                put(gallery_base_url="")
            write_storage(cfg)
            save_rules(**kv)
            return None
        if step == "network":
            return None
        return None

    def context(step):
        cfg = VW.config()
        extra_ = dict(app_pub=app_ssh_key()[1], host_fp=B.host_key_fingerprint) if step in ("storage", "galleries") else {}
        if step == "shoot":
            preset_keys = {t["key"] for pr in C.PRESETS.values() for t in pr["types"]} | {C.PERSONAL_TYPE["key"]}
            have = {t["key"] for t in C.TYPES}
            chosen = [k for k, pr in C.PRESETS.items() if all(t["key"] in have for t in pr["types"])] \
                if "shoot" in done() else []           # a new install starts with nothing ticked; none ticked = the small set
            return dict(get=get, rules=C.current_rules(), types=C.SESSION_TYPES, presets=C.PRESETS, chosen=chosen,
                        custom=[t for t in C.TYPES if t["key"] not in preset_keys])
        if step == "tax":
            reg = app_hooks().tax_region
            return dict(get=get, rules=C.current_rules(), types=C.SESSION_TYPES, region=reg["name"] if reg else None)
        r = dict(C.current_rules())
        base = dict(get=get, rules=r, types=C.SESSION_TYPES, **extra_)
        if step == "business":
            base.update(zones=timezones(), tz=get("timezone", "") or host_tz(),   # until chosen: the browser's (setup.js),
                        tz_saved=bool(get("timezone", "")))                       # else the machine's TZ
        elif step == "storage":
            def is_marked(c):
                return c.get("type") == "local" and os.path.isfile(os.path.join(c.get("path", ""), B.LocalBackend.MARKER))
            import settings_io
            primary = dict(cfg.get("primary", {}))
            if primary.get("type", "local") == "local" and not is_marked(primary) and (
                    os.path.isdir("/vault") or settings_io._inside_private(primary.get("path", ""))):
                primary["path"] = suggested_vault()  # the folder the container mounts for backups, else one the
                                                     # checks accept (never the default inside the program folder)
            secondary = cfg.get("secondary", {})
            if request.method == "POST":           # a refused save shows what was typed, not the old settings
                primary = backend_from_form(request.form, "primary_", primary) or primary
                secondary = backend_from_form(request.form, "secondary_", secondary) or {}
            base.update(marked={"primary_": is_marked(primary), "secondary_": is_marked(secondary)},
                        primary=primary, secondary=secondary,
                        btime=(request.form.get("backup_time") if request.method == "POST" else None) or get("backup_time", "02:40"),
                        data_dir=str(VW.CFG_PATH.parent))
        elif step == "money":
            base.update(pay_choices=PAYMENT_CHOICES)
        elif step == "galleries":
            gal = dict(cfg.get("gallery_host", {}))
            if request.method == "POST" and request.form.get("gallery_type"):   # a refused save shows what was typed
                gal = backend_from_form(request.form, "gallery_", gal) or gal
            base.update(gallery=gal, gallery_marked=gal.get("type") == "local" and
                        os.path.isfile(os.path.join(gal.get("path", ""), B.LocalBackend.MARKER)))
        elif step == "network":
            base.update(nets=[str(n) for n in allow_nets], me=request.remote_addr,
                        only=bool(os.environ.get("CRM_ALLOW_ONLY")))
        return base
