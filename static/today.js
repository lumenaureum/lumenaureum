(() => {
  
  async function select(key) {
    if (!key) return;
    const [k, id] = key.split(':');
    location = k === 'e' ? `/event/${id}` : `/c/${id}`; return;
    const row = document.querySelector(`.client[data-key="${key}"]`);
    document.querySelectorAll('.client.open').forEach(c => { if (c !== row) { c.classList.remove('open'); c.querySelector('.cdetail').innerHTML = ''; } });
    if (!row) { location = k === 'e' ? `?e=${id}` : `?c=${id}`; return; }
    if (row.classList.contains('open')) { row.classList.remove('open'); row.querySelector('.cdetail').innerHTML = ''; history.replaceState(null, '', location.pathname); mark(''); return; }
    const html = await (await fetch(k === 'e' ? `/focus/event/${id}` : `/focus/client/${id}`)).text();
    const det = row.querySelector('.cdetail'); det.innerHTML = html; row.classList.add('open');
    history.replaceState(null, '', k === 'e' ? `?e=${id}` : `?c=${id}`);
    mark(key); wire(det);
    row.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function mark(key) { document.querySelectorAll('[data-sel]').forEach(x => x.classList.toggle('on', !!key && x.dataset.sel === key)); }
  const sel = (document.getElementById('today-sel') || {dataset: {}}).dataset.sel || '';
  mark(sel);
  if (sel) { const op = document.querySelector('.client.open'); if (op) op.scrollIntoView({ block: 'start' }); }

  
  const labels = { overdue: 'Clients with something late', week: 'Clients with something due this week', money: 'Clients who owe you', lead: 'Leads' };
  const attr = { overdue: 'late', week: 'week', money: 'money', lead: 'lead' };
  let on = null;
  function filter(f) {
    on = on === f ? null : f;
    document.querySelectorAll('.kpi').forEach(k => k.classList.toggle('on', k.dataset.filter === on));
    document.querySelectorAll('.client').forEach(c => c.hidden = !!on && c.dataset[attr[on]] !== '1');
    const note = document.querySelector('.filter-note'); note.hidden = !on; note.querySelector('span').textContent = on ? labels[on] : '';
  }
  document.querySelectorAll('.tile').forEach(tl => tl.addEventListener('click', e => {
    if (e.target.closest('a,button,input,form,select,details,summary')) return;
    const [k, id] = tl.dataset.key.split(':'); location = k === 'e' ? `/event/${id}` : `/c/${id}`; }));
  document.querySelectorAll('.kpi').forEach(k => k.addEventListener('click', () => filter(k.dataset.filter)));
  const clr = document.querySelector('[data-clear]'); if (clr) clr.addEventListener('click', () => filter(on));

  
  let d = null;
  const target = (x, y) => {
    const el = document.elementFromPoint(x, y); const t = el && el.closest('[data-drop]');
    if (!t) return null;
    if (t.dataset.for && t.dataset.for !== d.el.dataset.id) return null;
    if (t.dataset.drop === 'stage' && !t.dataset.for && d.el.classList.contains('puck')) return null;
    return t;
  };
  document.querySelectorAll('.cdue-item, .cshoot').forEach(el => el.addEventListener('click', e => {
    if (el._dragged) { el._dragged = false; return; }
    const [k, id] = el.dataset.sel.split(':'); location = k === 'e' ? `/event/${id}` : `/c/${id}`; }));
  function wire(root) {
    root.querySelectorAll('[data-sel]').forEach(el => { if (el._w) return; el._w = 1; el.addEventListener('click', e => {
      if (e.target.closest('a,button,input,select') || el._dragged) { el._dragged = false; return; } select(el.dataset.sel); }); });
    root.querySelectorAll('[data-drag]').forEach(el => { if (el._dw) return; el._dw = 1; el.addEventListener('pointerdown', e => {
      if (e.button || e.target.closest('a,button,input')) return;
      e.preventDefault();
      d = { el, x: e.clientX, y: e.clientY, moved: false, ghost: null, over: null }; }); });
  }
  wire(document);
  document.addEventListener('pointermove', e => {
    if (!d) return;
    if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 6) return;
    if (!d.moved) {
      d.moved = true; d.ghost = d.el.cloneNode(true); d.ghost.classList.add('drag-ghost'); document.body.appendChild(d.ghost);
      d.el.classList.add('dragging'); document.body.classList.add('dragging-any');
      document.body.dataset.dragKind = d.el.classList.contains('puck') ? 'puck' : 'item';
    }
    d.ghost.style.left = e.clientX - 20 + 'px'; d.ghost.style.top = e.clientY - 20 + 'px';
    const t = target(e.clientX, e.clientY);
    if (d.over && d.over !== t) d.over.classList.remove('over');
    if (t) t.classList.add('over'); d.over = t;
  });
  document.addEventListener('pointercancel', () => { if (d && d.ghost) { d.ghost.remove(); d.el.classList.remove('dragging'); document.body.classList.remove('dragging-any'); } d = null; });
  document.addEventListener('pointerup', async e => {
    if (!d) return;
    const cur = d; d = null;
    if (!cur.moved) return;
    cur.el._dragged = true;
    cur.ghost.remove(); cur.el.classList.remove('dragging'); document.body.classList.remove('dragging-any');
    if (cur.over) cur.over.classList.remove('over');
    const t = cur.over; if (!t) return;
    const kind = cur.el.dataset.kind, id = cur.el.dataset.id, fd = new FormData(); let url;
    if (t.dataset.drop === 'stage') { fd.append('stage', t.dataset.value); url = kind === 'event' ? `/event/${id}/stage` : `/session/${id}/stage`; }
    else if (t.dataset.drop === 'day') {
      if (kind === 'event') { fd.append('date', t.dataset.value); url = `/event/${id}/date`; }
      else { fd.append('act', 'date'); fd.append('shoot_date', t.dataset.value); url = `/session/${id}/quick`; }
    }
    await fetch(url, { method: 'POST', body: fd, headers: { 'X-Fetch': '1' } });
    location.reload();
  });
})();

