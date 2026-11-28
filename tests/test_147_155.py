import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, run, one
from test_user_testing_fixes import _app, _q, _new

E = {"REMOTE_ADDR": "127.0.0.1"}

def _page(c, url="/history"):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def _lines(page):
    return re.findall(r'<div class="htext"><span>([^<]*)</span>', page)

def _seed(A):
    run(A, """
        INSERT INTO households (id, name) VALUES (2, 'Reyes');
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (5, 1, 'portrait', 'inquiry', '2027-01-01');
        INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (6, 2, 'portrait', 'inquiry', '2027-02-01');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-20 10:00:00', 1, 5, 'Marked the quote sent', 'Owner');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-18 08:00:00', 2, 6, 'Recorded a $100 deposit', 'Owner');
        INSERT INTO audit (at, action, who) VALUES ('2026-10-02 09:00:00', 'Ignored 1 entry(ies) in deletions.json that did not verify (changed outside Lumen Aureum)', 'Lumen Aureum');
        INSERT INTO audit (at, action, who) VALUES ('2026-10-01 09:00:00', 'Deleted again 1 client record(s) that had been deleted after this backup was made', 'Lumen Aureum');
        INSERT INTO audit (at, action, who) VALUES ('2026-09-15 12:00:00', 'Imported clients from a file: 2 added, 1 updated', 'Owner');
        INSERT INTO audit (at, action, who) VALUES ('2026-11-21 09:00:00', 'Signed in from a new device', 'Owner');
        INSERT INTO audit (at, action, who) VALUES ('2026-11-21 09:05:00', 'Security: changed the password', 'Owner');
        INSERT INTO log (household_id, session_id, at, channel, summary) VALUES (1, 5, '2026-11-19', 'call', 'Called about dates');""")

def test_every_line_of_the_studio_newest_first_security_left_to_account_activity(env):
    A, c = env
    _seed(A)
    page = _page(c)
    assert _lines(page) == ["Marked the quote sent", "Called about dates", "Recorded a $100 deposit",
                            "Ignored 1 entry(ies) in deletions.json that did not verify (changed outside Lumen Aureum)",
                            "Deleted again 1 client record(s) that had been deleted after this backup was made",
                            "Imported clients from a file: 2 added, 1 updated"]
    assert "Signed in" not in page and "changed the password" not in page

    assert page.index("November 2026") < page.index("October 2026") < page.index("September 2026")
    assert re.search(r'<details class="ui-fold\s*" id="m-2026-11" open>', page) and "November 2026 (3)" in page

def test_each_line_opens_its_client_and_session(env):
    A, c = env
    _seed(A)
    page = _page(c)
    row = page.split("Marked the quote sent", 1)[1].split("</div>", 1)[0]
    assert 'href="/c/1?s=5"' in row and "Lane: Portraits" in row
    row = page.split("Recorded a $100 deposit", 1)[1].split("</div>", 1)[0]
    assert 'href="/c/2?s=6"' in row and "Reyes" in row
    row = page.split("did not verify", 1)[1].split("</div>", 1)[0]
    assert "href=" not in row and "Lumen Aureum" in row

def test_every_line_carries_its_kind_for_the_chips(env):
    A, c = env
    _seed(A)
    page = _page(c)
    kinds = dict(re.findall(r'data-f-g-kind="([^"]*)">.*?<div class="htext"><span>([^<]*)</span>', page, re.S))
    kinds = {v: k for k, v in kinds.items()}
    assert kinds["Imported clients from a file: 2 added, 1 updated"] == "Imports"
    assert kinds["Deleted again 1 client record(s) that had been deleted after this backup was made"] == "Restores"
    assert kinds["Ignored 1 entry(ies) in deletions.json that did not verify (changed outside Lumen Aureum)"] == "Checks"
    assert kinds["Called about dates"] == "Notes and contact"
    assert kinds["Recorded a $100 deposit"] == "Payments"
    bar = page.split('id="filter-history"', 1)[1].split('data-f-none', 1)[0]
    assert 'data-f-val="Checks"' in bar and 'data-f-val="Imports"' in bar and 'data-f-q' in bar

def test_client_dates_and_words_narrow_it(env):
    A, c = env
    _seed(A)
    assert _lines(_page(c, "/history?client=2")) == ["Recorded a $100 deposit"]
    assert _lines(_page(c, "/history?client=1")) == ["Marked the quote sent", "Called about dates"]
    assert _lines(_page(c, "/history?client=none")) == [
        "Ignored 1 entry(ies) in deletions.json that did not verify (changed outside Lumen Aureum)",
        "Deleted again 1 client record(s) that had been deleted after this backup was made",
        "Imported clients from a file: 2 added, 1 updated"]
    assert _lines(_page(c, "/history?from=2026-10-01&to=2026-10-31")) == [
        "Ignored 1 entry(ies) in deletions.json that did not verify (changed outside Lumen Aureum)",
        "Deleted again 1 client record(s) that had been deleted after this backup was made"]

    assert _lines(_page(c, "/history?from=2026-11-20&to=2026-11-19")) == ["Marked the quote sent", "Called about dates"]
    assert _lines(_page(c, "/history?q=Reyes")) == ["Recorded a $100 deposit"]
    assert _lines(_page(c, "/history?q=deposit")) == ["Recorded a $100 deposit"]
    page = _page(c, "/history?client=2&from=2026-11-01")
    assert 'value="2" selected' in page and 'value="2026-11-01"' in page and "Reyes" in page.split("</header>")[0]
    for junk in ("/history?client=abc&from=yesterday&page=-3", "/history?page=99"):
        _page(c, junk)

def test_newest_first_a_page_at_a_time(env):
    A, c = env
    A.STUDIO_HISTORY_PAGE = 3
    rows = ";".join(f"INSERT INTO audit (at, household_id, action, who) VALUES ('2026-11-{d:02d} 10:00:00', 1, 'Line {d}', 'Owner')"
                    for d in range(1, 8))
    run(A, rows + ";")
    first = _page(c)
    assert _lines(first) == ["Line 7", "Line 6", "Line 5"] and "Older entries" in first and "Newer entries" not in first
    second = _page(c, "/history?page=2")
    assert _lines(second) == ["Line 4", "Line 3", "Line 2"] and "Older entries" in second and "Newer entries" in second
    third = _page(c, "/history?page=3")
    assert _lines(third) == ["Line 1"] and "Older entries" not in third
    assert 'href="/history?page=2&amp;client=1"' in _page(c, "/history?client=1")

def test_a_real_deletion_and_a_forged_deletions_entry_show_here(env):
    A, c = env
    run(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (5, 1, 'portrait', 'inquiry', '2027-01-01')")
    assert c.post("/client/1/delete", data={"confirm": "DELETE"}, environ_base=E).status_code in (302, 303)
    assert not one(A, "SELECT 1 FROM households WHERE id=1")
    A.DELETIONS_JSON.write_text(json.dumps(json.loads(A.DELETIONS_JSON.read_text()) + [{"id": 77, "created": "x", "mac": "0" * 64}]))
    A.reapply_deletions()
    lines = _lines(_page(c))
    assert "Deleted a client record and everything about it" in lines
    assert any("did not verify" in t for t in lines)

def test_an_import_line_shows_like_any_other(env):

    A, c = env
    with A.app.test_request_context():
        A.db()
        A.audit("Imported clients from a file: 1 added", note="clients.csv")
        A.audit("Added from an imported file", hid=1, note="clients.csv")
        A.db().commit()
    page = _page(c, "/history?q=imported")
    assert _lines(page) == ["Added from an imported file", "Imported clients from a file: 1 added"]
    assert page.count('data-f-g-kind="Imports"') == 2 and "clients.csv" in page

def test_findable_from_search_and_a_clients_history_not_the_top_bar(env):
    A, c = env
    _seed(A)
    page = _page(c, "/search?q=deposit")
    assert 'href="/history?q=deposit"' in page and "1 History line with" in page
    assert 'href="/history"' in _page(c, "/search")
    assert "/history" not in _page(c, "/search?q=zzzz-nothing")
    assert c.get("/search?q=Reyes", environ_base=E).status_code == 302
    client = _page(c, "/c/1")
    hist = client.split('id="more-notes"', 1)[1].split("</details>", 1)[0]
    assert 'href="/history"' in hist and "Studio history, all clients" in hist
    top = _page(c, "/").split('<main id="main"', 1)[0]
    assert "/history" not in top

def _line(c, sid, amount):
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Session", "amount": amount}, environ_base=E)

def test_an_older_quote_says_which_quote_replaced_it(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    _line(c, sid, "50")
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    old, new = [r[0] for r in _q(A, "SELECT id FROM quote_versions WHERE session_id=? ORDER BY id", (sid,))]
    no_old, no_new = (A_no(A, old), A_no(A, new))
    hid = _q(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]
    page = c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)
    assert f"Quote {no_old}, " in page and f", replaced by {no_new} &rsaquo;" in page
    assert not re.search(rf"Quote {no_new}, [^<]*replaced", page)
    doc = c.get(f"/quote/{old}", environ_base=E).get_data(as_text=True)
    assert "Replaced by" in doc and f'href="/quote/{new}"' in doc and f"quote {no_new}" in doc
    assert "Replaced by" not in c.get(f"/quote/{new}", environ_base=E).get_data(as_text=True)
    assert f"Quote {no_new} - " in c.get(f"/quote/{new}", environ_base=E).get_data(as_text=True)

def A_no(A, vid):
    with A.app.test_request_context():
        A.db()
        return A.quote_no(vid)

def test_a_quote_pdf_file_name_carries_its_number(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")
    c.post(f"/session/{sid}/quote/send", environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions WHERE session_id=?", (sid,))[0][0]
    seen = {}
    monkeypatch.setattr(A, "html_pdf", lambda html, name: seen.update(html=html, name=name) or "pdf")
    c.get(f"/quote/{vid}.pdf", environ_base=E)
    no = A_no(A, vid)
    assert seen["name"].startswith(f"quote-{no}-") and seen["name"].endswith(".pdf")
    assert f"Quote {no} - " in seen["html"] and "Replaced by" not in seen["html"]

def test_repeats_on_a_day_read_as_one_line_but_never_across_clients(env):
    A, c = env
    run(A, """INSERT INTO households (id, name) VALUES (2, 'Reyes');
        INSERT INTO audit (at, household_id, action, who) VALUES ('2026-11-20 10:00:00', 1, 'Changed the address', 'Owner');
        INSERT INTO audit (at, household_id, action, who) VALUES ('2026-11-20 10:05:00', 1, 'Changed the address', 'Owner');
        INSERT INTO audit (at, household_id, action, who) VALUES ('2026-11-20 10:06:00', 2, 'Changed the address', 'Owner');""")
    lines = _lines(_page(c))
    assert lines == ["Changed the address", "Changed the address · 2 times"]
