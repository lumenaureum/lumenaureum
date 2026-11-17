import base64
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

from flask import Response, abort, g, redirect, render_template_string, request, session, stream_with_context, url_for
from markupsafe import Markup, escape

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import printorders as P

ORDER_NAME = re.compile(r"^(?:.*/)?([0-9a-f]{32})\.las$")
ORDERS_KEEP = "orders/index.html"
PLUGIN_TIMEOUT = 30
UNOPENED_KEEP_DAYS = 30
SHIP_DEFAULT = {"us": dict(on=True, rule="cost", handling=0, flat=0, say="Shipping at cost, on your invoice"),
                "akhi": dict(on=True, rule="cost", handling=0, flat=0,
                             say="Shipping to Alaska and Hawaii at cost, on your invoice"),
                "ca": dict(on=False, rule="cost", handling=0, flat=0, say=""),
                "pickup": dict(on=True, rule="cost", handling=0, flat=0, say="")}
DEST_LABEL = {"us": "US (48 states and DC)", "akhi": "Alaska and Hawaii", "ca": "Canada", "pickup": "Pick up from the studio"}
RULE_LABEL = {"cost": "At cost", "cost_plus": "At cost plus handling", "flat": "A flat amount"}
ARRIVE_DEFAULT = "Prints come straight from our lab."
POLICY_LINE = ("Prints are made by our lab after this invoice is paid in full. Prints damaged in shipping, lost or "
               "defective are remade by the lab at no cost to you; a crop, size or address you chose is a new order.")
ACCOUNTANT = ("Whether to charge tax on prints and shipping, and at what rate, is your decision with your accountant; "
              "Lumen Aureum applies what you choose.")

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

class LabError(RuntimeError):
    pass

def check_host_folder(folder):

    if not isinstance(folder, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}\.d", folder):
        raise ValueError("a lab plugin's host folder is letters, digits and hyphens ending in .d")
    return folder

def check_host_name(name):

    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", name) or ".." in name:
        raise ValueError("a file name in the lab's host folder is letters, digits, dots, hyphens and underscores")
    return name

def register(ctx):
    app, q, x, C = ctx.app, ctx.q, ctx.x, ctx.C
    if app.config.get("gallery_take_down"):
        _GMOD[0] = ModuleView(app.config["gallery_take_down"].__globals__)
    G = _G()
    app.config["gallery_module"] = G
    K = app.config["print_core"]
    (appctx, setting, put, studio_public, gallery_row, price_list_of, list_price, held, trim_ship) = (
        K.appctx, K.setting, K.put, K.studio_public, K.gallery_row, K.price_list_of, K.list_price, K.held, K.trim_ship)

    def active():

        return ctx.plugin_on("prints")

    def open_orders_words():
        n = (q(f"SELECT COUNT(*) n FROM print_orders WHERE state IN ({','.join('?' * len(P.OPEN))})", P.OPEN, one=True)
             or {"n": 0})["n"]
        return (f"{n} print order{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} still open. Finish them first, or "
                "turn off anyway (they're kept and come back when print orders are on again).") if n else ""

    ctx.switchable(default=False, label="Print orders", before_off=open_orders_words,
                   about="Clients order prints from their download galleries; you review, invoice, and order them at "
                         "any lab. Off: no gallery offers prints (the offer comes off at the next gallery check) and "
                         "the print pages are hidden. Everything already recorded is kept; orders a client sends anyway "
                         "are collected and kept, and Today says so; print money always stays in Finances, statements "
                         "and the accountant export.")

    @app.before_request
    def _prints_off():

        ep = request.endpoint or ""
        if ep.startswith("prints_") and not active():
            if request.method == "GET" and not ep.endswith(("_image", "_photo", "_zip")):
                session["notice"] = "Print orders are off. Turn them on in Settings, Plugins."
                return redirect(url_for("plugins_settings"))
            abort(404)

    def ship_rules():
        try:
            got = json.loads(setting("prints_ship") or "{}")
        except ValueError:
            got = {}
        out = {}
        for d_, dflt in SHIP_DEFAULT.items():
            r_ = dict(dflt, **(got.get(d_) or {})) if isinstance(got.get(d_), dict) else dict(dflt)
            if r_.get("rule") not in P.CHARGE_RULES:
                r_["rule"] = "cost"
            out[d_] = r_
        return out

    def markup():
        try:
            return P.markup_in(setting("prints_markup") or "3")
        except ValueError:
            return P.MARKUP_DEFAULT

    def saved_once():
        return setting("prints_saved") == "1"

    def prints_on():
        return active() and saved_once() and setting("prints_on") == "1"

    def plv():
        try:
            return max(1, int(setting("prints_plv") or 1))
        except ValueError:
            return 1

    def type_off():
        try:
            return set(json.loads(setting("prints_type_off") or "[]"))
        except ValueError:
            return set()

    def studio_name():
        return setting("business_name") or setting("photographer_name") or "the studio"

    def contact():
        return G.contact_email()

    def items(only_on=False):
        if not q("SELECT 1 FROM price_list_items LIMIT 1", one=True) and not saved_once():
            for it in P.starter_items():
                x("""INSERT OR IGNORE INTO price_list_items (id, label, w, h, finish, each_cents, ppi_hide, ppi_mark, offered, sort)
                     VALUES (?,?,?,?,?,?,?,?,1,?)""", (it["id"], it["label"], it["w"], it["h"], it["finish"], it["each"],
                                                      it["ppi_hide"], it["ppi_mark"], it["sort"]))
            put("prints_next_item", len(P.STARTER) + 1)
        rows = [dict(r_) for r_ in q("SELECT * FROM price_list_items ORDER BY sort, id")]
        for r_ in rows:
            try:
                r_["lab"] = json.loads(r_["lab"] or "{}")
            except ValueError:
                r_["lab"] = {}
        return [r_ for r_ in rows if r_["offered"]] if only_on else rows

    def item_of(iid):
        return next((i_ for i_ in items() if i_["id"] == iid), None)

    def host_kind():

        try:
            caps = json.loads(setting("gallery_host_caps") or "{}")
        except ValueError:
            return ""
        return ((caps.get("now") or caps.get("setup") or {}).get("kind")) or ""

    def full_build(gid):
        return bool(gid and G.GID_OK.match(gid) and G.is_download(gid) and (G.OUT_ROOT / gid / "manifest.bin").is_file())

    def manifest_of(r):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        gid = r["gallery_id"]
        try:
            blob = (G.OUT_ROOT / gid / "manifest.bin").read_bytes()
            return json.loads(AESGCM(_gkey(r)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))
        except Exception:
            return None

    def gallery_switch(sid):
        r_ = q("SELECT off FROM print_galleries WHERE session_id=?", (sid,), one=True)
        return not (r_ and r_["off"])

    def why_no_prints(r):

        if not prints_on():
            return "Prints are off (Settings, Print prices)."
        if r["type"] in type_off():
            return "This kind of session does not offer prints."
        if not gallery_switch(r["sid"]):
            return "Prints are turned off for this gallery."
        if not (r["upload_state"] == "up" and not r["removed_at"]):
            return "The gallery is not online."
        if not (G.is_download(r["gallery_id"]) if "uploading" in r.keys() else G.online_download(r["gallery_id"])):
            return "Prints are offered from the download gallery only."
        if not full_build(r["gallery_id"]):
            return "Prints need this gallery's files; rebuild it to offer prints."
        return ""

    def offer_plain(r):

        if why_no_prints(r):
            return None, ""
        kind = host_kind()
        if kind == "none":
            body = dict(v=1, email_only=True, contact=contact(), studio=studio_name())
        elif kind != "ok":
            return None, ""
        else:
            import sealed
            spk = studio_public()
            rules = ship_rules()
            on = [i_ for i_ in items(only_on=True) if i_["each_cents"] is not None]
            body = dict(v=1, plv=plv(), cur=P.CURRENCY, kid=sealed.kid_of(spk).hex(),
                        spk=base64.urlsafe_b64encode(spk).decode().rstrip("="),
                        items=[dict(id=i_["id"], label=i_["label"], w=float(min(i_["w"], i_["h"])), h=float(max(i_["w"], i_["h"])),
                                    each=int(i_["each_cents"]), ppi_hide=int(i_["ppi_hide"]), ppi_mark=int(i_["ppi_mark"]))
                               for i_ in on],
                        ship={d_: dict(on=bool(rules[d_]["on"]), say=rules[d_]["say"] or (
                            f"Pick up from {studio_name()}" if d_ == "pickup" else "")) for d_ in P.DESTS},
                        invoice_days=int(setting("prints_invoice_days") or 3), lines_max=P.LINES_MAX, qty_max=P.QTY_MAX,
                        contact=contact(), studio=studio_name(), arrive=setting("prints_arrive", ARRIVE_DEFAULT),
                        states=dict(us=[s_ for s_ in P.US_STATES if s_ not in P.AKHI], akhi=list(P.AKHI), ca=P.CA_PROVINCES))
            if not body["items"]:
                return None, ""
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        return raw, hashlib.sha256(raw).hexdigest()

    def offer_to_host(r, host):

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        gid = r["gallery_id"]
        raw, sig = offer_plain(r)
        had = q("SELECT offer_sig FROM print_galleries WHERE session_id=?", (r["sid"],), one=True)
        had_sig = had["offer_sig"] if had else ""
        if sig == had_sig and (sig or not had):
            return False
        if raw is None:
            for n_ in ("offer-kid.php", "offer.bin"):
                host.delete(f"{gid}/{n_}")
        else:
            body = json.loads(raw)
            if body.get("kid"):
                if host.stat(f"{gid}/{ORDERS_KEEP}") is None:

                    host.put_stream(f"{gid}/{ORDERS_KEEP}", io.BytesIO(b""), content_type="text/html; charset=utf-8")
                host.put_stream(f"{gid}/offer-kid.php", io.BytesIO(("<?php exit; ?>\n" + body["kid"] + "\n").encode()),
                                content_type="text/plain", cache_control="no-cache")
            else:
                host.delete(f"{gid}/offer-kid.php")
            host.put_stream(f"{gid}/offer.bin", io.BytesIO(G.seal(AESGCM(_gkey(r)), raw, "offer.bin")),
                            content_type="application/octet-stream", cache_control="no-cache")
        x("""INSERT INTO print_galleries (session_id, gid, offer_sig, offered_at, ever) VALUES (?,?,?,?,?)
             ON CONFLICT(session_id) DO UPDATE SET gid=excluded.gid, offer_sig=excluded.offer_sig,
             offered_at=excluded.offered_at, ever=MAX(ever, excluded.ever)""",
          (r["sid"], gid, sig, datetime.now().isoformat(timespec="seconds"), 1 if raw else 0))
        ctx.audit(("Prints offered on the gallery" if body.get("kid") else "The gallery says: for prints, email the studio")
                  if raw else "Prints no longer offered on the gallery", sid=r["sid"], who="Lumen Aureum")
        return True

    def offer_at_upload(gid, host):
        with appctx():
            r_ = gallery_row(gid=gid)
            if r_:
                r_ = dict(r_, upload_state="up", removed_at=None, uploading=True)
                if why_no_prints(r_):
                    return
                x("UPDATE print_galleries SET offer_sig='' WHERE session_id=?", (r_["sid"],))
                offer_to_host(r_, host)

    G.OFFER_PUT.append(offer_at_upload)

    def refresh_offers():

        x("""UPDATE print_galleries SET offer_sig='' WHERE offer_sig != '' AND session_id IN
             (SELECT session_id FROM galleries WHERE removed_at IS NOT NULL)""")
        if not G.has_host():
            return
        fp = G.host_fingerprint()
        rows = q("""SELECT g.*, s.gallery_id, s.type, s.id AS sid, s.household_id FROM galleries g JOIN sessions s
                    ON s.id=g.session_id WHERE g.upload_state='up' AND g.removed_at IS NULL AND (g.host_fp IS NULL OR g.host_fp=?)""",
                 (fp,))
        host = None
        for r_ in rows:
            if not G.GID_OK.match(r_["gallery_id"] or ""):
                continue
            raw, sig = offer_plain(r_)
            had = q("SELECT offer_sig FROM print_galleries WHERE session_id=?", (r_["sid"],), one=True)
            if (had["offer_sig"] if had else "") == sig:
                continue
            if host is None:
                host = G.gallery_host()
            try:
                with host.session():
                    offer_to_host(r_, host)
            except Exception:
                app.logger.exception("prints: the offer of %s", r_["gallery_id"])

    def check_hook():
        with appctx():
            if active():
                refresh_offers()
                daily()

    G.SYNC_HOOKS.append(check_hook)
    app.config["prints_refresh_offers"] = refresh_offers

    def order(oid_):
        r_ = q("SELECT * FROM print_orders WHERE id=?", (oid_,), one=True)
        return dict(r_) if r_ else None

    def payload_of(o):
        try:
            return json.loads(o["payload"])
        except (ValueError, TypeError):
            return {}

    def lines_of(oid_):
        out = []
        for r_ in q("SELECT * FROM print_order_lines WHERE order_id=? ORDER BY n, id", (oid_,)):
            d_ = dict(r_)
            d_["crop"] = json.loads(d_["crop"])
            out.append(d_)
        return out

    def who_of(o):

        if o["contact_id"]:
            p_ = q("SELECT name, email FROM people WHERE id=?", (o["contact_id"],), one=True)
            if p_:
                return p_["name"], (p_["email"] or o["bill_email"]), True
        return o["bill_name"], o["bill_email"], False

    def ship_of(o):
        return payload_of(o).get("ship") or {"how": "pickup"}

    def tax_flags():
        mode = setting("tax_mode", "none")
        on_prints = setting("tax_prints", "" if mode == "none" else "1") == "1" and mode != "none"
        on_ship = setting("tax_print_shipping", "0") == "1" and mode != "none"
        return on_prints, on_ship

    def tax_area_for(o):

        reg = ctx.tax_region()
        if not reg:
            return "flat", ""
        keys = [k_ for k_, _ in reg["areas"]]
        if o.get("tax_area") in keys:
            return o["tax_area"], ""
        sh = ship_of(o)
        addr = setting("business_address") if sh.get("how") == "pickup" else \
            "\n".join(x_ for x_ in (sh.get("addr1"), sh.get("addr2"), f"{sh.get('city', '')}, {sh.get('state', '')} {sh.get('zip', '')}") if x_)
        return reg["area_of"](addr or "")

    def figures(o, lines=None):

        lines = lines if lines is not None else lines_of(o["id"])
        sub = sum(l_["each_cents"] * l_["qty"] for l_ in lines)
        dest = P.dest_of(ship_of(o)) or "us"
        ship = o["ship_cents"] if o.get("ship_cents") is not None else rule_ship(o)
        on_prints, on_ship = tax_flags()
        if o.get("tax_rate") is not None:
            rate = float(o["tax_rate"])
        else:
            rate = ctx.sales_tax_pct(tax_area_for(o)[0]) if (on_prints or on_ship) else 0.0
        taxable = (sub if on_prints else 0) + ((ship or 0) if on_ship else 0)
        tax = P.tax_cents(taxable, rate) if rate else 0
        pays = q("SELECT * FROM print_payments WHERE order_id=? AND voided IS NULL ORDER BY received, id", (o["id"],))
        paid = sum(p_["amount_cents"] if p_["kind"] == "payment" else -p_["amount_cents"] for p_ in pays)
        total = sub + (ship or 0) + tax
        return dict(sub=sub, ship=ship, ship_missing=ship is None, rate=rate, taxable=taxable, tax=tax, total=total,
                    paid=paid, balance=total - paid, pays=[dict(p_) for p_ in pays], dest=dest)

    def rule_ship(o):

        rules = ship_rules()
        dest = P.dest_of(ship_of(o)) or "us"
        ship = P.shipping_charge(rules.get(dest), o.get("ship_lab_cents"))
        if ship is None and dest == "pickup" and (rules.get(dest) or {}).get("rule", "cost") == "cost":
            ship = 0
        return ship

    def paid_in_full(oid_):
        o = order(oid_)
        if not o or not o["invoice_number"] or o["invoice_void"]:
            return False
        f_ = issued_figures(o)
        return f_["paid"] > 0 and f_["paid"] >= f_["total"]

    def issued_figures(o):

        f_ = figures(o)
        if o["invoice_data"]:
            try:
                f_["total"] = json.loads(o["invoice_data"])["total"]
                f_["balance"] = f_["total"] - f_["paid"]
            except (ValueError, KeyError):
                pass
        return f_

    def move(o, new, why="", by="hand"):

        old = o["state"]
        if old == new:
            return True
        if not P.may_move(old, new, by):
            raise LabError(f"an order that is {P.WORDS.get(old, old).lower()} cannot become {P.WORDS.get(new, new).lower()}")
        stamp = {"ordered": "ordered_at", "shipped": "shipped_at", "delivered": "delivered_at", "closed": "closed_at",
                 "canceled": "closed_at", "expired": "closed_at", "prepared": "prepared_at"}.get(new)
        x("UPDATE print_orders SET state=?, prev_state=? WHERE id=?", (new, old, o["id"]))
        if stamp == "ordered_at":
            x("UPDATE print_orders SET ordered_at=COALESCE(ordered_at, ?) WHERE id=?",
              (datetime.now().isoformat(timespec="seconds"), o["id"]))
        elif stamp:
            x(f"UPDATE print_orders SET {stamp}=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), o["id"]))
        who = who_of(o)[0]
        ctx.audit(f"Print order from {who}: {P.WORDS.get(new, new)}" + (f" ({why})" if why else ""), sid=o["session_id"],
                  who="Lumen Aureum" if by != "hand" else "Owner")
        o["state"] = new
        try:
            ctx.hooks.fire("print_order_changed", o["id"], old, new)
            if new == "paid":
                ctx.hooks.fire("print_order_paid", o["id"])
        except Exception:
            app.logger.exception("prints: a plugin's event")
        return True

    for ev_ in ("print_order_changed", "print_order_paid", "host_gone", "gallery_host_changed"):
        ctx.hooks.on.setdefault(ev_, [])

    def follow_payment(o):

        o = order(o["id"])
        if not o["invoice_number"] or o["invoice_void"]:
            return
        if o["state"] == "invoiced" and paid_in_full(o["id"]):
            move(o, "paid", "paid in full")
        elif o["state"] in ("paid", "prepared") and not paid_in_full(o["id"]) and not o["before_payment"]:
            move(o, "invoiced", "no longer paid in full")

    def flags_of(o, lines=None):

        out = []
        p_ = payload_of(o)
        lines = lines if lines is not None else lines_of(o["id"])
        sh = ship_of(o)
        s_ = q("SELECT household_id FROM sessions WHERE id=?", (o["session_id"],), one=True)
        hid = s_["household_id"] if s_ else None
        if o["contact_id"]:
            c_ = q("SELECT household_id FROM people WHERE id=?", (o["contact_id"],), one=True)
            hid = c_["household_id"] if c_ else hid
        if o["state"] == "unreadable":
            return [("unreadable", "This order could not be read: " + (json.loads(o["flags"] or "{}").get("why") or ""), None)]
        if not o["contact_id"]:
            out.append(("email", "The orderer's email is not on file", None))
        if sh.get("how") != "pickup" and not sh.get("reduced") and hid:
            addr = q("SELECT address FROM households WHERE id=?", (hid,), one=True)
            if not addr or (sh.get("addr1") or "").lower() not in (addr["address"] or "").lower() or not sh.get("addr1"):
                out.append(("shipto", "The ship-to address is not on file for this client", None))
        if not q("SELECT 1 FROM print_orders WHERE gid=? AND id < ? AND state NOT IN ('unreadable')", (o["gid"], o["id"]), one=True):
            out.append(("first", "The first order from this gallery", None))
        n_prints = sum(l_["qty"] for l_ in lines)
        if n_prints > int(setting("prints_large_n") or P.LARGE_PRINTS) or \
                sum(l_["each_cents"] * l_["qty"] for l_ in lines) > int(setting("prints_large_cents") or P.LARGE_CENTS):
            out.append(("large", f"A large order: {n_prints} prints", None))
        sig = sorted((l_["photo_file"], l_["item_id"], l_["qty"]) for l_ in lines)
        for other in q("""SELECT id, payload, collected_at FROM print_orders WHERE gid=? AND id != ? AND state != 'unreadable'""",
                       (o["gid"], o["id"])):
            try:
                if abs((datetime.fromisoformat(other["collected_at"]) - datetime.fromisoformat(o["collected_at"])).total_seconds()) > 86400:
                    continue
            except (TypeError, ValueError):
                continue
            ol = sorted((l_["photo_file"], l_["item_id"], l_["qty"]) for l_ in lines_of(other["id"]))
            osh = json.loads(other["payload"] or "{}").get("ship")
            if ol == sig or (osh == sh and sh.get("how") != "pickup"):
                out.append(("dup", "Likely a duplicate of another order from this gallery in the same day",
                            url_for("prints_order", oid_=other["id"])))
                break
        if o["plv"] and o["plv"] < plv():
            now_ = {i_["id"]: i_ for i_ in items()}
            changed = [f"{l_['label']}: {P.dollars(l_['each_cents'])} then, {P.dollars(now_[l_['item_id']]['each_cents'])} now"
                       for l_ in lines if l_["item_id"] in now_ and now_[l_["item_id"]]["each_cents"] != l_["each_cents"]]
            out.append(("plv", "Made under an older price list" + (": " + "; ".join(changed) if changed else ""), None))
        priced = price_list_of(o["plv"]) if o["plv"] else {}
        for l_ in lines:
            want = list_price(priced, l_["item_id"])
            if want is None:
                out.append(("price", f"Line {l_['n']}: not on the price list the client ordered from; check its price", None))
            elif l_.get("shown_cents") is not None and l_["shown_cents"] != want:
                out.append(("price", f"Line {l_['n']}: the client's page said {P.dollars(l_['shown_cents'])} each; "
                                     f"your price list says {P.dollars(want)}", None))
        if P.incomplete(sh) and o["state"] not in P.FINAL:
            out.append(("addr_missing", "Shipping address incomplete: only the city, state and ZIP are kept. Enter the full "
                                        "address before Prepare for the lab.", None))
        if o.get("ship_by_hand") and o.get("ship_cents") is not None and o["state"] in ("new", "unmatched"):
            now_ = rule_ship(o)
            if now_ != o["ship_cents"]:
                out.append(("shiphand", f"Shipping set by hand: {P.dollars(o['ship_cents'])} (rules now say "
                                        f"{P.dollars(now_) if now_ is not None else 'type the lab' + chr(39) + 's shipping'})", None))
        for w_ in P.address_flags(sh, p_.get("who")):
            out.append(("addr", w_, None))
        files = photo_files(o)
        for l_ in lines:
            if files is not None and l_["photo_file"] not in files:
                out.append(("changed", f"Line {l_['n']}: Photo changed since the order", None))
            if l_["ppi"] and l_["item_id"]:
                it = item_of(l_["item_id"]) or {}
                if l_["ppi"] < int(it.get("ppi_mark") or P.PPI_MARK):
                    out.append(("soft", f"Line {l_['n']}: may look soft at this size ({l_['ppi']} pixels per inch)", None))
        if p_.get("rel", {}).get("kind") == "change":
            out.append(("change", "The client says this changes an earlier order" + (
                "" if not p_["rel"].get("prev") else ""), None))
        return out

    def photo_files(o):

        r_ = gallery_row(sid=o["session_id"])
        if not r_ or not full_build(r_["gallery_id"]):
            return None
        m = manifest_of(r_)
        if not m:
            return None
        return {it.get("o"): it for it in m.get("items", []) if it.get("o")}

    def full_size(o, line):

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        r_ = gallery_row(sid=o["session_id"])
        if not r_ or not G.GID_OK.match(r_["gallery_id"] or ""):
            raise ValueError("the gallery is gone")
        files = photo_files(o)
        if files is None or line["photo_file"] not in files:
            raise ValueError("Photo changed since the order" if files is not None else "the gallery's files are not here")
        it = files[line["photo_file"]]
        p_ = G.OUT_ROOT / r_["gallery_id"] / line["photo_file"]
        blob = p_.read_bytes()
        data = AESGCM(_gkey(r_)).decrypt(blob[:12], blob[12:], ("lumen-aureum-gallery:" + line["photo_file"]).encode())
        return data, (int(it.get("ow") or 0), int(it.get("oh") or 0))

    def line_check(o, line):

        it = item_of(line["item_id"]) or {}
        w, h = line["w"] or it.get("w"), line["h"] or it.get("h")
        files = photo_files(o)
        if files is None:
            return None, 0, "the gallery's files are not here"
        if line["photo_file"] not in files:
            return None, 0, "Photo changed since the order"
        f_ = files[line["photo_file"]]
        ow, oh = int(f_.get("ow") or 0), int(f_.get("oh") or 0)
        if not (w and h and ow and oh):
            return None, 0, "the size or the photo's pixels are not known"
        try:
            rect = P.cut_rect(line["crop"], ow, oh, w, h, line["turn"])
        except ValueError as e:
            return None, 0, str(e)
        ppi = P.cut_ppi(rect, w, h)
        if ppi < int(it.get("ppi_hide") or P.PPI_HIDE):
            return rect, ppi, f"only {ppi} pixels per inch at this size: change the crop or the size"
        return rect, ppi, ""

    def ready_checks(o, lines=None):

        lines = lines if lines is not None else lines_of(o["id"])
        out = []
        if P.incomplete(ship_of(o)):
            out.append("Shipping address incomplete: enter the full address on the order first.")
        if photo_files(o) is None:
            out.append("The gallery's files are not here (rebuild it).")
        for l_ in lines:
            _rect, ppi, why = line_check(o, l_)
            if why:
                out.append(f"Line {l_['n']}: {why}.")
            if l_["crop"] != [0.0, 0.0, 1.0, 1.0] and not l_["crop_confirmed"]:
                out.append(f"Line {l_['n']}: check the crop and mark it Looks right.")
            it = item_of(l_["item_id"]) or {}
            if ppi and ppi < int(it.get("ppi_mark") or P.PPI_MARK) and not l_["soft_confirmed"]:
                out.append(f"Line {l_['n']}: confirm it may look soft at this size.")
        return out

    def print_files(oid_, rules=None):

        o = order(oid_)
        if not o:
            raise LabError("no such order")
        lines = lines_of(oid_)
        bad = ready_checks(o, lines)
        if bad:
            raise LabError(" ".join(bad))
        rules = rules or {}
        for l_ in lines:
            rect, _ppi, _why = line_check(o, l_)
            it = item_of(l_["item_id"]) or {}
            w, h = l_["w"] or it.get("w"), l_["h"] or it.get("h")
            name = P.file_name(o["invoice_number"] or f"order-{o['id']}", l_["n"], w, h, l_["finish"] or it.get("finish", ""), l_["qty"])

            def make(l_=l_, rect=rect):
                src, _sz = full_size(o, l_)
                data, qv = P.cut_jpeg(src, rect, quality=int(rules.get("quality") or 95), max_bytes=rules.get("max_bytes"))
                return data, qv
            data, qv = make()
            yield f"{o['id']}-{l_['n']}", name, [l_["n"]], data

    INVOICE_DOC = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{ v.number }}</title>
<style>body{font:14px/1.5 Georgia,serif;color:#1f1b16;max-width:720px;margin:24px auto;padding:0 16px}
h1{font-weight:400;margin:0 0 4px}table{width:100%;border-collapse:collapse;margin:14px 0}td,th{padding:5px 4px;border-bottom:1px solid #ddd;text-align:left}
td.r,th.r{text-align:right}.mute{color:#6b6157}.tot td{font-weight:700;border-bottom:0}</style></head><body>
<h1>Invoice {{ v.number }}</h1><div class="mute">{{ v.date }}{% if v.void %} &middot; VOID ({{ v.void }}){% endif %}</div>
<p><b>{{ v.business or v.photographer }}</b>{% if v.address %}<br>{{ v.address|replace('\\n', ', ') }}{% endif %}{% if v.tax_id %}<br>Tax ID {{ v.tax_id }}{% endif %}</p>
<p>Bill to: <b>{{ v.bill_name }}</b>{% if v.bill_email %}<br>{{ v.bill_email }}{% endif %}</p>
<p>Prints for {{ v.what }}{% if v.ship_lines %}<br>Ship to: {{ v.ship_lines|join(', ') }}{% endif %}</p>
<table><thead><tr><th>Print</th><th class="r">Qty</th><th class="r">Each</th><th class="r">Amount</th></tr></thead><tbody>
{% for l in v.lines %}<tr><td>{{ l.label }}, {{ l.photo }}{% if l.cropped %}, cropped{% endif %}</td><td class="r">{{ l.qty }}</td><td class="r">{{ l.each }}</td><td class="r">{{ l.amount }}</td></tr>{% endfor %}
<tr><td>{{ v.ship_label }}</td><td></td><td></td><td class="r">{{ v.ship }}</td></tr>
{% if v.tax_line %}<tr><td>{{ v.tax_line }}</td><td></td><td></td><td class="r">{{ v.tax }}</td></tr>{% endif %}
<tr class="tot"><td>Total</td><td></td><td></td><td class="r">{{ v.total_s }}</td></tr></tbody></table>
<p>{{ v.policy }}</p>{% if v.arrive %}<p>{{ v.arrive }}</p>{% endif %}
{% if v.pay_words %}<p>How to pay: {{ v.pay_words }}{% if v.pay_details %}. {{ v.pay_details }}{% endif %}</p>{% endif %}
</body></html>"""

    def invoice_data(o):
        o = order(o["id"])
        lines = lines_of(o["id"])
        f_ = figures(o, lines)
        bn, be, _on = who_of(o)
        sh = ship_of(o)
        s_ = q("""SELECT s.title, s.type, s.shoot_date, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                  WHERE s.id=?""", (o["session_id"],), one=True)
        rules = ship_rules()
        rule = rules.get(f_["dest"], {})
        ship_label = ("Shipping" if sh.get("how") != "pickup" else "Shipping to the studio for pick up") + (
            ", at cost" if rule.get("rule") == "cost" else ", at cost plus handling" if rule.get("rule") == "cost_plus" else "")
        import config as _C
        pay_methods = _C._or_list(_C.payment_methods()) if hasattr(_C, "_or_list") else ""
        return dict(number=o["invoice_number"], date=ctx.long_date(date.today().isoformat()), void="",
                    business=setting("business_name"), photographer=setting("photographer_name"),
                    address=setting("business_address"), tax_id=setting("business_tax_id"), bill_name=bn, bill_email=be,
                    what=(s_["title"] or C.SESSION_TYPES.get(s_["type"], s_["type"]) if s_ else "your photos")
                    + (f", {ctx.long_date(s_['shoot_date'])}" if s_ and s_["shoot_date"] else ""),
                    ship_lines=P.label_lines(sh, bn),
                    lines=[dict(label=l_["label"], photo=l_["photo_name"], cropped=l_["crop"] != [0.0, 0.0, 1.0, 1.0],
                                qty=l_["qty"], each=P.dollars(l_["each_cents"]), amount=P.dollars(l_["each_cents"] * l_["qty"]))
                           for l_ in lines],
                    ship_label=ship_label, ship=P.dollars(f_["ship"] or 0),
                    tax_line=(f"Sales tax ({f_['rate']:g}%)" if f_["tax"] else ""), tax=P.dollars(f_["tax"]),
                    total=f_["total"], total_s=P.dollars(f_["total"]), sub=f_["sub"], ship_cents=f_["ship"] or 0,
                    tax_cents=f_["tax"], rate=f_["rate"], policy=POLICY_LINE,
                    arrive=(setting("prints_arrive", ARRIVE_DEFAULT) if sh.get("how") == "other" else ""),
                    pay_words=pay_methods, pay_details=" ".join((setting("pay_details") or "").split()))

    def issue_invoice(o):
        f_ = figures(o)
        if f_["ship_missing"]:
            raise LabError("Type the lab's shipping for this order first: the invoice cannot be issued while an at-cost "
                           "shipping line has no amount.")
        if not lines_of(o["id"]):
            raise LabError("The order has no lines.")
        num_ = ctx.doc_number(o["session_id"], f"PRINT:{o['id']}", "INV")
        area, _w = tax_area_for(o)
        x("UPDATE print_orders SET invoice_number=?, invoice_issued=?, tax_rate=?, tax_area=?, ship_cents=?, invoice_void=NULL WHERE id=?",
          (num_, date.today().isoformat(), f_["rate"], area if ctx.tax_region() else None, f_["ship"], o["id"]))
        data = invoice_data(o)
        x("UPDATE print_orders SET invoice_data=?, invoice_html=? WHERE id=?",
          (json.dumps(data), render_template_string(INVOICE_DOC, v=data), o["id"]))
        o = order(o["id"])
        move(o, "invoiced", f"invoice {num_}")
        follow_payment(o)
        return num_

    def void_invoice(o, why):

        if not o["invoice_number"]:
            return
        x("UPDATE doc_numbers SET kind=? WHERE session_id=? AND kind=?",
          (f"PRINT-void:{o['id']}:{o['invoice_number']}", o["session_id"], f"PRINT:{o['id']}"))
        data = json.loads(o["invoice_data"] or "{}")
        data["void"] = why
        fl = json.loads(o["flags"] or "{}")
        fl.setdefault("voided", []).append(dict(number=o["invoice_number"], why=why, at=date.today().isoformat(),
                                                html=render_template_string(INVOICE_DOC, v=data)))

        x("""UPDATE print_orders SET invoice_number=NULL, invoice_issued=NULL, invoice_data=NULL, invoice_html=NULL,
             invoice_void=NULL, tax_rate=NULL, flags=?, ship_cents=CASE WHEN COALESCE(ship_by_hand, 0)=1 THEN ship_cents
             ELSE NULL END WHERE id=?""", (json.dumps(fl), o["id"]))
        ctx.audit(f"Print invoice {o['invoice_number']} voided ({why})", sid=o["session_id"])

    COST_KEYS = ("products", "shipping", "tax", "other")

    def costs_of(oid_):
        return [dict(r_) for r_ in q("SELECT * FROM print_lab_costs WHERE order_id=? ORDER BY day, id", (oid_,))]

    def record_cost(oid_, kind, costs, lab_name="", number="", day=None, reverses=None):
        day = day or date.today().isoformat()
        return x("""INSERT INTO print_lab_costs (order_id, day, kind, lab_name, lab_number, products, shipping, tax, other,
                    reverses, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (oid_, day, kind, lab_name or "", str(number or "")[:60], *[int(costs.get(k_) or 0) for k_ in COST_KEYS],
                  reverses, datetime.now().isoformat(timespec="seconds")))

    def latest_number(o):
        try:
            nums = json.loads(o["lab_orders"] or "[]")
        except ValueError:
            nums = []
        return str((nums[-1] if nums else {}).get("number") or "")

    def reverse_costs(o, number=None, why="", day=None):

        rows = costs_of(o["id"])
        done = {r_["reverses"] for r_ in rows if r_["reverses"]}
        n = 0
        for r_ in rows:
            if r_["kind"] == "refund" or r_["id"] in done or (number is not None and r_["lab_number"] != number):
                continue
            if not any(r_[k_] for k_ in COST_KEYS):
                continue
            record_cost(o["id"], "refund", {k_: -r_[k_] for k_ in COST_KEYS}, r_["lab_name"], r_["lab_number"], day=day,
                        reverses=r_["id"])
            n += 1
        if n:
            ctx.audit(f"Print order from {who_of(o)[0]}: the lab's cost taken back" + (f" ({why})" if why else ""),
                      sid=o["session_id"])
        return n

    def cost_words(r_):
        what = {"order": "Lab order", "remake": "Remake", "refund": "Lab refund"}.get(r_["kind"], r_["kind"])
        amt = ", ".join(f"{n_} {P.dollars(r_[k_])}" for n_, k_ in zip(("products", "shipping", "the lab's tax", "other"),
                                                                       COST_KEYS) if r_[k_])
        return (f"{ctx.nice(r_['day'])}: {what}" + (f" {r_['lab_number']}" if r_["lab_number"] else "")
                + (f" at {r_['lab_name']}" if r_["lab_name"] else "") + ": " + (amt or "$0.00"))

    app.config["prints_paid_in_full"] = paid_in_full
    app.config["prints_figures"] = lambda oid_: figures(order(oid_))

    def daily():

        now_ = datetime.now()
        if not (app.config.get("clock_ok") or (lambda: True))():
            return
        cut = (date.today() - timedelta(days=P.UNPAID_EXPIRE_DAYS)).isoformat()
        for r_ in q("""SELECT * FROM print_orders WHERE state='invoiced' AND invoice_issued IS NOT NULL AND invoice_issued <= ?
                       AND COALESCE(before_payment, 0)=0""", (cut,)):
            o = dict(r_)
            if paid_in_full(o["id"]):
                continue
            if figures(o)["paid"] > 0:
                continue
            void_invoice(o, "expired unpaid")
            move(order(o["id"]), "expired", f"not paid in {P.UNPAID_EXPIRE_DAYS} days", by="rule")
        cut = (date.today() - timedelta(days=P.CLOSE_AFTER_SHIPPED_DAYS)).isoformat()
        for r_ in q("""SELECT * FROM print_orders WHERE state IN ('shipped', 'delivered') AND shipped_at IS NOT NULL
                       AND substr(shipped_at,1,10) <= ?""", (cut,)):
            o = dict(r_)
            if o["before_payment"] and owed(o) > 0:
                continue
            move(o, "closed", f"{P.CLOSE_AFTER_SHIPPED_DAYS} days after it shipped", by="rule")
        trim_ship()
        for r_ in q("SELECT * FROM print_orders WHERE state IN ('new', 'invoiced', 'paid', 'prepared')"):
            o = dict(r_)
            if gallery_row(sid=o["session_id"]) is None or photo_files(o) is None:
                move(o, "needs_files", "the gallery's files are not here", by="rule")
        if G.KEPT_ROOT.is_dir():
            for d_ in G.KEPT_ROOT.glob("*/orders-unopened"):
                for f_ in d_.iterdir():
                    try:
                        if now_.timestamp() - f_.stat().st_mtime > UNOPENED_KEEP_DAYS * 86400:
                            f_.unlink()
                    except OSError:
                        pass

    def owed(o):

        if not o.get("invoice_number"):
            return 0
        return max(0, issued_figures(o)["balance"])

    app.config["prints_pass"] = daily

    def loop():
        time.sleep(90)
        while True:
            if ctx.unlocked():
                try:
                    with app.app_context():
                        if active():
                            daily()
                except Exception:
                    app.logger.exception("prints: the daily rules")
            time.sleep(3600)

    if not os.environ.get("CRM_TESTING"):
        threading.Thread(target=loop, name="prints-daily", daemon=True).start()

    def local_time(iso):

        try:
            d_ = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if d_.tzinfo is None:
                return str(iso)[:16].replace("T", " ")
            return d_.astimezone().strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(iso)[:16].replace("T", " ")

    def first_name(o):
        return who_of(o)[0] or "Someone"

    def first_word(o):

        parts = (who_of(o)[0] or "").split()
        return parts[0] if parts else "them"

    def today_items():
        if not active():
            return []
        out = []
        rows = [dict(r_) for r_ in q("""SELECT o.*, s.household_id, h.name AS household FROM print_orders o
                                        JOIN sessions s ON s.id=o.session_id JOIN households h ON h.id=s.household_id
                                        WHERE o.state NOT IN ('closed', 'canceled', 'expired') ORDER BY o.collected_at, o.host_mtime""")]
        n_new = [r_ for r_ in rows if r_["state"] == "new"]
        n_un = [r_ for r_ in rows if r_["state"] in ("unmatched", "unreadable")]
        if n_new:
            out.append(dict(text=f"New print orders ({len(n_new)})", link=url_for("prints_order", oid_=n_new[0]["id"])))
        if n_un:
            out.append(dict(text=f"Print orders from people not on file ({len(n_un)})", link=url_for("prints_review")))
        lab_name = lambda r_: r_["lab_name"] or "the lab"
        now_ = datetime.now()

        def age(col, r_):
            try:
                return (now_ - datetime.fromisoformat((r_[col] or "")[:19])).days
            except ValueError:
                return 0
        for r_ in rows:
            nm = first_name(r_)
            link = url_for("prints_order", oid_=r_["id"])
            who = dict(name=r_["household"], link=link, hid=r_["household_id"])
            due_now = date.today().isoformat()
            st = r_["state"]
            if st == "needs_files":
                out.append(dict(text=f"{nm}: needs the photo files", link=link, who=who, when=due_now))
            elif st == "paid":
                out.append(dict(text=f"{nm}: paid, ready for the lab", link=link, who=who, when=due_now))
            elif st == "prepared" and age("prepared_at", r_) >= 2:
                out.append(dict(text=f"{nm}: files ready, order it at the lab", link=link, who=who, when=due_now))
            elif st == "invoiced" and age("invoice_issued", r_) >= P.UNPAID_EXPIRE_DAYS and figures(r_)["paid"] > 0:
                out.append(dict(text=f"{nm}: part paid, the rest unpaid after {P.UNPAID_EXPIRE_DAYS} days (a part payment "
                                     "keeps the invoice from expiring)", link=link, who=who, when=due_now))
            elif st == "invoiced" and age("invoice_issued", r_) >= P.UNPAID_REMIND_DAYS:
                out.append(dict(text=f"{nm}: waiting for payment since {ctx.nice(r_['invoice_issued'])}", link=link, who=who, when=due_now))
            elif st in ("new", "unmatched") and age("collected_at", r_) >= 2:
                out.append(dict(text=f"{nm}: not answered for 2 days", link=link, who=who, when=due_now))
            elif st == "ordered" and age("ordered_at", r_) >= 14:
                out.append(dict(text=f"{nm}: ordered at {lab_name(r_)}, not shipped after 14 days", link=link, who=who, when=due_now))
            elif st == "shipped" and (r_["shipped_at"] or "")[:10] == date.today().isoformat() and not r_["told_shipped"]:
                out.append(dict(text=f"{nm}: shipped. Tell {first_word(r_)}", link=link, who=who, when=due_now))
            if r_["before_payment"] and st != "invoiced" and owed(r_) > 0:
                out.append(dict(text=f"{nm}: ordered before payment, {P.dollars(owed(r_))} still owed", link=link, who=who,
                                when=due_now))
            if r_["lab_problem"]:
                out.append(dict(text=f"{r_['lab_problem']} had a problem with {nm}'s order", link=link))
        aside = sum(1 for d_ in G.KEPT_ROOT.glob("*/orders-unopened") for _f in d_.iterdir()) if G.KEPT_ROOT.is_dir() else 0
        if aside:
            out.append(dict(text=f"{aside} file{'s' if aside != 1 else ''} from a gallery's orders folder could not be "
                                 f"opened as a print order (set aside for {UNOPENED_KEEP_DAYS} days; History names the "
                                 "gallery)", link=url_for("prints_review")))
        if saved_once():
            try:
                seen = json.loads(setting("prints_low_named") or "{}")
            except ValueError:
                seen = {}
            now_seen, today_ = {}, date.today().isoformat()
            for it in items():
                if P.low_margin(it["each_cents"], it["cost_cents"]):
                    sig = f"{it['each_cents']}/{it['cost_cents']}"
                    was = seen.get(it["id"]) or []
                    now_seen[it["id"]] = was if was and was[0] == sig else [sig, today_]
                    if now_seen[it["id"]][1] == today_:
                        out.append(dict(text=f"{it['label']} priced below 1.5 times cost", link=url_for("prints_settings")))
            if now_seen != seen:
                put("prints_low_named", json.dumps(now_seen))
        if prints_on() and host_kind() in ("old", "unreachable"):
            out.append(dict(text="Prints are off on your web host: the PHP check failed", link=url_for("setup_step", step="galleries")))
        for r_ in q("""SELECT s.id, s.household_id, h.name FROM retention r JOIN sessions s ON s.id=r.session_id
                       JOIN households h ON h.id=s.household_id WHERE r.purged_at IS NULL AND r.notice LIKE '%print_hold%'"""):
            if held(r_["id"]):
                link = url_for("client_stage", hid=r_["household_id"]) + f"?s={r_['id']}"
                out.append(dict(text=f"Kept for print orders: {r_['name']}", link=link))
        return out

    ctx.hooks.today.append(today_items)

    SETTINGS = """{% extends "base.html" %}{% block title %}Print prices - Lumen Aureum{% endblock %}{% block body %}
<h1>Print prices</h1>
<p class="mute" style="max-width:760px">Clients order prints from their download gallery. Each order is sealed in their
browser so only your Lumen Aureum can open it, comes to you for review, and is invoiced to whoever ordered. Once it is
paid, Prepare for the lab gives you the print-ready files and an order sheet, and you order the prints at any lab.</p>
<form method="post" action="{{ url_for('prints_switch') }}" class="card f" style="max-width:760px">
  <label class="row" style="gap:6px"><input type="checkbox" name="offer" value="1" data-autosubmit {{ 'checked' if on }} {{ 'disabled' if not saved }}>Offer prints on download galleries</label>
  {% if not saved %}<div class="sub" style="color:var(--warn)">The price list below is a SAMPLE. Check it and press Save once; then prints can be turned on.</div>{% endif %}
  {% if on %}<input type="hidden" name="types_shown" value="1"><div class="sub">Kinds of session that offer prints:</div>
  <div class="row" style="flex-wrap:wrap;gap:10px">{% for k, lab in types.items() %}<label class="row" style="gap:4px"><input type="checkbox" name="type_{{ k }}" value="1" {{ 'checked' if k not in off }}>{{ lab }}</label>{% endfor %}</div>{% endif %}
  <noscript><button class="quiet">Save</button></noscript>{% if on %}<div><button class="quiet">Save</button></div>{% endif %}
</form>
<form method="post" action="{{ url_for('prints_save') }}" class="card f" style="max-width:1100px" id="list">
  <h2>Price list {% if not saved %}<span style="color:var(--warn);font-size:13px;font-weight:600">SAMPLE</span>{% endif %}</h2>
  <p class="sub" style="font-size:13px;margin:0">Sizes are in inches. "Your lab cost" is what one print costs you: it shows your margin and never changes a price. To set prices from your costs, press Fill prices from costs below. Sizes too small for a photo's pixels are hidden on the page (below "hide") or marked "May look soft" (below "mark").</p>
  <style>table.prices th { white-space: normal; font-size: 12px; } table.prices td, table.prices th { padding: 4px 3px; }
  table.prices input[type=checkbox] { width: auto; }</style>
  <div style="overflow-x:auto"><table class="r prices"><thead><tr><th>Shown as</th><th>Short</th><th>Long</th><th>Finish</th><th>Price</th><th>Your lab cost</th><th>Margin</th><th>Hide below ppi</th><th>Mark below ppi</th><th>Offered</th><th>Remove</th></tr></thead><tbody>
  {% for it in items %}<tr><td><input type="hidden" name="id" value="{{ it.id }}"><input name="label_{{ it.id }}" value="{{ it.label }}" maxlength="80" style="width:170px" aria-label="Shown as"></td>
    <td><input name="w_{{ it.id }}" value="{{ num(it.w) }}" style="width:60px" aria-label="Short side, inches"></td><td><input name="h_{{ it.id }}" value="{{ num(it.h) }}" style="width:60px" aria-label="Long side, inches"></td>
    <td><input name="finish_{{ it.id }}" value="{{ it.finish }}" maxlength="40" style="width:100px" aria-label="Finish"></td>
    <td><input name="each_{{ it.id }}" value="{{ cents(it.each_cents) }}" style="width:80px" aria-label="Price"></td>
    <td><input name="cost_{{ it.id }}" value="{{ cents(it.cost_cents) }}" style="width:80px" aria-label="Your lab cost">{% if it.cost_from and it.cost_from != 'you' %}<div class="sub" style="font-size:11px">from {{ it.cost_from }}{% if it.cost_date %}, {{ nice(it.cost_date) }}{% endif %}</div>{% endif %}</td>
    <td>{% set m = margin(it) %}{% if m %}<span {% if low(it) %}style="color:var(--warn)" data-tip="Below 1.5 times your cost"{% endif %}>{{ dollars(m[0]) }}, {{ m[1] }}x</span>{% endif %}</td>
    <td><input name="hide_{{ it.id }}" value="{{ it.ppi_hide }}" style="width:52px" aria-label="Hide below pixels per inch"></td><td><input name="mark_{{ it.id }}" value="{{ it.ppi_mark }}" style="width:52px" aria-label="Mark below pixels per inch"></td>
    <td><input type="checkbox" name="on_{{ it.id }}" value="1" {{ 'checked' if it.offered }} aria-label="Offered"></td>
    <td><input type="checkbox" name="drop_{{ it.id }}" value="1" aria-label="Remove"></td></tr>
    {% for f in lab_fields(it) %}<tr><td colspan="11">{{ f }}</td></tr>{% endfor %}{% endfor %}
  <tr><td><input name="label_new" placeholder="New size, e.g. 5x5 print, Lustre" maxlength="80" style="width:170px" aria-label="New size: shown as"></td><td><input name="w_new" style="width:60px" aria-label="New size: short side"></td><td><input name="h_new" style="width:60px" aria-label="New size: long side"></td><td><input name="finish_new" style="width:100px" aria-label="New size: finish"></td><td><input name="each_new" style="width:80px" aria-label="New size: price"></td><td><input name="cost_new" style="width:80px" aria-label="New size: your lab cost"></td><td colspan="5"></td></tr>
  </tbody></table></div>
  <div class="f2"><div><label class="l" for="pr-markup">Markup (times your lab cost)</label><input id="pr-markup" name="markup" value="{{ markup }}" style="width:80px"></div>
    <div><label class="l" for="pr-days">Invoice within (days, as the page says)</label><input id="pr-days" name="invoice_days" value="{{ invoice_days }}" style="width:80px"></div></div>
  <div class="f2"><div><label class="l" for="pr-ln">A large order: more prints than</label><input id="pr-ln" name="large_n" value="{{ large_n }}" style="width:80px"></div>
    <div><label class="l" for="pr-lc">or more dollars than</label><input id="pr-lc" name="large_c" value="{{ cents(large_c) }}" style="width:100px"></div></div>
  <h2 id="shipping">Shipping</h2>
  <p class="sub" style="font-size:13px;margin:0">Per destination: at the lab's real cost for each order (you type it at review), that cost plus a handling amount, or a flat amount. The page shows the line you write, never a figure the app works out.</p>
  {% for d, r in ship.items() %}<div class="row" style="flex-wrap:wrap;gap:8px;align-items:end;margin:6px 0">
    <label class="row" style="gap:4px;min-width:200px"><input type="checkbox" name="ship_on_{{ d }}" value="1" {{ 'checked' if r.on }}>{{ dest_label[d] }}</label>
    <select name="ship_rule_{{ d }}" aria-label="{{ dest_label[d] }}: charge">{% for k, lab in rule_label.items() %}<option value="{{ k }}" {{ 'selected' if r.rule == k }}>{{ lab }}</option>{% endfor %}</select>
    <span><label class="l" for="sh-{{ d }}">Handling</label><input id="sh-{{ d }}" name="ship_handling_{{ d }}" value="{{ cents(r.handling) }}" style="width:70px"></span>
    <span><label class="l" for="sf-{{ d }}">Flat</label><input id="sf-{{ d }}" name="ship_flat_{{ d }}" value="{{ cents(r.flat) }}" style="width:70px"></span>
    <span class="grow"><label class="l" for="ss-{{ d }}">What the page says</label><input id="ss-{{ d }}" name="ship_say_{{ d }}" value="{{ r.say }}" maxlength="160" placeholder="{{ 'Pick up from ' ~ studio if d == 'pickup' else '' }}"></span>
    {% if ranges[d] %}<span class="sub" style="font-size:12px">{{ ranges[d] }}</span>{% endif %}</div>{% endfor %}
  {% if not ship.ca.on %}<p class="sub" style="font-size:12.5px">Canada: before you turn it on, write the page's line; labs often add duties or a surcharge across the border.</p>{% endif %}
  <div><label class="l" for="pr-arrive">How prints arrive (on the page and the invoice)</label><input id="pr-arrive" name="arrive" value="{{ arrive }}" maxlength="200"></div>
  <div class="row" style="gap:8px;margin-top:10px"><button class="primary">Save</button></div>
</form>
{% if costed %}<form method="post" action="{{ url_for('prints_fill') }}" class="card f" style="max-width:760px" data-confirm="{% if plan %}Change {{ plan|length }} price{{ '' if plan|length == 1 else 's' }} to {{ markup }} times your lab cost, rounded up to the whole dollar? {{ fill_words(plan) }}. Nothing else changes.{% else %}Every price with a lab cost already matches {{ markup }} times it. Nothing would change.{% endif %}">
  <div class="row"><span class="grow">Sets each price that has a lab cost to {{ markup }} times that cost, rounded up to the whole dollar, only when you press it. Typing a cost never changes a price.</span><button class="quiet">Fill prices from costs ({{ markup }}x)</button></div></form>{% endif %}
<form method="post" action="{{ url_for('prints_lab_choice') }}" class="card f" style="max-width:760px">
  <h2>Where prints are made</h2>
  <label class="row" style="gap:6px"><input type="radio" name="lab" value="hand" {{ 'checked' if lab == 'hand' }}>By hand, at any lab (Prepare for the lab)</label>
  {% for l in labs %}<label class="row" style="gap:6px"><input type="radio" name="lab" value="{{ l.id }}" {{ 'checked' if lab == l.id }} {{ 'disabled' if not l.ok }}>{{ l.name }}{% if l.words %} <span class="sub">{{ l.words }}</span>{% endif %}</label>{% endfor %}
  <div><button class="quiet">Save</button></div>
</form>
{% for l in labs if l.ok and (l.costs or l.starter) %}<div class="card f" style="max-width:760px"><h2>{{ l.name }}</h2><div class="row" style="gap:8px;flex-wrap:wrap">
  {% if l.costs %}<form method="post" action="{{ url_for('prints_lab_costs', lab=l.id) }}" class="inline" data-confirm="Fill your lab cost from {{ l.name }} for every size tied to it? Costs you typed by hand are replaced; your prices stay."><button class="quiet">Fill costs from {{ l.name }}</button></form>{% endif %}
  {% if l.starter and not saved %}<form method="post" action="{{ url_for('prints_lab_starter', lab=l.id) }}" class="inline" data-confirm="Replace the SAMPLE list with {{ l.name }}'s starter list?"><button class="quiet">Use {{ l.name }}'s starter list</button></form>{% endif %}
</div></div>{% endfor %}
<div class="card f" style="max-width:760px"><h2>Sales tax on prints</h2>
  <p style="margin:0">{{ accountant }}</p>
  <p class="sub" style="margin:4px 0 0">Tax on prints: {{ 'yes' if tax[0] else 'no' }}. Tax on shipping: {{ 'yes' if tax[1] else 'no' }}. <a class="go" href="{{ url_for('setup_step', step='tax') }}">Sales tax &rsaquo;</a></p></div>
{% endblock %}"""

    def ship_ranges():

        out = {}
        for d_ in P.DESTS:
            vals = []
            for r_ in q("""SELECT payload, cost_shipping, ship_lab_cents FROM print_orders WHERE (cost_shipping IS NOT NULL
                           OR ship_lab_cents IS NOT NULL) ORDER BY id DESC LIMIT 200"""):
                if P.dest_of(json.loads(r_["payload"] or "{}").get("ship") or {}) == d_:
                    vals.append(r_["cost_shipping"] if r_["cost_shipping"] is not None else r_["ship_lab_cents"])
                if len(vals) >= 20:
                    break
            if vals:
                lo, hi = min(vals), max(vals)
                out[d_] = (f"Your last {len(vals)} {'' if d_ == 'pickup' else DEST_LABEL[d_].split(' (')[0] + ' '}orders: "
                           f"{P.dollars(lo)} to {P.dollars(hi)} (an estimate)")
            else:
                out[d_] = ""
        return out

    def lab_list():
        out = []
        for lab in ctx.hooks.labs:
            ok, words = call_lab(lab, "ready", default=(False, "did not answer"))
            out.append(dict(id=lab["id"], name=lab.get("name") or lab["id"], ok=bool(ok), words=words or "",
                            costs=callable(lab.get("costs")), starter=callable(lab.get("starter_items"))))
        return out

    def lab_fields(it):
        out = []
        for lab in ctx.hooks.labs:
            if lab.get("item_fields"):
                h_ = call_lab(lab, "item_fields", it, default=None)
                if h_:
                    out.append(Markup(h_))
        return out

    def cents_s(c):
        return "" if c is None else f"{c // 100}.{c % 100:02d}"

    @app.get("/settings/prints", endpoint="prints_settings")
    def prints_settings():
        its = items()
        return render_template_string(
            SETTINGS, items=its, saved=saved_once(), on=prints_on(), types=C.SESSION_TYPES, off=type_off(),
            markup=markup(), invoice_days=setting("prints_invoice_days") or 3, ship=ship_rules(), dest_label=DEST_LABEL,
            rule_label=RULE_LABEL, ranges=ship_ranges(), arrive=setting("prints_arrive", ARRIVE_DEFAULT), studio=studio_name(),
            labs=lab_list(), lab=setting("prints_lab") or "hand", tax=tax_flags(), accountant=ACCOUNTANT,
            cents=cents_s, dollars=P.dollars, num=P.num, nice=ctx.nice, lab_fields=lab_fields,
            margin=lambda it: P.margin(it["each_cents"], it["cost_cents"]),
            low=lambda it: P.low_margin(it["each_cents"], it["cost_cents"]),
            costed=sum(1 for it in its if it["cost_cents"]), plan=fill_plan(), fill_words=fill_words, large_n=setting("prints_large_n") or P.LARGE_PRINTS,
            large_c=int(setting("prints_large_cents") or P.LARGE_CENTS))

    app.jinja_env.globals["SETTINGS_ENDPOINTS"].add("prints_settings")

    def refuse(words, back):
        session["notice"] = words
        g.refused = True
        return redirect(back)

    def save_version():
        v = plv() + (1 if saved_once() else 0)
        put("prints_plv", v)
        x("INSERT OR REPLACE INTO price_list_versions (plv, json, saved_at) VALUES (?,?,?)",
          (v, json.dumps(dict(items=items(), ship=ship_rules(), markup=str(markup()))),
           datetime.now().isoformat(timespec="seconds")))
        return v

    @app.post("/settings/prints", endpoint="prints_save")
    def prints_save():
        f = request.form
        back = url_for("prints_settings") + "#list"
        try:
            mk = P.markup_in(f.get("markup") or "3")
            days = int(f.get("invoice_days") or 3)
            large_n = int(f.get("large_n") or P.LARGE_PRINTS)
            large_c = P.cents(f.get("large_c")) or P.LARGE_CENTS
            if not 1 <= days <= 30 or not 1 <= large_n <= 1000:
                raise ValueError
        except ValueError:
            return refuse("Check the markup (1 to 20), the invoice days (1 to 30) and the large-order numbers. Nothing was saved.", back)
        rows = []
        have = {i_["id"]: i_ for i_ in items()}
        for iid in f.getlist("id"):
            if iid not in have:
                continue
            if f.get("drop_" + iid):
                rows.append(("drop", iid, None))
                continue
            try:
                w, h = sorted((P.size_in(f.get("w_" + iid)), P.size_in(f.get("h_" + iid))))
                each, cost = P.cents(f.get("each_" + iid)), P.cents(f.get("cost_" + iid))
                hide, mark_ = int(f.get("hide_" + iid) or P.PPI_HIDE), int(f.get("mark_" + iid) or P.PPI_MARK)
                if not (50 <= hide <= 600 and hide <= mark_ <= 1200):
                    raise ValueError("ppi")
            except ValueError:
                return refuse(f"Check the line {have[iid]['label']}: sizes 2 to 60 inches, amounts like 24 or 24.50, "
                              "pixels per inch from 50 (mark at least hide). Nothing was saved.", back)
            label = P.clean(f.get("label_" + iid))[:80] or f"{P.num(w)}x{P.num(h)} print"
            rows.append(("set", iid, dict(label=label, w=float(w), h=float(h), finish=P.clean(f.get("finish_" + iid))[:40],
                                          each=each, cost=cost, hide=hide, mark=mark_, on=1 if f.get("on_" + iid) else 0)))
        new = None
        if (f.get("label_new") or f.get("w_new") or f.get("each_new")):
            try:
                w, h = sorted((P.size_in(f.get("w_new")), P.size_in(f.get("h_new"))))
                each, cost = P.cents(f.get("each_new")), P.cents(f.get("cost_new"))
            except ValueError:
                return refuse("The new size needs a short and a long side (2 to 60 inches) and a price like 24 or 24.50. "
                              "Nothing was saved.", back)
            new = dict(label=P.clean(f.get("label_new"))[:80] or f"{P.num(w)}x{P.num(h)} print", w=float(w), h=float(h),
                       finish=P.clean(f.get("finish_new"))[:40], each=each, cost=cost)
        today_ = date.today().isoformat()
        for what, iid, v in rows:
            if what == "drop":
                x("DELETE FROM price_list_items WHERE id=?", (iid,))
                continue
            old = have[iid]

            price_set = old["price_set"] or (v["each"] is not None and v["each"] != old["each_cents"])
            each = v["each"]
            cost_changed = v["cost"] != old["cost_cents"]
            x("""UPDATE price_list_items SET label=?, w=?, h=?, finish=?, each_cents=?, cost_cents=?, ppi_hide=?, ppi_mark=?,
                 offered=?, price_set=?, cost_from=CASE WHEN ? THEN 'you' ELSE cost_from END,
                 cost_date=CASE WHEN ? THEN ? ELSE cost_date END WHERE id=?""",
              (v["label"], v["w"], v["h"], v["finish"], each, v["cost"], v["hide"], v["mark"], v["on"],
               1 if price_set else 0, 1 if cost_changed else 0, 1 if cost_changed else 0, today_, iid))
        if new:
            n_ = int(setting("prints_next_item") or 1)
            while q("SELECT 1 FROM price_list_items WHERE id=?", (f"pl-{n_}",), one=True):
                n_ += 1
            put("prints_next_item", n_ + 1)
            each = new["each"]
            x("""INSERT INTO price_list_items (id, label, w, h, finish, each_cents, cost_cents, cost_from, cost_date, price_set,
                 offered, sort) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
              (f"pl-{n_}", new["label"], new["w"], new["h"], new["finish"], each, new["cost"], "you" if new["cost"] else "",
               today_ if new["cost"] else "", 1 if new["each"] is not None else 0, n_))
        rules = ship_rules()
        for d_ in P.DESTS:
            try:
                rules[d_] = dict(on=bool(f.get("ship_on_" + d_)), rule=f.get("ship_rule_" + d_) if f.get("ship_rule_" + d_) in P.CHARGE_RULES else "cost",
                                 handling=P.cents(f.get("ship_handling_" + d_)) or 0, flat=P.cents(f.get("ship_flat_" + d_)) or 0,
                                 say=P.clean(f.get("ship_say_" + d_))[:160])
            except ValueError:
                return refuse(f"Check the shipping amounts for {DEST_LABEL[d_]}. Nothing was saved.", back)
        if rules["ca"]["on"] and not rules["ca"]["say"]:
            return refuse("Before Canada is turned on, write what the page says about shipping there (labs often add "
                          "duties or a surcharge). Nothing was saved.", back)
        for lab in ctx.hooks.labs:
            if callable(lab.get("item_save")):
                for it in items():
                    res = call_lab(lab, "item_save", dict(it), f, default=None)
                    if res and not res[1] and res[0] is not None:
                        refs = dict(it["lab"])
                        refs[lab["id"]] = res[0]
                        x("UPDATE price_list_items SET lab=? WHERE id=?", (json.dumps(refs), it["id"]))
                    elif res and res[1]:
                        session["notice"] = f"{lab.get('name') or lab['id']}: {res[1]}"
        put("prints_ship", json.dumps(rules))
        put("prints_markup", str(mk))
        put("prints_invoice_days", days)
        put("prints_large_n", large_n)
        put("prints_large_cents", large_c)
        put("prints_arrive", P.clean(f.get("arrive"))[:200] or ARRIVE_DEFAULT)
        v = save_version()
        put("prints_saved", "1")
        g.audit_note = f"version {v}"
        session["notice"] = "Saved."
        return redirect(back)

    def fill_plan():

        mk = markup()
        out = []
        for it in items():
            if it["cost_cents"]:
                new = P.fill_price(it["cost_cents"], mk)
                if new != it["each_cents"]:
                    out.append((it, it["each_cents"], new))
        return out

    def fill_words(plan):
        return "; ".join(f"{it['label']} {P.dollars(a) if a is not None else 'no price'} to {P.dollars(b)}" for it, a, b in plan)

    @app.post("/settings/prints/fill", endpoint="prints_fill")
    def prints_fill():

        mk = markup()
        plan = fill_plan()
        for it, _a, new in plan:
            x("UPDATE price_list_items SET each_cents=?, price_set=1 WHERE id=?", (new, it["id"]))
        if plan:
            save_version()
            put("prints_saved", "1")
        n = len(plan)
        g.audit_note = f"{n} price{'' if n == 1 else 's'} at {mk}x lab cost" + (f": {fill_words(plan)}" if plan else "")
        session["notice"] = (f"{n} price{'' if n == 1 else 's'} filled from your lab costs ({mk}x)." if plan else
                             "Every price with a lab cost already matches it. Nothing changed.")
        return redirect(url_for("prints_settings") + "#list")

    @app.post("/settings/prints/switch", endpoint="prints_switch")
    def prints_switch():
        f = request.form
        if f.get("offer") and not saved_once():
            return refuse("The price list is still the SAMPLE: check it and press Save first.", url_for("prints_settings"))
        put("prints_on", "1" if f.get("offer") else "0")
        if f.get("offer") and f.get("types_shown"):
            put("prints_type_off", json.dumps([k_ for k_ in C.SESSION_TYPES if not f.get("type_" + k_)]))
        return redirect(url_for("prints_settings"))

    @app.post("/settings/prints/lab", endpoint="prints_lab_choice")
    def prints_lab_choice():
        lab = request.form.get("lab", "hand")
        if lab != "hand" and not any(l_["id"] == lab and l_["ok"] for l_ in lab_list()):
            return refuse("That lab can't be chosen now.", url_for("prints_settings"))
        put("prints_lab", lab)
        return redirect(url_for("prints_settings"))

    @app.post("/settings/prints/lab/<lab>/costs", endpoint="prints_lab_costs")
    def prints_lab_costs(lab):

        l_ = lab_by_id(lab) or abort(404)
        got = call_lab(l_, "costs", items(), default=None) or {}
        n = 0
        for iid, c_ in got.items():
            if isinstance(c_, dict) and isinstance(c_.get("cents"), int) and 0 < c_["cents"] <= P.MONEY_MAX and item_of(iid):
                x("UPDATE price_list_items SET cost_cents=?, cost_from=?, cost_date=? WHERE id=?",
                  (c_["cents"], l_.get("name") or lab, str(c_.get("as_of") or date.today().isoformat())[:10], iid))
                n += 1
        g.audit_note = f"{n} cost{'' if n == 1 else 's'} from {l_.get('name') or lab}"
        return redirect(url_for("prints_settings") + "#list")

    @app.post("/settings/prints/lab/<lab>/starter", endpoint="prints_lab_starter")
    def prints_lab_starter(lab):
        l_ = lab_by_id(lab) or abort(404)
        if saved_once():
            return refuse("Your own price list is saved; the starter list replaces only the SAMPLE.", url_for("prints_settings"))
        got = call_lab(l_, "starter_items", default=None) or []
        rows = []
        for i, it in enumerate(got):
            try:
                w, h = sorted((P.size_in(it["w"]), P.size_in(it["h"])))
                rows.append((f"pl-{i + 1}", P.clean(it.get("label"))[:80] or f"{P.num(w)}x{P.num(h)} print", float(w), float(h),
                             P.clean(it.get("finish"))[:40], it.get("each"), it.get("cost"), l_.get("name") or lab,
                             json.dumps({lab: (it.get("lab") or {}).get(lab)} if (it.get("lab") or {}).get(lab) is not None else {}), i))
            except (KeyError, TypeError, ValueError):
                continue
        if rows:
            x("DELETE FROM price_list_items")
            for r_ in rows:
                x("""INSERT INTO price_list_items (id, label, w, h, finish, each_cents, cost_cents, cost_from, lab, sort, offered)
                     VALUES (?,?,?,?,?,?,?,?,?,?,1)""", r_)
            put("prints_next_item", len(rows) + 1)
        g.audit_note = f"{len(rows)} sizes from {l_.get('name') or lab}"
        return redirect(url_for("prints_settings") + "#list")

    app.config.setdefault("ep_words", {}).update({
        "prints_lab_costs": "Filled print costs from a lab", "prints_lab_starter": "Took a lab's starter price list",
        "prints_save": "Saved the print price list", "prints_fill": "Filled print prices from lab costs",
        "prints_switch": lambda f: "Turned print ordering on" if f.get("offer") else "Turned print ordering off", "prints_lab_choice": "Chose where prints are made"})

    REVIEW = """{% extends "base.html" %}{% block title %}Print orders to review - Lumen Aureum{% endblock %}{% block body %}
<h1>Print orders from people not on file</h1>
<p class="mute" style="max-width:760px">Whoever orders pays. These orders came from someone whose email is not on file. The app never makes a client by itself: choose for each.</p>
{% for o in rows %}<div class="card f" style="max-width:760px">
  {% if o.state == 'unreadable' %}<h3 style="margin:0">Unreadable order <span class="mute" style="font-weight:400;font-size:13px">{{ o.client }}, collected {{ o.collected_at[:16].replace('T', ' ') }}</span></h3>
  <p class="sub">It opened, but breaks the order rules ({{ o.why }}). It is never acted on. What it says:</p>
  <pre style="white-space:pre-wrap;max-height:200px;overflow:auto;font-size:12px">{{ o.raw }}</pre>
  <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='discard') }}" class="inline" data-confirm="Remove this unreadable order? Its id stays known, so it is never taken in again."><button class="quiet">Remove</button></form>
  {% else %}<h3 style="margin:0">{{ o.name }} <span class="mute" style="font-weight:400;font-size:13px">{{ o.email }}</span></h3>
  <div class="sub">From the gallery of {{ o.client }}, {{ o.n }} print{{ '' if o.n == 1 else 's' }}, {{ dollars(o.sub) }} before shipping and tax. Collected {{ o.collected_at[:16].replace('T', ' ') }}.</div>
  <div class="row" style="flex-wrap:wrap;gap:8px;margin-top:8px">
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='guest') }}" class="inline"><button class="quiet">Bill {{ o.name }} as a guest</button></form>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='add_contact') }}" class="inline"><button class="quiet">Add {{ o.name }} to {{ o.client }}</button></form>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='new_client') }}" class="inline" data-confirm="Make {{ o.name }} ({{ o.email }}) a client of their own? The order and its payments go on their own statement, and a note on their record links them to {{ o.client }}'s gallery."><button class="quiet">Make a new client</button></form>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='not_real') }}" class="inline" data-confirm="Delete this order as not a real order? Its id stays known, so it is never taken in again."><button class="ghost">Not a real order</button></form>
    <a class="go" href="{{ url_for('prints_order', oid_=o.id) }}">Look at it &rsaquo;</a></div>{% endif %}
</div>{% else %}<p class="mute">Nothing to review.</p>{% endfor %}
{% endblock %}"""

    def client_of(sid):
        r_ = q("SELECT h.name, h.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (sid,), one=True)
        return r_ if r_ else {"name": "", "id": None}

    @app.get("/prints/review", endpoint="prints_review")
    def prints_review():
        rows = []
        for r_ in q("SELECT * FROM print_orders WHERE state IN ('unmatched', 'unreadable') ORDER BY collected_at, host_mtime, id"):
            o = dict(r_)
            ls = lines_of(o["id"])
            fl = json.loads(o["flags"] or "{}")
            rows.append(dict(o, name=o["bill_name"], email=o["bill_email"], client=client_of(o["session_id"])["name"],
                             n=sum(l_["qty"] for l_ in ls), sub=sum(l_["qty"] * l_["each_cents"] for l_ in ls),
                             why=fl.get("why", ""), raw=o["payload"][:20000]))
        return render_template_string(REVIEW, rows=rows, dollars=P.dollars)

    ORDER = """{% extends "base.html" %}{% block title %}Print order - Lumen Aureum{% endblock %}{% block body %}
<p><a class="go" href="{{ back }}">&lsaquo; {{ client }}</a></p>
<h1>Print order <span class="mute" style="font-weight:400;font-size:16px">{{ words }}{% if o.lab_name and o.state in ('ordered','shipped','delivered','remake') %} at {{ o.lab_name }}{% endif %}{% if lab_numbers %}, no. {{ lab_numbers }}{% endif %}</span></h1>
<div class="card f" style="max-width:980px">
  <div><b>{{ who[0] }}</b> {{ who[1] }} {% if not who[2] %}<span class="mute">(not on file)</span>{% endif %}{% if p.who and p.who.phone %} &middot; {{ p.who.phone }}{% endif %}</div>
  <div class="sub">{% if rel %}{{ rel }} &middot; {% endif %}Collected {{ (o.collected_at or '')[:16].replace('T', ' ') }}{% if o.browser_at %} (the client's device said {{ local_time(o.browser_at) }}){% endif %} &middot; Prints made {{ lab_label }}</div>
  <div class="sub">Ship to: {{ ship_lines|join(', ') }}{% if ship.phone %} &middot; {{ ship.phone }}{% endif %}</div>
  {% if p.note %}<div style="margin-top:6px">Note: {{ p.note }}</div>{% endif %}
  {% if o.before_payment %}<div style="color:var(--crit);margin-top:6px">Ordered before payment: you carry the risk for this order.</div>{% endif %}
  {% if lab_words %}<div class="sub">{{ lab_words }}</div>{% endif %}
</div>
{% if incomplete %}<div class="card f" style="max-width:980px;border-left:3px solid var(--crit)"><h2 style="margin:0">Shipping address incomplete</h2>
  <p class="sub" style="margin:4px 0">Only the city, state and ZIP were kept (the full address is reduced a year after an order closes). Prepare for the lab and any lab hand-off wait until the full address is entered.</p>
  <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='ship_to') }}" class="f2" style="max-width:760px">
    <div><label class="l" for="sn">Name{% if ship.how == 'other' %} (who receives it){% endif %}</label><input id="sn" name="name" maxlength="60" {{ 'required' if ship.how == 'other' }}></div>
    <div><label class="l" for="sa1">Street</label><input id="sa1" name="addr1" maxlength="60" required></div>
    <div><label class="l" for="sa2">Apartment, suite</label><input id="sa2" name="addr2" maxlength="60"></div>
    <div><label class="l" for="sc">City</label><input id="sc" name="city" maxlength="40" value="{{ ship.city }}" required></div>
    <div><label class="l" for="ss">State</label><input id="ss" name="state" maxlength="2" value="{{ ship.state }}" required style="width:60px"></div>
    <div><label class="l" for="sz">ZIP</label><input id="sz" name="zip" maxlength="10" value="{{ ship.zip }}" required style="width:100px"></div>
    <input type="hidden" name="country" value="{{ ship.country or 'US' }}">
    <div><label class="l" for="sp">Phone</label><input id="sp" name="phone" maxlength="25"></div>
    <div><button class="quiet">Save the full address</button></div></form></div>{% endif %}
{% if flags %}<div class="card f" style="max-width:980px;border-left:3px solid var(--warn)"><h2 style="margin:0">Check</h2><ul style="margin:4px 0">{% for k, w, link in flags %}<li>{{ w }}{% if link %} <a class="go" href="{{ link }}">Open &rsaquo;</a>{% endif %}</li>{% endfor %}</ul>
  {% if o.state not in ('unreadable',) %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='flags_ok') }}" class="inline"><label class="row" style="gap:6px"><input type="checkbox" name="checked" value="1" data-autosubmit {{ 'checked' if flags_ok }}>I have checked these</label><noscript><button class="quiet">Save</button></noscript></form>{% endif %}</div>{% endif %}
{% if o.state != 'unreadable' %}
<div class="card f" style="max-width:980px"><h2 style="margin:0">Each print as it will print</h2>
{% for l in lines %}<div class="row" style="gap:14px;align-items:flex-start;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:10px">
  <img src="{{ url_for('prints_line_image', oid_=o.id, lid=l.id) }}" alt="Line {{ l.n }} as it will print" style="max-width:220px;max-height:220px;background:var(--surface-2)" loading="lazy">
  <div class="grow"><b>{{ l.n }}. {{ l.label }}</b> &times; {{ l.qty }} &middot; {{ l.photo_name }}{% if l.cropped %} &middot; cropped{% else %} &middot; full frame{% endif %}
    <div class="sub">{{ l.ppi_words }}{% if l.problem %} <span style="color:var(--crit)">{{ l.problem }}</span>{% endif %}</div>
    <div class="sub">Client price {{ dollars(l.each_cents) }} each{% if l.cost is not none %} &middot; your lab cost {{ dollars(l.cost) }} &middot; profit {{ dollars((l.each_cents - l.cost) * l.qty) }}{% endif %}</div>
    {% if editable %}<form method="post" action="{{ url_for('prints_line', oid_=o.id, lid=l.id) }}" class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">
      <label class="l" for="q{{ l.id }}">Quantity</label><input id="q{{ l.id }}" name="qty" value="{{ l.qty }}" style="width:60px">
      <label class="l" for="e{{ l.id }}">Each</label><input id="e{{ l.id }}" name="each" value="{{ cents(l.each_cents) }}" style="width:80px">
      <button class="quiet" name="do" value="save">Save</button><button class="ghost" name="do" value="remove" data-confirm="Remove this line from the order?">Remove</button></form>{% endif %}
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">
      {% if l.cropped and can_check %}<form method="post" action="{{ url_for('prints_line', oid_=o.id, lid=l.id) }}" class="inline"><input type="hidden" name="do" value="{{ 'unconfirm' if l.crop_confirmed else 'confirm' }}"><label class="row" style="gap:4px"><input type="checkbox" data-autosubmit {{ 'checked' if l.crop_confirmed }}>Looks right</label><noscript><button class="quiet">Save</button></noscript></form>{% endif %}
      {% if l.soft and can_check %}<form method="post" action="{{ url_for('prints_line', oid_=o.id, lid=l.id) }}" class="inline"><input type="hidden" name="do" value="{{ 'unsoft' if l.soft_confirmed else 'soft' }}"><label class="row" style="gap:4px"><input type="checkbox" data-autosubmit {{ 'checked' if l.soft_confirmed }}>Print it anyway (may look soft)</label><noscript><button class="quiet">Save</button></noscript></form>{% endif %}
      {% if can_check and not l.problem_photo %}<a class="go" href="{{ url_for('prints_crop', oid_=o.id, lid=l.id) }}">Change crop &rsaquo;</a>{% endif %}
      {% if l.problem_photo and can_check %}<form method="post" action="{{ url_for('prints_line', oid_=o.id, lid=l.id) }}" class="row" style="gap:6px"><input type="hidden" name="do" value="photo"><select name="photo" aria-label="The matching photo">{% for f, n in photos %}<option value="{{ f }}" {{ 'selected' if n == l.photo_name }}>{{ n }}</option>{% endfor %}</select><button class="quiet">Use this photo</button></form>{% endif %}
    </div></div></div>{% endfor %}
</div>
<div class="card f" style="max-width:980px"><h2 style="margin:0">Money</h2>
  <table class="r" style="max-width:640px"><tbody>
  <tr><td>Prints</td><td class="r">{{ dollars(f.sub) }}</td>{% if cost_lines is not none %}<td class="mute">your lab cost {{ dollars(cost_lines) }}</td>{% endif %}</tr>
  <tr><td>Shipping charged ({{ rule_words }})</td><td class="r">{% if f.ship is none %}<span style="color:var(--warn)">type the lab's shipping</span>{% else %}{{ dollars(f.ship) }}{% endif %}</td><td class="mute">{% if o.ship_lab_cents is not none %}the lab's shipping {{ dollars(o.ship_lab_cents) }}{% endif %}</td></tr>
  <tr><td>Sales tax{% if f.rate %} ({{ '%g' % f.rate }}%){% endif %}</td><td class="r">{{ dollars(f.tax) }}</td><td class="mute">{{ tax_warning }}</td></tr>
  <tr><td><b>Total</b></td><td class="r"><b>{{ dollars(f.total) }}</b></td>{% if profit is not none %}<td class="mute">profit {{ dollars(profit) }}</td>{% endif %}</tr>
  <tr><td>Paid</td><td class="r">{{ dollars(f.paid) }}</td><td></td></tr><tr><td>Balance</td><td class="r">{{ dollars(f.balance) }}</td><td></td></tr></tbody></table>
  {% if editable %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='shipping') }}" class="row" style="gap:6px;flex-wrap:wrap;align-items:end">
    <span><label class="l" for="labship">The lab's shipping for this order</label><input id="labship" name="lab_ship" value="{{ cents(o.ship_lab_cents) }}" style="width:90px"></span>
    <span><label class="l" for="chship">Charge the client (blank: by the rule)</label><input id="chship" name="ship" value="{{ cents(o.ship_cents) }}" style="width:90px"></span>
    {% if areas %}<span><label class="l" for="area">Taxed for</label><select id="area" name="area">{% for k, lab in areas %}<option value="{{ k }}" {{ 'selected' if k == area }}>{{ lab }}</option>{% endfor %}</select></span>{% endif %}
    <button class="quiet">Save</button></form>
    {% if quote_lab %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='quote') }}" class="inline"><button class="quiet">Shipping from {{ quote_lab }}</button></form>{% endif %}{% endif %}
  {% for pmt in f.pays %}<div class="row" style="gap:8px"><span class="grow">{{ nice(pmt.received) }}: {{ 'Refund' if pmt.kind == 'refund' else 'Payment' }} {{ dollars(pmt.amount_cents) }}{% if pmt.method %} ({{ pmt.method }}{% if pmt.ref %} {{ pmt.ref }}{% endif %}){% endif %}</span>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='void_payment') }}" class="inline" data-confirm="Void this {{ pmt.kind }}?"><input type="hidden" name="pid" value="{{ pmt.id }}"><button class="ghost">Void</button></form></div>{% endfor %}
  {% if o.invoice_number %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='payment') }}" class="row" style="gap:6px;flex-wrap:wrap;align-items:end;margin-top:6px" data-balance="{{ '%.2f'|format(pay_balance / 100) }}" data-credit-on="order">
    <input type="hidden" name="once" value="{{ form_once() }}">
    <span><label class="l" for="pk">Record</label><select id="pk" name="kind"><option value="payment">Payment</option><option value="refund">Refund</option></select></span>
    <span><label class="l" for="pay-amount">Amount</label><input id="pay-amount" name="amount" value="{{ cents(f.balance) if f.balance > 0 else '' }}" style="width:90px"></span>
    <span><label class="l" for="pd">Received</label><input id="pd" type="date" name="received" value="{{ today }}"></span>
    <span><label class="l" for="pm">How</label><input id="pm" name="method" style="width:110px" maxlength="40"></span>
    <span><label class="l" for="pr">Check number</label><input id="pr" name="ref" style="width:90px" maxlength="40"></span><button class="quiet">Record</button></form>{% endif %}
  {% if lab_costs %}<div style="margin-top:6px"><b>Lab costs, as paid</b> <span class="mute">(each stays in the year it was paid)</span>
    {% for c in lab_costs %}<div class="row sub" style="gap:8px;flex-wrap:wrap">{{ c.words }}{% if c.kind != 'refund' and not c.back %}
      <details><summary>The lab canceled or refunded this</summary><form method="post" action="{{ url_for('prints_act', oid_=o.id, do='lab_refund') }}" class="row" style="gap:6px;flex-wrap:wrap" data-confirm="Record the lab taking this cost back? The year it was paid keeps it; the refund counts on the day it came."><input type="hidden" name="cost" value="{{ c.id }}"><label class="l" for="lr{{ c.id }}">Refunded on</label><input id="lr{{ c.id }}" type="date" name="day" value="{{ today }}"><button class="quiet">Record the lab's refund</button></form></details>{% endif %}</div>{% endfor %}</div>
  {% elif o.state in ('ordered','shipped','delivered','closed','remake') %}<div class="sub" style="margin-top:6px">Lab cost recorded: none typed</div>{% endif %}
</div>
<div class="card f" style="max-width:980px"><h2 style="margin:0">Next</h2>
  {% if o.invoice_number %}<div class="row" style="gap:8px;flex-wrap:wrap">Invoice {{ o.invoice_number }}: <a class="go" href="{{ url_for('prints_invoice', oid_=o.id) }}">Open &rsaquo;</a> <a class="go" href="{{ mail_invoice }}" title="Opens a draft in your mail app with the invoice's number, total and how to pay.">Email it &rsaquo;</a> <span class="mute">The email gives the invoice's number, total and how to pay. Save the invoice as a PDF and attach it for their records, if you like.</span></div>{% endif %}
  {% for v in voided %}<div class="sub">Invoice {{ v.number }} voided {{ nice(v.at) }} ({{ v.why }})</div>{% endfor %}
  <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
  {% if o.state == 'new' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='invoice') }}" class="inline"><button class="primary">Make invoice</button></form>{% endif %}
  {% if o.state == 'invoiced' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='reissue') }}" class="inline" data-confirm="Void invoice {{ o.invoice_number }} so the order can be changed? A new invoice gets a new number."><button class="quiet">Change the order</button></form>{% endif %}
  {% if o.state in ('paid', 'prepared', 'ordered') or (o.before_payment and o.state == 'invoiced') %}<a class="go" href="{{ url_for('prints_prepare', oid_=o.id) }}">{{ 'Download again' if o.prepared_at else 'Prepare for the lab' }} &rsaquo;</a>{% endif %}
  {% if lab_send %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='hand_off') }}" class="inline"><button class="quiet">Send to {{ lab_send }}</button></form>{% endif %}
  {% if o.state == 'with_lab' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='check_lab') }}" class="inline"><button class="quiet">Check now</button></form>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='take_back') }}" class="inline" data-confirm="Take this order back from the lab? The lab is asked to cancel it first."><button class="quiet">Take it back</button></form>{% endif %}
  {% if o.state in ('expired',) %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='reopen') }}" class="inline" data-confirm="Reopen this order? It needs a new invoice."><button class="quiet">Reopen</button></form> <a class="go" href="{{ mail_expired }}">Tell {{ who[0] }} &rsaquo;</a>{% endif %}
  {% if o.state == 'needs_files' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='files_back') }}" class="inline"><button class="quiet">The files are here again</button></form>{% endif %}
  </div>
  {% if o.state in ('paid', 'prepared') or (o.before_payment and o.state == 'invoiced') %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='ordered') }}" class="f2" style="margin-top:10px;max-width:760px">
    <h3 style="margin:0;grid-column:1 / -1">Ordered at the lab</h3><input type="hidden" name="once" value="{{ form_once() }}">
    <div><label class="l" for="ln">Lab</label><input id="ln" name="lab_name" value="{{ last_lab }}" maxlength="60" required></div>
    <div><label class="l" for="lo">The lab's order number</label><input id="lo" name="lab_number" maxlength="60" required></div>
    <div><label class="l" for="cp">Lab cost: products</label><input id="cp" name="cost_products" style="width:90px"></div>
    <div><label class="l" for="cs">shipping</label><input id="cs" name="cost_shipping" style="width:90px"></div>
    <div><label class="l" for="ct">the lab's sales tax</label><input id="ct" name="cost_tax" style="width:90px"></div>
    <div><label class="l" for="co">other charges</label><input id="co" name="cost_other" style="width:90px"></div>
    <div><button class="quiet">Ordered at the lab</button></div></form>{% endif %}
  {% if o.state == 'remake' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='ordered') }}" class="row" style="gap:6px;flex-wrap:wrap;margin-top:10px;align-items:end">
    <input type="hidden" name="lab_name" value="{{ o.lab_name or last_lab }}"><span><label class="l" for="rn">The remake's lab order number</label><input id="rn" name="lab_number" maxlength="60" required></span>
    <span><label class="l" for="rcp">Remake cost: products</label><input id="rcp" name="cost_products" style="width:80px"></span>
    <span><label class="l" for="rcs">shipping</label><input id="rcs" name="cost_shipping" style="width:80px"></span>
    <span><label class="l" for="rct">the lab's tax</label><input id="rct" name="cost_tax" style="width:80px"></span>
    <span><label class="l" for="rco">other</label><input id="rco" name="cost_other" style="width:80px"></span>
    <button class="quiet">Remake ordered</button><span class="sub">Blank: a free remake ($0). The first order's cost stays as it was.</span></form>{% endif %}
  {% if o.state == 'ordered' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='shipped') }}" class="row" style="gap:6px;flex-wrap:wrap;margin-top:10px;align-items:end">
    {% if pickup %}<button class="quiet">Arrived at the studio</button>{% else %}<span><label class="l" for="sc">Carrier</label><input id="sc" name="carrier" maxlength="40" style="width:100px"></span>
    <span><label class="l" for="st">Tracking number</label><input id="st" name="tracking" maxlength="60" style="width:160px"></span>
    <span><label class="l" for="su">Tracking link</label><input id="su" name="url" maxlength="300" style="width:220px"></span><button class="quiet">Mark shipped</button>{% endif %}</form>{% endif %}
  {% if o.state == 'shipped' %}<div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px"><a class="go" href="{{ mail_shipped }}">Tell {{ who[0] }} &rsaquo;</a>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='delivered') }}" class="inline"><button class="quiet">{{ 'Picked up' if pickup else 'Mark delivered' }}</button></form></div>{% endif %}
  {% if o.state == 'delivered' %}<form method="post" action="{{ url_for('prints_act', oid_=o.id, do='close') }}" class="inline"><button class="quiet">Close</button></form>{% endif %}
  {% if o.state in ('shipped', 'delivered') %}<details style="margin-top:8px"><summary>Damaged, defective or lost</summary><form method="post" action="{{ url_for('prints_act', oid_=o.id, do='remake') }}" class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px"><input name="note" maxlength="300" placeholder="What happened" style="width:320px" aria-label="What happened"><button class="quiet">Remake requested</button></form><div class="sub">Ask the lab for a remake under its own policy, then type the remake's order number here.</div></details>{% endif %}
  {% if o.state in ('new', 'unmatched', 'invoiced', 'paid', 'prepared', 'ordered', 'needs_files', 'with_lab') %}<details style="margin-top:8px"><summary>Cancel order</summary>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='cancel') }}" class="f2" style="margin-top:6px" data-confirm="Cancel this print order?">
    {% if o.state == 'ordered' %}<div role="radiogroup" aria-label="At the lab">Did you cancel it at {{ o.lab_name or 'the lab' }} too?
      {% for k, w in cancel_answers %}<label class="row" style="gap:4px"><input type="radio" name="lab" value="{{ k }}" required>{{ w }}</label>{% endfor %}</div>{% endif %}
    {% if f.paid > 0 %}<div class="sub">The client paid {{ dollars(f.paid) }}: record a refund separately.</div>{% endif %}
    <div><button class="danger">Cancel order</button></div></form></details>{% endif %}
  {% if o.state == 'invoiced' and not o.before_payment %}<details style="margin-top:8px"><summary>Order before payment</summary>
    <form method="post" action="{{ url_for('prints_act', oid_=o.id, do='before_payment') }}" style="margin-top:6px" data-confirm="Order these prints before the invoice is paid? You carry the risk: if the client never pays, the lab's cost is yours. This is recorded in History.">
    <p class="sub" style="margin:0">Allows Prepare for the lab and ordering at the lab before the invoice is paid, for this order only. You carry the risk. Never used by any automatic sending.</p>
    <button class="danger">Order before payment</button></form></details>{% endif %}
</div>
{% endif %}
{% endblock %}"""

    CANCEL_ANSWERS = (("canceled", "Canceled at the lab"), ("finishing", "The lab is finishing the cancellation"),
                      ("not", "Not canceled at the lab"))

    def mailto(to, subject, lines):
        from urllib.parse import quote as _qu
        sign = setting("photographer_name") or setting("business_name")
        first = (to[0].split() or [""])[0] if to[0] else ""
        body = "\n\n".join([f"Hi {first}," if first else "Hello,"] + lines + [f"Thank you,\n{sign}" if sign else "Thank you"])
        return f"mailto:{_qu(to[1] or '', safe='@')}?subject={_qu(subject)}&body={_qu(body)}"

    def order_or_404(oid_):
        o = order(oid_)
        if not o:
            abort(404)
        return o

    def lab_numbers(o):
        try:
            return ", ".join(str(n_.get("number")) for n_ in json.loads(o["lab_orders"] or "[]") if n_.get("number"))
        except ValueError:
            return ""

    def back_to(o):
        c_ = client_of(o["session_id"])
        return url_for("client_stage", hid=c_["id"]) + f"?s={o['session_id']}#prints" if c_["id"] else url_for("home")

    @app.get("/prints/order/<int:oid_>", endpoint="prints_order")
    def prints_order(oid_):
        o = order_or_404(oid_)
        p_ = payload_of(o)
        ls = lines_of(oid_)
        files = photo_files(o)
        view = []
        cost_total, all_costed = 0, True
        for l_ in ls:
            it = item_of(l_["item_id"]) or {}
            rect, ppi, why = line_check(o, l_)
            soft = bool(ppi and ppi < int(it.get("ppi_mark") or P.PPI_MARK))
            cost = it.get("cost_cents")
            if cost is None:
                all_costed = False
            else:
                cost_total += cost * l_["qty"]
            view.append(dict(l_, cropped=l_["crop"] != [0.0, 0.0, 1.0, 1.0], soft=soft, cost=cost, problem=why,
                             problem_photo=why == "Photo changed since the order",
                             ppi_words=(f"{ppi} pixels per inch at this size" if ppi else "")))
        f_ = figures(o, ls)
        rules = ship_rules()
        rule = rules.get(f_["dest"], {})
        reg = ctx.tax_region()
        area, warn = tax_area_for(o)
        fl = json.loads(o["flags"] or "{}")
        who = who_of(o)
        sh = ship_of(o)
        rel = ""
        if (p_.get("rel") or {}).get("kind") == "change":
            prev = q("SELECT collected_at FROM print_orders WHERE oid=?", (p_["rel"].get("prev") or "",), one=True)
            rel = "A change to the order of " + (ctx.nice(prev["collected_at"][:10]) if prev else "an earlier day")
        lab = active_lab(o)
        lab_send = ""
        if o["state"] == "paid" and lab and not o["lab_id"]:
            lab_send = lab.get("name") or lab["id"]
        costs = [o[k_] for k_ in ("cost_products", "cost_shipping", "cost_tax", "cost_other")]
        profit = (f_["total"] - f_["tax"] - cost_total - (o["ship_lab_cents"] or 0)) if all_costed and ls and \
            o["ship_lab_cents"] is not None else None
        inv_mail = mailto(who, f"Invoice {o['invoice_number']}", invoice_mail_lines(o)) if o["invoice_number"] else ""
        return render_template_string(
            ORDER, o=o, p=p_, words=P.WORDS.get(o["state"], o["state"]), lines=view, flags=flags_of(o, ls), f=f_,
            pay_balance=issued_figures(o)["balance"] if o["invoice_number"] else f_["balance"],
            who=who, client=client_of(o["session_id"])["name"], back=back_to(o), ship=sh,
            ship_lines=P.label_lines(sh, who[0]), rel=rel, dollars=P.dollars, cents=cents_s, nice=ctx.nice,
            editable=o["state"] in ("new", "unmatched"), can_check=o["state"] not in P.FINAL,
            photos=[(f, it.get("n") or f) for f, it in (files or {}).items()],
            rule_words=RULE_LABEL.get(rule.get("rule"), "at cost").lower(), tax_warning=warn,
            areas=reg["areas"] if reg else None, area=area, profit=profit,
            cost_lines=cost_total if all_costed and ls else None,
            lab_cost_words=", ".join(f"{n_} {P.dollars(c_)}" for n_, c_ in zip(("products", "shipping", "the lab's tax", "other"), costs)
                                     if c_ is not None) or "none typed",
            lab_costs=[dict(r_, words=cost_words(r_), back=any(b_["reverses"] == r_["id"] for b_ in costs_of(oid_)))
                       for r_ in costs_of(oid_)],
            voided=fl.get("voided", []), today=date.today().isoformat(), flags_ok=bool(fl.get("flags_ok")), last_lab=setting("prints_last_lab"),
            pickup=sh.get("how") == "pickup", cancel_answers=CANCEL_ANSWERS, lab_numbers=lab_numbers(o),
            lab_label=("at " + (lab.get("name") or lab["id"])) if lab else "by hand", lab_send=lab_send,
            quote_lab=(lab.get("name") or lab["id"]) if lab and callable(lab.get("shipping_quote")) else "",
            lab_words=o["lab_words"], mail_invoice=inv_mail, local_time=local_time,
            incomplete=P.incomplete(sh) and o["state"] not in P.FINAL,
            mail_shipped=mailto(who, "Your prints are on their way" if sh.get("how") != "pickup" else "Your prints are ready",
                                ([f"Your prints{order_words(o)} are ready to collect from {studio_name()}."] if sh.get("how") == "pickup" else
                                 [f"Your prints{order_words(o)} are on their way to {', '.join(P.label_lines(sh, who[0]))}."]
                                 + [f"Tracking: {t_}" for t_ in tracking_words(o)]
                                 + [setting("prints_arrive", ARRIVE_DEFAULT)])),
            mail_expired=mailto(who, "Your print order", [
                f"Your print order{order_words(o, voided=True)} was not paid within {P.UNPAID_EXPIRE_DAYS} days, so its "
                "invoice was voided and nothing was printed. If you would still like the prints, just reply and I will "
                "set it up again."]))

    def order_words(o, voided=False):

        number, total = o["invoice_number"], None
        if o["invoice_data"]:
            total = json.loads(o["invoice_data"]).get("total")
        elif voided:
            v_ = (json.loads(o["flags"] or "{}").get("voided") or [{}])[-1]
            number = v_.get("number") or number
        n_ = sum(l_["qty"] for l_ in lines_of(o["id"]))
        bits = [f"invoice {number}" if number else "", P.n_prints(n_) if n_ else "",
                P.dollars(total) if total is not None else ""]
        bits = [b_ for b_ in bits if b_]
        return f" ({', '.join(bits)})" if bits else ""

    def invoice_mail_lines(o):

        f_ = issued_figures(o)
        lines = lines_of(o["id"])
        s_ = q("SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?", (o["session_id"],), one=True)
        data = json.loads(o["invoice_data"] or "{}")
        n_ = sum(l_["qty"] for l_ in lines)
        out = [f"Invoice {o['invoice_number']} for {P.n_prints(n_)} from {('the gallery of ' + s_['name']) if s_ else 'your gallery'}: "
               f"{P.dollars(f_['total'])}"
               + (f" (prints {P.dollars(data['sub'])}, shipping {P.dollars(data.get('ship_cents') or 0)}"
                  + (f", sales tax {P.dollars(data['tax_cents'])}" if data.get("tax_cents") else "") + ")"
                  if data.get("sub") is not None else "") + "."]
        if f_["paid"] > 0:
            out.append(f"Paid so far: {P.dollars(f_['paid'])}. Balance: {P.dollars(max(f_['balance'], 0))}.")
        if f_["balance"] > 0:
            pay = data.get("pay_words") or ""
            details = (data.get("pay_details") or "").rstrip(".")
            out.append((f"You can pay by {pay}." if pay else "Ask me how to pay.") + (f" {details}." if details else ""))
            out.append("The prints are made by our lab once it is paid in full.")
        else:
            out.append("It is paid in full, thank you: the prints go to our lab.")
        return out

    def tracking_words(o):
        out = []
        for n_ in json.loads(o["lab_orders"] or "[]"):
            t_ = " ".join(x_ for x_ in (n_.get("carrier"), n_.get("tracking")) if x_)
            if n_.get("url"):
                t_ = (t_ + " " + n_["url"]).strip()
            if t_:
                out.append(t_)
        return out

    app.config.setdefault("quiet_endpoints", set()).update({"prints_act", "prints_line", "prints_crop_save"})

    def money_in(v):
        c_ = P.cents(v)
        if c_ is not None and c_ > P.MONEY_MAX:
            raise ValueError("too much")
        return c_

    @app.post("/prints/order/<int:oid_>/<do>", endpoint="prints_act")
    def prints_act(oid_, do):
        o = order_or_404(oid_)
        f = request.form
        back = url_for("prints_order", oid_=oid_)
        st = o["state"]
        name = who_of(o)[0]
        try:
            if do in ("guest", "add_contact", "new_client"):
                if st != "unmatched":
                    return refuse("This order is already matched.", back)
                if do == "add_contact":
                    hid = client_of(o["session_id"])["id"]
                    pid = x("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?,?,?,?)",
                            (hid, o["bill_name"], "adult", o["bill_email"], (payload_of(o).get("who") or {}).get("phone", "")))
                    x("UPDATE print_orders SET contact_id=? WHERE id=?", (pid, oid_))
                    ctx.audit(f"{o['bill_name']} added to this client from a print order", sid=o["session_id"])
                elif do == "new_client":

                    fam = client_of(o["session_id"])
                    s_ = q("SELECT title, type, shoot_date FROM sessions WHERE id=?", (o["session_id"],), one=True)
                    what = ((s_["title"] or C.SESSION_TYPES.get(s_["type"], s_["type"] or "session"))
                            + (f", {ctx.nice(s_['shoot_date'])}" if s_["shoot_date"] else "")) if s_ else "a session"
                    sh = ship_of(o)
                    addr = "\n".join(P.label_lines(dict(sh, name=""))) if sh.get("how") == "me" and not sh.get("reduced") else ""
                    note = (f"Ordered prints from the gallery of {fam['name']} ({what}) on "
                            f"{ctx.nice((o['collected_at'] or '')[:10])}." if fam["name"] else "Ordered prints from a client's gallery.")
                    hid = x("INSERT INTO households (id, name, source, notes, address, referred_by) VALUES (?,?,?,?,?,?)",
                            (ctx.next_id("households"), o["bill_name"], "Print order", note, addr, fam["id"]))
                    pid = x("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?,?,?,?)",
                            (hid, o["bill_name"], "adult", o["bill_email"], (payload_of(o).get("who") or {}).get("phone", "")))
                    x("UPDATE print_orders SET contact_id=? WHERE id=?", (pid, oid_))
                    ctx.audit(f"New client from a print order from the gallery of {fam['name'] or 'a client'}", hid=hid)
                    if fam["id"]:
                        ctx.audit(f"{o['bill_name']} ordered prints from this gallery and is now a client of their own",
                                  sid=o["session_id"])
                else:
                    x("UPDATE print_orders SET guest=1 WHERE id=?", (oid_,))
                move(order(oid_), "new", "billed as a guest" if do == "guest" else "matched")
                return redirect(back)
            if do in ("not_real", "discard"):
                if st not in ("unmatched", "unreadable"):
                    return refuse("Only an order on the review list can be removed this way.", back)
                x("DELETE FROM print_orders WHERE id=?", (oid_,))
                ctx.audit("A print order was deleted as not a real order" if do == "not_real" else "An unreadable print order was removed",
                          sid=o["session_id"])
                return redirect(url_for("prints_review"))
            if do == "shipping":
                if st not in ("new", "unmatched"):
                    return refuse("The invoice is issued: change the order to change its shipping.", back)
                lab_c, ch = money_in(f.get("lab_ship")), money_in(f.get("ship"))
                reg = ctx.tax_region()
                area = f.get("area") if reg and f.get("area") in [k_ for k_, _ in reg["areas"]] else o["tax_area"]
                x("UPDATE print_orders SET ship_lab_cents=?, ship_cents=?, ship_by_hand=?, tax_area=? WHERE id=?",
                  (lab_c, ch, 1 if ch is not None else 0, area, oid_))
                ctx.audit(f"Print order from {name}: shipping " + (f"charged {P.dollars(ch)}" if ch is not None else "by the rule")
                          + (f", the lab's {P.dollars(lab_c)}" if lab_c is not None else ""), sid=o["session_id"])
            elif do == "flags_ok":
                fl = json.loads(o["flags"] or "{}")
                fl["flags_ok"] = bool(f.get("checked"))
                x("UPDATE print_orders SET flags=? WHERE id=?", (json.dumps(fl), oid_))
                if fl["flags_ok"]:
                    ctx.audit(f"Print order from {name}: its review flags were checked", sid=o["session_id"])
            elif do == "quote":
                lab = active_lab(o)
                res = call_lab(lab, "shipping_quote", snapshot(oid_), default=None, oid_=oid_) if lab else None
                if not res or not isinstance(res.get("cents"), int):
                    return refuse("The lab gave no shipping quote; type it.", back)
                x("UPDATE print_orders SET ship_lab_cents=? WHERE id=?", (res["cents"], oid_))
                ctx.audit(f"Print order from {name}: the lab's shipping {P.dollars(res['cents'])} from {lab.get('name') or lab['id']}",
                          sid=o["session_id"])
            elif do == "invoice":
                if st != "new":
                    return refuse("Only a new order is invoiced.", back)
                n_ = issue_invoice(o)
                session["notice"] = f"Invoice {n_} issued."
            elif do == "reissue":
                void_invoice(o, "changed before payment")
                move(order(oid_), "new", "to be changed and invoiced again")
            elif do == "payment":
                if app.view_functions and ctx.app.jinja_env.globals.get("form_once") and _sent_twice():
                    return refuse("That was sent twice. It was saved once.", back)
                if not o["invoice_number"]:
                    return refuse("Make the invoice first.", back)
                amt = money_in(f.get("amount"))
                if not amt or amt <= 0:
                    return refuse("Enter the amount as a positive number. Nothing was recorded.", back)
                day = (f.get("received") or date.today().isoformat())[:10]
                try:
                    if not "2000-01-01" <= date.fromisoformat(day).isoformat() <= (date.today() + timedelta(days=1)).isoformat():
                        raise ValueError
                except ValueError:
                    return refuse("That date isn't usable (from 2000 to today). Nothing was recorded.", back)
                kind = "refund" if f.get("kind") == "refund" else "payment"
                f_ = figures(o)
                if kind == "refund" and amt > f_["paid"]:
                    return refuse(f"A refund can't be more than what was paid ({P.dollars(f_['paid'])}). Nothing was recorded.", back)
                x("INSERT INTO print_payments (order_id, kind, amount_cents, received, method, ref) VALUES (?,?,?,?,?,?)",
                  (oid_, kind, amt, day, P.clean(f.get("method"))[:40], P.clean(f.get("ref"))[:40]))
                ctx.audit(f"Print order from {name}: {'refund' if kind == 'refund' else 'payment'} of {P.dollars(amt)} recorded",
                          sid=o["session_id"])
                follow_payment(o)
                over = -issued_figures(order(oid_))["balance"]
                if kind == "payment" and over > 0:
                    session["notice"] = (f"Recorded. That's {P.dollars(over)} more than the balance, so the order now "
                                         f"shows a {P.dollars(over)} credit to give back. Once you give it back, record "
                                         f"it as a Refund.")
            elif do == "void_payment":
                x("UPDATE print_payments SET voided=? WHERE id=? AND order_id=? AND voided IS NULL",
                  (date.today().isoformat(), int(f.get("pid") or 0), oid_))
                ctx.audit(f"Print order from {name}: a payment was voided", sid=o["session_id"])
                follow_payment(o)
            elif do == "before_payment":
                if st != "invoiced":
                    return refuse("Only an invoiced order can be ordered before payment.", back)
                x("UPDATE print_orders SET before_payment=1 WHERE id=?", (oid_,))
                ctx.audit(f"Print order from {name}: Order before payment turned on; the photographer carries the risk",
                          sid=o["session_id"])
            elif do == "ordered":
                lab_name, num_ = P.clean(f.get("lab_name"))[:60], P.clean(f.get("lab_number"))[:60]
                if not lab_name or not num_:
                    return refuse("Type the lab's name and its order number.", back)
                if P.incomplete(ship_of(o)):
                    return refuse("Shipping address incomplete: enter the full address first.", back)
                if st in ("paid", "prepared") and not o["before_payment"] and not paid_in_full(oid_):
                    return refuse("This order is not paid in full. Record the payment first, or turn on Order before "
                                  "payment.", back)
                if st == "invoiced" and o["before_payment"]:
                    x("UPDATE print_orders SET state='paid', prev_state='invoiced' WHERE id=?", (oid_,))
                    o = order(oid_)
                    st = "paid"
                costs = {k_: money_in(f.get(k_)) for k_ in ("cost_products", "cost_shipping", "cost_tax", "cost_other")}
                nums = json.loads(o["lab_orders"] or "[]")
                nums.append(dict(number=num_, status="ordered", at=date.today().isoformat(), remake=st == "remake"))
                x("UPDATE print_orders SET lab_name=?, lab_orders=? WHERE id=?", (lab_name, json.dumps(nums), oid_))
                as_ = {"products": costs["cost_products"], "shipping": costs["cost_shipping"], "tax": costs["cost_tax"],
                       "other": costs["cost_other"]}
                if st == "remake":
                    record_cost(oid_, "remake", as_, lab_name, num_)
                else:
                    x("""UPDATE print_orders SET cost_products=COALESCE(?, cost_products), cost_shipping=COALESCE(?, cost_shipping),
                         cost_tax=COALESCE(?, cost_tax), cost_other=COALESCE(?, cost_other) WHERE id=?""",
                      (costs["cost_products"], costs["cost_shipping"], costs["cost_tax"], costs["cost_other"], oid_))
                    if any(v_ is not None for v_ in as_.values()):
                        record_cost(oid_, "order", as_, lab_name, num_)
                put("prints_last_lab", lab_name)
                move(order(oid_), "ordered", f"at {lab_name}, no. {num_}")
                f_ = figures(order(oid_))
                if costs["cost_shipping"] is not None and f_["ship"] is not None and costs["cost_shipping"] != f_["ship"] \
                        and ship_rules().get(f_["dest"], {}).get("rule") == "cost":
                    session["notice"] = (f"The lab's shipping was {P.dollars(costs['cost_shipping'])}; the invoice charged "
                                         f"{P.dollars(f_['ship'])}. The issued invoice stays as issued.")
            elif do == "shipped":
                nums = json.loads(o["lab_orders"] or "[]")
                if nums:
                    nums[-1].update(carrier=P.clean(f.get("carrier"))[:40], tracking=P.clean(f.get("tracking"))[:60],
                                    url=(f.get("url") or "").strip()[:300] if (f.get("url") or "").strip().startswith("https://") else "",
                                    status="shipped")
                x("UPDATE print_orders SET lab_orders=? WHERE id=?", (json.dumps(nums), oid_))
                move(o, "shipped", "arrived at the studio" if ship_of(o).get("how") == "pickup" else "")
            elif do == "delivered":
                move(o, "delivered", "picked up" if ship_of(o).get("how") == "pickup" else "")
            elif do == "close":
                move(o, "closed")
            elif do == "ship_to":
                sh = ship_of(o)
                if not P.incomplete(sh) or st in P.FINAL:
                    return refuse("This order's ship-to is complete.", back)
                try:
                    full = P.check_ship({k_: P.clean(f.get(k_)) for k_ in ("name", "addr1", "addr2", "city", "zip", "phone")}
                                        | {"how": sh.get("how"), "state": (f.get("state") or "").strip().upper(),
                                           "country": (f.get("country") or "US").strip().upper()},
                                        (payload_of(o).get("who") or {}).get("phone", ""))
                except P.Refused as e_:
                    return refuse(f"Check the address ({e_}). Nothing was saved.", back)
                data = payload_of(o)
                data["ship"] = full
                x("UPDATE print_orders SET payload=?, reduced_at=NULL WHERE id=?", (json.dumps(data), oid_))
                ctx.audit(f"Print order from {name}: the full ship-to address was entered again", sid=o["session_id"])
            elif do == "lab_refund":
                rid = int(f.get("cost") or 0)
                row_ = next((r_ for r_ in costs_of(oid_) if r_["id"] == rid and r_["kind"] != "refund"), None)
                if not row_:
                    return refuse("No such lab cost on this order.", back)
                day = (f.get("day") or date.today().isoformat())[:10]
                try:
                    if not "2000-01-01" <= date.fromisoformat(day).isoformat() <= (date.today() + timedelta(days=1)).isoformat():
                        raise ValueError
                except ValueError:
                    return refuse("That date isn't usable (from 2000 to today). Nothing was recorded.", back)
                if any(r_["reverses"] == rid for r_ in costs_of(oid_)):
                    return refuse("That lab cost was already taken back.", back)
                record_cost(oid_, "refund", {k_: -row_[k_] for k_ in COST_KEYS}, row_["lab_name"], row_["lab_number"],
                            day=day, reverses=rid)
                ctx.audit(f"Print order from {name}: the lab canceled or refunded lab order {row_['lab_number'] or ''}"
                          .rstrip() + f" ({P.dollars(sum(row_[k_] for k_ in COST_KEYS))} back)", sid=o["session_id"])
            elif do == "remake":
                x("UPDATE print_orders SET remake_note=? WHERE id=?", (P.clean(f.get("note"))[:300], oid_))
                move(o, "remake", P.clean(f.get("note"))[:120])
            elif do == "reopen":
                if st != "expired":
                    return refuse("Only an expired order is reopened.", back)
                if photo_files(o) is None:
                    return refuse("The gallery's files are gone, so this order can't be reopened.", back)
                move(o, "new", "reopened: it needs a new invoice")
                if P.incomplete(ship_of(o)):
                    session["notice"] = ("Reopened. Its shipping address is incomplete (only the city, state and ZIP were "
                                         "kept): enter the full address before Prepare for the lab.")
            elif do == "files_back":
                prev = o["prev_state"] or "new"
                if photo_files(o) is None:
                    return refuse("The gallery's files are still not here.", back)
                move(o, prev if P.may_move("needs_files", prev) else "new", "the files are here again")
            elif do == "cancel":
                ans = dict(CANCEL_ANSWERS).get(f.get("lab", ""))
                if st == "ordered" and not ans:
                    return refuse(f"Say whether it was canceled at {o['lab_name'] or 'the lab'} too.", back)
                if st == "with_lab":
                    out = lab_cancel(o, confirm=True)
                    ans = f"the lab plugin answered: {out.get('words') or out.get('outcome')}"
                x("UPDATE print_orders SET cancel_lab=? WHERE id=?", (ans or "", oid_))
                if f.get("lab", "") == "canceled" or (st == "with_lab" and out.get("outcome") in ("canceled", "not_placed")):
                    reverse_costs(o, latest_number(o), "the lab order was canceled")
                if o["invoice_number"] and not paid_in_full(oid_) and figures(o)["paid"] <= 0:
                    void_invoice(o, "order canceled")
                move(order(oid_), "canceled", ans or "")
            elif do == "hand_off":
                lab = active_lab(o)
                if not lab:
                    return refuse("No lab plugin is chosen.", back)
                ok, words = hand_off(oid_, lab["id"])
                session["notice"] = words
                if not ok:
                    g.refused = True
            elif do == "check_lab":
                lab = lab_by_id(o["lab_id"])
                res = call_lab(lab, "status", snapshot(oid_), default=None, oid_=oid_) if lab else None
                session["notice"] = (res or {}).get("words") or "The lab had nothing new."
            elif do == "take_back":
                out = lab_cancel(o, confirm=True)
                if out.get("outcome") in ("canceled", "not_placed"):
                    reverse_costs(o, latest_number(o), "the lab order was canceled")
                    x("UPDATE print_orders SET lab_id=NULL, lab_words='' WHERE id=?", (oid_,))
                    move(order(oid_), "paid", f"taken back from the lab: {out.get('words') or out.get('outcome')}")
                else:
                    return refuse(f"The lab did not give it back: {out.get('words') or out.get('outcome') or 'no answer'}.", back)
            else:
                abort(404)
        except LabError as e:
            g.db.rollback()
            return refuse(str(e), back)
        except ValueError:
            g.db.rollback()
            return refuse("Check the amounts (like 24 or 24.50). Nothing was saved.", back)
        return redirect(back)

    def _sent_twice():
        import importlib
        A_ = sys.modules.get("app") or importlib.import_module("app")
        return A_.sent_twice()

    @app.post("/prints/order/<int:oid_>/line/<int:lid>", endpoint="prints_line")
    def prints_line(oid_, lid):
        o = order_or_404(oid_)
        l_ = next((l_ for l_ in lines_of(oid_) if l_["id"] == lid), None) or abort(404)
        f = request.form
        do = f.get("do")
        back = url_for("prints_order", oid_=oid_)
        nm = who_of(o)[0]
        if do in ("save", "remove", "photo") and (o["state"] not in ("new", "unmatched", "needs_files")
                                                  or (o["state"] == "needs_files" and o["invoice_number"])):
            if not (do == "photo" and o["state"] not in P.FINAL):
                return refuse("The invoice is issued: change the order to change its lines.", back)
        if do == "save":
            try:
                qty, each = int(f.get("qty") or 0), money_in(f.get("each"))
            except ValueError:
                return refuse("Check the quantity and the price.", back)
            if not 1 <= qty <= P.QTY_MAX or each is None:
                return refuse(f"A quantity is 1 to {P.QTY_MAX}, and a line needs a price.", back)
            x("UPDATE print_order_lines SET qty=?, each_cents=? WHERE id=?", (qty, each, lid))
            ctx.audit(f"Print order from {nm}: line {l_['n']} changed from {l_['qty']} at {P.dollars(l_['each_cents'])} "
                      f"to {qty} at {P.dollars(each)}", sid=o["session_id"])
        elif do == "remove":
            if len(lines_of(oid_)) <= 1:
                return refuse("An order keeps at least one line; cancel the order instead.", back)
            x("DELETE FROM print_order_lines WHERE id=?", (lid,))
            ctx.audit(f"Print order from {nm}: line {l_['n']} removed ({l_['label']}, {l_['photo_name']})", sid=o["session_id"])
        elif do == "photo":
            files = photo_files(o) or {}
            new = f.get("photo")
            if new not in files:
                return refuse("Pick a photo of this gallery.", back)
            x("UPDATE print_order_lines SET photo_file=?, photo_name=?, crop_confirmed=0 WHERE id=?",
              (new, files[new].get("n") or new, lid))
            ctx.audit(f"Print order from {nm}: line {l_['n']} now prints {files[new].get('n') or new} (the photo changed since the order)",
                      sid=o["session_id"])
        elif do in ("confirm", "unconfirm"):
            x("UPDATE print_order_lines SET crop_confirmed=? WHERE id=?", (1 if do == "confirm" else 0, lid))
            if do == "confirm":
                ctx.audit(f"Print order from {nm}: line {l_['n']}'s crop looks right", sid=o["session_id"])
        elif do in ("soft", "unsoft"):
            x("UPDATE print_order_lines SET soft_confirmed=? WHERE id=?", (1 if do == "soft" else 0, lid))
            if do == "soft":
                ctx.audit(f"Print order from {nm}: line {l_['n']} prints although it may look soft", sid=o["session_id"])
        else:
            abort(400)
        return redirect(back)

    def preview_jpeg(o, l_, edge=900, whole=False):
        from PIL import Image
        src, (ow, oh) = full_size(o, l_)
        with Image.open(io.BytesIO(src)) as im:
            im = im.convert("RGB")
            if not whole:
                rect, _p, _w = line_check(o, l_)
                if rect:
                    x0, y0, W, H = rect
                    im = im.crop((x0, y0, x0 + W, y0 + H))
            im.thumbnail((edge, edge))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=82)
            return b.getvalue()

    def jpeg_response(data):
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    PLACEHOLDER = (b'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="160"><rect width="220" height="160" fill="#2a2622"/>'
                   b'<text x="110" y="85" fill="#a99c86" font-size="13" text-anchor="middle" font-family="sans-serif">'
                   b'The photo file is not here</text></svg>')

    @app.get("/prints/order/<int:oid_>/line/<int:lid>.jpg", endpoint="prints_line_image")
    def prints_line_image(oid_, lid):
        o = order_or_404(oid_)
        l_ = next((l_ for l_ in lines_of(oid_) if l_["id"] == lid), None) or abort(404)
        try:
            return jpeg_response(preview_jpeg(o, l_))
        except Exception:
            return Response(PLACEHOLDER, mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.get("/prints/order/<int:oid_>/line/<int:lid>/photo.jpg", endpoint="prints_line_photo")
    def prints_line_photo(oid_, lid):
        o = order_or_404(oid_)
        l_ = next((l_ for l_ in lines_of(oid_) if l_["id"] == lid), None) or abort(404)
        try:
            return jpeg_response(preview_jpeg(o, l_, edge=1400, whole=True))
        except Exception:
            abort(404)

    CROP = """{% extends "base.html" %}{% block title %}Change crop - Lumen Aureum{% endblock %}{% block body %}
<p><a class="go" href="{{ url_for('prints_order', oid_=o.id) }}">&lsaquo; The order</a></p>
<h1>Change crop: line {{ l.n }}, {{ l.label }}</h1>
<p class="mute">Drag the frame to move it; the slider makes it smaller. The frame always keeps the print's shape.</p>
<div id="cropbox" style="position:relative;display:inline-block;max-width:100%;overflow:hidden;touch-action:none" data-crop="{{ crop }}" data-aspect="{{ aspect }}" data-aspect-turned="{{ aspect_t }}" data-turn="{{ l.turn }}" data-ow="{{ ow }}" data-oh="{{ oh }}">
  <img id="cropimg" src="{{ url_for('prints_line_photo', oid_=o.id, lid=l.id) }}" alt="The whole photo" style="display:block;max-width:min(900px,100%);height:auto">
  <div id="cropframe" style="position:absolute;border:2px solid var(--gold);box-shadow:0 0 0 9999px rgba(0,0,0,.5);cursor:move"></div></div>
<form method="post" action="{{ url_for('prints_crop_save', oid_=o.id, lid=l.id) }}" class="row" style="gap:10px;flex-wrap:wrap;margin-top:10px;align-items:center">
  <label class="row" style="gap:6px">Size <input type="range" id="cropsize" min="10" max="100" value="100" aria-label="Frame size"></label>
  <button type="button" class="quiet" id="cropturn">Turn the frame</button>
  <input type="hidden" name="x" id="cx"><input type="hidden" name="y" id="cy"><input type="hidden" name="w" id="cw"><input type="hidden" name="h" id="ch"><input type="hidden" name="turn" id="ct" value="{{ l.turn }}">
  <span id="cropppi" class="sub"></span><button class="primary">Save the crop</button></form>
<script nonce="{{ csp_nonce() }}">
(function () {
  var box = document.getElementById("cropbox"), img = document.getElementById("cropimg"), fr = document.getElementById("cropframe");
  var ow = +box.dataset.ow, oh = +box.dataset.oh, turn = +box.dataset.turn, c = JSON.parse(box.dataset.crop);
  var A = [+box.dataset.aspect, +box.dataset.aspectTurned], size = document.getElementById("cropsize");
  function asp() { return A[turn]; }           // width / height of the frame, in photo pixels
  function fit() {                              // the largest frame of this shape, in fractions
    var a = asp() * oh / ow;                     // the frame's width / height in fraction units
    return a > 1 ? [1, 1 / a] : [a, 1];
  }
  function clamp() { c[0] = Math.max(0, Math.min(1 - c[2], c[0])); c[1] = Math.max(0, Math.min(1 - c[3], c[1])); }
  function draw() {
    var W = img.clientWidth, H = img.clientHeight;
    fr.style.left = c[0] * W + "px"; fr.style.top = c[1] * H + "px"; fr.style.width = c[2] * W + "px"; fr.style.height = c[3] * H + "px";
    ["cx", "cy", "cw", "ch"].forEach(function (id, i) { document.getElementById(id).value = c[i].toFixed(4); });
    document.getElementById("ct").value = turn;
  }
  function resize(pct) {
    var f = fit(), cxm = c[0] + c[2] / 2, cym = c[1] + c[3] / 2;
    c[2] = f[0] * pct / 100; c[3] = f[1] * pct / 100; c[0] = cxm - c[2] / 2; c[1] = cym - c[3] / 2; clamp(); draw();
  }
  size.oninput = function () { resize(+size.value); };
  document.getElementById("cropturn").onclick = function () { turn = 1 - turn; size.value = 100; c = [0, 0, 1, 1]; resize(100); };
  var drag = null;
  fr.addEventListener("pointerdown", function (e) { drag = [e.clientX, e.clientY, c[0], c[1]]; fr.setPointerCapture(e.pointerId); });
  fr.addEventListener("pointermove", function (e) { if (!drag) return;
    c[0] = drag[2] + (e.clientX - drag[0]) / img.clientWidth; c[1] = drag[3] + (e.clientY - drag[1]) / img.clientHeight; clamp(); draw(); });
  fr.addEventListener("pointerup", function () { drag = null; });
  function start() { var f = fit(); size.value = Math.round(100 * Math.max(c[2] / f[0], 0.1)); draw(); }
  if (img.complete) start(); else img.onload = start;
  window.addEventListener("resize", draw);
})();
</script>
{% endblock %}"""

    @app.get("/prints/order/<int:oid_>/line/<int:lid>/crop", endpoint="prints_crop")
    def prints_crop(oid_, lid):
        o = order_or_404(oid_)
        l_ = next((l_ for l_ in lines_of(oid_) if l_["id"] == lid), None) or abort(404)
        files = photo_files(o) or {}
        it = files.get(l_["photo_file"]) or abort(404)
        ow, oh = int(it.get("ow") or 0), int(it.get("oh") or 0)
        w, h = l_["w"], l_["h"]
        land = P.frame_landscape(ow, oh, 0)
        return render_template_string(CROP, o=o, l=l_, crop=json.dumps(l_["crop"]), ow=ow, oh=oh,
                                      aspect=P.frame_aspect(w, h, land), aspect_t=P.frame_aspect(w, h, not land))

    @app.post("/prints/order/<int:oid_>/line/<int:lid>/crop", endpoint="prints_crop_save")
    def prints_crop_save(oid_, lid):
        o = order_or_404(oid_)
        l_ = next((l_ for l_ in lines_of(oid_) if l_["id"] == lid), None) or abort(404)
        back = url_for("prints_order", oid_=oid_)
        if o["state"] in P.FINAL or o["state"] in ("ordered", "shipped", "delivered", "with_lab"):
            return refuse("This order is past the point where its crop can change.", back)
        files = photo_files(o) or {}
        it = files.get(l_["photo_file"])
        try:
            crop = P.check_crop([round(float(request.form.get(k_)), 4) for k_ in ("x", "y", "w", "h")])
            turn = 1 if request.form.get("turn") == "1" else 0
        except (TypeError, ValueError, P.Refused):
            return refuse("That crop could not be read.", back)
        if not it or not P.shape_ok(crop, int(it["ow"]), int(it["oh"]), l_["w"], l_["h"], turn):
            return refuse("That crop does not have the print's shape.", back)
        x("UPDATE print_order_lines SET crop=?, turn=?, crop_confirmed=1, soft_confirmed=0, ppi=? WHERE id=?",
          (json.dumps(crop), turn, P.effective_ppi(crop, int(it["ow"]), int(it["oh"]), l_["w"], l_["h"], turn), lid))
        ctx.audit(f"Print order from {who_of(o)[0]}: line {l_['n']}'s crop changed by the photographer", sid=o["session_id"])
        return redirect(back)

    PREPARE = """{% extends "base.html" %}{% block title %}Prepare for the lab - Lumen Aureum{% endblock %}{% block body %}
<p><a class="go" href="{{ url_for('prints_order', oid_=o.id) }}">&lsaquo; The order</a></p>
<h1>Prepare for the lab</h1>
{% if lab_note %}<p style="color:var(--warn)">{{ lab_note }}</p>{% endif %}
{% if problems %}<div class="card f" style="max-width:760px;border-left:3px solid var(--crit)"><p style="margin:0">Before the files can be made:</p><ul>{% for p in problems %}<li>{{ p }}</li>{% endfor %}</ul></div>
{% else %}<div class="card f" style="max-width:760px">
<p style="margin:0">One zip, {{ zip_name }}: a print-ready file for each line, cut to the exact shape, and an order sheet (order-sheet.pdf to read or print, order.txt to copy from). It comes straight to this browser as a download: nothing readable is saved on this computer's data folders or put on your web host. What you save on your own computer is yours to look after.</p>
<p><a class="go" href="{{ url_for('prints_prepare_zip', oid_=o.id) }}{{ '?confirm=1' if confirm else '' }}" download>Download {{ zip_name }} &rsaquo;</a></p>
<p class="sub">Then place the order at your lab and record it on the order with Ordered at the lab.</p></div>{% endif %}
{% endblock %}"""

    def may_prepare(o):
        return o["state"] in ("paid", "prepared", "ordered") or (o["state"] == "invoiced" and o["before_payment"]) or \
            o["state"] == "with_lab"

    @app.get("/prints/order/<int:oid_>/prepare", endpoint="prints_prepare")
    def prints_prepare(oid_):
        o = order_or_404(oid_)
        if not may_prepare(o):
            return refuse("Prepare for the lab opens once the invoice is paid in full.", url_for("prints_order", oid_=oid_))
        problems = ready_checks(o)
        if not o["invoice_number"]:
            problems.insert(0, "The order has no invoice.")
        if photo_files(o) is None and o["state"] not in ("needs_files",) and o["state"] in ("paid", "invoiced"):
            move(o, "needs_files", "the gallery's files are not here")
        lab_note = ""
        if o["state"] == "with_lab":
            lab_note = (f"This order is with {o['lab_name'] or o['lab_id']}. Downloading the files asks the lab plugin to cancel "
                        "it there first; confirm the lab order was canceled or never placed.")
        return render_template_string(PREPARE, o=o, problems=problems, zip_name=f"{o['invoice_number']}-prints.zip",
                                      lab_note=lab_note, confirm=bool(lab_note))

    @app.get("/prints/order/<int:oid_>/prepare.zip", endpoint="prints_prepare_zip")
    def prints_prepare_zip(oid_):
        o = order_or_404(oid_)
        back = url_for("prints_order", oid_=oid_)
        if not may_prepare(o) or not o["invoice_number"]:
            return refuse("Prepare for the lab opens once the invoice is paid in full.", back)
        if ready_checks(o):
            return redirect(url_for("prints_prepare", oid_=oid_))
        if o["state"] == "with_lab":
            if request.args.get("confirm") != "1":
                return redirect(url_for("prints_prepare", oid_=oid_))
            out = lab_cancel(o, confirm=True)
            ctx.audit(f"Print order from {who_of(o)[0]}: taken back from the lab to prepare by hand "
                      f"({out.get('words') or out.get('outcome') or 'no answer'})", sid=o["session_id"])
            x("UPDATE print_orders SET lab_id=NULL, lab_words='' WHERE id=?", (oid_,))
            move(order(oid_), "paid", "taken back to prepare by hand")
            o = order(oid_)
        lines = lines_of(oid_)
        p_ = payload_of(o)
        who = who_of(o)
        sheet = dict(invoice=o["invoice_number"], date=ctx.long_date(date.today().isoformat()), ship=ship_of(o),
                     orderer=who[0], note=p_.get("note", ""), lines=[])

        gen = print_files(oid_)
        sheet["lines"] = []
        for l_ in lines:
            it = item_of(l_["item_id"]) or {}
            _r, ppi, _w = line_check(o, l_)
            sheet["lines"].append(dict(file=P.file_name(o["invoice_number"], l_["n"], l_["w"], l_["h"], l_["finish"], l_["qty"]),
                                       name=l_["photo_name"], label=l_["label"], w=l_["w"], h=l_["h"], finish=l_["finish"],
                                       qty=l_["qty"], cropped=l_["crop"] != [0.0, 0.0, 1.0, 1.0],
                                       soft=ppi < int(it.get("ppi_mark") or P.PPI_MARK)))
        text = P.sheet_text(sheet)
        pdf = sheet_pdf(text)

        def files():
            for _fid, name, _ns, data in gen:
                yield name, data
            yield "order.txt", text.encode()
            yield "order-sheet.pdf", pdf
        sid = o["session_id"]
        state_was = o["state"]

        def body():
            for piece in P.stream_zip(files()):
                yield piece
            con = ctx.connect()
            try:
                now_ = datetime.now().isoformat(timespec="seconds")
                if state_was in ("paid", "invoiced"):
                    con.execute("""UPDATE print_orders SET state='prepared', prev_state=?, prepared_at=? WHERE id=?
                                   AND state=?""", (state_was, now_, oid_, state_was))
                else:
                    con.execute("UPDATE print_orders SET prepared_at=? WHERE id=?", (now_, oid_))
                con.execute("INSERT INTO audit (at, session_id, action, who) VALUES (?,?,?,?)",
                            (now_, sid, f"Print order from {who[0]}: files prepared for the lab"
                             + (" (downloaded again)" if state_was not in ("paid", "invoiced") else ""), "Owner"))
                con.commit()
            finally:
                con.close()
        r = Response(stream_with_context(body()), mimetype="application/zip")
        r.headers["Content-Disposition"] = f'attachment; filename="{o["invoice_number"]}-prints.zip"'
        r.headers["Cache-Control"] = "no-store"
        return r

    app.config.setdefault("writing_gets", set()).add("prints_prepare_zip")

    def sheet_pdf(text):

        try:
            import weasyprint
            html = ("<!doctype html><meta charset='utf-8'><style>body{font:12px/1.45 sans-serif}pre{white-space:pre-wrap;"
                    "font:12px/1.45 sans-serif}</style><pre>" + str(escape(text)) + "</pre>")
            return weasyprint.HTML(string=html).write_pdf()
        except Exception:
            app.logger.exception("prints: the order sheet PDF")
            return b""

    @app.get("/prints/order/<int:oid_>/invoice", endpoint="prints_invoice")
    def prints_invoice(oid_):
        o = order_or_404(oid_)
        if not o["invoice_html"]:
            abort(404)
        f_ = issued_figures(o)
        since = "" if f_["paid"] <= 0 else ("This is the invoice as issued. It has since been paid in full." if f_["balance"] <= 0 else
                                            f"This is the invoice as issued. Payments since leave {P.dollars(f_['balance'])} to pay.")
        page = o["invoice_html"]
        if since:
            page = page.replace("<body>", f"<body><p style=\"background:#f3ead8;padding:6px 10px\">{escape(since)}</p>", 1)
        return Response(page, mimetype="text/html")

    def lab_by_id(lid):
        return next((l_ for l_ in ctx.hooks.labs if l_.get("id") == lid), None) if lid else None

    def active_lab(o=None):
        if o and o.get("lab_id"):
            return lab_by_id(o["lab_id"])
        lid = setting("prints_lab") or "hand"
        return lab_by_id(lid) if lid != "hand" else None

    def note_problem(oid_, lab):
        if oid_:
            try:
                x("UPDATE print_orders SET lab_problem=? WHERE id=?", ((lab or {}).get("name") or (lab or {}).get("id") or "The lab", oid_))
            except Exception:
                app.logger.exception("prints: noting a lab problem")

    def call_lab(lab, fn, *args, default=None, oid_=None, **kw):

        f_ = (lab or {}).get(fn)
        if not callable(f_):
            return default
        out, err = [], []

        def run():
            try:
                with app.app_context():
                    out.append(f_(*args, **kw))
            except BaseException as e:
                err.append(e)
        t_ = threading.Thread(target=run, name=f"lab-{lab.get('id')}-{fn}", daemon=True)
        t_.start()
        t_.join(LAB_TIMEOUT[0])
        if t_.is_alive() or err:
            app.logger.error("prints: the lab plugin %s failed in %s: %s", lab.get("id"), fn,
                             "timed out" if t_.is_alive() else repr(err[0])[:200])
            note_problem(oid_, lab)
            return default
        return out[0] if out else default

    LAB_TIMEOUT = [PLUGIN_TIMEOUT]
    app.config["prints_lab_timeout"] = LAB_TIMEOUT

    def snapshot(oid_):

        o = order(oid_)
        if not o:
            return None
        lab = lab_by_id(o["lab_id"]) or active_lab(o)
        lid = (lab or {}).get("id")
        sh = ship_of(o)
        its = {i_["id"]: i_ for i_ in items()}
        from types import MappingProxyType
        return MappingProxyType(dict(
            id=o["id"], oid=o["oid"], invoice=o["invoice_number"], state=o["state"], paid_in_full=paid_in_full(oid_),
            lines=tuple(MappingProxyType(dict(n=l_["n"], item=l_["item_id"], lab=(its.get(l_["item_id"], {}).get("lab") or {}).get(lid),
                                              qty=l_["qty"], label=l_["label"], w=l_["w"], h=l_["h"])) for l_ in lines_of(oid_)),
            ship=MappingProxyType({k_: sh.get(k_, "") for k_ in ("how", "name", "addr1", "addr2", "city", "state", "zip",
                                                                  "country", "phone")})))

    def open_flags(o):
        return [k_ for k_, _w, _l in flags_of(o)]

    def hand_off(oid_, lab_id, automatic=False):

        o = order(oid_)
        lab = lab_by_id(lab_id)
        if not active():
            return False, "Print orders are off."
        if not o or not lab:
            return False, "No such order or lab."
        ok_state = o["state"] == "paid" or (o["state"] == "invoiced" and o["before_payment"] and not automatic)
        if not ok_state or not (paid_in_full(oid_) or (o["before_payment"] and not automatic)):
            return False, "Only an order paid in full goes to the lab."
        bad = ready_checks(o)
        if bad:
            return False, " ".join(bad)
        fl = open_flags(o)
        if fl and not json.loads(o["flags"] or "{}").get("flags_ok"):
            return False, "Review the order's flags first."
        can = call_lab(lab, "can_send", snapshot(oid_), default=(True, []), oid_=oid_)
        if not can or not can[0]:
            return False, "; ".join(can[1] if can else ["The lab did not answer."]) or "The lab can't take this order."
        res = call_lab(lab, "send", snapshot(oid_), default=None, oid_=oid_)
        if not res or not res.get("accepted"):
            return False, (res or {}).get("words") or f"{lab.get('name') or lab_id} did not take the order."
        o = order(oid_)
        if o["state"] == "invoiced":
            x("UPDATE print_orders SET state='paid', prev_state='invoiced' WHERE id=?", (oid_,))
            o = order(oid_)
        x("UPDATE print_orders SET lab_id=?, lab_words=?, lab_name=?, lab_problem='' WHERE id=?",
          (lab_id, (res.get("words") or "")[:200], lab.get("name") or lab_id, oid_))
        move(order(oid_), "with_lab", (res.get("words") or "")[:120], by="lab_handoff" if automatic else "hand")
        return True, res.get("words") or f"Handed to {lab.get('name') or lab_id}."

    def lab_cancel(o, confirm=True):
        lab = lab_by_id(o["lab_id"])
        if not lab:
            return dict(outcome="not_placed", words="no lab plugin holds it")
        res = call_lab(lab, "cancel", snapshot(o["id"]), confirm=confirm, default=None, oid_=o["id"])
        out = res or dict(outcome="kept", words="the lab did not answer")
        ctx.audit(f"Print order from {who_of(o)[0]}: cancel at {lab.get('name') or lab['id']}: {out.get('outcome')}"
                  + (f" ({out.get('words')})" if out.get("words") else ""), sid=o["session_id"])
        return out

    REPORT = {"placed": "ordered", "shipped": "shipped", "delivered": "delivered", "given_back": "paid", "canceled": "paid"}

    def report(oid_, lab_id, what, numbers=None, carrier="", tracking="", url="", outcome="", cost=None, words=""):

        if not active():
            raise LabError("print orders are off: nothing changes until they are on again")
        o = order(oid_)
        if not o or o["lab_id"] != lab_id and not (what == "placed" and o["state"] == "remake"):
            raise LabError("this lab does not hold that order")
        new = REPORT.get(what)
        if not new:
            raise LabError(f"unknown report: {what}")
        if what == "canceled" and o["state"] != "with_lab":
            raise LabError("a lab can report canceled only for an order it still holds")
        if not P.may_move(o["state"], new, "lab"):
            raise LabError(f"an order that is {P.WORDS.get(o['state'])} cannot become {P.WORDS.get(new)}")
        nums = json.loads(o["lab_orders"] or "[]")
        if what == "placed":
            for n_ in (numbers or []):
                nums.append(dict(number=str(n_)[:60], status="ordered", at=date.today().isoformat()))
        if what == "shipped":
            for n_ in nums:
                n_.update(carrier=str(carrier)[:40], tracking=str(tracking)[:60],
                          url=url if str(url).startswith("https://") else "", status="shipped")
        x("UPDATE print_orders SET lab_orders=?, lab_words=? WHERE id=?", (json.dumps(nums), str(words or "")[:200], oid_))
        if cost:
            remake_ = o["state"] == "remake"
            if not remake_:
                x("""UPDATE print_orders SET cost_products=COALESCE(?, cost_products), cost_shipping=COALESCE(?, cost_shipping),
                     cost_tax=COALESCE(?, cost_tax), cost_other=COALESCE(?, cost_other), lab_tax_pending=? WHERE id=?""",
                  (cost.get("products"), cost.get("shipping"), cost.get("lab_tax"), cost.get("other"), 1 if cost.get("pending") else 0, oid_))
            num_ = latest_number(dict(o, lab_orders=json.dumps(nums)))
            had = [r_ for r_ in costs_of(oid_) if r_["lab_number"] == num_ and r_["kind"] != "refund"]
            new_ = {"products": cost.get("products"), "shipping": cost.get("shipping"), "tax": cost.get("lab_tax"),
                    "other": cost.get("other")}
            delta = {k_: ((new_[k_] if new_[k_] is not None else sum(r_[k_] for r_ in had)) - sum(r_[k_] for r_ in had))
                     for k_ in COST_KEYS}
            if not had or any(delta.values()):
                record_cost(oid_, (had[0]["kind"] if had else ("remake" if remake_ else "order")), delta,
                            o["lab_name"] or "", num_)
        if what in ("given_back", "canceled"):
            reverse_costs(o, latest_number(dict(o, lab_orders=json.dumps(nums))), f"the lab reported {what.replace('_', ' ')}")
            x("UPDATE print_orders SET lab_id=NULL WHERE id=?", (oid_,))
        move(order(oid_), new, (words or outcome or what)[:120], by="lab")
        return True

    class HostArea:

        def __init__(self, lab):
            self.folder = check_host_folder(lab.get("host_folder") or f"{lab['id']}.d")

        def _n(self, name):
            return f"{self.folder}/{check_host_name(name)}"

        def put(self, name, data):
            G.gallery_host().put_stream(self._n(name), io.BytesIO(data), content_type="application/octet-stream")

        def delete(self, name):
            G.gallery_host().delete(self._n(name))

        def list(self):
            return [n_.split("/", 1)[1] for n_ in G.gallery_host().list(self.folder + "/") if n_.startswith(self.folder + "/")]

        def public_url(self, name):
            with appctx():
                base = setting("gallery_base_url")
            return (base if base.endswith("/") else base + "/") + self._n(name)

        def fetch_public(self, name):
            import hostcheck
            return hostcheck.fetch(self.public_url(name))

        def probe(self):
            try:
                with appctx():
                    return (json.loads(setting("gallery_host_caps") or "{}").get("now") or {})
            except ValueError:
                return {}

    def secret_get(name):
        r_ = q("SELECT value FROM print_lab_secrets WHERE name=?", (str(name),), one=True)
        return r_["value"] if r_ else None

    def secret_set(name, value):
        x("INSERT INTO print_lab_secrets (name, value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
          (str(name), value))

    class Prints:

        @staticmethod
        def print_files(order_id, lab_id=None):
            lab = lab_by_id(lab_id) if lab_id else None
            return print_files(order_id, rules=(lab or {}).get("file_rules") or {})

        @staticmethod
        def host(lab_id):
            lab = lab_by_id(lab_id)
            if not lab:
                raise LabError("no such lab plugin")
            return HostArea(lab)

    for n_, f_ in (("order", snapshot), ("hand_off", hand_off), ("report", report), ("paid_in_full", paid_in_full),
                   ("secret_get", secret_get), ("secret_set", secret_set)):
        setattr(Prints, n_, staticmethod(f_))
    type(ctx).prints = Prints
    app.config["prints_api"] = Prints

    BLOCK = """<div id="prints">{% if why %}<p class="sub" style="font-size:13px;margin:0">{{ why }}</p>{% elif email_only %}<p class="sub" style="font-size:13px;margin:0">This host runs no PHP: the gallery says "For prints, email the studio".</p>{% else %}<p class="sub" style="font-size:13px;margin:0">Clients can order prints from this gallery.</p>{% endif %}
{% if can_switch %}<form method="post" action="{{ url_for('prints_gallery_switch', sid=sid) }}" class="inline"><label class="row" style="gap:6px"><input type="checkbox" name="offer" value="1" data-autosubmit {{ 'checked' if switch }}>Offer prints from this gallery</label><noscript><button class="quiet">Save</button></noscript></form>{% endif %}
{% for o in orders %}<div class="row" style="gap:8px;font-size:13px"><span class="grow">{{ o.when }} &middot; {{ o.who }}: {{ o.words }}</span><a class="go" href="{{ url_for('prints_order', oid_=o.id) }}">Open &rsaquo;</a></div>{% endfor %}</div>"""

    def block(s):
        if not active():
            return []
        r_ = gallery_row(sid=s["id"])
        orders = q("SELECT * FROM print_orders WHERE session_id=? ORDER BY collected_at DESC, id DESC", (s["id"],))
        if not orders and not (r_ and G.is_download(r_["gallery_id"] or "") and prints_on()):
            return []
        why = why_no_prints(dict(r_)) if r_ else "No gallery yet."
        view = [dict(id=o["id"], when=ctx.nice((o["collected_at"] or "")[:10]), who=who_of(dict(o))[0],
                     words=P.WORDS.get(o["state"], o["state"])) for o in orders]
        html = render_template_string(BLOCK, why=why if why not in ("Prints are turned off for this gallery.",) else "",
                                      email_only=host_kind() == "none", orders=view, sid=s["id"], switch=gallery_switch(s["id"]),
                                      can_switch=bool(r_) and prints_on())
        return [("Print orders", html)]

    ctx.hooks.stage_blocks.append(block)

    @app.post("/session/<int:sid>/prints/switch", endpoint="prints_gallery_switch")
    def prints_gallery_switch(sid):
        r_ = gallery_row(sid=sid) or abort(404)
        on = bool(request.form.get("offer"))
        x("""INSERT INTO print_galleries (session_id, gid, off) VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET off=excluded.off""",
          (sid, r_["gallery_id"], 0 if on else 1))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("session_view", sid=sid))

    app.config["ep_words"]["prints_gallery_switch"] = lambda f: ("Offered prints from this gallery" if f.get("offer") else
                                                                 "Stopped offering prints from this gallery")
