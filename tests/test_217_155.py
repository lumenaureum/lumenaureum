import os
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c, tmp_path

def _ctx(studio):
    import make_fixture as MF
    A, c, work = studio
    x = MF.Ctx(A, c, str(work))
    x.v15 = True
    return MF, x

def test_the_wedding_client_shares_one_agreement_and_has_releases_for_two(studio):
    MF, x = _ctx(studio)
    m = MF.More154()
    m.s154, m.h154, m.e154 = {}, {}, {}
    m.types154(x)
    m.wedding(x)
    s_wed, s_eng, hid = m.s154["wedding"], m.s154["engagement"], m.h154["wedding"]
    one, rows = x.one, x.rows
    assert one("SELECT plan_of FROM sessions WHERE id=?", (s_eng,))[0] == s_wed
    assert [r[0] for r in rows("SELECT status FROM documents WHERE session_id=? AND kind='agreement'", (s_wed,))] == ["signed"] * 2
    assert sorted(r[0] for r in rows("SELECT status FROM documents WHERE session_id=? AND kind='release'", (s_wed,))) == ["sent", "signed"]

    for url in (f"/client/{hid}", f"/c/{hid}?s={s_wed}", f"/c/{hid}?s={s_eng}", f"/session/{s_wed}/agreement",
                f"/session/{s_wed}/edit"):
        r = x.c.get(url, environ_base=E)
        assert r.status_code == 200, (url, r.status_code)
    assert "Wynn and Sato" in x.get(f"/c/{hid}?s={s_eng}").get_data(as_text=True)
    r = x.c.get(f"/session/{s_eng}/agreement", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/session/{s_wed}/agreement")
    agreement = x.get(f"/session/{s_wed}/agreement").get_data(as_text=True)
    assert "Robin Wynn" in agreement and "Alex Sato" in agreement

def test_the_school_event_has_many_families_and_a_release_per_child(studio):
    MF, x = _ctx(studio)
    m = MF.More154()
    m.s154, m.h154, m.e154 = {}, {}, {}
    m.types154(x)
    m.school(x)
    eid = m.e154["school"]
    fams = {k: v for k, v in m.s154.items() if k.startswith("school_")}
    assert len(fams) == 7
    signed = x.one("""SELECT COUNT(*) FROM documents WHERE kind='release' AND status='signed'
                      AND session_id IN (SELECT id FROM sessions WHERE event_id=?)""", (eid,))[0]
    assert signed == 9
    page = x.get(f"/event/{eid}").get_data(as_text=True)
    for name, _, _ in MF.More154.FAMILIES:
        assert name in page, name

def test_the_measure_records_what_the_new_clients_show(studio):
    MF, x = _ctx(studio)
    m = MF.More154()
    m.s154, m.h154, m.e154 = {}, {}, {}
    m.types154(x)
    m.wedding(x)
    m.school(x)
    got = m._measure154(x, dict(sessions_by_name={}))
    assert got["wedding"]["plan"] == {str(m.s154["engagement"]): m.s154["wedding"]}
    assert [d["status"] for d in got["wedding"]["agreement"]] == ["signed", "signed"]
    assert got["wedding"]["totals"][str(m.s154["wedding"])] == dict(quoted=3150.0, paid=945.0, due=2205.0)
    assert got["wedding"]["delivery_method"] == "link"
    fam = got["school"]["families"]
    assert fam["brandt"]["children"] == ["Max", "Ida", "Paul"] and fam["brandt"]["releases"] == ["signed"] * 3
    assert fam["farrow"]["releases"] == ["needed"] and fam["farrow"]["due"] > 0
    assert fam["abara"]["due"] == 0

def test_the_demo_studio_has_the_wedding_couple_and_their_pages_open(studio):
    A, c, _ = studio
    import config as C
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    con = A.connect()
    try:
        hid = con.execute("SELECT id FROM households WHERE name='Wynn and Sato' AND demo=1").fetchone()[0]
        ss = {r[1]: (r[0], r[2]) for r in con.execute("SELECT id, type, plan_of FROM sessions WHERE household_id=?", (hid,))}
        assert set(ss) == {"wedding", "engagement"}
        assert ss["engagement"][1] == ss["wedding"][0]
        assert not con.execute("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (ss["engagement"][0],)).fetchone()
        rel = [r[0] for r in con.execute("SELECT status FROM documents WHERE session_id=? AND kind='release' ORDER BY id",
                                         (ss["wedding"][0],))]
        assert sorted(rel) == ["sent", "signed"]

        assert con.execute("SELECT COUNT(*) FROM households WHERE demo=1").fetchone()[0] == 11
    finally:
        con.close()
    for url in (f"/client/{hid}", f"/c/{hid}?s={ss['wedding'][0]}", f"/c/{hid}?s={ss['engagement'][0]}",
                f"/session/{ss['wedding'][0]}/agreement", "/clients", "/"):
        r = c.get(url, environ_base=E)
        assert r.status_code == 200, (url, r.status_code)

def test_a_hand_typed_engagement_line_covers_the_engagement(studio):

    A, _, _ = studio
    MF, x = _ctx(studio)
    m = MF.More154()
    m.s154, m.h154, m.e154 = {}, {}, {}
    m.types154(x)
    m.wedding(x)
    s_wed = m.s154["wedding"]
    with A.app.app_context():
        A.x("DELETE FROM lines WHERE session_id=?", (s_wed,))
        A.x("INSERT INTO lines (session_id, label, amount) VALUES (?, 'Wedding day coverage (8 hr)', 2800)", (s_wed,))
        assert not A.plan_covers(s_wed, "engagement")
        for typed in ("Engagement session (1.5 hr)", "Engagement shoot", "Two engagements"):
            A.x("DELETE FROM lines WHERE session_id=? AND amount=350", (s_wed,))
            A.x("INSERT INTO lines (session_id, label, amount) VALUES (?, ?, 350)", (s_wed, typed))
            assert A.plan_covers(s_wed, "engagement"), typed
        assert not A.plan_covers(s_wed, "headshot")
