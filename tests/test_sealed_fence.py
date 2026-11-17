import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAGIC = (b"LAK" + b"1", b"LAQ" + b"1")
SKIP_DIRS = {"tests", "docs"}
EXEMPT = {"legacy_130.py", "upgrade_140.py", "CHANGELOG.md", "tools/make_fixture.py", "tools/make_fixture_100.py"}

def _files():

    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True)
    names = r.stdout.decode().split("\0") if r.returncode == 0 else \
        [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
         and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.parts[len(ROOT.parts)] != "data"]
    for rel in sorted(set(n for n in names if n)):
        p = ROOT / rel
        if not p.is_file() or rel.split("/", 1)[0] in SKIP_DIRS:
            continue
        yield rel.replace("\\", "/"), p

def test_only_the_upgrade_imports_the_legacy_reader():
    bad = []
    pat = re.compile(rb"^\s*(import\s+legacy_130|from\s+legacy_130\s+import)\b|\blegacy_130\b\s*=\s*__import__|importlib\.import_module\(\s*['\"]legacy_130",
                     re.M)
    for rel, p in _files():
        if not rel.endswith(".py") or rel == "upgrade_140.py":
            continue
        if pat.search(p.read_bytes()):
            bad.append(rel)
    assert bad == [], f"only upgrade_140.py may import legacy_130: {bad}"
    assert re.search(rb"^import legacy_130$", (ROOT / "upgrade_140.py").read_bytes(), re.M)

def test_no_other_file_names_the_old_forms():
    bad = []
    for rel, p in _files():
        if rel in EXEMPT or p.suffix.lower() in (".png", ".jpg", ".woff2", ".gz", ".ico", ".db", ".svg"):
            continue
        data = p.read_bytes()
        if any(m in data for m in MAGIC):
            bad.append(rel)
    assert bad == [], f"files outside the fence name the old sealed forms: {bad}"

def test_the_runtime_code_never_reads_or_writes_a_bin_sealed_item():

    allowed = {"upgrade_140.py", "legacy_130.py", "plugins/gallery.py", "plugins/gallery_assets/htaccess",
               "plugins/studio_page.py", "tools/make_fixture.py", "tools/make_fixture_100.py", "CHANGELOG.md"}
    bad = []
    for rel, p in _files():
        if rel in allowed or p.suffix.lower() not in (".py", ".php", ".js", ".html", ""):
            continue
        if re.search(rb"(picks|draft)\.bin", p.read_bytes()):
            bad.append(rel)
    assert bad == [], bad
    g = (ROOT / "plugins" / "gallery.py").read_text()

    lines = [l_ for l_ in g.splitlines() if re.search(r"(picks|draft)\.bin", l_) and not l_.lstrip().startswith("#")]
    assert all("HOST_KEEPS" in l_ or l_.strip().startswith('"picks.bin"') for l_ in lines), lines
