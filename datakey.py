import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

import pyrage
from pyrage import passphrase, x25519
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DATA_DIR = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent / "data" / "crm.db")).parent
KEYRING = Path(os.environ.get("CRM_KEYRING", DATA_DIR / "keyring.json"))
FILE_MAGIC = b"LAE1"
PENDING_TTL = 300

class Locked(Exception):
    pass

_state = {"dk": None, "ready": False, "pending": {}}
_lock = threading.RLock()

def is_unlocked():

    return _state["dk"] is not None and _state["ready"]

def mark_ready():
    _state["ready"] = True

def dk():
    k = _state["dk"]
    if k is None:
        raise Locked()
    return k

def set_unlocked(key, ready=False):

    with _lock:
        _state["dk"] = key
        _state["ready"] = ready
        _state["pending"].clear()

ON_LOCK = []

def lock():
    with _lock:
        _state["dk"] = None
        _state["ready"] = False
        _state["pending"].clear()
    for fn in list(ON_LOCK):
        try:
            fn()
        except Exception:
            pass

def hold_pending(key, extra=None):

    tok = secrets.token_urlsafe(16)
    with _lock:
        now = time.time()
        for t in [t for t, v in _state["pending"].items() if now - v[1] > PENDING_TTL]:
            del _state["pending"][t]
        _state["pending"][tok] = (key, now, extra or {})
    return tok

def take_pending(tok, consume=False):
    with _lock:
        v = _state["pending"].get(tok or "")
        if not v or time.time() - v[1] > PENDING_TTL:
            _state["pending"].pop(tok or "", None)
            return None
        if consume:
            del _state["pending"][tok]
        return v[0], v[2]

def drop_pending(tok=None):

    if tok is None:
        return
    with _lock:
        _state["pending"].pop(tok, None)

def drop_all_pending():

    with _lock:
        _state["pending"].clear()

def subkey(label, key=None):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"lumen-aureum", info=label.encode()).derive(key or dk())

def db_key_hex(key=None):
    return subkey("sqlcipher", key).hex()

def seal_bytes(data, aad, label="files", key=None):
    nonce = os.urandom(12)
    return FILE_MAGIC + nonce + AESGCM(subkey(label, key)).encrypt(nonce, data, aad.encode())

def open_bytes(blob, aad, label="files", key=None):
    if not blob.startswith(FILE_MAGIC):
        raise ValueError("not an encrypted file")
    from cryptography.exceptions import InvalidTag
    try:
        return AESGCM(subkey(label, key)).decrypt(blob[4:16], blob[16:], aad.encode())
    except InvalidTag:
        raise ValueError("an encrypted file failed its check (damaged, altered, or from another install)")

def is_sealed_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == FILE_MAGIC
    except OSError:
        return False

def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)

def write_sealed(path, data, aad, label="files", key=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(seal_bytes(data, aad, label, key))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)

def exists():
    return KEYRING.exists()

def _b64(b):
    return base64.b64encode(b).decode()

def _mac(ring, key):

    body = json.dumps({k: ring.get(k) for k in ("v", "password", "master", "master_pub", "auto", "auto_pub")}, sort_keys=True)
    return hmac.new(subkey("keyring-mac", key), body.encode(), hashlib.sha256).hexdigest()

def _read(path=None):
    return json.loads(Path(path or KEYRING).read_text())

_RING_LOCK = threading.RLock()

def _write(ring, key, path=None):
    ring["mac"] = _mac(ring, key)
    dest = Path(path or KEYRING)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    with _RING_LOCK:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(ring, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    _fsync_dir(dest.parent)

def _check(ring, key):
    if not hmac.compare_digest(ring.get("mac", ""), _mac(ring, key)):
        raise ValueError("The keyring file has been changed outside Lumen Aureum. Restore it from a backup "
                         "(the master key opens any backup) before signing in.")
    return key

class TooSlow(ValueError):
    pass

def unwrap_password(password, path=None):

    ring = _read(path)
    try:
        key = passphrase.decrypt(base64.b64decode(ring["password"]), password)
    except Exception as e:
        if "xcessive work" in str(e):
            raise TooSlow("This keyring's password protection was made on a much faster computer, and this one is too "
                          "slow to check it. Use Forgot your password (the master key) to set the password here.")
        return None
    return _check(ring, key)

STANDARD_WORK = 18

def password_work(ring=None):

    try:
        head = base64.b64decode((ring or _read())["password"])[:200].split(b"\n")
        parts = head[1].split(b" ")
        if head[0] == b"age-encryption.org/v1" and len(parts) == 4 and parts[:2] == [b"->", b"scrypt"] and parts[3].isdigit():
            return int(parts[3])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    return None

class NotStronger(ValueError):
    pass

def rewrap_password_here(password, path=None):

    key = unwrap_password(password, path)
    if not key:
        return None
    old = password_work(_read(path))
    wrap = _b64(passphrase.encrypt(key, password))
    new = password_work({"password": wrap})
    if old is None or new is None or new <= old:
        raise NotStronger(f"This computer is no faster than the one that set the password (work factor {new} here, "
                          f"{old} now). Nothing was changed.")
    if new > old + 3:
        raise NotStronger(f"This computer is so much faster (work factor {new} here, {old} now) that the machine "
                          "running Lumen Aureum might be unable to check the password. Nothing was changed.")
    with _RING_LOCK:
        ring = _read(path)
        _check(ring, key)
        ring["password"] = wrap
        _write(ring, key, path)
    drop_all_pending()
    return old, new

def unwrap_master(secret, ring_path=None):
    ring = _read(ring_path)
    try:
        ident = x25519.Identity.from_str(secret.strip())
    except Exception:
        raise ValueError("That is not a master key (it starts AGE-SECRET-KEY-1).")
    try:
        key = pyrage.decrypt(base64.b64decode(ring["master"]), [ident])
    except Exception:
        raise ValueError("That master key does not open this install.")
    return _check(ring, key)

def rewrap_password(new_password, key=None):
    key = key or dk()
    wrap = _b64(passphrase.encrypt(key, new_password))
    with _RING_LOCK:
        ring = _read()
        _check(ring, key)
        ring["password"] = wrap
        _write(ring, key)
    drop_all_pending()

def master_pub():
    try:
        return _read().get("master_pub")
    except (OSError, ValueError):
        return None

LEGACY_AUTO_ENV = "CRM_UNLOCK_KEY_FILE"

def legacy_auto_notice():

    try:
        ring = _read()
    except (OSError, ValueError):
        return False
    if ring.get("auto_removed"):
        return False
    return bool(ring.get("auto")) or bool(os.environ.get(LEGACY_AUTO_ENV, "").strip())

def forget_legacy_auto(key=None):

    key = key or dk()
    with _RING_LOCK:
        ring = _read()
        if ring.get("auto_removed") or not (ring.get("auto") or os.environ.get(LEGACY_AUTO_ENV, "").strip()):
            return False
        _check(ring, key)
        ring.pop("auto", None)
        ring.pop("auto_pub", None)
        ring["auto_removed"] = "1.5.0"
        _write(ring, key)
    return True

def new_ring_for(new_key, password, master_pub_, path=None):

    ring = {"v": 1, "password": _b64(passphrase.encrypt(new_key, password)),
            "master": _b64(pyrage.encrypt(new_key, [x25519.Recipient.from_str(master_pub_)])), "master_pub": master_pub_}
    _write(ring, new_key, path)

def password_wrap(key, password):

    return _b64(passphrase.encrypt(key, password))

def create_from(key, pw_wrap, master_pub_):

    KEYRING.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(KEYRING, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        raise FileExistsError("a keyring already exists")
    ring = {"v": 1, "password": pw_wrap,
            "master": _b64(pyrage.encrypt(key, [x25519.Recipient.from_str(master_pub_)])), "master_pub": master_pub_}
    _write(ring, key)

def replace_master(new_pub, key=None):

    key = key or dk()
    with _RING_LOCK:
        ring = _read()
        _check(ring, key)
        ring["master"] = _b64(pyrage.encrypt(key, [x25519.Recipient.from_str(new_pub)]))
        ring["master_pub"] = new_pub
        _write(ring, key)
    drop_all_pending()
