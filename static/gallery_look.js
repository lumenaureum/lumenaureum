(() => {
  const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const lum = c => { const f = c.map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]; };
  const contrast = (a, b) => { const la = lum(a), lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05); };
  const mix = (a, b, t) => [0, 1, 2].map(i => Math.floor(a[i] + (b[i] - a[i]) * t + 0.5));
  const hex = c => "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
  const GOLD = [184, 134, 47];
  function palette(bg, tuned) {
    bg = /^#[0-9a-f]{6}$/.test((bg || "").toLowerCase()) ? bg.toLowerCase() : "#f6f3ed";
    const c = rgb(bg), dark = contrast(c, [0, 0, 0]) >= contrast(c, [255, 255, 255]);
    const to = dark ? [20, 17, 14] : [250, 246, 240];
    let r = 0;                                   // the focus ring: the gold, moved toward the text until it is 3:1
    while (r < 100 && contrast(c, mix(GOLD, to, r / 100)) < 3) r++;
    const ring = hex(mix(GOLD, to, r / 100));
    if (tuned && tuned[bg]) return Object.assign({}, tuned[bg], { ring });
    const reach = (start, want, most) => { let k = start;
      while (k < most && contrast(c, mix(c, to, k / 100)) < want) k++;
      return mix(c, to, k / 100); };
    let ink = reach(90, 7, 100);
    if (contrast(c, ink) < 4.5) ink = dark ? [0, 0, 0] : [255, 255, 255];   // a mid gray: only pure black or white reads well enough
    let mute = reach(50, 4.5, 100);
    if (contrast(c, mute) < 4.5 || contrast(c, mute) > contrast(c, ink)) mute = ink;   // no room for both: the text color
    return { bg, ink: hex(ink), mute: hex(mute), line: hex(mix(c, to, 0.1)),
             cell: hex(mix(c, to, 0.06)), edge: hex(mix(c, to, 0.22)), ring };
  }
  if (typeof module !== "undefined") { module.exports = { palette }; return; }      // the test harness (node)
  const f = document.getElementById("gallery-look"), pv = document.getElementById("gl-prev");
  if (!f || !pv) return;
  let tuned = {}; try { tuned = JSON.parse(pv.dataset.palettes || "{}"); } catch (e) {}
  const custom = f.elements.gallery_bg_custom;
  function paint() {
    const font = (f.querySelector("input[name=gallery_font]:checked") || {}).value || "classic";
    const pick = (f.querySelector("input[name=gallery_bg]:checked") || {}).value || "#f6f3ed";
    const p = palette(pick === "custom" ? custom.value : pick, tuned);
    Object.entries(p).forEach(([k, v]) => pv.style.setProperty("--" + k, v));
    pv.className = font;
    const c = rgb(p.bg), dark = contrast(c, [0, 0, 0]) < contrast(c, [255, 255, 255]);   // is_dark() in settings_io.py
    const img = document.getElementById("gl-logo-img"), dl = document.getElementById("gl-dark");
    if (img) { const src = dark ? img.dataset.dark : img.dataset.light; img.hidden = !src; if (src && img.getAttribute("src") !== src) img.src = src; }
    if (dl) dl.hidden = !dark;
    const out = document.getElementById("gl-str-out"), s = document.getElementById("gl-str");
    if (out && s) out.textContent = s.value;
  }
  custom.addEventListener("input", () => { const r = f.querySelector("input[name=gallery_bg][value=custom]"); if (r) r.checked = true; paint(); });
  f.addEventListener("input", paint);
  f.addEventListener("change", paint);
  const lf = document.getElementById("gl-logo");
  if (lf) lf.addEventListener("submit", () => {
    lf.querySelectorAll("input[data-look]").forEach(i => i.remove());
    const add = (n, v) => { const i = document.createElement("input"); i.type = "hidden"; i.name = n; i.value = v; i.dataset.look = "1"; lf.append(i); };
    add("look_posted", "1");
    new FormData(f).forEach((v, n) => { if (n !== "look_posted") add(n, v); });
  });
  paint();
})();

