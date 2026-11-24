import csv
import io
import re
from datetime import date

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLS = 60

FIELDS = [
    ("client", "Client name"),
    ("company", "Company"),
    ("address", "Address"),
    ("client_notes", "Client notes"),
    ("person", "Person's name"),
    ("relationship", "Relationship"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("birth_date", "Birth date"),
    ("minor", "Minor"),
    ("person_notes", "Person notes"),
    ("client_number", "Client number"),
]
FIELD_LABELS = dict(FIELDS)
LEAVE_OUT = ""

NAMES = {
    "client": {"client", "clientname", "household", "householdname", "family", "account", "accountname"},
    "company": {"company", "companyname", "business", "businessname", "organization", "brokerage"},
    "address": {"address", "mailingaddress", "homeaddress", "streetaddress", "street", "address1", "address2",
                "city", "town", "state", "province", "zip", "zipcode", "postalcode", "country"},
    "client_notes": {"notes", "note", "clientnotes", "comments", "comment"},
    "person": {"person", "name", "fullname", "contact", "contactname", "personname", "firstname", "first",
               "givenname", "middlename", "lastname", "last", "surname", "familyname"},
    "relationship": {"relationship", "relation", "relationshiptoclient"},
    "email": {"email", "emailaddress", "mail", "email1", "email1value"},
    "phone": {"phone", "phonenumber", "mobile", "mobilephone", "cell", "cellphone", "telephone", "tel", "homephone",
              "workphone", "phone1value"},
    "birth_date": {"birthdate", "birthday", "dob", "dateofbirth", "born", "duedate"},
    "minor": {"minor", "isminor", "under18"},
    "person_notes": {"personnotes"},
    "client_number": {"clientnumber", "clientid", "clientno"},
}
JOIN = {"person": " ", "address": ", ", "client_notes": "\n", "person_notes": "\n", "client": " ", "company": " "}
CAP = {"client_notes": 4000, "person_notes": 2000, "address": 500}
YES = {"yes", "y", "true", "1", "x", "minor"}
NO = {"no", "n", "false", "0", "adult"}

class ImportProblem(Exception):
    pass

def norm_header(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())

def guess(header):

    out = []
    for h in header:
        n_ = norm_header(h)
        k_ = next((k for k, words in NAMES.items() if n_ in words), None)
        if k_ is None and n_:
            for words, key in ((("email", "mail"), "email"), (("phone", "mobile", "cell"), "phone"),
                               (("birth",), "birth_date"), (("address", "street", "zip", "postal"), "address"),
                               (("company", "business"), "company"), (("relation",), "relationship"),
                               (("note", "comment"), "client_notes"), (("name",), "person")):
                if any(w in n_ for w in words):
                    k_ = key
                    break
        out.append(k_ or LEAVE_OUT)
    return out

def decode(raw):

    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "UTF-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "UTF-16"
    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), "Windows (Latin-1)"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "Latin-1"

def read(raw):

    if len(raw) > MAX_BYTES:
        raise ImportProblem(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.")
    text, enc = decode(raw)
    if "\x00" in text:
        raise ImportProblem("This doesn't look like a CSV file. Save the list from your spreadsheet as CSV and try again.")
    first = text.split("\n", 1)[0]
    delim = max((",", ";", "\t"), key=lambda d_: first.count(d_))
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    except csv.Error:
        raise ImportProblem("The file could not be read as CSV. Save it from your spreadsheet as CSV and try again.")
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise ImportProblem("The file is empty.")
    header = [clean(h, 80) for h in rows[0]]
    if len(header) > MAX_COLS:
        raise ImportProblem(f"The file has {len(header)} columns; the most it can have is {MAX_COLS}.")
    body = rows[1:]
    if not body:
        raise ImportProblem("The file has a header row and nothing under it.")
    if len(body) > MAX_ROWS:
        raise ImportProblem(f"The file has {len(body)} rows; the most one import takes is {MAX_ROWS}. Split it.")
    w_ = len(header)
    return header, [(r + [""] * w_)[:w_] for r in body], enc

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")

def clean(v, cap=200, lines=False):

    s_ = _CTRL.sub("", (v or "").replace("\r\n", "\n").replace("\r", "\n"))
    s_ = s_.strip()
    if s_[:1] == "'" and s_[1:2] in ("=", "+", "-", "@"):
        s_ = s_[1:]
    if lines:
        s_ = "\n".join(l_.rstrip() for l_ in s_.split("\n")).strip()
    else:
        s_ = " ".join(s_.split())
    return s_[:cap]

def birth_date(v):

    s_ = (v or "").strip()
    m_ = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?", s_)
    if m_:
        y, mo, d = int(m_.group(1)), int(m_.group(2)), int(m_.group(3))
    else:
        m_ = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})", s_)
        if not m_:
            return None
        mo, d, y = int(m_.group(1)), int(m_.group(2)), int(m_.group(3))
        if y < 100:
            y += 2000 if y <= date.today().year % 100 else 1900
    try:
        out = date(y, mo, d)
    except ValueError:
        return None
    if not (1900 <= out.year <= date.today().year + 1):
        return None
    return out.isoformat()

def records(header, rows, mapping):

    out = []
    for i, r in enumerate(rows):
        parts = {}
        for col, key in zip(r, mapping):
            if key:
                parts.setdefault(key, []).append(col)
        vals, notes = {}, []
        for key, cols in parts.items():
            many = [clean(c_, 10_000, lines=key in ("client_notes", "person_notes", "address")) for c_ in cols]
            many = [c_ for c_ in many if c_]
            if key in ("email", "phone", "birth_date", "minor", "client_number", "relationship"):
                vals[key] = many[0] if many else ""
            else:
                vals[key] = JOIN.get(key, " ").join(many)
            vals[key] = vals[key][:CAP.get(key, 200)]
        if vals.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", vals["email"]):
            notes.append(f"Email \"{vals['email'][:60]}\" doesn't look like an email address: left out")
            vals["email"] = ""
        if vals.get("birth_date"):
            bd_ = birth_date(vals["birth_date"])
            if not bd_:
                notes.append(f"Birth date \"{vals['birth_date'][:30]}\" could not be read: left blank")
            vals["birth_date"] = bd_ or ""
        if "minor" in vals:
            m_ = vals["minor"].strip().lower()
            vals["minor"] = 1 if m_ in YES else 0 if m_ in NO else None
            if vals["minor"] is None and m_:
                notes.append(f"Minor \"{m_[:20]}\" is not yes or no: left to the birth date")
        vals["relationship"] = vals.get("relationship", "").lower()
        vals["client_number"] = vals.get("client_number", "") if vals.get("client_number", "").isdigit() else ""
        out.append((i + 2, vals, notes))
    return out

def groups(recs):

    by_key, order, problems = {}, [], []
    for row, v, notes in recs:
        name = v.get("client") or v.get("company") or v.get("person")
        if not name:
            if any(v.get(k) for k in ("email", "phone", "address")):
                problems.append((row, "No name: left out"))
            continue
        key = ("n", v["client_number"]) if v.get("client_number") else \
              ("c", v["client"].lower()) if v.get("client") else ("r", row)
        gr = by_key.get(key)
        if gr is None:
            gr = by_key[key] = dict(key=key, rows=[], people=[], notes=[],
                                    client=dict(name=name, company=v.get("company", ""), address=v.get("address", ""),
                                                notes=v.get("client_notes", ""), number=v.get("client_number", "")))
            order.append(key)
        else:
            c_ = gr["client"]
            for k_, f_ in (("company", "company"), ("address", "address"), ("notes", "client_notes")):
                if not c_[k_] and v.get(f_):
                    c_[k_] = v[f_]
        gr["rows"].append(row)
        gr["notes"] += [(row, n_) for n_ in notes]

        pname = v.get("person") or (v.get("client") or v.get("company") if any(
            v.get(k) for k in ("email", "phone", "birth_date", "relationship")) or "person" not in v else "")
        if pname:
            gr["people"].append(dict(name=pname, relationship=v.get("relationship", ""), email=v.get("email", ""),
                                     phone=v.get("phone", ""), birth_date=v.get("birth_date", ""),
                                     minor=v.get("minor"), notes=v.get("person_notes", ""), row=row))
    return [by_key[k] for k in order], problems

def same_text(a, b):

    return " ".join((a or "").split()).lower() == " ".join((b or "").split()).lower()

def has_text(part, whole):

    return " ".join((part or "").split()).lower() in " ".join((whole or "").split()).lower()

def phone_key(p):

    d_ = re.sub(r"\D", "", p or "")
    return d_[-10:] if len(d_) >= 7 else ""
