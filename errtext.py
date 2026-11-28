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

_KINDS = (
    (PermissionError, "permission was refused"),
    (FileNotFoundError, "a file or folder is missing"),
    (IsADirectoryError, "a folder was found where a file should be"),
    (TimeoutError, "it took too long"),
    (ConnectionError, "the connection failed"),
    (MemoryError, "the computer ran out of memory"),
    (OSError, "a disk or network problem"),
    (ImportError, "it needs a part this studio doesn't have"),
    (SyntaxError, "its code has a mistake in it"),
)
_OWN_ERRNO = re.compile(r"^\[Errno -?\d+\]\s*")

def plain(e, limit=LIMIT):

    kind = next((w for k, w in _KINDS if isinstance(e, k)), "an unexpected error")
    if isinstance(e, OSError) and e.strerror:
        own = e.strerror + (f": {e.filename}" if e.filename else "")
    elif isinstance(e, ImportError):
        return scrub(kind + (f" ({e.name})" if e.name else ""), limit)
    elif isinstance(e, (KeyError, IndexError, AttributeError, TypeError, NameError, AssertionError)):
        own = ""
    else:
        own = _OWN_ERRNO.sub("", str(e))
    return scrub(own, limit) or kind
