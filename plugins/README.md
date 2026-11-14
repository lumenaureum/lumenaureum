# Writing your own plugins

A plugin runs inside Lumen Aureum with full access: once you sign in, it can read and change every record, see your
keys and reach the internet. Only load plugins you trust. A plugin can break the protections Lumen Aureum is built on,
which can lead to harm including, but not limited to, exposure or loss of data, including client data.

A plugin is one Python file with a `register(ctx)` function. It can add pages, steps to a session's workflow,
sections to the client screen, items on Today, search results, and more, without changing Lumen Aureum's own code.
The plugins in this folder (galleries, letters, retention) are built the same way and are good examples.

## Where your plugins live
Keep your own plugins **outside** this code, in a folder of your own, so updates never touch them:

1. Make a folder, for example `my-plugins/`, next to your `compose.yaml`.
2. Mount it into the container and tell Lumen Aureum about it:
   ```yaml
   services:
     lumen:
       volumes:
         - ./my-plugins:/plugins:ro
       environment:
         CRM_PLUGIN_DIRS: /plugins
   ```
   Several folders can be listed, separated by `:`.
3. Restart the container (`docker compose up -d`). Every `*.py` file in the folder is loaded at start, in name order.
   Files starting with `_` are skipped, so `_draft.py` stays off.

To switch a plugin off without deleting it, list its file name (without `.py`) in `CRM_PLUGINS_OFF`, separated by
commas. That works for the built-in ones too, except `retention`: sealing the unedited files and deleting them on the
day the agreement names is a promise to clients, so it always runs. Online galleries (`gallery`) can be switched off.

If a plugin needs a Python package the image doesn't have, build your own image on top of this one:
```dockerfile
FROM ghcr.io/lumenaureum/lumenaureum:1.0.1     # the version you run
USER root
RUN pip install some-package
USER lumen
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
- `step_labels` (a dict) - `{step label: short label}` for tiles.
- `step_icons` - `(words, Material Symbol name)`: an icon for steps whose title contains one of the words.
- `session_tabs` - `fn(session_row) -> (title, count or None, html)` or None: a section on the session record.
- `session_panels` - `fn(session_row) -> html or None`: shown in the shoot section.
- `stage_blocks` - `fn(session_row) -> [(title, html)]`: sections in the right column of the client screen.
- `detail_locations` - `fn(session) -> [dict(time=, name=, town=, what=, light=)]` or None: the locations on the
  session details sheet sent to the client (the first plugin that answers wins; otherwise the session's own list).
- `search` - `fn(term) -> [dict(title=, rows=[dict(text=, sub=, link=)])]`: extra sections in search results.
- `guards` (a dict) - `ctx.hooks.guards.setdefault("gallery_upload", []).append(fn)` where
  `fn(proceed, **details) -> bool`: return True to hold the action (for an approval, say) and call `proceed()` when it
  may go ahead. `details` for a gallery upload: `sid`, `gallery_id`, `n_files`.
- `tax_region` (set it, don't append) - `dict(name=, areas=[(key, label)], area_of=fn(client_address) -> (area, warning),
  extra_pct=fn(area, local_pct) -> percent added to the base rate)`: sales tax that depends on where the client is.
  When it is set, Settings and setup offer a "regional" sales-tax choice.
- `on["session_created"]` - `fn(session_id)`; `on["stage_changed"]` - `fn(session_id, old_stage, new_stage)`;
  `on["gallery_window_changed"]` - `fn(session_id)`.

Through `ctx.app.config`:
- `ep_words` (dict) - `{endpoint: "plain sentence"}` for the history line of your POST routes.
- `quiet_endpoints` (set) - POST routes that should not appear in history at all.

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
