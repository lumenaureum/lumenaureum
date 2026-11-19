"use strict";
const fs = require("fs");
const LSO = require("./ls_open.js");
const html = fs.readFileSync(process.argv[2], "utf8");
const SPK = process.argv[3];
const CASES = JSON.parse(process.argv[4]);
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const subtle = globalThis.crypto.subtle;
const GID = "g-galPrints14aAAAAAAAAAAA";

function el(tag, id) {
  const cls = new Set();
  const e = {
    tagName: tag, id, children: [], hidden: false, className: "", dataset: {}, attrs: {}, _text: "", href: "", value: "", checked: false,
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), toggle: (c, on) => ((on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c)),
                 contains: c => cls.has(c) },
    set textContent(v) { this.children = []; this._text = String(v); },
    get textContent() { return this._text + this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); },
    append(...c) { this.children.push(...c); }, prepend(...c) { this.children.unshift(...c); }, remove() {}, after() {},
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }, getAttribute(k) { return this.attrs[k]; },
    removeAttribute(k) { delete this.attrs[k]; }, focus() { this.focused = true; }, scrollIntoView() { this.scrolled = true; }, addEventListener() {}, querySelector() { return el("x"); },
    querySelectorAll() { return []; }, contains() { return false; },
    getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100 }; },
  };
  Object.defineProperty(e, "className", { get() { return [...cls].join(" "); }, set(v) { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => cls.add(c)); } });
  return e;
}
function walk(e, out = []) { if (e && typeof e === "object") { out.push(e); (e.children || []).forEach(c => walk(c, out)); } return out; }

async function seal(keyBytes, data, name) {
  const k = await subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode("lumen-aureum-gallery:" + name) }, k, data));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12); return out;
}
const hex = i => i.toString(16).padStart(24, "0") + ".bin";

async function run(c) {
  const key = globalThis.crypto.getRandomValues(new Uint8Array(32));
  const items = [{ f: hex(1), t: hex(2), o: hex(3), w: 1200, h: 800, tw: 300, th: 200, ow: 6000, oh: 4000, n: "Lane-034.jpg", on: "Lane-034.jpg", os: 1, z: 0 }];
  const manifest = { v: 1, title: "Lane", items, brand: { studio: "Sample Studio", email: "studio@example.com" }, picks: { post: true }, dl: 1, zips: [] };
  const offer = { v: 1, plv: 4, cur: "USD", kid: "0".repeat(16), spk: SPK, contact: "studio@example.com", studio: "Sample Studio",
    items: [{ id: "pl-1", label: "4x6 print, Lustre", w: 4, h: 6, each: 1500, ppi_hide: 150, ppi_mark: 200 },
            { id: "pl-3", label: "8x10 print, Lustre", w: 8, h: 10, each: 3000, ppi_hide: 150, ppi_mark: 200 },
            { id: "pl-9", label: "40x60 print, Lustre", w: 40, h: 60, each: 90000, ppi_hide: 150, ppi_mark: 200 }],
    ship: { us: { on: true, say: "Shipping at cost, on your invoice" }, akhi: { on: true, say: "" }, ca: { on: false, say: "" },
            pickup: { on: true, say: "Pick up from Sample Studio" } },
    invoice_days: 3, lines_max: 50, qty_max: 25, arrive: "Prints come straight from our lab.",
    states: { us: ["IL", "NY"], akhi: ["AK", "HI"], ca: [] } };
  const man = await seal(key, new TextEncoder().encode(JSON.stringify(manifest)), "manifest.bin");
  const off = c.no_offer ? null : await seal(key, new TextEncoder().encode(JSON.stringify(c.email_only ? { v: 1, email_only: true, contact: "studio@example.com" } : offer)), "offer.bin");
  const els = {};
  const root = el("html");
  root.dataset.gid = GID; root.dataset.gate = "1";
  const document = {
    documentElement: root, getElementById: id => (els[id] = els[id] || el("div", id)), createElement: t => el(t),
    querySelector: () => el("x"), querySelectorAll: () => [], addEventListener() {}, removeEventListener() {}, body: el("body"), activeElement: null,
    fonts: { add() {} }, visibilityState: "visible",
  };
  const mem = new Map(Object.entries(c.store || {})), smem = new Map();
  const localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
  const sessionStorage = { getItem: k => (smem.has(k) ? smem.get(k) : null), setItem: (k, v) => smem.set(k, String(v)), removeItem: k => smem.delete(k) };
  const posts = [], gets = [];
  const statuses = (c.statuses || [204]).slice();
  const bin = (b, hd) => ({ ok: true, status: 200, headers: { get: n => (hd && n in hd ? hd[n] : "application/octet-stream") }, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) });
  const status = a => { const s = typeof a === "object" ? a.s : a, hd = (typeof a === "object" && a.h) || {};
    return { ok: s >= 200 && s < 300, status: s, headers: { get: n => hd[n] || "" }, arrayBuffer: async () => new ArrayBuffer(0), json: async () => ({}) }; };
  const fetch = async (u, o = {}) => {
    gets.push(u);
    if (u === "gate.php?f=manifest.bin") return bin(man, c.manifest_headers);
    if (u === "gate.php?f=offer.bin") return off ? bin(off) : status(404);
    if (u === "seen.php") return status(204);
    if (u === "orders.php") { posts.push({ proof: (o.headers || {})["X-Proof"] || "", body: String(o.body) }); return status(statuses.length ? statuses.shift() : 204); }
    return status(404);
  };
  const location = { hash: "#k=" + Buffer.from(key).toString("base64url"), pathname: "/g/x/", href: "" };
  const g = { document, fetch, location, localStorage, sessionStorage,
    window: { crypto: globalThis.crypto, addEventListener() {}, innerWidth: 1024, print() {} }, crypto: globalThis.crypto,
    IntersectionObserver: class { observe() {} unobserve() {} }, URL, Blob, TextEncoder, TextDecoder, atob, btoa, setTimeout, clearTimeout, Image: class {} };
  new Function(...Object.keys(g), src)(...Object.values(g));
  await new Promise(r => setTimeout(r, 400));
  const box = () => els.prdbox;
  const buttons = () => walk(box()).filter(e => e.tagName === "button");
  const press = async (text) => { const b = buttons().find(b_ => b_.textContent.startsWith(text)); if (!b) throw new Error("no button " + text + " in: " + box().textContent);
    const p_ = b.onclick({ stopPropagation() {} }); if (p_ && p_.catch) p_.catch(() => {}); await new Promise(r => setTimeout(r, 30)); };
  const out = { prline: els.prline ? els.prline.textContent : "", prbar_hidden: els.prbar ? els.prbar.hidden : true, sizes: [], said: [] };
  out.sswhen = els.sswhen ? els.sswhen.textContent : ""; out.pp_wired = !!(els.pp && els.pp.onclick);
  if (c.email_only || c.no_offer || c.window_only) return out;
  els.grid.children[0].onclick({ target: {} });     // open photo 1
  await new Promise(r => setTimeout(r, 50));
  els.pp.onclick({ stopPropagation() {} });          // Order prints
  out.sizes = walk(box()).filter(e => e.tagName === "button" && e.className.includes("size")).map(b => b.textContent);
  await press(c.size || "8x10");
  out.said.push(box().textContent);
  await press("+");                                  // two of them
  await press("Add to my order");
  await new Promise(r => setTimeout(r, 50));
  out.cart_after_add = JSON.parse((await LSO.open(mem, key, ["cart:" + GID]))["cart:" + GID]);
  out.said.push(box().textContent);
  await press("Continue");
  const field = (id, v) => { const i = walk(box()).find(e => e.id === id); i.value = v; if (i.oninput) i.oninput(); if (i.onchange) i.onchange(); };
  await press("Continue");                           // nothing typed: refused with a reason
  out.said.push(box().textContent);
  out.err_in_view = walk(box()).some(e => e.className === "err" && e.scrolled && e.focused);
  field("pf-name", c.name || "Avery Sample"); field("pf-email", "avery@example.com");
  const radio = walk(box()).find(e => e.attrs && e.attrs.value === (c.how || "pickup") && e.attrs.type === "radio");
  radio.checked = true; radio.onchange();
  if ((c.how || "pickup") !== "pickup") {
    field("pf-addr1", "12 Elm St"); field("pf-city", "Springfield"); field("pf-state", "IL"); field("pf-zip", "62704");
  }
  if (c.ack_first) {                                 // everything but the invoice checkbox: refused AT the checkbox
    await press("Continue");
    const w = walk(box()), lab = w.find(e => e.tagName === "label" && e.className.split(" ").includes("ack"));
    const a = w.find(e => e.id === "pf-ack"), i = w.indexOf(lab), errs = w.filter(e => e.className === "err");
    out.ack = { focused: !!a.focused, want: lab.className.includes("want"), invalid: a.attrs["aria-invalid"] === "true",
                err: errs.map(e => e.textContent), err_after_box: errs.length === 1 && w.indexOf(errs[0]) > i,
                label: lab.textContent, heading: (w.find(e => e.id === "prdt") || {}).textContent };
  }
  const ack = walk(box()).find(e => e.id === "pf-ack"); ack.checked = true; ack.onchange();
  await press("Continue");
  out.said.push(box().textContent);
  await press("Send to");
  if (c.rel) { out.said.push(box().textContent); await press(c.rel); }
  for (let i = 0; i < 100 && !/Sent to|not sent/.test(els.prdt ? "" : "") ; i++) {
    await new Promise(r => setTimeout(r, 50));
    if (/Sent to Sample Studio\.|was not sent|could not take|several orders/.test(box().textContent)) break;
  }
  out.said.push(box().textContent);
  await new Promise(r => setTimeout(r, 50));
  out.session_mid = await LSO.open(smem, key, ["order-pending:" + GID]);
  out.session_mid_stored = Object.fromEntries(smem);        // what this tab keeps while the order waits to be sent
  if (c.again) { await press("Try again"); for (let i = 0; i < 100; i++) { await new Promise(r => setTimeout(r, 50)); if (/Sent to Sample Studio\./.test(box().textContent)) break; }
    out.said.push(box().textContent); }
  out.posts = posts;
  await new Promise(r => setTimeout(r, 100));
  out.store = await LSO.open(mem, key, LSO.names("/g/x/", GID));
  out.session = await LSO.open(smem, key, ["order-pending:" + GID]);
  out.stored = Object.fromEntries(mem);
  out.session_stored = Object.fromEntries(smem);
  return out;
}

setTimeout(() => { process.stderr.write("harness timed out\n"); process.exit(3); }, 150000).unref();
(async () => {
  const res = {};
  for (const [n, c] of Object.entries(CASES)) res[n] = await run(c);
  process.stdout.write(JSON.stringify(res), () => process.exit(0));   // exit once written: a pipe takes it in parts
})();

