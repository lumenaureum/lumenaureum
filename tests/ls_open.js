"use strict";
const subtle = globalThis.crypto.subtle;
const te = new TextEncoder();

async function keys(key) {
  const base = await subtle.importKey("raw", key, "HKDF", false, ["deriveBits"]);
  const bits = new Uint8Array(await subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0),
    info: te.encode("lumen-aureum local storage") }, base, 512));
  return {
    ak: await subtle.importKey("raw", bits.slice(0, 32), "AES-GCM", false, ["decrypt", "encrypt"]),
    nk: await subtle.importKey("raw", bits.slice(32), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]),
  };
}

async function nameOf(nk, n) {
  return "la:" + Buffer.from(await subtle.sign("HMAC", nk, te.encode(n))).toString("hex").slice(0, 32);
}

function names(path, gid) {
  const p = "picks:" + path;
  return ["look:" + path, p, p + ":sent", p + ":legacy", p + ":welcomed", "cart:" + gid, "orders:" + gid, "order-pending:" + gid];
}

async function open(mem, key, list) {
  const { ak, nk } = await keys(key);
  const out = {};
  for (const n of list) {
    const v = mem.get(await nameOf(nk, n));
    if (v === undefined || v === null) continue;
    const b = Buffer.from(v, "base64");
    out[n] = new TextDecoder().decode(await subtle.decrypt({ name: "AES-GCM", iv: b.subarray(0, 12), additionalData: te.encode(n) },
      ak, b.subarray(12)));
  }
  return out;
}

async function seal(key, n, value) {                 // [stored name, stored value], as the page writes it
  const { ak, nk } = await keys(key);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv, additionalData: te.encode(n) }, ak, te.encode(value)));
  return [await nameOf(nk, n), Buffer.concat([Buffer.from(iv), Buffer.from(ct)]).toString("base64")];
}

module.exports = { names, open, seal };

