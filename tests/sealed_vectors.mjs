// The LAS1 vectors (tests/vectors/sealed-v1.json) against the JavaScript library (plugins/sealed_assets/sealed.js),
// in Node 20. Prints one JSON line: {"pass": n, "fail": [names], "purposes": {...}}.
//   node tests/sealed_vectors.mjs <vectors.json>
//   node tests/sealed_vectors.mjs cross <job.json>    seals and opens what the job lists (for tests/test_sealed_vectors.py)
// The library's randomness is only crypto.getRandomValues (the nonce) and subtle.generateKey (the one-time key): both
// are replaced here, per vector, to reproduce its bytes.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "..", "plugins", "sealed_assets", "sealed.js"), "utf8");
const LAS = new Function(src + "\nreturn LAS;")();
const subtle = globalThis.crypto.subtle;
const realRandom = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
const realGenerate = subtle.generateKey.bind(subtle);
const hex = h => Uint8Array.from((h || "").match(/../g) || [], x => parseInt(x, 16));
const toHex = u => Array.from(u, b => b.toString(16).padStart(2, "0")).join("");
const b64u = u => Buffer.from(u).toString("base64url");
const sha = async u => toHex(new Uint8Array(await subtle.digest("SHA-256", u)));
const te = new TextEncoder();

function fix(nonceHex, ephPrivHex, ephPubHex) {
  globalThis.crypto.getRandomValues = a => { a.set(hex(nonceHex).subarray(0, a.length)); return a; };
  subtle.generateKey = async () => {
    const p = hex(ephPubHex);
    const jwk = { kty: "EC", crv: "P-256", d: b64u(hex(ephPrivHex)), x: b64u(p.slice(1, 33)), y: b64u(p.slice(33)), ext: true };
    const alg = { name: "ECDH", namedCurve: "P-256" };
    return { privateKey: await subtle.importKey("jwk", jwk, alg, false, ["deriveBits"]),
             publicKey: await subtle.importKey("raw", p, alg, true, []) };
  };
}
function unfix() { globalThis.crypto.getRandomValues = realRandom; subtle.generateKey = realGenerate; }
const code = async f => { try { await f(); return ""; } catch (e) { if (!e.code) throw e; return e.code; } };
const rulePt = n => Uint8Array.from({ length: n }, (_, i) => i % 251);
const ruleEnv = r => { const p = hex(r.prefix_hex), o = new Uint8Array(r.total_len).fill(parseInt(r.fill_byte, 16)); o.set(p); return o; };

async function vectors(file) {
  const V = JSON.parse(fs.readFileSync(file, "utf8")), K = V.keys;
  let pass = 0; const failed = [];
  const ok = (cond, name) => { if (cond) pass++; else failed.push(name); };
  const purposes = {};
  for (const [k, p] of Object.entries(LAS.PURPOSES)) purposes[k] = { code: p.code, mode: p.mode, max: p.max, kinds: p.kinds };
  ok(JSON.stringify(purposes) === JSON.stringify(V.purposes), "purposes");
  for (const v of V.valid) {
    const p = V.purposes[v.purpose], pt = v.plaintext_rule ? rulePt(v.plaintext_rule.len) : hex(v.plaintext_hex);
    const gk = v.link_key ? hex(K[v.link_key]) : null;
    fix(v.nonce_hex, K.eph_private_hex, K.eph_public_hex);
    let env;
    try {
      env = p.mode === 1 ? await LAS.sealLink(v.purpose, v.gid, gk, pt)
        : await LAS.sealStudio(v.purpose, hex(K[v.recipient + "_public_hex"]), pt, p.mode === 3 ? { gid: v.gid, linkKey: gk } : undefined);
    } finally { unfix(); }
    if (v.envelope_hex) ok(toHex(env) === v.envelope_hex && LAS.encode(env) === v.envelope_b64u, v.name + ":seal");
    else ok(env.length === v.envelope_len && await sha(env) === v.envelope_sha256, v.name + ":seal");
    const kid = p.mode === 1 ? null : hex(K.kid_hex);
    ok(await code(() => LAS.check(env, v.purpose, v.gid, kid)) === "", v.name + ":check");
    ok(toHex(await LAS.kidOf(hex(K.recipient_public_hex))) === K.kid_hex, v.name + ":kid");
    if (p.mode === 1) ok(toHex(await LAS.openLink(v.purpose, v.gid, gk, env)) === toHex(pt), v.name + ":open");
  }
  for (const v of V.invalid) {
    const env = v.envelope_rule ? ruleEnv(v.envelope_rule) : hex(v.envelope_hex), x = v.expect;
    ok(await code(() => LAS.check(env, x.purpose, x.gid, x.kid_hex ? hex(x.kid_hex) : null)) === v.check, v.name + ":check");
    if (V.purposes[x.purpose].mode === 1)
      ok(await code(() => LAS.openLink(x.purpose, x.gid, hex(K[x.link_key]), env)) === v.open, v.name + ":open");
  }
  for (const t of V.transport) {
    const d = t.d_rule ? t.d_rule.char.repeat(t.d_rule.len) : t.d;
    let got = "", out = null;
    try { out = LAS.decode(d, t.purpose); } catch (e) { if (!e.code) throw e; got = e.code; }
    let same = got === t.error;
    if (same && !got) same = t.bytes_hex !== undefined ? toHex(out) === t.bytes_hex : out.length === t.bytes_len && await sha(out) === t.bytes_sha256;
    ok(same, t.name);
  }
  return { pass, fail: failed, purposes };
}

// cross: { seal: [{purpose, gid, link_key_hex, recipient_public_hex, plaintext_hex}],
//          open: [{purpose, gid, link_key_hex, envelope_hex}] } -> { sealed: [hex], opened: [hex or "E_..."] }
async function cross(file) {
  const J = JSON.parse(fs.readFileSync(file, "utf8")), out = { sealed: [], opened: [] };
  for (const s of J.seal || []) {
    const p = LAS.PURPOSES[s.purpose], gk = s.link_key_hex ? hex(s.link_key_hex) : null;
    const env = p.mode === 1 ? await LAS.sealLink(s.purpose, s.gid, gk, hex(s.plaintext_hex))
      : await LAS.sealStudio(s.purpose, hex(s.recipient_public_hex), hex(s.plaintext_hex), p.mode === 3 ? { gid: s.gid, linkKey: gk } : undefined);
    out.sealed.push(toHex(env));
  }
  for (const o of J.open || []) {
    try { out.opened.push(toHex(await LAS.openLink(o.purpose, o.gid, hex(o.link_key_hex), hex(o.envelope_hex)))); }
    catch (e) { out.opened.push(e.code || "error"); }
  }
  return out;
}

const res = process.argv[2] === "cross" ? await cross(process.argv[3]) : await vectors(process.argv[2]);
process.stdout.write(JSON.stringify(res) + "\n");
