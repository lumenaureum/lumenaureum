import json

from test_studio_page import E, env, setting, switch_on
from test_studio_site_release import php_site, upload
from test_w15_today import _item, _review, _today, q as _q

def q(A, sql, args=()):
    return [tuple(r_) for r_ in _q(A, sql, args)]

def _client(A, hid, name, person, email):
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (?, ?)", (hid, name))
        con.execute("INSERT INTO people (household_id, name, role, email) VALUES (?, ?, 'parent', ?)", (hid, person, email))
        con.commit()
    finally:
        con.close()

def _file(c, rid, client, **extra):
    data = {"do": "add_existing", "id": rid, "client": client, "type": "family", "back": "/", "contact_asked": "1"}
    data.update(extra)
    return c.post("/settings/studio-page/inquiries/review", data=data, environ_base=E)

def _page(c, hid, sid):
    return c.get(f"/c/{hid}?s={sid}", environ_base=E).get_data(as_text=True)

def _sid(A, hid):
    return q(A, "SELECT id FROM sessions WHERE household_id=? ORDER BY id DESC", (hid,))[0][0]

def test_filed_under_an_existing_client_the_message_keeps_its_real_sender(env):
    A, c, S, tmp, web = env
    _client(A, 51, "Grant Family", "Morgan Grant", "grant@example.org")
    ids = _review(A, c, S, web, dict(name="Riley Carter", email="riley@example.org", phone="555-0142",
                                     message="Could we book an autumn shoot?"))
    item = _item(_today(c), ids["Riley Carter"])
    assert 'name="add_contact" value="1" checked' in item and "Add Riley Carter to this client as a contact" in item
    r = _file(c, ids["Riley Carter"], "Grant Family #51", add_contact="1")
    assert r.status_code == 302
    sid = _sid(A, 51)
    page = _page(c, 51, sid)
    msg = page[page.index('class="ui-msg"'):]
    msg = msg[:msg.index("</div>")]
    assert "<b>Riley Carter</b>" in msg and "Morgan Grant" not in msg and "from your website" in msg

    assert q(A, "SELECT email, phone FROM people WHERE household_id=51 AND name='Riley Carter'") == [("riley@example.org", "555-0142")]
    hist = [r_[0] for r_ in q(A, "SELECT action FROM audit WHERE household_id=51")]
    assert "Inquiry from Riley Carter, filed under Grant Family" in hist
    assert "Added Riley Carter to Grant Family as a contact" in hist
    assert "Inquiry from Riley Carter, filed under Grant Family" in page

    assert "mailto:riley@example.org" in page and "mailto:grant@example.org?subject=Re" not in page

def test_unticked_no_contact_is_added_and_a_known_sender_is_never_added_twice(env):
    A, c, S, tmp, web = env
    _client(A, 52, "Ellis Family", "Jo Ellis", "jo@example.org")
    ids = _review(A, c, S, web, dict(name="Sam Other", email="sam@example.org"), dict(name="Jo Ellis", email="jo@example.org"))
    _file(c, ids["Sam Other"], "Ellis Family #52")
    assert not q(A, "SELECT 1 FROM people WHERE name='Sam Other'")
    page = _page(c, 52, _sid(A, 52))
    assert "<b>Sam Other</b>" in page
    _file(c, ids["Jo Ellis"], "Ellis Family #52", add_contact="1")
    assert q(A, "SELECT COUNT(*) FROM people WHERE household_id=52")[0][0] == 1

    ids = _review(A, c, S, web, dict(name="Jo Ellis", email="jo@example.org", message="Again?"))
    item = _item(_today(c), ids["Jo Ellis"])
    assert "Add to Ellis Family</button>" in item and "Add Jo Ellis to Ellis Family as a contact" not in item

def test_move_to_another_client_takes_the_session_message_and_contact_with_history_on_both(env):
    A, c, S, tmp, web = env
    _client(A, 61, "Carter Family", "Grant Carter", "grant@example.org")
    _client(A, 62, "Blake Family", "Avery Blake", "avery@example.org")
    ids = _review(A, c, S, web, dict(name="Riley Blake", email="riley.b@example.org", message="Spring please"))
    _file(c, ids["Riley Blake"], "Carter Family #61", add_contact="1")
    sid = _sid(A, 61)
    page = _page(c, 61, sid)
    assert "Filed under the wrong client?" in page and ">Move to another client</a>" in page and ">Make it a new client</button>" in page
    r = c.post(f"/session/{sid}/refile", data={"do": "move", "client": "Blake Family #62"}, environ_base=E)
    assert r.status_code == 302 and f"/c/62" in r.headers["Location"]
    assert q(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0] == 62
    assert q(A, "SELECT household_id FROM log WHERE session_id=?", (sid,)) == [(62,)]
    assert q(A, "SELECT household_id FROM people WHERE name='Riley Blake'") == [(62,)]
    assert not q(A, "SELECT 1 FROM people WHERE household_id=61 AND name='Riley Blake'")
    old = [r_[0] for r_ in q(A, "SELECT action FROM audit WHERE household_id=61")]
    new = [r_[0] for r_ in q(A, "SELECT action FROM audit WHERE household_id=62")]
    assert any(a_.startswith("Inquiry from Riley Blake moved to Blake Family") for a_ in old)
    assert "Inquiry from Riley Blake moved here from Carter Family" in new
    assert not any("filed under Carter Family" in a_ for a_ in old)
    assert "<b>Riley Blake</b>" in _page(c, 62, sid)
    assert not q(A, "SELECT 1 FROM sessions WHERE household_id=61")

def test_two_unrelated_people_with_the_same_surname_make_it_a_new_client(env):

    A, c, S, tmp, web = env
    _client(A, 71, "Carter", "Grant Carter", "grant@example.org")
    ids = _review(A, c, S, web, dict(name="Riley Carter", email="riley@example.org", phone="555-0199"))

    _file(c, ids["Riley Carter"], "Carter", add_contact="1")
    sid = _sid(A, 71)
    r = c.post(f"/session/{sid}/refile", data={"do": "new"}, environ_base=E)
    assert r.status_code == 302
    hid = q(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0]
    assert hid != 71 and q(A, "SELECT name FROM households WHERE id=?", (hid,)) == [("Riley Carter",)]
    assert q(A, "SELECT name, email, phone FROM people WHERE household_id=?", (hid,)) == [("Riley Carter", "riley@example.org", "555-0199")]
    assert q(A, "SELECT name FROM people WHERE household_id=71") == [("Grant Carter",)]
    assert q(A, "SELECT person_id FROM session_people WHERE session_id=?", (sid,)) == q(A, "SELECT id FROM people WHERE household_id=?", (hid,))
    assert any(a_[0].startswith("Inquiry from Riley Carter moved to a new client") for a_ in q(A, "SELECT action FROM audit WHERE household_id=71"))
    assert any("moved here from Carter as a new client" in a_[0] for a_ in q(A, "SELECT action FROM audit WHERE household_id=?", (hid,)))

    assert "Filed under the wrong client?" not in _page(c, hid, sid)
    assert c.post(f"/session/{sid}/refile", data={"do": "new"}, environ_base=E).status_code == 404

def test_refused_with_the_reason_once_something_has_gone_out_or_it_is_booked(env):
    A, c, S, tmp, web = env
    _client(A, 81, "Grant Family", "Morgan Grant", "grant@example.org")
    ids = _review(A, c, S, web, dict(name="Riley Carter", email="riley@example.org"))
    _file(c, ids["Riley Carter"], "Grant Family #81", add_contact="1")
    sid = _sid(A, 81)
    con = A.connect()
    try:
        con.execute("INSERT INTO payments (session_id, kind, amount, received, method) VALUES (?, 'deposit', 50, '2026-11-18', 'cash')", (sid,))
        con.commit()
    finally:
        con.close()
    page = _page(c, 81, sid)
    assert "It can't move now: a payment is recorded on it." in page and ">Move to another client</a>" not in page
    c.post(f"/session/{sid}/refile", data={"do": "new"}, environ_base=E)
    assert q(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0] == 81
    con = A.connect()
    try:
        con.execute("DELETE FROM payments WHERE session_id=?", (sid,))
        con.execute("UPDATE sessions SET stage='booked' WHERE id=?", (sid,))
        con.commit()
    finally:
        con.close()
    assert "It can't move now: it is Booked now" in _page(c, 81, sid)
    c.post(f"/session/{sid}/refile", data={"do": "move", "client": "Grant Family #81"}, environ_base=E)
    assert q(A, "SELECT household_id FROM sessions WHERE id=?", (sid,))[0][0] == 81

def test_the_filing_columns_are_added_once_and_older_filings_change_nothing(env):
    A, c, S, tmp, web = env
    for fn in A.UNLOCK_HOOKS + A.AFTER_UNLOCK_HOOKS:
        if getattr(fn, "__name__", "") == "_review_cols":
            fn()
            fn()
    cols = [r[1] for r in q(A, "PRAGMA table_info(studio_inquiries)")]
    assert all(cols.count(k_) == 1 for k_ in ("sender", "contact_id", "log_id", "filed"))

    _client(A, 91, "Old Family", "Pat Old", "pat@example.org")
    con = A.connect()
    try:
        con.execute("INSERT INTO sessions (id, household_id, type, stage) VALUES (9101, 91, 'family', 'inquiry')")
        con.execute("INSERT INTO studio_inquiries (sha256, session_id, household_id, received, outcome) VALUES ('x', 9101, 91, '2026-01-01', 'added')")
        con.commit()
    finally:
        con.close()
    assert "Filed under the wrong client?" not in _page(c, 91, 9101)
    assert json.loads(q(A, "SELECT COALESCE(sender, '{}') FROM studio_inquiries WHERE sha256='x'")[0][0]) == {}
