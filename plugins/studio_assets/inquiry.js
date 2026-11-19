// The studio page's inquiry form. What the visitor types is sealed here, in their browser, to the studio's public key
// (the shared sealed format, LAS1, purpose inquiry: ECDH P-256 with a one-time key, HKDF-SHA256, AES-256-GCM), and only
// the sealed envelope is sent: the web host keeps it until Lumen Aureum, which holds the private key, collects it. Before sending, the browser searches for 8 bytes w
// such that SHA-256(SHA-256(envelope) . w) starts with data-work zero bits (about a second on a phone), which
// inquiry.php checks with two hashes. Without script or this browser's crypto, the form stays hidden and the email link
// is the way to write.
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
(function () {
  "use strict";
  var f = document.getElementById("inq");
  var subtle = window.crypto && window.crypto.subtle;
  if (!f || !subtle || !window.fetch || !window.TextEncoder) return;
  var started = Date.now(), msg = document.getElementById("inq-msg"), alt = document.getElementById("inq-alt");
  // The inquiry's item ID (128 random bits): the same for every try at sending the same words, so the app takes it in
  // once however many copies reach the host; a new one when the words change between tries (an edited inquiry is
  // another inquiry: a copy of the first try may have reached the host) and once it is sent.
  var newId = function () { return Array.from(crypto.getRandomValues(new Uint8Array(16)), function (b) { return (b + 256).toString(16).slice(1); }).join(""); };
  var itemId = newId(), tried = null;
  f.hidden = false;
  if (alt) alt.hidden = true;
  function b64u(a) {
    var s = "", i;
    for (i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function unb64u(t) {
    var s = atob(t.replace(/-/g, "+").replace(/_/g, "/")), a = new Uint8Array(s.length), i;
    for (i = 0; i < s.length; i++) a[i] = s.charCodeAt(i);
    return a;
  }
  var K = new Int32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb,
    0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f,
    0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  var W = new Int32Array(64);
  // the first 32 bits of SHA-256 of one 40-byte message (in m, as 16 big-endian words, padding included)
  function head32(m) {
    var t, a, b, c, d, e, g, h, k, x, y, t1, t2;
    for (t = 0; t < 16; t++) W[t] = m[t];
    for (t = 16; t < 64; t++) {
      x = W[t - 15]; y = W[t - 2];
      W[t] = (W[t - 16] + (((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3)) + W[t - 7]
        + (((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10))) | 0;
    }
    a = 0x6a09e667 | 0; b = 0xbb67ae85 | 0; c = 0x3c6ef372 | 0; d = 0xa54ff53a | 0;
    e = 0x510e527f | 0; k = 0x9b05688c | 0; g = 0x1f83d9ab | 0; h = 0x5be0cd19 | 0;
    for (t = 0; t < 64; t++) {
      t1 = (h + (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))) + ((e & k) ^ (~e & g)) + K[t] + W[t]) | 0;
      t2 = ((((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = k; k = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    return (a + 0x6a09e667) >>> 0;
  }
  function hex8(n) { return ("0000000" + (n >>> 0).toString(16)).slice(-8); }
  // w for this envelope: SHA-256(SHA-256(envelope) . w) starts with `bits` zero bits
  async function work(env, bits) {
    var dg = new DataView(await subtle.digest("SHA-256", env)), m = new Int32Array(16), i, n = 0;
    for (i = 0; i < 8; i++) m[i] = dg.getInt32(i * 4);
    m[8] = window.crypto.getRandomValues(new Int32Array(1))[0];
    m[10] = 0x80000000 | 0; m[15] = 320;
    bits = Math.max(0, Math.min(32, bits | 0));
    for (;;) {
      for (i = 0; i < 20000; i++, n++) {
        m[9] = n;
        if (!bits || head32(m) >>> (32 - bits) === 0) return hex8(m[8]) + hex8(n);
      }
      await new Promise(function (r) { setTimeout(r, 0); });   // let the page breathe
    }
  }
  function say(t, bad) { msg.textContent = t; msg.className = bad ? "bad" : ""; msg.hidden = false; }
  function v(n) { return (f.elements[n] && f.elements[n].value || "").trim(); }
  function seal(text) {
    return LAS.sealStudio("inquiry", unb64u(f.dataset.key), new TextEncoder().encode(text));
  }
  f.addEventListener("submit", async function (e) {
    e.preventDefault();
    var email = v("email"), phone = v("phone");
    if (!v("name")) { say("Please add your name.", true); f.elements.name.focus(); return; }
    if (!email && !phone) { say("Please add an email address or a phone number, so you can be reached.", true); f.elements.email.focus(); return; }
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { say("That email address doesn't look complete.", true); f.elements.email.focus(); return; }
    if (f.dataset.off) { say("This is a preview: sending is off.", false); return; }
    var btn = f.querySelector("button[type=submit]");
    if (btn.disabled) return;
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Sending...";
    btn.setAttribute("aria-busy", "true");
    f.setAttribute("aria-busy", "true");
    msg.hidden = true;
    try {
      // sent within 3 seconds of the page opening: wait until they have passed, then send for real (the app drops a
      // form sent faster than a person fills one in, and one with the hidden field filled in)
      var early = 3000 - (Date.now() - started);
      if (early > 0) await new Promise(function (r) { setTimeout(r, early + 50); });
      var words = JSON.stringify([v("name"), email, phone, v("type"), v("date"), v("message"), v("website")]);
      if (tried !== null && tried !== words) itemId = newId();
      tried = words;
      var body = JSON.stringify({ v: 1, id: itemId, name: v("name"), email: email, phone: phone, type: v("type"), date: v("date"),
        message: v("message"), hp: v("website"), t: Date.now() - started });
      var env = await seal(body), w = await work(env, +f.dataset.work || 0);
      var post = function (w) { return fetch(f.dataset.post, { method: "POST", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: "w=" + w + "&d=" + b64u(env) }); };
      var r = await post(w);
      // behind a proxy or CDN the host asks each inquiry for more work (429, X-Work: bits): worked out, sent again
      var more = r.status === 429 && /^(\d{1,2})$/.exec(r.headers.get("X-Work") || "");
      if (more) r = await post(await work(env, +more[1]));
      if (r.status !== 204) throw new Error(String(r.status));
      itemId = newId();
      tried = null;
      f.reset();                       // sent: a page brought back from the browser's history starts empty
      f.hidden = true;
      var intro = document.getElementById("inq-intro"); if (intro) intro.hidden = true;   // sent: the invitation to ask is done
      say(f.dataset.thanks, false);
    } catch (err) {
      say(f.dataset.fail, true);
      if (alt) alt.hidden = false;
    } finally {
      btn.disabled = false;            // sent or not, the button reads Send again
      btn.textContent = label;
      btn.removeAttribute("aria-busy");
      f.removeAttribute("aria-busy");
    }
  });
})();
