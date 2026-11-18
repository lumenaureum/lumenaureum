import datetime
import hashlib
import hmac
import http.server
import re
import socket
import ssl
import threading
import urllib.parse
from xml.sax.saxutils import escape

ACCESS, SECRET, REGION = "AKFAKE", "fake-secret-key", "us-east-1"

def make_cert(tmp_path, name="localhost", ip="127.0.0.1"):

    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name), x509.IPAddress(ipaddress.ip_address(ip))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    tag = hashlib.sha256(cert.fingerprint(hashes.SHA256())).hexdigest()[:8]
    cp, kp = tmp_path / f"cert-{tag}.pem", tmp_path / f"key-{tag}.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return str(cp), str(kp)

def _q(s):
    return urllib.parse.quote(s, safe="-_.~")

def _sig_ok(method, raw_path, raw_query, headers, payload_hash):

    auth = headers.get("Authorization", "")
    m = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, "
                     r"SignedHeaders=([a-z0-9;-]+), Signature=([0-9a-f]{64})", auth)
    if not m or m.group(1) != ACCESS:
        return "InvalidAccessKeyId"
    date, region, signed, sig = m.group(2), m.group(3), m.group(4).split(";"), m.group(5)
    if "host" not in signed or "x-amz-date" not in signed:
        return "AccessDenied"
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
    cq = "&".join(f"{k}={v}" for k, v in sorted((_q(k), _q(v)) for k, v in pairs))
    ch = "".join(f"{h}:{' '.join((headers.get(h) or '').split())}\n" for h in signed)
    creq = "\n".join([method, raw_path, cq, ch, ";".join(signed), payload_hash])
    amz = headers.get("x-amz-date", "")
    scope = f"{date}/{region}/s3/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = hmac.new(("AWS4" + SECRET).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    want = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return None if hmac.compare_digest(want, sig) else "SignatureDoesNotMatch"

class FakeS3:
    def __init__(self, buckets=("test-bucket",), tls=None, page=1000, host="127.0.0.1", region=None, port=0):
        self.objects = {b: {} for b in buckets}
        self.uploads = {}
        self.log = []
        self.fail = []
        self.drop = 0
        self.cut = 0
        self.page = page
        self.region = region
        self.lock = threading.Lock()
        self._n = 0
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.handle(self, "GET")

            def do_PUT(self):
                fake.handle(self, "PUT")

            def do_POST(self):
                fake.handle(self, "POST")

            def do_DELETE(self):
                fake.handle(self, "DELETE")

            def do_HEAD(self):
                fake.handle(self, "HEAD")

        self.srv = http.server.ThreadingHTTPServer((host, port), H)
        self.srv.daemon_threads = True
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        self.scheme = "https" if tls else "http"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    @property
    def endpoint(self):
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _send(self, h, status, body=b"", headers=None, head=False):
        h.send_response(status)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body and not head:
            h.wfile.write(body)

    def _err(self, h, status, code, head=False, extra=None):
        body = (f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{code}</Message>'
                f"</Error>").encode()
        self._send(h, status, body, dict({"Content-Type": "application/xml"}, **(extra or {})), head)

    def handle(self, h, method):
        u = urllib.parse.urlsplit(h.path)
        raw_path, raw_query = u.path, u.query
        n = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(n) if n else b""
        q = dict(urllib.parse.parse_qsl(raw_query, keep_blank_values=True))
        host = (h.headers.get("Host") or "").split(":")[0]
        parts = urllib.parse.unquote(raw_path).split("/", 2)
        if host not in ("127.0.0.1", "localhost") and not re.fullmatch(r"[\d.]+", host):
            bucket, key = host.split(".", 1)[0], urllib.parse.unquote(raw_path)[1:]
        else:
            bucket, key = parts[1], (parts[2] if len(parts) > 2 else "")
        with self.lock:
            self.log.append((method, bucket, key, q, h.headers.get("Host")))
            if self.drop:
                self.drop -= 1
                h.close_connection = True
                try:
                    h.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            if self.fail:
                st = self.fail.pop(0)
                return self._err(h, st, "InternalError" if st == 500 else "SlowDown", method == "HEAD")
        ph = h.headers.get("x-amz-content-sha256", "")
        if ph != hashlib.sha256(body).hexdigest():
            return self._err(h, 400, "XAmzContentSHA256Mismatch", method == "HEAD")
        bad = _sig_ok(method, raw_path, raw_query, h.headers, ph)
        if bad:
            return self._err(h, 403, bad, method == "HEAD")
        auth_region = re.search(r"Credential=[^/]+/\d{8}/([^/]+)/", h.headers["Authorization"]).group(1)
        if self.region and auth_region != self.region:
            return self._err(h, 400, "AuthorizationHeaderMalformed", method == "HEAD",
                             {"x-amz-bucket-region": self.region})
        if bucket not in self.objects:
            return self._err(h, 404, "NoSuchBucket", method == "HEAD")
        objs = self.objects[bucket]
        meta = {k: h.headers[k] for k in ("Content-Type", "Cache-Control") if h.headers.get(k)}
        if method == "GET" and not key:
            return self._list(h, objs, q)
        if method == "POST" and "uploads" in q:
            with self.lock:
                self._n += 1
                uid = f"up{self._n}"
                self.uploads[uid] = (bucket, key, {}, meta)
            return self._send(h, 200, f"<InitiateMultipartUploadResult><Bucket>{bucket}</Bucket><Key>{escape(key)}</Key>"
                                      f"<UploadId>{uid}</UploadId></InitiateMultipartUploadResult>".encode())
        if "uploadId" in q:
            up = self.uploads.get(q["uploadId"])
            if not up:
                return self._err(h, 404, "NoSuchUpload")
            if method == "PUT":
                up[2][int(q["partNumber"])] = body
                return self._send(h, 200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})
            if method == "DELETE":
                del self.uploads[q["uploadId"]]
                return self._send(h, 204)
            if method == "POST":
                nums = [int(x) for x in re.findall(rb"<PartNumber>(\d+)</PartNumber>", body)]
                etags = re.findall(rb"<ETag>([^<]+)</ETag>", body)
                for i, (num, et) in enumerate(zip(nums, etags)):
                    data = up[2].get(num)
                    if data is None or et.decode().replace("&quot;", '"') != f'"{hashlib.md5(data).hexdigest()}"':
                        return self._err(h, 400, "InvalidPart")
                    if i < len(nums) - 1 and len(data) < 5 * 1024 * 1024:
                        return self._err(h, 400, "EntityTooSmall")
                objs[up[1]] = (b"".join(up[2][x] for x in nums), up[3])
                del self.uploads[q["uploadId"]]
                return self._send(h, 200, f"<CompleteMultipartUploadResult><Key>{escape(key)}</Key>"
                                          "</CompleteMultipartUploadResult>".encode())
        if method == "PUT":
            objs[key] = (body, meta)
            return self._send(h, 200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})
        if method in ("GET", "HEAD"):
            if key not in objs:
                return self._err(h, 404, "NoSuchKey", method == "HEAD")
            data, m = objs[key]
            rng = h.headers.get("Range")
            status, start = 200, 0
            if rng:
                start = int(re.fullmatch(r"bytes=(\d+)-", rng).group(1))
                status = 206
            out = data[start:]
            hd = dict(m, ETag=f'"{hashlib.md5(data).hexdigest()}"')
            if status == 206:
                hd["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
            if method == "GET" and self.cut and len(out) > 2:
                with self.lock:
                    self.cut -= 1
                h.send_response(status)
                for k, v in hd.items():
                    h.send_header(k, v)
                h.send_header("Content-Length", str(len(out)))
                h.end_headers()
                h.wfile.write(out[:len(out) // 2])
                h.wfile.flush()
                h.close_connection = True
                h.connection.shutdown(socket.SHUT_RDWR)
                return
            if method == "HEAD":
                h.send_response(200)
                for k, v in hd.items():
                    h.send_header(k, v)
                h.send_header("Content-Length", str(len(data)))
                h.end_headers()
                return
            return self._send(h, status, out, hd)
        if method == "DELETE":
            objs.pop(key, None)
            return self._send(h, 204)
        return self._err(h, 400, "NotImplemented")

    def _list(self, h, objs, q):
        if q.get("list-type") != "2":
            return self._err(h, 400, "NotImplemented")
        keys = sorted(k for k in objs if k.startswith(q.get("prefix", "")))
        start = int(q.get("continuation-token", "t0")[1:])
        chunk = keys[start:start + self.page]
        more = start + self.page < len(keys)
        xs = "".join(f"<Contents><Key>{escape(k)}</Key><Size>{len(objs[k][0])}</Size></Contents>" for k in chunk)
        tail = f"<NextContinuationToken>t{start + self.page}</NextContinuationToken>" if more else ""
        body = ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<KeyCount>{len(chunk)}</KeyCount><IsTruncated>{'true' if more else 'false'}</IsTruncated>{xs}{tail}"
                "</ListBucketResult>").encode()
        return self._send(h, 200, body, {"Content-Type": "application/xml"})
