"use strict";
const fs = require("fs");
const LSO = require("./ls_open.js");
const html = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const subtle = globalThis.crypto.subtle;

function el(tag, id) {
  const cls = new Set();
  return {
    tagName: tag, id, children: [], hidden: false, className: "", dataset: {}, attrs: {}, _text: "", href: "",
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), toggle: (c, on) => ((on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c)),
                 contains: c => cls.has(c), list: cls },
    set textContent(v) { this.children = []; this._text = String(v); },
    get textContent() { return this._text + this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); },
    append(...c) { this.children.push(...c); }, prepend(...c) { this.children.unshift(...c); }, remove() {},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
    focus() {}, addEventListener() {}, querySelector() { return el("x"); },
  };
}

async function seal(keyBytes, data, name) {
  const k = await subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode("lumen-aureum-gallery:" + name) }, k, data));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12); return out;
}

async function run({ manifest, store = {} }) {
  const key = globalThis.crypto.getRandomValues(new Uint8Array(32));
  const blob = await seal(key, new TextEncoder().encode(JSON.stringify(manifest)), "manifest.bin");
  const els = {};
  const root = el("html");
  root.dataset.gid = "galX";
  const document = {
    documentElement: root, getElementById: id => (els[id] = els[id] || el("div", id)), createElement: t => el(t),
    querySelector: () => el("x"), querySelectorAll: () => [], addEventListener() {}, body: el("body"), activeElement: null,
    fonts: { add() {} },
  };
  const mem = new Map(Object.entries(store));
  const localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
  const fetch = async () => ({ ok: true, status: 200, headers: { get: () => "application/octet-stream" }, arrayBuffer: async () => blob.buffer.slice(0) });
  class FontFace { constructor(fam, url) { this.fam = fam; this.url = url; FontFace.made.push([fam, url]); } load() { return Promise.resolve(this); } }
  FontFace.made = [];
  const location = { hash: "#k=" + Buffer.from(key).toString("base64url"), pathname: "/g/x/", href: "" };
  const g = { document, fetch, location, localStorage, FontFace,
    window: { crypto: globalThis.crypto, addEventListener() {}, innerWidth: 1024, FontFace }, crypto: globalThis.crypto,
    IntersectionObserver: class { observe() {} unobserve() {} }, URL, Blob, TextEncoder, TextDecoder, atob, setTimeout };
  new Function(...Object.keys(g), src)(...Object.values(g));
  await new Promise(r => setTimeout(r, 400));
  const body = decodeURIComponent((els.send ? els.send.href : "").split("&body=")[1] || "");
  return { vars: root.style.props, classes: [...root.classList.list], fonts: FontFace.made,
           lines: body.split("\n").filter(l => l && !l.startsWith("Fingerprint")),
           saved: await LSO.open(mem, key, LSO.names("/g/x/", "galX")), stored: Object.fromEntries(mem), title: els.title ? els.title.textContent : "",
           rights: els.rights && !els.rights.hidden ? els.rights.textContent : "" };
}

(async () => {
  const out = {};
  for (const [name, c] of Object.entries(cases)) out[name] = await run(c);
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();

