import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta

from flask import abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import datakey
import db as dbmod
import netsec

SCHEMA = """CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  pw_hash TEXT NOT NULL DEFAULT '', totp_secret TEXT NOT NULL, recovery TEXT NOT NULL DEFAULT '[]', created TEXT,
  failed INTEGER DEFAULT 0, locked_until TEXT);"""
MAX_FAILS, LOCK_MIN = 5, 15
IP_FAILS_PER_HOUR = 20
OPEN = {"static", "about", "login", "login_2fa", "setup_owner", "setup_owner_verify", "setup_owner_key", "unlock_master", "healthz"}
MIN_PASSWORD = 12
SESSIONS = {}
SETUP = {}
SETUP_TTL = 1800

def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")

def totp(secret, at=None, step=30, digits=6):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(code).zfill(digits)

def totp_ok(secret, code, at=None, window=1, after=None):

    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    now = at if at is not None else time.time()
    for d in range(-window, window + 1):
        step = int((now + d * 30) // 30)
        if (after is None or step > after) and hmac.compare_digest(totp(secret, now + d * 30), code):
            return step
    return None

def otpauth_uri(secret, email):
    from urllib.parse import quote
    return f"otpauth://totp/{quote('Lumen Aureum:' + email)}?secret={secret}&issuer=Lumen%20Aureum"

def new_recovery_codes(n=10):
    codes = ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(n)]
    return codes, [generate_password_hash(c) for c in codes]

def start_session(uid):
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = uid
    return sid

def end_sessions(uid, keep=None):

    for sid in [s_ for s_, u in SESSIONS.items() if u == uid and s_ != keep]:
        SESSIONS.pop(sid, None)

def init_schema(connect):
    con = connect()
    try:
        con.executescript(SCHEMA)
        if "totp_last" not in dbmod.columns(con, "users"):
            dbmod.add_column(con, "users", "totp_last", "INTEGER DEFAULT 0")
        con.commit()
    finally:
        con.close()

class Throttle:

    def __init__(self):
        self.ip = defaultdict(deque)
        self.until = {}
        self.log = deque(maxlen=500)

    MAX_TRACKED = 10000

    def _trim(self, ip):
        q = self.ip.get(ip)
        if q is None:
            return deque()
        while q and time.time() - q[0] > 3600:
            q.popleft()
        if not q:
            self.ip.pop(ip, None)
        return q

    def _sweep(self):
        now = time.time()
        for k in [k for k, t in self.until.items() if t < now]:
            del self.until[k]
        while len(self.ip) > self.MAX_TRACKED:
            del self.ip[next(iter(self.ip))]

    def ip_blocked(self, ip):
        return len(self._trim(ip)) >= IP_FAILS_PER_HOUR or time.time() < self.until.get(ip, 0)

    def wait_text(self, ip):

        now, q = time.time(), self._trim(ip)
        ends = [self.until.get(ip, 0)]
        if len(q) >= IP_FAILS_PER_HOUR:
            ends.append(q[len(q) - IP_FAILS_PER_HOUR] + 3600)
        mins = max(1, -(-int(max(ends) - now) // 60))
        return "in a minute" if mins == 1 else f"in {mins} minutes"

    def fail(self, ip, what="sign-in"):
        now = time.time()
        self._sweep()
        q = self._trim(ip)
        self.ip[ip] = q
        q.append(now)
        self.log.append((now, ip, what))
        if sum(1 for t in q if now - t < LOCK_MIN * 60) >= MAX_FAILS:
            self.until[ip] = now + LOCK_MIN * 60

    def ok(self, ip=None):
        if ip:
            self.ip.pop(ip, None)
            self.until.pop(ip, None)

    def take_report(self):
        items = list(self.log)
        self.log.clear()
        return items

MASTER_CHECK_DAYS, MASTER_FIRST_CHECK_DAYS, MASTER_LATER_DAYS = 90, 7, 7

def _day(v):
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None

def master_check_due(con, today):

    if not datakey.exists():
        return False
    get = lambda k: (con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or ("",))[0] or ""
    later = _day(get("master_check_later"))
    if later and later > today:
        return False
    last = _day(get("master_checked"))
    if last:
        return last + timedelta(days=MASTER_CHECK_DAYS) <= today
    made = _day(con.execute("SELECT MIN(created) FROM users").fetchone()[0])
    return not made or made + timedelta(days=MASTER_FIRST_CHECK_DAYS) <= today

def master_key_opens(secret):

    try:
        return hmac.compare_digest(datakey.unwrap_master(secret or ""), datakey.dk() or b"")
    except ValueError:
        return False

SCRYPT_SLOTS = __import__("threading").BoundedSemaphore(2)

class Busy(Exception):
    pass

def unwrap_password_limited(pw):

    if not SCRYPT_SLOTS.acquire(blocking=False):
        raise Busy()
    try:
        return datakey.unwrap_password(pw)
    finally:
        SCRYPT_SLOTS.release()

def register(app, connect, allowed, audit, on_unlock):
    app.secret_key = secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=True,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    th = Throttle()
    app.config["auth_throttle"] = th
    kth = Throttle()
    kth.log = th.log
    app.config["key_throttle"] = kth

    def has_owner():
        return datakey.exists()

    def sign_in(uid, remember=False, nxt=None):
        session.clear()
        session["uid"] = uid
        session["sid"] = start_session(uid)
        session.permanent = bool(remember)
        th.ok(request.remote_addr)
        report = th.take_report()
        if report:
            last = report[-1]
            when = datetime.fromtimestamp(last[0]).strftime("%b %-d, %-I:%M %p")
            text = (f"{len(report)} failed attempt{'s' if len(report) != 1 else ''} since the last sign-in "
                    f"(the last: {last[2]} from {last[1]}, {when}).")
            session["notice"] = text
            try:
                audit("Security: " + text)
            except Exception:
                pass
        return redirect(nxt or url_for("home"))

    def gate():

        ep = request.endpoint or ""
        if not netsec.host_ok(request.host):
            return "Unknown host name. Add it to CRM_HOSTNAMES if this is how you reach Lumen Aureum.\n", 400
        if netsec.refused_as_gateway(request.remote_addr):
            return ("This connection arrived through Docker's port proxy, which hides where it came from, so it "
                    "is refused. Connect from another device on your network, or list this address in CRM_ALLOW if "
                    "you mean to allow it.\n"), 403
        if not allowed(request.remote_addr) and not netsec.health_probe(request):
            abort(403)
        if not netsec.secure_request(request) and ep != "healthz":
            return "Lumen Aureum only answers over HTTPS. Use https:// in the address.\n", 403
        if ep in OPEN:
            return None
        if not has_owner():
            return redirect(url_for("setup_owner"))
        nxt = request.path if request.method == "GET" else None
        if not datakey.is_unlocked():
            session.pop("uid", None)
            return redirect(url_for("login", next=nxt))
        uid = session.get("uid")
        if uid and SESSIONS.get(session.get("sid")) != uid:
            session.clear()
            uid = None
        if uid:
            con = connect()
            try:
                if not con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                    session.clear()
                    uid = None
            finally:
                con.close()
        if not uid:
            return redirect(url_for("login", next=nxt))
        return None

    app.before_request_funcs.setdefault(None, []).insert(0, gate)

    @app.get("/healthz", endpoint="healthz")
    def healthz():
        return {"ok": True, "locked": not datakey.is_unlocked()}

    def setup_state():
        st = SETUP.get(session.get("setup_tok", ""))
        if st and time.time() - st["at"] > SETUP_TTL:
            SETUP.pop(session.get("setup_tok", ""), None)
            st = None
        return st

    @app.route("/setup/owner", methods=["GET", "POST"], endpoint="setup_owner")
    def setup_owner():
        if has_owner():
            abort(404)
        err = None
        if request.method == "POST":
            f = request.form
            name, email, pw = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
            if th.ip_blocked(request.remote_addr):
                err = "slow"
            elif not hmac.compare_digest(re.sub(r"[^0-9A-Z]", "", f.get("claim", "").upper()),
                                         re.sub(r"[^0-9A-Z]", "", netsec.claim_code())):
                th.fail(request.remote_addr, "claim code")
                err = "claim"
            elif not name or "@" not in email:
                err = "name"
            elif len(pw) < MIN_PASSWORD or pw != f.get("password2", ""):
                err = "password"
            else:

                key = secrets.token_bytes(32)
                tok = secrets.token_urlsafe(16)
                for t in [t for t, v in SETUP.items() if time.time() - v["at"] > SETUP_TTL]:
                    SETUP.pop(t, None)
                SETUP[tok] = dict(name=name, email=email, totp=new_totp_secret(), key=key,
                                  pw_wrap=datakey.password_wrap(key, pw), at=time.time())
                session.clear()
                session["setup_tok"] = tok
                return redirect(url_for("setup_owner_verify"))
        keep = {k: request.form.get(k, "")[:200] for k in ("claim", "name", "email")} if request.method == "POST" else {}
        return render_template("auth_setup_owner.html", err=err, min_pw=MIN_PASSWORD, blocked=th.ip_blocked(request.remote_addr),
                               keep=keep)

    @app.route("/setup/owner/verify", methods=["GET", "POST"], endpoint="setup_owner_verify")
    def setup_owner_verify():
        if has_owner():
            abort(404)
        st = setup_state()
        if not st:
            return redirect(url_for("setup_owner"))
        err = False
        if request.method == "POST":
            if totp_ok(st["totp"], request.form.get("code")):
                st["totp_ok"] = True
                return redirect(url_for("setup_owner_key"))
            err = True
        r = app.make_response(render_template("auth_setup_verify.html", qr=_qr(otpauth_uri(st["totp"], st["email"])),
                                              secret=st["totp"], err=err))
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.route("/setup/owner/key", methods=["GET", "POST"], endpoint="setup_owner_key")
    def setup_owner_key():

        if has_owner():
            if session.get("uid"):
                session["notice"] = ("Setup is finished. The recovery codes were shown once; if you didn't keep them, "
                                     "make new ones under Security.")
                return redirect(url_for("security"))
            abort(404)
        st = setup_state()
        if not st or not st.get("totp_ok"):
            return redirect(url_for("setup_owner"))
        from vault import keys as K
        if "master_pub" not in st:
            secret, st["master_pub"] = K.generate_master()
            st["tail"] = secret[-6:].upper()
            st["secret"] = secret
        if request.method == "POST":
            if request.form.get("tail", "").strip().upper() != st["tail"]:
                return _no_store(app, render_template("auth_setup_key.html", secret=st.get("secret"), err=True))
            try:
                datakey.create_from(st["key"], st["pw_wrap"], st["master_pub"])
            except FileExistsError:
                abort(404)
            datakey.set_unlocked(st["key"])
            on_unlock()
            codes, hashes = new_recovery_codes()
            con = connect()
            try:
                K.set_master_pub(con, st["master_pub"])
                con.execute("""INSERT INTO users (name, email, pw_hash, totp_secret, recovery, created) VALUES (?,?,?,?,?,?)""",
                            (st["name"], st["email"], "", st["totp"], json.dumps(hashes), datetime.now().isoformat(timespec="seconds")))
                con.commit()
                uid = con.execute("SELECT id FROM users WHERE email=?", (st["email"],)).fetchone()[0]
            finally:
                con.close()
            SETUP.pop(session.get("setup_tok", ""), None)
            netsec.forget_claim_code()
            sign_in(uid, remember=True)
            audit("Created the owner account and the master key")
            return _no_store(app, render_template("auth_recovery.html", codes=codes))

        return _no_store(app, render_template("auth_setup_key.html", secret=st.get("secret"), err=False))

    @app.route("/login", methods=["GET", "POST"], endpoint="login")
    def login():
        if not has_owner():
            return redirect(url_for("setup_owner"))
        err = None
        if request.method == "POST":
            ip = request.remote_addr
            datakey.drop_pending(session.get("pending_tok"))
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            if th.ip_blocked(ip):
                err = "slow"
            else:
                try:
                    key = unwrap_password_limited(pw)
                except Busy:
                    th.fail(ip, "password")
                    key, err = None, "busy"
                except datakey.TooSlow:
                    key, err = None, "tooslow"
                except ValueError:
                    key, err = None, "tamper"
                if key and not _owner_email_ok(key, email):
                    key = None
                if key:
                    return _to_2fa(datakey.hold_pending(key, dict(email=email)))
                if not err:
                    th.fail(ip, "password")
                    err = "bad"
        return render_template("auth_login.html", err=err, next=request.args.get("next", ""),
                               locked=not datakey.is_unlocked(), auto_refused=datakey.auto_refused())

    def _owner_email_ok(key, email):
        try:
            con = dbmod.connect(key=key)
            try:
                return con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone() is not None
            finally:
                con.close()
        except Exception:
            return True

    def _to_2fa(tok):
        nxt, remember = _safe_next(request.form.get("next")), bool(request.form.get("remember"))
        session.clear()
        session["pending_tok"] = tok
        session["next"] = nxt
        session["remember"] = remember
        return redirect(url_for("login_2fa"))

    def _check_code(u, code, set_recovery, last=None, set_last=None):

        step = totp_ok(u[1], code, after=last)
        if step:
            if set_last:
                set_last(step)
            return True
        hashes = json.loads(u[2] or "[]")
        for i, h in enumerate(hashes):
            if check_password_hash(h, code.lower()):
                hashes.pop(i)
                set_recovery(hashes)
                return True
        return False

    @app.route("/login/2fa", methods=["GET", "POST"], endpoint="login_2fa")
    def login_2fa():
        tok = session.get("pending_tok")
        held = datakey.take_pending(tok)
        if not held:
            session.clear()
            return redirect(url_for("login"))
        key, ctx = held
        err = False
        if request.method == "POST":
            code = request.form.get("code", "").strip()
            ip = request.remote_addr
            if th.ip_blocked(ip):
                datakey.drop_pending(tok)
                session.clear()
                return redirect(url_for("login"))
            con = dbmod.connect(key=key)
            try:
                cols = dbmod.columns(con, "users")
                u = con.execute("SELECT id, totp_secret, recovery, totp_last FROM users WHERE email=?" if "totp_last" in cols
                                else "SELECT id, totp_secret, recovery, 0 FROM users WHERE email=?", (ctx.get("email", ""),)).fetchone()

                def set_rec(h):
                    con.execute("UPDATE users SET recovery=? WHERE id=?", (json.dumps(h), u[0]))
                    con.commit()

                def set_last(st):
                    if "totp_last" in cols:
                        con.execute("UPDATE users SET totp_last=? WHERE id=?", (st, u[0]))
                        con.commit()
                ok = bool(u) and _check_code(u, code, set_rec, u[3] or 0, set_last)
            finally:
                con.close()
            if ok:
                datakey.drop_pending(tok)
                if not datakey.is_unlocked():
                    datakey.set_unlocked(key)
                    on_unlock()
                nxt, remember = session.get("next"), session.get("remember")
                return sign_in(u[0], remember, nxt)
            th.fail(ip, "two-factor code")
            err = True
        return render_template("auth_2fa.html", err=err)

    @app.post("/logout", endpoint="logout")
    def logout():
        datakey.drop_pending(session.get("pending_tok"))
        SESSIONS.pop(session.get("sid"), None)
        session.clear()
        return redirect(url_for("login"))

    @app.route("/unlock/master", methods=["GET", "POST"], endpoint="unlock_master")
    def unlock_master():
        if not datakey.exists():
            return redirect(url_for("login"))
        err = None
        netsec.recovery_code()
        if request.method == "POST":
            f = request.form
            pw = f.get("password", "")
            if th.ip_blocked(request.remote_addr):
                err = "slow"
            elif len(pw) < MIN_PASSWORD or pw != f.get("password2", ""):
                err = "password"
            elif not netsec.recovery_ok(f.get("code", "")):
                th.fail(request.remote_addr, "recovery code")
                err = "code"
            else:
                try:
                    key = datakey.unwrap_master(f.get("secret", ""))
                except ValueError as e:
                    th.fail(request.remote_addr, "master key")
                    err, key = str(e), None
                if key:
                    netsec.forget_recovery_code()
                    datakey.rewrap_password(pw, key)
                    datakey.drop_all_pending()
                    for u_ in list(set(SESSIONS.values())):
                        end_sessions(u_)
                    if not datakey.is_unlocked():
                        datakey.set_unlocked(key)
                        on_unlock()
                    con = connect()
                    try:
                        uid = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
                    finally:
                        con.close()
                    r = sign_in(uid)
                    audit("Unlocked with the master key, set a new password and signed out every other device")
                    return r
        return _no_store(app, render_template("auth_master_unlock.html", err=err, min_pw=MIN_PASSWORD))

    def _put(con, k, v):
        con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

    def _today():
        return (app.config.get("today") or date.today)()

    @app.route("/security/master-key", methods=["GET", "POST"], endpoint="master_key_check")
    def master_key_check():

        result = None
        if request.method == "POST":
            ip_ = request.remote_addr or ""
            if kth.ip_blocked(ip_):
                result = "slow"
            elif master_key_opens(request.form.get("secret", "")):
                con = connect()
                try:
                    _put(con, "master_checked", _today().isoformat())
                    con.execute("DELETE FROM settings WHERE key='master_check_later'")
                    con.commit()
                finally:
                    con.close()
                audit("Checked the master key")
                result = "ok"
            else:
                kth.fail(ip_, "master key check")
                result = "slow" if kth.ip_blocked(ip_) else "wrong"
        con = connect()
        try:
            last = (con.execute("SELECT value FROM settings WHERE key='master_checked'").fetchone() or ("",))[0]
        finally:
            con.close()
        wait = kth.wait_text(request.remote_addr or "") if result == "slow" else ""
        return _no_store(app, render_template("master_key_check.html", result=result, last=_day(last), wait=wait))

    @app.post("/security/master-key/later", endpoint="master_key_later")
    def master_key_later():
        con = connect()
        try:
            _put(con, "master_check_later", (_today() + timedelta(days=MASTER_LATER_DAYS)).isoformat())
            con.commit()
        finally:
            con.close()
        return redirect(url_for("home"))

    @app.route("/security", methods=["GET", "POST"], endpoint="security")
    def security():
        msg = err = None
        codes = None
        if request.method == "POST":
            f = request.form
            cur = f.get("current", "")
            act = f.get("act")
            busy = False
            ip_ = request.remote_addr or ""
            if th.ip_blocked(ip_):
                ok, blocked_ = False, True
            else:
                blocked_ = False
                try:
                    ok = hmac.compare_digest(unwrap_password_limited(cur) or b"", datakey.dk() or b"")
                except Busy:
                    ok, busy = False, True
                except ValueError:
                    ok = False
                if not ok and not busy:
                    th.fail(ip_, "current password on Security")
            if not ok:
                err = ("Too many wrong passwords. Try again later." if blocked_ else
                       "Busy checking other sign-ins. Try again in a moment." if busy else "Your current password, please.")
            elif act == "password":
                pw = f.get("password", "")
                if len(pw) < MIN_PASSWORD or pw != f.get("password2", ""):
                    err = f"New passwords must match and be at least {MIN_PASSWORD} characters."
                else:
                    datakey.rewrap_password(pw)
                    datakey.drop_all_pending()
                    end_sessions(session["uid"], keep=session.get("sid"))
                    audit("Changed the password (other devices signed out)")
                    msg = "Password changed. Other devices are signed out."
            elif act == "codes":
                codes, hashes = new_recovery_codes()
                con = connect()
                try:
                    con.execute("UPDATE users SET recovery=? WHERE id=?", (json.dumps(hashes), session["uid"]))
                    con.commit()
                finally:
                    con.close()
                datakey.drop_all_pending()
                end_sessions(session["uid"], keep=session.get("sid"))
                audit("Made new recovery codes (the old ones no longer work; other devices signed out)")
            elif act == "others":
                datakey.drop_all_pending()
                end_sessions(session["uid"], keep=session.get("sid"))
                audit("Signed out every other device")
                msg = "Every other device is signed out."
            elif act == "auto_on":
                try:
                    datakey.enable_auto()
                    audit("Turned on unattended unlock")
                    msg = "Unattended unlock is on."
                except ValueError as e:
                    err = str(e)
            elif act == "auto_off":
                datakey.disable_auto()
                audit("Turned off unattended unlock")
                msg = "Unattended unlock is off."
        con = connect()
        try:
            left = len(json.loads(con.execute("SELECT recovery FROM users WHERE id=?", (session["uid"],)).fetchone()[0] or "[]"))
        finally:
            con.close()
        return _no_store(app, render_template("auth_security.html", msg=msg, err=err, codes=codes, left=left,
                                              auto=datakey.auto_enabled(), auto_file=datakey.auto_file(),
                                              auto_refused=datakey.auto_refused(),
                                              min_pw=MIN_PASSWORD, pw_work=datakey.password_work(),
                                              std_work=datakey.STANDARD_WORK))

def _no_store(app, html):
    r = app.make_response(html)
    r.headers["Cache-Control"] = "no-store"
    r.headers["Referrer-Policy"] = "same-origin"
    return r

def _safe_next(n):

    from urllib.parse import urlsplit
    if not n or not n.startswith("/") or any(ord(c) < 33 or c == "\\" for c in n[:3]) or n.startswith("//"):
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in n) or "\\" in n:
        return None
    u = urlsplit(n)
    return n if not u.scheme and not u.netloc else None

def _qr(data):
    import segno
    return segno.make(data, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff", omitsize=True)
