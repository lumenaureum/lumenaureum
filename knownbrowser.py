import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

COOKIE = "la_known"
DAYS = 30
MAX_KEPT = 20
MAX_FAILS = 5
FILE = "known-browsers.json"
_LOCK = threading.RLock()

def path():
    return Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db")).parent / FILE

def _hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()

def ring_print():

    import datakey
    try:
        ring = datakey._read()
    except (OSError, ValueError):
        return None
    if not ring.get("password") or not ring.get("master"):
        return None
    return hashlib.sha256(json.dumps([ring["password"], ring["master"]]).encode()).hexdigest()

def _load():
    try:
        data = json.loads(path().read_text())
        return data if isinstance(data, dict) and isinstance(data.get("browsers"), dict) else {"browsers": {}}
    except (OSError, ValueError):
        return {"browsers": {}}

def _save(data):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def _usable(rec, now, rp):
    return (isinstance(rec, dict) and rp is not None and rec.get("ring") == rp
            and isinstance(rec.get("exp"), (int, float)) and rec["exp"] > now and rec.get("fails", 0) < MAX_FAILS)

def issue(old=None):

    rp = ring_print()
    if rp is None:
        return None
    tok, now = secrets.token_urlsafe(32), time.time()
    with _LOCK:
        data = _load()
        b = {h: r for h, r in data["browsers"].items() if _usable(r, now, rp)}
        if old:
            b.pop(_hash(old), None)
        b[_hash(tok)] = {"exp": now + DAYS * 86400, "ring": rp, "fails": 0}
        while len(b) > MAX_KEPT:
            b.pop(min(b, key=lambda h: b[h]["exp"]))
        _save({"v": 1, "browsers": b})
    return tok

def known(tok):

    if not tok or not isinstance(tok, str) or len(tok) > 100:
        return False
    with _LOCK:
        rec = _load()["browsers"].get(_hash(tok))
    return _usable(rec, time.time(), ring_print())

def failed(tok):

    if not tok or not isinstance(tok, str) or len(tok) > 100:
        return
    with _LOCK:
        data = _load()
        h = _hash(tok)
        rec = data["browsers"].get(h)
        if not isinstance(rec, dict):
            return
        rec["fails"] = int(rec.get("fails", 0)) + 1
        if rec["fails"] >= MAX_FAILS:
            data["browsers"].pop(h, None)
        try:
            _save({"v": 1, "browsers": data["browsers"]})
        except OSError:
            revoke_all()

def revoke_all():

    with _LOCK:
        try:
            path().unlink()
        except FileNotFoundError:
            pass

def set_cookie(resp, tok):
    resp.set_cookie(COOKIE, tok, max_age=DAYS * 86400, path="/", secure=True, httponly=True, samesite="Strict")
