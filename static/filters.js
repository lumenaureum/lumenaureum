const uiFilterStart = bar => {
  const KEY = 'la-filter:' + bar.dataset.uiFilter;
  const today = bar.dataset.today;
  const scope = (bar.dataset.fScope && document.getElementById(bar.dataset.fScope)) || bar.closest('main') || document.body;
  const items = () => [...scope.querySelectorAll('[data-f]')];
  const qbox = bar.querySelector('[data-f-q]'), sortSel = bar.querySelector('[data-f-sort]');
  const N = +bar.dataset.days || 7;           // fixed quick chips (W5): Due today and Due in 7 days, no number box
  let st = { status: '', g: {}, q: '', sort: '' };
  try { const s = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s && typeof s === 'object') st = Object.assign(st, s, { g: Object.assign({}, s.g || {}) }); } catch (_) {}
  st.n = N;
  const url = new URL(location.href);
  if (url.searchParams.has('q')) st.q = url.searchParams.get('q');            // a search in the address wins
  if (url.searchParams.has('sort')) st.sort = url.searchParams.get('sort');
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (_) {} };

  const addDays = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const words = () => (st.q || '').toLowerCase().split(/\s+/).filter(Boolean);
  const match = (el, w, until) => {
    if (st.status === 'mine' || st.status === 'waiting') { if (el.dataset.fStatus !== st.status) return false; }
    else if (st.status === 'due') { const d = el.dataset.fDue; if (!d || d > until) return false; }
    else if (st.status === 'today') { const d = el.dataset.fDue; if (!d || d > today) return false; }
    for (const [k, v] of Object.entries(st.g)) {
      if (!v) continue;
      const have = (el.getAttribute('data-f-g-' + k) || '').split('|');
      if (!have.includes(v)) return false;
    }
    if (w.length) { const t = (el.textContent + ' ' + Object.values(el.dataset).join(' ')).toLowerCase(); if (!w.every(x => t.includes(x))) return false; }
    return true;
  };
  const active = () => !!(st.status || Object.values(st.g).some(Boolean) || words().length);

  const apply = () => {
    const w = words(), until = addDays(today, Math.max(0, st.n || 0));
    let shown = 0;
    items().forEach(el => { const ok = match(el, w, until); el.hidden = !ok; if (ok) shown++;
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
    bar.querySelectorAll('[data-f-key]').forEach(b => {
      const k = b.dataset.fKey, v = b.dataset.fVal;
      b.setAttribute('aria-pressed', String(k === 'status' ? st.status === v : (st.g[k] || '') === v));
    });
    const all = items(), cnt = { '': all.length, mine: 0, waiting: 0, today: 0, due: 0 };
    all.forEach(el => { const s = el.dataset.fStatus, d = el.dataset.fDue;
      if (s === 'mine' || s === 'waiting') cnt[s]++;
      if (d && d <= today) cnt.today++;
      if (d && d <= until) cnt.due++; });
    bar.querySelectorAll('[data-f-c]').forEach(c => { c.textContent = cnt[c.dataset.fC] ?? ''; });
    const none = bar.querySelector('[data-f-none]'); if (none) none.hidden = shown > 0 || !items().length;
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

  bar.querySelectorAll('[data-f-row], [data-f-script-only]').forEach(r => { r.hidden = false; });
  bar.querySelectorAll('[data-f-noscript]').forEach(b => { b.hidden = true; });
  if (qbox) { qbox.value = st.q || ''; qbox.addEventListener('input', () => { st.q = qbox.value; save(); apply(); }); }
  bar.querySelector('form') && bar.querySelector('form').addEventListener('submit', e => { e.preventDefault(); });
  if (sortSel) {
    if (st.sort && [...sortSel.options].some(o => o.value === st.sort)) sortSel.value = st.sort;
    sortSel.addEventListener('change', () => { st.sort = sortSel.value; save(); sortNow(); });
  }
  bar.addEventListener('click', e => {
    const b = e.target.closest('[data-f-key]');
    if (b) { const k = b.dataset.fKey, v = b.dataset.fVal;
      if (k === 'status') st.status = v; else st.g[k] = (st.g[k] === v ? '' : v);   // a chip tapped again lets go
      save(); apply(); return; }
    if (e.target.closest('[data-f-clear]')) { st.status = ''; st.g = {}; st.q = ''; if (qbox) qbox.value = ''; save(); apply(); }
  });
  Object.keys(st.g).forEach(k => { if (st.g[k] && !bar.querySelector(`[data-f-key="${CSS.escape(k)}"][data-f-val="${CSS.escape(st.g[k])}"]`)) st.g[k] = ''; });
  sortNow();
  apply();
  return { bar, apply };
};
(() => {
  let cur = document.querySelector('[data-ui-filter]') ? uiFilterStart(document.querySelector('[data-ui-filter]')) : null, t = 0;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => {
    const b = document.querySelector('[data-ui-filter]');
    if (!b) { cur = null; return; }
    if (!cur || cur.bar !== b) cur = uiFilterStart(b); else cur.apply();
  }, 30); }).observe(document.body, { childList: true, subtree: true });
})();

