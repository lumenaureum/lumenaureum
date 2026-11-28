import json
import re
from datetime import datetime

import config as C

FORMAT = 1
SETTING_KEYS = ("business_name", "photographer_name", "studio_name", "business_email", "business_phone", "business_address", "timezone",
                "home_zip", "home_lat", "home_lon", "mileage_rate", "quote_term_days", "tax_mode", "sales_tax_pct",
                "local_tax_pct", "gallery_base_url", "backup_time", "governing_law", "credit_off", "agreement_text", "release_text", "transfer_text", "license_text",
                "home_base", "business_tax_id", "bank_details", "pay_details", "age_majority", "logo_png",
                "logo_dark_png", "gallery_font", "gallery_bg", "proof_mark", "proof_mark_text", "proof_mark_strength",
                "business_website", "brand_font_400", "brand_font_300", "brand_font_name", "taxes", "deposit_word")
SECRET_FIELDS = ("secret_key", "password", "private_key")
STORAGE_PARTS = ("primary", "secondary", "gallery_host")

PIN_FIELDS = ("host_key", "known_hosts")
BACKEND_FIELDS = {"local": ("type", "path"), "sftp": ("type", "host", "user", "key_file", "root", "port", "auth"),
                  "s3": ("type", "bucket", "endpoint", "region", "access_key", "prefix", "addressing")}
FEE_UNITS = ("flat", "hour", "each", "mile", "person")
MAX_TEXT = 2000

GALLERY_FONTS = ("classic", "elegant", "script", "own")
FONT_MAX = 2 * 1024 * 1024

FONT_UNPACKED_MAX = 10 * 1024 * 1024
FONT_GLYPHS_MAX = 65535

FONT_TABLES = {"cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "glyf", "loca", "CFF ", "CFF2", "VORG",
               "GDEF", "GPOS", "GSUB", "kern", "gasp", "prep", "fpgm", "cvt ", "vhea", "vmtx", "fvar", "gvar", "avar",
               "cvar", "HVAR", "VVAR", "MVAR", "STAT"}

def _font_unpacked(data):

    import struct
    import zlib
    limit = FONT_UNPACKED_MAX
    if data[:4] == b"wOFF":
        if len(data) < 44:
            raise ValueError("not a font")
        num, total = struct.unpack(">H2xI", data[12:20])
        meta_off, meta_len, meta_orig = struct.unpack(">III", data[24:36])
        if total > limit or meta_orig > limit or len(data) < 44 + 20 * num:
            raise ValueError("too large unpacked")
        size = 0
        parts = [struct.unpack(">4xIII4x", data[44 + 20 * i:64 + 20 * i]) for i in range(num)]
        if meta_len:
            parts.append((meta_off, meta_len, meta_orig))
        for off, comp, orig in parts:
            size += orig
            if size > limit:
                raise ValueError("too large unpacked")
            if comp < orig:
                d = zlib.decompressobj()
                try:
                    got = len(d.decompress(data[off:off + comp], orig + 1))
                except zlib.error:
                    raise ValueError("not a font")
                if got > orig:
                    raise ValueError("too large unpacked")
        return size
    if data[:4] == b"wOF2":
        import brotli
        if len(data) < 48:
            raise ValueError("not a font")
        flavor, num, total, comp_total = data[4:8], *struct.unpack(">H2xII", data[12:24])
        meta_off, meta_len, meta_orig = struct.unpack(">III", data[28:40])
        if flavor == b"ttcf" or total > limit or meta_orig > limit:
            raise ValueError("not a usable font")
        pos, want = 48, 0

        def base128():
            nonlocal pos
            v = 0
            for _ in range(5):
                if pos >= len(data):
                    raise ValueError("not a font")
                b = data[pos]
                pos += 1
                v = (v << 7) | (b & 0x7f)
                if not b & 0x80:
                    return v
            raise ValueError("not a font")
        for _ in range(num):
            if pos >= len(data):
                raise ValueError("not a font")
            flags = data[pos]
            pos += 1
            known, version = flags & 0x3f, flags >> 6
            if known == 63:
                pos += 4
            orig = base128()
            transformed = version == 0 if known in (10, 11) else version != 0
            want += base128() if transformed else orig
            if want > limit:
                raise ValueError("too large unpacked")

        def unpack(blob, most):
            d, n, i = brotli.Decompressor(), 0, 0
            try:
                while True:
                    if d.can_accept_more_data():
                        if i >= len(blob):
                            break
                        n += len(d.process(blob[i:i + 4096], output_buffer_limit=1 << 20))
                        i += 4096
                    else:
                        n += len(d.process(b"", output_buffer_limit=1 << 20))
                    if n > most:
                        raise ValueError("too large unpacked")
            except brotli.error:
                raise ValueError("not a font")
            return n
        got = unpack(data[pos:pos + comp_total], want)
        if meta_len:
            got += unpack(data[meta_off:meta_off + meta_len], meta_orig)
        return got
    return len(data)

def clean_font(data):

    import io
    if not data or len(data) > FONT_MAX or data[:4] not in (b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"true", b"OTTO"):
        raise ValueError("not a font")
    from fontTools.ttLib import TTFont
    try:
        if _font_unpacked(data) > FONT_UNPACKED_MAX:
            raise ValueError("too large unpacked")
        f = TTFont(io.BytesIO(data))
        if "name" not in f or "cmap" not in f or "glyf" not in f and "CFF " not in f and "CFF2" not in f:
            raise ValueError("not a usable font")
        if f["maxp"].numGlyphs > FONT_GLYPHS_MAX or len(f.getGlyphOrder()) > FONT_GLYPHS_MAX:
            raise ValueError("too many glyphs")
        fam = f["name"].getDebugName(16) or f["name"].getDebugName(1) or ""
        for tag in [t_ for t_ in f.keys() if t_ != "GlyphOrder" and t_ not in FONT_TABLES]:
            del f[tag]
        f.flavor = "woff2"
        out = io.BytesIO()
        f.save(out)
    except ValueError:
        raise
    except Exception:
        raise ValueError("not a usable font")
    if len(out.getvalue()) > FONT_MAX:
        raise ValueError("too large")
    return out.getvalue(), re.sub(r"[^\w .'-]", "", fam).strip()[:60]

def font_weights(data, default):

    import io
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(io.BytesIO(data), lazy=True)
        if "fvar" in f:
            for ax in f["fvar"].axes:
                if ax.axisTag == "wght":
                    lo, hi = int(round(ax.minValue)), int(round(ax.maxValue))
                    if 1 <= lo <= hi <= 1000:
                        return f"{lo} {hi}"
    except Exception:
        pass
    return str(default)

def own_font_weights(regular, light):

    wr = font_weights(regular, 400)
    if not light:
        return wr, None
    wl = font_weights(light, 300)
    lo, _, hi = wr.partition(" ")
    if hi and int(lo) < 400 <= int(hi):
        wr = "400" if int(hi) == 400 else f"400 {hi}"
    lo, _, hi = wl.partition(" ")
    if hi and int(lo) < 400 <= int(hi):
        wl = f"{lo} 399"
    return wr, wl

GALLERY_BG = "#f6f3ed"
PROOF_MARKS = ("", "tiled", "corner")
PALETTES = {
    "#f6f3ed": dict(bg="#f6f3ed", ink="#1f1b16", mute="#756b60", line="#e2dbcf", cell="#e9e3d8", edge="#cfc5b4"),
    "#ffffff": dict(bg="#ffffff", ink="#1b1b1b", mute="#6f6f6f", line="#e6e6e6", cell="#f0f0f0", edge="#cfcfcf"),
    "#262422": dict(bg="#262422", ink="#eee7dc", mute="#a59b8f", line="#3a3631", cell="#33302c", edge="#57514a"),
    "#0d0c0b": dict(bg="#0d0c0b", ink="#ece6dc", mute="#968d82", line="#24211e", cell="#1a1816", edge="#46413b"),
}

def site_problem(value):

    return ("it must start with https://" if re.match(r"\s*http://", value or "", re.I)
            else "that isn't a web address")

def clean_site(value):

    from urllib.parse import urlsplit
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) > 200 or re.search(r"[\s\"'<>\\\x00-\x1f\x7f]", v):
        return None
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*)://", v)
    if m and m.group(1).lower() != "https":
        return None
    if not m and re.match(r"[A-Za-z][A-Za-z0-9+.-]*:(?!\d)", v):
        return None
    rest = v[m.end():] if m else v
    try:
        u = urlsplit("https://" + rest)
        port = u.port
    except ValueError:
        return None
    host = u.hostname or ""
    labels = host.split(".")
    if u.username or u.password or len(labels) < 2 or not all(
            re.fullmatch(r"[^\W_](?:[\w-]*[^\W_])?", l_) for l_ in labels) or not re.fullmatch(r"[^\W\d_]{2,}|xn--[a-z0-9-]+", labels[-1]):
        return None
    return "https://" + host + (f":{port}" if port else "") + u.path + (f"?{u.query}" if u.query else "") \
        + (f"#{u.fragment}" if u.fragment else "")

def clean_look(font, bg, mark, text, strength):

    font = (font or "").strip().lower()
    bg = (bg or "").strip().lower()
    mark = (mark or "").strip().lower()
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u200b\u200e\u200f\u2028-\u202e\u2060-\u206f\ufeff]", "", str(text or ""))
    text = " ".join(text.split())[:20].strip()
    try:
        n = int(float(str(strength).strip()))
    except (TypeError, ValueError, OverflowError):
        n = 20
    return dict(gallery_font=font if font in GALLERY_FONTS else "classic",
                gallery_bg=bg if re.fullmatch(r"#[0-9a-f]{6}", bg) else GALLERY_BG,
                proof_mark=mark if mark in PROOF_MARKS else "",
                proof_mark_text=text or "PROOF",
                proof_mark_strength=str(max(5, min(100, n))))

def _rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]

def _lum(c):

    f = [v / 255 for v in c]
    f = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in f]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

def _mix(a, b, t):

    return [int(a[i] + (b[i] - a[i]) * t + 0.5) for i in range(3)]

def _hex(c):
    return "#" + "".join(f"{v:02x}" for v in c)

def palette(bg):

    bg = clean_look("", bg, "", "", 20)["gallery_bg"]
    c = _rgb(bg)
    dark = _contrast(c, [0, 0, 0]) >= _contrast(c, [255, 255, 255])
    to = [20, 17, 14] if dark else [250, 246, 240]
    ring = _ring(c, to)
    if bg in PALETTES:
        return dict(PALETTES[bg], ring=ring)

    def reach(start, want, most):
        k = start
        while k < most and _contrast(c, _mix(c, to, k / 100)) < want:
            k += 1
        return _mix(c, to, k / 100)
    ink = reach(90, 7, 100)
    if _contrast(c, ink) < 4.5:
        ink = [0, 0, 0] if dark else [255, 255, 255]
    mute = reach(50, 4.5, 100)
    if _contrast(c, mute) < 4.5 or _contrast(c, mute) > _contrast(c, ink):
        mute = ink
    return dict(bg=bg, ink=_hex(ink), mute=_hex(mute), line=_hex(_mix(c, to, 0.1)),
                cell=_hex(_mix(c, to, 0.06)), edge=_hex(_mix(c, to, 0.22)), ring=ring)

GOLD = [184, 134, 47]

def _ring(c, to):

    k = 0
    while k < 100 and _contrast(c, _mix(GOLD, to, k / 100)) < 3:
        k += 1
    return _hex(_mix(GOLD, to, k / 100))

def is_dark(bg):

    c = _rgb(clean_look("", bg, "", "", 20)["gallery_bg"])
    return _contrast(c, [0, 0, 0]) < _contrast(c, [255, 255, 255])

LOGO_MAX_PIXELS = 40_000_000
LOGO_BOX = (600, 240)

def clean_logo(data):

    import io
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ("PNG", "JPEG") or im.width * im.height > LOGO_MAX_PIXELS:
            raise ValueError("not a logo")
        im = ImageOps.exif_transpose(im).convert("RGBA")
        im.thumbnail(LOGO_BOX, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("not a logo") from e
    return buf.getvalue()

def _small_logo(im):

    return im.width * im.height <= 4 * LOGO_BOX[0] * LOGO_BOX[1]

def white_logo(b64):

    import base64
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        if not _small_logo(im):
            return b64
        im = im.convert("RGBA")
    except Exception:
        return b64
    a = im.getchannel("A")
    if a.getextrema()[0] >= 250:
        return b64
    out = Image.new("RGBA", im.size, (255, 255, 255, 0))
    out.putalpha(a)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()

def gallery_logo(bg, logo, logo_dark):

    if not is_dark(bg):
        return logo or ""
    return logo_dark or (white_logo(logo) if logo else "")

class ImportProblem(ValueError):
    pass

def export(get_setting, fee_items, storage_cfg, plugins=None):
    settings = {k: get_setting(k) for k in SETTING_KEYS if get_setting(k) is not None}
    storage = {}
    for part in STORAGE_PARTS:
        cfg = storage_cfg.get(part)
        if cfg:
            storage[part] = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS and k not in PIN_FIELDS}
    return {"lumen_aureum_settings": FORMAT, "exported": datetime.now().isoformat(timespec="seconds"),
            "settings": settings, "rules": C.current_rules(), "fees": fee_items, "storage": storage,
            **({"plugins": plugins} if plugins else {})}

def plugin_parts(text):

    try:
        raw = json.loads(text).get("plugins")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and re.fullmatch(r"[a-z0-9_]{1,40}", k)}

def _text(v, what, limit=MAX_TEXT):
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ImportProblem(f"{what}: not a plain value")
    v = str(v)
    if len(v) > limit:
        raise ImportProblem(f"{what}: too long")
    return v

def _rules(raw):
    if not isinstance(raw, dict):
        raise ImportProblem("rules: not a list of settings")
    out = {}
    for k, v in raw.items():
        if k not in C.RULE_KEYS:
            continue
        if k == "TYPES":
            out[k] = _types(v)
            continue
        if k == "DISCOUNTS":
            out[k] = _discounts(v)
            continue
        if k == "OFFER_RETOUCH":
            if not isinstance(v, bool):
                raise ImportProblem(f"rules.{k}: expected true or false")
            out[k] = v
            continue
        if k in ("NET_TERMS_TYPES", "LICENSE_TYPES", "OFFERED_TYPES", "PAYMENT_METHODS", "BEFORE_TYPES"):
            if not isinstance(v, list) or not all(isinstance(x, str) and len(x) <= 60 for x in v) or len(v) > 50:
                raise ImportProblem(f"rules.{k}: expected a short list of names")
            if k != "PAYMENT_METHODS":
                v = [x for x in v if re.fullmatch(r"[a-z0-9_]{1,30}", x)]
        else:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v > 100000:
                raise ImportProblem(f"rules.{k}: expected a number")
        out[k] = v
    return out

def _types(raw):
    if not isinstance(raw, list) or not raw or len(raw) > 40:
        raise ImportProblem("rules.TYPES: expected a list of session types")
    out, keys = [], set()
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ImportProblem(f"rules.TYPES[{i}]: not a session type")
        key = _text(t.get("key", ""), f"rules.TYPES[{i}].key", 30)
        if not re.fullmatch(r"[a-z0-9_]+", key) or key in keys:
            raise ImportProblem(f"rules.TYPES[{i}].key: letters, digits, and _ only, each once")
        keys.add(key)
        if t.get("kind") not in C.KINDS:
            raise ImportProblem(f"rules.TYPES[{i}].kind: one of {', '.join(C.KINDS)}")
        mins = t.get("minutes", 60)
        if not isinstance(mins, int) or isinstance(mins, bool) or not 5 <= mins <= 1440:
            raise ImportProblem(f"rules.TYPES[{i}].minutes: 5 to 1440")
        intake = _questions(t.get("intake", []), f"rules.TYPES[{i}].intake")
        clean = dict(key=key, label=_text(t.get("label", key), f"rules.TYPES[{i}].label", 60), kind=t["kind"],
                     minutes=mins, grad=bool(t.get("grad")), intake=intake)
        for flag in ("license", "animals", "all_sign", "indoors", "baby", "products"):
            if t.get(flag):
                clean[flag] = True
        if t.get("questions"):
            clean["questions"] = _questions(t["questions"], f"rules.TYPES[{i}].questions")
        if t.get("steps"):
            clean["steps"] = _steps(t["steps"], f"rules.TYPES[{i}].steps")
        if t.get("steps_off"):
            clean["steps_off"] = _steps_off(t["steps_off"], f"rules.TYPES[{i}].steps_off")
        if t.get("policy"):
            clean["policy"] = _policy(t["policy"], f"rules.TYPES[{i}].policy")
        for k_ in ("portfolio_pct", "portfolio_amount"):
            if t.get(k_) is not None:
                v_ = t[k_]
                if not isinstance(v_, int) or isinstance(v_, bool) or not 0 <= v_ <= (100 if k_ == "portfolio_pct" else 100000):
                    raise ImportProblem(f"rules.TYPES[{i}].{k_}: a whole number in range")
                clean[k_] = v_
        for k_ in ("deposit_pct", "deposit_amount"):
            if t.get(k_) is not None:
                v_ = t[k_]
                if not isinstance(v_, int) or isinstance(v_, bool) or not 0 <= v_ <= (100 if k_ == "deposit_pct" else 100000):
                    raise ImportProblem(f"rules.TYPES[{i}].{k_}: a whole number in range")
                clean[k_] = v_
        for doc in ("agreement_text", "release_text", "transfer_text", "license_text"):
            if (t.get(doc) or "").strip():
                clean[doc] = _text(t[doc], f"rules.TYPES[{i}].{doc}", 20000)
        out.append(clean)
    return out

RESERVED_QUESTION_KEYS = {"back", "csrf", "csrf_token", "_csrf", "submit", "action"}

def _questions(raw, where):

    if not isinstance(raw, list) or len(raw) > 20 or not all(
            isinstance(q, (list, tuple)) and len(q) == 2 and re.fullmatch(r"[a-z0-9_]{1,30}", str(q[0]))
            and isinstance(q[1], str) and 0 < len(q[1].strip()) <= 120 for q in raw):
        raise ImportProblem(f"{where}: a short list of [key, question]")
    out, seen = [], set()
    for a, b in raw:
        if a in RESERVED_QUESTION_KEYS:
            raise ImportProblem(f"{where}: {a} is not allowed as a question key")
        if a in seen:
            raise ImportProblem(f"{where}: each key once")
        seen.add(a)
        out.append([str(a), _text(b, where, 120)])
    return out

def _steps(raw, where):

    if not isinstance(raw, dict) or not set(raw) <= set(C.STEP_STAGES):
        raise ImportProblem(f"{where}: stages are {', '.join(C.STEP_STAGES)}")
    out = {}
    for stage, steps in raw.items():
        if not isinstance(steps, list) or len(steps) > 30:
            raise ImportProblem(f"{where}.{stage}: a short list of steps")
        rows = []
        for j, st in enumerate(steps):
            w = f"{where}.{stage}[{j}]"
            if not isinstance(st, list) or not st or st[0] not in ("do", "event", "auto"):
                raise ImportProblem(f"{w}: [\"do\", label, days] or [\"auto\", step]")
            if st[0] == "auto":
                if len(st) != 2 or st[1] not in C.AUTO_KEYS:
                    raise ImportProblem(f"{w}: automatic steps are {', '.join(C.AUTO_KEYS)}")
                rows.append(["auto", st[1]])
            else:
                days = st[2] if len(st) > 2 else None
                if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not -365 <= days <= 365):
                    raise ImportProblem(f"{w}: days from the shoot, -365 to 365, or null")
                rows.append([st[0], _text(st[1] if len(st) > 1 else "", w, 80), days])
        out[stage] = rows
    return out

def _steps_off(raw, where):

    if not isinstance(raw, dict) or not set(raw) <= set(C.STEP_STAGES):
        raise ImportProblem(f"{where}: stages are {', '.join(C.STEP_STAGES)}")
    out = {}
    for stage, ids in raw.items():
        if not isinstance(ids, list) or len(ids) > 30:
            raise ImportProblem(f"{where}.{stage}: a short list of steps")
        rows = []
        for j, st in enumerate(ids):
            w = f"{where}.{stage}[{j}]"
            if not isinstance(st, list) or len(st) != 2 or st[0] not in ("do", "auto"):
                raise ImportProblem(f"{w}: [\"do\", label] or [\"auto\", step]")
            if st[0] == "auto" and st[1] not in C.AUTO_KEYS:
                raise ImportProblem(f"{w}: automatic steps are {', '.join(C.AUTO_KEYS)}")
            rows.append([st[0], st[1] if st[0] == "auto" else _text(st[1], w, 80)])
        if rows:
            out[stage] = rows
    return out

def _policy(raw, where):
    if not isinstance(raw, dict):
        raise ImportProblem(f"{where}: not a policy")
    out = {}
    for k, v in raw.items():
        if k in ("free_reschedule", "outfits", "gallery_codeword"):
            out[k] = bool(v)
        elif k == "delivery_method":
            if v not in C.DELIVERY_METHODS:
                raise ImportProblem(f"{where}.{k}: one of {', '.join(C.DELIVERY_METHODS)}")
            out[k] = v
        elif k in {key for key, _, _ in C.FILL_INS} | {"wear", "keep_raw"}:
            out[k] = _text(v, f"{where}.{k}", 200)
        elif k in ("keep_raw_days", "gallery_days", "delivery_days"):
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 3650:
                raise ImportProblem(f"{where}.{k}: a whole number of days")
            out[k] = v
        elif k in ("notice_hours", "late_minutes"):
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 2000:
                raise ImportProblem(f"{where}.{k}: a whole number")
            out[k] = v
    return out

def _discounts(raw):
    if not isinstance(raw, list) or len(raw) > 30:
        raise ImportProblem("rules.DISCOUNTS: expected a list")
    out = []
    for i, d in enumerate(raw):
        if not (isinstance(d, (list, tuple)) and len(d) == 3 and re.fullmatch(r"[a-z0-9_]{1,30}", str(d[0]))
                and isinstance(d[2], (int, float)) and not isinstance(d[2], bool) and 0 <= d[2] <= 100):
            raise ImportProblem(f"rules.DISCOUNTS[{i}]: expected [key, label, percent]")
        out.append([str(d[0]), _text(d[1], f"rules.DISCOUNTS[{i}].label", 80), d[2]])
    return out

def _fees(raw):
    if not isinstance(raw, list) or not raw or len(raw) > 200:
        raise ImportProblem("fees: expected a list of prices")
    out, keys = [], set()
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            raise ImportProblem(f"fees[{i}]: not a price")
        key = _text(it.get("key", ""), f"fees[{i}].key", 40)
        if not re.fullmatch(r"[a-z0-9_]+", key) or key in keys:
            raise ImportProblem(f"fees[{i}].key: letters, digits, and _ only, each once")
        keys.add(key)
        amt = it.get("amount", 0)
        if not isinstance(amt, (int, float)) or isinstance(amt, bool) or not 0 <= amt <= 1000000:
            raise ImportProblem(f"fees[{i}].amount: expected a number")
        unit = it.get("unit", "flat")
        if unit not in FEE_UNITS:
            raise ImportProblem(f"fees[{i}].unit: one of {', '.join(FEE_UNITS)}")
        item = dict(key=key, label=_text(it.get("label", key), f"fees[{i}].label", 200), amount=amt, unit=unit)
        if "type" in it:
            item["type"] = _text(it["type"], f"fees[{i}].type", 30)
        if "minutes" in it and isinstance(it["minutes"], int) and not isinstance(it["minutes"], bool) and 0 < it["minutes"] <= 1440:
            item["minutes"] = it["minutes"]
        for flag in ("taxable", "passthrough"):
            if flag in it:
                item[flag] = bool(it[flag])
        if it.get("untaxed"):
            if not isinstance(it["untaxed"], list) or not all(isinstance(k_, str) and re.fullmatch(r"[a-z0-9_]{1,20}", k_)
                                                              for k_ in it["untaxed"]):
                raise ImportProblem(f"fees[{i}].untaxed: a list of tax keys")
            item["untaxed"] = list(dict.fromkeys(it["untaxed"]))[:20]
        if "min" in it and isinstance(it["min"], (int, float)) and not isinstance(it["min"], bool):
            item["min"] = it["min"]
        out.append(item)
    return out

def check_taxes(raw):

    try:
        items = json.loads(raw or "[]")
    except ValueError:
        raise ImportProblem("settings.taxes: not readable")
    if not isinstance(items, list) or len(items) > 10:
        raise ImportProblem("settings.taxes: a list of up to 10 taxes")
    out, keys, names = [], set(), set()
    for i, t_ in enumerate(items):
        if not isinstance(t_, dict):
            raise ImportProblem(f"settings.taxes[{i}]: not a tax")
        key = t_.get("key")
        name = _text(t_.get("name", ""), f"settings.taxes[{i}].name", 40).strip()
        rate = t_.get("rate")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]{1,20}", key) or key in keys:
            raise ImportProblem(f"settings.taxes[{i}].key: letters, digits, and _ only, each once")
        if not name or name.lower() in names:
            raise ImportProblem(f"settings.taxes[{i}].name: a name, each once")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0 <= rate <= 25:
            raise ImportProblem(f"settings.taxes[{i}].rate: a percentage from 0 to 25")
        keys.add(key)
        names.add(name.lower())
        out.append(dict(key=key, name=name, rate=float(rate), included=bool(t_.get("included")),
                        default=bool(t_.get("default", True))))
    return json.dumps(out)

def _storage(raw, current):
    if not isinstance(raw, dict):
        raise ImportProblem("storage: not a list of locations")
    out = {}
    for part in STORAGE_PARTS:
        cfg = raw.get(part)
        if not cfg:
            continue
        if not isinstance(cfg, dict) or cfg.get("type") not in BACKEND_FIELDS:
            raise ImportProblem(f"storage.{part}: unknown kind of storage")
        clean = {}
        for k, v in cfg.items():
            if k not in BACKEND_FIELDS[cfg["type"]]:
                continue
            if k == "port":
                if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 65535:
                    raise ImportProblem(f"storage.{part}.port: a port number, 1 to 65535")
                clean[k] = v
            else:
                clean[k] = _text(v, f"storage.{part}.{k}", 2000)
        for k in ("path", "key_file"):
            if clean.get(k) and _inside_private(clean[k]):
                raise ImportProblem(f"storage.{part}.{k}: can't be inside Lumen Aureum's data folder or program")
        if clean.get("type") == "s3" and clean.get("endpoint"):
            from vault import s3 as S3
            why = S3.endpoint_problem(clean["endpoint"])
            if why:
                raise ImportProblem(f"storage.{part}.endpoint: {why}")
        if clean.get("type") == "s3" and clean.get("addressing", "auto") not in ("auto", "path", "virtual"):
            raise ImportProblem(f"storage.{part}.addressing: auto, path, or virtual")
        if clean.get("type") == "sftp" and clean.get("auth", "password") != "password":
            raise ImportProblem(f"storage.{part}.auth: password, or left out for a key")
        if clean.get("auth") == "password" and part != "gallery_host":
            raise ImportProblem(f"storage.{part}.auth: a password sign-in is for a web host only")
        from vault import backends as B
        if clean.get("type") == "sftp":

            mine = current.get(part) or {}
            if B.same_account(mine, clean):
                clean.update({k: mine[k] for k in PIN_FIELDS if mine.get(k)})

        out[part] = B.carry_secrets(current.get(part) or {}, clean, SECRET_FIELDS)
    return out

def _inside_private(path):

    import os
    real = os.path.realpath(path)
    data = os.path.realpath(os.path.dirname(os.environ.get("CRM_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crm.db"))))
    prog = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    private = [os.path.join(data, n) for n in ("keyring.json", "tls", "crm.db", "crm.db-wal", "crm.db-shm", "faces",
                                                "SETUP-CODE.txt", "RECOVERY-CODE.txt", "storage.json", "unlock.key",
                                                "known-browsers.json")]
    under = lambda d: real == d or real.startswith(d.rstrip("/") + "/")
    return real == data or under(prog) or any(under(p_) for p_ in private)

FONT_TEXT_MAX = 3_000_000
LOGO_TEXT_MAX = 600_000

FILE_MAX = 2 * FONT_TEXT_MAX + 2 * LOGO_TEXT_MAX + 4_000_000

def parse(text, current_storage):

    try:
        data = json.loads(text)
    except ValueError:
        raise ImportProblem("That isn't a Lumen Aureum settings file (not readable as JSON).")
    if not isinstance(data, dict) or data.get("lumen_aureum_settings") != FORMAT:
        raise ImportProblem("That isn't a Lumen Aureum settings file.")
    raw = data.get("settings") or {}
    if not isinstance(raw, dict):
        raise ImportProblem("settings: not a list of settings")
    settings = {k: _text(v, k, 20000 if k.endswith("_text") else LOGO_TEXT_MAX if k in ("logo_png", "logo_dark_png")
                     else FONT_TEXT_MAX if k in ("brand_font_400", "brand_font_300") else MAX_TEXT)
                for k, v in raw.items() if k in SETTING_KEYS}
    for k_ in ("sales_tax_pct", "local_tax_pct", "mileage_rate", "home_lat", "home_lon", "quote_term_days", "age_majority"):
        v_ = (settings.get(k_) or "").strip()
        if v_:
            try:
                n_ = float(v_)
            except ValueError:
                raise ImportProblem(f"settings.{k_}: a number, not {v_[:20]!r}")
            if n_ != n_ or n_ in (float("inf"), float("-inf")) or n_ < -360 or n_ > 100000:
                raise ImportProblem(f"settings.{k_}: out of range")
    if "deposit_word" in settings:
        import config as _C
        settings["deposit_word"] = _C.clean_deposit_word(settings["deposit_word"])
    if "taxes" in settings:
        settings["taxes"] = check_taxes(settings["taxes"])
    elif "tax_mode" in settings:
        try:
            rate_ = min(25.0, max(0.0, float(settings.get("sales_tax_pct") or 0)))
        except ValueError:
            rate_ = 0.0
        settings["taxes"] = json.dumps([dict(key="sales", name="Sales tax", rate=rate_, included=False, default=True)]
                                       if settings["tax_mode"] != "none" and rate_ else [])
    for lk_ in ("logo_png", "logo_dark_png"):
        if settings.get(lk_):
            import base64
            import binascii
            try:
                settings[lk_] = base64.b64encode(clean_logo(base64.b64decode(settings[lk_], validate=True))).decode()
            except (binascii.Error, ValueError):
                raise ImportProblem(f"settings.{lk_}: not a PNG or JPEG picture this can use")
    for fk_ in ("brand_font_400", "brand_font_300"):
        if settings.get(fk_):
            import base64
            import binascii
            try:
                settings[fk_] = base64.b64encode(clean_font(base64.b64decode(settings[fk_], validate=True))[0]).decode()
            except (binascii.Error, ValueError):
                raise ImportProblem(f"settings.{fk_}: not a font file this can use")
    if settings.get("brand_font_name"):
        settings["brand_font_name"] = re.sub(r"[^\w .'-]", "", settings["brand_font_name"]).strip()[:60]
    if settings.get("gallery_font") == "own" and not settings.get("brand_font_400"):
        settings["gallery_font"] = "classic"
    look = clean_look(settings.get("gallery_font"), settings.get("gallery_bg"), settings.get("proof_mark"),
                      settings.get("proof_mark_text"), settings.get("proof_mark_strength"))
    settings.update({k: v for k, v in look.items() if k in settings})
    if settings.get("gallery_base_url") and not settings["gallery_base_url"].startswith("https://"):
        raise ImportProblem("gallery_base_url: must start with https://")
    if "business_website" in settings:
        site = clean_site(settings["business_website"])
        if site is None:
            raise ImportProblem(("the website in this file must start with https://" if site_problem(settings["business_website"])
                                 .startswith("it must") else "the website in this file isn't a web address")
                                + ". Fix it in the file, or remove it.")
        settings["business_website"] = site
    rules = _rules(data.get("rules") or {})
    fees = _fees(data["fees"]) if data.get("fees") else None
    storage = _storage(data.get("storage") or {}, current_storage)
    return settings, rules, fees, storage
