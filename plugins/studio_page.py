import base64
import contextlib
import hashlib
import html as _html
import io
import json
import os
import re
import secrets
import shutil
import threading
import unicodedata
import items
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_DATA = Path(os.environ.get("CRM_DB", _REPO / "data" / "crm.db")).parent
SITE_ROOT = Path(os.environ.get("CRM_SITE_ROOT", _DATA / "site"))
FONTS = _HERE / "gallery_assets" / "fonts"
ASSETS = _HERE / "studio_assets"
SEALED_ASSETS = _HERE / "sealed_assets"
THEME_FONTS = {"elegant": "cormorant-garamond.woff2", "script": "great-vibes.woff2"}

DISPLAY_EDGE = 1100
QUALITY = 86
KEEP_EDGE = 2400
MAX_PHOTOS = 24
MAX_UPLOAD = 40 * 1024 * 1024
TAGLINE_MAX, INTRO_MAX, DESC_MAX, ABOUT_MAX, ALT_MAX = 120, 200, 300, 8000, 200
BACKGROUNDS = (("", "Same as your galleries"), ("#f6f3ed", "Ivory"), ("#ffffff", "White"), ("#262422", "Charcoal"),
               ("#0d0c0b", "Black"))
INQ_CHECK_MIN = 15
GUARD = "gallery_upload"

LOCK = threading.Lock()
JOB = {}
READY = {}

_MARK_SVG = (_REPO / "brand" / "mark-small.svg").read_text().strip() if (_REPO / "brand" / "mark-small.svg").is_file() else ""
CREDIT_HTML = ('<a id="la" href="https://lumenaureum.org" rel="noopener">'
               + re.sub(r"<svg ", '<svg aria-hidden="true" focusable="false" ', _MARK_SVG, count=1)
               + '<span>Site by <b>Lumen Aureum</b><small>lumenaureum.org</small></span></a>')

class SiteProblem(ValueError):
    pass

class Conflict(SiteProblem):

    def __init__(self, names, edited=(), ours=(), declined=()):
        self.names = list(names)
        ed, mine, no = set(edited), [n for n in self.names if n in set(ours) and n not in set(edited)], list(declined)
        ed = [n for n in self.names if n in ed]
        other = [n for n in self.names if n not in set(ed) and n not in set(mine)]
        self.ours, self.declined = mine, no

        def few(ns):
            return ", ".join(ns[:12]) + (" and more" if len(ns) > 12 else "")
        said = []
        if no:
            said.append(("These are client photos" if len(no) > 1 else "This is a client photo") + " on your web host "
                        "that " + ("their clients" if len(no) > 1 else "its client") + " no longer allow"
                        + ("" if len(no) > 1 else "s") + ", put there by an earlier upload; delete "
                        + ("them" if len(no) > 1 else "it") + " on your host: " + few(no) + ".")
        if other:
            said.append("These files are already on your web host and weren't put there by Lumen Aureum, so they are "
                        "left as they are: " + few(other) + ".")
        if mine:
            said.append(("These files" if len(mine) > 1 else "This file") + " on your web host "
                        + ("were" if len(mine) > 1 else "was") + " put there by Lumen Aureum under your earlier web "
                        "host settings, which couldn't be confirmed to reach this same folder, so Lumen Aureum won't write "
                        "over " + ("them" if len(mine) > 1 else "it") + ": " + few(mine) + ".")
        if ed:
            said.append(("These files" if len(ed) > 1 else "This file") + " went up with an earlier upload but "
                        + ("were" if len(ed) > 1 else "was") + " changed on your web host since, so Lumen Aureum won't "
                        "write over " + ("them" if len(ed) > 1 else "it") + ": " + few(ed) + ".")
        super().__init__(" ".join(said) + " Move them aside or delete them on your host, then press Upload again.")

def _pixels(src):

    from PIL import Image, ImageCms, ImageOps
    with Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src) as im:
        if im.format not in ("JPEG", "PNG"):
            raise ValueError("not a JPEG or PNG")
        if im.format == "JPEG":
            im.draft("RGB", (KEEP_EDGE, KEEP_EDGE))
        im.load()
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        if icc and im.mode in ("RGB", "CMYK"):
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:
                pass
        if im.mode == "P" or "A" in im.getbands():
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            im = flat
        im = im.convert("RGB")
        return Image.frombytes("RGB", im.size, im.tobytes())

def _font(px):
    from PIL import ImageFont
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, int(px))
        except OSError:
            pass
    return ImageFont.load_default(int(px))

def copyright_mark(img, text):

    from PIL import Image, ImageDraw
    text = (text or "").strip()
    if not text:
        return img
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = max(12, base.width // 70)
    margin = max(8, base.width // 90)
    f = _font(size)
    d.text((margin, base.height - margin - size), text, font=f, fill=(255, 255, 255, 215),
           stroke_width=max(1, size // 12), stroke_fill=(0, 0, 0, 200))
    return Image.alpha_composite(base, layer).convert("RGB")

def _jpeg(img, quality):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=quality, optimize=True, progressive=True)
    return b.getvalue()

def keep_own(data):

    from PIL import Image
    img = _pixels(data)
    img.thumbnail((KEEP_EDGE, KEEP_EDGE), Image.LANCZOS)
    return _jpeg(img, 92)

def site_photo(data, mark_text=""):

    from PIL import Image
    img = _pixels(data)
    img.thumbnail((DISPLAY_EDGE, DISPLAY_EDGE), Image.LANCZOS)
    img = copyright_mark(img, mark_text)
    return _jpeg(img, QUALITY), img.size

def thumb(data, edge=320):
    from PIL import Image
    img = _pixels(data)
    img.thumbnail((edge, edge), Image.LANCZOS)
    return _jpeg(img, 80)

def clean_favicon(data):

    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if im.format not in ("PNG", "JPEG", "GIF", "ICO") or im.width * im.height > 40_000_000:
        raise ValueError("not an icon")
    im = im.convert("RGBA")
    side = max(im.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    sq = sq.resize((256, 256), Image.LANCZOS)
    b = io.BytesIO()
    sq.save(b, "PNG", optimize=True)
    return b.getvalue()

VETO_NAME = re.compile(r"[^\W_][\w .()+~-]{0,199}", re.UNICODE)
VETO_NUMBERED = re.compile(r"\s*\(photo \d{1,5}\)\s*$", re.I)
VETO_BY_NUMBER = re.compile(r"(?:photo|image|picture|pic|number|no|nr)s?\.?\s*#?\s*\d+|[\d\s._()#-]+")

def _stem(name):

    n = (name or "").strip().lower()
    base, dot, ext = n.rpartition(".")
    if dot and base and re.fullmatch(r"[a-z0-9]{2,5}", ext) and not ext.isdigit():
        n = base
    return n.strip(" ._-")

def _starts_like(name_stem, veto_stem):

    parts = re.findall(r"[^\W_]+", veto_stem)
    if not parts:
        return False
    body = r"[\s._-]*".join(re.escape(p) for p in parts)
    return re.match(r"(?:" + body + r")(?![0-9])", name_stem) is not None

VERSION_TAIL = re.compile(r"(?:[\s._-]*(?:edit(?:ed)?|copy|final|retouch(?:ed)?|v\d{1,3})|[\s._-]+\d{1,3}|\s*\(\d{1,3}\))$")

def _base_stem(stem, have=()):

    known = [b for b in have if b and b != stem and _starts_like(stem, b)]
    if known:
        return min(known, key=len)
    base = stem
    while True:
        m = VERSION_TAIL.search(base)
        if not m or not m.start():
            break
        cut = base[:m.start()].rstrip(" ._-")
        w = VERSION_TAIL.search(cut)
        if not re.search(r"[a-z]", m.group(0)) and not cut[-1:].isdigit() and not (w and re.search(r"[a-z]", w.group(0))):
            break
        if not re.search(r"[^\W_]", cut):
            break
        base = cut
    return base

class Veto(NamedTuple):

    stems: tuple
    unclear: bool

    def hides(self, name):
        if self.unclear:
            return True
        s = _stem(name)
        if not s:
            return bool(self.stems)
        return any(_starts_like(s, v) or _starts_like(v, s) for v in self.stems)

def read_veto(veto_text, names=None):

    text = (veto_text or "").strip()
    if not text:
        return Veto((), False)
    stems, have = [], {_stem(n) for n in (names or ()) if _stem(n)}
    for tok in re.split(r"[,;\n\r]+", text):
        tok = VETO_NUMBERED.sub("", tok).strip()
        if not tok:
            continue
        s = _stem(tok)
        if not VETO_NAME.fullmatch(tok) or not s or VETO_BY_NUMBER.fullmatch(tok.lower()):
            return Veto((), True)
        if names is not None and s not in have:
            return Veto((), True)
        stems.append(s)
        b = _base_stem(s, have)
        if b != s:
            stems.append(b)
    return Veto(tuple(dict.fromkeys(stems)), False)

def vetoed(name, veto_text, names=None):

    return read_veto(veto_text, names).hides(name)

LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https://[^\s()<>\"'\\]{1,300}|mailto:[^\s()<>\"'\\@]{1,64}@[^\s()<>\"'\\@]{1,190})\)")

def _inline(t):

    out, pos = [], 0
    for m in LINK.finditer(t):
        out.append(_html.escape(t[pos:m.start()]))
        out.append(f'<a href="{_html.escape(m.group(2))}">{_html.escape(m.group(1))}</a>')
        pos = m.end()
    out.append(_html.escape(t[pos:]))
    return "".join(out)

def about_blocks(text):

    out = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r", "").strip()):
        lines = [l_.strip() for l_ in block.split("\n") if l_.strip()]
        while lines and lines[0].startswith("## "):
            out.append(("h", _inline(lines.pop(0)[3:].strip())))
        if lines:
            out.append(("lede" if not out else "p", _inline(" ".join(lines))))
    return out

def _slug(t, n):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40].strip("-")
    return t or f"photo-{n}"

CSS = """{font}:root {{ --bg: {bg}; --ink: {ink}; --mute: {mute}; --line: {line}; --cell: {cell}; --edge: {edge}; --ring: {ring};
  --gold: #b8862f; --serif: {serif}; --title: {title}; color-scheme: {scheme}; }}
* {{ box-sizing: border-box; }}
html {{ background: #141210; }}           /* below a short page: the footer's color, so no empty band shows */
html, body {{ margin: 0; color: var(--ink);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; -webkit-text-size-adjust: 100%; }}
body {{ background: var(--bg); }}
a {{ color: inherit; }}
a:focus-visible, button:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 3px; }}
.skip {{ position: absolute; left: -9999px; }} .skip:focus {{ left: 12px; top: 12px; z-index: 5; background: var(--bg); padding: 6px 10px; }}
header {{ padding: 40px 20px 26px; max-width: 1100px; width: 100%; margin: 0 auto; text-align: center; }}
header h1 {{ margin: 0 0 10px; font: 400 clamp(30px, 4.4vw, 46px)/1.15 var(--title); letter-spacing: .01em; }}
header h1 a {{ text-decoration: none; }}
.rule {{ width: 56px; height: 1px; background: var(--gold); margin: 0 auto 16px; border: 0; }}
.tagline {{ margin: 0; color: var(--mute); font-size: 13px; letter-spacing: .16em; text-transform: uppercase; text-wrap: balance; }}
.intro {{ margin: 10px 0 0; font: 400 17px/1.5 var(--serif); letter-spacing: .03em; text-wrap: balance; }}
nav {{ margin-top: 18px; display: flex; flex-wrap: wrap; gap: 4px 24px; justify-content: center; font-size: 12.5px;
  letter-spacing: .16em; text-transform: uppercase; }}
nav a {{ color: var(--mute); text-decoration: none; padding: 4px 0; border-bottom: 1px solid transparent; }}
nav a:hover, nav a[aria-current] {{ color: var(--ink); border-bottom-color: var(--gold); }}
#logo {{ display: block; max-height: 72px; max-width: min(320px, 70vw); width: auto; height: auto; }}
header.two {{ display: grid; grid-template-columns: auto 1px minmax(0, 1fr); column-gap: 28px; align-items: center;
  max-width: 980px; text-align: left; }}
header.two #brand {{ grid-column: 1; justify-self: end; }}
header.two .rule {{ grid-column: 2; width: 1px; height: 100%; min-height: 90px; margin: 0; align-self: stretch; }}
header.two .words {{ grid-column: 3; }} header.two nav {{ justify-content: flex-start; }}
@media (max-width: 640px) {{ header.two {{ display: block; text-align: center; }}
  header.two #brand {{ display: inline-block; margin-bottom: 18px; }}
  header.two .rule {{ width: 56px; height: 1px; min-height: 0; margin: 0 auto 16px; }} header.two nav {{ justify-content: center; }} }}
main {{ flex: 1 0 auto; width: 100%; max-width: 1140px; margin: 0 auto; padding: 0 20px 28px; }}
.carousel {{ position: relative; margin: 0 auto; width: 100%; aspect-ratio: {ratio}; }}
.carousel img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; opacity: 0;
  transition: opacity 1.2s ease; -webkit-user-drag: none; }}
.carousel img.on {{ opacity: 1; }}
.carousel button {{ display: none; position: absolute; top: 50%; transform: translateY(-50%); width: 44px; height: 44px;
  border: 0; border-radius: 50%; background: rgba(12, 10, 8, .38); color: #fff; font: 300 30px/1 -apple-system, sans-serif;
  cursor: pointer; opacity: 0; transition: opacity .3s; }}
.js .carousel button {{ display: block; }}
.carousel:hover button, .carousel button:focus-visible {{ opacity: .9; }}
@media (hover: none) {{ .carousel button {{ opacity: .55; }} }}
.carousel .prev {{ left: 10px; }} .carousel .next {{ right: 10px; }}
@media (prefers-reduced-motion: reduce) {{ .carousel img {{ transition: none; }} }}
.text {{ max-width: 38rem; margin: 0 auto; font-size: 17px; line-height: 1.75; }}
.text .lede {{ font: 400 21px/1.6 var(--serif); margin: 0 0 1.6em; }}
.text h2 {{ font-size: 12px; font-weight: 600; letter-spacing: .3em; text-transform: uppercase; color: var(--mute); margin: 2em 0 .4em; }}
.text p {{ margin: 0 0 1em; }}
.text a {{ text-decoration-color: var(--gold); text-underline-offset: 3px; }}
.ask {{ text-align: center; margin: 28px 0 0; }}
.btn, .inq button {{ display: inline-block; font: 600 14px/1 -apple-system, BlinkMacSystemFont, sans-serif; padding: 12px 24px;
  border-radius: 22px; border: 0; background: #e3aa48; color: #1a1308; text-decoration: none; cursor: pointer; letter-spacing: .02em; }}
.btn:hover, .inq button:hover {{ background: #ebb862; }} .inq button:disabled {{ opacity: .6; cursor: default; }}
.inq {{ margin: 1.4em 0 0; }} .inq[hidden] {{ display: none; }}
.inq p {{ margin: 0 0 14px; }} .inq .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }}
@media (max-width: 560px) {{ .inq .two {{ grid-template-columns: 1fr; }} }}
.inq label {{ display: block; font-size: 13px; letter-spacing: .06em; color: var(--mute); margin-bottom: 4px; }}
.inq label span {{ letter-spacing: 0; }}
.inq input, .inq select, .inq textarea {{ width: 100%; font: inherit; font-size: 16px; padding: 9px 11px; color: var(--ink);
  background: var(--cell); border: 1px solid var(--edge); border-radius: 8px; }}
.inq input:focus-visible, .inq select:focus-visible, .inq textarea:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 1px; }}
.inq .hp {{ position: absolute; left: -10000px; width: 1px; height: 1px; overflow: hidden; }}
.inq .send {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }} .inq .sub {{ font-size: 13px; color: var(--mute); }}
.note {{ font-size: 14px; color: var(--mute); border-left: 3px solid var(--gold); padding: 2px 10px; }}
#inq-msg {{ font: 400 19px/1.5 var(--serif); }} #inq-msg.bad {{ font: inherit; color: #c9463d; }}
#inq-msg[hidden], #inq-alt[hidden] {{ display: none; }}
.already {{ margin-top: 2em; color: var(--mute); }}
.back {{ margin-top: 2.4em; text-align: center; font-size: 13px; letter-spacing: .16em; text-transform: uppercase; }}
.back a {{ text-decoration: none; color: var(--mute); }} .back a:hover {{ color: var(--ink); }}
footer {{ background: #141210; color: #b9ad9a; padding: 26px 20px 30px; text-align: center; font-size: 13px; }}
#contact {{ margin-bottom: 8px; color: #e9e1d2; }}
#contact a {{ color: #e9e1d2; text-decoration: none; border-bottom: 1px solid #4a4237; }} #contact a:hover {{ color: #fff; }}
#contact span {{ display: inline-block; }}
#contact span + span::before {{ content: "\\00b7"; margin: 0 10px; color: #6e6455; }}
#addr {{ margin: 0 0 14px; }}
#rights {{ margin: 0 0 14px; font-size: 12px; color: #8f8474; }}
#la {{ display: inline-flex; align-items: center; gap: 10px; color: #b9ad9a; text-decoration: none; letter-spacing: .03em; text-align: left; }}
#la svg {{ width: 32px; height: 32px; }} #la b {{ color: #f0dfb4; font-weight: 600; }} #la:hover b {{ color: #f6dfa6; }}
#la small {{ display: block; font-size: 11px; color: #8f8472; letter-spacing: .08em; }}
.lost {{ min-height: 100vh; display: grid; place-items: center; padding: 8vh 16px; text-align: center; }}
.lost .box {{ width: 100%; max-width: 32rem; }}
.vf {{ position: relative; width: 100%; aspect-ratio: 3 / 2; }}
.vf i {{ position: absolute; width: 12%; height: 18%; border: 0 solid var(--edge); }}
.vf .tl {{ top: 0; left: 0; border-top-width: 2px; border-left-width: 2px; }}
.vf .tr {{ top: 0; right: 0; border-top-width: 2px; border-right-width: 2px; }}
.vf .bl {{ bottom: 0; left: 0; border-bottom-width: 2px; border-left-width: 2px; }}
.vf .br {{ bottom: 0; right: 0; border-bottom-width: 2px; border-right-width: 2px; }}
.vf .af {{ top: 50%; left: 50%; width: 5%; height: 7.5%; transform: translate(-50%, -50%); border: 2px solid #c9463d; }}
.readout {{ margin-top: 14px; font: 14px/1.4 ui-monospace, "SF Mono", Menlo, Consolas, monospace; letter-spacing: .12em; color: var(--mute); }}
.lost h1 {{ font: 400 clamp(24px, 4vw, 32px)/1.2 var(--title); margin: 5vh 0 0; letter-spacing: .04em; }}
.lost p {{ margin: .8em 0 0; color: var(--mute); }}
.lost a {{ display: inline-block; margin-top: 2em; text-decoration: none; border-bottom: 1px solid var(--gold);
  letter-spacing: .16em; font-size: 13px; text-transform: uppercase; }}
"""

SCRIPT = """(function () {
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
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{{ title }}</title>
{% if description %}<meta name="description" content="{{ description }}">
{% endif %}<meta name="author" content="{{ owner }}">
{% if draft %}<meta name="robots" content="noindex, nofollow">
{% elif noindex %}<meta name="robots" content="noindex">
{% endif %}{% if canonical %}<link rel="canonical" href="{{ canonical }}">
<meta property="og:url" content="{{ canonical }}">
{% endif %}<meta property="og:type" content="{{ og_type }}">
<meta property="og:title" content="{{ og_title }}">
{% if description %}<meta property="og:description" content="{{ description }}">
{% endif %}{% if og_image %}<meta property="og:image" content="{{ og_image }}">
<meta name="twitter:card" content="summary_large_image">
{% endif %}{% if favicon %}<link rel="icon" href="{{ root }}favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="{{ root }}apple-touch-icon.png">
{% endif %}<link rel="stylesheet" href="{{ root }}assets/site.css">
{% if script %}<script src="{{ root }}assets/site.js" defer></script>
{% endif %}{% if page == 'contact' and form %}<script src="{{ root }}assets/inquiry.js?v={{ form.js_v }}" defer></script>
{% endif %}</head>
<body>
{% if lost %}<main class="lost"><div class="box">
  <div class="vf" role="img" aria-label="An empty camera viewfinder"><i class="tl"></i><i class="tr"></i><i class="bl"></i><i class="br"></i><i class="af"></i></div>
  <div class="readout" aria-hidden="true">1/404 &nbsp; f/4.04 &nbsp; ISO 404</div>
  <h1>Lens cap on?</h1>
  <p>The page you were looking for isn&rsquo;t here.</p>
  <a href="{{ home }}">Back to {{ 'the photographs' if has_photos else 'the home page' }}</a>
</div></main>
{% else %}<a class="skip" href="#main">Skip to the content</a>
<header class="{{ 'two' if logo }}">
{% if logo %}  <a id="brand" href="{{ home }}"><img id="logo" src="{{ root }}assets/logo.png" alt="{{ name }}" width="{{ logo.w }}" height="{{ logo.h }}"></a>
  <hr class="rule">
{% endif %}  <div class="words">
    <h1>{% if page == 'home' %}{{ name }}{% else %}<a href="{{ home }}">{{ name }}</a>{% endif %}</h1>
{% if not logo %}    <hr class="rule">
{% endif %}{% if tagline %}    <p class="tagline">{{ tagline }}</p>
{% endif %}{% if intro and page == 'home' %}    <p class="intro">{{ intro }}</p>
{% endif %}    <nav aria-label="Site"><a href="{{ home }}"{{ ' aria-current="page"' | safe if page == 'home' }}>{{ 'Photographs' if has_photos else 'Home' }}</a>{% if has_about %}<a href="{{ root }}about/"{{ ' aria-current="page"' | safe if page == 'about' }}>About</a>{% endif %}{% if contact_page %}<a href="{{ root }}contact/"{{ ' aria-current="page"' | safe if page == 'contact' }}>Contact</a>{% endif %}</nav>
  </div>
</header>
<main id="main">
{% if page == 'home' %}{% if photos %}<figure class="carousel" aria-label="Photographs">
{% for p in photos %}  <img src="{{ p.src }}" alt="{{ p.alt }}" width="{{ p.w }}" height="{{ p.h }}"{% if loop.first %} class="on"{% else %} aria-hidden="true" decoding="async"{% endif %}>
{% endfor %}{% if photos|length > 1 %}  <button type="button" class="prev" aria-label="Previous photo">&lsaquo;</button><button type="button" class="next" aria-label="Next photo">&rsaquo;</button>
{% endif %}</figure>
{% endif %}{% if form %}<p class="ask"><a class="btn" href="{{ root }}contact/">Send an inquiry</a></p>
{% endif %}{% elif page == 'about' %}<div class="text">
{% for kind, h in about %}{% if kind == 'h' %}<h2>{{ h | safe }}</h2>
{% else %}<p{{ ' class="lede"' | safe if kind == 'lede' }}>{{ h | safe }}</p>
{% endif %}{% endfor %}<p class="back"><a href="{{ home }}">&larr; {{ 'Photographs' if has_photos else 'Home' }}</a></p>
</div>
{% elif page == 'contact' %}<div class="text">
<h2>Send an inquiry</h2>
{% if form %}<p>Ask about a session, a date, or anything else. {{ owner }} will write back or call.</p>
{% if form_off %}<p class="note">Preview: sending is off. The form works once the site is on your web host.</p>
{% endif %}<form id="inq" class="inq" hidden novalidate data-key="{{ form.key }}" data-work="{{ form.work }}" data-post="{{ root }}inquiry.php"{% if form_off %} data-off="1"{% endif %}
  data-thanks="Thanks. Your inquiry is on its way to {{ owner }}." data-fail="It couldn't be sent just now.{% if email %} Please write to {{ email }} instead.{% endif %}">
  <div class="two"><p><label for="inq-name">Your name</label><input id="inq-name" name="name" maxlength="200" autocomplete="name" required></p>
  <p><label for="inq-email">Email</label><input id="inq-email" name="email" type="email" maxlength="254" autocomplete="email"></p></div>
  <div class="two"><p><label for="inq-phone">Phone <span>(optional)</span></label><input id="inq-phone" name="phone" type="tel" maxlength="40" autocomplete="tel"></p>
  <p><label for="inq-date">Preferred date <span>(optional)</span></label><input id="inq-date" name="date" type="date" min="{{ year }}-01-01" max="2100-12-31"></p></div>
  <p><label for="inq-type">What kind of session</label><select id="inq-type" name="type"><option value="">Not sure yet</option>{% for k, l in form.types %}<option value="{{ k }}">{{ l }}</option>{% endfor %}</select></p>
  <p><label for="inq-message">Message</label><textarea id="inq-message" name="message" rows="6" maxlength="3000"></textarea></p>
  <p class="hp" aria-hidden="true"><label for="inq-website">Leave this empty</label><input id="inq-website" name="website" tabindex="-1" autocomplete="off"></p>
  <p class="send"><button type="submit">Send</button> <span class="sub">Name, and an email or a phone number, please.</span></p>
</form>
<p id="inq-msg" role="status" hidden></p>
{% endif %}<p id="inq-alt">{% if email %}Write to <a href="mailto:{{ email }}">{{ email }}</a>{% if phone %}, or call <a href="tel:{{ phone_link }}">{{ phone }}</a>{% endif %}.{% elif phone %}Call <a href="tel:{{ phone_link }}">{{ phone }}</a>.{% endif %}</p>
<p class="already">Already a client? Your gallery opens from the private link in your letter or email.{% if email %} Lost it? Write to <a href="mailto:{{ email }}">{{ email }}</a>.{% endif %}</p>
<p class="back"><a href="{{ home }}">&larr; {{ 'Photographs' if has_photos else 'Home' }}</a></p>
</div>
{% endif %}</main>
<footer>
  <div id="contact"><span>{{ owner }}</span>{% if email %}<span><a href="mailto:{{ email }}">{{ email }}</a></span>{% endif %}{% if phone %}<span><a href="tel:{{ phone_link }}">{{ phone }}</a></span>{% endif %}</div>
{% if address %}  <div id="addr">{{ address }}</div>
{% endif %}  <div id="rights">&copy; {{ year }} {{ owner }}. All rights reserved.</div>
{% if credit %}  {{ credit | safe }}
{% endif %}</footer>
{% endif %}</body>
</html>
"""

class Built(NamedTuple):
    out_dir: Path
    files: list
    photos: list

def _env():
    import jinja2
    return jinja2.Environment(autoescape=True, keep_trailing_newline=True)

def build_site(out_dir, conf, photos):

    from PIL import Image
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_dir.parent / f".{out_dir.name}.{secrets.token_hex(4)}.building"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "assets").mkdir(parents=True)
    files, pub = [], []

    def put(name, data):
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        files.append(name)

    try:
        items, used = [], set()
        for n, ph in enumerate(photos, 1):
            raw = ph["data"]() if callable(ph["data"]) else ph["data"]
            jpg, (w, h) = site_photo(raw, ph.get("mark") or "")
            del raw
            base = _slug(ph.get("alt") or "", n)
            name = f"photos/{base}-{hashlib.sha256(jpg).hexdigest()[:8]}.jpg"
            while name in used:
                name = name[:-4] + "-2.jpg"
            used.add(name)
            put(name, jpg)
            items.append(dict(name=name, alt=(ph.get("alt") or "").strip(), w=w, h=h))
            pub.append((ph.get("id"), name))
        pal = conf["palette"]
        font = THEME_FONTS.get(conf.get("font") or "")
        serif = '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif'
        face = ""
        if font:
            shutil.copyfile(FONTS / font, tmp / "assets" / "font.woff2")
            files.append("assets/font.woff2")
            fam = "Site Script" if conf.get("font") == "script" else "Site Serif"
            face = f'@font-face {{ font-family: "{fam}"; font-weight: 400; font-display: swap; src: url(font.woff2) format("woff2"); }}\n'
        title_f = f'"Site Script", {serif}' if conf.get("font") == "script" else (f'"Site Serif", {serif}' if font else serif)
        body_f = f'"Site Serif", {serif}' if conf.get("font") == "elegant" else serif
        own = conf.get("own_font") or {}
        own_css = ""
        if own.get(400):
            face = ""
            import settings_io
            weights = dict(zip((400, 300), settings_io.own_font_weights(own[400], own.get(300))))
            for w_ in (400, 300):
                if own.get(w_):
                    put(f"assets/own-{w_}.woff2", own[w_])
                    face += ('@font-face { font-family: "Site Own"; font-weight: %s; font-display: swap; '
                             'src: url(own-%d.woff2) format("woff2"); }\n' % (weights[w_], w_))
            title_f = body_f = f'"Site Own", {serif}'
            own_css = ('\nbody, nav, .tagline, .btn, .inq button, .inq input, .inq select, .inq textarea, footer '
                       '{ font-family: "Site Own", Georgia, serif; }\n'
                       + ('.text, .intro, .text .lede { font-weight: 300; }\n' if own.get(300) else ""))
        if items:
            r_ = max(0.8, min(2.0, items[0]["w"] / items[0]["h"]))
            ratio = f"{round(r_ * 1000)} / 1000"
        else:
            ratio = "3 / 2"
        put("assets/site.css", CSS.format(font=face, serif=body_f, title=title_f, ratio=ratio,
                                          scheme="dark" if conf.get("dark") else "light", **pal) + own_css)
        put("assets/site.js", SCRIPT)
        logo = None
        if conf.get("logo"):
            try:
                lg = Image.open(io.BytesIO(conf["logo"]))
                if lg.width * lg.height > 4 * 600 * 240:
                    raise ValueError("logo too large")
                lg = lg.convert("RGBA")
                b = io.BytesIO()
                Image.frombytes("RGBA", lg.size, lg.tobytes()).save(b, "PNG", optimize=True)
                put("assets/logo.png", b.getvalue())
                logo = dict(w=lg.width, h=lg.height)
            except Exception:
                logo = None
        fav = None
        if conf.get("favicon"):
            try:
                im = Image.open(io.BytesIO(conf["favicon"])).convert("RGBA")
                b = io.BytesIO()
                im.save(b, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
                put("favicon.ico", b.getvalue())
                bg = Image.new("RGBA", (180, 180), tuple(int(pal["bg"][i:i + 2], 16) for i in (1, 3, 5)) + (255,))
                bg.alpha_composite(im.resize((180, 180), Image.LANCZOS))
                b = io.BytesIO()
                bg.convert("RGB").save(b, "PNG", optimize=True)
                put("apple-touch-icon.png", b.getvalue())
                fav = True
            except Exception:
                fav = None
        draft = bool(conf.get("draft"))
        site_url, site_path = conf.get("site_url") or "", conf.get("site_path") or "/"
        link_url = "" if draft else site_url
        about = about_blocks(conf.get("about") or "")
        phone = (conf.get("phone") or "").strip()
        email = conf.get("email") or ""
        inq = conf.get("inquiry")
        form_off = draft or bool(conf.get("preview"))
        inq_js = (ASSETS / "inquiry.js").read_text()

        form = dict(inq, work=inq.get("work", INQ_WORK), js_v=hashlib.sha256(inq_js.encode()).hexdigest()[:8]) \
            if inq and (conf.get("php") or form_off) else None
        common = dict(name=conf["name"], owner=conf.get("owner") or conf["name"], tagline=conf.get("tagline") or "",
                      intro=conf.get("intro") or "", email=email, phone=phone,
                      phone_link=re.sub(r"[^0-9+]", "", phone), address=conf.get("address") or "",
                      year=conf.get("year") or date.today().year, credit=CREDIT_HTML if conf.get("credit") else "",
                      logo=logo, favicon=fav, has_photos=bool(items), has_about=bool(about),
                      description=conf.get("description") or "",
                      og_image=(link_url + items[0]["name"]) if link_url and items else "", draft=draft,
                      form=form, form_off=form_off, contact_page=bool(form or email or phone))
        tpl = _env().from_string(PAGE)
        name = conf["name"]
        full = name + (f" \u00b7 {conf['tagline']}" if conf.get("tagline") else "")
        pages = [("index.html", "home", "", dict(title=full, og_title=full, og_type="website", canonical=link_url))]
        if about:
            pages.append(("about/index.html", "about", "../", dict(title=f"About {name}", og_title=f"About {name}",
                                                                    og_type="profile", canonical=link_url and link_url + "about/")))
        if common["contact_page"]:
            pages.append(("contact/index.html", "contact", "../", dict(title=f"Send an inquiry \u00b7 {name}",
                          og_title=f"Send an inquiry \u00b7 {name}", og_type="website",
                          canonical=link_url and link_url + "contact/")))
        for fname, page, root, extra in pages:
            ph_ = [dict(it, src=root + it["name"]) for it in items]
            put(fname, tpl.render(page=page, root=root, home=root or "./", photos=ph_, about=about,
                                  script=page == "home" and len(items) > 1, lost=False, noindex=False, **common, **extra))
        if form:
            put("assets/inquiry.js", inq_js)
            if not form_off:
                put("sealed.php", (SEALED_ASSETS / "sealed.php").read_text())
                put("inquiry.php", inquiry_php_source(inq.get("salt") or secrets.token_hex(16), form["work"],
                                                      inq.get("kid") or kid_hex(inq["key"]), bool(inq.get("shared"))))
                put(f"{INQ_DIR}/.htaccess", (ASSETS / "inquiries.htaccess").read_text())
                put(f"{INQ_DIR}/index.html", "")

        lost_root = "" if draft else (conf.get("preview_base") or "") if conf.get("preview") else site_path
        put("404.html", tpl.render(page="404", root=lost_root, home=lost_root or "./", photos=[], about=[], script=False,
                                   lost=True, noindex=True, title=f"Not found \u00b7 {name}", og_title=f"Not found \u00b7 {name}",
                                   og_type="website", canonical="", **{k: v for k, v in common.items() if k != "og_image"},
                                   og_image=""))
        if not draft:
            put("robots.txt", "User-agent: *\nAllow: /\n"
                + (f"Disallow: {site_path}{INQ_DIR}/\n" if form and not form_off else "")
                + (f"Sitemap: {site_url}sitemap.xml\n" if site_url else ""))
            if site_url:
                locs = [site_url] + ([site_url + "about/"] if about else []) \
                    + ([site_url + "contact/"] if common["contact_page"] else [])
                put("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                    + "".join(f"  <url><loc>{_html.escape(u)}</loc></url>\n" for u in locs) + "</urlset>\n")
            if conf.get("htaccess"):
                put(".htaccess", f"ErrorDocument 404 {site_path}404.html\n")
        old = out_dir.parent / f".{out_dir.name}.old"
        shutil.rmtree(old, ignore_errors=True)
        if out_dir.exists():
            out_dir.rename(old)
        tmp.rename(out_dir)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return Built(out_dir, sorted(files), pub)

def clear_build(name):

    shutil.rmtree(SITE_ROOT / name, ignore_errors=True)
    for p in SITE_ROOT.glob(f".{name}.*") if SITE_ROOT.is_dir() else []:
        shutil.rmtree(p, ignore_errors=True)

INQ_MAX = 16384

INQ_NAME = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{32}\.las$")
INQ_TMP = re.compile(r"^\.[0-9a-f]{32}\.tmp$")
INQ_RATE = ".rate.php"
INQ_HOUSE = (".rate", INQ_RATE, "index.html")
INQ_MIN_MS = 3000
INQ_WORK = 17
INQ_PER_CHECK = 200
INQ_MANY_A_DAY = 10
INQ_CUT = 4000
EMAIL_OK = re.compile(r"[^\s\"'<>\\=;&?#%@]{1,64}@[^\s\"'<>\\=;&?#%@]+\.[^\s\"'<>\\=;&?#%@]+")

def work_ok(body, w_hex, bits):

    try:
        w = bytes.fromhex(w_hex)
    except (TypeError, ValueError):
        return False
    if len(w) != 8:
        return False
    h = int.from_bytes(hashlib.sha256(hashlib.sha256(body).digest() + w).digest()[:4], "big")
    return bits <= 0 or h >> (32 - min(bits, 32)) == 0

def solve_work(body, bits):

    inner, i = hashlib.sha256(body).digest(), 0
    while True:
        w = i.to_bytes(8, "big")
        if bits <= 0 or int.from_bytes(hashlib.sha256(inner + w).digest()[:4], "big") >> (32 - min(bits, 32)) == 0:
            return w.hex()
        i += 1

def inquiry_php_source(salt, work=INQ_WORK, kid="", shared=False):

    if not re.fullmatch(r"[0-9a-f]{16,64}", salt or "") or not 0 <= int(work) <= 32 or not re.fullmatch(r"[0-9a-f]{16}", kid or ""):
        raise ValueError("bad inquiry salt, work or key ID")
    src = (ASSETS / "inquiry.php").read_text()
    out = re.sub(r"(?m)^\$SALT = '[^'\n]*';", f"$SALT = '{salt}';", src, count=1)
    out = re.sub(r"(?m)^\$WORK = \d+;", f"$WORK = {int(work)};", out, count=1)
    out = re.sub(r"(?m)^\$KID = '[^'\n]*';", f"$KID = '{kid}';", out, count=1)
    out = re.sub(r"(?m)^\$SHARED = \d;", f"$SHARED = {1 if shared else 0};", out, count=1)
    if out.count(f"$SALT = '{salt}';") != 1 or out.count(f"$WORK = {int(work)};") != 1 or out.count(f"$KID = '{kid}';") != 1 \
            or len(re.findall(r"(?m)^\$SHARED = [01];", out)) != 1:
        raise ValueError("inquiry.php has no salt, work, key ID or proxy line")
    return out

def kid_hex(public_b64):

    import sealed
    return sealed.kid_of(base64.urlsafe_b64decode(public_b64 + "=" * (-len(public_b64) % 4))).hex()

def inquiry_keys(pem):

    from cryptography.hazmat.primitives import serialization
    import sealed
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {sealed.kid_of(raw): k}

def new_inquiry_key():

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return pem, inquiry_public(pem)

def inquiry_public(pem):

    from cryptography.hazmat.primitives import serialization
    k = serialization.load_pem_private_key(pem.encode(), password=None)
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

def seal_inquiry(public_b64, fields):

    import sealed
    site_raw = base64.urlsafe_b64decode(public_b64 + "=" * (-len(public_b64) % 4))
    fields = dict(fields)
    if "id" not in fields:
        fields["id"] = secrets.token_hex(16)
    elif fields["id"] is None:
        del fields["id"]
    return sealed.seal_studio("inquiry", site_raw, json.dumps(fields).encode())

def open_inquiry(pem, blob):

    import sealed
    if not isinstance(blob, (bytes, bytearray)) or len(blob) > INQ_MAX:
        raise ValueError("not an inquiry")
    try:
        data = json.loads(sealed.open_studio("inquiry", inquiry_keys(pem), bytes(blob)))
    except Exception as e:
        raise ValueError("not an inquiry") from e
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("not an inquiry")
    return data

def clean_inquiry(data, types):

    def txt(k, n, one_line=True):
        v = data.get(k)
        v = v if isinstance(v, str) else ""
        v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200e\u200f\u2028-\u202e\u2060-\u206f\ufeff]", "", v)
        v = " ".join(v.split()) if one_line else "\n".join(" ".join(l_.split()) for l_ in v.replace("\r", "").split("\n")).strip()
        return v[:n]
    out = dict(name=txt("name", 200), email=txt("email", 254), phone=txt("phone", 40), type=txt("type", 60),
               date=txt("date", 10), message=txt("message", INQ_CUT + 1, one_line=False))
    if len(out["message"]) > INQ_CUT:
        out["message"], out["cut"] = out["message"][:INQ_CUT], True
    if out["email"] and not EMAIL_OK.fullmatch(out["email"]):
        out["email"] = ""
    if out["type"] not in dict(types):
        out["type"] = ""
    try:
        d_ = date.fromisoformat(out["date"])
        out["date"] = d_.isoformat() if 1900 <= d_.year <= 2100 else ""
    except ValueError:
        out["date"] = ""
    t = data.get("t")
    if (isinstance(data.get("hp"), str) and data["hp"].strip()) or not isinstance(t, (int, float)) or t < INQ_MIN_MS:
        return out, "spam"
    if not out["name"] or not (out["email"] or out["phone"]):
        return out, "incomplete"
    return out, ""

NAME_OK = re.compile(r"^(?:(?:photos|assets|about|galleries|contact)/)?[A-Za-z0-9][A-Za-z0-9._-]{0,120}$|^(?:inquiries/)?\.htaccess$|^inquiries/index\.html$")
INQ_DIR = "inquiries"

SITE_TOP_NAMES = frozenset({"photos", "assets", "about", "contact", INQ_DIR, "index.html", "404.html", "sealed.php",
                            "inquiry.php", "robots.txt", "sitemap.xml", ".htaccess", "favicon.ico",
                            "apple-touch-icon.png"})

def reserved_folder(name):

    return (name or "").strip("/").lower() in SITE_TOP_NAMES
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".jpg": "image/jpeg", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
        ".xml": "application/xml", ".woff2": "font/woff2", ".htaccess": "text/plain", ".php": "application/x-httpd-php"}

def top_files(host):

    if host is None:
        return []
    if host.kind == "local":
        try:
            with os.scandir(host.root) as it:
                return [(e.name, e.stat(follow_symlinks=False).st_mtime, e.is_file(follow_symlinks=False)) for e in it]
        except FileNotFoundError:
            return []
    if host.kind == "sftp":
        import stat as st_

        def ls(sf):
            try:
                return [(e.filename, e.st_mtime or 0, st_.S_ISREG(e.st_mode or 0)) for e in sf.listdir_attr(host.root or ".")]
            except IOError:
                return []
        return host._run(ls)
    return []

def site_target(gallery_cfg, gallery_base):

    from urllib.parse import urlsplit
    if not gallery_cfg:
        raise SiteProblem("Set up online galleries first (Settings, Online galleries): the studio page goes to the same "
                          "web host, in the folder above your galleries.")
    u = urlsplit((gallery_base or "").strip())
    if u.scheme != "https" or not u.hostname:
        raise SiteProblem("The gallery address in Online galleries must be set, starting with https://.")
    segs = [s for s in u.path.split("/") if s]
    if not segs:
        raise SiteProblem("Your galleries are at the top of your web address, so there is no room for the studio page "
                          "above them. Galleries need their own folder, such as https://example.com/g/.")
    folder = segs[-1]
    cfg = dict(gallery_cfg)
    t = cfg.get("type")
    if t == "sftp":
        root = (cfg.get("root") or "").rstrip("/")
        parent, _, last = root.rpartition("/")
        if root.startswith("/") and not parent:
            raise SiteProblem("Your galleries' folder is at the top of the server's disk; the studio page can't go above it.")
        cfg["root"] = parent
    elif t == "local":
        p = os.path.normpath(cfg.get("path") or "")
        last, cfg["path"] = os.path.basename(p), os.path.dirname(p)
    elif t == "s3":
        pre = (cfg.get("prefix") or "").strip("/")
        parent, _, last = pre.rpartition("/")
        cfg["prefix"] = parent
    else:
        raise SiteProblem("The web host isn't one the studio page knows.")
    if not last:
        raise SiteProblem("Your galleries are at the top of your web host, so there is no room for the studio page "
                          "above them. Galleries need their own folder, such as g/.")
    if last != folder:
        raise SiteProblem(f"Your galleries' folder on the host ({last}/) and the end of the gallery address ({folder}/) "
                          "differ, so where your site's top folder is can't be worked out safely.")
    site_url = f"https://{u.netloc}/" + "".join(s + "/" for s in segs[:-1])
    return cfg, site_url, folder

def open_host(gallery_cfg, site_cfg):

    import sys as _sys
    if str(_REPO) not in _sys.path:
        _sys.path.insert(0, str(_REPO))
    from vault import backends as VB
    if site_cfg.get("type") == "local":
        VB.from_config(dict(gallery_cfg, public=True))
        return VB.LocalBackend(site_cfg["path"], public=True, marked=False)
    return VB.from_config(dict(site_cfg, public=True))

def _gallery_mod():

    import sys as _sys
    if str(_REPO) not in _sys.path:
        _sys.path.insert(0, str(_REPO))
    from plugins import gallery as G
    return G

def host_fingerprint(site_cfg):

    return _gallery_mod().fingerprint_of(site_cfg)

def legacy_host_fingerprint(site_cfg):

    cfg = {k: v for k, v in (site_cfg or {}).items() if k not in ("secret_key", "password", "private_key", "known_hosts", "host_key")}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]

def same_site_place(cur_host, cur_cfg, old_host, old_cfg):

    G = _gallery_mod()
    if G.host_identity(cur_cfg) == G.host_identity(old_cfg):
        return True
    name = f".la-probe-{secrets.token_hex(8)}.txt"
    with cur_host.session():
        cur_host.put_stream(name, io.BytesIO(b"lumen-aureum probe"), content_type="text/plain")
    try:
        with old_host.session():
            return old_host.stat(name) is not None
    finally:
        try:
            with cur_host.session():
                cur_host.delete(name)
        except Exception:
            pass

def check_name(name, folder):

    if not NAME_OK.match(name or "") or ".." in name.split("/"):
        raise SiteProblem(f"refused to write {name!r}")
    if folder and name.split("/", 1)[0].lower() == folder.lower():
        raise SiteProblem(f"The galleries' folder ({folder}/) has the same name as one of the site's own; nothing was "
                          "written.")
    return name

def _order(n):

    return (n == "index.html", n.endswith(".html"), n != "sealed.php", n == "inquiry.php", n)

class _Sha:
    def __init__(self):
        self.h = hashlib.sha256()

    def write(self, b):
        self.h.update(b)
        return len(b)

def host_copy(host, name, size, sha):

    have = host.stat(name)
    if have is None:
        return "gone"
    if have != size:
        return "changed"
    if not sha:
        return "same"
    w = _Sha()
    host.get_stream(name, w)
    return "same" if w.h.hexdigest() == sha else "changed"

def _owned(v):
    return v if isinstance(v, tuple) else (v, None)

def publish(host, out_dir, owned, folder, kind="", on_put=lambda name, size, sha: None, on_forget=lambda name: None,
            progress=lambda done, total: None, hold=(), on_left=None, edited=()):

    out_dir = Path(out_dir)
    files = sorted((str(p.relative_to(out_dir)).replace(os.sep, "/") for p in out_dir.rglob("*") if p.is_file()), key=_order)
    for n in files:
        check_name(n, folder)
    for n in owned:
        check_name(n, folder)
    notes, left, removed = [], [], []
    if kind == "s3" and ".htaccess" in files:
        files.remove(".htaccess")
    with host.session():
        theirs = [n for n in files if n not in owned and host.stat(n) is not None]
        if ".htaccess" in theirs:
            theirs.remove(".htaccess")
            files.remove(".htaccess")
            notes.append("htaccess")
        if theirs:
            raise Conflict(theirs, edited)
        progress(0, len(files))
        put = []
        for i, n in enumerate(files):
            if n in owned and host_copy(host, n, *_owned(owned[n])) == "changed":
                left.append(n)
                progress(i + 1, len(files))
                continue
            put.append(n)
            with open(out_dir / n, "rb") as f:
                size, sha = host.put_stream(n, f, content_type=MIME.get(Path(n).suffix or n), cache_control="no-cache")
            on_put(n, size, sha)
            progress(i + 1, len(files))
        gone = [n for n in owned if n not in files and n not in hold]
        for n in gone:
            st = host_copy(host, n, *_owned(owned[n]))
            if st == "changed":
                left.append(n)
                if on_left:
                    on_left(n)
                    continue
            elif st == "same":
                host.delete(n)
                removed.append(n)
            on_forget(n)
        for d in sorted({n.rsplit("/", 1)[0] for n in removed if "/" in n}, key=lambda d_: -d_.count("/")):
            host.remove_dir(d + "/")
    return dict(put=put, removed=removed, left=left, notes=notes)

def take_down(host, owned, folder, on_forget=lambda name: None, on_left=None):

    removed, left = [], []
    with host.session():
        for n in sorted(owned, key=lambda n_: (n_ == "index.html",)):
            check_name(n, folder)
            st = host_copy(host, n, *_owned(owned[n]))
            if st == "changed":
                left.append(n)
                if on_left:
                    on_left(n)
                    continue
            elif st == "same":
                host.delete(n)
                removed.append(n)
            on_forget(n)
        for d in sorted({n.rsplit("/", 1)[0] for n in removed if "/" in n}, key=lambda d_: -d_.count("/")):
            host.remove_dir(d + "/")
    return removed, left

SETTINGS_PAGE = """{% extends "base.html" %}
{% block title %}Studio page - Lumen Aureum{% endblock %}
{% block body %}
<h1>Studio page</h1>
{% if request.args.get('saved') %}<p class="ok-msg" style="color:var(--gold)">Saved.</p>{% endif %}
<form method="post" action="{{ url_for('studio_page_switch') }}" class="card f" style="max-width:720px;margin-top:4px">
  <p style="font-size:13px;margin:0">A small public website for your studio, in the look of your galleries: a few photos you pick, an About page, and how to reach you, with an inquiry form. It goes to your web host, in the folder above your galleries (your galleries stay as they are), only when you press Upload. No tracking, nothing loaded from anywhere else.</p>
  <label class="row" style="gap:6px"><input type="checkbox" name="use" value="1" data-autosubmit {{ 'checked' if on }}>Use the studio page</label>
  <noscript><div><button class="quiet">Save</button></div></noscript>
</form>
{% if on %}
<form method="post" action="{{ url_for('studio_page_save') }}" class="card f" style="max-width:720px" id="words">
  <h2>Words</h2>
  <div class="f2"><div><label class="l" for="sp-name">Name at the top</label><input id="sp-name" name="site_name" maxlength="120" value="{{ v.site_name }}" placeholder="{{ d.name }}"></div>
    <div><label class="l" for="sp-tag">Line under the name</label><input id="sp-tag" name="site_tagline" maxlength="{{ L.tag }}" value="{{ v.site_tagline }}" placeholder="Photographer · Your town"></div></div>
  <div><label class="l" for="sp-intro">One line about your work (home page)</label><input id="sp-intro" name="site_intro" maxlength="{{ L.intro }}" value="{{ v.site_intro }}" placeholder="Portraits · Families · Weddings"></div>
  <div><label class="l" for="sp-desc">Short description, for search results and link previews</label><input id="sp-desc" name="site_description" maxlength="{{ L.desc }}" value="{{ v.site_description }}"></div>
  <div><label class="l" for="sp-about">About</label><textarea id="sp-about" name="site_about" rows="12" maxlength="{{ L.about }}">{{ v.site_about }}</textarea>
    <div class="sub" style="font-size:12.5px;margin-top:4px">A blank line starts a new paragraph; the first one is shown larger. A line starting with ## is a small heading. A link: [words](https://example.com). Leave it empty for no About page.</div></div>
  <div class="sub" style="font-size:13px">Contact: {% if contact %}{{ contact | join(' · ') }}{% else %}<span class="mute">none yet</span>{% endif %}. These come from <a class="go" href="{{ url_for('settings_view') }}">Settings, General &rsaquo;</a></div>
  <div class="row" style="gap:18px;flex-wrap:wrap">
    <label class="row" style="gap:6px"><input type="checkbox" name="site_show_phone" value="1" {{ 'checked' if v.site_show_phone != '0' }}>Show the phone number</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="site_show_address" value="1" {{ 'checked' if v.site_show_address != '0' }}>Show the mailing address</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="site_inquiries" value="1" {{ 'checked' if v.site_inquiries != '0' }}>An inquiry form</label></div>
  <div class="sub" style="font-size:12.5px">The inquiry form is sealed in the visitor's browser so only this app can read it; your web host keeps it until the app collects it and adds it as a new inquiry. It needs a host that runs PHP (SFTP web hosting); elsewhere the Contact page shows your email instead.</div>
  <div><label class="l" for="sp-bg">Background</label><select id="sp-bg" name="site_bg">{% for c_, l_ in backgrounds %}<option value="{{ c_ }}"{{ ' selected' if v.site_bg == c_ }}>{{ l_ }}</option>{% endfor %}</select>
    <div class="sub" style="font-size:12.5px;margin-top:4px">The font and colors follow your <a class="go" href="{{ url_for('settings_view') }}#gallery-look">client galleries' look &rsaquo;</a></div></div>
  <div class="row" style="gap:10px;flex-wrap:wrap"><label class="row" style="gap:6px"><input type="checkbox" name="site_mark" value="1" {{ 'checked' if v.site_mark != '0' }}>Copyright mark on each photo</label>
    <label class="l" for="sp-markname" style="margin:0">Name in the mark</label><input id="sp-markname" name="site_mark_name" maxlength="80" value="{{ v.site_mark_name }}" placeholder="{{ d.owner }}" style="width:220px"></div>
  <div class="sub" style="font-size:12.5px">Small, in the bottom-left corner: &copy; {{ d.year }} {{ v.site_mark_name or d.owner }}, the year each photo first goes online.</div>
  <div><button class="primary">Save</button></div>
</form>

<div class="card f" style="max-width:720px" id="photos">
  <h2>Photos <span class="mute" style="font-weight:400;font-size:13px">{{ photos|length }} of up to {{ max_photos }}, shown in this order</span></h2>
  {% if photos %}<table class="data sp-photos"><tbody>{% for p in photos %}<tr>
    <td style="width:110px"><img src="{{ url_for('studio_page_thumb', pid=p.id) }}" alt="" style="max-width:100px;max-height:70px;display:block"></td>
    <td><form method="post" action="{{ url_for('studio_page_photo', pid=p.id) }}" class="row" style="gap:6px">
      <input name="alt" maxlength="{{ L.alt }}" value="{{ p.alt }}" aria-label="What the photo shows (for people who can't see it)" placeholder="What it shows" class="grow">
      <button class="quiet" name="do" value="alt">Save</button></form>
      <div class="sub" style="font-size:12px">{% if p.source == 'session' %}{{ p.who }}: {{ p.name }}{% if p.problem %} <span style="color:var(--red)">{{ p.problem }}</span>{% endif %}{% else %}Your upload: {{ p.name }}{% endif %}{% if not p.alt %} <span style="color:var(--amber, #b07a12)">No description yet: people who can't see the photo get nothing.</span>{% endif %}</div></td>
    <td style="width:150px;white-space:nowrap"><form method="post" action="{{ url_for('studio_page_photo', pid=p.id) }}" class="inline">
      <button class="ghost" name="do" value="up" aria-label="Move up"{{ ' disabled' if loop.first }}>&uarr;</button><button class="ghost" name="do" value="down" aria-label="Move down"{{ ' disabled' if loop.last }}>&darr;</button>
      <button class="ghost" name="do" value="remove">Remove</button></form></td></tr>{% endfor %}</tbody></table>
  {% else %}<p class="mute" style="font-size:13px;margin:0">No photos yet.</p>{% endif %}
  {% if photos|length < max_photos %}
  <form method="post" action="{{ url_for('studio_page_add_own') }}" enctype="multipart/form-data" class="row" style="gap:8px;flex-wrap:wrap">
    <label class="l" for="sp-own" style="margin:0">Your own photos</label><input id="sp-own" type="file" name="photos" accept="image/jpeg,image/png" multiple required style="width:auto">
    <button class="quiet">Add</button></form>
  <div class="sub" style="font-size:12.5px">JPEG or PNG, up to 40 MB at a time. Only the pixels are kept, at a size for the web: nothing of the file's camera data or location.</div>
  <form method="get" action="{{ url_for('studio_page') }}#photos" class="row" style="gap:8px;flex-wrap:wrap">
    <label class="l" for="sp-sess" style="margin:0">From a client session</label>
    <select id="sp-sess" name="from" class="grow"><option value="">Sessions whose client allows portfolio use</option>{% for s_ in sessions %}<option value="{{ s_.id }}"{{ ' selected' if pick and pick.id == s_.id }}>{{ s_.label }}</option>{% endfor %}</select>
    <button class="quiet">Choose photos</button></form>
  {% if pick %}<form method="post" action="{{ url_for('studio_page_add_session', sid=pick.id) }}" class="f" style="border-left:3px solid var(--gold);padding-left:12px">
    <div class="sub" style="font-size:13px">{{ pick.label }}. Up to {{ pick.cap }} photo{{ '' if pick.cap == 1 else 's' }} from one session, as your agreement says{% if pick.used %} ({{ pick.used }} on the page already){% endif %}. Photos the client asked you not to show aren't listed.{% if not pick.picks_in %} The client hasn't sent picks from the gallery yet, so hasn't had the chance to say no to any photo.{% endif %}</div>
    {% if pick.unclear %}<p style="font-size:13px;margin:0;color:var(--amber, #b07a12)">The client's Don't show note for {{ pick.label }} isn't specific file names, so none of its photos are offered. To offer the others, write the note as the names of the photos not to show (on the session page, Delivery).</p>
    {% elif pick.files %}<div class="f" style="gap:4px;max-height:260px;overflow:auto">{% for f_ in pick.files %}<label class="row" style="gap:6px;font-size:13px"><input type="checkbox" name="file" value="{{ f_.id }}"{{ ' disabled' if f_.on }}>{{ f_.name }}{% if f_.suggested %} <span class="pill green">client's favorite</span>{% endif %}{% if f_.on %} <span class="mute">on the page</span>{% endif %}</label>{% endfor %}</div>
    <div><button class="quiet"{{ ' disabled' if pick.used >= pick.cap }}>Add these</button></div>{% else %}<p class="mute" style="font-size:13px;margin:0">No photos from this session can be shown.</p>{% endif %}
  </form>{% endif %}
  {% endif %}
</div>

<div class="card f" style="max-width:720px" id="icon">
  <h2>Icon</h2>
  <p class="mute" style="font-size:13px;margin:0">The small picture a browser shows on the tab. A square PNG works best; ICO, GIF and JPEG are fine too.</p>
  {% if favicon %}<div><img src="{{ url_for('studio_page_favicon_png') }}" alt="Your icon" width="32" height="32"></div>{% endif %}
  <form method="post" action="{{ url_for('studio_page_favicon') }}" enctype="multipart/form-data" class="row" style="gap:8px;flex-wrap:wrap">
    <input type="file" name="icon" accept="image/png,image/jpeg,image/gif,image/x-icon,image/vnd.microsoft.icon,.ico" required aria-label="Icon for the browser tab" style="width:auto"><button class="quiet">Upload</button></form>
  {% if favicon %}<form method="post" action="{{ url_for('studio_page_favicon') }}"><button class="ghost" name="remove" value="1">Remove the icon</button></form>{% endif %}
</div>
{% endif %}

{% if review %}<div class="card f" style="max-width:720px" id="inquiries">
  <h2>Website inquiries to review <span class="mute" style="font-weight:400;font-size:13px">{{ review|length }}</span></h2>
  <p class="mute" style="font-size:13px;margin:0">Sent from the form on your site. Add makes a new client with an Inquiry session; Add to a client the inquiry matches gives that client a new Inquiry session instead; Spam removes it. Nothing becomes a client record until you add it.</p>
  {% for i_ in review %}<div style="border-left:3px solid var(--gold);padding-left:12px">
    <div><b>{{ i_.name }}</b>{% if i_.email %} &middot; {{ i_.email }}{% endif %}{% if i_.phone %} &middot; {{ i_.phone }}{% endif %} <span class="mute" style="font-size:12.5px">{{ i_.received }}</span></div>
    <div class="sub" style="font-size:13px">Kind of session: {{ i_.type_label or 'not given' }}{% if i_.date %}, preferred date {{ i_.date }}{% endif %}</div>
    {% for m_ in i_.matches %}<div class="sub" style="font-size:13px;color:var(--amber, #b07a12)">Matches {{ m_.name }}: the same email or phone number</div>{% endfor %}
    {% if i_.twin %}<div class="sub" style="font-size:13px;color:var(--amber, #b07a12)">The same email or phone number as another inquiry waiting here</div>{% endif %}
    {% if i_.message %}<div style="font-size:13.5px;white-space:pre-wrap">{{ i_.message }}</div>{% endif %}
    {% if i_.cut %}<div class="sub" style="font-size:12.5px">The message was longer than {{ inq_cut }} characters; only the first {{ inq_cut }} were kept.</div>{% endif %}
    <form method="post" action="{{ url_for('studio_page_review') }}" class="row" style="gap:8px;margin-top:4px;flex-wrap:wrap"><input type="hidden" name="id" value="{{ i_.id }}">
      {% if not i_.type_label %}<label class="l" for="rv-type-{{ i_.id }}" style="margin:0">Kind of session</label><select id="rv-type-{{ i_.id }}" name="type" style="width:auto">{% for k_, l_ in types %}<option value="{{ k_ }}">{{ l_ }}</option>{% endfor %}</select>{% endif %}
      <button class="quiet" name="do" value="add">{{ 'Add as a new client' if i_.matches else 'Add' }}</button>{% for m_ in i_.matches %}<button class="quiet" name="add_to" value="{{ m_.hid }}">Add to {{ m_.name }}</button>{% endfor %}<button class="ghost" name="do" value="spam">Spam</button></form>
  </div>{% endfor %}
  {% if review|length > 1 %}{% set fresh = review | rejectattr('matches') | list %}<div class="row" style="gap:8px;flex-wrap:wrap">
    {% if fresh %}<form method="post" action="{{ url_for('studio_page_review') }}" class="inline" data-confirm="Add {{ 'this inquiry' if fresh|length == 1 else 'all ' ~ fresh|length ~ ' inquiries' }} as new clients, each with an Inquiry session?{% if fresh|length < review|length %} Those that match a client you have are left for you to choose.{% endif %}">{% for i_ in fresh %}<input type="hidden" name="id" value="{{ i_.id }}">{% endfor %}<button class="quiet" name="do" value="add_all">Add all {{ fresh|length }}</button></form>{% endif %}
    <form method="post" action="{{ url_for('studio_page_review') }}" class="inline" data-confirm="Remove all {{ review|length }} inquiries as spam? They can't be brought back.">{% for i_ in review %}<input type="hidden" name="id" value="{{ i_.id }}">{% endfor %}<button class="ghost" name="do" value="spam_all">Spam all {{ review|length }}</button></form></div>{% endif %}
</div>
{% endif %}
{% if on or n_files or earlier %}<div class="card f" style="max-width:720px" id="publish">
  {% if on %}<h2>Preview and upload</h2>
  {% if no_alt %}<p style="font-size:13px;margin:0;color:var(--amber, #b07a12)">{{ no_alt }} photo{{ '' if no_alt == 1 else 's' }} on the page {{ 'has' if no_alt == 1 else 'have' }} no description. Add a few words saying what each shows (<a class="go" href="#photos">Photos</a>), for people who can't see them.</p>{% endif %}
  {% if problem %}<p style="font-size:13px;margin:0;color:var(--amber, #b07a12)">{{ problem }}</p>
  {% else %}<p style="font-size:13px;margin:0">Your site: <b>{{ site_url }}</b>, with your galleries at {{ site_url }}{{ folder }}/.</p>{% endif %}
  <div class="row" style="gap:8px;flex-wrap:wrap">
    <form method="post" action="{{ url_for('studio_page_preview_build') }}" target="_blank" class="inline"><button class="quiet">Preview</button></form>
    {% if not problem %}<form method="post" action="{{ url_for('studio_page_upload') }}" class="inline" data-confirm="Put the studio page online at {{ site_url }}? It is built fresh first. Your galleries aren't touched."><button class="primary"{{ ' disabled' if busy }}>Upload</button></form>{% endif %}
  </div>
  {% else %}<h2>Your site is still online</h2>
  <p style="font-size:13px;margin:0">The studio page is switched off, but the files it put on your web host{% if site_url %} ({{ site_url }}){% endif %} are still there{% if inq_live %}, and so is its inquiry form: what visitors send is still collected{% endif %}. {{ 'Take them down below.' if problem else 'Take the site down to remove them.' }}</p>
  {% if problem %}<p style="font-size:13px;margin:0;color:var(--amber, #b07a12)">{{ problem }} Until then, each web host the studio page put files on is listed here.</p>{% endif %}
  {% endif %}
  <div class="sub" style="font-size:13px" role="status">{% if busy %}{{ job.label or 'Working' }}{% if job.total %}: {{ job.done }} of {{ job.total }}{% endif %}
    {% elif state == 'awaiting' %}Waiting for approval. Press {{ 'Upload' if on else 'Take the site down' }} again to ask again.
    {% elif state == 'error' %}<span style="color:var(--red)">{{ error }}</span>
    {% elif state == 'up' %}Online since {{ nice(up_at) }}{% if n_files %} ({{ n_files }} files){% endif %}.
    {% elif state == 'down' %}Taken down {{ nice(up_at) }}.
    {% else %}Not online yet.{% endif %}{% if note %} {{ note }}{% endif %}</div>
  {% for s_ in stale %}<div class="row" style="gap:8px;flex-wrap:wrap"><span class="grow" style="font-size:13px;color:var(--amber, #b07a12)">{{ s_.name }} is a photo its client no longer allows{% if s_.edited %}. It was changed on your web host since it went up, so an upload or a take-down leaves it there: delete it on your host, or remove it here{% else %}{{ ': press Upload to take it off' if on else ': take the site down to remove it' }}{% endif %}.</span>
    {% if s_.edited and not busy %}<form method="post" action="{{ url_for('studio_page_remove_file') }}" class="inline" data-confirm="Remove {{ s_.name }} from your web host, whatever was changed in it there?"><input type="hidden" name="name" value="{{ s_.name }}"><button class="ghost">Remove it anyway</button></form>{% endif %}</div>{% endfor %}
  {% for h_ in earlier %}<div class="row" style="gap:8px;flex-wrap:wrap"><span class="grow" style="font-size:13px;color:var(--amber, #b07a12)">{% if problem %}On {{ h_.label }}: {{ h_.n }} file{{ '' if h_.n == 1 else 's' }} the studio page put there{% else %}Left on an earlier web host, {{ h_.label }}: {{ h_.n }} file{{ '' if h_.n == 1 else 's' }} the studio page put there before your web host settings changed to another place{% endif %}{% if h_.stale %}, among them {{ h_.stale|map(attribute='name')|join(', ') }}: {{ 'client photos their clients no longer allow' if h_.stale|length > 1 else 'a client photo its client no longer allows' }}{% endif %}.{% if not h_.known %} Its settings aren't known here: delete the files on that host yourself.{% endif %}</span>
    {% if not busy %}{% if h_.known %}<form method="post" action="{{ url_for('studio_page_take_down_earlier') }}" class="inline" data-confirm="Remove the studio page's files from {{ h_.label }}? Only the files Lumen Aureum put there are removed, and only while they are as it put them."><input type="hidden" name="host" value="{{ h_.fp }}"><button class="ghost">Take down there</button></form>{% endif %}
    {% if not problem %}<form method="post" action="{{ url_for('studio_page_forget_host') }}" class="inline" data-confirm="Forget {{ h_.label }} and its list of files? Do this only when that host is gone for good: nothing is removed from it, and Today stops reminding about it."><input type="hidden" name="host" value="{{ h_.fp }}"><button class="quiet">Gone for good</button></form>{% endif %}{% endif %}</div>
    {% for s_ in h_.stale if s_.edited and h_.known and not busy %}<div class="row" style="gap:8px"><span class="grow" style="font-size:13px">{{ s_.name }} on {{ h_.label }} was changed there since it went up, so taking down leaves it.</span><form method="post" action="{{ url_for('studio_page_remove_file') }}" class="inline" data-confirm="Remove {{ s_.name }} from {{ h_.label }}, whatever was changed in it there?"><input type="hidden" name="name" value="{{ s_.name }}"><input type="hidden" name="host" value="{{ h_.fp }}"><button class="ghost">Remove it anyway</button></form></div>{% endfor %}{% endfor %}
  {% if inq_live or (on and inq_on and n_files) %}<form method="post" action="{{ url_for('studio_page_inquiries') }}" class="row" style="gap:8px">
    <span class="grow sub" style="font-size:13px">{% if inq_at %}Inquiries last collected {{ inq_at }}{% if inq_note %}: {{ inq_note }}{% endif %}.{% else %}Inquiries from the form are collected every {{ inq_min }} minutes.{% endif %}</span>
    <button class="quiet">Check for inquiries</button></form>{% endif %}
  {% if n_files and not busy %}<form method="post" action="{{ url_for('studio_page_take_down') }}" data-confirm="Remove the studio page from your web host? Only the files Lumen Aureum put there are removed; your galleries and anything else stay."><button class="ghost">Take the site down</button></form>{% endif %}
</div>
{% endif %}
{% endblock %}
"""

def register(ctx):
    app, q, x, C = ctx.app, ctx.q, ctx.x, ctx.C
    from flask import abort, redirect, render_template_string, request, send_from_directory, session as _fs, url_for

    ctx.migrate("""CREATE TABLE IF NOT EXISTS studio_photos (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
        file_id TEXT, name TEXT DEFAULT '', alt TEXT DEFAULT '', sort INTEGER DEFAULT 0, data BLOB,
        first_year INTEGER, added TEXT);
    CREATE TABLE IF NOT EXISTS studio_files (
        name TEXT NOT NULL, host_fp TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT, session_id INTEGER,
        photo_id TEXT, at TEXT, PRIMARY KEY (name, host_fp));
    CREATE TABLE IF NOT EXISTS studio_inquiries (
        sha256 TEXT PRIMARY KEY, session_id INTEGER, household_id INTEGER, received TEXT, outcome TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS studio_inquiry_review (
        id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, fields TEXT NOT NULL, received TEXT NOT NULL);""")
    ctx.migrate(items.TABLE)
    ctx.on_unlock(items.migrate_hook(ctx.connect))

    @ctx.on_unlock
    def _restamp_hosts():

        import logging
        try:
            from vault import web as VW
            hc = VW.config()
            con = ctx.connect()
            try:
                fps = {r_[0] for r_ in con.execute("SELECT DISTINCT host_fp FROM studio_files")}
                if not fps:
                    return
                val = lambda k: (con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or [""])[0] or ""
                try:
                    hosts = json.loads(val("site_hosts") or "{}")
                    marks = json.loads(val("site_left") or "{}")
                except ValueError:
                    hosts, marks = {}, {}
                hosts = hosts if isinstance(hosts, dict) else {}
                marks = marks if isinstance(marks, dict) else {}
                for g_ in [hc.get("gallery_host"), hc.get("gallery_host_off")] + _gallery_mod().earlier_host_cfgs(hc):
                    if not g_:
                        continue
                    try:
                        site_cfg, site_url, folder = site_target(g_, val("gallery_base_url"))
                    except SiteProblem:
                        continue
                    old, new = legacy_host_fingerprint(site_cfg), host_fingerprint(site_cfg)
                    if old in fps and old != new:
                        con.execute("UPDATE OR IGNORE studio_files SET host_fp=? WHERE host_fp=?", (new, old))
                        con.execute("DELETE FROM studio_files WHERE host_fp=?", (old,))
                        for n_, how in (marks.pop(old, None) or {}).items():
                            marks.setdefault(new, {}).setdefault(n_, how)
                        fps = (fps - {old}) | {new}
                    if new in fps and new not in hosts:
                        hosts[new] = dict(site=site_cfg, gallery=dict(g_), url=site_url, folder=folder)
                for k_, v_ in (("site_hosts", hosts), ("site_left", marks)):
                    con.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (k_, json.dumps(v_, separators=(",", ":"))))
                con.commit()
            finally:
                con.close()
        except Exception:
            logging.getLogger(__name__).exception("studio page: restamping the list of uploaded files")

    app.config.setdefault("quiet_endpoints", set()).update({"studio_page_review", "studio_page_remove_file"})

    def setting(k, d=""):
        r_ = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
        return r_["value"] if r_ and r_["value"] is not None else d

    def put_setting(k, v):
        x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

    def on():
        return setting("site_on") == "1"

    def studio_name():
        return (setting("studio_name") or setting("photographer_name") or setting("business_name")).strip()

    def gallery_cfg():
        from vault import web as VW
        return VW.config().get("gallery_host") or {}

    def target():

        return site_target(gallery_cfg(), setting("gallery_base_url"))

    def session_row(sid):
        return q("""SELECT s.*, h.name AS hname FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=?""",
                 (sid,), one=True)

    def session_label(s):
        when = ""
        if s["shoot_date"]:
            try:
                when = ", " + ctx.nice(s["shoot_date"][:10])
            except Exception:
                when = ""
        return ((s["title"] if "title" in s.keys() else "") or s["hname"] or "Session") + f" ({C.SESSION_TYPES.get(s['type'], s['type'])}{when})"

    def file_row(fid):
        return q("SELECT * FROM photo_files WHERE id=? AND state='done' AND kind IN ('export', 'final')", (fid,), one=True)

    def session_veto(s):

        names = [r_["name"] for r_ in q("SELECT name FROM photo_files WHERE session_id=?", (s["id"],))] if s["veto"] else []
        return read_veto(s["veto"], names)

    UNCLEAR = "the client's Don't show note isn't specific file names"

    def why_not(s, f):

        if not s:
            return "the session was deleted"
        if s["portfolio_no"]:
            return "the client declined portfolio use"
        if not f:
            return "the photo is no longer here"
        v = session_veto(s)
        if v.unclear:
            return UNCLEAR
        if v.hides(f["name"]):
            return "the client asked you not to show it"
        return ""

    def cap():
        return max(0, int(getattr(C, "MARKETING_PICKS", 2) or 0))

    def allowed_now(rows):

        ok, skipped, per = [], [], {}
        for r in rows:
            if r["source"] == "own":
                ok.append(r)
                continue
            s, f = session_row(r["session_id"]) if r["session_id"] else None, file_row(r["file_id"])
            why = why_not(s, f)
            if not why and per.get(r["session_id"], 0) >= cap():
                why = "more photos from that session than your agreement allows"
            if why:
                skipped.append((r, why))
                continue
            per[r["session_id"]] = per.get(r["session_id"], 0) + 1
            ok.append(r)
        return ok, skipped

    def eligible_sessions():
        return q("""SELECT s.*, h.name AS hname FROM sessions s JOIN households h ON h.id=s.household_id
                    WHERE COALESCE(s.portfolio_no, 0)=0 AND EXISTS (SELECT 1 FROM photo_files f WHERE f.session_id=s.id
                    AND f.kind IN ('export', 'final') AND f.state='done')
                    ORDER BY COALESCE(s.shoot_date, s.created) DESC LIMIT 300""")

    def photo_rows():
        return q("SELECT id, source, session_id, file_id, name, alt, sort, first_year, added FROM studio_photos ORDER BY sort, added, id")

    def photo_bytes(r):
        if r["source"] == "own":
            d = q("SELECT data FROM studio_photos WHERE id=?", (r["id"],), one=True)
            return bytes(d["data"]) if d and d["data"] else b""
        import photostore
        f = file_row(r["file_id"])
        if not f:
            raise ValueError("photo not found")
        return photostore.plain_bytes(f)

    def inquiry_key():

        pem = setting("site_inquiry_key")
        if not pem:
            pem, _ = new_inquiry_key()
            put_setting("site_inquiry_key", pem)
        return pem

    def host_shared():

        try:
            caps = json.loads(setting("gallery_host_caps") or "{}")
        except ValueError:
            return False
        return bool((caps.get("now") or caps.get("setup") or {}).get("proxy"))

    def inquiry_salt():

        s = setting("site_inquiry_salt")
        if not re.fullmatch(r"[0-9a-f]{32}", s):
            s = secrets.token_hex(16)
            put_setting("site_inquiry_salt", s)
        return s

    def form_types():
        return [(t["key"], t["label"]) for t in C.TYPES if t.get("key") in C.SESSION_TYPES and t.get("kind") != "personal"] \
            or list(C.SESSION_TYPES.items())

    def conf_now(preview=False):
        import settings_io
        look = settings_io.clean_look(setting("gallery_font"), setting("gallery_bg"), "", "", 20)
        bg = setting("site_bg") if setting("site_bg") in [b for b, _ in BACKGROUNDS if b] else look["gallery_bg"]
        logo = settings_io.gallery_logo(bg, setting("logo_png"), setting("logo_dark_png"))
        try:
            site_cfg, site_url, folder = target()
            problem = ""
        except SiteProblem as e:
            site_cfg, site_url, folder, problem = None, "", "", str(e)
        from urllib.parse import urlsplit
        name = (setting("site_name") or studio_name() or "Photography").strip()
        owner = (setting("studio_name") or setting("business_name") or setting("photographer_name") or name).strip()
        addr = ", ".join(l_.strip() for l_ in setting("business_address").splitlines() if l_.strip())
        fav = setting("site_favicon")
        own_font = {}
        if look["gallery_font"] == "own":
            for w_ in (400, 300):
                v_ = setting(f"brand_font_{w_}")
                if v_:
                    own_font[w_] = base64.b64decode(v_)
        email = setting("business_email").strip()
        return dict(name=name, owner=owner, tagline=setting("site_tagline").strip(), intro=setting("site_intro").strip(),
                    description=setting("site_description").strip() or " ".join(
                        t for t in (setting("site_tagline").strip(), setting("site_intro").strip()) if t),
                    about=setting("site_about"), email=email if re.fullmatch(r"[^\s\"'<>\\=;&?#%]{1,64}@[^\s\"'<>\\=;&?#%@]+\.[^\s\"'<>\\=;&?#%@]+", email) else "",
                    phone=setting("business_phone").strip() if setting("site_show_phone") != "0" else "",
                    address=addr if setting("site_show_address") != "0" else "",
                    site_url=site_url, site_path=urlsplit(site_url).path if site_url else "/",
                    logo=base64.b64decode(logo) if logo else None, palette=settings_io.palette(bg),
                    dark=settings_io.is_dark(bg), font=look["gallery_font"], credit=setting("credit_off") != "1",
                    favicon=base64.b64decode(fav) if fav else None, own_font=own_font,
                    year=date.today().year, gallery_folder=folder,
                    htaccess=bool(site_cfg) and site_cfg.get("type") in ("sftp", "local"),
                    mark=setting("site_mark") != "0", mark_name=(setting("site_mark_name") or owner).strip()[:80],
                    problem=problem, site_cfg=site_cfg, preview=preview,
                    preview_base=url_for("studio_page_preview") if preview else "",
                    php=bool(site_cfg) and site_cfg.get("type") in ("sftp", "local"),
                    inquiry=dict(key=inquiry_public(inquiry_key()), kid=kid_hex(inquiry_public(inquiry_key())),
                                 types=form_types(), salt=inquiry_salt(), work=INQ_WORK, shared=host_shared())
                    if setting("site_inquiries") != "0" else None)

    def build(out_name, preview=False):

        conf = conf_now(preview)
        rows = photo_rows()
        ok, skipped = allowed_now(rows)
        photos = [dict(id=r["id"], alt=r["alt"], data=(lambda r=r: photo_bytes(r)),
                       mark=f"\u00a9 {r['first_year'] or date.today().year} {conf['mark_name']}" if conf["mark"] and conf["mark_name"] else "")
                  for r in ok]
        built = build_site(SITE_ROOT / out_name, conf, photos)
        return built, conf, skipped

    ctx.hooks.settings_tabs.append(("Studio page", "studio_page", "your public website"))
    app.jinja_env.globals["SETTINGS_ENDPOINTS"].add("studio_page")

    def has_manifest_rows(fp):
        return q("SELECT COUNT(*) n FROM studio_files WHERE host_fp=?", (fp,), one=True)["n"]

    @app.get("/settings/studio-page", endpoint="studio_page")
    def studio_page():
        v = {k: setting(k) for k in ("site_name", "site_tagline", "site_intro", "site_description", "site_about", "site_bg",
                                      "site_mark", "site_mark_name", "site_show_phone", "site_show_address",
                                      "site_inquiries")}
        rows = photo_rows()
        ok_ids = {r["id"] for r in allowed_now(rows)[0]}
        skipped = dict((r["id"], why) for r, why in allowed_now(rows)[1])
        photos = []
        for r in rows:
            who = ""
            if r["source"] == "session":
                s = session_row(r["session_id"]) if r["session_id"] else None
                who = session_label(s) if s else "A deleted session"
            photos.append(dict(r, who=who, problem=("Not shown: " + skipped[r["id"]]) if r["id"] not in ok_ids else ""))
        pick = None
        sid_ = request.args.get("from", "")
        if sid_.isdigit():
            s = q("""SELECT s.*, h.name AS hname FROM sessions s JOIN households h ON h.id=s.household_id
                     WHERE s.id=? AND COALESCE(s.portfolio_no, 0)=0""", (int(sid_),), one=True)
            if s:
                import photostore
                con = ctx.connect()
                try:
                    fl = [f for k_ in ("final", "export") for f in photostore.files(con, s["id"], k_, done_only=True)]
                finally:
                    con.close()
                veto = session_veto(s)
                fl = [f for f in fl if Path(f["name"]).suffix.lower() in (".jpg", ".jpeg", ".png") and not veto.hides(f["name"])]
                on_ = {r["file_id"] for r in rows if r["source"] == "session"}
                fav = (s["favorites"] or "").lower()
                used = sum(1 for r in rows if r["session_id"] == s["id"])
                pick = dict(id=s["id"], label=session_label(s), cap=cap(), used=used, unclear=veto.unclear,
                            picks_in=bool(s["favorites"] or s["veto"] or s["retouch_picks"]),
                            files=[dict(id=f["id"], name=f["name"] + ("" if f["kind"] == "export" else " (retouched)"),
                                        on=f["id"] in on_, suggested=bool(fav) and f["name"].lower() in fav) for f in fl])
        sessions = [dict(id=s["id"], label=session_label(s)) for s in eligible_sessions()]
        try:
            site_cfg, site_url, folder = target()
            problem = ""
            fp = host_fingerprint(site_cfg)
        except SiteProblem as e:
            site_url, folder, problem, fp = "", "", str(e), ""
        n_files = has_manifest_rows(fp) if fp else 0
        all_stale = stale_photos()
        stale = [r_ for r_ in all_stale if r_["host_fp"] == fp]
        known = site_hosts()
        earlier = [dict(fp=f_, label=host_name(f_), n=len(owned(f_)), known=f_ in known,
                        stale=[r_ for r_ in all_stale if r_["host_fp"] == f_]) for f_ in earlier_fps(fp)]
        contact = [t for t in (setting("business_email").strip(), setting("business_phone").strip(),
                               ", ".join(l_.strip() for l_ in setting("business_address").splitlines() if l_.strip())) if t]
        owner = (setting("studio_name") or setting("business_name") or setting("photographer_name") or "").strip()
        return render_template_string(SETTINGS_PAGE, on=on(), v=v, photos=photos, pick=pick, sessions=sessions,
                                      max_photos=MAX_PHOTOS, backgrounds=BACKGROUNDS, contact=contact,
                                      d=dict(name=studio_name() or "Your name", owner=owner or "Your name", year=date.today().year),
                                      L=dict(tag=TAGLINE_MAX, intro=INTRO_MAX, desc=DESC_MAX, about=ABOUT_MAX, alt=ALT_MAX),
                                      favicon=bool(setting("site_favicon")), problem=problem, site_url=site_url, folder=folder,
                                      review=review_rows(),
                                      no_alt=sum(1 for p_ in photos if p_["id"] in ok_ids and not (p_["alt"] or "").strip()),
                                      busy=JOB.get("state") == "running", job=JOB, state=setting("site_state"),
                                      error=setting("site_error"), up_at=setting("site_at"), n_files=n_files, stale=stale,
                                      earlier=earlier,
                                      note=setting("site_note"), inq_on=setting("site_inquiries") != "0",
                                      inq_live=form_online(), inq_cut=INQ_CUT, types=form_types(),
                                      inq_at=setting("site_inq_at"), inq_note=setting("site_inq_note"), inq_min=INQ_CHECK_MIN)

    back = lambda anchor="": url_for("studio_page") + anchor

    @app.post("/settings/studio-page/switch", endpoint="studio_page_switch")
    def studio_page_switch():
        put_setting("site_on", "1" if request.form.get("use") else "0")
        return redirect(back())

    def clip(v, n):
        v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v or "")
        return v[:n]

    @app.post("/settings/studio-page/save", endpoint="studio_page_save")
    def studio_page_save():
        f = request.form
        one = lambda k, n: " ".join(clip(f.get(k, ""), n).split())
        put_setting("site_name", one("site_name", 120))
        put_setting("site_tagline", one("site_tagline", TAGLINE_MAX))
        put_setting("site_intro", one("site_intro", INTRO_MAX))
        put_setting("site_description", one("site_description", DESC_MAX))
        put_setting("site_about", clip(f.get("site_about", "").replace("\r\n", "\n"), ABOUT_MAX).strip())
        put_setting("site_bg", f.get("site_bg", "") if f.get("site_bg", "") in [b for b, _ in BACKGROUNDS] else "")
        put_setting("site_mark_name", one("site_mark_name", 80))
        for k in ("site_mark", "site_show_phone", "site_show_address", "site_inquiries"):
            put_setting(k, "1" if f.get(k) else "0")
        return redirect(url_for("studio_page", saved=1) + "#words")

    def next_sort():
        r_ = q("SELECT COALESCE(MAX(sort), 0) m FROM studio_photos", one=True)
        return (r_["m"] or 0) + 1

    def count():
        return q("SELECT COUNT(*) n FROM studio_photos", one=True)["n"]

    @app.post("/settings/studio-page/photos/add", endpoint="studio_page_add_own")
    def studio_page_add_own():
        from flask import g as _g
        bad, added = [], 0
        for fs in request.files.getlist("photos"):
            if count() >= MAX_PHOTOS:
                bad.append(f"{fs.filename} (the page holds {MAX_PHOTOS} photos)")
                continue
            try:
                data = keep_own(fs.stream.read(MAX_UPLOAD + 1))
            except Exception:
                bad.append(fs.filename or "a file")
                continue
            nm = re.sub(r"[\x00-\x1f\x7f/\\]", "", fs.filename or "")[:120] or "photo.jpg"
            x("INSERT INTO studio_photos (id, source, name, data, sort, added) VALUES (?,?,?,?,?,?)",
              (secrets.token_hex(8), "own", nm, data, next_sort(), datetime.now().isoformat(timespec="seconds")))
            added += 1
        if bad:
            _fs["notice"] = "Not added (a JPEG or PNG photo is needed): " + ", ".join(bad[:6])
        if not added:
            _g.refused = True
        return redirect(back("#photos"))

    @app.post("/settings/studio-page/photos/from/<int:sid>", endpoint="studio_page_add_session")
    def studio_page_add_session(sid):
        from flask import g as _g
        s = session_row(sid)
        ids = request.form.getlist("file")
        used = q("SELECT COUNT(*) n FROM studio_photos WHERE session_id=?", (sid,), one=True)["n"]
        refused = []
        for fid in ids:
            f = q("SELECT * FROM photo_files WHERE id=? AND session_id=? AND state='done' AND kind IN ('export', 'final')",
                  (fid, sid), one=True)
            why = why_not(s, f)
            if not why and used >= cap():
                why = f"at most {cap()} from one session"
            if not why and count() >= MAX_PHOTOS:
                why = f"the page holds {MAX_PHOTOS} photos"
            if not why and q("SELECT 1 FROM studio_photos WHERE file_id=?", (fid,), one=True):
                continue
            if why:
                refused.append(((f["name"] if f else "a photo") + f": {why}"))
                continue
            x("INSERT INTO studio_photos (id, source, session_id, file_id, name, sort, added) VALUES (?,?,?,?,?,?,?)",
              (secrets.token_hex(8), "session", sid, fid, f["name"], next_sort(), datetime.now().isoformat(timespec="seconds")))
            used += 1
        if refused:
            _fs["notice"] = "Not added: " + "; ".join(refused[:6])
            _g.refused = not (len(ids) > len(refused))
        return redirect(url_for("studio_page", **({"from": sid} if s else {})) + "#photos")

    @app.post("/settings/studio-page/photos/<pid>/edit", endpoint="studio_page_photo")
    def studio_page_photo(pid):
        r = q("SELECT id, sort FROM studio_photos WHERE id=?", (pid,), one=True) or abort(404)
        do = request.form.get("do")
        if do == "alt":
            x("UPDATE studio_photos SET alt=? WHERE id=?", (" ".join(clip(request.form.get("alt", ""), ALT_MAX).split()), pid))
        elif do == "remove":
            x("DELETE FROM studio_photos WHERE id=?", (pid,))
        elif do in ("up", "down"):
            ids = [r_["id"] for r_ in photo_rows()]
            i = ids.index(pid)
            j = i - 1 if do == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for n_, id_ in enumerate(ids, 1):
                    x("UPDATE studio_photos SET sort=? WHERE id=?", (n_, id_))
        return redirect(back("#photos"))

    THUMBS = {}

    def forget_plain():

        READY.clear()
        THUMBS.clear()
        clear_build("ready")
        clear_build("preview")

    import datakey as _dk
    _dk.ON_LOCK.append(forget_plain)
    forget_plain()

    @app.get("/settings/studio-page/photos/<pid>.jpg", endpoint="studio_page_thumb")
    def studio_page_thumb(pid):
        from flask import Response
        r = q("SELECT id, source, session_id, file_id FROM studio_photos WHERE id=?", (pid,), one=True) or abort(404)
        key = (pid, r["file_id"])
        if key not in THUMBS:
            try:
                THUMBS[key] = thumb(photo_bytes(r))
            except Exception:
                abort(404)
            if len(THUMBS) > 64:
                THUMBS.pop(next(iter(THUMBS)))
        return Response(THUMBS[key], mimetype="image/jpeg")

    @app.post("/settings/studio-page/icon", endpoint="studio_page_favicon")
    def studio_page_favicon():
        from flask import g as _g
        if request.form.get("remove"):
            x("DELETE FROM settings WHERE key='site_favicon'")
            return redirect(back("#icon"))
        f = request.files.get("icon")
        try:
            put_setting("site_favicon", base64.b64encode(clean_favicon(f.stream.read(8 * 1024 * 1024))).decode())
        except Exception:
            _fs["notice"] = "That file isn't a picture that can be an icon (PNG, ICO, GIF or JPEG)."
            _g.refused = True
        return redirect(back("#icon"))

    @app.get("/settings/studio-page/icon.png", endpoint="studio_page_favicon_png")
    def studio_page_favicon_png():
        from flask import Response
        v = setting("site_favicon")
        if not v:
            abort(404)
        return Response(base64.b64decode(v), mimetype="image/png")

    @app.post("/settings/studio-page/preview", endpoint="studio_page_preview_build")
    def studio_page_preview_build():
        if not on():
            abort(404)
        try:
            _, _, skipped = build("preview", preview=True)
        except Exception as e:
            app.logger.exception("studio page preview")
            _fs["notice"] = f"The preview couldn't be built: {e}"
            return redirect(back("#publish"))
        if skipped:
            _fs["notice"] = "Left out: " + "; ".join(f"{r['name']} ({why})" for r, why in skipped[:5])
        return redirect(url_for("studio_page_preview"))

    @app.get("/studio-page/preview/", endpoint="studio_page_preview")
    @app.get("/studio-page/preview/<path:p>", endpoint="studio_page_preview_file")
    def studio_page_preview(p=""):
        root = SITE_ROOT / "preview"
        if not root.is_dir():
            return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>No preview yet</title><p style='font:16px system-ui;"
                    "margin:2em'>Press Preview on the Studio page settings to build one.</p>"), 404
        if p == "" or p.endswith("/"):
            p += "index.html"

        if (not NAME_OK.match(p) and p not in ("about/index.html", "galleries/index.html")
                or ".." in p.split("/") or not (root / p).is_file()):
            if not (root / "404.html").is_file():
                abort(404)
            r = send_from_directory(root, "404.html")
            r.status_code, r.headers["Cache-Control"] = 404, "no-store"
            return r
        r = send_from_directory(root, p)
        r.headers["Cache-Control"] = "no-store"
        return r

    def left_marks():

        try:
            v = json.loads(setting("site_left") or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def listed_fps():
        return {r_["host_fp"] for r_ in q("SELECT DISTINCT host_fp FROM studio_files")}

    def mark_left(fp, name, how):
        m = left_marks()
        keep = listed_fps() | {fp}
        m = {k: v for k, v in m.items() if k in keep and isinstance(v, dict)}
        if how:
            m.setdefault(fp, {})[name] = how
        else:
            m.get(fp, {}).pop(name, None)
        put_setting("site_left", json.dumps(m, separators=(",", ":")))

    def stale_photos():

        out, marks = [], left_marks()
        for r in q("SELECT name, host_fp, session_id, photo_id FROM studio_files WHERE session_id IS NOT NULL ORDER BY name"):
            s = session_row(r["session_id"])
            ph = q("SELECT file_id FROM studio_photos WHERE id=?", (r["photo_id"],), one=True) if r["photo_id"] else None
            f = q("SELECT name FROM photo_files WHERE id=?", (ph["file_id"],), one=True) if ph else None
            if not s or s["portfolio_no"] or not ph or not f or session_veto(s).hides(f["name"]):
                out.append(dict(r, edited=(marks.get(r["host_fp"]) or {}).get(r["name"]) == "kept"))
        return out

    def site_hosts():

        try:
            v = json.loads(setting("site_hosts") or "{}")
        except ValueError:
            v = {}
        return {k: h for k, h in v.items() if isinstance(h, dict) and isinstance(h.get("site"), dict)} if isinstance(v, dict) else {}

    def put_hosts(h):
        keep = listed_fps()
        put_setting("site_hosts", json.dumps({k: v for k, v in h.items() if k in keep}, separators=(",", ":")))

    def remember_host(fp, site_cfg, gcfg, site_url, folder):
        h = site_hosts()
        h[fp] = dict(site=dict(site_cfg), gallery=dict(gcfg), url=site_url, folder=folder)
        keep = listed_fps() | {fp}
        put_setting("site_hosts", json.dumps({k: v for k, v in h.items() if k in keep}, separators=(",", ":")))

    def earlier_fps(fp):

        return sorted(f_ for f_ in listed_fps() if f_ != fp)

    def host_name(fp):
        rec = site_hosts().get(fp)
        if not rec:
            return "an earlier web host"
        return (rec.get("url") or "") + " (" + _gallery_mod().host_label(rec["site"]) + ")" if rec.get("url") \
            else _gallery_mod().host_label(rec["site"])

    def adopt(old, fp):

        for r_ in q("SELECT * FROM studio_files WHERE host_fp=?", (old,)):
            now_ = q("SELECT at FROM studio_files WHERE name=? AND host_fp=?", (r_["name"], fp), one=True)
            if now_ and (now_["at"] or "") >= (r_["at"] or ""):
                continue
            x("""INSERT OR REPLACE INTO studio_files (name, host_fp, size, sha256, session_id, photo_id, at)
                 VALUES (?,?,?,?,?,?,?)""", (r_["name"], fp, r_["size"], r_["sha256"], r_["session_id"], r_["photo_id"], r_["at"]))
        x("DELETE FROM studio_files WHERE host_fp=?", (old,))
        m = left_marks()
        for n_, how in (m.pop(old, None) or {}).items():
            m.setdefault(fp, {}).setdefault(n_, how)
        put_setting("site_left", json.dumps(m, separators=(",", ":")))
        h = site_hosts()
        h.pop(old, None)
        put_hosts(h)

    def settle(site_cfg, gcfg, site_url, folder, host=None):

        import logging
        fp = host_fingerprint(site_cfg)
        leg = legacy_host_fingerprint(site_cfg)
        if leg != fp and leg in listed_fps():
            adopt(leg, fp)
        remember_host(fp, site_cfg, gcfg, site_url, folder)
        if host is None:
            return fp
        for ofp in earlier_fps(fp):
            rec = site_hosts().get(ofp)
            names = list(owned(ofp))
            if not rec or not names:
                continue
            try:
                with host.session():
                    seen = any(host.stat(n_) is not None for n_ in names if NAME_OK.match(n_) and ".." not in n_.split("/"))
                if not seen:
                    continue
                same = same_site_place(host, site_cfg, open_host(rec["gallery"], rec["site"]), rec["site"])
            except Exception:
                logging.getLogger(__name__).exception("studio page: is an earlier host the one in use")
                continue
            if same:
                label = host_name(ofp)
                adopt(ofp, fp)
                ctx.audit(f"Studio page: the web host settings changed, but still reach the same folder as {label}; "
                          "its list of uploaded files carries over", who="Lumen Aureum")
        return fp

    def refusal(host, names, fp, edited):

        ours, declined = [], []
        stale = [r_ for r_ in stale_photos() if r_["host_fp"] != fp]
        with host.session():
            for n_ in names:
                for ofp in earlier_fps(fp):
                    v_ = owned(ofp).get(n_)
                    if v_ and host_copy(host, n_, *v_) == "same":
                        ours.append(n_)
                        break
            for r_ in stale:
                if r_["name"] not in declined and NAME_OK.match(r_["name"]) and ".." not in r_["name"].split("/") \
                        and host.stat(r_["name"]) is not None:
                    declined.append(r_["name"])
        return Conflict(names, edited, ours=ours, declined=declined)

    def collect_picks(sids):

        import sqlite3
        sids = sorted({s_ for s_ in sids if s_})
        if not sids:
            return
        try:
            rows = q(f"""SELECT g.*, s.gallery_id, s.id AS sid FROM galleries g JOIN sessions s ON s.id=g.session_id
                         WHERE g.upload_state='up' AND g.removed_at IS NULL AND s.id IN ({",".join("?" * len(sids))})""",
                     tuple(sids))
        except sqlite3.OperationalError:
            return
        if not rows:
            return
        G = _gallery_mod()
        try:
            fp_now = G.host_fingerprint()
        except Exception:
            fp_now = ""
        for r_ in rows:
            gid = r_["gallery_id"]
            if not gid or not G.GID_OK.match(gid) or (r_["host_fp"] and r_["host_fp"] != fp_now) or G.online_download(gid):
                continue
            try:
                if ("sealed_gen" in r_.keys() and not r_["sealed_gen"]) or not G.is_link(gid):

                    import upgrade_140
                    upgrade_140.ensure(ctx, G._G() if hasattr(G, "_G") else G, r_["sid"])
                    gid = q("SELECT gallery_id FROM sessions WHERE id=?", (r_["sid"],), one=True)["gallery_id"]
                blob = G.picks_blob(gid)
            except Exception as e:
                raise SiteProblem("The client's latest picks couldn't be read from your web host, so nothing was "
                                  f"uploaded ({str(e)[:200]}). Try again in a moment.")
            if not blob:
                continue
            try:
                pk = G.open_picks(blob, r_["ekey"] or r_["key"], gid)
            except Exception:
                pk = None
            if pk is None:
                continue

            cur = q("SELECT veto FROM sessions WHERE id=?", (r_["sid"],), one=True)
            have = [v.strip() for v in re.split(r"[,\n]", (cur["veto"] if cur else "") or "") if v.strip()]
            add = [v for v in pk["veto"] if v.strip() and v.strip() not in have]
            if add:
                x("UPDATE sessions SET veto=? WHERE id=?", (", ".join(have + add), r_["sid"]))
                ctx.audit(f"Don't show from the gallery, read before the studio page upload: {len(add)} more",
                          sid=r_["sid"], who="Lumen Aureum")

    def page_sessions():
        return [r_["session_id"] for r_ in q("SELECT DISTINCT session_id FROM studio_photos WHERE session_id IS NOT NULL")]

    def owned(fp):
        return {r["name"]: (r["size"], r["sha256"]) for r in q("SELECT name, size, sha256 FROM studio_files WHERE host_fp=?", (fp,))}

    def edited_names(fp):

        return {n for n, how in (left_marks().get(fp) or {}).items() if how == "let go"}

    def left_on_host(fp):

        def on_left(name):
            with app.app_context():
                r_ = q("SELECT session_id FROM studio_files WHERE name=? AND host_fp=?", (name, fp), one=True)
                if r_ and r_["session_id"] is not None:
                    mark_left(fp, name, "kept")
                else:
                    x("DELETE FROM studio_files WHERE name=? AND host_fp=?", (name, fp))
                    mark_left(fp, name, "let go")
        return on_left

    INQ_HT = f"{INQ_DIR}/.htaccess"

    def retire_inquiries(host, gcfg, site_cfg, have, on_forget):

        import logging
        failed = ""
        try:
            collect_inquiries(limit=200, force=True)
        except Exception as e:
            logging.getLogger(__name__).exception("studio page inquiries before take-down")
            failed = str(e)[:200]
        ih = open_inq_host(gcfg, site_cfg)
        entries = top_files(ih)
        names = [n for n, _, plain in entries if plain]
        waiting = [n for n in names if INQ_NAME.match(n)]
        if waiting:
            return (f"{len(waiting)} inquir{'y' if len(waiting) == 1 else 'ies'} still waiting on your host couldn't be "
                    "collected" + (f" ({failed})" if failed else "") + ", so the inquiries folder was left as it is, still "
                    "closed to the web. Press Check for inquiries, then do this again.")
        with ih.session() if ih else contextlib.nullcontext():
            for n in names:
                if n in INQ_HOUSE or INQ_TMP.match(n):
                    ih.delete(n)
        others = [n for n, _, plain in entries if not plain or n not in (".htaccess",) + INQ_HOUSE and not INQ_TMP.match(n)]
        if others:
            return ("Other files in the inquiries folder on your host (" + ", ".join(others[:5]) + ") were left, so its "
                    ".htaccess stays, keeping them closed to the web.")
        if INQ_HT not in have:
            return ""
        with host.session():
            st = host_copy(host, INQ_HT, *have[INQ_HT])
            if st == "same":
                host.delete(INQ_HT)
            on_forget(INQ_HT)
            host.remove_dir(INQ_DIR + "/")
        return "inquiries/.htaccess was changed on your host since, so it was left there." if st == "changed" else ""

    def left_out_note(skipped):
        return ("Left out: " + "; ".join(f"{r['name']} ({why})" for r, why in skipped[:5]) + ".") if skipped else ""

    def do_upload(stamp):

        import logging
        ran = False
        log = logging.getLogger(__name__)
        with LOCK:
            if JOB.get("state") == "running":
                return
            JOB.clear()
            JOB.update(state="running", done=0, total=0, label="Uploading the studio page", error="")
        try:
            with app.app_context():
                if READY.get("stamp") != stamp:
                    return
                if not (SITE_ROOT / "ready" / "index.html").is_file():
                    raise SiteProblem("The site built for this upload is gone (the app was locked). Press Upload again.")
                ran = True
                site_cfg, site_url, folder = target()
                gcfg = gallery_cfg()
                fp = settle(site_cfg, gcfg, site_url, folder, host=open_host(gcfg, site_cfg))
                have = owned(fp)
                edited = edited_names(fp)
                photo_of = dict(READY.get("photos") or {})
                left_out = READY.get("left_out") or ""
                kind = site_cfg.get("type")

                collect_picks(page_sessions())
                ok_ids = {r_["id"] for r_ in allowed_now(photo_rows())[0]}
                if any(pid not in ok_ids for pid in photo_of.values()):
                    JOB.update(label="Building the studio page again (a client's permission changed)")
                    built, _, skipped = build("ready")
                    photo_of = {name: pid for pid, name in built.photos}
                    left_out = left_out_note(skipped)
                    JOB.update(label="Uploading the studio page")
            host = open_host(gcfg, site_cfg)

            def on_put(name, size, sha):
                pid = photo_of.get(name)
                with app.app_context():
                    sid = None
                    if pid:
                        r_ = q("SELECT session_id FROM studio_photos WHERE id=?", (pid,), one=True)
                        sid = r_["session_id"] if r_ else None
                    x("""INSERT INTO studio_files (name, host_fp, size, sha256, session_id, photo_id, at) VALUES (?,?,?,?,?,?,?)
                         ON CONFLICT(name, host_fp) DO UPDATE SET size=excluded.size, sha256=excluded.sha256,
                         session_id=excluded.session_id, photo_id=excluded.photo_id, at=excluded.at""",
                      (name, fp, size, sha, sid, pid, datetime.now().isoformat(timespec="seconds")))
                    if name in (left_marks().get(fp) or {}):
                        mark_left(fp, name, None)

            def on_forget(name):
                with app.app_context():
                    x("DELETE FROM studio_files WHERE name=? AND host_fp=?", (name, fp))
                    if (left_marks().get(fp) or {}).get(name) == "kept":
                        mark_left(fp, name, None)

            hold = {INQ_HT} if INQ_HT in have and not (SITE_ROOT / "ready" / INQ_HT).is_file() else set()
            try:
                res = publish(host, SITE_ROOT / "ready", have, folder, kind, on_put, on_forget,
                              progress=lambda d, t: JOB.update(done=d, total=t), hold=hold, on_left=left_on_host(fp),
                              edited=edited)
            except Conflict as e:
                with app.app_context():
                    raise refusal(host, e.names, fp, edited) from None
            inq_note = retire_inquiries(host, gcfg, site_cfg, have, on_forget) if hold else ""
            with app.app_context():

                yr = date.today().year
                for pid in set(photo_of.values()):
                    x("UPDATE studio_photos SET first_year=? WHERE id=? AND first_year IS NULL", (yr, pid))
                note = []
                if "htaccess" in res["notes"]:
                    from urllib.parse import urlsplit
                    note.append("Your host already has a .htaccess file, so it was left as it is. For the site's own "
                                f"'not found' page, it needs the line: ErrorDocument 404 {urlsplit(site_url).path}404.html")
                if res["left"]:
                    note.append("Changed on your host since Lumen Aureum put them there, so left as they are: "
                                + ", ".join(res["left"][:5]) + (" and more" if len(res["left"]) > 5 else "") + ".")
                if inq_note:
                    note.append(inq_note)
                if left_out:
                    note.insert(0, left_out)
                put_setting("site_state", "up")
                put_setting("site_at", date.today().isoformat())
                put_setting("site_error", "")
                put_setting("site_note", " ".join(note))
                ctx.audit(f"Studio page uploaded to {site_url} ({len(res['put'])} files"
                          + (f"; {len(res['removed'])} earlier file{'' if len(res['removed']) == 1 else 's'} removed" if res["removed"] else "")
                          + ")", who="Lumen Aureum")
            JOB.update(state="done")
        except Exception as e:
            log.exception("studio page upload")
            JOB.update(state="error", error=str(e))
            with app.app_context():
                put_setting("site_state", "error")
                put_setting("site_error", ("Upload stopped: " if not isinstance(e, SiteProblem) else "") + str(e)[:1500])
        finally:
            if ran:
                READY.clear()
                clear_build("ready")

    app.config["studio_page_do_upload"] = do_upload

    @app.post("/settings/studio-page/upload", endpoint="studio_page_upload")
    def studio_page_upload():
        from flask import g as _g
        if not on():
            abort(404)
        if JOB.get("state") == "running":
            _g.refused = True
            return redirect(back("#publish"))
        try:
            site_cfg, site_url, folder = target()
            gcfg = gallery_cfg()
            clear_build("preview")
            collect_picks(page_sessions())
            built, conf, skipped = build("ready")
            names = built.files
            for n in names:
                check_name(n, folder)
            host = open_host(gcfg, site_cfg)
            fp = settle(site_cfg, gcfg, site_url, folder, host=host)
            have = owned(fp)
            edited = edited_names(fp)
            with host.session():
                theirs = [n for n in names if n not in have and n != ".htaccess" and host.stat(n) is not None]
            if theirs:
                raise refusal(host, theirs, fp, edited)
        except SiteProblem as e:
            clear_build("ready")
            put_setting("site_state", "error")
            put_setting("site_error", str(e)[:1500])
            _g.refused = True
            return redirect(back("#publish"))
        except Exception as e:
            clear_build("ready")
            app.logger.exception("studio page upload")
            put_setting("site_state", "error")
            put_setting("site_error", f"The site couldn't be built or the host couldn't be reached: {e}"[:600])
            return redirect(back("#publish"))
        stamp = secrets.token_hex(8)
        READY.clear()
        READY.update(stamp=stamp, photos={name: pid for pid, name in built.photos}, left_out=left_out_note(skipped))
        put_setting("site_note", left_out_note(skipped))
        if ctx.hooks.guard(GUARD, lambda: do_upload(stamp), sid=None, gallery_id=None, n_files=len(built.files), site=True,
                           site_url=site_url):
            put_setting("site_state", "awaiting")
            return redirect(back("#publish"))
        threading.Thread(target=do_upload, args=(stamp,), name="studio-page-upload", daemon=True).start()
        return redirect(back("#publish"))

    def do_take_down():
        import logging
        with LOCK:
            if JOB.get("state") == "running":
                return
            JOB.clear()
            JOB.update(state="running", done=0, total=0, label="Taking the studio page down", error="")
        try:
            with app.app_context():
                site_cfg, site_url, folder = target()
                gcfg = gallery_cfg()
                fp = settle(site_cfg, gcfg, site_url, folder, host=open_host(gcfg, site_cfg))
                have = owned(fp)
            host = open_host(gcfg, site_cfg)

            def on_forget(name):
                with app.app_context():
                    x("DELETE FROM studio_files WHERE name=? AND host_fp=?", (name, fp))
                    if (left_marks().get(fp) or {}).get(name) == "kept":
                        mark_left(fp, name, None)
            READY.clear()
            clear_build("ready")
            clear_build("preview")
            removed, left = take_down(host, {n: v for n, v in have.items() if n != INQ_HT}, folder, on_forget,
                                      on_left=left_on_host(fp))
            inq_note = retire_inquiries(host, gcfg, site_cfg, have, on_forget) if INQ_HT in have else ""
            with app.app_context():
                put_setting("site_state", "down")
                put_setting("site_at", date.today().isoformat())
                put_setting("site_error", "")
                put_setting("site_note", " ".join(t_ for t_ in (
                    ("Changed on your host since Lumen Aureum put them there, so left as they are: " + ", ".join(left[:5])
                     + ".") if left else "", inq_note) if t_))
                ctx.audit(f"Studio page taken down from {site_url} ({len(removed)} files removed)", who="Lumen Aureum")
            JOB.update(state="done")
        except Exception as e:
            logging.getLogger(__name__).exception("studio page take down")
            JOB.update(state="error", error=str(e))
            with app.app_context():
                put_setting("site_state", "error")
                put_setting("site_error", f"Taking the site down stopped: {e}"[:600])

    @app.post("/settings/studio-page/take-down", endpoint="studio_page_take_down")
    def studio_page_take_down():
        from flask import g as _g
        if JOB.get("state") == "running":
            _g.refused = True
            return redirect(back("#publish"))
        try:
            site_cfg, _, _ = target()
        except SiteProblem as e:
            put_setting("site_error", str(e)[:600])
            _g.refused = True
            return redirect(back("#publish"))
        if ctx.hooks.guard(GUARD, do_take_down, sid=None, gallery_id=None, n_files=len(owned(host_fingerprint(site_cfg))),
                           site=True, remove=True):
            put_setting("site_state", "awaiting")
            return redirect(back("#publish"))
        threading.Thread(target=do_take_down, name="studio-page-down", daemon=True).start()
        return redirect(back("#publish"))

    def place_of(fp):

        try:
            site_cfg, site_url, folder = target()
            if host_fingerprint(site_cfg) == fp:
                return site_cfg, gallery_cfg(), site_url, folder
        except SiteProblem:
            pass
        rec = site_hosts().get(fp)
        if not rec:
            raise SiteProblem("The settings of that earlier web host aren't known here, so nothing can be done there "
                              "from the app. Delete the files on that host yourself, then forget it here.")
        return rec["site"], rec["gallery"], rec.get("url") or "", rec.get("folder") or ""

    def is_in_use(fp, ocfg, ogcfg):

        try:
            site_cfg, site_url, folder = target()
        except SiteProblem:
            return False
        cur = host_fingerprint(site_cfg)
        if cur == fp:
            return True
        gcfg = gallery_cfg()
        if same_site_place(open_host(gcfg, site_cfg), site_cfg, open_host(ogcfg, ocfg), ocfg):
            label = host_name(fp)
            remember_host(cur, site_cfg, gcfg, site_url, folder)
            adopt(fp, cur)
            ctx.audit(f"Studio page: {label} is the folder in use now, reached another way; its list of uploaded "
                      "files carries over", who="Lumen Aureum")
            return True
        return False

    def do_remove_file(name, at=None):

        import logging
        with LOCK:
            if JOB.get("state") == "running":
                return
            JOB.clear()
            JOB.update(state="running", done=0, total=1, label="Removing a photo from the studio page", error="")
        try:
            with app.app_context():
                if at is None:
                    site_cfg, site_url, folder = target()
                    fp, gcfg = host_fingerprint(site_cfg), gallery_cfg()
                else:
                    fp = at
                    site_cfg, gcfg, site_url, folder = place_of(fp)
                if not any(r_["name"] == name and r_["host_fp"] == fp for r_ in stale_photos()):
                    JOB.update(state="done")
                    return
            check_name(name, folder)
            host = open_host(gcfg, site_cfg)
            with host.session():
                if host.stat(name) is not None:
                    host.delete(name)
                if "/" in name:
                    host.remove_dir(name.rsplit("/", 1)[0] + "/")
            with app.app_context():
                x("DELETE FROM studio_files WHERE name=? AND host_fp=?", (name, fp))
                mark_left(fp, name, None)
                put_setting("site_note", f"Removed {name} from your host.")
                ctx.audit(f"Studio page: removed {name} from {site_url or host_name(fp)} (its client no longer allows it; "
                          "it had been changed on the host)", who="Lumen Aureum")
            JOB.update(state="done", done=1)
        except Exception as e:
            logging.getLogger(__name__).exception("studio page remove a photo")
            JOB.update(state="error", error=str(e))
            with app.app_context():
                put_setting("site_note", f"The photo couldn't be removed: {e}"[:600])

    def asked_host():

        at = request.form.get("host", "")
        return at if at and at in listed_fps() else None

    @app.post("/settings/studio-page/remove-file", endpoint="studio_page_remove_file")
    def studio_page_remove_file():
        from flask import g as _g
        name, at = request.form.get("name", ""), asked_host()
        if JOB.get("state") == "running" or not any(r_["name"] == name and (at is None or r_["host_fp"] == at)
                                                    for r_ in stale_photos()):
            _g.refused = True
            return redirect(back("#publish"))
        try:
            place_of(at) if at else target()
        except SiteProblem as e:
            put_setting("site_error", str(e)[:600])
            _g.refused = True
            return redirect(back("#publish"))
        ctx.audit(f"Asked to remove {name} from the studio page's host" + (f" {host_name(at)}" if at else ""))
        if ctx.hooks.guard(GUARD, lambda: do_remove_file(name, at), sid=None, gallery_id=None, n_files=1, site=True,
                           remove=True):
            put_setting("site_note", f"Removing {name} is waiting for approval.")
            return redirect(back("#publish"))
        threading.Thread(target=do_remove_file, args=(name, at), name="studio-page-remove", daemon=True).start()
        return redirect(back("#publish"))

    def do_take_down_earlier(fp):

        import logging
        with LOCK:
            if JOB.get("state") == "running":
                return
            JOB.clear()
            JOB.update(state="running", done=0, total=0, label="Taking the studio page down from an earlier host", error="")
        try:
            with app.app_context():
                if fp not in listed_fps():
                    JOB.update(state="done")
                    return
                ocfg, ogcfg, ourl, ofolder = place_of(fp)
                label = host_name(fp)
            try:
                with app.app_context():
                    same = is_in_use(fp, ocfg, ogcfg)
            except Exception as e:
                raise SiteProblem(f"Couldn't check that {label} is another place than the web host in use ({str(e)[:200]}), "
                                  "so nothing was removed there.")
            if same:
                with app.app_context():
                    put_setting("site_note", f"{label} is the folder in use now, reached another way, so nothing was "
                                             "removed there; its files are listed with the host in use.")
                JOB.update(state="done")
                return
            with app.app_context():
                have = owned(fp)
            host = open_host(ogcfg, ocfg)

            def on_forget(name):
                with app.app_context():
                    x("DELETE FROM studio_files WHERE name=? AND host_fp=?", (name, fp))
                    if (left_marks().get(fp) or {}).get(name) == "kept":
                        mark_left(fp, name, None)
            removed, left = take_down(host, {n: v for n, v in have.items() if n != INQ_HT}, ofolder, on_forget,
                                      on_left=left_on_host(fp))
            inq = ""
            if INQ_HT in have:
                ih = open_inq_host(ogcfg, ocfg)
                entries = top_files(ih)
                waiting = [n for n, _, plain in entries if plain and INQ_NAME.match(n)]
                others = [n for n, _, plain in entries if not plain or n not in (".htaccess",) + INQ_HOUSE and not INQ_TMP.match(n)
                          and not INQ_NAME.match(n)]
                if waiting or others:
                    inq = ("The inquiries folder there still holds files (" + ", ".join((waiting + others)[:5]) + "), so it "
                           "was left, closed to the web.")
                else:
                    with ih.session() if ih else contextlib.nullcontext():
                        for n, _, plain in entries:
                            if plain and (n in INQ_HOUSE or INQ_TMP.match(n)):
                                ih.delete(n)
                    with host.session():
                        st = host_copy(host, INQ_HT, *have[INQ_HT])
                        if st == "same":
                            host.delete(INQ_HT)
                        on_forget(INQ_HT)
                        host.remove_dir(INQ_DIR + "/")
            with app.app_context():
                put_setting("site_note", " ".join(t_ for t_ in (
                    f"Took the studio page down from {label}: {len(removed)} file{'' if len(removed) == 1 else 's'} removed.",
                    ("Changed on that host since Lumen Aureum put them there, so left as they are: " + ", ".join(left[:5])
                     + ".") if left else "", inq) if t_))
                ctx.audit(f"Studio page taken down from the earlier host {label} ({len(removed)} files removed)",
                          who="Lumen Aureum")
                if fp not in listed_fps():
                    h = site_hosts()
                    h.pop(fp, None)
                    put_hosts(h)
            JOB.update(state="done")
        except Exception as e:
            logging.getLogger(__name__).exception("studio page take down from an earlier host")
            JOB.update(state="error", error=str(e))
            with app.app_context():
                put_setting("site_note", ("" if isinstance(e, SiteProblem) else "Taking the site down from the earlier "
                                          "host stopped: ") + str(e)[:600])

    @app.post("/settings/studio-page/take-down-earlier", endpoint="studio_page_take_down_earlier")
    def studio_page_take_down_earlier():
        from flask import g as _g
        at = asked_host()
        cur = ""
        try:
            cur = host_fingerprint(target()[0])
        except SiteProblem:
            pass
        if JOB.get("state") == "running" or not at or at == cur:
            _g.refused = True
            return redirect(back("#publish"))
        try:
            place_of(at)
        except SiteProblem as e:
            put_setting("site_note", str(e)[:600])
            _g.refused = True
            return redirect(back("#publish"))
        if ctx.hooks.guard(GUARD, lambda: do_take_down_earlier(at), sid=None, gallery_id=None, n_files=len(owned(at)),
                           site=True, remove=True):
            put_setting("site_note", f"Taking the studio page down from {host_name(at)} is waiting for approval.")
            return redirect(back("#publish"))
        threading.Thread(target=do_take_down_earlier, args=(at,), name="studio-page-down-earlier", daemon=True).start()
        return redirect(back("#publish"))

    @app.post("/settings/studio-page/forget-host", endpoint="studio_page_forget_host")
    def studio_page_forget_host():

        from flask import g as _g
        at = asked_host()
        try:
            cur = host_fingerprint(target()[0])
        except SiteProblem:
            cur = None
        if JOB.get("state") == "running" or not at or cur is None or at == cur:
            _g.refused = True
            return redirect(back("#publish"))
        label, n = host_name(at), len(owned(at))
        x("DELETE FROM studio_files WHERE host_fp=?", (at,))
        m = left_marks()
        m.pop(at, None)
        put_setting("site_left", json.dumps(m, separators=(",", ":")))
        h = site_hosts()
        h.pop(at, None)
        put_hosts(h)
        put_setting("site_note", f"Forgot {label} and the {n} file{'' if n == 1 else 's'} listed for it.")
        ctx.audit(f"Studio page: forgot the earlier host {label} and the {n} files listed for it (gone for good, the "
                  "photographer said)")
        return redirect(back("#publish"))

    def open_inq_host(gcfg, site_cfg):

        t = site_cfg.get("type")
        if t == "local":
            from vault import backends as VB
            VB.from_config(dict(gcfg, public=True))
            d = os.path.join(site_cfg["path"], INQ_DIR)
            return VB.LocalBackend(d, public=True, marked=False) if os.path.isdir(d) else None
        if t == "sftp":
            return open_host(gcfg, dict(site_cfg, root=(site_cfg.get("root") + "/" if site_cfg.get("root") else "") + INQ_DIR))
        return None

    def add_inquiry(f, type_=None, to=None):

        types = dict(form_types())
        type_ = f.get("type") if f.get("type") in types else (type_ if type_ in types else next(iter(types)))
        if to:
            hid, pid = to
        else:
            hid = x("INSERT INTO households (id, name, source) VALUES (?,?,?)", (ctx.next_id("households"), f["name"], "Website"))
            pid = x("INSERT INTO people (household_id, name, role, email, phone) VALUES (?,?,?,?,?)",
                    (hid, f["name"], "adult", f["email"], f["phone"]))
        sid = ctx.create_session(hid, type_, "inquiry", "", f.get("date") or None, subject_ids=[pid] if pid else [])
        if f.get("date"):
            x("UPDATE sessions SET date_tentative=1 WHERE id=?", (sid,))
        summary = "Inquiry from your website" + ("" if f.get("type") else " (kind of session not given)") \
            + (f" from {f['name']}" if to else "") \
            + (f", preferred date {f['date']}" if f.get("date") else "") + (":\n" + f["message"] if f.get("message") else "") \
            + (f"\n(The message was longer; only its first {INQ_CUT} characters were kept.)" if f.get("cut") else "")
        x("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
          (hid, sid, "email" if f.get("email") else "call", summary, ctx.now_local()))
        ctx.audit("Inquiry from your website" + (" (added to an existing client)" if to else ""), sid=sid, who="Website")
        return sid, hid

    def arrived_today():
        return q("SELECT COUNT(*) n FROM studio_inquiries WHERE outcome IN ('waiting', 'added', 'spam') AND received LIKE ?",
                 (date.today().isoformat() + "%",), one=True)["n"]

    def form_online():

        try:
            fp = host_fingerprint(target()[0])
        except SiteProblem:
            return False
        return bool(q("SELECT 1 FROM studio_files WHERE host_fp=? AND name='inquiry.php'", (fp,), one=True))

    def collect_inquiries(limit=None, force=False):

        import time as _time
        with app.app_context():
            if not setting("site_inquiry_key") or not force and not form_online() and (
                    not on() or setting("site_inquiries") == "0"):
                return None
            try:
                site_cfg, _, _ = target()
            except SiteProblem:
                return None
            pem, gcfg, types = setting("site_inquiry_key"), gallery_cfg(), form_types()
            room = INQ_PER_CHECK if limit is None else limit
        host = open_inq_host(gcfg, site_cfg)
        if host is None:
            return None
        res, more = dict(added=0, dropped=0, bad=0), False
        with host.session():
            for n in [n for n in host.list("") if INQ_NAME.match(n)][:room + 200]:
                if res["added"] >= room:
                    more = True
                    break
                size = host.stat(n)
                blob = b""
                if size is not None and size <= INQ_MAX:
                    buf = io.BytesIO()
                    host.get_stream(n, buf)
                    blob = buf.getvalue()
                sha = hashlib.sha256(blob).hexdigest()
                with app.app_context():
                    had = q("SELECT 1 FROM studio_inquiries WHERE sha256=?", (sha,), one=True)
                if had:
                    host.delete(n)
                    continue
                try:
                    data_ = open_inquiry(pem, blob)
                    item_ = items.item_of(data_)
                    if item_ is None:
                        raise ValueError("no item id")
                    fields, why = clean_inquiry(data_, types)
                except ValueError:
                    res["bad"] += 1
                    host.delete(n)
                    continue
                with app.app_context():
                    seen_ = items.state(q, "inquiry", "", item_) != "new"
                if seen_:
                    host.delete(n)
                    continue
                if not save_inquiry(sha, fields, why, item_):
                    break
                host.delete(n)
                res["dropped" if why else "added"] += 1
            for n, mt, plain in top_files(host):
                if plain and INQ_TMP.match(n) and mt < _time.time() - 86400:
                    host.delete(n)
        with app.app_context():
            put_setting("site_inq_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
            put_setting("site_inq_note", ", ".join(t for t in (
                f"{res['added']} new to review" if res["added"] else "",
                "more waiting for the next check" if more else "",
                f"{res['dropped']} dropped as spam or incomplete" if res["dropped"] else "",
                f"{res['bad']} unreadable file{'' if res['bad'] == 1 else 's'} removed" if res["bad"] else "") if t))
            if res["dropped"] or res["bad"]:
                ctx.audit(f"Website inquiries: {res['dropped']} dropped (spam or no way to reply), {res['bad']} unreadable "
                          "file" + ("" if res["bad"] == 1 else "s") + " removed from the host", who="Lumen Aureum")
        return res

    app.config["studio_page_collect"] = collect_inquiries

    def save_inquiry(sha, fields, why, item=None):

        import locks
        from flask import g as _g
        if not locks.WRITES.acquire(timeout=60):
            return False
        try:
            with app.app_context():
                _g.tx = True
                try:
                    now_ = datetime.now().isoformat(timespec="seconds")
                    if not why:
                        x("INSERT INTO studio_inquiry_review (sha256, fields, received) VALUES (?,?,?)",
                          (sha, json.dumps(fields), now_))
                    x("INSERT INTO studio_inquiries (sha256, session_id, household_id, received, outcome) VALUES (?,?,?,?,?)",
                      (sha, None, None, now_, why or "waiting"))
                    if item:
                        items.take(q, x, "inquiry", "", item)
                    _g.db.commit()
                except BaseException:
                    _g.db.rollback()
                    raise
                finally:
                    _g.pop("tx", None)
        finally:
            locks.WRITES.release()
        return True

    LEGACY_INQ = re.compile(r"^(?:[0-9a-f]{16}-)?[0-9a-f]{32}\.bin$")

    def _record_put(fp, name, size, sha):
        x("""INSERT INTO studio_files (name, host_fp, size, sha256, session_id, photo_id, at) VALUES (?,?,?,?,?,?,?)
             ON CONFLICT(name, host_fp) DO UPDATE SET size=excluded.size, sha256=excluded.sha256, at=excluded.at""",
          (name, fp, size, sha, None, None, datetime.now().isoformat(timespec="seconds")))

    def up_hosts():
        site_cfg, site_url, _folder = target()
        gcfg = gallery_cfg()
        return (open_host(gcfg, site_cfg), open_inq_host(gcfg, site_cfg), site_url,
                "php" if site_cfg.get("type") in ("sftp", "local") else "static")

    def up_put_scripts(host):

        site_cfg, _u, _f = target()
        fp = host_fingerprint(site_cfg)
        src = inquiry_php_source(inquiry_salt(), INQ_WORK, kid_hex(inquiry_public(inquiry_key())), host_shared())
        with host.session():
            for name, data in (("sealed.php", (SEALED_ASSETS / "sealed.php").read_bytes()), ("inquiry.php", src.encode()),
                               (f"{INQ_DIR}/.htaccess", (ASSETS / "inquiries.htaccess").read_bytes()),
                               (f"{INQ_DIR}/index.html", b"")):
                size, sha = host.put_stream(name, io.BytesIO(data), content_type=MIME.get(Path(name).suffix or Path(name).name),
                                            cache_control="no-cache")
                _record_put(fp, name, size, sha)

    def _read_inq(ih, n):
        size = ih.stat(n)
        if size is None:
            return None
        buf = io.BytesIO()
        if size <= INQ_MAX:
            ih.get_stream(n, buf)
        return buf.getvalue()

    def _delete_if(ih, n, sha):

        now_ = _read_inq(ih, n)
        if now_ is not None and hashlib.sha256(now_).hexdigest() == sha:
            ih.delete(n)

    def up_sweep(ih, opener, shas):

        res = dict(added=0, dropped=0, left=0)
        if ih is None:
            return res
        pem, types = setting("site_inquiry_key"), form_types()
        with ih.session():
            for n in [n for n in ih.list("") if LEGACY_INQ.match(n)]:
                blob = _read_inq(ih, n)
                if blob is None:
                    continue
                sha = hashlib.sha256(blob).hexdigest()
                if q("SELECT 1 FROM studio_inquiries WHERE sha256=?", (sha,), one=True):
                    _delete_if(ih, n, sha)
                    continue
                try:
                    fields, why = clean_inquiry(opener(pem, blob), types)
                except ValueError:
                    shas[n] = sha
                    res["left"] += 1
                    continue
                if not save_inquiry(sha, fields, why):
                    raise RuntimeError("the app was busy; the inquiries are collected at the next try")
                _delete_if(ih, n, sha)
                res["dropped" if why else "added"] += 1
        if res["added"] or res["dropped"]:
            ctx.audit(f"Website inquiries sent before the 1.4 update: {res['added']} to review"
                      + (f", {res['dropped']} dropped (spam or no way to reply)" if res["dropped"] else ""), who="Lumen Aureum")
        return res

    def up_put_form(host):

        site_cfg, _u, _f = target()
        fp = host_fingerprint(site_cfg)
        have = owned(fp)
        if "contact/index.html" not in have:
            return
        try:
            built, _conf, _sk = build("upgrade")
            with host.session():
                for name in ("assets/inquiry.js", "contact/index.html"):
                    p = SITE_ROOT / "upgrade" / name
                    if not p.is_file():
                        continue
                    with open(p, "rb") as f_:
                        size, sha = host.put_stream(name, f_, content_type=MIME.get(Path(name).suffix), cache_control="no-cache")
                    _record_put(fp, name, size, sha)
        finally:
            clear_build("upgrade")

    def up_drop_unreadable(shas):

        if not shas:
            return
        site_cfg, _u, _f = target()
        ih = open_inq_host(gallery_cfg(), site_cfg)
        if ih is None:
            return
        with ih.session():
            for n, sha in shas.items():
                if LEGACY_INQ.match(n):
                    _delete_if(ih, n, sha)

    def up_form_online():

        try:
            return form_online()
        except Exception:
            return False

    app.config["studio_page_upgrade"] = dict(form_online=up_form_online, hosts=up_hosts, put_scripts=up_put_scripts,
                                             sweep=up_sweep, put_form=up_put_form, drop_unreadable=up_drop_unreadable)

    @ctx.after_unlock
    def _upgrade_form_without_galleries():

        if app.config.get("gallery_upgrade_run"):
            return
        import jobs as _jobs
        import upgrade_140
        with app.app_context():
            if not upgrade_140.finished(q):
                _jobs.start(app, "upgrade-140", "Updating the inquiry form", lambda job: upgrade_140.run(ctx, None, job))

    def collect_quietly():
        try:
            collect_inquiries()
        except Exception as e:
            app.logger.exception("studio page inquiries")
            with app.app_context():
                put_setting("site_inq_note", f"the check stopped: {e}"[:300])

    def _digits(phone):
        d = re.sub(r"\D", "", phone or "")
        return d[-10:] if len(d) >= 7 else ""

    def review_rows():

        rows = []
        for r_ in q("SELECT id, fields, received FROM studio_inquiry_review ORDER BY received, id"):
            try:
                f_ = json.loads(r_["fields"])
            except ValueError:
                f_ = {}
            f_ = f_ if isinstance(f_, dict) else {}
            rows.append(dict(f_, id=r_["id"], received=r_["received"][:16].replace("T", " "),
                             type_label=dict(form_types()).get(f_.get("type") or "", ""),
                             em=(f_.get("email") or "").strip().lower(), ph=_digits(f_.get("phone"))))
        if not rows:
            return rows
        by_em, by_ph = {}, {}
        for p_ in q("""SELECT p.id, p.household_id, p.email, p.phone, h.name FROM people p JOIN households h
                       ON h.id=p.household_id WHERE COALESCE(h.demo, 0)=0 ORDER BY h.name, p.id"""):
            m_ = dict(hid=p_["household_id"], pid=p_["id"], name=p_["name"])
            e_, d_ = (p_["email"] or "").strip().lower(), _digits(p_["phone"])
            if e_:
                by_em.setdefault(e_, []).append(m_)
            if d_:
                by_ph.setdefault(d_, []).append(m_)
        for r_ in rows:
            ms, seen = [], set()
            for m_ in (by_em.get(r_["em"], []) if r_["em"] else []) + (by_ph.get(r_["ph"], []) if r_["ph"] else []):
                if m_["hid"] not in seen:
                    seen.add(m_["hid"])
                    ms.append(m_)
            r_["matches"] = ms[:3]
            r_["twin"] = any(o_["id"] != r_["id"] and ((r_["em"] and o_["em"] == r_["em"]) or (r_["ph"] and o_["ph"] == r_["ph"]))
                             for o_ in rows)
        return rows

    def review_one(rid, do, type_=None, to=None):

        r_ = q("SELECT id, sha256, fields FROM studio_inquiry_review WHERE id=?", (rid,), one=True)
        if not r_:
            return False
        sid = hid = None
        if do in ("add", "add_to"):
            f_ = json.loads(r_["fields"])
            dest = None
            if do == "add_to":
                hit = [m_ for i_ in review_rows() if i_["id"] == rid for m_ in i_["matches"] if m_["hid"] == to]
                if not hit:
                    return False
                dest = (hit[0]["hid"], hit[0]["pid"])
            sid, hid = add_inquiry(f_, type_, dest)
        x("UPDATE studio_inquiries SET outcome=?, session_id=?, household_id=? WHERE sha256=?",
          ("spam" if do == "spam" else "added", sid, hid, r_["sha256"]))
        x("DELETE FROM studio_inquiry_review WHERE id=?", (rid,))
        return True

    @app.post("/settings/studio-page/inquiries/review", endpoint="studio_page_review")
    def studio_page_review():

        from flask import g as _g
        f = request.form
        do = f.get("do", "")
        to = f.get("add_to", "")
        if to.isdigit():
            do = "add_to"
        if do not in ("add", "spam", "add_all", "spam_all", "add_to"):
            abort(400)
        ids = [int(i_) for i_ in f.getlist("id") if i_.isdigit()]
        if do == "add_all":
            pending = review_rows()
            ids = [i_["id"] for i_ in pending if not i_["matches"] and (not ids or i_["id"] in ids)]
        n = sum(1 for rid in ids if review_one(rid, {"add_all": "add", "spam_all": "spam"}.get(do, do),
                                                f.get("type") or None, int(to) if to.isdigit() else None))
        if not n:
            _g.refused = True
        elif do in ("spam", "spam_all"):
            ctx.audit(f"Website inquiries: {n} marked as spam and removed" if n > 1
                      else "Website inquiry marked as spam and removed")
        return redirect(back("#inquiries"))

    @app.post("/settings/studio-page/inquiries", endpoint="studio_page_inquiries")
    def studio_page_inquiries():
        threading.Thread(target=collect_quietly, name="studio-page-inquiries", daemon=True).start()
        return redirect(back("#publish"))

    def inquiry_loop():
        import time
        time.sleep(30)
        while True:
            if ctx.unlocked():
                collect_quietly()
            time.sleep(INQ_CHECK_MIN * 60)

    if not os.environ.get("CRM_TESTING"):
        threading.Thread(target=inquiry_loop, name="studio-page-inquiry-check", daemon=True).start()

    def inquiry_items():
        since = (date.today().toordinal() - 7)
        out = []
        for r in q("""SELECT i.session_id, i.received, s.household_id, h.name FROM studio_inquiries i
                      JOIN sessions s ON s.id=i.session_id JOIN households h ON h.id=s.household_id
                      WHERE i.outcome='added' AND s.stage='inquiry' ORDER BY i.received"""):
            try:
                if date.fromisoformat(r["received"][:10]).toordinal() < since:
                    continue
            except ValueError:
                continue
            out.append(dict(text="New inquiry from the website", link=f"/session/{r['session_id']}",
                            who=dict(name=r["name"], link=f"/session/{r['session_id']}", hid=r["household_id"]),
                            when=r["received"][:10]))
        return out

    ctx.hooks.today.append(inquiry_items)

    def many_inquiries():
        out = []
        n = q("SELECT COUNT(*) n FROM studio_inquiry_review", one=True)["n"]
        if n:
            out.append(dict(text=f"{n} website inquir{'y' if n == 1 else 'ies'} to review: Add or Spam",
                            link=url_for("studio_page") + "#inquiries"))
        if arrived_today() > INQ_MANY_A_DAY:
            out.append(dict(text="Many website inquiries at once; check for spam", link=url_for("studio_page") + "#inquiries"))
        return out

    ctx.hooks.today.append(many_inquiries)

    def today_items():

        st = stale_photos()
        if not st:
            return []
        try:
            fp = host_fingerprint(target()[0])
        except SiteProblem:
            fp = ""
        out = []
        if not fp:
            return [dict(text="Your studio page's web host still has a photo a client no longer allows: take it down "
                              "on the Studio page", link=url_for("studio_page") + "#publish")]
        if any(r_["host_fp"] != fp for r_ in st):
            out.append(dict(text="An earlier web host of your studio page still has a photo a client no longer allows: "
                                 "take it down there on the Studio page", link=url_for("studio_page") + "#publish"))
        st = [r_ for r_ in st if r_["host_fp"] == fp]
        if not st:
            return out
        if any(r_["edited"] for r_ in st):
            return out + [dict(text="Your studio page's host still has a photo a client no longer allows, changed there since "
                              "it went up, so an upload leaves it: remove it on the Studio page",
                         link=url_for("studio_page") + "#publish")]
        return out + [dict(text="Your studio page shows a photo a client no longer allows: upload the studio page again to take it off"
                     if on() else "Your studio page shows a photo a client no longer allows: take the site down, or "
                     "switch the page on and upload it again", link=url_for("studio_page") + "#publish")]

    ctx.hooks.today.append(today_items)
    app.config.setdefault("ep_words", {}).update({
        "studio_page_switch": "Switched the studio page on or off", "studio_page_save": "Saved the studio page's words",
        "studio_page_add_own": "Added photos to the studio page", "studio_page_add_session": "Added a client's photos to the studio page",
        "studio_page_photo": "Changed a photo on the studio page", "studio_page_favicon": "Changed the studio page's icon",
        "studio_page_preview_build": "Built a preview of the studio page", "studio_page_upload": "Asked to upload the studio page",
        "studio_page_take_down": "Asked to take the studio page down",
        "studio_page_take_down_earlier": "Asked to take the studio page down from an earlier web host",
        "studio_page_forget_host": "Forgot an earlier web host of the studio page",
        "studio_page_inquiries": "Checked for inquiries from the website",
        "studio_page_review": "Reviewed inquiries from the website"})
