import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, one, run

import client_csv as CC

E = {"REMOTE_ADDR": "127.0.0.1"}

def _all(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _upload(c, data, name="list.csv"):
    r = c.post("/clients/import", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data",
               environ_base=E)
    assert r.status_code == 302
    return r.headers["Location"]

def _token(loc):
    assert "/clients/import/" in loc, loc
    return loc.rstrip("/").split("/clients/import/")[1].split("#")[0]

def _check(A, c, tok):
    r = c.get(f"/clients/import/{tok}", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _plan(A, tok):
    with A.app.test_request_context():
        A.db()
        return A.import_plan(A._IMPORT_DRAFTS[tok])

def _confirm(A, c, tok, picks=None, sig=None):
    if sig is None:
        with A.app.test_request_context():
            sig = A._plan_sig(A.import_plan(A._IMPORT_DRAFTS[tok]))
    data = {"sig": sig}
    data.update(picks or {})
    return c.post(f"/clients/import/{tok}/confirm", data=data, environ_base=E)

def _seed(A):
    run(A, """
        UPDATE households SET name='Lane Family', address='12 Oak St' || char(10) || 'Springfield', notes='Likes  mornings'
          WHERE id=1;
        INSERT INTO people (id, household_id, name, role, relation, email, phone, birth_date, is_minor, notes)
          VALUES (1, 1, 'Robin Lane', 'parent', 'mother', 'robin@example.com', '(555) 201-3344', NULL, 0, '');
        INSERT INTO people (id, household_id, name, role, relation, email, phone, birth_date, is_minor, notes)
          VALUES (2, 1, 'Kit Lane', 'child', 'son', '', '', '2015-04-09', 1, '=HYPERLINK("http://x")');
        INSERT INTO households (id, name, company) VALUES (2, 'Bright Meadow Realty', 'Bright Meadow Realty');
        INSERT INTO people (id, household_id, name, role, email, phone) VALUES (3, 2, 'Sam Ray', 'adult', 'sam@example.com', '+1 555 777 8888');
        INSERT INTO households (id, name) VALUES (3, 'Nobody Yet');""")

APP_HEADER = ["Client number", "Client", "Company", "Address", "Client notes", "Person", "Relationship", "Email",
              "Phone", "Birth date", "Minor", "Person notes"]
APP_KEYS = ["client_number", "client", "company", "address", "client_notes", "person", "relationship", "email", "phone",
            "birth_date", "minor", "person_notes"]
APP_FILE = ("\ufeff" + ",".join(APP_HEADER) + "\r\n"
            '1,Lane Family,,"12 Oak St\nSpringfield",Likes  mornings,Robin Lane,mother,robin@example.com,(555) 201-3344,,,\r\n'
            '1,Lane Family,,"12 Oak St\nSpringfield",Likes  mornings,Kit Lane,son,,,2015-04-09,yes,'
            '"\'=HYPERLINK(""http://x"")"\r\n'
            "2,Bright Meadow Realty,Bright Meadow Realty,,,Sam Ray,,sam@example.com,'+1 555 777 8888,,,\r\n"
            "3,Nobody Yet,,,,,,,,,,\r\n").encode("utf-8")

def test_encodings_are_read_utf8_bom_utf16_and_latin1():
    for raw, enc in (("Name\nJosé\n".encode("utf-8"), "UTF-8"), ("Name\nJosé\n".encode("utf-8-sig"), "UTF-8"),
                     ("Name\nJosé\n".encode("utf-16"), "UTF-16"), ("Name\nJosé\n".encode("cp1252"), "Windows (Latin-1)")):
        header, rows, got = CC.read(raw)
        assert header == ["Name"] and rows == [["José"]] and got == enc

def test_a_file_too_big_binary_empty_or_too_long_is_refused():
    with pytest.raises(CC.ImportProblem, match="larger than"):
        CC.read(b"Name\n" + b"x" * (CC.MAX_BYTES + 1))
    with pytest.raises(CC.ImportProblem, match="doesn't look like a CSV"):
        CC.read(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    with pytest.raises(CC.ImportProblem, match="empty"):
        CC.read(b"\n\n")
    with pytest.raises(CC.ImportProblem, match="nothing under it"):
        CC.read(b"Name,Email\n")
    with pytest.raises(CC.ImportProblem, match="rows"):
        CC.read(("Name\n" + "a\n" * (CC.MAX_ROWS + 1)).encode())
    with pytest.raises(CC.ImportProblem, match="columns"):
        CC.read((",".join(f"c{i}" for i in range(CC.MAX_COLS + 1)) + "\n" + "x\n").encode())

def test_semicolon_and_tab_files_are_read():
    assert CC.read(b"Name;Email\nAva;a@example.com\n")[1] == [["Ava", "a@example.com"]]
    assert CC.read(b"Name\tEmail\nAva\ta@example.com\n")[1] == [["Ava", "a@example.com"]]

def test_the_app_guesses_common_columns():
    got = CC.guess(["First Name", "Last Name", "E-mail Address", "Mobile Phone", "Street", "City", "ZIP", "Notes",
                    "Birthday", "Relationship", "Family", "Favorite color"])
    assert got == ["person", "person", "email", "phone", "address", "address", "address", "client_notes",
                   "birth_date", "relationship", "client", ""]
    assert CC.guess(APP_HEADER) == APP_KEYS

def test_values_are_cleaned_and_the_formula_guard_comes_off():
    assert CC.clean("'=SUM(A1)") == "=SUM(A1)" and CC.clean("'+1 555") == "+1 555"
    assert CC.clean("Ann‮evil\x07  Lee") == "Annevil Lee"
    assert len(CC.clean("x" * 500)) == 200

@pytest.mark.parametrize("typed, iso", [("2015-04-09", "2015-04-09"), ("4/9/2015", "2015-04-09"),
                                        ("04-09-2015", "2015-04-09"), ("2015/4/9", "2015-04-09"),
                                        ("13/45/2015", None), ("soon", None), ("1/1/1850", None)])
def test_birth_dates(typed, iso):
    assert CC.birth_date(typed) == iso

def test_round_trip_the_apps_own_file_rebuilds_the_clients(env):
    A, c = env
    _seed(A)
    data = APP_FILE
    before = _all(A, """SELECT h.name, h.company, h.address, h.notes, p.name, p.relation, p.role, p.email, p.phone,
                        p.birth_date, p.is_minor, p.notes FROM households h LEFT JOIN people p ON p.household_id=h.id
                        ORDER BY h.name, p.name""")
    run(A, "DELETE FROM households")
    tok = _token(_upload(c, data))
    plan = _plan(A, tok)
    assert len(plan["add"]) == 3 and not plan["match"] and not plan["problems"]
    assert _all(A, "SELECT COUNT(*) FROM households")[0][0] == 0
    r = _confirm(A, c, tok)
    assert r.status_code == 302
    after = _all(A, """SELECT h.name, h.company, h.address, h.notes, p.name, p.relation, p.role, p.email, p.phone,
                       p.birth_date, p.is_minor, p.notes FROM households h LEFT JOIN people p ON p.household_id=h.id
                       ORDER BY h.name, p.name""")
    assert [tuple(r_) for r_ in after] == [tuple(r_) for r_ in before]

def test_importing_the_apps_own_file_again_matches_everyone_and_changes_nothing(env):
    A, c = env
    _seed(A)
    data = APP_FILE
    tok = _token(_upload(c, data))
    plan = _plan(A, tok)
    assert not plan["add"] and len(plan["match"]) == 3
    assert all(not g_["ops"] for g_ in plan["match"]), [g_["ops"] for g_ in plan["match"]]
    by_ = {g_["client"]["name"]: g_["by"] for g_ in plan["match"]}
    assert "email" in by_["Lane Family"] and by_["Nobody Yet"] == "client number"
    n = one(A, "SELECT COUNT(*) FROM households")[0]
    _confirm(A, c, tok)
    assert one(A, "SELECT COUNT(*) FROM households")[0] == n

GENERIC = ("First Name,Last Name,Email,Phone,Street,City,Notes,Birthday\n"
           "Ava,Stone,ava@example.com,555-100-2000,1 Elm St,Riverton,Met at the fair,3/14/1990\n"
           "Robin,Lane,ROBIN@example.com,,,,,\n"
           ",,,555-000-1111,,,,\n"
           "Jo,Park,not-an-email,,,,,someday\n").encode()

def test_a_simple_list_previews_adds_matches_and_problems_and_writes_nothing_yet(env):
    A, c = env
    _seed(A)
    n_aud = one(A, "SELECT COUNT(*) FROM audit")[0]
    tok = _token(_upload(c, GENERIC))
    page = _check(A, c, tok)
    assert "Ready to import" in page and "Import 2 clients" in page and "matches Lane Family by email" in page
    assert "Row 4" in page and "No name" in page and "Row 5" in page
    plan = _plan(A, tok)
    assert [g_["client"]["name"] for g_ in plan["add"]] == ["Ava Stone", "Jo Park"]
    ava = plan["add"][0]
    assert ava["client"]["address"] == "1 Elm St, Riverton" and ava["people"][0]["birth_date"] == "1990-03-14"
    assert plan["add"][1]["people"][0]["email"] == ""
    assert any("doesn't look like an email" in w for _r, w in plan["notes"])
    assert any("could not be read" in w for _r, w in plan["notes"])
    assert one(A, "SELECT COUNT(*) FROM households")[0] == 3 and one(A, "SELECT COUNT(*) FROM audit")[0] == n_aud

def test_changing_a_column_updates_the_preview(env):
    A, c = env
    tok = _token(_upload(c, b"Who,Mail\nAva Stone,ava@example.com\n"))
    first = _plan(A, tok)
    assert not first["add"] and "No name" in first["problems"][0][1]
    c.post(f"/clients/import/{tok}/columns", data={"col0": "person", "col1": ""}, environ_base=E)
    assert _plan(A, tok)["add"][0]["people"][0]["email"] == ""
    c.post(f"/clients/import/{tok}/columns", data={"col0": "person", "col1": "email", "col2": "bogus"}, environ_base=E)
    assert _plan(A, tok)["add"][0]["people"][0]["email"] == "ava@example.com"
    c.post(f"/clients/import/{tok}/columns", data={"col0": "drop table", "col1": "email"}, environ_base=E)
    assert A._IMPORT_DRAFTS[tok]["mapping"][0] == ""

def test_skip_is_the_default_and_leaves_a_match_alone(env):
    A, c = env
    _seed(A)
    tok = _token(_upload(c, b"Name,Email,Phone\nRobin Lane,robin@example.com,555-999-0000\n"))
    plan = _plan(A, tok)
    assert len(plan["match"]) == 1 and plan["match"][0]["ops"]
    r = _confirm(A, c, tok)
    assert r.status_code == 302
    assert one(A, "SELECT phone FROM people WHERE id=1")[0] == "(555) 201-3344"

def test_update_changes_only_what_the_preview_said_and_says_so_in_that_clients_history(env):
    A, c = env
    _seed(A)
    tok = _token(_upload(c, b"Name,Email,Phone,Notes\nRobin Lane,robin@example.com,555-999-0000,Prefers texts\n"
                            b"Dana Fox,dana@example.com,,\n"))
    plan = _plan(A, tok)
    gid = plan["match"][0]["id"]
    _confirm(A, c, tok, picks={"pick_" + gid: "update"})
    assert one(A, "SELECT phone FROM people WHERE id=1")[0] == "555-999-0000"
    assert one(A, "SELECT name FROM households WHERE id=1")[0] == "Lane Family"
    assert "Likes  mornings" in one(A, "SELECT notes FROM households WHERE id=1")[0]
    assert "Prefers texts" in one(A, "SELECT notes FROM households WHERE id=1")[0]
    assert one(A, "SELECT COUNT(*) FROM households WHERE name='Dana Fox'")[0] == 1
    dana = one(A, "SELECT id FROM households WHERE name='Dana Fox'")[0]
    assert [tuple(r_) for r_ in _all(A, "SELECT action, note FROM audit WHERE household_id=?", (dana,))] == \
        [("Added from an imported file", "list.csv")]
    with A.app.test_request_context():
        A.db()
        assert any(r_["text"] == "Added from an imported file" for r_ in A.client_history(dana))
    hist = [r_[0] for r_ in _all(A, "SELECT action FROM audit WHERE household_id=1")]
    assert any(h_.startswith("Updated from an imported file") and "Robin Lane's phone" in h_ for h_ in hist)
    studio = _all(A, "SELECT action, note FROM audit WHERE action LIKE 'Imported clients%'")
    assert len(studio) == 1 and studio[0][0] == "Imported clients from a file: 1 added, 1 updated" and studio[0][1] == "list.csv"

def test_a_row_matching_two_clients_or_an_earlier_row_is_a_problem_not_a_merge(env):
    A, c = env
    _seed(A)
    tok = _token(_upload(c, b"Name,Email,Phone\nMix Up,robin@example.com,+1 555 777 8888\n"
                            b"New One,new@example.com,\nNew Two,new@example.com,\n"))
    plan = _plan(A, tok)
    words = " ".join(w for _r, w in plan["problems"])
    assert "matches 2 clients" in words and "same email or phone as row 3" in words
    assert [g_["client"]["name"] for g_ in plan["add"]] == ["New One"]

def test_a_stale_preview_writes_nothing(env):
    A, c = env
    tok = _token(_upload(c, b"Name,Email\nAva Stone,ava@example.com\n"))
    sig = None
    with A.app.test_request_context():
        A.db()
        sig = A._plan_sig(A.import_plan(A._IMPORT_DRAFTS[tok]))
    run(A, "INSERT INTO households (id, name) VALUES (50, 'Ava Stone'); "
           "INSERT INTO people (household_id, name, email) VALUES (50, 'Ava Stone', 'ava@example.com');")
    _confirm(A, c, tok, sig=sig)
    assert one(A, "SELECT COUNT(*) FROM households WHERE name='Ava Stone'")[0] == 1

def test_cancel_forgets_the_file_and_a_gone_token_says_so(env):
    A, c = env
    tok = _token(_upload(c, b"Name\nAva Stone\n"))
    c.post(f"/clients/import/{tok}/cancel", environ_base=E)
    assert tok not in A._IMPORT_DRAFTS
    r = c.get(f"/clients/import/{tok}", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/clients/import")

def test_a_bad_file_is_refused_with_its_reason(env):
    A, c = env
    r = c.post("/clients/import", data={"file": (io.BytesIO(b"\x00\x01\x02"), "x.csv")},
               content_type="multipart/form-data", environ_base=E)
    assert r.headers["Location"].endswith("/clients/import")
    assert "look like a CSV" in c.get("/clients/import", environ_base=E).get_data(as_text=True)
    assert not A._IMPORT_DRAFTS

def test_a_formula_imported_is_kept_as_text(env):
    A, c = env
    tok = _token(_upload(c, b'Name,Notes\n"=cmd|\' /C calc\'!A0",-2+3\n'))
    _confirm(A, c, tok)
    assert one(A, "SELECT COUNT(*) FROM households WHERE name LIKE '=cmd%' AND notes='-2+3'")[0] == 1

def test_remove_this_import_takes_back_untouched_clients_and_keeps_worked_ones(env):
    A, c = env
    tok = _token(_upload(c, b"Name,Email\nAva Stone,ava@example.com\nJo Park,jo@example.com\n"))
    _confirm(A, c, tok)
    ava = one(A, "SELECT id FROM households WHERE name='Ava Stone'")[0]
    jo = one(A, "SELECT id FROM households WHERE name='Jo Park'")[0]
    run(A, "INSERT INTO log (household_id, summary) VALUES (?, 'worked with')", (jo,))
    page = c.get("/clients/import", environ_base=E).get_data(as_text=True)
    assert "Remove this import" in page and "list.csv" in page
    bid = A.json.loads(one(A, "SELECT value FROM settings WHERE key='client_imports'")[0])[-1]["id"]
    c.post(f"/clients/import/batch/{bid}/remove", environ_base=E)
    assert not one(A, "SELECT 1 FROM households WHERE id=?", (ava,))
    assert one(A, "SELECT 1 FROM households WHERE id=?", (jo,))
    assert one(A, "SELECT COUNT(*) FROM people WHERE household_id=?", (ava,))[0] == 0
    assert one(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Removed an import: 1 client removed, 1 kept%'")[0] == 1
    assert "Remove this import" not in c.get("/clients/import", environ_base=E).get_data(as_text=True)
    c.post(f"/clients/import/batch/{bid}/remove", environ_base=E)
    assert one(A, "SELECT 1 FROM households WHERE id=?", (jo,))

def test_clients_offers_the_import_and_settings_does_not(env):
    A, c = env
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert "Import clients from a file" in page
    assert "Import clients from a file" not in c.get("/settings", environ_base=E).get_data(as_text=True)
    assert c.get("/clients/import", environ_base=E).status_code == 200
