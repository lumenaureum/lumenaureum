"""Walk me through this: a session type's settings one at a time, after setup (never part of it).

Each step shows the setting with its current value (the sensible default until changed), a sentence on why that default
suits this kind of shoot, and the words a client will read. Keep, Change or Skip; nothing is required. Progress per
type is kept in the settings key "walk" as {type: {"done": [...], "skipped": [...]}}. Finishing does not mark the
terms reviewed: that stays a separate, deliberate step (it removes SAMPLE)."""
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


def _set_pol(t, k, v):
    t.setdefault("policy", {})
    if v is None:
        t["policy"].pop(k, None)
    else:
        t["policy"][k] = v


def steps_for(t):
    """The steps that apply to this type, in order, simplest first."""
    key, kind = t["key"], t["kind"]
    lic = key in C.LICENSE_TYPES
    out = [
        dict(id="minutes", title="How long a session usually runs",
             why="This is used to warn you about double bookings. A quote with timed items uses their times instead.",
             field=("number", "minutes", "minutes"), get=lambda: int(t.get("minutes") or 60),
             put=lambda f: t.__setitem__("minutes", _num(f.get("minutes"), 5, 1440) or int(t.get("minutes") or 60)),
             preview=lambda: ""),
        dict(id="deposit", title="The deposit that holds the date",
             why=("The client pays the deposit when they book, and it holds the date. Weddings and events usually take a"
                  " percentage of the price, and portraits a flat amount. Leave it blank to use your general deposit."),
             field=("deposit", "", ""), get=lambda: ("pct", t["deposit_pct"]) if t.get("deposit_pct") is not None else
             ("amt", t["deposit_amount"]) if t.get("deposit_amount") is not None else ("", ""),
             put=lambda f: _put_deposit(t, f),
             preview=lambda: dict(C.build_terms(key)).get("Booking", "")),
        dict(id="reschedule", title="Rescheduling and running late",
             why=("Most portrait clients expect one free reschedule if they give notice. For a date that can't move, such as"
                  " a wedding, turn the free reschedule off."),
             field=("reschedule", "", ""), get=lambda: (_pol(t)["free_reschedule"], _pol(t)["notice_hours"], _pol(t)["late_minutes"]),
             put=lambda f: _put_reschedule(t, f),
             preview=lambda: dict(C.build_terms(key)).get("Cancellation", "")),
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
                        preview=lambda: C.portfolio_choice() + (f" Allowing it takes {C.portfolio_discount_words(key)} off the price."
                                                                 if C.portfolio_discount_words(key) else "")))
        out.append(dict(id="signers", title="Who signs the agreement",
                        why="For couples, both partners usually sign, so the partner needs no separate release. For families, one parent usually signs.",
                        field=("check", "all_sign", "Every adult on the client record signs"), get=lambda: bool(t.get("all_sign")),
                        put=lambda f: t.__setitem__("all_sign", True) if f.get("all_sign") else t.pop("all_sign", None),
                        preview=lambda: "Every adult on the record signs." if t.get("all_sign") else "One adult signs for the household."))
    if lic:
        out.append(dict(id="license", title="What the license covers",
                        why="You keep the copyright, and the client gets a license. Describe what the license is for in a short phrase.",
                        field=("text", "license_use", "e.g. marketing this property, in print and online"),
                        get=lambda: _pol(t).get("license_use") or "",
                        put=lambda f: _set_pol(t, "license_use", (f.get("license_use") or "").strip()[:200]),
                        preview=lambda: f"Your license covers {C.fill(key, 'license_use')}."))
    return out


def _put_deposit(t, f):
    t.pop("deposit_pct", None)
    t.pop("deposit_amount", None)
    mode, v = f.get("deposit_mode"), _num(f.get("deposit_value"), 0, 100000)
    if v is not None and mode in ("pct", "amt"):
        t["deposit_pct" if mode == "pct" else "deposit_amount"] = min(v, 100) if mode == "pct" else v


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
