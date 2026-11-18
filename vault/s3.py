import datetime
import hashlib
import hmac
import http.client
import ipaddress
import random
import socket
import ssl
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

PART = 16 * 1024 * 1024
MAX_PARTS = 10000
TIMEOUT = 60
TRIES = 4
BACKOFF = 0.5
SLEEP = time.sleep
STORE = "s3_certs"
SEEN_KEY = "s3_cert_seen:"
NOTE = None
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_LAN = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "127.0.0.0/8", "::1/128",
    "169.254.0.0/16", "fe80::/10",
    "fc00::/7",
    "100.64.0.0/10")]

class S3Error(IOError):

    def __init__(self, status, code="", message="", key=""):
        self.status, self.code, self.s3_message, self.key = status, code or "", message or "", key
        super().__init__(words_for(status, self.code, self.s3_message))

def words_for(status, code, message=""):
    plain = {
        "AccessDenied": "The storage refused access: check that the access key may use this bucket.",
        "AllAccessDisabled": "The storage refused access: check that the access key may use this bucket.",
        "NoSuchBucket": "There is no bucket by that name at this address.",
        "InvalidAccessKeyId": "The storage doesn't know this access key.",
        "SignatureDoesNotMatch": "The secret key doesn't match the access key.",
        "RequestTimeTooSkewed": "This machine's clock is too far from the storage's: set the clock right and try again.",
        "AuthorizationHeaderMalformed": "The storage expects another region: check the region setting.",
        "PermanentRedirect": "The bucket is in another region, or at another address: check the region and endpoint.",
        "InvalidBucketName": "That isn't a valid bucket name.",
        "EntityTooLarge": "The file is larger than the storage accepts.",
        "QuotaExceeded": "The storage is full (its quota is reached).",
    }.get(code)
    if plain:
        return plain
    if status and status >= 500:
        return f"The storage had a problem of its own (it answered {status}{' ' + code if code else ''}). Try again later."
    return f"The storage answered {status}{' ' + code if code else ''}" + (f": {message[:200]}" if message else ".")

class PlainHTTPRefused(ValueError):

    def __init__(self, host=""):
        super().__init__(f"Plain http:// is accepted only for S3 storage on your own network{' (' + host + ' is not)' if host else ''}. "
                         "Use its https:// address.")

class CertProblem(OSError):

    def __init__(self, kind, address, seen=None, pinned=None):
        super().__init__(cert_words(kind))
        self.kind, self.address, self.seen, self.pinned = kind, address, seen, pinned

def cert_words(kind):
    if kind == "changed":
        return "The S3 storage's certificate changed. Check it in Setup."
    return "The S3 storage's certificate isn't trusted. Check it in Setup."

def lan_ip(ip):

    try:
        ip = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in _LAN)

def endpoint_problem(endpoint, resolve=True):

    ep = (endpoint or "").strip()
    if not ep or ep.lower().startswith("https://"):
        return None
    if not ep.lower().startswith("http://"):
        return "The S3 endpoint must start with https:// (or http:// for storage on your own network)."
    host = (urllib.parse.urlsplit(ep).hostname or "").strip("[]")
    if not host:
        return "The S3 endpoint has no server name or address."
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        return None if lan_ip(host) else str(PlainHTTPRefused(host))
    if host.lower() == "localhost":
        return None
    if not resolve:
        return str(PlainHTTPRefused(host))
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        return f"Can't find {host} on the network. Check the endpoint's address."
    return None if addrs and all(lan_ip(a) for a in addrs) else str(PlainHTTPRefused(host))

def plain_http(endpoint):
    return (endpoint or "").strip().lower().startswith("http://")

def address(host, port):
    host = host.lower().rstrip(".")
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

def cert_address(cfg):

    if not isinstance(cfg, dict) or cfg.get("type") != "s3":
        return ""
    ep = str(cfg.get("endpoint") or "").strip()
    if not ep.lower().startswith("https://"):
        return ""
    try:
        c = Client(cfg.get("bucket") or "bucket", "", "", endpoint=ep, region=cfg.get("region"),
                   addressing=cfg.get("addressing") or "auto")
    except ValueError:
        return ""
    return address(c._target("")[0], c.port)

def pins():

    try:
        from vault import web as VW
        p = VW.config().get(STORE) or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}

def pin_for(addr, table=None):
    p = (pins() if table is None else table).get(addr)
    return p if isinstance(p, dict) and len(str(p.get("spki") or "")) == 64 else None

def _note(addr, kind, seen):
    if NOTE is not None:
        try:
            NOTE(addr, kind, seen)
        except Exception:
            pass

def check(addr, table=None, timeout=10):

    import webtls
    seen = webtls.peek(addr, timeout=timeout)
    if seen is None:
        return "unreachable", None
    seen["name_ok"] = webtls.names_address(seen, webtls.host_of(addr))
    pin = pin_for(addr, table)
    if pin:
        state = "pinned" if webtls.matches_pin(seen, pin, addr) else "changed"
    else:
        state = "public" if webtls.publicly_trusted(addr, timeout=timeout) else "untrusted"
    _note(addr, state if state in ("untrusted", "changed") else None, seen)
    return state, seen

def _q(s, safe="-_.~"):
    return urllib.parse.quote(s, safe=safe)

def canonical_query(pairs):

    enc = sorted((_q(k), _q(v)) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in enc)

def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()

def signing_key(secret, date, region, service):
    k = _hmac(("AWS4" + secret).encode(), date)
    return _hmac(_hmac(_hmac(k, region), service), "aws4_request")

def sign(method, path, query, headers, payload_hash, access_key, secret_key, region, service="s3", amz_date=None):

    amz_date = amz_date or headers.get("x-amz-date") or headers.get("X-Amz-Date")
    date = amz_date[:8]
    hs = {}
    for k, v in headers.items():
        hs.setdefault(k.lower().strip(), []).append(" ".join(str(v).split()))
    names = sorted(hs)
    canon_headers = "".join(f"{n}:{','.join(hs[n])}\n" for n in names)
    signed = ";".join(names)
    creq = "\n".join([method, path, canonical_query(query), canon_headers, signed, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    sig = hmac.new(signing_key(secret_key, date, region, service), sts.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"

class _PinnedHTTPS(http.client.HTTPSConnection):

    def __init__(self, host, port, pin, addr, timeout):
        import webtls
        super().__init__(host, port, timeout=timeout, context=webtls._pinned_context())
        self._pin, self._addr = pin, addr

    def connect(self):
        import webtls
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        seen = webtls.info(der) if der else None
        if not webtls.matches_pin(seen, self._pin, self._addr):
            try:
                self.sock.close()
            finally:
                self.sock = None
            if seen is not None:
                seen["name_ok"] = webtls.names_address(seen, webtls.host_of(self._addr))
            raise CertProblem("changed", self._addr, seen=seen, pinned=self._pin)

class _LanHTTP(http.client.HTTPConnection):

    def connect(self):
        super().connect()
        peer = self.sock.getpeername()[0]
        if not lan_ip(peer):
            try:
                self.sock.close()
            finally:
                self.sock = None
            raise PlainHTTPRefused(self.host)

_RETRY_EXC = (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
              http.client.RemoteDisconnected, http.client.IncompleteRead)

class _Retry(Exception):

    def __init__(self, err):
        super().__init__(str(err))
        self.err = err

def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _xml(body):
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        return None

def _find(el, name):
    if el is None:
        return None
    r = el.find(_NS + name)
    return r if r is not None else el.find(name)

def _text(el, name, default=""):
    r = _find(el, name)
    return r.text if r is not None and r.text is not None else default

def _error_of(status, body, key=""):
    root = _xml(body) if body else None
    if root is not None and root.tag.endswith("Error"):
        return S3Error(status, _text(root, "Code"), _text(root, "Message"), key)
    return S3Error(status, {404: "NotFound", 403: "AccessDenied"}.get(status, ""), "", key)

def _dns_bucket(b):
    import re
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", b))

class Client:

    def __init__(self, bucket, access_key, secret_key, endpoint=None, region=None, addressing="auto", pins_table=None,
                 timeout=None):
        self.bucket = (bucket or "").strip()
        if not self.bucket:
            raise ValueError("No bucket name.")
        self.access_key, self.secret_key = access_key or "", secret_key or ""
        self.region = (region or "").strip() or "us-east-1"
        self.timeout = timeout or TIMEOUT
        self._pins = pins_table
        self._local = threading.local()
        self._noted = False
        ep = (endpoint or "").strip()
        self.aws = not ep
        if ep:
            u = urllib.parse.urlsplit(ep)
            if plain_http(ep):
                h_ = (u.hostname or "").strip("[]")
                try:
                    literal = ipaddress.ip_address(h_)
                except ValueError:
                    literal = None
                if not h_ or (literal is not None and not lan_ip(literal)):
                    raise PlainHTTPRefused(h_)
            elif not ep.lower().startswith("https://"):
                raise ValueError("The S3 endpoint must start with https:// (or http:// for storage on your own network).")
            self.scheme = u.scheme.lower()
            self.host = (u.hostname or "").lower()
            try:
                self.port = u.port or (443 if self.scheme == "https" else 80)
            except ValueError:
                raise ValueError("The S3 endpoint's port isn't a number.") from None
            self.base = u.path.rstrip("/")
            self.aws = self.host.endswith(".amazonaws.com")
        else:
            self.scheme, self.port, self.base = "https", 443, ""
            self.host = self._aws_host()
        addressing = (addressing or "auto").lower()
        if addressing not in ("auto", "path", "virtual"):
            raise ValueError("Addressing is auto, path or virtual.")
        if addressing == "auto":
            addressing = "virtual" if (self.aws and _dns_bucket(self.bucket) and "." not in self.bucket) else "path"
        self.addressing = addressing

    def _aws_host(self):
        return f"s3.{self.region}.amazonaws.com"

    def _target(self, key):

        k = _q(key, safe="/-_.~") if key else ""
        if self.addressing == "virtual":
            host = f"{self.bucket}.{self.host}"
            path = f"{self.base}/{k}"
        else:
            host = self.host
            path = f"{self.base}/{_q(self.bucket)}" + (f"/{k}" if key else "/")
        hosthdr = host if self.port == (443 if self.scheme == "https" else 80) else f"{host}:{self.port}"
        if ":" in host and not host.startswith("["):
            hosthdr = f"[{host}]" if self.port in (80, 443) else f"[{host}]:{self.port}"
        return host, hosthdr, path

    def _conn(self, host):
        cache = getattr(self._local, "conns", None)
        if cache is None:
            cache = self._local.conns = {}
        c = cache.get(host)
        if c is not None:
            return c
        if self.scheme == "http":
            c = _LanHTTP(host, self.port, timeout=self.timeout)
        else:
            addr = address(host, self.port)
            pin = pin_for(addr, self._pins)
            if pin:
                c = _PinnedHTTPS(host, self.port, pin, addr, self.timeout)
            else:
                c = http.client.HTTPSConnection(host, self.port, timeout=self.timeout, context=ssl.create_default_context())
        cache[host] = c
        return c

    def _drop(self, host):
        cache = getattr(self._local, "conns", None) or {}
        c = cache.pop(host, None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def close(self):
        for h in list((getattr(self._local, "conns", None) or {})):
            self._drop(h)

    def _open(self, method, key="", query=(), headers=None, body=b"", stream=False, ok=(200,), payload_hash=None):

        last = None
        for attempt in range(TRIES):
            if attempt:
                SLEEP(BACKOFF * (2 ** (attempt - 1)) * (0.5 + random.random()))
            try:
                return self._once(method, key, query, headers or {}, body, stream, ok, payload_hash)
            except _Retry as e:
                last = e.err
            except _RETRY_EXC as e:
                last = e
            except ssl.SSLCertVerificationError:
                host = self._target(key)[0]
                addr = address(host, self.port)
                import webtls
                seen = webtls.peek(addr, timeout=min(self.timeout, 10))
                if seen is not None:
                    seen["name_ok"] = webtls.names_address(seen, webtls.host_of(addr))
                _note(addr, "untrusted", seen)
                raise CertProblem("untrusted", addr, seen=seen) from None
            except CertProblem as e:
                _note(e.address, e.kind, e.seen)
                raise
            except _Region:
                continue
        if isinstance(last, S3Error):
            raise last
        raise TimeoutError(f"The S3 storage didn't answer in time ({type(last).__name__}).") from last

    def _once(self, method, key, query, headers, body, stream, ok, payload_hash):
        host, hosthdr, path = self._target(key)
        c = self._conn(host)
        if payload_hash is None:
            payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA
        amz = _now()
        h = {"Host": hosthdr, "X-Amz-Date": amz, "X-Amz-Content-Sha256": payload_hash}
        h.update(headers)
        h["Authorization"] = sign(method, path, list(query), h, payload_hash, self.access_key, self.secret_key,
                                  self.region, amz_date=amz)
        url = path + (("?" + canonical_query(list(query))) if query else "")
        h["Content-Length"] = str(len(body))
        h["User-Agent"] = "lumen-aureum"
        try:
            c.request(method, url, body=body or None, headers=h)
            r = c.getresponse()
        except BaseException:
            self._drop(host)
            raise
        if not self._noted and self.scheme == "https":
            self._noted = True
            _note(address(host, self.port), None, None)
        if r.status in ok:
            if stream:
                return r
            data = r.read()
            if r.will_close:
                self._drop(host)
            return r.status, r, data
        data = r.read(1_000_000)
        if r.will_close or r.length:
            self._drop(host)
        region = r.getheader("x-amz-bucket-region")
        if region and region != self.region and r.status in (301, 307, 400) and not getattr(self, "_moved", False):
            self._moved = True
            self.region = region
            if self.aws and self.host.startswith("s3.") and self.host.endswith(".amazonaws.com"):
                self.close()
                self.host = self._aws_host()
            raise _Region()
        err = _error_of(r.status, data, key)
        if r.status >= 500:
            raise _Retry(err)
        raise err

    def head(self, key):

        try:
            _, r, _ = self._open("HEAD", key, ok=(200,))
        except S3Error as e:
            if e.status == 404 or e.code in ("NoSuchKey", "NotFound"):
                return None
            raise
        return int(r.getheader("Content-Length") or 0)

    def head_headers(self, key):
        _, r, _ = self._open("HEAD", key, ok=(200,))
        return {k.lower(): v for k, v in r.getheaders()}

    def put(self, key, data, content_type=None, cache_control=None):
        h = self._meta(content_type, cache_control)
        self._open("PUT", key, headers=h, body=bytes(data))

    @staticmethod
    def _meta(content_type, cache_control):
        h = {}
        if content_type:
            h["Content-Type"] = content_type
        if cache_control:
            h["Cache-Control"] = cache_control
        return h

    def upload(self, key, reader, content_type=None, cache_control=None, part_size=None):

        size = part_size or PART
        first = _read_full(reader, size)
        if len(first) < size:
            self.put(key, first, content_type, cache_control)
            return len(first)
        q = [("uploads", "")]
        _, _, body = self._open("POST", key, query=q, headers=self._meta(content_type, cache_control))
        upload_id = _text(_xml(body), "UploadId")
        if not upload_id:
            raise S3Error(200, "", "the storage started a multipart upload without an upload id", key)
        parts, total, n, buf = [], 0, 1, first
        try:
            while buf:
                if n > MAX_PARTS:
                    raise S3Error(0, "EntityTooLarge", "", key)
                _, r, _ = self._open("PUT", key, query=[("partNumber", str(n)), ("uploadId", upload_id)], body=buf)
                etag = r.getheader("ETag") or ""
                if not etag:
                    raise S3Error(200, "", "the storage stored a part without an ETag", key)
                parts.append((n, etag))
                total += len(buf)
                n += 1
                if n % 1000 == 0:
                    size *= 2
                buf = _read_full(reader, size)
            self._complete(key, upload_id, parts, total)
        except BaseException:
            try:
                self._open("DELETE", key, query=[("uploadId", upload_id)], ok=(200, 204))
            except Exception:
                pass
            raise
        return total

    def _complete(self, key, upload_id, parts, total):
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{_xml_escape(e)}</ETag></Part>" for n, e in parts
        ) + "</CompleteMultipartUpload>"
        try:
            _, _, body = self._open("POST", key, query=[("uploadId", upload_id)], body=xml.encode(),
                                    headers={"Content-Type": "application/xml"})
        except S3Error as e:

            if e.code == "NoSuchUpload" and self.head(key) == total:
                return
            raise
        root = _xml(body)
        if root is None or root.tag.endswith("Error"):
            raise _error_of(500, body, key)

    def download(self, key, writer):

        got, total, tries = 0, None, 0
        while True:
            hdr = {"Range": f"bytes={got}-"} if got else {}
            r = self._open("GET", key, headers=hdr, stream=True, ok=(200, 206))
            host = self._target(key)[0]
            if got and r.status != 206:
                r.close()
                self._drop(host)
                raise S3Error(r.status, "", "the storage ignored the request to resume a download", key)
            if total is None:
                total = int(r.getheader("Content-Length") or -1)
                if r.status == 206:
                    total = -1
            try:
                while True:
                    b = r.read(1024 * 1024)
                    if not b:
                        break
                    writer.write(b)
                    got += len(b)
                if total >= 0 and got < total:
                    raise http.client.IncompleteRead(b"", total - got)
                if r.will_close:
                    self._drop(host)
                return got
            except _RETRY_EXC:
                self._drop(host)
                tries += 1
                if tries >= TRIES:
                    raise
                SLEEP(BACKOFF * (2 ** (tries - 1)))

    def delete(self, key):
        try:
            self._open("DELETE", key, ok=(200, 204))
        except S3Error as e:
            if e.status != 404:
                raise

    def list(self, prefix=""):

        out, token = [], None
        while True:
            q = [("list-type", "2")] + ([("prefix", prefix)] if prefix else []) + \
                ([("continuation-token", token)] if token else [])
            _, _, body = self._open("GET", "", query=q)
            root = _xml(body)
            if root is None:
                raise S3Error(200, "", "the storage's list of files couldn't be read")
            for c in list(root.findall(_NS + "Contents")) + list(root.findall("Contents")):
                k = _text(c, "Key")
                if k:
                    out.append(k)
            if _text(root, "IsTruncated").lower() != "true":
                return out
            nxt = _text(root, "NextContinuationToken")
            if not nxt or nxt == token:
                raise S3Error(200, "", "the storage's list of files went on without a way to continue")
            token = nxt

class _Region(Exception):
    pass

def _read_full(reader, n):

    buf = bytearray()
    while len(buf) < n:
        b = reader.read(n - len(buf))
        if not b:
            break
        buf += b
    return bytes(buf)
