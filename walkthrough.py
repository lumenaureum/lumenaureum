import config as C

KIND_WHY = {"portrait": "people", "event": "events", "property": "places and products", "personal": "your own work"}

def _num(v, lo, hi):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))

def _pol(t):
    return C.POLICY[t["key"]]

def _keep_default(t, k, v, default):

    have = (t.get("policy") or {}).get(k, default)
    if v != have:
        _set_pol(t, k, v)

def _set_pol(t, k, v):
    t.setdefault("policy", {})
    if v is None:
        t["policy"].pop(k, None)
    else:
        t["policy"][k] = v

def steps_for(t):

    key, kind = t["key"], t["kind"]
    lic = key in C.LICENSE_TYPES
    out = [
        dict(id="minutes", title="How long a session usually runs",
             why="This is used to warn you about double bookings. A quote with timed items uses their times instead.",
             field=("number", "minutes", "minutes"), get=lambda: int(t.get("minutes") or 60),
             put=lambda f: t.__setitem__("minutes", _num(f.get("minutes"), 5, 1440) or int(t.get("minutes") or 60)),
             preview=lambda: ""),
        dict(id="steps", title="Your steps",
             why=("Each session of this type goes through these steps, stage by stage. A step you turn off stays in its"
                  " place, so you can turn it back on later."),
             field=("steps", "", ""), get=lambda: None,
             put=lambda f: put_steps_on(t, f),
             preview=lambda: steps_on_words(t), pv_head="Steps on"),
        dict(id="deposit", title="The deposit that holds the date",
             why=("The client pays the deposit when they book, and it holds the date. Weddings and events usually take a"
                  " percentage of the price, and portraits a flat amount. Leave it blank to use your answer in Payments"
                  " and terms."),
             field=("deposit", "", ""), get=lambda: ("", "") if C.deposit_follows_setup(t) else
             ("pct", t["deposit_pct"]) if t.get("deposit_pct") is not None else
             ("amt", t["deposit_amount"]) if t.get("deposit_amount") is not None else ("", ""),
             put=lambda f: _put_deposit(t, f),
             preview=lambda: dict(C.build_terms(key)).get("Booking", "")),
        dict(id="installments", title="Payments after the deposit",
             why=("1 is one payment, when your terms say the balance is due. More splits it into equal payments, one a"
                  " month on the booking's day of the month, the last on that day. At most " + str(C.INSTALLMENTS_MAX)
                  + " payments. A session can still have its own plan."),
             field=("number", "installments", "payments"), get=lambda: int(t.get("installments") or 1),
             put=lambda f: _put_installments(t, f),
             preview=lambda: "The balance is " + C.balance_text(key, int(t.get("installments") or 0)) + "."),
        dict(id="reschedule", title="Rescheduling and running late",
             why=("Most portrait clients expect one free reschedule if they give notice. For a date that can't move, such as"
                  " a wedding, turn the free reschedule off."),
             field=("reschedule", "", ""), get=lambda: (_pol(t)["free_reschedule"], _pol(t)["notice_hours"], _pol(t)["late_minutes"]),
             put=lambda f: _put_reschedule(t, f),
             preview=lambda: dict(C.build_terms(key)).get("Cancellation", "")),
        dict(id="indoors", title="Shot indoors",
             why="Indoor sessions need no golden hour or sunset times, so they are left off this type's pages and documents.",
             field=("check", "indoors", "This type is shot indoors"), get=lambda: bool(t.get("indoors")),
             put=lambda f: t.__setitem__("indoors", True) if f.get("indoors") else t.pop("indoors", None),
             preview=lambda: "No sunset times on this type's pages." if t.get("indoors") else "Sunset times show for the shoot day."),
        dict(id="weather", title="If the weather turns",
             why="Say what happens if the weather is bad, so it is settled before the day. For indoor sessions, say that nothing changes.",
             field=("text", "weather", "e.g. we reschedule at no charge"), get=lambda: _pol(t).get("weather") or "",
             put=lambda f: _set_pol(t, "weather", (f.get("weather") or "").strip()[:200]),
             preview=lambda: C.cancel_text(key).split(" If the weather turns, ")[-1]),
        dict(id="delivery", title="What you deliver",
             why="Describe the edited photos the client receives, such as full-resolution files or copies sized for the web. The next step sets how they arrive.",
             field=("text", "delivery", "e.g. as full-resolution digital files"), get=lambda: _pol(t).get("delivery") or "",
             put=lambda f: _set_pol(t, "delivery", (f.get("delivery") or "").strip()[:200]),
             preview=lambda: "You receive the edited photos " + C.delivery_text(key) + "."),
        dict(id="method", title="How the files reach the client",
             why=("Choose how the files usually reach the client. Each session can choose another way, and its agreement"
                  " says which one."),
             field=("method", "delivery_method", ""), get=lambda: C.delivery_method(key),
             put=lambda f: _set_pol(t, "delivery_method", f.get("delivery_method") if f.get("delivery_method") in C.DELIVERY_METHODS else None),
             preview=lambda: "You receive the edited photos " + C.delivery_text(key) + "."),
        dict(id="delivery_days", title="Days until the photos are ready",
             why="Pick a number you can meet in your busiest month. Weddings often take six to eight weeks, and portraits about two.",
             field=("number", "delivery_days", "days"), get=lambda: C.delivery_days(key),
             put=lambda f: _set_pol(t, "delivery_days", _num(f.get("delivery_days"), 1, 365)),
             preview=lambda: dict(C.build_terms(key)).get("Delivery", "")),
    ]
    if kind != "property":
        out.append(dict(id="gallery_days", title="How long the online gallery stays up",
                        why=("The count starts on the day the client first opens the gallery. Thirty days gives a family"
                             " time to share the photos before the gallery comes down."),
                        field=("number", "gallery_days", "days"), get=lambda: C.gallery_days(key),
                        put=lambda f: _set_pol(t, "gallery_days", _num(f.get("gallery_days"), 1, 3650)),
                        preview=lambda: f"The gallery stays up {C.gallery_days(key)} days after you first open it."))
    out += [
        dict(id="keep_raw", title="How long you keep the unedited files",
             why=("The agreement states this number, and the app deletes the RAWs and outtakes on that day. Today warns you"
                  " a week ahead. Ninety days leaves time for most reprint requests."),
             field=("number", "keep_raw_days", "days"), get=lambda: C.raw_keep_days(key),
             put=lambda f: _set_pol(t, "keep_raw_days", _num(f.get("keep_raw_days"), 1, 3650)),
             preview=lambda: f"I keep the unedited files for {C.fill(key, 'keep_raw')} after delivery, then delete them."),
    ]
    if kind != "property":
        out.append(dict(id="portfolio", title="Portfolio permission discount",
                        why=(f"You may show up to {C.MARKETING_PICKS} photos that you choose. The client may suggest favorites"
                             " and may say no to any photo. A discount for allowing this gives the client a real choice. Leave"
                             " it blank for no discount. Some photographers don't offer it when children are photographed."),
                        field=("portfolio", "", ""), get=lambda: ("pct", t["portfolio_pct"]) if t.get("portfolio_pct") else
                        ("amt", t["portfolio_amount"]) if t.get("portfolio_amount") else ("", ""),
                        put=lambda f: _put_portfolio(t, f),
                        preview=lambda: C.portfolio_choice() + (f" Allowing it takes {C.portfolio_discount_words(key)} off the price, before tax."
                                                                 if C.portfolio_discount_words(key) else "")))
        out.append(dict(id="signers", title="Who signs the agreement",
                        why="For couples, both partners usually sign, so the partner needs no separate release. For families, one parent usually signs.",
                        field=("check", "all_sign", "Every adult on the client record signs"), get=lambda: bool(t.get("all_sign")),
                        put=lambda f: t.__setitem__("all_sign", True) if f.get("all_sign") else t.pop("all_sign", None),
                        preview=lambda: "Every adult on the record signs." if t.get("all_sign") else "One adult signs for the household."))
    if lic:
        out.append(dict(id="license", reuse="license_use", title="What the license covers",
                        why="You keep the copyright, and the client gets a license. Describe what the license is for in a short phrase.",
                        field=("text", "license_use", "e.g. marketing this property, in print and online"),
                        get=lambda: _pol(t).get("license_use") or "",
                        put=lambda f: _set_pol(t, "license_use", (f.get("license_use") or "").strip()[:200]),
                        preview=lambda: f"Your license covers {C.fill(key, 'license_use')}."))
    out.append(dict(id="wear", title="What to wear and bring",
                    why="The session details tell the client how to get ready. Outfits and props lines add room for what they plan to bring.",
                    field=("wear", "wear", "clothes, hair and grooming"),
                    get=lambda: (bool(_pol(t).get("outfits")), _pol(t).get("wear") or C.DEFAULT_WEAR),
                    put=lambda f: (_keep_default(t, "outfits", bool(f.get("outfits")), _pol(t).get("outfits", False)),
                                   _keep_default(t, "wear", ((f.get("wear") or "").strip().rstrip(".")[:200] or C.DEFAULT_WEAR),
                                                 _pol(t).get("wear") or C.DEFAULT_WEAR)),
                    preview=lambda: "You plan the look: " + (_pol(t).get("wear") or C.DEFAULT_WEAR) + "."
                                    + (" Outfits and props get their own lines." if _pol(t).get("outfits") else "")))
    out.append(dict(id="codeword", title="Ask for a gallery passphrase",
                    why=("The client picks three words at booking, in person or on the phone. The gallery opens only with"
                         " the link and the words together."),
                    field=("check", "gallery_codeword", "Ask for a passphrase"),
                    get=lambda: bool(_pol(t).get("gallery_codeword", True)),
                    put=lambda f: _keep_default(t, "gallery_codeword", bool(f.get("gallery_codeword")),
                                                _pol(t).get("gallery_codeword", True)),
                    preview=lambda: ("Your gallery opens with its link and the three words you chose with me."
                                     if _pol(t).get("gallery_codeword", True) else "Your gallery opens with its link.")))
    out.append(dict(id="questions", title="Questions before the shoot",
                    why="What you ask the client before the session, one question per line. A question you keep word for word keeps its answers.",
                    field=("textarea", "questions", ""),
                    get=lambda: "\n".join(q_[1] for q_ in C.INTAKE[key]),
                    put=lambda f: _put_questions(t, f),
                    preview=lambda: ""))
    return out

def _put_questions(t, f):

    if "questions" not in f:
        return
    import app as A
    qs = A._parse_questions(f["questions"], C.INTAKE[t["key"]])
    base = [tuple(q_) for q_ in t.get("intake", [])] + C.KINDS[t["kind"]]["intake"]
    t.pop("questions", None)
    if qs != base:
        t["questions"] = [list(q_) for q_ in qs]

def put_steps_on(t, f):

    off = {}
    for stage, rows in C.type_steps_all(t).items():
        ids = []
        for i, (st, was_off) in enumerate(rows):
            v = f.get(f"{stage}_{i}_on")
            now_off = was_off if v not in ("0", "1") else v == "0"
            if now_off and C.step_id(st) != C.GALLERY_STEP:
                ids.append(list(C.step_id(st)))
        if ids:
            off[stage] = ids
    t.pop("steps_off", None)
    if off:
        t["steps_off"] = off

def steps_on_words(t):
    rows = [off for rows in C.type_steps_all(t).values() for _, off in rows]
    on = sum(1 for off in rows if not off)
    return f"{on} of {len(rows)} steps on." if on < len(rows) else f"All {len(rows)} steps on."

def _reschedule_words(free, notice, late):
    a = f"one free reschedule with {notice} hours' notice" if free else "no free reschedule"
    b = f"no-show after {late} minutes late" if late else "no no-show rule"
    return f"{a}, {b}"

def _money_words(rule):
    return f"{rule[1]}% of the price" if rule[0] == "pct" else f"${rule[1]:,}"

def _answer(k, fid):

    p = C.POLICY[k]
    if fid in ("weather", "delivery", "license_use", "wear"):
        v = (p.get(fid) or "").strip().rstrip(".")
        return (v, {fid: v}) if v else None
    if fid == "keep_raw":
        n = C.raw_keep_days(k)
        return f"{n} days", {"keep_raw_days": n}
    if fid == "delivery_days":
        n = C.delivery_days(k)
        return f"{n} days", {"delivery_days": n}
    if fid == "gallery_days":
        n = C.gallery_days(k)
        return f"{n} days", {"gallery_days": n}
    if fid == "method":
        m = C.delivery_method(k)
        return C.DELIVERY_METHODS[m][0], {"delivery_method": m}
    if fid == "reschedule":
        free, notice, late = bool(p["free_reschedule"]), int(p["notice_hours"] or 0), int(p["late_minutes"] or 0)
        return _reschedule_words(free, notice, late), {"free_reschedule": free, "notice_hours": notice, "late_minutes": late}
    if fid in ("deposit", "portfolio"):
        t_ = next((x for x in C.TYPES if x["key"] == k), {})
        rule = (None if C.deposit_follows_setup(t_) else C.DEPOSIT_RULE.get(k)) if fid == "deposit" else C.PORTFOLIO_DISCOUNT.get(k)
        if not rule:
            return None
        return _money_words(rule), {f"{fid}_value": rule[1], f"{fid}_mode": rule[0]}
    return None

REUSE_FIELDS = ("deposit", "reschedule", "weather", "delivery", "method", "delivery_days", "gallery_days", "keep_raw",
                "portfolio", "license_use", "wear")

def reuse(key, fid):

    if fid not in REUSE_FIELDS:
        return []
    mine = _answer(key, fid)
    out = {}
    for t in C.TYPES:
        k = t["key"]
        if k == key or t.get("kind") == "personal":
            continue
        if fid == "license_use" and k not in C.LICENSE_TYPES:
            continue
        if fid in ("gallery_days", "portfolio") and t.get("kind") == "property":
            continue
        a = _answer(k, fid)
        if not a or (mine and a[1] == mine[1]):
            continue
        sig = tuple(sorted((n, str(v)) for n, v in a[1].items()))
        out.setdefault(sig, [[], a])[0].append(t["label"])
    return [dict(words=_names(labels) + ": " + a[0], names=_names(labels), short=_short(a[0]), full=a[0], fill=a[1])
            for labels, a in out.values()]

def _short(answer):

    a = str(answer).strip()
    first = a.split()[0] if a.split() else a
    if first[:1].isdigit() or first[:1] == "$":
        return first.rstrip(",")
    return a if len(a) <= 28 else a[:27].rsplit(" ", 1)[0] + "..."

def _names(labels):

    if len(labels) > 3:
        labels = labels[:3] + [f"{len(labels) - 3} more"]
    return " and ".join(labels) if len(labels) < 3 else ", ".join(labels[:-1]) + ", and " + labels[-1]

def _put_installments(t, f):

    had = int(t.pop("installments", None) or 0)
    n = _num(f.get("installments"), 1, C.INSTALLMENTS_KEPT_MAX)
    if n and n >= 2:
        t["installments"] = n if n == had else min(n, C.INSTALLMENTS_MAX)

def _put_deposit(t, f):

    t.pop("deposit_pct", None)
    t.pop("deposit_amount", None)
    t.pop("deposit_own", None)
    mode, v = f.get("deposit_mode"), _num(f.get("deposit_value"), 0, 100000)
    if v is not None and mode in ("pct", "amt"):
        t["deposit_pct" if mode == "pct" else "deposit_amount"] = min(v, 100) if mode == "pct" else v
        t["deposit_own"] = True
        return
    shipped = C.shipped_type(t.get("key"))
    for k in ("deposit_pct", "deposit_amount"):
        if shipped and shipped.get(k) is not None:
            t[k] = shipped[k]

def _put_portfolio(t, f):
    t.pop("portfolio_pct", None)
    t.pop("portfolio_amount", None)
    mode, v = f.get("portfolio_mode"), _num(f.get("portfolio_value"), 0, 100000)
    if v and mode in ("pct", "amt"):
        t["portfolio_pct" if mode == "pct" else "portfolio_amount"] = min(v, 100) if mode == "pct" else v

def _put_reschedule(t, f):
    _set_pol(t, "free_reschedule", bool(f.get("free_reschedule")))
    _set_pol(t, "notice_hours", _num(f.get("notice_hours"), 0, 2000) or 0)
    _set_pol(t, "late_minutes", _num(f.get("late_minutes"), 0, 2000) or 0)
