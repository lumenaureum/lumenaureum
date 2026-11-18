import hashlib
import io
import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_s3 import ACCESS, SECRET, FakeS3, make_cert
from vault import s3 as S

EMPTY = S.EMPTY_SHA

@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(S, "SLEEP", lambda s: None)
    monkeypatch.setattr(S, "NOTE", None)

@pytest.fixture
def srv():
    f = FakeS3()
    yield f
    f.close()

def client(f, **kw):
    return S.Client("test-bucket", ACCESS, SECRET, endpoint=f.endpoint, region="us-east-1", **kw)

SUITE = ("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
SUITE_H = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}

@pytest.mark.parametrize("name,method,query,sig", [
    ("get-vanilla", "GET", [], "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"),
    ("get-vanilla-query-order-key-case", "GET", [("Param2", "value2"), ("Param1", "value1")],
     "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"),
    ("post-vanilla", "POST", [], "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"),
])
def test_sigv4_test_suite(name, method, query, sig):
    a = S.sign(method, "/", query, SUITE_H, EMPTY, *SUITE, "us-east-1", "service")
    assert a == ("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                 f"SignedHeaders=host;x-amz-date, Signature={sig}")

S3K = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
S3H = {"Host": "examplebucket.s3.amazonaws.com", "x-amz-date": "20130524T000000Z"}

def test_s3_example_get_object():
    h = dict(S3H, Range="bytes=0-9", **{"x-amz-content-sha256": EMPTY})
    assert S.sign("GET", "/test.txt", [], h, EMPTY, *S3K, "us-east-1").endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")

def test_s3_example_put_object():
    ph = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
    h = dict(S3H, Date="Fri, 24 May 2013 00:00:00 GMT", **{"x-amz-content-sha256": ph,
                                                           "x-amz-storage-class": "REDUCED_REDUNDANCY"})
    assert S.sign("PUT", "/test%24file.text", [], h, ph, *S3K, "us-east-1").endswith(
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")

def test_s3_example_get_lifecycle_and_list():
    h = dict(S3H, **{"x-amz-content-sha256": EMPTY})
    assert S.sign("GET", "/", [("lifecycle", "")], h, EMPTY, *S3K, "us-east-1").endswith(
        "Signature=fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543")
    assert S.sign("GET", "/", [("max-keys", "2"), ("prefix", "J")], h, EMPTY, *S3K, "us-east-1").endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")

def test_round_trip_and_metadata(srv):
    c = client(srv)
    data = os.urandom(70_000)
    assert c.upload("a/b c+d.age", io.BytesIO(data), content_type="application/octet-stream", cache_control="no-cache") == len(data)
    assert c.head("a/b c+d.age") == len(data) and c.head("absent") is None
    h = c.head_headers("a/b c+d.age")
    assert h["cache-control"] == "no-cache" and h["content-type"] == "application/octet-stream"
    out = io.BytesIO()
    assert c.download("a/b c+d.age", out) == len(data) and out.getvalue() == data
    c.delete("a/b c+d.age")
    c.delete("a/b c+d.age")
    assert c.head("a/b c+d.age") is None and c.list() == []

def test_wrong_secret_is_refused_in_plain_words(srv):
    c = S.Client("test-bucket", ACCESS, "not-the-secret", endpoint=srv.endpoint)
    with pytest.raises(S.S3Error) as e:
        c.put("x", b"1")
    assert e.value.code == "SignatureDoesNotMatch" and str(e.value) == "The secret key doesn't match the access key."
    with pytest.raises(S.S3Error) as e:
        S.Client("no-such-bucket", ACCESS, SECRET, endpoint=srv.endpoint).put("x", b"1")
    assert str(e.value) == "There is no bucket by that name at this address."

def test_multipart_streams_one_part_at_a_time(srv, monkeypatch):
    monkeypatch.setattr(S, "PART", 5 * 1024 * 1024)
    data = os.urandom(12 * 1024 * 1024 + 5)

    class Reader:
        biggest = 0

        def __init__(self):
            self.f = io.BytesIO(data)

        def read(self, n=-1):
            Reader.biggest = max(Reader.biggest, n)
            return self.f.read(n)

    c = client(srv)
    c.upload("big.age", Reader())
    assert Reader.biggest <= S.PART
    parts = [q for m, b, k, q, h in srv.log if m == "PUT" and "partNumber" in q]
    assert [q["partNumber"] for q in parts] == ["1", "2", "3"]
    assert srv.objects["test-bucket"]["big.age"][0] == data and not srv.uploads

def test_a_failed_multipart_is_aborted(srv, monkeypatch):
    monkeypatch.setattr(S, "PART", 5 * 1024 * 1024)

    class Breaks:
        n = 0

        def read(self, n=-1):
            Breaks.n += 1
            if Breaks.n > 2:
                raise IOError("the disk went away")
            return b"x" * n

    with pytest.raises(IOError):
        client(srv).upload("half.age", Breaks())
    assert not srv.uploads
    assert "half.age" not in srv.objects["test-bucket"]
    assert any(m == "DELETE" and "uploadId" in q for m, b, k, q, h in srv.log)

def test_list_follows_continuation_tokens():
    f = FakeS3(page=3)
    try:
        c = client(f)
        for i in range(10):
            c.put(f"p/{i:02}.age", b"x")
        c.put("other/z.age", b"x")
        assert c.list("p/") == [f"p/{i:02}.age" for i in range(10)]
        assert sum(1 for m, b, k, q, h in f.log if m == "GET" and q.get("list-type") == "2") == 4
        assert len(c.list()) == 11
    finally:
        f.close()

def test_backend_contract_through_the_client(srv):
    from vault import backends as B
    be = B.S3Backend("test-bucket", ACCESS, SECRET, endpoint=srv.endpoint, prefix="lumenaureum")
    data = os.urandom(3 * 1024 * 1024 + 17)
    size, sha = be.put_stream("s/abc.age", io.BytesIO(data))
    assert size == len(data) and sha == hashlib.sha256(data).hexdigest()
    assert be.stat("s/abc.age") == len(data) and "lumenaureum/s/abc.age" in srv.objects["test-bucket"]
    assert be.list("s/") == ["s/abc.age"] and be.list() == ["s/abc.age"]
    out = io.BytesIO(); be.get_stream("s/abc.age", out)
    assert out.getvalue() == data
    be.delete_verified("s/abc.age")
    assert be.stat("s/abc.age") is None
    with pytest.raises(ValueError):
        be.stat("../x")

def test_5xx_is_retried_with_backoff(srv, monkeypatch):
    waits = []
    monkeypatch.setattr(S, "SLEEP", waits.append)
    srv.fail = [500, 503]
    client(srv).put("x", b"1")
    assert srv.objects["test-bucket"]["x"][0] == b"1" and len(waits) == 2 and waits[1] > waits[0] * 0.6

def test_5xx_gives_up_after_the_last_try(srv):
    srv.fail = [500] * S.TRIES
    with pytest.raises(S.S3Error) as e:
        client(srv).put("x", b"1")
    assert e.value.status == 500 and "x" not in srv.objects["test-bucket"]

def test_4xx_is_not_retried(srv):
    srv.fail = [403]
    with pytest.raises(S.S3Error):
        client(srv).put("x", b"1")
    assert len(srv.log) == 1

def test_a_dropped_connection_is_retried(srv):
    srv.drop = 1
    client(srv).put("x", b"1")
    assert srv.objects["test-bucket"]["x"][0] == b"1"

def test_a_timeout_is_retried(srv, monkeypatch):
    c = client(srv)
    real = S.Client._once
    calls = []

    def slow(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise socket.timeout("timed out")
        return real(self, *a, **kw)

    monkeypatch.setattr(S.Client, "_once", slow)
    c.put("x", b"1")
    assert len(calls) == 2

def test_a_download_cut_short_resumes_where_it_stopped(srv):
    c = client(srv)
    data = os.urandom(3_000_000)
    c.put("big", data)
    srv.cut = 1
    out = io.BytesIO()
    assert c.download("big", out) == len(data) and out.getvalue() == data
    gets = [h for m, b, k, q, h in srv.log if m == "GET" and k == "big"]
    assert len(gets) == 2

def test_another_region_is_followed_once():
    f = FakeS3(region="eu-west-1")
    try:
        c = S.Client("test-bucket", ACCESS, SECRET, endpoint=f.endpoint, region="us-east-1")
        c.put("x", b"1")
        assert c.region == "eu-west-1" and f.objects["test-bucket"]["x"][0] == b"1"
    finally:
        f.close()

def test_addressing_styles():
    aws = S.Client("my-bucket", "a", "s", region="eu-west-1")
    assert aws._target("k/x.age") == ("my-bucket.s3.eu-west-1.amazonaws.com", "my-bucket.s3.eu-west-1.amazonaws.com", "/k/x.age")
    dotted = S.Client("my.bucket", "a", "s")
    assert dotted._target("k") == ("s3.us-east-1.amazonaws.com", "s3.us-east-1.amazonaws.com", "/my.bucket/k")
    nas = S.Client("b", "a", "s", endpoint="https://nas.example:9000")
    assert nas._target("k") == ("nas.example", "nas.example:9000", "/b/k") and nas._target("")[2] == "/b/"
    forced = S.Client("bkt", "a", "s", endpoint="https://r2.example.com", addressing="virtual")
    assert forced._target("k") == ("bkt.r2.example.com", "bkt.r2.example.com", "/k")

def test_virtual_host_addressing_against_the_server(srv, monkeypatch):
    real = socket.create_connection

    def to_local(addr, *a, **kw):
        return real(("127.0.0.1", addr[1]), *a, **kw)
    monkeypatch.setattr(socket, "create_connection", to_local)
    c = S.Client("test-bucket", ACCESS, SECRET, endpoint=f"http://localhost:{srv.port}", addressing="virtual")
    monkeypatch.setattr(S, "lan_ip", lambda ip: True)
    c.put("v/x.age", b"virtual")
    assert srv.objects["test-bucket"]["v/x.age"][0] == b"virtual"
    assert srv.log[-1][4] == f"test-bucket.localhost:{srv.port}"
    assert c.list("v/") == ["v/x.age"]

@pytest.mark.parametrize("ep,ok", [
    ("http://192.168.20.5:9000", True), ("http://10.0.0.5", True), ("http://127.0.0.1:9000", True),
    ("http://[fd00::5]:9000", True), ("http://100.101.102.103:9000", True), ("http://169.254.1.1", True),
    ("http://localhost:9000", True), ("https://s3.example.com", True), ("", True),
    ("http://8.8.8.8:9000", False), ("http://203.0.113.9", False), ("http://0.0.0.0:9000", False), ("http://[2001:4860::8888]", False), ("ftp://192.168.20.5", False),
])
def test_http_only_for_lan_addresses(ep, ok):
    assert (S.endpoint_problem(ep) is None) == ok

def test_http_name_is_judged_by_where_it_resolves(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", ("192.168.20.5", 0))])
    assert S.endpoint_problem("http://mini.lan:9000") is None
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", ("93.184.215.14", 0))])
    assert "own network" in S.endpoint_problem("http://s3.example.com")

def test_http_to_a_public_address_is_refused_by_the_client():
    with pytest.raises(S.PlainHTTPRefused):
        S.Client("b", "a", "s", endpoint="http://8.8.8.8:9000")

def test_http_name_that_leads_off_the_network_is_refused_at_connection(srv, monkeypatch):
    c = S.Client("test-bucket", ACCESS, SECRET, endpoint=f"http://localhost:{srv.port}")
    monkeypatch.setattr(S, "lan_ip", lambda ip: False)
    with pytest.raises(S.PlainHTTPRefused):
        c.put("x", b"1")
    assert "x" not in srv.objects["test-bucket"] and not srv.log

def test_self_signed_is_untrusted_then_pinned_then_a_new_key_stops(tmp_path):
    import webtls
    notes = []
    f = FakeS3(tls=make_cert(tmp_path))
    try:
        S.NOTE = lambda a, k, s: notes.append((a, k))
        with pytest.raises(S.CertProblem) as e:
            client(f, pins_table={}).put("x", b"1")
        p = e.value
        assert p.kind == "untrusted" and p.address == f"127.0.0.1:{f.port}" and p.seen["self_signed"]
        assert p.seen["name_ok"] and len(p.seen["spki"]) == 64 and not f.log
        assert str(p) == "The S3 storage's certificate isn't trusted. Check it in Setup."
        assert notes[-1] == (p.address, "untrusted")
        pins = {p.address: webtls.stamp(p.seen)}
        c = client(f, pins_table=pins)
        c.put("x", b"pinned")
        assert f.objects["test-bucket"]["x"][0] == b"pinned"
        assert S.check(p.address, table=pins)[0] == "pinned"
    finally:
        f.close()
    g = FakeS3(tls=make_cert(tmp_path))
    try:
        addr = f"127.0.0.1:{g.port}"
        with pytest.raises(S.CertProblem) as e:
            client(g, pins_table={addr: pins[p.address]}).put("y", b"1")
        assert e.value.kind == "changed" and e.value.pinned and e.value.seen["spki"] != pins[p.address]["spki"]
        assert str(e.value) == "The S3 storage's certificate changed. Check it in Setup."
        assert not g.log and S.check(addr, table={addr: pins[p.address]})[0] == "changed"
    finally:
        g.close()

def test_a_pinned_key_must_still_name_the_address(tmp_path):
    import webtls
    f = FakeS3(tls=make_cert(tmp_path, name="nas.example", ip="10.9.9.9"))
    try:
        addr = f"127.0.0.1:{f.port}"
        seen = webtls.peek(addr)
        with pytest.raises(S.CertProblem) as e:
            client(f, pins_table={addr: webtls.stamp(seen)}).put("x", b"1")
        assert e.value.kind == "changed" and e.value.seen["name_ok"] is False and not f.log
    finally:
        f.close()

def test_no_module_turns_certificate_checks_off():
    src = (Path(__file__).resolve().parent.parent / "vault" / "s3.py").read_text()
    for bad in ("check_hostname = False", "CERT_NONE", "_create_unverified_context", "verify_mode ="):
        assert bad not in src
