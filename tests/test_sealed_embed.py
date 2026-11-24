import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import sync_sealed

def test_each_copy_is_the_source_byte_for_byte():
    src = sync_sealed.block()
    for p in sync_sealed.COPIES:
        text = p.read_text()
        assert text.count(sync_sealed.BEGIN) == 1 and text.count(sync_sealed.END) == 1, p
        assert sync_sealed.embedded(text) == src, f"{p.name} carries an old copy of sealed.js: run tools/sync_sealed.py"

def test_the_check_mode_agrees():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_sealed.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

def test_the_check_catches_a_changed_copy(tmp_path, monkeypatch):
    v = tmp_path / "viewer.html"
    v.write_text(sync_sealed.COPIES[0].read_text().replace("const LAS = (() => {", "const LAS = (() => { /* changed */", 1))
    monkeypatch.setattr(sync_sealed, "COPIES", (v,))
    assert sync_sealed.main(["--check"]) == 1
    assert sync_sealed.main([]) == 0 and sync_sealed.embedded(v.read_text()) == sync_sealed.block()

def test_the_page_uses_the_library_not_its_own_sealing():
    viewer = (ROOT / "plugins" / "gallery_assets" / "viewer.html").read_text()
    inquiry = (ROOT / "plugins" / "studio_assets" / "inquiry.js").read_text()
    outside = viewer.replace(sync_sealed.embedded(viewer), "")
    assert "LAS.sealLink(" in outside and "LAS.openLink(" in outside
    assert 'sentOn("picks.las")' in outside and "gate.php?f=draft.las" in outside
    assert 'sentOn(mine("picks"))' in outside and 'mine("draft")' in outside
    assert "deriveBits" not in inquiry.replace(sync_sealed.embedded(inquiry), "")
    assert "LAS.sealStudio(\"inquiry\"" in inquiry
