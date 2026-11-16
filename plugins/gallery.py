import base64
import functools
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

from flask import abort, jsonify, redirect, request, send_from_directory, url_for

ASSETS = Path(__file__).with_name("gallery_assets")

def _script_hashes(html):

    import hashlib
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return "".join(f" 'sha256-{base64.b64encode(hashlib.sha256(b.encode()).digest()).decode()}'" for b in blocks)

_MARK = re.sub(r"<svg ", '<svg aria-hidden="true" focusable="false" ', (Path(__file__).resolve().parent.parent / "brand" / "mark-small.svg").read_text().strip(), count=1) \
    if (Path(__file__).resolve().parent.parent / "brand" / "mark-small.svg").is_file() else ""
CREDIT_HTML = ('<a id="la" href="https://lumenaureum.org" target="_blank" rel="noopener noreferrer">' + _MARK +
               '<span>Gallery by <b>Lumen Aureum</b><small>lumenaureum.org</small></span></a>')
VIEWER_SCRIPT_HASHES = _script_hashes((ASSETS / "viewer.html").read_text())
_DATA = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent.parent / "data" / "crm.db")).parent
OUT_ROOT = Path(os.environ.get("CRM_GALLERY_ROOT", _DATA / "galleries"))
PHOTOS_ROOT = Path(os.environ.get("CRM_PHOTOS_ROOT", _DATA / "photos"))

FOLDERS = os.environ.get("CRM_PHOTO_FOLDERS", "") == "1"
DEFAULT_BASE_URL = ""
STEP_LABEL = "Gallery built"
NO_PICKS_ACTION = "Deliver without picks: the next build is the download gallery"
WEB_EDGE, THUMB_EDGE = 2048, 480
JPEG_EXT = {".jpg", ".jpeg"}
GID_OK = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

JOBS = {}
UPDATING = {}
LOCK = threading.Lock()
PRUNE_GRACE_MIN = 20

def release_memory():

    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass

def _now_iso(plus_min=0):
    from datetime import datetime
    return (datetime.now() + timedelta(minutes=plus_min)).isoformat(timespec="seconds")

def _same_picks(a, b):

    return all(sorted(a.get(k_) or []) == sorted(b.get(k_) or []) for k_ in ("retouch", "suggest", "veto"))

BLOCK = """
<div id="gallery" class="f" style="gap:8px">
{% if job and job.state == 'running' %}
  <div class="row"><span class="mbar big"><span id="gal-bar" style="width:{{ pct }}%"></span></span>
    <span id="gal-n" class="sub">{{ job.done }} / {{ job.total }}</span></div>
  <div id="gal-what" class="sub" style="font-size:13px" role="status">{{ job.label or 'Building the gallery' }}</div>
  <span hidden data-poll="{{ url_for('gallery_status', sid=s.id) }}" data-bar="gal-bar" data-count="gal-n" data-label="gal-what"></span>
{% elif s.stage == 'closed' %}
  {% if not (g and g.built_at) %}<div class="mute" style="font-size:13px">No gallery</div>{% endif %}
{% else %}
  {% if use_fin %}<form method="post" action="{{ url_for('build_gallery', sid=s.id) }}" class="row"><input type="hidden" name="from" value="finals">
    <span class="grow" style="font-size:13px">{% if n_fin %}{{ n_fin }} retouched final{{ '' if n_fin == 1 else 's' }} uploaded{% else %}<span class="mute">Upload the retouched finals in Photos</span>{% endif %}</span>
    <button class="{{ '' if g and g.built_at and dl == dl_next else 'primary' }}"{{ ' disabled' if not n_fin }}>{{ 'Build download gallery' if not dl else 'Rebuild' }}</button>
  </form>
  {% if not n_fin and n_up %}<form method="post" action="{{ url_for('build_gallery', sid=s.id) }}" class="row" data-confirm="Build the download gallery from the {{ n_up }} proofs? They aren't retouched."><input type="hidden" name="from" value="uploads"><input type="hidden" name="proofs_ok" value="1">
    <span class="grow"></span><button class="quiet">Use the proofs</button></form>{% endif %}
  {% else %}<form method="post" action="{{ url_for('build_gallery', sid=s.id) }}" class="row"><input type="hidden" name="from" value="uploads">
    <span class="grow" style="font-size:13px">{% if n_up %}{{ n_up }} edited JPEG{{ '' if n_up == 1 else 's' }} uploaded{% else %}<span class="mute">Upload the edited JPEGs in Photos first</span>{% endif %}</span>
    <button class="{{ '' if g and g.built_at and dl == dl_next else 'primary' }}"{{ ' disabled' if not n_up }}>{{ 'Build download gallery' if dl_next and not dl else 'Rebuild' if g and g.built_at else 'Build gallery' }}</button>
  </form>{% endif %}
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
{% if skip_picks %}<form method="post" action="{{ url_for('gallery_no_picks', sid=s.id) }}" class="row" data-confirm="Make the next build the download gallery, without the client's picks?"><button class="quiet">Deliver without picks</button></form>{% endif %}
{% if cw_on and not s.gallery_codeword and not (g and g.cw_fixed == 'none') %}<div class="row" style="font-size:13px"><span class="grow">This gallery asks for a codeword the client chooses. None yet.</span>
  <a class="go" href="{{ url_for('session_view', sid=s.id) }}#delivery">Add the codeword &rsaquo;</a></div>{% endif %}
{% if stale_cw %}<div style="font-size:13px;color:var(--amber, #b07a12)">The codeword changed after this build: press Rebuild so the gallery asks for the new one.</div>{% endif %}
{% if g and g.built_at %}
  <div class="row" style="font-size:14px"><span class="grow"><b>{{ g.n_files }}</b> photo{{ '' if g.n_files == 1 else 's' }}{{ ' to download' if dl }}, built {{ nice(g.built_at) }}</span>
    <a class="go" href="{{ preview }}" target="_blank" rel="noopener">Preview &rsaquo;</a></div>
  {% set us = g.upload_state or '' %}
  <div class="row" style="font-size:14px">
  {% if us == 'up' %}<span class="grow">On the web since {{ nice(g.uploaded_at) }}</span>
    {% if link %}<span hidden id="gal-link-{{ s.id }}">{{ link }}</span><button type="button" class="quiet" data-copy="#gal-link-{{ s.id }}" data-tip="The client's link, to text or email. Anyone with it can open the gallery{{ ' (with the codeword)' if '&c=1' in link }}.">Copy link</button>
    {% if mailto %}<a class="go" href="{{ mailto }}">Email link</a>{% endif %}{% endif %}
    <form method="post" action="{{ url_for('gallery_sync_now') }}" class="inline"><input type="hidden" name="back" value="{{ back }}"><button class="quiet">Check now</button></form>
  {% elif us == 'awaiting' %}<span class="grow">Waiting for approval</span>
  {% elif us == 'uploading' %}<span class="grow">Uploading</span>
  {% elif us == 'removed' %}<span class="grow"{% if g.dark_at %} data-tip="Its link now says the gallery has gone dark and gives your email, for a year"{% endif %}>Removed from the web {{ nice(g.removed_at) }}</span>
    {% if can_restore %}<details class="closemenu"><summary title="More" aria-label="More actions for this gallery">&hellip;</summary><div class="cm-pop">
      <div class="cm-head">Put back online</div>
      {% for n_ in ([7, 14, usual] | unique | sort) %}<form method="post" action="{{ url_for('gallery_back_online', sid=s.id) }}" data-confirm="Put this gallery back online for {{ n_ }} days? The client's link works again until then, and the last days show the sunset notice."><input type="hidden" name="days" value="{{ n_ }}"><button class="cm-act">For {{ n_ }} days</button></form>{% endfor %}
      {% if can_once %}<form method="post" action="{{ url_for('gallery_back_online', sid=s.id) }}" data-confirm="Put this gallery back online for one download? The client's link works until Download all has finished, then it goes dark (or after {{ once_days }} days if it isn't used)."><input type="hidden" name="mode" value="once"><button class="cm-act">For one download</button></form>{% endif %}</div></details>{% endif %}
  {% elif not has_host %}<span class="grow mute">Previews work here; putting it online needs a web host.</span>
    <a class="go" href="{{ url_for('setup_step', step='galleries') }}">Set up galleries &rsaquo;</a>
  {% elif owed and not proof %}<span class="grow">{{ money(owed) }} still due. Your terms release the gallery on payment.</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline" data-confirm="{{ 'Put the download gallery online before the balance is paid? The client can download the full-size photos right away.' if dl else 'Put the gallery online before the balance is paid? Use this for a proofing gallery the client picks from.' }}"><input type="hidden" name="early" value="1"><button class="quiet">Upload before payment</button></form>
  {% else %}<span class="grow">{% if us == 'declined' %}Upload declined{% elif us == 'expired' %}Tap expired{% elif us == 'error' and not (g.upload_error or '').startswith('balance') %}<span style="color:var(--red)">Upload failed: {{ g.upload_error }}</span>{% elif owed and proof %}These proofs carry your mark, so they can go online before payment{% else %}Ready to upload{% endif %}</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline"><button class="primary">Upload</button></form>{% endif %}
  </div>
  {% if moved_off %}<div class="row" style="font-size:14px"><span class="grow" style="color:var(--amber, #b07a12)">This gallery is still on your earlier gallery host.</span>
    <a class="go" href="{{ url_for('galleries_move') }}">Move galleries &rsaquo;</a></div>{% endif %}
  {% if new_link %}<div class="row" style="font-size:14px"><span class="grow" style="color:var(--amber, #b07a12)">The gallery address changed. Send the client the new link (Copy link or Email link above).</span>
    <form method="post" action="{{ url_for('gallery_link_sent', sid=s.id) }}" class="inline"><input type="hidden" name="back" value="{{ back }}"><button class="quiet">Sent</button></form></div>{% endif %}
  {% if us == 'up' and updating %}{% if not (job and job.state == 'running') %}<div class="row" style="font-size:14px"><span class="grow" role="status"><span id="gal-up-what">Updating the copy online{% if updating.total %}: file {{ updating.done }} of {{ updating.total }}{% endif %}</span>. The client's link keeps working meanwhile.</span></div>
  <span hidden data-poll="{{ url_for('gallery_status', sid=s.id) }}" data-label="gal-up-what"></span>{% endif %}
  {% elif us == 'up' and stale and dl != dl_online %}<div class="row" style="font-size:14px">{% if owed and not proof %}<span class="grow">{{ money(owed) }} still due. Your terms release the download gallery on payment.</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline" data-confirm="Put the download gallery online before the balance is paid? The client can download the full-size photos right away."><input type="hidden" name="early" value="1"><button class="quiet">Upload before payment</button></form>
    {% else %}<span class="grow">{{ 'The proofing gallery is online. Upload puts the download gallery in its place.' if dl else 'The download gallery is online. Upload puts this build in its place.' }}</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline"><button class="primary">Upload</button></form>{% endif %}</div>
  {% elif us == 'up' and stale %}<div class="row" style="font-size:14px"><span class="grow" style="color:var(--amber, #b07a12)">The copy online is older than this build{% if g.upload_error %}. The upload didn't finish: {{ g.upload_error }}{% endif %}</span>
    <form method="post" action="{{ url_for('upload_gallery_req', sid=s.id) }}" class="inline"><button class="primary">Upload the new build</button></form></div>{% endif %}
  {% if us == 'removed' %}{# down already: no planned deletion to show #}
  {% elif expires %}
  <div class="row" style="font-size:14px">
    <span class="grow" {% if exp_status %}style="color:var(--{{ exp_status }})"{% endif %}
      data-tip="{{ 'Put back online' if g.restored_at and us != 'removed' else 'Opened' }} {{ nice(opened) }}, plus {{ days }} days{{ ' and the extension' if s.extension_used and not g.restored_at }}">{% if once_on %}One download, then dark. At the latest {{ nice(expires.isoformat()) }}{% else %}Planned deletion {{ nice(expires.isoformat()) }}{% endif %}</span>
    {% if s.extension_used and not g.restored_at %}<span class="mute" style="font-size:12px">extended</span>{% endif %}
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
            try:
                lg = Image.open(io.BytesIO(logo))
                if lg.width * lg.height > 4 * 600 * 240:
                    raise ValueError("logo too large")
                lg = lg.convert("RGBA")
            except Exception:
                logo = None
        if logo:
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

def file_meta(creator, rights):

    import unicodedata
    from xml.sax.saxutils import escape
    from PIL import Image
    ascii_ = lambda t: unicodedata.normalize("NFKD", t.replace("\u00a9", "(c)")).encode("ascii", "ignore").decode()
    exif = Image.Exif()
    if creator:
        exif[0x013B] = ascii_(creator)
    if rights:
        exif[0x8298] = ascii_(rights)
    desc = "".join(
        [f'<dc:creator><rdf:Seq><rdf:li>{escape(creator)}</rdf:li></rdf:Seq></dc:creator>' if creator else ""]
        + [f'<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">{escape(rights)}</rdf:li></rdf:Alt></dc:rights>' if rights else ""])
    xmp = ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
           '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about=""'
           ' xmlns:dc="http://purl.org/dc/elements/1.1/">' + desc + '</rdf:Description></rdf:RDF></x:xmpmeta>'
           '<?xpacket end="w"?>') if desc else ""
    return exif.tobytes() if (creator or rights) else b"", xmp.encode("utf-8")

def clean_jpeg(path, mark=None, full=False, meta=None):

    from PIL import Image, ImageCms, ImageOps
    with Image.open(path) as im:
        if im.format == "JPEG" and not full:
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
    if not full:
        return web, wsize, thumb, tsize
    b = io.BytesIO()
    exif_, xmp_ = meta or (b"", b"")
    extra = {**({"exif": exif_} if exif_ else {}), **({"xmp": xmp_} if xmp_ else {})}
    pixels.save(b, "JPEG", quality=95, optimize=True, progressive=True, **extra)
    return web, wsize, thumb, tsize, b.getvalue(), pixels.size

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
THEME_FONT_LIGHT = "font-light.woff2"

OWN_FONT = lambda: (None, None)

ZIP_PART = 8 * 1024 * 1024
ZIP_SPLIT = 500 * 1024 * 1024
DL_MARK = ".download"
WAS_MARK = ".online-was"

def zip_names(names):

    out, seen = [], set()
    for i, n in enumerate(names):
        n = re.sub(r"[\x00-\x1f\x7f]", "", str(n).replace("\\", "/").rsplit("/", 1)[-1]).strip() or f"photo-{i + 1:03d}.jpg"
        stem, dot, ext = n.rpartition(".")
        stem, ext = (stem, "." + ext) if dot and stem else (n, "")
        cand, k = n, 2
        while cand.lower() in seen:
            cand, k = f"{stem} ({k}){ext}", k + 1
        seen.add(cand.lower())
        out.append(cand)
    return out

def build(gid, src, key, progress=lambda done, total: None, picks=None, credit="", brand=None, mark=None, download=False):

    import zipfile
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(key_bytes(key))
    files = [opener for _, opener in src] if isinstance(src, list) else list_jpegs(src)
    names = [n_ for n_, _ in src] if isinstance(src, list) else [p_.name for p_ in files]
    if not files:
        raise ValueError("No JPEGs in folder")
    if download:
        mark = None
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    final = OUT_ROOT / gid
    tmp = OUT_ROOT / f".{gid}.building"
    plain_zip = OUT_ROOT / f".{gid}.zip"
    shutil.rmtree(tmp, ignore_errors=True)
    plain_zip.unlink(missing_ok=True)
    tmp.mkdir()
    used, items = set(), []

    def fresh():
        while True:
            n = secrets.token_hex(12) + ".bin"
            if n not in used:
                used.add(n)
                return n

    meta = None
    if download:
        b_ = brand or {}
        owner = (b_.get("studio") or b_.get("photographer") or "").strip()
        year = (re.findall(r"\b(\d{4})$", str(b_.get("date") or "")) or [str(date.today().year)])[0]
        meta = file_meta(owner, f"\u00a9 {year} {owner}. All rights reserved." if owner and b_.get("owns") is True else "")
    progress(0, len(files))
    skipped = []
    zips = []
    zf, zsize = None, 0

    def seal_zip():
        nonlocal zf, zsize
        zf.close()
        zf = None
        parts, size = [], plain_zip.stat().st_size
        with open(plain_zip, "rb") as fz:
            while True:
                chunk = fz.read(ZIP_PART)
                if not chunk:
                    break
                n_ = fresh()
                (tmp / n_).write_bytes(seal(aes, chunk, n_))
                parts.append(n_)
        plain_zip.unlink()
        zips.append(dict(parts=parts, size=size))
        zsize = 0

    try:
        in_zip = zip_names(names)
        stamp = date.today().timetuple()[:3] + (12, 0, 0)
        for i, p in enumerate(files):
            try:
                out = clean_jpeg(p() if callable(p) else p, mark, full=download, meta=meta)
            except Exception:
                skipped.append(names[i])
                progress(i + 1, len(files))
                continue
            web, (w, h), thumb, (tw, th) = out[:4]
            f, t = fresh(), fresh()
            (tmp / f).write_bytes(seal(aes, web, f))
            (tmp / t).write_bytes(seal(aes, thumb, t))
            item = dict(f=f, t=t, w=w, h=h, tw=tw, th=th, n=names[i])
            if download:
                o = fresh()
                (tmp / o).write_bytes(seal(aes, out[4], o))
                item.update(o=o, ow=out[5][0], oh=out[5][1], on=in_zip[i], os=len(out[4]))
                adds = len(out[4]) + 2 * len(in_zip[i].encode()) + 140
                if zf is not None and zsize + adds > ZIP_SPLIT:
                    seal_zip()
                if zf is None:
                    zf = zipfile.ZipFile(plain_zip, "w", zipfile.ZIP_STORED)
                zf.writestr(zipfile.ZipInfo(in_zip[i], stamp), out[4])
                zsize += adds
                item["z"] = len(zips)
            items.append(item)
            del out
            progress(i + 1, len(files))
        if not items:
            raise ValueError("None of these files is a JPEG photo this can read: " + ", ".join(skipped[:5]))
        if zf is not None:
            seal_zip()
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        if zf is not None:
            zf.close()
        plain_zip.unlink(missing_ok=True)
    brand = dict(brand or {})
    font = THEME_FONTS.get(((brand.get("theme") or {}).get("font")) or "")
    own = OWN_FONT() if ((brand.get("theme") or {}).get("font")) == "own" else (None, None)
    if (brand.get("theme") or {}).get("font") == "own":
        brand["theme"] = dict(brand["theme"], font="own" if own[0] else "classic", light=bool(own[0] and own[1]))
        if own[0]:
            import settings_io
            wr, wl = settings_io.own_font_weights(own[0], own[1])
            brand["theme"].update(wr=wr, **({"wl": wl} if wl else {}))
    manifest = dict(v=1, title=brand.pop("client", "") or "Your photos", items=items, picks=picks or {}, brand=brand)
    if download:
        base = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', "", manifest["title"]).strip() or "Photos"
        for j, z in enumerate(zips):
            z["name"] = base + (f"-part-{j + 1}-of-{len(zips)}" if len(zips) > 1 else "") + ".zip"
        manifest.update(dl=1, zips=zips)
    (tmp / "manifest.bin").write_bytes(seal(aes, json.dumps(manifest, separators=(",", ":")).encode(), "manifest.bin"))
    (tmp / "index.html").write_text(viewer_page(gid, gate=bool((picks or {}).get("post")), credit=bool(credit)))
    shutil.copyfile(ASSETS / "seen.php", tmp / "seen.php")
    shutil.copyfile(ASSETS / "picks.php", tmp / "picks.php")
    shutil.copyfile(ASSETS / "gate.php", tmp / "gate.php")
    import hashlib

    proof = hashlib.sha256(key_bytes(key)).hexdigest()[:32]
    (tmp / "seen-key.php").write_text("<?php exit; ?>\n" + hashlib.sha256(proof.encode()).hexdigest() + "\n")
    shutil.copyfile(ASSETS / "htaccess", tmp / ".htaccess")
    if font:
        shutil.copyfile(ASSETS / "fonts" / font, tmp / THEME_FONT_FILE)
    if own[0]:
        (tmp / THEME_FONT_FILE).write_bytes(own[0])
        if own[1]:
            (tmp / THEME_FONT_LIGHT).write_bytes(own[1])
    if download:
        (tmp / DL_MARK).write_text("1\n")
    was = online_mode(gid)
    if was is not None:
        (tmp / WAS_MARK).write_text(("download" if was else "proofing") + "\n")
    old = OUT_ROOT / f".{gid}.old"
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        final.rename(old)
    tmp.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return Built(final, len(items), skipped)

def viewer_page(gid, gate, credit):

    page = (ASSETS / "viewer.html").read_text()
    page = page.replace('<html lang="en">', f'<html lang="en" data-gid="{gid}">', 1)
    if gate:
        page = page.replace('<html lang="en"', '<html lang="en" data-gate="1"', 1)
    page = page.replace("<span hidden data-la-logo></span>", LA_LOGO, 1)
    if credit:
        page = page.replace("<span hidden data-credit></span>", CREDIT_HTML)
    return page

def is_download(gid):

    return bool(gid) and GID_OK.match(gid) is not None and (OUT_ROOT / gid / DL_MARK).is_file()

def online_mode(gid):

    if not gid or not GID_OK.match(gid) or not (OUT_ROOT / gid).is_dir():
        return None
    if online_is_current(gid):
        return is_download(gid)
    try:
        was = (OUT_ROOT / gid / WAS_MARK).read_text().strip()
    except OSError:
        return None
    return {"download": True, "proofing": False}.get(was)

def online_download(gid):

    return bool(online_mode(gid))

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

SUNSET_DAYS = 7

def sunset_days(days):

    return min(SUNSET_DAYS, max(1, -(-int(days) // 4)))

EMAIL_OK = re.compile(r"[^\s\"'<>\\=;&?#%]{1,64}@[^\s\"'<>\\=;&?#%@]+\.[^\s\"'<>\\=;&?#%@]+")
CONTACT = lambda: ""

def contact_email():
    try:
        e = (CONTACT() or "").strip()
    except Exception:
        return ""
    return e if len(e) <= 254 and EMAIL_OK.fullmatch(e) else ""

ONCE_DAYS = 7
ONCE_GRACE_MIN = 60

def zip_pieces(gid, key):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not GID_OK.match(gid or ""):
        return []
    try:
        blob = (OUT_ROOT / gid / "manifest.bin").read_bytes()
        m = json.loads(AESGCM(key_bytes(key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))
    except Exception:
        return []
    names = [p for z in (m.get("zips") or []) if isinstance(z, dict) for p in (z.get("parts") or [])]
    return [n for n in names if isinstance(n, str) and re.fullmatch(r"[0-9a-f]{24}\.bin", n)]

def gate_built(gid):

    try:
        return 'data-gate="1"' in (OUT_ROOT / gid / "index.html").read_text()[:400]
    except OSError:
        return False

def done_at(gid):

    import io
    host = gallery_host()
    if host.stat(f"{gid}/done.txt") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/done.txt", buf)
    t = buf.getvalue().decode().strip()
    return int(t) if t.isdigit() else None

def day_close(day):

    from datetime import datetime, time as _t
    d = date.fromisoformat(str(day)[:10])
    return int(datetime.combine(d + timedelta(days=1), _t.min).astimezone().timestamp()) - 1

def put_limits(gid, days, unopened_until, host=None, until=None, once=None):

    import io
    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    unopened_until = date.fromisoformat(str(unopened_until)[:10]).isoformat()
    body = (f"days={int(days)}\nunopened_until={unopened_until}\nunopened_ts={day_close(unopened_until)}\n"
            f"warn_days={sunset_days(days)}\n")
    if until:
        until = date.fromisoformat(str(until)[:10]).isoformat()
        body += f"until={until}\nuntil_ts={day_close(until)}\n"
    once = [n for n in (once or []) if re.fullmatch(r"[0-9a-f]{24}\.bin", n)]
    if once:
        body += f"once={','.join(once)}\n"
    if contact_email():
        body += f'contact="{contact_email()}"\n'
    try:
        if "async function keyProof" in (OUT_ROOT / gid / "index.html").read_text():
            body += "proof=1\n"
    except OSError:
        pass
    body = body.encode()
    (host or gallery_host()).put_stream(f"{gid}/limits.txt", io.BytesIO(body), content_type="text/plain")

CACHE_FRESH = "no-cache"
CACHE_FOREVER = "public, max-age=31536000, immutable"

def cache_for(name):
    if name in ("index.html", "manifest.bin"):
        return CACHE_FRESH
    if re.fullmatch(r"[0-9a-f]{24}\.bin", name):
        return CACHE_FOREVER
    return CACHE_FRESH

def upload_gallery(gid, days=None, unopened_until=None, progress=lambda done, total: None, until=None, once=None):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    files = sorted((p for p in (OUT_ROOT / gid).iterdir() if p.is_file() and p.name not in (ONLINE_MARK, DL_MARK, WAS_MARK)),
                   key=lambda p: (p.name == "index.html", p.name == "manifest.bin"))
    with host.session():
        if days is not None:
            put_limits(gid, days, unopened_until, host=host, until=until, once=once)
        progress(0, len(files))
        for i, p in enumerate(files):

            src = ASSETS / HOST_SCRIPTS[p.name] if p.name in HOST_SCRIPTS else p
            with open(src, "rb") as f:
                host.put_stream(f"{gid}/{p.name}", f, content_type=MIME.get(p.suffix or p.name), cache_control=cache_for(p.name))
            progress(i + 1, len(files))
    return len(files) + (1 if days is not None else 0)

HOST_SCRIPTS = {"gate.php": "gate.php", "picks.php": "picks.php", "seen.php": "seen.php", ".htaccess": "htaccess"}

ONLINE_MARK = ".online"

def mark_online(gid):
    if GID_OK.match(gid) and (OUT_ROOT / gid).is_dir():
        (OUT_ROOT / gid / ONLINE_MARK).write_text(date.today().isoformat() + "\n")

def online_is_current(gid):

    return (OUT_ROOT / gid / ONLINE_MARK).is_file()

HOST_KEEPS = {"limits.txt", "seen.txt", "picks.bin", "draft.bin", ".htaccess", "once.txt", "done.txt"}

def prune_host(gid):

    import logging
    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    have = {p.name for p in (OUT_ROOT / gid).iterdir() if p.is_file()}
    gone, skipped = 0, []
    with host.session():
        for n in host.list(f"{gid}/"):
            base = n.rsplit("/", 1)[-1]
            if base in have or base in HOST_KEEPS:
                continue
            if re.fullmatch(r"[0-9a-f]{24}\.bin", base) or base in (THEME_FONT_FILE, THEME_FONT_LIGHT):
                try:
                    host.delete(n)
                    gone += 1
                except Exception as e:
                    logging.getLogger(__name__).warning("gallery prune: skipped %s: %s", n, e)
                    skipped.append(base)
    return gone, skipped

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

def picks_blob(gid, name="picks.bin", key=None):

    import io
    host = gallery_host()
    if host.stat(f"{gid}/{name}") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/{name}", buf)
    keep_host_copy(gid, name, buf.getvalue(), key)
    return buf.getvalue()

KEPT_ROOT = OUT_ROOT.parent / (OUT_ROOT.name + "-kept")

def keep_host_copy(gid, name, blob, key=None):

    if not (GID_OK.match(gid or "") and name in ("picks.bin", "draft.bin") and blob and len(blob) <= PICKS_MAX):
        return
    try:
        d = KEPT_ROOT / gid
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        if key is not None and p.is_file():
            had = p.read_bytes()
            if had != blob and newest(key, name, blob, had) is had:
                return
        if not p.is_file() or p.read_bytes() != blob:
            tmp = d / (name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, p)
    except OSError:
        pass

def newest(key, name, *blobs):

    best, t_best = None, None
    for b in blobs:
        if not b or len(b) > PICKS_MAX:
            continue
        t = sealed_at(b, key, name)
        if t is not None and (t_best is None or t > t_best):
            best, t_best = b, t
    if best is not None:
        return best
    return next((b for b in blobs if b and len(b) <= PICKS_MAX), None)

def kept_copy(gid, name):
    try:
        return (KEPT_ROOT / gid / name).read_bytes() if GID_OK.match(gid or "") else None
    except OSError:
        return None

def earlier_host_cfgs(cfg=None):

    if cfg is None:
        from vault import web as VW
        cfg = VW.config()
    cur = cfg.get("gallery_host")
    out = []
    for h in list(cfg.get("gallery_hosts_prev") or []) + [cfg.get("gallery_host_prev")]:
        if not isinstance(h, dict) or not h.get("type"):
            continue
        if cur and host_identity(h) == host_identity(cur):
            continue
        if any(host_identity(h) == host_identity(o) for o in out):
            continue
        out.append(h)
    return out

def _put_earlier(cfg, hosts):
    cfg.pop("gallery_host_prev", None)
    if hosts:
        cfg["gallery_hosts_prev"] = hosts
    else:
        cfg.pop("gallery_hosts_prev", None)

def setup_host(cfg, new):

    was = cfg.get("gallery_host") or cfg.get("gallery_host_off")
    cfg.pop("gallery_host", None)
    hosts = earlier_host_cfgs(cfg)
    if was and host_identity(was) != host_identity(new):
        hosts = [h for h in hosts if host_identity(h) != host_identity(was)] + [was]
    _put_earlier(cfg, [h for h in hosts if host_identity(h) != host_identity(new)])
    cfg["gallery_host"] = new
    cfg.pop("gallery_host_off", None)

def setup_off(cfg):

    if cfg.get("gallery_host"):
        cfg["gallery_host_off"] = cfg.pop("gallery_host")
    _put_earlier(cfg, earlier_host_cfgs(cfg))

def host_cfg_for(fp, cfg=None):

    if not fp:
        return None
    if cfg is None:
        from vault import web as VW
        cfg = VW.config()
    for h in [cfg.get("gallery_host"), cfg.get("gallery_host_off")] + earlier_host_cfgs(cfg):
        if h and fingerprint_of(h) == fp:
            return h
    return None

def host_label(cfg):

    cfg = dict(cfg or {})
    t = cfg.get("type")
    if t == "sftp":
        port = str(cfg.get("port") or 22)
        return str(cfg.get("host") or "an SFTP server").strip() + ("" if port == "22" else f":{port}")
    if t == "s3":
        ep = re.sub(r"^https?://", "", str(cfg.get("endpoint") or "").strip()).rstrip("/")
        return f"the bucket {str(cfg.get('bucket') or '').strip()}" + (f" at {ep}" if ep else "")
    if t == "local":
        return "a folder on this computer (" + (os.path.basename(str(cfg.get("path") or "").rstrip("/")) or "/") + ")"
    return t or "a gallery host"

UNREACHABLE_RETRY = 24 * 3600
UNREACHABLE_KEY = "gallery_unreachable:"
_UNREACHABLE = {}
_DB = None

def _unreach_db(fn):

    if _DB is None:
        return None
    app_, q_, x_ = _DB
    try:
        from flask import has_app_context
        if has_app_context():
            return (fn(q_, x_),)
        with app_.app_context():
            return (fn(q_, x_),)
    except Exception:
        return None

def may_try(fp):
    got = _unreach_db(lambda q_, x_: q_("SELECT value FROM settings WHERE key=?", (UNREACHABLE_KEY + fp,), one=True))
    t = _UNREACHABLE.get(fp)
    if got and got[0]:
        try:
            t = max(t or 0, float(got[0]["value"]))
        except (TypeError, ValueError):
            pass
    return not t or time.time() - t >= UNREACHABLE_RETRY

def _host_failed(fp):
    now = time.time()
    if not _unreach_db(lambda q_, x_: x_(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (UNREACHABLE_KEY + fp, str(now)))):
        _UNREACHABLE[fp] = now

def _host_reached(fp):
    _UNREACHABLE.pop(fp, None)
    _unreach_db(lambda q_, x_: x_("DELETE FROM settings WHERE key=?", (UNREACHABLE_KEY + fp,)))

def open_host(cfg):
    from vault import backends as VB
    return VB.from_config(dict(cfg, public=True))

def retime_limits(gid, host):

    raw = _read(host, f"{gid}/limits.txt")
    if raw is None:
        return False
    lines, changed = raw.decode("utf-8", "replace").split("\n"), False
    dates = dict(l_.split("=", 1) for l_ in lines if "=" in l_)
    for i, l_ in enumerate(lines):
        k = l_.split("=", 1)[0]
        day = dates.get({"unopened_ts": "unopened_until", "until_ts": "until"}.get(k, ""))
        if day:
            try:
                want = f"{k}={day_close(day.strip())}"
            except ValueError:
                continue
            if l_ != want:
                lines[i], changed = want, True
    if changed:
        host.put_stream(f"{gid}/limits.txt", io.BytesIO("\n".join(lines).encode()), content_type="text/plain")
    return changed

def _read(host, path):
    import io as _io
    if host.stat(path) is None:
        return None
    buf = _io.BytesIO()
    host.get_stream(path, buf)
    return buf.getvalue()

def sealed_at(blob, key, name):

    from datetime import datetime as _dt, timezone as _tz
    try:
        at = str(_open_sealed(blob, key, PICKS_AAD if name == "picks.bin" else DRAFT_AAD).get("at") or "")
    except Exception:
        return None
    try:
        d = _dt.fromisoformat(at.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=_tz.utc)).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0

def same_folder(old, new, gid):

    probe = f"{gid}/.la-probe-{secrets.token_hex(6)}"
    new.put_stream(probe, io.BytesIO(b"1"), content_type="text/plain")
    try:
        return old.stat(probe) is not None
    finally:
        try:
            new.delete(probe)
        except Exception:
            pass

def refresh_old_page(old, gid):

    try:
        page = _read(old, f"{gid}/index.html")
        if not page:
            return False
        text = page.decode("utf-8", "replace")
        if _script_hashes(text) == VIEWER_SCRIPT_HASHES:
            return False
        head = text[:400]
        if 'data-gate="1"' not in head or f'data-gid="{gid}"' not in head:
            return False
        for name in ("gate.php", "seen.php"):
            with open(ASSETS / name, "rb") as f_:
                old.put_stream(f"{gid}/{name}", f_, content_type=MIME[".php"], cache_control=CACHE_FRESH)
        old.put_stream(f"{gid}/index.html", io.BytesIO(viewer_page(gid, gate=True, credit='id="la"' in text).encode()),
                       content_type=MIME[".html"], cache_control=CACHE_FRESH)
        return True
    except Exception:
        return False

def hand_over(old, new, gid, key):

    if same_folder(old, new, gid):
        return []
    lim = _read(old, f"{gid}/limits.txt")
    if lim is not None and b"moved=" not in lim:
        with open(ASSETS / "picks.php", "rb") as f_:
            old.put_stream(f"{gid}/picks.php", f_, content_type=MIME[".php"], cache_control=CACHE_FRESH)
        add = b"\nmoved=1\n"
        if b"contact=" not in lim and contact_email():
            add += f'contact="{contact_email()}"\n'.encode()
        old.put_stream(f"{gid}/limits.txt", io.BytesIO(lim.rstrip(b"\n") + add), content_type="text/plain")
        refresh_old_page(old, gid)
    got = []
    for name in ("picks.bin", "draft.bin"):
        theirs = _read(old, f"{gid}/{name}")
        if not theirs or len(theirs) > PICKS_MAX:
            continue
        t_old = sealed_at(theirs, key, name)
        if t_old is None:
            continue
        ours = _read(new, f"{gid}/{name}")
        if ours == theirs:
            continue
        t_new = sealed_at(ours, key, name) if ours else None
        if t_new is None or t_old > t_new:
            new.put_stream(f"{gid}/{name}", io.BytesIO(theirs), content_type="application/octet-stream")
            keep_host_copy(gid, name, theirs, key)
            got.append(name)
    return got

def host_identity(cfg):

    import posixpath
    cfg = dict(cfg or {})
    t = cfg.get("type")
    if not t:
        return None
    def _dir(p_):
        p_ = posixpath.normpath(str(p_ or ".").strip() or ".")
        return "/" if p_ == "//" else p_
    if t == "local":
        p_ = str(cfg.get("path") or "").strip()
        return ("local", os.path.realpath(os.path.expanduser(p_)) if p_ else "")
    if t == "sftp":
        try:
            port = int(cfg.get("port") or 22)
        except (TypeError, ValueError):
            port = 22
        return ("sftp", str(cfg.get("host") or "").strip().lower().rstrip("."), port, str(cfg.get("user") or "").strip(),
                _dir(cfg.get("root")))
    if t == "s3":
        ep = re.sub(r"^https?://", "", str(cfg.get("endpoint") or "").strip().lower()).rstrip("/")
        if not ep or re.fullmatch(r"s3([.-][a-z0-9-]+)?\.amazonaws\.com", ep):
            ep = "aws"
        return ("s3", ep, str(cfg.get("bucket") or "").strip().lower(), str(cfg.get("prefix") or "").strip().strip("/"))
    return (t,) + tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in cfg.items()
                               if k not in ("secret_key", "password", "private_key", "known_hosts", "host_key", "user",
                                            "access_key", "key_file", "public")))

def fingerprint_of(cfg):
    import hashlib
    ident = host_identity(cfg)
    return hashlib.sha256(json.dumps(ident).encode()).hexdigest()[:16] if ident else ""

def legacy_fingerprint_of(cfg):

    import hashlib
    cfg = dict(cfg or {})
    if not cfg:
        return ""
    for k in ("secret_key", "password", "private_key", "known_hosts", "host_key"):
        cfg.pop(k, None)
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]

def host_fingerprint():

    from vault import web as VW
    return fingerprint_of(VW.config().get("gallery_host"))

def same_place(cur_cfg, old_cfg):

    import io
    import secrets as _s
    from vault import backends as VB
    if host_identity(cur_cfg) == host_identity(old_cfg):
        return True
    cur = VB.from_config(dict(cur_cfg, public=True))
    old = VB.from_config(dict(old_cfg, public=True))
    name = f".la-probe-{_s.token_hex(8)}.txt"
    cur.put_stream(name, io.BytesIO(b"lumen-aureum probe"), content_type="text/plain")
    try:
        return old.stat(name) is not None
    finally:
        try:
            cur.delete(name)
        except Exception:
            pass

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

def delete_gallery(gid, host=None):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = host or gallery_host()
    with host.session():
        names = host.list(f"{gid}/") + [f"{gid}/.htaccess"]
        for n in names:
            host.delete(n)
        if any(host.stat(n) is not None for n in names):
            raise IOError("gallery files still on the host after delete")
        host.remove_dir(f"{gid}/")
    return len(names)

def remove_from(gid, host):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    with host.session():
        names = host.list(f"{gid}/")
        if not names and host.stat(f"{gid}/.htaccess") is None:
            return 0
        delete_gallery(gid, host=host)
    return len(names)

LA_LOGO = (Path(__file__).resolve().parent / "gallery_assets" / "la-logo.svg").read_text().strip()
DARK_KEEP_DAYS = 365

def dark_page(studio="", email=""):

    import html as _h
    email = email if email and len(email) <= 254 and EMAIL_OK.fullmatch(email) else ""
    who = f"Your photographer, {_h.escape(studio)}, may still be able to help" if studio else "Your photographer may still be able to help"
    reach = (f"<p>{who}:</p><p><a href=\"mailto:{_h.escape(email)}\">{_h.escape(email)}</a></p>" if email else f"<p>{who}.</p>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>This gallery has gone dark</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #12100e; color: #efe3cc;
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; text-align: center; padding: 24px 16px; box-sizing: border-box; }}
main {{ max-width: 460px; }}
.mark svg {{ display: block; width: min(220px, 60vw); height: auto; margin: 0 auto 28px; }}
h1 {{ font: 400 30px/1.25 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; margin: 0 0 12px; }}
p {{ margin: 0 0 6px; color: #a99c86; }} a {{ color: #e9b45c; overflow-wrap: anywhere; }}
</style></head>
<body><main><div class="mark">{LA_LOGO}</div><h1>This gallery has gone dark.</h1>{reach}</main></body></html>
""".encode()

def go_dark(gid, studio="", email=""):

    n = delete_gallery(gid)
    return n, put_dark(gid, studio, email)

def put_dark(gid, studio="", email="", host=None):

    try:
        (host or gallery_host()).put_stream(f"{gid}/index.html", io.BytesIO(dark_page(studio, email)),
                                            content_type=MIME[".html"], cache_control=CACHE_FRESH)
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).exception("gallery dark page %s", gid)
        return False

def clear_dark(gid):

    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    with host.session():
        for n in host.list(f"{gid}/"):
            host.delete(n)
        host.remove_dir(f"{gid}/")

class _Stranded(Exception):
    pass

def register(ctx):
    global CONTACT, _DB
    app, q, x = ctx.app, ctx.q, ctx.x
    _DB = (app, q, x)

    def _contact():
        with app.app_context():
            r_ = q("SELECT value FROM settings WHERE key='business_email'", one=True)
            return (r_["value"] if r_ and r_["value"] else "").strip()
    CONTACT = _contact
    from flask import render_template_string
    ctx.migrate("""CREATE TABLE IF NOT EXISTS galleries (
        session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        source_dir TEXT DEFAULT '', built_at TEXT, n_files INTEGER DEFAULT 0, out_dir TEXT DEFAULT '',
        key TEXT NOT NULL);""")

    ctx.migrate("""CREATE TABLE IF NOT EXISTS gallery_leftovers (
        gallery_id TEXT NOT NULL, host_fp TEXT NOT NULL, added TEXT, PRIMARY KEY (gallery_id, host_fp));""")
    @ctx.on_unlock
    def _migrate_cols():
        _con = ctx.connect()
        _gcols = [r_[1] for r_ in _con.execute("PRAGMA table_info(galleries)")]
        if "picks_hash" not in _gcols:
            ctx.add_column(_con, "galleries", "picks_hash", "TEXT DEFAULT ''")
        if "limits_days" not in _gcols:
            ctx.add_column(_con, "galleries", "limits_days", "INTEGER")
        if "limits_contact" not in _gcols:
            ctx.add_column(_con, "galleries", "limits_contact", "TEXT")
        if "dark_at" not in _gcols:
            ctx.add_column(_con, "galleries", "dark_at", "TEXT")
        if "restored_at" not in _gcols:
            ctx.add_column(_con, "galleries", "restored_at", "TEXT")
            ctx.add_column(_con, "galleries", "restore_days", "INTEGER")
            ctx.add_column(_con, "galleries", "restore_once", "TEXT")
        stamp_hosts = "host_fp" not in _gcols
        if stamp_hosts:
            ctx.add_column(_con, "galleries", "host_fp", "TEXT")
            ctx.add_column(_con, "galleries", "link_base", "TEXT")
        if "moved_from" not in _gcols:
            ctx.add_column(_con, "galleries", "moved_from", "TEXT")
        if "ekey" not in _gcols:
            ctx.add_column(_con, "galleries", "ekey", "TEXT DEFAULT ''")
        if "draft_n" not in _gcols:
            ctx.add_column(_con, "galleries", "draft_n", "INTEGER")
            ctx.add_column(_con, "galleries", "draft_at", "TEXT")
        if "proof" not in _gcols:
            ctx.add_column(_con, "galleries", "proof", "INTEGER DEFAULT 0")
        if "prune_due" not in _gcols:
            ctx.add_column(_con, "galleries", "prune_due", "TEXT")
        if "cw_fixed" not in _gcols:
            ctx.add_column(_con, "galleries", "cw_fixed", "TEXT")
            if "uploaded_at" in _gcols:
                _con.execute("UPDATE galleries SET cw_fixed='none' WHERE uploaded_at IS NOT NULL")
        gcols = [r[1] for r in _con.execute("PRAGMA table_info(galleries)")]
        for col, decl in (("upload_state", "TEXT DEFAULT ''"), ("uploaded_at", "TEXT"), ("removed_at", "TEXT"), ("upload_error", "TEXT DEFAULT ''")):
            if col not in gcols:
                ctx.add_column(_con, "galleries", col, decl)
        if stamp_hosts:
            try:
                fp_ = host_fingerprint()
            except Exception:
                fp_ = ""
            b_ = _con.execute("SELECT value FROM settings WHERE key='gallery_base_url'").fetchone()
            _con.execute("UPDATE galleries SET host_fp=?, link_base=? WHERE upload_state IN ('up', 'removed')",
                         (fp_ or None, (b_[0] if b_ and b_[0] else None)))
        else:
            try:
                from vault import web as _VW
                _hc = _VW.config()
                for _h in [_hc.get("gallery_host"), _hc.get("gallery_host_off")] + earlier_host_cfgs(_hc):
                    if _h and legacy_fingerprint_of(_h) != fingerprint_of(_h):
                        _con.execute("UPDATE galleries SET host_fp=? WHERE host_fp=?", (fingerprint_of(_h), legacy_fingerprint_of(_h)))
            except Exception:
                pass

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

        try:
            fp_ = host_fingerprint()
        except Exception:
            fp_ = ""
        for sid_, gid_, at_ in [] if not fp_ else _con.execute(
                """SELECT g.session_id, s.gallery_id, g.host_fp FROM galleries g JOIN sessions s ON s.id=g.session_id
                   WHERE g.upload_state='uploading'""").fetchall():
            if not at_:
                _con.execute("UPDATE galleries SET host_fp=? WHERE session_id=?", (fp_, sid_))
            elif at_ != fp_ and GID_OK.match(gid_ or ""):
                _con.execute("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
                             (gid_, fp_, date.today().isoformat()))
        _con.execute("""UPDATE galleries SET upload_state=CASE WHEN uploaded_at IS NOT NULL AND removed_at IS NULL THEN 'up' ELSE 'error' END,
                        upload_error='the upload stopped when the app restarted: press Upload again'
                        WHERE upload_state IN ('uploading', 'awaiting')""")
        _con.commit(); _con.close()

    def has_gallery_step(s):

        flow = ctx.C.WORKFLOW.get(s["type"], {})
        return any(st[0] == "auto" and st[1] == "gallery_built" for steps in flow.values() for st in steps) or link_delivery(s)

    def link_delivery(s):
        return ctx.C.delivery_method(s["type"], s["delivery_method"] if "delivery_method" in s.keys() else None) == "link"

    def wants_download(s):

        if not link_delivery(s):
            return False
        flow = ctx.C.WORKFLOW[s["type"]]
        picking = any(st[0] == "auto" and st[1] == "favorites" for steps in flow.values() for st in steps)
        return not picking or picks_in(s) or no_picks_chosen(s["id"])

    def picks_in(s):
        return bool(s["favorites"] or s["retouch_picks"] or s["veto"])

    def no_picks_chosen(sid):

        return bool(q("SELECT 1 FROM audit WHERE session_id=? AND action=? LIMIT 1", (sid, NO_PICKS_ACTION), one=True))

    def picking_type(s):
        flow = ctx.C.WORKFLOW[s["type"]]
        return any(st[0] == "auto" and st[1] == "favorites" for steps in flow.values() for st in steps)

    def wants_finals(s):

        return link_delivery(s) and picking_type(s)

    app.config["gallery_wants_finals"] = wants_finals

    def needs_finals(s):

        return wants_download(s) and picking_type(s) and picks_in(s)

    def build_from_finals(s, n_fin):

        return wants_download(s) and wants_finals(s) and (n_fin > 0 or needs_finals(s))

    def may_skip_picks(s):

        return link_delivery(s) and s["stage"] != "closed" and not picks_in(s) and not wants_download(s)

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

    def being_deleted(s):

        import jobs as _jobs
        if _jobs.running(f"session-delete:{s['id']}") or _jobs.running(f"client-delete:{s['household_id']}"):
            return True
        h_ = q("SELECT demo FROM households WHERE id=?", (s["household_id"],), one=True)
        return bool(h_ and h_["demo"] and _jobs.running("demo-delete"))

    def refuse_deleting(s, back):
        from flask import g as _g
        _g.refused = True
        JOBS[s["id"]] = dict(state="error", done=0, total=0,
                             error="This session is being deleted, so its gallery stays down.")
        return redirect(back)

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
        title = ((s["title"] if "title" in s.keys() else "") or "").strip()

        return dict(client=title or (h["name"] if h else ""), owns=owns,
                    what=label, date=when, logo=setting("logo_png"),
                    studio=setting("business_name") or setting("studio_name") or setting("photographer_name"),
                    photographer=setting("photographer_name"), email=setting("business_email"), phone=setting("business_phone"),
                    site=setting("business_website") if setting("business_website").startswith("https://") else "")

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

    def uploaded(sid, kind="export"):

        import photostore
        con = ctx.connect()
        try:
            rows = [r for r in photostore.files(con, sid, kind, done_only=True)
                    if Path(r["name"]).suffix.lower() in JPEG_EXT]
        finally:
            con.close()
        rows.sort(key=lambda r: natural(Path(r["name"])))
        return [(r["name"], (lambda r=r: io.BytesIO(photostore.plain_bytes(r)))) for r in rows]

    @app.post("/session/<int:sid>/gallery/build", endpoint="build_gallery")
    def build_gallery(sid):
        s = session(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        if being_deleted(s):
            return refuse_deleting(s, back)
        gid, g = ensure(s)
        if request.form.get("from") in ("uploads", "finals") or not FOLDERS:
            finals = uploaded(sid, "final")
            if needs_finals(s) and not finals and not request.form.get("proofs_ok"):

                JOBS[sid] = dict(state="error", done=0, total=0, error="Upload the retouched finals in Photos first.")
                return redirect(back)
            src = finals if build_from_finals(s, len(finals)) and finals else uploaded(sid)
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
            if JOBS.get(sid, {}).get("state") == "running" or sid in UPDATING:
                return redirect(back)
            JOBS[sid] = dict(state="running", done=0, total=0, error="", label="")

        em = q("SELECT value FROM settings WHERE key='business_email'", one=True)

        from vault import web as VW
        host_type = (VW.config().get("gallery_host") or {}).get("type", "")
        download = wants_download(s)
        picks_cfg = dict(retouch=ctx.C.RETOUCH_PICKS, market=0 if s["portfolio_no"] else ctx.C.MARKETING_PICKS,
                         email=(em["value"] if em else ""), ref=gid,
                         post=host_type in ("sftp", "local"))
        if download:
            picks_cfg = dict(post=picks_cfg["post"])
        off = q("SELECT value FROM settings WHERE key='credit_off'", one=True)
        credit = "" if off and off["value"] == "1" else ctx.C.CREDIT
        brand = gallery_brand(s)
        import settings_io
        look = settings_io.clean_look(*(setting_(k_) for k_ in ("gallery_font", "gallery_bg", "proof_mark",
                                                                 "proof_mark_text", "proof_mark_strength")))
        brand["theme"] = dict(font=look["gallery_font"], colors=settings_io.palette(look["gallery_bg"]))
        global OWN_FONT
        import base64 as _b64
        own_ = tuple(_b64.b64decode(v_) if v_ else None for v_ in (setting_("brand_font_400"), setting_("brand_font_300")))
        OWN_FONT = lambda: own_

        brand["logo"] = settings_io.gallery_logo(look["gallery_bg"], brand.get("logo") or "", setting_("logo_dark_png"))
        mark = None
        if look["proof_mark"] and not download:
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
                out, n, skipped = build(gid, src, key, progress, picks=picks_cfg, credit=credit, brand=brand, mark=mark,
                                        download=download)
                with app.app_context():
                    gone = not q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or (
                        q("SELECT 1 FROM sqlite_master WHERE name='retention'", one=True)
                        and q("SELECT 1 FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (sid,), one=True))
                    if gone:
                        shutil.rmtree(out, ignore_errors=True)
                        raise RuntimeError("the session's photos were deleted while the gallery was building")
                    x("UPDATE galleries SET built_at=?, n_files=?, out_dir=?, ekey=?, proof=? WHERE session_id=?",
                      (date.today().isoformat(), n, str(out) + "/", key, 1 if mark else 0, sid))
                    x("UPDATE sessions SET gallery_built=? WHERE id=?", (date.today().isoformat(), sid))
                    ctx.audit(("Download gallery" if download else "Gallery") + f" built: {n} photos encrypted" + (f"; skipped (not a JPEG this can read): {', '.join(skipped[:10])}" if skipped else ""),
                              sid=sid, who="Lumen Aureum")
                    ctx.advance(sid)
                    live = q("SELECT 1 FROM galleries WHERE session_id=? AND upload_state='up' AND removed_at IS NULL",
                             (sid,), one=True)
                if live and online_download(gid) == download:

                    try:
                        if not ctx.hooks.guard("gallery_upload", lambda: do_update(sid, gid), sid=sid, gallery_id=gid,
                                               n_files=n, update=True):
                            job.update(done=0, total=0, label="Updating the copy online")
                            do_update(sid, gid, job)
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
                release_memory()

        threading.Thread(target=run, name=f"gallery-{sid}", daemon=True).start()
        return redirect(back)

    app.config.setdefault("quiet_endpoints", set()).update({"picks_paste", "gallery_no_picks", "gallery_link_sent"})
    app.config.setdefault("ep_words", {}).update({
        "galleries_move_go": "Started putting galleries on the new gallery host",
        "galleries_clear_old": "Started taking the old copies down from the earlier gallery host",
        "galleries_forget_host": "Forgot an earlier gallery host, gone for good",
        "gallery_back_online": "Put the gallery back online"})

    @app.post("/session/<int:sid>/gallery/no-picks", endpoint="gallery_no_picks")
    def gallery_no_picks(sid):

        s = session(sid)
        if may_skip_picks(s):
            ctx.audit(NO_PICKS_ACTION, sid=sid)
        return redirect(url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery")
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
        u = UPDATING.get(sid)
        if u and j.get("state") != "running":
            j = dict(state="running", done=u["done"], total=u["total"], error="",
                     label=f"Updating the copy online: file {u['done']} of {u['total']}" if u["total"] else "Updating the copy online")
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

                return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Rebuild to preview</title>"
                        "<p style='font:16px system-ui;margin:2em'>This gallery was built by an older version. "
                        "Rebuild it to preview it here. (The copy on the web is not affected.)</p>"), 200
        r = send_from_directory(OUT_ROOT / gid, p)
        r.headers["Cache-Control"] = "no-store"
        r.headers["Referrer-Policy"] = "no-referrer"
        if p == "index.html":
            r.headers["Content-Security-Policy"] = ctx.csp(VIEWER_SCRIPT_HASHES)
        return r

    def do_upload(sid, gid, replace=False, held=False):

        with app.app_context():
            s_ = q("SELECT id, household_id FROM sessions WHERE id=?", (sid,), one=True)
            if not s_ or being_deleted(s_):
                return
            was = q("SELECT removed_at FROM galleries WHERE session_id=?", (sid,), one=True)
            back_up = bool(was and was["removed_at"])

        con_ = ctx.connect()
        try:
            n_ = con_.execute("""UPDATE galleries SET upload_state='uploading', upload_error='' WHERE session_id=?
                                 AND (NOT ? OR upload_state='awaiting' OR (? AND upload_state='up' AND removed_at IS NULL))""",
                              (sid, 1 if held else 0, 1 if replace else 0)).rowcount
            con_.commit()
        finally:
            con_.close()
        if n_ != 1:
            return
        try:
            fp_up = host_fingerprint()
        except Exception:
            fp_up = ""
        try:
            if replace:
                delete_gallery(gid)
                with app.app_context():
                    x("UPDATE galleries SET draft_n=NULL, draft_at=NULL, prune_due=NULL WHERE session_id=?", (sid,))
            with app.app_context():
                ext = (q("SELECT extension_used FROM sessions WHERE id=?", (sid,), one=True) or {"extension_used": 0})["extension_used"]
                days_ = gdays(sid)
            with app.app_context():
                link_base_now = base_url()
            until_ = once_ = None
            if back_up:
                with app.app_context():
                    rp = q("SELECT restore_days, restore_once FROM galleries WHERE session_id=?", (sid,), one=True)
                once_ = [n for n in ((rp["restore_once"] if rp else "") or "").split(",") if n]
                until_ = (date.today() + timedelta(days=(rp["restore_days"] if rp and rp["restore_days"] else days_ * (2 if ext else 1)))).isoformat()
            k = upload_gallery(gid, days_ * (2 if ext else 1),
                               (date.today() + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat(), until=until_, once=once_)
            if back_up and not is_download(gid):

                host_ = gallery_host()
                for name in ("picks.bin", "draft.bin"):
                    blob = kept_copy(gid, name)
                    if blob:
                        host_.put_stream(f"{gid}/{name}", io.BytesIO(blob), content_type="application/octet-stream")
            con_ = ctx.connect()
            try:
                n_ = con_.execute("""UPDATE galleries SET upload_state='up', uploaded_at=?, removed_at=NULL, dark_at=NULL, limits_contact=?,
                                     host_fp=?, link_base=?,
                                     moved_from=CASE WHEN host_fp IS NOT NULL AND host_fp != ? THEN host_fp ELSE moved_from END,
                                     restored_at=CASE WHEN ? THEN ? ELSE NULL END,
                                     restore_days=CASE WHEN ? THEN restore_days ELSE NULL END,
                                     restore_once=CASE WHEN ? THEN restore_once ELSE NULL END,
                                     cw_fixed=COALESCE(cw_fixed, CASE WHEN ekey != '' AND ekey != key THEN 'word' ELSE 'none' END)
                                     WHERE session_id=? AND upload_state='uploading'""", (date.today().isoformat(), contact_email(),
                                                                         host_fingerprint(), link_base_now, host_fingerprint(),
                                                                         1 if back_up else 0, date.today().isoformat(),
                                                                         1 if back_up else 0, 1 if back_up else 0, sid)).rowcount
                con_.commit()
            finally:
                con_.close()
            if n_ != 1:
                delete_gallery(gid)
                return
            mark_online(gid)
            with app.app_context():
                ctx.audit(("Download gallery" if is_download(gid) else "Gallery") + f" uploaded ({k} files)"
                          + (", in place of the one that was online" if replace else "")
                          + ((f", put back online for one download (until {ctx.nice(until_)} if it isn't used)" if once_ else
                              f", put back online until {ctx.nice(until_)}") if back_up else ""), sid=sid, who="Lumen Aureum")
        except Exception as e:
            app.logger.exception("gallery upload")

            con_ = ctx.connect()
            try:
                n_ = con_.execute("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=? AND upload_state='uploading'",
                                  (str(e)[:200], sid)).rowcount
                if n_ == 1 and fp_up:

                    at_ = con_.execute("SELECT host_fp FROM galleries WHERE session_id=?", (sid,)).fetchone()[0]
                    if not at_:
                        con_.execute("UPDATE galleries SET host_fp=? WHERE session_id=?", (fp_up, sid))
                    elif at_ != fp_up:
                        con_.execute("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
                                     (gid, fp_up, date.today().isoformat()))
                con_.commit()
            finally:
                con_.close()
            if n_ != 1:
                try:
                    remove_from(gid, gallery_host())
                except Exception:
                    app.logger.exception("gallery upload %s: taken down meanwhile, and the host failed", gid)
                    with app.app_context():
                        try:
                            add_leftover(gid, host_fingerprint())
                        except Exception:
                            app.logger.exception("gallery leftover %s", gid)

    def do_update(sid, gid, job=None):

        with LOCK:
            if sid in UPDATING:
                return
            UPDATING[sid] = dict(done=0, total=0)
        try:
            with app.app_context():
                x("UPDATE galleries SET upload_error='' WHERE session_id=?", (sid,))

            def progress(done, total):
                UPDATING[sid].update(done=done, total=total)
                if job is not None:
                    job.update(done=done, total=total, label=f"Updating the copy online: file {done} of {total}."
                                                             " The client's link keeps working meanwhile.")
            k = upload_gallery(gid, progress=progress)
            with app.app_context():
                still = q("SELECT 1 FROM galleries WHERE session_id=? AND upload_state='up' AND removed_at IS NULL", (sid,), one=True)
            if not still:
                delete_gallery(gid)
                return
            mark_online(gid)
            with app.app_context():
                once_ = once_rebuilt(sid, gid)
            if once_ is not None:

                host_ = gallery_host()
                with app.app_context():
                    window_changed(sid, strict=True)
                if host_.stat(f"{gid}/once.txt") is not None:
                    host_.delete(f"{gid}/once.txt")
            try:
                gone_, bad_ = prune_host(gid)
            except Exception:
                app.logger.exception("gallery prune %s", gid)
                gone_, bad_ = 0, None
            with app.app_context():
                x("UPDATE galleries SET prune_due=? WHERE session_id=?", (_now_iso(PRUNE_GRACE_MIN) if bad_ is None else None, sid))
                ctx.audit(f"Gallery updated on the web ({k} files; {gone_} earlier file{'' if gone_ == 1 else 's'} removed"
                          + (f"; {len(bad_)} could not be removed: {', '.join(bad_[:5])}" if bad_ else "")
                          + ("; the earlier files are removed at the next check" if bad_ is None else "") + ")",
                          sid=sid, who="Lumen Aureum")
        except Exception as e:
            app.logger.exception("gallery update")
            with app.app_context():
                x("UPDATE galleries SET upload_error=? WHERE session_id=?", (str(e)[:200], sid))
        finally:
            with LOCK:
                UPDATING.pop(sid, None)

    @app.post("/session/<int:sid>/gallery/upload", endpoint="upload_gallery_req")
    def upload_gallery_req(sid):
        s = session(sid)
        g = gal(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        if being_deleted(s):
            return refuse_deleting(s, back)
        if not g or not g["built_at"] or g["upload_state"] in ("awaiting", "uploading"):
            return redirect(back)
        replace = False
        if g["upload_state"] == "up" and not g["removed_at"]:
            gid = s["gallery_id"]
            if JOBS.get(sid, {}).get("state") == "running" or sid in UPDATING:
                from flask import g as _g
                _g.refused = True
                return redirect(back)
            if gid and not online_is_current(gid) and online_download(gid) != is_download(gid):
                replace = True
        if g["upload_state"] == "up" and not g["removed_at"] and not replace:
            gid = s["gallery_id"]
            if gid and not online_is_current(gid):
                try:
                    if not ctx.hooks.guard("gallery_upload", lambda: do_update(sid, gid), sid=sid, gallery_id=gid,
                                           n_files=g["n_files"], update=True):
                        threading.Thread(target=do_update, args=(sid, gid), name=f"gal-up-{sid}", daemon=True).start()
                except Exception as e:
                    x("UPDATE galleries SET upload_error=? WHERE session_id=?", (f"held upload failed: {e}"[:200], sid))
            return redirect(back)
        owed = 0 if proof_built(sid) else ctx.release_blocked(sid)
        if owed and not request.form.get("early"):
            if replace:
                x("UPDATE galleries SET upload_error=? WHERE session_id=?",
                  (f"balance {ctx.money(owed)} due - the download gallery is released on payment", sid))
            else:
                x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?",
                  (f"balance {ctx.money(owed)} due - the gallery is released on payment", sid))
            return redirect(back)
        gid = s["gallery_id"]
        word = (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or ""
        if codeword_key(g["key"], gid, word) != (g["ekey"] or g["key"]):
            x("UPDATE galleries SET upload_state=?, upload_error=? WHERE session_id=?",
              ("up" if replace else "error", "the codeword changed after the gallery was built: press Rebuild, then Upload", sid))
            return redirect(back)
        try:

            if ctx.hooks.guard("gallery_upload", lambda: do_upload(sid, gid, replace, held=True), sid=sid, gallery_id=gid,
                               n_files=g["n_files"]):
                if not replace:
                    x("UPDATE galleries SET upload_state='awaiting', upload_error='' WHERE session_id=?", (sid,))
                return redirect(back)
        except Exception as e:
            x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?", (f"held upload failed: {e}"[:200], sid))
            return redirect(back)
        threading.Thread(target=do_upload, args=(sid, gid, replace), name=f"gal-up-{sid}", daemon=True).start()
        return redirect(back)

    def restored_until(r, days):

        if not r["restored_at"]:
            return None
        n = r["restore_days"] if "restore_days" in r.keys() and r["restore_days"] else days
        return (date.fromisoformat(r["restored_at"][:10]) + timedelta(days=n)).isoformat()

    def restored_once(r):
        return [n for n in ((r["restore_once"] if "restore_once" in r.keys() else "") or "").split(",") if n] if r["restored_at"] else []

    @app.post("/session/<int:sid>/gallery/back-online", endpoint="gallery_back_online")
    def gallery_back_online(sid):

        s = session(sid)
        g = gal(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        if being_deleted(s):
            return refuse_deleting(s, back)
        gid = s["gallery_id"] or ""
        if not (g and g["upload_state"] == "removed" and g["removed_at"] and GID_OK.match(gid)
                and (OUT_ROOT / gid / "manifest.bin").is_file()):
            return redirect(back)
        if request.form.get("mode") == "once":
            pieces = zip_pieces(gid, g["ekey"] or g["key"]) if gate_built(gid) else []
            if not pieces:
                return redirect(back)
            x("UPDATE galleries SET restore_days=?, restore_once=? WHERE session_id=?", (ONCE_DAYS, ",".join(pieces), sid))
        else:
            try:
                n = int(request.form.get("days") or 0)
            except ValueError:
                n = 0
            if not 1 <= n <= 365:
                return redirect(back)
            x("UPDATE galleries SET restore_days=?, restore_once=NULL WHERE session_id=?", (n, sid))
        return upload_gallery_req(sid)

    def move_rows():

        fp = host_fingerprint() if has_host() else ""
        if not fp:
            return []
        return q("""SELECT g.*, s.gallery_id, s.first_visit, s.sd_shipped, s.extension_used, s.id AS sid, s.household_id,
                           h.name AS client FROM galleries g JOIN sessions s ON s.id=g.session_id
                    JOIN households h ON h.id=s.household_id
                    WHERE g.host_fp IS NOT NULL AND g.host_fp != ? AND
                          (g.upload_state='up' OR (g.upload_state IN ('removed', 'error') AND g.dark_at IS NOT NULL)
                           OR (g.upload_state='error' AND g.removed_at IS NULL))
                    ORDER BY h.name""", (fp,))

    def new_link_rows():

        if not has_host():
            return []
        return [r for r in q("""SELECT g.*, s.gallery_id, s.id AS sid, s.household_id, h.name AS client
                                FROM galleries g JOIN sessions s ON s.id=g.session_id JOIN households h ON h.id=s.household_id
                                WHERE g.upload_state='up' AND g.removed_at IS NULL AND g.link_base IS NOT NULL
                                ORDER BY h.name""") if r["link_base"] != base_url()]

    def leftover_rows(fp=None):
        return q("SELECT gallery_id, host_fp, added FROM gallery_leftovers" + (" WHERE host_fp=?" if fp else "")
                 + " ORDER BY added, gallery_id", (fp,) if fp else ())

    def add_leftover(gid, fp):
        x("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
          (gid, fp, date.today().isoformat()))

    def drop_leftover(gid, fp):
        x("DELETE FROM gallery_leftovers WHERE gallery_id=? AND host_fp=?", (gid, fp))

    def move_today():
        out = []
        n = len(move_rows())
        if n:
            out.append(dict(text=f"{n} galler{'y is' if n == 1 else 'ies are'} still on your earlier gallery host. "
                                 f"Put {'it' if n == 1 else 'them'} on the new one", link=url_for("galleries_move")))
        k = len(new_link_rows())
        if k:
            out.append(dict(text=f"The gallery address changed: {k} client{' needs' if k == 1 else 's need'} the new link",
                            link=url_for("galleries_move") + "#links"))
        gone = q("""SELECT COUNT(*) AS n FROM gallery_leftovers l WHERE NOT EXISTS
                    (SELECT 1 FROM sessions s WHERE s.gallery_id=l.gallery_id)""", one=True)["n"]
        m = len(leftover_rows()) - gone
        if gone:
            out.append(dict(text=f"{gone} galler{'y of a deleted session still has' if gone == 1 else 'ies of deleted sessions still have'}"
                                 " to come down from a gallery host", link=url_for("galleries_move") + "#earlier"))
        if m:
            out.append(dict(text=f"{m} old cop{'y of a gallery still has' if m == 1 else 'ies of galleries still have'}"
                                 " to come down from a gallery host", link=url_for("galleries_move") + "#earlier"))
        return out

    ctx.hooks.today.append(move_today)

    def possibly_online(fp):

        rows = q("""SELECT s.id AS sid, s.gallery_id, h.name AS client, g.host_fp, g.moved_from, g.upload_state, g.dark_at,
                           g.removed_at, g.uploaded_at
                    FROM galleries g JOIN sessions s ON s.id=g.session_id JOIN households h ON h.id=s.household_id
                    WHERE (g.host_fp=? AND (g.upload_state IN ('up', 'removed', 'error'))) OR g.moved_from=? ORDER BY h.name""", (fp, fp))
        out, named = [], set()
        for r in rows:
            here, err = r["host_fp"] == fp, r["upload_state"] == "error"
            what = ("not moved yet" if here and r["upload_state"] == "up" else
                    "its dark page" if here and r["dark_at"] else
                    "not moved yet; its last upload failed" if here and err and not r["removed_at"] and r["uploaded_at"] else
                    "its upload failed partway; parts of it may be there" if here and err and not r["removed_at"] else
                    "putting it back online failed partway; parts of it may be there" if here and err else
                    "its copy there could not be removed" if here else "the old copy")
            out.append(dict(sid=r["sid"], text=f"{r['client']} ({what}, folder {r['gallery_id']})"))
            named.add(r["gallery_id"])
        for r in leftover_rows(fp):
            if r["gallery_id"] in named:
                continue
            o = q("""SELECT s.id AS sid, h.name AS client FROM sessions s JOIN households h ON h.id=s.household_id
                     WHERE s.gallery_id=?""", (r["gallery_id"],), one=True)
            out.append(dict(sid=o["sid"], text=f"{o['client']} (an old copy, folder {r['gallery_id']})") if o else
                       dict(sid=None, text=f"a deleted session's gallery (folder {r['gallery_id']})"))
        return out

    def earlier_view():

        from vault import web as VW
        cfg = VW.config()
        waiting = move_rows()
        out = []
        for h in earlier_host_cfgs(cfg):
            fp = fingerprint_of(h)
            n_wait = sum(1 for r in waiting if r["host_fp"] == fp)
            out.append(dict(fp=fp, label=host_label(h), waiting=n_wait, things=possibly_online(fp),
                            can_clear=bool(cfg.get("gallery_host")) and not n_wait, unreachable=not may_try(fp)))
        return out

    MOVE_PAGE = """{% extends "base.html" %}{% block title %}Move galleries - Lumen Aureum{% endblock %}{% block body %}
<h1>Move galleries</h1>
<p class="mute" style="max-width:720px">Every gallery is built on this computer and its key is in your data, so the
galleries can go up on another host with the same links. If the gallery address stays the same (your own domain, now
pointing at the new host), every link and every QR letter keeps working. A new address means new links.</p>
{% if not on %}<p class="notice" style="max-width:820px">Galleries are turned off in Setup, so nothing is moved or taken
down now. Turn them on again (Setup, Galleries) to carry on: with the same host, everything is where it was.</p>{% endif %}
<section class="card" style="max-width:820px;margin-top:14px">
<h3>On the earlier host</h3>
{% if rows %}<ul>{% for r in rows %}<li>{{ r.client }}: {{ 'online' if r.upload_state == 'up' else 'gone dark' if r.removed_at else 'online, but its last upload failed: press Upload on its session' if r.uploaded_at else 'its upload failed partway: press Upload on its session' }}{% if r.upload_state == 'up' and not built(r.gallery_id) %} <span style="color:var(--red)">(no build here: press Rebuild on the session first)</span>{% endif %}</li>{% endfor %}</ul>
<form method="post" action="{{ url_for('galleries_move_go') }}" data-confirm="Put {{ rows|length }} galler{{ 'y' if rows|length == 1 else 'ies' }} on the new gallery host, with their days, picks and links as they are?"><button class="primary">Put {{ rows|length }} galler{{ 'y' if rows|length == 1 else 'ies' }} on the new host</button></form>
{% elif on %}<p class="mute">Nothing: every gallery is on the gallery host set up now.</p>{% else %}<p class="mute">Nothing moves while galleries are off.</p>{% endif %}
</section>
{% if on %}<section class="card" id="links" style="max-width:820px;margin-top:14px">
<h3>Clients who need the new link</h3>
{% if links %}<p class="mute">The gallery address is now {{ base }}. These clients have a link at the earlier address.</p>
{% for r in links %}<div class="row" style="gap:8px;flex-wrap:wrap;margin:6px 0"><span class="grow">{{ r.client }}</span>
  <span hidden id="nl-{{ r.sid }}">{{ link(r.sid) }}</span><button type="button" class="quiet" data-copy="#nl-{{ r.sid }}">Copy link</button>
  <a class="go" href="{{ url_for('client_stage', hid=r.household_id) }}?s={{ r.sid }}#gallery">Open</a>
  <form method="post" action="{{ url_for('gallery_link_sent', sid=r.sid) }}" class="inline"><button class="quiet">Sent</button></form></div>{% endfor %}
{% else %}<p class="mute">Nobody: every client's link has today's address.</p>{% endif %}
</section>{% endif %}
{% if earlier %}<section class="card" id="earlier" style="max-width:820px;margin-top:14px"><h3>Earlier gallery hosts</h3>
{% for h in earlier %}<div style="margin:10px 0 16px"><h4 style="margin:0 0 4px">{{ h.label }}</h4>
{% if h.things %}<p class="mute" style="margin:0 0 4px">May still be online there:</p><ul style="margin-top:0">{% for i in h.things %}<li>{{ i.text }}</li>{% endfor %}</ul>
{% else %}<p class="mute" style="margin:0 0 4px">Nothing is listed as still on it; its folders for your galleries come down with the old copies.</p>{% endif %}
{% if h.unreachable %}<p class="mute" style="margin:0 0 4px">It couldn't be reached at the last try; the regular check tries it again once a day.</p>{% endif %}
<div class="row" style="gap:8px;flex-wrap:wrap">
{% if h.can_clear %}<form method="post" action="{{ url_for('galleries_clear_old') }}" class="inline" data-confirm="Remove every gallery from {{ h.label }}? The host in use keeps its copies."><input type="hidden" name="host" value="{{ h.fp }}"><button class="quiet">Take the old copies down</button></form>
{% elif on and h.waiting %}<span class="mute">Move its galleries first; then its old copies can come down.</span>{% endif %}
<form method="post" action="{{ url_for('galleries_forget_host') }}" class="inline" data-confirm="Forget {{ h.label }}? Do this only if that host is gone for good: nothing more is taken down from it or read from it, and{% if h.things %} {{ h.things|length }} galler{{ 'y' if h.things|length == 1 else 'ies' }} listed here may stay online there until the host itself is gone. History names them{% else %} nothing is listed as still on it{% endif %}."><input type="hidden" name="host" value="{{ h.fp }}"><button class="quiet">This host is gone for good: forget it</button></form>
</div></div>{% endfor %}</section>{% endif %}
{% endblock %}"""

    @app.get("/galleries/move", endpoint="galleries_move")
    def galleries_move():
        on = has_host()
        return render_template_string(MOVE_PAGE, rows=move_rows(), links=new_link_rows(), base=base_url() if on else "",
                                      link=gallery_link, earlier=earlier_view(), on=on,
                                      built=lambda gid: bool(GID_OK.match(gid or "") and (OUT_ROOT / gid / "manifest.bin").is_file()))

    def earlier_backends():

        cache = {}
        fp_now = host_fingerprint()

        def get(fp):
            if fp not in cache:
                cfg_ = host_cfg_for(fp) if fp and fp != fp_now else None
                try:
                    cache[fp] = open_host(cfg_) if cfg_ else None
                except Exception:
                    cache[fp] = False
            return cache[fp]
        return get, cache

    def move_work(job):

        import io as _io
        if not has_host():
            return "Galleries are turned off in Setup, so nothing was moved."
        fp, host = host_fingerprint(), gallery_host()
        rows = move_rows()
        old_for, olds = earlier_backends()
        job["total"] = len(rows)
        moved, dark, missing, failed = 0, 0, [], []
        for i, r in enumerate(rows):
            gid = r["gallery_id"]
            try:
                if r["upload_state"] == "error" and not r["removed_at"]:
                    failed.append(r["client"])
                elif r["upload_state"] == "removed" or r["removed_at"]:
                    b_ = gallery_brand(session(r["sid"]))

                    remove_from(gid, host)
                    drop_leftover(gid, fp)
                    put_dark(gid, b_["studio"], contact_email(), host=host)
                    x("UPDATE galleries SET host_fp=?, moved_from=? WHERE session_id=?", (fp, r["host_fp"], r["sid"]))
                    dark += 1
                elif not (GID_OK.match(gid or "") and (OUT_ROOT / gid / "manifest.bin").is_file()):
                    missing.append(r["client"])
                else:
                    old = old_for(r["host_fp"])
                    days_ = gdays(r["sid"]) * (2 if r["extension_used"] else 1)
                    first = r["sd_shipped"] if online_download(gid) else r["first_visit"]
                    up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
                    until = restored_until(r, days_) or (
                        (date.fromisoformat(first[:10]) + timedelta(days=days_)).isoformat() if first else None)

                    left_here = b"moved=" in (_read(host, f"{gid}/limits.txt") or b"")
                    if left_here:
                        for name in ("once.txt", "done.txt"):
                            host.delete(f"{gid}/{name}")
                    upload_gallery(gid, days_, (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat(),
                                   until=until, once=restored_once(r))
                    if left_here:
                        try:
                            prune_host(gid)
                        except Exception:
                            app.logger.exception("gallery move: the copy left here %s", gid)
                    key_ = r["ekey"] or r["key"]
                    for name in ("picks.bin", "draft.bin"):

                        here = _read(host, f"{gid}/{name}")
                        best = newest(key_, name, here, earlier_copy(old, gid, name), kept_copy(gid, name))
                        if best and best != here:
                            host.put_stream(f"{gid}/{name}", _io.BytesIO(best), content_type="application/octet-stream")
                        if best:
                            keep_host_copy(gid, name, best, key_)

                    got = []
                    if old:
                        try:
                            if restored_once(r):
                                for name in ("once.txt", "done.txt"):
                                    blob = _read(old, f"{gid}/{name}")
                                    if blob and len(blob) <= 65536 and host.stat(f"{gid}/{name}") is None:
                                        host.put_stream(f"{gid}/{name}", _io.BytesIO(blob), content_type="text/plain")
                            got = hand_over(old, host, gid, r["ekey"] or r["key"])
                            _host_reached(r["host_fp"])
                        except Exception:
                            app.logger.exception("gallery move: the earlier host %s", gid)
                            olds[r["host_fp"]] = False
                    x("UPDATE galleries SET host_fp=?, limits_days=?, limits_contact=?, moved_from=? WHERE session_id=?",
                      (fp, days_, contact_email(), r["host_fp"], r["sid"]))
                    ctx.audit("Gallery moved to the new gallery host"
                              + ("; the client's latest picks came over from the earlier host" if got else ""),
                              sid=r["sid"], who="Lumen Aureum")
                    moved += 1
            except Exception as e:
                app.logger.exception("gallery move %s", gid)
                missing.append(f"{r['client']} ({str(e)[:80]})")
            job["done"] = i + 1
        msg = f"Moved {moved} galler{'y' if moved == 1 else 'ies'}" + (f" and {dark} dark page{'' if dark == 1 else 's'}" if dark else "") + "."
        if missing:
            msg += " Not moved: " + ", ".join(missing[:8]) + (" and more" if len(missing) > 8 else "") + \
                   ". Rebuild those on their sessions, then move again."
        if failed:
            msg += " Still on the earlier host after a failed upload: " + ", ".join(failed[:8]) + \
                   (" and more" if len(failed) > 8 else "") + ". Press Upload on those sessions."
        ctx.audit(msg, who="Lumen Aureum")
        return msg

    def start_move():
        import jobs as _jobs
        _jobs.start(app, "gallery-move", "Putting galleries on the new gallery host", move_work, link=url_for("galleries_move"))

    @app.post("/galleries/move", endpoint="galleries_move_go")
    def galleries_move_go():
        rows = move_rows()
        if rows:

            if not ctx.hooks.guard("gallery_upload", lambda: start_move(), sid=None, gallery_id=None, move=True,
                                   n_galleries=len(rows), n_files=sum(int(r["n_files"] or 0) for r in rows)):
                start_move()
        return redirect(url_for("galleries_move"))

    def earlier_copy(old, gid, name):

        if not old:
            return None
        try:
            if old.stat(f"{gid}/{name}") is None:
                return None
            buf = io.BytesIO()
            old.get_stream(f"{gid}/{name}", buf)
            blob = buf.getvalue()
            if not blob or len(blob) > PICKS_MAX:
                return None
            return blob
        except Exception:
            return None

    def take_down_earlier(gid, fp):

        prev = host_cfg_for(fp)
        if not prev or fp == host_fingerprint():
            return 0, "that host is no longer set up here"
        if not may_try(fp):
            return 0, f"{host_label(prev)} couldn't be reached at an earlier try"
        try:
            n = remove_from(gid, open_host(prev))
            _host_reached(fp)
            return n, ""
        except Exception:
            app.logger.exception("gallery %s on the earlier host", gid)
            _host_failed(fp)
            return 0, f"{host_label(prev)} can't be reached"

    def tidy_earlier():

        from vault import web as VW
        cfg = VW.config()
        before = (cfg.get("gallery_hosts_prev"), cfg.get("gallery_host_prev"))
        _put_earlier(cfg, earlier_host_cfgs(cfg))
        if (cfg.get("gallery_hosts_prev"), cfg.get("gallery_host_prev")) != before:
            VW.write_config(cfg)

    def forget_host(fp):

        from vault import web as VW
        cfg = VW.config()
        _put_earlier(cfg, [h for h in earlier_host_cfgs(cfg) if fingerprint_of(h) != fp])
        VW.write_config(cfg)
        x("DELETE FROM gallery_leftovers WHERE host_fp=?", (fp,))
        x("UPDATE galleries SET moved_from=NULL WHERE moved_from=?", (fp,))
        _host_reached(fp)

    def clearable(fp=None):

        if not has_host():
            return []
        waiting = {r["host_fp"] for r in move_rows()}
        return [fingerprint_of(h) for h in earlier_host_cfgs()
                if fingerprint_of(h) not in waiting and (fp is None or fingerprint_of(h) == fp)]

    def clear_old_work(job, fps):
        from vault import web as VW
        msgs = []
        for fp_ in fps:
            cfg = VW.config()
            cur = cfg.get("gallery_host")
            prev = next((h for h in earlier_host_cfgs(cfg) if fingerprint_of(h) == fp_), None)
            if prev is None:
                continue
            if not cur:
                msgs.append("Galleries are turned off in Setup, so nothing was taken down.")
                break
            if fp_ in {r["host_fp"] for r in move_rows()}:
                msgs.append(f"{host_label(prev)} still has galleries to move, so nothing was taken down from it.")
                continue
            label = host_label(prev)
            try:
                same = same_place(cur, prev)
            except Exception:
                app.logger.exception("gallery: the earlier host %s", label)
                _host_failed(fp_)
                msgs.append(f"Couldn't check that {label} is a different place from the host in use, so nothing was "
                            "removed from it. If that host is gone for good, forget it.")
                continue
            if same:
                forget_host(fp_)
                ctx.audit(f"The earlier gallery host {label} is the one in use now; nothing was taken down from it",
                          who="Lumen Aureum")
                msgs.append(f"{label} and the host in use are the same place, so nothing was removed.")
                continue
            try:
                old = open_host(prev)
                gids = [r["gallery_id"] for r in q("SELECT gallery_id FROM sessions WHERE gallery_id IS NOT NULL AND gallery_id != ''")]
                gids = list(dict.fromkeys(g_ for g_ in gids + [r["gallery_id"] for r in leftover_rows(fp_)] if GID_OK.match(g_ or "")))
                job["total"] = job.get("total", 0) + len(gids)
                n = 0
                with old.session():
                    for gid in gids:
                        names = old.list(f"{gid}/")
                        for name in names + [f"{gid}/.htaccess"]:
                            try:
                                old.delete(name)
                            except Exception:
                                pass
                        if names:
                            try:
                                old.remove_dir(f"{gid}/")
                            except Exception:
                                pass
                            n += 1
                        job["done"] = job.get("done", 0) + 1
            except Exception:
                app.logger.exception("gallery: the earlier host %s", label)
                _host_failed(fp_)
                msgs.append(f"{label} can't be reached. Nothing was removed from it.")
                continue
            forget_host(fp_)
            ctx.audit(f"Removed {n} galler{'y' if n == 1 else 'ies'} from the earlier gallery host {label}", who="Lumen Aureum")
            msgs.append(f"Removed {n} galler{'y' if n == 1 else 'ies'} from {label}.")
        return " ".join(msgs) or "Nothing to take down."

    def start_clear_old(fps):
        import jobs as _jobs
        _jobs.start(app, "gallery-clear-old", "Removing galleries from the earlier host", lambda job: clear_old_work(job, fps),
                    link=url_for("galleries_move"))

    @app.post("/galleries/clear-old", endpoint="galleries_clear_old")
    def galleries_clear_old():
        tidy_earlier()
        fps = clearable(request.form.get("host") or None)
        if not fps:
            from flask import g as _g
            _g.refused = True
            return redirect(url_for("galleries_move"))

        if not ctx.hooks.guard("gallery_upload", lambda: start_clear_old(fps), sid=None, gallery_id=None, remove=True,
                               earlier_host=True):
            start_clear_old(fps)
        return redirect(url_for("galleries_move"))

    @app.post("/galleries/forget-host", endpoint="galleries_forget_host")
    def galleries_forget_host():

        fp = request.form.get("host", "")
        prev = next((h for h in earlier_host_cfgs() if fingerprint_of(h) == fp), None)
        if prev is None:
            from flask import g as _g
            _g.refused = True
            return redirect(url_for("galleries_move"))
        from flask import g as _g
        label, items = host_label(prev), possibly_online(fp)
        for i in items:
            if i["sid"]:
                ctx.audit(f"The earlier gallery host {label} was forgotten as gone for good; this gallery may still be "
                          f"online there: {i['text']}", sid=i["sid"], who="Lumen Aureum")
        gone = [i["text"] for i in items if not i["sid"]]
        forget_host(fp)
        _g.audit_note = (f"{label}; " + (f"{len(items)} galler{'y' if len(items) == 1 else 'ies'} may still be online there"
                                        + (": " + ", ".join(gone) if gone else "") if items else "nothing was listed as still on it"))
        return redirect(url_for("galleries_move"))

    @app.post("/session/<int:sid>/gallery/link-sent", endpoint="gallery_link_sent")
    def gallery_link_sent(sid):
        x("UPDATE galleries SET link_base=? WHERE session_id=?", (base_url(), sid))
        ctx.audit("The new gallery link was sent", sid=sid)
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("galleries_move") + "#links")

    PICKS_ON = lambda r: True

    def check_moved():

        rows = q("""SELECT g.session_id AS sid, g.key, g.ekey, g.moved_from, s.gallery_id FROM galleries g
                    JOIN sessions s ON s.id=g.session_id
                    WHERE g.moved_from IS NOT NULL AND g.upload_state='up' AND g.removed_at IS NULL""")
        if not rows or not has_host():
            return
        fp_now = host_fingerprint()
        by = {}
        for r in rows:
            by.setdefault(r["moved_from"], []).append(r)
        new = gallery_host()
        for fp, rs in by.items():
            if fp == fp_now:
                continue
            prev = next((h for h in earlier_host_cfgs() if fingerprint_of(h) == fp), None)
            if prev is None:
                x("UPDATE galleries SET moved_from=NULL WHERE moved_from=?", (fp,))
                continue
            if not may_try(fp):
                continue
            try:
                old = open_host(prev)
                with old.session(), new.session():
                    for r in rs:
                        if not GID_OK.match(r["gallery_id"] or ""):
                            continue
                        if hand_over(old, new, r["gallery_id"], r["ekey"] or r["key"]):
                            ctx.audit("Picks the client sent to the gallery's earlier host came over", sid=r["sid"], who="Lumen Aureum")
                _host_reached(fp)
            except Exception:
                _host_failed(fp)
                app.logger.warning("gallery: the earlier host can't be reached now; tried again in a day")

    def clear_leftovers():

        rows = leftover_rows()
        if not rows:
            return
        fp_now = host_fingerprint()
        by = {}
        for r in rows:
            by.setdefault(r["host_fp"], []).append(r["gallery_id"])
        for fp, gids in by.items():
            cfg_ = host_cfg_for(fp)
            if cfg_ is None:
                x("DELETE FROM gallery_leftovers WHERE host_fp=?", (fp,))
                continue
            if fp != fp_now and not may_try(fp):
                continue
            n = 0
            try:
                h_ = open_host(cfg_)
                with h_.session():
                    for gid in gids:
                        live = q("""SELECT 1 FROM galleries g JOIN sessions s ON s.id=g.session_id
                                    WHERE s.gallery_id=? AND (g.upload_state IN ('uploading', 'awaiting')
                                    OR (g.upload_state='up' AND g.removed_at IS NULL)) AND (? OR g.host_fp=?)""",
                                 (gid, 1 if fp == fp_now else 0, fp), one=True)
                        if not live and GID_OK.match(gid or ""):
                            delete_gallery(gid, host=h_)
                            own = q("""SELECT s.id AS sid, g.dark_at, g.host_fp FROM galleries g
                                       JOIN sessions s ON s.id=g.session_id WHERE s.gallery_id=?""", (gid,), one=True)
                            if own and own["dark_at"] and own["host_fp"] == fp:
                                b_ = gallery_brand(session(own["sid"]))
                                put_dark(gid, b_["studio"], contact_email(), host=h_)
                            if own:
                                ctx.audit(f"An old copy of the gallery came down from the gallery host {host_label(cfg_)}",
                                          sid=own["sid"], who="Lumen Aureum")
                            else:
                                n += 1
                        drop_leftover(gid, fp)
                _host_reached(fp)
            except Exception:
                app.logger.warning("gallery: a deleted session's gallery is still on %s; tried again later", host_label(cfg_))
                if fp != fp_now:
                    _host_failed(fp)
            if n:
                ctx.audit(f"Removed {n} galler{'y' if n == 1 else 'ies'} of deleted sessions from the gallery host "
                          f"{host_label(cfg_)}", who="Lumen Aureum")

    def sync_once():

        from datetime import datetime, timezone
        check_moved()
        try:
            clear_leftovers()
        except Exception:
            app.logger.exception("gallery: galleries of deleted sessions")
        retime_all()
        rows = q("""SELECT g.*, s.gallery_id, s.first_visit, s.sd_shipped, s.extension_used, s.id AS sid FROM galleries g
                    JOIN sessions s ON s.id=g.session_id WHERE g.upload_state='up' AND g.removed_at IS NULL""")
        try:
            fp_now = host_fingerprint()
        except Exception:
            fp_now = ""
        for r in rows:
            gid = r["gallery_id"]
            dl = online_download(gid)
            first = r["sd_shipped"] if dl else r["first_visit"]
            stranded = bool(r["host_fp"]) and r["host_fp"] != fp_now
            try:
                if stranded:
                    raise _Stranded
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

                if (r["limits_days"] or 0) != want_days or (r["limits_contact"] or "") != contact_email():
                    up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
                    put_limits(gid, want_days, (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat(),
                               until=restored_until(r, want_days), once=restored_once(r))
                    x("UPDATE galleries SET limits_days=?, limits_contact=? WHERE session_id=?",
                      (want_days, contact_email(), r["sid"]))
                if not first:
                    st = seen_at(gid)
                    if st:
                        local = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone().date().isoformat()
                        x("UPDATE sessions SET first_visit=? WHERE id=? AND first_visit IS NULL", (local, r["sid"]))
                        if dl:
                            x("UPDATE sessions SET sd_shipped=? WHERE id=? AND sd_shipped IS NULL", (local, r["sid"]))
                            x("""UPDATE tasks SET done=? WHERE session_id=? AND title='Send retouched photos' AND done IS NULL""",
                              (local, r["sid"]))
                        ctx.audit("Client opened the download gallery: files delivered" if dl else "Client opened the gallery",
                                  sid=r["sid"], who="Lumen Aureum")
                        ctx.advance(r["sid"])
                        first = local
                if dl:
                    if r["draft_n"] is not None:
                        x("UPDATE galleries SET draft_n=NULL, draft_at=NULL WHERE session_id=?", (r["sid"],))
                elif PICKS_ON(r):
                    key_ = r["ekey"] or r["key"]
                    blob = picks_blob(gid, key=key_)
                    kept_ = kept_copy(gid, "picks.bin") if blob else None
                    if kept_ and kept_ != blob and newest(key_, "picks.bin", blob, kept_) is kept_:

                        try:
                            gallery_host().put_stream(f"{gid}/picks.bin", io.BytesIO(kept_), content_type="application/octet-stream")
                            ctx.audit("The gallery host had older picks than the client's latest; their latest went back up",
                                      sid=r["sid"], who="Lumen Aureum")
                        except Exception:
                            app.logger.exception("gallery %s: the client's latest picks back up", gid)
                        blob = kept_
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
            except _Stranded:
                pass
            except Exception:
                app.logger.exception("gallery sync %s", gid)
            try:
                once_done = None
                if restored_once(r):
                    t_ = done_at(gid)
                    if t_ and t_ <= time.time():
                        once_done = True
                if r["restored_at"]:
                    gone = date.fromisoformat(restored_until(r, gdays(r["sid"]) * (2 if r["extension_used"] else 1)))
                elif first:
                    gone = date.fromisoformat(first[:10]) + timedelta(days=gdays(r["sid"]) * (2 if r["extension_used"] else 1))
                else:
                    gone = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10]) + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)

                if (once_done or time.time() > day_close(gone)) and app.config["clock_ok"]():
                    b_ = gallery_brand(session(r["sid"]))
                    fp_after, left_behind = fp_now, ""
                    if stranded:

                        dark = False
                        try:
                            remove_from(gid, gallery_host())
                            dark = put_dark(gid, b_["studio"], contact_email())
                        except Exception:
                            app.logger.exception("gallery expiry %s: the copy on the host in use", gid)
                            add_leftover(gid, fp_now)
                        n, left_behind = take_down_earlier(gid, r["host_fp"])
                        if left_behind:
                            fp_after = r["host_fp"]
                    else:
                        n, dark = go_dark(gid, b_["studio"], contact_email())
                    old_ = r["moved_from"]
                    if old_ and old_ not in (fp_now, r["host_fp"]) and host_cfg_for(old_):
                        add_leftover(gid, old_)
                        if not take_down_earlier(gid, old_)[1]:
                            drop_leftover(gid, old_)
                    x("UPDATE galleries SET removed_at=?, upload_state='removed', dark_at=?, host_fp=?, moved_from=NULL WHERE session_id=?",
                      (date.today().isoformat(), date.today().isoformat() if dark else None, fp_after or None, r["sid"]))
                    if left_behind:
                        ctx.audit("Its days are over, but its copy on the earlier gallery host could not be removed ("
                                  + left_behind + "). Move galleries lists it; Take the old copies down removes it.",
                                  sid=r["sid"], who="Lumen Aureum")
                    ctx.audit(f"Gallery removed from the web ({n} files), "
                              + ("the one-time download was made" if once_done else
                                 "its days back online are over" if r["restored_at"] else
                                 f"{gdays(r['sid'])} day window over" if first else "never opened in a year")
                              + ("; its link now says the gallery has gone dark" if dark else ""),
                              sid=r["sid"], who="Lumen Aureum")
                    continue
            except Exception:
                app.logger.exception("gallery expiry %s", gid)
            try:
                due_ = r["prune_due"] if "prune_due" in r.keys() and not stranded else None
                if due_ and due_ <= _now_iso():
                    if GID_OK.match(gid or "") and (OUT_ROOT / gid).is_dir() and online_is_current(gid):
                        gone_, bad_ = prune_host(gid)
                        if gone_ or bad_:
                            ctx.audit(f"Removed {gone_} old file{'' if gone_ == 1 else 's'} from the copy online"
                                      + (f"; {len(bad_)} could not be removed: {', '.join(bad_[:5])}" if bad_ else ""),
                                      sid=r["sid"], who="Lumen Aureum")
                    x("UPDATE galleries SET prune_due=NULL WHERE session_id=?", (r["sid"],))
            except Exception:
                app.logger.exception("gallery prune %s", gid)
        if app.config["clock_ok"]():
            clear_old_dark()

    def tz_mark():

        return f"{day_close('2026-01-15')},{day_close('2026-07-15')}"

    def retime_all():

        mark = tz_mark()
        r_ = q("SELECT value FROM settings WHERE key='gallery_limits_tz'", one=True)
        if r_ and r_["value"] == mark:
            return
        if r_ and has_host():
            fp_now, host_ = host_fingerprint(), gallery_host()
            failed = 0
            for r in q("""SELECT s.gallery_id, g.host_fp FROM galleries g JOIN sessions s ON s.id=g.session_id
                          WHERE g.upload_state='up' AND g.removed_at IS NULL"""):
                if not GID_OK.match(r["gallery_id"] or "") or (r["host_fp"] and r["host_fp"] != fp_now):
                    continue
                try:
                    retime_limits(r["gallery_id"], host_)
                except Exception:
                    app.logger.exception("gallery limits after a time zone change %s", r["gallery_id"])
                    failed += 1
            if failed:
                return
        elif r_:
            return
        x("INSERT INTO settings (key, value) VALUES ('gallery_limits_tz', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
          (mark,))

    def clear_old_dark():

        cut = (date.today() - timedelta(days=DARK_KEEP_DAYS)).isoformat()
        for r in q("""SELECT g.session_id AS sid, s.gallery_id FROM galleries g JOIN sessions s ON s.id=g.session_id
                      WHERE g.upload_state='removed' AND g.dark_at IS NOT NULL AND g.dark_at < ?""", (cut,)):
            try:
                if r["gallery_id"] and GID_OK.match(r["gallery_id"]):
                    clear_dark(r["gallery_id"])
                x("UPDATE galleries SET dark_at=NULL WHERE session_id=?", (r["sid"],))
            except Exception:
                app.logger.exception("gallery dark page %s", r["gallery_id"])

    def draft_status(r, sent):

        n = at = None
        d = picks_blob(r["gallery_id"], "draft.bin", r["ekey"] or r["key"])
        if d:
            try:
                dp, n, at = open_draft(d, r["ekey"] or r["key"])
            except Exception:
                dp = None
            if dp is not None:
                try:
                    same = sent is not None and _same_picks(open_picks(sent, r["ekey"] or r["key"]), dp)
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
            except Exception:
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

        r = q("""SELECT s.gallery_id, g.upload_state, g.removed_at, g.uploaded_at, g.host_fp, g.moved_from, g.dark_at
                 FROM galleries g JOIN sessions s ON s.id=g.session_id WHERE g.session_id=?""", (sid,), one=True)
        if not r or not GID_OK.match(r["gallery_id"] or ""):
            return
        gid = r["gallery_id"]

        live = r["upload_state"] in ("uploading", "awaiting") or (r["upload_state"] == "up" and not r["removed_at"])
        if not (live or r["upload_state"] or r["uploaded_at"] or r["host_fp"] or r["moved_from"]):
            return
        from vault import web as VW
        hc = VW.config()
        fp_now = fingerprint_of(hc.get("gallery_host"))
        off = hc.get("gallery_host_off")
        at = r["host_fp"] or (fingerprint_of(off) if off and not fp_now else fp_now)

        on_cur = bool(fp_now) and (at == fp_now or r["upload_state"] in ("uploading", "awaiting"))
        done_cur = False
        if live:

            x("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?", (date.today().isoformat(), sid))
            if on_cur:
                try:
                    delete_gallery(gid)
                except Exception:
                    x("UPDATE galleries SET removed_at=?, upload_state=? WHERE session_id=?", (r["removed_at"], r["upload_state"], sid))
                    raise
                done_cur = True
        if fp_now and not done_cur:
            add_leftover(gid, fp_now)
            try:
                remove_from(gid, gallery_host())
                drop_leftover(gid, fp_now)
            except Exception:
                app.logger.exception("gallery %s of a deleted session on the host in use", gid)
        places = [fingerprint_of(off)] if off and not fp_now else []
        places += [fingerprint_of(h) for h in earlier_host_cfgs(hc)]
        for fp in dict.fromkeys(p_ for p_ in places if p_ and p_ != fp_now):
            add_leftover(gid, fp)
            n_, why = take_down_earlier(gid, fp)
            if not why:
                drop_leftover(gid, fp)

    def gallery_online(sid):

        r = q("""SELECT 1 FROM galleries WHERE session_id=? AND (upload_state IN ('uploading', 'awaiting')
                 OR (upload_state='up' AND removed_at IS NULL))""",
              (sid,), one=True)
        return bool(r)

    app.config["gallery_online"] = gallery_online

    app.config["gallery_take_down"] = take_down

    def proof_built(sid):

        g_ = gal(sid)
        return bool(g_ and g_["built_at"] and "proof" in g_.keys() and g_["proof"])

    app.config["gallery_proofs"] = proof_built

    def download_built(sid):

        s_ = q("SELECT gallery_id FROM sessions WHERE id=?", (sid,), one=True)
        g_ = gal(sid)
        return bool(s_ and g_ and g_["built_at"] and is_download(s_["gallery_id"]))

    def download_online(sid):

        s_ = q("SELECT gallery_id FROM sessions WHERE id=?", (sid,), one=True)
        return bool(s_ and online_download(s_["gallery_id"]))

    def restored_end(sid):

        import sqlite3
        try:
            r = q("SELECT restored_at, restore_days, restore_once, upload_state, removed_at FROM galleries WHERE session_id=?",
                  (sid,), one=True)
        except sqlite3.OperationalError:
            return None
        if not (r and r["restored_at"] and r["upload_state"] != "removed" and not r["removed_at"]):
            return None
        return date.fromisoformat(restored_until(r, gdays(sid)))

    app.config["gallery_restored_end"] = restored_end
    app.config["gallery_download"] = download_built
    app.config["gallery_download_online"] = download_online
    app.config["gallery_brand"] = gallery_brand

    def gdays(sid):
        r_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
        return ctx.C.gallery_days(r_["type"] if r_ else None)

    def window_changed(sid, strict=False):

        r = q("""SELECT s.gallery_id, s.extension_used, g.uploaded_at, g.restored_at, g.restore_days, g.restore_once
                 FROM galleries g JOIN sessions s ON s.id=g.session_id
                 WHERE g.session_id=? AND g.upload_state='up' AND g.removed_at IS NULL""", (sid,), one=True)
        if not r or not r["gallery_id"]:
            return
        up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
        try:
            put_limits(r["gallery_id"], gdays(sid) * (2 if r["extension_used"] else 1),
                       (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat(),
                       until=restored_until(r, gdays(sid) * (2 if r["extension_used"] else 1)), once=restored_once(r))
        except Exception:
            if strict:
                raise
            app.logger.exception("gallery limits update")

    def once_rebuilt(sid, gid):

        r = q("SELECT key, ekey, restored_at, restore_once FROM galleries WHERE session_id=?", (sid,), one=True)
        if not r or not restored_once(r):
            return None
        pieces = zip_pieces(gid, r["ekey"] or r["key"])
        x("UPDATE galleries SET restore_once=? WHERE session_id=?", (",".join(pieces) or None, sid))
        return pieces

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
        dl_online = online_download(gid)
        opened = s["sd_shipped"] if dl_online else s["first_visit"]
        restored = bool(g and "restored_at" in g.keys() and g["restored_at"] and (g["upload_state"] or "") != "removed")
        if restored:
            opened = g["restored_at"]
            days = g["restore_days"] or days
        if opened:
            first = date.fromisoformat(opened[:10])

            expires = first + timedelta(days=days * (2 if s["extension_used"] and not restored else 1))
            ext_date = first + timedelta(days=days * 2)
            left = (expires - date.today()).days
            exp_status = "red" if left < 0 else "amber" if left <= 1 else None
        preview = None
        if g and g["built_at"]:
            preview = url_for("gallery_preview", gid=gid) + "#k=" + (g["ekey"] or g["key"])
        pct = round(100 * job["done"] / job["total"]) if job and job.get("total") else 0
        n_fin_ = len(uploaded(s["id"], "final"))
        html = render_template_string(BLOCK, s=s, g=g, job=job, pct=pct, src=src, src_n=src_n, dirs=src_dirs() if FOLDERS else [],
                                      n_up=len(uploaded(s["id"])), folders=FOLDERS,
                                      n_fin=n_fin_, use_fin=build_from_finals(s, n_fin_),
                                      preview=preview, expires=expires, exp_status=exp_status, ext_date=ext_date,
                                      days=days, nice=ctx.nice, money=ctx.money, has_host=has_host(),
                                      can_restore=bool(g and g["upload_state"] == "removed" and has_host() and GID_OK.match(gid)
                                                       and not being_deleted(s)
                                                       and (OUT_ROOT / gid / "manifest.bin").is_file()),
                                      can_once=bool(g and g["upload_state"] == "removed" and gate_built(gid) and not being_deleted(s)
                                                    and zip_pieces(gid, g["ekey"] or g["key"])),
                                      once_on=bool(restored and g["restore_once"]), usual=ctx.C.gallery_days(s["type"]),
                                      once_days=ONCE_DAYS,
                                      moved_off=bool(g and g["upload_state"] in ("up", "removed") and g["host_fp"]
                                                     and (g["upload_state"] == "up" or g["dark_at"]) and has_host()
                                                     and g["host_fp"] != host_fingerprint()),
                                      new_link=bool(g and g["upload_state"] == "up" and g["link_base"] and g["link_base"] != base_url()),
                                      owed=ctx.release_blocked(s["id"]), proof=proof_built(s["id"]),
                                      updating=UPDATING.get(s["id"]),
                                      cw_on=bool(ctx.C.POLICY[s["type"]].get("gallery_codeword")),
                                      stale_cw=bool(g and g["built_at"] and not g["cw_fixed"] and codeword_key_cached(
                                          g["key"], gid, (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or "")
                                          != (g["ekey"] or g["key"])),
                                      back=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery",
                                      link=gallery_link(s["id"]) if g and g["upload_state"] == "up" else None,
                                      stale=bool(g and gid and g["upload_state"] == "up" and not online_is_current(gid)),
                                      dl=is_download(gid) if g and g["built_at"] else False, dl_next=wants_download(s),
                                      dl_online=dl_online, opened=opened,
                                      skip_picks=may_skip_picks(s) and not (job and job.get("state") == "running"),
                                      mailto=link_mail(s) if g and g["upload_state"] == "up" else "")
        return [("Gallery", html)]

    def link_mail(s):

        from urllib.parse import quote as qu
        link = gallery_link(s["id"])
        if not link:
            return ""
        who = q("""SELECT name, email FROM people WHERE household_id=? AND role IN ('parent','adult') AND COALESCE(email,'') != ''
                   ORDER BY role='parent' DESC, id LIMIT 1""", (s["household_id"],), one=True)
        first = ((who["name"] or "").split() or [""])[0] if who else ""
        sign = setting_("photographer_name") or setting_("business_name") or setting_("studio_name")
        dl = online_download(s["gallery_id"])
        title = ((s["title"] if "title" in s.keys() else "") or "").strip()
        picked = not dl and bool(s["favorites"] or s["retouch_picks"] or s["veto"])
        subject = ("Your photos are ready to download" if dl else "Your gallery link" if picked
                   else "Your photos are ready to pick from") + (f": {title}" if title else "")
        days = gdays(s["id"]) * (2 if s["extension_used"] else 1)
        lines = [f"Hi {first}," if first else "Hello,",
                 ("Your photos are ready to download:" if dl else
                  "Thank you for your picks. Here is your gallery link again:" if picked else
                  "Your photos are ready. Open your gallery to pick your favorites:") + "\n" + link,
                 f"The gallery is open for {days} days after you first open it."
                 + (" Please download your photos in that time." if dl else "")
                 + (" When it asks, enter the word you chose with me." if "&c=1" in link else ""),
                 f"Thank you,\n{sign}" if sign else "Thank you"]
        body = "\n\n".join(lines)
        return f"mailto:{qu(who['email'] if who else '', safe='@')}?subject={qu(subject)}&body={qu(body)}"

    def step_action(label, s):
        here = url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery"
        if label == STEP_LABEL and has_gallery_step(s):
            return dict(verb="Build gallery", href=here)
        if label == "Files delivered" and link_delivery(s) and s["stage"] != "closed":

            g = gal(s["id"])
            gid = s["gallery_id"] or ""
            alt = dict(verb="Mark delivered", post=url_for("quick", sid=s["id"]), fields=dict(act="mark", field="sd_shipped"))
            if not wants_download(s):
                return None
            if not (g and g["built_at"] and is_download(gid)):
                return dict(what="Build the download gallery", verb="Build download gallery", href=here, alt=alt)
            if not (g["upload_state"] == "up" and not g["removed_at"] and online_download(gid)):
                return dict(what="Upload the download gallery", verb="Upload", href=here, alt=alt)
            return dict(what="Files delivered when the client opens the download gallery", verb="Check now",
                        post=url_for("gallery_sync_now"), fields={}, theirs=True, alt=alt)
        return None

    ctx.hooks.stage_blocks.append(block)
    ctx.hooks.step_actions.append(step_action)

    def still_picking(s):

        g = gal(s["id"])
        if not (g and g["draft_n"] is not None and g["upload_state"] == "up" and not g["removed_at"]) \
                or online_download(s["gallery_id"]):
            return []
        n = g["draft_n"]
        return [dict(what=f"Client is still picking ({n} photo{'' if n == 1 else 's'} marked)", theirs=True,
                     since=(g["draft_at"] or "")[:10] or None, verb="Check now", post=url_for("gallery_sync_now"), fields={})]

    ctx.hooks.session_actions.append(still_picking)
