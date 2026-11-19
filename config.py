import re

from formats import money as _money

CREDIT = "Made with Lumen Aureum\u2122 \u00b7 lumenaureum.org"

STAGES = ["inquiry", "quoted", "booked", "shot", "delivered", "closed"]
OFF_PIPELINE = ["lost"]
STAGE_LABELS = {
    "inquiry": "Inquiry", "quoted": "Quoted", "booked": "Booked", "shot": "Shot",
    "delivered": "Delivered", "closed": "Closed", "lost": "Lost",
}

PORTRAIT_FLOW = {
    "inquiry": [("auto", "logged"), ("auto", "intake_done")],
    "quoted": [("auto", "quote_built"), ("do", "Send quote", None), ("do", "Client said yes", None)],
    "booked": [("auto", "date_set"), ("auto", "agreement_signed"), ("auto", "deposit_paid"),
               ("auto", "releases_signed"), ("do", "Send session details", -7), ("do", "Confirm time and place", -1)],
    "shot": [("do", "Back up photos", 0), ("do", "Cull", 3), ("do", "Edit", 10),
             ("auto", "gallery_built"), ("do", "Send invoice", 10), ("auto", "balance_paid"), ("auto", "letter_mailed")],
    "delivered": [("auto", "first_visit"), ("auto", "favorites"), ("do", "Send retouched photos", 28), ("auto", "sd_shipped")],
}
EVENT_FLOW = {

    "inquiry": [("auto", "logged"), ("auto", "intake_done")],
    "quoted": [("auto", "quote_built"), ("do", "Send quote", None), ("do", "Client said yes", None)],
    "booked": [("event", "Confirm the venue's photo rules", -14), ("auto", "releases_signed"),
               ("event", "Scout the venue, dial in settings", -1)],
    "shot": [("event", "Back up photos", 0), ("event", "Cull", 3), ("event", "Edit", 10),
             ("auto", "gallery_built"), ("auto", "letter_mailed")],
    "delivered": [("auto", "first_visit"), ("auto", "favorites")],
}

EVENT_FAMILY_PAID_FLOW = {
    "inquiry": EVENT_FLOW["inquiry"], "quoted": EVENT_FLOW["quoted"],
    "booked": [("event", "Confirm the venue's photo rules", -14), ("auto", "deposit_paid"),
               ("auto", "releases_signed"), ("event", "Scout the venue, dial in settings", -1)],
    "shot": [("event", "Back up photos", 0), ("event", "Cull", 3), ("event", "Edit", 10), ("auto", "gallery_built"),
             ("do", "Send invoice", 10), ("auto", "balance_paid"), ("auto", "letter_mailed")],
    "delivered": EVENT_FLOW["delivered"],
}

ORGANIZER_PAYER_FLOW = {
    "inquiry": [("auto", "logged")],
    "quoted": [("auto", "quote_built"), ("do", "Send quote", None), ("do", "Client said yes", None)],
    "booked": [("auto", "agreement_signed"), ("auto", "deposit_paid")],
    "shot": [("do", "Send invoice", 10), ("auto", "balance_paid")],
    "delivered": [("do", "Deliver the organizer's copies", 21)],
}
EVENT_RIGHTS = {"families": ("I keep the copyright in the event photos. Each family receives a permanent personal license to its own"
                             " photos and to group photos that include its child: to print, share and post them, not to sell them."
                             " The organizer may use event photos in its newsletter and on its website, without names."),
                "organizer": ("The organizer owns the copyright in the event photos under its agreement with me. Each family"
                              " receives, from the organizer through me, a permanent personal license to its own photos and to"
                              " group photos that include its child: to print, share and post them, not to sell them."),
                "custom": ""}

EVENT_RIGHTS_RELEASE = {"families": ("{photographer} keeps the copyright in the event photos. My family receives a permanent personal"
                                     " license to our own photos and to group photos that include {person}: to print, share and"
                                     " post them, not to sell them. The organizer may use event photos in its newsletter and on its"
                                     " website, without names."),
                        "organizer": ("The organizer owns the copyright in the event photos under its agreement with"
                                      " {photographer}. My family receives a permanent personal license to our own photos and to"
                                      " group photos that include {person}: to print, share and post them, not to sell them.")}

EVENT_RIGHTS_ORGANIZER = {"organizer": ("When the balance is paid in full, I transfer to the organizer all copyright in the event"
                                        " photos I deliver. The organizer grants each participating family a permanent personal"
                                        " license to its own photos and to group photos that include its child, to print, share and"
                                        " post them but not to sell them, and authorizes me to deliver the photos to the families with"
                                        " that license. The organizer also gives me permission, which continues after the transfer,"
                                        " to keep copies to fulfill families' orders and to show photos as the portfolio section says.")}
REALESTATE_FLOW = {
    "inquiry": [("auto", "logged"), ("auto", "intake_done")],
    "quoted": [("auto", "quote_built"), ("do", "Send quote", None), ("do", "Client said yes", None)],
    "booked": [("auto", "date_set"), ("auto", "agreement_signed"), ("do", "Confirm access", -2)],
    "shot": [("do", "Back up photos", 0), ("do", "Edit", 1)],
    "delivered": [("auto", "sd_shipped"), ("do", "Send invoice", 2), ("auto", "balance_paid")],
}
PORTFOLIO_FLOW = {
    "booked": [("auto", "date_set"), ("do", "Plan locations", -3)],
    "shot": [("do", "Back up photos", 0), ("do", "Cull", 3), ("do", "Edit", 10)],
}

BUILTIN_STEPS = ("Send quote", "Client said yes", "Send session details", "Confirm time and place", "Send invoice",
                 "Send retouched photos")

AUTO_KEYS = ("logged", "intake_done", "quote_built", "date_set", "agreement_signed", "deposit_paid", "releases_signed",
             "gallery_built", "letter_mailed", "balance_paid", "first_visit", "favorites", "sd_shipped")
STEP_STAGES = ("inquiry", "quoted", "booked", "shot", "delivered")

DEFAULT_WEAR = "clothes, hair and grooming"
DEFAULT_POLICY = dict(free_reschedule=True, notice_hours=48, late_minutes=30, outfits=True, wear=DEFAULT_WEAR,
                      delivery="as full-resolution digital files, with an online gallery",
                      weather="we reschedule at no charge",
                      keep_raw_days=90,
                      license_use="personal use: printing, sharing and posting them, and having them printed anywhere",

                      gallery_codeword=True)

FILL_INS = [("delivery", "You receive the edited photos ___.", "HOW YOU DELIVER"),
            ("weather", "If the weather turns, ___.", "WEATHER PLAN"),
            ("license_use", "Your license covers ___.", "WHAT THE LICENSE COVERS")]
BLANK = "[{}]"

def fill(stype, key):

    d = (POLICY[stype].get(key) or "").strip().rstrip(".")
    if key == "keep_raw":
        return days_words(raw_keep_days(stype))
    return d or BLANK.format(next(b for k, _, b in FILL_INS if k == key))

def raw_keep_days(stype):

    own = next(((t.get("policy") or {}) for t in TYPES if t.get("key") == stype), {})
    if own.get("keep_raw_days") not in (None, ""):
        try:
            return max(1, min(int(own["keep_raw_days"]), 3650))
        except (TypeError, ValueError):
            pass
    return phrase_days(own.get("keep_raw")) or DEFAULT_POLICY["keep_raw_days"]

_NUM = dict(a=1, an=1, one=1, two=2, three=3, four=4, five=5, six=6, seven=7, eight=8, nine=9, ten=10, twelve=12,
            eighteen=18, thirty=30, sixty=60, ninety=90)

def phrase_days(text):

    m = __import__("re").match(r"\s*(\d+|[a-z]+)\s+(day|week|month|year)s?\b", (text or "").lower())
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else _NUM.get(m.group(1))
    if not n:
        return None
    if m.group(2) == "month":
        return n // 12 * 365 if n % 12 == 0 else 182 if n == 6 else n * 30
    return n * dict(day=1, week=7, year=365)[m.group(2)]

def days_words(n):

    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    if n % 365 == 0 and n // 365 in words:
        return words[n // 365] + (" year" if n == 365 else " years")
    return f"{n} days" if n != 1 else "1 day"

def gallery_days(stype=None):

    if stype:
        try:
            v = int(POLICY[stype].get("gallery_days") or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return min(v, 3650)
    return GALLERY_DAYS

def delivery_days(stype=None):

    if stype:
        try:
            v = int(POLICY[stype].get("delivery_days") or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return min(v, 365)
        if TYPE_KIND.get(stype) == "event":
            return EVENT_DELIVERY_DAYS
    return DELIVERY_DAYS

DOC_LABELS = {"portrait": "Portrait session", "senior": "Senior portrait session", "family": "Family session",
              "wedding": "Wedding", "sports": "Sports portrait session", "newborn": "Newborn session",
              "engagement": "Engagement session", "headshot": "Headshot session", "pet": "Pet session", "event": "Group event",
              "realestate": "Real estate shoot", "product": "Product shoot", "food": "Food shoot", "portfolio": "Personal project"}

def doc_label(stype):
    t = next((t_ for t_ in TYPES if t_.get("key") == stype), None)
    return ((t or {}).get("doc_label") or DOC_LABELS.get(stype) or SESSION_TYPES.get(stype, stype or "Session"))

def has_deposit_step(stype):

    return any(st[0] == "auto" and st[1] == "deposit_paid" for steps in WORKFLOW[stype].values() for st in steps)

GALLERY_PHRASE = __import__("re").compile(r",?\s*(?:with|through|in|via|plus)\s+(?:an?\s+)?(?:private\s+)?online gallery", __import__("re").I)
galleries_on = lambda: True

def wear_text(stype=None):

    w = ((POLICY[stype].get("wear") if stype else "") or DEFAULT_WEAR).strip().rstrip(".")
    return w

DELIVERY_METHODS = {"sd": ("SD card", "on an SD card"), "usb": ("USB drive", "on a USB drive"),
                    "disc": ("CD or DVD", "on a CD or DVD"), "link": ("Download link", "through a download link I send you")}
DELIVERY_CHOICES = [(k_, v_[0]) for k_, v_ in DELIVERY_METHODS.items()]

def is_place(stype):

    return bool(stype) and TYPE_KIND.get(stype) == "property" and stype not in PRODUCT_TYPES and stype not in INDOOR_TYPES

def delivery_method(stype, chosen=None):

    if chosen in DELIVERY_METHODS:
        return chosen
    d = (POLICY[stype].get("delivery_method") or "") if stype else ""
    if d in DELIVERY_METHODS:
        return d
    return "link" if stype and TYPE_KIND.get(stype) == "property" else "usb"

def delivery_text(stype, method=None):

    text = fill(stype, "delivery")
    try:
        on = galleries_on()
    except Exception:
        on = True
    m = GALLERY_PHRASE.search(text)
    core = (GALLERY_PHRASE.sub("", text).strip().rstrip(",") or text)
    medium = DELIVERY_METHODS[delivery_method(stype, method)][1]
    if medium.split(" ", 2)[-1] in core or core.startswith("["):
        medium = ""
    out = core + (("," if "," in core else "") + " " + medium if medium else "")
    if m and on:
        out += m.group(0) if m.group(0).startswith(",") else ", " + m.group(0).strip()
    return out

_PORTRAIT_INTAKE = [("who", "Who is coming"), ("interests", "Interests"), ("looks", "Looks wanted"),
                    ("outfits", "Number of outfits"), ("must", "Must-have shots"), ("avoid", "Anything to avoid"),
                    ("props", "Pets or props")]
KINDS = {
    "portrait": dict(label="Portrait session", flow=PORTRAIT_FLOW, intake=_PORTRAIT_INTAKE, agreement=True, release=True, subjects=True),
    "event": dict(label="Group event (many families)", flow=EVENT_FLOW, intake=[("who", "Who is being photographed"), ("must", "Must-have moments")],
                  agreement=False, release=True, subjects=True),
    "property": dict(label="Property or business (licensed photos, invoiced)", flow=REALESTATE_FLOW,
                     intake=[("address", "Address"), ("access", "Access notes"), ("rooms", "What to feature")],
                     agreement=True, release=False, subjects=False),
    "personal": dict(label="Personal project", flow=PORTFOLIO_FLOW, intake=[("goal", "What I'm after")],
                     agreement=False, release=False, subjects=False),
}

_ADDONS = [
    {"key": "extra_hour", "label": "Additional hour", "amount": 150, "unit": "hour"},
    {"key": "extra_person", "label": "Additional person", "amount": 25, "unit": "each"},
    {"key": "extra_loc", "label": "Additional location", "amount": 50, "unit": "each"},
    {"key": "retouch", "label": "Additional retouching, per image", "amount": 20, "unit": "each"},
    {"key": "rush", "label": "Rush editing (within 3 days)", "amount": 100, "unit": "flat"},
    {"key": "travel", "label": "Travel beyond the free miles, per mile each way", "amount": 0, "unit": "mile", "passthrough": True},
]
PRESETS = {
    "portraits": dict(label="Portraits", types=[dict(key="portrait", label="Portraits", kind="portrait", minutes=60)],
                      fees=[{"key": "portrait", "label": "Portrait session (1 hr)", "amount": 250, "unit": "flat", "minutes": 60, "type": "portrait"}]),
    "seniors": dict(label="Seniors", types=[dict(key="senior", label="Senior portraits", kind="portrait", minutes=90, grad=True,
                                                 intake=[["school", "School and grad year"]])],
                    fees=[{"key": "senior", "label": "Senior session (1.5 hr)", "amount": 350, "unit": "flat", "minutes": 90, "type": "senior"},
                          {"key": "senior_ext", "label": "Senior session, extended (2.5 hr)", "amount": 500, "unit": "flat", "minutes": 150, "type": "senior"}]),
    "families": dict(label="Families", types=[dict(key="family", label="Family", kind="portrait", minutes=60)],
                     fees=[{"key": "family", "label": "Family session (1 hr)", "amount": 300, "unit": "flat", "minutes": 60, "type": "family"},
                           {"key": "mini", "label": "Mini session (30 min)", "amount": 150, "unit": "flat", "minutes": 30, "type": "family"}]),
    "weddings": dict(label="Weddings", types=[dict(key="wedding", label="Weddings", kind="portrait", minutes=480,
                                                   deposit_pct=30, all_sign=True,
                                                   questions=[["venue", "Venue"], ["timeline", "Day timeline"], ["second", "Second shooter"],
                                                              ["guests", "Guest count"], ["who", "Family groups for the formal photos"],
                                                              ["must", "Must-have shots"], ["avoid", "Anything to avoid"]],
                                                   policy=dict(free_reschedule=False, notice_hours=0, late_minutes=0, outfits=False,
                                                               delivery_days=60,
                                                               weather="we shoot as planned, rain or shine, and I bring what's needed"))],
                     fees=[{"key": "wedding", "label": "Wedding day (8 hr)", "amount": 2800, "unit": "flat", "minutes": 480, "type": "wedding"}]),
    "sports": dict(label="Sports", types=[dict(key="sports", label="Sports", kind="portrait", minutes=45, grad=True,
                                               intake=[["sport", "Sport, team, position"]])],
                   fees=[{"key": "sports", "label": "Sports portraits (45 min)", "amount": 175, "unit": "flat", "minutes": 45, "type": "sports"}]),
    "newborn": dict(label="Newborn and baby", types=[dict(key="newborn", label="Newborn and baby", kind="portrait", minutes=180, indoors=True, baby=True,
                                                         questions=[["baby", "Baby's name, due or birth date"], ["where", "At home or in the studio"],
                                                                    ["who", "Who else is in the photos"], ["looks", "Looks wanted"],
                                                                    ["must", "Must-have shots"], ["props", "Keepsakes or props"]],
                                                         policy=dict(free_reschedule=True, notice_hours=0, late_minutes=0, outfits=False,
                                                                     weather="nothing changes, since we shoot indoors"))],
                    fees=[{"key": "newborn", "label": "Newborn session (up to 3 hr)", "amount": 400, "unit": "flat", "minutes": 180, "type": "newborn"},
                          {"key": "milestone", "label": "Baby milestone session (45 min)", "amount": 225, "unit": "flat", "minutes": 45, "type": "newborn"}]),
    "engagements": dict(label="Engagements", types=[dict(key="engagement", label="Engagements", kind="portrait", minutes=90,
                                                         all_sign=True,
                                                         questions=[["story", "How you met, places that matter"], ["wdate", "Wedding date"],
                                                                    ["looks", "Looks wanted"], ["outfits", "Number of outfits"],
                                                                    ["must", "Must-have shots"], ["avoid", "Anything to avoid"]])],
                        fees=[{"key": "engagement", "label": "Engagement session (1.5 hr)", "amount": 350, "unit": "flat", "minutes": 90, "type": "engagement"}]),
    "headshots": dict(label="Headshots and business", types=[dict(key="headshot", label="Headshots", kind="portrait", minutes=30, license=True,
                                                                  questions=[["usage", "Where the photos will be used"], ["company", "Company, if a team"],
                                                                             ["looks", "Backdrop and style"], ["outfits", "Number of outfits"],
                                                                             ["avoid", "Anything to avoid"]],
                                                                  policy=dict(free_reschedule=True, notice_hours=24, late_minutes=15, outfits=True,
                                                                              delivery="as full-resolution files, plus copies sized for profiles and the web",
                                                                              license_use="professional profiles, websites, press and marketing for the people photographed and their employer",
                                                                              weather="we shoot indoors or pick a new day at no charge"))],
                      fees=[{"key": "headshot", "label": "Headshot session (30 min)", "amount": 175, "unit": "flat", "minutes": 30, "type": "headshot"},
                            {"key": "team_head", "label": "Team headshots on site, per person", "amount": 75, "unit": "each", "type": "headshot"}]),
    "pets": dict(label="Pets", types=[dict(key="pet", label="Pets", kind="portrait", minutes=60, animals=True,
                                           policy=dict(outfits=False, weather="we pick a new day at no charge"),
                                           questions=[["pet", "Pet's name, kind and temperament"], ["who", "People in the photos too"],
                                                      ["looks", "Looks wanted"], ["must", "Must-have shots"], ["avoid", "Anything to avoid"]])],
                 fees=[{"key": "pet", "label": "Pet session (1 hr)", "amount": 225, "unit": "flat", "minutes": 60, "type": "pet"}]),
    "events": dict(label="Group events (teams, schools, groups)", types=[dict(key="event", label="Group events", kind="event", minutes=120,
                                                     policy=dict(free_reschedule=False, notice_hours=0, late_minutes=0, outfits=False,
                                                               weather="we shoot as planned, rain or shine, and I bring what's needed"))],
                   fees=[{"key": "event", "label": "Event coverage, per hour (2 hr minimum)", "amount": 175, "unit": "hour", "min": 2, "type": "event"}]),
    "realestate": dict(label="Real estate", types=[dict(key="realestate", label="Real estate", kind="property", minutes=90,
                                                        policy=dict(free_reschedule=True, notice_hours=24, late_minutes=0, outfits=False,
                                                        delivery="as full-resolution files, sized for listings and the web",
                                                        license_use="marketing this property, in print and online, for as long as it is marketed"))],
                       fees=[{"key": "re_small", "label": "Real estate, up to 2,500 sq ft", "amount": 175, "unit": "flat", "minutes": 75, "type": "realestate"},
                             {"key": "re_large", "label": "Real estate, over 2,500 sq ft", "amount": 275, "unit": "flat", "minutes": 120, "type": "realestate"}]),
    "products": dict(label="Products and brands", types=[dict(key="product", label="Products and brands", kind="property", minutes=180, products=True,
                                                              questions=[["items", "Products, and how many"], ["usage", "Where the photos will be used"],
                                                                         ["rooms", "Style or references"]],
                                                              policy=dict(free_reschedule=True, notice_hours=24, late_minutes=0, outfits=False,
                                                              delivery="as full-resolution files, sized for the web and print",
                                                                    license_use="showing and selling these products, in print and online"))],
                     fees=[{"key": "product", "label": "Product photos, per image", "amount": 40, "unit": "each", "type": "product"}]),
    "food": dict(label="Food and restaurants", types=[dict(key="food", label="Food and restaurants", kind="property", minutes=240, indoors=True,
                                                           questions=[["menu", "Dishes or drinks to feature"], ["address", "Address"],
                                                                         ["access", "Access notes"], ["usage", "Where the photos will be used"]],
                                                           policy=dict(free_reschedule=True, notice_hours=24, late_minutes=0, outfits=False,
                                                           delivery="as full-resolution files, sized for menus, the web and social media",
                                                           weather="nothing changes, since we shoot indoors",
                                                              license_use="promoting this business and its menu, in print and online"))],
                 fees=[{"key": "food", "label": "Food shoot, half day (up to 15 dishes)", "amount": 650, "unit": "flat", "minutes": 240, "type": "food"},
                       {"key": "food_dish", "label": "Additional dish", "amount": 35, "unit": "each", "type": "food"}]),
}
PERSONAL_TYPE = dict(key="portfolio", label="Personal project", kind="personal", minutes=120)
PRESETS["personal"] = dict(label="Personal projects", types=[PERSONAL_TYPE], fees=[])

TYPE_ICONS = {"portrait": "face", "senior": "school", "family": "family_restroom", "wedding": "rings",
              "engagement": "diamond", "sports": "sports_soccer", "newborn": "stroller", "headshot": "badge", "pet": "pets",
              "event": "groups", "realestate": "house", "product": "shopping_bag", "food": "restaurant",
              "portfolio": "photo_camera"}
KIND_ICONS = {"event": "groups", "property": "house"}

def type_icon(stype):

    return TYPE_ICONS.get(stype) or KIND_ICONS.get(TYPE_KIND.get(stype)) or "photo_camera"
BASIC_PRESETS = ("portraits", "families", "events")

def shipped_type(key):

    return next((t for pr in PRESETS.values() for t in pr["types"] if t["key"] == key), None)

def deposit_follows_setup(t):

    if t.get("deposit_own"):
        return False
    s_ = shipped_type(t.get("key"))
    keys = ("deposit_pct", "deposit_amount")
    return bool(s_) and any(s_.get(k) is not None for k in keys) and all(t.get(k) == s_.get(k) for k in keys)

def deposit_rule(t):

    if deposit_follows_setup(t):
        return ("pct", EVENT_DEPOSIT_PCT) if t.get("deposit_pct") is not None else ("amt", DEPOSIT_AMOUNT)
    return (("pct", t["deposit_pct"]) if t.get("deposit_pct") is not None else
            ("amt", t["deposit_amount"]) if t.get("deposit_amount") is not None else None)

def setup_pct_types():

    return [t["label"] for t in TYPES if t.get("kind") != "event" and t.get("deposit_pct") is not None
            and deposit_follows_setup(t)]

def own_delivery_types():

    out = []
    for t in TYPES:
        try:
            v = int(((t.get("policy") or {}).get("delivery_days")) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0 and t.get("kind") != "event":
            out.append((t["label"], min(v, 365)))
    return out

def preset_types(names):
    return [t for n in names if n in PRESETS for t in PRESETS[n]["types"]]

def preset_fees(names):
    return [f for n in names if n in PRESETS for f in PRESETS[n]["fees"]] + _ADDONS

class _ByType(dict):

    def __init__(self, data, fallback):
        super().__init__(data)
        self.fallback = fallback

    def __missing__(self, key):
        return self.fallback

TYPES = preset_types(BASIC_PRESETS)

def type_flow(t):

    flow = dict(KINDS[t["kind"]]["flow"])
    for stage, steps in (t.get("steps") or {}).items():
        if stage in STEP_STAGES:
            flow[stage] = [tuple(st) for st in steps]
    return flow

def type_questions(t):

    if t.get("questions"):
        return [tuple(q) for q in t["questions"]]
    return [tuple(q) for q in t.get("intake", [])] + KINDS[t["kind"]]["intake"]

def with_family_money(flow):

    out = {k: list(v) for k, v in flow.items()}
    booked = out.setdefault("booked", [])
    if ("auto", "deposit_paid") not in booked:
        booked.insert(0, ("auto", "deposit_paid"))
    shot = out.setdefault("shot", [])
    if ("auto", "balance_paid") not in shot:
        shot += [("do", "Send invoice", 10), ("auto", "balance_paid")]
    return out

def build_types():

    g = globals()
    types = [t for t in g["TYPES"] if t.get("kind") in KINDS and t.get("key")]
    if not types:
        types = preset_types(BASIC_PRESETS)
    g["SESSION_TYPES"] = _ByType({t["key"]: t["label"] for t in types}, "Session")
    g["TYPE_KIND"] = _ByType({t["key"]: t["kind"] for t in types}, "portrait")
    g["WORKFLOW"] = _ByType({t["key"]: type_flow(t) for t in types}, PORTRAIT_FLOW)
    g["POLICY"] = _ByType({t["key"]: {**DEFAULT_POLICY, **(t.get("policy") or {})} for t in types}, dict(DEFAULT_POLICY))
    g["INTAKE"] = _ByType({t["key"]: type_questions(t) for t in types}, _PORTRAIT_INTAKE)
    g["NEEDS_AGREEMENT"] = {t["key"] for t in types if KINDS[t["kind"]]["agreement"]}
    g["ANIMAL_TYPES"] = {t["key"] for t in types if t.get("animals")}
    g["NEEDS_RELEASE"] = {t["key"] for t in types if KINDS[t["kind"]]["release"] and not t.get("animals")}
    g["SUBJECT_TYPES"] = {t["key"] for t in types if KINDS[t["kind"]]["subjects"]}
    g["GRAD_TYPES"] = {t["key"] for t in types if t.get("grad")}
    g["ALL_SIGN"] = {t["key"]: bool(t.get("all_sign")) for t in types}
    g["DEPOSIT_RULE"] = {t["key"]: deposit_rule(t) for t in types}
    g["PORTFOLIO_DISCOUNT"] = {t["key"]: (("pct", t["portfolio_pct"]) if t.get("portfolio_pct") else
                                         ("amt", t["portfolio_amount"]) if t.get("portfolio_amount") else None) for t in types}
    g["PRODUCT_TYPES"] = {t["key"] for t in types if t.get("products")}
    g["BABY_TYPES"] = {t["key"] for t in types if t.get("baby")}
    g["INDOOR_TYPES"] = {t["key"] for t in types if t.get("indoors") or "indoors" in ((t.get("policy") or {}).get("weather") or "")}
    g["TYPE_MINUTES"] = _ByType({t["key"]: int(t.get("minutes") or 60) for t in types}, 60)
    g["EVENT_TYPE"] = next((t["key"] for t in types if t["kind"] == "event"), "event")
    g["NEW_SESSION_TYPES"] = [t["key"] for t in types if t["kind"] != "event"]

DEPOSIT_AMOUNT = 100
EVENT_DEPOSIT_PCT = 50
build_types()
GALLERY_DAYS = 30
CLOSED_CLIENT_YEARS = 7
GALLERY_UNOPENED_DAYS = 365
LEAD_QUIET_DAYS = 5

DEFAULT_FEES = preset_fees(BASIC_PRESETS)

DISCOUNTS = [("", "No discount", 0), ("intro", "Introductory rate", 25), ("friends", "Friends and family", 20),
             ("nonprofit", "Nonprofit", 15)]
QUOTE_TERM_DAYS = 30
DELIVERY_DAYS = 14
RESCHEDULE_FEE = 50
RETOUCH_PICKS = 5
MARKETING_PICKS = 2

EXTRA_PERSON_FEE = 25
RETOUCH_FEE = 15
TRAVEL_FREE_MILES = 25

BOOKING_BUFFER_MIN = 60
PAYMENT_METHODS = ["cash", "check"]
ADDON_FEES = ("extra_hour", "extra_person", "extra_loc", "retouch", "rush", "reschedule", "travel")
LOST_REASONS = ["Price", "Chose someone else", "Date didn't work", "No reply", "Canceled", "No-show", "Other"]

NOT_BOOKED_REASONS = ["They went elsewhere", "Not a fit for me", "No answer", "Other"]

CANCEL_REASONS = [("Canceled", "They canceled"), ("No-show", "No-show"), ("Date didn't work", "Date didn't work"), ("Other", "Other")]
EVENT_DELIVERY_DAYS = 21
BALANCE_NET_DAYS = 30
BALANCE_BEFORE_DAYS = 14
BEFORE_TYPES = ()
NET_TERMS_TYPES = tuple(t["key"] for t in TYPES if t["kind"] == "property")
LICENSE_TYPES = tuple(t["key"] for t in TYPES if t["kind"] == "property")
OFFERED_TYPES = tuple(SESSION_TYPES)

def build_editing(stype=None):
    if stype and TYPE_KIND.get(stype) == "property":
        return [
            ("Editing (every delivered image)", [
                "The best frames only, exposure, white balance and color corrected and matched across the set.",
                "Straightened verticals and lines, cropped to the agreed sizes.",
            ]),
            ("Not included", ["Removing or adding objects, composites, replacing "
                              + ("backgrounds" if stype in PRODUCT_TYPES or stype in INDOOR_TYPES else "skies or backgrounds")
                              + ", unless quoted."]),
            ("More editing", ["At the per-image price on the price list. Anything heavier gets a quote first."]),
        ]
    return [
        ("Editing (every delivered image)", [
            "Culling: the best frames only, no near-duplicates, blinks or missed focus.",
            "Exposure, white balance, color and contrast, matched across the whole set.",
            "Cropping and straightening.",
        ]),
        (f"Light retouching (up to {RETOUCH_PICKS} favorites the client picks)", [
            "Stray fur, eye discharge, a leash or lead where it is simple to remove.",
            "The aim is that they still look like themselves.",
        ] if stype in ANIMAL_TYPES else [
            "Temporary blemishes, a few stray hairs, softening under the eyes.",
            "The aim is that it still looks like the person.",
        ]),
        ("Not included", [
            "Reshaping faces or bodies, heavy skin smoothing, changing appearance.",
            "Removing people or objects from the scene, composites, replacing backgrounds.",
        ]),
        ("More retouching", ["At the per-image price on the price list. Anything heavier gets a quote first."]),
    ]

EDITING_POLICY = build_editing()

def _or_list(words):
    words = [w for w in words if w]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1] if words else ""

def portfolio_choice(n=None):

    n = MARKETING_PICKS if n is None else n
    return (f"I may show up to {n} photo{'s' if n != 1 else ''} from your session in my portfolio, on my website, and on my"
            " business's social media accounts. I choose them; you are welcome to suggest favorites. Before I first show a photo,"
            " you may say no to it, and I will not show it. After that, if you ask me in writing to take a photo down, I will"
            " remove it from my portfolio, website, and social media within 14 days and will not post it again; I cannot recall"
            " copies already printed or posts others have shared.")

def portfolio_discount_words(stype):

    r = PORTFOLIO_DISCOUNT.get(stype)
    return "" if not r else (f"{r[1]}%" if r[0] == "pct" else _money(r[1]))

def payment_text(business=""):

    m = payment_methods()
    if not m:
        return "Ask me how to pay."
    txt = _or_list(m)
    txt = txt[0].upper() + txt[1:] + "."
    if "check" in m:
        b = " ".join((business or "").split()).rstrip(".")
        txt += f" Checks are made out to {b}." if b else " Checks are made out to the business named on your invoice."
    how = " ".join((pay_details() or "").split())
    if how:
        txt += " " + how + ("" if re.search(r"[./]\w+/?$", how) and not how.endswith(".") else ("" if how.endswith(".") else "."))
    return txt

def payment_methods():
    m = list(PAYMENT_METHODS)
    if any(x not in ("cash", "check", "card", "bank transfer", "payment app") for x in m):
        m = [x for x in m if x != "payment app"]
    return m

def pay_details():
    return ""

def mileage_rate():
    return 0

def rights_text(stype):
    return ("I keep the copyright; you receive a license to use the photos, as the agreement describes."
            if stype in LICENSE_TYPES else "The photos are yours: the copyright transfers to you once the balance is paid.")

def balance_text(stype):
    if stype in BEFORE_TYPES:
        return f"due {BALANCE_BEFORE_DAYS} days before the session"
    if stype in NET_TERMS_TYPES:
        return f"due within {BALANCE_NET_DAYS} days of the invoice, which follows the session"
    return "due on delivery; your photos are released on payment"

def _notice(h):
    return f"{h // 24} days'" if h >= 72 and h % 24 == 0 else f"{h} hours'"

def cancel_text(stype):
    p = POLICY[stype]
    no_dep = has_no_deposit(stype)
    txt = "If you cancel before the session, nothing is owed." if no_dep else "If you cancel, the deposit is not refunded."
    if p["free_reschedule"]:
        txt += (f" One free reschedule{' with ' + _notice(p['notice_hours']) + ' notice' if p['notice_hours'] else ''}; "
                f"after that, {_money(RESCHEDULE_FEE)} per reschedule, which I may waive. Illness on either side "
                "is always a free reschedule.")
    else:
        txt += " A new date depends on my availability" + ("." if no_dep else "; the deposit carries over once.")
    return txt + f" If the weather turns, {fill(stype, 'weather')}."

def has_no_deposit(stype):

    own = DEPOSIT_RULE.get(stype)
    if own is not None:
        return not own[1]
    if TYPE_KIND.get(stype) == "event":
        return not EVENT_DEPOSIT_PCT
    return (TYPE_KIND.get(stype) == "property" and not has_deposit_step(stype)) or not DEPOSIT_AMOUNT

def late_text(stype):
    m = POLICY[stype]["late_minutes"]
    if not m:
        return ""
    keep = ("I may bill half of the price" if has_no_deposit(stype) else "I keep the deposit")
    return (f"The session starts and ends at the booked times. If you arrive {m} or more minutes late, or do not come, it"
            f" counts as a missed session: {keep}, and a new date needs a new booking"
            + ("." if has_no_deposit(stype) else " and a new deposit."))

def build_terms(stype=None, business=""):
    types = [stype] if stype else list(SESSION_TYPES)
    kinds = {TYPE_KIND.get(t) for t in types}
    label = lambda ts: _or_list([SESSION_TYPES.get(t, t) for t in ts]).replace(" or ", " and ")
    own = DEPOSIT_RULE.get(stype) if stype else None
    if stype and not own and TYPE_KIND.get(stype) == "property" and not has_deposit_step(stype):
        booking = "A deposit, if required, holds your date."
    elif own:
        booking = ("No deposit." if not own[1] else
                   f"A deposit of {own[1]}% of the price holds your date." if own[0] == "pct" else f"A {_money(own[1])} deposit holds your date.")
    elif kinds == {"event"}:
        booking = f"A deposit of {EVENT_DEPOSIT_PCT}% of the price holds your date."
    elif not DEPOSIT_AMOUNT and "event" not in kinds:
        booking = "No deposit."
    elif not DEPOSIT_AMOUNT:
        ev_ = [t for t in types if TYPE_KIND.get(t) == "event"]
        booking = (f"No deposit, except {label(ev_)}: a deposit of {EVENT_DEPOSIT_PCT}% of the price holds your date."
                   if EVENT_DEPOSIT_PCT else "No deposit.")
    else:
        booking = f"A {_money(DEPOSIT_AMOUNT)} deposit holds your date" + (f" ({EVENT_DEPOSIT_PCT}% for events)." if "event" in kinds else ".")
    if stype:
        booking += f" The balance is {balance_text(stype)}."
    else:
        before = [t for t in types if t in BEFORE_TYPES]
        net = [t for t in types if t in NET_TERMS_TYPES and t not in BEFORE_TYPES]
        rest = [t for t in types if t not in NET_TERMS_TYPES and t not in BEFORE_TYPES]
        if rest:
            booking += " The balance is due on delivery; photos are released on payment."
        if before:
            booking += f" {label(before)}: due {BALANCE_BEFORE_DAYS} days before the session."
        if net:
            booking += f" {label(net)}: due within {BALANCE_NET_DAYS} days of the invoice."
    out = [("Booking", booking), ("Payment", payment_text(business))]
    outfits = any(POLICY[t]["outfits"] for t in types)
    if kinds <= {"property"}:
        out.append(("What I provide", "Photography of what we agreed on, at the place and time we agreed on."))
    elif outfits:
        out.append(("What I provide", "Photography at the places we agree on. You plan the look: "
                                      f"{wear_text(stype)}, and any props."))
    else:
        out.append(("What I provide", "Photography at the places and times we agree on."))
    lic = [t for t in types if t in LICENSE_TYPES]
    if stype:
        out.append(("The photos", rights_text(stype)))
    elif lic and len(lic) < len(types):
        out.append(("The photos", f"The copyright transfers to you once the balance is paid, except {label(lic)}: "
                                  "I keep the copyright and you receive a license to use the photos."))
    else:
        out.append(("The photos", rights_text(types[0] if types else None)))

    for head, text_of in (("Cancellation", cancel_text), ("Running late", late_text)):
        groups = {}
        for t in types:
            if text_of(t):
                groups.setdefault(text_of(t), []).append(t)
        for txt, ts in groups.items():
            out.append((f"{head}: {label(ts)}" if len(groups) > 1 or len(ts) < len(types) else head, txt))
    if stype:
        days = delivery_days(stype)
        out.append(("Delivery", f"Your photos are ready within {days} days of the session, {delivery_text(stype)}."))
    else:
        out.append(("Delivery", f"Your photos are ready within {DELIVERY_DAYS} days of the session"
                                + (f" ({EVENT_DELIVERY_DAYS} for events)." if "event" in kinds else ".")))
    try:
        rate_ = float(mileage_rate() or 0)
    except (TypeError, ValueError):
        rate_ = 0
    out.append(("Travel and permits", f"Travel is free within {TRAVEL_FREE_MILES} miles, then "
                                      + (f"${rate_:.2f} a mile" if rate_ > 0 else "the mileage rate on your quote") + ", "
                                      "each way. Location permits, parking and venue fees are charged at cost."))
    return out

TERMS = build_terms()

RULE_KEYS = ("DEPOSIT_AMOUNT", "EVENT_DEPOSIT_PCT", "BALANCE_NET_DAYS", "NET_TERMS_TYPES", "LICENSE_TYPES",
             "RESCHEDULE_FEE", "PAYMENT_METHODS", "DELIVERY_DAYS", "EVENT_DELIVERY_DAYS", "GALLERY_DAYS",
             "GALLERY_UNOPENED_DAYS", "RETOUCH_PICKS", "MARKETING_PICKS", "EXTRA_PERSON_FEE", "RETOUCH_FEE",
             "TRAVEL_FREE_MILES", "OFFERED_TYPES", "TYPES", "DISCOUNTS", "BALANCE_BEFORE_DAYS", "BEFORE_TYPES")
_DEFAULTS = {}

def apply(rules):

    g = globals()
    if not _DEFAULTS:
        _DEFAULTS.update({k: g[k] for k in RULE_KEYS if k in g})
    for k in RULE_KEYS:
        if k in _DEFAULTS:
            g[k] = _DEFAULTS[k]
    import copy
    for k, v in (rules or {}).items():
        if k not in RULE_KEYS:
            continue
        if k in ("NET_TERMS_TYPES", "LICENSE_TYPES", "OFFERED_TYPES", "BEFORE_TYPES"):
            g[k] = tuple(v)
        elif k == "PAYMENT_METHODS":
            g[k] = list(v)
        elif k == "TYPES":
            g[k] = copy.deepcopy(list(v))
        elif k == "DISCOUNTS":
            g[k] = [("", "No discount", 0)] + [tuple(d) for d in v if d and d[0]]
        else:
            g[k] = v
    build_types()
    for k in ("NET_TERMS_TYPES", "LICENSE_TYPES"):
        if k not in (rules or {}):
            g[k] = tuple(t["key"] for t in g["TYPES"] if t.get("key") in g["TYPE_KIND"]
                         and (t.get("kind") == "property" or k == "LICENSE_TYPES"))
    g["OFFERED_TYPES"] = tuple(g["SESSION_TYPES"])
    g["TERMS"] = build_terms()
    g["EDITING_POLICY"] = build_editing()

def current_rules():
    g = globals()
    out = {k: (list(g[k]) if isinstance(g[k], (tuple, list)) else g[k]) for k in RULE_KEYS if k in g}
    out["DISCOUNTS"] = [list(d) for d in g["DISCOUNTS"] if d[0]]
    return out
