import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import calfeed

TIMEOUT = 20
DEADLINE_TIMEOUTS = 3
MAX_BODY = 4 * 1024 * 1024
USER_AGENT = "LumenAureum-calendar"
CA_FILE = None
IDENT_RE = re.compile(r"la[0-9a-v]{24}")
TEST_SUMMARY = "Test event from your studio"
PROVIDERS = {"google": "Google Calendar", "outlook": "Outlook", "caldav": "your calendar service"}
ICLOUD_RE = re.compile(r"(p\d+-)?caldav\.icloud\.com")

class SendError(Exception):

    def __init__(self, words, signin=False, retry_after=None):
        super().__init__(words)
        self.words = words
        self.signin = signin
        self.retry_after = retry_after

def new_salt():
    import secrets
    return secrets.token_hex(32)

def studio_mark(salt):

    raw = hmac.new(bytes.fromhex(salt), b"lumen-aureum studio", hashlib.sha256).digest()[:5]
    return base64.b32hexencode(raw).decode().lower()

def ident(salt, key):
    raw = hmac.new(bytes.fromhex(salt), repr(key).encode(), hashlib.sha256).digest()[:10]
    return "la" + studio_mark(salt) + base64.b32hexencode(raw).decode().lower()

def mark_of(ident_):

    return ident_[2:10]

def digest(pub):

    def v(x):
        return x.isoformat() if isinstance(x, (date, datetime)) else x
    return hashlib.sha256(json.dumps({k: v(x) for k, x in sorted(pub.items())}, sort_keys=True).encode()).hexdigest()[:32]

def _z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _is_lan(host):

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    return bool(addrs) and all(a.is_private or a.is_loopback or a.is_link_local for a in addrs)

BAD_ADDRESS = "the server address isn't a web address this can use (check it for typos)"

def origin(url):
    u = urllib.parse.urlsplit(url)
    return (u.scheme.lower(), (u.hostname or "").lower(), u.port or (443 if u.scheme.lower() == "https" else 80))

def _context():
    return ssl.create_default_context(cafile=CA_FILE) if CA_FILE else ssl.create_default_context()

_CONN_WORDS = {ConnectionRefusedError: "the server refused the connection (check the address)",
               ConnectionResetError: "the connection was cut off",
               ConnectionAbortedError: "the connection was cut off",
               BrokenPipeError: "the connection was cut off"}

def _conn_words(e):

    for cls, words in _CONN_WORDS.items():
        if isinstance(e, cls):
            return words
    if isinstance(e, http.client.HTTPException):
        return "its answer wasn't understood"
    return "the connection failed (check the address, and this machine's internet connection)"

def fetch(method, url, headers=None, body=None, lan_http=False, *, max_body=None, deadline=None,
          too_big="the answer was far larger than a calendar's should be"):

    max_body = MAX_BODY if max_body is None else max_body
    try:
        u = urllib.parse.urlsplit(url)
        scheme, host, port = origin(url)
        if not host:
            raise SendError("the address is not complete")
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=_context())
        elif scheme == "http" and lan_http and _is_lan(host):
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        elif scheme == "http":
            raise SendError("the address must start with https:// (plain http is accepted only on your own network)")
        else:
            raise SendError("the address must start with https://")
    except (ValueError, http.client.InvalidURL):
        raise SendError(BAD_ADDRESS) from None
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    if isinstance(body, str):
        body = body.encode()
    own = time.monotonic() + TIMEOUT * DEADLINE_TIMEOUTS
    deadline = own if deadline is None else min(own, deadline)

    def left():
        n = deadline - time.monotonic()
        if n <= 0:
            raise socket.timeout
        return n
    try:
        conn.timeout = min(TIMEOUT, left())
        conn.request(method, path, body=body, headers=hdrs)
        conn.sock.settimeout(min(TIMEOUT, left()))
        r = conn.getresponse()
        chunks, size = [], 0
        while size <= max_body:
            conn.sock and conn.sock.settimeout(min(TIMEOUT, left()))
            part = r.read1(min(65536, max_body + 1 - size))
            if not part:
                break
            chunks.append(part)
            size += len(part)
            left()
        if size > max_body:
            raise SendError(too_big)
        return r.status, {k.lower(): v for k, v in r.getheaders()}, b"".join(chunks)
    except SendError:
        raise
    except (ValueError, http.client.InvalidURL):
        raise SendError(BAD_ADDRESS) from None
    except ssl.SSLCertVerificationError:
        raise SendError("its security certificate isn't one this studio can trust") from None
    except (socket.timeout, TimeoutError):
        raise SendError("it took too long to answer") from None
    except socket.gaierror:
        raise SendError("the address wasn't found (check it, and this machine's internet connection)") from None
    except (OSError, http.client.HTTPException) as e:
        raise SendError(_conn_words(e)) from None
    finally:
        conn.close()

def _json(body):
    try:
        v = json.loads(body or b"{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}

def _retry_after(hd):

    v = str((hd or {}).get("retry-after") or "").strip()
    if not v:
        return None
    if v.isdigit():
        return int(v)
    try:
        from email.utils import parsedate_to_datetime
        return max(0, int((parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None

def refused(status, hd=None, words=None):

    return SendError(words or _status_words(status), retry_after=_retry_after(hd) if status in (429, 503) else None)

def _status_words(status):
    if status in (401, 403):
        return "it refused the studio's sign-in or permission"
    if status == 404:
        return "it doesn't know that calendar"
    if status == 429:
        return "it asked the studio to slow down; trying again later"
    if 300 <= status < 400:
        return "it sent the studio to another address, which is never followed"
    if status >= 500:
        return "it had a problem of its own; trying again later"
    return f"it answered with an error ({status})"

class Connection:

    kind = ""

    def authorized(self, method, url, headers=None, body=None):
        raise NotImplementedError

    def secret(self):
        raise NotImplementedError

    def account(self):
        return ""

def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

class GoogleConnection(Connection):

    kind = "google"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com"
    SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def __init__(self, key):
        self.key = parse_google_key(key)
        self._token, self._until = None, 0

    def secret(self):
        return dict(key=self.key)

    def account(self):
        return self.key["client_email"]

    def jwt(self, now=None):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(now or time.time())
        head = {"alg": "RS256", "typ": "JWT", "kid": self.key.get("private_key_id", "")}
        claims = {"iss": self.key["client_email"], "scope": self.SCOPE, "aud": self.TOKEN_URL, "iat": now,
                  "exp": now + 3600}
        signing = _b64url(json.dumps(head, separators=(",", ":")).encode()) + "." + \
            _b64url(json.dumps(claims, separators=(",", ":")).encode())
        pk = serialization.load_pem_private_key(self.key["private_key"].encode(), password=None)
        sig = pk.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing + "." + _b64url(sig)

    def token(self, fresh=False):
        if self._token and not fresh and time.time() < self._until - 60:
            return self._token
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": self.jwt()})
        st, hd, data = fetch("POST", self.TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        got = _json(data)
        if st != 200 or not got.get("access_token"):
            if st in (400, 401, 403):
                raise SendError("Google didn't accept the service account key (it may have been deleted or "
                                "disabled)", signin=True)
            raise refused(st, hd)
        self._token, self._until = got["access_token"], time.time() + int(got.get("expires_in") or 3600)
        return self._token

    def authorized(self, method, url, headers=None, body=None):
        if origin(url) != origin(self.API):
            raise SendError("an address outside Google's calendar service was refused")
        for attempt in (0, 1):
            h = dict(headers or {}, Authorization="Bearer " + self.token(fresh=bool(attempt)))
            st, hd, data = fetch(method, url, h, body)
            if st != 401:
                return st, hd, data
        return st, hd, data

def parse_google_key(key):

    if isinstance(key, (str, bytes)):
        try:
            key = json.loads(key)
        except ValueError:
            raise SendError("that isn't a service account key file (it should be the .json file Google gave you)")
    if not isinstance(key, dict) or key.get("type") != "service_account" or not key.get("client_email") \
            or not key.get("private_key"):
        raise SendError("that isn't a service account key file (it should be the .json file Google gave you)")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        pk = serialization.load_pem_private_key(str(key["private_key"]).encode(), password=None)
        if not isinstance(pk, rsa.RSAPrivateKey):
            raise ValueError
    except Exception:
        raise SendError("the key file's private key can't be read") from None
    return {k: str(key[k]) for k in ("type", "client_email", "private_key", "private_key_id") if key.get(k)}

class GraphConnection(Connection):

    kind = "outlook"
    LOGIN = "https://login.microsoftonline.com/common/oauth2/v2.0"
    API = "https://graph.microsoft.com"
    SCOPE = "offline_access Calendars.ReadWrite"

    def __init__(self, client_id, refresh_token, user="", on_refresh=None):
        self.client_id, self.refresh_token, self.user = client_id, refresh_token, user
        self.on_refresh = on_refresh
        self._token, self._until = None, 0

    def secret(self):
        return dict(client_id=self.client_id, refresh_token=self.refresh_token, user=self.user)

    def account(self):
        return self.user

    def token(self, fresh=False):
        if self._token and not fresh and time.time() < self._until - 60:
            return self._token
        got = graph_token(self.client_id, {"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        self._token, self._until = got["access_token"], time.time() + int(got.get("expires_in") or 3600)
        if got.get("refresh_token") and got["refresh_token"] != self.refresh_token:
            self.refresh_token = got["refresh_token"]
            if self.on_refresh:
                self.on_refresh(self.secret())
        return self._token

    def authorized(self, method, url, headers=None, body=None):
        if origin(url) != origin(self.API):
            raise SendError("an address outside Microsoft's calendar service was refused")
        for attempt in (0, 1):
            h = dict(headers or {}, Authorization="Bearer " + self.token(fresh=bool(attempt)))
            st, hd, data = fetch(method, url, h, body)
            if st != 401:
                return st, hd, data
        return st, hd, data

def graph_token(client_id, form):
    body = urllib.parse.urlencode(dict(form, client_id=client_id, scope=GraphConnection.SCOPE))
    st, hd, data = fetch("POST", GraphConnection.LOGIN + "/token",
                        {"Content-Type": "application/x-www-form-urlencoded"}, body)
    got = _json(data)
    if st == 200 and got.get("access_token"):
        return got
    err = got.get("error") or ""
    if err == "authorization_pending":
        raise SendError("not signed in yet")
    if err == "slow_down":
        raise SendError("not signed in yet (wait a few seconds)")
    if err in ("invalid_grant", "expired_token", "authorization_declined", "bad_verification_code",
               "interaction_required", "invalid_client", "unauthorized_client"):
        raise SendError("Microsoft no longer accepts the studio's sign-in", signin=True)
    raise refused(st, hd, None if st != 200 else "Microsoft's answer wasn't understood")

def graph_device_start(client_id):

    if not re.fullmatch(r"[0-9a-fA-F-]{36}", client_id or ""):
        raise SendError("the application (client) ID should look like 1a2b3c4d-... (36 characters)")
    body = urllib.parse.urlencode({"client_id": client_id, "scope": GraphConnection.SCOPE})
    st, hd, data = fetch("POST", GraphConnection.LOGIN + "/devicecode",
                        {"Content-Type": "application/x-www-form-urlencoded"}, body)
    got = _json(data)
    if st != 200 or not got.get("device_code") or not got.get("user_code"):
        if got.get("error") in ("invalid_client", "unauthorized_client", "invalid_request"):
            raise SendError("Microsoft doesn't accept that application ID for this kind of sign-in (check that "
                            "public client flows are allowed in its settings)")
        raise refused(st, hd)
    uri = got.get("verification_uri") or "https://microsoft.com/devicelogin"
    if origin(uri)[0] != "https" or not origin(uri)[1].endswith(("microsoft.com", "microsoftonline.com", "live.com")):
        uri = "https://microsoft.com/devicelogin"
    return dict(user_code=str(got["user_code"])[:20], verification_uri=uri, device_code=got["device_code"],
                interval=int(got.get("interval") or 5),
                expires_at=time.time() + min(int(got.get("expires_in") or 900), 1800))

def graph_device_finish(client_id, device_code):

    got = graph_token(client_id, {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                  "device_code": device_code})
    if not got.get("refresh_token"):
        raise SendError("Microsoft signed in without the permission to keep the studio signed in")
    return got

class CalDAVConnection(Connection):

    kind = "caldav"

    def __init__(self, url, user, password):
        url = (url or "").strip()
        if url and "://" not in url:
            url = "https://" + url
        try:
            if not url or not origin(url)[1]:
                raise SendError("enter the server's address")
        except ValueError:
            raise SendError(BAD_ADDRESS) from None
        self.url, self.user, self.password = url, (user or "").strip(), password or ""
        self.base = origin(url)

    def secret(self):
        return dict(url=self.url, user=self.user, password=self.password)

    def account(self):
        return self.user

    def trusted(self, url):
        o = origin(url)
        if o == self.base:
            return True
        return o[0] == "https" and o[2] == 443 and self.base[0] == "https" and self.base[2] == 443 and \
            bool(ICLOUD_RE.fullmatch(self.base[1])) and bool(ICLOUD_RE.fullmatch(o[1]))

    def authorized(self, method, url, headers=None, body=None):
        if not self.trusted(url):
            raise SendError("the server pointed to an address on another domain, which was refused")
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        for _ in range(3):
            st, hd, data = fetch(method, url, dict(headers or {}, Authorization="Basic " + auth), body, lan_http=True)
            if st in (301, 302, 307, 308) and hd.get("location"):
                nxt = urllib.parse.urljoin(url, hd["location"])
                if origin(nxt) == origin(url):
                    url = nxt
                    continue
            if st == 401:
                raise SendError("it didn't accept the user name and app password", signin=True)
            return st, hd, data
        return st, hd, data

class GoogleCalendar:
    def __init__(self, conn, calendar_id):
        self.conn, self.cid = conn, (calendar_id or "").strip()
        if not self.cid:
            raise SendError("enter the calendar ID")

    def _url(self, rest="", query=None):
        u = f"{self.conn.API}/calendar/v3/calendars/{urllib.parse.quote(self.cid, safe='')}/events{rest}"
        return u + ("?" + urllib.parse.urlencode(query) if query else "")

    def check(self):

        st, hd, data = self.conn.authorized("GET", self._url(query={"maxResults": "1"}))
        if st == 404:
            raise SendError("Google doesn't know that calendar ID, or it isn't shared with the service account yet")
        if st == 403:
            raise SendError("the calendar is shared with the service account without \"Make changes to events\"")
        if st != 200:
            raise refused(st, hd)
        return " ".join(str(_json(data).get("summary") or "").split())[:120]

    def list_ours(self, since, mark):
        out, token = {}, None
        for _ in range(50):
            q = {"privateExtendedProperty": "lumenAureum=" + mark, "timeMin": _z(since), "maxResults": "2500",
                 "showDeleted": "false"}
            if token:
                q["pageToken"] = token
            st, hd, data = self.conn.authorized("GET", self._url(query=q))
            if st != 200:
                raise refused(st, hd)
            got = _json(data)
            for it in got.get("items") or []:
                if IDENT_RE.fullmatch(str(it.get("id") or "")) and mark_of(it["id"]) == mark \
                        and it.get("status") != "cancelled":
                    out[it["id"]] = dict(rid=it["id"], etag=str(it.get("etag") or ""))
            token = got.get("nextPageToken")
            if not token:
                break
        return out

    def body(self, ident_, pub):
        def when(v):
            return {"date": v.isoformat()} if pub["all_day"] else {"dateTime": _z(v), "timeZone": "UTC"}
        return {"id": ident_, "summary": pub["summary"], "start": when(pub["start"]), "end": when(pub["end"]),
                "status": "tentative" if pub["tentative"] else "confirmed", "transparency": "opaque",
                "visibility": "private", "extendedProperties": {"private": {"lumenAureum": mark_of(ident_)}}}

    def put(self, ident_, pub, rid=None):
        payload = json.dumps(self.body(ident_, pub))
        hj = {"Content-Type": "application/json"}
        if rid:
            st, hd, data = self.conn.authorized("PUT", self._url("/" + ident_), hj, payload)
            if st not in (404, 410):
                return self._done(st, hd, data)
        st, hd, data = self.conn.authorized("POST", self._url(), hj, payload)
        if st == 409:
            st, hd, data = self.conn.authorized("PUT", self._url("/" + ident_), hj, payload)
        return self._done(st, hd, data)

    def _done(self, st, hd, data):
        if st not in (200, 201):
            raise refused(st, hd)
        got = _json(data)
        return dict(rid=str(got.get("id") or ""), etag=str(got.get("etag") or ""))

    def delete(self, rid):
        st, hd, _ = self.conn.authorized("DELETE", self._url("/" + rid))
        if st not in (200, 204, 404, 410):
            raise refused(st, hd)

class GraphCalendar:

    PROP = "String {7c1e5bd4-5f0b-4a43-9c55-61a4b5a9e2f3} Name LumenAureumId"

    def __init__(self, conn, calendar_id):
        self.conn, self.cid = conn, (calendar_id or "").strip()
        if not self.cid:
            raise SendError("pick a calendar")

    def _api(self, path, query=None):
        return f"{self.conn.API}/v1.0{path}" + ("?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
                                                 if query else "")

    def check(self):
        st, hd, _ = self.conn.authorized("GET", self._api(f"/me/calendars/{urllib.parse.quote(self.cid, safe='')}",
                                                         {"$select": "id,name"}))
        if st != 200:
            raise refused(st, hd)

    def list_ours(self, since, mark):
        out = {}
        url = self._api(f"/me/calendars/{urllib.parse.quote(self.cid, safe='')}/events", {
            "$select": "id,start,isAllDay", "$top": "200",
            "$filter": f"start/dateTime ge '{(since - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%S')}'",
            "$expand": f"singleValueExtendedProperties($filter=id eq '{self.PROP}')"})
        for _ in range(100):
            st, hd, data = self.conn.authorized("GET", url, {"Prefer": 'outlook.timezone="UTC"'})
            if st != 200:
                raise refused(st, hd)
            got = _json(data)
            for it in got.get("value") or []:
                props = it.get("singleValueExtendedProperties") or []
                val = next((p.get("value") for p in props if str(p.get("id", "")).lower() == self.PROP.lower()), None)
                if not val or not IDENT_RE.fullmatch(str(val)) or mark_of(str(val)) != mark:
                    continue
                try:
                    st_ = datetime.fromisoformat(str((it.get("start") or {}).get("dateTime"))[:19]).replace(
                        tzinfo=timezone.utc)
                except ValueError:
                    st_ = None

                if st_ is not None and (st_.date() < since.date() if it.get("isAllDay") else st_ < since):
                    continue
                out[val] = dict(rid=str(it.get("id") or ""), etag=str(it.get("@odata.etag") or ""))
            url = got.get("@odata.nextLink")
            if not url:
                break
        return out

    def body(self, ident_, pub, new=True):
        def when(v):
            if pub["all_day"]:
                return {"dateTime": v.isoformat() + "T00:00:00", "timeZone": "UTC"}
            return {"dateTime": v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}
        b = {"subject": pub["summary"], "start": when(pub["start"]), "end": when(pub["end"]),
             "isAllDay": pub["all_day"], "showAs": "tentative" if pub["tentative"] else "busy",
             "sensitivity": "private"}
        if new:
            b["singleValueExtendedProperties"] = [{"id": self.PROP, "value": ident_}]
        return b

    def put(self, ident_, pub, rid=None):
        hj = {"Content-Type": "application/json"}
        if rid:
            st, hd, data = self.conn.authorized("PATCH", self._api(f"/me/events/{urllib.parse.quote(rid, safe='')}"),
                                               hj, json.dumps(self.body(ident_, pub, new=False)))
            if st not in (404, 410):
                return self._done(st, hd, data)
        st, hd, data = self.conn.authorized(
            "POST", self._api(f"/me/calendars/{urllib.parse.quote(self.cid, safe='')}/events"), hj,
            json.dumps(self.body(ident_, pub)))
        return self._done(st, hd, data)

    def _done(self, st, hd, data):
        if st not in (200, 201):
            raise refused(st, hd)
        got = _json(data)
        return dict(rid=str(got.get("id") or ""), etag=str(got.get("@odata.etag") or ""))

    def delete(self, rid):
        st, hd, _ = self.conn.authorized("DELETE", self._api(f"/me/events/{urllib.parse.quote(rid, safe='')}"))
        if st not in (200, 204, 404, 410):
            raise refused(st, hd)

def graph_calendars(conn):

    st, hd, data = conn.authorized("GET", f"{conn.API}/v1.0/me/calendars?$select=id,name,canEdit&$top=100")
    if st != 200:
        raise refused(st, hd)
    return [(str(c["id"]), str(c.get("name") or "Calendar")[:120]) for c in _json(data).get("value") or []
            if c.get("id") and c.get("canEdit", True)]

DAV, CALDAV_NS = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"

class _Declared(Exception):
    pass

def _no_dtd(*_a):
    raise _Declared

def safe_xml(body):

    from xml.parsers import expat
    head = (body or b"")[:MAX_BODY]
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", head, re.I):
        raise SendError("the server's answer was refused (it declared entities)")
    scan = expat.ParserCreate()
    scan.StartDoctypeDeclHandler = scan.EntityDeclHandler = scan.UnparsedEntityDeclHandler = _no_dtd
    scan.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        scan.Parse(head, True)
    except _Declared:
        raise SendError("the server's answer was refused (it declared entities)") from None
    except expat.ExpatError:
        raise SendError("the server's answer wasn't understood") from None
    try:
        return ET.fromstring(head)
    except ET.ParseError:
        raise SendError("the server's answer wasn't understood") from None

def _responses(root):
    for r in root.iter(DAV + "response"):
        href = (r.findtext(DAV + "href") or "").strip()
        ok = [p for ps in r.iter(DAV + "propstat") for p in ps.iter(DAV + "prop")
              if " 200 " in (ps.findtext(DAV + "status") or " 200 ")]
        yield href, ok

PROPFIND_START = b"""<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:current-user-principal/><c:calendar-home-set/><d:resourcetype/><d:displayname/></d:prop></d:propfind>"""
PROPFIND_HOME = b"""<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>"""

def _propfind(conn, url, body, depth="0"):
    st, hd, data = conn.authorized("PROPFIND", url, {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
                                  body)
    if st != 207:
        raise refused(st, hd, None if st != 200 else "it doesn't answer as a calendar server does")
    return safe_xml(data)

def _is_calendar(props):
    return any(p.find(DAV + "resourcetype/" + CALDAV_NS + "calendar") is not None for p in props)

def _takes_events(props):
    sets = [s for p in props for s in p.iter(CALDAV_NS + "supported-calendar-component-set")]
    return not sets or any(c.get("name") == "VEVENT" for s in sets for c in s.iter(CALDAV_NS + "comp"))

def caldav_calendars(conn):

    def first_href(root, tag):
        for _, props in _responses(root):
            for p in props:
                el = p.find(tag + "/" + DAV + "href")
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
        return None

    start = conn.url
    try:
        root = _propfind(conn, start, PROPFIND_START)
    except SendError as e:
        if e.signin:
            raise
        start = urllib.parse.urljoin(conn.url, "/.well-known/caldav")
        root = _propfind(conn, start, PROPFIND_START)
    for href, props in _responses(root):
        if _is_calendar(props) and _takes_events(props):
            name = next((p.findtext(DAV + "displayname") for p in props if p.findtext(DAV + "displayname")), "Calendar")
            return [(urllib.parse.urljoin(start, href), name[:120])]
    home = first_href(root, CALDAV_NS + "calendar-home-set")
    if not home:
        principal = first_href(root, DAV + "current-user-principal")
        if not principal:
            raise SendError("it doesn't say where this account's calendars are (try the calendar's own address)")
        purl = urllib.parse.urljoin(start, principal)
        home = first_href(_propfind(conn, purl, PROPFIND_START), CALDAV_NS + "calendar-home-set")
        if not home:
            raise SendError("it doesn't say where this account's calendars are (try the calendar's own address)")
        start = purl
    hurl = urllib.parse.urljoin(start, home)
    out = []
    for href, props in _responses(_propfind(conn, hurl, PROPFIND_HOME, depth="1")):
        if _is_calendar(props) and _takes_events(props):
            name = next((p.findtext(DAV + "displayname") for p in props if p.findtext(DAV + "displayname")), "")
            url = urllib.parse.urljoin(hurl, href)
            if conn.trusted(url):
                out.append((url, (name or href.rstrip("/").rsplit("/", 1)[-1] or "Calendar")[:120]))
    if not out:
        raise SendError("this account has no calendar that takes events")
    return out

class CalDAVCalendar:
    UID_SUFFIX = "@lumen-aureum"

    def __init__(self, conn, url):
        if not url:
            raise SendError("pick a calendar")
        self.conn, self.url = conn, url if url.endswith("/") else url + "/"

    def check(self):
        props = [p for _, ps in _responses(_propfind(self.conn, self.url, PROPFIND_HOME)) for p in ps]
        if not _is_calendar(props):
            raise SendError("that address isn't a calendar")

    def list_ours(self, since, mark):
        body = f"""<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:getetag/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
<c:time-range start="{since.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"/></c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""
        st, hd, data = self.conn.authorized("REPORT", self.url, {"Depth": "1",
                                                                "Content-Type": "application/xml; charset=utf-8"}, body)
        if st != 207:
            raise refused(st, hd)
        out = {}
        for href, props in _responses(safe_xml(data)):
            name = urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])
            if name.endswith(".ics") and IDENT_RE.fullmatch(name[:-4]) and mark_of(name[:-4]) == mark:
                etag = next((p.findtext(DAV + "getetag") for p in props if p.findtext(DAV + "getetag")), "")
                out[name[:-4]] = dict(rid=name[:-4], etag=etag.strip())
        return out

    def put(self, ident_, pub, rid=None):
        e = dict(key=ident_, summary=pub["summary"], start=pub["start"], end=pub["end"], tentative=pub["tentative"])
        st, hd, _ = self.conn.authorized("PUT", self.url + ident_ + ".ics",
                                         {"Content-Type": "text/calendar; charset=utf-8"},
                                         calfeed.one_event(e, ident_ + self.UID_SUFFIX))
        if st not in (200, 201, 204):
            raise refused(st, hd)
        return dict(rid=ident_, etag=(hd.get("etag") or "").strip())

    def delete(self, rid):
        st, hd, _ = self.conn.authorized("DELETE", self.url + rid + ".ics")
        if st not in (200, 204, 404, 410):
            raise refused(st, hd)

def connect(secret, on_refresh=None):

    kind = secret.get("kind")
    if kind == "google":
        conn = GoogleConnection(secret["key"])
        return conn, GoogleCalendar(conn, secret.get("calendar"))
    if kind == "outlook":
        conn = GraphConnection(secret["client_id"], secret["refresh_token"], secret.get("user", ""), on_refresh)
        return conn, GraphCalendar(conn, secret.get("calendar"))
    if kind == "caldav":
        conn = CalDAVConnection(secret["url"], secret["user"], secret["password"])
        return conn, CalDAVCalendar(conn, secret.get("calendar"))
    raise SendError("nothing is set up")

def _marks(mark, *idents):

    if mark:
        return [mark]
    return sorted({mark_of(i) for ids in idents for i in ids if IDENT_RE.fullmatch(i)})

def _list(cal, since, marks):
    out = {}
    for m in marks:
        out.update(cal.list_ours(since, m))
    return out

def sync(cal, desired, state, since, keep=(), mark=None):

    remote = _list(cal, since, _marks(mark, desired, state, keep))
    n = dict(added=0, changed=0, removed=0, total=len(desired))
    for i, pub in sorted(desired.items()):
        d = digest(pub)
        have, was = remote.get(i), state.get(i)
        if have and was and was.get("digest") == d and (not was.get("etag") or was["etag"] == have["etag"]):
            if not was.get("etag"):
                was["etag"] = have["etag"]
            was["rid"] = have["rid"]
            continue
        got = cal.put(i, pub, rid=have["rid"] if have else None)
        state[i] = dict(rid=got["rid"] or (have or {}).get("rid") or i, etag=got["etag"], digest=d,
                        day=pub["start"].isoformat()[:10])
        n["changed" if have else "added"] += 1
    for i, have in sorted(remote.items()):
        if i not in desired and i not in keep:
            cal.delete(have["rid"])
            state.pop(i, None)
            n["removed"] += 1
    for i in [i for i in state if i not in desired and i not in remote]:
        state.pop(i)
    return n

def remove_all(cal, state, since, mark=None):

    remote = _list(cal, since, _marks(mark, state))
    rids = {i: h["rid"] for i, h in remote.items()}
    for i, s in state.items():
        rids.setdefault(i, s.get("rid") or i)
    for i, rid in sorted(rids.items()):
        cal.delete(rid)
        state.pop(i, None)
    return len(rids)

def test_event(now=None):

    now = now or datetime.now(timezone.utc)
    start = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return dict(summary=TEST_SUMMARY, start=start, end=start + timedelta(minutes=30), all_day=False, tentative=False)
