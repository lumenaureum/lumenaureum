import base64
import json

PICKS_MAX = 262144
INQ_MAX = 16384
_NAMES = {"picks.bin": b"lumen-aureum-gallery:picks.bin", "draft.bin": b"lumen-aureum-gallery:draft.bin"}

def _key(key):
    if isinstance(key, (bytes, bytearray)):
        return bytes(key)
    return base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))

def open_picks_payload(blob, key, name):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aad = _NAMES.get(name)
    if aad is None:
        raise ValueError("not a legacy picks file name")
    if not isinstance(blob, (bytes, bytearray)) or blob[:4] != b"LAK1" or len(blob) < 32 or len(blob) > PICKS_MAX:
        raise ValueError("not a picks file")
    try:
        plain = AESGCM(_key(key)).decrypt(bytes(blob[4:16]), bytes(blob[16:]), aad)
        data = json.loads(plain)
    except Exception as e:
        raise ValueError("not a picks file") from e
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise ValueError("not picks")
    return plain

def open_inquiry(pem, blob):

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    magic = b"LAQ1"
    if not isinstance(blob, (bytes, bytearray)) or len(blob) > INQ_MAX or len(blob) < 4 + 65 + 12 + 16 or blob[:4] != magic:
        raise ValueError("not an inquiry")
    try:
        k = serialization.load_pem_private_key(pem.encode(), password=None)
        site_raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        eph_raw = bytes(blob[4:69])
        shared = k.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), eph_raw))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_raw + site_raw, info=b"lumen-aureum inquiry v1").derive(shared)
        data = json.loads(AESGCM(key).decrypt(bytes(blob[69:81]), bytes(blob[81:]), magic))
    except Exception as e:
        raise ValueError("not an inquiry") from e
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("not an inquiry")
    return data

def _before_links(env, purpose, gid):

    import re
    import sealed
    p = sealed.PURPOSES.get(purpose)
    env = bytes(env) if isinstance(env, (bytes, bytearray)) else b""
    if p is None or not isinstance(gid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", gid) or len(env) > p.max_len \
            or sealed.LINK_ID.fullmatch(gid):
        raise ValueError("not a sealed item of this gallery")
    g = gid.encode()
    if env[:4] != sealed.MAGIC or len(env) < 7 or env[4] != p.mode or env[5] != p.code or env[6] != len(g) \
            or env[7:7 + len(g)] != g:
        raise ValueError("not a sealed item of this gallery")
    h = 7 + len(g) + (0 if p.mode == 1 else 73)
    if len(env) < h + 12 + 17:
        raise ValueError("not a sealed item of this gallery")
    return p.mode, h

def open_las_before_links(purpose, gid, key, env):

    import sealed
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    mode, h = _before_links(env, purpose, gid)
    if mode != 1:
        raise ValueError("not a gallery item")
    env = bytes(env)
    try:
        return AESGCM(sealed._key(_key(key), b"", env[:h])).decrypt(env[h:h + 12], env[h + 12:], env[:h])
    except Exception as e:
        raise ValueError("does not open") from e

def open_order_before_links(private_keys, env, gid, key):

    import sealed
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    mode, h = _before_links(env, "print-order", gid)
    env = bytes(env)
    n = env[6]
    priv = private_keys.get(env[7 + n:15 + n]) if hasattr(private_keys, "get") else None
    if priv is None:
        raise ValueError("no key for this order")
    try:
        epk = env[15 + n:80 + n]
        z = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk))
        rpk = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        k = sealed._key(z + _key(key), epk + rpk, env[:7 + n])
        return AESGCM(k).decrypt(env[h:h + 12], env[h + 12:], env[:h])
    except Exception as e:
        raise ValueError("does not open") from e
