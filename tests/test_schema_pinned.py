import difflib

import schema_pin

def test_fresh_install_matches_the_pinned_schema(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos")):
        monkeypatch.setenv(k, str(tmp_path / v))
    version, text = schema_pin.fresh_install_schema()
    path = schema_pin.pinned_path(version)
    how = ("The schema changed. If intended: add a numbered step if it rewrites anything, bump SCHEMA_VERSION, "
           f"regenerate with {schema_pin.REGENERATE}.")
    assert path.exists(), f"{how} (No pinned schema for version {version}: {path.name} is missing.)"
    want = schema_pin.render(version, text)
    have = path.read_text()
    if have != want:
        diff = "\n".join(difflib.unified_diff(have.splitlines(), want.splitlines(), path.name, "fresh install", lineterm=""))
        raise AssertionError(f"{how}\n{diff}")
