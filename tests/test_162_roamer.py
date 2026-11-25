import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_user_testing_fixes import _app, E
from test_162_browser import browser, env

def _page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def test_inquiry_type_picker_says_not_listed_once(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    page = _page(c, "/inquiry?hid=1")
    assert page.count("Not listed?") == 1, re.findall(r"Not listed\?.{0,90}", page)

def test_all_type_settings_link_on_the_walk_does_not_lead_back_to_the_walk(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("DELETE FROM settings WHERE key='walk'")
    con.commit()
    con.close()
    walk = _page(c, "/settings/type/family/walk/0")
    m = re.search(r'<a class="go" href="(/settings/type/family[^"]*)">All Family settings', walk)
    assert m, "the walk offers All Family settings"
    r = c.get(m.group(1).replace("&amp;", "&"), environ_base=E)
    assert r.status_code == 200, "All Family settings › redirects back to the walk-through page it was clicked on"
    assert c.get("/settings/type/family", environ_base=E).status_code == 302

def test_offer_prints_note_that_asks_for_a_build_has_the_rebuild_action(tmp_path, monkeypatch):

    import prints_env as PE
    from prints_env import GID
    import shutil
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    e = PE.make(tmp_path, monkeypatch, proofs=True)
    e.G.build(GID, e.tmp / "src", e.key, picks=dict(post=True), brand=dict(client="The Sample Family"))
    e.G.mark_online(GID)
    src = e.G.PHOTOS_ROOT / "sample-family"
    shutil.copytree(e.tmp / "src", src)
    con = e.A.connect()
    con.execute("UPDATE galleries SET source_dir=? WHERE session_id=1", (str(src),))
    con.commit()
    con.close()
    page = e.c.get("/session/1", environ_base=PE.E, follow_redirects=True).get_data(as_text=True)
    assert "Build the gallery again to offer prints from these proofs." in page
    block = page.split('id="gal-prints-rebuild"', 1)
    assert len(block) == 2, "no Rebuild beside the note"
    assert "Rebuild to offer prints" in block[1].split("</div>", 1)[0]

def test_host_table_and_paragraph_agree_when_the_gate_does_not_hold(monkeypatch):

    sys.path.insert(0, ROOT)
    import hostcheck
    monkeypatch.setattr(hostcheck, "probe", lambda h, b: hostcheck.Probe(kind="ok", version="8.2", wait=2, detail=""))
    monkeypatch.setattr(hostcheck, "htaccess_honored", lambda h, b: False)
    monkeypatch.setattr(hostcheck, "gate_holds", lambda h, b, g=None: False)
    monkeypatch.setattr(hostcheck, "cert_state", lambda b, p: "public")
    caps = hostcheck.capabilities(None, "https://www.example.com/g/")
    feats = {k: (works, note) for k, _l, works, note in caps["features"]}
    assert feats["expiry"] == (False, hostcheck.GATE_NOTE)
    assert feats["codeword"] == (False, hostcheck.GATE_NOTE)
    assert feats["picks"][0] and feats["inquiry"][0] and feats["prints"][0]
    monkeypatch.setattr(hostcheck, "gate_holds", lambda h, b, g=None: True)
    assert all(w for _k, _l, w, _n in hostcheck.capabilities(None, "https://www.example.com/g/")["features"])

def test_appearance_fits_a_phone(request):

    from test_162_browser import BASE, _tab
    browser, (A, c) = request.getfixturevalue("browser"), request.getfixturevalue("env")
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/settings?tab=appearance")
        rights = p.evaluate("[...document.querySelectorAll('.sui-theme')].map(e => e.getBoundingClientRect().right)")
        assert len(rights) == 3 and max(rights) <= 390, rights
        assert p.evaluate("document.querySelector('main').scrollWidth") <= 390
    finally:
        ctx.close()

def test_sample_question_comes_before_the_invoice_number(request, tmp_path, monkeypatch):

    from test_162_browser import BASE, _tab
    from test_money_docs_101 import _line
    from test_user_testing_fixes import _new
    browser = request.getfixturevalue("browser")
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "300")

    def issued():
        con = A.connect()
        try:
            return con.execute("SELECT number FROM doc_numbers WHERE session_id=? AND kind='INV'", (sid,)).fetchall()
        finally:
            con.close()
    ctx, p = _tab(browser, c, 1280)
    try:
        asked = []
        p.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        p.goto(BASE + f"/session/{sid}/invoice")
        assert p.locator(".draft").count()
        p.get_by_role("button", name="Issue and email it").click()
        p.wait_for_timeout(300)
        assert len(asked) == 1 and asked[0].startswith("This still prints SAMPLE"), asked
        assert not issued(), "No to the SAMPLE question left the invoice issued"
    finally:
        ctx.close()
    ctx, p = _tab(browser, c, 1280)
    try:
        asked = []
        p.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        p.goto(BASE + f"/session/{sid}/invoice")
        p.get_by_role("button", name="Issue and email it").click()
        p.wait_for_load_state()
        p.wait_for_timeout(800)
        assert issued()
        assert len(asked) == 1, asked
        assert p.evaluate("document.body.dataset.sampleOk") == "1"
    finally:
        ctx.close()

def test_moving_a_date_says_the_new_date(tmp_path, monkeypatch):

    from test_user_testing_fixes import _new
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{sid}/quick", data={"act": "date", "shoot_date": "2031-06-12", "start_time": "14:00",
                                          "back": f"/session/{sid}/edit"}, environ_base=E)
    page = _page(c, f"/session/{sid}/edit")
    assert "Set: Thu Jun 12, 2031, 2:00 pm." in page
    c.post(f"/session/{sid}/quick", data={"act": "date", "shoot_date": "2031-06-19", "start_time": "14:00",
                                          "back": f"/session/{sid}/edit"}, environ_base=E)
    page = _page(c, f"/session/{sid}/edit")
    assert "Moved to Thu Jun 19, 2031, 2:00 pm." in page
