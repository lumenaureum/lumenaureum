(() => {
  document.addEventListener('click', e => {
    const line = e.target.closest && e.target.closest('.ui-tline[data-ui-href]');
    if (!line || e.defaultPrevented || e.target.closest('a, button, input, select, textarea, label, form, details, [data-tip]')) return;
    if (window.getSelection && String(window.getSelection())) return;      // selecting text is not a tap
    location.assign(line.dataset.uiHref);
  });
})();

