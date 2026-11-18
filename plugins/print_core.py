import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import g, url_for

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import printorders as P

ORDER_NAME = re.compile(r"^(?:.*/)?([0-9a-f]{32})\.las$")
ORDERS_KEEP = "orders/index.html"
ORDERS_KEEP_OLD = "orders/keep.txt"
UNOPENED_KEEP_DAYS = 30

class ModuleView:

    def __init__(self, glb):
        object.__setattr__(self, "_g", glb)

    def __getattr__(self, name):
        try:
            return self._g[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self._g[name] = value

_GMOD = [None]

def _G():

    if _GMOD[0] is None:
        from plugins import gallery as m
        return m
    return _GMOD[0]

def _gkey(r):
    return _G().key_bytes(r["ekey"] or r["key"])

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    if app.config.get("gallery_take_down"):
        _GMOD[0] = ModuleView(app.config["gallery_take_down"].__globals__)
    G = _G()
    app.config["gallery_module"] = G

    def plv():
        try:
            return max(1, int(setting("prints_plv") or 1))
        except ValueError:
            return 1

    def items_ro():

        rows = [dict(r_) for r_ in q("SELECT * FROM price_list_items ORDER BY sort, id")]
        if not rows:
            rows = [dict(id=i_["id"], label=i_["label"], w=i_["w"], h=i_["h"], finish=i_["finish"], each_cents=i_["each"])
                    for i_ in P.starter_items()]
        return rows

    ctx.migrate("""
CREATE TABLE IF NOT EXISTS price_list_items (id TEXT PRIMARY KEY, label TEXT NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  finish TEXT DEFAULT '', each_cents INTEGER, cost_cents INTEGER, cost_from TEXT DEFAULT '', cost_date TEXT DEFAULT '',
  price_set INTEGER DEFAULT 0, ppi_hide INTEGER DEFAULT 150, ppi_mark INTEGER DEFAULT 200, offered INTEGER DEFAULT 1,
  lab TEXT DEFAULT '{}', sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS price_list_versions (plv INTEGER PRIMARY KEY, json TEXT NOT NULL, saved_at TEXT);
CREATE TABLE IF NOT EXISTS print_galleries (session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  gid TEXT, off INTEGER DEFAULT 0, offer_sig TEXT DEFAULT '', offered_at TEXT, ever INTEGER DEFAULT 0, host_fp TEXT);
CREATE TABLE IF NOT EXISTS print_orders (id INTEGER PRIMARY KEY, oid TEXT NOT NULL UNIQUE, gid TEXT NOT NULL,
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, state TEXT NOT NULL, prev_state TEXT,
  contact_id INTEGER REFERENCES people(id) ON DELETE SET NULL, guest INTEGER DEFAULT 0, bill_name TEXT DEFAULT '',
  bill_email TEXT DEFAULT '', payload TEXT NOT NULL, env_sha256 TEXT, plv INTEGER, browser_at TEXT, host_mtime TEXT,
  collected_at TEXT, invoice_number TEXT, invoice_issued TEXT, invoice_data TEXT, invoice_html TEXT, invoice_void TEXT,
  ship_lab_cents INTEGER, ship_cents INTEGER, tax_rate REAL, tax_area TEXT, flags TEXT DEFAULT '{}',
  before_payment INTEGER DEFAULT 0, prepared_at TEXT, lab_id TEXT, lab_words TEXT DEFAULT '', lab_name TEXT DEFAULT '',
  lab_orders TEXT DEFAULT '[]', cost_products INTEGER, cost_shipping INTEGER, cost_tax INTEGER, cost_other INTEGER,
  lab_tax_pending INTEGER DEFAULT 0, ordered_at TEXT, shipped_at TEXT, delivered_at TEXT, closed_at TEXT,
  remake_note TEXT DEFAULT '', cancel_lab TEXT DEFAULT '', told_shipped TEXT, reduced_at TEXT, lab_problem TEXT DEFAULT '',
  ship_by_hand INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS print_orders_sid ON print_orders(session_id);
CREATE TABLE IF NOT EXISTS print_order_lines (id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES print_orders(id) ON DELETE CASCADE, n INTEGER NOT NULL, photo_file TEXT NOT NULL,
  photo_name TEXT DEFAULT '', item_id TEXT NOT NULL, label TEXT DEFAULT '', w REAL, h REAL, finish TEXT DEFAULT '',
  qty INTEGER NOT NULL, crop TEXT NOT NULL, turn INTEGER DEFAULT 0, each_cents INTEGER NOT NULL, ppi INTEGER DEFAULT 0,
  crop_confirmed INTEGER DEFAULT 0, soft_confirmed INTEGER DEFAULT 0, shown_cents INTEGER);
CREATE TABLE IF NOT EXISTS print_order_oids (oid TEXT PRIMARY KEY, gid TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS print_payments (id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES print_orders(id) ON DELETE CASCADE, kind TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  received TEXT NOT NULL, method TEXT DEFAULT '', ref TEXT DEFAULT '', voided TEXT);
CREATE TABLE IF NOT EXISTS print_books (id INTEGER PRIMARY KEY, day TEXT NOT NULL, what TEXT NOT NULL, amount_cents INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS print_lab_secrets (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS print_lab_costs (id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES print_orders(id) ON DELETE CASCADE, day TEXT NOT NULL, kind TEXT NOT NULL,
  lab_name TEXT DEFAULT '', lab_number TEXT DEFAULT '', products INTEGER DEFAULT 0, shipping INTEGER DEFAULT 0,
  tax INTEGER DEFAULT 0, other INTEGER DEFAULT 0, reverses INTEGER, at TEXT);
CREATE INDEX IF NOT EXISTS print_lab_costs_order ON print_lab_costs(order_id);
""")

    def _off_n_from_1_4_0(con):

        st = {r_[0]: r_[1] for r_ in con.execute("SELECT key, value FROM settings WHERE key IN ('plugin_on_prints', "
                                                 "'plugin_off_at_prints', 'plugin_off_n_prints')")}
        if st.get("plugin_on_prints") != "0" or not st.get("plugin_off_at_prints") or st.get("plugin_off_n_prints"):
            return False
        con.execute("INSERT INTO settings (key, value) VALUES ('plugin_off_n_prints', '1')")
        for id_, fl_ in con.execute("SELECT id, flags FROM print_orders WHERE COALESCE(collected_at, '') >= ?",
                                    (st["plugin_off_at_prints"],)).fetchall():
            try:
                d_ = json.loads(fl_ or "{}")
            except ValueError:
                d_ = {}
            d_["off_n"] = 1
            con.execute("UPDATE print_orders SET flags=? WHERE id=?", (json.dumps(d_), id_))
        return True

    @ctx.on_unlock
    def _later_columns():

        con = ctx.connect()
        try:
            for table_, col_, decl_ in (("print_order_lines", "shown_cents", "INTEGER"),
                                        ("print_orders", "ship_by_hand", "INTEGER DEFAULT 0")):
                if col_ not in [r_[1] for r_ in con.execute(f"PRAGMA table_info({table_})")]:
                    ctx.add_column(con, table_, col_, decl_)
                    con.commit()
            if _off_n_from_1_4_0(con):
                con.commit()
        finally:
            con.close()

    @ctx.on_unlock
    def _lab_costs_as_paid():

        con = ctx.connect()
        try:
            rows = con.execute("""SELECT * FROM print_orders o WHERE ordered_at IS NOT NULL AND (cost_products IS NOT NULL
                OR cost_shipping IS NOT NULL OR cost_tax IS NOT NULL OR cost_other IS NOT NULL)
                AND NOT EXISTS (SELECT 1 FROM print_lab_costs c WHERE c.order_id=o.id)""").fetchall()
            for r_ in rows:
                try:
                    nums = json.loads(r_["lab_orders"] or "[]")
                except ValueError:
                    nums = []
                num_ = str((nums[0] if nums else {}).get("number") or "")
                cur = con.execute("""INSERT INTO print_lab_costs (order_id, day, kind, lab_name, lab_number, products, shipping,
                    tax, other, at) VALUES (?,?,?,?,?,?,?,?,?,?)""", (r_["id"], r_["ordered_at"][:10], "order", r_["lab_name"] or "",
                    num_, r_["cost_products"] or 0, r_["cost_shipping"] or 0, r_["cost_tax"] or 0, r_["cost_other"] or 0,
                    r_["ordered_at"]))
                if r_["state"] == "canceled" and (r_["cancel_lab"] or "") == "Canceled at the lab":
                    day_ = (r_["closed_at"] or r_["ordered_at"])[:10]
                    con.execute("""INSERT INTO print_lab_costs (order_id, day, kind, lab_name, lab_number, products, shipping,
                        tax, other, reverses, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (r_["id"], day_, "refund", r_["lab_name"] or "",
                        num_, -(r_["cost_products"] or 0), -(r_["cost_shipping"] or 0), -(r_["cost_tax"] or 0),
                        -(r_["cost_other"] or 0), cur.lastrowid, day_))
            con.commit()
        finally:
            con.close()

    def appctx():

        import contextlib
        from flask import has_app_context
        return contextlib.nullcontext() if has_app_context() else app.app_context()

    def setting(k, d=""):
        r_ = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
        return r_["value"] if r_ and r_["value"] is not None else d

    def put(k, v):
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))

    def studio_pem():

        pem = setting("site_inquiry_key")
        if not pem:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            k = ec.generate_private_key(ec.SECP256R1())
            pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
            put("site_inquiry_key", pem)
        return pem

    def key_map():

        from cryptography.hazmat.primitives import serialization
        import sealed
        out = {}
        pems = [studio_pem()]
        try:
            pems += json.loads(setting("prints_old_keys") or "[]")
        except ValueError:
            pass
        for pem in pems:
            try:
                k = serialization.load_pem_private_key(pem.encode(), password=None)
            except Exception:
                continue
            raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            out[sealed.kid_of(raw)] = k
        return out

    def studio_public():
        from cryptography.hazmat.primitives import serialization
        k = serialization.load_pem_private_key(studio_pem().encode(), password=None)
        return k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def gallery_row(sid=None, gid=None):
        if sid is not None:
            return q("""SELECT g.*, s.gallery_id, s.type, s.id AS sid, s.household_id FROM galleries g JOIN sessions s
                        ON s.id=g.session_id WHERE g.session_id=?""", (sid,), one=True)
        return q("""SELECT g.*, s.gallery_id, s.type, s.id AS sid, s.household_id FROM galleries g JOIN sessions s
                    ON s.id=g.session_id WHERE s.gallery_id=? OR g.link_was=? ORDER BY s.gallery_id=? DESC""",
                 (gid, gid, gid), one=True)

    def unopened_dir(gid):
        return G.KEPT_ROOT / gid / "orders-unopened"

    def host_mtime(host, name):
        try:
            if getattr(host, "kind", "") == "local":
                return datetime.fromtimestamp(os.path.getmtime(host._p(name))).isoformat(timespec="seconds")
            if getattr(host, "kind", "") == "sftp":
                t_ = host._run(lambda sf: sf.stat(host._p(name)).st_mtime)
                return datetime.fromtimestamp(t_).isoformat(timespec="seconds")
        except Exception:
            pass
        return None

    def match_contact(sid, email):

        e_ = (email or "").strip().lower()
        if not e_:
            return None
        s_ = q("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True)
        if s_:
            r_ = q("SELECT id FROM people WHERE household_id=? AND lower(trim(email))=? ORDER BY id", (s_["household_id"], e_), one=True)
            if r_:
                return r_["id"]
        r_ = q("""SELECT p.id FROM people p JOIN households h ON h.id=p.household_id WHERE lower(trim(p.email))=?
                  AND COALESCE(h.demo, 0)=0 ORDER BY p.id""", (e_,), one=True)
        return r_["id"] if r_ else None

    def record_order(gid, sid, env, data, mtime, refused=""):

        import locks
        sha = hashlib.sha256(env).hexdigest()
        oid = data.get("oid") if isinstance(data, dict) and isinstance(data.get("oid"), str) and P.OID_OK.fullmatch(
            data.get("oid") or "") else "x" + sha[:31]
        cid, known, old, priced = None, {}, {}, {}
        if not refused:
            with appctx():
                cid = match_contact(sid, data["who"]["email"])
                known = {i_["id"]: i_ for i_ in items_ro()}
                old = old_items(data["plv"])
                priced = price_list_of(data["plv"])
        if not locks.WRITES.acquire(timeout=60):
            raise RuntimeError("the app is busy; the order waits for the next check")
        from flask import has_app_context
        own = not (has_app_context() and "db" in g)
        try:
            con = ctx.connect() if own else g.db
            if not own:
                con.commit()

            try:
                con.execute("BEGIN IMMEDIATE")
                if con.execute("SELECT 1 FROM print_order_oids WHERE oid=?", (oid,)).fetchone():
                    con.execute("ROLLBACK")
                    return False
                now_ = datetime.now().isoformat(timespec="seconds")
                con.execute("INSERT INTO print_order_oids (oid, gid, first_seen) VALUES (?,?,?)", (oid, gid, now_))

                off_ = con.execute("SELECT value FROM settings WHERE key='plugin_off_n_prints'").fetchone()
                off_n = 0 if prints_on_now() else int((off_[0] if off_ else 0) or 0)
                if refused:
                    con.execute("""INSERT INTO print_orders (oid, gid, session_id, state, payload, env_sha256, host_mtime,
                                   collected_at, flags) VALUES (?,?,?,?,?,?,?,?,?)""",
                                (oid, gid, sid, "unreadable", json.dumps(data) if not isinstance(data, str) else data, sha,
                                 mtime, now_, json.dumps({"why": refused, **({"off_n": off_n} if off_n else {})})))
                else:
                    cur = con.execute("""INSERT INTO print_orders (oid, gid, session_id, state, contact_id, bill_name,
                                         bill_email, payload, env_sha256, plv, browser_at, host_mtime, collected_at, flags)
                                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                      (oid, gid, sid, "new" if cid else "unmatched", cid, data["who"]["name"],
                                       data["who"]["email"], json.dumps(data), sha, data["plv"], data["at"], mtime, now_,
                                       json.dumps({"off_n": off_n}) if off_n else "{}"))
                    order_id = cur.lastrowid
                    for n_, ln in enumerate(data["lines"], 1):
                        it = known.get(ln["item"]) or old.get(ln["item"]) or {}

                        each = list_price(priced, ln["item"])
                        con.execute("""INSERT INTO print_order_lines (order_id, n, photo_file, photo_name, item_id, label, w, h,
                                       finish, qty, crop, turn, each_cents, ppi, crop_confirmed, shown_cents)
                                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (order_id, n_, ln["p"], ln["n"], ln["item"], it.get("label") or ln["item"],
                                     it.get("w"), it.get("h"), it.get("finish") or "", ln["qty"], json.dumps(ln["crop"]),
                                     ln["turn"], ln["each"] if each is None else each, ln["ppi"],
                                     1 if ln["crop"] == [0.0, 0.0, 1.0, 1.0] else 0, ln["each"]))
                    con.execute("INSERT INTO audit (at, household_id, session_id, action, who) VALUES (?,?,?,?,?)",
                                (now_, None, sid, f"Print order received from {data['who']['name']}: "
                                 + P.n_prints(sum(l_['qty'] for l_ in data['lines']))
                                 + ("" if cid else " (from someone not on file: on the review list)"), "Lumen Aureum"))
                con.execute("COMMIT")
                return True
            except BaseException:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                if own:
                    con.close()
        finally:
            locks.WRITES.release()

    def price_list_of(v):

        got = old_items(v)
        if not got and v == plv():
            got = {i_["id"]: i_ for i_ in items_ro()}
        return got

    def list_price(priced, iid):
        it = priced.get(iid)
        try:
            return int(it["each_cents"]) if it and it.get("each_cents") is not None else None
        except (TypeError, ValueError):
            return None

    def old_items(v):
        r_ = q("SELECT json FROM price_list_versions WHERE plv=?", (v,), one=True)
        try:
            return {i_["id"]: i_ for i_ in json.loads(r_["json"])["items"]} if r_ else {}
        except (ValueError, KeyError, TypeError):
            return {}

    def collect_from(gid, host, strict=True):

        with appctx():
            r_ = gallery_row(gid=gid)
            kmap = key_map() if r_ else None
        if not r_:
            return 0
        import sealed
        folder, gid = gid, r_["gallery_id"]
        if folder != gid or not G.is_link(folder):
            import upgrade_140

            def open_(env_):
                try:
                    return upgrade_140.open_order_before_links(kmap, env_, folder, _gkey(r_))
                except ValueError:
                    raise sealed.SealedError("E_AUTH") from None
        else:
            def open_(env_):
                return sealed.open_studio("print-order", kmap, env_, gid=gid, link_key=_gkey(r_))
        new = 0
        names = [n_ for n_ in host.list(f"{folder}/orders/") if ORDER_NAME.match(n_) and n_.startswith(f"{folder}/orders/")]
        for name in names:
            try:
                size = host.stat(name)
                if size is None:
                    continue
                buf = io.BytesIO()
                if size <= 65536:
                    host.get_stream(name, buf)
                env = buf.getvalue()
                mt = host_mtime(host, name)
                try:
                    plain = open_(env)
                except sealed.SealedError:
                    d_ = unopened_dir(gid)
                    d_.mkdir(parents=True, exist_ok=True)
                    (d_ / name.rsplit("/", 1)[-1]).write_bytes(env)
                    host.delete(name)
                    with appctx():
                        ctx.audit("A file in the gallery's orders folder could not be opened; it was set aside and "
                                  "removed from the host", sid=r_["sid"], who="Lumen Aureum")
                    continue
                try:
                    data = json.loads(plain)
                    clean_ = P.check_payload(data)
                    rec = record_order(gid, r_["sid"], env, clean_, mt)
                except (ValueError, P.Refused) as e:
                    raw_ = plain.decode("utf-8", "replace")[:70000]
                    try:
                        data = json.loads(raw_)
                    except ValueError:
                        data = raw_
                    rec = record_order(gid, r_["sid"], env, data, mt, refused=str(e)[:200] or "unreadable")
                new += 1 if rec else 0
                host.delete(name)
            except Exception:
                app.logger.exception("prints: collecting %s", name)
                if strict:
                    raise
        return new

    G.BEFORE_DELETE.append(lambda gid, host: collect_from(gid, host, strict=True))

    def collect_all():

        if not G.has_host():
            return 0
        fp = G.host_fingerprint()
        rows = q("""SELECT s.gallery_id FROM galleries g JOIN sessions s ON s.id=g.session_id
                    JOIN print_galleries p ON p.session_id=g.session_id
                    WHERE g.upload_state='up' AND g.removed_at IS NULL AND p.ever=1 AND (g.host_fp IS NULL OR g.host_fp=?)""", (fp,))
        n = 0
        host = None
        for r_ in rows:
            if not G.GID_OK.match(r_["gallery_id"] or ""):
                continue
            host = host or G.gallery_host()
            try:
                with host.session():
                    n += collect_from(r_["gallery_id"], host, strict=False)
            except Exception:
                app.logger.exception("prints: collecting from %s", r_["gallery_id"])
        return n

    def trim_ship():

        cut = (date.today() - timedelta(days=P.REDUCE_AFTER_DAYS)).isoformat()
        for r_ in q("""SELECT id, payload, contact_id, session_id FROM print_orders WHERE reduced_at IS NULL
                       AND state IN ('closed', 'canceled', 'expired') AND closed_at IS NOT NULL AND substr(closed_at,1,10) <= ?""", (cut,)):
            data = json.loads(r_["payload"] or "{}")
            sh = data.get("ship") or {}
            if sh.get("reduced") or not P.trimmed(sh, bool(r_["contact_id"])):
                x("UPDATE print_orders SET reduced_at=? WHERE id=?", (date.today().isoformat(), r_["id"]))
                continue
            data["ship"] = P.reduce_ship(sh)
            x("UPDATE print_orders SET payload=?, reduced_at=? WHERE id=?", (json.dumps(data), date.today().isoformat(), r_["id"]))
            ctx.audit("A print order's ship-to was reduced to city, state and ZIP (a year after the order closed)",
                      sid=r_["session_id"], who="Lumen Aureum")

    def held(sid):

        if q(f"SELECT 1 FROM print_orders WHERE session_id=? AND state IN ({','.join('?' * len(P.OPEN))})",
             (sid, *P.OPEN), one=True):
            return True
        r_ = q("""SELECT pg.offer_sig FROM print_galleries pg JOIN galleries g ON g.session_id=pg.session_id
                  WHERE pg.session_id=? AND g.removed_at IS NULL AND (g.upload_state='up' OR g.uploaded_at IS NOT NULL)""",
               (sid,), one=True)
        return bool(r_ and r_["offer_sig"])

    app.config["print_hold"] = held

    def held_money(sid):

        r_ = q("""SELECT COALESCE(SUM(CASE WHEN p.kind='refund' THEN -p.amount_cents ELSE p.amount_cents END), 0) v
                  FROM print_payments p JOIN print_orders o ON o.id=p.order_id WHERE o.session_id=? AND p.voided IS NULL""",
               (sid,), one=True)
        return (r_["v"] if r_ else 0) / 100

    def keep_books(sids):

        for sid in sids:
            for r_ in q("""SELECT p.kind, p.amount_cents, p.received FROM print_payments p JOIN print_orders o ON o.id=p.order_id
                           WHERE o.session_id=? AND p.voided IS NULL""", (sid,)):
                x("INSERT INTO print_books (day, what, amount_cents) VALUES (?,?,?)",
                  (r_["received"], r_["kind"], r_["amount_cents"] if r_["kind"] == "payment" else -r_["amount_cents"]))
            for r_ in q("""SELECT c.* FROM print_lab_costs c JOIN print_orders o ON o.id=c.order_id WHERE o.session_id=?""",
                        (sid,)):
                c_, t_ = r_["products"] + r_["shipping"] + r_["other"], r_["tax"]
                if c_:
                    x("INSERT INTO print_books (day, what, amount_cents) VALUES (?,?,?)", (r_["day"], "lab_cost", c_))
                if t_:
                    x("INSERT INTO print_books (day, what, amount_cents) VALUES (?,?,?)", (r_["day"], "lab_tax", t_))

    app.config["print_money_held"] = held_money
    app.config["print_orders_of"] = lambda sid: q("SELECT COUNT(*) n FROM print_orders WHERE session_id=?", (sid,),
                                                  one=True)["n"]
    app.config["print_keep_books"] = keep_books

    def year(y):

        y = str(y)
        sales = ship = tax = refunds = received = 0
        rows = []

        parts, runs = {}, {}
        for r_ in q("""SELECT p.*, o.invoice_data FROM print_payments p JOIN print_orders o ON o.id=p.order_id
                       WHERE p.voided IS NULL AND p.order_id IN (SELECT order_id FROM print_payments WHERE voided IS NULL
                       AND substr(received,1,4)=?) ORDER BY p.order_id, p.received, p.id""", (y,)):
            try:
                inv = json.loads(r_["invoice_data"] or "{}")
            except ValueError:
                inv = {}
            tot = inv.get("total") or 0
            run = runs.setdefault(r_["order_id"], dict(cum=0, sh=0, t=0))
            run["cum"] += r_["amount_cents"] if r_["kind"] == "payment" else -r_["amount_cents"]
            sh_cum = round(inv.get("ship_cents", 0) * run["cum"] / tot) if tot else 0
            t_cum = round(inv.get("tax_cents", 0) * run["cum"] / tot) if tot else 0
            amt_ = r_["amount_cents"] if r_["kind"] == "payment" else -r_["amount_cents"]
            parts[r_["id"]] = ((amt_ - (sh_cum - run["sh"]) - (t_cum - run["t"])) if tot else 0, sh_cum - run["sh"],
                               t_cum - run["t"])
            run["sh"], run["t"] = sh_cum, t_cum
        for r_ in q("""SELECT p.*, o.invoice_data, o.invoice_number, o.bill_name, o.id AS oid FROM print_payments p
                       JOIN print_orders o ON o.id=p.order_id WHERE p.voided IS NULL AND substr(p.received,1,4)=?
                       ORDER BY p.received, p.id""", (y,)):
            amt = r_["amount_cents"] if r_["kind"] == "payment" else -r_["amount_cents"]
            received += amt
            if r_["kind"] == "refund":
                refunds += r_["amount_cents"]
            s_, sh_, t_ = parts.get(r_["id"], (0, 0, 0))
            sales += s_
            ship += sh_
            tax += t_
            rows.append(dict(day=r_["received"], invoice=r_["invoice_number"] or "", who=r_["bill_name"], kind=r_["kind"],
                             amount=amt, sales=s_, ship=sh_, tax=t_, method=r_["method"] or ""))
        for b_ in q("SELECT * FROM print_books WHERE substr(day,1,4)=? AND what IN ('payment', 'refund')", (y,)):
            received += b_["amount_cents"]
            rows.append(dict(day=b_["day"], invoice="", who="Deleted client", kind=b_["what"], amount=b_["amount_cents"],
                             sales=0, ship=0, tax=0, method=""))
        cost = lab_tax = 0
        costs = []
        for r_ in q("""SELECT c.*, o.invoice_number, o.bill_name FROM print_lab_costs c JOIN print_orders o ON o.id=c.order_id
                       WHERE substr(c.day,1,4)=? ORDER BY c.day, c.id""", (y,)):
            c_ = r_["products"] + r_["shipping"] + r_["other"]
            cost += c_
            lab_tax += r_["tax"]
            costs.append(dict(day=r_["day"], invoice=r_["invoice_number"] or "", who=r_["bill_name"], lab=r_["lab_name"] or "",
                              what={"order": "lab order", "remake": "remake", "refund": "lab refund"}.get(r_["kind"], r_["kind"]),
                              number=r_["lab_number"] or "", products=r_["products"], shipping=r_["shipping"],
                              other=r_["other"], tax=r_["tax"]))
        for b_ in q("SELECT * FROM print_books WHERE substr(day,1,4)=? AND what IN ('lab_cost', 'lab_tax')", (y,)):
            if b_["what"] == "lab_cost":
                cost += b_["amount_cents"]
            else:
                lab_tax += b_["amount_cents"]
        return dict(received=received, sales=sales, shipping=ship, tax=tax, refunds=refunds, cost=cost, lab_tax=lab_tax,
                    profit=received - tax - cost - lab_tax, rows=rows, costs=costs,
                    any=bool(rows or costs or received or cost or lab_tax))

    app.config["prints_year"] = year

    def accounts(y):

        ym = year(y)
        if not ym["any"]:
            return []
        m = lambda c_: f"{c_ / 100:.2f}"
        pay = [[r_["day"], r_["invoice"], r_["who"], r_["kind"], r_["method"], m(r_["amount"]), m(r_["sales"]), m(r_["ship"]),
                m(r_["tax"])] for r_ in ym["rows"]]
        cst = [[r_["day"], r_["invoice"], r_["who"], r_["lab"], r_["what"], r_["number"], m(r_["products"]),
                m(r_["shipping"]), m(r_["other"]), m(r_["tax"])] for r_ in ym["costs"]]
        return [("prints.csv", ["date received", "invoice", "billed to", "kind", "method", "amount", "print sales",
                                "shipping charged", "sales tax collected"], pay),
                ("print-lab-costs.csv", ["date paid", "invoice", "billed to", "lab", "what", "lab order", "products",
                                         "shipping", "other", "the lab's sales tax"], cst)]

    app.config["prints_accounts"] = accounts

    def statement(hid):

        invs, pays = [], []
        for r_ in q("""SELECT o.* FROM print_orders o JOIN people p ON p.id=o.contact_id WHERE p.household_id=?""", (hid,)):
            o = dict(r_)
            if o["invoice_number"] and o["invoice_data"]:
                invs.append(dict(sid=o["session_id"], number=o["invoice_number"], issued=o["invoice_issued"],
                                 amount=json.loads(o["invoice_data"]).get("total", 0) / 100, kind="Print order"))
            for p_ in q("SELECT * FROM print_payments WHERE order_id=? AND voided IS NULL", (o["id"],)):
                pays.append(dict(sid=o["session_id"], received=p_["received"], method=p_["method"] or "",
                                 kind="print refund" if p_["kind"] == "refund" else "print order",
                                 amount=(p_["amount_cents"] if p_["kind"] == "payment" else -p_["amount_cents"]) / 100))
        return invs, pays

    app.config["prints_statement"] = statement

    def prints_on_now():
        return ctx.plugin_on("prints")

    def offers_down():

        x("""UPDATE print_galleries SET offer_sig='' WHERE offer_sig != '' AND session_id IN
             (SELECT session_id FROM galleries WHERE removed_at IS NOT NULL)""")
        if not G.has_host():
            return 0
        fp = G.host_fingerprint()
        rows = q("""SELECT pg.session_id, s.gallery_id FROM print_galleries pg JOIN sessions s ON s.id=pg.session_id
                    JOIN galleries g ON g.session_id=pg.session_id WHERE pg.offer_sig != '' AND g.removed_at IS NULL
                    AND (g.host_fp IS NULL OR g.host_fp=?)""", (fp,))
        host, failed = None, 0
        for r_ in rows:
            gid = r_["gallery_id"]
            if not G.GID_OK.match(gid or ""):
                continue
            try:
                host = host or G.gallery_host()
                with host.session():
                    for n_ in ("offer-kid.php", "offer.bin"):
                        host.delete(f"{gid}/{n_}")
                    if not [n_ for n_ in host.list(f"{gid}/orders/") if ORDER_NAME.match(n_)]:
                        host.delete(f"{gid}/{ORDERS_KEEP}")
                        host.delete(f"{gid}/{ORDERS_KEEP_OLD}")
            except Exception:
                app.logger.exception("print_core: taking the print offer of %s down", gid)
                failed += 1
                continue
            x("UPDATE print_galleries SET offer_sig='' WHERE session_id=?", (r_["session_id"],))
            ctx.audit("Prints no longer offered on the gallery (print orders are off)", sid=r_["session_id"],
                      who="Lumen Aureum")
        if (setting("prints_offers_stuck") or "") != (str(failed) if failed else ""):
            put("prints_offers_stuck", str(failed) if failed else "")
        return failed

    def check_hook():

        with appctx():
            collect_all()
            if not prints_on_now():
                offers_down()
            elif setting("prints_offers_stuck"):
                put("prints_offers_stuck", "")
            trim_ship()

    G.SYNC_HOOKS.append(check_hook)

    def today_items():

        if prints_on_now():
            return []
        out = []
        spell = int(setting("plugin_off_n_prints") or 0)
        n = (q("""SELECT COUNT(*) n FROM print_orders WHERE state IN ('new', 'unmatched', 'unreadable')
                  AND ? > 0 AND CAST(json_extract(COALESCE(NULLIF(flags, ''), '{}'), '$.off_n') AS INTEGER) = ?""",
                  (spell, spell), one=True) or {"n": 0})["n"]
        if n:
            out.append(dict(text=f"{n} print order{'' if n == 1 else 's'} arrived while print orders are off (kept: "
                                 "turn print orders on to review them)", link=url_for("plugins_settings")))
        stuck = int(setting("prints_offers_stuck") or 0)
        if stuck:
            out.append(dict(text=f"Print offers still on {stuck} galler{'y' if stuck == 1 else 'ies'}: the web host did "
                                 "not answer. Tried again at each gallery check.", link=url_for("plugins_settings")))
        return out

    ctx.hooks.today.append(today_items)

    def loop():
        time.sleep(120)
        while True:
            if ctx.unlocked():
                try:
                    with app.app_context():
                        trim_ship()
                except Exception:
                    app.logger.exception("print_core: the ship-to rule")
            time.sleep(3600)

    if not os.environ.get("CRM_TESTING"):
        threading.Thread(target=loop, name="print-core-daily", daemon=True).start()

    app.config["prints_collect"] = collect_all
    app.config["print_trim"] = trim_ship
    app.config["print_offers_down"] = offers_down
    app.config["print_galleries_on"] = lambda fp: (q(
        """SELECT COUNT(*) n FROM print_galleries p JOIN galleries g ON g.session_id=p.session_id WHERE p.ever=1 AND
           g.host_fp=?""", (fp,), one=True) or {"n": 0})["n"]
    app.config["print_core"] = SimpleNamespace(
        appctx=appctx, setting=setting, put=put, studio_pem=studio_pem, key_map=key_map, studio_public=studio_public,
        gallery_row=gallery_row, unopened_dir=unopened_dir, host_mtime=host_mtime, match_contact=match_contact,
        record_order=record_order, price_list_of=price_list_of, list_price=list_price, old_items=old_items,
        collect_from=collect_from, collect_all=collect_all, held=held, trim_ship=trim_ship, offers_down=offers_down,
        items_ro=items_ro)
