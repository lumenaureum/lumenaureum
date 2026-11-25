import gzip
import json
import os
import re
import secrets
import threading
import time
import zoneinfo
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

import config as C
import datakey
from vault import backends as B, s3 as S3, web as VW

ROOT = Path(__file__).resolve().parent
STEPS = [
    ("business", "Your business", True),
    ("storage", "Backups", True),
    ("shoot", "What you shoot", False),
    ("money", "Payments and terms", False),
    ("tax", "Taxes", False),
    ("documents", "Documents", False),
    ("galleries", "Online galleries", False),
    ("network", "Who can reach this", False),
]
SLUGS = [s for s, _, _ in STEPS]
OPEN_DURING_SETUP = {"static", "logout", "healthz", "setup_step", "setup_test", "setup_host_check", "setup_web_cert_check",
                     "setup_web_cert_trust", "setup_web_cert_refuse", "security", "settings_import",
                     "settings_import_gallery", "settings_export", "about"}

PAYMENT_CHOICES = ["cash", "check", "card", "bank transfer", "payment app"]
_ZIPS = None

SECRET_KEYS = ("secret_key", "password", "private_key", "known_hosts", "host_key")

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

    import sys
    tz = getattr(sys.modules.get("app") or sys.modules.get("__main__"), "HOST_TZ", "")
    return tz if tz in zoneinfo.available_timezones() else ""

def _data_and_program():
    data = os.path.dirname(os.path.realpath(os.environ.get("CRM_DB", str(ROOT / "data" / "crm.db"))))
    return data, os.path.realpath(str(ROOT))

VAULT_MOUNT = "/vault"

def suggested_vault():

    import settings_io
    data, prog = _data_and_program()
    for p_ in (VAULT_MOUNT, os.path.join(data, "vault"), os.path.join(os.path.dirname(prog), "lumen-aureum-vault")):
        if (p_ != VAULT_MOUNT or os.path.isdir(p_)) and not settings_io._inside_private(p_):
            return p_
    return ""

def inside_private_message(key):

    data, prog = _data_and_program()
    if key != "path":
        return (f"That file is inside Lumen Aureum's own data folder ({data}) or program folder ({prog}). Keep keys"
                " somewhere else, for example a folder mounted only for them.")
    ex = suggested_vault()
    return (f"That folder is inside Lumen Aureum's own data folder ({data}) or program folder ({prog}), where a backup"
            " would be lost along with the app. Choose a folder outside both, ideally on another disk"
            + (f", for example {ex}." if ex else "."))

def same_disk(path, other):

    try:
        return os.stat(path).st_dev == os.stat(other).st_dev
    except OSError:
        return False

def backups_share_disk(cfg=None):

    cfg = cfg if cfg is not None else VW.config()
    prim = cfg.get("primary") or {}
    return prim.get("type") == "local" and same_disk(prim.get("path", ""), str(VW.CFG_PATH.parent)) and not cfg.get("secondary")

def write_storage(cfg):
    VW.write_config(cfg)

PROVIDERS = {
    "sftp.dreamhost": dict(name="DreamHost", root="{d}/g", keymode="password", where=(
        "In your DreamHost panel, the SFTP Users & Files page: Login info shows the user name and the server's name,"
        " and resets the password.",
        "Host can be your website's name once it points to DreamHost. Your website's folder has its name, so galleries"
        " go in example.com/g.")),
    "sftp.siteground": dict(name="SiteGround", port="18765", root="", keymode="app",
                            root_ph="your website's public_html folder, then /g", where=(
        "In Site Tools, Devs > SSH Keys Manager: import the app's key below, then the key's menu > SSH Credentials"
        " shows the host name and user name.",
        "SiteGround signs in with a key only, on port 18765 (filled in). Site > File Manager shows your website's"
        " public_html folder.")),
    "sftp.bluehost": dict(name="Bluehost", root="public_html/g", keymode="password", where=(
        "Turn on SSH access in your Bluehost account first: it is off on shared plans.",
        "The user name and password are your cPanel ones, and Host is your website's name.")),
    "local.drive": dict(name="A drive you plug in", where=(
        "Plug the drive into the computer this app runs on, and type the folder on it where backups go.",
        "Leave it plugged in: backups go there every day.")),
    "sftp.nas": dict(name="Your NAS", where=(
        "Turn on your NAS's SFTP service in its control panel's file services settings, and make a user with a"
        " shared folder for backups.",
        "Host is the NAS's name or address on your network. Add the app's key below to that user.",
        "Already mounted into the app? Choose A folder instead.")),
    "s3.b2": dict(name="Backblaze B2", endpoint_ph="e.g. s3.us-west-004.backblazeb2.com", where=(
        "On Backblaze, Buckets shows each bucket's Endpoint: copy it into Service address.",
        "Application Keys makes a key: its keyID is the access key, its applicationKey the secret key.")),
    "s3.r2": dict(name="Cloudflare R2", endpoint_ph="e.g. https://<account id>.r2.cloudflarestorage.com", where=(
        "In Cloudflare, R2: your bucket's Settings show its S3 API address. Paste it into Service address.",
        "Manage API tokens makes a token that shows its access key and secret key once.")),
    "s3.wasabi": dict(name="Wasabi", endpoint_ph="e.g. s3.us-east-2.wasabisys.com", where=(
        "In the Wasabi console, Buckets shows each bucket's region: the service address is s3.<region>.wasabisys.com.",
        "Access Keys makes the access key and secret key.")),
    "s3.aws": dict(name="Amazon S3 (AWS)", where=(
        "In the AWS console, S3 lists your bucket's name. Leave Service address blank.",
        "IAM > Users > Security credentials makes an access key and its secret key.")),
}
GALLERY_HOSTS = ("sftp.dreamhost", "sftp.siteground", "sftp.bluehost")
BACKUP_PLACES = ("local.drive", "sftp.nas")
CLOUDS = ("s3.b2", "s3.r2", "s3.wasabi", "s3.aws")

def provider_of(c):

    c = c or {}
    if c.get("type") == "s3":
        name = cloud_name(c)
        return next((k for k in CLOUDS if PROVIDERS[k]["name"] == name), "")
    if c.get("type") == "sftp" and str(c.get("host") or "").lower().rstrip(".").endswith(".dreamhost.com"):
        return "sftp.dreamhost"
    return ""

def cloud_name(c):

    ep = re.sub(r"^https?://", "", str((c or {}).get("endpoint") or "").strip().lower()).split("/")[0].split(":")[0]
    if not ep or ep.endswith(".amazonaws.com"):
        return PROVIDERS["s3.aws"]["name"]
    for k, end in (("s3.b2", ".backblazeb2.com"), ("s3.r2", ".r2.cloudflarestorage.com"), ("s3.wasabi", ".wasabisys.com")):
        if ep.endswith(end):
            return PROVIDERS[k]["name"]
    return ""

def backend_from_form(f, prefix, old=None):

    old = old or {}
    t, _, preset = f.get(prefix + "type", "").partition(".")
    pv = PROVIDERS.get(f"{t}.{preset}", {}) if preset else {}
    if t == "local":
        return {"type": "local", "path": f.get(prefix + "path", "").strip()}
    if t == "sftp":
        c = {"type": "sftp", "host": f.get(prefix + "host", "").strip(), "user": f.get(prefix + "user", "").strip(),
             "key_file": f.get(prefix + "key_file", "").strip(), "root": f.get(prefix + "root", "").strip()}
        if not c["root"] and pv.get("root") and "{d}" not in pv["root"]:
            c["root"] = pv["root"]
        port = f.get(prefix + "port", "").strip() or pv.get("port", "")
        if port.isdigit() and port != "22":
            c["port"] = int(port)
        mode = f.get(prefix + "keymode", "app")
        if mode == "password" and prefix == "gallery_":
            c["key_file"] = ""
            c["auth"] = "password"
            typed = f.get(prefix + "password", "")

            c["password"] = typed if typed.strip() else (old.get("password", "") if B.same_account(old, c) else "")
        elif mode == "app":
            c["key_file"] = ""
            c["private_key"] = app_ssh_key()[0]
        same_server = old.get("host") == c["host"] and int(old.get("port", 22)) == int(c.get("port", 22))
        pin = (f.get(prefix + "host_key") or "").strip() if f.get(prefix + "trust") else ""
        if pin or (same_server and old.get("host_key")):
            c["host_key"] = pin or old["host_key"]
        return c
    if t == "s3":
        bucket, ep = f.get(prefix + "bucket", "").strip(), f.get(prefix + "endpoint", "").strip()
        if ep and "://" not in ep:
            ep = "https://" + ep
        if ep and bucket and ep.rstrip("/").endswith("/" + bucket):
            ep = ep.rstrip("/")[:-len(bucket) - 1]
        c = {"type": "s3", "bucket": bucket, "endpoint": ep or None,
             "region": f.get(prefix + "region", "").strip() or None, "access_key": f.get(prefix + "access_key", "").strip(),
             "secret_key": f.get(prefix + "secret_key", "").strip() or old.get("secret_key", ""),
             "prefix": f.get(prefix + "s3prefix", "").strip(),
             "addressing": f.get(prefix + "addressing", "auto") if f.get(prefix + "addressing") in ("path", "virtual") else None}
        return {k: v for k, v in c.items() if v is not None}
    return None

def conn_rows(c, gallery=False):

    c = c or {}
    t = c.get("type")
    if t == "local":
        return [("Kind", "A web server at home" if gallery else "A folder on this machine or a mounted share"),
                ("Folder", c.get("path", ""))]
    if t == "sftp":
        port = f":{c['port']}" if c.get("port") else ""
        kind = PROVIDERS.get(provider_of(c), {}).get("name") or ("Your web host's login" if gallery else "A server you sign in to")
        return [("Kind", kind), ("Server", f"{c.get('user', '')}@{c.get('host', '')}{port}"),
                ("Folder", c.get("root") or "its home folder"),
                ("Sign-in", "A password, saved encrypted") if c.get("auth") == "password" else
                ("Sign-in key", "A key file: " + c["key_file"] if c.get("key_file") else "The app's own key")]
    if t == "s3":
        return [("Kind", cloud_name(c) or "Cloud storage"), ("Bucket", c.get("bucket", "") + (f" / {c['prefix']}" if c.get("prefix") else ""))] + (
                [("Service address", c["endpoint"])] if c.get("endpoint") else []) + [("Access key", c.get("access_key", ""))]
    return []

def conn_plain_http(c):
    return bool(c and c.get("type") == "s3" and (c.get("endpoint") or "").lower().startswith("http://"))

def gallery_only_here(url):

    import netsec
    words = netsec.only_here_words(url)
    return dict(text=words, help=netsec.ONLY_HERE_HELP) if words else None

def app_ssh_key():

    priv = _get("ssh_app_key")
    if not priv:
        priv, _ = B.new_ssh_key()
        _put(ssh_app_key=priv)
    return priv, B.public_line(priv)

_DB = {}

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

def test_backend(cfg, init_local=False, keep=True, seen=None):

    made = []
    try:
        return _test_backend(cfg, init_local, made, seen)
    finally:
        if not keep:
            for path in reversed(made):
                try:
                    (os.rmdir if os.path.isdir(path) else os.remove)(path)
                except OSError:
                    pass

def _test_backend(cfg, init_local, made, seen=None):

    if not cfg:
        return "Nothing chosen."
    if cfg.get("type") == "sftp" and cfg.get("auth") == "password" and not cfg.get("password"):
        return "Type the password your web host gave you for this user."
    if cfg.get("type") == "sftp" and len(cfg.get("password") or "") > 1000:
        return "That password is longer than 1,000 characters."
    import settings_io
    for k in ("path", "key_file", "known_hosts"):
        if cfg.get(k) and settings_io._inside_private(cfg[k]):
            return inside_private_message(k)
    ep = (cfg.get("endpoint") or "") if cfg.get("type") == "s3" else ""
    why = S3.endpoint_problem(ep) if ep else None
    if why:
        return why
    try:
        if cfg["type"] == "local":
            p = cfg.get("path", "")
            if not p or not os.path.isabs(p):
                return "Give the full path of a folder, starting with /."
            if init_local and not os.path.exists(p) and os.path.isdir(os.path.dirname(p.rstrip("/"))):
                os.mkdir(p, 0o700)
                made.append(p)
            if init_local and os.path.isdir(p) and not os.path.isfile(os.path.join(p, B.LocalBackend.MARKER)):
                if any(os.scandir(p)):
                    return "That folder already has files in it. Pick an empty folder, or one used by Lumen Aureum before."
                B.LocalBackend.init_root(p)
                made.append(os.path.join(p, B.LocalBackend.MARKER))
            elif not os.path.isdir(p):
                if os.path.exists("/.dockerenv") or os.environ.get("LUMENAUREUM_BUILD"):
                    return ("That folder doesn't exist inside the app's container. A folder on your machine must be mounted"
                            " into the container first: add a line under volumes: in compose.yaml (for example"
                            " - /mnt/usb-backup/lumenaureum:/backup2), restart the app, then type the path after the colon"
                            " (/backup2). See docs/where-it-runs.md, Folders outside the container.")
                return "That folder doesn't exist. Create it (and mount the share, if it is one) and try again."
        return _round_trip(cfg)
    except Exception as e:
        if isinstance(e, S3.S3Error) and _other_addressing(cfg):
            return ""
        if seen is not None:
            seen.append(e)
        return plain_error(e, cfg)

def _round_trip(cfg):

    import io
    be = B.from_config(cfg)
    name = f"setup-test/{secrets.token_hex(6)}.txt"
    body = b"Lumen Aureum storage test\n"
    be.put_stream(name, io.BytesIO(body), "text/plain")
    out = io.BytesIO()
    be.get_stream(name, out)
    be.delete(name)
    try:
        be.remove_dir("setup-test")
    except Exception:
        pass
    return "" if out.getvalue() == body else "The file read back differently from what was written."

def _other_addressing(cfg):

    if cfg.get("type") != "s3" or cfg.get("addressing"):
        return False
    try:
        auto = S3.Client(cfg.get("bucket") or "-", "", "", endpoint=cfg.get("endpoint"), region=cfg.get("region")).addressing
        import ipaddress
        host = re.sub(r"^https?://", "", str(cfg.get("endpoint") or "")).split("/")[0].rsplit(":", 1)[0].strip("[]")
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        other = "path" if auto == "virtual" else "virtual"
        if _round_trip(dict(cfg, addressing=other)) == "":
            cfg["addressing"] = other
            return True
    except Exception:
        pass
    return False

def plain_error(e, cfg):

    text = str(e)
    host = cfg.get("host") or "that server"
    if "is not initialised" in text or "is not initialized" in text:
        return ("That folder hasn't been set up for Lumen Aureum yet. If it is a new empty folder, tick \"New empty folder:"
                " set it up\". If it should already be set up, check that the share is mounted.")
    if type(e).__name__ == "BadHostKeyException":
        return (f"{host}'s key changed since you trusted it. Trust the new key below only if your host confirms its"
                " fingerprint changed (a rebuilt or moved server, say).")
    if isinstance(e, FileNotFoundError) and "known_hosts" in text or "not found in known_hosts" in text or (
            "known_hosts" in text.lower() and "server" in text.lower()):
        return f"This app hasn't connected to {host} before. Test again to see its key fingerprint and trust it."
    if isinstance(e, FileNotFoundError) and cfg.get("key_file") and cfg["key_file"] in text:
        return "The private key file isn't there."
    if isinstance(e, S3.CertProblem):
        return ("Its certificate changed since you trusted it. Check it below, under The S3 storage's certificate."
                if e.kind == "changed" else
                "Its certificate isn't from a public authority (it may be self-signed). Check its fingerprint below, under"
                " The S3 storage's certificate, and trust it if it matches.")
    if isinstance(e, (S3.S3Error, S3.PlainHTTPRefused)):
        return str(e)
    if "WRONG_VERSION_NUMBER" in text:
        return "This address doesn't answer with HTTPS. Check the port, or whether the server uses https."
    if "CERTIFICATE_VERIFY_FAILED" in text:
        if re.search(r"self[- ]signed", text):
            return "Its certificate isn't trusted (self-signed). Test again to see its fingerprint and trust it."
        return "Its certificate isn't trusted. Check the address, and that the certificate is current."
    if type(e).__name__ in ("AuthenticationException", "BadAuthenticationType"):
        if cfg.get("auth") == "password":
            return "The server refused the user name or password."
        return "The server refused the key."
    if isinstance(e, B.PasswordMissing):
        return "Type the password your web host gave you for this user."
    if isinstance(e, (TimeoutError, ConnectionRefusedError)) or type(e).__name__ in ("NoValidConnectionsError", "gaierror"):
        return "Can't reach that server."
    import errtext
    return errtext.named(e)

def unknown_host(c):

    if not c or c.get("type") != "sftp" or not c.get("host") or c.get("host_key"):
        return False
    kh = os.path.expanduser(c.get("known_hosts", "~/.ssh/known_hosts"))
    if not os.path.isfile(kh):
        return True
    try:
        import paramiko
        port = int(c.get("port", 22))
        return paramiko.HostKeys(kh).lookup(c["host"] if port == 22 else f"[{c['host']}]:{port}") is None
    except Exception:
        return True

def first_contact(c):

    try:
        line, fp = B.fetch_host_key(c["host"], c.get("port", 22))
    except Exception as e:
        return plain_error(e, c), None
    return None, dict(host_key=line, fp=fp)

def first_contact_message(c):
    return (f"First connection to {c['host']}. Check that the key fingerprint shown below matches the one your host"
            " gives, tick Trust this server, and test again.")

SECRET_FIELDS = ("primary_secret_key", "secondary_secret_key", "gallery_secret_key", "gallery_password")
CW_OFF_WORDS = ("Turned gallery codewords off for every session type and for sessions not online yet, as the web host"
                " can't check them")
STEP_PREFIXES = {"storage": ("primary_", "secondary_"), "galleries": ("gallery_",)}

def offer_trust(locs):

    from flask import g
    trust, msgs = g.setdefault("setup_trust", {}), []
    for label, p_, c_ in locs:
        if unknown_host(c_):
            why, got = first_contact(c_)
            if got:
                trust[p_] = got
            msgs.append(label + (why or first_contact_message(c_)))
    return " ".join(msgs)

def offer_changed_key(p_, seen):

    from flask import g
    e = seen[-1] if seen else None
    if type(e).__name__ == "BadHostKeyException" and getattr(e, "key", None) is not None:
        line = f"{e.key.get_name()} {e.key.get_base64()}"
        g.setdefault("setup_trust", {})[p_] = dict(host_key=line, fp=B.host_key_fingerprint(line))

def trust_boxes(prefixes):

    from flask import g
    out = {k_: dict(v_) for k_, v_ in (getattr(g, "setup_trust", None) or {}).items()}
    if request.method != "POST":
        return out
    for p_ in prefixes:
        hk = request.form.get(p_ + "host_key", "").strip()
        ticked = bool(request.form.get(p_ + "trust"))
        if p_ in out:
            out[p_]["ticked"] = ticked and hk == out[p_]["host_key"]
        elif hk:
            try:
                out[p_] = dict(host_key=hk, fp=B.host_key_fingerprint(hk), ticked=ticked)
            except Exception:
                pass
    return out

def form_value(name, saved=""):

    return request.form.get(name, "") if request.method == "POST" else ("" if saved is None else saved)

def form_ticked(name, saved):

    return name in request.form if request.method == "POST" else bool(saved)

def in_container():
    return os.path.exists("/.dockerenv") or bool(os.environ.get("LUMENAUREUM_BUILD"))

_PENDING, _PENDING_TTL, _PENDING_LOCK = {}, 900, threading.Lock()

def _pending_forget():

    with _PENDING_LOCK:
        _PENDING.clear()

datakey.ON_LOCK.append(_pending_forget)
datakey.ON_LOCK.append(B.forget_passwords)

def _pending_token(make=False):
    from flask import session as fs
    t = fs.get("setup_pending")
    if not t and make:
        t = fs["setup_pending"] = secrets.token_urlsafe(18)
    return t

def pending_secrets():
    t, now = _pending_token(), time.time()
    with _PENDING_LOCK:
        for k_ in [k_ for k_, (at, _) in _PENDING.items() if at < now]:
            del _PENDING[k_]
        return {f_: v_ for (t_, f_), (_, v_) in _PENDING.items() if t and t_ == t}

def keep_secrets(form):
    typed = {f_: form.get(f_, "") if f_.endswith("password") else form.get(f_, "").strip()
             for f_ in SECRET_FIELDS if form.get(f_, "").strip()}
    if typed:
        t = _pending_token(make=True)
        with _PENDING_LOCK:
            for f_, v_ in typed.items():
                _PENDING[(t, f_)] = (time.time() + _PENDING_TTL, v_)

def drop_secrets(prefixes):
    t = _pending_token()
    with _PENDING_LOCK:
        for k_ in [k_ for k_ in _PENDING if k_[0] == t and k_[1].startswith(prefixes)]:
            del _PENDING[k_]

def with_pending(form):

    pend = pending_secrets()
    if not pend:
        return form
    f = form.copy()
    for f_, v_ in pend.items():
        if not f.get(f_, "").strip():
            f[f_] = v_
    return f

def _app():
    import sys
    return sys.modules["app"] if "app" in sys.modules else sys.modules["__main__"]

def _rate(v):
    v = (v or "").strip().rstrip("%").strip()
    if not v:
        return None
    try:
        r_ = float(v)
    except ValueError:
        return -1.0
    return r_ if r_ == r_ else -1.0

def save_taxes_form(f, get, put):

    cur = _app().taxes_list()
    if "tax_mode" in f and not f.get("taxes_posted"):
        mode = f.get("tax_mode", "none")
        if mode not in ("none", "flat"):
            return "Pick how sales tax works for you."
        r_ = _rate(f.get("sales_tax_pct"))
        if mode == "flat" and not r_:
            return "Enter your rate, or choose No sales tax for now."
        if mode == "flat" and not 0 < r_ <= 25:
            return "Tax rates are between 0 and 25 percent."
        first = cur[0] if cur else dict(key="sales", name="Sales tax", included=False, default=True)
        items = [dict(first, rate=r_)] + cur[1:] if mode == "flat" else []
    else:
        items, names = [], set()
        rows = [(f.get(f"tax_key_{i}", ""), i) for i in range(len(cur))]
        by_key = {t_["key"]: t_ for t_ in cur}
        for key, i in rows:
            if key not in by_key or f.get(f"tax_del_{i}"):
                continue
            items.append(dict(key=key, name=(f.get(f"tax_name_{i}") or "").strip()[:40], rate=_rate(f.get(f"tax_rate_{i}")),
                              included=f.get(f"tax_how_{i}") == "in", default=bool(f.get(f"tax_default_{i}"))))
        if (f.get("new_tax_name") or "").strip() or (f.get("new_tax_rate") or "").strip():
            used = {t_["key"] for t_ in cur}
            n_ = int(get("tax_next", "1") or 1)
            while f"t{n_}" in used:
                n_ += 1
            items.append(dict(key=f"t{n_}", name=(f.get("new_tax_name") or "").strip()[:40] or ("Sales tax" if not items else ""),
                              rate=_rate(f.get("new_tax_rate")), included=f.get("new_tax_how") == "in",
                              default=bool(f.get("new_tax_default"))))
        for t_ in items:
            if not t_["name"]:
                return "Give each tax a name, as your invoices should show it (Sales tax, VAT, GST)."
            if t_["name"].lower() in names:
                return f"Two taxes are called {t_['name']}. Give each its own name."
            names.add(t_["name"].lower())
            if t_["rate"] is None or not 0 < t_["rate"] <= 25:
                return f"Enter the rate for {t_['name']}: a percentage above 0, up to 25, like 6 or 7.25."
    new_ = [int(t_["key"][1:]) for t_ in items if t_["key"] not in {c_["key"] for c_ in cur} and t_["key"][1:].isdigit()]
    if new_:
        put(tax_next=max(new_) + 1)
    on_top = round(sum(t_["rate"] for t_ in items if t_["default"] and not t_["included"]), 4)
    put(taxes=json.dumps(items), tax_mode="flat" if items else "none", sales_tax_pct=f"{on_top:g}" if items else "")
    return None

def app_hooks():
    import sys
    return sys.modules["app"].hooks if "app" in sys.modules else sys.modules["__main__"].hooks

def register(app, connect, apply_settings, audit, allow_nets, after_rules=None):
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

    def conn_status():

        try:
            return json.loads(get("conn_status", "{}") or "{}")
        except ValueError:
            return {}

    def conn_mark(which, ok, why=""):
        st = conn_status()
        st[which] = dict(at=time.strftime("%Y-%m-%d %H:%M"), ok=bool(ok), why=(why or "")[:300])
        put(conn_status=json.dumps(st))

    app.jinja_env.globals["conn_status"] = conn_status
    app.jinja_env.globals["conn_rows"] = conn_rows
    app.jinja_env.globals.update(providers=PROVIDERS, provider_of=provider_of, gallery_hosts=GALLERY_HOSTS,
                                 backup_places=BACKUP_PLACES, clouds=CLOUDS)
    app.jinja_env.globals["conn_plain_http"] = conn_plain_http
    app.jinja_env.globals["gallery_only_here"] = gallery_only_here
    app.config["conn_mark"] = conn_mark
    app.config["conn_status"] = conn_status

    def rules():
        try:
            return json.loads(get("rules", "{}"))
        except ValueError:
            return {}

    def save_rules(**kv):
        r = rules()
        if "TYPES" in kv and "LICENSE_TYPES" not in kv and "LICENSE_TYPES" in r:
            had = {t_.get("key") for t_ in C.TYPES}
            new_ = [t_["key"] for t_ in kv["TYPES"] if t_.get("key") not in had]
            if new_:
                kv = dict(kv, LICENSE_TYPES=list(r["LICENSE_TYPES"]) + [k_ for k_ in new_ if k_ not in r["LICENSE_TYPES"]])
        r.update(kv)
        put(rules=json.dumps(r))
        apply_settings()
        if after_rules:
            after_rules()

    def check_host():

        import errtext
        import hostcheck
        from plugins.gallery import fingerprint_of, open_host
        gh = VW.config().get("gallery_host")
        base = get("gallery_base_url", "")
        if not gh or not base:
            return None

        loaded = app.config.get("gallery_take_down")
        live = loaded.__globals__["check_gallery"] if loaded else (lambda: None)
        try:
            res = hostcheck.capabilities(open_host(gh), base, live())
        except Exception as e:
            res = dict(at=time.strftime("%Y-%m-%d %H:%M"), kind="unreachable", version="", detail=errtext.words(e),
                       htaccess=None, features=[(k_, l_, False, "the host couldn't be checked") for k_, l_ in hostcheck.FEATURES])
        put(gallery_host_caps=json.dumps(dict(fp=fingerprint_of(gh), setup=res, now=res, checked=time.time())))
        return res

    def codewords_off(log=True, con=None, save=None):

        from plugins.gallery import codewords_kept, codewords_waiting
        types = [dict(t, policy=dict(t.get("policy") or {}, gallery_codeword=False)) for t in C.TYPES]
        (save or save_rules)(TYPES=types)
        own = con is None
        con = connect() if own else con
        try:
            ids = codewords_waiting(con)
            con.executemany("UPDATE sessions SET gallery_codeword='' WHERE id=?", [(i,) for i in ids])
            kept = codewords_kept(con)
            if own:
                con.commit()
        finally:
            if own:
                con.close()
        if log:
            _app().audit_after(CW_OFF_WORDS)
        n = len(ids)
        from flask import session as fs_
        fs_["notice"] = ((fs_.get("notice") or "Saved.") + " Codewords are off for galleries here."
                         + (f" Cleared the codeword on {n} session{'' if n == 1 else 's'} not online yet." if n else "")
                         + (f" {kept} galler{'y' if kept == 1 else 'ies'} already online keep{'s' if kept == 1 else ''}"
                            f" {'its' if kept == 1 else 'their'} codeword." if kept else ""))

    @app.post("/setup/galleries/codewords-off", endpoint="setup_codewords_off")
    def setup_codewords_off():

        if (VW.config().get("gallery_host") or {}).get("type") != "s3":
            abort(400)
        codewords_off(log=False)
        return redirect(url_for("setup_step", step="galleries"))

    app.config.setdefault("ep_words", {})["setup_codewords_off"] = CW_OFF_WORDS
    app.config["codewords_off"] = codewords_off

    @app.post("/setup/galleries/app-key", endpoint="setup_gallery_app_key")
    def setup_gallery_app_key():

        from flask import g as g_, session as fs_
        cfg = VW.config()
        gh = dict(cfg.get("gallery_host") or {})
        if gh.get("type") != "sftp" or gh.get("auth") != "password":
            abort(400)
        new = {k: v for k, v in gh.items() if k not in ("auth", "password", "key_file")}
        new["private_key"] = app_ssh_key()[0]
        msg = test_backend(new)
        if msg:
            g_.refused = True
            fs_["notice"] = "Still signing in with the password, as the app's own key didn't work: " + msg
            fs_["notice_refused"] = True
        else:
            write_storage(dict(cfg, gallery_host=new))
            conn_mark("gallery", True)
            fs_["notice"] = "Switched to the app's own key. The password is no longer kept."
        return redirect(url_for("setup_step", step="galleries"))

    app.config.setdefault("ep_words", {})["setup_gallery_app_key"] = "Switched the web host to the app's own key"

    @app.post("/setup/host-check", endpoint="setup_host_check")
    def setup_host_check():

        check_host()
        return redirect(url_for("setup_step", step="galleries") + "#hostcaps")

    def seen_certs():
        import webtls
        con = connect()
        try:
            rows = con.execute("SELECT key, value FROM settings WHERE key LIKE ?", (webtls.SEEN_KEY + "%",)).fetchall()
        finally:
            con.close()
        out = {}
        for k_, v_ in rows:
            try:
                v_ = json.loads(v_)
            except (TypeError, ValueError):
                continue
            if isinstance(v_, dict) and v_.get("kind") in ("untrusted", "changed"):
                out[k_[len(webtls.SEEN_KEY):]] = v_
        return out

    def cert_rows():

        import webtls
        base = get("gallery_base_url", "")
        seen = seen_certs()
        addrs = ([webtls.address(base)] if base.startswith("https://") else []) + sorted(seen)
        pins = webtls.pins()
        rows, had = [], set()
        for a_ in addrs:
            if not a_ or a_ in had:
                continue
            had.add(a_)
            pin = pins.get(a_) if isinstance(pins.get(a_), dict) and pins[a_].get("spki") else None
            now_ = None
            if pin:
                try:
                    now_ = json.loads(get(webtls.NOW_KEY + a_, "") or "null")
                except ValueError:
                    now_ = None
                if not (isinstance(now_, dict) and now_.get("spki") == pin.get("spki")
                        and now_.get("sha256") != pin.get("sha256")):
                    now_ = None
            s_ = seen.get(a_)
            if s_ and s_.get("cert") and "name_ok" not in s_["cert"]:
                s_["cert"]["name_ok"] = webtls.names_address(s_["cert"], webtls.host_of(a_))
            rows.append(dict(addr=a_, host=webtls.host_of(a_), pinned=pin, renewed=now_, seen=s_,
                             expired=bool(pin and webtls.expired(now_ or pin))))
        return rows

    def known_addr(a_):
        return a_ and any(r_["addr"] == a_ for r_ in cert_rows())

    def after_cert():

        base = get("gallery_base_url", "")
        if base:
            check_host()
        fn = app.config.get("gallery_start_upgrade")
        if fn:
            try:
                fn()
            except Exception:
                app.logger.exception("web host certificate: starting the gallery update")

    def drop_seen(a_):
        import webtls
        con = connect()
        try:
            con.execute("DELETE FROM settings WHERE key IN (?,?)", (webtls.SEEN_KEY + a_, webtls.NOW_KEY + a_))
            con.commit()
        finally:
            con.close()

    @app.post("/setup/web-cert/check", endpoint="setup_web_cert_check")
    def setup_web_cert_check():

        import webtls
        a_ = (request.form.get("addr") or "")[:300]
        if known_addr(a_):
            webtls.check(a_)
        return redirect(url_for("setup_step", step="galleries") + "#webcert")

    @app.post("/setup/web-cert/trust", endpoint="setup_web_cert_trust")
    def setup_web_cert_trust():

        import webtls
        a_, _, sha_ = (request.form.get("cert") or "")[:400].strip().rpartition(" ")
        sha_ = sha_.lower()
        rec = seen_certs().get(a_) or {}
        cert = rec.get("cert") or {}
        if not known_addr(a_) or not sha_ or cert.get("sha256") != sha_:
            return redirect(url_for("setup_step", step="galleries", cert="stale") + "#webcert")
        now = webtls.peek(a_)
        if now is None or now["sha256"] != sha_:
            webtls.check(a_)
            return redirect(url_for("setup_step", step="galleries", cert="stale") + "#webcert")
        public = webtls.publicly_trusted(a_)
        if not public and not webtls.names_address(now, webtls.host_of(a_)):

            return redirect(url_for("setup_step", step="galleries", cert="name") + "#webcert")
        cert = now
        cfg = VW.config()
        certs = dict(cfg.get(webtls.STORE) or {})
        if public:
            certs.pop(a_, None)
            said = f"The web host's certificate at {a_} is publicly trusted now; the certificate confirmed before is forgotten"
        else:
            certs[a_] = webtls.stamp(cert)
            said = (f"Trusted the web host's certificate at {a_} (SHA-256 {cert.get('fp', '')}; its key "
                    f"{cert.get('spki_fp', '')})")
        if certs:
            cfg[webtls.STORE] = certs
        else:
            cfg.pop(webtls.STORE, None)
        write_storage(cfg)
        drop_seen(a_)
        after_cert()
        audit(said)
        if not complete() and "galleries" in done() and not any(
                r_["seen"] and not r_["seen"].get("refused") for r_ in cert_rows()):

            return redirect(url_for("setup_step", step="galleries", cert="trusted", go=1) + "#hostcaps")
        return redirect(url_for("setup_step", step="galleries", cert="trusted") + "#webcert")

    @app.post("/setup/web-cert/refuse", endpoint="setup_web_cert_refuse")
    def setup_web_cert_refuse():

        import webtls
        a_ = (request.form.get("addr") or "")[:300]
        rec = seen_certs().get(a_)
        if rec and known_addr(a_):
            rec["refused"] = (rec.get("cert") or {}).get("sha256", "")
            put(**{webtls.SEEN_KEY + a_: json.dumps(rec)})
            audit(f"Refused the web host's new certificate at {a_} (SHA-256 {(rec.get('cert') or {}).get('fp', '')})")
        return redirect(url_for("setup_step", step="galleries") + "#webcert")

    app.config.setdefault("ep_words", {})["setup_web_cert_check"] = "Checked the web host's certificate"
    app.config.setdefault("quiet_endpoints", set()).update({"setup_web_cert_trust", "setup_web_cert_refuse"})

    def s3_note(addr, kind, seen):

        key = S3.SEEN_KEY + addr
        con = connect()
        try:
            was = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if kind:
                rec = dict(kind=kind, cert=seen, at=time.time())
                try:
                    old = json.loads(was[0]) if was else {}
                except (TypeError, ValueError):
                    old = {}
                if isinstance(old, dict) and old.get("refused") and old["refused"] == (seen or {}).get("sha256"):
                    rec["refused"] = old["refused"]
                con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(rec)))
                con.commit()
            elif was:
                con.execute("DELETE FROM settings WHERE key=?", (key,))
                con.commit()
        finally:
            con.close()

    S3.NOTE = s3_note

    def s3_seen():
        con = connect()
        try:
            rows = con.execute("SELECT key, value FROM settings WHERE key LIKE ?", (S3.SEEN_KEY + "%",)).fetchall()
        finally:
            con.close()
        out = {}
        for k_, v_ in rows:
            try:
                v_ = json.loads(v_)
            except (TypeError, ValueError):
                continue
            if isinstance(v_, dict) and v_.get("kind") in ("untrusted", "changed"):
                out[k_[len(S3.SEEN_KEY):]] = v_
        return out

    def s3_cert_rows(step):

        import webtls
        cfg = VW.config()
        locs = [cfg.get(p_) for p_ in (("primary", "secondary") if step == "storage" else ("gallery_host",))]
        seen = s3_seen()
        table = cfg.get(S3.STORE) or {}
        rows, had = [], set()
        for a_ in [S3.cert_address(c_) for c_ in locs] + sorted(seen):
            if not a_ or a_ in had:
                continue
            had.add(a_)
            pin, s_ = S3.pin_for(a_, table), seen.get(a_)
            if not pin and not s_:
                continue
            if s_ and s_.get("cert") and "name_ok" not in s_["cert"]:
                s_["cert"]["name_ok"] = webtls.names_address(s_["cert"], webtls.host_of(a_))
            rows.append(dict(addr=a_, host=webtls.host_of(a_), pinned=pin, seen=s_))
        return rows

    def s3_seen_prune():

        cfg = VW.config()
        used = {S3.cert_address(cfg.get(p_)) for p_ in ("primary", "secondary", "gallery_host")} - {""}
        stale = [a_ for a_ in s3_seen() if a_ not in used]
        if not stale:
            return
        con = connect()
        try:
            con.executemany("DELETE FROM settings WHERE key=?", [(S3.SEEN_KEY + a_,) for a_ in stale])
            con.commit()
        finally:
            con.close()

    def s3_cert_action(step, f):

        from flask import g
        import webtls
        known = lambda a_: bool(a_) and any(r_["addr"] == a_ for r_ in s3_cert_rows(step))
        table = VW.config().get(S3.STORE) or {}
        if f.get("s3cert_check"):
            a_ = f["s3cert_check"][:300]
            if known(a_):
                S3.check(a_, table=table)
            g.s3cert_msg = "checked"
            return "Checked the S3 storage's certificate: it is shown below."
        if f.get("s3cert_refuse"):
            a_ = f["s3cert_refuse"][:300]
            rec = s3_seen().get(a_)
            if rec and known(a_):
                rec["refused"] = (rec.get("cert") or {}).get("sha256", "")
                put(**{S3.SEEN_KEY + a_: json.dumps(rec)})
                audit(f"Refused the S3 storage's new certificate at {a_} (SHA-256 {(rec.get('cert') or {}).get('fp', '')})")
            return "Kept the certificate you confirmed before. Backups there stay stopped until the server shows it again."
        a_, _, sha_ = (f.get("s3cert_trust") or "")[:400].strip().rpartition(" ")
        sha_ = sha_.lower()
        rec = s3_seen().get(a_) or {}
        stale = "The certificate changed again since it was shown. Check the one shown now."
        if not known(a_) or not sha_ or (rec.get("cert") or {}).get("sha256") != sha_:
            g.s3cert_msg = "stale"
            return stale
        now = webtls.peek(a_)
        if now is None or now["sha256"] != sha_:
            S3.check(a_, table=table)
            g.s3cert_msg = "stale"
            return stale
        public = webtls.publicly_trusted(a_)
        if not public and not webtls.names_address(now, webtls.host_of(a_)):
            g.s3cert_msg = "name"
            return "Not trusted: that certificate doesn't name the S3 storage's address."
        cfg = VW.config()
        certs = dict(cfg.get(S3.STORE) or {})
        if public:
            certs.pop(a_, None)
            said = f"The S3 storage's certificate at {a_} is publicly trusted now; the certificate confirmed before is forgotten"
        else:
            certs[a_] = webtls.stamp(now)
            said = f"Trusted the S3 storage's certificate at {a_} (SHA-256 {now.get('fp', '')}; its key {now.get('spki_fp', '')})"
        if certs:
            cfg[S3.STORE] = certs
        else:
            cfg.pop(S3.STORE, None)
        write_storage(cfg)
        con = connect()
        try:
            con.execute("DELETE FROM settings WHERE key=?", (S3.SEEN_KEY + a_,))
            con.commit()
        finally:
            con.close()
        from flask import after_this_request

        @after_this_request
        def _said(resp):
            audit(said)
            return resp
        g.s3cert_msg = "trusted"
        return None

    def host_caps():
        try:
            caps = json.loads(get("gallery_host_caps", "") or "{}")
        except ValueError:
            return None
        from plugins.gallery import fingerprint_of
        gh = VW.config().get("gallery_host")
        if not gh or caps.get("fp") != fingerprint_of(gh):
            return None
        return caps.get("now") or caps.get("setup")

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

        ep = request.endpoint or ""
        if ep in OPEN_DURING_SETUP or ep.startswith("setup_owner") or ep.startswith("login") or ep == "unlock_master":
            return None
        import datakey
        if not datakey.is_unlocked() or complete():
            return None
        from flask import session as fs
        if not fs.get("uid"):
            return None
        return redirect(url_for("setup_step", step=first_open()))

    app.before_request_funcs.setdefault(None, []).insert(1, gate)
    app.config.setdefault("quiet_endpoints", set()).add("setup_test")
    app.config.setdefault("ep_words", {})["setup_test"] = "Tested a storage connection"
    app.config["ep_words"]["setup_host_check"] = "Checked what the web host can do"

    EMBED_BACKS = ("/vault#where", "/settings?tab=profile#location", "/settings?tab=general#where-home")

    def nxt(step):
        i = SLUGS.index(step)
        if not complete() and i + 1 < len(SLUGS):
            return redirect(url_for("setup_step", step=SLUGS[i + 1]))
        return redirect(url_for("setup_step", step=step, saved=1) if complete() else url_for("setup_step", step=step))

    @app.route("/setup/<step>", methods=["GET", "POST"], endpoint="setup_step")
    def setup_step(step):
        if step not in SLUGS:
            abort(404)
        if request.method == "GET" and step == "business" and complete():

            return redirect(url_for("settings_view", tab="profile") + "#location")
        err = None
        f = request.form
        held = None
        if request.method == "POST" and step in STEP_PREFIXES and (
                f.get("s3cert_trust") or f.get("s3cert_refuse") or f.get("s3cert_check")):
            held = s3_cert_action(step, with_pending(f))
            if held:
                keep_secrets(f)
                err = held
        if request.method == "POST" and not held:
            skip = f.get("skip") and not dict((s, r) for s, _, r in STEPS)[step]
            if skip and (step != SLUGS[-1] or complete()):
                mark(step)
                return nxt(step)
            err = None if skip else save(step, with_pending(f) if step in STEP_PREFIXES else f)
            if err and step in STEP_PREFIXES:
                keep_secrets(f)
            if not err:
                if step in STEP_PREFIXES:
                    drop_secrets(STEP_PREFIXES[step])
                    s3_seen_prune()
                mark(step)
                if step == "galleries" and f.get("gallery_type") and any(
                        r_["seen"] and not r_["seen"].get("refused") for r_ in cert_rows()):

                    return redirect(url_for("setup_step", step="galleries", cert="check") + "#webcert")
                if step == SLUGS[-1] and not complete():
                    missing = [t for s, t, r in STEPS if r and s not in done()]
                    if missing:
                        err = "Still needed before you can start: " + ", ".join(missing) + "."
                    else:
                        put(setup_complete="1")
                        audit("Setup finished")
                        return redirect(url_for("home"))
                else:
                    back_ = (f.get("back") or "")
                    if complete() and back_ in EMBED_BACKS:
                        from flask import session as fs_
                        if back_.startswith("/vault"):
                            fs_["vault_result"] = [("Backup destinations", True, "Saved.")]
                        else:
                            fs_["notice"] = "Saved."
                        return redirect(back_)
                    return nxt(step)
        return render_template("setup.html", step=step, steps=STEPS, done=done(),
                               complete=complete(), err=err, required=dict((s, r) for s, _, r in STEPS)[step], saved=request.args.get("saved"), **context(step))

    @app.post("/setup/connection/<which>/test", endpoint="setup_conn_test")
    def setup_conn_test(which):

        from flask import session as fs
        part = {"primary": "primary", "secondary": "secondary", "gallery": "gallery_host"}.get(which) or abort(404)
        cfg = VW.config().get(part)
        if not cfg:
            abort(404)
        msg = test_backend(cfg, keep=False)
        conn_mark(which, not msg, msg)
        if which == "gallery" and not msg:
            check_host()
        fs["notice"] = f"Tested: it works." if not msg else f"The test failed: {msg}"
        if msg:
            fs["notice_refused"] = True
        back_ = request.form.get("back") or ""
        if not back_.startswith("/") or back_.startswith("//") or "\\" in back_:
            back_ = url_for("setup_step", step="galleries" if which == "gallery" else "storage")
        return redirect(back_)

    app.config.setdefault("ep_words", {})["setup_conn_test"] = "Tested a connection"

    def embed(step, back):

        return render_template("setup.html", step=step, steps=STEPS, done=done(), complete=complete(), err=None,
                               required=dict((s, r) for s, _, r in STEPS)[step], saved=None, embed=back, **context(step))

    app.config["setup_embed"] = embed

    @app.post("/setup/test/<which>", endpoint="setup_test")
    def setup_test(which):

        cfg = VW.config()
        old = cfg.get({"primary": "primary", "secondary": "secondary", "gallery": "gallery_host"}.get(which, ""), {})
        c = backend_from_form(request.form, which + "_", old)
        if unknown_host(c):
            why, got = first_contact(c)
            if why:
                return {"ok": False, "message": why}
            return {"ok": False, "host_key": got["host_key"], "fingerprint": got["fp"], "message": first_contact_message(c)}
        msg = test_backend(c, init_local=which in ("primary", "secondary", "gallery") and request.form.get(which + "_init") == "1",
                           keep=False)
        if not msg and which == "primary" and c.get("type") == "local" and same_disk(c.get("path", ""), str(VW.CFG_PATH.parent)):
            return {"ok": False, "message": "Works, but it's on the same disk as your data: one failure loses both."}
        return {"ok": not msg, "message": msg or "Connected: a test file was written, read back, and removed."}

    def finish_items():

        if not complete() or get("finish_hidden") == "1":
            return []
        con = connect()
        try:
            one = lambda sql: con.execute(sql).fetchone()[0]
            clients = one("SELECT COUNT(*) FROM households WHERE COALESCE(demo, 0)=0")
        finally:
            con.close()
        cfg = VW.config()
        items = []
        if not get("timezone"):
            items.append(("Set your time zone, so evening work gets today's date", url_for("settings_view", tab="profile") + "#tz"))
        if get("atrest_upgraded"):
            items.append(("Delete the unencrypted snapshots and copies made before the upgrade", url_for("security")))
        if get("doc_reviews") in (None, "", "{}"):
            items.append(("Your documents print as SAMPLE until approved: approve each where you use it, or read them all here",
                          url_for("settings_view", tab="documents")))

        if not get("business_email"):
            items.append(("Add your business email (clients' gallery picks are sent there)",
                          url_for("settings_view", tab="profile") + "#f-business_email"))
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

    def urgent_items():

        if not complete():
            return []
        items = []
        if get("atrest_upgraded"):
            items.append(("Delete the unencrypted snapshots and copies made before the upgrade", url_for("security")))
        if backups_share_disk(VW.config()):
            items.append(("Your backups are on the same disk as your data: move the vault or add a second location",
                          url_for("setup_step", step="storage")))
        return items

    app.config["urgent_items"] = urgent_items

    def tz_prompt():

        if not complete() or get("timezone"):
            return None
        import sys
        cur = getattr(sys.modules.get("app") or sys.modules.get("__main__"), "TZ", None)
        return dict(zones=timezones(), host=host_tz(), now=getattr(cur, "key", "UTC"))

    app.jinja_env.globals["tz_prompt"] = tz_prompt

    @app.post("/settings/timezone", endpoint="settings_timezone")
    def settings_timezone():

        from flask import g, session as fs
        tz = request.form.get("timezone", "") or request.form.get("browser_tz", "")
        if tz not in zoneinfo.available_timezones():
            g.refused = True
            fs["notice"] = "Pick your time zone from the list. Nothing was changed."
            return redirect(url_for("settings_view", tab="profile") + "#tz")
        put(timezone=tz)
        apply_settings()
        app.config["clock_rezone"](lambda k_, v_: put(**{k_: v_}))
        audit(f"Set the time zone to {tz}")
        fs["notice"] = f"Time zone set to {tz}. Documents already issued keep their dates."
        return redirect(url_for("settings_view", tab="profile"))

    @app.post("/setup/hide-list", endpoint="setup_hide_list")
    def setup_hide_list():
        put(finish_hidden="1")
        return redirect(url_for("home"))

    def save(step, f):
        if step == "business":
            names_ = "business_name" in f or not complete()
            if names_ and (not f.get("business_name", "").strip() or not f.get("photographer_name", "").strip()):
                return "Your business name and your own name, please."
            tz = f.get("timezone", "") or f.get("browser_tz", "")

            keep_unset_ = complete() and not get("timezone") and "timezone" not in f and "browser_tz" not in f
            if not keep_unset_ and tz not in zoneinfo.available_timezones():
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
            if names_:
                put(business_name=f["business_name"].strip(), photographer_name=f["photographer_name"].strip(),
                    studio_name=f.get("studio_name", "").strip(), business_email=f.get("business_email", "").strip(),
                    business_phone=f.get("business_phone", "").strip()[:40], business_address=f.get("business_address", "").strip())
            put(**({} if keep_unset_ else dict(timezone=tz)), home_zip=z, home_lat=lat, home_lon=lon)
            apply_settings()
            app.config["clock_rezone"](lambda k_, v_: put(**{k_: v_}))
            return None
        if step == "storage":
            cfg = VW.config()

            only = "secondary" if f.get("remove") == "secondary" else f.get("only", "") if complete() else ""
            if only not in ("", "primary", "secondary", "neither") or (only and not cfg.get("primary")):
                only = ""
            fresh = [w_ for w_ in ("primary", "secondary") if only in ("", w_)]
            prim = backend_from_form(f, "primary_", cfg.get("primary")) if "primary" in fresh else cfg.get("primary")
            if "secondary" in fresh:
                sec = (backend_from_form(f, "secondary_", cfg.get("secondary"))
                       if f.get("secondary_type") and f.get("remove") != "secondary" else None)
            else:
                sec = cfg.get("secondary")
            msg = offer_trust([(w_, p_, c_) for w_, p_, c_ in (("Primary location: ", "primary_", prim),
                                                                  ("Second location: ", "secondary_", sec))
                               if p_[:-1] in fresh])
            if msg:
                return msg
            seen = []
            if "primary" in fresh:
                msg = test_backend(prim, init_local=f.get("primary_init") == "1", seen=seen)
                if msg:
                    offer_changed_key("primary_", seen)
                    return "Primary location: " + msg
            cfg["primary"] = prim
            if sec:
                msg = test_backend(sec, init_local=f.get("secondary_init") == "1", seen=seen) if "secondary" in fresh else ""
                if msg:
                    offer_changed_key("secondary_", seen)
                    return "Second location: " + msg
                if f.get("same_disk_ok"):
                    cfg["same_disk_ok"] = True
                if (prim.get("type") == "local" and sec.get("type") == "local" and not cfg.get("same_disk_ok")
                        and same_disk(prim.get("path", ""), sec.get("path", ""))):
                    return ("SAME-DISK: The second copy is on the same disk as the first: one failed disk would take both. "
                            "Pick a folder on another disk, a drive you plug in, your NAS, or cloud storage. Or press Keep it anyway.")
                cfg["secondary"] = sec
            else:
                cfg.pop("secondary", None)
            if f.get("one_disk_ok"):
                cfg["one_disk_ok"] = True
            if backups_share_disk(cfg) and not cfg.get("one_disk_ok"):
                return ("ONE-DISK: Your backups would be on the same disk as your data. They protect against mistakes, but"
                        " not against that disk failing. Pick a folder on another disk or dataset, add a second copy below"
                        " (a USB drive or an online account), or press Continue anyway and add one later.")
            write_storage(cfg)
            for w_ in fresh:
                if cfg.get(w_):
                    conn_mark(w_, True)
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
            if not 1 <= term <= 365:
                return "Quotes are good for 1 to 365 days."
            if any(v < 0 for v in kv.values()) or rate < 0 or kv["EVENT_DEPOSIT_PCT"] > 100:
                return "Amounts can't be negative, and the event deposit is a percentage up to 100."
            methods = [m for m in PAYMENT_CHOICES if f.get("pay_" + m)] + \
                      [m.strip() for m in f.get("pay_other", "").split(",") if m.strip()]
            if not methods:
                return "Pick at least one way clients can pay."
            kv["PAYMENT_METHODS"] = methods
            kv["NET_TERMS_TYPES"] = [t for t in C.SESSION_TYPES if f.get("bal_" + t) == "after" or f.get("net_" + t)]
            kv["BEFORE_TYPES"] = [t for t in C.SESSION_TYPES if f.get("bal_" + t) == "before"]
            if "deposit_word" in f:
                w_ = f.get("deposit_word", "")
                if " ".join(w_.split()) and not C.clean_deposit_word(w_) and " ".join(w_.split()).lower() != "deposit":
                    return "Call the deposit: letters, spaces, and hyphens only, up to 30."
                put(deposit_word=C.clean_deposit_word(w_))
            save_rules(**kv)
            put(mileage_rate=rate, quote_term_days=term)
            for k_ in ("pay_details", "bank_details"):
                if k_ in f:
                    put(**{k_: f.get(k_, "")})
            return None
        if step == "tax":
            err_ = save_taxes_form(f, get, put)
            if err_:
                return err_
            _app().retax_after_taxes()
            if f.get("prints_shown"):
                put(tax_prints="1" if f.get("tax_prints") else "0", tax_print_shipping="1" if f.get("tax_print_shipping") else "0")
            return None
        if step == "shoot":
            chosen = [k for k in C.PRESETS if f.get("preset_" + k)]
            keep = [t for t in C.TYPES if f.get("keep_" + t["key"])]
            if not chosen and not keep and rules().get("TYPES"):
                return None
            have = {t["key"]: t for t in C.TYPES}
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
                    if starter[0] == 1 and not used:
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
            if f.get("keep_host") and complete():
                save_rules(**kv)
                return None
            if f.get("gallery_type"):
                gh = backend_from_form(f, "gallery_", cfg.get("gallery_host") or cfg.get("gallery_host_off"))
                from plugins.gallery import folder_names, studio_page_clash
                clash = studio_page_clash(gh, f.get("gallery_base_url", ""), in_use=folder_names(
                    cfg.get("gallery_host") or cfg.get("gallery_host_off"), get("gallery_base_url", "")))
                if clash:
                    return clash
                from plugins.gallery import NO_CODEWORD_CHECK, codewords_on
                if (gh or {}).get("type") == "s3" and codewords_on() and not f.get("s3_codewords_off"):

                    return NO_CODEWORD_CHECK + (" Codewords are on: turn codewords off for galleries here, or choose a"
                                                " host that can check codewords.")
                msg = offer_trust([("Web host: ", "gallery_", gh)])
                if msg:
                    return msg
                seen = []
                msg = test_backend(gh, init_local=f.get("gallery_init") == "1", seen=seen)
                if msg:
                    offer_changed_key("gallery_", seen)
                    return "Web host: " + msg
                base = f.get("gallery_base_url", "").strip()
                if not base.startswith("https://"):
                    return ("The web address of the gallery folder must start with https://. Clients' browsers open the"
                            " encrypted photos only over https, so the folder's web server needs a certificate.")

                from plugins.gallery import copy_of, copy_words, name_local_host, setup_host
                con_ = connect()
                try:
                    name_local_host(cfg, gh, con_)
                finally:
                    con_.close()
                twin = copy_of(cfg, gh)
                setup_host(cfg, gh)
                put(gallery_base_url=base.rstrip("/") + "/")
                import netsec
                warn_ = " ".join(w_ for w_ in (netsec.only_here_words(base), copy_words(twin)) if w_)
                if warn_:
                    from flask import session as fs_
                    fs_["notice"] = "Saved. " + warn_
                    fs_["notice_refused"] = "warn"
            else:
                from plugins.gallery import setup_off
                setup_off(cfg)
                put(gallery_base_url="")
            write_storage(cfg)
            save_rules(**kv)
            if f.get("gallery_type"):
                from plugins.gallery import restamp_local
                con_ = connect()
                try:
                    restamp_local(con_, cfg)
                    con_.commit()
                finally:
                    con_.close()
                conn_mark("gallery", True)
                check_host()
                from plugins.gallery import SITE_TOP_WORDS, at_site_top
                if at_site_top(cfg.get("gallery_host"), get("gallery_base_url", "")):
                    from flask import session as fs_
                    fs_["notice"] = (fs_.get("notice") or "Saved.") + " " + SITE_TOP_WORDS
                if f.get("s3_codewords_off") and (cfg.get("gallery_host") or {}).get("type") == "s3":
                    codewords_off()

            return None
        if step == "network":
            return None
        return None

    def g_s3msg():
        from flask import g
        return g.get("s3cert_msg", "")

    def context(step):
        cfg = VW.config()
        extra_ = dict(app_pub=app_ssh_key()[1], host_fp=B.host_key_fingerprint) if step in ("storage", "galleries") else {}
        if step == "shoot":
            preset_keys = {t["key"] for pr in C.PRESETS.values() for t in pr["types"]}
            have = {t["key"] for t in C.TYPES}
            chosen = [k for k, pr in C.PRESETS.items() if all(t["key"] in have for t in pr["types"])] \
                if "shoot" in done() or rules().get("TYPES") else []
            return dict(get=get, rules=C.current_rules(), types=C.SESSION_TYPES, presets=C.PRESETS, chosen=chosen, fv=form_value, fc=form_ticked,
                        custom=[t for t in C.TYPES if t["key"] not in preset_keys])
        if step == "tax":
            return dict(get=get, rules=C.current_rules(), types=C.SESSION_TYPES, taxes=_app().taxes_list(),
                        fv=form_value, fc=form_ticked)
        r = dict(C.current_rules())
        base = dict(get=get, rules=r, types=C.SESSION_TYPES, fv=form_value, fc=form_ticked, **extra_)

        base.update(primary_saved=cfg.get("primary"), secondary_saved=cfg.get("secondary"), gallery_saved=cfg.get("gallery_host"))
        if step in STEP_PREFIXES:
            base.update(trust=trust_boxes(STEP_PREFIXES[step]), pending=pending_secrets())
        if step == "business":
            base.update(zones=timezones(), tz=get("timezone", "") or host_tz(),
                        tz_saved=bool(get("timezone", "")))
        elif step == "storage":
            def is_marked(c):
                return c.get("type") == "local" and os.path.isfile(os.path.join(c.get("path", ""), B.LocalBackend.MARKER))
            import settings_io
            primary = dict(cfg.get("primary", {}))
            mounted = False
            if primary.get("type", "local") == "local" and not is_marked(primary) and (
                    os.path.isdir(VAULT_MOUNT) or settings_io._inside_private(primary.get("path", ""))):
                primary["path"] = suggested_vault()

                mounted = primary["path"] == VAULT_MOUNT
            secondary = cfg.get("secondary", {})
            if request.method == "POST":
                primary = backend_from_form(request.form, "primary_", primary) or primary
                secondary = backend_from_form(request.form, "secondary_", secondary) or {}
            base.update(s3_certs=s3_cert_rows(step), s3cert_msg=g_s3msg())
            base.update(marked={"primary_": is_marked(primary), "secondary_": is_marked(secondary)}, vault_mounted=mounted,
                        primary=primary, secondary=secondary,
                        btime=(request.form.get("backup_time") if request.method == "POST" else None) or get("backup_time", "02:40"),
                        data_dir=str(VW.CFG_PATH.parent))
        elif step == "money":
            base.update(pay_choices=PAYMENT_CHOICES)
        elif step == "galleries":
            gal = dict(cfg.get("gallery_host", {}))
            if request.method == "POST" and request.form.get("gallery_type"):
                gal = backend_from_form(request.form, "gallery_", gal) or gal
            base.update(s3_certs=s3_cert_rows(step), s3cert_msg=g_s3msg())
            from plugins.gallery import SITE_TOP_HELP, SITE_TOP_WORDS, WEB_TOP_NAMES, at_site_top
            base.update(site_top=dict(
                words=SITE_TOP_WORDS, help=SITE_TOP_HELP, names=sorted(WEB_TOP_NAMES),
                saved=at_site_top(cfg.get("gallery_host"), get("gallery_base_url", "")),
                typed=request.method == "POST" and bool(request.form.get("gallery_type")) and at_site_top(
                    gal, request.form.get("gallery_base_url", ""))))
            go_on =(url_for("setup_step", step=SLUGS[SLUGS.index("galleries") + 1])
                     if request.args.get("go") and not complete() and "galleries" in done() else "")
            from plugins.gallery import NO_CODEWORD_CHECK, codewords_off_confirm, codewords_on, codewords_waiting
            if codewords_on():
                con_ = connect()
                try:
                    base.update(cw_confirm=codewords_off_confirm(len(codewords_waiting(con_))))
                finally:
                    con_.close()
            base.update(gallery=gal, host_caps=host_caps(), no_cw_check=NO_CODEWORD_CHECK if codewords_on() else "", web_certs=cert_rows(), cert_msg=request.args.get("cert", ""),
                        go_on=go_on,
                        gallery_marked=gal.get("type") == "local" and
                        os.path.isfile(os.path.join(gal.get("path", ""), B.LocalBackend.MARKER)))
        elif step == "network":
            base.update(nets=[str(n) for n in allow_nets], me=request.remote_addr,
                        only=bool(os.environ.get("CRM_ALLOW_ONLY")), container=in_container())
        return base
