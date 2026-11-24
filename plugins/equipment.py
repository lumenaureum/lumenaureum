import re

from flask import abort, redirect, render_template_string, request, url_for

MAX_LINES = 60
MAX_LINE = 80
MAX_NAME = 120
MAX_RECEIPT = 20 << 20
RENEW_DAYS = 30
MAX_TYPES = 40

TYPE_PANEL = """{% import "_ui.html" as ui with context %}{% import "_settings_ui.html" as sui with context %}{% from "_macros.html" import help with context %}
{% call ui.section('Packing list', id='packing', part=true) %}
<form method="post" action="{{ url_for('packing_save', key=t.key) }}" data-ui-inplace>
  <input type="hidden" name="back" value="{{ url_for('type_edit', key=t.key) }}#packing">
  <label class="ui-label" for="packing-lines">One thing per line {{ help("Shown on every booked " ~ t.label ~ " session, to tick off as you pack. Ticks belong to each session.") }}</label>
  <textarea id="packing-lines" name="lines" rows="{{ [lines|length + 2, 14]|min }}" maxlength="{{ max_len }}" placeholder="Camera body&#10;Spare batteries&#10;Memory cards">{{ lines|join('\\n') }}</textarea>
  <div class="ui-btnrow"><button class="ui-btn ui-btn-quiet">Save the packing list</button>{{ sui.unsaved() }}</div>
</form>
{% if gear %}<form method="post" action="{{ url_for('packing_add', key=t.key) }}" class="ui-form-row" data-ui-inplace>
  <input type="hidden" name="back" value="{{ url_for('type_edit', key=t.key) }}#packing">
  <select name="eid" aria-label="Equipment to add to the packing list">{% for e in gear %}<option value="{{ e.id }}">{{ e.name }}</option>{% endfor %}</select>
  <button class="ui-btn ui-btn-quiet">Add from your equipment</button></form>{% endif %}
{% endcall %}"""

SESSION_PANEL = """{% import "_ui.html" as ui with context %}
{% call ui.fold('Packing list: ' ~ (packed|length) ~ ' of ' ~ (lines|length) ~ ' packed', id='packing', open=open_, part=true, always=true) %}
{% for line in lines %}<form method="post" action="{{ url_for('packing_tick', sid=sid) }}" class="ui-form-row" data-ui-inplace>
  <input type="hidden" name="line" value="{{ line }}"><input type="hidden" name="back" value="{{ back }}#packing">
  <label class="ui-check"><input type="checkbox" name="packed" value="1" data-autosubmit{{ ' checked' if line in packed }}>{{ line }}</label>
  <noscript><button class="ui-btn ui-btn-quiet">Save the tick</button></noscript></form>{% endfor %}
{% endcall %}"""

PAGE = """{% extends "base.html" %}{% import "_ui.html" as ui with context %}{% import "_settings_ui.html" as sui with context %}{% from "_macros.html" import help with context %}
{% block title %}Equipment - Lumen Aureum{% endblock %}
{% block body %}
{{ ui.page_head('Equipment', status=(rows|length ~ (' thing' if rows|length == 1 else ' things') ~ ', ' ~ money(total) ~ ' paid') if rows else 'Nothing listed yet') }}
{% macro fields(e=None) -%}
  <div class="ui-form-grid">
    <label><span>Name</span><input name="name" maxlength="{{ max_name }}" value="{{ e.name if e else '' }}" required placeholder="Camera body"></label>
    <label><span>Serial number (optional)</span><input name="serial" maxlength="{{ max_name }}" value="{{ (e.serial or '') if e else '' }}"></label>
    <label><span>Bought (optional)</span><input type="date" name="bought" value="{{ (e.bought or '') if e else '' }}" class="ui-input-date"></label>
    <label><span>Renews on (optional){{ help("A warranty or insurance date. Today reminds you " ~ renew_days ~ " days before.") }}</span><input type="date" name="renews" value="{{ (e.renews or '') if e else '' }}" class="ui-input-date"></label>
    <label><span>Price paid (optional)</span><input name="price" inputmode="decimal" value="{{ ('%.2f'|format(e.price_cents / 100)) if e and e.price_cents is not none else '' }}" placeholder="0.00"></label>
    <label><span>{{ 'Replace the receipt' if e and e.receipt_fid else 'Receipt (optional)' }}{{ help("A photo or PDF of the receipt, up to " ~ max_mb ~ " MB. Kept sealed like your photos, and backed up with them.") }}</span><input type="file" name="receipt" accept="image/*,application/pdf"></label>
  </div>
{%- endmacro %}
{% call ui.section('Your equipment', id='equipment', part=true) %}
  {% for e in rows %}{% set rc = receipts.get(e.receipt_fid) %}
  {% call ui.fold(e.name ~ (', serial ' ~ e.serial if e.serial else ''), id='eq-' ~ e.id, note=money(e.price_cents) if e.price_cents is not none else '', always=true) %}
    <form method="post" action="{{ url_for('equipment_save', item_id=e.id) }}" enctype="multipart/form-data">
      {{ fields(e) }}
      <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Save {{ e.name }}</button>{{ sui.unsaved() }}</div>
    </form>
    {% if rc %}<div class="ui-form-row"><span class="ui-grow">Receipt: {{ rc.name }}</span>
      {{ ui.button_link(url_for('equipment_receipt', item_id=e.id), 'Download the receipt') }}
      {{ ui.action(url_for('equipment_receipt_delete', item_id=e.id), 'Remove the receipt', inplace=false, confirm='Remove the receipt for ' ~ e.name ~ '? Its sealed copy is deleted here and from the backup location.') }}</div>{% endif %}
    <div class="ui-btnrow">{{ ui.action(url_for('equipment_delete', item_id=e.id), 'Delete ' ~ e.name, inplace=false, confirm='Delete ' ~ e.name ~ (' and its receipt' if rc else '') ~ ' from the list?') }}</div>
  {% endcall %}
  {% else %}<p class="ui-mute">Nothing listed yet.</p>{% endfor %}
{% endcall %}
{% call ui.section('Add equipment', id='add') %}
  <form method="post" action="{{ url_for('equipment_add') }}" enctype="multipart/form-data">
    {{ fields() }}
    <div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Add to the list</button>{{ sui.unsaved() }}</div>
  </form>
{% endcall %}
{% endblock %}"""

def clean_lines(text):

    out = []
    for ln in (text or "").splitlines():
        ln = " ".join(ln.split())[:MAX_LINE]
        if ln and ln.lower() not in {o.lower() for o in out}:
            out.append(ln)
    return out[:MAX_LINES]

def register(ctx):
    app, q, x, C = ctx.app, ctx.q, ctx.x, ctx.C
    ctx.migrate("""
        CREATE TABLE IF NOT EXISTS packing_lists (type_key TEXT PRIMARY KEY, lines TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, serial TEXT DEFAULT '', bought TEXT, price_cents INTEGER,
            receipt_fid TEXT, renews TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS packing_ticks (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, line TEXT NOT NULL,
            PRIMARY KEY (session_id, line));""")

    @ctx.on_unlock
    def _renews_column():

        import db as D
        con = ctx.connect()
        try:
            if "renews" not in D.columns(con, "equipment"):
                ctx.add_column(con, "equipment", "renews", "TEXT")
                con.commit()
        finally:
            con.close()

    ctx.switchable(default=False, label="Equipment and packing lists",
                   about="A list of your equipment (serial numbers, prices, receipts) for insurance, and a packing "
                         "list on each session type, ticked off on the shoot day. Off: nothing shows; everything "
                         "already recorded is kept and comes back when it is on again.",
                   when_on=lambda: "Equipment is in the top bar. Add a packing list on a session type's page; booked "
                                   "sessions of that type show it in The shoot.",
                   links=[("Equipment", "equipment_list")])
    active = ctx.active

    def lines_of(type_key):
        import json
        r = q("SELECT lines FROM packing_lists WHERE type_key=?", (type_key,), one=True)
        try:
            return [str(v) for v in json.loads(r["lines"])] if r else []
        except ValueError:
            return []

    def known_type(key):
        return any(t["key"] == key for t in C.TYPES)

    def type_panel(t):
        if not active():
            return None
        lines = lines_of(t["key"])
        have = {ln.lower() for ln in lines}
        gear = [e for e in q("SELECT id, name FROM equipment ORDER BY name COLLATE NOCASE")
                if e["name"][:MAX_LINE].lower() not in have]
        return render_template_string(TYPE_PANEL, t=t, lines=lines, gear=gear, max_len=MAX_LINES * (MAX_LINE + 1))

    ctx.hooks.type_panels.append(type_panel)

    @app.post("/settings/type/<key>/packing", endpoint="packing_save")
    def packing_save(key):
        import json
        if not active() or not known_type(key):
            abort(404)
        lines = clean_lines(request.form.get("lines"))
        x("""INSERT INTO packing_lists (type_key, lines) VALUES (?, ?)
             ON CONFLICT(type_key) DO UPDATE SET lines=excluded.lines""", (key, json.dumps(lines)))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("type_edit", key=key))

    app.config.setdefault("ep_words", {})["packing_save"] = "Saved a session type's packing list"

    @app.post("/settings/type/<key>/packing/add", endpoint="packing_add")
    def packing_add(key):

        import json
        if not active() or not known_type(key):
            abort(404)
        e = q("SELECT name FROM equipment WHERE id=?", (request.form.get("eid", type=int),), one=True) or abort(404)
        lines = clean_lines("\n".join(lines_of(key) + [e["name"]]))
        x("""INSERT INTO packing_lists (type_key, lines) VALUES (?, ?)
             ON CONFLICT(type_key) DO UPDATE SET lines=excluded.lines""", (key, json.dumps(lines)))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("type_edit", key=key))

    app.config["ep_words"]["packing_add"] = lambda f: "Added equipment to a session type's packing list"

    def session_panel(s):
        if not active() or s["stage"] != "booked":
            return None
        lines = lines_of(s["type"])
        if not lines:
            return None
        packed = {r["line"] for r in q("SELECT line FROM packing_ticks WHERE session_id=?", (s["id"],))} & set(lines)
        shoot_day = bool(s["shoot_date"]) and s["shoot_date"][:10] == ctx.now_local()[:10]
        back = url_for("client_stage", hid=s["household_id"], s=s["id"])
        return render_template_string(SESSION_PANEL, lines=lines, packed=packed, sid=s["id"], back=back, open_=shoot_day)

    ctx.hooks.session_panels.append(session_panel)

    @app.post("/session/<int:sid>/packing", endpoint="packing_tick")
    def packing_tick(sid):
        s = q("SELECT id, type FROM sessions WHERE id=?", (sid,), one=True)
        if not active() or not s:
            abort(404)
        line = " ".join((request.form.get("line") or "").split())[:MAX_LINE]
        if line in lines_of(s["type"]):
            if request.form.get("packed"):
                x("INSERT OR IGNORE INTO packing_ticks (session_id, line) VALUES (?, ?)", (sid, line))
            else:
                x("DELETE FROM packing_ticks WHERE session_id=? AND line=?", (sid, line))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("session_view", sid=sid))

    app.config.setdefault("quiet_endpoints", set()).add("packing_tick")

    def items():
        return q("SELECT * FROM equipment ORDER BY name COLLATE NOCASE, id")

    def item(eid):
        return q("SELECT * FROM equipment WHERE id=?", (eid,), one=True) or abort(404)

    def cents_in(v):

        v = (v or "").replace("$", "").replace(",", "").strip()
        if not v:
            return None
        from decimal import Decimal, InvalidOperation
        try:
            d = Decimal(v)
        except InvalidOperation:
            raise ValueError("The price isn't a number.") from None
        if d < 0 or d > 10_000_000:
            raise ValueError("The price is out of range.")
        return int((d * 100).quantize(Decimal("1")))

    def day_in(v):
        from datetime import date
        v = (v or "").strip()
        if not v:
            return None
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("A date isn't one the app can read.") from None

    def fields_in(f):
        name = " ".join((f.get("name") or "").split())[:MAX_NAME]
        if not name:
            raise ValueError("Give the equipment a name.")
        return dict(name=name, serial=" ".join((f.get("serial") or "").split())[:MAX_NAME],
                    bought=day_in(f.get("bought")), price_cents=cents_in(f.get("price")), renews=day_in(f.get("renews")))

    def receipt_in():

        fs = request.files.get("receipt")
        if not fs or not fs.filename:
            return None
        data = fs.read(MAX_RECEIPT + 1)
        if not data:
            return None
        if len(data) > MAX_RECEIPT:
            raise ValueError(f"The receipt is over {MAX_RECEIPT >> 20} MB. Save it smaller (a photo or a PDF) and add it again.")
        return fs.filename, data

    def seal_receipt(con, eid, got):

        import photostore
        from vault import keys as VK
        row = photostore.keep(con, "receipt", got[0], got[1], master_pub=VK.master_pub(con), label=photostore.OTHER_KINDS["receipt"])
        old = con.execute("SELECT receipt_fid FROM equipment WHERE id=?", (eid,)).fetchone()
        con.execute("UPDATE equipment SET receipt_fid=? WHERE id=?", (row["id"], eid))
        con.commit()
        drop_receipt(con, old["receipt_fid"] if old else None)

    def drop_receipt(con, fid):
        import photostore
        r = con.execute("SELECT * FROM photo_files WHERE id=? AND kind='receipt'", (fid,)).fetchone() if fid else None
        if r:
            photostore.delete(con, r)

    def refuse(words, back):
        from flask import session
        session["notice"] = words
        from flask import g
        g.refused = True
        return redirect(back)

    @app.get("/equipment", endpoint="equipment_list")
    def equipment_list():
        if not active():
            abort(404)
        rows = items()
        receipts = {r["id"]: r for r in q("SELECT id, name, size FROM photo_files WHERE kind='receipt' AND state='done'")}
        total = sum(r["price_cents"] or 0 for r in rows)
        return render_template_string(PAGE, rows=rows, receipts=receipts, total=total, money=money, max_name=MAX_NAME,
                                      max_mb=MAX_RECEIPT >> 20, renew_days=RENEW_DAYS)

    def money(cents):
        return ctx.money((cents or 0) / 100)

    @app.post("/equipment/add", endpoint="equipment_add")
    def equipment_add():
        if not active():
            abort(404)
        back = url_for("equipment_list")
        try:
            v = fields_in(request.form)
            got = receipt_in()
        except ValueError as e:
            return refuse(str(e), back)
        con = ctx.connect()
        try:
            eid = con.execute("INSERT INTO equipment (name, serial, bought, price_cents, renews) VALUES (?,?,?,?,?)",
                              (v["name"], v["serial"], v["bought"], v["price_cents"], v["renews"])).lastrowid
            con.commit()
            if got:
                seal_receipt(con, eid, got)
        finally:
            con.close()
        return redirect(f"{back}#eq-{eid}")

    app.config["ep_words"]["equipment_add"] = lambda f: f"Added equipment: {(f.get('name') or '').strip()[:80]}"

    @app.post("/equipment/<int:item_id>", endpoint="equipment_save")
    def equipment_save(item_id):
        eid = item_id
        if not active():
            abort(404)
        item(eid)
        back = f"{url_for('equipment_list')}#eq-{eid}"
        try:
            v = fields_in(request.form)
            got = receipt_in()
        except ValueError as e:
            return refuse(str(e), back)
        con = ctx.connect()
        try:
            con.execute("UPDATE equipment SET name=?, serial=?, bought=?, price_cents=?, renews=? WHERE id=?",
                        (v["name"], v["serial"], v["bought"], v["price_cents"], v["renews"], eid))
            con.commit()
            if got:
                seal_receipt(con, eid, got)
        finally:
            con.close()
        return redirect(back)

    app.config["ep_words"]["equipment_save"] = lambda f: f"Saved equipment: {(f.get('name') or '').strip()[:80]}"

    @app.post("/equipment/<int:item_id>/delete", endpoint="equipment_delete")
    def equipment_delete(item_id):
        eid = item_id
        if not active():
            abort(404)
        r = item(eid)
        con = ctx.connect()
        try:
            con.execute("DELETE FROM equipment WHERE id=?", (eid,))
            con.commit()
            drop_receipt(con, r["receipt_fid"])
        finally:
            con.close()
        return redirect(url_for("equipment_list"))

    app.config["ep_words"]["equipment_delete"] = "Deleted a piece of equipment and its receipt"

    @app.post("/equipment/<int:item_id>/receipt/delete", endpoint="equipment_receipt_delete")
    def equipment_receipt_delete(item_id):
        eid = item_id
        if not active():
            abort(404)
        r = item(eid)
        con = ctx.connect()
        try:
            con.execute("UPDATE equipment SET receipt_fid=NULL WHERE id=?", (eid,))
            con.commit()
            drop_receipt(con, r["receipt_fid"])
        finally:
            con.close()
        return redirect(f"{url_for('equipment_list')}#eq-{eid}")

    app.config["ep_words"]["equipment_receipt_delete"] = "Removed an equipment receipt"

    @app.get("/equipment/<int:item_id>/receipt", endpoint="equipment_receipt")
    def equipment_receipt(item_id):

        eid = item_id
        import mimetypes
        from urllib.parse import quote
        import photostore
        if not active():
            abort(404)
        r = item(eid)
        f = q("SELECT * FROM photo_files WHERE id=? AND kind='receipt' AND state='done'", (r["receipt_fid"],), one=True) \
            if r["receipt_fid"] else None
        if not f:
            abort(404)
        try:
            data = photostore.plain_bytes(f)
        except Exception:
            return refuse(f"The receipt for {r['name']} can't be opened here right now. After a restore, the app copies "
                          "files back from the backup location on its next pass; try again then.",
                          f"{url_for('equipment_list')}#eq-{eid}")
        kind = mimetypes.guess_type(f["name"])[0] or "application/octet-stream"
        return app.response_class(data, mimetype=kind, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f['name'])}",
            "X-Content-Type-Options": "nosniff"})

    def today_items():

        if not active():
            return []
        from datetime import date, timedelta
        t = date.today().isoformat()
        rows = q("SELECT id, name, renews FROM equipment WHERE renews IS NOT NULL AND renews != '' AND renews <= ? "
                 "ORDER BY renews, id", ((date.today() + timedelta(days=RENEW_DAYS)).isoformat(),))
        return [dict(text=f"{r['name']}: {'lapsed' if r['renews'] <= t else 'renews'} on {ctx.nice(r['renews'])}",
                     sub="Renewed? Update the date on Equipment" if r["renews"] <= t else "",
                     late=r["renews"] <= t, link=f"{url_for('equipment_list')}#eq-{r['id']}", when=r["renews"])
                for r in rows]

    ctx.hooks.today.append(today_items)

    def export_packing(con):

        import json
        if not con.execute("SELECT 1 FROM settings WHERE key=?", (f"plugin_on_{ctx.name}",)).fetchone():
            return None
        out = {}
        for r in con.execute("SELECT type_key, lines FROM packing_lists ORDER BY type_key"):
            try:
                lines = [str(v) for v in json.loads(r[1])]
            except ValueError:
                continue
            if lines:
                out[r[0]] = lines
        return out

    def check_packing(raw):
        if not isinstance(raw, dict) or len(raw) > MAX_TYPES:
            raise ValueError("plugins.packing_lists: expected a short list of session types")
        out = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not re.fullmatch(r"[a-z0-9_]{1,30}", k):
                raise ValueError("plugins.packing_lists: a session type's name isn't one the app can use")
            if not isinstance(v, list) or len(v) > MAX_LINES * 2 or not all(isinstance(ln, str) and len(ln) <= 1000 for ln in v):
                raise ValueError(f"plugins.packing_lists.{k}: expected a list of short lines")
            out[k] = clean_lines("\n".join(v))
        return out

    def apply_packing(cleaned):
        import json
        left = []
        for k, lines in cleaned.items():
            if not known_type(k):
                left.append(k)
                continue
            x("""INSERT INTO packing_lists (type_key, lines) VALUES (?, ?)
                 ON CONFLICT(type_key) DO UPDATE SET lines=excluded.lines""", (k, json.dumps(lines)))
        n = len(cleaned) - len(left)
        out = [f"Packing lists for {n} session type{'' if n == 1 else 's'} came in."] if n else []
        if left:
            out.append(f"Packing lists for {len(left)} session type{'' if len(left) == 1 else 's'} this studio doesn't "
                       "have were left out.")
        return out

    ctx.hooks.settings_parts["packing_lists"] = dict(export=export_packing, check=check_packing, apply=apply_packing)

    add_nav = app.config.get("add_nav")
    if add_nav:
        add_nav("equipment", "Equipment", "equipment_list", "finances", active)
