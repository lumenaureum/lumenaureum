import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _needs, _page, _session, _tab, browser, env

def _x(A, sql, args=()):
    con = A.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def _history(A, sid=1):
    return [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

WEB_MSG = "Inquiry from your website, preferred date 2027-04-18:\nCould you do a family session at the lake?"

def test_their_message_reads_like_an_email(env):
    A, c = env
    _session(A, "inquiry", days=None, msg=WEB_MSG)
    needs = _needs(_page(c))
    head = needs.split('class="ui-msg-head"', 1)[1].split("</p>", 1)[0]
    assert "Alex Morgan" in head and "from your website" in head and "9:00 am" in head
    assert head.index("Alex Morgan") < needs.index("Could you do a family session")
    assert re.search(r'Preferred date</span><span class="ui-fact-v">Sun Apr 18, 2027<', needs)
    assert '<p class="ui-message">Could you do a family session at the lake?</p>' in needs
    assert "Inquiry from your website" not in needs and "2027-04-18" not in needs
    assert _q(A, "SELECT summary FROM log WHERE session_id=1")[0][0] == WEB_MSG

def test_a_message_typed_in_by_the_photographer_keeps_its_channel(env):
    A, c = env
    _session(A, "inquiry", days=None, msg="Wants a session in the spring")
    needs = _needs(_page(c))
    assert "by email" in needs and '<p class="ui-message">Wants a session in the spring</p>' in needs
    assert "Preferred date" not in needs

def test_inquiry_actions_and_book_without_a_quote_under_more(env):
    A, c = env
    _session(A, "inquiry", days=None, msg=WEB_MSG)
    page = _page(c)
    needs = _needs(page)
    assert [b for b in ("Reply", "Build a quote", "Do not book") if b in needs] == ["Reply", "Build a quote", "Do not book"]
    assert "They said yes" not in page and ">Not a fit<" not in page
    more = page.split('id="more"', 1)[1]
    assert ">Book without a quote</button>" in more
    c.post("/session/1/quick", data={"act": "book", "back": "/c/1?s=1"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "booked"
    assert any("Moved to Booked: booked without a quote" in h for h in _history(A))

def test_quoted_offers_book_it_and_do_not_book(env):
    A, c = env
    _session(A, "quoted")
    needs = _needs(_page(c))
    assert '>Book it</button>' in needs and ">Do not book</a>" in needs
    assert "They said yes" not in needs and ">Not a fit<" not in needs

@pytest.mark.parametrize("stage", ["inquiry", "quoted"])
def test_do_not_book_records_the_reason_and_can_be_undone(env, stage):
    A, c = env
    _session(A, stage, days=None, msg=WEB_MSG)
    _x(A, "INSERT INTO stage_log (session_id, stage) VALUES (1, ?)", (stage,))
    needs = _needs(_page(c))
    for why in A.C.NOT_BOOKED_REASONS:
        assert f'value="{why}"' in needs
    r = c.post("/session/1/stage", data={"stage": "lost", "lost_reason": "They went elsewhere", "back": "/c/1?s=1"},
               environ_base=E)
    assert r.status_code == 302
    assert _q(A, "SELECT stage, lost_reason FROM sessions WHERE id=1")[0] == ("lost", "They went elsewhere")
    assert "Did not book: They went elsewhere" in _history(A)
    page = _page(c)
    needs = _needs(page)
    assert "Not booked</h2>" in needs and "Not booked: They went elsewhere." in needs
    label = "Undo: reopen at " + A.C.STAGE_LABELS[stage]
    assert f">{label}</button>" in needs
    c.post("/session/1/stage", data={"stage": stage, "back": "/c/1?s=1"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == stage
    assert f"Undid Do not book: back at {A.C.STAGE_LABELS[stage]}" in _history(A)

def test_do_not_book_needs_no_reason(env):
    A, c = env
    _session(A, "inquiry", days=None, msg=WEB_MSG)
    c.post("/session/1/stage", data={"stage": "lost", "back": "/c/1?s=1"}, environ_base=E)
    assert _q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "lost"
    assert "Did not book" in _history(A)

def test_today_offers_the_inquiry_actions(env):
    A, c = env
    _session(A, "inquiry", days=None, msg=WEB_MSG)
    with A.app.test_request_context():
        s = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        contact = A.q("SELECT * FROM people WHERE household_id=1", one=True)
        assert A.today_action(s, [], contact)["a"]["verb"] == "Reply"
        do = A.today_action(s, [], None)
        assert do["a"]["verb"] == "Build a quote" and do["a"]["href"].endswith("#billing")
        assert do["what"] == "Could you do a family session at the lake?"

def _today_studio(A):

    for hid, name in ((2, "Lee"), (3, "Kim"), (4, "Ellis")):
        _x(A, "INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
        _x(A, "INSERT INTO people (household_id, name, role, email) VALUES (?, ?, 'parent', ?)", (hid, name + " Parent", name.lower() + "@example.com"))
    for sid, hid in ((2, 2), (3, 3)):
        _x(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (?, ?, 'family', 'inquiry')", (sid, hid))
        _x(A, "INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?, ?, 'email', ?, ?)",
           (hid, sid, "Inquiry from your website:\nHello", A.today().isoformat() + " 09:00:00"))
    _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, retouch_picks) VALUES (4, 4, 'family', 'delivered', ?, 'IMG_0101')",
       ((A.today() - timedelta(days=20)).isoformat(),))
    with A.app.test_request_context():
        for sid in (2, 3, 4):
            A.sync_workflow_tasks(sid)
        A.db().commit()

def _badge(page):
    m = re.search(r'<span class="ui-inbox-n">(\d+)</span>', page.split("<main", 1)[0])
    return int(m.group(1)) if m else 0

def _wrote(page):

    sec = page.split('id="work"', 1)[1].split("</section>", 1)[0]
    return {m.group(2): "ui-tline-new" in m.group(1) for m in re.finditer(r'<li class="(ui-tline[^"]*)" id="line-s(\d+)"', sec)}

def test_filed_inquiries_and_picks_are_tasks_tagged_new_until_the_client_page_is_opened(env):

    A, c = env
    _today_studio(A)
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Inquiries and picks</h2>" not in page and ">Tasks</h2>" in page and 'id="inbox"' not in page
    assert {k_: v_ for k_, v_ in _wrote(page).items() if k_ in "234"} == {"2": True, "3": True, "4": True}
    assert _badge(page) == 0 and page.count(">New</span>") == 3
    c.get("/c/3?s=3", environ_base=E)
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert _wrote(page)["3"] is False and _wrote(page)["2"] is True and page.count(">New</span>") == 2
    c.get("/c/4?s=4", environ_base=E)
    assert _wrote(c.get("/", environ_base=E).get_data(as_text=True))["4"] is False
    _x(A, "UPDATE sessions SET retouch_picks='IMG_0101\nIMG_0207' WHERE id=4")
    assert _wrote(c.get("/", environ_base=E).get_data(as_text=True))["4"] is True

def test_seen_survives_a_restart_and_never_grows(env):
    A, c = env
    _today_studio(A)
    c.get("/c/2?s=2", environ_base=E)
    _x(A, "UPDATE sessions SET stage='quoted' WHERE id=2")
    c.get("/c/3?s=3", environ_base=E)
    import json
    seen = json.loads(_q(A, "SELECT value FROM settings WHERE key='today_seen'")[0][0])
    assert set(seen) == {"s3"}

def test_a_today_line_has_two_targets(env):
    A, c = env
    _today_studio(A)
    page = c.get("/", environ_base=E).get_data(as_text=True)
    line = re.search(r'<li class="ui-tline[^"]*" id="line-s2"[^>]*>(.*?)</li>', page, re.S).group(1)
    assert len(re.findall(r"<a\b", line)) == 1 and 'href="/c/2?s=2"' in line
    assert len(re.findall(r"<button\b", line)) == 1 and ">Reply</button>" in line
    assert '<span class="ui-tline-face" aria-hidden="true">' in line

def test_the_whole_line_opens_the_client_page_and_the_name_is_not_a_link_of_its_own(browser, env):
    A, c = env
    _today_studio(A)
    ctx, p = _tab(browser, c)
    try:
        p.goto(BASE + "/")
        line = p.locator("#line-s2")
        name = line.locator(".ui-tline-name")
        assert name.evaluate("e => getComputedStyle(e).textDecorationLine") == "none"
        assert name.evaluate("e => getComputedStyle(e).color") == line.locator(".ui-tline-what").evaluate("e => getComputedStyle(e).color")
        box = line.locator(".ui-tline-face").bounding_box()
        hit = p.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('a, button') && document.elementFromPoint(x, y).closest('a, button').getAttribute('href')",
                         [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        assert hit == "/c/2?s=2"
        btn = line.get_by_role("button", name="Reply").bounding_box()
        hit = p.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('a, button').textContent",
                         [btn["x"] + btn["width"] / 2, btn["y"] + btn["height"] / 2])
        assert hit.strip() == "Reply"
        p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        p.wait_for_url(BASE + "/c/2?s=2")
    finally:
        ctx.close()

def _booked(A):
    _session(A, "booked", days=20, start="15:30")
    _x(A, "INSERT INTO documents (session_id, kind, signer) VALUES (1, 'agreement', 'Alex Morgan')")
    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 400, 'fee')")
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()

def _row(page, words):
    m = re.search(r'<li class="ui-todo[^"]*"[^>]*>((?:(?!</li>).)*?' + re.escape(words) + r'.*?)</li>', page, re.S)
    return m.group(1) if m else ""

def test_before_the_send_each_client_step_is_the_photographers_action(env):
    A, c = env
    _booked(A)
    page = _page(c)
    agr, dep = _row(page, "Send the agreement"), _row(page, "Send the deposit invoice")
    assert agr and dep
    for r_ in (agr, dep):
        assert "waiting on the client" not in r_
    assert 'href="/session/1/agreement" target="_blank">Send the agreement</a>' in agr
    assert 'href="/session/1/invoice" target="_blank">Send the deposit invoice</a>' in dep
    assert "Agreement signed" not in page.split('id="more"', 1)[0] and "Deposit paid" not in page.split('id="more"', 1)[0]
    with A.app.test_request_context():
        s = A.q("SELECT * FROM sessions WHERE id=1", one=True)
        rows = A.focus_rows([a for a in A.build_actions(only_sid=1) if a["who"]["link"] == "/session/1"], s, A.totals(1))
        do = A.today_action(s, rows, None)
        assert A.button_words(do["a"]).startswith("Send the") and not do["a"].get("theirs")

def test_the_intake_questions_go_out_as_a_draft_then_wait_on_the_client(env):
    A, c = env
    _session(A, "inquiry", days=None, msg=WEB_MSG)
    intk = _row(_page(c), "Send the intake questions")
    assert intk and "waiting on the client" not in intk and "Fill in their answers" not in intk
    assert 'action="/session/1/intake/send"' in intk and "data-ui-mail=" in intk and ">Send the intake questions</button>" in intk
    r = c.post("/session/1/intake/send", data={"mail": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:alex.morgan.long.address@example.com")
    from urllib.parse import unquote
    body = unquote(r.headers["Location"])
    for _k, label in A.C.INTAKE["family"]:
        assert label in body
    assert _q(A, "SELECT summary FROM log WHERE session_id=1")[-1][0] == "Sent the intake questions"
    row = _row(_page(c), "Intake answers")
    assert "waiting on the client" in row and "Fill in their answers" in row

def test_mark_sent_on_the_agreement_then_it_waits_on_the_client(env):
    A, c = env
    _booked(A)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert 'action="/session/1/docs/sent?kind=agreement"' in doc and ">Mark sent</button>" in doc
    c.post("/session/1/docs/sent?kind=agreement", data={"set": "1", "back": "/session/1/agreement"}, environ_base=E)
    assert _q(A, "SELECT status, sent FROM documents WHERE session_id=1")[0] == ("sent", A.today().isoformat())
    assert "Marked the agreement sent" in _history(A)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert ">Mark sent</button>" not in doc and "Sent " in doc
    row = _row(_page(c), "Agreement signed")
    assert "waiting on the client" in row and "Send the agreement" not in _page(c)

def test_mark_sent_on_the_deposit_invoice_then_it_waits_on_the_client(env):
    A, c = env
    _booked(A)
    c.post("/session/1/invoice/issue", environ_base=E)
    inv = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    mark = re.search(r'<form method="post" action="([^"]+)" class="ui-action marksent">', inv).group(1)
    assert ">Mark sent</button>" in inv
    c.post(mark, data={"set": "1", "back": "/session/1/invoice"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM invoice_sent WHERE session_id=1")[0][0] == 1
    assert "class=\"ui-action marksent\"" not in c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    row = _row(_page(c), "Deposit paid")
    assert "waiting on the client" in row and "Send the deposit invoice" not in _page(c)

def test_the_deposit_invoice_route_when_no_invoice_step_is_open(env):
    A, c = env
    _booked(A)
    _x(A, "UPDATE tasks SET done='2026-01-01' WHERE title='Send invoice'")
    c.post("/session/1/invoice/issue", environ_base=E)
    inv = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert 'action="/session/1/invoice/deposit-sent"' in inv
    c.post("/session/1/invoice/deposit-sent", data={"back": "/session/1/invoice"}, environ_base=E)
    assert _q(A, "SELECT COUNT(*) FROM invoice_sent WHERE session_id=1")[0][0] == 1
    assert "Marked the deposit invoice sent" in _history(A)
    assert "deposit-sent" not in c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)

def test_a_release_is_sent_on_its_own(env):
    A, c = env
    _booked(A)
    _x(A, "INSERT INTO people (id, household_id, name, role) VALUES (7, 1, 'Sam Morgan', 'child')")
    did = _x(A, "INSERT INTO documents (session_id, kind, about_person, signer) VALUES (1, 'release', 7, 'Alex Morgan')") or \
        _q(A, "SELECT id FROM documents WHERE kind='release'")[0][0]
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    row = _row(_page(c), "Send the release")
    assert f'href="/doc/{did}/release"' in row
    c.post(f"/session/1/docs/sent?kind=release&did={did}", environ_base=E)
    assert "waiting on the client" in _row(_page(c), "Releases signed")

def _priced(A, c, stage="inquiry"):
    _session(A, stage, days=30, msg=WEB_MSG)
    with A.app.test_request_context():
        key = A.current_schedule()["items"][0]["key"]
    c.post("/session/1/quote/fee", data={"fee_key": key, "qty": "1", "back": "/c/1?s=1"}, environ_base=E)

def test_prices_ship_as_sample_and_are_approved_on_the_quote(env):
    A, c = env
    _priced(A, c)
    with A.app.test_request_context():
        sid_ = A.current_schedule()["id"]
        assert not A.prices_approved(sid_) and A.sched_name(sid_) == "Sample prices"
    page = _page(c)
    bill = page.split('id="billing"', 1)[1]
    assert "SAMPLE prices</span>" in bill and f'action="/fees/{sid_}/approve"' in bill and ">Approve these prices</button>" in bill
    assert "Approving counts as your review: they are final" in bill
    today_ = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Review your prices." in today_ and f'href="/fees/{sid_}"' in today_
    c.post("/session/1/quote/send", environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    doc = c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True)
    assert "SAMPLE prices, not yet approved by you." in doc and f'action="/fees/{sid_}/approve"' in doc
    assert "sample prices." in doc.split('class="foot"', 1)[1].split("</p>", 1)[0]
    r = c.post(f"/fees/{sid_}/approve", data={"back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    t = A.today()
    name, code = "Prices as of " + t.strftime("%b %-d, %Y"), t.strftime("%Y%m%d") + "-1"
    with A.app.test_request_context():
        assert A.prices_approved(sid_) and A.sched_name(sid_) == name and A.sched_code(sid_) == code
    assert any(h[0].startswith(f"Approved the prices: {name} ({code})") for h in _q(A, "SELECT action FROM audit"))
    assert "SAMPLE prices" not in _page(c)
    assert "Review your prices." not in c.get("/", environ_base=E).get_data(as_text=True)
    doc = c.get(f"/quote/{vid}", environ_base=E).get_data(as_text=True)
    assert "SAMPLE prices" not in doc and f"{name} ({code})" in doc and "fee schedule #" not in doc
    fees = c.get(f"/fees/{sid_}", environ_base=E).get_data(as_text=True)
    assert f"<h1>{name}</h1>" in fees and "Fee schedule #" not in fees

def test_the_price_list_itself_approves_and_a_changed_version_stays_yours(env):
    A, c = env
    with A.app.test_request_context():
        sch = A.current_schedule()
    fees = c.get(f"/fees/{sch['id']}", environ_base=E).get_data(as_text=True)
    assert "<h1>Sample prices</h1>" in fees and ">Approve these prices</button>" in fees
    c.post(f"/fees/{sch['id']}/approve", environ_base=E)
    data = {"fees_posted": "1"}
    for i, it in enumerate(sch["items"]):
        data.update({f"fee_label_{i}": it["label"], f"fee_amt_{i}": str(it["amount"] + (5 if i == 0 else 0))})
    c.post("/settings?tab=fees", data=data, environ_base=E)
    with A.app.test_request_context():
        new_ = A.current_schedule()["id"]
        assert new_ != sch["id"] and A.prices_approved(new_)
        assert A.sched_code(new_) == A.today().strftime("%Y%m%d") + "-2"
    fees = c.get(f"/fees/{new_}", environ_base=E).get_data(as_text=True)
    assert ">Approve these prices</button>" not in fees

def test_sample_wording_is_approved_in_place_on_the_document(env):
    A, c = env
    _booked(A)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert "SAMPLE wording" in doc and 'action="/settings/review/agreement"' in doc and ">Approve this wording</button>" in doc
    assert 'name="type" value="family"' in doc and "Approving counts as your review: the document is final" in doc
    form = re.search(r'<form method="post" action="/settings/review/agreement".*?</form>', doc, re.S).group(0)
    data = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    r = c.post("/settings/review/agreement", data=data, environ_base=E)
    assert r.headers["Location"].endswith("/session/1/agreement")
    with A.app.test_request_context():
        assert A.doc_reviewed("agreement", "family")
    assert "SAMPLE wording" not in c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    pdf_html = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert 'action="/settings/review/terms"' in pdf_html

def test_the_upgrade_approves_only_prices_used_on_a_sent_quote(env):
    A, c = env
    _priced(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    c.post(f"/quote/{vid}/sent", environ_base=E)
    sid_ = _q(A, "SELECT schedule_id FROM quote_versions WHERE id=?", (vid,))[0][0]
    _x(A, "INSERT INTO fee_schedules (created, items, note) SELECT '2026-10-02 10:00:00', items, '' FROM fee_schedules WHERE id=?", (sid_,))
    _x(A, "DELETE FROM settings WHERE key='price_approvals'")
    with A.app.test_request_context():
        got = A.price_approval_upgrade()
        A.db().commit()
        cur = A.current_schedule()["id"]
        assert got == [sid_, cur] and A.prices_approved(cur)
        assert A.sched_code(cur) == "20261002-1"
        assert A.price_approval_upgrade() is None
    assert "Review your prices." not in c.get("/", environ_base=E).get_data(as_text=True)

def test_the_upgrade_leaves_prices_never_quoted_as_sample(env):
    A, c = env
    _priced(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    _x(A, "DELETE FROM settings WHERE key='price_approvals'")
    with A.app.test_request_context():
        assert A.price_approval_upgrade() == []
        A.db().commit()
        assert not A.prices_approved(A.current_schedule()["id"])
    assert "Review your prices." in c.get("/", environ_base=E).get_data(as_text=True)

@pytest.mark.parametrize("stage", ["booked", "shot", "delivered"])
def test_the_quote_is_read_only_and_looks_it_from_booked_on(env, stage):
    A, c = env
    _priced(A, c, "quoted")
    c.post("/session/1/quote/discount", data={"reason": "friends", "pct": "20", "back": "/c/1?s=1"}, environ_base=E)
    _x(A, "UPDATE lines SET qty=2, unit=amount/1 WHERE kind='fee'")
    _x(A, "UPDATE sessions SET stage=? WHERE id=1", (stage,))
    _x(A, "INSERT INTO stage_log (session_id, stage) VALUES (1, 'quoted'), (1, 'booked')")
    page = _page(c)
    bill = page.split('id="billing"', 1)[1].split("</details>", 1)[0] if '<details class="ui-fold" id="billing"' in page else \
        page.split('id="billing"', 1)[1].split("</section>", 1)[0]
    table = bill.split("<table", 1)[1].split("</table>", 1)[0]
    assert "<input" not in table and "ui-x" not in table and "<form" not in table
    assert "qnewf" not in bill and "/quote/fee" not in bill and "/quote/discount" not in bill
    assert "/portfolio" not in bill and "Changes after booking" not in bill
    assert re.search(r'Discount</span><span class="ui-fact-v">Friends and family, 20%<', bill)
    assert re.search(r'Portfolio</span><span class="ui-fact-v">Allowed<', bill)

    more = page.split('id="more"', 1)[1]
    assert ">Replace the quote</button>" in more and "agreed to $" in more and "kept in History" in more

def test_the_quote_is_built_at_inquiry_and_quoted(env):
    A, c = env
    _priced(A, c, "quoted")
    page = _page(c)
    assert "qnewf" in page and 'action="/session/1/quote/discount"' in page

def _blocks(page):

    main = page.split("</header>", 1)[1]
    first = re.search(r'<section class="ui-needs" id="([^"]+)"', main).group(1)
    rest = main.split('class="ui-needs"', 1)[1].split("</section>", 1)[1]
    return [first] + re.findall(r'<(?:section|details|div) class="ui-(?:needs|sec|fold|flat)[^"]*" id="([^"]+)"', rest)

@pytest.mark.parametrize("stage,days,first", [("booked", 10, "needs"), ("booked", 0, "shoot"), ("shot", -3, "needs"),
                                              ("delivered", -20, "needs")])
def test_the_timeline_sits_right_under_the_needs_you_block(env, stage, days, first):
    A, c = env
    _session(A, stage, days=days, start="23:59")
    blocks = _blocks(_page(c))
    assert blocks[0] == first and blocks[1] == "timeline", blocks

@pytest.mark.parametrize("stage", ["inquiry", "quoted"])
def test_no_timeline_before_booked(env, stage):
    A, c = env
    _session(A, stage, days=10)
    assert 'id="timeline"' not in _page(c)

def test_adding_a_line_says_what_was_added_and_never_looks_like_a_button(browser, env):
    A, c = env
    _session(A, "inquiry", days=30, msg=WEB_MSG)
    ctx, p = _tab(browser, c, width=1100)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        sel = p.locator("form.qnewf select[name=fee_key]")
        opt = sel.locator("option[value]:not([value='']):not([value='custom'])").first
        label = opt.inner_text().split(" - ")[0]
        sel.select_option(opt.get_attribute("value"))
        p.locator("form.qnewf").get_by_role("button", name="Add").click()
        note = p.locator(".ui-note")
        note.wait_for()
        assert p.evaluate("window.__stay") == 1
        assert note.inner_text().startswith("Added: ") and label.split(",")[0] in note.inner_text()
        assert note.inner_text() != "Done"
        st = note.evaluate("e => { const s = getComputedStyle(e); return [s.borderTopWidth, s.backgroundColor, s.cursor] }")
        assert st[0] == "0px" and st[1] in ("rgba(0, 0, 0, 0)", "transparent") and st[2] != "pointer"
        assert note.evaluate("e => getComputedStyle(e, '::before').borderRightWidth") == "2px"
        p.wait_for_timeout(5200)
        assert p.locator(".ui-note").count() == 0
    finally:
        ctx.close()

def test_the_quantity_sits_with_add_never_alone_on_a_phone(browser, env):
    A, c = env
    _priced(A, c)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        qty = p.locator("form.qnewf input[name=qty]").bounding_box()
        add = p.locator("form.qnewf").get_by_role("button", name="Add").bounding_box()
        sel = p.locator("form.qnewf select[name=fee_key]").bounding_box()
        assert abs((qty["y"] + qty["height"] / 2) - (add["y"] + add["height"] / 2)) < 6
        assert qty["y"] >= sel["y"] + sel["height"] - 1
    finally:
        ctx.close()

def test_mark_sent_in_the_quote_window_refreshes_the_client_page_in_place(browser, env):
    A, c = env
    _priced(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _q(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    ctx, p = _tab(browser, c, width=1100)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        assert "Inquiry" in p.locator(".ui-head .hn-stage").inner_text()
        doc = ctx.new_page()
        doc.goto(BASE + f"/quote/{vid}")
        doc.locator("form.marksent").get_by_role("button", name="Mark sent").click()
        doc.wait_for_load_state()
        p.locator(".ui-head .hn-stage", has_text="Quoted").wait_for(timeout=10000)
        assert p.evaluate("window.__stay") == 1
        assert p.locator(".ui-needs#quote-now").count() == 1 and p.locator(".ui-needs").get_by_role("button", name="Book it").count() == 1
    finally:
        ctx.close()
    assert _q(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "quoted"

def test_mark_sent_on_the_agreement_refreshes_the_to_do_in_place(browser, env):
    A, c = env
    _booked(A)
    ctx, p = _tab(browser, c, width=1100)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        assert p.locator(".ui-todo", has_text="Send the agreement").count() == 1
        doc = ctx.new_page()
        doc.goto(BASE + "/session/1/agreement")
        doc.locator("form.marksent").get_by_role("button", name="Mark sent").click()
        doc.wait_for_load_state()
        p.locator(".ui-todo", has_text="Agreement signed").wait_for(timeout=10000)
        assert p.evaluate("window.__stay") == 1
        assert p.locator(".ui-todo", has_text="Send the agreement").count() == 0
    finally:
        ctx.close()

RULES_JS = """() => {
  const out = [];
  for (const e of document.querySelectorAll('main *')) {
    if (!e.checkVisibility || !e.checkVisibility()) continue;
    const s = getComputedStyle(e), r = e.getBoundingClientRect();
    if (r.width < 40 || parseFloat(s.borderLeftWidth) > 0 || parseFloat(s.borderRightWidth) > 0) continue;   // boxes
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== 'none') out.push([r.top + scrollY, r.left, r.right, e.className || e.tagName]);
    if (parseFloat(s.borderBottomWidth) > 0 && s.borderBottomStyle !== 'none') out.push([r.bottom + scrollY, r.left, r.right, e.className || e.tagName]);
  }
  return out;
}"""

def _stacked(rules):
    rules = sorted(rules)
    bad = []
    for a, b in zip(rules, rules[1:]):
        overlap = min(a[2], b[2]) - max(a[1], b[1])
        if 1.5 < b[0] - a[0] < 16 and overlap > 0.5 * min(a[2] - a[1], b[2] - b[1]):
            bad.append((a, b))
    return bad

@pytest.mark.parametrize("width", [390, 1100])
def test_no_two_rules_back_to_back_anywhere(browser, env, width):
    A, c = env
    _priced(A, c, "quoted")
    c.post("/session/1/quote/discount", data={"reason": "friends", "pct": "20", "back": "/c/1?s=1"}, environ_base=E)
    ctx, p = _tab(browser, c, width=width)
    try:
        for stage in ("inquiry", "quoted", "booked", "shot"):
            _x(A, "UPDATE sessions SET stage=? WHERE id=1", (stage,))
            for path in ("/c/1?s=1", "/", "/client/1"):
                p.goto(BASE + path)
                for opened in (False, True):
                    p.evaluate("o => document.querySelectorAll('main details').forEach(d => d.open = o)", opened)
                    bad = _stacked(p.evaluate(RULES_JS))
                    assert not bad, (stage, path, opened, bad)
    finally:
        ctx.close()

def test_the_details_forms_line_up_on_a_wide_screen(browser, env):
    A, c = env
    _session(A, "inquiry", days=None)
    ctx, p = _tab(browser, c, width=1100)
    try:
        p.goto(BASE + "/client/1")
        tops = p.evaluate("[...document.querySelectorAll('#details .ui-form-grid > label')].map(l => Math.round(l.getBoundingClientRect().top))")
        assert len(set(tops[:4])) == 1, tops
        p.evaluate("document.querySelectorAll('#people details').forEach(d => d.open = true)")
        tops = p.evaluate("[...document.querySelector('#people .ui-form-grid').children].map(l => Math.round(l.getBoundingClientRect().top))")
        assert len(set(tops)) == 1, tops
    finally:
        ctx.close()
