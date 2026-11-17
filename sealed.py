import base64
import hashlib
import re
import secrets
from collections.abc import Mapping
from typing import NamedTuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"LAS1"
LABEL = b"lumen-aureum sealed\x00"
LINK, STUDIO, STUDIO_LINK = 1, 2, 3

class Purpose(NamedTuple):
    code: int
    mode: int
    max_len: int
    kinds: str

PURPOSES = {
    "picks": Purpose(1, LINK, 262144, "g"),
    "draft": Purpose(2, LINK, 262144, "g"),
    "inquiry": Purpose(3, STUDIO, 16384, ""),
    "print-order": Purpose(4, STUDIO_LINK, 65536, "g"),
}

KINDS = {"g": "gallery", "s": "session", "e": "event", "c": "client", "t": "team"}
LINK_LEN = 25

_GID_BYTE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
LINK_ID = re.compile(r"[gsect]-[A-Za-z0-9_-]{23}")

def link_id(kind):

    if kind not in KINDS:
        raise SealedError("E_KIND")
    return kind + "-" + secrets.token_urlsafe(17)
_B64U = re.compile(r"[A-Za-z0-9_-]+")

class SealedError(ValueError):

    def __init__(self, code):
        super().__init__(code)
        self.code = code

def _nonce():
    return secrets.token_bytes(12)

def _ephemeral():
    return ec.generate_private_key(ec.SECP256R1())

def _purpose(name):
    p = PURPOSES.get(name) if isinstance(name, str) else None
    if p is None:
        raise SealedError("E_PURPOSE")
    return p

def _pub(priv):
    return priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

def kid_of(public):

    return hashlib.sha256(bytes(public)).digest()[:8]

def _parse(env, purpose, gid="", kid=None):

    p = _purpose(purpose)
    env = bytes(env)
    n = len(env)
    if n < 4:
        raise SealedError("E_SHORT")
    if env[:3] != b"LAS":
        raise SealedError("E_MAGIC")
    if env[3:4] != b"1":
        raise SealedError("E_VERSION")
    if n > p.max_len:
        raise SealedError("E_LONG")
    if n < 7:
        raise SealedError("E_SHORT")
    mode, code, nid = env[4], env[5], env[6]
    if mode not in (1, 2, 3):
        raise SealedError("E_MODE")
    if code != p.code:
        raise SealedError("E_PURPOSE")
    if mode != p.mode:
        raise SealedError("E_MODE")
    if (mode == STUDIO and nid != 0) or (mode != STUDIO and nid != LINK_LEN):
        raise SealedError("E_SCOPE")
    if n < 7 + nid:
        raise SealedError("E_SHORT")
    got = env[7:7 + nid]
    if any(b not in _GID_BYTE for b in got) or (nid and got[1:2] != b"-") or \
            got != (gid or "").encode("utf-8", "replace"):
        raise SealedError("E_SCOPE")
    if nid and chr(got[0]) not in p.kinds:
        raise SealedError("E_KIND")
    h = 7 + nid if mode == LINK else 80 + nid
    if n < h + 12 + 17:
        raise SealedError("E_SHORT")
    if mode != LINK:
        if env[15 + nid] != 0x04:
            raise SealedError("E_POINT")
        if kid is not None and env[7 + nid:15 + nid] != bytes(kid):
            raise SealedError("E_KID")
    return mode, nid, h

def check(envelope, purpose, gid="", kid=None):

    _parse(envelope, purpose, gid, kid)

def host_check(envelope, purpose, gid="", kid=None):

    _, _, h = _parse(envelope, purpose, gid, kid)
    c = bytes(envelope)[h + 12:]
    if len(c) >= 64:
        printable = sum(1 for b in c if b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E)
        if printable * 100 > 85 * len(c):
            raise SealedError("E_TEXT")

def _binding(p, mode, gid_b):
    return MAGIC + bytes([mode, p.code, len(gid_b)]) + gid_b

def _key(ikm, salt, binding):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt or None, info=LABEL + binding).derive(ikm)

def _gid_ok(gid, mode):
    if not isinstance(gid, str):
        return False
    if mode == STUDIO:
        return gid == ""
    return LINK_ID.fullmatch(gid) is not None

def _kind_ok(p, gid):
    if gid and gid[0] not in p.kinds:
        raise SealedError("E_KIND")

def _link_key(k):
    if not isinstance(k, (bytes, bytearray)) or len(k) != 32:
        raise SealedError("E_KEY")
    return bytes(k)

def _public_point(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 65 or raw[0] != 0x04:
        raise SealedError("E_KEY")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(raw))
    except ValueError:
        raise SealedError("E_KEY") from None

def seal_link(purpose, gid, link_key, plaintext):

    p = _purpose(purpose)
    if p.mode != LINK:
        raise SealedError("E_MODE")
    if not _gid_ok(gid, p.mode):
        raise SealedError("E_SCOPE")
    _kind_ok(p, gid)
    pt = bytes(plaintext)
    g = gid.encode("ascii")
    if not pt or 7 + len(g) + 12 + len(pt) + 16 > p.max_len:
        raise SealedError("E_LONG")
    gk = _link_key(link_key)
    binding = _binding(p, p.mode, g)
    nonce = _nonce()
    return binding + nonce + AESGCM(_key(gk, b"", binding)).encrypt(nonce, pt, binding)

def open_link(purpose, gid, link_key, envelope):

    p = _purpose(purpose)
    if p.mode != LINK:
        raise SealedError("E_MODE")
    gk = _link_key(link_key)
    env = bytes(envelope)
    _, nid, h = _parse(env, purpose, gid)
    head = env[:h]
    try:
        return AESGCM(_key(gk, b"", head)).decrypt(env[h:h + 12], env[h + 12:], head)
    except InvalidTag:
        raise SealedError("E_AUTH") from None

def _studio_args(p, gid, link_key):
    if p.mode == LINK:
        raise SealedError("E_MODE")
    if p.mode == STUDIO:
        if gid or link_key is not None:
            raise SealedError("E_KEY")
        return None
    if not gid or link_key is None:
        raise SealedError("E_KEY")
    return link_key

def seal_studio(purpose, recipient_public, plaintext, *, gid="", link_key=None):

    p = _purpose(purpose)
    gk = _studio_args(p, gid, link_key)
    if not _gid_ok(gid, p.mode):
        raise SealedError("E_SCOPE")
    _kind_ok(p, gid)
    pt = bytes(plaintext)
    g = gid.encode("ascii")
    if not pt or 80 + len(g) + 12 + len(pt) + 16 > p.max_len:
        raise SealedError("E_LONG")
    if gk is not None:
        gk = _link_key(gk)
    rpk = bytes(recipient_public) if isinstance(recipient_public, (bytes, bytearray)) else recipient_public
    point = _public_point(rpk)
    eph = _ephemeral()
    epk = _pub(eph)
    z = eph.exchange(ec.ECDH(), point)
    binding = _binding(p, p.mode, g)
    head = binding + kid_of(rpk) + epk
    nonce = _nonce()
    key = _key(z + (gk or b""), epk + rpk, binding)
    return head + nonce + AESGCM(key).encrypt(nonce, pt, head)

def open_studio(purpose, private_keys, envelope, *, gid="", link_key=None):

    p = _purpose(purpose)
    gk = _studio_args(p, gid, link_key)
    if gk is not None:
        gk = _link_key(gk)
    env = bytes(envelope)
    _, nid, h = _parse(env, purpose, gid)
    kid = env[7 + nid:15 + nid]
    priv = private_keys.get(kid) if isinstance(private_keys, Mapping) else None
    if priv is None:
        raise SealedError("E_KID")
    epk = env[15 + nid:80 + nid]
    try:
        point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk)
    except ValueError:
        raise SealedError("E_POINT") from None
    head = env[:h]
    try:
        z = priv.exchange(ec.ECDH(), point)
        key = _key(z + (gk or b""), epk + _pub(priv), env[:7 + nid])
        return AESGCM(key).decrypt(env[h:h + 12], env[h + 12:], head)
    except (InvalidTag, ValueError):
        raise SealedError("E_AUTH") from None

def encode(envelope):

    return base64.urlsafe_b64encode(bytes(envelope)).rstrip(b"=").decode("ascii")

def encoded_limit(max_len):

    return max_len // 3 * 4 + (0, 2, 3)[max_len % 3]

def decode(d, purpose=None):

    if purpose is not None and isinstance(d, str) and len(d) > encoded_limit(_purpose(purpose).max_len):
        raise SealedError("E_LONG")
    if not isinstance(d, str) or not d or not _B64U.fullmatch(d) or len(d) % 4 == 1:
        raise SealedError("E_ENCODING")
    raw = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    if encode(raw) != d:
        raise SealedError("E_ENCODING")
    return raw
