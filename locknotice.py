import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import datakey

DAY = timedelta(days=1)
RETRY = timedelta(hours=1)
TICK = 60
WAITED_MIN = timedelta(minutes=15)
TITLE = "Studio locked"
TEST_TEXT = "Test notice: your studio can reach this topic. Notices never carry client details or a link."
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LOCK = threading.RLock()

def config_path():
    return datakey.KEYRING.parent / "ntfy.json"

def state_path():
    return datakey.KEYRING.parent / "lock-state.json"

def _now():
    return datetime.now(timezone.utc)

def _load(p):
    try:
        v = json.loads(Path(p).read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}

def _save(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, p)

def own_network(server, resolve=True):

    import ipaddress
    import socket
    from urllib.parse import urlsplit
    from vault.s3 import lan_ip
    host = (urlsplit(server).hostname or "").strip("[]")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return lan_ip(host)
    except ValueError:
        pass
    if host.lower() == "localhost":
        return True
    if not resolve:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        return False
    return bool(addrs) and all(lan_ip(a) for a in addrs)

def plain_http(server):
    return (server or "").strip().lower().startswith("http://")

PUBLIC_HOSTS = ("ntfy.sh",)

def public_server(server):

    from urllib.parse import urlsplit
    try:
        host = (urlsplit((server or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in PUBLIC_HOSTS)

def check(server, topic, resolve=True):

    server, topic = (server or "").strip(), (topic or "").strip()
    if not server.lower().startswith(("https://", "http://")) or len(server) < 11 or any(c.isspace() for c in server):
        return "The server address must start with https:// (or http:// for a server on your own network)."
    if plain_http(server) and not own_network(server, resolve=resolve):
        return ("Plain http:// is accepted only for a server on your own network. Use https:// for a server on the "
                "internet.")
    if "?" in server or "#" in server:
        return "The server address is just the server, without a topic or anything after it."
    if not TOPIC_RE.match(topic):
        return "The topic is letters, digits, -, and _ only (up to 64)."
    return None

def read_config():

    c = _load(config_path())
    if c.get("server") and c.get("topic") and not check(c["server"], c["topic"]):
        return c
    return None

def write_config(server, topic, token="", tz=""):
    with LOCK:
        _save(config_path(), dict(server=server.strip().rstrip("/"), topic=topic.strip(), token=(token or "").strip(),
                                  tz=tz or ""))

def clear_config():
    with LOCK:
        try:
            config_path().unlink()
        except FileNotFoundError:
            pass

def when(dt, tz=""):

    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo(tz)) if tz else dt.astimezone()
    except Exception:
        dt = dt.astimezone()
    h = dt.hour % 12 or 12
    return f"{dt.strftime('%a')} {h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"

def locked_text(since, tz=""):
    return (f"Your studio restarted and has been locked since {when(since, tz)}. Backups and gallery closings are "
            "waiting until you sign in.")

def _post(url, body, headers):

    import ssl
    import urllib.request
    if not url.lower().startswith("https://") and not (plain_http(url) and own_network(url)):
        raise ValueError("not HTTPS")
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect)
    with opener.open(req, timeout=15) as r:
        if r.status >= 300:
            raise OSError(f"the server answered {r.status}")

def send(cfg, text, title=TITLE):

    headers = {"Title": title, "Content-Type": "text/plain; charset=utf-8"}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    try:
        _post(f"{cfg['server'].rstrip('/')}/{cfg['topic']}", text.encode(), headers)
        return None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("ntfy send failed: %s", str(e)[:200] or e.__class__.__name__)
        return plain_why(e, cfg.get("server", ""))

def plain_why(e, server=""):

    import socket
    import ssl
    import urllib.error
    from urllib.parse import urlsplit
    host = urlsplit(server).hostname or "the server"
    raw = str(e)
    if isinstance(e, ValueError) and "HTTPS" in raw:
        return "the server address must start with https:// (or http:// for a server on your own network)"
    if isinstance(e, urllib.error.HTTPError) or raw.startswith("the server answered"):
        code = getattr(e, "code", None) or next((int(w_) for w_ in raw.split() if w_.isdigit()), 0)
        if code in (401, 403):
            return "the server refused it. Check the topic and the access token"
        if 300 <= code < 400:
            return "the server sent it somewhere else, so it wasn't sent on. Check the server address"
        return "the server refused it"
    why_ = getattr(e, "reason", e)
    if isinstance(why_, socket.gaierror):
        return f"couldn't find {host}. Check the server address"
    if isinstance(why_, ssl.SSLError) or isinstance(e, ssl.SSLError):
        return f"{host} has a certificate that couldn't be checked"
    if isinstance(why_, (TimeoutError, socket.timeout, ConnectionError)) or "timed out" in raw:
        return "the server didn't answer"
    return "the server didn't answer"

def started_locked(now=None):

    with LOCK:
        st = _load(state_path())
        if not st.get("locked_since"):
            st = dict(locked_since=(now or _now()).isoformat(timespec="seconds"))
            _save(state_path(), st)
        return st

def locked_since():
    v = _load(state_path()).get("locked_since")
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None

def unlocked(now=None):

    with LOCK:
        since = locked_since()
        if since is not None or state_path().exists():
            _save(state_path(), {})
        return since

def tick(is_unlocked, log=None, now=None):

    if is_unlocked():
        return None
    cfg = read_config()
    if not cfg:
        return None
    now = now or _now()
    with LOCK:
        st = _load(state_path())
        since = st.get("locked_since")
        if not since:
            return None
        sent, tried = st.get("sent_at"), st.get("tried_at")
        if sent and now - datetime.fromisoformat(sent) < DAY:
            return None
        if tried and now - datetime.fromisoformat(tried) < RETRY:
            return None
        why = send(cfg, locked_text(datetime.fromisoformat(since), cfg.get("tz", "")))
        if why is None:
            st["sent_at"] = now.isoformat(timespec="seconds")
            st.pop("tried_at", None)
        else:
            st["tried_at"] = now.isoformat(timespec="seconds")
        _save(state_path(), st)
    if why is not None and log:
        log.warning("The locked-studio notice could not be sent (tried again in an hour): %s", why)
    return "sent" if why is None else "failed"

def start_loop(is_unlocked, log):

    def loop():
        while True:
            try:
                tick(is_unlocked, log)
            except Exception:
                log.exception("locked-studio notice")
            time.sleep(TICK)
    threading.Thread(target=loop, name="locked-notice", daemon=True).start()
