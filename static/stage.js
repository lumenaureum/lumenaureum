(() => {
  const ROW = 26, GAP = 6;
  const lay = (box, fromBottom) => {
    const base = fromBottom ? 18 : 20;  // rows clear the Today face and Shoot marker above, the axis labels below
    const marks = [...box.querySelectorAll('.jm')];
    marks.forEach(m => { m.style.top = ''; m.style.bottom = ''; m.style.top = '0px'; });
    const items = marks.map(m => ({ m, r: m.getBoundingClientRect() })).sort((a, b) => a.r.left - b.r.left);
    const rows = [];
    items.forEach(it => {
      let k = rows.findIndex(end => it.r.left >= end + GAP);
      if (k < 0) { rows.push(0); k = rows.length - 1; }
      rows[k] = it.r.right;
      it.m.style.top = ''; it.m.style[fromBottom ? 'bottom' : 'top'] = (base + k * ROW) + 'px';
    });
    box.style.height = Math.max(40, rows.length * ROW + base + 6) + 'px';
  };
  const run = () => { document.querySelectorAll('.j-past').forEach(b => lay(b, true)); document.querySelectorAll('.j-future').forEach(b => lay(b, false)); };
  run(); addEventListener('resize', run); document.fonts && document.fonts.ready.then(run);
})();
document.addEventListener('submit', e => {
  const f = e.target; if (!f.matches || !f.matches('form.qnewf')) return;
  const sel = f.querySelector('select[name=fee_key]'), msg = f.querySelector('.qmsg');
  if (sel && !sel.value) { e.preventDefault(); e.stopImmediatePropagation(); if (msg) msg.hidden = false; sel.focus(); }
}, true);
document.addEventListener('change', e => {
  const f = e.target.form; if (f && f.matches('form.qnewf')) { const m = f.querySelector('.qmsg'); if (m) m.hidden = true; }
});
document.addEventListener('submit', async e => {
  const f = e.target; if (!f.matches || !f.matches('form[data-quote-send]')) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const b = f.querySelector('button'); if (b) { if (b.disabled) return; b.disabled = true; }
  try {
    const r = await fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin', headers: { 'X-Quote-Fetch': '1' } });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok && j.url) { if (!window.open(j.url, '_blank')) { location.href = j.url; return; } }
  } catch (_) {}
  location.reload();     // this tab now shows Open quote N, or the refusal with the latest prices
}, true);

