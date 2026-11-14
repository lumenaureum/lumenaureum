(() => {
  const fmt = n => n >= 1 << 30 ? (n / (1 << 30)).toFixed(1) + ' GB' : n >= 1 << 20 ? (n / (1 << 20)).toFixed(1) + ' MB' : Math.ceil(n / 1024) + ' KB';
  let busy = 0;
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
    const show = (n, msg) => { bar.style.width = (100 * n / file.size) + '%'; pct.textContent = msg || (fmt(n) + ' of ' + fmt(file.size)); };
    busy++;
    try {
      let r, j;
      try { r = await fetch(zone.dataset.status + '?' + q, { headers: { 'X-File-Name': name } }); j = await r.json(); }
      catch (e) {                                   // not JSON: the app restarted and is locked, or signed out
        show(0, 'Signed out or locked: sign in, then drop the files again.'); row.classList.add('err'); return false; }
      if (!j.ok) { show(0, j.message || 'Refused'); row.classList.add('err'); return false; }
      let at = j.received, piece = j.piece, tries = 0;
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
          at = j.received; tries = 0; show(at); continue; }
        if (j && j.message) { show(at, j.message); row.classList.add('err'); return false; }
        if (++tries > 5) { show(at, 'Stopped. Drop the file again to resume.'); row.classList.add('err'); return false; }
        show(at, 'Connection lost, retrying...');
        await new Promise(res => setTimeout(res, 1500 * tries));
        try { j = await (await fetch(zone.dataset.status + '?' + q, { headers: { 'X-File-Name': name } })).json(); if (j.ok) at = j.received; } catch (e) {}
      }
      show(file.size, 'Sealed'); row.classList.add('ok'); return true;
    } finally { busy--; flying.delete(tag); }
  }
  async function take(zone, list) {
    let ok = 0;
    const jpg = list.filter(f => /\.(jpe?g|png|heic|tiff?)$/i.test(f.name));
    if (zone.dataset.kind === 'raw' && jpg.length && !confirm(jpg.length + ' of these look like finished images, not RAWs ('
        + jpg.slice(0, 3).map(f => f.name).join(', ') + (jpg.length > 3 ? ', ...' : '') + '). Edited JPEGs go in Edited exports, '
        + 'which the gallery is built from. Upload them as RAWs anyway?')) return;
    for (const f of list) if (await send(zone, f)) ok++;          // one file at a time: steady on a home network
    if (ok && !busy) setTimeout(() => (window.laRefresh ? window.laRefresh() : location.reload()), 400);   // the panel updates in place
  }
  document.querySelectorAll('.dropzone').forEach(zone => {
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('over'); take(zone, [...e.dataTransfer.files]); });
    const inp = zone.querySelector('input[type=file]');
    inp && inp.addEventListener('change', () => take(zone, [...inp.files]));
  });
})();

