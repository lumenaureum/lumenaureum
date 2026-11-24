import html
import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client
from test_ui_library import bare_labels, one_offs

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (1, 'The Sample Family')")
        con.execute("INSERT INTO sessions (id, household_id, type, stage, shoot_date) VALUES (1, 1, 'family', 'booked', ?)",
                    ((A.today() + timedelta(days=5)).isoformat(),))
        con.commit()
    finally:
        con.close()
    return A, c

def turn_on(A, on=True):
    sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('plugin_on_equipment', ?)", ("1" if on else "0",))

def sql(A, s, args=()):
    con = A.connect()
    try:
        rows = con.execute(s, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def text(c, path):
    t = c.get(path, environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))

def save_list(c, lines, key="family"):
    return c.post(f"/settings/type/{key}/packing", data={"lines": lines, "back": f"/settings/type/{key}#packing"},
                  environ_base=E)

def test_off_by_default_and_nothing_shows(env):
    A, c = env
    assert "Packing list" not in text(c, "/settings/type/family")
    assert "Packing list" not in text(c, "/c/1?s=1")
    assert save_list(c, "Camera").status_code == 404
    assert "Equipment and packing lists" in text(c, "/settings/plugins")

def test_the_list_lives_on_the_type_and_shows_on_a_booked_session(env):
    A, c = env
    turn_on(A)
    assert "Packing list" in text(c, "/settings/type/family")
    assert save_list(c, "Camera body\n\n  Spare   batteries \ncamera BODY\nReflector").status_code == 302
    page = c.get("/settings/type/family", environ_base=E).get_data(as_text=True)
    assert "Camera body\nSpare batteries\nReflector</textarea>" in page
    t = text(c, "/c/1?s=1")
    assert "Packing list: 0 of 3 packed" in t
    raw = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    fold = re.search(r'<details class="ui-fold[^"]*" id="packing"[^>]*>', raw)
    assert fold and " open" not in fold.group(0)

    sql(A, "UPDATE sessions SET type='headshots' WHERE id=1")
    assert "Packing list" not in text(c, "/c/1?s=1")

def test_ticks_belong_to_the_session_and_open_on_the_shoot_day(env):
    A, c = env
    turn_on(A)
    save_list(c, "Camera body\nSpare batteries\nReflector")
    r = c.post("/session/1/packing", data={"line": "Spare batteries", "packed": "1", "back": "/c/1?s=1#packing"},
               environ_base=E)
    assert r.status_code == 302
    c.post("/session/1/packing", data={"line": "Not on the list", "packed": "1"}, environ_base=E)
    assert [r_[0] for r_ in sql(A, "SELECT line FROM packing_ticks")] == ["Spare batteries"]
    assert "Packing list: 1 of 3 packed" in text(c, "/c/1?s=1")
    c.post("/session/1/packing", data={"line": "Spare batteries"}, environ_base=E)
    assert "Packing list: 0 of 3 packed" in text(c, "/c/1?s=1")
    c.post("/session/1/packing", data={"line": "Reflector", "packed": "1"}, environ_base=E)
    save_list(c, "Camera body\nSpare batteries")
    assert "Packing list: 0 of 2 packed" in text(c, "/c/1?s=1")
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=1", (A.today().isoformat(),))
    raw = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert re.search(r'<details class="ui-fold[^"]*" id="packing" open', raw)

def test_after_the_shoot_the_list_is_gone_and_off_hides_it(env):
    A, c = env
    turn_on(A)
    save_list(c, "Camera body")
    sql(A, "UPDATE sessions SET stage='shot' WHERE id=1")
    assert "Packing list" not in text(c, "/c/1?s=1")
    sql(A, "UPDATE sessions SET stage='booked' WHERE id=1")
    turn_on(A, False)
    assert "Packing list" not in text(c, "/c/1?s=1")
    assert c.post("/session/1/packing", data={"line": "Camera body", "packed": "1"}, environ_base=E).status_code == 404
    turn_on(A)
    assert "Packing list: 0 of 1 packed" in text(c, "/c/1?s=1")

def test_a_tick_is_not_history_and_saving_the_list_is(env):
    A, c = env
    turn_on(A)
    save_list(c, "Camera body")
    c.post("/session/1/packing", data={"line": "Camera body", "packed": "1"}, environ_base=E)
    acts = " | ".join(str(r_[0]) for r_ in sql(A, "SELECT action FROM audit"))
    assert "packing list" in acts.lower() and "Camera body" not in acts

def test_unknown_type_and_session_are_refused(env):
    A, c = env
    turn_on(A)
    assert save_list(c, "Camera", key="nope").status_code == 404
    assert c.post("/session/99/packing", data={"line": "Camera"}, environ_base=E).status_code == 404

def test_the_plugin_markup_is_built_from_the_library():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "equipment.py")
    spec = importlib.util.spec_from_file_location("equipment_markup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in [n for n in dir(mod) if n.isupper() and isinstance(getattr(mod, n), str)]:
        tpl = getattr(mod, name)
        assert not one_offs(tpl), (name, one_offs(tpl))
        assert not bare_labels(tpl), (name, bare_labels(tpl))

def add(c, data, receipt=None):
    import io
    d = dict(data)
    if receipt:
        d["receipt"] = (io.BytesIO(receipt[1]), receipt[0])
    return c.post("/equipment/add", data=d, content_type="multipart/form-data", environ_base=E)

def test_equipment_is_off_by_default_and_in_the_top_bar_when_on(env):
    A, c = env
    assert c.get("/equipment", environ_base=E).status_code == 404
    assert 'href="/equipment"' not in c.get("/", environ_base=E, follow_redirects=True).get_data(as_text=True)
    turn_on(A)
    assert 'href="/equipment"' in c.get("/", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "Nothing listed yet" in text(c, "/equipment")

def test_add_edit_and_delete_equipment_with_a_sealed_receipt(env, tmp_path):
    A, c = env
    turn_on(A)
    pdf = b"%PDF-1.4 sample receipt " + b"x" * 3000
    r = add(c, {"name": "Camera  body", "serial": "SN-0042", "bought": "2025-03-01", "price": "$1,299.99"},
            ("receipt.pdf", pdf))
    assert r.status_code == 302
    eid, name, serial, bought, cents, fid = sql(A, "SELECT id, name, serial, bought, price_cents, receipt_fid FROM equipment")[0]
    assert (name, serial, bought, cents) == ("Camera body", "SN-0042", "2025-03-01", 129999)
    t = text(c, "/equipment")
    assert "1 thing, $1,299.99 paid" in t and "Camera body, serial SN-0042" in t and "Receipt: receipt.pdf" in t

    import photostore
    row = sql(A, "SELECT session_id, kind, state FROM photo_files WHERE id=?", (fid,))[0]
    assert tuple(row) == (None, "receipt", "done")
    on_disk = photostore.path_of(fid).read_bytes()
    assert on_disk.startswith(b"LAP1") and b"sample receipt" not in on_disk
    got = c.get(f"/equipment/{eid}/receipt", environ_base=E)
    assert got.status_code == 200 and got.data == pdf and "attachment" in got.headers["Content-Disposition"]

    assert "receipt.pdf" not in text(c, "/c/1?s=1")

    r = c.post(f"/equipment/{eid}", data={"name": "Camera body", "serial": "SN-0042", "price": "1200",
                                          "receipt": (__import__("io").BytesIO(b"new receipt"), "new.jpg")},
               content_type="multipart/form-data", environ_base=E)
    assert r.status_code == 302, r.get_data(as_text=True)[-600:]
    fid2 = sql(A, "SELECT receipt_fid FROM equipment")[0][0]
    assert fid2 != fid and not photostore.path_of(fid).exists()
    assert not sql(A, "SELECT 1 FROM photo_files WHERE id=?", (fid,))
    assert sql(A, "SELECT price_cents, bought FROM equipment")[0][0] == 120000
    c.post(f"/equipment/{eid}/receipt/delete", environ_base=E)
    assert sql(A, "SELECT receipt_fid FROM equipment")[0][0] is None and not photostore.path_of(fid2).exists()
    add(c, {"name": "Reflector"}, ("r.png", b"\x89PNG receipt"))
    eid2, fid3 = sql(A, "SELECT id, receipt_fid FROM equipment WHERE name='Reflector'")[0]
    c.post(f"/equipment/{eid2}/delete", environ_base=E)
    assert not sql(A, "SELECT 1 FROM equipment WHERE id=?", (eid2,)) and not photostore.path_of(fid3).exists()
    acts = " | ".join(str(r_[0]) for r_ in sql(A, "SELECT action FROM audit"))
    assert "Added equipment: Camera" in acts

def test_bad_input_is_refused_in_words(env):
    A, c = env
    turn_on(A)
    for data in ({"name": ""}, {"name": "Lens", "price": "lots"}, {"name": "Lens", "bought": "someday"}):
        add(c, data)
    assert not sql(A, "SELECT 1 FROM equipment")
    import plugins.equipment as P
    add(c, {"name": "Lens"}, ("big.pdf", b"x" * (P.MAX_RECEIPT + 1)))
    assert not sql(A, "SELECT 1 FROM equipment") and not sql(A, "SELECT 1 FROM photo_files")
    assert "over 20 MB" in text(c, "/equipment")

def test_a_packing_list_picks_from_the_equipment(env):
    A, c = env
    turn_on(A)
    add(c, {"name": "Camera body"})
    add(c, {"name": "Light stand"})
    save_list(c, "camera body")
    page = c.get("/settings/type/family", environ_base=E).get_data(as_text=True)
    opts = re.findall(r'<option value="\d+">([^<]+)</option>', page.split('id="packing"', 1)[1])
    assert opts == ["Light stand"]
    eid = sql(A, "SELECT id FROM equipment WHERE name='Light stand'")[0][0]
    c.post("/settings/type/family/packing/add", data={"eid": eid}, environ_base=E)
    assert "Packing list: 0 of 2 packed" in text(c, "/c/1?s=1")

def test_a_receipt_is_recoverable_with_the_master_key_alone(env, tmp_path):

    A, c = env
    turn_on(A)
    add(c, {"name": "Camera body"}, ("receipt.pdf", b"%PDF receipt"))
    import photostore
    fid = sql(A, "SELECT receipt_fid FROM equipment")[0][0]
    assert photostore.key_path(fid).exists()
    row = sql(A, "SELECT * FROM photo_files WHERE id=?", (fid,))[0]
    assert photostore.plain_bytes(row) == b"%PDF receipt"

def test_a_renewal_shows_on_today_thirty_days_ahead(env):
    A, c = env
    turn_on(A)
    soon, later, gone = ((A.today() + timedelta(days=d)).isoformat() for d in (20, 45, -3))
    add(c, {"name": "Camera body", "renews": soon})
    add(c, {"name": "Light stand", "renews": later})
    add(c, {"name": "Flash", "renews": gone})
    add(c, {"name": "Reflector"})
    assert [r[0] for r in sql(A, "SELECT renews FROM equipment ORDER BY id")] == [soon, later, gone, None]
    t = text(c, "/")
    assert "Camera body: renews on" in t and "Light stand" not in t and "Reflector" not in t
    assert "Flash: lapsed on" in t and "Update the date on Equipment" in t
    eid = sql(A, "SELECT id FROM equipment WHERE name='Camera body'")[0][0]
    assert f'href="/equipment#eq-{eid}"' in c.get("/", environ_base=E, follow_redirects=True).get_data(as_text=True)
    c.post(f"/equipment/{eid}", data={"name": "Camera body", "renews": ""}, content_type="multipart/form-data",
           environ_base=E)
    assert "Camera body: renews" not in text(c, "/")
    add(c, {"name": "Lens", "renews": "next year"})
    assert not sql(A, "SELECT 1 FROM equipment WHERE name='Lens'")
    turn_on(A, False)
    assert "Flash: lapsed" not in text(c, "/")

def export(c):
    import json
    return json.loads(c.get("/settings/export.json", environ_base=E).get_data(as_text=True))

def import_file(c, data):
    import io
    import json
    body = json.dumps(dict({"lumen_aureum_settings": 1}, **data)).encode()
    return c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(body), "s.json")},
                  content_type="multipart/form-data", environ_base=E)

def test_packing_lists_go_in_the_settings_file_once_the_plugin_was_ever_on(env):
    A, c = env
    assert "plugins" not in export(c)
    turn_on(A)
    save_list(c, "Camera body\nSpare batteries")
    add(c, {"name": "Camera body", "serial": "SN-1", "price": "100"})
    got = export(c)["plugins"]
    assert got == {"packing_lists": {"family": ["Camera body", "Spare batteries"]}}
    turn_on(A, False)
    assert export(c)["plugins"]["packing_lists"] == {"family": ["Camera body", "Spare batteries"]}

    with A.app.app_context():
        import json
        extras = dict(A._backup_extras())
    assert json.loads(extras["settings.json"])["plugins"]["packing_lists"]["family"] == ["Camera body", "Spare batteries"]

def test_a_settings_file_brings_packing_lists_in(env):
    A, c = env
    import json
    import_file(c, {"plugins": {"packing_lists": {"family": ["Tripod", " tripod ", "Lens cloth"], "nope": ["X"]},
                                "someone_else": {"a": 1}}})
    lines = json.loads(sql(A, "SELECT lines FROM packing_lists WHERE type_key='family'")[0][0])
    assert lines == ["Tripod", "Lens cloth"] and not sql(A, "SELECT 1 FROM packing_lists WHERE type_key='nope'")
    t = text(c, "/settings")
    assert "Packing lists for 1 session type came in" in t and "this studio doesn't have were left out" in t

    import_file(c, {"settings": {"business_name": "Changed Studio"}, "plugins": {"packing_lists": {"family": "Tripod"}}})
    assert not sql(A, "SELECT 1 FROM settings WHERE key='business_name' AND value='Changed Studio'")
    assert json.loads(sql(A, "SELECT lines FROM packing_lists WHERE type_key='family'")[0][0]) == ["Tripod", "Lens cloth"]

def test_a_new_master_key_rewraps_a_receipt_and_keeps_its_label(env):
    import json
    import photostore
    import pyrage
    from vault import keys as K
    A, c = env
    turn_on(A)
    add(c, {"name": "Camera body"}, ("receipt.pdf", b"%PDF receipt"))
    fid = sql(A, "SELECT receipt_fid FROM equipment")[0][0]
    secret, pub = K.generate_master()
    con = A.connect()
    try:
        assert photostore.reseal_keys(con, pub) == 1
    finally:
        con.close()
    body = json.loads(pyrage.decrypt(photostore.key_path(fid).read_bytes(), [K.parse_secret(secret)]))
    assert (body["kind"], body["session"], body["label"], body["name"]) == ("receipt", None, "Receipts", "receipt.pdf")

def test_restore_says_receipts_apart_from_photos(env):
    import restore
    A, c = env
    turn_on(A)
    add(c, {"name": "Camera body"}, ("receipt.pdf", b"%PDF receipt"))
    fid = sql(A, "SELECT receipt_fid FROM equipment")[0][0]
    con = A.connect()
    try:
        assert restore.photo_ids(con) == {fid} and restore.other_ids(con) == {fid}
        assert restore.summary(con)["photos"] == 0
    finally:
        con.close()
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "restore.html")).read()
    assert "p.gone_other" in page and "equipment receipt" in page

def test_typed_forms_say_unsaved_changes(env):

    A, c = env
    turn_on(A)
    add(c, {"name": "Camera body"})
    page = c.get("/equipment", environ_base=E).get_data(as_text=True)
    assert page.count("data-ui-unsaved") == 2
    tp = c.get("/settings/type/family", environ_base=E).get_data(as_text=True).split('id="packing"', 1)[1]
    assert "data-ui-unsaved" in tp.split("</form>", 1)[0]
