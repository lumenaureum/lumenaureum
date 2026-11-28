import html
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
S3 = dict(type="s3", bucket="galleries", access_key="AK", secret_key="SK", endpoint="https://s3.example.com")
SAYS = "This web host can't check passphrases. Galleries stay sealed, and anyone with the link can open them."

def _app(tmp_path, monkeypatch, setup_done=True):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import setup
    tested = []
    monkeypatch.setattr(setup, "test_backend", lambda cfg, *a, **kw: tested.append(cfg) or None)
    import hostcheck
    monkeypatch.setattr(hostcheck, "capabilities", lambda *a, **kw: dict(kind="none", features=[], at="now"))
    c = signed_in_client(A, setup_done=setup_done)
    return A, c, tested

@pytest.fixture
def env(tmp_path, monkeypatch):
    return _app(tmp_path, monkeypatch)

def _s3_form(**kw):
    f = dict(gallery_days="30", unopened_days="14", gallery_base_url="https://example.com/g/", gallery_type="s3",
             gallery_bucket="galleries", gallery_access_key="AK", gallery_secret_key="SK",
             gallery_endpoint="https://s3.example.com")
    f.update(kw)
    return f

def _codewords():
    import config as C
    return {t: bool(C.POLICY[t].get("gallery_codeword")) for t in C.SESSION_TYPES}

def _host():
    from vault import web as VW
    return VW.config().get("gallery_host") or {}

def _history(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit ORDER BY id")]
    finally:
        con.close()

def test_the_words_are_the_decided_ones():
    from plugins import gallery as G
    assert G.NO_CODEWORD_CHECK == SAYS
    assert "—" not in SAYS

def test_the_s3_option_says_so_and_offers_the_two_choices_while_codewords_are_on(env):
    A, c, _t = env
    assert all(_codewords().values())
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    block = page.split('<div class="be" data-be="gallery_s3"><div class="hint">A static website', 1)[1].split(
        'data-be="gallery_any"', 1)[0]
    assert SAYS in block and 'id="no-cw-check"' in block
    assert re.search(r'<button type="submit" class="ui-btn ui-btn-quiet" name="s3_codewords_off" value="1" data-confirm="'
                     r'[^"]*">Turn passphrases off for galleries here</button>', block)
    assert re.search(r'<button type="button" class="ui-btn ui-btn-quiet" data-be-pick="gallery_type=sftp">Choose a host that '
                     r'can check passphrases</button>', block)
    assert "data-be-pick" in open(os.path.join(ROOT, "static", "setup.js")).read()

def test_s3_without_a_choice_is_not_saved_and_nothing_is_switched(env):
    A, c, tested = env
    page = html.unescape(c.post("/setup/galleries", data=_s3_form(), environ_base=E).get_data(as_text=True))
    assert SAYS in page.split('class="err"', 1)[1].split("</div>", 1)[0]
    assert not _host() and not tested
    assert all(_codewords().values())
    assert 'name="s3_codewords_off"' in page

def test_turn_codewords_off_saves_the_host_and_turns_them_off_for_every_type(env):
    A, c, tested = env
    r = c.post("/setup/galleries", data=_s3_form(s3_codewords_off="1"), environ_base=E)
    assert r.status_code == 302
    assert _host().get("type") == "s3" and tested
    assert not any(_codewords().values())
    assert any("Turned gallery passphrases off for every session type" in h for h in _history(A))
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert 'id="no-cw-check"' not in page

def test_a_refused_host_test_turns_nothing_off(env, monkeypatch):
    A, c, _t = env
    import setup
    monkeypatch.setattr(setup, "test_backend", lambda cfg, *a, **kw: "the bucket could not be reached")
    c.post("/setup/galleries", data=_s3_form(s3_codewords_off="1"), environ_base=E)
    assert not _host() and all(_codewords().values())

def test_with_codewords_already_off_s3_saves_without_asking(env):
    A, c, _t = env
    import config as C
    import setup
    types = [dict(t, policy=dict(t.get("policy") or {}, gallery_codeword=False)) for t in C.TYPES]
    con = A.connect()
    import json
    r = json.loads((con.execute("SELECT value FROM settings WHERE key='rules'").fetchone() or ["{}"])[0] or "{}")
    r["TYPES"] = types
    con.execute("INSERT INTO settings (key, value) VALUES ('rules', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(r),))
    con.commit()
    con.close()
    with A.app.app_context():
        A.apply_settings()
    assert not any(_codewords().values())
    assert c.post("/setup/galleries", data=_s3_form(), environ_base=E).status_code == 302
    assert _host().get("type") == "s3"
    assert setup.CW_OFF_WORDS not in " ".join(_history(A))

def test_another_host_is_never_asked(env):
    A, c, tested = env
    f = dict(gallery_days="30", unopened_days="14", gallery_base_url="https://example.com/g/", gallery_type="local",
             gallery_path=str(os.environ["HOME"]), gallery_init="1")
    c.post("/setup/galleries", data=f, environ_base=E)
    assert _host().get("type") == "local" and all(_codewords().values())

def _on_s3_already(A):
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host=dict(S3)))
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://example.com/g/') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit()
    con.close()

def test_a_studio_already_on_s3_is_told_on_online_galleries_and_in_the_alerts(env):
    A, c, _t = env
    _on_s3_already(A)
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    block = page.split('id="no-cw-check"', 1)[1].split("</div></div>", 1)[0]
    assert SAYS in block
    assert re.search(r'formaction="/setup/galleries/codewords-off" formnovalidate data-confirm="[^"]*">Turn passphrases off '
                     r'for galleries here</button>', block)
    assert re.search(r'<a class="ui-btn ui-btn-quiet" href="[^"]*replace=gallery[^"]*">Choose a host that can check '
                     r'passphrases</a>', block)
    with A.app.test_request_context():
        texts = [a["text"] for a in A.system_alerts()]
    assert any(t.startswith(SAYS) for t in texts)

def test_turning_codewords_off_there_clears_the_notice_and_the_alert(env):
    A, c, _t = env
    _on_s3_already(A)
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, stage, gallery_codeword) VALUES (1, 1, 'family', 'shot', "
                "'maple lantern heron')")
    con.commit()
    con.close()
    r = c.post("/setup/galleries/codewords-off", environ_base=E)
    assert r.status_code == 302
    assert not any(_codewords().values())
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert 'id="no-cw-check"' not in page
    assert "Passphrases are off for galleries here. Cleared the passphrase on 1 session not online yet." in page
    with A.app.test_request_context():
        assert not any(a["text"].startswith(SAYS) for a in A.system_alerts())
    h = _history(A)
    assert sum("Turned gallery passphrases off" in x for x in h) == 1

def test_codewords_off_endpoint_refuses_when_the_host_is_not_s3(env):
    A, c, _t = env
    assert c.post("/setup/galleries/codewords-off", environ_base=E).status_code == 400
    assert all(_codewords().values())

def _codeword_gallery(A, gid, host_fp=None, state="built"):
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO sessions (id, household_id, type, gallery_id, gallery_codeword) VALUES (1, 1, 'family', ?, ?)",
                (gid, "maple lantern heron"))
    con.execute("INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, n_files, host_fp, uploaded_at) "
                "VALUES (1, 'k', 'e', '2026-11-17', ?, 3, ?, ?)", (state, host_fp, "2026-11-17" if host_fp else None))
    con.commit()
    con.close()

def test_every_way_up_refuses_a_codeword_gallery_on_s3(env, monkeypatch):
    A, c, _t = env
    _on_s3_already(A)
    G = A.app.view_functions["build_gallery"].__globals__
    gid = "g-galStatic207bAAAAAAAAA"
    _codeword_gallery(A, gid)
    opened = []
    monkeypatch.setitem(G, "gallery_host", lambda *a: opened.append(1))
    with pytest.raises(RuntimeError) as e:
        G["upload_gallery"](gid)
    assert str(e.value) == G["WORD_REFUSED_STATIC"] and not opened
    con = A.connect()
    con.execute("UPDATE sessions SET gallery_codeword='' WHERE id=1")
    con.commit()
    con.close()
    assert G["codeword_of"](gid) == ""

def test_move_galleries_leaves_a_codeword_gallery_where_its_codeword_is_checked(env, monkeypatch):
    A, c, _t = env
    _on_s3_already(A)
    G = A.app.view_functions["build_gallery"].__globals__
    gid = "g-galMoved207bAAAAAAAAAA"
    _codeword_gallery(A, gid, host_fp="an-earlier-php-host", state="up")
    out = G["OUT_ROOT"] / gid
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.bin").write_bytes(b"x")
    uploaded = []
    monkeypatch.setitem(G, "upload_gallery", lambda *a, **kw: uploaded.append(a))
    monkeypatch.setitem(G, "gallery_host", lambda *a: object())
    import jobs
    ran = []

    def _start(app_, key, label, fn, **kw):
        job = {}
        with app_.test_request_context():
            ran.append(fn(job))
    monkeypatch.setattr(jobs, "start", _start)
    c.post("/galleries/move", environ_base=E)
    assert ran and not uploaded
    assert "Left on the earlier host, as this web host can't check passphrases: Lane." in ran[0]
    con = A.connect()
    fp = con.execute("SELECT host_fp FROM galleries WHERE session_id=1").fetchone()[0]
    con.close()
    assert fp == "an-earlier-php-host"

def test_no_dashes_or_jargon_in_the_new_words():
    page = open(os.path.join(ROOT, "templates", "_settings_ui.html")).read()
    block = page.split("macro no_cw_check", 1)[1].split("endmacro", 1)[0]
    assert "—" not in block and "PHP" not in block
