import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, one, run
from test_37_155 import _upload, _token, _check, _plan, _confirm, _all, E

import client_csv as CC

def _hid(A, name):
    return one(A, "SELECT id FROM households WHERE name=?", (name,))[0]

def test_update_does_not_overwrite_one_partner_with_another_who_shares_the_home_phone(env):
    A, c = env
    run(A, """INSERT INTO people (id, household_id, name, role, email, phone, birth_date)
              VALUES (1, 1, 'John Lane', 'adult', 'john@example.com', '555-201-3344', '1980-01-02');""")
    data = ("Client,Person,Email,Phone,Birth date\n"
            "Lane,John Lane,john@example.com,555-201-3344,1/2/1980\n"
            "Lane,Jane Lane,jane@example.com,555-201-3344,3/4/1982\n").encode()
    tok = _token(_upload(c, data))
    page = _check(A, c, tok)
    gr = _plan(A, tok)["match"][0]
    _confirm(A, c, tok, picks={"pick_" + gr["id"]: "update"})
    john = one(A, "SELECT email, birth_date FROM people WHERE id=1")

    assert "John Lane's email" not in [o_[2] for o_ in gr["ops"]], ("preview proposes giving John Jane's email", gr["ops"])
    assert (john[0], (john[1] or "")[:10]) == ("john@example.com", "1980-01-02"), tuple(john)
    assert one(A, "SELECT COUNT(*) FROM people WHERE household_id=1 AND name='Jane Lane'")[0] == 1

def test_two_rows_matching_one_client_both_keep_their_notes(env):
    A, c = env
    run(A, """UPDATE households SET notes='Long-time client' WHERE id=1;
              INSERT INTO people (id, household_id, name, role, email) VALUES (1, 1, 'John Lane', 'adult', 'john@example.com');
              INSERT INTO people (id, household_id, name, role, email) VALUES (2, 1, 'Jane Lane', 'adult', 'jane@example.com');""")
    data = ("Name,Email,Notes\n"
            "John Lane,john@example.com,Prefers mornings\n"
            "Jane Lane,jane@example.com,Allergic to cats\n").encode()
    tok = _token(_upload(c, data))
    plan = _plan(A, tok)
    assert len(plan["match"]) == 2 and all(g_["hid"] == 1 for g_ in plan["match"])
    _confirm(A, c, tok, picks={"pick_" + g_["id"]: "update" for g_ in plan["match"]})
    notes = one(A, "SELECT notes FROM households WHERE id=1")[0]
    hist = " ".join(r_[0] for r_ in _all(A, "SELECT action FROM audit WHERE household_id=1"))
    assert hist.count("notes added") == 2
    assert "Prefers mornings" in notes and "Allergic to cats" in notes, notes

def test_problem_row_number_matches_the_spreadsheet_row_after_a_blank_row(env):
    A, c = env

    data = b"Name,Email\nAva Stone,ava@example.com\n,\nJo Park,jo@example.com\n,kim@example.com\n"
    tok = _token(_upload(c, data))
    plan = _plan(A, tok)
    assert plan["problems"] == [(5, "No name: left out")], plan["problems"]

def test_a_duplicated_row_does_not_add_the_same_person_twice(env):
    A, c = env
    data = b"Client,Person,Email\nRivera,Ana Rivera,ana@example.com\nRivera,Ana Rivera,ana@example.com\n"
    tok = _token(_upload(c, data))
    _confirm(A, c, tok)
    hid = _hid(A, "Rivera")
    assert one(A, "SELECT COUNT(*) FROM people WHERE household_id=?", (hid,))[0] == 1

def test_google_contacts_export_is_guessed_usably():
    header = ["Name", "Given Name", "Additional Name", "Family Name", "Nickname", "E-mail 1 - Type", "E-mail 1 - Value",
              "Phone 1 - Type", "Phone 1 - Value"]
    row = ["John Smith", "John", "", "Smith", "Johnny", "* Home", "john@example.com", "Mobile", "555-111-2222"]
    recs = CC.records(header, [row], CC.guess(header))
    groups, _p = CC.groups(recs)
    p = groups[0]["people"][0]
    assert (p["name"], p["email"], p["phone"]) == ("John Smith", "john@example.com", "555-111-2222"), \
        (p["name"], p["email"], p["phone"])

def test_remove_import_keeps_a_client_made_an_event_contact(env):
    A, c = env
    tok = _token(_upload(c, b"Name,Email\nAva Stone,ava@example.com\n"))
    _confirm(A, c, tok)
    ava = _hid(A, "Ava Stone")
    c.post("/events", data={"name": "Spring gala", "date": "2026-12-12", "venue": "Hall", "contact": str(ava)},
           environ_base=E)
    assert one(A, "SELECT contact_hid FROM events")[0] == ava
    bid = A.json.loads(one(A, "SELECT value FROM settings WHERE key='client_imports'")[0])[-1]["id"]
    c.post(f"/clients/import/batch/{bid}/remove", environ_base=E)
    still = one(A, "SELECT 1 FROM households WHERE id=?", (ava,))
    dangling = one(A, "SELECT contact_hid FROM events")[0]
    assert still, f"the event's organizer contact was deleted by Remove this import (event contact_hid={dangling})"

GOOGLE_CSV = (
    "First Name,Middle Name,Last Name,Phonetic First Name,Phonetic Middle Name,Phonetic Last Name,Name Prefix,"
    "Name Suffix,Nickname,File As,Organization Name,Organization Title,Birthday,Notes,Labels,"
    "E-mail 1 - Label,E-mail 1 - Value,Phone 1 - Label,Phone 1 - Value\n"
    "Sarah,,Lopez,,,,,,,,,,,,* myContacts,* Home,sarah.lopez@example.com,Mobile,(555) 010-0199\n"
)

def test_a_google_contacts_export_keeps_the_email_and_phone_number(env):
    A, c = env
    _confirm(A, c, _token(_upload(c, GOOGLE_CSV.encode(), "contacts.csv")))
    row = one(A, "SELECT name, email, phone FROM people WHERE name LIKE 'Sarah%'")
    assert row is not None
    assert row["email"] == "sarah.lopez@example.com", dict(row)
    assert "555" in row["phone"], dict(row)

def test_a_google_contacts_export_does_not_double_the_name(env):
    A, c = env
    csv_ = ("Name,Given Name,Additional Name,Family Name,E-mail 1 - Type,E-mail 1 - Value,Phone 1 - Type,Phone 1 - Value\n"
            "Sarah Lopez,Sarah,,Lopez,* ,sarah.lopez@example.com,Mobile,555-010-0199\n")
    _confirm(A, c, _token(_upload(c, csv_.encode(), "contacts.csv")))
    names = [r_[0] for r_ in _all(A, "SELECT name FROM people")]
    assert names == ["Sarah Lopez"], names

def test_an_outlook_export_keeps_city_and_state_out_of_company(env):

    header = ["First Name", "Last Name", "Company", "Business Street", "Business City", "Business State",
              "Business Postal Code", "E-mail Address", "Mobile Phone"]
    g_ = CC.guess(header)
    assert g_ == ["person", "person", "company", "address", "address", "address", "address", "email", "phone"], g_

def test_a_partner_already_here_is_matched_by_name_not_by_the_shared_phone(env):

    A, c = env
    run(A, """INSERT INTO people (id, household_id, name, role, email, phone) VALUES (1, 1, 'John Lane', 'adult',
                'john@example.com', '555-201-3344');
              INSERT INTO people (id, household_id, name, role, email, phone) VALUES (2, 1, 'Jane Lane', 'adult', '', '');""")
    tok = _token(_upload(c, b"Client,Person,Email,Phone\nLane,Jane Lane,jane@example.com,555-201-3344\n"))
    gr = _plan(A, tok)["match"][0]
    assert [o_[2] for o_ in gr["ops"]] == ["Jane Lane's email", "Jane Lane's phone"], gr["ops"]

def test_a_blank_row_does_not_count_as_a_row(env):
    A, c = env
    tok = _token(_upload(c, b"Name,Email\nAva Stone,ava@example.com\n,\nJo Park,jo@example.com\n"))
    assert "2 rows, read as" in _check(A, c, tok)

def test_an_outlook_manager_or_assistant_is_not_joined_into_the_name():
    header = ["First Name", "Last Name", "Manager's Name", "Assistant's Name", "Assistant's Phone", "Mobile Phone"]
    rows = [["Ann", "Lee", "Bob Boss", "Cal Aide", "555-000-1111", "555-222-3333"]]
    p = CC.groups(CC.records(header, rows, CC.guess(header)))[0][0]["people"][0]
    assert (p["name"], p["phone"]) == ("Ann Lee", "555-222-3333"), p

def test_state_and_city_match_only_as_whole_words():
    header = ["Estate notes", "Real estate agent", "Statement", "United States?", "Home City", "Country"]
    assert CC.guess(header) == ["client_notes", "", "", "", "address", "address"], CC.guess(header)

def test_an_outlook_export_takes_only_the_home_address():
    header = ["First Name", "Last Name", "Home Street", "Home City", "Home State", "Business Street", "Business City",
              "Other Country/Region", "Notes"]
    rows = [["Ann", "Lee", "1 Elm St", "Springfield", "IL", "9 Office Rd", "Chicago", "USA", "x"]]
    gr = CC.groups(CC.records(header, rows, CC.guess(header)))[0][0]
    assert (gr["client"]["address"], gr["client"]["notes"]) == ("1 Elm St, Springfield, IL", "x"), gr["client"]

def test_a_google_formatted_address_is_not_repeated():
    header = ["Name", "Address 1 - Type", "Address 1 - Formatted", "Address 1 - Street", "Address 1 - City"]
    rows = [["Ann Lee", "Home", "1 Elm St, Springfield", "1 Elm St", "Springfield"]]
    gr = CC.groups(CC.records(header, rows, CC.guess(header)))[0][0]
    assert gr["client"]["address"] == "1 Elm St, Springfield", gr["client"]

def test_a_duplicated_row_without_a_client_column_adds_one_client(env):
    A, c = env
    _confirm(A, c, _token(_upload(c, b"Name,Notes\nAva Stone,Likes fall\nAva Stone,Likes fall\n")))
    assert one(A, "SELECT COUNT(*) FROM households WHERE name='Ava Stone'")[0] == 1

def test_rows_that_differ_are_not_called_the_same_row():
    header = ["Client", "Person", "Notes"]
    rows = [["Rivera", "Ana Rivera", "a"], ["Rivera", "Ana Rivera", "b"], ["Rivera", "Ana Rivera", "b"]]
    gr = CC.groups(CC.records(header, rows, CC.guess(header)))[0][0]
    assert len(gr["people"]) == 1
    assert gr["notes"] == [(3, "Ana Rivera is also on row 2: added once"), (4, "Same as row 3: left out")], gr["notes"]
