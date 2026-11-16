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
    el.textContent = ''; setTimeout(() => { el.textContent = item.msg; setTimeout(next, 1200); }, 60);
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
      show(file.size, 'Sealed'); row.classList.add('ok'); say(file.name + ': sealed'); return true;
    } finally { busy--; flying.delete(tag); }
  }
  async function take(zone, list) {
    let ok = 0;
    const jpg = list.filter(f => /\.(jpe?g|png|heic|tiff?)$/i.test(f.name));
    if (zone.dataset.kind === 'raw' && jpg.length && !confirm(jpg.length + ' of these look like finished images, not RAWs ('
        + jpg.slice(0, 3).map(f => f.name).join(', ') + (jpg.length > 3 ? ', ...' : '') + '). Edited JPEGs go in Edited exports, '
        + 'which the gallery is built from. Upload them as RAWs anyway?')) return;
    if (list.length > 1) say('Uploading ' + list.length + ' files, one at a time');
    for (const f of list) if (await send(zone, f)) ok++;
    if (list.length > 1) say(ok + ' of ' + list.length + ' files sealed');          // one file at a time: steady on a home network
    if (ok && !busy) setTimeout(() => (window.laRefresh ? window.laRefresh() : location.reload()), 400);   // the panel updates in place
  }
  const zoneOf = e => e.target && e.target.closest ? e.target.closest('.dropzone') : null;
  document.addEventListener('dragover', e => { const zone = zoneOf(e); if (!zone) return; e.preventDefault(); zone.classList.add('over'); });
  document.addEventListener('dragleave', e => { const zone = zoneOf(e); if (zone) zone.classList.remove('over'); });
  document.addEventListener('drop', e => { const zone = zoneOf(e); if (!zone) return;
    e.preventDefault(); zone.classList.remove('over'); take(zone, [...e.dataTransfer.files]); });
  document.addEventListener('change', e => { const zone = zoneOf(e);
    if (zone && e.target.matches('input[type=file]')) take(zone, [...e.target.files]); });
})();

