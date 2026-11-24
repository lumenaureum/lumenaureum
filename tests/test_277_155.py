import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules

OPS = (ROOT / "docs" / "operations.md").read_text()

def _section(text, heading):
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## )", text, re.S | re.M)
    assert m, heading
    return m.group(1)

PROXY = _section(OPS, "Behind a reverse proxy")

def _gate(monkeypatch, tmp_path, trusted):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", trusted)
    fresh_modules()
    import netsec
    seen = {}

    def inner(environ, start_response):
        seen.update(environ)
        start_response("200 OK", [])
        return [b"ok"]

    gate = netsec.ProxyGate(inner)

    def call(environ):
        seen.clear()
        out = {}
        b"".join(gate(dict(environ), lambda s, h: out.setdefault("s", s)))
        return out["s"], dict(seen)
    return call

@pytest.fixture(autouse=True)
def _clean():
    yield
    fresh_modules()

def test_replaced_header_gives_the_visitor(monkeypatch, tmp_path):
    call = _gate(monkeypatch, tmp_path, "10.0.0.5")
    s, seen = call({"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "192.168.1.7",
                    "HTTP_X_FORWARDED_PROTO": "https", "wsgi.url_scheme": "http"})
    assert s.startswith("200") and seen["REMOTE_ADDR"] == "192.168.1.7" and seen["wsgi.url_scheme"] == "https"

def test_appending_is_safe_only_with_the_proxy_alone_listed(monkeypatch, tmp_path):
    env = {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "203.0.113.9, 192.168.1.7"}
    _, seen = _gate(monkeypatch, tmp_path, "10.0.0.5")(env)
    assert seen["REMOTE_ADDR"] == "192.168.1.7"
    _, seen = _gate(monkeypatch, tmp_path, "10.0.0.5,192.168.1.0/24")(env)
    assert seen["REMOTE_ADDR"] == "203.0.113.9"

def test_all_listed_takes_the_first(monkeypatch, tmp_path):
    call = _gate(monkeypatch, tmp_path, "10.0.0.5")
    _, seen = call({"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "203.0.113.9, 10.0.0.5"})
    assert seen["REMOTE_ADDR"] == "203.0.113.9"

@pytest.mark.parametrize("proto", ["https, http", "http, https", "https,https"])
def test_proto_with_more_than_one_entry_is_not_believed(monkeypatch, tmp_path, proto):
    call = _gate(monkeypatch, tmp_path, "10.0.0.5")
    _, seen = call({"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_PROTO": proto, "wsgi.url_scheme": "http"})
    assert seen["wsgi.url_scheme"] == "http"

def test_the_curl_check(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_TRUSTED_PROXIES", "10.9.9.9")
    monkeypatch.delenv("CRM_ALLOW_ONLY", raising=False)
    fresh_modules()
    import app as A
    c = A.app.test_client()
    proxy = {"REMOTE_ADDR": "10.9.9.9"}
    passed_on = c.get("/healthz", environ_base=proxy, headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
    assert passed_on.status_code == 403
    replaced = c.get("/healthz", environ_base=proxy, headers={"X-Forwarded-For": "192.168.1.9", "X-Forwarded-Proto": "https"})
    assert replaced.status_code == 200 and replaced.get_json()["ok"] is True

def test_section_says_replace_and_names_both_headers():
    assert "**replace**" in PROXY and "X-Forwarded-For" in PROXY and "X-Forwarded-Proto" in PROXY
    assert "own address only" in PROXY

def test_nginx_example_replaces():
    block = PROXY.split("```nginx", 1)[1].split("```", 1)[0]
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in block
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in block
    assert "proxy_add_x_forwarded_for" not in block

def test_caddy_and_traefik_covered():
    block = PROXY.split("```caddy", 1)[1].split("```", 1)[0]
    assert "header_up X-Forwarded-For {remote_host}" in block and "header_up X-Forwarded-Proto {scheme}" in block
    assert "forwardedHeaders.insecure" in PROXY and "forwardedHeaders.trustedIPs" in PROXY

def test_no_doc_says_append_and_links_resolve():
    where = (ROOT / "docs" / "where-it-runs.md").read_text()
    sec = (ROOT / "docs" / "security.md").read_text()
    assert "must append" not in where
    for d in (where, sec):
        assert "operations.md#behind-a-reverse-proxy" in d
    assert "\n## Behind a reverse proxy\n" in OPS

def test_plain_style():
    assert "—" not in PROXY and "behaviour" not in PROXY
