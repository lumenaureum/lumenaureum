import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMAT_DOCS = {"docs/sealed-format.md", "docs/photo-format.md"}
LATIN = re.compile(r"Perpetuum|Sigillum")

def test_the_latin_names_are_in_the_format_docs_once_each():
    sealed_doc = (ROOT / "docs" / "sealed-format.md").read_text()
    photo_doc = (ROOT / "docs" / "photo-format.md").read_text()
    assert "LAP = Lumen Aureum Perpetuum" in sealed_doc and "LAS = Lumen Aureum Sigillum" in sealed_doc
    assert "Link files have no formal name" in sealed_doc
    assert "LAP = Lumen Aureum Perpetuum" in photo_doc

    assert sealed_doc.index("## The three formats, in plain words") < sealed_doc.index("Lumen Aureum Perpetuum")

def test_never_in_the_app_or_the_photographers_docs():
    bad = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if not p.is_file() or rel.startswith("tests/") or (rel.startswith(".") and not rel.startswith(".github/")) or rel in FORMAT_DOCS:
            continue
        if p.suffix not in (".py", ".html", ".js", ".php", ".md", ".css", ".txt", ".json", ".svg"):
            continue
        if LATIN.search(p.read_text(errors="replace")):
            bad.append(rel)
    assert bad == []
