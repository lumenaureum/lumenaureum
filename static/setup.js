// Setup steps: show the fields for the chosen storage kind, and test a connection before saving.
(() => {
  const be = (p, v) => document.querySelectorAll('[data-be^="' + p + '"]').forEach(e =>
    e.classList.toggle('on', e.dataset.be === p + v || (v && e.dataset.be === p + 'any')));
  document.querySelectorAll('[data-be-select]').forEach(s => {
    be(s.dataset.beSelect, s.value); s.addEventListener('change', () => be(s.dataset.beSelect, s.value)); });
  document.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', async () => {
    const w = b.dataset.test, f = document.getElementById('f'), out = document.getElementById('t_' + w);
    out.textContent = 'Testing...';
    try {
      const r = await fetch(f.dataset.testUrl.replace(/\/W$/, '/' + encodeURIComponent(w)), { method: 'POST', body: new FormData(f) });
      const j = await r.json(); out.textContent = j.message; out.style.color = j.ok ? 'var(--gold)' : 'var(--crit)';
      if (j.host_key) {          // first contact with an SFTP server: offer to trust the key the owner can now check
        const p = w + '_', hk = f.elements[p + 'host_key'], box = document.querySelector('[data-trust="' + w + '"]');
        if (hk) hk.value = j.host_key;
        if (box) { box.hidden = false; box.querySelector('.fp').textContent = j.fingerprint; }
      }
    } catch (e) { out.textContent = 'No answer'; out.style.color = 'var(--crit)'; }
  }));
})();
// time zone: preselect the browser's own when none is chosen yet
(() => {
  const tz = document.querySelector('select[name=timezone]');
  if (!tz || tz.value) return;
  try { const z = Intl.DateTimeFormat().resolvedOptions().timeZone; if ([...tz.options].some(o => o.value === z || o.text === z)) tz.value = z; } catch (e) {}
})();
