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
