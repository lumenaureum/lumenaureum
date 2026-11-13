"""Encrypted client review gallery.

Per session: a folder of exported JPEGs becomes a folder of AES-256-GCM ciphertext (web size + thumb per photo,
random file names, metadata stripped) plus an encrypted manifest and a static viewer that decrypts in the browser
with the key from the URL fragment. Only that folder may ever cross to the web host. The key stays in this
(home-only) database and reaches the client as the QR link in the mailed letter.

Upload sends the folder to the gallery host configured in storage.json ("gallery_host": SFTP, S3 or a local folder).
A plugin may hold an upload (for an approval, say) through hooks.guards["gallery_upload"] (see plugins/README.md). After upload,
everything is automatic: every SYNC_MIN minutes the app reads each live gallery's private seen.txt to tick
"Client opened it", and removes the folder once its days (one extension doubles them) run out.
"""
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

from flask import abort, jsonify, redirect, request, send_from_directory, url_for

ASSETS = Path(__file__).with_name("gallery_assets")


def _script_hashes(html):
    """CSP hashes of a page's inline script blocks, so the preview runs the viewer's own script and nothing else."""
    import hashlib
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return "".join(f" 'sha256-{base64.b64encode(hashlib.sha256(b.encode()).digest()).decode()}'" for b in blocks)


# The footer every client gallery carries: the Lumen Aureum mark and a link to the project (the owner can turn it off).
_MARK = re.sub(r"<svg ", '<svg aria-hidden="true" focusable="false" ', (Path(__file__).resolve().parent.parent / "brand" / "mark.svg").read_text().strip(), count=1) \
    if (Path(__file__).resolve().parent.parent / "brand" / "mark.svg").is_file() else ""
CREDIT_HTML = ('<a id="la" href="https://lumenaureum.org" target="_blank" rel="noopener noreferrer">' + _MARK +
               '<span>Gallery by <b>Lumen Aureum</b><small>lumenaureum.org</small></span></a>')
VIEWER_SCRIPT_HASHES = _script_hashes((ASSETS / "viewer.html").read_text())
_DATA = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent.parent / "data" / "crm.db")).parent
OUT_ROOT = Path(os.environ.get("CRM_GALLERY_ROOT", _DATA / "galleries"))
PHOTOS_ROOT = Path(os.environ.get("CRM_PHOTOS_ROOT", _DATA / "photos"))
# Plain photo folders (exported JPEGs left readable on a share) are an opt-in for those who accept that risk;
# by default galleries are built from the edited photos uploaded into the app, which are sealed on arrival.
FOLDERS = os.environ.get("CRM_PHOTO_FOLDERS", "") == "1"
DEFAULT_BASE_URL = ""   # set in Settings: the public address your gallery host serves, e.g. https://example.com/g/
STEP_LABEL = "Gallery built"
WEB_EDGE, THUMB_EDGE = 2048, 480
JPEG_EXT = {".jpg", ".jpeg"}
GID_OK = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

JOBS = {}  # session_id -> dict(state=running|done|error, done=int, total=int, error=str)
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


# ---------------------------------------------------------------- crypto + images (no Flask here)

def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def key_bytes(k):
    return base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))


CODEWORD_ROUNDS = 600_000          # PBKDF2-SHA256 rounds: slows every guess at a codeword (fixed: changing it breaks links)
CODEWORD_WORDS = ("amber", "anchor", "aspen", "birch", "blossom", "bramble", "breeze", "brook", "canyon", "cedar", "clover",
                  "comet", "coral", "cove", "crane", "dahlia", "dawn", "delta", "ember", "fern", "fjord", "glacier", "harbor",
                  "hazel", "heron", "hollow", "iris", "ivy", "juniper", "kestrel", "lagoon", "lantern", "laurel", "linden",
                  "maple", "meadow", "mesa", "orchard", "osprey", "otter", "pebble", "pine", "plover", "prairie", "quartz",
                  "raven", "reef", "ridge", "river", "robin", "sage", "sparrow", "spruce", "summit", "thistle", "tide",
                  "timber", "trellis", "tulip", "valley", "violet", "willow", "wren", "zephyr")


def codeword_strong(word):
    """At least three real words (three letters or more, not all the same), or 14 letters with some variety: anyone
    with the link can guess offline, so short or repetitive words fall in seconds."""
    w = norm_codeword(word)
    parts = w.split()
    letters = w.replace(" ", "")
    if len(set(letters)) < 5:
        return False
    return (len(parts) >= 3 and all(len(p_) >= 3 for p_ in parts) and len(set(parts)) >= 3) or len(letters) >= 14


def suggest_codeword():
    return " ".join(secrets.choice(CODEWORD_WORDS) for _ in range(4))   # four from the list: about 17 million


def norm_codeword(w):
    """How a codeword is compared: Unicode NFKC, trimmed, lowercase, runs of spaces made single. The gallery page does
    exactly the same in the browser, so "Biscuit", " biscuit " and "BISCUIT" open the same gallery."""
    import re as _re
    import unicodedata
    w = unicodedata.normalize("NFKC", w or "")
    w = _re.sub("[\u200b-\u200d\u2060\ufeff\u00ad]", "", w)                 # zero-width and soft-hyphen: dropped
    w = _re.sub(r"[\x00-\x1f\x7f\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+", " ", w)  # any space: one space
    return w.strip().lower()


def codeword_key(link_key, gid, word):
    """The key of a gallery protected by a codeword: PBKDF2-SHA256(codeword, salt = the link's 32-byte secret followed by
    the gallery id, CODEWORD_ROUNDS) -> 32 bytes. The link alone (a strong secret) or the word alone opens nothing; with
    the link, each guess at the word costs the rounds. Returned in the same form as new_key(). No word: the link's key."""
    import hashlib
    w = norm_codeword(word)
    if not w:
        return link_key
    dk = hashlib.pbkdf2_hmac("sha256", w.encode("utf-8"), key_bytes(link_key) + gid.encode("ascii"), CODEWORD_ROUNDS, 32)
    return base64.urlsafe_b64encode(dk).rstrip(b"=").decode()


@functools.lru_cache(maxsize=128)
def codeword_key_cached(link_key, gid, word):
    """codeword_key, remembered: the panel checks it on every render, and each costs the full rounds."""
    return codeword_key(link_key, gid, word)


def seal(aes, data, name):
    """AES-256-GCM with a fresh 96-bit nonce; the file's own name is the associated data, so a host can't serve one
    encrypted file in place of another (it would fail to open)."""
    iv = secrets.token_bytes(12)
    return iv + aes.encrypt(iv, data, ("lumen-aureum-gallery:" + name).encode())


def clean_jpeg(path):
    """Decode, orient, convert to sRGB, and re-encode from raw pixels so no EXIF/GPS/ICC/XMP/comment survives."""
    from PIL import Image, ImageCms, ImageOps
    with Image.open(path) as im:
        if im.format == "JPEG":
            w_, h_ = im.size                                # decode at a reduced size, never below the web size: PIL
            r_ = min(1.0, WEB_EDGE / max(w_, h_))            # shrinks only while both sides stay at or above the request
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
        pixels = Image.frombytes("RGB", im.size, im.tobytes())  # fresh image: empty info dict

    def encode(img, edge, quality):
        img = img.copy()
        img.thumbnail((edge, edge), Image.LANCZOS)
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


def build(gid, src, key, progress=lambda done, total: None, picks=None, credit="", brand=None, codeword=False):
    """Write the encrypted gallery for gid from src: a folder of JPEGs, or a list of (name, open) where open() gives a
    file object (an uploaded, sealed export decrypted in memory). Returns (out_dir, n_photos). Atomic: the old build is
    replaced at the end."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(key_bytes(key))
    files = [opener for _, opener in src] if isinstance(src, list) else list_jpegs(src)
    names = [n_ for n_, _ in src] if isinstance(src, list) else [p_.name for p_ in files]   # in the client's picks email
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
            web, (w, h), thumb, (tw, th) = clean_jpeg(p() if callable(p) else p)
        except Exception:                       # not a picture this can read (a mislabeled file): named, not fatal
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
    build.skipped = skipped                     # the caller names them in the result
    brand = dict(brand or {})                   # studio, logo, client, date: inside the encrypted manifest, never in the page
    manifest = dict(v=1, title=brand.pop("client", "") or "Your photos", items=items, picks=picks or {}, brand=brand)
    (tmp / "manifest.bin").write_bytes(seal(aes, json.dumps(manifest, separators=(",", ":")).encode(), "manifest.bin"))
    page = (ASSETS / "viewer.html").read_text()
    page = page.replace('<html lang="en">', f'<html lang="en" data-gid="{gid}">', 1)   # the codeword's salt (public: in the URL)
    if (picks or {}).get("post"):               # a PHP host: every file goes through gate.php, which enforces the window
        page = page.replace('<html lang="en"', '<html lang="en" data-gate="1"', 1)   # outside any script (CSP hash unchanged)
    if credit:                                  # plain markup in the page body, outside any script (the CSP hash is unchanged)
        page = page.replace("<span hidden data-credit></span>", CREDIT_HTML)
    (tmp / "index.html").write_text(page)
    shutil.copyfile(ASSETS / "seen.php", tmp / "seen.php")
    shutil.copyfile(ASSETS / "picks.php", tmp / "picks.php")
    shutil.copyfile(ASSETS / "gate.php", tmp / "gate.php")
    import hashlib
    # The visitor proves they hold the key by sending sha256(key) (32 hex). The host keeps only the SHA-256 of that proof,
    # inside a PHP file that prints nothing when fetched, so even a host that serves every file gives nothing away.
    proof = hashlib.sha256(key_bytes(key)).hexdigest()[:32]
    (tmp / "seen-key.php").write_text("<?php exit; ?>\n" + hashlib.sha256(proof.encode()).hexdigest() + "\n")
    shutil.copyfile(ASSETS / "htaccess", tmp / ".htaccess")
    old = OUT_ROOT / f".{gid}.old"
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        final.rename(old)
    tmp.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return final, len(items)


SYNC_MIN = 30
_HAS_LIMITS = set()
# Where galleries are served from is a storage backend (vault/backends.py): "gallery_host" in data/storage.json,
# e.g. {"type": "sftp", "host": ..., "user": ..., "key_file": ..., "root": "example.com/g"} or an S3 bucket behind a CDN.


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
        ".txt": "text/plain", ".key": "text/plain", ".htaccess": "text/plain"}


def put_limits(gid, days, unopened_until):
    """The gallery's own deadline, kept on the host: gate.php refuses every photo once it has passed, so a gallery
    expires on time even when Lumen Aureum is locked or switched off (hosts that run PHP; see docs)."""
    import io
    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    body = f"days={int(days)}\nunopened_until={unopened_until}\n".encode()
    gallery_host().put_stream(f"{gid}/limits.txt", io.BytesIO(body), content_type="text/plain")


def upload_gallery(gid, days=None, unopened_until=None):
    """Copy OUT_ROOT/<gid>/ (ciphertext, viewer, seen.php, gate.php, .htaccess) to the gallery host under <gid>/, with
    its deadline (limits.txt). The page goes last, so a visitor never loads half a gallery."""
    if not GID_OK.match(gid):
        raise ValueError("bad gallery id")
    host = gallery_host()
    if days is not None:
        put_limits(gid, days, unopened_until)
    files = sorted((p for p in (OUT_ROOT / gid).iterdir() if p.is_file()), key=lambda p: p.name == "index.html")
    for p in files:
        with open(p, "rb") as f:
            host.put_stream(f"{gid}/{p.name}", f, content_type=MIME.get(p.suffix or p.name))
    return len(files) + (1 if days is not None else 0)


def seen_at(gid):
    """The first-visit stamp the gallery wrote on the host (UTC ISO), or None (also None on hosts that cannot run
    the stamp script, such as a plain S3 bucket)."""
    import io
    host = gallery_host()
    if host.stat(f"{gid}/seen.txt") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/seen.txt", buf)
    return buf.getvalue().decode().strip() or None


PICKS_AAD = b"lumen-aureum-gallery:picks.bin"


def picks_blob(gid):
    """The client's encrypted picks (picks.bin) from the host, or None."""
    import io
    host = gallery_host()
    if host.stat(f"{gid}/picks.bin") is None:
        return None
    buf = io.BytesIO()
    host.get_stream(f"{gid}/picks.bin", buf)
    return buf.getvalue()


FP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def picks_fingerprint(lines, key):
    """The fingerprint the gallery puts in a picks email: HMAC-SHA256 (key from HKDF-SHA256 of the gallery key) over the
    lines, each trimmed with runs of spaces made single, blank lines dropped; 10 bytes as 16 letters in groups of 4."""
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
    """A pasted picks email -> (picks or None, matches, message). The fingerprint says whether the list is what was picked
    in the gallery. A list edited in the email (the client changed their mind while writing) still counts: it means
    asking the client which picks are final, not refusing them."""
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


def open_picks(blob, key):
    """picks.bin -> the picks, checked: 'LAK1' | 12-byte nonce | AES-256-GCM under the gallery key. Anything else
    (or anything that doesn't decrypt) is ignored."""
    import json as _json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob or blob[:4] != b"LAK1" or len(blob) < 32 or len(blob) > 65536:
        raise ValueError("not a picks file")
    data = _json.loads(AESGCM(key_bytes(key)).decrypt(blob[4:16], blob[16:], PICKS_AAD))
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise ValueError("not picks")
    out = dict(retouch=[], suggest=[], veto=[])
    for p_ in data["picks"][:500]:
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
    """Remove every file of <gid>/ from the gallery host, then the folder."""
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


# ---------------------------------------------------------------- plugin

def register(ctx):
    app, q, x = ctx.app, ctx.q, ctx.x
    from flask import render_template_string
    ctx.migrate("""CREATE TABLE IF NOT EXISTS galleries (
        session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        source_dir TEXT DEFAULT '', built_at TEXT, n_files INTEGER DEFAULT 0, out_dir TEXT DEFAULT '',
        key TEXT NOT NULL);""")
    @ctx.on_unlock
    def _migrate_cols():      # the database opens only after sign-in: migrate then, on a direct connection
        _con = ctx.connect()
        _gcols = [r_[1] for r_ in _con.execute("PRAGMA table_info(galleries)")]
        if "picks_hash" not in _gcols:
            ctx.add_column(_con, "galleries", "picks_hash", "TEXT DEFAULT ''")
        if "limits_days" not in _gcols:
            ctx.add_column(_con, "galleries", "limits_days", "INTEGER")
        if "ekey" not in _gcols:                   # the key the gallery is encrypted with (the link's, or with a codeword)
            ctx.add_column(_con, "galleries", "ekey", "TEXT DEFAULT ''")
        if "cw_fixed" not in _gcols:               # set at first upload: 'word' or 'none', never changed afterwards
            ctx.add_column(_con, "galleries", "cw_fixed", "TEXT")
            if "uploaded_at" in _gcols:
                _con.execute("UPDATE galleries SET cw_fixed='none' WHERE uploaded_at IS NOT NULL")   # online before codewords
        gcols = [r[1] for r in _con.execute("PRAGMA table_info(galleries)")]
        for col, decl in (("upload_state", "TEXT DEFAULT ''"), ("uploaded_at", "TEXT"), ("removed_at", "TEXT"), ("upload_error", "TEXT DEFAULT ''")):
            if col not in gcols:
                ctx.add_column(_con, "galleries", col, decl)
        _con.commit(); _con.close()

    def has_gallery_step(s):
        flow = ctx.C.WORKFLOW.get(s["type"], {})
        return any(st[0] == "auto" and st[1] == "gallery_built" for steps in flow.values() for st in steps)

    def gal(sid):
        return q("SELECT * FROM galleries WHERE session_id=?", (sid,), one=True)

    def ensure(s):
        """The gallery row (with a key), and a gallery_id on the session."""
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
        """What the gallery's header and footer show: the studio's logo and name, the client, the session and its
        date, and how to reach the photographer. All of it travels inside the encrypted manifest."""
        def setting(k):
            r_ = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
            return (r_["value"] if r_ and r_["value"] else "").strip()
        h = q("SELECT name FROM households WHERE id=?", (s["household_id"],), one=True)
        when = ""
        if s["shoot_date"]:
            d_ = date.fromisoformat(s["shoot_date"][:10])
            when = f"{d_:%B} {d_.day}, {d_.year}"
        label = ctx.C.SESSION_TYPES.get(s["type"], "")
        return dict(client=h["name"] if h else "",
                    what=label, date=when, logo=setting("logo_png"),
                    studio=setting("business_name") or setting("studio_name") or setting("photographer_name"),
                    photographer=setting("photographer_name"), email=setting("business_email"), phone=setting("business_phone"))

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
        return f"{base_url()}{s['gallery_id']}/#k={g['key']}" + ("&c=1" if cw else "")   # c=1: the page asks for the word

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
        """The session's uploaded edited JPEGs, in natural name order, each opened (decrypted in memory) on demand."""
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
            # whether a gallery has a codeword (and which) is fixed once it has been online: a link or letter already sent
            # must keep opening it
            JOBS[sid] = dict(state="error", done=0, total=0,
                             error="This gallery is already online " + ("without a codeword" if fixed == "none" else "with its codeword")
                                   + ", so that stays as it is: the link you sent keeps working.")
            return redirect(back)
        with LOCK:
            if JOBS.get(sid, {}).get("state") == "running":
                return redirect(back)
            JOBS[sid] = dict(state="running", done=0, total=0, error="")

        em = q("SELECT value FROM settings WHERE key='business_email'", one=True)
        # the client picks retouch photos (with notes) and marketing favorites in the gallery, then emails the list
        #; nothing is stored on the web host
        from vault import web as VW
        host_type = (VW.config().get("gallery_host") or {}).get("type", "")
        picks_cfg = dict(retouch=ctx.C.RETOUCH_PICKS, market=0 if s["portfolio_no"] else ctx.C.MARKETING_PICKS,
                         email=(em["value"] if em else ""), ref=gid,
                         post=host_type in ("sftp", "local"))       # a web host that runs PHP: picks come back by themselves
        off = q("SELECT value FROM settings WHERE key='credit_off'", one=True)
        credit = "" if off and off["value"] == "1" else ctx.C.CREDIT
        brand = gallery_brand(s)

        def run():
            import locks
            job = JOBS[sid]

            def progress(done, total):
                job["done"], job["total"] = done, total
            lk = locks.session(sid)                     # takes turns with sealing and deleting the session's photos
            lk.acquire()
            try:
                out, n = build(gid, src, key, progress, picks=picks_cfg, credit=credit, brand=brand, codeword=bool(norm_codeword(word)))
                with app.app_context():
                    gone = not q("SELECT 1 FROM sessions WHERE id=?", (sid,), one=True) or (
                        q("SELECT 1 FROM sqlite_master WHERE name='retention'", one=True)
                        and q("SELECT 1 FROM retention WHERE session_id=? AND purged_at IS NOT NULL", (sid,), one=True))
                    if gone:                            # deleted while it was building: nothing of it may stay
                        shutil.rmtree(out, ignore_errors=True)
                        raise RuntimeError("the session's photos were deleted while the gallery was building")
                    x("UPDATE galleries SET built_at=?, n_files=?, out_dir=?, ekey=? WHERE session_id=?",
                      (date.today().isoformat(), n, str(out) + "/", key, sid))
                    x("UPDATE sessions SET gallery_built=? WHERE id=?", (date.today().isoformat(), sid))
                    sk_ = getattr(build, "skipped", [])
                    ctx.audit(f"Gallery built: {n} photos encrypted" + (f"; skipped (not a JPEG this can read): {', '.join(sk_[:10])}" if sk_ else ""),
                              sid=sid, who="Lumen Aureum")
                    ctx.advance(sid)
                job["state"] = "done"
                if getattr(build, "skipped", []):
                    job["error"] = "Skipped, not a JPEG this can read: " + ", ".join(build.skipped[:10])
            except Exception as e:  # shown in the block
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

    app.config.setdefault("quiet_endpoints", set()).add("picks_paste")      # it writes its own History line
    @app.post("/session/<int:sid>/picks/paste", endpoint="picks_paste")
    def picks_paste(sid):
        """A picks email pasted in: recorded only when its fingerprint matches the list."""
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
                x("INSERT INTO tasks (session_id, title, due, sort, stage, own) VALUES (?,?,?,999,?,1)",
                  (sid, "Ask the client which picks are final", date.today().isoformat(), s["stage"]))
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
        if p == "gate.php":                         # the preview has no window to enforce: serve the named file
            p = request.args.get("f", "")
            if not re.fullmatch(r"(?:[0-9a-f]{24}|manifest)\.bin", p):
                abort(404)
        if p == "index.html":
            built = OUT_ROOT / gid / "index.html"
            if built.is_file() and _script_hashes(built.read_text(errors="replace")) != VIEWER_SCRIPT_HASHES:
                # built by an older version: its script isn't the one this policy allows, so say so plainly
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
            try:                                    # only if nobody took it down (or deleted the client) meanwhile
                n_ = con_.execute("""UPDATE galleries SET upload_state='up', uploaded_at=?, removed_at=NULL,
                                     cw_fixed=COALESCE(cw_fixed, CASE WHEN ekey != '' AND ekey != key THEN 'word' ELSE 'none' END)
                                     WHERE session_id=? AND upload_state='uploading'""", (date.today().isoformat(), sid)).rowcount
                con_.commit()
            finally:
                con_.close()
            if n_ != 1:
                delete_gallery(gid)                 # taken down while uploading: it never stays on the web
                return
            with app.app_context():
                ctx.audit(f"Gallery uploaded ({k} files)", sid=sid, who="Lumen Aureum")
        except Exception as e:
            app.logger.exception("gallery upload")
            with app.app_context():
                x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?", (str(e)[:200], sid))

    @app.post("/session/<int:sid>/gallery/upload", endpoint="upload_gallery_req")
    def upload_gallery_req(sid):
        s = session(sid)
        g = gal(sid)
        back = url_for("client_stage", hid=s["household_id"]) + f"?s={sid}#gallery"
        if not g or not g["built_at"] or g["upload_state"] in ("awaiting", "uploading"):
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
            # a plugin may hold the upload (an approval, say) and start it later through proceed()
            if ctx.hooks.guard("gallery_upload", lambda: do_upload(sid, gid), sid=sid, gallery_id=gid, n_files=g["n_files"]):
                x("UPDATE galleries SET upload_state='awaiting', upload_error='' WHERE session_id=?", (sid,))
                return redirect(back)
        except Exception as e:
            x("UPDATE galleries SET upload_state='error', upload_error=? WHERE session_id=?", (f"held upload failed: {e}"[:200], sid))
            return redirect(back)
        threading.Thread(target=do_upload, args=(sid, gid), name=f"gal-up-{sid}", daemon=True).start()
        return redirect(back)

    PICKS_ON = lambda r: True                 # every live gallery: a host without the script simply never has picks.bin

    def sync_once():
        """Opened stamps in, expired galleries out. Read-only except the approved deletion, and giving a gallery its
        host-side deadline (limits.txt) if it was uploaded before galleries carried one."""
        from datetime import datetime, timezone
        rows = q("""SELECT g.*, s.gallery_id, s.first_visit, s.extension_used, s.id AS sid FROM galleries g
                    JOIN sessions s ON s.id=g.session_id WHERE g.upload_state='up' AND g.removed_at IS NULL""")
        for r in rows:
            gid = r["gallery_id"]
            try:
                if gid not in _HAS_LIMITS:          # once per run: galleries from before host-side expiry get theirs
                    host = gallery_host()
                    if host.stat(f"{gid}/limits.txt") is None:
                        up = date.fromisoformat((r["uploaded_at"] or date.today().isoformat())[:10])
                        with open(ASSETS / "gate.php", "rb") as f_:             # the gate first,
                            host.put_stream(f"{gid}/gate.php", f_, content_type="text/plain")
                        put_limits(gid, gdays(r["sid"]) * (2 if r["extension_used"] else 1),
                                   (up + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)).isoformat())
                        with open(ASSETS / "htaccess", "rb") as f_:             # then the rule that routes through it
                            host.put_stream(f"{gid}/.htaccess", f_, content_type="text/plain")
                    _HAS_LIMITS.add(gid)
                want_days = gdays(r["sid"]) * (2 if r["extension_used"] else 1)
                if (r["limits_days"] or 0) != want_days:      # the window changed (a setting, an upgrade): the host follows
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
                                pk = None                    # not ours or not readable: ignored, never shown
                            x("UPDATE galleries SET picks_hash=? WHERE session_id=?", (hh, r["sid"]))
                            if pk is not None:
                                x("UPDATE sessions SET retouch_picks=?, favorites=?, veto=? WHERE id=?",
                                  ("\n".join(pk["retouch"]), ", ".join(pk["suggest"]), ", ".join(pk["veto"]), r["sid"]))
                                ctx.audit(f"Picks received from the gallery: {len(pk['retouch'])} to retouch, "
                                          f"{len(pk['suggest'])} suggested for the portfolio, {len(pk['veto'])} not to show",
                                          sid=r["sid"], who="Lumen Aureum")
                                ctx.advance(r["sid"])
                if first:
                    gone = date.fromisoformat(first[:10]) + timedelta(days=gdays(r["sid"]) * (2 if r["extension_used"] else 1))
                else:
                    gone = date.fromisoformat(r["uploaded_at"][:10]) + timedelta(days=ctx.C.GALLERY_UNOPENED_DAYS)
                if gone:
                    if date.today() > gone and app.config["clock_ok"]():    # never on a date that just jumped
                        n = delete_gallery(gid)
                        x("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?",
                          (date.today().isoformat(), r["sid"]))
                        ctx.audit(f"Gallery removed from the web ({n} files), " + (f"{gdays(r['sid'])} day window over" if first else "never opened in a year"),
                                  sid=r["sid"], who="Lumen Aureum")
            except Exception:
                app.logger.exception("gallery sync %s", gid)

    def sync_loop():
        import time
        time.sleep(20)
        while True:
            if not ctx.unlocked():          # locked after a restart: nothing to do until the owner signs in
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
        """Remove a session's gallery from the web now (used when demo clients are deleted)."""
        r = q("""SELECT s.gallery_id, g.upload_state FROM galleries g JOIN sessions s ON s.id=g.session_id
                 WHERE g.session_id=? AND g.upload_state IN ('up', 'uploading', 'awaiting') AND g.removed_at IS NULL""", (sid,), one=True)
        if r and r["gallery_id"]:
            # mark it first: an upload still running sees this at its end and deletes what it put up
            x("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?", (date.today().isoformat(), sid))
            delete_gallery(r["gallery_id"])

    app.config["gallery_take_down"] = take_down

    def gdays(sid):
        r_ = q("SELECT type FROM sessions WHERE id=?", (sid,), one=True)
        return ctx.C.gallery_days(r_["type"] if r_ else None)

    def window_changed(sid):
        """An extension (or an edited delivery record): the host's own deadline follows."""
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
            preview = url_for("gallery_preview", gid=gid) + "#k=" + (g["ekey"] or g["key"])   # the app holds the final key
        pct = round(100 * job["done"] / job["total"]) if job and job.get("total") else 0
        html = render_template_string(BLOCK, s=s, g=g, job=job, pct=pct, src=src, src_n=src_n, dirs=src_dirs() if FOLDERS else [],
                                      n_up=len(uploaded(s["id"])), folders=FOLDERS,
                                      preview=preview, expires=expires, exp_status=exp_status, ext_date=ext_date,
                                      days=days, nice=ctx.nice, money=ctx.money, has_host=has_host(),
                                      owed=ctx.release_blocked(s["id"]), cw_on=bool(ctx.C.POLICY[s["type"]].get("gallery_codeword")),
                                      stale_cw=bool(g and g["built_at"] and not g["cw_fixed"] and codeword_key_cached(
                                          g["key"], gid, (s["gallery_codeword"] if "gallery_codeword" in s.keys() else "") or "")
                                          != (g["ekey"] or g["key"])),
                                      back=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery")
        return [("Gallery", html)]

    def step_action(label, s):
        if label == STEP_LABEL and has_gallery_step(s):
            return dict(verb="Build gallery", href=url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}#gallery")
        return None

    ctx.hooks.stage_blocks.append(block)
    ctx.hooks.step_actions.append(step_action)
