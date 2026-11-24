import json
import re

from flask import abort, redirect, render_template_string, request, session, url_for

MAX_NAME = 120
MAX_ROLE = 60
MAX_CONTACT = 200
MAX_NOTES = 4000
ROLES = ("Second shooter", "Assistant", "Videographer", "Lighting assistant")
SHOWN_FROM = ("booked", "shot", "delivered", "closed")

PANEL = """{% import "_ui.html" as ui with context %}{% import "_settings_ui.html" as sui with context %}{% from "_macros.html" import help with context %}
{% call ui.fold('Crew', id='crew', open=open_, part=true, always=true, count=rows|length, note=names) %}
{% for r in rows %}
  <div class="ui-form-row"><span class="ui-grow"><b>{{ r.name }}</b>{% if r.role %}, {{ r.role }}{% endif %}, called {{ clock(r.call_time) if r.call_time else 'at the start' }}
  {%- if r.phone %} &middot; <a href="tel:{{ r.phone|replace(' ', '')|replace('-', '')|replace('(', '')|replace(')', '')|replace('.', '') }}">{{ r.phone }}</a>{% endif %}
  {%- if r.paid %} &middot; {{ ui.chip('Paid ' ~ money(r.paid.amount) ~ ' on ' ~ nice(r.paid.date), 'done') }}{% endif %}</span></div>
  <div class="ui-btnrow">
    {% if not r.paid %}{{ ui.opener('crew-pay-' ~ r.id, 'Pay ' ~ r.first) }}{% endif %}
    {{ ui.opener('crew-call-' ~ r.id, 'Change ' ~ r.first ~ "'s call") }}
    {{ ui.action(url_for('crew_unassign', sid=sid, aid=r.id), 'Take ' ~ r.first ~ ' off this session', back=back ~ '#crew',
                 confirm='Take ' ~ r.name ~ ' off this session? They stay on your crew list.' ~ (' The expense for their pay stays.' if r.paid else '')) }}
  </div>
  {% if not r.paid %}{% call ui.inline_form('crew-pay-' ~ r.id, 'Pay ' ~ r.name) %}
  <form method="post" action="{{ url_for('crew_pay', sid=sid, aid=r.id) }}" class="ui-form-row" data-ui-inplace><input type="hidden" name="once" value="{{ form_once() }}"><input type="hidden" name="back" value="{{ back }}#crew">
    <input type="number" step="0.01" min="0.01" name="amount" placeholder="$" aria-label="Amount paid to {{ r.name }}" required value="{{ '%.2f'|format(r.rate_cents / 100) if r.rate_cents }}" class="ui-input-money">
    <input type="date" name="date" value="{{ today }}" max="{{ today }}" aria-label="Paid on" class="ui-input-date">
    <button class="ui-btn ui-btn-primary">Add the expense</button>{{ help("Adds an expense to this session, as Add expense does: " ~ (r.role or 'Crew') ~ ": " ~ r.name ~ ". It shows with the session's expenses and on Finances.") }}</form>
  {% endcall %}{% endif %}
  {% call ui.inline_form('crew-call-' ~ r.id, "Change " ~ r.name ~ "'s call") %}
  <form method="post" action="{{ url_for('crew_assign_save', sid=sid, aid=r.id) }}" class="ui-form-row" data-ui-inplace><input type="hidden" name="back" value="{{ back }}#crew">
    <input name="role" maxlength="{{ max_role }}" value="{{ r.role }}" placeholder="Role" aria-label="Role" list="crew-roles" class="ui-grow">
    <input type="time" name="call_time" value="{{ r.call_time }}" aria-label="Call time" class="ui-input-date">
    <button class="ui-btn ui-btn-quiet">Save {{ r.first }}'s call</button></form>
  {% endcall %}
{% endfor %}
<datalist id="crew-roles">{% for ro in roles %}<option value="{{ ro }}">{% endfor %}</datalist>
{% if pick %}<form method="post" action="{{ url_for('crew_assign', sid=sid) }}" class="ui-form-row" data-ui-inplace><input type="hidden" name="back" value="{{ back }}#crew">
  <select name="crew_id" aria-label="Who from your crew">{% for p in pick %}<option value="{{ p.id }}">{{ p.name }}{% if p.role %}, {{ p.role }}{% endif %}</option>{% endfor %}</select>
  <input type="time" name="call_time" aria-label="Call time" class="ui-input-date">
  <button class="ui-btn ui-btn-quiet">Add to this session</button></form>{% endif %}
<div class="ui-btnrow">{{ ui.opener('crew-new', 'Add someone new to the crew') }}
  {% if rows %}{{ ui.button_link(url_for('crew_sheet', sid=sid), 'Show the call sheet') }}{% endif %}</div>
{% call ui.inline_form('crew-new', 'Add someone new to the crew') %}
<form method="post" action="{{ url_for('crew_assign', sid=sid) }}" data-ui-inplace><input type="hidden" name="back" value="{{ back }}#crew"><input type="hidden" name="new" value="1">
  <div class="ui-form-grid">
    <label><span>Name</span><input name="name" maxlength="{{ max_name }}" required></label>
    <label><span>Role (optional)</span><input name="role" maxlength="{{ max_role }}" list="crew-roles" placeholder="Second shooter"></label>
    <label><span>Phone (optional)</span><input name="phone" type="tel" maxlength="{{ max_contact }}"></label>
    <label><span>Email (optional)</span><input name="email" type="email" maxlength="{{ max_contact }}"></label>
    <label><span>Call time (optional)</span><input type="time" name="call_time" class="ui-input-date"></label>
  </div>
  <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Add to the crew and this session</button>{{ sui.unsaved() }}</div></form>
{% endcall %}
{% if rows %}
<form method="post" action="{{ url_for('crew_sheet_client', sid=sid) }}" class="ui-form-row" data-ui-inplace><input type="hidden" name="back" value="{{ back }}#crew">
  <label class="ui-check"><input type="checkbox" name="show" value="1" data-autosubmit{{ ' checked' if sheet.client_contact }}>Put the client's phone and email on the call sheet</label>
  <noscript><button class="ui-btn ui-btn-quiet">Save the choice</button></noscript></form>
<form method="post" action="{{ url_for('crew_sheet_notes', sid=sid) }}" data-ui-inplace><input type="hidden" name="back" value="{{ back }}#crew">
  <label class="ui-label" for="crew-notes-{{ sid }}">Notes for the crew {{ help("Printed on the call sheet: parking, what to wear, the order of the day, who covers what, who to ask for. Only your crew sees them.") }}</label>
  <textarea id="crew-notes-{{ sid }}" name="notes" rows="3" maxlength="{{ max_notes }}">{{ sheet.notes }}</textarea>
  <div class="ui-btnrow"><button class="ui-btn ui-btn-quiet">Save the notes for the crew</button>{{ sui.unsaved() }}</div></form>
{% endif %}
<p>{{ ui.go(url_for('crew_list'), 'Your crew list') }}</p>
{% endcall %}"""

LIST_PAGE = """{% extends "base.html" %}{% import "_ui.html" as ui with context %}{% import "_settings_ui.html" as sui with context %}{% from "_macros.html" import help with context %}
{% block title %}Crew - Lumen Aureum{% endblock %}
{% block body %}
{{ ui.page_head('Crew', status=(rows|length ~ (' person' if rows|length == 1 else ' people')) if rows else 'No one listed yet') }}
{% macro fields(p=None) -%}
  <div class="ui-form-grid">
    <label><span>Name</span><input name="name" maxlength="{{ max_name }}" value="{{ p.name if p else '' }}" required></label>
    <label><span>Usual role (optional)</span><input name="role" maxlength="{{ max_role }}" value="{{ p.role if p else '' }}" list="crew-roles" placeholder="Second shooter"></label>
    <label><span>Phone (optional)</span><input name="phone" type="tel" maxlength="{{ max_contact }}" value="{{ p.phone if p else '' }}"></label>
    <label><span>Email (optional)</span><input name="email" type="email" maxlength="{{ max_contact }}" value="{{ p.email if p else '' }}"></label>
    <label><span>Usual pay (optional){{ help("Filled in when you pay them from a session. You can change it each time.") }}</span><input name="rate" inputmode="decimal" value="{{ ('%.2f'|format(p.rate_cents / 100)) if p and p.rate_cents is not none else '' }}" placeholder="0.00"></label>
  </div>
{%- endmacro %}
<datalist id="crew-roles">{% for ro in roles %}<option value="{{ ro }}">{% endfor %}</datalist>
{% call ui.section('Your crew', id='crew', part=true) %}
  {% for p in rows %}
  {% call ui.fold(p.name ~ (', ' ~ p.role if p.role else ''), id='crew-' ~ p.id, note=(used.get(p.id, 0) ~ (' session' if used.get(p.id, 0) == 1 else ' sessions')), always=true) %}
    <form method="post" action="{{ url_for('crew_save', crew_id=p.id) }}">
      {{ fields(p) }}
      <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Save {{ p.name }}</button>{{ sui.unsaved() }}</div>
    </form>
    <div class="ui-btnrow">{{ ui.action(url_for('crew_delete', crew_id=p.id), 'Delete ' ~ p.name, inplace=false,
        confirm='Delete ' ~ p.name ~ ' from your crew list?' ~ ((' They come off ' ~ used.get(p.id) ~ (' session' if used.get(p.id) == 1 else ' sessions') ~ ' and those call sheets. Expenses already added for their pay stay.') if used.get(p.id) else '')) }}</div>
  {% endcall %}
  {% else %}<p class="ui-mute">No one listed yet.</p>{% endfor %}
{% endcall %}
{% call ui.section('Add someone to the crew', id='add') %}
  <form method="post" action="{{ url_for('crew_add') }}">
    {{ fields() }}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Add to the crew</button>{{ sui.unsaved() }}</div>
  </form>
{% endcall %}
{% endblock %}"""

SHEET = """{% from "_doc_bar.html" import doc_bar with context %}<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Call sheet - {{ v.client }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
{% include "_doc_style.html" %}</head><body>
{% if not pdf %}{{ doc_bar(url_for('crew_sheet_pdf', sid=v.sid), v.back) }}{% endif %}
<div class="page">
  {% include "_doc_logo.html" %}
  <h1>Call sheet</h1>
  <p class="sub">{{ v.photographer }}{% if v.business %}, {{ v.business }}{% endif %}</p>
  {% include "_doc_contact.html" %}
  <table class="facts" style="margin-top:14px">
    <tr><td>Session</td><td>{{ v.type_label }}{% if v.date_long %}, {{ v.date_long }}{% endif %}</td></tr>
    <tr><td>Starts</td><td>{{ v.meet }}</td></tr>
    <tr><td>Client</td><td>{{ v.client }}{% if v.contact %}{% if v.contact.name and v.contact.name != v.client %}, {{ v.contact.name }}{% endif %}
      {%- if v.contact.phone %}<br>{% if pdf %}{{ v.contact.phone }}{% else %}<a href="tel:{{ v.contact.tel }}">{{ v.contact.phone }}</a>{% endif %}{% endif %}
      {%- if v.contact.email %}<br>{{ v.contact.email }}{% endif %}{% endif %}</td></tr>
  </table>
  <h2>Crew</h2>
  <table>{% for r in v.crew %}<tr><td style="width:120px">{{ r.call }}</td><td><b>{{ r.name }}</b>{% if r.role %}, {{ r.role }}{% endif %}
    {%- if r.phone %}<br>{% if pdf %}{{ r.phone }}{% else %}<a href="tel:{{ r.tel }}">{{ r.phone }}</a>{% endif %}{% endif %}
    {%- if r.email %}<br>{{ r.email }}{% endif %}</td></tr>{% else %}<tr><td>No crew on this session yet.</td></tr>{% endfor %}</table>
  {% if v.stops %}<h2>Where</h2>
  <table>{% for st in v.stops %}<tr><td style="width:120px">{{ st.time or '' }}</td><td><b>{{ st.name }}</b>{% for ln in (st.note or '').splitlines() if ln.strip() %}<br>{{ ln }}{% endfor %}</td></tr>{% endfor %}</table>{% endif %}
  {% if v.timeline %}<h2>Timeline</h2>
  <p>{% for ln in v.timeline.splitlines() %}{{ ln }}{% if not loop.last %}<br>{% endif %}{% endfor %}</p>{% endif %}
  {% if v.notes %}<h2>Notes</h2>
  <p>{% for ln in v.notes.splitlines() %}{{ ln }}{% if not loop.last %}<br>{% endif %}{% endfor %}</p>{% endif %}
  <p class="foot">For the crew of this session only.</p>
</div>{% if not pdf %}<script src="{{ url_for('static', filename='ui.js', v=static_v('ui.js')) }}"></script>{% endif %}</body></html>"""

def one_line(v, cap):
    return " ".join(str(v or "").split())[:cap]

def tel(phone):
    return re.sub(r"[^0-9+]", "", phone or "")

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    ctx.migrate("""
        CREATE TABLE IF NOT EXISTS crew (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '', rate_cents INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS session_crew (
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            crew_id INTEGER NOT NULL REFERENCES crew(id) ON DELETE CASCADE, role TEXT NOT NULL DEFAULT '',
            call_time TEXT NOT NULL DEFAULT '', expense_id INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
            UNIQUE (session_id, crew_id));
        CREATE TABLE IF NOT EXISTS call_sheets (
            session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            notes TEXT NOT NULL DEFAULT '', client_contact INTEGER NOT NULL DEFAULT 0);""")

    ctx.switchable(default=False, label="Crew and call sheets",
                   about="Second shooters and assistants on a session, with their call times, a call sheet to print "
                         "or share as a PDF, and paying them as a session expense. Off: nothing shows; everything "
                         "already recorded is kept and comes back when it is on again.",
                   when_on=lambda: "Booked sessions show a Crew fold in The shoot. Add people there or on your crew "
                                   "list.",
                   links=[("Your crew list", "crew_list")])
    active = ctx.active
    words = app.config.setdefault("ep_words", {})

    def A():
        return __import__("app")

    def back_of(sid):
        if ctx.safe_back(request.form.get("back")):
            return ctx.safe_back(request.form.get("back"))
        s = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
        return (url_for("client_stage", hid=s["household_id"], s=sid) if s else url_for("home")) + "#crew"

    def refuse(text, back):
        from flask import g
        session["notice"] = text
        g.refused = True
        return redirect(back)

    def session_row(sid):
        s = q("SELECT id, household_id, stage FROM sessions WHERE id=?", (sid,), one=True)
        if not active() or not s:
            abort(404)
        return s

    def assignment(sid, aid):
        return q("""SELECT sc.*, c.name, c.rate_cents FROM session_crew sc JOIN crew c ON c.id=sc.crew_id
                    WHERE sc.id=? AND sc.session_id=?""", (aid, sid), one=True) or abort(404)

    def time_in(v):
        v = (v or "").strip()
        if not v:
            return ""
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("A call time is a time of day, such as 2:30 pm. Nothing was saved.")
        return v

    def person_in(f):
        name = one_line(f.get("name"), MAX_NAME)
        if not name:
            raise ValueError("Give them a name. Nothing was saved.")
        email = one_line(f.get("email"), MAX_CONTACT)
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("That email address doesn't look complete. Nothing was saved.")
        return dict(name=name, role=one_line(f.get("role"), MAX_ROLE), phone=one_line(f.get("phone"), MAX_CONTACT),
                    email=email, rate_cents=cents_in(f.get("rate")))

    def cents_in(v):
        from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
        v = (v or "").replace("$", "").replace(",", "").strip()
        if not v:
            return None
        try:
            d = Decimal(v)
        except InvalidOperation:
            d = None
        if d is None or not d.is_finite():
            raise ValueError("The usual pay isn't a number. Nothing was saved.") from None
        if d < 0 or d > 1_000_000:
            raise ValueError("The usual pay is out of range. Nothing was saved.")
        return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def sheet_of(sid):
        r = q("SELECT notes, client_contact FROM call_sheets WHERE session_id=?", (sid,), one=True)
        return dict(notes=r["notes"], client_contact=bool(r["client_contact"])) if r else dict(notes="", client_contact=False)

    def pay_of(expense_id):

        e = q("SELECT amount, date FROM expenses WHERE id=?", (expense_id,), one=True) if expense_id else None
        if not e:
            return None
        try:
            fix = q("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE corrects=?", (expense_id,), one=True)["v"]
        except Exception:
            fix = 0
        net = round((e["amount"] or 0) + (fix or 0), 2)
        return dict(amount=net, date=e["date"]) if net > 0.005 else None

    def crew_on(sid):
        rows = q("""SELECT sc.id, sc.role, sc.call_time, sc.expense_id, c.name, c.phone, c.email, c.rate_cents
                    FROM session_crew sc JOIN crew c ON c.id=sc.crew_id WHERE sc.session_id=?
                    ORDER BY sc.call_time = '', sc.call_time, c.name COLLATE NOCASE, sc.id""", (sid,))
        out = []
        for r in rows:
            d = dict(r)
            d["paid"] = pay_of(r["expense_id"])
            d["first"] = r["name"].split()[0] if r["name"].split() else r["name"]
            out.append(d)
        return out

    def session_panel(s):
        if not active() or s["stage"] == "lost":
            return None
        rows = crew_on(s["id"])
        if not rows and s["stage"] not in SHOWN_FROM:
            return None
        if not rows and s["stage"] == "closed":
            return None
        on = {r["crew_id"] for r in q("SELECT crew_id FROM session_crew WHERE session_id=?", (s["id"],))}
        pick = [p for p in q("SELECT id, name, role FROM crew ORDER BY name COLLATE NOCASE, id") if p["id"] not in on]
        shoot_day = bool(s["shoot_date"]) and s["shoot_date"][:10] == ctx.now_local()[:10]
        names = ", ".join(r["name"] for r in rows[:3]) + (f" and {len(rows) - 3} more" if len(rows) > 3 else "")
        back = url_for("client_stage", hid=s["household_id"], s=s["id"])
        return render_template_string(PANEL, rows=rows, pick=pick, sid=s["id"], back=back, open_=shoot_day and bool(rows),
                                      names=names, sheet=sheet_of(s["id"]), roles=ROLES, money=ctx.money, nice=ctx.nice,
                                      today=A().today().isoformat(), max_name=MAX_NAME, max_role=MAX_ROLE,
                                      max_contact=MAX_CONTACT, max_notes=MAX_NOTES)

    ctx.hooks.session_panels.append(session_panel)

    @app.post("/session/<int:sid>/crew", endpoint="crew_assign")
    def crew_assign(sid):

        session_row(sid)
        back = back_of(sid)
        try:
            call = time_in(request.form.get("call_time"))
            if request.form.get("new"):
                p = person_in(request.form)
                cid = x("INSERT INTO crew (name, role, phone, email) VALUES (?,?,?,?)",
                        (p["name"], p["role"], p["phone"], p["email"]))
                role = p["role"]
            else:
                c = q("SELECT id, role FROM crew WHERE id=?", (request.form.get("crew_id", type=int),), one=True)
                if not c:
                    raise ValueError("Pick someone from your crew list. Nothing was saved.")
                cid, role = c["id"], c["role"]
        except ValueError as e:
            return refuse(str(e), back)
        x("INSERT OR IGNORE INTO session_crew (session_id, crew_id, role, call_time) VALUES (?,?,?,?)", (sid, cid, role, call))
        return redirect(back)

    words["crew_assign"] = lambda f: (f"Added {one_line(f.get('name'), MAX_NAME)} to the crew and this session" if f.get("new")
                                      else "Added someone from the crew list to this session")

    @app.post("/session/<int:sid>/crew/<int:aid>", endpoint="crew_assign_save")
    def crew_assign_save(sid, aid):
        session_row(sid)
        r = assignment(sid, aid)
        back = back_of(sid)
        try:
            call = time_in(request.form.get("call_time"))
        except ValueError as e:
            return refuse(str(e), back)
        x("UPDATE session_crew SET role=?, call_time=? WHERE id=?", (one_line(request.form.get("role"), MAX_ROLE), call, r["id"]))
        return redirect(back)

    words["crew_assign_save"] = lambda f: ("Changed a crew member's role and call time"
                                           + (f": called {A().clock(f['call_time'])}" if f.get("call_time") else ""))

    @app.post("/session/<int:sid>/crew/<int:aid>/off", endpoint="crew_unassign")
    def crew_unassign(sid, aid):
        session_row(sid)
        r = assignment(sid, aid)
        x("DELETE FROM session_crew WHERE id=?", (r["id"],))
        return redirect(back_of(sid))

    words["crew_unassign"] = "Took a crew member off this session"

    def closed_year(day):

        import inspect
        fn = A().closed_year_refusal
        return fn(day, "expense") if "what" in inspect.signature(fn).parameters else fn(day)

    @app.post("/session/<int:sid>/crew/<int:aid>/pay", endpoint="crew_pay")
    def crew_pay(sid, aid):

        a_ = A()
        session_row(sid)
        r = assignment(sid, aid)
        back = back_of(sid)
        if a_.sent_twice():
            return a_._refuse_twice(back)
        if pay_of(r["expense_id"]):
            return refuse(f"{r['name']}'s pay is already an expense on this session. Nothing was recorded.", back)
        label = f"{r['role'] or 'Crew'}: {r['name']}"[:200]
        day, why = a_.money_day_in(request.form.get("date"), "expense")
        why = why or (closed_year(day) if day else "")
        try:
            amt = a_.amount_in(request.form.get("amount"))
        except a_.TooMuch:
            why = why or a_.TOO_MUCH
            amt = None
        if not why and (amt is None or amt <= 0):
            why = "Enter what you paid them, as a positive amount. Nothing was recorded."
        why = why or a_.add_expense(sid, dict(label=label, amount=request.form.get("amount"), date=day or ""))
        if why:
            return refuse(why, back)
        e = q("SELECT max(id) AS id FROM expenses WHERE session_id=? AND label=?", (sid, label), one=True)
        x("UPDATE session_crew SET expense_id=? WHERE id=?", (e["id"], r["id"]))
        session["notice"] = f"Added the {ctx.money(amt)} expense: {label}."
        return redirect(back)

    words["crew_pay"] = lambda f: f"Added a crew pay expense {A().money(A().num(f.get('amount')))}"

    @app.post("/session/<int:sid>/call-sheet/client", endpoint="crew_sheet_client")
    def crew_sheet_client(sid):
        session_row(sid)
        x("""INSERT INTO call_sheets (session_id, client_contact) VALUES (?, ?)
             ON CONFLICT(session_id) DO UPDATE SET client_contact=excluded.client_contact""",
          (sid, 1 if request.form.get("show") else 0))
        return redirect(back_of(sid))

    words["crew_sheet_client"] = lambda f: ("Put the client's phone and email on the call sheet" if f.get("show")
                                            else "Took the client's phone and email off the call sheet")

    @app.post("/session/<int:sid>/call-sheet/notes", endpoint="crew_sheet_notes")
    def crew_sheet_notes(sid):
        session_row(sid)
        notes = "\n".join(ln.rstrip() for ln in (request.form.get("notes") or "").strip().splitlines())[:MAX_NOTES]
        x("""INSERT INTO call_sheets (session_id, notes) VALUES (?, ?)
             ON CONFLICT(session_id) DO UPDATE SET notes=excluded.notes""", (sid, notes))
        return redirect(back_of(sid))

    words["crew_sheet_notes"] = "Saved the notes for the crew"

    def sheet_ctx(sid):
        a_ = A()
        s = a_.session_full(sid)
        stops = ctx.detail_stops(s)
        meet = ((a_.clock(s["start_time"]) if s["start_time"] else "Time to be confirmed")
                + (f" at {stops[0]['name']}" if stops else ", place to be confirmed"))
        sheet = sheet_of(sid)
        contact = None
        if sheet["client_contact"]:
            people = q("SELECT * FROM people WHERE household_id=? ORDER BY role='parent' DESC, id", (s["household_id"],))
            p = a_.household_contact(people)
            if p:
                contact = dict(name=p["name"], phone=(p["phone"] or "").strip(), email=(p["email"] or "").strip(),
                               tel=tel(p["phone"]))
        try:
            intake = json.loads(s["intake"] or "{}")
        except ValueError:
            intake = {}
        timeline = intake.get("timeline") if isinstance(intake, dict) else None
        crew = [dict(name=r["name"], role=r["role"], phone=r["phone"], email=r["email"], tel=tel(r["phone"]),
                     call=a_.clock(r["call_time"]) if r["call_time"] else (a_.clock(s["start_time"]) if s["start_time"] else ""))
                for r in crew_on(sid)]
        return dict(sid=sid, client=s["household"], type_label=ctx.C.doc_label(s["type"]),
                    date_long=ctx.long_date(s["shoot_date"]), meet=meet, stops=stops, crew=crew, contact=contact,
                    timeline=timeline.strip() if isinstance(timeline, str) else "", notes=sheet["notes"],
                    photographer=ctx.setting("photographer_name"), business=ctx.setting("business_name"),
                    back=url_for("client_stage", hid=s["household_id"], s=sid) + "#crew")

    @app.get("/session/<int:sid>/call-sheet", endpoint="crew_sheet")
    def crew_sheet(sid):
        session_row(sid)
        return render_template_string(SHEET, v=sheet_ctx(sid), pdf=False)

    @app.get("/session/<int:sid>/call-sheet.pdf", endpoint="crew_sheet_pdf")
    def crew_sheet_pdf(sid):
        a_ = A()
        session_row(sid)
        v = sheet_ctx(sid)
        return ctx.html_pdf(render_template_string(SHEET, v=v, pdf=True),
                            a_.file_slug("call-sheet", a_.client_slug(sid), (a_.session_full(sid)["shoot_date"] or "")[:10]) + ".pdf")

    @app.get("/crew", endpoint="crew_list")
    def crew_list():
        if not active():
            abort(404)
        rows = q("SELECT * FROM crew ORDER BY name COLLATE NOCASE, id")
        used = {r["crew_id"]: r["n"] for r in q("SELECT crew_id, COUNT(*) AS n FROM session_crew GROUP BY crew_id")}
        return render_template_string(LIST_PAGE, rows=rows, used=used, roles=ROLES, max_name=MAX_NAME, max_role=MAX_ROLE,
                                      max_contact=MAX_CONTACT)

    @app.post("/crew/add", endpoint="crew_add")
    def crew_add():
        if not active():
            abort(404)
        back = url_for("crew_list")
        try:
            p = person_in(request.form)
        except ValueError as e:
            return refuse(str(e), back)
        cid = x("INSERT INTO crew (name, role, phone, email, rate_cents) VALUES (?,?,?,?,?)",
                (p["name"], p["role"], p["phone"], p["email"], p["rate_cents"]))
        return redirect(f"{back}#crew-{cid}")

    words["crew_add"] = lambda f: f"Added {one_line(f.get('name'), MAX_NAME)} to the crew list"

    @app.post("/crew/<int:crew_id>", endpoint="crew_save")
    def crew_save(crew_id):
        if not active():
            abort(404)
        q("SELECT id FROM crew WHERE id=?", (crew_id,), one=True) or abort(404)
        back = f"{url_for('crew_list')}#crew-{crew_id}"
        try:
            p = person_in(request.form)
        except ValueError as e:
            return refuse(str(e), back)
        x("UPDATE crew SET name=?, role=?, phone=?, email=?, rate_cents=? WHERE id=?",
          (p["name"], p["role"], p["phone"], p["email"], p["rate_cents"], crew_id))
        return redirect(back)

    words["crew_save"] = lambda f: f"Saved {one_line(f.get('name'), MAX_NAME)} on the crew list"

    @app.post("/crew/<int:crew_id>/delete", endpoint="crew_delete")
    def crew_delete(crew_id):
        if not active():
            abort(404)
        q("SELECT id FROM crew WHERE id=?", (crew_id,), one=True) or abort(404)
        x("DELETE FROM crew WHERE id=?", (crew_id,))
        return redirect(url_for("crew_list"))

    words["crew_delete"] = "Deleted someone from the crew list"
