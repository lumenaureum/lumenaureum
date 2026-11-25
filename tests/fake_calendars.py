import base64
import http.server
import json
import socket
import ssl
import threading
import urllib.parse
import uuid

from fake_s3 import make_cert

def rsa_key_json(email="studio-sender@sample-project.iam.gserviceaccount.com"):

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return dict(type="service_account", project_id="sample-project", private_key_id="abc123keyid",
                private_key=pem, client_email=email, client_id="1234567890",
                token_uri="https://evil.example.invalid/token"), k.public_key()

def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

class _Server:

    def __init__(self, tmp_path, fn):
        self.requests = []
        cert, key = make_cert(tmp_path)
        self.cafile = cert
        owner = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
                rec = dict(method=self.command, path=urllib.parse.unquote(u.path), query=q, raw=self.path,
                           headers={k.lower(): v for k, v in self.headers.items()}, body=body)
                owner.requests.append(rec)
                st, hd, out = fn(self.command, rec["path"], q, rec["headers"], body)
                self.send_response(st)
                for k, v in (hd or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def bodies(self):

        return "\n".join(r["raw"] + "\n" + json.dumps(r["headers"]) + "\n" + r["body"].decode("utf-8", "replace")
                         for r in self.requests)

def _j(st, obj, extra=None):
    return st, dict({"Content-Type": "application/json"}, **(extra or {})), json.dumps(obj).encode()

class FakeGoogle(_Server):

    def __init__(self, tmp_path, public_key, calendar_id="sessions@group.calendar.google.com", email=None):
        self.public_key, self.calendar_id = public_key, calendar_id
        self.summary = "Studio sessions"
        self.email = email
        self.events = {}
        self.tokens = set()
        self.jwts = []
        self.fail = None
        self.fail_headers = {}
        self.n = 0
        super().__init__(tmp_path, self.handle)

    def check_jwt(self, jwt):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        h, c, s = jwt.split(".")
        head, claims = json.loads(_b64d(h)), json.loads(_b64d(c))
        self.public_key.verify(_b64d(s), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
        self.jwts.append((head, claims))
        return head, claims

    def handle(self, method, path, q, headers, body):
        if path == "/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            try:
                head, claims = self.check_jwt(form["assertion"])
            except Exception:
                return _j(400, {"error": "invalid_grant"})
            if form.get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" or \
                    (self.email and claims["iss"] != self.email):
                return _j(400, {"error": "invalid_grant"})
            tok = "tok-" + uuid.uuid4().hex
            self.tokens.add(tok)
            return _j(200, {"access_token": tok, "expires_in": 3599, "token_type": "Bearer"})
        if headers.get("authorization", "")[7:] not in self.tokens:
            return _j(401, {"error": {"code": 401}})
        if self.fail:
            return _j(self.fail, {"error": {"code": self.fail}}, self.fail_headers)
        pre = f"/calendar/v3/calendars/{self.calendar_id}/events"
        if not path.startswith(pre):
            return _j(404, {"error": {"code": 404}})
        rest = path[len(pre):].strip("/")
        if method == "GET" and not rest:
            items = [e for e in self.events.values() if e["status"] != "cancelled"]
            if "privateExtendedProperty" in q:
                k, v = q["privateExtendedProperty"].split("=", 1)
                items = [e for e in items if (e.get("extendedProperties") or {}).get("private", {}).get(k) == v]
            return _j(200, {"summary": self.summary, "items": items})
        if method == "POST" and not rest:
            ev = json.loads(body)
            if ev["id"] in self.events:
                return _j(409, {"error": {"code": 409, "message": "The requested identifier already exists."}})
            return self._store(ev, 200)
        if method == "PUT" and rest:
            if rest not in self.events:
                return _j(404, {"error": {"code": 404}})
            ev = json.loads(body)
            ev["id"] = rest
            return self._store(ev, 200)
        if method == "DELETE" and rest:
            e = self.events.get(rest)
            if not e or e["status"] == "cancelled":
                return _j(410, {"error": {"code": 410}})
            e["status"] = "cancelled"
            return 204, {}, b""
        return _j(400, {"error": {"code": 400}})

    def _store(self, ev, st):
        self.n += 1
        ev = dict(ev, etag=f'"{self.n}"')
        ev.setdefault("status", "confirmed")
        self.events[ev["id"]] = ev
        return _j(st, ev)

    def live(self):
        return {k: v for k, v in self.events.items() if v["status"] != "cancelled"}

class FakeGraph(_Server):

    PROP = "String {7c1e5bd4-5f0b-4a43-9c55-61a4b5a9e2f3} Name LumenAureumId"

    def __init__(self, tmp_path, client_id="11111111-2222-3333-4444-555555555555"):
        self.client_id = client_id
        self.calendars = {"cal-main": "Calendar", "cal-sessions": "Studio sessions"}
        self.events = {}
        self.access = set()
        self.refresh = {"rt-0"}
        self.pending_polls = 1
        self.issued = []
        self.fail = None
        super().__init__(tmp_path, self.handle)

    def handle(self, method, path, q, headers, body):
        if path == "/common/oauth2/v2.0/devicecode":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("client_id") != self.client_id:
                return _j(400, {"error": "invalid_client"})
            assert form["scope"] == "offline_access Calendars.ReadWrite", form
            return _j(200, {"device_code": "dev-code-1", "user_code": "ABCD-EFGH", "interval": 5, "expires_in": 900,
                            "verification_uri": "https://microsoft.com/devicelogin"})
        if path == "/common/oauth2/v2.0/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("client_id") != self.client_id:
                return _j(400, {"error": "invalid_client"})
            if form["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code":
                if form.get("device_code") != "dev-code-1":
                    return _j(400, {"error": "expired_token"})
                if self.pending_polls > 0:
                    self.pending_polls -= 1
                    return _j(400, {"error": "authorization_pending"})
            elif form["grant_type"] == "refresh_token":
                if form.get("refresh_token") not in self.refresh:
                    return _j(400, {"error": "invalid_grant"})
                self.refresh.discard(form["refresh_token"])
            else:
                return _j(400, {"error": "unsupported_grant_type"})
            at, rt = "at-" + uuid.uuid4().hex, "rt-" + uuid.uuid4().hex
            self.access.add(at)
            self.refresh.add(rt)
            self.issued.append(rt)
            return _j(200, {"access_token": at, "refresh_token": rt, "expires_in": 3600, "token_type": "Bearer",
                            "scope": "Calendars.ReadWrite"})
        if headers.get("authorization", "")[7:] not in self.access:
            return _j(401, {"error": {"code": "InvalidAuthenticationToken"}})
        if self.fail:
            return _j(self.fail, {"error": {"code": "x"}})
        if method == "GET" and path == "/v1.0/me/calendars":
            return _j(200, {"value": [dict(id=k, name=v, canEdit=True) for k, v in self.calendars.items()]})
        parts = path.split("/")
        if method == "GET" and len(parts) == 5 and parts[3] == "calendars":
            return _j(200, {"id": parts[4], "name": self.calendars.get(parts[4])}) if parts[4] in self.calendars \
                else _j(404, {"error": {"code": "ErrorItemNotFound"}})
        if len(parts) == 6 and parts[3] == "calendars" and parts[5] == "events":
            cid = parts[4]
            if method == "GET":
                assert self.PROP in q.get("$expand", ""), q
                out = []
                for e in self.events.values():
                    if e["calendar"] != cid:
                        continue
                    d = {"id": e["id"], "start": e["start"], "isAllDay": bool(e.get("isAllDay")), "@odata.etag": e["etag"]}
                    props = [p for p in e.get("singleValueExtendedProperties") or [] if p["id"] == self.PROP]
                    if props:
                        d["singleValueExtendedProperties"] = props
                    out.append(d)
                return _j(200, {"value": out})
            if method == "POST":
                ev = json.loads(body)
                gid = "AAMk" + uuid.uuid4().hex
                ev.update(id=gid, calendar=cid, etag='W/"' + uuid.uuid4().hex[:8] + '"')
                self.events[gid] = ev
                return _j(201, {k: v for k, v in ev.items() if k != "calendar"} | {"@odata.etag": ev["etag"]})
        if len(parts) == 5 and parts[3] == "events":
            gid = parts[4]
            if gid not in self.events:
                return _j(404, {"error": {"code": "ErrorItemNotFound"}})
            if method == "PATCH":
                ev = self.events[gid]
                ev.update(json.loads(body))
                ev["etag"] = 'W/"' + uuid.uuid4().hex[:8] + '"'
                return _j(200, {k: v for k, v in ev.items() if k != "calendar"} | {"@odata.etag": ev["etag"]})
            if method == "DELETE":
                del self.events[gid]
                return 204, {}, b""
        return _j(400, {"error": {"code": "BadRequest"}})

    def in_calendar(self, cid="cal-sessions"):
        return [e for e in self.events.values() if e["calendar"] == cid]

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
