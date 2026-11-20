(() => {
if (document.fonts) document.fonts.load('24px "Material Symbols Rounded"').then(() => document.documentElement.classList.add('fonts-ready'));
else document.documentElement.classList.add('fonts-ready');
function sortTable(th){
  const table = th.closest('table'), idx = [...th.parentNode.children].indexOf(th);
  const desc = th.classList.contains('sorted') && !th.classList.contains('desc');
  table.querySelectorAll('th').forEach(h => h.classList.remove('sorted','desc'));
  th.classList.add('sorted'); if (desc) th.classList.add('desc');
  const body = table.tBodies[0], rows = [...body.rows];
  const val = r => { const c = r.cells[idx]; const raw = c.dataset.v ?? c.innerText.trim();
    const n = Number(raw.replace(/[$,]/g, '')); return raw !== '' && !isNaN(n) ? n : raw.toLowerCase(); };
  rows.sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (desc ? -1 : 1); });
  rows.forEach(r => body.appendChild(r));
}
document.querySelectorAll('table.data th').forEach(th => th.addEventListener('click', () => sortTable(th)));
document.querySelectorAll('tr.rowlink').forEach(tr => tr.addEventListener('click', e => {
  if (!e.target.closest('a,button,input,select,form')) location = tr.dataset.href; }));
document.documentElement.style.scrollBehavior = 'smooth';
(() => {
  const tip = document.createElement('div'); tip.id = 'tipbox'; document.body.appendChild(tip);
  let cur = null;
  const place = (x, y) => {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y + pad;
    if (left + w > innerWidth - 8) left = Math.max(8, x - pad - w);
    if (top + h > innerHeight - 8) top = Math.max(8, y - pad - h);
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  };
  const show = (el, x, y) => { cur = el; tip.textContent = el.dataset.tip; tip.classList.toggle('lines', 'tipLines' in el.dataset); tip.style.display = 'block'; place(x, y); };
  const hide = () => { cur = null; tip.style.display = 'none'; };
  if (matchMedia('(hover: hover)').matches) {
    document.addEventListener('mouseover', e => { const t = e.target.closest('[data-tip]'); if (t && t.dataset.tip) show(t, e.clientX, e.clientY); else if (cur) hide(); });
    document.addEventListener('mousemove', e => { if (cur) place(e.clientX, e.clientY); });
    document.addEventListener('scroll', hide, true);
  } else {
    const lineHost = el => el.closest('.journey') ? null : el.closest('li, .ui-fact, .ui-form-row, .ui-chips, p');
    let line = null;
    const dropLine = () => { if (line) { line.remove(); line = null; } };
    document.addEventListener('click', e => {
      const t = e.target.closest('[data-tip]');
      const ok = t && t.dataset.tip && !e.target.closest('a,button,input,label,select,textarea');
      const host = ok && lineHost(t);
      if (host) {
        const same = line && line.dataset.for === t.dataset.tip;
        dropLine(); hide();
        if (same) return;
        line = document.createElement(host.matches('li') ? 'li' : 'div'); line.className = 'ui-tipline'; line.setAttribute('role', 'note');
        line.dataset.for = t.dataset.tip; line.textContent = t.dataset.tip; host.after(line);
        return;
      }
      dropLine();
      if (ok && t !== cur) show(t, e.clientX, e.clientY); else hide();
    });
  }
  const at = el => { const r = el.getBoundingClientRect(); show(el, r.left, r.bottom); };
  const keyed = el => el && el.closest && el.closest('[data-tip][tabindex]');
  document.addEventListener('focusin', e => { const t = keyed(e.target); if (t && t.dataset.tip && t.matches(':focus-visible')) at(t); });
  document.addEventListener('focusout', e => { if (cur && keyed(e.target) === cur) hide(); });
  document.addEventListener('keydown', e => {
    const t = keyed(e.target);
    if (e.key === 'Escape' && cur) { hide(); return; }
    if (!t || t !== e.target || !t.dataset.tip || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    if (cur === t) hide(); else at(t);
  });
})();

const _waits = new WeakMap();
document.addEventListener('change', e => {
  const el = e.target.closest && e.target.closest('[data-autosubmit]'); if (!el || !el.form) return;
  const f = el.form, go = () => { if (f.requestSubmit) f.requestSubmit(); else f.submit(); };
  clearTimeout(_waits.get(f)); _pending.delete(f);
  if (el.type === 'number') { _waits.set(f, setTimeout(() => { _pending.delete(f); go(); }, 700)); _pending.set(f, go); } else go();
});
const _pending = new Map();
document.addEventListener('pointerdown', e => {
  if (!_pending.size || !e.target.closest('button, a, input[type=submit]')) return;
  for (const [f, go] of [..._pending]) { if (e.target.closest('form') === f) continue; clearTimeout(_waits.get(f)); _pending.delete(f); go(); }
}, true);
document.addEventListener('submit', async e => {
  const f = e.target, box = f.closest && f.closest('[data-inplace]');
  if (!box || e.defaultPrevented || f.target === '_blank' || (f.getAttribute('method') || '').toLowerCase() !== 'post') return;
  e.preventDefault();
  const body = new FormData(f);
  if (e.submitter && e.submitter.name) body.append(e.submitter.name, e.submitter.value);
  box.classList.add('saving');
  try {
    const r = await fetch(f.action, { method: 'POST', body });
    if (!r.ok) throw new Error(r.status);
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const fresh = doc.getElementById(box.id); if (!fresh) throw new Error('part');
    const open_ = [...box.querySelectorAll('details')].map(d => d.open);      // keep folded parts as they were
    fresh.querySelectorAll('details').forEach((d, i) => { if (open_[i]) d.open = true; });
    box.replaceWith(fresh); wire(fresh);
    document.querySelectorAll('[data-refresh]').forEach(el => { const n = doc.getElementById(el.id); if (n) { el.replaceWith(n); wire(n); } });
    const act = f.getAttribute('action'), nf = [...fresh.querySelectorAll('form')].find(x => x.getAttribute('action') === act);
    const tgt = nf && (nf.querySelector('[data-focus-after]') || nf.querySelector('input:not([type=hidden]):not([disabled]), select, textarea'));
    if (tgt) tgt.focus(); else { if (!fresh.hasAttribute('tabindex')) fresh.setAttribute('tabindex', '-1'); fresh.focus(); }
    const note = doc.querySelector('main > [role=status]'), here = document.querySelector('main > [role=status]');
    if (here) here.remove();
    if (note) document.querySelector('main').prepend(note);
  } catch (_) {
    location.reload();
  }
});
function wire(root) {
root.querySelectorAll('[data-ui-map]:not([data-ui-map-done])').forEach(el => {
  el.dataset.uiMapDone = '1';
  const q = encodeURIComponent(el.dataset.uiMap), ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const href = ios ? 'maps://?q=' + q : /Android/.test(ua) ? 'geo:0,0?q=' + q : '';
  if (href) { const a = document.createElement('a'); a.className = 'ui-go'; a.href = href; a.textContent = el.dataset.uiMap + ' \u203a'; el.replaceChildren(a); return; }
  if (!el.id) el.id = 'place-' + Math.random().toString(36).slice(2, 8);
  const b = document.createElement('button'); b.type = 'button'; b.className = 'ui-btn ui-btn-quiet ui-btn-small';
  b.dataset.copy = '#' + el.id; b.textContent = 'Copy address'; el.after(' ', b);
});
root.querySelectorAll('input[data-avail]').forEach(inp => {
  const form = inp.form, time = form && form.querySelector('input[name=start_time]');
  const out = document.createElement('div'); out.className = 'avail'; (inp.closest('.f2,.f3,.row') || inp).after(out);
  const check = async () => {
    if (!inp.value) { out.textContent = ''; return; }
    const u = '/availability?d=' + inp.value + '&t=' + (time ? time.value : '') + '&sid=' + inp.dataset.avail + '&type=' + (inp.dataset.type || '');
    try { const r = await (await fetch(u)).json();
      out.className = 'avail' + (r.clashes.length ? ' busy' : '');
      out.textContent = r.clashes.length ? 'Warning: already booked ' + r.day + ' - ' + r.clashes.join('; ') : 'Nothing else booked ' + r.day;
    } catch (e) {}
  };
  inp.addEventListener('change', check); time && time.addEventListener('change', check); if (inp.value) check();
});
root.querySelectorAll('[data-show-if]').forEach(el => {
  const [name, val] = el.dataset.showIf.split('='), form = el.form || el.closest('form');   // a field, or a block of them
  if (!form) return;
  const vals = val.split('|');                                     // one choice, or any of several: "tax_mode=flat|regional"
  const sync = () => { const r = form.querySelector(`[name="${name}"]:checked`); el.hidden = !(r && vals.includes(r.value)); };
  form.addEventListener('change', sync); sync(); });
root.querySelectorAll('[data-after-refund]').forEach(box => {
  const form = box.closest('form'); if (!form) return;
  const paid = parseFloat(box.dataset.paid) || 0, amt = form.querySelector('input[name=amount]');
  let byHand = false;
  box.addEventListener('change', () => { byHand = true; });
  const sync = () => {
    const k = form.querySelector('[name=kind]:checked'); box.hidden = !(k && k.value === 'refund');
    const a = amt ? parseFloat(amt.value) : NaN;
    if (byHand || isNaN(a)) return;
    const r = box.querySelector(`input[value="${paid - a > 0.005 ? 'release' : 'hold'}"]`); if (r) r.checked = true; };
  form.addEventListener('change', sync); amt && amt.addEventListener('input', sync); sync(); });
root.querySelectorAll('select[data-show-custom]').forEach(sel => {
  const box = sel.form && sel.form.querySelector('.qcustom'); if (!box) return;
  const sync = () => { const on = sel.value === 'custom'; box.hidden = !on;
    box.querySelectorAll('input[name=label],input[name=amount]').forEach(i => i.required = on); };
  sel.addEventListener('change', sync); sync(); });
root.querySelectorAll('.intake-note[data-need]').forEach(note => {
  const f = note.closest('form'), need = +note.dataset.need, base = note.textContent;
  const sync = () => { const n = [...f.querySelectorAll('input:not([type=hidden])')].filter(i => i.value.trim()).length;
    note.textContent = base + ' ' + n + ' answered' + (n >= need ? ': enough to tick it off.' : '.'); };
  f.addEventListener('input', sync); sync(); });
root.querySelectorAll('input[data-age]').forEach(age => {
  const f = age.form; if (!f) return;
  const box = f.elements[age.dataset.minor], ap = f.elements[age.dataset.approx], maj = +age.dataset.majority || 18;
  if (!box) return;
  const fill = () => { const n = parseInt(age.value, 10); if (isNaN(n)) return;
    if (ap && ap.checked && Math.abs(n - maj) <= 2) return; box.checked = n < maj; };
  age.addEventListener('change', fill); ap && ap.addEventListener('change', fill); });
root.querySelectorAll('[data-poll-up]:not([data-poll-up-done])').forEach(el => {
  el.dataset.pollUpDone = '1';
  const t = setInterval(async () => {
    if (!el.isConnected) { clearInterval(t); return; }
    try { const r = await (await fetch(el.dataset.pollUp, { credentials: 'same-origin' })).json();
      if (r.upload !== 'uploading') { clearInterval(t); refreshParts(); return; }
      const lab = el.dataset.label && document.getElementById(el.dataset.label);   // "Uploading: file N of M" (backlog 236)
      if (lab && r.up_label && lab.textContent !== r.up_label) lab.textContent = r.up_label; } catch (_) {} }, 2000); });
}
wire(document);

document.addEventListener('submit', e => {
  const f = e.target; if (f.dataset && f.dataset.confirm && !('noConfirm' in f.dataset) && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
  if (f.dataset && f.dataset.confirm && f.dataset.confirmSets && !f.querySelector(`input[name="${f.dataset.confirmSets}"]`)) {
    const i = document.createElement('input'); i.type = 'hidden'; i.name = f.dataset.confirmSets; i.value = '1'; f.appendChild(i); } }, true);
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-confirm], input[data-confirm], a[data-confirm]');
  if (b && !confirm(b.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); return; }
  const o = e.target.closest('[data-did-output][data-sends]');
  if (o && document.querySelector('.draft') && !document.body.dataset.sampleOk) {
    if (!confirm('This still prints SAMPLE: the wording has not been reviewed yet. Send it anyway?')) {
      e.preventDefault(); e.stopPropagation(); const r = document.querySelector('.bar a.review'); if (r) r.focus(); return; }
    document.body.dataset.sampleOk = '1'; }
  const p = e.target.closest('[data-print]'); if (p) { e.preventDefault(); window.print(); return; }
  const c = e.target.closest('[data-copy]');
  if (c) { const src = document.querySelector(c.dataset.copy), was = c.textContent;
    if (src && navigator.clipboard) navigator.clipboard.writeText(src.textContent.trim())
      .then(() => { c.textContent = 'Copied'; setTimeout(() => c.textContent = was, 1500); }, () => {});
    return; }
  const t = e.target.closest('[data-toggle-open]');
  if (t) { const box = t.closest(t.dataset.toggleOpen || 'li'); box.classList.toggle('open');
    const first = box.classList.contains('open') && box.querySelector('.act-form input:not([type=hidden]):not([type=radio]), .act-form textarea, .act-form select');
    if (first) first.focus(); }
});
if (document.querySelector('body > .bar')) {
  document.addEventListener('submit', e => { if ((e.target.getAttribute('method') || '').toLowerCase() === 'post') {
    try { sessionStorage.setItem('la-posted', '1'); } catch (_) {} } });
  try { if (sessionStorage.getItem('la-posted')) { sessionStorage.removeItem('la-posted'); localStorage.setItem('la-changed', String(Date.now())); } } catch (_) {}
}
async function refreshParts() {
  const parts = document.querySelectorAll('[data-refresh][id], [data-inplace][id], [data-ui-part][id]'); if (!parts.length) return;
  try {
    const doc = new DOMParser().parseFromString(await (await fetch(location.href, { credentials: 'same-origin' })).text(), 'text/html');
    const main = document.querySelector('main'), nmain = doc.querySelector('main');
    if (main && nmain && document.querySelector('[data-ui-part]') && uiLayout(nmain) !== uiLayout(main)) {
      if (uiDirty(main)) return;                                  // never lose typing: it waits for the next change
      const y = scrollY; uiKeepOpen(main, nmain); main.replaceWith(nmain); wire(nmain); scrollTo({ top: y, behavior: 'instant' });
      return;
    }
    parts.forEach(el => { const n = doc.getElementById(el.id); if (n && !el.contains(document.activeElement) && !(el.matches('[data-ui-part]') && uiDirty(el))) {
      const open_ = [...el.querySelectorAll('details')].map(d => d.open);
      n.querySelectorAll('details').forEach((d, i) => { if (open_[i]) d.open = true; });
      if (el.matches('[data-ui-part]')) uiKeepOpen(el, n);
      el.replaceWith(n); wire(n); } });
  } catch (_) {}
}
document.addEventListener('click', e => { const x = e.target.closest('[data-dismiss]'); if (x) x.closest('.toast').remove(); });
setTimeout(() => document.querySelectorAll('.toast').forEach(t => t.style.opacity = '.92'), 0);
window.laRefresh = refreshParts;
window.addEventListener('storage', e => { if (e.key === 'la-changed') refreshParts(); });
document.addEventListener('click', e => {
  const c = e.target.closest('[data-close]'); if (!c) return;
  e.preventDefault(); window.close(); setTimeout(() => { location.href = c.href; }, 200); });
document.addEventListener('click', e => {
  if (e.target.closest('[data-did-output]')) document.querySelectorAll('.marksent').forEach(f => f.classList.add('asking')); });
(() => { const then = document.body && document.body.dataset.then; if (!then) return;
  setTimeout(() => {
    if (then === 'print') { window.print(); return; }
    const a = document.querySelector(then === 'download' ? '.bar a[download]' : '.bar a[data-sends][href^="mailto:"]');
    if (a) a.click(); }, 300); })();
document.addEventListener('submit', e => {
  const f = e.target, bal = f.dataset && f.dataset.balance; if (bal === undefined || e.defaultPrevented) return;
  const kind = (f.querySelector('[name=kind]:checked') || f.querySelector('[name=kind]') || {}).value;
  const amt = parseFloat(((f.querySelector('[name=amount]') || {}).value || '0').replace(/[$,\s]/g, ''));
  if (kind === 'refund' || !(amt > parseFloat(bal) + 0.005)) return;
  const over = (amt - Math.max(parseFloat(bal), 0)).toFixed(2), on = f.dataset.creditOn || 'session';
  if (!confirm('That is $' + over + ' more than the balance. Record it anyway? The extra shows as a credit on the ' + on + '.'))
    e.preventDefault(); });
document.addEventListener('submit', e => {
  const f = e.target; if ((f.getAttribute('method') || '').toLowerCase() !== 'post' || f.target === '_blank') return;
  try { sessionStorage.setItem('scrollAt', JSON.stringify({p: location.pathname, y: scrollY, t: Date.now()})); } catch (_) {} });
try { const s = JSON.parse(sessionStorage.getItem('scrollAt') || 'null'); sessionStorage.removeItem('scrollAt');
  if (s && s.p === location.pathname && Date.now() - s.t < 15 * 1000 && !location.hash) {
    scrollTo(0, s.y);
    const show = () => { const note = document.querySelector('main .ok-msg, main .err');
      if (note && note.getBoundingClientRect().top < 8)
        scrollTo({ top: Math.max(0, scrollY + note.getBoundingClientRect().top - 80), behavior: 'instant' }); };
    show(); addEventListener('load', () => setTimeout(show, 0), { once: true });
  } } catch (_) {}
const keptKinds = el => el.name && !['hidden', 'password', 'file', 'submit', 'button', 'reset'].includes(el.type);
document.addEventListener('submit', e => {
  const f = e.target; if ((f.getAttribute('method') || '').toLowerCase() !== 'post' || f.target === '_blank') return;
  try { sessionStorage.setItem('la-form', JSON.stringify({a: new URL(f.action, location.href).pathname, t: Date.now(),
    v: [...f.elements].filter(keptKinds).map(el => [el.name, el.value, el.type === 'radio' || el.type === 'checkbox' ? el.checked : null])})); } catch (_) {} });
try { const k = JSON.parse(sessionStorage.getItem('la-form') || 'null'); sessionStorage.removeItem('la-form');
  const at = (document.querySelector('.toast[data-refill]') || {dataset: {}}).dataset.refill;
  if (k && at && k.a === at && Date.now() - k.t < 60 * 1000) {
    const f = [...document.querySelectorAll('form')].find(f_ => new URL(f_.action, location.href).pathname === at
      && k.v.every(([n]) => f_.elements[n]));
    if (f) {
      k.v.forEach(([n, v, on]) => { const els = f.elements[n] instanceof RadioNodeList ? [...f.elements[n]] : [f.elements[n]];
        els.filter(keptKinds).forEach(el => { if (on === null) el.value = v; else if (el.value === v) el.checked = on; }); });
      for (let d = f.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
    } } } catch (_) {}
document.addEventListener('change', e => {
  const r = e.target.closest('[data-fill]'); if (r && r.form && r.form[r.dataset.fill]) r.form[r.dataset.fill].value = r.dataset.value; });
document.querySelectorAll('[data-filter-rows]').forEach(inp => inp.addEventListener('input', () => {
  const v = inp.value.toLowerCase();
  document.querySelectorAll(inp.dataset.filterRows).forEach(r => r.hidden = !!v && !r.textContent.toLowerCase().includes(v));
  if (inp.dataset.filterHeads) document.querySelectorAll(inp.dataset.filterHeads).forEach(h => {   // a heading hides with its rows
    let n = h.nextElementSibling, any = false;
    while (n && !n.matches(inp.dataset.filterHeads)) { if (!n.hidden) any = true; n = n.nextElementSibling; }
    h.hidden = !any; }); }));
document.querySelectorAll('[data-poll]').forEach(el => { const t = setInterval(async () => {
  try { const r = await (await fetch(el.dataset.poll)).json();
    if (r.state !== 'running') { clearInterval(t); location.reload(); return; }
    const bar = document.getElementById(el.dataset.bar), n = document.getElementById(el.dataset.count);
    if (bar) bar.style.width = (r.total ? 100 * r.done / r.total : 0) + '%';
    if (n) n.textContent = r.done + ' / ' + r.total;
    const lab = el.dataset.label && document.getElementById(el.dataset.label);   // what the job is doing now, if it says
    if (lab && r.label && lab.textContent !== r.label) lab.textContent = r.label; } catch (e) {} }, 700); });
document.querySelectorAll('aside.jobs').forEach(box => {
  const fit = () => document.documentElement.style.setProperty('--jobs-h', box.offsetHeight + 'px');
  fit();
  if (window.ResizeObserver) new ResizeObserver(fit).observe(box);
  box.querySelectorAll('.job.done').forEach(j => setTimeout(() => {
    j.remove();
    if (!box.querySelector('.job')) { box.remove(); document.documentElement.style.removeProperty('--jobs-h'); }
  }, 30000)); });
document.querySelectorAll('[data-go-after]').forEach(el => setTimeout(() => location.href = el.dataset.href, +el.dataset.goAfter));

const uiToggle = (id, on) => {
  const box = document.getElementById(id); if (!box) return;
  on = on === undefined ? !box.classList.contains('ui-open') : on;
  box.classList.toggle('ui-open', on);
  document.querySelectorAll('[data-ui-open]').forEach(b => { if (b.dataset.uiOpen === id) b.setAttribute('aria-expanded', String(on)); });
  if (!on) return;
  const fld = box.querySelector('input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea, select');
  if (fld) fld.focus({ preventScroll: true });
  const r = box.getBoundingClientRect(); if (r.bottom > innerHeight || r.top < 0) box.scrollIntoView({ block: 'nearest' });
};
document.addEventListener('click', e => {
  const o = e.target.closest('[data-ui-open]'); if (!o) return; e.preventDefault(); uiToggle(o.dataset.uiOpen); });
const uiOpenTo = () => {
  if (!location.hash || location.hash.length < 2) return;
  const el = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (!el) return;
  for (let d = el.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
  if (el.matches('.ui-inline')) uiToggle(el.id, true); else el.scrollIntoView();
};
addEventListener('hashchange', uiOpenTo); uiOpenTo();

const uiSig = f => (f.getAttribute('action') || '') + '|' + [...f.querySelectorAll('input[type=hidden]')]
  .filter(i => !['once', 'seen', 'back'].includes(i.name)).map(i => i.name + '=' + i.value).join('&');
const uiLayout = root => { const p = root.querySelector('[data-ui-layout]');
  return (p ? p.dataset.uiLayout : '') + ':' + [...root.querySelectorAll('[data-ui-part][id]')].map(x => x.id).join(','); };
const uiDirty = el => [...el.querySelectorAll('input, textarea')].some(i => i.type !== 'hidden' && i.type !== 'file' &&
  ((i.type === 'checkbox' || i.type === 'radio') ? i.checked !== i.defaultChecked : i.value !== i.defaultValue));
const uiKeepOpen = (from, to, except) => {           // folds, menus and inline forms stay as they were
  if (from.matches('details') && to.matches('details')) to.open = from.open;
  const ds = [...from.querySelectorAll('details')], dn = [...to.querySelectorAll('details')];
  ds.forEach((d, i) => { const n = d.id ? to.querySelector('#' + CSS.escape(d.id)) : dn[i]; if (n && !d.matches('.ui-rowmore, .ui-more')) n.open = d.open; });
  from.querySelectorAll('.ui-inline.ui-open[id]').forEach(b => { if (b.id !== except) { const n = to.querySelector('#' + CSS.escape(b.id)); if (n) n.classList.add('ui-open'); } });
};
const uiLive = msg => { const l = document.getElementById('ui-live'); if (l) { l.textContent = ''; setTimeout(() => { l.textContent = msg; }, 60); } };
const uiNote = (where, msg, bad, after) => {
  document.querySelectorAll('.ui-note[data-ui-note]').forEach(n => n.remove());
  const n = document.createElement('span'); n.className = 'ui-note' + (bad ? ' ui-note-error' : ' ui-note-ok'); n.dataset.uiNote = '';
  n.setAttribute('role', bad ? 'alert' : 'status');
  n.textContent = msg; if (after) where.after(n); else where.appendChild(n);
  uiLive(msg);
  if (!bad) setTimeout(() => { n.classList.add('ui-fade'); setTimeout(() => n.remove(), 700); }, 4000);
  return n;
};
const UI_PAST = { Add: 'Added', Apply: 'Applied', Approve: 'Approved', Book: 'Booked', Close: 'Closed', Collect: 'Collected',
  Give: 'Gave', Log: 'Logged', Mark: 'Marked', Move: 'Moved', Record: 'Recorded', Remove: 'Removed', Reopen: 'Reopened',
  Reply: 'Replied', Save: 'Saved', Send: 'Sent', Set: 'Set', Undo: 'Undid', Follow: 'Followed', Hide: 'Hid', Fill: 'Filled' };
const uiSaid = (btn, what) => {
  const label = ((btn && (btn.getAttribute('aria-label') || btn.textContent)) || '').trim().replace(/\s+/g, ' ');
  const m = label.match(/^(\S+)(.*)$/);
  let said = 'Saved';
  if (m && /^Do not\b/.test(label)) said = 'Did not' + label.slice(6);
  else if (m && UI_PAST[m[1].replace(/:$/, '')]) said = UI_PAST[m[1].replace(/:$/, '')] + (m[1].endsWith(':') ? ':' : '') + m[2].replace(/^ it$/, '');
  else if (label && /\bdone$/i.test(label)) said = label;
  else if (label) said = 'Saved: ' + label;
  return what && !said.toLowerCase().includes(what.toLowerCase()) ? said + ': ' + what : said;
};
document.addEventListener('submit', async e => {
  const f = e.target;
  if (!f.matches || !f.matches('form[data-ui-inplace]') || e.defaultPrevented || f.target === '_blank'
      || (f.getAttribute('method') || '').toLowerCase() !== 'post' || !window.fetch || !window.DOMParser) return;
  e.preventDefault();
  if (f.dataset.uiBusy) return;
  const btn = e.submitter || f.querySelector('button:not([type=button]), [type=submit]');
  const body = new FormData(f); if (e.submitter && e.submitter.name) body.append(e.submitter.name, e.submitter.value);
  const part = f.closest('[data-ui-part]'), anchorEl = f.closest('[id]'), anchorId = anchorEl && anchorEl.id;
  const top0 = anchorEl ? anchorEl.getBoundingClientRect().top : 0, sig = uiSig(f);
  const prevId = anchorEl && anchorEl.previousElementSibling && anchorEl.previousElementSibling.id;
  const inInline = f.closest('.ui-inline'), boxId = inInline && inInline.id;
  const rowEl = anchorEl && (anchorEl.matches('.ui-todo, .ui-tline') ? anchorEl
    : (inInline && inInline.previousElementSibling && inInline.previousElementSibling.matches('.ui-todo, .ui-tline') ? inInline.previousElementSibling : null));
  const whatEl = rowEl && rowEl.querySelector('.ui-todo-what, .ui-tline-what');
  const what = whatEl && whatEl.firstChild ? whatEl.firstChild.textContent.trim() : '';
  try { sessionStorage.removeItem('scrollAt'); sessionStorage.removeItem('la-form'); } catch (_) {}
  f.dataset.uiBusy = '1'; f.classList.add('ui-saving'); if (btn) btn.disabled = true;
  let r, text;
  try { r = await fetch(f.action, { method: 'POST', body, credentials: 'same-origin' }); text = await r.text(); }
  catch (_) {
    delete f.dataset.uiBusy; f.classList.remove('ui-saving'); if (btn) btn.disabled = false;
    uiNote(btn || f, 'No answer from the app. Reload the page to see whether it was saved.', true, !!btn); return;
  }
  delete f.dataset.uiBusy; f.classList.remove('ui-saving'); if (btn) btn.disabled = false;
  const html = /html/.test(r.headers.get('content-type') || '');
  const doc = html ? new DOMParser().parseFromString(text, 'text/html') : null;
  const main = document.querySelector('main'), nmain = doc && doc.querySelector('main');
  if (r.ok && r.redirected && new URL(r.url).pathname !== location.pathname) { location.assign(r.url); return; }   // it lives elsewhere now
  const toast = nmain && nmain.querySelector(':scope > .toast[role=status]');
  let msg = '';
  if (toast) { const c = toast.cloneNode(true); c.querySelectorAll('button').forEach(b => b.remove()); msg = c.textContent.trim(); toast.remove(); }
  const refused = !r.ok || !!(toast && toast.hasAttribute('data-refused'));
  const clash = !!(doc && doc.getElementById('edit-clash'));      // changed in another tab: the latest version, with what was typed
  if (!r.ok && !msg) msg = (nmain ? '' : (doc && doc.body ? doc.body.textContent : text)).trim().slice(0, 240) || ('Not saved (the app answered ' + r.status + ').');
  if (nmain && document.querySelector('[data-ui-part]')) {
    if (uiLayout(nmain) !== uiLayout(main) && r.ok) {           // the stage moved: the page shows other things now
      const y = scrollY; uiKeepOpen(main, nmain, boxId); main.replaceWith(nmain); wire(nmain); scrollTo({ top: y, behavior: 'instant' });
    } else {
      document.querySelectorAll('[data-ui-part][id], [data-refresh][id]').forEach(p => {
        const n = doc.getElementById(p.id); if (!n) return;
        if (p === part ? (refused && !clash) : (p.contains(document.activeElement) || uiDirty(p))) return;   // never lose typing
        uiKeepOpen(p, n, boxId); p.replaceWith(n); wire(n);
      });
    }
  } else if (!nmain && r.ok) { location.reload(); return; }
  if (doc && r.ok) {                  // the nav's inbox count follows what the action changed (Add, Spam: web host test #12)
    const ob = document.querySelector('a.ui-inbox'), nb = doc.querySelector('a.ui-inbox');
    if (ob && nb) ob.replaceWith(nb); else if (ob && doc.querySelector('nav.sections')) ob.remove();
  }
  const scope = (anchorId && document.getElementById(anchorId)) || (part && document.getElementById(part.id)) || document;
  const nf = [scope, document].map(sc => [...sc.querySelectorAll('form[data-ui-inplace]')].find(x => uiSig(x) === sig)).find(Boolean);
  const text_ = msg || (refused ? 'Not saved.' : uiSaid(btn, what));
  let keep;
  if (nf && !(refused && nf !== f)) { const b = nf.querySelector('button:not([type=button]), [type=submit]'); keep = uiNote(b || nf, text_, refused, !!b); if (b && document.activeElement === document.body) b.focus({ preventScroll: true }); }
  else if (anchorId && document.getElementById(anchorId) && !document.getElementById(anchorId).matches('.ui-inline')) {
    const el = document.getElementById(anchorId);       // beside where the button was: its action slot, never a stray grid cell
    keep = uiNote(el.querySelector('.ui-todo-act, .ui-tline-act, .ui-btnrow') || el, text_, refused); }
  else {
    const prev = prevId && document.getElementById(prevId), fresh = part && document.getElementById(part.id);
    const host = prev ? prev.parentElement : (fresh && (fresh.querySelector('ul.ui-todos') || fresh.querySelector('.ui-fold-body') || fresh)) || main;
    const fl = document.createElement(host.matches('ul, ol') ? 'li' : 'div'); fl.className = 'ui-flash';
    if (prev) prev.after(fl); else host.prepend(fl);
    keep = uiNote(fl, text_, refused);
    if (!refused) setTimeout(() => fl.remove(), 4700);      // gone with its note, never an empty line left on the row
  }
  const ref = (anchorId && document.getElementById(anchorId)) || keep;
  if (ref && anchorEl) scrollBy({ top: ref.getBoundingClientRect().top - top0, behavior: 'instant' });   // the page does not move under the finger
});
})();

document.addEventListener('submit', async e => {
  const f = e.target, box = f.closest && f.closest('[data-save-together]');
  if (!box || f.dataset.together || (f.getAttribute('method') || '').toLowerCase() !== 'post') return;
  const typed = o => [...o.elements].some(el => el.name && ['text', 'search', 'email', 'tel', 'url', 'number', 'textarea'].includes(el.type)
    && el.value !== el.defaultValue);
  const others = [...box.querySelectorAll('form')].filter(o => o !== f && (o.getAttribute('method') || '').toLowerCase() === 'post' && typed(o));
  if (!others.length) return;
  e.preventDefault(); e.stopImmediatePropagation();
  for (const o of others) {
    const body = new FormData(o), btn = o.querySelector('button[name]');
    if (btn) body.append(btn.name, btn.value);
    try { await fetch(o.action, { method: 'POST', body, credentials: 'same-origin' });
      [...o.elements].forEach(el => { if ('defaultValue' in el) el.defaultValue = el.value; }); } catch (_) {}
  }
  f.dataset.together = '1';
  f.requestSubmit ? f.requestSubmit(e.submitter) : f.submit();
}, true);
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const open = document.activeElement && document.activeElement.closest && document.activeElement.closest('details.closemenu[open], details.ui-rowmore[open], details.ui-more[open]');   // a menu, never a fold
  if (open && open.querySelector('summary')) { open.open = false; open.querySelector('summary').focus(); }
});

document.addEventListener('submit', e => {
  const f = e.target; if (!f.matches || !f.matches('form[data-ui-mail]') || e.defaultPrevented) return;
  const m = f.querySelector('input[name=mail]'); if (m) m.remove();
  const a = document.createElement('a'); a.href = f.dataset.uiMail; a.hidden = true;
  document.body.appendChild(a); a.click(); a.remove();
}, true);

(() => {
  const KEY = 'la-keep:' + location.pathname + location.search;
  const posts = () => [...document.querySelectorAll('form')].filter(f => (f.getAttribute('method') || '').toLowerCase() === 'post');
  const fid = f => { const a = new URL(f.action, location.href).pathname;
    return a + '#' + posts().filter(o => new URL(o.action, location.href).pathname === a).indexOf(f); };
  const kinds = el => !['hidden', 'password', 'file', 'submit', 'button', 'reset', 'image'].includes(el.type);
  const keepable = el => el.name && kinds(el) && !el.disabled && !el.closest('[data-no-keep]')
    && !['off', 'one-time-code', 'new-password', 'current-password'].includes((el.getAttribute('autocomplete') || '').toLowerCase());
  const base = new WeakMap(), now_ = el => el.type === 'checkbox' || el.type === 'radio' ? el.checked
    : el.tagName === 'SELECT' ? [...el.options].map(o => o.selected).join() : el.value;
  const dflt = el => el.type === 'checkbox' || el.type === 'radio' ? el.defaultChecked
    : el.tagName === 'SELECT' ? [...el.options].map(o => o.defaultSelected).join() : el.defaultValue;
  const touched = new WeakSet();
  const touch = el => { const f = el.form, g = f && el.name && f.elements[el.name];
    (g instanceof RadioNodeList ? [...g] : [el]).forEach(x => touched.add(x)); };
  const changed = el => touched.has(el) && now_(el) !== (base.has(el) ? base.get(el) : dflt(el));
  const read = () => { try { return JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch (_) { return {}; } };
  const write = s => { try { Object.keys(s).length ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY); } catch (_) {} };
  const snap = f => [...f.elements].filter(el => keepable(el) && changed(el))
    .map(el => [el.name, el.type === 'radio' || el.type === 'checkbox' ? el.value : null, el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value]);
  const save = f => { if (!f || !f.matches || !posts().includes(f)) return; const s = read(), v = snap(f);
    if (v.length) s[fid(f)] = v; else delete s[fid(f)]; write(s); };
  document.addEventListener('input', e => { if (e.isTrusted && e.target.form) touch(e.target); save(e.target.form); }, true);
  document.addEventListener('change', e => { if (e.isTrusted && e.target.form) touch(e.target); save(e.target.form); }, true);
  let leaving = false;
  document.addEventListener('submit', e => { const f = e.target, s = read(); if (posts().includes(f)) { delete s[fid(f)]; write(s); if (!f.closest('[data-inplace]')) leaving = true; } });
  window.addEventListener('beforeunload', e => {
    if (leaving || window.laSignedOut) return;
    const dirty = posts().flatMap(f => [...f.elements].filter(el => keepable(el) && changed(el)));
    if (!dirty.length) return;
    dirty.forEach(el => { const at = el.closest('label, .seg') || el;
      if (at.nextElementSibling && at.nextElementSibling.matches('.ui-unsaved')) return;
      const n = document.createElement('small'); n.className = 'ui-note ui-note-error ui-unsaved'; n.textContent = 'Not saved yet'; at.after(n); });
    e.preventDefault(); e.returnValue = ''; });
  posts().forEach(f => [...f.elements].forEach(el => base.set(el, now_(el))));
  const s = read(); let first = null;
  posts().forEach(f => { const v = s[fid(f)]; if (!v) return;
    v.forEach(([n, val, now]) => { const els = f.elements[n] instanceof RadioNodeList ? [...f.elements[n]] : [f.elements[n]];
      els.filter(el => el && keepable(el)).forEach(el => {
        const was = now_(el);
        touch(el);                     // put back from what was typed before: it still counts as unsaved typing
        if (el.type === 'checkbox' || el.type === 'radio') { if (el.value === val) el.checked = now; } else el.value = now;
        if (now_(el) !== was) { el.dispatchEvent(new Event('input', {bubbles: true})); if (!first) first = f; } }); }); });
  if (first) { const n = document.createElement('p'); n.className = 'ui-note'; n.setAttribute('role', 'status'); n.dataset.kept = '1';
    n.textContent = 'What you typed before the page reloaded is back. It is not saved yet.';
    first.parentNode.insertBefore(n, first); }
})();

(() => {
  const nav = document.querySelector('.settabs[data-settings-moved]');
  if (!nav || !location.hash || location.hash.length < 2) return;
  const id = decodeURIComponent(location.hash.slice(1));
  if (document.getElementById(id)) return;
  let to = {}; try { to = JSON.parse(nav.dataset.settingsMoved); } catch (_) { return; }
  if (to[id] && to[id].split('#')[0] !== location.pathname + location.search) location.replace(to[id]);
})();
const uiFound = () => {
  if (!document.querySelector('.settabs') || !location.hash || location.hash.length < 2) return;
  const el = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (!el) return;
  if (el.matches('section, form, details, .ui-sec')) { el.classList.add('ui-found'); return; }
  const fld = el.matches('input, select, textarea') ? el : el.matches('label[for]') ? document.getElementById(el.htmlFor)
    : (el.closest('tr, label, div') || el).querySelector('input:not([type=hidden]), select, textarea');
  const box = (fld || el).closest('tr, label.row, label.choice, .two > div, .f2 > div, .row') || el;
  box.classList.remove('ui-found'); void box.offsetWidth; box.classList.add('ui-found');
  box.scrollIntoView({ block: 'center' });
  if (fld && !fld.disabled) fld.focus({ preventScroll: true });
};
addEventListener('hashchange', uiFound); uiFound();
(() => {
  const nav = document.querySelector('.settabs'), on = nav && nav.querySelector('a.on');
  if (!on || nav.scrollHeight <= nav.clientHeight + 2) return;
  const top = on.getBoundingClientRect().top - nav.getBoundingClientRect().top + nav.scrollTop, room = nav.clientHeight;
  if (top < nav.scrollTop || top + on.offsetHeight > nav.scrollTop + room) nav.scrollTop = Math.max(0, top - room / 3);
})();

(() => {
  const mw = document.querySelector('.mwrap'); if (!mw) return;
  const on_ = mw.querySelector('.mcell-on'); let cur = on_ ? on_.dataset.day : '';
  const open = iso => {
    cur = iso;
    mw.querySelectorAll('.mday').forEach(p => { p.hidden = p.dataset.day !== iso; });
    mw.querySelectorAll('.mcell[data-day]').forEach(c => { const on = c.dataset.day === iso;
      c.classList.toggle('mcell-on', on); c.setAttribute('aria-expanded', on ? 'true' : 'false'); });
    mw.classList.toggle('mwrap-open', !!iso);
    const u = new URL(location.href); u.hash = '';
    if (iso) u.searchParams.set('d', iso); else u.searchParams.delete('d');
    if (!u.searchParams.get('m')) u.searchParams.set('m', mw.dataset.month);
    history.replaceState(null, '', u);
    const p = iso && document.getElementById('day-' + iso);
    if (p && innerWidth < 900) p.scrollIntoView({ block: 'nearest' });
  };
  mw.addEventListener('click', e => {
    const c = e.target.closest('.mcell[data-day]'), x = e.target.closest('.mday-close');
    if (x) { e.preventDefault(); const was = cur; open(''); const c_ = mw.querySelector(`.mcell[data-day="${was}"]`); if (c_) c_.focus(); return; }
    if (!c || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault(); open(cur === c.dataset.day ? '' : c.dataset.day);
  });
  addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !cur) return;
    const was = cur; open(''); const c = mw.querySelector(`.mcell[data-day="${was}"]`); if (c) c.focus();
  });
})();

(() => {
  const box = document.querySelector('[data-idle-warn]'); if (!box) return;
  const limit = +box.dataset.idle || 0; if (!limit) return;
  const WARN = 120; let timer = null;
  const ask = async () => { try { const r = await fetch(box.dataset.idleLeft, { credentials: 'same-origin', headers: { 'X-LA-Background': '1' } });
    const j = await r.json(); return typeof j.left === 'number' ? j.left : null; } catch (_) { return 0; } };
  const out = () => { window.laSignedOut = true; location.reload(); };
  const plan = secs => { clearTimeout(timer); timer = setTimeout(check, Math.max(1, secs) * 1000); };
  const check = async () => {
    const left = await ask();
    if (left === null) return;
    if (left <= 0) return out();
    if (left <= WARN + 5) { box.hidden = false; box.dataset.at = String(Date.now() + left * 1000); plan(left + 1); }
    else { box.hidden = true; plan(left - WARN); }
  };
  plan(limit - WARN);
  document.addEventListener('submit', () => { box.hidden = true; plan(limit - WARN); }, true);
  box.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault(); e.stopPropagation();
    try { await fetch(e.target.action, { method: 'POST', credentials: 'same-origin', headers: { 'X-LA-Inplace': '1' } }); } catch (_) {}
    box.hidden = true; plan(limit - WARN);
  });
})();

document.addEventListener('submit', async e => {
  const f = e.target;
  if (!f.matches || !f.matches('form[data-doc-wording]') || e.defaultPrevented || !window.fetch || !window.DOMParser) return;
  e.preventDefault();
  if (f.dataset.busy) return;
  f.dataset.busy = '1';
  const btn = f.querySelector('button'); if (btn) btn.disabled = true;
  const y = scrollY;
  let r, text;
  try { r = await fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin' }); text = await r.text(); }
  catch (_) { r = null; }
  delete f.dataset.busy; if (btn) btn.disabled = false;
  const doc = r && r.ok && /html/.test(r.headers.get('content-type') || '') ? new DOMParser().parseFromString(text, 'text/html') : null;
  const page = document.querySelector('body > .page'), npage = doc && doc.querySelector('body > .page');
  if (!page || !npage || (r.redirected && new URL(r.url).pathname !== location.pathname)) {
    const n = document.createElement('span'); n.className = 'ui-note ui-note-error'; n.setAttribute('role', 'alert');
    n.textContent = r ? 'Not saved here. Reload the document to see whether it was saved.' : 'No answer from the app. Reload the document to see whether it was saved.';
    (btn || f).after(n); return;
  }
  const bar = document.querySelector('body > .bar'), nbar = doc.querySelector('body > .bar');
  if (bar && nbar) bar.replaceWith(nbar);
  page.replaceWith(npage);
  delete document.body.dataset.sampleOk;
  scrollTo({ top: y, behavior: 'instant' });
  const b2 = document.querySelector('body > .bar');
  if (b2) { const n = document.createElement('span'); n.className = 'doc-saved'; n.setAttribute('role', 'status');
    n.textContent = 'Wording saved and approved. This document no longer prints SAMPLE.';
    const close = b2.querySelector('[data-close]'); if (close) close.before(n); else b2.append(n); }
  try { sessionStorage.setItem('la-posted', '1'); localStorage.setItem('la-changed', String(Date.now())); } catch (_) {}
});

document.addEventListener('submit', e => {
  const f = e.target; if (!f.matches || !f.matches('form[data-busy-says]') || e.defaultPrevented) return;
  if (f.dataset.busyNow) { e.preventDefault(); return; }
  f.dataset.busyNow = '1'; f.setAttribute('aria-busy', 'true'); f.classList.add('ui-saving');
  const b = e.submitter || f.querySelector('button'); if (b) { b.dataset.busyWas = b.textContent; b.textContent = f.dataset.busySays; }
});
addEventListener('pageshow', e => { if (!e.persisted) return;
  document.querySelectorAll('form[data-busy-now]').forEach(f => { delete f.dataset.busyNow; f.removeAttribute('aria-busy'); f.classList.remove('ui-saving');
    f.querySelectorAll('[data-busy-was]').forEach(b => { b.textContent = b.dataset.busyWas; delete b.dataset.busyWas; }); }); });

