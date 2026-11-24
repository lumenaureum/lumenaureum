import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers_tools import need_tool

SCRIPTS = [ROOT / "plugins" / "gallery_assets" / n for n in ("gate.php", "picks.php", "seen.php", "orders.php")] + \
          [ROOT / "plugins" / "studio_assets" / "inquiry.php"]
SEALED = ROOT / "plugins" / "sealed_assets" / "sealed.php"

PINNED = {"1.4.0": "47418e23c3ed3b83b83655caa7e850ebaedd3d0ed8569397d6c288af07ef7a9d",
          "1.5.0": "341d40ad949622e658f925078fdc52325703dd5df303c717518bc57ecdea7cca",
          "1.6.0": "db26e5a8debf2050797e96be37506350bf4f447ab63020d3f340f18875b17f06"}

def _set_version():
    src = (ROOT / "plugins" / "gallery.py").read_text()
    return re.search(r'^HOST_SCRIPTS_SET = "([^"]+)"', src, re.M).group(1)

def _set_hash():
    h = hashlib.sha256()
    files = SCRIPTS + [SEALED, ROOT / "plugins" / "gallery_assets" / "htaccess"]
    for p in sorted(files, key=lambda p_: p_.name):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()

def test_every_script_says_its_set_first():
    want = f"header('X-Host-Scripts: {_set_version()}');"
    for p in SCRIPTS:
        first = re.search(r"^(?!\s*//)\s*(header\(.*?\);)", p.read_text(), re.M)
        assert first and first.group(1) == want, p.name

def test_the_app_and_the_host_check_agree_on_the_set():
    import hostcheck
    assert hostcheck.SCRIPT_SET == _set_version()

def test_the_set_version_moves_with_the_scripts():
    v = _set_version()
    assert v in PINNED, f"no pinned hash for the script set {v}"
    assert _set_hash() == PINNED[v], (f"a host script changed: give the set a new version ({v} -> next) and pin its hash "
                                      "(while that version is unreleased, update the pin)")

def test_scripts_keep_to_php_74_syntax():
    for p in SCRIPTS + [SEALED]:
        src = p.read_text()
        code = "\n".join(l_.split("//", 1)[0] for l_ in src.splitlines())
        for pat in (r"\bfn\s*\(", r"\bmatch\s*\(", r"str_starts_with|str_ends_with|str_contains", r"\?->", r"#\[",
                    r"^\s*(public|private|protected)\s+(int|string|bool|array|float|\?)", r"\benum\s+\w+"):
            assert not re.search(pat, code, re.M), (p.name, pat)
    assert not SEALED.read_text().rstrip().endswith("?>")

def test_scripts_parse_and_sealed_php_prints_nothing():
    php = need_tool("php")
    for p in SCRIPTS + [SEALED]:
        r = subprocess.run([php, "-l", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, (p.name, r.stdout, r.stderr)
    r = subprocess.run([php, str(SEALED)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

def test_the_gallery_htaccess_keeps_the_sealed_files_and_orders_away():
    ht = (ROOT / "plugins" / "gallery_assets" / "htaccess").read_text()
    deny = re.search(r'<FilesMatch "([^"]+)">\s*Require all denied', ht).group(1)
    for name in ("picks.las", "draft.las", "picks.las.abc.tmp", "sealed.php", "picks.bin", "draft.bin", "limits.txt"):
        assert re.match(deny, name), name
    for name in ("index.html", "manifest.bin", "gate.php", "picks.php"):
        assert not re.match(deny, name), name
    assert re.search(r"^RewriteRule \^orders\(/\|\$\) - \[F,L\]$", ht, re.M)
