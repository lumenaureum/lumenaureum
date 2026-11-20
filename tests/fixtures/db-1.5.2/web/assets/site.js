(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var box = document.querySelector(".carousel");
  if (!box) return;
  var imgs = box.querySelectorAll("img"), i = 0, timer = null, held = false, x0 = null, y0 = 0;
  if (imgs.length < 2) return;
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function show(n) {
    imgs[i].classList.remove("on"); imgs[i].setAttribute("aria-hidden", "true");
    i = (n + imgs.length) % imgs.length;
    imgs[i].classList.add("on"); imgs[i].removeAttribute("aria-hidden");
  }
  function restart() {
    clearInterval(timer); timer = null;
    if (!still && !held && !document.hidden) timer = setInterval(function () { show(i + 1); }, 7000);
  }
  function step(d) { show(i + d); restart(); }
  box.querySelector(".prev").addEventListener("click", function () { step(-1); });
  box.querySelector(".next").addEventListener("click", function () { step(1); });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1);
  });
  box.addEventListener("mouseenter", function () { held = true; restart(); });
  box.addEventListener("mouseleave", function () { held = false; restart(); });
  box.addEventListener("focusin", function () { held = true; restart(); });
  box.addEventListener("focusout", function () { held = false; restart(); });
  box.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  box.addEventListener("touchend", function (e) {
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
  }, { passive: true });
  box.addEventListener("contextmenu", function (e) { if (e.target.tagName === "IMG") e.preventDefault(); });
  document.addEventListener("visibilitychange", restart);
  restart();
})();
