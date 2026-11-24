import hashlib
import hmac
import re
import secrets
from datetime import date, datetime, timedelta, timezone

KEY_TOKEN = "calendar_feed_token"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
PAST_DAYS = 90
RETRY_LOCKED = 900
CAL_NAME = "Studio sessions"

def cal_name(studio=""):

    studio = " ".join((studio or "").split())
    return f"{studio} sessions" if studio else CAL_NAME

def new_token():
    return secrets.token_urlsafe(32)

def token_matches(given, stored):

    if not stored or not given or not TOKEN_RE.fullmatch(given):
        return False
    return hmac.compare_digest(given.encode(), stored.encode())

def escape(text):

    text = str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")

def fold(line):

    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)

def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def uid(token, key):

    return hmac.new(token.encode(), str(key).encode(), hashlib.sha256).hexdigest()[:32] + "@lumen-aureum"

def build(entries, token, now=None, studio=""):

    now = now or datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lumen Aureum//Calendar link//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + escape(cal_name(studio)), "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]
    for e in entries:
        start, end = e["start"], e["end"]
        lines += ["BEGIN:VEVENT", "UID:" + uid(token, e["key"]), "DTSTAMP:" + _utc(now)]
        if isinstance(start, datetime):
            lines += ["DTSTART:" + _utc(start), "DTEND:" + _utc(end)]
        else:
            lines += ["DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + end.strftime("%Y%m%d")]
        lines += ["SUMMARY:" + escape(e["summary"]), "STATUS:" + ("TENTATIVE" if e.get("tentative") else "CONFIRMED"),
                  "TRANSP:OPAQUE", "CLASS:PRIVATE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "".join(fold(ln) + "\r\n" for ln in lines)

def entry(key, summary, day, hm, minutes, tz, tentative=False):

    d_ = date.fromisoformat(day[:10])
    try:
        h_, m_ = (int(v) for v in (hm or "").split(":")[:2])
        start = datetime(d_.year, d_.month, d_.day, h_, m_, tzinfo=tz)
    except ValueError:
        return dict(key=key, summary=summary, start=d_, end=d_ + timedelta(days=1), tentative=tentative)
    return dict(key=key, summary=summary, start=start, end=start + timedelta(minutes=max(int(minutes or 0), 15)),
                tentative=tentative)
