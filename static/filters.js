const uiFilterStart = bar => {
  const KEY = 'la-filter2:' + bar.dataset.uiFilter;
  const today = bar.dataset.today;
  const scope = (bar.dataset.fScope && document.getElementById(bar.dataset.fScope)) || bar.closest('main') || document.body;
  const items = () => [...scope.querySelectorAll('[data-f]')];
  const chips = () => [...bar.querySelectorAll('[data-f-key]')];
  const qbox = bar.querySelector('[data-f-q]'), sortSel = bar.querySelector('[data-f-sort]');
  const N = +bar.dataset.days || 7;           // fixed quick chips (W5): Due today and Due in 7 days, no number box
  const hasViews = bar.hasAttribute('data-f-views');
  const url = new URL(location.href);

  let st = null;
  try { const s = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s && typeof s === 'object' && s.g && typeof s.g === 'object') st = s; } catch (_) {}
  if (!st) st = { g: hasViews && bar.dataset.fDefault ? { show: bar.dataset.fDefault.split('|').filter(Boolean) } : {}, q: '', sort: '' };
  Object.keys(st.g).forEach(k => { if (!Array.isArray(st.g[k])) st.g[k] = st.g[k] ? [String(st.g[k])] : []; });
  if (hasViews && url.searchParams.has('view')) {                            // a view in the address wins
    const v = url.searchParams.get('view');
    st.g.show = chips().some(b => b.dataset.fKey === 'show' && b.dataset.fVal === v) ? [v] : [];
  }
  if (url.searchParams.has('q')) st.q = url.searchParams.get('q');            // a search in the address wins
  if (url.searchParams.has('sort')) st.sort = url.searchParams.get('sort');
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (_) {} };
  const letGoOfView = () => { if (hasViews && url.searchParams.has('view')) { url.searchParams.delete('view'); try { history.replaceState(history.state, '', url); } catch (_) {} } };

  const addDays = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const until = addDays(today, Math.max(0, N));
  const words = () => (st.q || '').toLowerCase().split(/\s+/).filter(Boolean);
  const has = (el, k) => {
    if (k === 'who') return [el.dataset.fStatus || ''];
    if (k === 'due') { const d = el.dataset.fDue, out = []; if (d && d <= today) out.push('today'); if (d && d <= until) out.push('week'); return out; }
    return (el.getAttribute('data-f-g-' + k) || '').split('|').filter(Boolean);
  };
  const picked = k => st.g[k] || [];
  const okIn = (el, k) => { const p = picked(k); if (!p.length) return true; const h = has(el, k); return p.some(v => h.includes(v)); };
  const okWords = (el, w) => { if (!w.length) return true; const t = (el.textContent + ' ' + Object.values(el.dataset).join(' ')).toLowerCase();
    return w.every(x => x.startsWith('label:') ? has(el, 'label').some(l => l.toLowerCase().includes(x.slice(6))) : t.includes(x)); };
  const match = (el, w, skip) => Object.keys(st.g).every(k => k === skip || okIn(el, k)) && okWords(el, w);
  const active = () => Object.values(st.g).some(p => p.length) || words().length > 0;

  const apply = () => {
    const w = words(), all = items();
    let shown = 0;
    all.forEach(el => { const ok = match(el, w); el.hidden = !ok; if (ok) shown++;
      const nx = el.nextElementSibling;          // a line's inline form follows it: it goes with its line
      if (nx && nx.matches('.ui-inline') && !ok) nx.hidden = true; else if (nx && nx.matches('.ui-inline') && nx.hidden && ok) nx.hidden = false; });
    scope.querySelectorAll('section, details, .ui-flat').forEach(box => {
      if (box.closest('[data-ui-filter]') || !box.querySelector('[data-f]')) return;
      const any = [...box.querySelectorAll('[data-f]')].some(el => !el.hidden);
      if (!any && !box.hidden) { box.hidden = true; box.dataset.fHid = '1'; }
      else if (any && box.dataset.fHid) { box.hidden = false; delete box.dataset.fHid; }
      if (box.tagName === 'DETAILS') {
        if (active() && any && !box.open) { box.open = true; box.dataset.fOpened = '1'; }
        else if (!active() && box.dataset.fOpened) { box.open = false; delete box.dataset.fOpened; }
      }
    });
    chips().forEach(b => {
      const k = b.dataset.fKey, v = b.dataset.fVal, on = picked(k).includes(v);
      b.setAttribute('aria-pressed', String(on));
      const n = all.filter(el => match(el, w, k) && has(el, k).includes(v)).length;
      const c = b.querySelector('[data-f-n]'); if (c && c.textContent !== String(n)) c.textContent = n;
      b.classList.toggle('ui-fchip-zero', !n && !on);
    });
    document.querySelectorAll('[data-f-count]').forEach(c => { const t = shown + ' ' + (shown === 1 ? c.dataset.one : c.dataset.many); if (c.textContent !== t) c.textContent = t; });
    const clear = bar.querySelector('[data-f-clear]'); if (clear) clear.hidden = !active();
    const none = bar.querySelector('[data-f-none]'); if (none) none.hidden = shown > 0 || !all.length;
  };

  const sortNow = () => {
    if (!sortSel) return;
    const o = sortSel.selectedOptions[0]; if (!o) return;
    const key = 's' + o.dataset.key.replace(/(^|-)(\w)/g, (m, a, c) => c.toUpperCase()), desc = o.dataset.dir === 'desc', num = !!o.dataset.num;
    const groups = new Map();
    items().forEach(el => { if (!groups.has(el.parentNode)) groups.set(el.parentNode, []); groups.get(el.parentNode).push(el); });
    groups.forEach((els, parent) => {
      const val = el => { const v = el.dataset[key] || ''; return num ? (parseFloat(v) || 0) : v.toLowerCase(); };
      els.slice().sort((a, b) => {
        const x = val(a), y = val(b);
        if (!num && (x === '') !== (y === '')) return x === '' ? 1 : -1;      // no value: always last
        const c = x < y ? -1 : x > y ? 1 : 0;
        return (desc ? -c : c) || (a.dataset.sName || '').localeCompare(b.dataset.sName || '');
      }).forEach(el => parent.appendChild(el));
    });
  };

  bar.querySelectorAll('[data-f-js]').forEach(r => { r.hidden = false; });
  bar.querySelectorAll('[data-f-noscript]').forEach(b => { b.hidden = true; });
  chips().forEach(b => { if (b.tagName === 'A') { b.setAttribute('role', 'button'); b.removeAttribute('aria-current'); } });
  if (qbox) { qbox.value = st.q || ''; qbox.addEventListener('input', () => { st.q = qbox.value; save(); apply(); }); }
  bar.querySelector('form') && bar.querySelector('form').addEventListener('submit', e => { e.preventDefault(); });
  if (sortSel) {
    if (st.sort && [...sortSel.options].some(o => o.value === st.sort)) sortSel.value = st.sort;
    sortSel.addEventListener('change', () => { st.sort = sortSel.value; save(); sortNow(); });
  }
  bar.addEventListener('click', e => {
    const b = e.target.closest('[data-f-key]');
    if (b) { e.preventDefault();
      const k = b.dataset.fKey, v = b.dataset.fVal, p = picked(k);
      if (p.includes(v)) st.g[k] = p.filter(x => x !== v);                    // a chip tapped again lets go
      else st.g[k] = b.hasAttribute('data-f-one') ? [v] : p.concat([v]);
      letGoOfView(); save(); apply(); return; }
    const c = e.target.closest('[data-f-clear]');
    if (c) { e.preventDefault();
      if (bar.hasAttribute('data-f-server') && url.searchParams.get('q')) { st = { g: {}, q: '', sort: st.sort }; save(); location.href = c.getAttribute('href') || location.pathname; return; }
      st.g = {}; st.q = ''; if (qbox) qbox.value = ''; letGoOfView(); save(); apply(); }
  });
  bar.addEventListener('keydown', e => {                                     // a view link acts as a button: Space too
    const b = e.target.closest('a[data-f-key]'); if (b && e.key === ' ') { e.preventDefault(); b.click(); }
  });
  Object.keys(st.g).forEach(k => { st.g[k] = st.g[k].filter(v => bar.querySelector(`[data-f-key="${CSS.escape(k)}"][data-f-val="${CSS.escape(v)}"]`)); });
  sortNow();
  apply();
  cue(bar);
  return { bar, apply };
};
const cue = bar => {
  const row = bar.querySelector('[data-f-row]'); if (!row || row.parentElement.classList.contains('ui-filter-rowwrap')) return;
  const wrap = document.createElement('div'); wrap.className = 'ui-filter-rowwrap';
  row.before(wrap); wrap.appendChild(row);
  const more = document.createElement('button'); more.type = 'button'; more.className = 'ui-btn ui-btn-quiet ui-filter-more';
  more.setAttribute('aria-label', 'Show more filters'); more.title = 'More filters'; more.hidden = true;
  more.innerHTML = '<span aria-hidden="true">&rsaquo;</span>'; wrap.appendChild(more);
  const look = () => {
    const right = row.scrollLeft + row.clientWidth < row.scrollWidth - 2, left = row.scrollLeft > 2;
    more.hidden = !right; row.toggleAttribute('data-more-right', right); row.toggleAttribute('data-more-left', left);
  };
  more.addEventListener('click', () => row.scrollBy({ left: Math.max(120, row.clientWidth * 0.7), behavior: 'smooth' }));
  row.addEventListener('scroll', look, { passive: true });
  addEventListener('resize', look);
  bar.addEventListener('click', () => setTimeout(look, 0));        // a chip picked or cleared changes the row's width
  look();
};
(() => {
  let cur = document.querySelector('[data-ui-filter]') ? uiFilterStart(document.querySelector('[data-ui-filter]')) : null, t = 0;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => {
    const b = document.querySelector('[data-ui-filter]');
    if (!b) { cur = null; return; }
    if (!cur || cur.bar !== b) cur = uiFilterStart(b); else cur.apply();
  }, 30); }).observe(document.body, { childList: true, subtree: true });
})();

