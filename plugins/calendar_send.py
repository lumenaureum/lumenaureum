from contextlib import contextmanager
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import abort, redirect, render_template_string, request, session, url_for

NAME = "calendar_send"
JOB = "calendar-send"
K_KIND = "calsend_kind"
K_SALT = "calsend_salt"

K_STATUS = "calsend_status"
K_DIGEST = "calsend_digest"
K_FULL = "calsend_full_at"
K_TEST = "calsend_test"
K_STOPPING = "calsend_stopping"

FULL_EVERY = 6 * 3600
LOOP_EVERY = 15 * 60
TEST_KEEP = 20 * 60
BACKOFF_FIRST = 5 * 60
BACKOFF_MAX = 3600
BACKOFF_SIGNIN = 6 * 3600
RETRY_AFTER_MAX = 24 * 3600
FAILING_AFTER = 3600
PENDING_TTL = 30 * 60
SEAL_LABEL = "calendar-send"

MS_CLIENT_ID = ""
CALDAV_SERVERS = {"icloud": ("iCloud", "https://caldav.icloud.com/"), "fastmail": ("Fastmail", "https://caldav.fastmail.com/"),
                  "yahoo": ("Yahoo", "https://caldav.calendar.yahoo.com/"), "other": ("Another service", "")}

PAGE = """{% extends "base.html" %}{% import "_ui.html" as ui with context %}{% import "_settings_ui.html" as sui with context %}{% from "_macros.html" import help with context %}
{% block title %}Send to your calendar - Lumen Aureum{% endblock %}
{% block body %}
{{ sui.head('Send to your calendar') }}
{% if v.ready %}
{% call sui.section('Sending to ' ~ v.service, id='calsend') %}
  <div class="sui-conn" id="calsend-conn">
    <div class="sui-conn-head"><b>{{ v.calendar }}</b>
    {%- if v.sending %}<span class="sui-conn-state ui-mute">Sending now</span><span hidden data-poll="{{ url_for('jobs_state', key=v.job) }}"></span>
    {%- elif v.status and v.status.ok %}<span class="sui-conn-state sui-ok"><span class="ms" aria-hidden="true">check_circle</span>Last sent {{ v.status.when }}</span>
    {%- elif v.status %}<span class="sui-conn-state sui-warn"><span class="ms" aria-hidden="true">warning</span>{{ v.status.words }} ({{ v.status.when }})</span>
    {%- else %}<span class="sui-conn-state ui-mute">Sending for the first time</span>{% endif %}</div>
    <dl class="sui-conn-facts">{% for k_, v_ in v.rows %}<div><dt>{{ k_ }}</dt><dd>{{ v_ }}</dd></div>{% endfor %}</dl>
    {% if v.status and v.status.retry and not v.sending %}<p class="sui-hint">Your studio tries again by itself at {{ v.status.retry }}. Send now tries at once.</p>{% endif %}
    {% if v.stopping %}<p class="sui-hint sui-conn-warn">Taking the events it sent out of your calendar. This page shows when that is done.</p>{% endif %}
    <div class="ui-btnrow">
      {{ ui.action(url_for('calendar_send_test'), 'Send a test event', inplace=false) }}
      {{ ui.action(url_for('calendar_send_now'), 'Send now', inplace=false) }}
    </div>
  </div>
  <p class="sui-hint">Each session shows as its type, day, start time, and length, and nothing else. A penciled date shows as tentative. An inquiry shows only once you pencil its date.{{ help("Never a client's name, a place, a note, an amount, or an email. Entries show the session type's name, so keep type names free of client names. Sessions from 90 days ago on are sent; older ones already in your calendar stay as they are. Changes go out within a few minutes.") }}</p>
{% endcall %}
{% call sui.danger_zone('Stop sending', id='calsend-off') %}
  <p class="sui-tip">Stopping deletes the saved sign-in from your studio. You choose whether the events it sent stay in your calendar.</p>
  <div class="ui-btnrow">
    {{ ui.action(url_for('calendar_send_off'), 'Stop and remove its events', fields={'remove': '1'}, inplace=false, confirm="Stop sending, and take the events your studio sent out of " ~ v.calendar ~ "? Events you added yourself stay.") }}
    {{ ui.action(url_for('calendar_send_off'), 'Stop and leave its events', fields={'remove': '0'}, inplace=false, confirm="Stop sending? The events already in " ~ v.calendar ~ " stay there and no longer change.") }}
  </div>
{% endcall %}

{% elif v.pending and v.pending.code %}
{% call sui.section('Sign in to Microsoft', id='calsend') %}
  <ol class="sui-steps">
    <li>Open <a href="{{ v.pending.uri }}" target="_blank" rel="noopener noreferrer">{{ v.pending.uri }}</a> on any device.</li>
    <li>Enter this code: <b id="calsend-code" style="font-size:1.3em;letter-spacing:.08em">{{ v.pending.code }}</b>
      <button type="button" class="ui-btn ui-btn-quiet" data-copy="#calsend-code">Copy the code</button></li>
    <li>Sign in with the Microsoft account whose calendar you want, and allow access.</li>
    <li>Come back here and press the button below.</li>
  </ol>
  <p class="sui-hint">The code works for 15 minutes.</p>
  <div class="ui-btnrow">{{ ui.action(url_for('calendar_send_outlook_finish'), "I've signed in", kind='primary', inplace=false) }}
    {{ ui.action(url_for('calendar_send_cancel'), 'Cancel', inplace=false) }}</div>
{% endcall %}

{% elif v.pending and v.pending.calendars %}
{% call sui.section('Pick the calendar', id='calsend') %}
  <p class="sui-tip">Signed in{% if v.pending.account %} as {{ v.pending.account }}{% endif %}. Which calendar should your sessions go to?{{ help("A calendar of their own is best: your studio adds, changes, and removes only the events it sent, but a calendar kept for sessions keeps them apart from everything else.") }}</p>
  <form method="post" action="{{ url_for('calendar_send_pick') }}">
    {% for cid_, name_ in v.pending.calendars %}<label class="ui-check"><input type="radio" name="pick" value="{{ loop.index0 }}"{{ ' checked' if loop.first }}>{{ name_ }}</label>{% endfor %}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Send sessions to this calendar</button></div>
  </form>
  <div class="ui-btnrow">{{ ui.action(url_for('calendar_send_cancel'), 'Cancel', inplace=false) }}</div>
{% endcall %}

{% else %}
{% call sui.section('Your sessions in your own calendar', id='calsend') %}
  <p class="sui-tip">Your studio can add each session to a calendar you already use, and keep it up to date when a session moves or is canceled. Pick where your calendar is.</p>
  <p class="sui-hint">Each session shows as its type, day, start time, and length, and nothing else. With this on, your studio connects to the calendar service you choose. To keep your sessions at home instead, use the <a href="{{ url_for('settings_view', tab='calendar') }}">calendar link</a>.</p>
  <div class="ui-btnrow" role="group" aria-label="Where your calendar is">
    {% for k_, w_ in [('google', 'Google Calendar'), ('outlook', 'Outlook or Microsoft 365'), ('caldav', 'iCloud and others')] %}
    {{ ui.button_link(url_for('calendar_send_settings', with=k_) ~ '#calsend-setup', w_, kind='primary' if v.pick == k_ else 'quiet') }}{% endfor %}
  </div>
{% endcall %}

{% if v.pick == 'google' %}
{% call sui.section('Google Calendar', id='calsend-setup') %}
  <p class="sui-tip">Google lets a studio add events through a "service account": a helper account that can reach only the calendars you share with it. Setting it up takes about ten minutes, once.</p>
  <ol class="sui-steps">
    <li>Open <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">console.cloud.google.com</a>, sign in, and make a new project. Any name works.</li>
    <li>In the project, open APIs and services, Library. Find Google Calendar API and press Enable.</li>
    <li>Open IAM and admin, Service accounts, and press Create service account. Give it a name. It needs no roles: skip those steps.</li>
    <li>Open the new service account, then Keys, Add key, Create new key, JSON. A .json file downloads. Keep it private: it is the account's password.</li>
    <li>In Google Calendar, make a new calendar for your sessions. Open its Settings and sharing. Under Share with specific people, add the service account's email (it ends in iam.gserviceaccount.com) and choose Make changes to events.</li>
    <li>On the same page, under Integrate calendar, copy the Calendar ID.</li>
  </ol>
  <form method="post" action="{{ url_for('calendar_send_google') }}" enctype="multipart/form-data">
    <div><span class="ui-label">The key file</span>{{ ui.file_pick('key', 'the .json key file', accept='.json,application/json', required=true, id='calsend-key') }}</div>
    {{ sui.field('calendar', 'Calendar ID', v.typed.get('calendar', ''), attrs='required maxlength="300" autocomplete="off"', hint="From the calendar's Settings and sharing page, under Integrate calendar.") }}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Check and start sending</button></div>
  </form>
  <p class="sui-hint">The service account reaches only calendars shared with it, so sharing just this one calendar keeps the rest of your Google account out of reach.</p>
{% endcall %}

{% elif v.pick == 'outlook' %}
{% call sui.section('Outlook or Microsoft 365', id='calsend-setup') %}
  {% if v.ms_builtin %}
  <p class="sui-tip">You sign in to Microsoft in your browser with a code your studio shows you. Your password never comes to the studio.</p>
  <form method="post" action="{{ url_for('calendar_send_outlook_start') }}">
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Sign in to Microsoft</button></div>
    {% call ui.fold('Use your own app registration', id='calsend-own') %}
    {{ sui.field('client_id', 'Application (client) ID', v.typed.get('client_id', ''), attrs='maxlength="36" autocomplete="off"', hint='Only if your organization asks you to use its own. Leave it empty otherwise.') }}
    {% endcall %}
  </form>
  {% else %}
  <p class="sui-tip">Microsoft asks each app that writes to an Outlook calendar to be registered. Registering one for your studio takes about ten minutes, once. Then you sign in with a code, and your password never comes to the studio.</p>
  <ol class="sui-steps">
    <li>Open <a href="https://entra.microsoft.com/" target="_blank" rel="noopener noreferrer">entra.microsoft.com</a> and sign in. A work or school Microsoft 365 account can register apps there. For a personal Outlook.com account, Microsoft first asks you to make a free Azure account.</li>
    <li>Open App registrations, New registration. Name it after your studio. Under Supported account types, choose the one that includes personal Microsoft accounts. Leave the redirect address empty and press Register.</li>
    <li>Open Authentication, and under Advanced settings turn on Allow public client flows. Save.</li>
    <li>Open API permissions, Add a permission, Microsoft Graph, Delegated permissions. Tick Calendars.ReadWrite and offline_access, and add them.</li>
    <li>Open Overview and copy the Application (client) ID.</li>
  </ol>
  <form method="post" action="{{ url_for('calendar_send_outlook_start') }}">
    {{ sui.field('client_id', 'Application (client) ID', v.typed.get('client_id', ''), attrs='required maxlength="36" autocomplete="off"') }}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Sign in to Microsoft</button></div>
  </form>
  {% endif %}
  <p class="sui-hint">Microsoft has no permission for a single calendar: signing in lets the studio reach every calendar of that account. Your studio only ever writes to the one you pick, and only its own events.</p>
{% endcall %}

{% elif v.pick == 'caldav' %}
{% call sui.section('iCloud and other calendar services', id='calsend-setup') %}
  <p class="sui-tip">Most calendar services take events from other apps through CalDAV, with your user name and an app password made for your studio.{{ help("An app password is a separate password made for one app, in your account's security settings. You can revoke it there at any time, without changing your own password.") }}</p>
  <form method="post" action="{{ url_for('calendar_send_caldav') }}">
    {{ sui.field('service', 'Your calendar service', v.typed.get('service', 'icloud'), type='select', options=v.servers) }}
    {{ sui.field('server', 'Server address (another service only)', v.typed.get('server', ''), type='url', attrs='maxlength="500" autocomplete="off" placeholder="https://"', hint="Your service's help pages give its CalDAV address. For Nextcloud it ends in /remote.php/dav.") }}
    {{ sui.field('user', 'User name', v.typed.get('user', ''), attrs='required maxlength="200" autocomplete="off"', hint='For iCloud, your Apple Account email. For Fastmail and Yahoo, your email address.') }}
    {{ sui.field('password', 'App password', '', type='password', attrs='required maxlength="300" autocomplete="new-password"') }}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Find my calendars</button></div>
  </form>
  <ul class="sui-hint">
    <li>iCloud: make the app password at account.apple.com, Sign-In and Security, App-Specific Passwords. It reaches every calendar of that Apple Account, and its other iCloud data too, so make a calendar just for sessions and pick that one.</li>
    <li>Fastmail: Settings, Privacy and security, Manage app passwords. Give it access to Calendars (CalDAV) only.</li>
    <li>Yahoo: Account security, Generate app password.</li>
    <li>Proton Calendar can't be reached by any other app, so it can't be used here.</li>
  </ul>
{% endcall %}
{% endif %}
{% endif %}
{% endblock %}"""

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    import calsend as S
    ctx.migrate("""
        CREATE TABLE IF NOT EXISTS calsend_secret (name TEXT PRIMARY KEY, value BLOB NOT NULL, at REAL);
        CREATE TABLE IF NOT EXISTS calsend_events (ident TEXT PRIMARY KEY, rid TEXT NOT NULL DEFAULT '',
            etag TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL DEFAULT '', day TEXT NOT NULL DEFAULT '');""")

    def before_off():
        if not ctx.setting(K_KIND):
            return ""
        return ("Your sessions stop going to your calendar, and the saved sign-in is deleted. The events already "
                "there stay. To take them out too, use Stop and remove its events on Send to your calendar first.")

    def when_on():
        return "" if ctx.setting(K_KIND) else "Choose the calendar your sessions go to."

    ctx.switchable(default=False, label="Send sessions to your calendar",
                   about="Adds each session to a calendar you use, at Google, Outlook, iCloud, or another calendar "
                         "service, and keeps it up to date. Each shows as its type, day, start time, and length, and "
                         "nothing else. Off: nothing is sent.",
                   before_off=before_off, when_on=when_on, links=[("Send to your calendar", "calendar_send_settings")],
                   on_off=lambda: leave())
    app.jinja_env.globals["SETTINGS_ENDPOINTS"].add("calendar_send_settings")
    words = app.config.setdefault("ep_words", {})
    quiet = app.config.setdefault("quiet_endpoints", set())
    run = dict(again=False, force=False)

    def A():
        return __import__("app")

    def refuse(text, back=None, typed=None):

        from flask import g
        session["notice"] = text
        if typed:
            session["calsend_typed"] = {k: str(v)[:500] for k, v in typed.items() if v}
        g.refused = True
        return redirect(back or url_for("calendar_send_settings"))

    @contextmanager
    def talking():

        from flask import g
        import locks
        held, tx = g.pop("write_lock", None), g.pop("tx", None)
        if held:
            if "db" in g:
                g.db.commit()
            locks.WRITES.release()
        try:
            yield
        finally:
            if held:
                if not locks.WRITES.acquire(timeout=45):
                    from werkzeug.exceptions import ServiceUnavailable
                    raise ServiceUnavailable("Busy finishing another change. Try again in a moment.")
                g.write_lock = True
                if tx:
                    g.tx = True

    def here():

        return redirect(url_for("calendar_send_settings"))

    def seal(obj):
        import datakey
        return datakey.seal_bytes(json.dumps(obj).encode(), NAME, label=SEAL_LABEL)

    def unseal(blob):
        import datakey
        try:
            return json.loads(datakey.open_bytes(bytes(blob), NAME, label=SEAL_LABEL))
        except (ValueError, TypeError):
            return None

    def secret_get(name):
        r = q("SELECT value, at FROM calsend_secret WHERE name=?", (name,), one=True)
        if not r:
            return None
        v = unseal(r["value"])
        if v is not None and name == "pending" and time.time() - (r["at"] or 0) > PENDING_TTL:
            return None
        return v

    def secret_put(name, obj):
        x("""INSERT INTO calsend_secret (name, value, at) VALUES (?,?,?)
             ON CONFLICT(name) DO UPDATE SET value=excluded.value, at=excluded.at""", (name, seal(obj), time.time()))

    def drop_stale_pending():

        r = q("SELECT at FROM calsend_secret WHERE name='pending'", one=True)
        if r and time.time() - (r["at"] or 0) > PENDING_TTL:
            x("DELETE FROM calsend_secret WHERE name='pending'")
            return True
        return False

    def secret_drop(name=None):
        if name:
            x("DELETE FROM calsend_secret WHERE name=?", (name,))
        else:
            x("DELETE FROM calsend_secret")

    def salt():
        s_ = ctx.setting(K_SALT)
        if not s_:
            s_ = S.new_salt()
            put_setting(K_SALT, s_)
        return s_

    def put_setting(k, v):
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

    def del_setting(*keys):
        for k in keys:
            x("DELETE FROM settings WHERE key=?", (k,))

    def forget():

        secret_drop()
        x("DELETE FROM calsend_events")
        del_setting(K_KIND, K_STATUS, K_DIGEST, K_FULL, K_TEST, K_STOPPING)

    def test_record():
        try:
            t = json.loads(ctx.setting(K_TEST) or "{}")
        except ValueError:
            t = {}
        return t if isinstance(t, dict) else {}

    def leave():

        sec, t = secret_get("conn"), test_record()
        forget()
        if not sec or not t.get("ident"):
            return
        mark = S.studio_mark(salt())

        def take_out(job):
            try:
                _, cal = S.connect(sec)
                rid = t.get("rid")
                if not rid:
                    rid = (cal.list_ours(since(), mark).get(t["ident"]) or {}).get("rid")
                if rid:
                    cal.delete(rid)
            except S.SendError as e:
                raise RuntimeError(f"The test event couldn't be taken out of your calendar ({e.words}). Delete "
                                   f"\"{S.TEST_SUMMARY}\" there yourself.") from None
            return "Took the test event out of your calendar."
        import jobs
        jobs.start(app, JOB + "-test", "Taking the test event out of your calendar", take_out, quiet=True)

    def ready():
        return ctx.active() and bool(ctx.setting(K_KIND))

    def desired():

        import calfeed
        s_ = salt()
        return {S.ident(s_, e["key"]): calfeed.public(e) for e in ctx.calendar_entries()}

    def since():
        import calfeed
        d_ = A().today() - timedelta(days=calfeed.PAST_DAYS)
        return datetime(d_.year, d_.month, d_.day, tzinfo=A().TZ)

    def state_load():
        return {r["ident"]: dict(rid=r["rid"], etag=r["etag"], digest=r["digest"], day=r["day"])
                for r in q("SELECT * FROM calsend_events")}

    def state_save(st):
        x("DELETE FROM calsend_events")
        for i, v in st.items():
            x("INSERT INTO calsend_events (ident, rid, etag, digest, day) VALUES (?,?,?,?,?)",
              (i, v.get("rid") or "", v.get("etag") or "", v.get("digest") or "", v.get("day") or ""))

    def status():
        try:
            v = json.loads(ctx.setting(K_STATUS) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def say(ok, words, n=None, err=None):

        was = status()
        now = time.time()
        v = dict(at=now, ok=ok, words=words, n=n if n is not None else was.get("n"),
                 failing_since=None if ok else (was.get("failing_since") or now))
        if not ok:
            fails = int(was.get("fails") or 0) + 1
            wait = min(BACKOFF_FIRST * 2 ** (fails - 1), BACKOFF_MAX)
            if err is not None and err.signin:
                wait = max(wait, BACKOFF_SIGNIN)
            asked = min(int(err.retry_after), RETRY_AFTER_MAX) if err is not None and err.retry_after else 0
            v.update(fails=fails, next_at=now + max(wait, asked), asked_until=(now + asked) if asked else None)
        put_setting(K_STATUS, json.dumps(v))

    def failing():
        v = status()
        return bool(ready() and not v.get("ok", True) and v.get("failing_since")
                    and time.time() - v["failing_since"] >= FAILING_AFTER)

    app.config["calendar_send_failing"] = failing

    def service(kind, sec=None):

        if kind == "caldav":
            sec = sec if sec is not None else (secret_get("conn") or {})
            if sec.get("service_name"):
                return sec["service_name"]
        return S.PROVIDERS.get(kind, "your calendar service")

    def store_rotated(new_secret):

        import locks
        cur = secret_get("conn") or {}
        with locks.WRITES:
            secret_put("conn", dict(cur, **new_secret))

    def open_calendar():
        sec = secret_get("conn")
        if not sec:
            raise S.SendError("the saved sign-in can't be read on this install: set it up again", signin=True)
        return S.connect(sec, on_refresh=store_rotated)

    def send_once(force=False):

        import locks
        with locks.WRITES:
            pass
        if not ready() or ctx.setting(K_STOPPING):
            return "Nothing to send."
        want = desired()
        fp = S.digest({k: S.digest(v) for k, v in want.items()})
        full_at = float(ctx.setting(K_FULL) or 0)
        keep, test_due = set(), False
        try:
            t = json.loads(ctx.setting(K_TEST) or "{}")
            if t.get("ident") and time.time() - float(t.get("at") or 0) < TEST_KEEP:
                keep.add(t["ident"])
            test_due = bool(t.get("ident")) and not keep
        except (ValueError, TypeError):
            pass
        was = status()
        if not force and not test_due and fp == ctx.setting(K_DIGEST) and was.get("ok") \
                and time.time() - full_at < FULL_EVERY:
            return "Nothing new to send."
        if not was.get("ok", True) and time.time() < float((was.get("asked_until") if force else was.get("next_at"))
                                                           or 0):
            return "Waiting to try again."
        kind = ctx.setting(K_KIND)
        st = state_load()
        ok, n, words, err = False, None, "", None
        try:
            _, cal = open_calendar()
            n = S.sync(cal, want, st, since(), keep=keep, mark=S.studio_mark(salt()))
            ok = True
        except S.SendError as e:
            words, err = f"Couldn't reach {service(kind)}: {e.words}", e
        except Exception as e:
            app.logger.error("calendar send failed: %s", type(e).__name__)
            words = f"Couldn't send to {service(kind)}: something went wrong in your studio"
        with locks.WRITES:
            if not ready():
                return "Stopped."
            state_save(st)
            if ok:
                put_setting(K_DIGEST, fp)
                put_setting(K_FULL, str(time.time()))
                if keep == set() and ctx.setting(K_TEST):
                    del_setting(K_TEST)
                say(True, "", n["total"])
            else:
                say(False, words, err=err)
        return "Sessions sent to your calendar." if ok else words

    def work(job):
        out = ""
        for _ in range(5):
            force, run["force"], run["again"] = run["force"], False, False
            out = send_once(force)
            if not run["again"]:
                break
        return out

    def kick(force=False):

        import jobs
        run["force"] = run["force"] or force
        run["again"] = True
        jobs.start(app, JOB, "Sending sessions to your calendar", work, quiet=True)

    @app.after_request
    def _after_change(resp):
        try:
            from flask import g
            if request.method == "POST" and g.get("write_lock") and ctx.unlocked():
                drop_stale_pending()
        except Exception:
            app.logger.exception("calendar send: could not tidy a stale setup")
        try:
            if request.method == "POST" and resp.status_code < 400 and request.endpoint \
                    and not request.endpoint.startswith("calendar_send") and ctx.unlocked() and ready():
                kick()
        except Exception:
            app.logger.exception("calendar send: could not start")
        return resp

    @ctx.after_unlock
    def _catch_up():
        if not os.environ.get("CRM_TESTING"):
            with app.app_context():
                if ready():
                    kick(force=True)

    def loop():
        time.sleep(90)
        while True:
            try:
                if ctx.unlocked():
                    with app.app_context():
                        import locks
                        with locks.WRITES:
                            drop_stale_pending()
                        if ready():
                            kick()
            except Exception:
                app.logger.exception("calendar send loop")
            time.sleep(LOOP_EVERY)

    if not os.environ.get("CRM_TESTING"):
        threading.Thread(target=loop, name="calendar-send", daemon=True).start()

    def today_items():
        if not failing():
            return []
        return [dict(text="Sessions couldn't be sent to your calendar", link=url_for("calendar_send_settings"),
                     sub=status().get("words") or "")]

    ctx.hooks.today.append(today_items)

    def when_words(at):
        try:
            dt = datetime.fromtimestamp(float(at), timezone.utc).astimezone(A().TZ)
        except (TypeError, ValueError):
            return ""
        return f"{ctx.nice(dt.date().isoformat())}, {A().clock(dt.strftime('%H:%M'))}"

    def view():
        p = request.args.get("with")
        import jobs
        typed = session.pop("calsend_typed", None)
        v = dict(ready=False, pending=None, pick=p if p in ("google", "outlook", "caldav") else "",
                 ms_builtin=bool(MS_CLIENT_ID), servers=[(k, w) for k, (w, _) in CALDAV_SERVERS.items()],
                 typed=typed if isinstance(typed, dict) else {}, job=JOB)
        if ready():
            sec = secret_get("conn") or {}
            kind = ctx.setting(K_KIND)
            st = status()
            v.update(ready=True, service=service(kind, sec), calendar=sec.get("calendar_name") or "Your calendar",
                     stopping=ctx.setting(K_STOPPING) == "1", sending=jobs.running(JOB))
            rows = []
            if sec.get("account"):
                rows.append(("Signed in as", sec["account"]))
            rows.append(("Sign-in", {"google": "Service account key (kept sealed)",
                                     "outlook": "Microsoft sign-in (kept sealed)",
                                     "caldav": "App password " + "•" * 8 + " (kept sealed)"}.get(kind, "Kept sealed")))
            if st.get("n") is not None and st.get("ok"):
                rows.append(("In your calendar", f"{st['n']} session{'' if st['n'] == 1 else 's'}"))
            if not sec:
                st = dict(ok=False, words="The saved sign-in can't be read on this install. Stop sending, then set it up again.",
                          at=time.time())
            v["rows"] = rows
            retry = st.get("next_at") if st and not st.get("ok") and (st.get("next_at") or 0) > time.time() else None
            v["status"] = dict(ok=st.get("ok"), words=st.get("words"), when=when_words(st.get("at")),
                               retry=when_words(retry) if retry else "") if st else None
        else:
            pend = secret_get("pending")
            if pend is None:
                import locks
                if locks.WRITES.acquire(timeout=5):
                    try:
                        drop_stale_pending()
                    finally:
                        locks.WRITES.release()
            if pend:
                if pend.get("code"):
                    v["pending"] = dict(code=pend["code"], uri=pend.get("uri") or "https://microsoft.com/devicelogin")
                elif pend.get("calendars"):
                    v["pending"] = dict(calendars=pend["calendars"], account=pend.get("account", ""))
        return v

    @app.get("/settings/calendar-send", endpoint="calendar_send_settings")
    def calendar_send_settings():
        if not ctx.active():
            return redirect(url_for("plugin_page", name=NAME))
        return render_template_string(PAGE, v=view())

    def live():
        if not ctx.active():
            abort(404)

    def start(kind, sec, cal_name, account):

        x("DELETE FROM calsend_events")
        del_setting(K_STATUS, K_DIGEST, K_FULL, K_TEST, K_STOPPING)
        secret_put("conn", dict(sec, kind=kind, calendar_name=cal_name, account=account))
        secret_drop("pending")
        put_setting(K_KIND, kind)
        salt()
        ctx.audit(f"Started sending sessions to {service(kind)}")
        session["notice"] = (f"Your sessions are on their way to {cal_name}. They show there within a few minutes.")
        kick(force=True)
        return here()

    @app.post("/settings/calendar-send/google", endpoint="calendar_send_google")
    def calendar_send_google():
        live()
        back = url_for("calendar_send_settings", **{"with": "google"})
        cid = (request.form.get("calendar") or "").strip()[:300]
        f = request.files.get("key")
        raw = f.read(64 * 1024 + 1) if f else b""
        if not raw or len(raw) > 64 * 1024:
            return refuse("Choose the .json key file Google gave you. Nothing was saved.", back, dict(calendar=cid))
        try:
            with talking():
                conn = S.GoogleConnection(raw)
                cal = S.GoogleCalendar(conn, cid)
                name = cal.check()
        except S.SendError as e:
            return refuse(f"Google Calendar couldn't be reached: {e.words}. Nothing was saved.", back,
                          dict(calendar=cid))
        return start("google", dict(conn.secret(), calendar=cal.cid), name or cal.cid, conn.account())

    @app.post("/settings/calendar-send/outlook", endpoint="calendar_send_outlook_start")
    def calendar_send_outlook_start():
        live()
        back = url_for("calendar_send_settings", **{"with": "outlook"})
        typed = (request.form.get("client_id") or "").strip()[:36]
        cid = typed or MS_CLIENT_ID
        try:
            with talking():
                got = S.graph_device_start(cid)
        except S.SendError as e:
            return refuse(f"Microsoft couldn't be reached: {e.words}. Nothing was saved.", back, dict(client_id=typed))
        secret_put("pending", dict(kind="outlook", client_id=cid, device_code=got["device_code"], code=got["user_code"],
                                   uri=got["verification_uri"], expires_at=got["expires_at"]))
        return here()

    @app.post("/settings/calendar-send/outlook/done", endpoint="calendar_send_outlook_finish")
    def calendar_send_outlook_finish():
        live()
        pend = secret_get("pending")
        if not pend or pend.get("kind") != "outlook" or not pend.get("device_code"):
            return refuse("That sign-in has expired. Start again.")
        if time.time() > float(pend.get("expires_at") or 0):
            secret_drop("pending")
            return refuse("The code has expired. Start again: you'll get a new one.")
        try:
            with talking():
                got = S.graph_device_finish(pend["client_id"], pend["device_code"])
                conn = S.GraphConnection(pend["client_id"], got["refresh_token"])
                conn._token, conn._until = got["access_token"], time.time() + int(got.get("expires_in") or 3600)
                cals = S.graph_calendars(conn)
        except S.SendError as e:
            if e.words.startswith("not signed in yet"):
                return refuse("Microsoft hasn't seen the code yet. Enter it at the address shown, sign in, then "
                              "press I've signed in again.")
            secret_drop("pending")
            return refuse(f"Microsoft couldn't be reached: {e.words}. Start again.")
        if not cals:
            secret_drop("pending")
            return refuse("That Microsoft account has no calendar your studio can add events to.")
        secret_put("pending", dict(kind="outlook", client_id=pend["client_id"], refresh_token=conn.refresh_token,
                                   calendars=cals[:50], account=""))
        return here()

    @app.post("/settings/calendar-send/caldav", endpoint="calendar_send_caldav")
    def calendar_send_caldav():
        live()
        back = url_for("calendar_send_settings", **{"with": "caldav"})
        svc = request.form.get("service") or "other"
        svc = svc if svc in CALDAV_SERVERS else "other"
        server = (request.form.get("server") or "").strip()[:500]
        url = CALDAV_SERVERS[svc][1] or server
        user, pw = (request.form.get("user") or "").strip()[:200], (request.form.get("password") or "")[:300]
        typed = dict(service=svc, server=server, user=user)
        missing = [w for w, have in (("the server address", url), ("your user name", user), ("the app password", pw))
                   if not have]
        if missing:
            return refuse(f"Enter {A().oxford(missing)}. Nothing was saved.", back, typed)
        named = CALDAV_SERVERS[svc][0] if svc != "other" else ""
        try:
            with talking():
                conn = S.CalDAVConnection(url, user, pw)
                cals = S.caldav_calendars(conn)
        except S.SendError as e:
            return refuse(f"{named or 'The calendar service'} couldn't be reached: {e.words}. Nothing was saved.", back,
                          typed)
        secret_put("pending", dict(kind="caldav", url=conn.url, user=user, password=pw, calendars=cals[:50],
                                   account=user, service_name=named or S.origin(conn.url)[1]))
        return here()

    @app.post("/settings/calendar-send/pick", endpoint="calendar_send_pick")
    def calendar_send_pick():
        live()
        pend = secret_get("pending")
        if not pend or not pend.get("calendars"):
            return refuse("That setup has expired. Start again.")
        try:
            cid, name = pend["calendars"][int(request.form.get("pick") or 0)]
        except (ValueError, IndexError, TypeError):
            return refuse("Pick one of the calendars listed.")
        try:
            with talking():
                if pend["kind"] == "outlook":
                    conn = S.GraphConnection(pend["client_id"], pend["refresh_token"])
                    S.GraphCalendar(conn, cid).check()
                    sec = dict(client_id=pend["client_id"], refresh_token=conn.refresh_token, calendar=cid)
                    account = ""
                else:
                    conn = S.CalDAVConnection(pend["url"], pend["user"], pend["password"])
                    S.CalDAVCalendar(conn, cid).check()
                    sec = dict(url=conn.url, user=conn.user, password=conn.password, calendar=cid,
                               service_name=pend.get("service_name") or "")
                    account = conn.user
        except S.SendError as e:
            name_ = service(pend["kind"], pend)
            return refuse(f"{name_[:1].upper() + name_[1:]} couldn't be reached: {e.words}. Try again.")
        return start(pend["kind"], sec, name, account)

    @app.post("/settings/calendar-send/cancel", endpoint="calendar_send_cancel")
    def calendar_send_cancel():
        live()
        secret_drop("pending")
        return here()

    @app.post("/settings/calendar-send/test", endpoint="calendar_send_test")
    def calendar_send_test():
        live()
        if not ready():
            return refuse("Set up a calendar first.")
        kind = ctx.setting(K_KIND)
        i = S.ident(salt(), ("test", int(time.time())))
        try:
            with talking():
                _, cal = open_calendar()
                got = cal.put(i, S.test_event())
        except S.SendError as e:
            return refuse(f"The test event couldn't be sent. Couldn't reach {service(kind)}: {e.words}.")
        put_setting(K_TEST, json.dumps(dict(ident=i, at=time.time(), rid=got.get("rid") or "")))
        sec = secret_get("conn") or {}
        session["notice"] = (f"Sent. Look in {sec.get('calendar_name') or 'your calendar'} for \"{S.TEST_SUMMARY}\" "
                             "tomorrow at noon (UTC, so it may show at another hour). Your studio takes it out again "
                             "in about 20 minutes.")
        return here()

    @app.post("/settings/calendar-send/now", endpoint="calendar_send_now")
    def calendar_send_now():
        live()
        if not ready():
            return refuse("Set up a calendar first.")
        kick(force=True)
        session["notice"] = "Sending now. This page updates when it is done."
        return here()

    @app.post("/settings/calendar-send/off", endpoint="calendar_send_off")
    def calendar_send_off():
        live()
        if not ready():
            return here()
        kind = ctx.setting(K_KIND)
        if request.form.get("remove") != "1":
            leave()
            ctx.audit(f"Stopped sending sessions to {service(kind)} (its events stay)")
            session["notice"] = "Stopped. The events already in your calendar stay there, and the sign-in is deleted."
            return here()
        put_setting(K_STOPPING, "1")
        ctx.audit(f"Stopped sending sessions to {service(kind)}, and asked for its events to be removed")

        def remove_work(job):
            import locks
            with locks.WRITES:
                pass
            st = state_load()
            try:
                _, cal = open_calendar()
                n = S.remove_all(cal, st, since() - timedelta(days=3650), mark=S.studio_mark(salt()))
            except S.SendError as e:
                with locks.WRITES:
                    state_save(st)
                    put_setting(K_STOPPING, "failed")
                    say(False, f"Couldn't remove the events: couldn't reach {service(kind)}: {e.words}. Try again, "
                               "or stop and leave them.")
                return "The events couldn't all be removed."
            with locks.WRITES:
                forget()
            return f"Removed {n} event{'' if n == 1 else 's'} from your calendar, and stopped."

        import jobs
        jobs.start(app, JOB + "-off", "Removing the events your studio sent to your calendar", remove_work)
        return here()

    for ep_ in ("calendar_send_google", "calendar_send_outlook_start", "calendar_send_outlook_finish",
                "calendar_send_caldav", "calendar_send_pick", "calendar_send_off"):
        quiet.add(ep_)
    words["calendar_send_cancel"] = "Canceled setting up Send to your calendar"
    words["calendar_send_test"] = "Sent a test event to your calendar"
    words["calendar_send_now"] = "Sent sessions to your calendar now"
