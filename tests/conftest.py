import os
import sys
import time

import pytest

os.environ["CRM_TESTING"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flask
from helpers import HttpsClient

flask.Flask.test_client_class = HttpsClient

@pytest.fixture(autouse=True)
def _environment_restored():

    saved = dict(os.environ)
    yield
    if dict(os.environ) != saved:
        os.environ.clear()
        os.environ.update(saved)
    time.tzset()

@pytest.fixture(autouse=True)
def _no_job_outlives_its_test():

    yield
    m = sys.modules.get("jobs")
    if m is not None:
        with m._LOCK:
            m.JOBS.clear()

@pytest.fixture(autouse=True)
def _no_write_waits_on_its_own_thread():

    getattr(sys.modules.get("db"), "SELF_WAITS", []).clear()
    yield
    m = sys.modules.get("db")
    waits = list(getattr(m, "SELF_WAITS", None) or ())
    if waits:
        m.SELF_WAITS.clear()
        pytest.fail("a write waited on another connection of its own thread (it can never succeed):\n" + waits[0],
                    pytrace=False)
