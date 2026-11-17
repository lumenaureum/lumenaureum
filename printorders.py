import io
import math
import re
import zipfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

CURRENCY = "USD"
PAD_SIZES = (4096, 8192, 16384, 32768, 65536)
LINES_MAX = 50
QTY_MAX = 25
NAME_MAX, STREET_MAX, CITY_MAX, NOTE_MAX, PHONE_MAX = 60, 60, 40, 500, 25
MONEY_MAX = 10_000_000 * 100
MARKUP_DEFAULT = Decimal("3")
MARKUP_MIN, MARKUP_MAX = Decimal("1"), Decimal("20")
PPI_HIDE, PPI_MARK = 150, 200
SIZE_MIN, SIZE_MAX = Decimal("2"), Decimal("60")
LOW_MARGIN = Decimal("1.5")
LARGE_PRINTS, LARGE_CENTS = 25, 25000
UNPAID_REMIND_DAYS, UNPAID_EXPIRE_DAYS = 7, 30
CLOSE_AFTER_SHIPPED_DAYS = 45
REDUCE_AFTER_DAYS = 365

EMAIL_OK = re.compile(r"[^\s\"'<>\\=;&?#%]{1,64}@[^\s\"'<>\\=;&?#%@]+\.[^\s\"'<>\\=;&?#%@]+")
OID_OK = re.compile(r"[0-9a-f]{32}")
PHOTO_OK = re.compile(r"[0-9a-f]{24}\.bin")
ITEM_OK = re.compile(r"[A-Za-z0-9_-]{1,40}")
ZIP_US = re.compile(r"\d{5}(-\d{4})?")
ZIP_CA = re.compile(r"[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d")
PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.I)
_STATES = "ALAKAZARCACOCTDEDCFLGAHIIDILINIAKSKYLAMEMDMAMIMNMSMOMTNENVNHNJNMNYNCNDOHOKORPARISCSDTNTXUTVTVAWAWVWIWY"
US_STATES = [_STATES[i:i + 2] for i in range(0, len(_STATES), 2)]
AKHI = ("AK", "HI")
CA_PROVINCES = "AB BC MB NB NL NS NT NU ON PE QC SK YT".split()
DESTS = ("us", "akhi", "ca", "pickup")
CHARGE_RULES = ("cost", "cost_plus", "flat")

class Refused(ValueError):
    pass

_CTRL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")

def clean(s):

    return re.sub(r"\s+", " ", _CTRL.sub("", str(s or ""))).strip()

def label_ok(s):

    for ch in str(s or ""):
        if ch < " " or "\x7f" <= ch <= "\x9f":
            return False
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            return False
    return True

def _text(v, lo, hi, what):
    if not isinstance(v, str):
        raise Refused(f"{what} is not text")
    if v != clean(v) or not lo <= len(v) <= hi:
        raise Refused(f"{what} breaks its length or character rule")
    return v

def _int(v, lo, hi, what):
    if type(v) is not int or not lo <= v <= hi:
        raise Refused(f"{what} is not a whole number from {lo} to {hi}")
    return v

TOP_KEYS = {"v", "oid", "at", "plv", "cur", "who", "ship", "lines", "sub", "ack", "rel", "note", "pad"}
SHIP_KEYS = {"how", "name", "addr1", "addr2", "city", "state", "zip", "country", "phone"}

def check_crop(c):
    if not isinstance(c, list) or len(c) != 4:
        raise Refused("crop is not four numbers")
    for v in c:
        if type(v) not in (int, float) or not 0 <= v <= 1 or abs(round(v, 4) - v) > 1e-9:
            raise Refused("crop numbers are fractions from 0 to 1, four decimals")
    x, y, w, h = c
    if w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise Refused("crop leaves the photo")
    return [float(v) for v in c]

def check_payload(data, lines_max=LINES_MAX, qty_max=QTY_MAX):

    if not isinstance(data, dict):
        raise Refused("not an object")
    extra = set(data) - TOP_KEYS
    if extra:
        raise Refused("unknown fields: " + ", ".join(sorted(extra))[:80])
    if data.get("v") != 1 or type(data.get("v")) is not int:
        raise Refused("payload version is not 1")
    oid = data.get("oid")
    if not isinstance(oid, str) or not OID_OK.fullmatch(oid):
        raise Refused("oid is not 32 lowercase hex")
    at = data.get("at")
    if not isinstance(at, str) or not 1 <= len(at) <= 40 or not re.fullmatch(r"[0-9T:.+Z-]+", at):
        raise Refused("at is not a time")
    plv = _int(data.get("plv"), 1, 10**9, "plv")
    if data.get("cur") != CURRENCY:
        raise Refused("currency is not USD")
    who = data.get("who")
    if not isinstance(who, dict) or set(who) - {"name", "email", "phone"}:
        raise Refused("who is not the orderer")
    name = _text(who.get("name"), 1, NAME_MAX, "name")
    email = _text(who.get("email"), 3, 254, "email")
    if not EMAIL_OK.fullmatch(email):
        raise Refused("email is not an email address")
    phone = _text(who.get("phone", ""), 0, PHONE_MAX, "phone")
    ship_ = check_ship(data.get("ship"), phone)
    lines = data.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= lines_max:
        raise Refused("lines are not 1 to the most an order may have")
    out_lines, total = [], 0
    for ln in lines:
        if not isinstance(ln, dict) or set(ln) != {"p", "n", "item", "qty", "crop", "turn", "each", "ppi"}:
            raise Refused("a line has the wrong fields")
        if not isinstance(ln["p"], str) or not PHOTO_OK.fullmatch(ln["p"]):
            raise Refused("a line's photo is not a file name")
        if not isinstance(ln["item"], str) or not ITEM_OK.fullmatch(ln["item"]):
            raise Refused("a line's item is not an item id")
        if ln["turn"] not in (0, 1) or type(ln["turn"]) is not int:
            raise Refused("turn is not 0 or 1")
        n = ln["n"]
        if not isinstance(n, str) or not 1 <= len(n) <= 200 or _CTRL.search(n):
            raise Refused("a line's photo name breaks its rule")
        qty = _int(ln["qty"], 1, qty_max, "quantity")
        each = _int(ln["each"], 0, MONEY_MAX, "price")
        out_lines.append(dict(p=ln["p"], n=n, item=ln["item"], qty=qty, crop=check_crop(ln["crop"]), turn=ln["turn"],
                              each=each, ppi=_int(ln["ppi"], 0, 100000, "ppi")))
        total += each * qty
    if _int(data.get("sub"), 0, MONEY_MAX * LINES_MAX * QTY_MAX, "total") != total:
        raise Refused("the total does not add up")
    if data.get("ack") is not True:
        raise Refused("the invoice box was not checked")
    rel = data.get("rel")
    if not isinstance(rel, dict) or set(rel) != {"kind", "prev"} or rel["kind"] not in ("new", "change") or not (
            rel["prev"] is None or (isinstance(rel["prev"], str) and OID_OK.fullmatch(rel["prev"]))):
        raise Refused("rel is not new or change")
    note = data.get("note", "")
    if not isinstance(note, str) or len(note) > NOTE_MAX or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", note):
        raise Refused("note breaks its rule")
    pad = data.get("pad", "")
    if not isinstance(pad, str) or pad.strip(" "):
        raise Refused("padding is not spaces")
    return dict(v=1, oid=oid, at=at, plv=plv, cur=CURRENCY, who=dict(name=name, email=email, phone=phone), ship=ship_,
                lines=out_lines, sub=total, ack=True, rel=dict(kind=rel["kind"], prev=rel["prev"]), note=note.strip())

def pad_size(n):

    return next((s for s in PAD_SIZES if n <= s), None)

def host_name(envelope):

    import hashlib
    return hashlib.sha256(envelope).hexdigest()[:32] + ".las"

def cents(v):

    s = str(v or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    if not re.fullmatch(r"\d{1,8}(\.\d{1,2})?", s):
        raise ValueError("not an amount")
    return int((Decimal(s) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def dollars(c):
    if c is None:
        return ""
    sign = "-" if c < 0 else ""
    c = abs(int(c))
    return f"{sign}${c // 100:,}.{c % 100:02d}"

def markup_in(v):
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{1,2}(\.\d)?", s):
        raise ValueError("markup is a number like 3 or 3.5")
    m = Decimal(s)
    if not MARKUP_MIN <= m <= MARKUP_MAX:
        raise ValueError("markup is from 1 to 20")
    return m

def fill_price(cost_cents, markup=MARKUP_DEFAULT):

    raw = Decimal(int(cost_cents)) * Decimal(str(markup)) / 100
    return int(raw.to_integral_value(rounding="ROUND_CEILING")) * 100

def margin(price_cents, cost_cents):

    if cost_cents is None or price_cents is None or cost_cents <= 0:
        return None
    return price_cents - cost_cents, (Decimal(price_cents) / Decimal(cost_cents)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

def low_margin(price_cents, cost_cents):
    return cost_cents is not None and cost_cents > 0 and price_cents is not None and \
        Decimal(price_cents) < LOW_MARGIN * Decimal(cost_cents)

def size_in(v):
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{1,2}(\.\d{1,2})?", s):
        raise ValueError("a size is inches, like 8 or 8.5")
    d = Decimal(s)
    if not SIZE_MIN <= d <= SIZE_MAX:
        raise ValueError("sizes are from 2 to 60 inches")
    return d

def num(d):

    s = format(Decimal(str(d)).normalize(), "f")
    return s

STARTER = [("4x6", 4, 6, 1500), ("5x7", 5, 7, 2000), ("8x10", 8, 10, 3000), ("8x12", 8, 12, 3500),
           ("11x14", 11, 14, 8000), ("12x18", 12, 18, 9500), ("16x20", 16, 20, 15_000), ("20x30", 20, 30, 22_500)]

def starter_items():
    return [dict(id=f"pl-{i + 1}", label=f"{lab} print, Lustre", w=w, h=h, finish="Lustre", each=each, cost=None,
                 cost_from="", cost_date="", ppi_hide=PPI_HIDE, ppi_mark=PPI_MARK, on=1, lab={}, sort=i)
            for i, (lab, w, h, each) in enumerate(STARTER)]

def frame_landscape(ow, oh, turn):

    return (ow > oh) != bool(turn)

def frame_aspect(w, h, landscape):

    s, l_ = sorted((Decimal(str(w)), Decimal(str(h))))
    return float(l_ / s) if landscape else float(s / l_)

def fits_without_crop(ow, oh, w, h):

    if not ow or not oh:
        return False
    r = max(ow, oh) / min(ow, oh)
    s, l_ = sorted((float(w), float(h)))
    return abs(r - l_ / s) / (l_ / s) <= 0.01

def full_crop(ow, oh, w, h, turn=0):

    a = frame_aspect(w, h, frame_landscape(ow, oh, turn))
    if ow / oh > a:
        cw = a * oh / ow
        return [round((1 - cw) / 2, 4), 0.0, round(cw, 4), 1.0]
    ch = (ow / a) / oh
    return [0.0, round((1 - ch) / 2, 4), 1.0, round(ch, 4)]

def shape_ok(crop, ow, oh, w, h, turn, tol=0.02):

    cw, ch = crop[2] * ow, crop[3] * oh
    if cw <= 0 or ch <= 0:
        return False
    want = frame_aspect(w, h, frame_landscape(ow, oh, turn))
    return abs(cw / ch - want) / want <= tol

def effective_ppi(crop, ow, oh, w, h, turn):

    land = frame_landscape(ow, oh, turn)
    s, l_ = sorted((float(w), float(h)))
    pw, ph = (l_, s) if land else (s, l_)
    return int(min(crop[2] * ow / pw, crop[3] * oh / ph))

def _ratio(w, h, landscape):
    s, l_ = sorted((Fraction(str(w)), Fraction(str(h))))
    r = (l_ / s) if landscape else (s / l_)
    return r.numerator, r.denominator

def cut_rect(crop, ow, oh, w, h, turn):

    land = frame_landscape(ow, oh, turn)
    a, b = _ratio(w, h, land)
    x0, y0 = crop[0] * ow, crop[1] * oh
    cw, ch = min(crop[2] * ow, ow - x0), min(crop[3] * oh, oh - y0)
    if a <= 200 and b <= 200:
        k = int(min(cw // a, ch // b))
        W, H = a * k, b * k
    else:
        W = int(cw)
        H = int(round(W * b / a))
        if H > ch:
            H = int(ch)
            W = int(round(H * a / b))
    if W < 1 or H < 1:
        raise ValueError("the crop is too small to print")
    x = int(round(x0 + (cw - W) / 2))
    y = int(round(y0 + (ch - H) / 2))
    x = max(0, min(x, ow - W))
    y = max(0, min(y, oh - H))
    return x, y, W, H

def cut_ppi(rect, w, h):

    _x, _y, W, H = rect
    s, l_ = sorted((float(w), float(h)))
    pw, ph = (l_, s) if W >= H else (s, l_)
    return int(min(W / pw, H / ph))

def cut_jpeg(src, rect, quality=95, max_bytes=None):

    from PIL import Image
    with Image.open(io.BytesIO(src)) as im:
        exif, xmp = im.info.get("exif"), im.info.get("xmp")
        x, y, W, H = rect
        cut = im.convert("RGB").crop((x, y, x + W, y + H))
        out = Image.frombytes("RGB", cut.size, cut.tobytes())
    extra = {**({"exif": exif} if exif else {}), **({"xmp": xmp} if xmp else {})}
    for qv in [quality] + ([90, 85] if max_bytes else []):
        b = io.BytesIO()
        out.save(b, "JPEG", quality=qv, optimize=True, **extra)
        data = b.getvalue()
        if not max_bytes or len(data) <= max_bytes:
            return data, qv
    return data, qv

def file_name(invoice, line, w, h, finish, qty):

    s, l_ = sorted((Decimal(str(w)), Decimal(str(h))))
    fin = re.sub(r"[^A-Za-z0-9-]", "", re.sub(r"\s+", "-", (finish or "").strip()))[:30].strip("-")
    inv = re.sub(r"[^A-Za-z0-9-]", "", invoice or "order")
    return f"{inv}-{line}-{num(s)}x{num(l_)}" + (f"-{fin}" if fin else "") + f"-x{qty}.jpg"

WORDS = {"new": "New print order", "unmatched": "From someone not on file", "needs_files": "Needs the photo files",
         "invoiced": "Invoiced, waiting for payment", "paid": "Paid, ready for the lab",
         "prepared": "Files ready, order it at the lab", "with_lab": "Handed to the lab", "ordered": "Ordered at the lab",
         "shipped": "Shipped", "delivered": "Delivered", "remake": "Remake requested", "canceled": "Canceled",
         "closed": "Done", "expired": "Expired unpaid", "unreadable": "Unreadable order"}
OPEN = ("new", "unmatched", "needs_files", "invoiced", "paid", "prepared", "with_lab", "ordered", "shipped",
        "delivered", "remake")
FINAL = ("canceled", "closed", "expired", "unreadable")

HAND = {
    "unmatched": {"new", "canceled"},
    "new": {"invoiced", "canceled", "needs_files"},
    "needs_files": {"new", "unmatched", "invoiced", "paid", "prepared", "canceled"},
    "invoiced": {"paid", "new", "canceled", "expired", "needs_files"},
    "paid": {"prepared", "ordered", "with_lab", "invoiced", "canceled", "needs_files"},
    "prepared": {"ordered", "prepared", "canceled", "needs_files", "invoiced"},
    "with_lab": {"paid", "canceled"},
    "ordered": {"shipped", "canceled"},
    "shipped": {"delivered", "remake", "closed"},
    "delivered": {"closed", "remake"},
    "remake": {"ordered"},
    "expired": {"new"},
}

LAB = {("with_lab", "ordered"), ("ordered", "shipped"), ("shipped", "delivered"), ("with_lab", "paid"),
       ("remake", "ordered")}

def may_move(old, new, by="hand"):
    if by == "lab":
        return (old, new) in LAB
    return new in HAND.get(old, set())

def check_ship(ship, phone=""):

    if not isinstance(ship, dict) or set(ship) - SHIP_KEYS:
        raise Refused("ship is not a ship-to")
    how = ship.get("how")
    if how not in ("me", "other", "pickup"):
        raise Refused("ship.how is not me, other or pickup")
    if how == "pickup":
        if any(ship.get(k) not in (None, "") for k in SHIP_KEYS - {"how"}):
            raise Refused("pick up carries no address")
        return {"how": "pickup"}
    country = ship.get("country")
    if country not in ("US", "CA"):
        raise Refused("country is not US or CA")
    st = ship.get("state")
    if st not in (US_STATES if country == "US" else CA_PROVINCES):
        raise Refused("state does not belong to the country")
    z = _text(ship.get("zip"), 3, 10, "ZIP")
    if not (ZIP_US if country == "US" else ZIP_CA).fullmatch(z):
        raise Refused("ZIP or postal code has the wrong form")
    rphone = _text(ship.get("phone", ""), 0, PHONE_MAX, "phone")
    if country == "CA" and not (rphone or phone):
        raise Refused("a phone number is needed for Canada")
    return dict(how=how, name=_text(ship.get("name", ""), 1 if how == "other" else 0, NAME_MAX, "recipient"),
                addr1=_text(ship.get("addr1"), 1, STREET_MAX, "street"),
                addr2=_text(ship.get("addr2", ""), 0, STREET_MAX, "apartment"),
                city=_text(ship.get("city"), 1, CITY_MAX, "city"), state=st, zip=z, country=country, phone=rphone)

def incomplete(ship):

    return ship.get("how") != "pickup" and bool(ship.get("reduced"))

def dest_of(ship):

    if ship.get("how") == "pickup":
        return "pickup"
    if ship.get("country") == "CA":
        return "ca"
    if ship.get("country") == "US":
        return "akhi" if ship.get("state") in AKHI else "us"
    return None

def trimmed(ship, client):

    if ship.get("how") == "pickup":
        return False
    return ship.get("how") == "other" or not client

def reduce_ship(ship):

    if ship.get("how") == "pickup":
        return {"how": "pickup"}
    return dict(how=ship.get("how"), city=ship.get("city", ""), state=ship.get("state", ""), zip=ship.get("zip", ""),
                country=ship.get("country", ""), reduced=True)

def n_prints(n):

    return f"{n} print{'' if n == 1 else 's'}"

def label_lines(ship, orderer=""):

    if ship.get("how") == "pickup":
        return ["Pick up from the studio"]
    out = [ship.get("name") or orderer] if (ship.get("name") or orderer) and not ship.get("reduced") else []
    out += [x for x in (ship.get("addr1"), ship.get("addr2")) if x]
    out.append(f"{ship.get('city', '')}, {ship.get('state', '')} {ship.get('zip', '')}".strip(", "))
    if ship.get("country") == "CA":
        out.append("Canada")
    return out

def address_flags(ship, who=None):

    out = []
    fields = [ship.get(k, "") for k in ("name", "addr1", "addr2", "city")] + ([who.get("name", "")] if who else [])
    if not all(label_ok(f) for f in fields):
        out.append("Some letters may not print on a shipping label")
    if ship.get("how") != "pickup" and PO_BOX.search(ship.get("addr1") or ""):
        out.append("A PO box: some labs and carriers cannot deliver there")
    d = dest_of(ship)
    if d == "ca":
        out.append("Ships to Canada: the lab may add duties or a surcharge")
    elif d == "akhi":
        out.append("Ships to Alaska or Hawaii")
    return out

def tax_cents(amount_cents, rate_pct):
    return int((Decimal(int(amount_cents)) * Decimal(str(rate_pct)) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def shipping_charge(rule, lab_cents):

    how = (rule or {}).get("rule", "cost")
    if how == "flat":
        return int(rule.get("flat") or 0)
    if lab_cents is None:
        return None
    return int(lab_cents) + (int(rule.get("handling") or 0) if how == "cost_plus" else 0)

def sheet_text(order):

    out = [f"Order {order['invoice']}", f"Date: {order['date']}", "", "Ship to:"]
    out += ["  " + l_ for l_ in label_lines(order["ship"], order.get("orderer", ""))]
    if order["ship"].get("phone"):
        out.append("  Phone: " + order["ship"]["phone"])
    out += ["", "Prints:"]
    n = 0
    for i, ln in enumerate(order["lines"], 1):
        s, l_ = sorted((Decimal(str(ln["w"])), Decimal(str(ln["h"]))))
        out.append(f"  {i}. {ln['file']}")
        fin = ln["finish"] or "standard finish"
        fin = "" if ln["finish"] and ln["finish"].lower() in str(ln["label"]).lower() else ", " + fin
        out.append(f"     {ln['label']} ({num(s)} x {num(l_)} in{fin}), quantity {ln['qty']}, "
                   + ("cropped" if ln["cropped"] else "full frame"))
        out.append(f"     Photo: {ln['name']}")
        if ln.get("soft"):
            out.append("     May look soft at this size (confirmed)")
        n += ln["qty"]
    out += ["", f"Number of prints: {n}"]
    if order.get("note"):
        out += ["", "Note from the client:", "  " + order["note"].replace("\n", "\n  ")]
    return "\n".join(out) + "\n"

class _Sink:

    def __init__(self):
        self.buf, self.n = bytearray(), 0

    def write(self, b):
        self.buf += b
        self.n += len(b)
        return len(b)

    def tell(self):
        return self.n

    def flush(self):
        pass

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out

def stream_zip(entries, when=None):

    sink = _Sink()
    stamp = (when or datetime.now()).timetuple()[:6]
    z = zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED)
    for name, data in entries:
        if callable(data):
            data = data()
        z.writestr(zipfile.ZipInfo(name, date_time=stamp), data)
        del data
        yield sink.take()
    z.close()
    yield sink.take()

def ceil_div(a, b):
    return -(-a // b)

def round_days(seconds):
    return int(math.floor(seconds / 86400))

def seal_order(payload, spk, gid, link_key):

    import json
    import sealed
    body = dict(payload, pad="")
    raw = json.dumps(body, separators=(",", ":")).encode()
    over = 80 + len(gid.encode()) + 12 + 16
    size = pad_size(len(raw) + over)
    if size is None:
        raise ValueError("This order is too large to send at once; please split it")
    body["pad"] = " " * (size - over - len(raw))
    env = sealed.seal_studio("print-order", spk, json.dumps(body, separators=(",", ":")).encode(), gid=gid,
                             link_key=link_key)
    assert len(env) == size
    return env

def solve_work(env, bits=17):

    import hashlib
    inner, i = hashlib.sha256(env).digest(), 0
    while True:
        w = i.to_bytes(8, "big")
        if int.from_bytes(hashlib.sha256(inner + w).digest()[:4], "big") >> (32 - bits) == 0:
            return w.hex()
        i += 1
