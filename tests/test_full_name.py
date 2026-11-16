import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARE = re.compile(r"\blumen(?![ _%-]?(?:20)?aureum)(?!_HOST_IP\b)", re.I)

def test_no_bare_lumen_in_shipped_files():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        files = r.stdout.split()
    else:
        files = [str(f.relative_to(ROOT)) for f in ROOT.rglob("*") if f.is_file() and ".git" not in f.parts
                 and "__pycache__" not in f.parts and ".pytest_cache" not in f.parts]
    hits = []
    for f in files:
        if f.startswith("tests/fixtures/") or f == "tests/test_full_name.py":
            continue
        p = ROOT / f
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if BARE.search(line):
                hits.append(f"{f}:{n}: {line.strip()[:100]}")
    assert not hits, "say lumenaureum in full:\n" + "\n".join(hits)

def test_no_code_depends_on_an_html_comment():

    hits = []
    for f in (ROOT / "plugins").rglob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"""replace\(\s*["']<!--""", line):
                hits.append(f"{f.relative_to(ROOT)}:{n}")
    for f in ROOT.glob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"""replace\(\s*["']<!--""", line):
                hits.append(f"{f.relative_to(ROOT)}:{n}")
    assert not hits, "placeholders written as HTML comments: " + ", ".join(hits)

def test_the_gone_dark_mark_survives_comment_stripping():
    page = (ROOT / "plugins" / "gallery_assets" / "viewer.html").read_text()
    stripped = re.sub(r"<!--(?!\[if).*?-->", "", page, flags=re.S)
    assert "<span hidden data-la-logo></span>" in stripped
