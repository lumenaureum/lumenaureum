"use strict";
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const fields = JSON.parse(process.argv[4]);
let now = 1000000;
const RealDate = Date;
class FakeDate extends RealDate { static now() { return now; } }
const els = {};
let submit = null;
const posted = [];
function field(v) { return { value: v || "", focus() {} }; }
const form = {
  hidden: true, dataset: { key: process.argv[3], work: String(fields.work === undefined ? 12 : fields.work), post: "../inquiry.php", thanks: "Thanks.", fail: "Failed." },
  elements: { name: field(fields.name), email: field(fields.email), phone: field(fields.phone), type: field(fields.type),
              date: field(fields.date), message: field(fields.message), website: field(fields.website) },
  addEventListener(ev, fn) { if (ev === "submit") submit = fn; },
  querySelector() { return button; },
  setAttribute() {}, removeAttribute() {},
  reset() { for (const k in this.elements) this.elements[k].value = ""; this.cleared = true; },
};
const button = { disabled: false, textContent: "Send", attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; } };
let busy = null;
els.inq = form;
els["inq-msg"] = { textContent: "", className: "", hidden: true };
els["inq-alt"] = { hidden: false };
const document = { getElementById: id => els[id] || null };
const window = { crypto: globalThis.crypto, fetch: true, TextEncoder };
let answer = 204;                 // what the host answers this try ("tries": a 500 after the envelope reached the host)
const fetch = async (url, opts) => { if (fields.fail) return { status: 500 }; busy = { text: button.textContent, aria: button.attrs["aria-busy"] || null, disabled: button.disabled }; posted.push({ url, type: opts.headers["Content-Type"], body: opts.body, credentials: opts.credentials }); return { status: answer }; };
const timer = (fn, ms) => { now += ms || 0; return setTimeout(fn, 0); };
new Function("document", "window", "fetch", "Date", "btoa", "atob", "TextEncoder", "setTimeout", src)(document, window, fetch, FakeDate, btoa, atob, TextEncoder, timer);
(async () => {
  now += fields.wait_ms === undefined ? 10000 : fields.wait_ms;
  for (const t of fields.tries || [{}]) {     // "tries": [{set: {field: value}, status}], one press of Send each
    for (const k in t.set || {}) form.elements[k].value = t.set[k];
    answer = t.status || 204;
    await submit({ preventDefault() {} });
  }
  process.stdout.write(JSON.stringify({ shown: !form.hidden, msg: els["inq-msg"].textContent, posted, busy,
    after: { text: button.textContent, aria: button.attrs["aria-busy"] || null, disabled: button.disabled },
    cleared: !!form.cleared }));
})();

