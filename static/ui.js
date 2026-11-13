// Lumen Aureum page behavior. No inline handlers anywhere: pages mark elements with data- attributes and this
// file wires them, so the Content-Security-Policy can refuse inline script.
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
// One floating tooltip:
// it lives on <body>, so no card's overflow can cut it off. Mouse: follows the pointer. Phone: tap to show, tap away.
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
  const show = (el, x, y) => { cur = el; tip.textContent = el.dataset.tip; tip.style.display = 'block'; place(x, y); };
  const hide = () => { cur = null; tip.style.display = 'none'; };
  if (matchMedia('(hover: hover)').matches) {
    document.addEventListener('mouseover', e => { const t = e.target.closest('[data-tip]'); if (t && t.dataset.tip) show(t, e.clientX, e.clientY); else if (cur) hide(); });
    document.addEventListener('mousemove', e => { if (cur) place(e.clientX, e.clientY); });
    document.addEventListener('scroll', hide, true);
  } else {
    document.addEventListener('click', e => {
      const t = e.target.closest('[data-tip]');
      if (t && t !== cur && t.dataset.tip && !e.target.closest('a,button,input,label,select,textarea')) show(t, e.clientX, e.clientY); else hide();
    });
  }
})();

// data-autosubmit: a change saves the form. A number box waits until the clicking stops (0.7 s), so quick clicks on
// its arrows save once, with the last value.
const _waits = new WeakMap();
document.addEventListener('change', e => {
  const el = e.target.closest && e.target.closest('[data-autosubmit]'); if (!el || !el.form) return;
  const f = el.form, go = () => { if (f.requestSubmit) f.requestSubmit(); else f.submit(); };
  clearTimeout(_waits.get(f)); _pending.delete(f);
  if (el.type === 'number') { _waits.set(f, setTimeout(() => { _pending.delete(f); go(); }, 700)); _pending.set(f, go); } else go();
});
// A quantity still waiting to save is saved first when any other button or link is pressed (Send quote must never
// go out with the old quantity).
const _pending = new Map();
document.addEventListener('pointerdown', e => {
  if (!_pending.size || !e.target.closest('button, a, input[type=submit]')) return;
  for (const [f, go] of [..._pending]) { if (e.target.closest('form') === f) continue; clearTimeout(_waits.get(f)); _pending.delete(f); go(); }
}, true);
// data-inplace: a form inside this part saves without reloading the page; the part (and any [data-refresh] part)
// is replaced with its new state. If anything goes wrong, the form is sent the ordinary way.
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
    const note = doc.querySelector('main > [role=status]'), here = document.querySelector('main > [role=status]');
    if (here) here.remove();
    if (note) document.querySelector('main').prepend(note);
  } catch (_) {
    // Never send the form again on its own: the server may already have acted (a payment recorded twice). Show the
    // page as it now stands instead; anything not saved is plain to see and can be done again by hand.
    location.reload();
  }
});
// Behavior for parts of a page; run on the page at load and again on any part swapped in place.
function wire(root) {
// availability: picking a shoot date shows whatever is already booked that day, however far out
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
// a field shown only while a choice in its form is picked: data-show-if="method=check"
root.querySelectorAll('[data-show-if]').forEach(el => {
  const [name, val] = el.dataset.showIf.split('='), form = el.form;
  if (!form) return;
  const sync = () => { const r = form.querySelector(`[name="${name}"]:checked`); el.hidden = !(r && r.value === val); };
  form.addEventListener('change', sync); sync(); });
// the new quote line: "Something else" opens the what-and-price fields
root.querySelectorAll('select[data-show-custom]').forEach(sel => {
  const box = sel.form && sel.form.querySelector('.qcustom'); if (!box) return;
  const sync = () => { const on = sel.value === 'custom'; box.hidden = !on;
    box.querySelectorAll('input[name=label],input[name=amount]').forEach(i => i.required = on); };
  sel.addEventListener('change', sync); sync(); });
// intake: a live count of answers against what ticks the step off
root.querySelectorAll('.intake-note[data-need]').forEach(note => {
  const f = note.closest('form'), need = +note.dataset.need, base = note.textContent;
  const sync = () => { const n = [...f.querySelectorAll('input:not([type=hidden])')].filter(i => i.value.trim()).length;
    note.textContent = base + ' ' + n + ' answered' + (n >= need ? ': enough to tick it off.' : '.'); };
  f.addEventListener('input', sync); sync(); });
// A typed age fills in the Minor box (which always has the last word); an approximate age near the age of
// majority leaves it alone. Fields are found by name in the same form (form= attributes included).
root.querySelectorAll('input[data-age]').forEach(age => {
  const f = age.form; if (!f) return;
  const box = f.elements[age.dataset.minor], ap = f.elements[age.dataset.approx], maj = +age.dataset.majority || 18;
  if (!box) return;
  const fill = () => { const n = parseInt(age.value, 10); if (isNaN(n)) return;
    if (ap && ap.checked && Math.abs(n - maj) <= 2) return; box.checked = n < maj; };
  age.addEventListener('change', fill); ap && ap.addEventListener('change', fill); });
}
wire(document);

// data-confirm on a form (asked on submit) or a button (asked on click)
document.addEventListener('submit', e => {
  const f = e.target; if (f.dataset && f.dataset.confirm && !confirm(f.dataset.confirm)) e.preventDefault(); }, true);
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-confirm], input[data-confirm], a[data-confirm]');
  if (b && !confirm(b.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); return; }
  // a document still printing the SAMPLE banner: ask once before it goes out
  const o = e.target.closest('[data-did-output]');
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
  const t = e.target.closest('[data-toggle-open]'); if (t) { t.closest(t.dataset.toggleOpen || 'li').classList.toggle('open'); }
});
// A document window that changes something (Mark sent, Mark signed, an email added) tells the app's other windows,
// which refresh their parts in place: the session page never shows a stale status after the document closes.
if (document.querySelector('body > .bar')) {
  document.addEventListener('submit', e => { if ((e.target.getAttribute('method') || '').toLowerCase() === 'post') {
    try { sessionStorage.setItem('la-posted', '1'); } catch (_) {} } });
  try { if (sessionStorage.getItem('la-posted')) { sessionStorage.removeItem('la-posted'); localStorage.setItem('la-changed', String(Date.now())); } } catch (_) {}
}
async function refreshParts() {
  const parts = document.querySelectorAll('[data-refresh], [data-inplace]'); if (!parts.length) return;
  try {
    const doc = new DOMParser().parseFromString(await (await fetch(location.href)).text(), 'text/html');
    parts.forEach(el => { const n = doc.getElementById(el.id); if (n && !el.contains(document.activeElement)) {
      const open_ = [...el.querySelectorAll('details')].map(d => d.open);
      n.querySelectorAll('details').forEach((d, i) => { if (open_[i]) d.open = true; });
      el.replaceWith(n); wire(n); } });
  } catch (_) {}
}
document.addEventListener('click', e => { const x = e.target.closest('[data-dismiss]'); if (x) x.closest('.toast').remove(); });
setTimeout(() => document.querySelectorAll('.toast').forEach(t => t.style.opacity = '.92'), 0);
window.laRefresh = refreshParts;
window.addEventListener('storage', e => { if (e.key === 'la-changed') refreshParts(); });
// Documents open in their own window. Close shuts it; a page that was not opened as its own window goes back instead.
document.addEventListener('click', e => {
  const c = e.target.closest('[data-close]'); if (!c) return;
  e.preventDefault(); window.close(); setTimeout(() => { location.href = c.href; }, 200); });
// Email it / Text it / Print / Download don't prove the client got it: afterwards the bar asks "Did you send it?"
document.addEventListener('click', e => {
  if (e.target.closest('[data-did-output]')) document.querySelectorAll('.marksent').forEach(f => f.classList.add('asking')); });
// After a form posts back to the same page, return to the same scroll spot instead of the top.
document.addEventListener('submit', e => {
  const f = e.target; if ((f.getAttribute('method') || '').toLowerCase() !== 'post' || f.target === '_blank') return;
  try { sessionStorage.setItem('scrollAt', JSON.stringify({p: location.pathname, y: scrollY, t: Date.now()})); } catch (_) {} });
try { const s = JSON.parse(sessionStorage.getItem('scrollAt') || 'null'); sessionStorage.removeItem('scrollAt');
  if (s && s.p === location.pathname && Date.now() - s.t < 15000 && !location.hash) scrollTo(0, s.y); } catch (_) {}
// a radio that fills a number field: data-fill="fieldname" data-value="..."
document.addEventListener('change', e => {
  const r = e.target.closest('[data-fill]'); if (r && r.form && r.form[r.dataset.fill]) r.form[r.dataset.fill].value = r.dataset.value; });
// a search box that hides rows: data-filter-rows=".selector"
document.querySelectorAll('[data-filter-rows]').forEach(inp => inp.addEventListener('input', () => {
  const v = inp.value.toLowerCase();
  document.querySelectorAll(inp.dataset.filterRows).forEach(r => r.hidden = !!v && !r.textContent.toLowerCase().includes(v));
  if (inp.dataset.filterHeads) document.querySelectorAll(inp.dataset.filterHeads).forEach(h => {   // a heading hides with its rows
    let n = h.nextElementSibling, any = false;
    while (n && !n.matches(inp.dataset.filterHeads)) { if (!n.hidden) any = true; n = n.nextElementSibling; }
    h.hidden = !any; }); }));
// a running job: poll its status URL, move the bar, reload when it stops. data-poll, data-bar, data-count (element ids)
document.querySelectorAll('[data-poll]').forEach(el => { const t = setInterval(async () => {
  try { const r = await (await fetch(el.dataset.poll)).json();
    if (r.state !== 'running') { clearInterval(t); location.reload(); return; }
    const bar = document.getElementById(el.dataset.bar), n = document.getElementById(el.dataset.count);
    if (bar) bar.style.width = (r.total ? 100 * r.done / r.total : 0) + '%';
    if (n) n.textContent = r.done + ' / ' + r.total; } catch (e) {} }, 700); });
// go somewhere after a pause: data-go-after="ms" data-href="..."
document.querySelectorAll('[data-go-after]').forEach(el => setTimeout(() => location.href = el.dataset.href, +el.dataset.goAfter));
})();

// People table: each row is its own form, so saving one row used to drop what was typed in the others. Now Save on any
// row saves every changed row first (in place), then submits; leaving with unsaved rows asks first.
(() => {
  const sec = document.getElementById('people'); if (!sec) return;
  const forms = () => [...sec.querySelectorAll('form[id^="p"]')].filter(f => f.id !== 'pnew');
  const dirty = f => [...f.elements].some(el => (el.type === 'checkbox' || el.type === 'radio')
    ? el.checked !== el.defaultChecked : (el.tagName === 'INPUT' && el.type !== 'hidden' && el.value !== el.defaultValue));
  let saving = false;
  sec.addEventListener('submit', async e => {
    const f = e.target; if (saving || !f.id || f.id === 'pnew' || e.submitter && e.submitter.name === 'delete') return;
    const others = forms().filter(o => o !== f && dirty(o)); if (!others.length) return;
    e.preventDefault(); saving = true;
    for (const o of others) {
      try { await fetch(o.action, { method: 'POST', body: new FormData(o), credentials: 'same-origin' }); } catch (_) {}
    }
    f.requestSubmit ? f.requestSubmit(e.submitter) : f.submit();
  });
  window.addEventListener('beforeunload', e => { if (!saving && forms().some(dirty)) { e.preventDefault(); e.returnValue = ''; } });
  sec.addEventListener('submit', () => { saving = true; });
})();
