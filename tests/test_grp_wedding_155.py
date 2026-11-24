import html
import os
import re
import sys
from datetime import date, timedelta
from urllib.parse import unquote

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
    c.post("/setup/shoot", data={"preset_weddings": "1", "preset_engagements": "1", "preset_families": "1"}, environ_base=E)
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        out = con.execute(q_, args).fetchall()
        con.commit()
        return out
    finally:
        con.close()

def person(A, hid, name, rel="", email="", role=None):
    import app as A_
    role = role or ("parent" if rel in A_.PARENT_REL else "adult")
    sql(A, "INSERT INTO people (household_id, name, relation, role, email, is_minor) VALUES (?,?,?,?,?,0)",
        (hid, name, rel, role, email))

def wedding_record(A, planner_first=True, type_="wedding"):

    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    if planner_first:
        person(A, 1, "Quinn Planner", "planner", "quinn@example.com")
    person(A, 1, "Avery Rowe", "bride", "avery@example.com")
    person(A, 1, "Jordan Penn", "partner", "jordan@example.com")
    person(A, 1, "Morgan Rowe", "mother", "morgan@example.com")
    person(A, 1, "Casey Penn", "mother", "casey@example.com")
    with A.app.test_request_context():
        sid = A.create_session(1, type_, stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    return sid

def test_wedding_types_are_couple_types_and_families_are_not(env):
    A, _ = env
    assert {"wedding", "engagement"} <= A.C.COUPLE_TYPES
    assert "family" not in A.C.COUPLE_TYPES
    assert A.C.couple_type(dict(key="elope", label="Elopements"))
    assert not A.C.couple_type(dict(key="family", label="Family"))

def test_a_wedding_draft_goes_to_both_partners_not_the_planner_or_a_parent(env):
    A, _ = env
    sid = wedding_record(A)
    with A.app.test_request_context("/"):
        v = A._send_ctx(sid, "quote", "Your quote", ["Here it is."])
    to = unquote(v["mailto"].split("?", 1)[0][len("mailto:"):])
    assert to == "avery@example.com,jordan@example.com"
    assert v["to"] == "avery@example.com, jordan@example.com"
    assert "Hi Avery and Jordan," in unquote(v["mailto"])
    assert "quinn" not in v["mailto"] and "morgan" not in v["mailto"]

def test_a_wedding_invoice_bills_both_partners(env):
    A, _ = env
    sid = wedding_record(A)
    with A.app.test_request_context("/"):
        v = A.bill_ctx(sid, live=True)
    assert v["bill_to"] == "Avery Rowe and Jordan Penn"

def test_a_wedding_agreement_is_signed_by_the_couple_and_others_stay_a_choice(env):
    A, c = env
    sid = wedding_record(A)
    signers = [r_[0] for r_ in sql(A, """SELECT p.name FROM documents d JOIN people p ON p.id=d.about_person
                                          WHERE d.session_id=? AND d.kind='agreement' ORDER BY d.id""", (sid,))]
    assert signers == ["Avery Rowe", "Jordan Penn"]
    with A.app.test_request_context("/"):
        ch = [p_["name"] for p_ in A.signer_choices(1, (), "wedding")]
    assert ch[:2] == ["Avery Rowe", "Jordan Penn"]
    assert {"Morgan Rowe", "Casey Penn", "Quinn Planner"} <= set(ch)
    page = html.unescape(c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True))
    boxes = {n: on for on, n in re.findall(r'name="signer" value="\d+"( checked)?>([^<]+)</label>', page)}
    assert boxes == {"Avery Rowe": " checked", "Jordan Penn": " checked", "Quinn Planner": "", "Morgan Rowe": "",
                     "Casey Penn": ""}

def test_an_engagement_with_no_planner_names_the_couple(env):
    A, _ = env
    sid = wedding_record(A, planner_first=False, type_="engagement")
    with A.app.test_request_context("/"):
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Avery Rowe and Jordan Penn"
        assert [p_["name"] for p_ in A.default_signers(1, "engagement")] == ["Avery Rowe", "Jordan Penn"]

def test_nobody_without_a_partner_relationship_counts_beside_named_ones(env):

    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    person(A, 1, "Quinn Planner", "", "quinn@example.com")
    person(A, 1, "Avery Rowe", "fiancee", "avery@example.com")
    person(A, 1, "Jordan Penn", "groom", "jordan@example.com")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, "wedding")] == ["Avery Rowe", "Jordan Penn"]
        assert A._send_ctx(sid, "quote", "Q", ["x"])["to"] == "avery@example.com, jordan@example.com"

def test_a_planner_is_never_the_default_even_with_no_partner_named(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    person(A, 1, "Quinn Planner", "planner", "quinn@example.com")
    person(A, 1, "Avery Rowe", "", "avery@example.com")
    person(A, 1, "Morgan Rowe", "mother", "morgan@example.com")
    with A.app.test_request_context():
        sid = A.create_session(1, "wedding", stage="booked", shoot_date=(date.today() + timedelta(days=200)).isoformat())
        A.db().commit()
    with A.app.test_request_context("/"):
        assert A.couple_of(1, "wedding") == []
        assert A._send_ctx(sid, "quote", "Q", ["x"])["to"] == "avery@example.com"
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Avery Rowe"
        assert [p_["name"] for p_ in A.default_signers(1, "wedding")] == ["Avery Rowe"]

def test_with_no_relationships_typed_the_first_two_adults_are_the_couple(env):
    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Rowe and Penn')")
    for n_ in ("Avery Rowe", "Jordan Penn", "Sam Guest"):
        person(A, 1, n_, "", "")
    with A.app.test_request_context("/"):
        assert [p_["name"] for p_ in A.couple_of(1, "wedding")] == ["Avery Rowe", "Jordan Penn"]

def test_one_partner_with_email_gets_the_draft_alone(env):
    A, _ = env
    sid = wedding_record(A)
    sql(A, "UPDATE people SET email='' WHERE name='Jordan Penn'")
    with A.app.test_request_context("/"):
        v = A._send_ctx(sid, "quote", "Your quote", ["Here it is."])
    assert v["to"] == "avery@example.com" and "Hi Avery," in unquote(v["mailto"])

def test_a_family_session_goes_to_the_parent_who_booked(env):

    A, _ = env
    sql(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    person(A, 1, "Dana Lane", "mother", "dana@example.com")
    person(A, 1, "Riley Lane", "", "riley@example.com")
    with A.app.test_request_context():
        sid = A.create_session(1, "family", stage="booked", shoot_date=(date.today() + timedelta(days=20)).isoformat())
        A.db().commit()
    with A.app.test_request_context("/"):
        v = A._send_ctx(sid, "quote", "Your quote", ["Here it is."])
        assert v["to"] == "dana@example.com"
        assert A.bill_ctx(sid, live=True)["bill_to"] == "Dana Lane"
        assert [p_["name"] for p_ in A.default_signers(1, "family")] == ["Dana Lane"]

GID = "g-galWed155AAAAAAAAAAAAAA"
DEV_A, DEV_B = "a1" * 8, "b2" * 8

@pytest.fixture
def gal(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    from PIL import Image
    src = tmp_path / "photos" / "export" / "rowe"
    src.mkdir(parents=True)
    for n_, col in (("a.jpg", "red"), ("b.jpg", "blue"), ("c.jpg", "green")):
        Image.new("RGB", (32, 24), col).save(src / n_, "JPEG")
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    from plugins import gallery as G
    key = G.new_key()
    G.build(GID, src, key, picks=dict(retouch=3, market=2, post=True))
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gallery_base_url', 'https://example.com/g/')")
    con.execute("INSERT INTO households (name) VALUES ('Rowe and Penn')")
    con.execute("""INSERT INTO sessions (household_id, type, stage, shoot_date, gallery_id)
                   VALUES (1, 'family', 'delivered', '2026-01-10', ?)""", (GID,))
    con.execute("UPDATE sessions SET first_visit=? WHERE id=1", (date.today().isoformat(),))
    con.execute("INSERT INTO galleries (session_id, key, built_at, n_files) VALUES (1, ?, ?, 3)", (key, date.today().isoformat()))
    con.commit(); con.close()
    G.upload_gallery(GID, A.C.GALLERY_DAYS, "2099-01-01")
    con = A.connect()
    con.execute("UPDATE galleries SET upload_state='up', uploaded_at=? WHERE session_id=1", (date.today().isoformat(),))
    con.commit(); con.close()
    return A, c, G, host / GID, key

def seal(key, picks, purpose="picks", dev=None, rp=None, at="2026-11-20T15:00:00.000Z", item=None):

    import base64
    import json
    import secrets
    import sealed
    body = {"v": 1, "id": item or secrets.token_hex(16), "at": at}
    if dev is not None:
        body["d"] = dev
    if rp is not None:
        body["rp"] = rp
    body["picks"] = picks
    return sealed.seal_link(purpose, GID, base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)), json.dumps(body).encode())

def put(folder, key, plain, blob):

    import base64
    import hostfiles
    proof = hostfiles.proof_of(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)))
    (folder / hostfiles.name_for(proof, plain)).write_bytes(blob)

def pick(name, i, r=False, note="", m=False, v=False):
    return {"i": i, "name": name, "k": name, "r": r, "note": note, "m": m, "v": v}

def recorded(A):
    return tuple(sql(A, "SELECT retouch_picks, favorites, veto FROM sessions WHERE id=1")[0])

def test_two_partners_picking_apart_keep_both_sets_of_picks(gal):
    A, c, G, d, key = gal
    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True, note="soften"), pick("b.jpg", 1, m=True)], dev=DEV_A))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg: soften", "b.jpg", "")

    put(d, key, f"picks-{DEV_B}.las", seal(key, [pick("a.jpg", 0, r=True, note="brighten"), pick("b.jpg", 1, v=True),
                                                  pick("c.jpg", 2, m=True)], dev=DEV_B))
    c.post("/gallery/sync", environ_base=E)
    retouch, fav, veto = recorded(A)
    assert retouch == "a.jpg: soften / brighten"
    assert fav == "c.jpg" and veto == "b.jpg"
    hist = [r_[0] for r_ in sql(A, "SELECT action FROM audit WHERE session_id=1 ORDER BY id")]
    assert any("from 2 devices, put together" in h_ for h_ in hist)

    assert G.kept_device_names(GID) == [f"picks-{DEV_A}.las", f"picks-{DEV_B}.las"]

def test_a_gallery_with_the_old_single_picks_file_still_reads(gal):
    import hashlib
    A, c, G, d, key = gal
    old = seal(key, [pick("a.jpg", 0, r=True, note="soften"), pick("c.jpg", 2, v=True)])
    put(d, key, "picks.las", old)
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg: soften", "", "c.jpg")
    assert sql(A, "SELECT picks_hash FROM galleries WHERE session_id=1")[0][0] == hashlib.sha256(old).hexdigest()

    put(d, key, f"picks-{DEV_B}.las", seal(key, [pick("b.jpg", 1, m=True)], dev=DEV_B))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg: soften", "b.jpg", "c.jpg")

def test_a_device_that_carries_on_from_the_old_file_replaces_it(gal):

    A, c, G, d, key = gal
    item = "c" * 32
    put(d, key, "picks.las", seal(key, [pick("a.jpg", 0, r=True), pick("b.jpg", 1, m=True)], item=item))
    c.post("/gallery/sync", environ_base=E)
    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True)], dev=DEV_A, rp=[item]))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg", "", "")

def test_a_new_device_that_carries_on_from_another_devices_file_replaces_it(gal):

    A, c, G, d, key = gal
    a_item = "a" * 32
    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True), pick("b.jpg", 1, m=True)], dev=DEV_A, item=a_item))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg", "b.jpg", "")

    put(d, key, f"picks-{DEV_B}.las", seal(key, [pick("a.jpg", 0, r=True)], dev=DEV_B, rp=[a_item]))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg", "", "")

    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True), pick("b.jpg", 1, m=True)], dev=DEV_A, item=a_item, rp=[a_item]))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg", "", "")

    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("c.jpg", 2, m=True)], dev=DEV_A))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) == ("a.jpg", "c.jpg", "")

def test_a_file_under_another_devices_name_is_never_read(gal):
    A, c, G, d, key = gal
    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True)], dev=DEV_B))
    put(d, key, f"picks-{DEV_B}.las", seal(key, [pick("b.jpg", 1, r=True)], dev="zz"))
    c.post("/gallery/sync", environ_base=E)
    assert recorded(A) in ((None, None, None), ("", "", ""))

def test_still_picking_counts_every_devices_draft(gal):
    A, c, G, d, key = gal
    put(d, key, f"draft-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True)], "draft", dev=DEV_A))
    put(d, key, f"draft-{DEV_B}.las", seal(key, [pick("b.jpg", 1, m=True), pick("a.jpg", 0, r=True)], "draft", dev=DEV_B))
    c.post("/gallery/sync", environ_base=E)
    assert sql(A, "SELECT draft_n FROM galleries WHERE session_id=1")[0][0] == 2

    put(d, key, f"picks-{DEV_A}.las", seal(key, [pick("a.jpg", 0, r=True)], dev=DEV_A))
    put(d, key, f"draft-{DEV_B}.las", seal(key, [pick("c.jpg", 2, v=True)], "draft", dev=DEV_B))
    c.post("/gallery/sync", environ_base=E)
    assert sql(A, "SELECT draft_n FROM galleries WHERE session_id=1")[0][0] == 1

def test_device_names_hide_the_device_on_the_host():
    import hostfiles
    proof = "0" * 32
    n_ = hostfiles.name_for(proof, f"picks-{DEV_A}.las")
    assert re.fullmatch(r"[0-9a-f]{24}\.las", n_) and DEV_A not in n_
    assert n_ != hostfiles.name_for(proof, f"picks-{DEV_B}.las") != hostfiles.name_for(proof, "picks.las")
    with pytest.raises(ValueError):
        hostfiles.name_for(proof, "picks-xyz.las")

def test_merge_keeps_one_line_per_photo():
    sys.path.insert(0, ROOT)
    from plugins import gallery as G
    out = G.merge_lists([[pick("a.jpg", 0, r=True, note="x"), pick("b.jpg", 1, m=True, v=False)],
                         [pick("a.jpg", 0, r=True, note="x"), pick("b.jpg", 1, v=True)]])
    assert [(p_["name"], p_["r"], p_["note"], p_["m"], p_["v"]) for p_ in out] == [("a.jpg", True, "x", False, False),
                                                                                  ("b.jpg", False, "", False, True)]

@pytest.fixture
def php_host(gal):
    import socket
    import subprocess
    import time
    from helpers_tools import need_tool
    from helpers import host_write
    php = need_tool("php")
    A, c, G, d, key = gal
    host_write(d, "limits.txt", "days=30\nunopened_until=2099-01-01\nproof=1\n")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([php, "-S", f"127.0.0.1:{port}", "-t", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    import hashlib
    yield f"http://127.0.0.1:{port}/", d, hashlib.sha256(G.key_bytes(key)).hexdigest()[:32], key
    p.terminate(); p.wait()

def call(url, body=None, proof=None):
    import urllib.error
    import urllib.request
    from helpers import form_d
    h = {"X-Proof": proof} if proof else {}
    if body is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
        body = form_d(body)
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""

def test_picks_php_keeps_each_devices_own_file(php_host):
    import hostfiles
    base, d, proof, key = php_host
    a = seal(key, [pick("a.jpg", 0, r=True)], dev=DEV_A)
    b = seal(key, [pick("b.jpg", 1, r=True)], dev=DEV_B)
    assert call(base + "picks.php?dev=XYZ", a, proof)[0] == 400
    assert call(base + f"picks.php?dev={DEV_A}", a, proof)[0] == 204
    assert call(base + f"picks.php?dev={DEV_B}", b, proof)[0] == 204
    assert (d / hostfiles.name_for(proof, f"picks-{DEV_A}.las")).read_bytes() == a
    assert (d / hostfiles.name_for(proof, f"picks-{DEV_B}.las")).read_bytes() == b
    assert not (d / hostfiles.name_for(proof, "picks.las")).exists()
    assert not any(DEV_A in p_.name or DEV_B in p_.name for p_ in d.iterdir())

    assert call(base + f"gate.php?f=picks-{DEV_A}.las", proof=proof) == (200, a)
    assert call(base + f"gate.php?f=picks-{DEV_A}.las")[0] == 403
    assert call(base + "gate.php?f=picks-1234.las", proof=proof)[0] == 404

    old = seal(key, [pick("c.jpg", 2, r=True)])
    assert call(base + "picks.php", old, proof)[0] == 204
    assert (d / hostfiles.name_for(proof, "picks.las")).read_bytes() == old

def test_the_gate_lists_every_devices_file_for_a_new_device(php_host):

    import hostfiles
    import urllib.request
    base, d, proof, key = php_host
    a = seal(key, [pick("a.jpg", 0, r=True)], "draft", dev=DEV_A)
    assert call(base + f"picks.php?kind=draft&dev={DEV_A}", a, proof)[0] == 204
    req = urllib.request.Request(base + "gate.php?f=manifest.bin", headers={"X-Proof": proof})
    with urllib.request.urlopen(req, timeout=10) as r:
        listed = r.headers.get("X-Picks")
    hn = hostfiles.name_for(proof, f"draft-{DEV_A}.las")
    assert listed == hn and DEV_A not in listed
    assert call(base + f"gate.php?f={hn}", proof=proof) == (200, a)
    assert call(base + f"gate.php?f={hn}")[0] == 403
