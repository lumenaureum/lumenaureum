
(function () {
  const changed = form => { if (form) { form.dispatchEvent(new Event('input', { bubbles: true }));
    form.dispatchEvent(new Event('change', { bubbles: true })); } };

  document.addEventListener('click', e => {
    const b = e.target.closest('button[data-step-sw]');
    if (b) {
      e.preventDefault();
      const cell = b.parentElement, on = cell.querySelector('input[data-step-on]'), row = b.closest('tr, li');
      if (!on) return;
      const turningOff = on.value !== '0';
      if (turningOff) {
        if (b.dataset.warn && !confirm(b.dataset.warn + ' Turn it off?')) return;
        const group = b.closest('[data-stage-warn]');
        if (group && group.dataset.stageWarn) {
          const left = [...group.querySelectorAll('input[data-step-on]')].filter(i => i !== on && i.value !== '0').length
            + group.querySelectorAll('button.step-sw[disabled]').length;
          if (!left && !confirm(group.dataset.stageWarn + ' Turn it off?')) return;
        }
      }
      on.value = turningOff ? '0' : '1';
      b.textContent = turningOff ? 'Turn on' : 'Turn off';
      b.setAttribute('aria-pressed', turningOff ? 'false' : 'true');
      const name = row && row.querySelector('.step-name');
      const label = name ? (name.value || name.textContent || '').trim() : '';
      b.setAttribute('aria-label', (turningOff ? 'Turn on ' : 'Turn off ') + label);
      if (row) row.classList.toggle('step-off', turningOff);
      changed(b.closest('form'));
      return;
    }
    const f = e.target.closest('button[data-fill]');
    if (f) {
      e.preventDefault();
      const form = f.closest('form'); let vals;
      try { vals = JSON.parse(f.dataset.fill); } catch (_) { return; }
      if (!form) return;
      Object.entries(vals).forEach(([name, v]) => {
        const els = form.querySelectorAll(`[name="${name}"]`);
        els.forEach(el => {
          if (el.type === 'checkbox') el.checked = !!v;
          else if (el.type === 'radio') el.checked = el.value === String(v);
          else el.value = v;
        });
      });
      changed(form);
    }
  });

  const pv = document.getElementById('walk-pv'), acc = document.getElementById('walk-defaults');
  const form = (pv || acc) && (pv || acc).closest('form');
  if (!form) return;
  const box = document.getElementById('walk-pv-box'); let t = null, n = 0;
  const go = () => { if (!pv) return; const my = ++n; const fd = new FormData(form); fd.delete('act');
    fetch(pv.dataset.url, { method: 'POST', body: fd, credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).then(j => {
      if (!j || my !== n || j.preview === null) return; pv.textContent = j.preview; if (box) box.hidden = !j.preview; }).catch(() => {}); };
  const grayOut = () => { if (acc) { acc.disabled = true; acc.title = 'You changed this step: press Next to keep it'; } };
  form.addEventListener('input', () => { grayOut(); clearTimeout(t); t = setTimeout(go, 250); });
  form.addEventListener('change', () => { grayOut(); clearTimeout(t); t = setTimeout(go, 50); });

  document.addEventListener('change', e => {
    const box = e.target.closest('input[type=checkbox][data-off-warn]'); if (!box) return;
    if (!box.checked && !confirm(box.dataset.offWarn)) { box.checked = true; }
    const note = box.id && document.querySelector('[data-off-note="' + box.id + '"]');
    if (note) note.hidden = box.checked;
  }, true);
})();

