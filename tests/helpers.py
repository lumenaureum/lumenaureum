import secrets

from flask.testing import FlaskClient

class HttpsClient(FlaskClient):

    def open(self, *args, **kw):
        kw.setdefault("base_url", "https://localhost")
        return super().open(*args, **kw)

TEST_PASSWORD = "test password 123"
MODULES = ("app", "auth", "config", "setup", "sun", "datakey", "locknotice", "db", "atrest", "rotate", "netsec", "about", "settings_io", "serve", "seed_demo", "walkthrough")

def fresh_modules():

    import sys
    for m in [m for m in sys.modules if m in MODULES or m.startswith(("vault", "plugins"))]:
        del sys.modules[m]

def unlock_new_install(A, password=TEST_PASSWORD):

    import datakey
    from vault import keys as K
    if datakey.exists():
        datakey.set_unlocked(datakey.unwrap_password(password))
        A.on_unlock()
        return None
    secret, pub = K.generate_master()
    key = secrets.token_bytes(32)
    datakey.create_from(key, datakey.password_wrap(key, password), pub)
    datakey.set_unlocked(key)
    A.on_unlock()
    con = A.connect()
    K.set_master_pub(con, pub)
    con.close()
    return secret

def signed_in_client(A, setup_done=True):
    import datakey
    secret = None
    if not datakey.is_unlocked():
        secret = unlock_new_install(A)
    con = A.connect()
    try:
        con.execute("""INSERT OR IGNORE INTO users (id, name, email, pw_hash, totp_secret) VALUES
                       (1, 'Owner', 'owner@example.com', '', 'JBSWY3DPEHPK3PXP')""")
        if setup_done:
            con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
        con.commit()
    finally:
        con.close()
    A.app.test_client_class = HttpsClient
    c = A.app.test_client()
    import auth
    with c.session_transaction() as s:
        s["uid"] = 1
        s["sid"] = auth.start_session(1)
    c.master_secret = secret
    return c

def seal_picks(key, gid, picks, purpose="picks", at="2026-11-14T15:00:00.000Z", v=1, item=None):

    import base64
    import json
    import secrets
    import sealed
    body = {"v": v}
    if item != "":
        body["id"] = item or secrets.token_hex(16)
    if at is not None:
        body["at"] = at
    body["picks"] = picks
    k = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    return sealed.seal_link(purpose, gid, k, json.dumps(body).encode())

def link_id(name="x"):

    import hashlib
    import base64
    return "g-" + base64.urlsafe_b64encode(hashlib.sha256(name.encode()).digest()).decode()[:23]

def las_text(gid, purpose="picks", letters=200):

    import sealed
    p = sealed.PURPOSES[purpose]
    g = gid.encode()
    return b"LAS1" + bytes([p.mode, p.code, len(g)]) + g + b"\x00" * 12 + (b"abcdefghij" * (letters // 10 + 1))[:letters]

def form_d(envelope):

    import base64
    from urllib.parse import urlencode
    return urlencode({"d": base64.urlsafe_b64encode(envelope).decode().rstrip("=")}).encode()

def legacy_picks(key, picks, name="picks.bin", at="2026-11-14T15:00:00.000Z"):

    import base64
    import json
    import secrets as _s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    k = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    iv = _s.token_bytes(12)
    body = json.dumps({"v": 1, "at": at, "picks": picks} if at is not None else {"v": 1, "picks": picks}).encode()
    return bytes.fromhex("4c414b31") + iv + AESGCM(k).encrypt(iv, body, ("lumen-aureum-gallery:" + name).encode())

def posted_envelope(buf):

    import base64
    from urllib.parse import parse_qs
    if buf and buf.startswith(b"d="):
        d = parse_qs(buf.decode())["d"][0]
        return base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
    return buf

LS_READ = """async ([n, k, session]) => {
  const s = k.replace(/-/g, "+").replace(/_/g, "/"), raw = Uint8Array.from(atob(s + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));
  const te = new TextEncoder(), C = crypto.subtle;
  const base = await C.importKey("raw", raw, "HKDF", false, ["deriveBits"]);
  const bits = new Uint8Array(await C.deriveBits({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0),
    info: te.encode("lumen-aureum local storage") }, base, 512));
  const ak = await C.importKey("raw", bits.slice(0, 32), "AES-GCM", false, ["decrypt"]);
  const nk = await C.importKey("raw", bits.slice(32), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const hn = "la:" + [...new Uint8Array(await C.sign("HMAC", nk, te.encode(n)))].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
  const v = (session ? sessionStorage : localStorage).getItem(hn);
  if (v === null) return null;
  const b = Uint8Array.from(atob(v), c => c.charCodeAt(0));
  return new TextDecoder().decode(await C.decrypt({ name: "AES-GCM", iv: b.subarray(0, 12), additionalData: te.encode(n) }, ak, b.subarray(12)));
}"""

GALLERY_KEYS = {}

def remember_key(gid, key):
    if gid and key:
        GALLERY_KEYS[gid] = key

def _key_for(folder, key):
    return key or GALLERY_KEYS.get(getattr(folder, "name", ""))

def hosted(folder, plain, key=None):

    import base64
    import re
    from pathlib import Path
    import hostfiles
    folder = Path(folder)
    key = _key_for(folder, key)
    if plain in hostfiles.STATE:
        p = folder / hostfiles.STATE[plain]
        return p if p.exists() else folder / plain
    if plain in hostfiles.GATED:
        if key:
            raw = key if isinstance(key, bytes) else base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
            p = folder / hostfiles.name_for(hostfiles.proof_of(raw), plain)
            if p.exists():
                return p
        if plain.endswith(".las") and folder.is_dir():
            import sealed
            code = sealed.PURPOSES[plain[:-4]].code
            for p in sorted(folder.iterdir()):
                if re.fullmatch(r"[0-9a-f]{24}\.las", p.name) and p.read_bytes()[5:6] == bytes([code]):
                    return p
    return folder / plain

def host_bytes(folder, plain, key=None):

    import hostfiles
    b = hosted(folder, plain, key).read_bytes()
    return hostfiles.state_bytes(b) if plain in hostfiles.STATE else b

def host_text(folder, plain, key=None):
    return host_bytes(folder, plain, key).decode()

def host_has(folder, plain, key=None):
    return hosted(folder, plain, key).exists()

def host_write(folder, plain, data, key=None):

    import base64
    from pathlib import Path
    import hostfiles
    folder = Path(folder)
    key = _key_for(folder, key)
    data = data.encode() if isinstance(data, str) else data
    if plain in hostfiles.STATE:
        (folder / plain).unlink(missing_ok=True)
        (folder / hostfiles.STATE[plain]).write_bytes(hostfiles.EXIT + data)
    elif plain in hostfiles.GATED and key:
        raw = key if isinstance(key, bytes) else base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        (folder / hostfiles.name_for(hostfiles.proof_of(raw), plain)).write_bytes(data)
    else:
        (folder / plain).write_bytes(data)

def host_names(folder, key=None):

    from pathlib import Path
    import hostfiles
    folder = Path(folder)
    back = {v: k for k, v in hostfiles.STATE.items()}
    for plain in hostfiles.GATED:
        p = hosted(folder, plain, key)
        if p.exists() and p.name != plain:
            back[p.name] = plain
    return {back.get(p.name, p.name) for p in folder.iterdir() if p.is_file()}

def gallery_key(A, sid=1):

    con = A.connect()
    try:
        r = con.execute("SELECT key, ekey FROM galleries WHERE session_id=?", (sid,)).fetchone()
    finally:
        con.close()
    return r[1] or r[0]
