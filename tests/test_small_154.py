import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER, TOPIC = "https://ntfy.example.net", "studio-k3j9x2q7"
CFG = dict(server=SERVER, topic=TOPIC, token="")
T0 = datetime(2026, 11, 21, 9, 0, 0)

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "s.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('backup_notice', '1')")
    con.commit()
    con.close()
    return lambda: sqlite3.connect(p, isolation_level=None)

@pytest.fixture
def sent(monkeypatch):
    import inboxnotice
    out = []
    monkeypatch.setattr(inboxnotice.locknotice, "_post", lambda url, body, headers: out.append((body.decode(), dict(headers))))
    return out

def test_a_backup_problem_is_told_once_then_daily_and_cleared_when_fine(db, sent):
    import inboxnotice as N
    assert N.backup_tick(db, CFG, "", T0) is None and not sent
    assert N.backup_tick(db, CFG, "failed", T0) == "sent"
    body, headers = sent[-1]
    assert headers["Title"] == "Studio backup" and body == "The last backup failed. Sign in to your studio to see why."
    assert not {"Click", "Actions", "Attach"} & set(headers)
    assert N.backup_tick(db, CFG, "failed", T0 + timedelta(hours=5)) is None and len(sent) == 1
    assert N.backup_tick(db, CFG, "failed", T0 + timedelta(hours=25)) == "sent" and len(sent) == 2
    assert N.backup_tick(db, CFG, "", T0 + timedelta(hours=26)) is None
    assert N.backup_tick(db, CFG, "late", T0 + timedelta(hours=27)) == "sent"
    assert sent[-1][0] == "No backup has been made in over a day. Sign in to your studio to check backups."

def test_the_backup_notice_is_off_by_default_and_never_while_locked(db, sent):
    import inboxnotice as N
    con = db()
    con.execute("UPDATE settings SET value='0' WHERE key='backup_notice'")
    con.close()
    assert N.backup_tick(db, CFG, "failed", T0) is None and not sent
    con = db()
    con.execute("UPDATE settings SET value='1' WHERE key='backup_notice'")
    con.close()
    asked = []
    N.step(lambda: False, db, lambda: CFG, T0, backup_problem=lambda: asked.append(1) or "failed")
    assert not sent and not asked
    N.step(lambda: True, db, lambda: CFG, T0, backup_problem=lambda: "failed")
    assert [s[1]["Title"] for s in sent] == ["Studio backup"]

def test_the_backup_switch_sits_with_the_notices_and_the_app_reads_a_failed_backup(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    for k, v in (("ntfy_server", SERVER), ("ntfy_topic", TOPIC), ("ntfy_token", "")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit()
    con.close()
    page = lambda: e.c.get("/settings?tab=notifications", environ_base=PE.E).get_data(as_text=True)
    assert 'name="backup_on" value="1" data-autosubmit>A backup failed or is late' in page()
    e.c.post("/security/notice", data=dict(act="backup", backup_on="1"), environ_base=PE.E)
    assert 'data-autosubmit checked>A backup failed or is late' in page()
    con = e.A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('backup_last_error', 'sftp to nas.local: refused')")
    con.commit()
    con.close()
    with e.A.app.app_context():
        assert e.A.backup_problem() == "failed"
    acts = [r["action"] for r in PE.q(e, "SELECT action FROM audit WHERE action LIKE '%backup notice%'")]
    assert acts == ["Turned on the backup notice"]

def _card(e, PE):
    html = e.c.get("/galleries", environ_base=PE.E).get_data(as_text=True)
    (card,) = re.findall(r'<li class="ui-cover".*?</li>', html, re.S)
    return card

def test_upload_is_in_the_card_menu_grayed_out_while_online(tmp_path, monkeypatch):
    from test_fixes_141 import _served_env
    e, PE = _served_env(tmp_path, monkeypatch)
    card = _card(e, PE)
    assert re.search(r'<button type="button" class="ui-menu-item" disabled>Upload<small[^>]*>Already online', card)
    items = re.findall(r'class="ui-menu-item"[^>]*>([A-Z][a-z][^<]*)', card)
    assert items[:3] == ["Show it on the client page", "Upload", "Close now"]

def test_upload_from_the_card_menu_comes_back_to_galleries(tmp_path, monkeypatch):
    from test_fixes_141 import _served_env
    e, PE = _served_env(tmp_path, monkeypatch)
    e.c.post("/session/1/gallery/close-now", data={"back": "/galleries"}, environ_base=PE.E)
    con = e.A.connect()
    con.execute("UPDATE galleries SET upload_state='', removed_at=NULL WHERE session_id=1")
    con.commit()
    con.close()
    card = _card(e, PE)
    m = re.search(r'<form method="post" action="([^"]+)"><input type="hidden" name="back" value="/galleries"><button class="ui-menu-item">Upload</button>', card)
    assert m, card
    r = e.c.post(m.group(1), data={"back": "/galleries"}, environ_base=PE.E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/galleries")

def test_new_inquiry_says_where_to_add_a_session_type(tmp_path, monkeypatch):
    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    page = e.c.get("/inquiry?hid=1", environ_base=PE.E).get_data(as_text=True)
    assert 'Not listed? <a class="ui-go" href="/setup/shoot#types">Add a kind of shoot in Settings, What you shoot</a>' in page
    assert e.c.get("/setup/shoot", environ_base=PE.E).status_code == 200

def test_the_menu_and_the_cards_line_agree_on_upload(tmp_path, monkeypatch):

    from test_fixes_141 import _served_env
    e, PE = _served_env(tmp_path, monkeypatch)
    e.c.post("/session/1/gallery/close-now", data={"back": "/galleries"}, environ_base=PE.E)
    con = e.A.connect()
    con.execute("UPDATE galleries SET upload_state='' WHERE session_id=1")
    con.commit()
    con.close()
    card = _card(e, PE)
    assert "Ready to upload" in card
    assert '<button class="ui-menu-item">Upload</button>' in card
def test_a_saved_persons_save_button_waits_for_a_change(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    page = e.c.get("/client/1", environ_base=PE.E).get_data(as_text=True)
    assert re.search(r'<button class="ui-btn ui-btn-primary" data-ui-unchanged="No changes to save">Save [^<]+</button>', page)
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "ui.js")).read()
    assert "button[data-ui-unchanged]" in js and "b.disabled = true" in js

def test_people_on_a_shoot_are_listed_by_name(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    ids = []
    for n in ("Zoe Lane", "ava Lane"):
        ids.append(con.execute("INSERT INTO people (household_id, name, role) VALUES (1, ?, 'child')", (n,)).lastrowid)
    for pid in ids:
        con.execute("INSERT INTO session_people (session_id, person_id) VALUES (1, ?)", (pid,))
    con.commit()
    con.close()
    with e.A.app.test_request_context():
        e.A.db()
        names = e.A.subjects_of(1)
    assert names.index("ava Lane") < names.index("Zoe Lane")
    page = e.c.get("/session/1/edit", environ_base=PE.E).get_data(as_text=True)
    assert page.index("ava Lane") < page.index("Zoe Lane")

def test_adding_a_person_is_in_place_and_its_question_sits_under_the_list(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    r = e.c.post("/client/1/person", data={"name": "Nora Aloe"}, environ_base=PE.E, follow_redirects=True)
    page = r.get_data(as_text=True) if r.status_code == 200 else e.c.get("/client/1", environ_base=PE.E).get_data(as_text=True)
    assert 'id="pnew" data-ui-inplace data-ui-done-close="person-new"' in page
    assert re.search(r'<section class="ui-sec [^"]*" id="people" data-ui-part>', page)
    page = e.c.get("/client/1?added=" + str(PE.q(e, "SELECT id FROM people WHERE name='Nora Aloe'")[0]["id"]),
                   environ_base=PE.E).get_data(as_text=True)
    assert 'id="offer-q"' in page and page.index("Nora Aloe") < page.index('id="offer-q"') < page.index("+ Add a person")

def test_relationship_fields_offer_only_the_apps_list():

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    found = []
    for n in os.listdir(root):
        found += re.findall(r'<input[^>]*list="rels"[^>]*>', open(os.path.join(root, n)).read())
    assert len(found) >= 3 and all('autocomplete="off"' in f for f in found)

def test_relationships_in_use_extend_the_list_and_a_fixed_typo_drops_out(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("INSERT INTO people (household_id, name, relation) VALUES (1, 'Nora Aloe', 'Granddaughter ')")
    pid = con.execute("INSERT INTO people (household_id, name, relation) VALUES (1, 'Sam Aloe', 'sun')").lastrowid
    con.commit()
    opts = lambda: re.findall(r'<option value="([^"]+)">', re.search(r'<datalist id="rels">(.*?)</datalist>',
                              e.c.get("/client/1", environ_base=PE.E).get_data(as_text=True), re.S).group(1))
    o = opts()
    assert o[:4] == ["mother", "father", "daughter", "son"] and "granddaughter" in o and "sun" in o
    assert o.index("granddaughter") > o.index("other")
    con.execute("UPDATE people SET relation='son' WHERE id=?", (pid,))
    con.commit()
    con.close()
    o = opts()
    assert "sun" not in o and o.count("son") == 1 and "granddaughter" in o

def test_the_clients_list_shows_each_clients_picture(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Okafor and Reyes')")
    con.commit()
    con.close()
    page = e.c.get("/clients", environ_base=PE.E).get_data(as_text=True)
    row = re.search(r'<span class="ui-roster-who"><span aria-hidden="true">(.*?)</span><a class="ui-roster-name"[^>]*>Okafor and Reyes</a>', page, re.S)
    assert row and '<span class="av"' in row.group(1) and ">OR</span>" in row.group(1)
    assert 'loading="lazy"' in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "_macros.html")).read()
def test_labels_are_asked_for_in_the_box_not_chips(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES ('Hartman')").lastrowid
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (?, 'family', 'booked', '2026-12-12')", (hid,))
    con.commit()
    con.close()
    e.c.post(f"/client/{hid}/labels", data={"label": "Personal"}, environ_base=PE.E)
    page = e.c.get("/sessions?view=all", environ_base=PE.E).get_data(as_text=True)
    assert 'data-f-key="label"' not in page and "label:name" in page and 'data-f-g-label="Personal"' in page
    page = e.c.get("/sessions?view=all&q=label:personal", environ_base=PE.E).get_data(as_text=True)
    assert "Hartman" in page and "The Sample Family" not in page
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "filters.js")).read()
    assert "x.startsWith('label:')" in js

def test_adding_a_note_is_in_plain_sight_on_the_session(tmp_path, monkeypatch):

    import prints_env as PE
    e = PE.make(tmp_path, monkeypatch)
    page = e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
    notes = page.split('id="notes"', 1)[1].split('id="more"', 1)[0]
    assert 'action="/client/1/note"' in notes and ">Save note</button>" in notes
    hist = page.split('id="history"', 1)[1]
    assert 'action="/client/1/note"' not in hist and "Search notes and history" in hist
    e.c.post("/client/1/note", data={"session_id": "1", "text": "Prefers the lake at golden hour", "channel": "note",
                                     "back": "/c/1?s=1#notes"}, environ_base=PE.E)
    page = e.c.get("/c/1?s=1", environ_base=PE.E).get_data(as_text=True)
    notes = page.split('id="notes"', 1)[1].split('id="more"', 1)[0]
    assert "Prefers the lake at golden hour" in notes and 'data-confirm="Delete this note?"' in notes
