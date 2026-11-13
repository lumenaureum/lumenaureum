// Steps never overlap: measure each mark as drawn and give it the first row where it clears
// every mark already there; the strip grows a row when it must. Runs again on resize (phone vs desktop widths).
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
