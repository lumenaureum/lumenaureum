import html
import os
import re
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        r_ = con.execute(q_, args)
        out = r_.fetchall()
        con.commit()
        return out
    finally:
        con.close()

def page_of(c, url):
    return html.unescape(c.get(url, environ_base=E).get_data(as_text=True))

def later(n):
    return (date.today() + timedelta(days=n)).isoformat()

def test_c2_a_custom_line_plus_an_hourly_add_on_keeps_the_types_length_and_leaves_it_editable(env):

    A, c = env
    c.post("/setup/shoot", data={"preset_weddings": "1"}, environ_base=E)
    sql(A, "INSERT INTO households (name) VALUES ('Kim')")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'wedding', 'inquiry', ?)", (later(90),))
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, qty) VALUES (1, 'Wedding package', 333.33, 'custom', 1)")
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, fee_key, qty, unit) VALUES (1, 'Additional hour x 0.5', 75, 'fee', 'extra_hour', 0.5, 150)")
    with A.app.test_request_context():
        assert A.session_minutes(1, "wedding") == A.C.TYPE_MINUTES["wedding"] + 30
        assert not A.length_from_quote(1)
        assert A.session_minutes(1, "wedding", default=False) == 30

    sql(A, "INSERT INTO lines (session_id, label, amount, kind, fee_key) VALUES (1, 'Wedding day (8 hr)', 2800, 'fee', 'wedding')")
    with A.app.test_request_context():
        assert A.session_minutes(1, "wedding") == 480 + 30
        assert A.length_from_quote(1)

def test_c4_accented_names_match_in_any_case_and_sort_in_their_place(env):

    A, c = env
    import db as dbmod
    assert dbmod.fold("ZOË ÑÚÑEZ") == dbmod.fold("zoe nunez") == dbmod.fold("Zoë Ñúñez") == "zoe nunez"
    for n_ in ("Zoë Ñúñez", "Mia Zimmer", "Ana Martin"):
        sql(A, "INSERT INTO households (name) VALUES (?)", (n_,))
        sql(A, "INSERT INTO people (household_id, name, role) VALUES ((SELECT MAX(id) FROM households), ?, 'adult')", (n_,))
    for typed in ("zoë ñúñez", "ZOË ÑÚÑEZ", "Zoe Nunez"):
        r_ = c.post("/contact/new", data={"contact_name": typed}, environ_base=E)
        assert "Already a client?" in html.unescape(r_.get_data(as_text=True)), typed
    assert sql(A, "SELECT COUNT(*) FROM households")[0][0] == 3
    for term in ("ñúñez", "ÑÚÑEZ", "nunez", "zoë"):
        assert "Zoë Ñúñez" in page_of(c, f"/search?q={term}"), term
    p_ = page_of(c, "/clients")
    assert p_.index("Ana Martin") < p_.index("Zoë Ñúñez") < p_.index("Mia Zimmer")
    p_ = page_of(c, "/clients?q=NUNEZ")
    assert "Zoë Ñúñez" in p_ and "Mia Zimmer" not in p_

def test_c5_the_duplicate_warning_keeps_gender_age_approximate_and_channel(env):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Avery Adams')")
    sql(A, "INSERT INTO people (household_id, name, role) VALUES (1, 'Avery Adams', 'adult')")
    r_ = c.post("/contact/new", data={"contact_name": "Avery Adams", "contact_gender": "F", "contact_age": "34",
                                      "contact_age_approx": "1", "channel": "text", "note": "Hi"}, environ_base=E)
    p_ = r_.get_data(as_text=True)
    assert "Already a client?" in html.unescape(p_)
    assert re.search(r'name="contact_gender" value="F"[^>]*checked', p_)
    assert re.search(r'name="contact_gender" value="M"(?![^>]*checked)', p_)
    assert re.search(r'name="contact_age"[^>]*value="34"', p_)
    assert re.search(r'name="contact_age_approx"[^>]*checked', p_)
    assert re.search(r'name="channel" value="text"[^>]*checked', p_)

def test_c6_a_client_or_person_name_cant_be_saved_blank(env):

    A, c = env
    sql(A, "INSERT INTO households (name) VALUES ('Kim')")
    sql(A, "INSERT INTO people (household_id, name, role) VALUES (1, 'Sophie Kim', 'adult')")
    p_ = page_of(c, "/client/1")
    assert re.search(r'<input name="name" maxlength="200" required value="Kim">', p_)
    assert all("required" in m_ for m_ in re.findall(r'<input name="name"[^>]*>', p_))
    c.post("/client/1", data={"name": "   ", "company": ""}, environ_base=E)
    assert sql(A, "SELECT name FROM households")[0][0] == "Kim"
    assert "Enter the client's name. Nothing was saved." in page_of(c, "/client/1")
    c.post("/client/1/person", data={"name": " ", "role": "adult"}, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM people")[0][0] == 1
    assert "Enter the person's name. Nothing was added." in page_of(c, "/client/1")
    c.post("/person/1", data={"name": "", "role": "adult"}, environ_base=E)
    assert sql(A, "SELECT name FROM people")[0][0] == "Sophie Kim"
    assert "Enter the person's name. Nothing was saved." in page_of(c, "/client/1")
    c.post("/person/1", data={"name": "  Sophie Kim-Ray ", "role": "adult"}, environ_base=E)
    assert sql(A, "SELECT name FROM people")[0][0] == "Sophie Kim-Ray"

def _client(A, name="Ruiz"):
    sql(A, "INSERT INTO households (name) VALUES (?)", (name,))
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES ((SELECT MAX(id) FROM households), ?, 'adult', 'a@example.com')",
        (f"Ana {name}",))
    return sql(A, "SELECT MAX(id) FROM households")[0][0]

def test_c7_an_inquiry_takes_a_long_list_of_names_and_refuses_too_long_with_a_plain_message(env):

    A, c = env
    hid = _client(A)
    p_ = page_of(c, f"/inquiry?hid={hid}&type=family")
    assert re.search(r'name="subject_name" maxlength="1000"', p_)
    nine = ", ".join(f"Person{i} Longlastname-Ruiz" for i in range(9))
    assert 200 < len(nine) < 1000
    r_ = c.post("/inquiry", data={"type": "family", "household_id": str(hid), "subject_name": nine}, environ_base=E)
    assert r_.status_code in (302, 303)
    assert sql(A, "SELECT COUNT(*) FROM sessions")[0][0] == 1
    assert sql(A, "SELECT COUNT(*) FROM people WHERE name LIKE 'Person%'")[0][0] == 9
    back_ = f"https://localhost/inquiry?hid={hid}&type=family"
    r_ = c.post("/inquiry", data={"type": "family", "household_id": str(hid), "subject_name": "x" * 1001},
                environ_base=E, headers={"Referer": back_})
    assert r_.status_code in (302, 303) and r_.headers["Location"].endswith(f"/inquiry?hid={hid}&type=family")
    assert sql(A, "SELECT COUNT(*) FROM sessions")[0][0] == 1
    assert "The names are too long (the limit is 1,000 characters). Nothing was saved." in page_of(c, f"/inquiry?hid={hid}&type=family")

def test_c8_back_after_creating_an_inquiry_then_create_again_makes_no_second_session(env):

    A, c = env
    hid = _client(A)
    p_ = page_of(c, f"/inquiry?hid={hid}&type=family")
    tok = re.search(r'name="once" value="([^"]+)"', p_).group(1)
    form = {"type": "family", "household_id": str(hid), "subject_name": "Ava", "once": tok}
    r1 = c.post("/inquiry", data=form, environ_base=E)
    r2 = c.post("/inquiry", data=form, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM sessions")[0][0] == 1
    assert r2.headers["Location"] == r1.headers["Location"]
    assert "That inquiry was already made. It was saved once." in page_of(c, r2.headers["Location"])
    form["subject_name"] = "Ben"
    c.post("/inquiry", data=form, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM sessions")[0][0] == 2

def test_c9_the_day_the_stage_rules_started_follows_the_time_zone_chosen_after_it(env):

    A, c = env
    sql(A, "DELETE FROM settings WHERE key IN ('stage_rules_from', 'stage_rules_from_utc', 'timezone')")
    sql(A, "INSERT INTO settings (key, value) VALUES ('stage_rules_from', '2026-11-26')")
    sql(A, "INSERT INTO settings (key, value) VALUES ('stage_rules_from_utc', '2026-11-26T01:30:00+00:00')")
    _client(A)
    sql(A, "INSERT INTO sessions (household_id, type, stage, created) VALUES (1, 'family', 'shot', '2026-11-25 21:00:00')")
    sql(A, "INSERT INTO doc_numbers (session_id, kind, number, issued) VALUES (1, 'INV', '2026112501', '2026-11-25')")
    with A.app.test_request_context():
        assert A.invoice_issued_before_15(1)
    c.post("/settings/timezone", data={"timezone": "America/New_York"}, environ_base=E)
    assert sql(A, "SELECT value FROM settings WHERE key='stage_rules_from'")[0][0] == "2026-11-25"
    with A.app.test_request_context():
        assert not A.invoice_issued_before_15(1)

def _text_boxes(box):
    for ch_ in getattr(box, "children", None) or []:
        if hasattr(ch_, "text"):
            yield ch_
        else:
            yield from _text_boxes(ch_)

def test_c11_a_long_unbroken_label_wraps_and_the_amounts_stay_on_the_page(env):

    A, c = env
    import weasyprint
    long_ = "Ultra" + "x" * 240
    _client(A, name="Kim" + "y" * 200)
    sql(A, "INSERT INTO sessions (household_id, type, stage) VALUES (1, 'family', 'inquiry')")
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, qty) VALUES (1, ?, 123.45, 'custom', 1)", (long_,))
    doc_ = page_of(c, "/session/1/quote/preview")
    assert long_ in doc_
    pages_ = weasyprint.HTML(string=doc_, base_url=A.ROOT.as_posix()).render(
        stylesheets=[weasyprint.CSS(string="@page { size: Letter; margin: 0.5in; }")]).pages
    page_w = pages_[0].width
    boxes_ = [b_ for p_ in pages_ for b_ in _text_boxes(p_._page_box)]
    amounts_ = [b_ for b_ in boxes_ if "123.45" in b_.text]
    assert amounts_
    for b_ in boxes_:
        assert b_.position_x + b_.width <= page_w + 1, (b_.text[:40], b_.position_x, b_.width, page_w)

def _quote_with_lines(A, c, tax=True, stype="family"):
    if stype == "wedding":
        c.post("/setup/shoot", data={"preset_weddings": "1"}, environ_base=E)
    if tax:
        sql(A, "DELETE FROM settings WHERE key IN ('taxes', 'tax_mode', 'sales_tax_pct')")
        sql(A, "INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8')")
    hid = _client(A, name="Lee")
    c.post("/inquiry", data={"type": stype, "household_id": str(hid)}, environ_base=E)
    sid = sql(A, "SELECT MAX(id) FROM sessions")[0][0]
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Prints", "amount": "100", "taxable": "1",
                                          "taxable_shown": "1", "back": f"/session/{sid}"}, environ_base=E)
    return sid

def test_c12_the_x_on_a_tax_or_discount_line_removes_it(env):

    A, c = env
    sid = _quote_with_lines(A, c)
    c.post(f"/session/{sid}/quote/discount", data={"pct": "10", "reason": ""}, environ_base=E)
    kinds = lambda: sorted(r_[0] for r_ in sql(A, "SELECT kind FROM lines WHERE session_id=?", (sid,)))
    assert kinds() == ["custom", "discount", "tax"]
    disc = sql(A, "SELECT id FROM lines WHERE session_id=? AND kind='discount'", (sid,))[0][0]
    c.post(f"/money/line/{disc}/delete", data={"back": f"/session/{sid}"}, environ_base=E)
    assert kinds() == ["custom", "tax"]
    assert "Discount removed." in page_of(c, f"/c/1?s={sid}")
    tax = sql(A, "SELECT id FROM lines WHERE session_id=? AND kind='tax'", (sid,))[0][0]
    c.post(f"/money/line/{tax}/delete", data={"back": f"/session/{sid}"}, environ_base=E)
    assert kinds() == ["custom"]
    assert "Sales tax is off for this session." in page_of(c, f"/c/1?s={sid}")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Album", "amount": "50", "taxable": "1",
                                          "taxable_shown": "1", "back": f"/session/{sid}"}, environ_base=E)
    assert kinds() == ["custom", "custom"]

@pytest.fixture
def genv(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "data/crm.db"), ("CRM_STORAGE_CFG", "data/storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("CRM_DEV", "1")
    (tmp_path / "data").mkdir()
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Diaz Family')")
    sql(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Emma Diaz', 'parent', 'emma@example.com')")
    sql(A, "INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://photos.example/g/')")
    return A, c

def test_c12_the_two_delivery_rows_have_different_titles(genv):

    A, c = genv
    sql(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date, gallery_id, delivery_method) VALUES "
           "(1, 1, 'family', 'delivered', ?, 'g-testgallery01', 'usb')", (later(-3),))
    with A.app.test_request_context():
        A.sync_workflow_tasks(1)
        A.db().commit()
    sql(A, "UPDATE tasks SET done=date('now'), outcome='done' WHERE session_id=1 AND title NOT IN (?, ?)",
        ("QR letter mailed", "Files delivered"))
    p_ = page_of(c, "/c/1?s=1")
    titles = re.findall(r'class="ui-todo-what">\s*([^<]+)', p_)
    assert any(t_.startswith("Get the USB drive to Emma") for t_ in titles), titles
    assert len(titles) == len(set(titles)), titles

def test_c12_a_contact_note_without_a_channel_is_filed_as_a_note(env):

    A, c = env
    c.post("/contact/new", data={"contact_name": "Noa Levi", "note": "Met at the fair"}, environ_base=E)
    c.post("/contact/new", data={"contact_name": "Ben Ora", "note": "Wrote in", "channel": "email"}, environ_base=E)
    assert [tuple(r_) for r_ in sql(A, "SELECT channel, summary FROM log ORDER BY id")] == [("note", "Met at the fair"), ("email", "Wrote in")]

def test_c12_the_two_tabs_page_lists_only_fields_that_differ(env):

    A, c = env
    _client(A)
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'inquiry', ?)", (later(60),))
    assert sql(A, "SELECT delivery_method FROM sessions")[0][0] is None
    seen_ = lambda: re.search(r'name="seen" value="([0-9a-f]+)"', page_of(c, "/session/1/edit")).group(1)
    form = {"title": "", "type": "family", "shoot_date": later(60), "start_time": "", "stops": "", "notes": "",
            "delivery_method": A.C.delivery_method("family")}
    tab_a, tab_b = seen_(), seen_()
    assert c.post("/session/1", data={**form, "seen": tab_b, "notes": "gate code 9911"}, environ_base=E).status_code == 302
    r_ = c.post("/session/1", data={**form, "seen": tab_a, "title": "Fall minis"}, environ_base=E)
    assert r_.status_code == 409
    rows_ = re.findall(r'<tr data-field="([a-z_]+)"', r_.get_data(as_text=True))
    assert "title" in rows_ and "delivery_method" not in rows_, rows_

def test_c12_shot_is_never_said_before_the_shoot_happened(env):

    A, c = env
    _client(A)
    past = later(-25)
    with A.app.test_request_context():
        when_ = A.nice(past)
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'inquiry', ?)", (past,))
    for stage in ("inquiry", "quoted", "booked"):
        sql(A, "UPDATE sessions SET stage=?", (stage,))
        p_ = page_of(c, "/c/1?s=1")
        assert f"Shot {when_}" not in p_ and f"Shoot date {when_} has passed" in p_, stage
    sql(A, "UPDATE sessions SET stage='shot'")
    assert f"Shot {when_}" in page_of(c, "/c/1?s=1")

def test_c12_the_price_list_sample_banner_says_nothing_about_signing(env):

    A, c = env
    p_ = page_of(c, "/fees/sheet?type=family")
    assert "SAMPLE wording, not yet reviewed by you." in p_
    assert "These terms also go on your quotes and agreements." in p_
    assert "signed" not in p_.lower().split("sample wording", 1)[1].split("</div>", 1)[0]
    _client(A)
    sql(A, "INSERT INTO sessions (household_id, type, stage) VALUES (1, 'family', 'booked')")
    assert "Once signed, this may be binding on you and your client." in page_of(c, "/session/1/agreement")

def test_c12_a_credit_cant_be_more_than_the_rest_of_the_quote(env):

    A, c = env
    sid = _quote_with_lines(A, c)
    lines_ = lambda: sorted((r_[0], r_[1]) for r_ in sql(A, "SELECT kind, amount FROM lines WHERE session_id=?", (sid,)))
    add = lambda label, amt: c.post(f"/session/{sid}/money", data={"what": "line", "label": label, "amount": amt,
                                                                   "back": f"/c/1?s={sid}"}, environ_base=E)
    add("Gift card", "-200")
    assert ("custom", -200) not in lines_()
    assert "A credit can't be more than the rest of the quote ($100.00). Make it smaller, or use a discount." in page_of(c, f"/c/1?s={sid}")
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "custom", "label": "Gift card", "amount": "-60", "qty": "2",
                                              "back": f"/c/1?s={sid}"}, environ_base=E)
    assert ("custom", -120) not in lines_()
    add("Gift card", "-100")
    assert ("custom", -100) in lines_()
    with A.app.test_request_context():
        assert A.totals(sid)["quoted"] >= 0
    prints = sql(A, "SELECT id FROM lines WHERE session_id=? AND amount=100", (sid,))[0][0]
    c.post(f"/money/line/{prints}/delete", data={"back": f"/c/1?s={sid}"}, environ_base=E)
    assert ("custom", 100) in lines_()
    assert "That would leave a credit bigger than the rest of the quote." in page_of(c, f"/c/1?s={sid}")
    gift = sql(A, "SELECT id FROM lines WHERE session_id=? AND amount=-100", (sid,))[0][0]
    c.post(f"/money/line/{gift}/delete", data={"back": f"/c/1?s={sid}"}, environ_base=E)
    assert ("custom", -100) not in lines_()

def _booked_with_plan(A, c, days, amount=1000.0, n=3, booked_days_ago=0):
    _client(A, name="Park")
    sql(A, "INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'booked', ?)", (later(days),))
    sql(A, "INSERT INTO lines (session_id, label, amount, kind, taxable) VALUES (1, 'Session', ?, 'custom', 1)", (amount,))
    sql(A, "INSERT INTO stage_log (session_id, stage, at) VALUES (1, 'booked', ?)", (later(-booked_days_ago),))
    with A.app.test_request_context():
        A.ensure_quote(1)
        A.db().commit()
    c.post("/session/1/installments", data={"n": str(n), "back": "/c/1?s=1"}, environ_base=E)

def test_c3_an_unpaid_deposit_is_its_own_first_line_and_the_lines_add_up_to_the_balance(env):

    A, c = env
    _booked_with_plan(A, c, days=120)
    with A.app.test_request_context():
        t_ = A.totals(1)
        rows = A.payment_schedule(1, t_, paid=True)
        assert t_["deposit_short"] > 0.005 and rows[0].get("deposit") and rows[0]["amount"] == round(t_["deposit_short"], 2)
        assert round(sum(r_["amount"] for r_ in rows), 2) == round(t_["due"], 2)
        assert [r_["n"] for r_ in rows[1:]] == [1, 2, 3]
    p_ = page_of(c, "/session/1/invoice")
    assert "Deposit, due now" in p_ and "Payment 1 of 3, due" in p_
    c.post("/session/1/money", data={"what": "payment", "kind": "deposit", "amount": str(t_["deposit_short"]),
                                     "method": "check", "received": "", "back": "/c/1?s=1"}, environ_base=E)
    with A.app.test_request_context():
        t_ = A.totals(1)
        rows = A.payment_schedule(1, t_, paid=True)
        assert not any(r_.get("deposit") for r_ in rows)
        assert round(sum(r_["amount"] for r_ in rows), 2) == round(t_["due"], 2)

def test_c3_with_no_time_left_for_a_plan_the_balance_is_one_payment_due_now(env):

    A, c = env
    _booked_with_plan(A, c, days=-30)
    with A.app.test_request_context():
        assert A.installment_schedule(1) == [] and A.payment_schedule(1, paid=True) == []
        assert "equal payments" not in A.balance_rule("family", 1)
    assert "Payment 1 of 3" not in page_of(c, "/session/1/invoice")
    assert "3 equal payments" not in page_of(c, "/session/1/agreement")

def test_c1_a_sent_quote_edited_after_it_went_counts_as_changed_and_book_it_waits(env):

    A, c = env
    sid = _quote_with_lines(A, c, tax=False, stype="wedding")
    back = f"/c/1?s={sid}"
    c.post(f"/session/{sid}/quote/send", data={"back": back}, environ_base=E)
    vid = sql(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    c.post(f"/quote/{vid}/sent", data={"back": back}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "quoted"
    p_ = page_of(c, back)
    assert "$100.00, sent" in p_ and "changed since you sent" not in p_
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Rush editing", "amount": "50", "back": back}, environ_base=E)
    p_ = page_of(c, back)
    assert "$150.00, changed since you sent $100.00" in p_ and "$150.00, sent" not in p_
    a_ = page_of(c, f"/session/{sid}/agreement")
    assert "$100.00" in a_ and "$150.00" not in a_
    c.post(f"/session/{sid}/quick", data={"act": "book", "back": back}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "quoted"
    assert "This quote changed since you sent it. Send the new one, or put it back." in page_of(c, back)
    rush = sql(A, "SELECT id FROM lines WHERE label='Rush editing'")[0][0]
    c.post(f"/money/line/{rush}/delete", data={"back": back}, environ_base=E)
    assert "changed since you sent" not in page_of(c, back)
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Rush editing", "amount": "50", "back": back}, environ_base=E)
    c.post(f"/session/{sid}/quote/send", data={"back": back}, environ_base=E)
    vid2 = sql(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    assert vid2 != vid
    c.post(f"/quote/{vid2}/sent", data={"back": back}, environ_base=E)
    assert "$150.00, sent" in page_of(c, back)
    assert "$150.00" in page_of(c, f"/session/{sid}/agreement")
    c.post(f"/session/{sid}/quick", data={"act": "book", "back": back}, environ_base=E)
    assert sql(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0][0] == "booked"

def test_c1_a_tax_change_that_moves_a_sent_quote_marks_it_changed(env):

    A, c = env
    sid = _quote_with_lines(A, c, stype="wedding")
    back = f"/c/1?s={sid}"
    c.post(f"/session/{sid}/quote/send", data={"back": back}, environ_base=E)
    vid = sql(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    c.post(f"/quote/{vid}/sent", data={"back": back}, environ_base=E)
    with A.app.test_request_context():
        assert not A.quote_changed(sid)
    sql(A, "UPDATE settings SET value='9' WHERE key='sales_tax_pct'")
    with A.app.test_request_context():
        A.apply_settings()
        A.retax_open_quotes()
        A.db().commit()
        assert A.quote_changed(sid) == dict(sent=A.today().isoformat(), total=108.0)
    assert "changed since you sent $108.00" in page_of(c, back)
