// Runs the gallery viewer's script against a small stub DOM and a scripted fetch, and prints what a visitor would see
// at each prompt as JSON. Used by tests/test_gallery_clash_101.py:  node viewer_harness.js <viewer.html>
"use strict";
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const subtle = globalThis.crypto.subtle;

function el(tag, id) {
  const e = {
    tagName: tag, id, children: [], parent: null, hidden: false, disabled: false, className: "", type: "", value: "",
    style: {}, dataset: {}, attrs: {}, _text: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    set innerHTML(v) { this.children = []; this._text = v; }, get innerHTML() { return this._text; },
    set textContent(v) { this.children.forEach(c => { if (typeof c !== "string") c.parent = null; }); this.children = []; this._text = String(v); },
    get textContent() { return this._text + this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); },
    append(...c) { c.forEach(x => { if (typeof x !== "string") { if (x.parent) x.remove(); x.parent = this; } this.children.push(x); }); },
    prepend(...c) { this.children.unshift(...c); },
    remove() { if (this.parent) { this.parent.children = this.parent.children.filter(x => x !== this); this.parent = null; } },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    focus() { focused = this; }, addEventListener() {}, querySelector() { return el("x"); },
  };
  return e;
}
let focused = null;

async function run({ fetchPlan, words = [], key, gid = "galX", wordMode = false, clicks = 5 }) {
  focused = null;
  const els = {};
  const document = {
    documentElement: { dataset: { gid } },
    getElementById: id => (els[id] = els[id] || el("div", id)),
    createElement: t => el(t),
    querySelector: () => el("x"), querySelectorAll: () => [], addEventListener() {}, body: el("body"), activeElement: null,
  };
  let fetches = 0;
  const fetch = async u => { fetches++; return fetchPlan.shift()(u); };
  const location = { hash: "#k=" + Buffer.from(key).toString("base64url") + (wordMode ? "&c=1" : ""), pathname: "/g/x/" };
  const g = { document, fetch, location, window: { crypto: globalThis.crypto }, crypto: globalThis.crypto,
    localStorage: { getItem() { return null; }, setItem() {} },
    IntersectionObserver: class { observe() {} unobserve() {} }, URL, Blob, TextEncoder, TextDecoder, atob, setTimeout };
  const seen = [];
  const snap = what => {
    const act = els.msgact ? els.msgact.children : [];
    const form = act.find(c => c.tagName === "form");
    seen.push({ what, say: els.msgsay ? els.msgsay.textContent : "", err: els.msgerr ? els.msgerr.textContent : "",
      buttons: act.filter(c => c.tagName === "button").map(b => b.textContent),
      form: !!form && !form.hidden, prefill: form ? form.children[0].value : null,
      focus: focused ? (focused.textContent || focused.tagName) : null });
  };
  let busy = false;
  const pump = setInterval(() => {
    if (busy || !els.msgact) return;
    const act = els.msgact.children;
    const again = act.find(c => c.tagName === "button" && c.textContent === "Try again" && !c.disabled);
    const form = act.find(c => c.tagName === "form" && !c.hidden && c.onsubmit && !c.children[1].disabled);
    if (again && clicks-- > 0) { snap("retry"); again.onclick(); return; }
    if (form && words.length) { snap("ask"); form.children[0].value = words.shift(); form.onsubmit({ preventDefault() {} }); }
  }, 5);
  new Function(...Object.keys(g), src)(...Object.values(g));
  await new Promise(r => setTimeout(r, 4500));
  clearInterval(pump);
  snap("end");
  return { fetches, title: els.title ? els.title.textContent : "", seen };
}

async function seal(keyBytes, data, name) {
  const k = await subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode("lumen-aureum-gallery:" + name) }, k, data));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12); return out;
}
async function wordKey(raw, gid, w) {
  const salt = new Uint8Array([...raw, ...new TextEncoder().encode(gid)]);
  const base = await subtle.importKey("raw", new TextEncoder().encode(w), "PBKDF2", false, ["deriveBits"]);
  return new Uint8Array(await subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: 600000 }, base, 256));
}
const ok = buf => async () => ({ ok: true, status: 200, headers: { get: () => "application/octet-stream" }, arrayBuffer: async () => buf.buffer.slice(0) });
const netfail = async () => { throw new TypeError("Failed to fetch"); };
const status = s => async () => ({ ok: false, status: s, headers: { get: () => "" } });

(async () => {
  const key = globalThis.crypto.getRandomValues(new Uint8Array(32));
  const man = new TextEncoder().encode(JSON.stringify({ v: 1, title: "Lane", items: [], picks: {}, brand: {} }));
  const good = await seal(key, man, "manifest.bin");
  const wgood = await seal(await wordKey(key, "galX", "blue river stone"), man, "manifest.bin");
  const out = {};
  out.plain_ok = await run({ key, fetchPlan: [ok(good)] });
  out.plain_network_retry = await run({ key, fetchPlan: [netfail, ok(good)] });
  out.plain_closed = await run({ key, fetchPlan: [status(410)] });
  out.word_wrong_then_right = await run({ key, wordMode: true, words: ["wrong one", "blue river stone"], fetchPlan: [ok(wgood)] });
  out.word_network_retry = await run({ key, wordMode: true, words: ["blue river stone"], fetchPlan: [netfail, ok(wgood)] });
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
