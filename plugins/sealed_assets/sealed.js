// sealed.js begin
// The shared sealed format, LAS1 (docs/sealed-format.md): the one source; tools/sync_sealed.py copies it, byte for byte,
// into the gallery page and the studio page's inquiry script. Seals in the browser with WebCrypto only; opens mode 1
// (gallery) envelopes; checks any envelope's shape exactly as sealed.py and sealed.php do. Errors carry .code (E_...).
const LAS = (() => {
  "use strict";
  const PURPOSES = Object.freeze({
    picks: Object.freeze({ code: 1, mode: 1, max: 262144, kinds: "g" }),
    draft: Object.freeze({ code: 2, mode: 1, max: 262144, kinds: "g" }),
    inquiry: Object.freeze({ code: 3, mode: 2, max: 16384, kinds: "" }),
    "print-order": Object.freeze({ code: 4, mode: 3, max: 65536, kinds: "g" }),
  });
  const LINK_LEN = 25;                                // kind letter, "-", 23 characters
  const te = new TextEncoder();
  const LABEL = te.encode("lumen-aureum sealed\0");
  const MAGIC = Uint8Array.of(0x4c, 0x41, 0x53, 0x31);
  const C = () => globalThis.crypto;
  const fail = c => { const e = new Error(c); e.code = c; return e; };
  const u8 = x => x instanceof Uint8Array ? x : new Uint8Array(x);
  const cat = (...a) => { let n = 0; a.forEach(x => { n += x.length; }); const o = new Uint8Array(n); n = 0;
    a.forEach(x => { o.set(x, n); n += x.length; }); return o; };
  const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  const gidByte = b => (b >= 48 && b <= 57) || (b >= 65 && b <= 90) || (b >= 97 && b <= 122) || b === 45 || b === 95;
  const purpose = name => { const p = Object.prototype.hasOwnProperty.call(PURPOSES, name) ? PURPOSES[name] : null;
    if (!p) throw fail("E_PURPOSE"); return p; };
  function parse(env, name, gid, kid) {              // steps 1 to 15 of the check, in order: { mode, n, h }
    const p = purpose(name), e = u8(env), len = e.length;
    if (len < 4) throw fail("E_SHORT");
    if (e[0] !== 0x4c || e[1] !== 0x41 || e[2] !== 0x53) throw fail("E_MAGIC");
    if (e[3] !== 0x31) throw fail("E_VERSION");
    if (len > p.max) throw fail("E_LONG");
    if (len < 7) throw fail("E_SHORT");
    const mode = e[4], n = e[6];
    if (mode < 1 || mode > 3) throw fail("E_MODE");
    if (e[5] !== p.code) throw fail("E_PURPOSE");
    if (mode !== p.mode) throw fail("E_MODE");
    if (mode === 2 ? n !== 0 : n !== LINK_LEN) throw fail("E_SCOPE");
    if (len < 7 + n) throw fail("E_SHORT");
    const got = e.subarray(7, 7 + n);
    if (!got.every(gidByte) || (n && got[1] !== 45) || !same(got, te.encode(gid || ""))) throw fail("E_SCOPE");
    if (n && p.kinds.indexOf(String.fromCharCode(got[0])) < 0) throw fail("E_KIND");
    const h = mode === 1 ? 7 + n : 80 + n;
    if (len < h + 12 + 17) throw fail("E_SHORT");
    if (mode !== 1) {
      if (e[15 + n] !== 0x04) throw fail("E_POINT");
      if (kid && !same(e.subarray(7 + n, 15 + n), u8(kid))) throw fail("E_KID");
    }
    return { mode, n, h };
  }
  const gidOk = (gid, mode) => typeof gid === "string" && (mode === 2 ? gid === "" :
    /^[gsect]-[A-Za-z0-9_-]{23}$/.test(gid));
  const kindOk = (p, gid) => { if (gid && p.kinds.indexOf(gid[0]) < 0) throw fail("E_KIND"); };
  async function aesKey(ikm, salt, binding) {
    const s = C().subtle;
    const base = await s.importKey("raw", ikm, "HKDF", false, ["deriveKey"]);
    return s.deriveKey({ name: "HKDF", hash: "SHA-256", salt, info: cat(LABEL, binding) }, base,
      { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  const binding = (p, gid) => { const g = te.encode(gid); return cat(MAGIC, Uint8Array.of(p.mode, p.code, g.length), g); };
  async function kidOf(pub) { return new Uint8Array(await C().subtle.digest("SHA-256", u8(pub))).slice(0, 8); }
  async function sealLink(name, gid, linkKey, plaintext) {
    const p = purpose(name);
    if (p.mode !== 1) throw fail("E_MODE");
    if (!gidOk(gid, 1)) throw fail("E_SCOPE");
    kindOk(p, gid);
    const pt = u8(plaintext);
    if (!pt.length || 7 + gid.length + 12 + pt.length + 16 > p.max) throw fail("E_LONG");
    if (!linkKey || u8(linkKey).length !== 32) throw fail("E_KEY");
    const b = binding(p, gid), nonce = C().getRandomValues(new Uint8Array(12));
    const key = await aesKey(u8(linkKey), new Uint8Array(0), b);
    const ct = new Uint8Array(await C().subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: b }, key, pt));
    return cat(b, nonce, ct);
  }
  async function openLink(name, gid, linkKey, envelope) {
    const p = purpose(name);
    if (p.mode !== 1) throw fail("E_MODE");
    if (!linkKey || u8(linkKey).length !== 32) throw fail("E_KEY");
    const e = u8(envelope), { h } = parse(e, name, gid, null), head = e.slice(0, h);
    try {
      const key = await aesKey(u8(linkKey), new Uint8Array(0), head);
      return new Uint8Array(await C().subtle.decrypt({ name: "AES-GCM", iv: e.slice(h, h + 12), additionalData: head },
        key, e.slice(h + 12)));
    } catch (x) { throw fail("E_AUTH"); }
  }
  async function sealStudio(name, recipientPublic, plaintext, opt) {
    const p = purpose(name), o = opt || {};
    if (p.mode === 1) throw fail("E_MODE");
    const gid = o.gid || "", gk = o.linkKey;
    if (p.mode === 2 ? gid || gk != null : !gid || gk == null) throw fail("E_KEY");
    if (!gidOk(gid, p.mode)) throw fail("E_SCOPE");
    kindOk(p, gid);
    const pt = u8(plaintext);
    if (!pt.length || 80 + gid.length + 12 + pt.length + 16 > p.max) throw fail("E_LONG");
    if (p.mode === 3 && u8(gk).length !== 32) throw fail("E_KEY");
    const rpk = recipientPublic ? u8(recipientPublic) : new Uint8Array(0);
    if (rpk.length !== 65 || rpk[0] !== 4) throw fail("E_KEY");
    const s = C().subtle, alg = { name: "ECDH", namedCurve: "P-256" };
    let point;
    try { point = await s.importKey("raw", rpk, alg, false, []); } catch (x) { throw fail("E_KEY"); }
    const eph = await s.generateKey(alg, false, ["deriveBits"]);
    const epk = new Uint8Array(await s.exportKey("raw", eph.publicKey));
    let z = new Uint8Array(await s.deriveBits({ name: "ECDH", public: point }, eph.privateKey, 256));
    if (p.mode === 3) z = cat(z, u8(gk));
    const b = binding(p, gid), head = cat(b, await kidOf(rpk), epk);
    const nonce = C().getRandomValues(new Uint8Array(12));
    const key = await aesKey(z, cat(epk, rpk), b);
    const ct = new Uint8Array(await s.encrypt({ name: "AES-GCM", iv: nonce, additionalData: head }, key, pt));
    return cat(head, nonce, ct);
  }
  function check(envelope, name, gid, kid) { parse(envelope, name, gid, kid || null); }
  function encode(a) {
    let s = ""; const b = u8(a);
    for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function decode(d, name) {
    if (name !== undefined && name !== null && typeof d === "string") {
      const m = purpose(name).max;
      if (d.length > Math.floor(m / 3) * 4 + [0, 2, 3][m % 3]) throw fail("E_LONG");
    }
    if (typeof d !== "string" || !/^[A-Za-z0-9_-]+$/.test(d) || d.length % 4 === 1) throw fail("E_ENCODING");
    const s = atob(d.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((d.length + 3) % 4));
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    if (encode(out) !== d) throw fail("E_ENCODING");
    return out;
  }
  return Object.freeze({ PURPOSES, sealLink, openLink, sealStudio, check, kidOf, encode, decode });
})();
// sealed.js end
