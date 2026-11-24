import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = re.compile(r"links[\s\"'+]*hosts?\b", re.I)
SKIP_DIRS = {"tests/fixtures", "tests/compat", "node_modules", "__pycache__"}
SUFFIXES = {".py", ".html", ".js", ".css", ".php", ".md", ".txt", ".yaml", ".yml", ".json", ".htaccess"}

def _files():
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if not p.is_file() or (rel.startswith(".") and not rel.startswith(".github/")) or any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if p.name == Path(__file__).name:
            continue
        if p.suffix in SUFFIXES or p.name == ".htaccess":
            yield rel, p

def test_no_links_host_anywhere():
    bad = []
    for rel, p in _files():
        text = p.read_text(errors="replace")
        if rel == "CHANGELOG.md":
            text = text.split("\n## ", 2)[1] if "\n## " in text else text
        for m in OLD.finditer(text):
            bad.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}")
    assert not bad, "say \"web host\":\n" + "\n".join(bad)

def test_no_gallery_host_as_a_name():

    bad = []
    for rel, p in _files():
        if rel in ("wording.py", "wording_legacy.py", "CHANGELOG.md"):
            continue
        text = p.read_text(errors="replace")
        for m in re.finditer(r"gallery[\s\"'+]*host\b", text, re.I):
            bad.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}")
    assert not bad, "say \"web host\":\n" + "\n".join(bad)

def test_setup_names_the_web_host():
    page = (ROOT / "templates" / "setup.html").read_text()
    assert "Where are your galleries hosted?" in page
    assert "Your web host is where your galleries, client links and studio page are published." in page
    assert "Choosing a web host" in (ROOT / "docs" / "where-it-runs.md").read_text()
