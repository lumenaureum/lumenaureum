import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, one

E = {"REMOTE_ADDR": "127.0.0.1"}
NONE = {"Sec-Fetch-Site": None}
WRITING = ["/client/1/export.json", "/settings/export.json", "/money/accounts.zip"]

def _audit(A):
    return one(A, "SELECT COUNT(*) FROM audit")[0]

def test_a_save_that_says_nothing_about_where_it_came_from_is_refused(env):
    A, c = env
    n = _audit(A)
    r = c.post("/client/1", data={"name": "Changed"}, headers=NONE, environ_base=E)
    assert r.status_code == 403
    assert one(A, "SELECT name FROM households WHERE id=1")[0] == "Lane" and _audit(A) == n
    assert c.post("/client/1", data={"name": "Changed"}, environ_base=E).status_code != 403

@pytest.mark.parametrize("url", WRITING)
def test_a_writing_download_with_no_provenance_is_refused_and_writes_nothing(env, url):
    A, c = env
    n = _audit(A)
    assert c.get(url, headers=NONE, environ_base=E).status_code == 403
    assert _audit(A) == n

@pytest.mark.parametrize("url", WRITING)
def test_an_older_browser_link_with_a_same_site_referer_still_downloads(env, url):
    A, c = env
    r = c.get(url, headers={"Referer": "https://localhost/money"}, environ_base=E)
    assert r.status_code == 200

def test_an_older_browser_save_with_a_same_site_origin_still_saves(env):
    A, c = env
    r = c.post("/settings", data={"business_name": "Bright Studio"}, headers={"Origin": "https://localhost"},
               environ_base=E)
    assert r.status_code != 403

@pytest.mark.parametrize("hdrs", [{"Origin": "https://evil.example"}, {"Referer": "https://evil.example/x"},
                                  {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
def test_another_site_is_still_refused(env, hdrs):
    A, c = env
    assert c.post("/settings", data={"business_name": "x"}, headers=hdrs, environ_base=E).status_code == 403
    assert c.get("/client/1/export.json", headers=hdrs, environ_base=E).status_code == 403

def test_plain_pages_sign_in_and_the_health_check_need_no_provenance(env):
    A, c = env
    for url in ("/", "/client/1", "/money"):
        assert c.get(url, headers=NONE, environ_base=E).status_code == 200, url
    fresh = A.app.test_client()
    assert fresh.get("/login", headers=NONE, environ_base=E).status_code == 200
    assert fresh.get("/healthz", headers=NONE, environ_base=E).status_code == 200

def test_the_test_client_speaks_like_a_same_site_page_unless_told_otherwise(env):
    from helpers import _like_a_browser
    assert _like_a_browser(None)["Sec-Fetch-Site"] == "same-origin"
    kept = _like_a_browser({"Referer": "https://evil.example/"})
    assert "Sec-Fetch-Site" not in kept and kept["Referer"] == "https://evil.example/"
    assert "Sec-Fetch-Site" not in _like_a_browser(NONE)
    assert "Sec-Fetch-Site" not in _like_a_browser(None, {"HTTP_ORIGIN": "https://localhost"})
