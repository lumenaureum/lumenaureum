import re

import pytest

from test_stage_logic_150 import (E, _acts, _day, _history, _in, _invoice_sent, _pay, _price, _session, _sql, _stage,
                                  env)

def _page(c, sid=1):
    r = c.get(f"/c/1?s={sid}", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _todo(page):

    block = page.split('class="ui-todos"', 1)[1].split("</ul>", 1)[0]
    return [(i, re.sub(r"<[^>]+>", " ", re.sub(r'<span class="ms[^"]*"[^>]*>[^<]*</span>', "", w)).split())
            for i, w in re.findall(r'<li class="ui-todo[^"]*" id="([^"]+)">(.*?)</li>', block, re.S)]

def _words(page):
    return [" ".join(w) for _i, w in _todo(page)]

def test_missing_shoot_facts_are_a_needs_you_row_with_its_form(env):
    A, c = env
    _session(A, "booked", days=10)
    acts = [a for a in _acts(A) if a.get("shoot_facts")]
    assert len(acts) == 1 and acts[0]["what"] == "Set the shoot date, time and place" and acts[0]["detail"] == "No start time"
    assert not [a for a in _acts(A) if (a.get("what") or "").startswith("Confirm time and place")]
    page = _page(c)
    first = _todo(page)[0]
    assert first[0] == "row-date_set" and " ".join(first[1]).startswith("Shoot date, time and place")
    form = page.split('id="f-date_set-1"', 1)[1].split("</form>", 1)[0]
    assert 'value="Longwood Gardens"' in form and 'name="tentative"' in form and "Date not final yet" in form
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert 'href="#f-date_set-1"' in head and 'data-ui-open="f-date_set-1"' in head

    r = c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 10), "start_time": "16:00",
                                         "where": "Longwood Gardens", "tentative_shown": "1", "back": "/c/1?s=1"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/c/1?s=1")
    assert not [a for a in _acts(A) if a.get("shoot_facts")]
    assert [a for a in _acts(A) if a.get("what") == "Confirm time and place"]

    page = _page(c)
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert 'href="#f-date_set-1"' in head and "4:00" in head and not re.search(r"<(form|button)\b", head)
    assert 'id="f-date_set-1"' in page.split('id="needs"', 1)[1]

def test_a_date_not_final_yet_is_the_row_and_the_form_makes_it_final(env):
    A, c = env
    _session(A, "booked", days=30, start="10:00")
    _sql(A, "UPDATE sessions SET date_tentative=1 WHERE id=1")
    row = [a for a in _acts(A) if a.get("shoot_facts")]
    assert row and row[0]["detail"] == "Date not final yet"
    assert "date not final yet" in _page(c).split("</header>", 1)[0]
    c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 31), "start_time": "10:00", "tentative_shown": "1"},
           environ_base=E)
    assert tuple(_sql(A, "SELECT date_tentative, shoot_date FROM sessions WHERE id=1")[0]) == (0, _day(A, 31))

    c.post("/session/1/quick", data={"act": "date", "shoot_date": _day(A, 32), "start_time": "10:00", "tentative_shown": "1",
                                     "tentative": "1"}, environ_base=E)
    assert _sql(A, "SELECT date_tentative FROM sessions WHERE id=1")[0][0] == 1

def test_the_edit_form_says_date_not_final_yet_and_shows_no_business_fields_for_a_family(env):
    A, c = env
    _session(A, "booked", days=30)
    page = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert "Date not final yet</label>" in page and "waiting on a due date" not in page
    assert "Business client options" not in page and 'name="po_number"' not in page and 'name="lic_term"' not in page

def test_length_comes_from_the_quote_in_words_and_is_read_only(env):
    A, c = env
    _session(A, "booked", days=30)
    page = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert 'name="duration_min"' in page
    _price(A, c, "400")
    page = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert 'name="duration_min"' not in page and "Length" in page and "1 hour</span>" in page
    stamp = page.split('name="seen" value="')[1].split('"')[0]
    c.post("/session/1", data={"title": "", "type": "family", "shoot_date": _day(A, 30), "start_time": "", "stops": "",
                               "duration_min": "300", "seen": stamp}, environ_base=E)
    assert _sql(A, "SELECT duration_min FROM sessions WHERE id=1")[0][0] is None
    with A.app.test_request_context():
        assert [A.length_words(m) for m in (120, 90, 45, 60)] == ["2 hours", "1 hour 30 minutes", "45 minutes", "1 hour"]

@pytest.fixture
def biz(env):

    A, c = env
    if "family" not in A.C.LICENSE_TYPES:
        pytest.skip("family sessions are not licensed in this setup")
    _sql(A, "UPDATE households SET company='Acme Realty' WHERE id=1")
    return A, c, "family"

def test_license_terms_sit_with_the_quote_and_lock_at_book_it(biz):
    A, c, t = biz
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, ?, 'quoted', ?)", (t, _day(A, 20)))
    _sql(A, "INSERT INTO stage_log (session_id, stage) VALUES (1, 'quoted')")
    _in(A, lambda: A.sync_workflow_tasks(1))
    _price(A, c, "900")
    edit = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert 'name="po_number"' in edit and 'name="lic_term"' not in edit
    bill = _page(c).split('id="billing"', 1)[1]
    assert 'action="/session/1/quote/license"' in bill
    c.post("/session/1/quote/license", data={"lic_term": "2 years", "lic_territory": "United States", "lic_present": "1",
                                             "lic_exclusive": "1"}, environ_base=E)
    assert tuple(_sql(A, "SELECT lic_term, lic_territory, lic_exclusive FROM sessions WHERE id=1")[0]) == ("2 years", "United States", 1)
    c.post("/session/1/quote/send", environ_base=E)
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    assert _stage(A) == "booked"
    r = c.post("/session/1/quote/license", data={"lic_term": "forever", "lic_present": "1"}, environ_base=E)
    assert r.status_code == 302 and _sql(A, "SELECT lic_term FROM sessions WHERE id=1")[0][0] == "2 years"
    bill = _page(c).split('id="billing"', 1)[1]
    assert 'action="/session/1/quote/license"' not in bill and "2 years, United States, exclusive" in bill
    edit = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    stamp = edit.split('name="seen" value="')[1].split('"')[0]
    c.post("/session/1", data={"title": "", "type": t, "shoot_date": _day(A, 20), "start_time": "", "stops": "",
                               "po_number": "PO-77", "lic_term": "forever", "lic_present": "1", "seen": stamp}, environ_base=E)
    assert tuple(_sql(A, "SELECT po_number, lic_term FROM sessions WHERE id=1")[0]) == ("PO-77", "2 years")

def test_shot_follows_the_work_and_never_stacks_parentheticals(env, monkeypatch):
    A, c = env
    monkeypatch.setattr(A.C, "galleries_on", lambda: True)
    _session(A, "booked", days=-1, start="10:00")
    _price(A, c, "400")
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    assert _stage(A) == "shot"
    words = _words(_page(c))
    first = [w for w in words if "From Booked" in w]
    assert first and all(words.index(w) < words.index(next(x for x in words if x.startswith("Upload original photos"))) for w in first)
    order = ["Upload original photos", "Cull", "Upload edited photos", "Gallery", "Invoice ($400.00 balance, Family session)",
             "Mail QR letter once paid"]
    at = [next((i for i, w in enumerate(words) if w.startswith(o)), o) for o in order]
    assert all(isinstance(i, int) for i in at), (at, words)
    assert at == sorted(at), words
    page = _page(c)
    todo = page.split('class="ui-todos"', 1)[1].split("</ul>", 1)[0]
    assert "(from " not in todo and not re.search(r"\)\s*(<[^>]+>\s*)*\(", todo)

    assert not [w for w in words if w.startswith(("Deposit", "Collect the deposit", "Send the deposit invoice"))]
    assert not [w for w in words if w.startswith("Morgan owes")]

def test_an_unpaid_deposit_still_holds_the_proof_gallery(env, monkeypatch):
    A, c = env
    monkeypatch.setattr(A.C, "galleries_on", lambda: True)
    _session(A, "shot", days=-5)
    _price(A, c, "400")
    _sql(A, "UPDATE sessions SET gallery_built=? WHERE id=1", (_day(A, -1),))
    with A.app.test_request_context():
        dep = A.deposit_held(1)
    assert dep > 0
    old = A.app.config.get("gallery_proofs")
    A.app.config["gallery_proofs"] = lambda sid: True
    try:
        row = [a for a in _acts(A) if (a.get("close") or {}).get("step") == "letter_mailed"]
        assert row and row[0]["what"] == f"Collect the {A.money(dep)} deposit first" and row[0]["form"] == "deposit_paid"
        assert row[0]["verb"] == f"Record payment ({A.money(dep)})"
        assert "The proof gallery link goes out once it is in." in row[0]["detail"]
        assert f"Collect the {A.money(dep)} deposit first" in _page(c)
        r = c.post("/session/1/quick", data={"act": "mark", "field": "letter_mailed"}, environ_base=E)
        assert r.status_code == 302 and not _sql(A, "SELECT letter_mailed FROM sessions WHERE id=1")[0][0]
        _pay(A, c, str(dep), kind="deposit")
        row = [a for a in _acts(A) if (a.get("close") or {}).get("step") == "letter_mailed"]
        assert not row or "deposit first" not in (row[0].get("verb") or "")
    finally:
        A.app.config["gallery_proofs"] = old

def _booked_with_quote(A, c):
    _sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, start_time, stops) "
            "VALUES (1, 1, 'family', 'quoted', ?, '10:00', 'Longwood Gardens')", (_day(A, 20),))
    _sql(A, "INSERT INTO stage_log (session_id, stage) VALUES (1, 'quoted')")
    _in(A, lambda: A.sync_workflow_tasks(1))
    _price(A, c, "400")
    c.post("/session/1/quote/send", environ_base=E)
    c.post("/session/1/quick", data={"act": "book"}, environ_base=E)
    assert _stage(A) == "booked"

def test_replace_the_quote_cancels_and_keeps_it_and_book_it_clears_it(env):
    A, c = env
    _booked_with_quote(A, c)
    old_vid, old_no = _sql(A, "SELECT id, number FROM quote_versions WHERE session_id=1")[0]
    _sql(A, "INSERT INTO documents (session_id, kind, status, sent, signed) VALUES (1, 'agreement', 'signed', ?, ?)",
         (_day(A, -2), _day(A, -1)))
    with A.app.test_request_context():
        dep = A.totals(1)["deposit_req"]
    _pay(A, c, str(dep), kind="deposit")
    _page(c)
    assert _sql(A, "SELECT COUNT(*) FROM doc_snapshots WHERE kind='agreement' AND ref=1")[0][0] == 1
    more = _page(c).split('id="more"', 1)[1]
    assert ">Replace the quote</button>" in more and "agreed to $400" in more and "fresh signature on the agreement" in more

    c.post("/session/1/quote/fee", data={"fee_key": "custom", "label": "Album", "amount": "300", "qty": "1"}, environ_base=E)
    assert not _sql(A, "SELECT 1 FROM lines WHERE label='Album'")
    c.post("/session/1/quote/replace", data={"back": "/c/1?s=1"}, environ_base=E)
    assert _stage(A) == "booked"
    h = _history(A)[-1]
    assert h.startswith(f"Canceled the booked quote {old_no or old_vid} ($400.00) to replace it: kept in History") and "stays on file" in h
    assert tuple(_sql(A, "SELECT status, sent, signed FROM documents WHERE session_id=1 AND kind='agreement'")[0]) == ("needed", None, None)
    _page(c)
    assert _sql(A, "SELECT COUNT(*) FROM doc_snapshots_old WHERE kind='agreement' AND ref=1")[0][0] == 1
    page = _page(c)
    rows = _words(page)
    assert rows[0].startswith("New quote waiting for their OK") and "Book it" in rows[0]
    assert any(w.startswith("Agreement") and "Send the agreement" in w for w in rows), rows
    assert 'action="/session/1/quote/fee"' in page
    c.post("/session/1/quote/fee", data={"fee_key": "custom", "label": "Album", "amount": "300", "qty": "1"}, environ_base=E)
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)
    with A.app.test_request_context():
        t = A.totals(1)
        assert A.replacing(1) is None and A.quote_locked(1)
    assert t["quoted"] == 700 and t["paid"] == dep
    assert t["deposit_short"] == max(0, t["deposit_req"] - dep)
    new_vid = _sql(A, "SELECT new_vid FROM quote_replacements WHERE session_id=1")[0][0]
    assert new_vid and new_vid != old_vid and _sql(A, "SELECT 1 FROM quote_marks WHERE vid=?", (new_vid,))
    new_no = _sql(A, "SELECT number FROM quote_versions WHERE id=?", (new_vid,))[0][0]
    assert _history(A)[-1].startswith(f"Booked the new quote {new_no} ($700.00), replacing the canceled one ($400.00)")
    page = _page(c)
    assert not [w for w in _words(page) if w.startswith("New quote waiting")]
    assert re.search(rf"Quote {old_no or old_vid}, [^<]*, canceled", page) and not re.search(rf"Quote {new_no}, [^<]*, canceled", page)

    n = len(_history(A))
    c.post("/session/1/quick", data={"act": "rebook"}, environ_base=E)
    assert len(_history(A)) == n

def test_replace_the_quote_only_from_booked_on(env):
    A, c = env
    _session(A, "quoted", days=20)
    _price(A, c, "400")
    c.post("/session/1/quote/replace", environ_base=E)
    assert not _sql(A, "SELECT 1 FROM quote_replacements")

def test_money_rows_say_what_for_and_nothing_is_owed_before_the_invoice(env):
    A, c = env
    _session(A, "shot", days=-5)
    _price(A, c, "450")
    acts = _acts(A)
    send = [a for a in acts if a["what"] == "Send the invoice ($450.00 balance, Family session)"]
    assert len(send) == 1 and send[0]["doc_verb"] == "Send the invoice" and not send[0]["theirs"]
    assert not [a for a in acts if " owes " in a["what"] or a["what"].startswith("Balance")]
    _invoice_sent(A, c)
    inv = _sql(A, "SELECT number FROM doc_numbers WHERE session_id=1 AND kind='INV'")[0][0]
    acts = _acts(A)
    owes = [a for a in acts if " owes " in a["what"]]
    assert len(owes) == 1 and owes[0]["what"].startswith(f"Morgan owes $450.00 · invoice {inv} sent ")
    assert owes[0]["detail"].startswith(f"Balance $450.00 of $450.00 (Family session, invoice {inv}).")
    assert not [a for a in acts if a["what"].startswith("Send the invoice")]
    assert _sql(A, "SELECT done IS NOT NULL FROM tasks WHERE session_id=1 AND title='Send invoice'")[0][0] == 1
    page = _page(c)
    assert "Morgan owes $450" in page and 'id="f-balance_paid-1"' in page

def test_other_payment_says_what_and_counts_at_its_value(env):
    A, c = env
    _session(A, "shot", days=-5)
    _price(A, c, "450")
    c.post("/session/1/money", data={"what": "payment", "kind": "balance", "amount": "200", "method": "other"}, environ_base=E)
    assert not _sql(A, "SELECT 1 FROM payments")
    c.post("/session/1/money", data={"what": "payment", "kind": "balance", "amount": "200", "method": "other",
                                     "note": "A cow in trade"}, environ_base=E)
    assert tuple(_sql(A, "SELECT amount, method, note FROM payments")[0]) == (200, "other", "A cow in trade")
    with A.app.test_request_context():
        assert A.totals(1)["paid"] == 200
    assert _history(A)[-1] == "Recorded a $200.00 payment, other: A cow in trade, at that value"
    page = _page(c)
    assert "A cow in trade" in page and 'value="other"' in page and "Other (say what)" in page

def test_waive_the_balance_is_a_write_off_with_a_reason_not_a_payment(env):
    A, c = env
    _session(A, "shot", days=-5)
    _price(A, c, "450")
    _invoice_sent(A, c)
    _pay(A, c, "300")
    page = _page(c)
    menu = page.split('id="row-balance_paid"', 1)[1].split("</li>", 1)[0]
    assert ">Waive the balance</button>" in menu and 'name="note_why" maxlength="200" required' in menu
    c.post("/close", data={"sid": "1", "step": "balance_paid", "outcome": "waived", "note_why": "Family friend"}, environ_base=E)
    assert tuple(_sql(A, "SELECT amount, why, note FROM write_offs")[0]) == (150, "waived", "Family friend")
    with A.app.test_request_context():
        t = A.totals(1)
    assert t["paid"] == 300 and t["written_off"] == 150 and t["due"] == 0
    assert any("Wrote off the balance of $150.00 (waived: Family friend). A write-off, not a payment" == h for h in _history(A))

def test_a_session_never_closes_while_money_is_owed_back(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "300")
    for t in _sql(A, "SELECT id FROM tasks WHERE session_id=1 AND done IS NULL"):
        _sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE id=?", (_day(A, -1), t[0]))
    _sql(A, "UPDATE sessions SET sd_shipped=?, favorites='IMG_1' WHERE id=1", (_day(A, -1),))
    _pay(A, c, "350")
    assert _stage(A) == "delivered"
    _pay(A, c, "50", kind="refund")
    assert _stage(A) == "closed"

def test_a_recorded_fact_settles_its_step_and_a_later_step_settles_earlier_ones(env):
    A, c = env
    _session(A, "shot", days=-5)
    _sql(A, "UPDATE sessions SET gallery_built=? WHERE id=1", (_day(A, -1),))
    _in(A, lambda: A.advance(1))
    done = dict(_sql(A, "SELECT title, outcome_note FROM tasks WHERE session_id=1 AND title IN ('Cull', 'Edit', 'Back up photos')"))
    assert done["Cull"] == done["Edit"] == "Settled: the gallery is built" and done["Back up photos"] is None
    assert "Done: Cull, Upload edited photos (the gallery is built)" in _history(A)
    n = len(_history(A))
    _in(A, lambda: A.advance(1))
    assert len(_history(A)) == n

def test_the_1_4_0_database_takes_the_new_table_and_columns_once(tmp_path, monkeypatch):
    from test_upgrade_from_140 import _open, _place, _q
    _place(tmp_path)
    A, c, _taken = _open(tmp_path, monkeypatch)
    cols = lambda t: {r[1] for r in _q(A, f"PRAGMA table_info({t})")}
    assert {"note"} <= cols("payments") and {"note"} <= cols("write_offs")
    assert {"session_id", "old_vid", "old_total", "old_lines", "agreement", "new_vid", "booked_at"} <= cols("quote_replacements")
    assert _q(A, "PRAGMA user_version")[0][0] == A.SCHEMA_VERSION == 9
    assert _q(A, "SELECT COUNT(*) FROM payments WHERE note != ''")[0][0] == 0
    paid = _q(A, "SELECT session_id, SUM(amount) FROM payments WHERE voided IS NULL GROUP BY session_id ORDER BY 1")
    wo = _q(A, "SELECT session_id, SUM(amount) FROM write_offs GROUP BY session_id ORDER BY 1")
    A2, _c2, _ = _open(tmp_path, monkeypatch)
    assert _q(A2, "SELECT session_id, SUM(amount) FROM payments WHERE voided IS NULL GROUP BY session_id ORDER BY 1") == paid
    assert _q(A2, "SELECT session_id, SUM(amount) FROM write_offs GROUP BY session_id ORDER BY 1") == wo
    assert _q(A2, "SELECT COUNT(*) FROM quote_replacements")[0][0] == 0
