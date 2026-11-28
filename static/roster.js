(() => {
  const digits = t => (t || '').replace(/\D/g, '');
  const fold = t => (t || '').normalize('NFKD').toLowerCase().replace(/\p{M}/gu, '').normalize('NFC');
  document.querySelectorAll('[data-ui-roster]').forEach(wrap => {
    const box = wrap.querySelector('[data-r-q]'), table = wrap.querySelector('table'), body = table.tBodies[0];
    const none = wrap.querySelector('[data-r-none]');
    const rows = () => [...body.rows];
    wrap.querySelectorAll('[data-r-noscript]').forEach(b => { b.hidden = true; });
    const form = wrap.querySelector('form');
    if (form) form.addEventListener('submit', e => e.preventDefault());
    const phoneWord = w => /\d/.test(w) && !/[a-z]/.test(w);
    const match = (tr, words) => {
      const text = fold(tr.dataset.rText || (tr.textContent + ' ' + (tr.dataset.rFind || ''))).replace(/\s+/g, ' ');
      const phone = digits(tr.dataset.rPhone);
      return words.every(w => text.includes(w) || (phoneWord(w) && phone.includes(digits(w))));
    };
    const why = (tr, words) => {
      let src = [];
      try { src = JSON.parse(tr.dataset.rWhy || '[]'); } catch (e) { src = []; }
      const name = fold((tr.querySelector('.ui-roster-name') || {}).textContent), out = [];
      words.forEach(w => {
        if (name.includes(w)) return;
        const hit = src.find(([lab, v]) => fold(v).includes(w) || (phoneWord(w) && lab === 'Phone' && digits(v).includes(digits(w))));
        if (hit && !out.includes(`${hit[0]}: ${hit[1]}`)) out.push(`${hit[0]}: ${hit[1]}`);
      });
      return out;
    };
    const count = document.querySelector('[data-r-count]'), add = wrap.querySelector('[data-r-add]'), all = wrap.querySelector('[data-r-all]');
    const filter = () => {
      const y = window.scrollY, typed = (box.value || '').trim(), words = fold(typed).split(/\s+/).filter(Boolean);
      let shown = 0;
      rows().forEach(tr => {
        const ok = match(tr, words); tr.hidden = !ok; if (ok) shown++;
        const line = tr.querySelector('[data-r-whyline]');
        if (line && tr.dataset.rWhy) { const w = ok ? why(tr, words) : []; line.textContent = w.join(' · '); line.hidden = !w.length; }
      });
      if (none) none.hidden = shown > 0;
      if (count) {                                                       // the count follows the filter
        const total = rows().length;
        count.textContent = (words.length ? `${shown} of ` : '') + `${total} ${count.dataset.noun || ''}`.trim();
      }
      const q = encodeURIComponent(typed);
      if (all) all.href = all.dataset.rAll + (typed ? `?q=${q}` : '');
      if (add) {                                                         // "Add Garcia as a new contact"
        const named = /[^\d\s()+.\-]/.test(typed);
        add.hidden = !named;
        add.href = add.dataset.rAdd + (named ? `?name=${q}` : '');
        const t = add.querySelector('[data-r-typed]'); if (t) t.textContent = typed;
      }
      if (window.scrollY !== y) window.scrollTo(window.scrollX, y);     // typing never moves the page
    };
    if (box) box.addEventListener('input', filter);
    let cur = (wrap.querySelector('th[aria-sort] [data-r-sort]') || {}).dataset?.rSort || 'name';
    let desc = !!wrap.querySelector('th[aria-sort="descending"]');
    const sortBy = (key, d, num) => {
      const name = k => k.replace(/-(\w)/g, (m, c) => c.toUpperCase());
      const val = tr => { const v = tr.dataset['r' + name('-' + key)] || ''; return num ? (parseFloat(v) || 0) : fold(v); };
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

