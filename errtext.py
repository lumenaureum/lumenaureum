import re

HIDDEN = "[hidden]"
LIMIT = 200

_SECRET_NAME = r"[\w.-]*(?:key|token|secret|signature|sig|password|passwd|pwd|credential|session|auth)[\w.-]*"
_AUTH_HEADER = re.compile(r"\b(Proxy-)?(Authorization)(\s*[:=]\s*)[^\r\n'\"}]+", re.I)
_AUTH_SCHEME = re.compile(r"\b(Bearer|Basic|Digest|AWS4-HMAC-SHA256|AWS)\s+[A-Za-z0-9+/=._~:,-]{6,}", re.I)
_URL = re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*)://([^\s/?#'\"<>]*)([^\s'\"<>]*)")
_USERINFO = re.compile(r"(?<![\w/])[^\s:/@'\"<>]+:[^\s@/'\"<>]+@(?=[\w\[])")
_PAIR = re.compile(r"(?i)\b(" + _SECRET_NAME + r")(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s&,;'\")}\]]+)")
_QUOTED = re.compile(r"(?i)(['\"]" + _SECRET_NAME + r"['\"]\s*:\s*)(\"[^\"]*\"|'[^']*'|[^\s,}\]]+)")
_QUERY = re.compile(r"\?[^\s'\"<>]*=[^\s'\"<>]*")

def _url(m):
    host = m.group(2).rsplit("@", 1)[-1]
    rest = m.group(3)
    return f"{m.group(1)}://{host}" + ("/..." if rest and rest != "/" else "")

def scrub(text, limit=LIMIT):

    t = str(text or "")
    t = _AUTH_HEADER.sub(lambda m: (m.group(1) or "") + m.group(2) + m.group(3) + HIDDEN, t)
    t = _AUTH_SCHEME.sub(lambda m: m.group(1) + " " + HIDDEN, t)
    t = _URL.sub(_url, t)
    t = _USERINFO.sub(HIDDEN + "@", t)
    t = _PAIR.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, t)
    t = _QUOTED.sub(lambda m: m.group(1) + HIDDEN, t)
    t = _QUERY.sub("?...", t)
    t = " ".join(t.split())
    if limit and len(t) > limit:
        t = t[:max(limit - 3, 0)].rstrip() + "..."
    return t

def words(e, limit=LIMIT):

    return scrub(e, limit) or type(e).__name__

def named(e, limit=LIMIT):

    text = scrub(e, 0)
    return scrub(f"{type(e).__name__}: {text}" if text else type(e).__name__, limit)
