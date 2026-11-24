(() => {
  const fmt = n => n >= 1 << 30 ? (n / (1 << 30)).toFixed(1) + ' GB' : n >= 1 << 20 ? (n / (1 << 20)).toFixed(1) + ' MB' : Math.ceil(n / 1024) + ' KB';
  let busy = 0;
  const regions = {};
  const region = k => {
    let el = regions[k] && regions[k].isConnected ? regions[k] : document.getElementById('photos-' + k);
    if (!el) { el = document.createElement('div'); el.id = 'photos-' + k; el.className = 'vh'; el.setAttribute('role', k); document.body.appendChild(el); }
    return regions[k] = el;
  };
  const queue = [];
  let speaking = false;
  const next = () => {
    const item = queue.shift();
    if (!item) { speaking = false; return; }
    speaking = true;
    const el = region(item.k);
    el.textContent = ''; setTimeout(() => { el.textContent = item.msg; setTimeout(() => { if (el.textContent === item.msg) el.textContent = ''; }, 5000);
      setTimeout(next, 1200); }, 60);
  };
  const say = (msg, urgent, progressOf) => {
    if (progressOf) for (let i = queue.length - 1; i >= 0; i--) if (queue[i].progressOf === progressOf) queue.splice(i, 1);
    queue.push({ msg, k: urgent ? 'alert' : 'status', progressOf });
    if (!speaking) next();
  };
  const flying = new Set();              // a file already uploading here is not started twice
  window.addEventListener('beforeunload', e => { if (busy) { e.preventDefault(); e.returnValue = ''; } });
  async function send(zone, file) {
    const row = document.createElement('div'); row.className = 'dz-row';
    row.innerHTML = '<span class="dz-name"></span><span class="mbar"><span></span></span><span class="dz-pct mute"></span>';
    row.querySelector('.dz-name').textContent = file.name;
    zone.querySelector('.dz-list').appendChild(row);
    const bar = row.querySelector('.mbar span'), pct = row.querySelector('.dz-pct');
    const kind = zone.dataset.kind, name = encodeURIComponent(file.name), q = 'kind=' + kind + '&size=' + file.size;
    const tag = kind + '/' + file.name + '/' + file.size;
    if (flying.has(tag)) { row.remove(); return false; }
    flying.add(tag);
    const prog = row.querySelector('.mbar');
    prog.setAttribute('role', 'progressbar'); prog.setAttribute('aria-label', file.name);
    prog.setAttribute('aria-valuemin', '0'); prog.setAttribute('aria-valuemax', '100');
    let quarter = -1;
    const show = (n, msg) => {
      const p = file.size ? Math.floor(100 * n / file.size) : 100;
      bar.style.width = p + '%'; prog.setAttribute('aria-valuenow', String(p));
      pct.textContent = msg || (fmt(n) + ' of ' + fmt(file.size));
      const q_ = Math.floor(p / 25);
      if (!msg && q_ > quarter && p < 100) { quarter = q_; say(file.name + ': ' + (p ? q_ * 25 + ' percent' : 'uploading'), false, tag); }
    };
    const fail = m => { show(at_, m); row.classList.add('err'); say(file.name + ': ' + m, true); return false; };
    let at_ = 0;
    busy++;
    try {
      let r, j;
      try { r = await fetch(zone.dataset.status + '?' + q, { headers: { 'X-File-Name': name } }); j = await r.json(); }
      catch (e) {                                   // not JSON: the app restarted and is locked, or signed out
        return fail('Signed out or locked: sign in, then drop the files again.'); }
      if (!j.ok) return fail(j.message || 'Refused');
      let at = at_ = j.received, piece = j.piece, tries = 0;
      show(at);
      while (at < file.size) {
        const end = Math.min(at + piece, file.size);
        try {
          r = await fetch(zone.dataset.upload + '?' + q + '&offset=' + at, { method: 'POST', body: file.slice(at, end),
            headers: { 'X-File-Name': name, 'Content-Type': 'application/octet-stream' } });
          j = await r.json();
        } catch (e) { j = null; }
        if (j && (j.ok || r.status === 409) && typeof j.received === 'number') {
          if (!j.ok && j.received === at) await new Promise(res => setTimeout(res, 1000));   // busy elsewhere: wait, then go on
          at = at_ = j.received; tries = 0; show(at); continue; }
        if (j && j.message) return fail(j.message);
        if (++tries > 5) return fail('Stopped. Drop the file again to resume.');
        show(at, 'Connection lost, retrying...');
        await new Promise(res => setTimeout(res, 1500 * tries));
        try { j = await (await fetch(zone.dataset.status + '?' + q, { headers: { 'X-File-Name': name } })).json(); if (j.ok) at = at_ = j.received; } catch (e) {}
      }
      show(file.size, 'Sealed'); row.classList.add('ok'); say(file.name + ': sealed');
      if (j && j.note) notes.add(j.note);             // what the upload settled ("Cull and Edit marked done: ...")
      return true;
    } finally { busy--; flying.delete(tag); }
  }
  const notes = new Set();
  const SMALL = 8 << 20;                 // one upload piece (photostore.PIECE): sent in one request
  const RAW_EXT = /\.(3fr|ari|arw|bay|braw|cr2|cr3|crw|dcr|dng|erf|fff|iiq|k25|kdc|mef|mos|mrw|nef|nrw|orf|ori|pef|raf|raw|rw2|rwl|sr2|srf|srw|x3f)$/i;
  async function take(zone, list) {
    let ok = 0;
    const odd = list.filter(f => !RAW_EXT.test(f.name));
    if (zone.dataset.kind === 'raw' && odd.length && !confirm((odd.length === list.length ? (list.length === 1 ? 'This doesn\'t' : 'These don\'t')
        : odd.length + ' of these don\'t') + ' look like RAW files (' + odd.slice(0, 3).map(f => f.name).join(', ') + (odd.length > 3 ? ', ...' : '')
        + '). Edited photos go in Edited exports, which the gallery is built from. Upload them as RAWs anyway?')) return;
    if (list.length > 1) say('Uploading ' + list.length + ' files');
    const pool = new Set();
    const run = f => { const p = send(zone, f).then(r => { if (r) ok++; pool.delete(p); }); pool.add(p); return p; };
    for (const f of list) {
      if (f.size > SMALL) { await Promise.all([...pool]); await run(f); continue; }
      if (pool.size >= 3) await Promise.race([...pool]);
      run(f);
    }
    await Promise.all([...pool]);
    const added = ok + (ok === 1 ? ' file added' : ' files added');
    if (ok) say(added);
    if (ok && !busy) {
      if (document.activeElement && zone.contains(document.activeElement)) document.activeElement.blur();
      const said = [...notes]; notes.clear();
      setTimeout(async () => {
        if (window.laRefresh) await window.laRefresh(); else { location.reload(); return; }
        const z_ = document.getElementById(zone.id), carry = z_ && z_.closest('.ui-todo-carry');
        const row = carry && carry.previousElementSibling && carry.previousElementSibling.matches('.ui-todo') ? carry.previousElementSibling : null;
        const at_ = row ? row.querySelector('.ui-todo-what') : (z_ && z_.closest('.ui-photokind'));
        if (at_) { const n = document.createElement(row ? 'small' : 'p'); n.className = (row ? 'ui-todo-line ' : '') + 'ui-note ui-note-ok ui-added';
          n.setAttribute('role', 'status'); n.textContent = added; at_.appendChild(n); }
        said.forEach(m => { say(m); const z = document.getElementById(zone.id) || document.querySelector('[data-ui-part]');
          if (z) { const n = document.createElement('p'); n.className = 'ui-note ui-note-ok'; n.setAttribute('role', 'status'); n.textContent = m;
            z.after(n); setTimeout(() => n.remove(), 7000); } });
      }, 400);
    }
  }
  const zoneOf = e => e.target && e.target.closest ? e.target.closest('.dropzone') : null;
  document.addEventListener('dragover', e => { const zone = zoneOf(e); if (!zone) return; e.preventDefault(); zone.classList.add('over'); });
  document.addEventListener('dragleave', e => { const zone = zoneOf(e); if (zone) zone.classList.remove('over'); });
  document.addEventListener('drop', e => { const zone = zoneOf(e); if (!zone) return;
    e.preventDefault(); zone.classList.remove('over'); take(zone, [...e.dataTransfer.files]); });
  document.addEventListener('change', e => { const zone = zoneOf(e);
    if (zone && e.target.matches('input[type=file]')) take(zone, [...e.target.files]); });
  document.addEventListener('keydown', e => { const c = e.target.closest && e.target.closest('[data-ui-choose]');
    if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); c.click(); } });
})();

(() => {
  let box = null, list = [], at = 0, from = null, x0 = null;
  const build = () => {
    box = document.createElement('div'); box.className = 'ui-viewer'; box.hidden = true;
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', 'Photo viewer');
    box.innerHTML = '<div class="ui-viewer-bar"><span class="ui-viewer-count" aria-live="polite"></span><span class="ui-viewer-name"></span>'
      + '<a class="ui-btn ui-btn-quiet ui-viewer-dl">Download for editing</a>'
      + '<button type="button" class="ui-btn ui-btn-quiet ui-viewer-close">Close</button></div>'
      + '<div class="ui-viewer-stage"><button type="button" class="ui-btn ui-btn-quiet ui-viewer-prev" aria-label="Previous photo">&lsaquo;</button>'
      + '<img alt=""><span class="ui-viewer-file"><span class="ms" aria-hidden="true">description</span>No picture to show for this file</span><button type="button" class="ui-btn ui-btn-quiet ui-viewer-next" aria-label="Next photo">&rsaquo;</button></div>';
    document.body.appendChild(box);
    box.querySelector('.ui-viewer-close').addEventListener('click', close);
    box.querySelector('.ui-viewer-prev').addEventListener('click', () => go(-1));
    box.querySelector('.ui-viewer-next').addEventListener('click', () => go(1));
    const stage = box.querySelector('.ui-viewer-stage');
    stage.addEventListener('touchstart', e => { x0 = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
    stage.addEventListener('touchend', e => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1); });
    document.addEventListener('keydown', e => {      // on the page, not the box: a button that just went disabled drops focus
      if (box.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
      else if (e.key === 'Tab') {            // focus stays in the viewer while it is open
        const f = [...box.querySelectorAll('a[href], button')].filter(b => !b.disabled && b.offsetParent !== null);
        if (!f.length) return;
        const i = f.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    });
  };
  const show = () => {
    const a = list[at];
    box.classList.remove('ui-viewer-nopic');
    const img = box.querySelector('img'); img.src = a.href; img.alt = a.dataset.name || '';
    box.querySelector('.ui-viewer-name').textContent = a.dataset.name || '';
    box.querySelector('.ui-viewer-count').textContent = (at + 1) + ' of ' + list.length;
    const dl = box.querySelector('.ui-viewer-dl'); if (a.dataset.download) { dl.href = a.dataset.download; dl.hidden = false; } else dl.hidden = true;
    const prev = box.querySelector('.ui-viewer-prev'), next = box.querySelector('.ui-viewer-next'), had = document.activeElement;
    prev.disabled = at === 0; next.disabled = at === list.length - 1;
    if ((had === prev && prev.disabled) || (had === next && next.disabled)) box.querySelector('.ui-viewer-close').focus({ preventScroll: true });
  };
  const go = d => { const n = at + d; if (n < 0 || n >= list.length) return; at = n; show(); };
  function close() {
    if (!box || box.hidden) return;
    box.hidden = true; document.documentElement.classList.remove('ui-viewer-on');
    if (from && from.isConnected) from.focus({ preventScroll: true });
  }
  document.addEventListener('click', e => {
    const m = e.target.closest && e.target.closest('[data-ui-viewer-more]');
    if (!m || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    const grid = m.closest('[data-photo-hits]') && m.closest('[data-photo-hits]').querySelector('[data-ui-viewer]');
    if (!grid) return;
    e.preventDefault();
    if (!box) build();
    list = [...grid.querySelectorAll('a.ui-thumb-pic')]; at = Math.max(0, list.findIndex(a => a.closest('[hidden]'))); from = m;
    show(); box.hidden = false; document.documentElement.classList.add('ui-viewer-on');
    box.querySelector('.ui-viewer-close').focus({ preventScroll: true });
  });
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('[data-ui-viewer] a.ui-thumb-pic');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault();
    if (!box) build();
    list = [...a.closest('[data-ui-viewer]').querySelectorAll('a.ui-thumb-pic')]; at = list.indexOf(a); from = a;
    show(); box.hidden = false; document.documentElement.classList.add('ui-viewer-on');
    box.querySelector('.ui-viewer-close').focus({ preventScroll: true });
  });
})();

(() => {
  const size = n => { for (const [u, f] of [['GB', 1 << 30], ['MB', 1 << 20], ['KB', 1 << 10]]) if (n >= f) return (n / f).toFixed(1).replace(/\.0$/, '') + ' ' + u;
    return n + ' bytes'; };
  document.addEventListener('submit', async e => {
    const f = e.target; if (!f.matches || !f.matches('form[data-photo-del]') || !window.fetch) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (f.dataset.busy || !confirm(f.dataset.confirm)) return;
    f.dataset.busy = '1';
    const li = f.closest('.ui-thumb'), grid = li && li.closest('.ui-thumbs'), kind = li && li.closest('.ui-photokind');
    let j = null;
    try { const r = await fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin', headers: { 'X-Fetch': '1' } }); j = await r.json(); }
    catch (_) { j = null; }
    delete f.dataset.busy;
    const note = (msg, bad) => { const n = document.createElement('p'); n.className = 'ui-note' + (bad ? ' ui-note-error' : ' ui-note-ok');
      n.setAttribute('role', bad ? 'alert' : 'status'); n.textContent = msg; (grid || f).before(n); if (!bad) setTimeout(() => n.remove(), 7000); };
    if (!j || !j.ok) { note((j && j.message) || 'No answer from the app. Reload the page to see whether it was deleted.', true); return; }
    const y = scrollY; li.remove();
    const left = grid ? [...grid.querySelectorAll('.ui-thumb')] : [];
    const k_ = li && li.dataset.kind && document.querySelector('[data-photo-n="' + li.dataset.kind + '"]');
    if (k_) {                                 // the Photos section's summary line counts the whole kind (1.5 final look)
      const n = Math.max(0, (+k_.dataset.n || 0) - 1), b = Math.max(0, (+k_.dataset.bytes || 0) - (+li.dataset.size || 0));
      k_.dataset.n = n; k_.dataset.bytes = b;
      k_.textContent = n ? n + (n === 1 ? ' file, ' : ' files, ') + size(b) + ', sealed' : 'none now';
    }
    const n_ = !k_ && kind && kind.querySelector('.ui-photokind-n');
    if (n_) n_.textContent = left.length ? left.length + (left.length === 1 ? ' file, ' : ' files, ') + size(left.reduce((a, t) => a + (+t.dataset.size || 0), 0)) + ', sealed' : 'none now';
    note(j.message);
    scrollTo({ top: y, behavior: 'instant' });
    document.dispatchEvent(new Event('photos-picked'));     // a ticked one deleted on its own: the count follows
  }, true);
})();

(() => {
  const sync = () => document.querySelectorAll('[data-photo-picked]').forEach(row => {
    const f = row.querySelector('form'); if (!f || !f.id) return;
    const n = document.querySelectorAll('input[type=checkbox][name=pick][form="' + f.id + '"]:checked').length;
    if (row.hidden !== !n) row.hidden = !n;
    const b = f.querySelector('button'), nf = f.querySelector('input[name=n]');
    const label = 'Delete the ' + (n === 1 ? 'selected file' : n + ' selected');
    if (b && b.textContent !== label) b.textContent = label;     // only a real change: the observer below sees writes
    if (nf && nf.value !== String(n)) nf.value = String(n);
    f.dataset.confirm = 'Delete the ' + (n === 1 ? 'selected file' : n + ' selected files')
      + '? They are removed from the app and its backup copy. This cannot be undone.';
  });
  document.addEventListener('change', e => { if (e.target.matches && e.target.matches('input[name=pick]')) sync(); });
  document.addEventListener('photos-picked', sync);
  new MutationObserver(() => sync()).observe(document.documentElement, { childList: true, subtree: true });
  sync();
})();

document.addEventListener('error', e => {
  const img = e.target; if (!img || img.tagName !== 'IMG') return;
  const a = img.closest('a.ui-thumb-pic');
  if (a) { const t = document.createElement('a'); t.className = 'ui-thumb-file'; t.href = a.dataset.download || a.href;
    t.title = 'Download ' + (a.dataset.name || ''); t.setAttribute('aria-label', t.title);
    const ext = ((a.dataset.name || '').split('.').pop() || 'file').slice(0, 5).toUpperCase();
    t.innerHTML = '<span class="ms" aria-hidden="true">description</span><span class="ui-thumb-ext"></span>'; t.lastChild.textContent = ext;
    a.replaceWith(t); return; }
  if (img.closest('.ui-viewer')) img.closest('.ui-viewer').classList.add('ui-viewer-nopic');
}, true);

(() => {
  let t = null, n = 0;
  const run = async f => {
    const u = new URL(f.action, location.href); u.hash = '';
    new FormData(f).forEach((v, k) => u.searchParams.set(k, v));
    const mine = ++n;
    let html = null;
    try { const r = await fetch(u, { credentials: 'same-origin' }); if (r.ok) html = await r.text(); } catch (_) { html = null; }
    if (mine !== n || html === null) return;
    const doc = new DOMParser().parseFromString(html, 'text/html'), nw = doc.getElementById('photo-hits'), old = document.getElementById('photo-hits');
    if (nw && old) { const y = scrollY; old.replaceWith(nw); scrollTo({ top: y, behavior: 'instant' }); }
  };
  document.addEventListener('input', e => { const f = e.target.closest && e.target.closest('form[data-photo-find]');
    if (!f) return; clearTimeout(t); t = setTimeout(() => run(f), 250); });
  document.addEventListener('submit', e => { const f = e.target; if (!f.matches || !f.matches('form[data-photo-find]') || !window.fetch) return;
    e.preventDefault(); clearTimeout(t); run(f); });
})();

