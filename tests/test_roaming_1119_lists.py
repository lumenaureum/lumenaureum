import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, E

def _x(A, sql, args=()):
    con = A.connect()
    con.execute(sql, args)
    con.commit()
    con.close()

def _page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def _day(A, n):
    return (A.today() + A.timedelta(days=n)).isoformat()

def _studio(A):

    _x(A, "UPDATE people SET email='avery@example.com' WHERE household_id=1")
    for hid, name in ((2, "Garcia Family"), (3, "Ortiz"), (4, "Kim")):
        _x(A, "INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
    _x(A, "INSERT INTO people (household_id, name, role, phone, email) VALUES (2, 'Priya Garcia', 'adult', '555-0142', 'priya@example.com')")
    _x(A, "INSERT INTO people (household_id, name, role) VALUES (3, 'Dana Ortiz', 'adult')")
    _x(A, "INSERT INTO people (household_id, name, role) VALUES (4, 'Jo Kim', 'adult')")
    for sid, hid, stage, days in ((21, 2, "booked", 20), (22, 3, "closed", -300), (23, 4, "inquiry", None)):
        _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (?, ?, 'family', ?, ?)",
           (sid, hid, stage, _day(A, days) if days is not None else None))

def _row(page, hid):
    i = page.index(f'<a class="ui-roster-name" href="/c/{hid}">')
    return page[page.rindex("<tr", 0, i):page.index("</tr>", i)]

def test_the_clients_count_follows_the_filter_and_a_match_says_why(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _studio(A)
    whole = _page(c, "/clients")
    assert re.search(r"<span data-r-count[^>]*>4 clients</span>", whole)
    page = _page(c, "/clients?q=0142")
    assert re.search(r"<span data-r-count[^>]*>1 of 4 clients</span>", page)
    assert "Phone: 555-0142" in _row(page, 2)
    page = _page(c, "/clients?q=priya")
    assert "Person: Priya Garcia" in _row(page, 2)
    page = _page(c, "/clients?q=garcia")
    assert "data-r-whyline hidden" in _row(page, 2)
    with A.app.test_request_context():
        row = dict(name="Kim", why_src=[("Person", "Jo Kim"), ("Phone", ""), ("Where they stand", "Booked · shoot Sun Dec 6")])
        assert A.rolodex_why(row, "dec") == ["Where they stand: Booked · shoot Sun Dec 6"]
        assert A.rolodex_why(row, "kim") == []

def test_no_match_offers_the_name_as_a_new_contact(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _studio(A)
    page = _page(c, "/clients?q=Realty")
    assert "No one matches." in page
    assert 'href="/contact/new?name=Realty"' in page and "Add <span data-r-typed>Realty</span> as a new contact" in page
    assert "data-r-all" not in page and "Search everything" not in page
    digits = _page(c, "/clients?q=9999")
    assert re.search(r'<a class="ui-go" href="/contact/new" data-r-add="/contact/new" hidden>', digits)
    assert 'value="Sam Realty"' in _page(c, "/contact/new?name=Sam%20%20Realty")
    assert 'href="/contact/new?name=Xyzzq"' in _page(c, "/search?q=Xyzzq")

def test_client_details_name_the_person_in_full_with_their_email_and_end_contact_apart(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _studio(A)
    page = _page(c, "/client/2")
    head = page.split("</header>", 1)[0]
    assert "priya@example.com" in head and "555-0142" in head
    people = page.split('id="people"', 1)[1]
    assert ">Priya Garcia, 555-0142<" in people
    labels = page.split('id="labels"', 1)[1].split("</section>", 1)[0]
    assert "data-end-contact" in labels and "Stop check-in reminders for this client." in labels
    assert labels.index("Add the label") < labels.index("data-end-contact")

def test_the_inquiry_hints_where_to_add_a_kind_of_shoot_and_ticks_the_contact(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _studio(A)
    types = _page(c, "/inquiry?hid=2")
    assert 'href="/setup/shoot">Add a kind of shoot in Settings, What you shoot</a>' in types
    form = _page(c, "/inquiry?hid=2&type=family")
    pid = _q(A, "SELECT id FROM people WHERE name='Priya Garcia'")[0][0]
    assert re.search(rf'name="subjects" value="{pid}" checked', form)
    assert not re.search(r'type="date"[^>]*autofocus', form)
    assert re.search(r'<details[^>]*><summary[^>]*>Gallery codeword</summary>', form)
    _x(A, "INSERT INTO people (household_id, name, role, is_minor) VALUES (2, 'Ava Garcia', 'child', 1)")
    form = _page(c, "/inquiry?hid=2&type=family")
    assert not re.search(rf'name="subjects" value="{pid}" checked', form)

def test_the_inquiry_contact_picker_says_who_is_a_lead_and_who_a_past_client(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    _studio(A)
    page = _page(c, "/inquiry")
    word = lambda hid: re.search(rf'href="/inquiry\?hid={hid}".*?<span class="sub"[^>]*>([^<]+)</span>', page, re.S).group(1)
    assert (word(1), word(2), word(3), word(4)) == ("New contact", "Booked", "Past client", "Lead")

def test_sessions_list_soonest_first_then_undated_then_the_most_recent_past(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    for sid, days in ((31, -200), (32, -5), (33, 30), (34, 2), (35, None)):
        _x(A, "INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (?, 1, 'family', 'booked', ?)",
           (sid, _day(A, days) if days is not None else None))
    page = _page(c, "/sessions?view=all")
    order = [int(s_) for s_ in re.findall(r'href="/c/1\?s=(\d+)"', page)]
    assert order == [34, 33, 35, 32, 31], order
