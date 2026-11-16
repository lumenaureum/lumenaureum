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
    document.addEventListener('click', e => {
      const t = e.target.closest('[data-tip]');
      if (t && t !== cur && t.dataset.tip && !e.target.closest('a,button,input,label,select,textarea')) show(t, e.clientX, e.clientY); else hide();
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
  const [name, val] = el.dataset.showIf.split('='), form = el.form;
  if (!form) return;
  const sync = () => { const r = form.querySelector(`[name="${name}"]:checked`); el.hidden = !(r && r.value === val); };
  form.addEventListener('change', sync); sync(); });
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
}
wire(document);

document.addEventListener('submit', e => {
  const f = e.target; if (f.dataset && f.dataset.confirm && !('noConfirm' in f.dataset) && !confirm(f.dataset.confirm)) e.preventDefault(); }, true);
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
  const amt = parseFloat((f.querySelector('[name=amount]') || {}).value || '0');
  if (kind === 'refund' || !(amt > parseFloat(bal) + 0.005)) return;
  const over = (amt - Math.max(parseFloat(bal), 0)).toFixed(2);
  if (!confirm('That is $' + over + ' more than the balance. Record it anyway? The extra shows as a credit on the session.'))
    e.preventDefault(); });
document.addEventListener('submit', e => {
  const f = e.target; if ((f.getAttribute('method') || '').toLowerCase() !== 'post' || f.target === '_blank') return;
  try { sessionStorage.setItem('scrollAt', JSON.stringify({p: location.pathname, y: scrollY, t: Date.now()})); } catch (_) {} });
try { const s = JSON.parse(sessionStorage.getItem('scrollAt') || 'null'); sessionStorage.removeItem('scrollAt');
  if (s && s.p === location.pathname && Date.now() - s.t < 15 * 1000 && !location.hash) scrollTo(0, s.y); } catch (_) {}
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
document.querySelectorAll('[data-go-after]').forEach(el => setTimeout(() => location.href = el.dataset.href, +el.dataset.goAfter));
})();

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
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const open = document.activeElement && document.activeElement.closest && document.activeElement.closest('details[open]');
  if (open && open.querySelector('summary')) { open.open = false; open.querySelector('summary').focus(); }
});

