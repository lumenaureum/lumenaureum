import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def test_every_released_version_has_a_fixture():
    current = (ROOT / "VERSION").read_text().strip()
    released = re.findall(r"^## (\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert released, "CHANGELOG.md lists no released version"
    missing = [v for v in released if v != current and not (ROOT / "tests" / "fixtures" / f"db-{v}" / "crm.db").exists()]
    assert not missing, (
        f"No database fixture for released version(s) {', '.join(missing)}. Before releasing, save one made by that "
        "version's own code (tools/make_fixture.py --version <v>: add the version's scenario there; see "
        "tests/fixtures/db-1.2.0/README.md), and add an upgrade test like tests/test_upgrade_from_120.py.")
