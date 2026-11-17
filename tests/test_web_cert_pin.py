import datetime as dt
import ipaddress
import re
import socket
import ssl
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, signed_in_client
from test_upgrade_140 import GID, new_id, q, run, site, state

E = {"REMOTE_ADDR": "127.0.0.1"}

def make_cert(d, name, names=("127.0.0.1",), ca=None, is_ca=False, days=(-1, 365), key=None, cn=None):

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = key or ec.generate_private_key(ec.SECP256R1())
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn or name)])
    now = dt.datetime.now(dt.timezone.utc)
    alt = []
    for n in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            alt.append(x509.DNSName(n))
    b = (x509.CertificateBuilder().subject_name(subj).issuer_name(ca[0].subject if ca else subj)
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now + dt.timedelta(days=days[0])).not_valid_after(now + dt.timedelta(days=days[1])))
    if alt:
        b = b.add_extension(x509.SubjectAlternativeName(alt), critical=False)
    b = b.add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    if is_ca:
        b = b.add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                          key_encipherment=False, data_encipherment=False, key_agreement=False,
                                          encipher_only=False, decipher_only=False), critical=True)
        b = b.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    else:
        b = b.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if ca:
        b = b.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca[1].public_key()), critical=False)
    cert = b.sign(ca[1] if ca else key, hashes.SHA256())
    cp, kp = d / f"{name}.pem", d / f"{name}.key"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return cert, key, cp, kp

def sha_of(cert):
    from cryptography.hazmat.primitives import hashes
    return cert.fingerprint(hashes.SHA256()).hex()

class Front:

    def __init__(self, cert, key, backend=None, answer=None):
        self.backend, self.requests = backend, 0
        self.answer = answer
        self.use(cert, key)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def use(self, cert, key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        self.ctx = ctx

    def _loop(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            tls = self.ctx.wrap_socket(conn, server_side=True)
        except (OSError, ssl.SSLError):
            conn.close()
            return
        try:
            tls.settimeout(10)
            first = tls.recv(65536)
            if not first:
                return
            self.requests += 1
            if self.answer is not None:
                self.answer(tls)
                return
            if self.backend is None:
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
                return
            up = socket.create_connection(("127.0.0.1", self.backend), timeout=10)

            def pump():
                try:
                    while True:
                        b = tls.recv(65536)
                        if not b:
                            break
                        up.sendall(b)
                except OSError:
                    pass
                try:
                    up.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            up.sendall(first)
            threading.Thread(target=pump, daemon=True).start()
            while True:
                b = up.recv(65536)
                if not b:
                    break
                tls.sendall(b)
            up.close()
        except OSError:
            pass
        finally:
            try:
                tls.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                tls.close()
            except OSError:
                pass

    def close(self):
        self.stop = True
        self.sock.close()

@pytest.fixture
def certs(tmp_path):
    d = tmp_path / "certs"
    d.mkdir()
    out = {}
    out["self"] = make_cert(d, "self", names=("127.0.0.1",))
    out["other"] = make_cert(d, "other", names=("127.0.0.1",))
    out["elsewhere"] = make_cert(d, "elsewhere", names=("nas.home.example",))
    out["expired"] = make_cert(d, "expired", names=("127.0.0.1",), days=(-400, -10))
    out["renewed"] = make_cert(d, "renewed", names=("127.0.0.1",), key=out["self"][1], days=(-1, 800))
    out["cn-only"] = make_cert(d, "127.0.0.1", names=(), cn="127.0.0.1")
    ca = make_cert(d, "ca", names=(), is_ca=True)
    out["ca"] = ca
    out["public"] = make_cert(d, "public", names=("127.0.0.1",), ca=ca[:2])
    out["public-wrong-name"] = make_cert(d, "pubwrong", names=("www.example.org",), ca=ca[:2])
    return out

@pytest.fixture
def public_ca(certs, monkeypatch):

    import webtls
    cafile = str(certs["ca"][2])
    monkeypatch.setattr(webtls, "_public_context", lambda: ssl.create_default_context(cafile=cafile))
    return cafile

@pytest.fixture
def pins(monkeypatch):

    import webtls
    held = {}
    monkeypatch.setattr(webtls, "pins", lambda: held)
    notes = []
    monkeypatch.setattr(webtls, "NOTE", lambda a, k, s: notes.append((a, k, s)))
    held_notes = notes
    return held, held_notes

def serve(certs, which, **kw):
    return Front(certs[which][2], certs[which][3], **kw)

def pin(held, addr, cert):
    import webtls
    from cryptography.hazmat.primitives import serialization
    held[addr] = webtls.stamp(webtls.info(cert.public_bytes(serialization.Encoding.DER)))

def test_a_publicly_trusted_certificate_works_as_before(certs, public_ca, pins):
    import hostcheck
    held, notes = pins
    f = serve(certs, "public")
    try:
        st, _h, body = hostcheck._http("GET", f"https://127.0.0.1:{f.port}/x")
        assert (st, body) == (200, b"hello")
        assert held == {} and all(k is None for _a, k, _s in notes)
    finally:
        f.close()

def test_the_public_path_keeps_normal_verification(certs, public_ca, pins):

    import hostcheck
    import webtls
    ctx = webtls._public_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    for which in ("public-wrong-name", "self"):
        f = serve(certs, which)
        try:
            with pytest.raises(webtls.CertProblem) as e:
                hostcheck._http("GET", f"https://127.0.0.1:{f.port}/x")
            assert e.value.kind == "untrusted" and f.requests == 0
        finally:
            f.close()

def test_a_self_signed_certificate_without_a_pin_stops_and_is_shown(certs, pins):
    import hostcheck
    import webtls
    held, notes = pins
    f = serve(certs, "self")
    try:
        with pytest.raises(webtls.CertProblem) as e:
            hostcheck._http("GET", f"https://127.0.0.1:{f.port}/x")
        assert e.value.kind == "untrusted" and f.requests == 0
        seen = e.value.seen
        assert seen["sha256"] == sha_of(certs["self"][0]) and seen["self_signed"] and seen["subject"] == "self"
        assert re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", seen["fp"])
        assert notes[-1][:2] == (f"127.0.0.1:{f.port}", "untrusted") and notes[-1][2]["sha256"] == seen["sha256"]
        assert held == {}
        p = hostcheck.probe(_Nowhere(), f"https://127.0.0.1:{f.port}/g/")
        assert p.kind == "unreachable" and p["cert"] == "untrusted" and hostcheck.why(p) == "cert-untrusted"
        assert p["detail"] == "its certificate isn't trusted"
    finally:
        f.close()

class _Nowhere:

    def put_stream(self, *a, **kw):
        pass

    def delete(self, *a):
        pass

def test_a_pinned_certificate_is_accepted_and_only_that_one(certs, pins):
    import hostcheck
    import webtls
    held, notes = pins
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        pin(held, addr, certs["self"][0])
        assert hostcheck._http("GET", f"https://{addr}/x")[0] == 200
        assert notes[-1] == (addr, None, None)
        f.use(certs["other"][2], certs["other"][3])
        n = f.requests
        with pytest.raises(webtls.CertProblem) as e:
            hostcheck._http("GET", f"https://{addr}/x")
        assert e.value.kind == "changed" and f.requests == n
        assert e.value.seen["sha256"] == sha_of(certs["other"][0]) and e.value.pinned["sha256"] == sha_of(certs["self"][0])
        assert notes[-1][1] == "changed"
        assert held[addr]["sha256"] == sha_of(certs["self"][0])
        assert webtls.check(addr)[0] == "changed"
    finally:
        f.close()

def test_a_pinned_key_in_a_certificate_for_another_name_is_refused(certs, pins):

    import hostcheck
    import webtls
    held, _notes = pins
    f = serve(certs, "elsewhere")
    try:
        addr = f"127.0.0.1:{f.port}"
        pin(held, addr, certs["elsewhere"][0])
        n = f.requests
        with pytest.raises(webtls.CertProblem) as e:
            hostcheck._http("GET", f"https://{addr}/x")
        assert e.value.kind == "changed" and f.requests == n and e.value.seen["name_ok"] is False
        assert webtls.check(addr)[0] == "changed"
    finally:
        f.close()
    g = serve(certs, "self")
    try:
        pin(held, f"127.0.0.1:{g.port}", certs["self"][0])
        assert hostcheck._http("GET", f"https://127.0.0.1:{g.port}/x")[0] == 200
        with pytest.raises(webtls.CertProblem) as e:
            hostcheck._http("GET", f"https://localhost:{g.port}/x")
        assert e.value.kind == "untrusted" and e.value.address == f"localhost:{g.port}"
    finally:
        g.close()

def test_names_are_matched_as_browsers_do():
    import webtls
    i = dict(san=True, names=["photos.example.com", "*.studio.example", "192.168.86.81"], cn="ignored.example")
    assert webtls.names_address(i, "photos.example.com") and webtls.names_address(i, "PHOTOS.example.com.")
    assert webtls.names_address(i, "g.studio.example") and not webtls.names_address(i, "a.b.studio.example")
    assert not webtls.names_address(i, "studio.example") and webtls.names_address(i, "192.168.86.81")
    assert not webtls.names_address(i, "192.168.86.82") and not webtls.names_address(i, "ignored.example")
    assert webtls.names_address(dict(san=False, names=[], cn="nas.home"), "nas.home")
    assert not webtls.names_address(dict(san=True, names=[], cn="nas.home"), "nas.home")
    assert webtls.names_address(dict(san=True, names=["::1"]), "[::1]")

def test_a_certificate_with_only_a_common_name_is_pinned_when_it_names_the_address(certs, pins):
    import hostcheck
    held, _notes = pins
    f = serve(certs, "cn-only")
    try:
        pin(held, f"127.0.0.1:{f.port}", certs["cn-only"][0])
        assert hostcheck._http("GET", f"https://127.0.0.1:{f.port}/x")[0] == 200
    finally:
        f.close()

def test_a_renewal_with_the_same_key_keeps_working_and_a_new_key_stops(certs, pins, monkeypatch):
    import hostcheck
    import webtls
    held, _notes = pins
    renewed = []
    monkeypatch.setattr(webtls, "RENEWED", lambda a, i: renewed.append((a, i)))
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        pin(held, addr, certs["self"][0])
        f.use(certs["renewed"][2], certs["renewed"][3])
        assert hostcheck._http("GET", f"https://{addr}/x")[0] == 200
        assert renewed and renewed[0][0] == addr and renewed[0][1]["sha256"] == sha_of(certs["renewed"][0])
        assert renewed[0][1]["spki"] == held[addr]["spki"] and held[addr]["sha256"] == sha_of(certs["self"][0])
        assert webtls.check(addr)[0] == "pinned"
        f.use(certs["other"][2], certs["other"][3])
        with pytest.raises(webtls.CertProblem) as e:
            hostcheck._http("GET", f"https://{addr}/x")
        assert e.value.kind == "changed"
    finally:
        f.close()

def _answer_redirect(to):
    def answer(tls):
        tls.sendall(f"HTTP/1.1 302 Found\r\nLocation: {to}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    return answer

def test_a_redirect_is_never_followed_and_the_pin_never_relaxes(certs, pins, public_ca):

    import hostcheck
    import webtls
    held, _notes = pins
    other = serve(certs, "public")
    f = Front(certs["self"][2], certs["self"][3], answer=_answer_redirect(f"https://127.0.0.1:{0}/x"))
    try:
        f.answer = _answer_redirect(f"https://127.0.0.1:{other.port}/probe.php")
        addr = f"127.0.0.1:{f.port}"
        pin(held, addr, certs["self"][0])
        st, h, body = hostcheck._http("GET", f"https://{addr}/probe.php")
        assert st == 302 and other.requests == 0 and body == b""
        f.answer = _answer_redirect(f"https://{addr}/elsewhere")
        assert hostcheck._http("GET", f"https://{addr}/probe.php")[0] == 302
        p = hostcheck.probe(_Nowhere(), f"https://{addr}/g/")
        assert p.kind == "unreachable" and other.requests == 0
        assert p["detail"] == "its web address answered 302 for a file just put there"

        import urllib.request
        req = urllib.request.Request(f"https://{addr}/x")
        with pytest.raises(urllib.error.HTTPError) as e:
            webtls.open_request(req, timeout=5, handlers=(urllib.request.HTTPRedirectHandler,))
        assert e.value.code == 302 and other.requests == 0
    finally:
        f.close()
        other.close()

def _answer_stall(tls):
    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nabc")
    import time as _t
    _t.sleep(6)

def _answer_drip(tls):
    import time as _t
    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n")
    for _ in range(40):
        try:
            tls.sendall(b"x")
        except OSError:
            return
        _t.sleep(0.3)

def test_every_read_has_a_timeout(certs, pins):

    import time
    import hostcheck
    held, _notes = pins
    for answer in (_answer_stall, _answer_drip):
        f = Front(certs["self"][2], certs["self"][3], answer=answer)
        try:
            pin(held, f"127.0.0.1:{f.port}", certs["self"][0])
            t0 = time.monotonic()
            with pytest.raises(OSError):
                hostcheck._http("GET", f"https://127.0.0.1:{f.port}/x", timeout=1)
            assert time.monotonic() - t0 < 4.5, answer.__name__
        finally:
            f.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    try:
        port = s.getsockname()[1]
        t0 = time.monotonic()
        with pytest.raises(OSError):
            hostcheck._http("GET", f"https://127.0.0.1:{port}/x", timeout=1)
        import webtls
        assert webtls.peek(f"127.0.0.1:{port}", timeout=1) is None
        assert not webtls.publicly_trusted(f"127.0.0.1:{port}", timeout=1)
        assert time.monotonic() - t0 < 6
    finally:
        s.close()

def test_a_pin_for_one_port_is_not_used_on_another(certs, pins):
    import hostcheck
    import webtls
    held, _notes = pins
    a, b = serve(certs, "self"), serve(certs, "self")
    try:
        pin(held, f"127.0.0.1:{a.port}", certs["self"][0])
        assert hostcheck._http("GET", f"https://127.0.0.1:{a.port}/x")[0] == 200
        with pytest.raises(webtls.CertProblem):
            hostcheck._http("GET", f"https://127.0.0.1:{b.port}/x")
    finally:
        a.close()
        b.close()

def test_an_expired_pinned_certificate_is_still_matched(certs, pins):
    import hostcheck
    import webtls
    held, _notes = pins
    f = serve(certs, "expired")
    try:
        addr = f"127.0.0.1:{f.port}"
        pin(held, addr, certs["expired"][0])
        assert hostcheck._http("GET", f"https://{addr}/x")[0] == 200
        from cryptography.hazmat.primitives.serialization import Encoding
        assert webtls.expired(held[addr])
        assert not webtls.expired(webtls.info(certs["self"][0].public_bytes(Encoding.DER)))
    finally:
        f.close()

def test_other_network_failures_keep_their_words(pins):
    import hostcheck
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = hostcheck.probe(_Nowhere(), f"https://127.0.0.1:{port}/g/")
    assert p.kind == "unreachable" and not p.get("cert") and hostcheck.why(p) == "unreachable"
    assert p["detail"] == "its web address didn't answer"

def test_addresses():
    import webtls
    assert webtls.address("https://Example.COM/g/") == "example.com:443"
    assert webtls.address("https://192.168.86.81:8791/g/") == "192.168.86.81:8791"
    assert webtls.address("https://[::1]:8443/") == "[::1]:8443"
    assert webtls._split("[::1]:8443") == ("::1", 8443)

CHECKS_OFF = re.compile(r"check_hostname\s*=\s*False|CERT_NONE|_create_unverified_context|verify_mode\s*=|"
                        r"PYTHONHTTPSVERIFY|ssl\._create_default_https_context|verify\s*=\s*False")
ALLOWED = {
    "webtls.py",
    "healthcheck.py",
}

def test_no_code_path_turns_certificate_checks_off():
    found = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "data/")) or (rel.startswith(".") and not rel.startswith(".github/")) or "/node_modules/" in rel:
            continue
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            code = line.split("#", 1)[0]
            if CHECKS_OFF.search(code) and rel not in ALLOWED:
                found.append(f"{rel}:{n}: {line.strip()}")
    assert not found, "certificate checks turned off outside webtls.py:\n" + "\n".join(found)

    src = (ROOT / "webtls.py").read_text()
    assert src.count("ssl.CERT_NONE") == 1 and src.count("check_hostname = False") == 1
    assert "def _pinned_context" in src and "if not matches_pin(seen, self._pin, self._addr):" in src
    assert 'i.get("spki") == pin.get("spki") and names_address(i, host_of(addr))' in src

def test_healthcheck_reaches_only_this_machine():
    src = (ROOT / "healthcheck.py").read_text()
    calls = [l_ for l_ in src.splitlines() if "urlopen(" in l_]
    assert calls and all('://127.0.0.1:' in l_ for l_ in calls)

@pytest.fixture
def app_(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def _set_base(A, base, host_dir):
    from vault import web as VW
    host_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host_dir)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', ?)", (base,))
    con.commit()
    con.close()

def test_setup_shows_the_certificate_and_pins_it_once_confirmed(app_, certs, tmp_path, monkeypatch):
    import hostcheck
    import webtls
    A, c = app_
    started = []
    A.app.config["gallery_start_upgrade"] = lambda: started.append(1)
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            with pytest.raises(webtls.CertProblem):
                hostcheck._http("GET", f"https://{addr}/g/x")
        today = c.get("/", environ_base=E).get_data(as_text=True)
        assert "The web host&#39;s certificate isn&#39;t trusted. Check it in Setup." in today
        assert "cannot be reached" not in today and "/setup/galleries#webcert" in today
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        fp = webtls.colon_hex(sha_of(certs["self"][0]))
        assert fp in page and "Trust this certificate" in page and "itself (self-signed)" in page
        assert webtls.pins() == {}

        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {'0' * 64}"}, environ_base=E)
        assert webtls.pins() == {}
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        assert webtls.pins()[addr]["sha256"] == sha_of(certs["self"][0]) and started
        with A.app.app_context():
            assert hostcheck._http("GET", f"https://{addr}/g/x")[0] == 200
        today = c.get("/", environ_base=E).get_data(as_text=True)
        assert "certificate" not in today
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert "The one you confirmed on" in page and fp in page
        con = A.connect()
        assert con.execute("SELECT COUNT(*) FROM audit WHERE action LIKE 'Trusted the web host''s certificate at %'"
                           ).fetchone()[0] == 1
        con.close()
    finally:
        f.close()

def test_a_changed_certificate_stops_and_setup_shows_both(app_, certs, tmp_path):
    import hostcheck
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            webtls.check(addr)
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        f.use(certs["other"][2], certs["other"][3])
        with A.app.app_context():
            with pytest.raises(webtls.CertProblem) as e:
                hostcheck._http("GET", f"https://{addr}/g/x")
        assert e.value.kind == "changed"
        today = c.get("/", environ_base=E).get_data(as_text=True)
        assert "The web host&#39;s certificate changed. Check it in Setup." in today
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        old, new = webtls.colon_hex(sha_of(certs["self"][0])), webtls.colon_hex(sha_of(certs["other"][0]))
        assert old in page and new in page and "Trust the new certificate" in page and "Keep the old one" in page

        c.post("/setup/web-cert/refuse", data={"addr": addr}, environ_base=E)
        assert webtls.pins()[addr]["sha256"] == sha_of(certs["self"][0])
        with A.app.app_context():
            with pytest.raises(webtls.CertProblem):
                hostcheck._http("GET", f"https://{addr}/g/x")
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert "You refused this one." in page and "Keep the old one" not in page

        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['other'][0])}"}, environ_base=E)
        assert webtls.pins()[addr]["sha256"] == sha_of(certs["other"][0])
        with A.app.app_context():
            assert hostcheck._http("GET", f"https://{addr}/g/x")[0] == 200
        assert "certificate" not in c.get("/", environ_base=E).get_data(as_text=True)
    finally:
        f.close()

def test_an_expired_pinned_certificate_is_noted_on_today(app_, certs, tmp_path):
    import hostcheck
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "expired")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            webtls.check(addr)
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['expired'][0])}"}, environ_base=E)
        with A.app.app_context():
            assert hostcheck._http("GET", f"https://{addr}/g/x")[0] == 200
        end = certs["expired"][0].not_valid_after_utc.date()
        today = c.get("/", environ_base=E).get_data(as_text=True)
        assert f"The web host&#39;s certificate expired on {end.strftime('%B')} {end.day}, {end.year}." in today
        assert "isn&#39;t trusted" not in today
        assert f"It expired on {end.isoformat()}" in c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    finally:
        f.close()

def test_a_publicly_trusted_certificate_confirmed_forgets_the_pin(app_, certs, tmp_path, public_ca):

    import hostcheck
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            webtls.check(addr)
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        assert addr in webtls.pins()
        f.use(certs["public"][2], certs["public"][3])
        with A.app.app_context():
            with pytest.raises(webtls.CertProblem):
                hostcheck._http("GET", f"https://{addr}/g/x")
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['public'][0])}"}, environ_base=E)
        assert addr not in webtls.pins()
        with A.app.app_context():
            assert hostcheck._http("GET", f"https://{addr}/g/x")[0] == 200
    finally:
        f.close()

def test_the_host_check_panel_shows_the_certificate(app_, certs, tmp_path):
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        c.post("/setup/host-check", environ_base=E)
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert "Its certificate isn't trusted yet: check it below." in page
        assert "its certificate must be checked first" in page
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert "Its certificate is the one you confirmed below." in page
        assert webtls.pins()[addr]
    finally:
        f.close()

def test_an_unknown_address_is_never_checked_or_pinned(app_, certs, tmp_path):
    import webtls
    A, c = app_
    f = serve(certs, "self")
    try:
        _set_base(A, "https://127.0.0.1:1/g/", tmp_path / "web" / "g")
        addr = f"127.0.0.1:{f.port}"
        c.post("/setup/web-cert/check", data={"addr": addr}, environ_base=E)
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        assert webtls.pins() == {} and f.requests == 0
        con = A.connect()
        assert not con.execute("SELECT 1 FROM settings WHERE key LIKE 'web_cert_seen:%'").fetchall()
        con.close()
    finally:
        f.close()

def test_the_move_waits_for_the_certificate_then_completes_once_confirmed(site, certs, monkeypatch):
    s = site
    f = serve(certs, "self", backend=s.port)
    try:
        import hostcheck
        import webtls
        monkeypatch.setattr(hostcheck, "HTTP", hostcheck._http)
        addr = f"127.0.0.1:{f.port}"
        base = f"https://{addr}/g/"
        con = s.A.connect()
        con.execute("UPDATE settings SET value=? WHERE key='gallery_base_url'", (base,))
        con.execute("UPDATE galleries SET link_base=?", (base,))
        con.commit()
        con.close()
        s.A.app.config["gallery_start_upgrade"] = lambda: None
        run(s)
        assert q(s, "SELECT gallery_id FROM sessions WHERE id=1")[0][0] == GID
        assert (state(s).get("waits") or {}).get(GID, {}).get("why") == "cert-untrusted"
        today = s.c.get("/", environ_base=E).get_data(as_text=True)
        assert "Updating galleries: 0 of 1 done." in today
        assert "The web host&#39;s certificate isn&#39;t trusted. Check it in Setup." in today
        assert "cannot be reached" not in today
        assert today.count("certificate isn&#39;t trusted") == 1

        page = s.c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert webtls.colon_hex(sha_of(certs["self"][0])) in page
        s.c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        for _ in range(3):
            run(s)
            if state(s).get("finished"):
                break
        assert state(s).get("finished"), state(s)
        new = new_id(s)
        assert new.startswith("g-") and (s.web / "g" / new / "index.html").is_file()
        assert "certificate" not in s.c.get("/", environ_base=E).get_data(as_text=True)

        f.use(certs["other"][2], certs["other"][3])
        p = hostcheck.probe(_Nowhere(), base)
        assert p.kind == "unreachable" and hostcheck.why(p) == "cert-changed"
        assert "The web host&#39;s certificate changed. Check it in Setup." in s.c.get("/", environ_base=E).get_data(as_text=True)
    finally:
        f.close()

def test_the_studio_page_form_waits_with_the_certificate_named(monkeypatch):
    import upgrade_140
    st = {"site_wait": {"why": "cert-untrusted", "words": "its certificate isn't trusted"}}
    monkeypatch.setattr(upgrade_140, "load", lambda q_: st)
    monkeypatch.setattr(upgrade_140, "online_rows", lambda q_: [])
    lines = upgrade_140.status_line(None, None)
    assert lines == ["Updating the studio page's inquiry form: it waits for its web host. The web host's certificate "
                     "isn't trusted. Check it in Setup."]

def test_setup_says_plainly_when_a_certificate_names_another_host_and_never_pins_it(app_, certs, tmp_path):
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "elsewhere")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            assert webtls.check(addr)[0] == "untrusted"
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert ("This certificate is for nas.home.example, not for 127.0.0.1, the address you set for the web host."
                in page)
        assert "Trust this certificate" not in page
        r = c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['elsewhere'][0])}"}, environ_base=E)
        assert "cert=name" in r.headers["Location"] and webtls.pins() == {}
        page = c.get(r.headers["Location"], environ_base=E).get_data(as_text=True)
        assert "Not trusted: that certificate doesn't name the web host's address." in page
    finally:
        f.close()

def test_setup_and_today_follow_a_renewal_with_the_same_key(app_, certs, tmp_path):
    import hostcheck
    import webtls
    A, c = app_
    A.app.config["gallery_start_upgrade"] = lambda: None
    f = serve(certs, "self")
    try:
        addr = f"127.0.0.1:{f.port}"
        _set_base(A, f"https://{addr}/g/", tmp_path / "web" / "g")
        with A.app.app_context():
            webtls.check(addr)
        c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(certs['self'][0])}"}, environ_base=E)
        f.use(certs["renewed"][2], certs["renewed"][3])
        webtls._RENEWED_SEEN.clear()
        with A.app.app_context():
            assert hostcheck._http("GET", f"https://{addr}/g/x")[0] == 200
        page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
        assert "Renewed since, with the same key" in page and webtls.colon_hex(sha_of(certs["renewed"][0])) in page
        assert "certificate" not in c.get("/", environ_base=E).get_data(as_text=True)
    finally:
        f.close()
