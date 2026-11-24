# Writing your own plugins

A plugin runs inside Lumen Aureum with full access: once you sign in, it can read and change every record, see your
keys and reach the internet. Only load plugins you trust. A plugin can break the protections Lumen Aureum is built on,
which can lead to harm including, but not limited to, exposure or loss of data, including client data.

A plugin is one Python file with a `register(ctx)` function. It can add pages, steps to a session's workflow,
sections to the client screen, items on Today, search results, and more, without changing Lumen Aureum's own code.
The plugins in this folder (galleries, letters, retention, the studio page, equipment, crew) are built the same way and are good examples.

## Where your plugins live
Keep your own plugins **outside** this code, in a folder of your own, so updates never touch them:

1. Make a folder, for example `my-plugins/`, next to your `compose.yaml`.
2. Mount it into the container and tell Lumen Aureum about it:
   ```yaml
   services:
     lumenaureum:
       volumes:
         - ./my-plugins:/plugins:ro
       environment:
         CRM_PLUGIN_DIRS: /plugins
   ```
   Several folders can be listed, separated by `:`.
3. Restart the container (`docker compose up -d`). Every `*.py` file in the folder is loaded at start, in name order.
   Files starting with `_` are skipped, so `_draft.py` stays off.

To switch a plugin off without deleting it, list its file name (without `.py`) in `CRM_PLUGINS_OFF`, separated by
commas. That works for the built-in ones too, except the core ones, which always run: `retention` (sealing the
unedited files and deleting them on the day the agreement names is a promise to clients) and `print_core` (whatever a
client sent to a gallery is collected before any gallery folder is deleted, and print money stays in Finances,
statements and the accountant export). Online galleries (`gallery`) and print orders (`prints`) can be switched off.

**Settings, Plugins** lists every plugin and its state. A plugin that calls `ctx.switchable(default=..., label=...,
about=..., before_off=fn)` gets an on/off switch there, stored as the setting `plugin_on_<name>` and read at each use
(no restart). The plugin stays loaded and checks `ctx.active()` wherever it acts; `before_off()` returns words to show
(with "Turn off anyway") when turning it off needs a second thought, or "". Optional `when_on=fn` returns words shown
under the switch while the plugin is on (where its work shows up, what to set up next), with `links=[(words,
endpoint)]` beside them. `ctx.plugin_on(name)` tells any plugin
whether another is on. `CRM_PLUGINS_OFF` always wins: a plugin it names is not loaded, and its switch can't turn it on.
Print orders (`prints`) are switchable and **off by default**: on a new install and after the 1.4 update.

If a plugin needs a Python package the image doesn't have, build your own image on top of this one:
```dockerfile
FROM ghcr.io/lumenaureum/lumenaureum:1.6.0     # the version you run
USER root
RUN pip install some-package
USER lumenaureum
```

## A complete example
`my-plugins/thank_you.py`: a "Send a thank-you card" step two days after every session, and a Today reminder.

```python
from datetime import date, timedelta


def register(ctx):
    ctx.migrate("""CREATE TABLE IF NOT EXISTS thank_you (session_id INTEGER PRIMARY KEY, sent TEXT)""")

    def steps(s):
        """Workflow steps that tick themselves: (stage, label, done, section)."""
        if s["stage"] not in ("shot", "delivered", "closed"):
            return []
        sent = ctx.q("SELECT sent FROM thank_you WHERE session_id=?", (s["id"],), one=True)
        return [("delivered", "Thank-you card sent", bool(sent), "thank-you")]

    def section(s):
        """A section on the session record: (title, count or None, html)."""
        sent = ctx.q("SELECT sent FROM thank_you WHERE session_id=?", (s["id"],), one=True)
        if sent:
            return ("Thank-you", None, f"<p>Card sent {ctx.nice(sent['sent'])}.</p>")
        url = ctx.app.url_for("thank_you_sent", sid=s["id"])
        return ("Thank-you", None, f'<form method="post" action="{url}"><input type="hidden" name="back" value="/session/{s["id"]}#thank-you">'
                f'<button>Mark card sent</button></form>')

    @ctx.app.post("/thank-you/<int:sid>", endpoint="thank_you_sent")
    def thank_you_sent(sid):
        from flask import redirect, request
        ctx.x("INSERT OR REPLACE INTO thank_you (session_id, sent) VALUES (?, ?)", (sid, date.today().isoformat()))
        return redirect(ctx.safe_back(request.form.get("back")) or "/")

    def today():
        cutoff = (date.today() - timedelta(days=2)).isoformat()
        rows = ctx.q("""SELECT s.id, s.household_id, h.name FROM sessions s JOIN households h ON h.id = s.household_id
                        WHERE s.shoot_date <= ? AND s.stage IN ('shot', 'delivered')
                        AND s.id NOT IN (SELECT session_id FROM thank_you)""", (cutoff,))
        return [dict(text=f"Send {r['name']} a thank-you card", link=f"/session/{r['id']}#thank-you",
                     who=dict(name=r["name"], link=f"/session/{r['id']}", hid=r["household_id"]), when=cutoff)
                for r in rows]

    ctx.hooks.steps.append(steps)
    ctx.hooks.session_tabs.append(section)
    ctx.hooks.today.append(today)
    ctx.hooks.step_icons.append((("thank-you",), "favorite"))
    ctx.app.config.setdefault("ep_words", {})["thank_you_sent"] = "Marked the thank-you card sent"
```

## What `ctx` gives you
- `ctx.app` - the Flask app. Add routes with `@ctx.app.get(...)` / `@ctx.app.post(...)`, always with `endpoint=`.
  Every page is already behind sign-in and the network allow-list.
- `ctx.q(sql, args, one=False)` / `ctx.x(sql, args)` - read / write the database (it is encrypted on disk; you never
  handle that).
- `ctx.connect()` - a direct connection, for work outside a page request (close it yourself).
- `ctx.migrate(sql)` - create your own tables (`CREATE TABLE IF NOT EXISTS ...`). It runs once the database is unlocked,
  after sign-in. Never alter the built-in tables.
- `ctx.on_unlock(fn)` - run `fn()` once the database is open (also usable as `@ctx.on_unlock`).
- `ctx.unlocked()` - False after a restart until the owner signs in. Background threads should wait while it is False.
- `ctx.audit(text, sid=, hid=, who=)` - add a line to a client's history.
- `ctx.render`, `ctx.C` (the business rules), `ctx.nice(date)`, `ctx.money(amount)`, `ctx.advance(session_id)`.
- `ctx.html_pdf(html, filename)` - turn HTML into a PDF download.
- Scripts: inline handlers (`onclick=...`) are refused by the page's security policy. Put script in a
  `<script nonce="{{ csp_nonce() }}">` block and pass values through `data-` attributes, never into the script text.
  The core's `static/ui.js` already handles `data-confirm`, `data-print`, `data-copy`, `data-autosubmit`.
- `ctx.csp(extra)` - the security policy with extra script sources, for a whole page a plugin serves itself.

## Hooks
Append functions to these (`ctx.hooks.<name>.append(fn)`):
- `nav` - `(label, endpoint)`: a link in the top bar.
- `today` - `fn() -> [dict(text=, link=, sub=)]`: items on Today, in its "Also" list. Give an item
  `who=dict(name=, link="/session/<id>", hid=<household id>)` and `when=` (ISO date) to put it on that client's tile
  instead, with its due date.
- `steps` - `fn(session_row) -> [(stage, label, done, section)]`: extra workflow steps that tick themselves.
- `step_actions` - `fn(step_label, session_row) -> dict(verb=, href=)` or None: the button for a step.
- `step_notes` - `fn(step_label, session_row) -> str` or None: a short line shown under a step on Today and the session page.
- `session_actions` - `fn(session_row) -> [dict(what=, verb=, post= or href=, fields=, theirs=, since=)]`: things to do
  (or to wait for, with `theirs=True`) that belong to the session as a whole, whatever its stage. Each shows once, on
  Today and the session page.
- `client_checks` - `fn(session_ids) -> dict(got=[phrase, ...], failed=bool)`: Check now, on the client page's From the
  client line at the top of its needs-you block (rows on Today waiting on the client post to `client_check_now` too). Read, for just those sessions, what the
  client can send through the web host, record it as the regular check does (with its own History lines), and say each
  thing that came in as a short phrase ("1 new print order"); `failed=True` when some of it could not be read now. The
  app joins every plugin's phrases into the one note shown in place. Anything new a client can send (a session link,
  say) plugs in here, so the page keeps one Check now.
- `client_online` - `fn(session_ids) -> dict(n=, at=)`: how many things of the client's are online to check, and when
  the app last read them (epoch seconds or None). The From the client line shows whenever any plugin says n > 0.
  A row a plugin marks `client_check=True` keeps its Check now on Today; on the client page it gives way to the line.
- `step_labels` (a dict) - `{step label: short label}` for tiles.
- `step_icons` - `(words, Material Symbol name)`: an icon for steps whose title contains one of the words.
- `session_tabs` - `fn(session_row) -> (title, count or None, html)` or None: a section on the session record.
- `session_panels` - `fn(session_row) -> html or None`: shown in the shoot section.
- `stage_blocks` - `fn(session_row) -> [(title, html)]`: sections in the right column of the client screen.
- `detail_locations` - `fn(session) -> [dict(time=, name=, note=)]` or None: the places on the session sheet sent to
  the client, each with a time and a note (one or more lines) under its name (the first plugin that answers wins;
  otherwise the session's own Where list).
- `search` - `fn(term) -> [dict(title=, rows=[dict(text=, sub=, link=)])]`: extra sections in search results.
- `guards` (a dict) - `ctx.hooks.guards.setdefault("gallery_upload", []).append(fn)` where
  `fn(proceed, **details) -> bool or str`: return True to hold the action and call `proceed()` when it may go ahead.
  Return a short sentence instead of True to hold it with your own words for the wait (shown on the gallery and on
  Today; otherwise "Upload on hold"). A held gallery upload also gets `give_up(words)`: call it when the upload will
  not go ahead, and the gallery is ready to upload again with your words beside its Upload button. `details` for a gallery upload: `sid`, `gallery_id`, `n_files`, and `update=True` when a rebuilt
  gallery goes over the one already online (the copy online stays up meanwhile). Moving every gallery to a new host
  asks once: `move=True`, `n_galleries`, `n_files`, with `sid` and `gallery_id` None. The studio page's upload asks the
  same guard: `site=True`, `n_files`, `site_url`, with `sid` and `gallery_id` None (taking the site down adds
  `remove=True`). Taking the old copies down from an
  earlier web host asks it too: `remove=True`, `earlier_host=True`, with `sid` and `gallery_id` None.
- `type_panels` - `fn(type_dict) -> html or None`: a section of a session type's page (Settings, Session types), below
  the type's own form. Give it its own form posting to your own route (the packing list in `equipment.py` does).
- `settings_parts` (a dict, set a key) - `{name: dict(export=fn(con), check=fn(raw), apply=fn(cleaned))}`: your part of
  the settings file (Settings, Export and Import, and the copy in each backup), under `"plugins": {name: ...}`.
  `export` returns a JSON value, or None to leave it out; `check` cleans what a file brings (raise ValueError with the
  words to show: nothing in the file is applied); `apply` writes it, after the file's rules (session types included),
  and returns notice lines. Settings only: never client records or secrets. Packing lists in `equipment.py` use it.
- `settings_tabs` - `(label, endpoint, sub)`: a page of your plugin's own settings, listed under Plugins in Settings.
  Add the endpoint to `ctx.app.jinja_env.globals["SETTINGS_ENDPOINTS"]` so it opens beside the Settings list.
- `tax_rules` - `fn(sale, taxes) -> {tax key: dict(on=, rate=, note=)}`: when a tax applies, and at what rate, for a
  sale (each part optional; `True` or `False` alone means on or off). `sale` is `dict(session_id=, address=, type=)`
  (`session_id` None for a print order, whose address is where it ships); `taxes` is the list in Settings, Taxes
  (`key, name, rate, included, default`). Rules run in order, a later one over an earlier one; `note` shows beside the
  tax's switch on the session. They apply to open quotes only: once the client says yes, each tax is kept as it was,
  and a switch set on the session by hand always wins. `plugins/examples/city_tax.py` is a complete one: a city that
  adds its own tax for clients inside it. Files in `plugins/examples/` are never loaded from there; copy one into your
  plugins folder to use it.
- `on["session_created"]` - `fn(session_id)`; `on["stage_changed"]` - `fn(session_id, old_stage, new_stage)`;
  `on["gallery_window_changed"]` - `fn(session_id)`.

Through `ctx.app.config`:
- `ep_words` (dict) - `{endpoint: "plain sentence"}` for the history line of your POST routes.
- `quiet_endpoints` (set) - POST routes that should not appear in history at all.

## The lab hook (print orders)
A plugin can talk to one print lab for the photographer. Core never contacts a lab: with no lab plugin every order is
prepared for the lab by hand (Prepare for the lab), and that stays available whatever a plugin does. A lab plugin is
off until the photographer connects it in its own settings, and "Where prints are made" (Settings, Print prices) lists
it beside "By hand, at any lab" while its `ready()` is true. Core keeps each order's state, money and review rules; a
plugin only reports what happened at its lab, and core decides whether that move is allowed.

Register a dict with `ctx.hooks.labs.append(lab)`. Only `id`, `name`, `ready` and `send` are required.

| Key | Signature | Core uses it for |
|---|---|---|
| `id`, `name` | strings | `id` (letters, digits, `-`) keys item references and orders; `name` is shown. |
| `ready` | `fn() -> (ok, words)` | Whether the lab can be chosen and used now. |
| `item_fields` | `fn(item) -> html or None` | Fields under a price list size, to tie it to the lab's product. |
| `item_save` | `fn(item, form) -> (ref, error)` | Stores the lab's reference in the size's `lab[id]` (any JSON value; core never reads inside it). |
| `starter_items` | `fn() -> [dict(label, w, h, finish, each, cost, lab)]` | "Use <lab>'s starter list" in place of the SAMPLE list. |
| `costs` | `fn(items) -> {item_id: dict(cents=, as_of=, words=)}` | "Fill costs from <lab>": only on the photographer's press. |
| `shipping_quote` | `fn(order) -> dict(cents=, lab_tax_cents=, total_cost_cents=, words=) or None` | "Shipping from <lab>" at review. |
| `file_rules` | dict: `max_bytes`, `quality`, `color` | Applied when core cuts the print files for this lab (a size cap re-encodes at 90, then 85). |
| `host_folder` | string ending `.d` (default `<id>.d`) | The plugin's own folder on the web host, beside the gallery folders (a link ID never holds a ".", so the two never collide). |
| `can_send` | `fn(order) -> (ok, [reasons])` | Before "Send to <lab>". |
| `send` | `fn(order) -> dict(accepted=bool, words=)` | The hand-off; return at once. Accepted: the order is "with the lab", showing `words`. |
| `status` | `fn(order) -> dict(words=) or None` | "Check now" on an order. Report changes through `report`, not here. |
| `cancel` | `fn(order, confirm=False) -> dict(outcome=, words=)` | Without `confirm`, what would happen; with it, act. `outcome`: `canceled`, `requested`, `kept`, `not_possible`, `not_placed`. |

`order` is a read-only snapshot: `id`, `oid`, `invoice`, `state`, `paid_in_full`, `lines` (`n`, `item`, `lab` (your
reference for that size), `qty`, `label`, `w`, `h`) and `ship` (`how`, `name`, address lines, `city`, `state`, `zip`,
`country`, `phone`). Never the orderer's email, never the gallery key.

What core gives a lab plugin, through `ctx.prints`:
- `order(order_id)`: the snapshot above.
- `print_files(order_id, lab_id)`: an iterator of `(file_id, file_name, line_numbers, jpeg_bytes)`, exactly the files
  Prepare for the lab makes, cut in memory one at a time with your `file_rules`. It runs the same checks (the photo
  opens, every cropped line was marked Looks right, no line below its size's floor) and raises with the reason.
- `hand_off(order_id, lab_id, automatic=False)`: core refuses unless the order is paid in full (by hand only, an order
  the photographer marked Order before payment; never with `automatic=True`), every cropped line looks right, no line
  is below its floor, and its review flags are checked. Then it calls `can_send` and `send`.
- `report(order_id, lab_id, what, numbers=, carrier=, tracking=, url=, outcome=, cost=dict(products=, shipping=,
  lab_tax=, other=, pending=), words=)`: the only way a plugin moves an order. `what` is `placed`, `shipped`,
  `delivered`, `given_back` or `canceled` (only while it still holds the order); any other move raises.
- `paid_in_full(order_id)`.
- `host(lab_id)`: your folder on the web host: `put(name, bytes)`, `delete(name)`, `list()`, `public_url(name)`,
  `fetch_public(name)` and `probe()` (the host's last PHP check). Names can't leave the folder.
- `secret_get(name)`, `secret_set(name, value)`: encrypted storage in the database for your keys; prefix the names
  with your `id`. Never exported in the settings file.

Events through `ctx.hooks.on`: `print_order_paid(order_id)`, `print_order_changed(order_id, old, new)`,
`host_refit(host_fp)`, `gallery_host_changed(old_fp, new_fp)`, `host_gone(host_fp)`.

Rules core keeps: any call to a lab plugin that raises or takes more than 30 seconds changes nothing; it is logged and
Today says "<lab> had a problem with <name>'s order". Prepare for the lab on an order a plugin holds asks first, then
calls its `cancel`. Only one plugin holds an order at a time. Use the `today` and `settings_tabs` hooks for your own
Today lines and settings page.

## Pages that look like the app
Use `render_template_string` with `{% extends "base.html" %}` and put your page in `{% block body %}`.
The app's look: dark theme via CSS variables (`--bg --surface --surface-2 --ink --mute --line --gold --warn --crit`);
color means status only (yellow warning, red problem); buttons do things, links ending in `›` go somewhere; hover
text (`data-tip="..."`) carries detail. Every POST is recorded in the client's history automatically.

## Good to know
- A plugin runs with the app's full access to your data. Only install plugins you wrote or trust.
- Plugins load before anyone signs in, so do database work in `ctx.migrate`, `ctx.on_unlock`, routes or hooks,
  never at the top level of `register`.
- After changing a plugin, restart the container.
