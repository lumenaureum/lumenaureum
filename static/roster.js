(() => {
  const digits = t => (t || '').replace(/\D/g, '');
  document.querySelectorAll('[data-ui-roster]').forEach(wrap => {
    const box = wrap.querySelector('[data-r-q]'), table = wrap.querySelector('table'), body = table.tBodies[0];
    const none = wrap.querySelector('[data-r-none]');
    const rows = () => [...body.rows];
    wrap.querySelectorAll('[data-r-noscript]').forEach(b => { b.hidden = true; });
    const form = wrap.querySelector('form');
    if (form) form.addEventListener('submit', e => e.preventDefault());
    const match = (tr, words) => {
      const text = (tr.textContent + ' ' + (tr.dataset.rFind || '')).toLowerCase().replace(/\s+/g, ' ');
      const phone = digits(tr.dataset.rPhone);
      return words.every(w => text.includes(w) || (/\d/.test(w) && !/[a-z]/.test(w) && phone.includes(digits(w))));
    };
    const filter = () => {
      const y = window.scrollY, words = (box.value || '').toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      rows().forEach(tr => { const ok = match(tr, words); tr.hidden = !ok; if (ok) shown++; });
      if (none) none.hidden = shown > 0;
      if (window.scrollY !== y) window.scrollTo(window.scrollX, y);     // typing never moves the page
    };
    if (box) box.addEventListener('input', filter);
    let cur = (wrap.querySelector('th[aria-sort] [data-r-sort]') || {}).dataset?.rSort || 'name';
    let desc = !!wrap.querySelector('th[aria-sort="descending"]');
    const sortBy = (key, d, num) => {
      const name = k => k.replace(/-(\w)/g, (m, c) => c.toUpperCase());
      const val = tr => { const v = tr.dataset['r' + name('-' + key)] || ''; return num ? (parseFloat(v) || 0) : v.toLowerCase(); };
      rows().sort((a, b) => {
        const x = val(a), y = val(b);
        if (!num && (x === '') !== (y === '')) return x === '' ? 1 : -1;      // no value: always last
        const c = x < y ? -1 : x > y ? 1 : 0;
        return (d ? -c : c) || (a.dataset.rName || '').localeCompare(b.dataset.rName || '');
      }).forEach(tr => body.appendChild(tr));
      wrap.querySelectorAll('th').forEach(th => th.removeAttribute('aria-sort'));
      const th = wrap.querySelector(`[data-r-sort="${key}"]`).closest('th');
      th.setAttribute('aria-sort', d ? 'descending' : 'ascending');
    };
    wrap.addEventListener('click', e => {
      const h = e.target.closest('[data-r-sort]');
      if (h) { e.preventDefault(); const k = h.dataset.rSort; desc = k === cur ? !desc : false; cur = k; sortBy(k, desc, !!h.dataset.rNum); return; }
      const tr = e.target.closest('tbody tr[data-ui-href]');
      if (!tr || e.target.closest('a, button, input')) return;
      if (window.getSelection && String(window.getSelection())) return;    // selecting text is not a tap
      location.assign(tr.dataset.uiHref);
    });
    if (box && box.value) filter();
  });
})();

