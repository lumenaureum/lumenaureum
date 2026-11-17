import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPAT = REPO / "tests" / "compat"
PAGES = {"viewer.html": "plugins/gallery_assets/viewer.html", "inquiry.js": "plugins/studio_assets/inquiry.js"}
SCRIPTS = {"gate.php": "plugins/gallery_assets/gate.php", "picks.php": "plugins/gallery_assets/picks.php",
           "seen.php": "plugins/gallery_assets/seen.php", "htaccess": "plugins/gallery_assets/htaccess",
           "inquiry.php": "plugins/studio_assets/inquiry.php", "sealed.php": "plugins/sealed_assets/sealed.php"}

def _read(path, rev):
    if not rev:
        p = REPO / path
        return p.read_bytes() if p.is_file() else None
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None

def _key(v):
    return tuple(int(x) for x in v.split("."))

def resolve(kind, version):

    d = COMPAT / kind / version
    seen = set()
    while (d / "same-as").is_file():
        v = (d / "same-as").read_text().strip()
        if v in seen:
            raise SystemExit(f"same-as loop at {d}")
        seen.add(v)
        d = COMPAT / kind / v
    return d

def kept(kind):
    d = COMPAT / kind
    return sorted((p.name for p in d.iterdir() if p.is_dir()), key=_key) if d.is_dir() else []

def snapshot(kind, files, version, rev):
    got = {n: _read(p, rev) for n, p in files.items()}
    got = {n: b for n, b in got.items() if b is not None}
    out = COMPAT / kind / version
    older = [v for v in kept(kind) if v != version and _key(v) < _key(version)]
    if older:
        prev = resolve(kind, older[-1])
        same = all((prev / n).is_file() and (prev / n).read_bytes() == b for n, b in got.items()) and \
            {p.name for p in prev.iterdir() if p.is_file()} == set(got)
        if same:
            out.mkdir(parents=True, exist_ok=True)
            for p in out.iterdir():
                p.unlink()
            (out / "same-as").write_text(older[-1] + "\n")
            return f"{kind}/{version}: same as {older[-1]}"
    out.mkdir(parents=True, exist_ok=True)
    for p in out.iterdir():
        p.unlink()
    for n, b in got.items():
        (out / n).write_bytes(b)
    return f"{kind}/{version}: {len(got)} files"

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--git", default="", help="read the files from this git revision instead of the working tree")
    a = ap.parse_args(argv)
    print(snapshot("pages", PAGES, a.version, a.git))
    print(snapshot("scripts", SCRIPTS, a.version, a.git))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
