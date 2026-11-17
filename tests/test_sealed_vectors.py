import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import sealed as S
from helpers_tools import need_tool

ROOT = Path(__file__).resolve().parent.parent
VFILE = ROOT / "tests" / "vectors" / "sealed-v1.json"
V = json.loads(VFILE.read_text())
K = V["keys"]
VECTORS_SHA256 = "0314bc2a64b107a6974e22e49fb45e519489237d281f5befad18bfe1e80b0934"

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
REF_KEYS = {"picks": "d4be3b32b180a30d32b76e1763b71366c46d2316b7ae56899e4670722344b9c2",
            "draft": "b629a6ae7d771138f9591f73f6b06f01c4bcebf6ed4bcfcc6a0f817f3ab68518",
            "inquiry": "68d4aae9fd98c8af08d0cfeddd4bb5cfa17836c08ed1a545d193194dbb179968",
            "print-order": "43a54561b02f985df0daebecf1e86798f5320b449646f72a1432d848649bfe7a"}
REF_ID = "g-gal123456789ABCDEFGHIJK"

def priv(name):
    return ec.derive_private_key(int(K[name + "_private_hex"], 16), ec.SECP256R1())

def gk(name):
    return bytes.fromhex(K[name]) if name else None

def fixed(monkeypatch, nonce_hex):
    monkeypatch.setattr(S, "_nonce", lambda: bytes.fromhex(nonce_hex))
    monkeypatch.setattr(S, "_ephemeral", lambda: priv("eph"))

def rule_env(r):
    pre = bytes.fromhex(r["prefix_hex"])
    return pre + bytes.fromhex(r["fill_byte"]) * (r["total_len"] - len(pre))

def code(fn):
    try:
        fn()
        return ""
    except S.SealedError as e:
        return e.code

def seal_v(v):
    p = S.PURPOSES[v["purpose"]]
    pt = bytes(i % 251 for i in range(v["plaintext_rule"]["len"])) if "plaintext_rule" in v else bytes.fromhex(v["plaintext_hex"])
    if p.mode == 1:
        env = S.seal_link(v["purpose"], v["gid"], gk(v["link_key"]), pt)
    elif p.mode == 2:
        env = S.seal_studio(v["purpose"], bytes.fromhex(K[v["recipient"] + "_public_hex"]), pt)
    else:
        env = S.seal_studio(v["purpose"], bytes.fromhex(K[v["recipient"] + "_public_hex"]), pt, gid=v["gid"],
                            link_key=gk(v["link_key"]))
    return env, pt

def open_x(env, x):
    p = S.PURPOSES[x["purpose"]]
    if p.mode == 1:
        return lambda: S.open_link(x["purpose"], x["gid"], gk(x["link_key"]), env)
    k = priv(x["recipient"])
    km = {S.kid_of(bytes.fromhex(K[x["recipient"] + "_public_hex"])): k}
    if p.mode == 2:
        return lambda: S.open_studio(x["purpose"], km, env)
    return lambda: S.open_studio(x["purpose"], km, env, gid=x["gid"], link_key=gk(x["link_key"]))

def test_vectors_file_is_frozen():
    assert hashlib.sha256(VFILE.read_bytes()).hexdigest() == VECTORS_SHA256, \
        "sealed-v1.json changed: it is frozen; a new version of the format gets a new file"
    assert V["format"] == "LAS1" and bytes.fromhex(V["label_hex"]) == S.LABEL

def test_worked_vectors_byte_for_byte(monkeypatch):
    fixed(monkeypatch, "a0a1a2a3a4a5a6a7a8a9aaab")
    g = bytes(range(32))
    pt = b'{"v":1,"at":"2026-11-16T12:00:00.000Z","picks":[]}'
    rpk = bytes.fromhex(K["recipient_public_hex"])
    assert S.seal_link("picks", REF_ID, g, pt).hex() == REF["picks"]
    assert S.seal_link("draft", REF_ID, g, pt).hex() == REF["draft"]
    assert S.seal_studio("inquiry", rpk, b'{"v":1,"name":"Ada"}').hex() == REF["inquiry"]
    assert S.seal_studio("print-order", rpk, b'{"v":1,"oid":"0123456789abcdef0123456789abcdef"}', gid=REF_ID,
                         link_key=g).hex() == REF["print-order"]
    by = {v["name"]: v for v in V["valid"]}
    for name, purpose in (("picks-typical", "picks"), ("draft-typical", "draft"), ("inquiry-typical", "inquiry"),
                          ("print-order-typical", "print-order")):
        assert by[name]["envelope_hex"] == REF[purpose] and by[name]["key_hex"] == REF_KEYS[purpose]

def test_purpose_table_python():
    assert {k: dict(code=p.code, mode=p.mode, max=p.max_len, kinds=p.kinds) for k, p in S.PURPOSES.items()} == V["purposes"]
    assert S.KINDS == V["kinds"] and S.LINK_LEN == V["link_len"]
    assert list(S.PURPOSES) == list(V["purposes"])

@pytest.mark.parametrize("v", V["valid"], ids=lambda v: v["name"])
def test_valid_python(v, monkeypatch):
    fixed(monkeypatch, v["nonce_hex"])
    env, pt = seal_v(v)
    if "envelope_hex" in v:
        assert env.hex() == v["envelope_hex"]
        assert S.encode(env) == v["envelope_b64u"] and S.decode(v["envelope_b64u"], v["purpose"]) == env
    else:
        assert len(env) == v["envelope_len"] == S.PURPOSES[v["purpose"]].max_len
        assert hashlib.sha256(env).hexdigest() == v["envelope_sha256"]
    p = S.PURPOSES[v["purpose"]]
    kid = None if p.mode == 1 else bytes.fromhex(K["kid_hex"])
    S.check(env, v["purpose"], v["gid"], kid)
    S.host_check(env, v["purpose"], v["gid"], kid)
    assert open_x(env, dict(purpose=v["purpose"], gid=v["gid"], link_key=v["link_key"],
                            recipient=v["recipient"]))() == pt
    assert S._key(bytes.fromhex(v["ikm_hex"]), bytes.fromhex(v["salt_hex"]),
                  bytes.fromhex(v["info_hex"])[len(S.LABEL):]).hex() == v["key_hex"]
    assert env[:len(bytes.fromhex(v["aad_hex"]))].hex() == v["aad_hex"]

@pytest.mark.parametrize("v", V["invalid"], ids=lambda v: v["name"])
def test_invalid_python(v):
    env = rule_env(v["envelope_rule"]) if "envelope_rule" in v else bytes.fromhex(v["envelope_hex"])
    x = v["expect"]
    kid = bytes.fromhex(x["kid_hex"]) if x["kid_hex"] else None
    assert code(lambda: S.check(env, x["purpose"], x["gid"], kid)) == v["check"]
    assert code(lambda: S.host_check(env, x["purpose"], x["gid"], kid)) == v["host"]
    assert code(open_x(env, x)) == v["open"]
    assert v["open"] and (v["check"] or v["open"] in ("E_AUTH", "E_KID", "E_POINT"))

@pytest.mark.parametrize("t", V["transport"], ids=lambda t: t["name"])
def test_transport_python(t):
    d = t["d_rule"]["char"] * t["d_rule"]["len"] if "d_rule" in t else t["d"]
    assert code(lambda: S.decode(d, t["purpose"])) == t["error"]
    if not t["error"]:
        raw = S.decode(d, t["purpose"])
        if "bytes_hex" in t:
            assert raw.hex() == t["bytes_hex"]
        else:
            assert len(raw) == t["bytes_len"] and hashlib.sha256(raw).hexdigest() == t["bytes_sha256"]

def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])

def test_vectors_javascript():
    res = _run([need_tool("node"), "tests/sealed_vectors.mjs", str(VFILE)])
    assert res["fail"] == [] and res["pass"] > 100
    assert res["purposes"] == V["purposes"]

def test_vectors_php():
    res = _run([need_tool("php"), "tests/sealed_vectors.php", str(VFILE)])
    assert res["fail"] == [] and res["pass"] > 100
    assert res["purposes"] == V["purposes"]

def test_fresh_seals_cross_between_javascript_and_python(tmp_path):

    node = need_tool("node")
    g = bytes.fromhex(K["link_key_hex"])
    rpk = bytes.fromhex(K["recipient_public_hex"])
    km = {S.kid_of(rpk): priv("recipient")}
    picks = json.dumps({"v": 1, "at": "2026-11-16T12:00:00.000Z", "picks": [{"i": 0, "name": "a.jpg", "r": True,
                                                                              "note": "café"}]}).encode()
    py_env = S.seal_link("draft", "g-Gal_xyz-123ABCDEFGHIJKL", g, picks)
    job = {"seal": [dict(purpose="picks", gid="g-Gal_xyz-123ABCDEFGHIJKL", link_key_hex=g.hex(), plaintext_hex=picks.hex()),
                    dict(purpose="inquiry", gid="", recipient_public_hex=rpk.hex(), plaintext_hex=b'{"v":1,"name":"Bo"}'.hex()),
                    dict(purpose="print-order", gid="g-Gal_xyz-123ABCDEFGHIJKL", link_key_hex=g.hex(), recipient_public_hex=rpk.hex(),
                         plaintext_hex=b'{"v":1,"oid":"x"}'.hex())],
           "open": [dict(purpose="draft", gid="g-Gal_xyz-123ABCDEFGHIJKL", link_key_hex=g.hex(), envelope_hex=py_env.hex()),
                    dict(purpose="picks", gid="g-Gal_xyz-123ABCDEFGHIJKL", link_key_hex=g.hex(), envelope_hex=py_env.hex())]}
    (tmp_path / "job.json").write_text(json.dumps(job))
    res = _run([node, "tests/sealed_vectors.mjs", "cross", str(tmp_path / "job.json")])
    js_picks, js_inq, js_order = (bytes.fromhex(h) for h in res["sealed"])
    assert S.open_link("picks", "g-Gal_xyz-123ABCDEFGHIJKL", g, js_picks) == picks
    assert S.open_studio("inquiry", km, js_inq) == b'{"v":1,"name":"Bo"}'
    assert S.open_studio("print-order", km, js_order, gid="g-Gal_xyz-123ABCDEFGHIJKL", link_key=g) == b'{"v":1,"oid":"x"}'
    assert res["opened"] == [picks.hex(), "E_PURPOSE"]
    for env in (js_picks, js_inq, js_order, py_env):
        S.host_check(env, {1: "picks", 3: "inquiry", 4: "print-order", 2: "draft"}[env[5]],
                     "" if env[4] == 2 else "g-Gal_xyz-123ABCDEFGHIJKL")

    again = _run([node, "tests/sealed_vectors.mjs", "cross", str(tmp_path / "job.json")])
    assert again["sealed"][0] != res["sealed"][0] and again["sealed"][1][30:160] != res["sealed"][1][30:160]
