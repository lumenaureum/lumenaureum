import hashlib
import io
import json
import re
import secrets
import time
import urllib.error
import urllib.request

PHP_FLOOR = (7, 4)
DOOR_LIMIT = 300
SCRIPT_SET = "1.6.0"
PROBE_PHP = """<?php
header('Cache-Control: no-store');
header('Content-Type: application/json');
$c = function_exists('openssl_get_cipher_methods') ? openssl_get_cipher_methods() : array();
echo json_encode(array('la' => 1, 'php' => PHP_VERSION, 'openssl' => extension_loaded('openssl'),
  'gcm' => in_array('aes-256-gcm', $c, true), 'sha256' => hash('sha256', 'abc') === 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  'memory_limit' => (string)ini_get('memory_limit'), 'max_execution_time' => (string)ini_get('max_execution_time'),
  'opcache_enable' => (string)ini_get('opcache.enable'), 'validate_timestamps' => (string)ini_get('opcache.validate_timestamps'),
  'revalidate_freq' => (string)ini_get('opcache.revalidate_freq'), 'ra' => (string)($_SERVER['REMOTE_ADDR'] ?? ''),
  'fwd' => array_values(array_filter(array('HTTP_X_FORWARDED_FOR', 'HTTP_FORWARDED', 'HTTP_CF_CONNECTING_IP', 'HTTP_X_REAL_IP',
    'HTTP_TRUE_CLIENT_IP', 'HTTP_FASTLY_CLIENT_IP', 'HTTP_X_CLUSTER_CLIENT_IP', 'HTTP_VIA', 'HTTP_CDN_LOOP'),
    function ($h) { return isset($_SERVER[$h]); }))));
"""

def _http(method, url, headers=None, data=None, timeout=20):

    import os
    from urllib.parse import urlsplit
    import webtls
    if os.environ.get("CRM_TESTING") and (urlsplit(url).hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        raise OSError("tests reach only this machine")
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}, **{"User-Agent": "Lumen Aureum"}))
    deadline = time.monotonic() + 2 * timeout
    try:
        with webtls.open_request(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, webtls.read(r, 2_000_000, deadline)
    except urllib.error.HTTPError as e:
        try:
            body = webtls.read(e, 2_000_000, deadline)
        except Exception:
            body = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body

HTTP = _http
SLEEP = time.sleep

def join(base, path):
    return (base if base.endswith("/") else base + "/") + path.lstrip("/")

class Probe(dict):

    @property
    def kind(self):
        return self["kind"]

def why(p):

    c = p.get("cert") or ""
    return f"cert-{c}" if c in ("untrusted", "changed") else "unreachable"

CERT_WAITS = ("cert-untrusted", "cert-changed")

def _version(v):
    m = re.match(r"(\d+)\.(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None

def door_wait(info):

    on = str(info.get("opcache_enable", "")).strip().lower() in ("1", "on", "true", "yes")
    if not on:
        return 2
    vt = str(info.get("validate_timestamps", "1")).strip().lower() in ("1", "on", "true", "yes", "")
    if not vt:
        return 62
    try:
        return max(0, int(float(info.get("revalidate_freq") or 0))) + 2
    except ValueError:
        return 4

def _no_answer(e):

    import socket
    import ssl
    import webtls
    r = getattr(e, "reason", e)
    if isinstance(r, webtls.CertProblem):
        return "its certificate changed" if r.kind == "changed" else "its certificate isn't trusted"
    if isinstance(r, (TimeoutError, socket.timeout)):
        return "its web address didn't answer in time"
    if isinstance(r, ssl.SSLError):
        return "its web address didn't answer over a secure connection"
    if isinstance(r, socket.gaierror):
        return "its web address could not be found"
    return "its web address didn't answer"

def probe(host, base_url):

    name = f"probe-{secrets.token_hex(8)}.php"
    try:
        host.put_stream(name, io.BytesIO(PROBE_PHP.encode()), content_type="application/x-httpd-php", cache_control="no-store")
    except Exception as e:
        return Probe(kind="unreachable", version="", wait=0, detail="the host would not take a small test file")
    try:
        try:
            st, _h, body = HTTP("GET", join(base_url, name))
        except Exception as e:
            import webtls
            return Probe(kind="unreachable", version="", wait=0, detail=_no_answer(e),
                         cert=e.kind if isinstance(e, webtls.CertProblem) else "")
    finally:
        try:
            host.delete(name)
        except Exception:
            pass
    if st == 200 and (body.lstrip().startswith(b"<?php") or b"PHP_VERSION" in body):
        return Probe(kind="none", version="", wait=0, detail="it sends PHP files back as text: PHP does not run there")
    if st != 200:
        return Probe(kind="unreachable", version="", wait=0, detail=f"its web address answered {st} for a file just put there")
    try:
        info = json.loads(body.decode("utf-8", "replace"))
        assert isinstance(info, dict) and info.get("la") == 1
    except Exception:
        return Probe(kind="unreachable", version="", wait=0, detail="its web address answered, but not with the test file")
    v = _version(info.get("php"))
    if v is None:
        return Probe(kind="unreachable", version="", wait=0, detail="PHP answered without a version")
    out = Probe(kind="ok" if v >= PHP_FLOOR else "old", version=f"{v[0]}.{v[1]}", wait=door_wait(info),
                detail="", sha256=bool(info.get("sha256")), gcm=bool(info.get("gcm")), openssl=bool(info.get("openssl")),
                full=str(info.get("php") or ""), proxy=proxied(info, base_url))
    if out["kind"] == "ok" and not out["sha256"]:
        out.update(kind="old", detail="PHP runs there, but without SHA-256 hashing")
    elif out["kind"] == "old":
        out["detail"] = f"Your web host runs PHP {out['version']}; Lumen Aureum needs 7.4 or later"
    return out

FORWARD_SAY = {"HTTP_X_FORWARDED_FOR": "X-Forwarded-For", "HTTP_FORWARDED": "Forwarded", "HTTP_CF_CONNECTING_IP": "CF-Connecting-IP",
               "HTTP_X_REAL_IP": "X-Real-IP", "HTTP_TRUE_CLIENT_IP": "True-Client-IP", "HTTP_FASTLY_CLIENT_IP": "Fastly-Client-IP",
               "HTTP_X_CLUSTER_CLIENT_IP": "X-Cluster-Client-IP", "HTTP_VIA": "Via", "HTTP_CDN_LOOP": "CDN-Loop"}

def proxied(info, base_url):

    import ipaddress
    from urllib.parse import urlsplit
    seen = [FORWARD_SAY.get(h, "") for h in (info.get("fwd") or []) if isinstance(h, str)]
    seen = [h for h in seen if h]
    if seen:
        return "it passed on " + ", ".join(seen)
    try:
        ra = ipaddress.ip_address(str(info.get("ra") or "").split("%")[0])
    except ValueError:
        return ""
    name = (urlsplit(base_url or "").hostname or "").strip("[]").lower()
    try:
        local_name = name == "localhost" or ipaddress.ip_address(name).is_loopback
    except ValueError:
        local_name = False
    return "every request reaches it from its own machine" if ra.is_loopback and not local_name else ""

def door_closed(base_url, gid, want, wait=2, limit=DOOR_LIMIT):

    t0 = time.monotonic()
    while True:
        ok = True
        for method, path, code in (("POST", f"{gid}/picks.php?kind=probe", 400), ("GET", f"{gid}/gate.php", 404)):
            try:
                st, h, _b = HTTP(method, join(base_url, path), headers={"Content-Type": "application/x-www-form-urlencoded"}
                                 if method == "POST" else None, data=b"" if method == "POST" else None)
            except Exception:
                return False
            if st != code or h.get("x-host-scripts", "") != want:
                ok = False
                break
        if ok:
            return True
        if time.monotonic() - t0 + wait > limit:
            return False
        SLEEP(max(1, wait))

def script_answers(url, want, method="GET", code=405):

    try:
        st, h, _b = HTTP(method, url)
    except Exception:
        return False
    return st == code and h.get("x-host-scripts", "") == want

def fetch(url, headers=None):

    try:
        st, _h, body = HTTP("GET", url, headers=headers)
        return st, body
    except Exception:
        return 0, b""

def htaccess_honored(host, base_url):

    d = f".la-check-{secrets.token_hex(6)}"
    try:
        host.put_stream(f"{d}/.htaccess", io.BytesIO(b"Require all denied\n"), content_type="text/plain")
        host.put_stream(f"{d}/x.txt", io.BytesIO(b"lumen aureum check\n"), content_type="text/plain")
    except Exception:
        return None
    try:
        st, body = fetch(join(base_url, f"{d}/x.txt"))
    finally:
        for n in (f"{d}/x.txt", f"{d}/.htaccess"):
            try:
                host.delete(n)
            except Exception:
                pass
        try:
            host.remove_dir(d + "/")
        except Exception:
            pass
    if st in (401, 403):
        return True
    if st == 200 and b"lumen aureum check" in body:
        return False
    return None

def gate_holds(host, base_url, gallery=None):

    import hostfiles
    if gallery:
        folder, key = gallery
        name = hostfiles.name_for(hostfiles.proof_of(key), "manifest.bin")
        st, body = fetch(join(base_url, f"{folder}/{name}"))
        if st == 200 and body:
            return False
        if st in (401, 403):
            return True
    from pathlib import Path
    assets = Path(__file__).resolve().parent / "plugins" / "gallery_assets"
    d = f".la-gate-{secrets.token_hex(6)}"
    proof = secrets.token_hex(16)
    marker = b"lumen aureum gate check " + secrets.token_hex(8).encode()
    files = {".htaccess": (assets / "htaccess").read_bytes(), "gate.php": (assets / "gate.php").read_bytes(),
             "seen-key.php": b"<?php exit; ?>\n" + hashlib.sha256(proof.encode()).hexdigest().encode() + b"\n",
             "limits.php": hostfiles.EXIT + b"days=1\nunopened_until=2099-01-01\nproof=1\ncodeword=1\n",
             hostfiles.name_for(proof, "manifest.bin"): marker, "orders/index.html": b"",
             "orders/" + secrets.token_hex(16) + ".las": marker}
    try:
        for n, b in files.items():
            host.put_stream(f"{d}/{n}", io.BytesIO(b), content_type="application/octet-stream")
    except Exception:
        _drop(host, d, files)
        return None
    try:
        verdict = None
        for n in (hostfiles.name_for(proof, "manifest.bin"), "limits.php", "orders/" + list(files)[-1].split("/", 1)[1]):
            st, body = fetch(join(base_url, f"{d}/{n}"))
            if st == 200 and marker in body or (n == "limits.php" and st == 200 and b"days=" in body):
                return False
            if n.endswith(".bin") and st == 0:
                return None
            if n.endswith(".bin"):
                verdict = st in (401, 403, 404, 410)
        st, body = fetch(join(base_url, f"{d}/orders/"))
        if st == 200 and b".las" in body:
            return False
        return verdict
    finally:
        _drop(host, d, files)

def _drop(host, d, files):
    for n in files:
        try:
            host.delete(f"{d}/{n}")
        except Exception:
            pass
    for sub in (f"{d}/orders/", d + "/"):
        try:
            host.remove_dir(sub)
        except Exception:
            pass

FEATURES = (("expiry", "Galleries close on their last day, even with Lumen Aureum switched off"),
            ("picks", "Clients' picks come back by themselves (sealed)"),
            ("inquiry", "The studio page's inquiry form"),
            ("prints", "Print ordering from download galleries"),
            ("codeword", "Galleries with a passphrase"))
GATE_NOTE = "its web server hands out gallery files without asking for the key"

GATED = ("expiry", "codeword")

def capabilities(host, base_url, gallery=None):

    p = probe(host, base_url)
    ht = htaccess_honored(host, base_url) if p.kind in ("ok", "old", "none") else None
    gate = gate_holds(host, base_url, gallery) if p.kind in ("ok", "old", "none") else None
    works = p.kind == "ok"
    feats = []
    for key, label in FEATURES:
        note = "" if works else ("needs PHP 7.4 or later" if p.kind == "old" else
                                 "needs a host that runs PHP" if p.kind == "none" else
                                 "its certificate must be checked first" if p.get("cert") else "the host couldn't be checked")
        ok = works
        if key in GATED and works and gate is False:
            ok, note = False, GATE_NOTE
        feats.append((key, label, ok, note))
    return dict(at=time.strftime("%Y-%m-%d %H:%M"), kind=p.kind, version=p.get("version", ""), detail=p.get("detail", ""),
                htaccess=ht, gate=gate, proxy=p.get("proxy", ""), features=feats, cert=cert_state(base_url, p))

def gate_fails(caps):

    if not caps:
        return False
    return caps.get("gate") is False or caps.get("kind") == "none"

def cert_state(base_url, p):

    import webtls
    from urllib.parse import urlsplit
    if urlsplit(base_url or "").scheme != "https":
        return ""
    if p.get("cert"):
        return p["cert"]
    d = str(p.get("detail", ""))
    if p.kind == "unreachable" and (d.startswith(("its web address didn't", "its web address could not"))
                                    or d == "the host would not take a small test file"):
        return ""
    return "pinned" if webtls.pin_for(webtls.address(base_url)) else "public"

def worse(was, now):

    if not was or not now:
        return ""
    if was.get("kind") == "ok" and now.get("kind") == "none":
        return ("Your web host no longer runs PHP: galleries there no longer close on their last day by themselves, "
                "and clients' picks don't come back. Check the host's settings.")
    if was.get("kind") == "ok" and now.get("kind") == "old":
        return (f"Your web host now runs PHP {now.get('version') or 'below 7.4'}; Lumen Aureum needs 7.4 or later. "
                "Galleries there keep the scripts they have until it is 7.4 or later again.")
    if was.get("gate") is not False and now.get("gate") is False and was.get("kind") == "ok":
        return ("Your web host now hands out gallery files without asking for the key, so galleries there no longer "
                "close on their last day by themselves (Lumen Aureum still takes each one down on its last day), and no "
                "new gallery with a passphrase can go there. Someone with the link of a gallery with a passphrase already "
                "there could now try passphrases much faster: take those galleries down, or move to a web host that "
                "passes the check in Setup, Online galleries.")
    return ""
