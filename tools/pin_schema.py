import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(CRM_TESTING="1", CRM_DB=os.path.join(tmp, "crm.db"),
                          CRM_STORAGE_CFG=os.path.join(tmp, "storage.json"), CRM_PHOTOS_ROOT=os.path.join(tmp, "photos"))
        os.environ.pop("CRM_KEYRING", None)
        sys.path[:0] = [str(REPO), str(REPO / "tests")]
        import flask
        from helpers import HttpsClient
        flask.Flask.test_client_class = HttpsClient
        import schema_pin
        version, text = schema_pin.fresh_install_schema()
        out = schema_pin.pinned_path(version)
        out.write_text(schema_pin.render(version, text))
        print("wrote", out)

if __name__ == "__main__":
    main()
