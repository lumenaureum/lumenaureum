"use strict";
const fs = require("fs");
const LSO = require("./ls_open.js");
const html = fs.readFileSync(process.argv[2], "utf8");
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const subtle = globalThis.crypto.subtle;
const LAS = new Function(fs.readFileSync(require("path").join(__dirname, "..", "plugins", "sealed_assets", "sealed.js"), "utf8")
  + "\nreturn LAS;")();
const GID = "g-galXYZAAAAAAAAAAAAAAAAA";

function el(tag, id) {
  const cls = new Set();
  return {
    tagName: tag, id, children: [], hidden: false, className: "", dataset: {}, attrs: {}, _text: "", href: "", value: "",
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), toggle: (c, on) => ((on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c)),
                 contains: c => cls.has(c) },
    set textContent(v) { this.children = []; this._text = String(v); },
    get textContent() { return this._text + this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); },
    append(...c) { this.children.push(...c); }, prepend(...c) { this.children.unshift(...c); }, remove() {},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
    focus() {}, addEventListener() {}, querySelector() { return el("x"); },
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
  };
}

async function seal(keyBytes, data, name) {
  const k = await subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode("lumen-aureum-gallery:" + name) }, k, data));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12); return out;
}
async function unseal(keyBytes, body, purpose) {
  if (!(body instanceof URLSearchParams)) return { bad: "not a form" };     // the envelope goes as a form field, d=
  try {
    return JSON.parse(new TextDecoder().decode(await LAS.openLink(purpose, GID, keyBytes, LAS.decode(body.get("d")))));
  } catch (e) { return { bad: e.code || "open" }; }
}
const items = names => names.map((n, i) => ({ f: i.toString(16).padStart(24, "0") + ".bin", t: (i + 100).toString(16).padStart(24, "0") + ".bin", w: 10, h: 10, tw: 5, th: 5, n }));

async function run({ draft, store = {}, pick = false }) {
  const key = globalThis.crypto.getRandomValues(new Uint8Array(32));
  const manifest = { v: 1, title: "Lane", items: items(["a.jpg", "b.jpg", "c.jpg"]), brand: {}, picks: { retouch: 3, market: 2, post: true } };
  const man = await seal(key, new TextEncoder().encode(JSON.stringify(manifest)), "manifest.bin");
  let hostDraft = null;
  if (draft && typeof draft === "object") {
    hostDraft = await LAS.sealLink("draft", GID, key, new TextEncoder().encode(JSON.stringify({ v: 1, ...draft })));
  } else if (draft === "garbage") hostDraft = globalThis.crypto.getRandomValues(new Uint8Array(80));
  const els = {};
  const root = el("html");
  root.dataset.gid = GID; root.dataset.gate = "1";
  const document = {
    documentElement: root, getElementById: id => (els[id] = els[id] || el("div", id)), createElement: t => el(t),
    querySelector: () => el("x"), querySelectorAll: () => [], addEventListener() {}, body: el("body"), activeElement: null,
    fonts: { add() {} }, visibilityState: "visible",
  };
  const mem = new Map(Object.entries(store));
  const localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
  const posts = [];
  const bin = b => ({ ok: true, status: 200, headers: { get: () => "application/octet-stream" }, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) });
  const status = s => ({ ok: s >= 200 && s < 300, status: s, headers: { get: () => "" }, arrayBuffer: async () => new ArrayBuffer(0) });
  const fetch = async (u, o = {}) => {
    if (u === "gate.php?f=manifest.bin") return bin(man);
    if (u === "gate.php?f=draft.las") {
      if (!o.headers || !/^[0-9a-f]{32}$/.test(o.headers["X-Proof"] || "")) return status(403);
      if (draft === "network") throw new TypeError("Failed to fetch");
      return hostDraft ? bin(hostDraft) : status(404);
    }
    if (u === "seen.php") return status(204);
    if (u.startsWith("picks.php")) { posts.push({ u, proof: (o.headers || {})["X-Proof"] || "", body: o.body }); return status(204); }
    return status(404);
  };
  const location = { hash: "#k=" + Buffer.from(key).toString("base64url"), pathname: "/g/x/", href: "" };
  const g = { document, fetch, location, localStorage,
    window: { crypto: globalThis.crypto, addEventListener() {}, innerWidth: 1024 }, crypto: globalThis.crypto,
    IntersectionObserver: class { observe() {} unobserve() {} }, URL, Blob, TextEncoder, TextDecoder, atob, setTimeout, clearTimeout };
  new Function(...Object.keys(g), src)(...Object.values(g));
  await new Promise(r => setTimeout(r, 400));
  if (pick) {                                     // open photo 2, press Retouch, type a note
    els.grid.children[1].onclick({ target: {} });
    await new Promise(r => setTimeout(r, 50));
    els.pr.onclick({ stopPropagation() {} });
    els.note.value = "less glare"; els.note.oninput();
  }
  await new Promise(r => setTimeout(r, 2200));
  const drafts = [];
  for (const p of posts) drafts.push({ u: p.u, proof: p.proof.length, payload: await unseal(key, p.body, p.u.includes("kind=draft") ? "draft" : "picks") });
  const saved = await LSO.open(mem, key, LSO.names("/g/x/", GID));
  return { kept: JSON.parse(saved["picks:/g/x/"] || "null"), stored: Object.fromEntries(mem), tray: els.trayn ? els.trayn.textContent : "", drafts };
}

(async () => {
  const old = "2026-11-10T10:00:00.000Z", newer = "2026-11-12T10:00:00.000Z";
  const local = { "picks:/g/x/": JSON.stringify({ v: 2, picks: { "n:a.jpg": { r: true, n: "soften" } }, at: old }) };
  const localNew = { "picks:/g/x/": JSON.stringify({ v: 2, picks: { "n:a.jpg": { r: true, n: "soften" } }, at: newer }) };
  const hostPicks = [{ i: 2, name: "c.jpg", k: "n:c.jpg", r: false, note: "", m: true, v: false },
                     { i: 1, name: "b.jpg", r: true, note: "brighter", m: false, v: false }];
  const cases = {
    host_newer: { draft: { at: newer, picks: hostPicks }, store: local },
    local_newer: { draft: { at: old, picks: hostPicks }, store: localNew },
    host_unreachable: { draft: "network", store: local },
    host_garbage: { draft: "garbage", store: local },
    host_missing: { draft: "missing", store: local },
    pick_saves_draft: { draft: "missing", pick: true },
  };
  const names = Object.keys(cases);
  const res = await Promise.all(names.map(n => run(cases[n])));
  process.stdout.write(JSON.stringify(Object.fromEntries(names.map((n, i) => [n, res[i]]))));
  process.exit(0);
})();

