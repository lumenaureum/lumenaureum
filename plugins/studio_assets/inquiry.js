(function () {
  "use strict";
  var f = document.getElementById("inq");
  var subtle = window.crypto && window.crypto.subtle;
  if (!f || !subtle || !window.fetch || !window.TextEncoder) return;
  var started = Date.now(), msg = document.getElementById("inq-msg"), alt = document.getElementById("inq-alt");
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
  async function seal(text) {
    var site = unb64u(f.dataset.key);
    var spub = await subtle.importKey("raw", site, { name: "ECDH", namedCurve: "P-256" }, false, []);
    var eph = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    var epub = new Uint8Array(await subtle.exportKey("raw", eph.publicKey));
    var shared = await subtle.deriveBits({ name: "ECDH", public: spub }, eph.privateKey, 256);
    var hk = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    var salt = new Uint8Array(epub.length + site.length);
    salt.set(epub); salt.set(site, epub.length);
    var key = await subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: salt, info: new TextEncoder().encode("lumen-aureum inquiry v1") },
      hk, { name: "AES-GCM", length: 256 }, false, ["encrypt"]);
    var iv = window.crypto.getRandomValues(new Uint8Array(12));
    var head = new TextEncoder().encode("LAQ1");
    var ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv: iv, additionalData: head }, key, new TextEncoder().encode(text)));
    var out = new Uint8Array(4 + 65 + 12 + ct.length);
    out.set(head); out.set(epub, 4); out.set(iv, 69); out.set(ct, 81);
    return out;
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
      var early = 3000 - (Date.now() - started);
      if (early > 0) await new Promise(function (r) { setTimeout(r, early + 50); });
      var body = JSON.stringify({ v: 1, name: v("name"), email: email, phone: phone, type: v("type"), date: v("date"),
        message: v("message"), hp: v("website"), t: Date.now() - started });
      var env = await seal(body), w = await work(env, +f.dataset.work || 0);
      var r = await fetch(f.dataset.post, { method: "POST", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: "w=" + w + "&d=" + b64u(env) });
      if (r.status !== 204) throw new Error(String(r.status));
      f.reset();                       // sent: a page brought back from the browser's history starts empty
      f.hidden = true;
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

