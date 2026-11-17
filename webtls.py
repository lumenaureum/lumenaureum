import functools
import hashlib
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

STORE = "web_certs"
SEEN_KEY = "web_cert_seen:"
NOW_KEY = "web_cert_now:"
NOTE = None
RENEWED = None
_RENEWED_SEEN = set()

class CertProblem(OSError):

    def __init__(self, kind, address, seen=None, pinned=None):
        super().__init__(words(kind))
        self.kind, self.address, self.seen, self.pinned = kind, address, seen, pinned

def words(kind):

    if kind == "changed":
        return "The web host's certificate changed. Check it in Setup."
    return "The web host's certificate isn't trusted. Check it in Setup."

def address(url):

    u = urlsplit(url or "")
    host = (u.hostname or "").lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    try:
        port = u.port or 443
    except ValueError:
        port = 443
    return f"{host}:{port}" if host else ""

def _split(addr):
    host, _, port = addr.rpartition(":")
    return host.strip("[]"), int(port)

def colon_hex(sha256_hex):

    return ":".join(sha256_hex[i:i + 2] for i in range(0, len(sha256_hex), 2)).upper()

def info(der):

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    sha = hashlib.sha256(der).hexdigest()
    out = dict(sha256=sha, fp=colon_hex(sha), spki="", spki_fp="", subject="", issuer="", cn="", names=[], san=False,
               not_before="", not_after="", self_signed=False)
    try:
        c = x509.load_der_x509_certificate(der)
    except ValueError:
        return out
    from cryptography.hazmat.primitives import serialization
    spki = hashlib.sha256(c.public_key().public_bytes(serialization.Encoding.DER,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    cn = c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)

    def name(n):
        for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
            v = n.get_attributes_for_oid(oid)
            if v:
                return str(v[0].value)[:200]
        return n.rfc4514_string()[:200]
    try:
        alt = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in alt.get_values_for_type(x509.DNSName)] + \
                [str(v) for v in alt.get_values_for_type(x509.IPAddress)]
        san = True
    except x509.ExtensionNotFound:
        names, san = [], False
    out.update(spki=spki, spki_fp=colon_hex(spki), cn=str(cn[0].value)[:200] if cn else "", san=san,
               subject=name(c.subject), issuer=name(c.issuer), names=names[:50],
               not_before=c.not_valid_before_utc.date().isoformat(), not_after=c.not_valid_after_utc.date().isoformat(),
               self_signed=c.subject == c.issuer)
    return out

def names_address(i, host):

    import ipaddress
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    cands = list(i.get("names") or []) if i.get("san") else ([i.get("cn")] if i.get("cn") else [])
    for n in cands:
        n = str(n).lower().rstrip(".")
        if ip is not None:
            try:
                if ipaddress.ip_address(n) == ip:
                    return True
            except ValueError:
                pass
            continue
        if n == host:
            return True
        if n.startswith("*.") and "." in host:
            first, rest = host.split(".", 1)
            if first and rest == n[2:] and "*" not in rest:
                return True
    return False

def host_of(addr):
    return _split(addr)[0]

def expired(i, now=None):

    from datetime import date, datetime, timezone
    try:
        end = date.fromisoformat(str(i.get("not_after") or ""))
    except ValueError:
        return False
    today = (datetime.fromtimestamp(now, timezone.utc) if now else datetime.now(timezone.utc)).date()
    return end < today

def pins():

    try:
        from vault import web as VW
        p = VW.config().get(STORE) or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}

def pin_for(addr):
    p = pins().get(addr)
    return p if isinstance(p, dict) and len(str(p.get("spki") or "")) == 64 else None

def matches_pin(i, pin, addr):

    return bool(i) and i.get("spki") == pin.get("spki") and names_address(i, host_of(addr))

def _note(addr, kind, seen):
    if NOTE is not None:
        try:
            NOTE(addr, kind, seen)
        except Exception:
            pass

def _public_context():
    return ssl.create_default_context()

def _pinned_context():

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

class _PinnedHTTPS(http.client.HTTPSConnection):

    def __init__(self, *a, pin=None, addr="", **kw):
        super().__init__(*a, **kw)
        self._pin, self._addr = pin, addr

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        seen = info(der) if der else None
        if not matches_pin(seen, self._pin, self._addr):
            try:
                self.sock.close()
            finally:
                self.sock = None
            if seen is not None:
                seen["name_ok"] = names_address(seen, host_of(self._addr))
            raise CertProblem("changed", self._addr, seen=seen, pinned=self._pin)
        if seen["sha256"] != self._pin.get("sha256") and (self._addr, seen["sha256"]) not in _RENEWED_SEEN:
            _RENEWED_SEEN.add((self._addr, seen["sha256"]))
            if RENEWED is not None:
                try:
                    RENEWED(self._addr, seen)
                except Exception:
                    pass

class _PinnedHandler(urllib.request.HTTPSHandler):
    def __init__(self, pin, addr):
        super().__init__(context=_pinned_context())
        self._pin, self._addr = pin, addr

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPS, pin=self._pin, addr=self._addr), req, context=self._context)

class _NoFollow(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, *a, **kw):
        return None

def read(r, limit, deadline):

    buf = bytearray()
    while len(buf) < limit:
        if time.monotonic() > deadline:
            raise TimeoutError("the web host's answer didn't arrive in time")
        b = getattr(r, "read1", r.read)(min(65536, limit - len(buf)))
        if not b:
            break
        buf += b
    return bytes(buf)

def peek(addr, timeout=10):

    host, port = _split(addr)
    ctx = _pinned_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as t:
                der = t.getpeercert(binary_form=True)
    except (OSError, ValueError):
        return None
    return info(der) if der else None

def publicly_trusted(addr, timeout=10):

    host, port = _split(addr)
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            with _public_context().wrap_socket(s, server_hostname=host):
                return True
    except (OSError, ValueError):
        return False

def open_request(req, timeout=20, handlers=()):

    url = req.full_url
    https = urlsplit(url).scheme == "https"
    addr = address(url) if https else ""
    pin = pin_for(addr) if https else None
    hs = [h for h in handlers if not (isinstance(h, type) and issubclass(h, urllib.request.HTTPRedirectHandler))
          and not isinstance(h, urllib.request.HTTPRedirectHandler)] + [_NoFollow]
    hs.append(_PinnedHandler(pin, addr) if pin else urllib.request.HTTPSHandler(context=_public_context()))
    opener = urllib.request.build_opener(*hs)
    try:
        r = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError:
        if https:
            _note(addr, None, None)
        raise
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, CertProblem):
            _note(addr, reason.kind, reason.seen)
            raise reason from None
        if isinstance(reason, ssl.SSLCertVerificationError) and https:
            seen = peek(addr, timeout=min(timeout, 10))
            if seen is not None:
                seen["name_ok"] = names_address(seen, host_of(addr))
            p = CertProblem("untrusted", addr, seen=seen)
            _note(addr, p.kind, p.seen)
            raise p from None
        raise
    if https:
        _note(addr, None, None)
    return r

def check(addr, timeout=10):

    seen = peek(addr, timeout=timeout)
    if seen is None:
        return "unreachable", None
    seen["name_ok"] = names_address(seen, host_of(addr))
    pin = pin_for(addr)
    if pin:
        state = "pinned" if matches_pin(seen, pin, addr) else "changed"
    else:
        state = "public" if publicly_trusted(addr, timeout=timeout) else "untrusted"
    _note(addr, state if state in ("untrusted", "changed") else None, seen)
    return state, seen

def stamp(i):

    keep = ("spki", "spki_fp", "sha256", "fp", "subject", "issuer", "cn", "san", "names", "not_before", "not_after",
            "self_signed")
    return dict({k: i.get(k) for k in keep if k in i}, at=time.strftime("%Y-%m-%d %H:%M"))
