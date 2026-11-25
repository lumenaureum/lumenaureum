import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import stat
import sys
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

PLUGIN_SIGNING_KEY = "uT2STQ03ZoKPNb3Tdb4V5IDffJuTvYtTsCB30rFnpvU="

FORMAT = "lumenaureum-plugin/1"
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
VERSION_RE = re.compile(r"\d{1,6}(?:\.\d{1,6}){0,3}")
PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
MAX_MANIFEST = 256 * 1024
MAX_PACKAGE = 16 * 1024 * 1024
MAX_FILES = 400
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_DEPTH = 8
MAX_LINK = 2000
DEADLINE = 90
MAX_REDIRECTS = 3
SETTING = "plugin_package_"
USER_AGENT = "LumenAureum-plugins"
MARK = ".on"

NOT_PLUGIN = "That isn't a Lumen Aureum plugin, so nothing was added."
NO_KEY = "Plugins can't be added yet: this version of Lumen Aureum has no key to check them with."
UNSIGNED = "That plugin isn't signed, so it wasn't added. Only plugins signed by Lumen Aureum can be added."
BAD_SIG = "That plugin's signature doesn't match Lumen Aureum's key, so it wasn't added."
MISMATCH = "That plugin's files don't match what its signature covers, so it wasn't added."
NOT_SAFE = ("That plugin holds something a plugin never may (a link, or a file outside its own folder), so it wasn't "
            "added.")
TOO_BIG = "That plugin is larger than a plugin can be, so it wasn't added."

class Refused(Exception):

    def __init__(self, words, short=""):
        super().__init__(words)
        self.words = words
        self.short = short or "it didn't pass its checks"

def _vtuple(v):
    return tuple(int(n) for n in v.split("."))

def newer(a, b):

    ta, tb = _vtuple(a), _vtuple(b)
    n = max(len(ta), len(tb))
    return ta + (0,) * (n - len(ta)) > tb + (0,) * (n - len(tb))

def app_version():
    import about
    return about.version()

def _path_ok(p, name):

    if not isinstance(p, str) or not p or len(p) > 400:
        return False
    parts = p.split("/")
    if len(parts) > MAX_DEPTH or not all(PART_RE.fullmatch(x) for x in parts):
        return False
    if len(parts) == 1:
        return p != "manifest.json"
    return parts[0] == f"{name}_assets"

def parse_manifest(raw):

    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_MANIFEST:
        raise Refused(TOO_BIG if raw and len(raw) > MAX_MANIFEST else NOT_PLUGIN)
    try:
        m = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused(NOT_PLUGIN) from None
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise Refused(NOT_PLUGIN)
    name, version, min_app = m.get("name"), m.get("version"), m.get("min_app")
    if not (isinstance(name, str) and NAME_RE.fullmatch(name) and isinstance(version, str)
            and VERSION_RE.fullmatch(version) and isinstance(min_app, str) and VERSION_RE.fullmatch(min_app)):
        raise Refused(NOT_PLUGIN)
    files = m.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise Refused(NOT_PLUGIN if not isinstance(files, list) or not files else TOO_BIG)
    out, seen, total = [], set(), 0
    for e in files:
        if not isinstance(e, dict):
            raise Refused(NOT_PLUGIN)
        p, size, sha = e.get("path"), e.get("size"), e.get("sha256")
        if not _path_ok(p, name):
            raise Refused(NOT_SAFE)
        if not (isinstance(size, int) and not isinstance(size, bool) and 0 <= size and isinstance(sha, str)
                and SHA_RE.fullmatch(sha)):
            raise Refused(NOT_PLUGIN)
        if size > MAX_FILE:
            raise Refused(TOO_BIG)
        if p.lower() in seen:
            raise Refused(NOT_SAFE)
        seen.add(p.lower())
        total += size
        out.append(dict(path=p, size=size, sha256=sha))
    if total > MAX_TOTAL:
        raise Refused(TOO_BIG)
    if f"{name}.py" not in {e["path"] for e in out}:
        raise Refused(NOT_PLUGIN)
    sig, pkg = m.get("signature", ""), m.get("package", "")
    if not isinstance(sig, str) or not isinstance(pkg, str) or len(pkg) > MAX_LINK or len(sig) > 200:
        raise Refused(NOT_PLUGIN)
    clean = dict(format=FORMAT, name=name, version=version, min_app=min_app,
                 files=sorted(out, key=lambda e: e["path"]), signature=sig)
    if pkg:
        clean["package"] = pkg
    return clean

def files_digest(files):

    lines = "".join(f"{e['sha256']} {e['size']} {e['path']}\n" for e in sorted(files, key=lambda e: e["path"]))
    return hashlib.sha256(lines.encode("ascii")).hexdigest()

def signed_bytes(m):

    return (f"{FORMAT}\nname {m['name']}\nversion {m['version']}\nmin_app {m['min_app']}\n"
            f"files {files_digest(m['files'])}\n").encode("ascii")

def verify_signature(m):

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = PLUGIN_SIGNING_KEY
    if not key:
        raise Refused(NO_KEY, "this version has no key to check plugins with")
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))
    except (ValueError, TypeError):
        raise Refused(NO_KEY, "this version has no key to check plugins with") from None
    if not m.get("signature"):
        raise Refused(UNSIGNED, "it isn't signed")
    try:
        sig = base64.b64decode(m["signature"], validate=True)
        pub.verify(sig, signed_bytes(m))
    except (ValueError, TypeError, InvalidSignature):
        raise Refused(BAD_SIG, "its signature doesn't match Lumen Aureum's key") from None

def check_fits(m, taken):

    if m["name"] in taken:
        raise Refused(f"A plugin named {m['name']} is already part of Lumen Aureum, so this one wasn't added.",
                      "its name belongs to another plugin")
    have = app_version()
    if VERSION_RE.fullmatch(have or "") and newer(m["min_app"], have):
        raise Refused(f"That plugin needs Lumen Aureum {m['min_app']} or newer, and this studio runs {have}. Update "
                      "first, then add it again.", f"it needs Lumen Aureum {m['min_app']} or newer")

def check_files(m, files):

    want = {e["path"]: e for e in m["files"]}
    if set(files) != set(want):
        raise Refused(MISMATCH, "its files changed since it was added")
    for p, b in files.items():
        if len(b) != want[p]["size"] or hashlib.sha256(b).hexdigest() != want[p]["sha256"]:
            raise Refused(MISMATCH, "its files changed since it was added")

def _zip_name_ok(n):
    if not n or len(n) > 400 or n.startswith("/") or "\\" in n or ":" in n.split("/")[0]:
        return False
    return all(x not in ("", ".", "..") for x in n.split("/"))

def read_zip(data):

    if len(data) > MAX_PACKAGE:
        raise Refused(TOO_BIG)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError):
        raise Refused(NOT_PLUGIN) from None
    if len(infos) > MAX_FILES * 2 + 1:
        raise Refused(TOO_BIG)
    files, total = {}, 0
    for info in infos:
        n = info.filename
        mode = info.external_attr >> 16
        if info.flag_bits & 0x1:
            raise Refused(NOT_PLUGIN)
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
            raise Refused(NOT_SAFE)
        if n.endswith("/"):
            if not _zip_name_ok(n[:-1]):
                raise Refused(NOT_SAFE)
            continue
        if not _zip_name_ok(n) or n in files:
            raise Refused(NOT_SAFE)
        if len(files) >= MAX_FILES + 1 or info.file_size > MAX_FILE:
            raise Refused(TOO_BIG)
        try:
            with zf.open(info) as fh:
                b = fh.read(MAX_FILE + 1)
        except (zipfile.BadZipFile, NotImplementedError, ValueError, OSError, EOFError, RuntimeError):
            raise Refused(NOT_PLUGIN) from None
        except Exception:
            raise Refused(NOT_PLUGIN) from None
        total += len(b)
        if len(b) > MAX_FILE or total > MAX_TOTAL:
            raise Refused(TOO_BIG)
        files[n] = b
    return files.pop("manifest.json", None), files

def verify_package(data, manifest=None, taken=()):

    raw, files = read_zip(data)
    if manifest is None:
        if raw is None:
            raise Refused(NOT_PLUGIN)
        manifest = parse_manifest(raw)
    verify_signature(manifest)
    check_files(manifest, files)
    check_fits(manifest, taken)
    return manifest, files

def _get(url, cap, deadline):

    import calsend
    for _ in range(MAX_REDIRECTS + 1):
        try:
            st, hd, body = calsend.fetch("GET", url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                                         max_body=cap, deadline=deadline, too_big="it was larger than a plugin can be")
        except calsend.SendError as e:
            raise Refused(f"The plugin couldn't be downloaded: {e.words}. Nothing was added.") from None
        except ValueError:
            raise Refused("That plugin link isn't a complete address. Nothing was added.") from None
        if st in (301, 302, 303, 307, 308) and hd.get("location"):
            try:
                url = urllib.parse.urljoin(url, hd["location"])
                https = urllib.parse.urlsplit(url).scheme.lower() == "https"
            except ValueError:
                https = False
            if not https:
                raise Refused("The plugin link sent the studio to an address that isn't https://, so nothing was "
                              "added.")
            continue
        if st != 200:
            raise Refused(f"The plugin couldn't be downloaded: the server answered with an error ({st}). Nothing "
                          "was added.")
        return url, body
    raise Refused("The plugin link sent the studio on to other addresses too many times, so nothing was added.")

def from_link(url, taken=()):

    url = (url or "").strip()
    try:
        https = urllib.parse.urlsplit(url).scheme.lower() == "https"
    except ValueError:
        https = False
    if len(url) > MAX_LINK or not https:
        raise Refused("A plugin link must start with https://. Nothing was added.")
    deadline = time.monotonic() + DEADLINE
    final, raw = _get(url, MAX_MANIFEST, deadline)
    m = parse_manifest(raw)
    verify_signature(m)
    check_fits(m, taken)
    if not m.get("package"):
        raise Refused(NOT_PLUGIN)
    try:
        purl = urllib.parse.urljoin(final, m["package"])
        https = urllib.parse.urlsplit(purl).scheme.lower() == "https"
    except ValueError:
        https = False
    if not https:
        raise Refused("A plugin's package must be at an https:// address, so nothing was added.")
    _final, data = _get(purl, MAX_PACKAGE, deadline)
    m, files = verify_package(data, manifest=m, taken=taken)
    return m, data, files

def home():

    import db
    return Path(db.DB_PATH).parent / "plugins"

def manifest_text(m):
    return json.dumps(m, sort_keys=True, indent=1) + "\n"

def record(m, data, source):

    from datetime import datetime
    return json.dumps(dict(manifest=m, package=base64.b64encode(data).decode("ascii"),
                           added=datetime.now().isoformat(timespec="seconds"), source=source))

def stored(con):

    out = {}
    for k, v in con.execute("SELECT key, value FROM settings WHERE key LIKE 'plugin\\_package\\_%' ESCAPE '\\'"):
        name = k[len(SETTING):]
        try:
            r = json.loads(v)
            out[name] = dict(manifest=r["manifest"], package=base64.b64decode(r["package"]), added=r.get("added", ""),
                             source=r.get("source", ""))
        except (ValueError, KeyError, TypeError):
            out[name] = dict(manifest=None, package=b"", added="", source="")
    return out

def _gone(p):

    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.exists():
        shutil.rmtree(p)

def write_folder(m, files):

    root = home()
    root.mkdir(mode=0o700, exist_ok=True)
    name = m["name"]
    tmp = root / f".new-{name}-{secrets.token_hex(4)}"
    tmp.mkdir(mode=0o700)
    try:
        for p, b in sorted(files.items()):
            t = tmp / p
            t.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(t, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(b)
        (tmp / "manifest.json").write_text(manifest_text(m))
        dest = root / name
        if is_on(name):
            (tmp / MARK).touch(mode=0o600)
        old = None
        if dest.exists() or dest.is_symlink():
            old = root / f".old-{name}-{secrets.token_hex(4)}"
            os.replace(dest, old)
        os.replace(tmp, dest)
        if old:
            _gone(old)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

def remove_folder(name, aside=None):

    p = home() / name
    if not (p.exists() or p.is_symlink()):
        return
    if aside is not None:
        dest = Path(aside) / "plugins"
        dest.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(p, dest / name)
        return
    old = home() / f".old-{name}-{secrets.token_hex(4)}"
    os.replace(p, old)
    _gone(old)

def is_on(name):

    try:
        return stat.S_ISREG(os.lstat(home() / name / MARK).st_mode)
    except OSError:
        return False

def set_on(name, on):

    folder = home() / name
    if folder.is_symlink() or not folder.is_dir() or is_on(name) == bool(on):
        return False
    p = folder / MARK
    if p.is_symlink() or p.exists():
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    if on:
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600))
    return True

def _read_capped(path, cap):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        b = fh.read(cap + 1)
    if len(b) > cap:
        raise Refused(TOO_BIG, "it is larger than a plugin can be")
    return b

def check_folder(name, taken=()):

    changed = Refused(MISMATCH, "its files changed since it was added")
    folder = home() / name
    if not NAME_RE.fullmatch(name) or folder.is_symlink() or not folder.is_dir():
        raise changed
    try:
        m = parse_manifest(_read_capped(folder / "manifest.json", MAX_MANIFEST))
    except (OSError, Refused):
        raise changed from None
    if m["name"] != name:
        raise changed
    verify_signature(m)
    found = set()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for n in dirnames + filenames:
            st_ = os.lstat(os.path.join(dirpath, n))
            if not (stat.S_ISDIR(st_.st_mode) or stat.S_ISREG(st_.st_mode)):
                raise changed
        for n in filenames:
            found.add(os.path.relpath(os.path.join(dirpath, n), folder).replace(os.sep, "/"))
        if len(found) > MAX_FILES + 1:
            raise changed
    want = {e["path"] for e in m["files"]}
    if found - {MARK} != want | {"manifest.json"}:
        raise changed
    files = {}
    try:
        for e in m["files"]:
            files[e["path"]] = _read_capped(folder / e["path"], MAX_FILE)
    except OSError:
        raise changed from None
    check_files(m, files)
    check_fits(m, taken)
    return m, files

def on_disk(taken):

    root = home()
    good, bad = [], []
    if not root.is_dir():
        return good, bad
    for entry in sorted(os.listdir(root)):
        if entry.startswith("."):
            try:
                _gone(root / entry)
            except OSError:
                pass
            continue
        try:
            m, files = check_folder(entry, taken)
        except Refused as e:
            bad.append((entry, e.short))
            continue
        good.append(dict(name=entry, version=m["version"], folder=str(root / entry), code=files[f"{entry}.py"],
                         on=is_on(entry)))
    return good, bad

def sync(con, aside=None):

    want = stored(con)
    changed = False
    for name, r in sorted(want.items()):
        try:
            m = parse_manifest(json.dumps(r["manifest"]).encode())
            if m["name"] != name:
                continue
            m, files = verify_package(r["package"], manifest=m)
        except (Refused, TypeError, ValueError):
            continue
        try:
            have, _f = check_folder(name)
            if files_digest(have["files"]) == files_digest(m["files"]) and have["version"] == m["version"]:
                continue
        except Refused:
            pass
        write_folder(m, files)
        changed = True
    for name in sorted(want):
        r_ = con.execute("SELECT value FROM settings WHERE key=?", (f"plugin_on_{name}",)).fetchone()
        if (home() / name).is_dir() and set_on(name, bool(r_) and r_[0] == "1"):
            changed = True
    root = home()
    if root.is_dir():
        for entry in sorted(os.listdir(root)):
            if not entry.startswith(".") and entry not in want:
                remove_folder(entry, aside)
                changed = True
    return changed

def after_restore(key, aside=None):

    import db as D
    if aside is not None and home().is_dir():
        os.replace(home(), Path(aside) / "plugins")
    con = D.connect(D.DB_PATH, key=key)
    try:
        sync(con)
    finally:
        con.close()

def restart_soon():

    if "gunicorn" not in sys.modules or os.environ.get("CRM_TESTING"):
        return False
    ppid = os.getppid()
    try:
        cmd = Path(f"/proc/{ppid}/cmdline").read_bytes()
    except OSError:
        return False
    if ppid <= 0 or b"gunicorn" not in cmd:
        return False

    def later():
        time.sleep(1.5)
        try:
            os.kill(ppid, signal.SIGHUP)
        except OSError:
            pass
    threading.Thread(target=later, name="plugin-restart", daemon=True).start()
    return True
