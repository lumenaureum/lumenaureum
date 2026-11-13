"""A trusted proxy sending a client address that is not an IP is refused, and the app's own certificate is made once, names its hosts, renews near expiry, and reports a stable fingerprint."""
import ipaddress
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules  # noqa: E402


def _gate(netsec):
    seen = {}

    def inner(environ, start_response):
        seen.update(environ)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    return netsec.ProxyGate(inner), seen


def _call(gate, environ):
    status = {}

    def start_response(s, headers):
        status["s"] = s

    body = b"".join(gate(environ, start_response))
    return status["s"], body


@pytest.fixture
def netsec(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", "10.0.0.5")
    monkeypatch.setenv("CRM_HOSTNAMES", "studio.example.com")
    for v in ("CRM_TLS_CERT", "CRM_TLS_KEY", "CRM_TLS_SAN"):
        monkeypatch.delenv(v, raising=False)
    fresh_modules()
    import netsec as N
    yield N
    fresh_modules()


@pytest.mark.parametrize("fwd", ["not-an-address", "<script>", "1.2.3.4.5"])
def test_trusted_proxy_with_non_ip_forwarded_for_is_refused(netsec, fwd):
    gate, seen = _gate(netsec)
    status, body = _call(gate, {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": fwd})
    assert status.startswith("400")
    assert b"not an IP address" in body
    assert not seen                                       # the app itself never ran


def test_trusted_proxy_with_real_address_passes(netsec):
    gate, seen = _gate(netsec)
    status, _ = _call(gate, {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "192.168.1.7, 10.0.0.5",
                             "HTTP_X_FORWARDED_PROTO": "https", "wsgi.url_scheme": "http"})
    assert status.startswith("200")
    assert seen["REMOTE_ADDR"] == "192.168.1.7"
    assert seen["lumen.peer"] == "10.0.0.5"
    assert seen["wsgi.url_scheme"] == "https"


def _load(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_certificate_made_once_with_names_and_private_key(netsec, monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_TLS_SAN", "192.168.1.50, nas.local")
    cert, key = netsec.ensure_cert()
    assert cert == tmp_path / "data" / "tls" / "cert.pem" and key == tmp_path / "data" / "tls" / "key.pem"
    assert oct(os.stat(key).st_mode & 0o777) == "0o600"
    from cryptography import x509
    san = _load(cert).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = set(san.get_values_for_type(x509.DNSName))
    ips = set(san.get_values_for_type(x509.IPAddress))
    assert {"localhost", "studio.example.com", "nas.local"} <= dns
    assert "192.168.1.50" not in dns
    assert {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("192.168.1.50")} <= ips
    fp = netsec.fingerprint()
    assert re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", fp)
    before = cert.read_bytes()
    assert netsec.ensure_cert() == (cert, key)
    assert cert.read_bytes() == before                    # kept, so the fingerprint stays the same
    assert netsec.fingerprint(cert) == fp


def _write_cert(netsec, cert, key, days_left):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "old")])
    now = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=700))
         .not_valid_after(now + timedelta(days=days_left)).sign(k, hashes.SHA256()))
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))


def test_own_certificate_renews_near_expiry(netsec):
    cert, key = netsec.cert_paths()
    _write_cert(netsec, cert, key, days_left=10)
    old = netsec.fingerprint(cert)
    assert netsec._expiring(cert)
    netsec.ensure_cert()
    assert netsec.fingerprint(cert) != old
    assert not netsec._expiring(cert)


def test_own_certificate_not_renewed_far_from_expiry(netsec):
    cert, key = netsec.cert_paths()
    _write_cert(netsec, cert, key, days_left=200)
    old = cert.read_bytes()
    netsec.ensure_cert()
    assert cert.read_bytes() == old


def test_unreadable_certificate_counts_as_expiring(netsec, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    assert netsec._expiring(bad)
    assert netsec._expiring(tmp_path / "missing.pem")
    assert netsec.fingerprint(bad) == ""
    assert netsec.fingerprint(tmp_path / "missing.pem") == ""


def test_supplied_certificate_is_never_replaced(netsec, monkeypatch, tmp_path):
    cert, key = tmp_path / "mine" / "cert.pem", tmp_path / "mine" / "key.pem"
    _write_cert(netsec, cert, key, days_left=5)
    monkeypatch.setenv("CRM_TLS_CERT", str(cert))
    monkeypatch.setenv("CRM_TLS_KEY", str(key))
    before = cert.read_bytes()
    assert netsec.ensure_cert() == (cert, key)
    assert cert.read_bytes() == before
    assert not (tmp_path / "data" / "tls").exists()


def test_new_address_in_tls_san_makes_a_new_certificate(netsec, monkeypatch):
    """CRM_TLS_SAN changed after the first start (a new address): the next start makes a certificate that names it."""
    monkeypatch.setenv("CRM_TLS_SAN", "192.168.1.40")
    cert, _ = netsec.ensure_cert()
    first = cert.read_bytes()
    netsec.ensure_cert()
    assert cert.read_bytes() == first                      # same names: kept
    monkeypatch.setenv("CRM_TLS_SAN", "192.168.1.40,10.0.0.40")
    netsec.ensure_cert()
    assert cert.read_bytes() != first and netsec._covers(cert, ["10.0.0.40", "192.168.1.40"])
