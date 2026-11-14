import io
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E
from vault import backends as B

BAD_BACKS = ["//evil.example", "///evil.example", "/\\evil.example", "\\/evil.example", "/\t/evil.example",
             "/\n/evil.example", "\r\n//evil.example", " //evil.example", "/\x00/evil.example", "/\x7f/evil.example",
             "https://evil.example", "http:/evil.example", "javascript:alert(1)", "data:text/html,x", "evil.example",
             "/x\r\nSet-Cookie: a=b", "", None]

def test_safe_back_keeps_only_same_site_paths(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    for bad in BAD_BACKS:
        assert A.safe_back(bad) is None, bad
    for ok in ["/", "/session/3#money", "/c/1?s=2", "/%2F%2Fevil.example", "/.//x"]:
        assert A.safe_back(ok) == ok
    con = A.connect()
    con.execute("INSERT INTO events (name, date) VALUES ('Team day', '2027-04-10')")
    con.commit(); con.close()
    for bad in BAD_BACKS[:-2]:
        r = c.post("/event/1/date", data={"date": "2027-04-11", "back": bad}, environ_base=E)
        loc = r.headers.get("Location", "")
        assert r.status_code == 302 and loc.startswith("/") and loc[1:2] not in ("/", "\\"), (bad, loc)

def test_trailing_age_matches_the_old_patterns(tmp_path, monkeypatch):
    A, _ = _app(tmp_path, monkeypatch)
    old = re.compile(r"^(.*?)[\s,(]+(\d{1,2})\s*\)?$")
    old_event = re.compile(r"^(.*?)[\s(]+(\d{1,2})\)?$")
    for s in ["Ada (7)", "Ada 7", "Ada, 7", "Ada (7 )", "Ada 123", "Ada", "(7)", "7", "Ada 1 2", "Ada\n7", "Ada 7\n",
              "Ada 7)\n\n", "Mia Rose (12)", "Ada,7", "Ada\t\t7", "Ada (٣)", "", "Ada ( 7 )"]:
        for rx, commas in ((old, True), (old_event, False)):
            m = rx.match(s)
            assert A.trailing_age(s, commas=commas) == ((m.group(1), m.group(2)) if m else None), (s, commas)

def test_a_long_run_of_tabs_is_quick(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    evil = "a" + "\t" * 200_000 + "x"
    t0 = time.monotonic()
    assert A.trailing_age(evil) is None and A.trailing_age(evil, commas=False) is None
    assert A.trailing_age("a" + "\t" * 200_000 + "7") == ("a", "7")
    assert time.monotonic() - t0 < 1
    t0 = time.monotonic()
    _new(A, c, "family", subject_name=evil)
    _new(A, c, "family", subject_name=", ".join(f"Kid{i}" for i in range(20_000)))
    con = A.connect()
    con.execute("INSERT INTO events (name, date) VALUES ('Team day', '2027-04-10')")
    con.commit(); con.close()
    c.post("/event/1/family", data={"household_name": "Bell", "children": evil}, environ_base=E)
    assert time.monotonic() - t0 < 10
    assert _q(A, "SELECT COUNT(*) FROM people WHERE name LIKE 'Kid%'")[0][0] <= 12

def test_ages_after_names_still_work(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _new(A, c, "family", subject_name="Ada (7), Ben 9 and Cole")
    rows = {r[0]: r[1] for r in _q(A, "SELECT name, birth_year FROM people WHERE name IN ('Ada', 'Ben', 'Cole')")}
    assert set(rows) == {"Ada", "Ben", "Cole"} and rows["Ada"] and rows["Ben"] and rows["Cole"] is None

def _root(tmp_path):
    root = tmp_path / "gal"
    B.LocalBackend.init_root(str(root))
    return root, B.LocalBackend(str(root), public=True)

def test_a_link_out_of_a_local_root_is_refused(tmp_path):
    root, be = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    mode_ = outside.stat().st_mode & 0o777
    (outside / "keep.bin").write_bytes(b"precious")
    os.symlink(outside, root / "g1")
    with pytest.raises(ValueError):
        be.put_stream("g1/new.bin", io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        be.get_stream("g1/keep.bin", io.BytesIO())
    with pytest.raises(ValueError):
        be.delete("g1/keep.bin")
    assert sorted(os.listdir(outside)) == ["keep.bin"] and (outside / "keep.bin").read_bytes() == b"precious"
    assert outside.stat().st_mode & 0o777 == mode_
    os.symlink(outside / "keep.bin", root / "f.bin")
    with pytest.raises(ValueError):
        be.get_stream("f.bin", io.BytesIO())

def test_a_local_root_still_works_and_may_itself_be_a_link(tmp_path):
    real, _ = _root(tmp_path)
    os.symlink(real, tmp_path / "alias")
    be = B.LocalBackend(str(tmp_path / "alias"), public=True)
    be.put_stream("g2/a.bin", io.BytesIO(b"hello"))
    out = io.BytesIO()
    be.get_stream("g2/a.bin", out)
    assert out.getvalue() == b"hello" and be.stat("g2/a.bin") == 5 and be.list() == ["g2/a.bin"]
    be.delete_verified("g2/a.bin")
    for bad in ("../x", "/etc/passwd", "a\\b", ""):
        with pytest.raises(ValueError):
            be.stat(bad)

def test_contained_helper(tmp_path):
    assert B.contained(str(tmp_path), str(tmp_path / "a" / "b")) == str(tmp_path / "a" / "b")
    with pytest.raises(ValueError):
        B.contained(str(tmp_path / "a"), str(tmp_path / "ab"))

def test_setup_code_file_is_owner_only_whatever_the_umask(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "data" / "crm.db"))
    from helpers import fresh_modules
    fresh_modules()
    import netsec
    monkeypatch.setitem(netsec.CLAIM, "code", None)
    old = os.umask(0)
    try:
        code = netsec.claim_code()
    finally:
        os.umask(old)
    p = netsec.code_file()
    assert code in p.read_text() and p.stat().st_mode & 0o777 == 0o600
