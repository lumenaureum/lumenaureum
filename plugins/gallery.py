import base64
import functools
import io
import json
import os
import re
import secrets
import shutil
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

from flask import abort, jsonify, redirect, request, send_from_directory, url_for

ASSETS = Path(__file__).with_name("gallery_assets")

def _script_hashes(html):

    import hashlib
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return "".join(f" 'sha256-{base64.b64encode(hashlib.sha256(b.encode()).digest()).decode()}'" for b in blocks)

_MARK = re.sub(r"<svg ", '<svg aria-hidden="true" focusable="false" ', (Path(__file__).resolve().parent.parent / "brand" / "mark.svg").read_text().strip(), count=1) \
    if (Path(__file__).resolve().parent.parent / "brand" / "mark.svg").is_file() else ""
CREDIT_HTML = ('<a id="la" href="https://lumenaureum.org" target="_blank" rel="noopener noreferrer">' + _MARK +
               '<span>Gallery by <b>Lumen Aureum</b><small>lumenaureum.org</small></span></a>')
VIEWER_SCRIPT_HASHES = _script_hashes((ASSETS / "viewer.html").read_text())
_DATA = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent.parent / "data" / "crm.db")).parent
OUT_ROOT = Path(os.environ.get("CRM_GALLERY_ROOT", _DATA / "galleries"))
PHOTOS_ROOT = Path(os.environ.get("CRM_PHOTOS_ROOT", _DATA / "photos"))

FOLDERS = os.environ.get("CRM_PHOTO_FOLDERS", "") == "1"
DEFAULT_BASE_URL = ""
STEP_LABEL = "Gallery built"
WEB_EDGE, THUMB_EDGE = 2048, 480
JPEG_EXT = {".jpg", ".jpeg"}
GID_OK = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

JOBS = {}
LOCK = threading.Lock()

BLOCK = """
<div id="gallery" class="f" style="gap:8px">
{% if job and job.state == 'running' %}
  <div class="row"><span class="mbar big"><span id="gal-bar" style="width:{{ pct }}%"></span></span>
    <span id="gal-n" class="sub">{{ job.done }} / {{ job.total }}</span></div>
  <span hidden data-poll="{{ url_for('gallery_status', sid=s.id) }}" data-bar="gal-bar" data-count="gal-n"></span>
{% elif s.stage == 'closed' %}
  {% if not (g and g.built_at) %}<div class="mute" style="font-size:13px">No gallery</div>{% endif %}
{% else %}
  <form method="post" action="{{ url_for('build_gallery', sid=s.id) }}" class="row"><input type="hidden" name="from" value="uploads">
    <span class="grow" style="font-size:13px">{% if n_up %}{{ n_up }} edited JPEG{{ '' if n_up == 1 else 's' }} uploaded{% else %}<span class="mute">Upload the edited JPEGs in Photos first</span>{% endif %}</span>
    <button class="{{ '' if g and g.built_at else 'primary' }}"{{ ' disabled' if not n_up }}>{{ 'Rebuild' if g and g.built_at else 'Build gallery' }}</button>
  </form>
  {% if folders %}<form method="post" action="{{ url_for('build_gallery', sid=s.id) }}" class="row">
    <input name="source_dir" list="gal-dirs" value="{{ src }}" placeholder="Folder" class="grow" style="width:auto;min-width:0" required>
    <datalist id="gal-dirs">{% for d_ in dirs %}<option value="{{ d_ }}">{% endfor %}</datalist>
    <button class="quiet">Build from folder</button>
  </form>
  {% if src_n is none %}<div class="mute" style="font-size:13px">Folder not found</div>
  {% elif src_n == 0 %}<div class="mute" style="font-size:13px">No JPEGs in folder</div>
  {% else %}<div class="sub" style="font-size:13px" data-tip="{{ src }}">{{ src_n }} JPEG{{ '' if src_n == 1 else 's' }} in folder</div>{% endif %}{% endif %}
  {% if job and job.state == 'error' %}<div style="color:var(--red);font-size:13px">{{ job.error }}</div>{% endif %}
{% endif %}
{% if cw_on and not s.gallery_codeword and not (g and g.cw_fixed == 'none') %}<div class="row" style="font-size:13px"><span class="grow">This gallery asks for a codeword the client chooses. None yet.</span>
  <a class="go" href="{{ url_for('session_view', sid=s.id) }}#delivery">Add the codeword &rsaquo;</a></div>{% endif %}
{% if stale_cw %}<div style="font-size:13px;color:var(--amber, #b07a12)">The codeword changed after this build: press Rebuild so the gallery asks for the new one.</div>{% endif %}
{% if g and g.built_at %}
  <div class="row" style="font-size:14px"><span class="grow"><b>{{ g.n_files }}</b> photo{{ '' if g.n_files == 1 else 's' }}, built {{ nice(g.built_at) }}</span>
    <a class="go" href="{{ preview }}" target="_blank" rel="noopener">Preview &rsaquo;</a></div>
  {% set us = g.upload_state or '' %}
  <div class="row" style="font-size:14px">
  {% if us == 'up' %}<span class="grow">On the web since {{ nice(g.uploaded_at) }}</span>
    {% if link %}<span hidden id="gal-link-{{ s.id }}">{{ link }}</span><button type="button" class="quiet" data-copy="#gal-link-{{ s.id }}" data-tip="The client's link, to text or email. Anyone with it can open the gallery{{ ' (with the codeword)' if '&c=1' in link }}.">Copy link</button>{% endif %}
    <form method="post" action="{{ url_for('gallery_sync_now') }}" class="inline"><input type="hidden" name="back" value="{{ back }}"><button class="quiet">Check now</button></form>
  {% elif us == 'awaiting' %}<span class="grow">Waiting for approval</span>
  {% elif us == 'uploading' %}<span class="grow">Uploading</span>
  {% elif us == 'removed' %}<span class="grow">Removed from the web {{ nice(g.removed_at) }}</span>
  {% elif not has_host %}<span class="grow mute">Previews work here; putting it online needs a web host.</span>
    <a class="go" href="{{ url_for('setup_step', step='galleries') }}">Set up galleries &rsaquo;</a>
  {% elif owed %}<span class="grow">{{ money(owed) }} still due. Your terms release the gallery on payment.</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline" data-confirm="Put the gallery online before the balance is paid? Use this for a proofing gallery the client picks from."><input type="hidden" name="early" value="1"><button class="quiet">Upload before payment</button></form>
  {% else %}<span class="grow">{% if us == 'declined' %}Upload declined{% elif us == 'expired' %}Tap expired{% elif us == 'error' and not (g.upload_error or '').startswith('balance') %}<span style="color:var(--red)">Upload failed: {{ g.upload_error }}</span>{% else %}Ready to upload{% endif %}</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline"><button class="primary">Upload</button></form>{% endif %}
  </div>
  {% if us == 'up' and stale %}<div class="row" style="font-size:14px"><span class="grow" style="color:var(--amber, #b07a12)">The copy online is older than this build{% if g.upload_error %}. The upload didn't finish: {{ g.upload_error }}{% endif %}</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline"><button class="primary">Upload the new build</button></form></div>{% endif %}
  {% if expires %}
  <div class="row" style="font-size:14px">
    <span class="grow" {% if exp_status %}style="color:var(--{{ exp_status }})"{% endif %}
      data-tip="Opened {{ nice(s.first_visit) }}, plus {{ days }} days{{ ' and the extension' if s.extension_used }}">Planned deletion {{ nice(expires.isoformat()) }}</span>
    {% if s.extension_used %}<span class="mute" style="font-size:12px">extended</span>{% endif %}
  </div>
  {% else %}
  <div class="sub" style="font-size:14px" data-tip="Deleted {{ days }} days after the first visit, {{ days * 2 }} with the one extension; unopened, a year after upload">Not opened</div>
  {% endif %}
{% endif %}
</div>
"""

def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()

def key_bytes(k):
    return base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))

CODEWORD_ROUNDS = 600_000
CODEWORD_WORDS = ("amber", "anchor", "aspen", "birch", "blossom", "bramble", "breeze", "brook", "canyon", "cedar", "clover",
                  "comet", "coral", "cove", "crane", "dahlia", "dawn", "delta", "ember", "fern", "fjord", "glacier", "harbor",
                  "hazel", "heron", "hollow", "aster", "ivy", "juniper", "kestrel", "lagoon", "lantern", "laurel", "linden",
                  "maple", "meadow", "mesa", "orchard", "osprey", "otter", "pebble", "pine", "plover", "prairie", "quartz",
                  "raven", "reef", "ridge", "river", "robin", "sage", "sparrow", "spruce", "summit", "thistle", "tide",
                  "timber", "trellis", "tulip", "valley", "violet", "willow", "wren", "zephyr")

def codeword_strong(word):

    w = norm_codeword(word)
    parts = w.split()
    letters = w.replace(" ", "")
    if len(set(letters)) < 5:
        return False
    return (len(parts) >= 3 and all(len(p_) >= 3 for p_ in parts) and len(set(parts)) >= 3) or len(letters) >= 14

def suggest_codeword():
    return " ".join(secrets.choice(CODEWORD_WORDS) for _ in range(5))

def norm_codeword(w):

    import re as _re
    import unicodedata
    w = unicodedata.normalize("NFKC", w or "")
    w = _re.sub("[\u200b-\u200d\u2060\ufeff\u00ad]", "", w)
    w = _re.sub(r"[\x00-\x1f\x7f-\x9f\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+", " ", w)
    return w.strip().lower()

def codeword_key(link_key, gid, word):

    import hashlib
    w = norm_codeword(word)
    if not w:
        return link_key
    dk = hashlib.pbkdf2_hmac("sha256", w.encode("utf-8"), key_bytes(link_key) + gid.encode("ascii"), CODEWORD_ROUNDS, 32)
    return base64.urlsafe_b64encode(dk).rstrip(b"=").decode()

@functools.lru_cache(maxsize=128)
def codeword_key_cached(link_key, gid, word):

    return codeword_key(link_key, gid, word)

def seal(aes, data, name):

    iv = secrets.token_bytes(12)
    return iv + aes.encrypt(iv, data, ("lumen-aureum-gallery:" + name).encode())

WATERMARKS = ("", "corner", "tiled")

def _font(px):
    from PIL import ImageFont
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, max(8, int(px)))
        except OSError:
            pass
    return ImageFont.load_default(max(8, int(px)))

def watermark(img, mark):

    from PIL import Image, ImageDraw
    style = (mark or {}).get("style") or ""
    if style not in WATERMARKS or not style:
        return img
    text = ((mark.get("text") or "PROOF").strip() or "PROOF")[:40]
    a = max(5, min(100, int(mark.get("strength") or 20))) / 100
    base = img.convert("RGBA")
    W, H = base.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    white = (255, 255, 255, int(255 * a))

    def fit(t, width):
        f = _font(100)
        w_ = d.textlength(t, font=f) or 1
        return _font(100 * width / w_)

    if style == "tiled":
        f = _font(max(W, H) / 22)
        tw = d.textlength(text, font=f)
        tile = Image.new("RGBA", (int(tw * 1.9) + 1, int(f.size * 3.2) + 1), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((tw * 0.45, f.size), text, font=f, fill=white)
        tile = tile.rotate(30, expand=True, resample=Image.BICUBIC)
        for ty in range(-tile.height, H + tile.height, int(tile.height * 0.7)):
            off = (ty // max(1, int(tile.height * 0.7))) % 2 * tile.width // 2
            for tx in range(-tile.width + off, W + tile.width, tile.width):
                layer.alpha_composite(tile, (max(0, tx), max(0, ty)), (max(0, -tx), max(0, -ty)))
    else:
        m = int(min(W, H) * 0.04)
        logo = mark.get("logo")
        if logo:
            lg = Image.open(io.BytesIO(logo)).convert("RGBA")
            if lg.getchannel("A").getextrema()[0] >= 250:
                logo = None
        if logo:
            lg.thumbnail((int(W * 0.22), int(H * 0.14)), Image.LANCZOS)
            al = lg.getchannel("A").point(lambda v: int(v * a))
            lg = Image.new("RGBA", lg.size, (255, 255, 255, 0))
            lg.putalpha(al)
            layer.alpha_composite(lg, (W - lg.width - m, H - lg.height - m))
        else:
            f = fit(text, W * 0.2)
            box = d.textbbox((0, 0), text, font=f)
            d.text((W - box[2] - m, H - box[3] - m), text, font=f, fill=white)
    return Image.alpha_composite(base, layer).convert("RGB")

def clean_jpeg(path, mark=None):

    from PIL import Image, ImageCms, ImageOps
    with Image.open(path) as im:
        if im.format == "JPEG":
            w_, h_ = im.size
            r_ = min(1.0, WEB_EDGE / max(w_, h_))
            im.draft("RGB", (max(1, -(-int(w_ * r_ * 1000) // 1000)), max(1, -(-int(h_ * r_ * 1000) // 1000))))
        im.load()
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        if icc and im.mode in ("RGB", "CMYK"):
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:
                pass
        im = im.convert("RGB")
        pixels = Image.frombytes("RGB", im.size, im.tobytes())

    def encode(img, edge, quality):
        img = img.copy()
        img.thumbnail((edge, edge), Image.LANCZOS)
        if mark:
            img = watermark(img, mark)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=quality, optimize=True, progressive=True)
        return b.getvalue(), img.size

    web, wsize = encode(pixels, WEB_EDGE, 86)
    thumb, tsize = encode(pixels, THUMB_EDGE, 80)
    return web, wsize, thumb, tsize

def natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]

def list_jpegs(folder):
    return sorted((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in JPEG_EXT
                   and not p.name.startswith(".")), key=natural)

class Built(NamedTuple):

    out_dir: Path
    n: int
    skipped: list

THEME_FONTS = {"elegant": "cormorant-garamond.woff2", "script": "great-vibes.woff2"}
THEME_FONT_FILE = "font.woff2"

def build(gid, src, key, progress=lambda done, total: None, picks=None, credit="", brand=None, mark=None):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(key_bytes(key))
    files = [opener for _, opener in src] if isinstance(src, list) else list_jpegs(src)
    names = [n_ for n_, _ in src] if isinstance(src, list) else [p_.name for p_ in files]
    if not files:
        raise ValueError("No JPEGs in folder")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    final = OUT_ROOT / gid
    tmp = OUT_ROOT / f".{gid}.building"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    used, items = set(), []

    def fresh():
        while True:
            n = secrets.token_hex(12) + ".bin"
            if n not in used:
                used.add(n)
                return n

    progress(0, len(files))
    skipped = []
    for i, p in enumerate(files):
        try:
            web, (w, h), thumb, (tw, th) = clean_jpeg(p() if callable(p) else p, mark)
        except Exception:
            skipped.append(names[i])
            progress(i + 1, len(files))
            continue
        f, t = fresh(), fresh()
        (tmp / f).write_bytes(seal(aes, web, f))
        (tmp / t).write_bytes(seal(aes, thumb, t))
        items.append(dict(f=f, t=t, w=w, h=h, tw=tw, th=th, n=names[i]))
        progress(i + 1, len(files))
    if not items:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError("None of these files is a JPEG photo this can read: " + ", ".join(skipped[:5]))
    brand = dict(brand or {})
    font = THEME_FONTS.get(((brand.get("theme") or {}).get("font")) or "")
    manifest = dict(v=1, title=brand.pop("client", "") or "Your photos", items=items, picks=picks or {}, brand=brand)
    (tmp / "manifest.bin").write_bytes(seal(aes, json.dumps(manifest, separators=(",", ":")).encode(), "manifest.bin"))
    page = (ASSETS / "viewer.html").read_text()
    page = page.replace('<html lang="en">', f'<html lang="en" data-gid="{gid}">', 1)
    if (picks or {}).get("post"):
        page = page.replace('<html lang="en"', '<html lang="en" data-gate="1"', 1)
    if credit:
        page = page.replace("<span hidden data-credit></span>", CREDIT_HTML)
    (tmp / "index.html").write_text(page)
    shutil.copyfile(ASSETS / "seen.php", tmp / "seen.php")
    shutil.copyfile(ASSETS / "picks.php", tmp / "picks.php")
    shutil.copyfile(ASSETS / "gate.php", tmp / "gate.php")
    import hashlib

    proof = hashlib.sha256(key_bytes(key)).hexdigest()[:32]
    (tmp / "seen-key.php").write_text("<?php exit; ?>\n" + hashlib.sha256(proof.encode()).hexdigest() + "\n")
    shutil.copyfile(ASSETS / "htaccess", tmp / ".htaccess")
    if font:
        shutil.copyfile(ASSETS / "fonts" / font, tmp / THEME_FONT_FILE)
    old = OUT_ROOT / f".{gid}.old"
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        final.rename(old)
    tmp.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return Built(final, len(items), skipped)

SYNC_MIN = 30
_HAS_LIMITS = set()

def has_host():
    try:
        gallery_host()
        return True
    except Exception:
        return False

def gallery_host():
    import sys as _sys
    root = str(Path(__file__).resolve().parent.parent)
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from vault import backends as VB, web as VW
    cfg = VW.config().get("gallery_host")
    if not cfg:
        raise RuntimeError("no gallery host configured")
    return VB.from_config(dict(cfg, public=True))

MIME = {".html": "text/html; charset=utf-8", ".php": "application/x-httpd-php", ".bin": "application/octet-stream",
        ".txt": "text/plain", ".key": "text/plain", ".htaccess": "text/plain", ".woff2": "font/woff2"}

def put_limits(gid, days, unopened_until):

    import io
    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    body = f"days={int(days)}\nunopened_until={unopened_until}\n"
    try:
        if "async function keyProof" in (OUT_ROOT / gid / "index.html").read_text():
            body += "proof=1\n"
    except OSError:
        pass
    body = body.encode()
    gallery_host().put_stream(f"{gid}/limits.txt", io.BytesIO(body), content_type="text/plain")

def upload_gallery(gid, days=None, unopened_until=None):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    if days is not None:
        put_limits(gid, days, unopened_until)
    files = sorted((p for p in (OUT_ROOT / gid).iterdir() if p.is_file() and p.name != ONLINE_MARK),
                   key=lambda p: (p.name == "index.html", p.name == "manifest.bin"))
    for p in files:
        with open(p, "rb") as f:
            host.put_stream(f"{gid}/{p.name}", f, content_type=MIME.get(p.suffix or p.name))
    return len(files) + (1 if days is not None else 0)

ONLINE_MARK = ".online"

def mark_online(gid):
    if GID_OK.match(gid) and (OUT_ROOT / gid).is_dir():
        (OUT_ROOT / gid / ONLINE_MARK).write_text(date.today().isoformat() + "\n")

def online_is_current(gid):

    return (OUT_ROOT / gid / ONLINE_MARK).is_file()

HOST_KEEPS = {"limits.txt", "seen.txt", "picks.bin", "draft.bin", ".htaccess"}

def prune_host(gid):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    have = {p.name for p in (OUT_ROOT / gid).iterdir() if p.is_file()}
    gone = 0
    for n in host.list(f"{gid}/"):
        base = n.rsplit("/", 1)[-1]
        if base in have or base in HOST_KEEPS:
            continue
        if re.fullmatch(r"[0-9a-f]{24}\.bin", base) or base == THEME_FONT_FILE:
            host.delete(n)
            gone += 1
    return gone

def seen_at(gid):

    import io
    host = gallery_host()
    if host.stat(f"{gid}/seen.txt") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/seen.txt", buf)
    return buf.getvalue().decode().strip() or None

PICKS_AAD = b"lumen-aureum-gallery:picks.bin"
DRAFT_AAD = b"lumen-aureum-gallery:draft.bin"

def picks_blob(gid, name="picks.bin"):

    import io
    host = gallery_host()
    if host.stat(f"{gid}/{name}") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/{name}", buf)
    return buf.getvalue()

FP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def picks_fingerprint(lines, key):

    import hmac as _hmac
    import hashlib as _hl
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    text = "\n".join(" ".join(l.split()) for l in lines if l.strip())
    hk = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"lumen-aureum picks fingerprint").derive(key_bytes(key))
    mac = _hmac.new(hk, text.encode(), _hl.sha256).digest()[:10]
    bits = val = 0
    out = ""
    for b in mac:
        val, bits = (val << 8) | b, bits + 8
        while bits >= 5:
            out += FP_ALPHABET[(val >> (bits - 5)) & 31]
            bits -= 5
    return "-".join(out[i:i + 4] for i in range(0, 16, 4))

def read_picks_email(text, key):

    import re as _re
    lines, fp = [], ""
    for raw_ in (text or "").replace("\r", "").split("\n"):
        l = " ".join(raw_.split())
        m = _re.match(r"(?i)^fingerprint:\s*([A-Z2-9-]{16,24})$", l)
        if m:
            fp = m.group(1).upper()
        elif _re.match(r"^(Retouch |Suggested for your portfolio: |Please don't show: )", l):
            lines.append(l)
    if not lines:
        return None, False, "No picks found in that text. Paste the whole email from the client."
    same = bool(fp) and _hmac_eq(fp, picks_fingerprint(lines, key))
    out = dict(retouch=[], suggest=[], veto=[])
    for l in lines:
        if l.startswith("Retouch "):
            out["retouch"].append(l[len("Retouch "):])
        elif l.startswith("Suggested for your portfolio: "):
            out["suggest"].append(l.split(": ", 1)[1])
        else:
            out["veto"].append(l.split(": ", 1)[1])
    return out, same, ("These match what the client picked in the gallery." if same else
                       "Recorded. These differ from what the client picked in the gallery (edited in the email), so ask the"
                       " client which picks are final; a to-do was added.")

def _hmac_eq(a, b):
    import hmac as _hmac
    return _hmac.compare_digest(a.replace("-", ""), b.replace("-", ""))

PICKS_MAX = 262144
PICKS_MAX_ENTRIES = 5000

def _open_sealed(blob, key, aad):
    import json as _json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob or blob[:4] != b"LAK1" or len(blob) < 32 or len(blob) > PICKS_MAX:
        raise ValueError("not a picks file")
    data = _json.loads(AESGCM(key_bytes(key)).decrypt(blob[4:16], blob[16:], aad))
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise ValueError("not picks")
    return data

def open_draft(blob, key):

    data = _open_sealed(blob, key, DRAFT_AAD)
    n = sum(1 for p_ in data["picks"][:PICKS_MAX_ENTRIES] if isinstance(p_, dict) and (p_.get("r") or p_.get("m") or p_.get("v")))
    at = str(data.get("at") or "")[:40]
    return _shape(data), n, at if re.fullmatch(r"[0-9T:.+Z-]+", at) else ""

def open_picks(blob, key):

    return _shape(_open_sealed(blob, key, PICKS_AAD))

def _shape(data):
    out = dict(retouch=[], suggest=[], veto=[])
    for p_ in data["picks"][:PICKS_MAX_ENTRIES]:
        if not isinstance(p_, dict):
            continue
        name = str(p_.get("name") or f"photo {int(p_.get('i', 0)) + 1}")[:200]
        if p_.get("r"):
            note = str(p_.get("note") or "").strip()[:200]
            out["retouch"].append(name + (f": {note}" if note else ""))
        if p_.get("m"):
            out["suggest"].append(name)
        if p_.get("v"):
            out["veto"].append(name)
    return out

def delete_gallery(gid):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    names = host.list(f"{gid}/") + [f"{gid}/.htaccess"]
    for n in names:
        host.delete(n)
    if any(host.stat(n) is not None for n in names):
        raise IOError("gallery files still on the host after delete")
    host.remove_dir(f"{gid}/")
    return len(names)

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    from flask import render_template_string
    ctx.migrate("""CREATE TABLE IF NOT EXISTS galleries (
        session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        source_dir TEXT DEFAULT '', built_at TEXT, n_files INTEGER DEFAULT 0, out_dir TEXT DEFAULT '',
        key TEXT NOT NULL);""")
    @ctx.on_unlock
    def _migrate_cols():
        _con = ctx.connect()
        _gcols = [r_[1] for r_ in _con.execute("PRAGMA table_info(galleries)")]
        if "picks_hash" not in _gcols:
            ctx.add_column(_con, "galleries", "picks_hash", "TEXT DEFAULT ''")
        if "limits_days" not in _gcols:
            ctx.add_column(_con, "galleries", "limits_days", "INTEGER")
        if "ekey" not in _gcols:
            ctx.add_column(_con, "galleries", "ekey", "TEXT DEFAULT ''")
        if "draft_n" not in _gcols:
            ctx.add_column(_con, "galleries", "draft_n", "INTEGER")
            ctx.add_column(_con, "galleries", "draft_at", "TEXT")
        if "cw_fixed" not in _gcols:
            ctx.add_column(_con, "galleries", "cw_fixed", "TEXT")
            if "uploaded_at" in _gcols:
                _con.execute("UPDATE galleries SET cw_fixed='none' WHERE uploaded_at IS NOT NULL")
        gcols = [r[1] for r in _con.execute("PRAGMA table_info(galleries)")]
        for col, decl in (("upload_state", "TEXT DEFAULT ''"), ("uploaded_at", "TEXT"), ("removed_at", "TEXT"), ("upload_error", "TEXT DEFAULT ''")):
            if col not in gcols:
                ctx.add_column(_con, "galleries", col, decl)

        done_ = _con.execute("SELECT value FROM settings WHERE key='gallery_online_marks'").fetchone()
        for r_ in [] if done_ else _con.execute("""SELECT s.gallery_id, g.uploaded_at FROM galleries g JOIN sessions s ON s.id=g.session_id
                                  WHERE g.upload_state='up' AND g.removed_at IS NULL""").fetchall():
            try:
                m_ = OUT_ROOT / (r_[0] or "") / "manifest.bin"
                if r_[0] and GID_OK.match(r_[0]) and m_.is_file() and not online_is_current(r_[0]) and r_[1] and \
                        date.fromtimestamp(m_.stat().st_mtime).isoformat() <= r_[1][:10]:
                    mark_online(r_[0])
            except OSError:
                pass
        _con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('gallery_online_marks', '1')")
        _con.commit(); _con.close()

    def has_gallery_step(s):
        flow = ctx.C.WORKFLOW.get(s["type"], {})
        return any(st[0] == "auto" and st[1] == "gallery_built" for steps in flow.values() for st in steps)

    def gal(sid):
        return q("SELECT * FROM galleries WHERE session_id=?", (sid,), one=True)

    def ensure(s):

        gid = s["gallery_id"]
        if not gid or not GID_OK.match(gid):
            gid = secrets.token_urlsafe(9)
            x("UPDATE sessions SET gallery_id=? WHERE id=?", (gid, s["id"]))
        if not gal(s["id"]):
            x("INSERT INTO galleries (session_id, key) VALUES (?,?)", (s["id"], new_key()))
        return gid, gal(s["id"])

    def session(sid):
        return q("SELECT * FROM sessions WHERE id=?", (sid,), one=True) or abort(404)

    def gallery_brand(s):

        def setting(k):
            r_ = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
            return (r_["value"] if r_ and r_["value"] else "").strip()
        h = q("SELECT name FROM households WHERE id=?", (s["household_id"],), one=True)
        when = ""
        if s["shoot_date"]:
            d_ = date.fromisoformat(s["shoot_date"][:10])
            when = f"{d_:%B} {d_.day}, {d_.year}"
        label = ctx.C.doc_label(s["type"])

        eid = (s["event_id"] if "event_id" in s.keys() else None) or (s["payer_event"] if "payer_event" in s.keys() else None)
        if eid:
            ev = q("SELECT rights, name FROM events WHERE id=?", (eid,), one=True)
            owns = bool(ev) and (ev["rights"] or "families") == "families"
            label = (ev["name"] if ev and ev["name"] else "") or label
        else:
            owns = ctx.licensed(s["id"])
        return dict(client=h["name"] if h else "", owns=owns,
                    what=label, date=when, logo=setting("logo_png"),
                    studio=setting("business_name") or setting("studio_name") or setting("photographer_name"),
                    photographer=setting("photographer_name"), email=setting("business_email"), phone=setting("business_phone"))

    def setting_(k):
        r_ = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
        return (r_["value"] if r_ and r_["value"] else "").strip()

    def base_url():
        r = q("SELECT value FROM settings WHERE key='gallery_base_url'", one=True)
        u = (r["value"] if r and r["value"] else DEFAULT_BASE_URL).strip()
        return u if u.endswith("/") else u + "/"

    def gallery_link(session_id):
        s = q("SELECT gallery_id FROM sessions WHERE id=?", (session_id,), one=True)
        g = gal(session_id)
        if not s or not g or not g["built_at"] or not s["gallery_id"]:
            return None
        cw = bool(g["ekey"]) and g["ekey"] != g["key"]
        return f"{base_url()}{s['gallery_id']}/#k={g['key']}" + ("&c=1" if cw else "")

    app.config["gallery_link"] = gallery_link

    def in_photos(path):
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        root = PHOTOS_ROOT.resolve()
        return p if p == root or root in p.parents else None

    def src_dirs():
        out = []
        for base in (PHOTOS_ROOT / "export",):
            try:
                out += sorted(str(d_) + "/" for d_ in base.iterdir() if d_.is_dir() and not d_.name.startswith("."))
            except OSError:
                pass
        return out[:200]

    def uploaded(sid):

        import photostore
        con = ctx.connect()
        try:
            rows = [r for r in photostore.files(con, sid, "export", done_only=True)
                    if Path(r["name"]).suffix.lower() in JPEG_EXT]
        finally:
            con.close()
        rows.sort(key=lambda r: natural(Path(r["name"])))
        return [(r["name"], (lambda r=r: io.BytesIO(photostore.plain_bytes(r)))) for r in rows]

    @app.post("/session/<int:sid>/gallery/build", endpoint="build_gallery")
    def build_gallery(sid):
        s = session(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        gid, g = ensure(s)
        if request.form.get("from") == "uploads" or not FOLDERS:
            src = uploaded(sid)
            if not src:
                JOBS[sid] = dict(state="error", done=0, total=0, error="No edited JPEGs uploaded yet: drop them in Photos first.")
                return redirect(back)
            x("UPDATE galleries SET source_dir='' WHERE session_id=?", (sid,))
        else:
            src = in_photos(request.form.get("source_dir", "").strip())
            if src is None:
                JOBS[sid] = dict(state="error", done=0, total=0, error="Folder must be under " + str(PHOTOS_ROOT))
                return redirect(back)
            x("UPDATE galleries SET source_dir=? WHERE session_id=?", (str(src) + "/", sid))
        word = (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or ""
        key = codeword_key(g["key"], gid, word)
        fixed = g["cw_fixed"] if "cw_fixed" in g.keys() else None
        if fixed and ((fixed == "none") != (not norm_codeword(word)) or (fixed == "word" and (g["ekey"] or "") not in ("", key))):

            JOBS[sid] = dict(state="error", done=0, total=0,
                             error="This gallery is already online " + ("without a codeword" if fixed == "none" else "with its codeword")
                                   + ", so that stays as it is: the link you sent keeps working.")
            return redirect(back)
        with LOCK:
            if JOBS.get(sid, {}).get("state") == "running":
                return redirect(back)
            JOBS[sid] = dict(state="running", done=0, total=0, error="")

        em = q("SELECT value FROM settings WHERE key='business_email'", one=True)

        from vault import web as VW
        host_type = (VW.config().get("gallery_host") or {}).get("type", "")
        picks_cfg = dict(retouch=ctx.C.RETOUCH_PICKS, market=0 if s["portfolio_no"] else ctx.C.MARKETING_PICKS,
                         email=(em["value"] if em else ""), ref=gid,
                         post=host_type in ("sftp", "local"))
        off = q("SELECT value FROM settings WHERE key='credit_off'", one=True)
        credit = "" if off and off["value"] == "1" else ctx.C.CREDIT
        brand = gallery_brand(s)
        import settings_io
        look = settings_io.clean_look(*(setting_(k_) for k_ in ("gallery_font", "gallery_bg", "proof_mark",
                                                                 "proof_mark_text", "proof_mark_strength")))
        brand["theme"] = dict(font=look["gallery_font"], colors=settings_io.palette(look["gallery_bg"]))

        brand["logo"] = settings_io.gallery_logo(look["gallery_bg"], brand.get("logo") or "", setting_("logo_dark_png"))
        mark = None
        if look["proof_mark"]:
            logo_ = setting_("logo_png")
            mark = dict(style=look["proof_mark"], text=look["proof_mark_text"], strength=int(look["proof_mark_strength"]),
                        logo=base64.b64decode(logo_) if look["proof_mark"] == "corner" and logo_ else None)

        def run():
            import locks
            job = JOBS[sid]

            def progress(done, total):
                job["done"], job["total"] = done, total
            lk = locks.session(sid)
            lk.acquire()
            try:
                out, n, skipped = build(gid, src, key, progress, picks=picks_cfg, credit=credit, brand=brand, mark=mark)
                with app.app_context():
                    gone = not q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or (
                        q("SELECT 1 FROM sqlite_master WHERE name='retention'", one=True)
                        and q("SELECT 1 FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (sid,), one=True))
                    if gone:
                        shutil.rmtree(out, ignore_errors=True)
                        raise RuntimeError("the session's photos were deleted while the gallery was building")
                    x("UPDATE galleries SET built_at=?, n_files=?, out_dir=?, ekey=? WHERE session_id=?",
                      (date.today().isoformat(), n, str(out) + "/", key, sid))
                    x("UPDATE sessions SET gallery_built=? WHERE id=?", (date.today().isoformat(), sid))
                    ctx.audit(f"Gallery built: {n} photos encrypted" + (f"; skipped (not a JPEG this can read): {', '.join(skipped[:10])}" if skipped else ""),
                              sid=sid, who="Lumen Aureum")
                    ctx.advance(sid)
                    live = q("SELECT 1 FROM galleries WHERE session_id=? AND upload_state='up' AND removed_at IS NULL",
                             (sid,), one=True)
                if live:
                    try:
                        if not ctx.hooks.guard("gallery_upload", lambda: do_update(sid, gid), sid=sid, gallery_id=gid,
                                               n_files=n, update=True):
                            do_update(sid, gid)
                    except Exception as e_:
                        app.logger.exception("gallery update")
                        with app.app_context():
                            x("UPDATE galleries SET upload_error=? WHERE session_id=?", (str(e_)[:200], sid))
                job["state"] = "done"
                if skipped:
                    job["error"] = "Skipped, not a JPEG this can read: " + ", ".join(skipped[:10])
            except Exception as e:
                app.logger.exception("gallery build failed")
                job.update(state="error", error=str(e) or e.__class__.__name__)
                try:
                    with app.app_context():
                        ctx.audit(f"Gallery build failed: {job['error']}", sid=sid, who="Lumen Aureum")
                except Exception:
                    app.logger.exception("gallery audit failed")
            finally:
                lk.release()

        threading.Thread(target=run, name=f"gallery-{sid}", daemon=True).start()
        return redirect(back)

    app.config.setdefault("quiet_endpoints", set()).add("picks_paste")
    @app.post("/session/<int:sid>/picks/paste", endpoint="picks_paste")
    def picks_paste(sid):

        from flask import session as _fs
        s = session(sid)
        gr = q("SELECT key, ekey FROM galleries WHERE session_id=?", (sid,), one=True)
        if not gr:
            _fs["notice"] = "This session has no gallery, so there is nothing to check the email against."
            return redirect(ctx.safe_back(request.form.get("back")) or url_for("client_stage", hid=s["household_id"]))
        pk, same, msg = read_picks_email(request.form.get("text", ""), gr["ekey"] or gr["key"])
        _fs["notice"] = msg
        if pk is not None:
            x("UPDATE sessions SET retouch_picks=?, favorites=?, veto=? WHERE id=?",
              ("\n".join(pk["retouch"]), ", ".join(pk["suggest"]), ", ".join(pk["veto"]), sid))
            ctx.audit("Picks recorded from the client's email" + (", as picked in the gallery" if same else
                      "; they differ from the gallery, so ask which are final"), sid=sid)
            if not same and not q("SELECT 1 FROM tasks WHERE session_id=? AND title=? AND done IS NULL",
                                  (sid, "Ask the client which picks are final"), one=True):
                x("INSERT INTO tasks (id, session_id, title, due, sort, stage, own) VALUES (?,?,?,?,999,?,1)",
                  (ctx.next_id("tasks"), sid, "Ask the client which picks are final", date.today().isoformat(), s["stage"]))
            ctx.advance(sid)
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("session_view", sid=sid) + "#delivery")

    @app.get("/session/<int:sid>/gallery/status", endpoint="gallery_status")
    def gallery_status(sid):
        j = JOBS.get(sid) or dict(state="idle", done=0, total=0, error="")
        return jsonify(j)

    @app.get("/gallery-preview/<gid>/", endpoint="gallery_preview")
    @app.get("/gallery-preview/<gid>/<path:p>", endpoint="gallery_preview_file")
    def gallery_preview(gid, p="index.html"):
        if not GID_OK.match(gid):
            abort(404)
        if p in ("seen.php", "picks.php"):
            return ("", 204)
        if p == "gate.php":
            p = request.args.get("f", "")
            if not re.fullmatch(r"(?:[0-9a-f]{24}|manifest)\.bin", p):
                abort(404)
        if p == "index.html":
            built = OUT_ROOT / gid / "index.html"
            if built.is_file() and _script_hashes(built.read_text(errors="replace")) != VIEWER_SCRIPT_HASHES:

                return ("<!doctype html><meta charset=utf-8><title>Rebuild to preview</title>"
                        "<p style='font:16px system-ui;margin:2em'>This gallery was built by an older version. "
                        "Rebuild it to preview it here. (The copy on the web is not affected.)</p>"), 200
        r = send_from_directory(OUT_ROOT / gid, p)
        r.headers["Cache-Control"] = "no-store"
        r.headers["Referrer-Policy"] = "no-referrer"
        if p == "index.html":
            r.headers["Content-Security-Policy"] = ctx.csp(VIEWER_SCRIPT_HASHES)
        return r

    def do_upload(sid, gid):
        with app.app_context():
            x("UPDATE galleries SET upload_state='uploading', upload_error='' WHERE session_id=?", (sid,))
        try:
            with app.app_context():
                ext = (q("SELECT extension_used FROM sessions WHERE id=?", (sid,), one=True) or {"extension_used": 0})["extension_used"]
                days_ = gdays(sid)
            k = upload_gallery(gid, days_ * (2 if ext else 1),
                               (date.today() + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat())
            con_ = ctx.connect()
            try:
                n_ = con_.execute("""UPDATE galleries SET upload_state='up', uploaded_at=?, removed_at=NULL,
                                     cw_fixed=COALESCE(cw_fixed, CASE WHEN ekey != '' AND ekey != key THEN 'word' ELSE 'none' END)
                                     WHERE session_id=? AND upload_state='uploading'""", (date.today().isoformat(), sid)).rowcount
                con_.commit()
            finally:
                con_.close()
            if n_ != 1:
                delete_gallery(gid)
                return
            mark_online(gid)
            with app.app_context():
                ctx.audit(f"Gallery uploaded ({k} files)", sid=sid, who="Lumen Aureum")
        except Exception as e:
            app.logger.exception("gallery upload")
            with app.app_context():
                x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?", (str(e)[:200], sid))

    def do_update(sid, gid):

        with app.app_context():
            x("UPDATE galleries SET upload_error='' WHERE session_id=?", (sid,))
        try:
            k = upload_gallery(gid)
            gone_ = prune_host(gid)
            with app.app_context():
                still = q("SELECT 1 FROM galleries WHERE session_id=? AND upload_state='up' AND removed_at IS NULL", (sid,), one=True)
            if not still:
                delete_gallery(gid)
                return
            mark_online(gid)
            with app.app_context():
                ctx.audit(f"Gallery updated on the web ({k} files" + (f", {gone_} old ones removed" if gone_ else "") + ")",
                          sid=sid, who="Lumen Aureum")
        except Exception as e:
            app.logger.exception("gallery update")
            with app.app_context():
                x("UPDATE galleries SET upload_error=? WHERE session_id=?", (str(e)[:200], sid))

    @app.post("/session/<int:sid>/gallery/upload", endpoint="upload_gallery_req")
    def upload_gallery_req(sid):
        s = session(sid)
        g = gal(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        if not g or not g["built_at"] or g["upload_state"] in ("awaiting", "uploading"):
            return redirect(back)
        if g["upload_state"] == "up" and not g["removed_at"]:
            gid = s["gallery_id"]
            if gid and not online_is_current(gid):
                try:
                    if not ctx.hooks.guard("gallery_upload", lambda: do_update(sid, gid), sid=sid, gallery_id=gid,
                                           n_files=g["n_files"], update=True):
                        threading.Thread(target=do_update, args=(sid, gid), name=f"gal-up-{sid}", daemon=True).start()
                except Exception as e:
                    x("UPDATE galleries SET upload_error=? WHERE session_id=?", (f"held upload failed: {e}"[:200], sid))
            return redirect(back)
        owed = ctx.release_blocked(sid)
        if owed and not request.form.get("early"):
            x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?",
              (f"balance {ctx.money(owed)} due - the gallery is released on payment", sid))
            return redirect(back)
        gid = s["gallery_id"]
        word = (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or ""
        if codeword_key(g["key"], gid, word) != (g["ekey"] or g["key"]):
            x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?",
              ("the codeword changed after the gallery was built: press Rebuild, then Upload", sid))
            return redirect(back)
        try:

            if ctx.hooks.guard("gallery_upload", lambda: do_upload(sid, gid), sid=sid, gallery_id=gid, n_files=g["n_files"]):
                x("UPDATE galleries SET upload_state='awaiting', upload_error='' WHERE session_id=?", (sid,))
                return redirect(back)
        except Exception as e:
            x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?", (f"held upload failed: {e}"[:200], sid))
            return redirect(back)
        threading.Thread(target=do_upload, args=(sid, gid), name=f"gal-up-{sid}", daemon=True).start()
        return redirect(back)

    PICKS_ON = lambda r: True

    def sync_once():

        from datetime import datetime, timezone
        rows = q("""SELECT g.*, s.gallery_id, s.first_visit, s.extension_used, s.id AS sid FROM galleries g
                    JOIN sessions s ON s.id=g.session_id WHERE g.upload_state='up' AND g.removed_at IS NULL""")
        for r in rows:
            gid = r["gallery_id"]
            try:
                if gid not in _HAS_LIMITS:
                    host = gallery_host()
                    if host.stat(f"{gid}/limits.txt") is None:
                        up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
                        with open(ASSETS / "gate.php", "rb") as f_:
                            host.put_stream(f"{gid}/gate.php", f_, content_type="text/plain")
                        put_limits(gid, gdays(r["sid"]) * (2 if r["extension_used"] else 1),
                                   (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat())
                        with open(ASSETS / "htaccess", "rb") as f_:
                            host.put_stream(f"{gid}/.htaccess", f_, content_type="text/plain")
                    _HAS_LIMITS.add(gid)
                want_days = gdays(r["sid"]) * (2 if r["extension_used"] else 1)
                if (r["limits_days"] or 0) != want_days:
                    up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
                    put_limits(gid, want_days, (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat())
                    x("UPDATE galleries SET limits_days=? WHERE session_id=?", (want_days, r["sid"]))
                first = r["first_visit"]
                if not first:
                    st = seen_at(gid)
                    if st:
                        local = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone().date().isoformat()
                        x("UPDATE sessions SET first_visit=? WHERE id=? AND first_visit IS NULL", (local, r["sid"]))
                        ctx.audit("Client opened the gallery", sid=r["sid"], who="Lumen Aureum")
                        ctx.advance(r["sid"])
                        first = local
                if PICKS_ON(r):
                    blob = picks_blob(gid)
                    if blob:
                        import hashlib as _h
                        hh = _h.sha256(blob).hexdigest()
                        if hh != (r["picks_hash"] or ""):
                            try:
                                pk = open_picks(blob, r["ekey"] or r["key"])
                            except Exception:
                                pk = None
                            x("UPDATE galleries SET picks_hash=? WHERE session_id=?", (hh, r["sid"]))
                            if pk is not None:
                                x("UPDATE sessions SET retouch_picks=?, favorites=?, veto=? WHERE id=?",
                                  ("\n".join(pk["retouch"]), ", ".join(pk["suggest"]), ", ".join(pk["veto"]), r["sid"]))
                                ctx.audit(f"Picks received from the gallery: {len(pk['retouch'])} to retouch, "
                                          f"{len(pk['suggest'])} suggested for the portfolio, {len(pk['veto'])} not to show",
                                          sid=r["sid"], who="Lumen Aureum")
                                ctx.advance(r["sid"])
                    draft_status(r, blob)
                if first:
                    gone = date.fromisoformat(first[:10]) + timedelta(days=gdays(r["sid"]) * (2 if r["extension_used"] else 1))
                else:
                    gone = date.fromisoformat(r["uploaded_at"][:10]) + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)
                if gone:
                    if date.today() > gone and app.config["clock_ok"]():
                        n = delete_gallery(gid)
                        x("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?",
                          (date.today().isoformat(), r["sid"]))
                        ctx.audit(f"Gallery removed from the web ({n} files), " + (f"{gdays(r['sid'])} day window over" if first else "never opened in a year"),
                                  sid=r["sid"], who="Lumen Aureum")
            except Exception:
                app.logger.exception("gallery sync %s", gid)

    def draft_status(r, sent):

        n = at = None
        d = picks_blob(r["gallery_id"], "draft.bin")
        if d:
            try:
                dp, n, at = open_draft(d, r["ekey"] or r["key"])
            except Exception:
                dp = None
            if dp is not None:
                try:
                    same = sent is not None and open_picks(sent, r["ekey"] or r["key"]) == dp
                except Exception:
                    same = False
                if same or (sent is None and not n):
                    n = at = None
            else:
                n = None
        if at:
            try:
                from datetime import datetime as _dt
                at = _dt.fromisoformat(at.replace("Z", "+00:00")).astimezone().isoformat(timespec="minutes")
            except ValueError:
                at = ""
        at = (at or None) if n is not None else None
        if (r["draft_n"], r["draft_at"]) != (n, at):
            x("UPDATE galleries SET draft_n=?, draft_at=? WHERE session_id=?", (n, at, r["sid"]))

    def sync_loop():
        import time
        time.sleep(20)
        while True:
            if not ctx.unlocked():
                time.sleep(60)
                continue
            try:
                with app.app_context():
                    sync_once()
            except Exception:
                app.logger.exception("gallery sync loop")
            time.sleep(SYNC_MIN * 60)

    if os.environ.get("CRM_GALLERY_SYNC", "1") == "1" and not os.environ.get("CRM_TESTING"):
        threading.Thread(target=sync_loop, name="gallery-sync", daemon=True).start()

    def take_down(sid):

        r = q("""SELECT s.gallery_id, g.upload_state FROM galleries g JOIN sessions s ON s.id=g.session_id
                 WHERE g.session_id=? AND g.upload_state IN ('up', 'uploading', 'awaiting') AND g.removed_at IS NULL""", (sid,), one=True)
        if r and r["gallery_id"]:

            x("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?", (date.today().isoformat(), sid))
            delete_gallery(r["gallery_id"])

    app.config["gallery_take_down"] = take_down
    app.config["gallery_brand"] = gallery_brand

    def gdays(sid):
        r_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
        return ctx.C.gallery_days(r_["type"] if r_ else None)

    def window_changed(sid):

        r = q("""SELECT s.gallery_id, s.extension_used, g.uploaded_at FROM galleries g JOIN sessions s ON s.id=g.session_id
                 WHERE g.session_id=? AND g.upload_state='up' AND g.removed_at IS NULL""", (sid,), one=True)
        if not r or not r["gallery_id"]:
            return
        up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
        try:
            put_limits(r["gallery_id"], gdays(sid) * (2 if r["extension_used"] else 1),
                       (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat())
        except Exception:
            app.logger.exception("gallery limits update")

    ctx.hooks.on["gallery_window_changed"].append(window_changed)

    @app.post("/gallery/sync", endpoint="gallery_sync_now")
    def gallery_sync_now():
        sync_once()
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    def block(s):
        if s["stage"] not in ("shot", "delivered", "closed") or not has_gallery_step(s):
            return []
        g = gal(s["id"])
        gid = s["gallery_id"] or ""
        src = (g["source_dir"] if g and g["source_dir"] else str(PHOTOS_ROOT / "export" / (gid or "")) + "/")
        sp = Path(src)
        try:
            src_n = len(list_jpegs(sp)) if sp.is_dir() else None
        except OSError:
            src_n = None
        job = JOBS.get(s["id"])
        days = ctx.C.gallery_days(s["type"])
        expires = exp_status = ext_date = None
        if s["first_visit"]:
            first = date.fromisoformat(s["first_visit"][:10])
            expires = first + timedelta(days=days * (2 if s["extension_used"] else 1))
            ext_date = first + timedelta(days=days * 2)
            left = (expires - date.today()).days
            exp_status = "red" if left < 0 else "amber" if left <= 1 else None
        preview = None
        if g and g["built_at"]:
            preview = url_for("gallery_preview", gid=gid) + "#k=" + (g["ekey"] or g["key"])
        pct = round(100 * job["done"] / job["total"]) if job and job.get("total") else 0
        html = render_template_string(BLOCK, s=s, g=g, job=job, pct=pct, src=src, src_n=src_n, dirs=src_dirs() if FOLDERS else [],
                                      n_up=len(uploaded(s["id"])), folders=FOLDERS,
                                      preview=preview, expires=expires, exp_status=exp_status, ext_date=ext_date,
                                      days=days, nice=ctx.nice, money=ctx.money, has_host=has_host(),
                                      owed=ctx.release_blocked(s["id"]), cw_on=bool(ctx.C.POLICY[s["type"]].get("gallery_codeword")),
                                      stale_cw=bool(g and g["built_at"] and not g["cw_fixed"] and codeword_key_cached(
                                          g["key"], gid, (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or "")
                                          != (g["ekey"] or g["key"])),
                                      back=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery",
                                      link=gallery_link(s["id"]) if g and g["upload_state"] == "up" else None,
                                      stale=bool(g and gid and g["upload_state"] == "up" and not online_is_current(gid)))
        return [("Gallery", html)]

    def step_action(label, s):
        if label == STEP_LABEL and has_gallery_step(s):
            return dict(verb="Build gallery", href=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery")
        return None

    ctx.hooks.stage_blocks.append(block)
    ctx.hooks.step_actions.append(step_action)

    def still_picking(s):

        g = gal(s["id"])
        if not (g and g["draft_n"] is not None and g["upload_state"] == "up" and not g["removed_at"]):
            return []
        n = g["draft_n"]
        return [dict(what=f"Client is still picking ({n} photo{'' if n == 1 else 's'} marked)", theirs=True,
                     since=(g["draft_at"] or "")[:10] or None, verb="Check now", post=url_for("gallery_sync_now"), fields={})]

    ctx.hooks.session_actions.append(still_picking)
