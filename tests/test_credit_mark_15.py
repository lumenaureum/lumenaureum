import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

def _ignored_tops():
    return {l.strip().strip("/") for l in (ROOT / ".dockerignore").read_text().splitlines()
            if l.strip() and not l.startswith("#") and "*" not in l}

def test_the_mark_lives_where_the_image_ships_it():
    mark = ROOT / "static" / "brand" / "mark-small.svg"
    assert mark.is_file()
    assert "static" not in _ignored_tops() and "brand" in _ignored_tops()

def test_studio_page_and_gallery_credits_carry_the_mark():
    import studio_page
    import gallery
    for html in (studio_page.CREDIT_HTML, gallery.CREDIT_HTML):
        assert "<svg" in html and 'aria-hidden="true"' in html and "Lumen Aureum" in html

def test_no_plugin_reads_from_brand_at_run_time():
    for p in (ROOT / "plugins").glob("*.py"):
        assert '/ "brand" /' not in p.read_text().replace('/ "static" / "brand" /', ""), p.name
    assert '/ "brand" /' not in (ROOT / "app.py").read_text().replace('/ "static" / "brand" /', "")
