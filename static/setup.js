(() => {
  const be = (p, v) => document.querySelectorAll('[data-be^="' + p + '"]').forEach(e =>
    e.classList.toggle('on', e.dataset.be === p + v || (v && e.dataset.be === p + 'any')));
  document.querySelectorAll('[data-be-select]').forEach(s => {
    be(s.dataset.beSelect, s.value); s.addEventListener('change', () => be(s.dataset.beSelect, s.value)); });
})();
(() => {
  const tz = document.querySelector('select[name=timezone]');
  if (!tz) return;
  let z = '';
  try { z = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}
  const bt = tz.form && tz.form.elements.browser_tz;
  if (bt) bt.value = z;
  if (tz.dataset.keep && tz.value) return;
  if ([...tz.options].some(o => o.value === z)) tz.value = z;
})();

