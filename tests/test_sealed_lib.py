import ast
import inspect
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import sealed as S

G = bytes(range(32))
GID = "g-gal123456789ABCDEFGHIJK"
H = 7 + len(GID)

def pub(k):
    from cryptography.hazmat.primitives import serialization
    return k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

def err(fn):
    with pytest.raises(S.SealedError) as e:
        fn()
    return e.value.code

@pytest.fixture
def studio():
    k = ec.generate_private_key(ec.SECP256R1())
    return k, pub(k)

def test_round_trips_every_purpose(studio):
    k, p = studio
    km = {S.kid_of(p): k}
    for purpose in ("picks", "draft"):
        assert S.open_link(purpose, GID, G, S.seal_link(purpose, GID, G, b"x")) == b"x"
    assert S.open_studio("inquiry", km, S.seal_studio("inquiry", p, b"hi")) == b"hi"
    env = S.seal_studio("print-order", p, b"order", gid=GID, link_key=G)
    assert S.open_studio("print-order", km, env, gid=GID, link_key=G) == b"order"

def test_seal_refusals(studio):
    _, p = studio
    assert err(lambda: S.seal_link("nope", GID, G, b"x")) == "E_PURPOSE"
    assert err(lambda: S.seal_link("picks", "short", G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", "x" * 65, G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", "gal123456789", G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID[:-1], G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID + "x", G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", "gx" + GID[2:], G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID[:5] + " " + GID[6:], G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID[:5] + "é" + GID[6:], G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID[:5] + "." + GID[6:], G, b"x")) == "E_SCOPE"
    assert err(lambda: S.seal_link("picks", GID, G, b"")) == "E_LONG"
    assert err(lambda: S.seal_link("picks", GID, G, b"x" * (262144 - H - 27))) == "E_LONG"
    assert len(S.seal_link("picks", GID, G, b"x" * (262144 - H - 28))) == 262144
    assert err(lambda: S.seal_link("picks", GID, G[:31], b"x")) == "E_KEY"
    assert err(lambda: S.seal_link("picks", GID, "k" * 32, b"x")) == "E_KEY"
    assert err(lambda: S.seal_studio("inquiry", p[:64], b"x")) == "E_KEY"
    assert err(lambda: S.seal_studio("inquiry", b"\x04" + b"\x01" * 64, b"x")) == "E_KEY"
    assert err(lambda: S.seal_studio("inquiry", b"\x02" + p[1:], b"x")) == "E_KEY"
    assert err(lambda: S.seal_studio("inquiry", p, b"x" * (16384 - 107))) == "E_LONG"
    assert len(S.seal_studio("inquiry", p, b"x" * (16384 - 108))) == 16384
    assert err(lambda: S.seal_studio("print-order", p, b"x", gid=GID, link_key=G[:16])) == "E_KEY"
    assert err(lambda: S.seal_studio("print-order", p, b"x", gid="no", link_key=G)) == "E_SCOPE"

def test_link_kinds(studio):

    k, p = studio
    km = {S.kid_of(p): k}
    for kind in "sect":
        other = kind + GID[1:]
        assert err(lambda: S.seal_link("picks", other, G, b"x")) == "E_KIND"
        assert err(lambda: S.seal_link("draft", other, G, b"x")) == "E_KIND"
        assert err(lambda: S.seal_studio("print-order", p, b"x", gid=other, link_key=G)) == "E_KIND"
    assert err(lambda: S.seal_link("picks", "x" + GID[1:], G, b"x")) == "E_SCOPE"
    env = S.seal_link("picks", GID, G, b"x")
    relabeled = env[:7] + b"s" + env[8:]
    assert err(lambda: S.check(relabeled, "picks", "s" + GID[1:])) == "E_KIND"
    assert err(lambda: S.open_link("picks", "s" + GID[1:], G, relabeled)) == "E_KIND"
    assert err(lambda: S.check(env, "picks", "s" + GID[1:])) == "E_SCOPE"
    order = S.seal_studio("print-order", p, b"x", gid=GID, link_key=G)
    assert err(lambda: S.open_studio("print-order", km, order[:7] + b"e" + order[8:], gid="e" + GID[1:],
                                     link_key=G)) == "E_KIND"

    import secrets
    for _ in range(200):
        n = secrets.randbelow(40) + 1
        assert not S.LINK_ID.fullmatch(secrets.token_urlsafe(n))
    for kind in S.KINDS:
        new = S.link_id(kind)
        assert S.LINK_ID.fullmatch(new) and new[:2] == kind + "-" and len(new) == S.LINK_LEN and "." not in new
    assert len({S.link_id("g") for _ in range(100)}) == 100
    assert err(lambda: S.link_id("x")) == "E_KIND"

def test_an_empty_id_relies_on_purpose_alone(studio):

    k, p = studio
    env = S.seal_studio("inquiry", p, b"hi")
    assert env[6] == 0
    S.check(env, "inquiry", "")
    assert err(lambda: S.check(env, "inquiry", GID)) == "E_SCOPE"
    assert err(lambda: S.seal_studio("inquiry", p, b"hi", gid=GID)) == "E_KEY"

def test_mode_arguments(studio):
    k, p = studio
    km = {S.kid_of(p): k}
    assert err(lambda: S.seal_studio("inquiry", p, b"x", gid=GID)) == "E_KEY"
    assert err(lambda: S.seal_studio("inquiry", p, b"x", link_key=G)) == "E_KEY"
    assert err(lambda: S.seal_studio("print-order", p, b"x")) == "E_KEY"
    assert err(lambda: S.seal_studio("print-order", p, b"x", gid=GID)) == "E_KEY"
    assert err(lambda: S.seal_studio("print-order", p, b"x", link_key=G)) == "E_KEY"
    env = S.seal_studio("inquiry", p, b"x")
    assert err(lambda: S.open_studio("inquiry", km, env, gid=GID, link_key=G)) == "E_KEY"
    order = S.seal_studio("print-order", p, b"x", gid=GID, link_key=G)
    assert err(lambda: S.open_studio("print-order", km, order)) == "E_KEY"
    assert err(lambda: S.seal_studio("picks", p, b"x")) == "E_MODE"
    assert err(lambda: S.seal_link("inquiry", GID, G, b"x")) == "E_MODE"
    assert err(lambda: S.open_link("inquiry", "", G, env)) == "E_MODE"
    assert err(lambda: S.open_studio("picks", km, env)) == "E_MODE"
    assert err(lambda: S.open_link("picks", GID, G[:5], env)) == "E_KEY"

def test_key_map_carries_a_changed_studio_key(studio):
    old, old_p = studio
    new = ec.generate_private_key(ec.SECP256R1())
    sent_before = S.seal_studio("inquiry", old_p, b"before")
    sent_after = S.seal_studio("inquiry", pub(new), b"after")
    both = {S.kid_of(old_p): old, S.kid_of(pub(new)): new}
    assert S.open_studio("inquiry", both, sent_before) == b"before"
    assert S.open_studio("inquiry", both, sent_after) == b"after"
    assert err(lambda: S.open_studio("inquiry", {S.kid_of(pub(new)): new}, sent_before)) == "E_KID"

    assert err(lambda: S.open_studio("inquiry", {S.kid_of(old_p): new}, sent_before)) == "E_AUTH"
    assert err(lambda: S.open_studio("inquiry", "not a map", sent_before)) == "E_KID"

def test_bindings_purpose_gallery_and_key():
    env = S.seal_link("picks", GID, G, b"x")
    assert err(lambda: S.open_link("draft", GID, G, env)) == "E_PURPOSE"
    assert err(lambda: S.open_link("picks", "g-gal000000000ABCDEFGHIJK", G, env)) == "E_SCOPE"
    assert err(lambda: S.open_link("picks", GID, bytes(32), env)) == "E_AUTH"

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with pytest.raises(Exception):
        AESGCM(G).decrypt(env[H:H + 12], env[H + 12:], env[:H])

def test_fresh_nonce_and_one_time_key(studio):
    _, p = studio
    a, b = S.seal_link("picks", GID, G, b"same"), S.seal_link("picks", GID, G, b"same")
    assert a != b and a[H:H + 12] != b[H:H + 12]
    x, y = S.seal_studio("inquiry", p, b"same"), S.seal_studio("inquiry", p, b"same")
    assert x[15:80] != y[15:80] and x[7:15] == y[7:15] == S.kid_of(p)

def test_host_check_text_floor():
    env = S.seal_link("picks", GID, G, b"x" * 100)
    S.host_check(env, "picks", GID)
    readable = env[:H + 12] + b"hello world, these are my picks. " * 3
    S.check(readable, "picks", GID)
    assert err(lambda: S.host_check(readable, "picks", GID)) == "E_TEXT"
    S.host_check(env[:H + 12] + b"A" * 63, "picks", GID)

def test_decode_rules():
    env = S.seal_link("picks", GID, G, b"x")
    assert S.decode(S.encode(env)) == env == S.decode(S.encode(env), "picks")
    for bad in ("", "AB", "A", "AA==", "A A", "AA\n", "+/AA", None, b"AAAA"):
        assert err(lambda: S.decode(bad)) == "E_ENCODING"
    assert err(lambda: S.decode("A" * 21847, "inquiry")) == "E_LONG"
    assert len(S.decode("A" * 21847)) == 16385
    assert err(lambda: S.decode("AAAA", "nope")) == "E_PURPOSE"
    assert S.encoded_limit(262144) == 349526 and S.encoded_limit(16384) == 21846 and S.encoded_limit(65536) == 87382

def test_check_accepts_any_bytes_like_and_unknown_purpose():
    env = S.seal_link("picks", GID, G, b"x")
    S.check(bytearray(env), "picks", GID)
    S.check(memoryview(env), "picks", GID)
    assert err(lambda: S.check(env, "nope", GID)) == "E_PURPOSE"
    assert err(lambda: S.check(env, None, GID)) == "E_PURPOSE"

def test_library_stands_alone():

    tree = ast.parse(Path(S.__file__).read_text())
    mods = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"base64", "hashlib", "re", "secrets", "collections", "typing", "cryptography"}, mods
    for name in ("seal_link", "seal_studio"):
        params = inspect.signature(getattr(S, name)).parameters
        assert not {"nonce", "eph", "ephemeral", "iv"} & set(params)
