(() => {
  const be = (p, v) => document.querySelectorAll('[data-be^="' + p + '"]').forEach(e =>
    e.classList.toggle('on', e.dataset.be === p + v || (v && e.dataset.be === p + v.split('.')[0]) || (v && e.dataset.be === p + 'any')));
  const filled = new WeakMap();
  const put = (inp, v) => {
    if (!inp || v === undefined) return;
    if (!inp.value.trim() || inp.value === filled.get(inp)) { inp.value = v; filled.set(inp, v); }
  };
  const preset = (s, first) => {
    const p = s.dataset.beSelect, o = s.selectedOptions[0], f = s.form, d = o ? o.dataset : {};
    if (!f) return;                                 // first: the page as served (a refused try keeps what was typed)
    const el = n => f.elements[p + n];
    if (!first) put(el('port'), d.port || '');
    const root = el('root');
    if (root) {
      if (!root.dataset.ph0) root.dataset.ph0 = root.placeholder;
      root.placeholder = d.rootPh ? 'e.g. ' + d.rootPh : root.dataset.ph0;
      if (!first && root.value === filled.get(root)) { root.value = ''; filled.delete(root); }   // the last preset's
      if (!first && d.root && !d.root.includes('{d}')) put(root, d.root);
      root.dataset.rootTpl = d.root === undefined ? '{d}/g' : d.root;
      if (!first && el('host') && el('host').value) el('host').dispatchEvent(new Event('input'));
    }
    const ep = el('endpoint');
    if (ep) {
      if (!ep.dataset.ph0) ep.dataset.ph0 = ep.placeholder;
      ep.placeholder = d.endpointPh || ep.dataset.ph0;
    }
    if (!first && d.keymode) {
      const r = f.querySelector('input[name="' + p + 'keymode"][value="' + d.keymode + '"]');
      if (r && !r.checked) { r.checked = true; r.dispatchEvent(new Event('change', {bubbles: true})); }
    }
  };
  document.querySelectorAll('[data-be-select]').forEach(s => {
    be(s.dataset.beSelect, s.value); preset(s, true);
    s.addEventListener('change', () => { be(s.dataset.beSelect, s.value); preset(s); }); });
  document.querySelectorAll('[data-be-pick]').forEach(b => b.addEventListener('click', () => {
    const [n, v] = b.dataset.bePick.split('=');
    const s = document.querySelector('select[name="' + n + '"]');
    if (!s) return;
    s.value = v; s.dispatchEvent(new Event('change')); s.focus();
  }));
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
(() => {
  const kind = document.querySelector('select[name=gallery_type]');
  const warn = document.getElementById('site-top');
  if (!kind || !warn) return;
  const el = n => kind.form.elements[n];
  const host = el('gallery_host'), root = el('gallery_root'), path = el('gallery_path'), url = el('gallery_base_url');
  const names = new Set(warn.dataset.names.split(' '));
  const filled = new WeakMap();
  const fill = (inp, v) => {
    if (inp && (!inp.value.trim() || inp.value === filled.get(inp))) { inp.value = v; filled.set(inp, v); }
  };
  const domain = h => h.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/^[^@]*@/, '')
    .replace(/[:/].*$/, '').replace(/^(ssh|sftp|ftp|shell)\./, '');
  const atTop = () => {
    const t = kind.value, u = url ? url.value.trim() : '';
    if (!t) return false;
    if (u) {
      try { if (!new URL(u.includes('//') ? u : 'https://' + u).pathname.split('/').some(Boolean)) return true; } catch (e) {}
    }
    if (t !== 'sftp' && t !== 'local') return false;
    const w = ((t === 'sftp' ? root : path) || {}).value || '';
    if (!w.trim()) return false;                   // nothing typed yet: no warning while the form is still blank
    const segs = w.replace(/\\/g, '/').split('/').filter(s => s && s !== '.');
    if (!segs.length || (segs.length === 1 && segs[0] === '~')) return t === 'sftp' || w.trim().startsWith('/');
    const last = segs[segs.length - 1].toLowerCase();
    return names.has(last) || (t === 'sftp' && last.replace(/^\.+|\.+$/g, '').includes('.'));
  };
  const check = () => { warn.hidden = !atTop(); };
  if (host) host.addEventListener('input', () => {
    const d = domain(host.value), ok = d.includes('.');
    const tpl = root && root.dataset.rootTpl !== undefined ? root.dataset.rootTpl : '{d}/g';
    if (tpl.includes('{d}')) fill(root, ok ? tpl.replace('{d}', d) : '');
    else if (root && root.value === filled.get(root)) { root.value = ''; filled.delete(root); }
    fill(url, ok ? 'https://' + d + '/g/' : '');
    check();
  });
  [kind, root, path, url].forEach(i => i && i.addEventListener(i === kind ? 'change' : 'input', check));
  if (!warn.hidden) return;                        // a refused save already showed it: keep it
  check();
})();

