"""Every test run (and the app subprocesses tests start) is marked as a test: no background jobs, no real backups.
The app answers only HTTPS, so the test client speaks it (helpers.HttpsClient); test_netsec.py checks that plain HTTP
is refused."""
import os
import sys

os.environ["CRM_TESTING"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flask  # noqa: E402
from helpers import HttpsClient  # noqa: E402

flask.Flask.test_client_class = HttpsClient
