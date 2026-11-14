import os
import sys

os.environ["CRM_TESTING"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flask
from helpers import HttpsClient

flask.Flask.test_client_class = HttpsClient
