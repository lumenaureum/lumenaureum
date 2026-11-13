"""Export and import settings: everything that describes the business, so a fresh install (or a second machine) can
start where another left off. Never client records, never secrets.

The file is plain JSON:
  {"lumen_aureum_settings": 1, "exported": ISO time, "settings": {...}, "rules": {...}, "fees": [...],
   "storage": {"primary": {...}, "secondary": {...}, "gallery_host": {...}}}
Secret keys (S3 secret_key) are left out on export and kept as they are on import. Import accepts only the keys listed
here, checks every value, adds the prices as a new price-list version (quotes already sent keep theirs), and records
the import in the history."""
import json
import re
from datetime import datetime

import config as C

FORMAT = 1
SETTING_KEYS = ("business_name", "photographer_name", "studio_name", "business_email", "business_phone", "business_address", "timezone",
                "home_zip", "home_lat", "home_lon", "mileage_rate", "quote_term_days", "tax_mode", "sales_tax_pct",
                "local_tax_pct", "gallery_base_url", "backup_time", "governing_law", "credit_off", "agreement_text", "release_text", "transfer_text", "license_text",
                "home_base", "business_tax_id", "bank_details", "pay_details", "age_majority", "logo_png")
SECRET_FIELDS = ("secret_key", "password", "private_key")
STORAGE_PARTS = ("primary", "secondary", "gallery_host")
BACKEND_FIELDS = {"local": ("type", "path"), "sftp": ("type", "host", "user", "key_file", "root", "port", "known_hosts", "host_key"),
                  "s3": ("type", "bucket", "endpoint", "region", "access_key", "prefix")}
FEE_UNITS = ("flat", "hour", "each", "mile", "person")
MAX_TEXT = 2000


class ImportProblem(ValueError):
    pass


def export(get_setting, fee_items, storage_cfg):
    settings = {k: get_setting(k) for k in SETTING_KEYS if get_setting(k) is not None}   # "" too: an import clears it
    storage = {}
    for part in STORAGE_PARTS:
        cfg = storage_cfg.get(part)
        if cfg:
            storage[part] = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
    return {"lumen_aureum_settings": FORMAT, "exported": datetime.now().isoformat(timespec="seconds"),
            "settings": settings, "rules": C.current_rules(), "fees": fee_items, "storage": storage}


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
            continue                                   # a newer or older version's rule: ignored, not an error
        if k == "TYPES":
            out[k] = _types(v)
            continue
        if k == "DISCOUNTS":
            out[k] = _discounts(v)
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
            raise ImportProblem(f"rules.TYPES[{i}].key: letters, digits and _ only, each once")
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
        if t.get("policy"):
            clean["policy"] = _policy(t["policy"], f"rules.TYPES[{i}].policy")
        for k_ in ("portfolio_pct", "portfolio_amount"):         # the portfolio permission discount
            if t.get(k_) is not None:
                v_ = t[k_]
                if not isinstance(v_, int) or isinstance(v_, bool) or not 0 <= v_ <= (100 if k_ == "portfolio_pct" else 100000):
                    raise ImportProblem(f"rules.TYPES[{i}].{k_}: a whole number in range")
                clean[k_] = v_
        for k_ in ("deposit_pct", "deposit_amount"):            # the type's own deposit
            if t.get(k_) is not None:
                v_ = t[k_]
                if not isinstance(v_, int) or isinstance(v_, bool) or not 0 <= v_ <= (100 if k_ == "deposit_pct" else 100000):
                    raise ImportProblem(f"rules.TYPES[{i}].{k_}: a whole number in range")
                clean[k_] = v_
        for doc in ("agreement_text", "release_text", "transfer_text", "license_text"):   # the type's own wording
            if (t.get(doc) or "").strip():
                clean[doc] = _text(t[doc], f"rules.TYPES[{i}].{doc}", 20000)
        out.append(clean)
    return out


RESERVED_QUESTION_KEYS = {"back", "csrf", "csrf_token", "_csrf", "submit", "action"}   # form fields of the intake page


def _questions(raw, where):
    """Intake questions: a short list of [key, question]."""
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
    """A type's own steps per stage: ["do"|"event", label, days or null] or ["auto", one of the automatic steps]."""
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
            raise ImportProblem(f"fees[{i}].key: letters, digits and _ only, each once")
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
        if "min" in it and isinstance(it["min"], (int, float)) and not isinstance(it["min"], bool):
            item["min"] = it["min"]
        out.append(item)
    return out


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
        for k in ("path", "key_file", "known_hosts"):        # never the app's own data folder or code
            if clean.get(k) and _inside_private(clean[k]):
                raise ImportProblem(f"storage.{part}.{k}: can't be inside Lumen Aureum's data folder or program")
        if clean.get("type") == "s3" and clean.get("endpoint") and not clean["endpoint"].startswith("https://"):
            raise ImportProblem(f"storage.{part}.endpoint: must start with https://")
        old = current.get(part) or {}
        if old.get("type") == clean.get("type"):
            clean.update({k: old[k] for k in SECRET_FIELDS if k in old})   # secrets are never in the file: keep ours
        out[part] = clean
    return out


def _inside_private(path):
    """True for the data folder itself, its keyring, certificate, database, faces, codes or storage file, or anything
    in the program folder: a backup target or SSH key path must never point there."""
    import os
    real = os.path.realpath(path)
    data = os.path.realpath(os.path.dirname(os.environ.get("CRM_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crm.db"))))
    prog = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    private = [os.path.join(data, n) for n in ("keyring.json", "tls", "crm.db", "crm.db-wal", "crm.db-shm", "faces",
                                                "SETUP-CODE.txt", "RECOVERY-CODE.txt", "storage.json", "unlock.key")]
    under = lambda d: real == d or real.startswith(d.rstrip("/") + "/")
    return real == data or under(prog) or any(under(p_) for p_ in private)


def parse(text, current_storage):
    """Check an export file. Returns (settings, rules, fees, storage) ready to apply, or raises ImportProblem."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ImportProblem("That isn't a Lumen Aureum settings file (not readable as JSON).")
    if not isinstance(data, dict) or data.get("lumen_aureum_settings") != FORMAT:
        raise ImportProblem("That isn't a Lumen Aureum settings file.")
    raw = data.get("settings") or {}
    if not isinstance(raw, dict):
        raise ImportProblem("settings: not a list of settings")
    settings = {k: _text(v, k, 20000 if k.endswith("_text") else 600000 if k == "logo_png" else MAX_TEXT)
                for k, v in raw.items() if k in SETTING_KEYS}
    for k_ in ("sales_tax_pct", "local_tax_pct", "mileage_rate", "home_lat", "home_lon", "quote_term_days", "age_majority"):
        v_ = (settings.get(k_) or "").strip()
        if v_:                                   # numbers must be numbers (a bad rate silently dropped sales tax)
            try:
                n_ = float(v_)
            except ValueError:
                raise ImportProblem(f"settings.{k_}: a number, not {v_[:20]!r}")
            if n_ != n_ or n_ in (float("inf"), float("-inf")) or n_ < -360 or n_ > 100000:
                raise ImportProblem(f"settings.{k_}: out of range")
    if settings.get("logo_png"):                 # a logo must be a real PNG; anything else is refused
        import base64
        import binascii
        try:
            ok = base64.b64decode(settings["logo_png"], validate=True)[:8] == b"\x89PNG\r\n\x1a\n"
        except (binascii.Error, ValueError):
            ok = False
        if not ok:
            raise ImportProblem("settings.logo_png: not a PNG picture")
    if settings.get("gallery_base_url") and not settings["gallery_base_url"].startswith("https://"):
        raise ImportProblem("gallery_base_url: must start with https://")
    rules = _rules(data.get("rules") or {})
    fees = _fees(data["fees"]) if data.get("fees") else None
    storage = _storage(data.get("storage") or {}, current_storage)
    return settings, rules, fees, storage
