import os
import re
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def _app(tmp_path, monkeypatch, presets=("portraits", "families", "events")):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"), ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    tmp_path.mkdir(parents=True, exist_ok=True)
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in presets}, environ_base=E)
    return A, c

def _family(A, stages=(("portrait", "booked"),)):

    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Okafor')").lastrowid
    mom = con.execute("INSERT INTO people (household_id, name, role, relation, email) VALUES (?, 'Ngozi Okafor', 'parent', 'mother', 'n@example.com')",
                      (hid,)).lastrowid
    ada = con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (?, 'Ada Okafor', 'child', 1)", (hid,)).lastrowid
    con.commit()
    con.close()
    sids = []
    shoot = (date.today() + timedelta(days=30)).isoformat()
    with A.app.test_request_context():
        A.db()
        for type_, stage in stages:
            sid = A.create_session(hid, type_, stage, "", shoot, subject_ids=[ada])
            A.db().commit()
            sids.append(sid)
    return hid, mom, ada, sids, shoot

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def test_adding_a_person_offers_the_open_session_and_adds_them_with_a_release(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, (sid,), shoot = _family(A)
    r = c.post(f"/client/{hid}/person", data={"name": "Chidi Okafor", "relation": "son", "age": "9"}, environ_base=E)
    assert r.status_code == 302 and "added=" in r.headers["Location"]
    chidi = _q(A, "SELECT id FROM people WHERE name='Chidi Okafor'")[0][0]
    page = c.get(f"/client/{hid}?added={chidi}", environ_base=E).data.decode()
    with A.app.test_request_context():
        when = A.nice(shoot)
    assert f"Add Chidi to the Portraits session on {when} too?" in page
    assert f'action="/session/{sid}/subject"' in page and "Not this time" in page
    r = c.post(f"/session/{sid}/subject", data={"person_id": str(chidi), "back": f"/client/{hid}?added={chidi}#people"},
               environ_base=E)
    assert r.status_code == 302
    assert _q(A, "SELECT 1 FROM session_people WHERE session_id=? AND person_id=?", (sid, chidi))
    rel = _q(A, "SELECT signer FROM documents WHERE session_id=? AND kind='release' AND about_person=?", (sid, chidi))
    assert rel == [("Parent or guardian: Ngozi Okafor",)]
    page = c.get(f"/client/{hid}?added={chidi}", environ_base=E).data.decode()
    assert "Chidi Okafor is now in the Portraits session" in page
    assert "too?" not in page
    assert _q(A, "SELECT 1 FROM audit WHERE action LIKE 'Added Chidi Okafor to the session%'")
    c.post(f"/session/{sid}/subject", data={"person_id": str(chidi)}, environ_base=E)
    assert len(_q(A, "SELECT 1 FROM documents WHERE session_id=? AND about_person=?", (sid, chidi))) == 1

def test_no_offer_without_an_open_session_and_one_button_each_when_several(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, sids, _ = _family(A, stages=(("portrait", "closed"), ("portrait", "lost")))
    c.post(f"/client/{hid}/person", data={"name": "Chidi Okafor", "relation": "son", "age": "9"}, environ_base=E)
    chidi = _q(A, "SELECT id FROM people WHERE name='Chidi Okafor'")[0][0]
    page = c.get(f"/client/{hid}?added={chidi}", environ_base=E).data.decode()
    assert "too?" not in page and "/subject" not in page

    A, c = _app(tmp_path / "two", monkeypatch)
    hid, mom, ada, sids, _ = _family(A, stages=(("portrait", "inquiry"), ("family", "booked"), ("portrait", "closed")))
    c.post(f"/client/{hid}/person", data={"name": "Chidi Okafor", "relation": "son", "age": "9"}, environ_base=E)
    chidi = _q(A, "SELECT id FROM people WHERE name='Chidi Okafor'")[0][0]
    page = c.get(f"/client/{hid}?added={chidi}", environ_base=E).data.decode()
    assert "Add Chidi to an open session too?" in page
    assert f'action="/session/{sids[0]}/subject"' in page and f'action="/session/{sids[1]}/subject"' in page
    assert f'action="/session/{sids[2]}/subject"' not in page

def test_offer_only_for_people_on_the_sessions_own_client(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, (sid,), _ = _family(A)
    con = A.connect()
    other = con.execute("INSERT INTO households (name) VALUES ('Reyes')").lastrowid
    stranger = con.execute("INSERT INTO people (household_id, name) VALUES (?, 'Sam Reyes')", (other,)).lastrowid
    con.commit()
    con.close()
    assert c.post(f"/session/{sid}/subject", data={"person_id": str(stranger)}, environ_base=E).status_code == 400
    assert not _q(A, "SELECT 1 FROM session_people WHERE person_id=?", (stranger,))
    assert "too?" not in c.get(f"/client/{hid}?added={stranger}", environ_base=E).data.decode()

def test_ticking_a_subject_on_the_session_page_adds_their_release(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, (sid,), _ = _family(A)
    con = A.connect()
    kid = con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (?, 'Ben Okafor', 'child', 1)", (hid,)).lastrowid
    con.commit()
    con.close()
    page = c.get(f"/session/{sid}", environ_base=E).data.decode()
    seen = re.search(r'name="seen" value="([^"]*)"', page).group(1)
    c.post(f"/session/{sid}", data={"seen": seen, "title": "", "type": "portrait", "subjects_present": "1",
                                    "subjects": [str(ada), str(kid)]}, environ_base=E)
    assert {r[0] for r in _q(A, "SELECT person_id FROM session_people WHERE session_id=?", (sid,))} == {ada, kid}
    assert _q(A, "SELECT signer FROM documents WHERE session_id=? AND kind='release' AND about_person=?", (sid, kid)) \
        == [("Parent or guardian: Ngozi Okafor",)]
    page = c.get(f"/session/{sid}", environ_base=E).data.decode()
    seen = re.search(r'name="seen" value="([^"]*)"', page).group(1)
    c.post(f"/session/{sid}", data={"seen": seen, "title": "", "type": "portrait", "subjects_present": "1",
                                    "subjects": [str(kid)]}, environ_base=E)
    assert [r[0] for r in _q(A, "SELECT person_id FROM session_people WHERE session_id=?", (sid,))] == [kid]

def test_custom_quote_line_has_a_quantity_box(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, _, _ = _family(A, stages=(("portrait", "inquiry"),))
    sid = _q(A, "SELECT id FROM sessions WHERE household_id=?", (hid,))[0][0]
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "custom", "label": "Prints", "amount": "20", "qty": "2"}, environ_base=E)
    lid, label, amt = _q(A, "SELECT id, label, amount FROM lines WHERE session_id=? AND kind='custom'", (sid,))[0]
    assert (label, amt) == ("Prints x 2", 40)
    page = c.get(f"/c/{hid}?s={sid}", environ_base=E).data.decode()
    assert f'action="/line/{lid}/qty"' in page
    c.post(f"/line/{lid}/qty", data={"qty": "3"}, environ_base=E)
    assert _q(A, "SELECT label, amount FROM lines WHERE id=?", (lid,))[0] == ("Prints x 3", 60)

def test_cancellation_rows_name_what_they_cover(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("portraits", "events"))
    import config as C
    heads = [h for h, _ in C.build_terms()]
    assert heads.count("Cancellation") == 0
    canc = [h for h in heads if h.startswith("Cancellation")]
    assert canc == ["Cancellation: Portraits", "Cancellation: Group events"]
    assert all(not t.startswith(("Portraits:", "Group events:")) for _, t in C.build_terms())
    assert dict(C.build_terms("portrait"))["Cancellation"]
    page = c.get("/fees/sheet", environ_base=E)
    assert page.status_code == 200 and "<b>Cancellation</b><br>" in page.data.decode()

    A, c = _app(tmp_path / "same", monkeypatch, presets=("portraits", "families"))
    import config as C
    heads = [h for h, _ in C.build_terms()]
    assert heads.count("Cancellation") == 1 and heads.count("Running late") == 1

def test_review_status_photo_license_row_follows_the_types(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("portraits",))
    page = c.get("/settings?tab=documents", environ_base=E).data.decode()
    assert "<td>Photo license</td>" in page
    with A.app.test_request_context():
        A.db(); A._save_rules(LICENSE_TYPES=[])
    page = c.get("/settings?tab=documents", environ_base=E).data.decode()
    assert "<td>Photo license</td>" not in page and "<td>Copyright transfer</td>" in page

def test_timeline_one_mark_per_day_with_a_count(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, (sid,), _ = _family(A)
    day = (date.today() - timedelta(days=3)).isoformat()
    con = A.connect()
    for i in range(4):
        con.execute("INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (?,?,?,?,?)",
                    (hid, sid, "email", f"Note {i}", day + " 10:00:00"))
    con.commit()
    con.close()
    page = c.get(f"/c/{hid}?s={sid}", environ_base=E).data.decode()
    past = page.split('<div class="j-past">', 1)[1].split('<div class="j-axis">', 1)[0]
    with A.app.test_request_context():
        nd = A.nice(day)
    assert past.count(f'data-tip="{nd}') == 1
    assert '<b class="jn" aria-hidden="true">4</b>' in past
    assert all(f"Note {i}" in past for i in range(4))
    with A.app.test_request_context():
        A.db()
        marks = A._same_day([dict(d=day, label="a", sub="", tip=f"{nd}: a"), dict(d=day, label="b", sub="", show=True, tip=f"{nd}: b")],
                            future=False)
    assert len(marks) == 1 and marks[0]["n"] == 2 and marks[0]["show"] and marks[0]["tip"].endswith("b\na")

def test_avatar_initials_are_the_client_contacts(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    hid, mom, ada, _, _ = _family(A)
    with A.app.test_request_context():
        A.db()
        assert A._face(hid)["initials"] == "NO"
    con = A.connect()
    kids = con.execute("INSERT INTO households (name) VALUES ('Lee')").lastrowid
    con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (?, 'Mia Lee', 'child', 1)", (kids,))
    con.commit()
    con.close()
    with A.app.test_request_context():
        A.db()
        assert A._face(kids)["initials"] == "ML"
