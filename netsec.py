import hashlib
import ipaddress
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _nets(var):
    return [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get(var, "").split(",") if n.strip()]

def tls_mode():
    return "off" if os.environ.get("CRM_TLS", "").strip().lower() in ("off", "0", "no", "false") else "on"

TRUSTED_PROXIES = _nets("CRM_TRUSTED_PROXIES")
HOSTNAMES = {h.strip().lower() for h in os.environ.get("CRM_HOSTNAMES", "").split(",") if h.strip()} | {"localhost"}

def startup_problem():

    if tls_mode() == "off" and not TRUSTED_PROXIES:
        return ("CRM_TLS=off is only for running behind your own HTTPS reverse proxy: set CRM_TRUSTED_PROXIES to its "
                "address. Lumen Aureum never serves plain HTTP. See docs/where-it-runs.md.")

    data_dir = Path(os.environ.get("CRM_DB", "/data/crm.db")).parent
    folders = [("data", data_dir)]
    for name_, env_, dflt in (("photos", "CRM_PHOTOS_ROOT", "/photos"), ("vault", None, "/vault")):
        p_ = Path(os.environ.get(env_, dflt)) if env_ else Path(dflt)
        if p_.exists():
            folders.append((name_, p_))
    uid = os.getuid() if hasattr(os, "getuid") else None
    for name_, p_ in folders:
        if not p_.is_dir():
            return (f"The {name_} folder {p_} is missing. Create it on the host and mount it into the container "
                    f"(see docs/where-it-runs.md).")
        if not os.access(p_, os.W_OK | os.X_OK):
            return (f"The {name_} folder {p_} isn't writable by the user this app runs as (uid {uid}). On TrueNAS, set that "
                    f"dataset's owner to the apps user (568), or give that user write permission, then start the app again.")
    for f_ in sorted(data_dir.glob("*.json")) + sorted(data_dir.glob("*.db")):
        if not os.access(f_, os.R_OK | os.W_OK):
            return (f"The file {f_} can't be read and written by the user this app runs as (uid {uid}). Give it the same "
                    f"owner as the rest of the data folder (see docs/operations.md, Restoring a backup), then start again.")
    for cert_ in (os.environ.get("CRM_TLS_CERT"), os.environ.get("CRM_TLS_KEY")):
        if cert_ and not os.access(cert_, os.R_OK):
            return (f"The certificate file {cert_} can't be read by the user this app runs as (uid {uid}). Make it readable "
                    f"by that user, or remove CRM_TLS_CERT/CRM_TLS_KEY to use the app's own certificate.")
    return None

def default_gateway():

    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                return ipaddress.ip_address(socket.inet_ntoa(bytes.fromhex(f[2])[::-1]))
    except (OSError, ValueError):
        pass
    return None

def health_probe(request):

    try:
        peer = request.environ.get("lumenaureum.peer", request.remote_addr)
        return request.endpoint == "healthz" and ipaddress.ip_address(peer).is_loopback
    except (ValueError, TypeError):
        return False

def explicitly_allowed(ip):
    return any(ip in n for n in _nets("CRM_ALLOW") + _nets("CRM_ALLOW_ONLY"))

GATEWAY = default_gateway()

def refused_as_gateway(addr):

    try:
        ip = ipaddress.ip_address(addr)
    except (ValueError, TypeError):
        return False
    return GATEWAY is not None and ip == GATEWAY and not explicitly_allowed(ip) and not _in(ip, TRUSTED_PROXIES)

def _in(ip, nets):
    return any(ip in n for n in nets)

class ProxyGate:

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        peer = environ.get("REMOTE_ADDR", "")
        environ["lumenaureum.peer"] = peer
        fwd = environ.get("HTTP_X_FORWARDED_FOR")
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            peer_ip = None
        if peer_ip is not None and _in(peer_ip, TRUSTED_PROXIES):
            if fwd:
                hops = [h.strip() for h in fwd.split(",") if h.strip()]
                client = next((h for h in reversed(hops) if not _trusted_str(h)), hops[0] if hops else peer)
                try:
                    environ["REMOTE_ADDR"] = str(ipaddress.ip_address(client))
                except ValueError:
                    start_response("400 Bad Request", [("Content-Type", "text/plain")])
                    return [b"The proxy sent a client address that is not an IP address.\n"]
            if proto in ("http", "https"):
                environ["wsgi.url_scheme"] = proto
        elif fwd or proto or environ.get("HTTP_FORWARDED"):
            start_response("400 Bad Request", [("Content-Type", "text/plain")])
            return [b"Forwarded headers from an untrusted address. Set CRM_TRUSTED_PROXIES to your proxy's address.\n"]
        return self.app(environ, start_response)

def _trusted_str(h):
    try:
        return _in(ipaddress.ip_address(h), TRUSTED_PROXIES)
    except ValueError:
        return False

def host_ok(host):

    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:h.find("]")] if "]" in h else h
    elif h.count(":") == 1:
        h = h.split(":")[0]
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return h in HOSTNAMES

def secure_request(request):

    return request.scheme == "https"

def tls_dir():
    return Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db")).parent / "tls"

def cert_paths():
    if os.environ.get("CRM_TLS_CERT") and os.environ.get("CRM_TLS_KEY"):
        return Path(os.environ["CRM_TLS_CERT"]), Path(os.environ["CRM_TLS_KEY"])
    return tls_dir() / "cert.pem", tls_dir() / "key.pem"

def ensure_cert():

    cert, key = cert_paths()
    extra_now = [h.strip() for h in os.environ.get("CRM_TLS_SAN", "").split(",") if h.strip()]
    if cert.exists() and key.exists():
        if os.environ.get("CRM_TLS_CERT") or (not _expiring(cert) and _covers(cert, extra_now)):
            return cert, key
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    extra = [h.strip() for h in os.environ.get("CRM_TLS_SAN", "").split(",") if h.strip()]
    names = [x509.DNSName("localhost")] + [x509.DNSName(h) for h in sorted((HOSTNAMES | {e for e in extra if not _is_ip(e)}) - {"localhost"})]
    ips = [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))] + [x509.IPAddress(ipaddress.ip_address(e)) for e in extra if _is_ip(e)]
    try:
        ips.append(x509.IPAddress(ipaddress.ip_address(socket.gethostbyname(socket.gethostname()))))
    except OSError:
        pass
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lumen Aureum")])
    now = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
         .not_valid_after(now + timedelta(days=730))
         .add_extension(x509.SubjectAlternativeName(names + ips), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    cert.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key

def _covers(cert, extra):

    if not extra:
        return True
    from cryptography import x509
    try:
        c = x509.load_pem_x509_certificate(Path(cert).read_bytes())
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    have = {str(v) for v in san.get_values_for_type(x509.IPAddress)} | {v.lower() for v in san.get_values_for_type(x509.DNSName)}
    return all((str(ipaddress.ip_address(e)) if _is_ip(e) else e.lower()) in have for e in extra)

def _is_ip(v):
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False

def _expiring(cert, days=30):

    from cryptography import x509
    try:
        c = x509.load_pem_x509_certificate(Path(cert).read_bytes())
    except (OSError, ValueError):
        return True
    return c.not_valid_after_utc < datetime.now(timezone.utc) + timedelta(days=days)

def fingerprint(cert=None):

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    p = cert or cert_paths()[0]
    try:
        c = x509.load_pem_x509_certificate(Path(p).read_bytes())
    except (OSError, ValueError):
        return ""
    return ":".join(f"{b:02X}" for b in c.fingerprint(hashes.SHA256()))

CLAIM = {"code": None}

CODE_FILE = "SETUP-CODE.txt"

def code_file():
    return tls_dir().parent / CODE_FILE

def claim_code():

    if CLAIM["code"] is None:
        CLAIM["code"] = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        try:
            p = code_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(f"Lumen Aureum setup code: {CLAIM['code']}\n\nType it on the first setup page. This file is "
                        f"deleted once setup is done. If Lumen Aureum restarts before then, a new code replaces this one.\n")
        except OSError:
            pass
    return CLAIM["code"]

RECOVERY = {"code": None, "at": 0.0}
RECOVERY_FILE = "RECOVERY-CODE.txt"
RECOVERY_MINUTES = 15

def recovery_code():

    import time
    if RECOVERY["code"] is None or time.time() - RECOVERY["at"] > RECOVERY_MINUTES * 60:
        RECOVERY.update(code="-".join(secrets.token_hex(2).upper() for _ in range(3)), at=time.time())
        try:
            p = tls_dir().parent / RECOVERY_FILE
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(f"Lumen Aureum recovery code: {RECOVERY['code']}\n\nType it with your master key on the "
                        f"'Forgot your password?' page within {RECOVERY_MINUTES} minutes. It works once.\n")
        except OSError:
            pass
    return RECOVERY["code"]

def recovery_ok(typed):
    import hmac
    import time
    code = RECOVERY["code"]
    return bool(code and typed and time.time() - RECOVERY["at"] <= RECOVERY_MINUTES * 60
                and hmac.compare_digest(typed.strip().upper(), code))

def forget_recovery_code():
    RECOVERY.update(code=None, at=0.0)
    try:
        (tls_dir().parent / RECOVERY_FILE).unlink()
    except OSError:
        pass

def forget_claim_code():
    CLAIM["code"] = None
    try:
        code_file().unlink()
    except OSError:
        pass

def banner(unclaimed, plugins=(), plugin_errors=()):
    lines = ["", "=" * 66, " Lumen Aureum"]
    own = [f"{os.path.basename(f.rstrip('/')) or f}/{n}" for n, b, f in plugins if not b]
    if own:
        import textwrap
        warn = ("*** WARNING *** : The following plugins are loaded and are not part of Lumen Aureum: " + ", ".join(own) + "."
                " Plugins run with full access and can break the protections Lumen Aureum is built on, which can lead to"
                " exposure or loss of data, including client data.")
        lines += [" " + ln for ln in textwrap.wrap(warn, 65, break_long_words=False, break_on_hyphens=False)]
    for e in plugin_errors:
        lines.append(f" Plugin not loaded: {e}")
    if tls_mode() == "on":
        lines.append(f" HTTPS certificate fingerprint (SHA-256):\n   {fingerprint()}")
        lines.append(" Check your browser shows this same fingerprint the first time you connect.")
    if unclaimed:
        lines.append(f" Setup code: {claim_code()}   (also in {CODE_FILE} in the data folder)")
    lines += ["=" * 66, ""]
    print("\n".join(lines), flush=True)
