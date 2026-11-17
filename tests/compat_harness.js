"use strict";
const fs = require("fs");
const [, , pagePath, base, keyB64, gid] = process.argv;
const html = fs.readFileSync(pagePath, "utf8");
const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];

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

(async () => {
  const els = {};
  const root = el("html");
  root.dataset.gid = gid; root.dataset.gate = "1";
  const grid = el("div", "grid");
  els.grid = grid;
  const document = {
    documentElement: root, getElementById: id => (els[id] = els[id] || el("div", id)), createElement: t => el(t),
    querySelector: () => el("x"), querySelectorAll: () => [], addEventListener() {}, body: el("body"), activeElement: null,
    fonts: { add() {} }, visibilityState: "visible",
  };
  const mem = new Map();
  const localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
  const calls = [];
  const fetch = async (u, o = {}) => {
    const r = await globalThis.fetch(base + u, o);
    if (/^(picks|seen|gate)\.php/.test(u)) calls.push({ u: u.replace(/\?.*$/, "") + (u.includes("kind=draft") ? "?kind=draft" : ""),
      status: r.status, set: r.headers.get("x-host-scripts") || "" });
    return r;
  };
  const location = { hash: "#k=" + keyB64, pathname: "/g/" + gid + "/", href: "" };
  const g = { document, fetch, location, localStorage,
    window: { crypto: globalThis.crypto, addEventListener() {}, innerWidth: 1024 }, crypto: globalThis.crypto,
    IntersectionObserver: class { observe() {} unobserve() {} }, URL, Blob, TextEncoder, TextDecoder, atob, btoa, setTimeout, clearTimeout };
  new Function(...Object.keys(g), src)(...Object.values(g));
  for (let i = 0; i < 80 && !grid.children.length; i++) await new Promise(r => setTimeout(r, 50));
  const opened = grid.children.length > 0;
  if (opened) {
    grid.children[0].onclick({ target: {} });
    await new Promise(r => setTimeout(r, 50));
    els.pr.onclick({ stopPropagation() {} });
    await new Promise(r => setTimeout(r, 2300));             // the draft goes up a moment after the change
    await els.send.onclick({ preventDefault() {} });
    await new Promise(r => setTimeout(r, 1200));
  }
  process.stdout.write(JSON.stringify({ opened, calls, sent: (els.sent || el("x")).textContent,
    tray: (els.trayn || el("x")).textContent, mailto: location.href || "" }));
  process.exit(0);
})();

