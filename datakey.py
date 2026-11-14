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

def lock():
    with _lock:
        _state["dk"] = None
        _state["ready"] = False
        _state["pending"].clear()

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

def _write(ring, key, path=None):
    ring["mac"] = _mac(ring, key)
    dest = Path(path or KEYRING)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(ring, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    _fsync_dir(dest.parent)

def _check(ring, key):
    if not hmac.compare_digest(ring.get("mac", ""), _mac(ring, key)):
        raise ValueError("The keyring file has been changed outside Lumen Aureum. Restore it from a backup "
                         "(the master key opens any backup) before signing in.")
    return key

def unwrap_password(password):

    ring = _read()
    try:
        key = passphrase.decrypt(base64.b64decode(ring["password"]), password)
    except Exception:
        return None
    return _check(ring, key)

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
    ring = _read()
    _check(ring, key)
    ring["password"] = _b64(passphrase.encrypt(key, new_password))
    _write(ring, key)

def master_pub():
    try:
        return _read().get("master_pub")
    except (OSError, ValueError):
        return None

def auto_file():
    p = os.environ.get("CRM_UNLOCK_KEY_FILE", "").strip()
    return Path(p) if p else None

def auto_enabled():
    try:
        return bool(_read().get("auto"))
    except (OSError, ValueError):
        return False

def enable_auto(key=None):

    key = key or dk()
    path = auto_file()
    if not path:
        raise ValueError("Set CRM_UNLOCK_KEY_FILE to a path on a separate volume first.")
    parent = path.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    data = KEYRING.resolve().parent
    if parent == data or data in parent.parents or os.stat(parent).st_dev == os.stat(data).st_dev:
        raise ValueError("The unlock key file must be on a different volume from the data: on the same disk it would "
                         "be no protection at all.")
    ident = x25519.Identity.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(str(ident) + "\n")
    ring = _read()
    _check(ring, key)
    ring["auto"] = _b64(pyrage.encrypt(key, [ident.to_public()]))
    ring["auto_pub"] = str(ident.to_public())
    _write(ring, key)

def disable_auto(key=None):
    key = key or dk()
    ring = _read()
    _check(ring, key)
    ring.pop("auto", None)
    ring.pop("auto_pub", None)
    _write(ring, key)
    p = auto_file()
    if p and p.exists():
        p.unlink()

def try_auto_unlock():

    path = auto_file()
    if not path or not path.exists() or not exists():
        return None
    ring = _read()
    if not ring.get("auto"):
        return None
    try:
        ident = x25519.Identity.from_str(path.read_text().strip())
        key = pyrage.decrypt(base64.b64decode(ring["auto"]), [ident])
        return _check(ring, key)
    except Exception:
        return None

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
    ring = _read()
    _check(ring, key)
    ring["master"] = _b64(pyrage.encrypt(key, [x25519.Recipient.from_str(new_pub)]))
    ring["master_pub"] = new_pub
    _write(ring, key)
