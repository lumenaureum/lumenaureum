import datetime as dt
import html
import re

import pytest

import prints_env as PE
import test_photos_b2 as B2
from prints_env import E
from test_photos_b2 import env

OID = "c" * 32

@pytest.fixture
def e(tmp_path, monkeypatch):
    return PE.make(tmp_path, monkeypatch)

def check(e, hid=1):
    r = e.c.post(f"/client/{hid}/check", data={"back": f"/c/{hid}?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/c/{hid}?s=1")
    import jobs
    j_ = jobs.JOBS.get(f"client-check:{hid}") or {}
    assert j_.get("state") == "done", j_
    return j_.get("message")

def _two_days_ago():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

def check_forms(p):
    return re.findall(r'<form method="post" action="/client/1/check"[^>]*>(?:(?!</form>).)*?<button[^>]*>([^<]*)</button>', p, re.S)

def test_check_now_reads_the_clients_gallery_and_print_orders_and_says_what_came_in(e):
    (e.host / PE.GID / "seen.txt").write_text(_two_days_ago())
    PE.send(e, PE.payload(e, OID))
    said = check(e)
    assert said.startswith("Checked: ") and "they opened the download gallery" in said and "1 new print order" in said
    assert PE.q(e, "SELECT first_visit FROM sessions WHERE id=1")[0]["first_visit"]
    assert len(PE.q(e, "SELECT * FROM print_orders")) == 1
    assert not [p_ for p_ in (e.host / PE.GID / "orders").iterdir() if p_.name != "index.html"]
    assert check(e) == "Checked: nothing new from the client."

def test_check_now_reads_only_that_clients_sessions(e):
    con = e.A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (2, 'Other Family')")
    con.commit()
    con.close()
    PE.send(e, PE.payload(e, OID))
    (e.host / PE.GID / "seen.txt").write_text(_two_days_ago())
    assert check(e, hid=2) == "Checked: nothing new from the client."
    assert not PE.q(e, "SELECT * FROM print_orders")
    assert PE.q(e, "SELECT first_visit FROM sessions WHERE id=1")[0]["first_visit"] is None
    assert e.c.post("/client/99/check", environ_base=E).status_code == 404

def test_check_now_is_not_the_regular_check_and_writes_no_history_of_its_own(e):
    G = e.G
    G.LAST_CHECK.pop("at", None)
    n0 = len(PE.q(e, "SELECT * FROM audit"))
    check(e)
    assert "at" not in G.LAST_CHECK and 1 in G.LAST_CHECK["sid"]
    assert len(PE.q(e, "SELECT * FROM audit")) == n0

def test_a_web_host_that_cannot_be_read_is_said_plainly(e):
    def boom(gid):
        raise OSError("host down")
    was = e.G.seen_at
    e.G.seen_at = boom
    try:
        said = check(e)
    finally:
        e.G.seen_at = was
    assert said.startswith("Checked: nothing new from the client.")
    assert "could not be reached for part of it" in said and "regular check" in said

def test_collectors_plug_in_and_one_that_fails_does_not_stop_the_others(e):

    A = e.A
    seen = []

    def link_check(sids):
        seen.append(list(sids))
        return dict(got=["they accepted the quote"])

    def broken(sids):
        raise RuntimeError("no")
    A.hooks.client_checks[:0] = [broken]
    A.hooks.client_checks.append(link_check)
    try:
        said = check(e)
    finally:
        A.hooks.client_checks.remove(broken)
        A.hooks.client_checks.remove(link_check)
    assert seen == [[1]]
    assert said.startswith("Checked: they accepted the quote.") and "could not be reached" in said

def with_host(tmp):

    from vault import web as VW
    host = tmp / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)

def from_client(p):
    m = re.search(r'<div class="ui-factrow" id="from-client"><p class="ui-factrow-text">([^<]*)</p>', p)
    return m.group(1) if m else None

def test_one_check_now_in_one_fixed_place_the_from_the_client_line_atop_the_needs_you_block(env):

    A, c, tmp = env
    B2._session(A, "delivered", days=-20)
    B2._gallery(A, tmp, dl=False, opened_days_ago=2, draft_n=3, draft_at="2026-11-20")
    with_host(tmp)
    p = B2._page(c)
    assert check_forms(p) == ["Check now"]
    needs = B2._needs(p)
    assert re.match(r'\s*<section class="ui-needs"[^>]*>\s*<h2 class="ui-sec-title">[^<]*</h2>\s*<div class="ui-factrow" id="from-client">', needs)
    assert from_client(p).startswith("From the client")
    row = needs.split("Client is still picking (3 photos marked)", 1)[1].split("</li>", 1)[0]
    assert "/client/1/check" not in row and "Since " in row
    gal = B2._block(p)
    assert "/client/1/check" not in gal and "Check now" not in gal and "Look for client activity now" not in p
    assert re.search(r'id="gal-activity"><p class="ui-factrow-text">Opened ', gal)
    B2._sql(A, "UPDATE galleries SET draft_n=NULL WHERE session_id=1")
    assert check_forms(B2._page(c)) == ["Check now"]

def test_the_line_shows_whenever_anything_of_theirs_is_online_and_carries_the_last_check(e):
    p = html.unescape(e.c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    assert from_client(p) is not None and check_forms(p) == ["Check now"]
    check(e)
    p = html.unescape(e.c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    assert from_client(p) == "From the client, checked just now: nothing new from the client."
    import jobs
    jobs.JOBS.pop("client-check:1", None)
    p = html.unescape(e.c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    assert from_client(p) == "From the client: last checked just now"
    con = e.A.connect()
    con.execute("UPDATE galleries SET upload_state='removed', removed_at='2026-11-21' WHERE session_id=1")
    con.commit()
    con.close()
    p = html.unescape(e.c.get("/c/1?s=1", environ_base=E).get_data(as_text=True))
    assert from_client(p) is None and not check_forms(p)

def test_today_keeps_check_now_on_the_row_waiting_on_the_client(env):
    A, c, tmp = env
    B2._session(A, "delivered", days=-20)
    B2._gallery(A, tmp, dl=False, opened_days_ago=2, draft_n=3, draft_at="2026-11-20")
    with A.app.test_request_context():
        rows = [a for a in A.build_actions(only_sid=1) if (a.get("what") or "").startswith("Client is still picking")]
    assert rows and rows[0]["verb"] == "Check now" and rows[0]["post"] == "/client/1/check"

def test_the_check_shows_its_result_in_place(env):

    A, c, tmp = env
    B2._session(A, "delivered", days=-20)
    B2._gallery(A, tmp, opened_days_ago=2)
    with_host(tmp)
    assert re.search(r'<form method="post" action="/client/1/check" class="ui-action" data-ui-inplace>', B2._page(c))
    r = c.post("/client/1/check", data={"back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302
    p = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert '<div class="clash toast" role="status">Checking now.' in p
    assert re.search(r'id="from-client"><p class="ui-factrow-text">From the client, checked just now: ', p)
