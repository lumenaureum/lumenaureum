import argparse
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import sealed as S

GID = "g-gal123456789ABCDEFGHIJK"
OTHER_GID = "g-gal987654321ABCDEFGHIJK"
SESSION_ID = "s-gal123456789ABCDEFGHIJK"
NONCE = bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaab")
PICKS_PT = b'{"v":1,"at":"2026-11-16T12:00:00.000Z","picks":[]}'

REF = {
    "picks": "4c415331010119672d67616c3132333435363738394142434445464748494a4ba0a1a2a3a4a5a6a7a8a9aaab5a5e0713644c68ec"
             "bc2f53a513a93df6ba402dcb4736f9c501f8d9c070b9245243722642ab9b77018986ef6ea6d1e2c9040324efaf686816443a2fff"
             "38aab99e7a3d",
    "draft": "4c415331010219672d67616c3132333435363738394142434445464748494a4ba0a1a2a3a4a5a6a7a8a9aaabed94c071bf4a95fa"
             "4c54e0ae817e28c43c3bb03b47ae60bca5ed454c6ba3abae910126af5f35fda8cc7ba7860edf7c4dfaf973f642809b493ae372db"
             "79bc56244c6e",
    "inquiry": "4c415331020300a79d901bae0e47f6045f3e7b341a2f24de0ec67b4c6b9698d6d144a5d08f5272dccc1beaf893186b0532df9da7"
               "0a5c9654fcaedd10a6c2003a91d028d3db5a775ec1c96bba3f3d61b2a0a1a2a3a4a5a6a7a8a9aaabb165af75a99bf8c225f5985b"
               "2892a7e650324ac240524d111d9b91e92a89a61607ffffd8",
    "print-order": "4c415331030419672d67616c3132333435363738394142434445464748494a4ba79d901bae0e47f6045f3e7b341a2f24de0ec67b"
                   "4c6b9698d6d144a5d08f5272dccc1beaf893186b0532df9da70a5c9654fcaedd10a6c2003a91d028d3db5a775ec1c96bba3f3d61"
                   "b2a0a1a2a3a4a5a6a7a8a9aaab3a48a776b220d31a7e09d7dc0321a7bf040065a2e69cb64fc8d1e5ea33d61d0bd4bab5a08f90e9"
                   "59c695d423a70a9fb177e72ab782412c9621d1e8d371178fd6",
}

def scalar(label):
    return hashlib.sha256(label.encode()).digest()

def priv_of(d):
    return ec.derive_private_key(int.from_bytes(d, "big"), ec.SECP256R1())

def pub_of(k):
    return k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

GK = bytes(range(32))
OTHER_GK = scalar("lumen-aureum sealed v1 test other gallery")
R_D = scalar("lumen-aureum sealed v1 test recipient")
O_D = scalar("lumen-aureum sealed v1 test other recipient")
E_D = scalar("lumen-aureum sealed v1 test ephemeral")
R, O, EPH = priv_of(R_D), priv_of(O_D), priv_of(E_D)
RPK, OPK, EPK = pub_of(R), pub_of(O), pub_of(EPH)
KEYS = dict(link_key_hex=GK.hex(), other_link_key_hex=OTHER_GK.hex(),
            recipient_private_hex=R_D.hex(), recipient_public_hex=RPK.hex(), kid_hex=S.kid_of(RPK).hex(),
            other_recipient_private_hex=O_D.hex(), other_recipient_public_hex=OPK.hex(),
            eph_private_hex=E_D.hex(), eph_public_hex=EPK.hex())
GKS = {"link_key_hex": GK, "other_link_key_hex": OTHER_GK, None: None}
RECIPIENTS = {"recipient": R, "other_recipient": O}

def fixed(nonce=NONCE):
    S._nonce = lambda: nonce
    S._ephemeral = lambda: EPH

def rule_pt(n):
    return bytes(i % 251 for i in range(n))

def seal(purpose, gid, pt, gk="link_key_hex", nonce=NONCE):
    fixed(nonce)
    p = S.PURPOSES[purpose]
    if p.mode == 1:
        return S.seal_link(purpose, gid, GKS[gk], pt)
    if p.mode == 2:
        return S.seal_studio(purpose, RPK, pt)
    return S.seal_studio(purpose, RPK, pt, gid=gid, link_key=GKS[gk])

def answer(fn):
    try:
        fn()
        return ""
    except S.SealedError as e:
        return e.code

def open_with(env, ex):
    p = S.PURPOSES[ex["purpose"]]
    gk = GKS[ex["link_key"]]
    if p.mode == 1:
        return lambda: S.open_link(ex["purpose"], ex["gid"], gk, env)
    priv = RECIPIENTS[ex["recipient"]]
    km = {S.kid_of(pub_of(priv)): priv}
    if p.mode == 2:
        return lambda: S.open_studio(ex["purpose"], km, env)
    return lambda: S.open_studio(ex["purpose"], km, env, gid=ex["gid"], link_key=gk)

def valid_entry(name, purpose, gid, pt, *, by_rule=False, nonce=NONCE):
    p = S.PURPOSES[purpose]
    env = seal(purpose, gid, pt, nonce=nonce)
    g = gid.encode()
    binding = S.MAGIC + bytes([p.mode, p.code, len(g)]) + g
    salt = b"" if p.mode == 1 else EPK + RPK
    ikm = GK if p.mode == 1 else EPH.exchange(ec.ECDH(), R.public_key()) + (GK if p.mode == 3 else b"")
    head = binding if p.mode == 1 else binding + S.kid_of(RPK) + EPK
    key = S._key(ikm, salt, binding)
    e = dict(name=name, purpose=purpose, gid=gid, link_key="link_key_hex" if p.mode != 2 else None,
             recipient=None if p.mode == 1 else "recipient", eph=None if p.mode == 1 else "eph",
             nonce_hex=nonce.hex(), info_hex=(S.LABEL + binding).hex(), salt_hex=salt.hex(), ikm_hex=ikm.hex(),
             key_hex=key.hex(), aad_hex=head.hex())
    if by_rule:
        assert pt == rule_pt(len(pt)) and len(env) == p.max_len, (name, len(env))
        e.update(plaintext_rule=dict(rule="byte i is i mod 251", len=len(pt)), envelope_len=len(env),
                 envelope_sha256=hashlib.sha256(env).hexdigest())
    else:
        e.update(plaintext_hex=pt.hex(), envelope_hex=env.hex(), envelope_b64u=S.encode(env))
    kid = S.kid_of(RPK) if p.mode != 1 else None
    S.check(env, purpose, gid, kid)
    S.host_check(env, purpose, gid, kid)
    assert open_with(env, dict(purpose=purpose, gid=gid, link_key="link_key_hex", recipient="recipient"))() == pt
    return e, env

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests" / "vectors" / "sealed-v1.json"))
    out = Path(ap.parse_args().out)

    assert seal("picks", GID, PICKS_PT).hex() == REF["picks"]
    assert seal("draft", GID, PICKS_PT).hex() == REF["draft"]
    assert seal("inquiry", "", b'{"v":1,"name":"Ada"}').hex() == REF["inquiry"]
    assert seal("print-order", GID, b'{"v":1,"oid":"0123456789abcdef0123456789abcdef"}').hex() == REF["print-order"]

    valid, envs = [], {}

    def v(name, *a, **kw):
        e, env = valid_entry(name, *a, **kw)
        valid.append(e)
        envs[name] = env

    v("picks-min", "picks", GID, b"{}")
    v("picks-typical", "picks", GID, PICKS_PT)
    v("draft-typical", "draft", GID, PICKS_PT)
    v("picks-id-dash-underscore", "picks", "g--_" + "x" * 17 + "-_Z0", b'{"v":1,"at":"2026-11-16T12:00:00.000Z","picks":[{"i":0}]}')
    v("picks-largest", "picks", GID, rule_pt(262144 - (7 + len(GID)) - 12 - 16), by_rule=True)
    v("inquiry-typical", "inquiry", "", b'{"v":1,"name":"Ada"}')
    v("inquiry-utf8", "inquiry", "", json.dumps({"v": 1, "name": "Zoë Ångström", "message":
                                                  "Café — 日本 \U0001F4F7"}, ensure_ascii=False).encode())
    v("inquiry-largest", "inquiry", "", rule_pt(16384 - 80 - 12 - 16), by_rule=True)
    v("print-order-typical", "print-order", GID, b'{"v":1,"oid":"0123456789abcdef0123456789abcdef"}')
    v("print-order-largest", "print-order", GID, rule_pt(65536 - (80 + len(GID)) - 12 - 16), by_rule=True)
    assert len(valid[3]["gid"]) == 25 and all(len(e["gid"]) in (0, 25) for e in valid)

    picks, inq, order, pmin = envs["picks-typical"], envs["inquiry-typical"], envs["print-order-typical"], envs["picks-min"]
    kid = S.kid_of(RPK).hex()

    def X(env=None, purpose="picks", gid=GID, kid_hex=None, gk="link_key_hex", recipient=None):
        if S.PURPOSES[purpose].mode != 1 and recipient is None:
            recipient = "recipient"
        if S.PURPOSES[purpose].mode == 2:
            gk = None
            gid = "" if gid == GID else gid
        return dict(purpose=purpose, gid=gid, kid_hex=kid_hex, link_key=gk, recipient=recipient)

    def change(b, i, val):
        b = bytearray(b)
        b[i] = val
        return bytes(b)

    def flip(b, i, bit=0x01):
        return change(b, i, b[i] ^ bit)

    invalid = []

    def bad(name, env, expect, check, open_, rule=None):
        e = dict(name=name)
        if rule:
            e["envelope_rule"] = rule
            env = bytes.fromhex(rule["prefix_hex"]) + bytes.fromhex(rule["fill_byte"]) * (rule["total_len"] - len(rule["prefix_hex"]) // 2)
        else:
            e["envelope_hex"] = env.hex()
        kidb = bytes.fromhex(expect["kid_hex"]) if expect["kid_hex"] else None
        got_c = answer(lambda: S.check(env, expect["purpose"], expect["gid"], kidb))
        got_h = answer(lambda: S.host_check(env, expect["purpose"], expect["gid"], kidb))
        got_o = answer(open_with(env, expect))
        assert (got_c, got_o) == (check, open_), (name, got_c, got_o)
        assert open_ and (check or open_ in ("E_AUTH", "E_KID", "E_POINT")), name
        e.update(expect=expect, check=got_c, host=got_h, open=got_o)
        invalid.append(e)

    H1 = 7 + len(GID)
    Hm = 7 + len(GID)

    bad("empty", b"", X(), "E_SHORT", "E_SHORT")
    bad("three-bytes", b"LAS", X(), "E_SHORT", "E_SHORT")
    bad("six-bytes", picks[:6], X(), "E_SHORT", "E_SHORT")
    bad("cut-in-gallery-id", picks[:10], X(), "E_SHORT", "E_SHORT")
    bad("cut-in-kid", inq[:10], X(purpose="inquiry", kid_hex=kid), "E_SHORT", "E_SHORT")
    bad("cut-in-epk", inq[:40], X(purpose="inquiry", kid_hex=kid), "E_SHORT", "E_SHORT")
    bad("cut-in-nonce", picks[:H1 + 6], X(), "E_SHORT", "E_SHORT")
    bad("tag-one-byte-short", pmin[:Hm + 12 + 15], X(), "E_SHORT", "E_SHORT")
    bad("no-plaintext-byte", pmin[:Hm + 12 + 16], X(), "E_SHORT", "E_SHORT")

    old_picks = bytes.fromhex("4c414b31") + NONCE + bytes(range(64))
    old_inq = bytes.fromhex("4c415131") + EPK + NONCE + bytes(range(64))
    bad("legacy-picks-envelope", old_picks, X(), "E_MAGIC", "E_MAGIC")
    bad("legacy-inquiry-envelope", old_inq, X(purpose="inquiry"), "E_MAGIC", "E_MAGIC")
    bad("magic-XAS1", b"X" + picks[1:], X(), "E_MAGIC", "E_MAGIC")
    bad("version-LAS2", change(picks, 3, ord("2")), X(), "E_VERSION", "E_VERSION")
    bad("version-LAS0", change(picks, 3, ord("0")), X(), "E_VERSION", "E_VERSION")

    bad("mode-00", change(picks, 4, 0), X(), "E_MODE", "E_MODE")
    bad("mode-04", change(picks, 4, 4), X(), "E_MODE", "E_MODE")
    bad("picks-with-mode-02", change(picks, 4, 2), X(), "E_MODE", "E_MODE")
    bad("inquiry-with-mode-01", change(inq, 4, 1), X(purpose="inquiry"), "E_MODE", "E_MODE")
    bad("print-order-with-mode-02", change(order, 4, 2), X(purpose="print-order"), "E_MODE", "E_MODE")

    bad("purpose-00", change(picks, 5, 0), X(), "E_PURPOSE", "E_PURPOSE")
    bad("purpose-7f", change(picks, 5, 0x7F), X(), "E_PURPOSE", "E_PURPOSE")
    bad("purpose-80", change(picks, 5, 0x80), X(), "E_PURPOSE", "E_PURPOSE")
    bad("picks-checked-as-draft", picks, X(purpose="draft"), "E_PURPOSE", "E_PURPOSE")
    bad("picks-relabeled-as-draft", change(picks, 5, 2), X(purpose="draft"), "", "E_AUTH")

    bad("checked-for-another-gallery", picks, X(gid=OTHER_GID), "E_SCOPE", "E_SCOPE")
    moved = picks[:7] + OTHER_GID.encode() + picks[H1:]
    bad("gallery-id-rewritten", moved, X(gid=OTHER_GID), "", "E_AUTH")
    bad("gallery-id-rewritten-other-key", moved, X(gid=OTHER_GID, gk="other_link_key_hex"), "", "E_AUTH")
    bad("id-length-24", change(picks, 6, 24), X(), "E_SCOPE", "E_SCOPE")
    bad("id-length-26", change(picks, 6, 26), X(), "E_SCOPE", "E_SCOPE")

    old_id = picks[:6] + bytes([12]) + b"gal123456789" + picks[H1:]
    bad("id-before-links", old_id, X(gid="gal123456789"), "E_SCOPE", "E_SCOPE")
    bad("id-without-kind-dash", change(picks, 8, ord("x")), X(gid=GID[0] + "x" + GID[2:]), "E_SCOPE", "E_SCOPE")

    as_session = picks[:7] + SESSION_ID.encode() + picks[H1:]
    bad("picks-for-a-session-link", as_session, X(gid=SESSION_ID), "E_KIND", "E_KIND")
    bad("picks-for-an-unknown-kind", change(picks, 7, ord("x")), X(gid="x" + GID[1:]), "E_KIND", "E_KIND")
    bad("print-order-for-a-session-link", order[:7] + SESSION_ID.encode() + order[H1:],
        X(purpose="print-order", gid=SESSION_ID, kid_hex=kid), "E_KIND", "E_KIND")
    bad("picks-checked-for-a-session-link", picks, X(gid=SESSION_ID), "E_SCOPE", "E_SCOPE")
    bad("id-length-0-in-mode-01", change(picks, 6, 0), X(), "E_SCOPE", "E_SCOPE")
    bad("id-length-12-in-mode-02", change(inq, 6, 12), X(purpose="inquiry"), "E_SCOPE", "E_SCOPE")
    for label, byte in (("space", 0x20), ("slash", 0x2F), ("nul", 0x00), ("high-byte", 0xC3)):
        bad(f"id-with-{label}", change(picks, 10, byte), X(), "E_SCOPE", "E_SCOPE")

    bad("wrong-gallery-key", picks, X(gk="other_link_key_hex"), "", "E_AUTH")
    bad("print-order-wrong-gallery-key", order, X(purpose="print-order", gk="other_link_key_hex"), "", "E_AUTH")
    bad("inquiry-other-recipient-only", inq, X(purpose="inquiry", recipient="other_recipient"), "", "E_KID")

    bad("kid-bit-changed-kid-expected", flip(inq, 7), X(purpose="inquiry", kid_hex=kid), "E_KID", "E_KID")
    bad("kid-bit-changed-no-kid-expected", flip(inq, 7), X(purpose="inquiry"), "", "E_KID")

    bad("epk-compressed-prefix", change(inq, 15, 0x02), X(purpose="inquiry", kid_hex=kid), "E_POINT", "E_POINT")
    bad("epk-not-on-curve", flip(inq, 79), X(purpose="inquiry", kid_hex=kid), "", "E_POINT")
    bad("epk-other-valid-point", inq[:15] + OPK + inq[80:], X(purpose="inquiry", kid_hex=kid), "", "E_AUTH")
    bad("print-order-epk-other-valid-point", order[:H1 + 8] + OPK + order[H1 + 73:],
        X(purpose="print-order", kid_hex=kid), "", "E_AUTH")

    bad("nonce-bit-changed", flip(picks, H1), X(), "", "E_AUTH")
    bad("first-ciphertext-byte-changed", flip(picks, H1 + 12), X(), "", "E_AUTH")
    bad("last-tag-byte-changed", flip(picks, len(picks) - 1), X(), "", "E_AUTH")
    bad("one-byte-appended", picks + b"\x00", X(), "", "E_AUTH")
    bad("inquiry-last-tag-byte-changed", flip(inq, len(inq) - 1), X(purpose="inquiry"), "", "E_AUTH")
    bad("print-order-header-gallery-id-changed", order[:7] + OTHER_GID.encode() + order[H1:],
        X(purpose="print-order", gid=OTHER_GID), "", "E_AUTH")

    for pname, env in (("picks", picks), ("draft", envs["draft-typical"]), ("inquiry", inq), ("print-order", order)):
        p = S.PURPOSES[pname]
        head = env[:7 + env[6] + (0 if p.mode == 1 else 73)]
        bad(f"{pname}-limit-plus-one", None, X(purpose=pname), "E_LONG", "E_LONG",
            rule=dict(prefix_hex=head.hex(), fill_byte="00", total_len=p.max_len + 1))

    bad("ciphertext-200-letters", picks[:H1 + 12] + b"abcdefghijklmnopqrstuvwxyz"[:20] * 10, X(), "", "E_AUTH")
    bad("ciphertext-40-letters", picks[:H1 + 12] + b"ABCDEFGHIJKLMNOPQRST" * 2, X(), "", "E_AUTH")
    assert invalid[-2]["host"] == "E_TEXT" and invalid[-1]["host"] == ""
    assert all(e["host"] == e["check"] for e in invalid if e["name"] != "ciphertext-200-letters")

    transport = []

    def t(name, purpose, d=None, error="", d_rule=None):
        e = dict(name=name, purpose=purpose)
        if d_rule:
            e["d_rule"] = d_rule
            d = d_rule["char"] * d_rule["len"]
        else:
            e["d"] = d
        got = answer(lambda: S.decode(d, purpose))
        assert got == error, (name, got)
        e["error"] = got
        if not got and d_rule:
            raw = S.decode(d, purpose)
            e.update(bytes_len=len(raw), bytes_sha256=hashlib.sha256(raw).hexdigest())
        elif not got:
            e["bytes_hex"] = S.decode(d, purpose).hex()
        transport.append(e)

    d = S.encode(picks)
    assert "_" in d and len(d) % 4 != 0
    t("plain", "picks", d)
    t("plain-inquiry", "inquiry", S.encode(inq))
    t("padded", "picks", d + "=" * (-len(d) % 4), "E_ENCODING")
    t("standard-alphabet", "picks", d.replace("-", "+").replace("_", "/"), "E_ENCODING")
    t("with-space", "picks", d[:10] + " " + d[10:], "E_ENCODING")
    t("trailing-newline", "picks", d + "\n", "E_ENCODING")
    t("empty", "picks", "", "E_ENCODING")
    t("length-4k-plus-1", "picks", "AAAAA", "E_ENCODING")
    t("non-canonical-final-character", "picks", "AB", "E_ENCODING")
    t("non-canonical-final-character-3", "picks", "AAB", "E_ENCODING")
    t("canonical-short", "picks", "AA")
    for pname in S.PURPOSES:
        lim = S.encoded_limit(S.PURPOSES[pname].max_len)
        t(f"{pname}-one-character-too-long", pname, error="E_LONG", d_rule=dict(char="A", len=lim + 1))
        t(f"{pname}-at-the-limit", pname, d_rule=dict(char="A", len=lim))

    doc = {
        "format": "LAS1",
        "label_hex": S.LABEL.hex(),
        "notes": ("link_key names an entry of keys; recipient is 'recipient' or 'other_recipient' (keys "
                  "<name>_private_hex / <name>_public_hex); eph 'eph' (keys eph_private_hex / eph_public_hex). In "
                  "invalid, open is a full open with expect: mode 1 purposes with the named gallery key; modes 2 and 3 "
                  "with a key map holding only the named recipient's private key (and, mode 3, the named gallery key). "
                  "envelope_rule: prefix then fill bytes to total_len. plaintext_rule: byte i is i mod 251. d_rule: "
                  "the character repeated len times."),
        "purposes": {k: dict(code=p.code, mode=p.mode, max=p.max_len, kinds=p.kinds) for k, p in S.PURPOSES.items()},
        "kinds": S.KINDS,
        "link_len": S.LINK_LEN,
        "keys": KEYS,
        "valid": valid,
        "invalid": invalid,
        "transport": transport,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=True) + "\n")
    print(out, hashlib.sha256(out.read_bytes()).hexdigest(), len(valid), len(invalid), len(transport))

if __name__ == "__main__":
    main()
