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

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "test_backend", lambda cfg, *a, **kw: None)
    import hostcheck
    monkeypatch.setattr(hostcheck, "capabilities", lambda *a, **kw: dict(kind="none", features=[], at="now"))
    return A, signed_in_client(A)

def _sql(A, s, args=()):
    con = A.connect()
    try:
        rows = con.execute(s, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def _on_s3(A):
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host=dict(S3)))
    _sql(A, "INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://example.com/g/') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")

def _codewords():
    import config as C
    return {t: bool(C.POLICY[t].get("gallery_codeword")) for t in C.SESSION_TYPES}

def _sessions(A):

    A.app.view_functions["build_gallery"]
    _sql(A, "INSERT INTO households (id, name) VALUES (1, 'Lane')")
    for sid, stage in ((1, "booked"), (2, "shot"), (3, "delivered"), (4, "delivered"), (5, "closed")):
        _sql(A, "INSERT INTO sessions (id, household_id, type, stage, gallery_id, gallery_codeword) VALUES (?, 1, 'family', ?, ?, ?)",
             (sid, stage, f"g-walk{sid}", f"maple lantern heron {sid}"))
    _sql(A, "INSERT INTO galleries (session_id, key, ekey, built_at, upload_state) VALUES (2, 'k', 'e', '2026-11-17', 'built')")
    _sql(A, "INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, uploaded_at, cw_fixed) "
            "VALUES (3, 'k', 'e', '2026-11-17', 'up', '2026-11-18', 'word')")
    _sql(A, "INSERT INTO galleries (session_id, key, ekey, built_at, upload_state, uploaded_at, removed_at, cw_fixed) "
            "VALUES (4, 'k', 'e', '2026-11-17', 'error', '2026-11-18', '2026-11-20', 'word')")

def _words(A):
    return dict(_sql(A, "SELECT id, gallery_codeword FROM sessions"))

def test_turning_codewords_off_clears_sessions_not_online_yet_and_keeps_the_rest(env):
    A, c = env
    _on_s3(A)
    _sessions(A)
    c.post("/setup/galleries/codewords-off", environ_base=E)
    w = _words(A)
    assert w[1] == "" and w[2] == ""
    assert w[3] and w[4]
    assert w[5]
    assert not any(_codewords().values())
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    assert ("Passphrases are off for galleries here. Cleared the passphrase on 2 sessions not online yet. 2 galleries "
            "already online keep their passphrase.") in page

def test_the_confirm_says_how_many_sessions_change(env):
    A, c = env
    _on_s3(A)
    _sessions(A)
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    m = re.search(r'formaction="/setup/galleries/codewords-off" formnovalidate data-confirm="([^"]*)"', page)
    assert m and m.group(1) == ("Turn passphrases off for every session type? 2 sessions lose their passphrase, as their "
                                "galleries are not online yet. Galleries already online keep theirs.")
    from plugins import gallery as G
    assert G.codewords_off_confirm(1).startswith("Turn passphrases off for every session type? 1 session loses its "
                                                 "passphrase, as its gallery is not online yet.")
    assert G.codewords_off_confirm(0) == ("Turn passphrases off for every session type? No session changes. Galleries "
                                          "already online keep theirs.")

def test_choosing_s3_in_setup_clears_them_too(env):
    A, c = env
    _sessions(A)
    f = dict(gallery_days="30", unopened_days="14", gallery_base_url="https://example.com/g/", gallery_type="s3",
             gallery_bucket="galleries", gallery_access_key="AK", gallery_secret_key="SK",
             gallery_endpoint="https://s3.example.com")
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    assert 'name="s3_codewords_off" value="1" data-confirm="Turn passphrases off for every session type? 2 sessions' in page
    c.post("/setup/galleries", data=dict(f, s3_codewords_off="1"), environ_base=E)
    w = _words(A)
    assert w[1] == "" and w[2] == "" and w[3]

def _type_form(**kw):
    import config as C
    f = {"questions": "\n".join(q for _, q in C.INTAKE["family"]), "notice_hours": "48", "late_minutes": "30",
         "delivery": "online", "weather": "rain date", "license_use": "x"}
    f.update(kw)
    return f

def test_the_switch_says_it_with_the_two_choices_on_s3_only(env):
    A, c = env
    page = c.get("/settings/type/family", environ_base=E).get_data(as_text=True)
    assert 'id="no-cw-check"' not in page
    _on_s3(A)
    _sessions(A)
    page = html.unescape(c.get("/settings/type/family", environ_base=E).get_data(as_text=True))
    block = page.split('id="f-gallery_codeword"', 1)[1].split('name="indoors"', 1)[0]
    assert 'id="no-cw-check" class="sui-cw" data-show-if="gallery_codeword=1"' in block
    assert SAYS in block
    assert re.search(r'<button type="submit" class="ui-btn ui-btn-quiet" name="cw_all_off" value="1" data-confirm="Turn '
                     r'passphrases off for every session type\? 2 sessions[^"]*">Turn passphrases off for galleries here</button>',
                     block)
    assert re.search(r'<a class="ui-btn ui-btn-quiet" href="/setup/galleries\?replace=gallery#f">Choose a host that can '
                     r'check passphrases</a>', block)

def test_turning_a_type_codeword_back_on_while_on_s3_is_allowed(env):
    A, c = env
    _on_s3(A)
    c.post("/setup/galleries/codewords-off", environ_base=E)
    assert not any(_codewords().values())
    r = c.post("/settings/type/family", data=_type_form(gallery_codeword="1"), environ_base=E)
    assert r.status_code == 302 and _codewords()["family"]
    page = html.unescape(c.get("/settings/type/family", environ_base=E).get_data(as_text=True))
    assert SAYS in page and "Turn passphrases off for galleries here" in page

def test_turn_off_at_the_switch_saves_the_type_first_then_turns_them_off(env):
    A, c = env
    _on_s3(A)
    _sessions(A)
    r = c.post("/settings/type/family", data=_type_form(gallery_codeword="1", notice_hours="72", cw_all_off="1"),
               environ_base=E)
    assert r.status_code == 302
    import config as C
    assert C.POLICY["family"]["notice_hours"] == 72
    assert not any(_codewords().values())
    assert _words(A)[1] == ""

def test_the_walk_through_screen_says_it_too(env):
    A, c = env
    _on_s3(A)
    import walkthrough as W
    import config as C
    t = next(t_ for t_ in C.TYPES if t_["key"] == "family")
    n = [s["id"] for s in W.steps_for(t)].index("codeword")
    page = html.unescape(c.get(f"/settings/type/family/walk/{n}", environ_base=E).get_data(as_text=True))
    assert SAYS in page and 'name="cw_all_off"' in page
    c.post(f"/settings/type/family/walk/{n}", data={"gallery_codeword": "1", "cw_all_off": "1"}, environ_base=E)
    assert not any(_codewords().values())

def test_a_type_switch_off_s3_never_turns_others_off(env):
    A, c = env
    c.post("/settings/type/family", data=_type_form(gallery_codeword="1", cw_all_off="1"), environ_base=E)
    assert all(_codewords().values())

def test_your_nas_says_a_mounted_share_is_a_folder(env):
    A, c = env
    page = html.unescape(c.get("/setup/storage?replace=primary", environ_base=E).get_data(as_text=True))
    lines = re.findall(r'data-be="(?:primary|secondary)_sftp\.nas">(.*?)</div>', page, re.S)
    assert lines and all(l_.endswith("<br>Already mounted into the app? Choose A folder instead.") for l_ in lines)
    assert "A folder on this machine or a mounted share" in page

PW_HOST = dict(type="sftp", host="example.com", user="u", root="example.com/g", auth="password", password="pw",
               key_file="", host_key="ssh-ed25519 AAAAtest")

def _on_password_host(A):
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host=dict(PW_HOST)))
    _sql(A, "INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://example.com/g/') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")

def test_a_password_host_is_offered_the_apps_own_key_with_short_steps(env):
    A, c = env
    _on_password_host(A)
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    block = page.split('id="app-key-offer"', 1)[1].split("</div></div>", 1)[0]
    assert "Signs in with a password. The app's own key is safer." in block
    assert block.count("<li>") == 3 and 'data-copy="#pub_switch"' in block
    assert re.search(r'<button type="submit" class="ui-btn ui-btn-quiet" formaction="/setup/galleries/app-key" '
                     r'formnovalidate>Switch to the app\'s own key</button>', block)

def test_switching_tests_the_key_first_and_then_forgets_the_password(env, monkeypatch):
    A, c = env
    _on_password_host(A)
    import setup
    from vault import web as VW
    tried = []
    monkeypatch.setattr(setup, "test_backend", lambda cfg, *a, **kw: tried.append(dict(cfg)) or None)
    c.post("/setup/galleries/app-key", environ_base=E)
    gh = VW.config()["gallery_host"]
    assert tried and "password" not in tried[0] and tried[0].get("private_key")
    assert "auth" not in gh and "password" not in gh and gh["private_key"] and gh["host_key"] == PW_HOST["host_key"]
    assert gh["host"] == "example.com" and gh["root"] == "example.com/g"
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    assert "Switched to the app's own key." in page and 'id="app-key-offer"' not in page
    con = A.connect()
    assert any("Switched the web host to the app's own key" in r[0] for r in con.execute("SELECT action FROM audit"))
    con.close()

def test_a_key_that_does_not_work_yet_changes_nothing(env, monkeypatch):
    A, c = env
    _on_password_host(A)
    import setup
    from vault import web as VW
    monkeypatch.setattr(setup, "test_backend", lambda cfg, *a, **kw: "the server refused the key")
    c.post("/setup/galleries/app-key", environ_base=E)
    assert VW.config()["gallery_host"]["password"] == "pw"
    page = html.unescape(c.get("/setup/galleries", environ_base=E).get_data(as_text=True))
    assert "Still signing in with the password, as the app's own key didn't work: the server refused the key" in page
    con = A.connect()
    assert not any("app's own key" in r[0] for r in con.execute("SELECT action FROM audit"))
    con.close()

def test_a_key_host_is_not_offered_it_and_presets_keep_password_first(env):
    A, c = env
    _on_password_host(A)
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host=dict(PW_HOST, auth="", password="")))
    assert 'id="app-key-offer"' not in c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert c.post("/setup/galleries/app-key", environ_base=E).status_code == 400
    import setup
    assert setup.PROVIDERS["sftp.dreamhost"]["keymode"] == "password" == setup.PROVIDERS["sftp.bluehost"]["keymode"]

@pytest.fixture
def studio(tmp_path, monkeypatch):

    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    for k, v in (("photographer_name", "Pat Lens"), ("gallery_base_url", "https://www.example.com/g/")):
        _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    web = tmp_path / "web"
    (web / "g").mkdir(parents=True)
    (web / "g" / ".vault-root").write_text("")
    from vault import web as VW
    VW.write_config(dict(VW.config(), gallery_host={"type": "local", "path": str(web / "g")}))
    from plugins import studio_page as S
    c.post("/settings/studio-page/switch", data={"use": "1"}, environ_base=E)
    return A, c, S

def _contrast(a, b):
    import settings_io
    return settings_io._contrast(settings_io._rgb(a), settings_io._rgb(b))

def _button_colors(snippet):
    m = re.search(r"background:(#[0-9a-f]{6});color:(#[0-9a-f]{6});", snippet)
    return m.group(1), m.group(2)

def test_the_button_is_gold_without_an_accent_with_words_that_read_on_it():
    from plugins import studio_page as S
    bg, fg = _button_colors(S.widget_snippet("https://www.example.com/inquiry-form.php", "Pat", button=True))
    assert bg == "#b8862f" and _contrast(bg, fg) >= 4.5

@pytest.mark.parametrize("accent", ["#1a5276", "#f4d03f", "#808080", "#7a3e9d", "#c0392b", "#ffffff", "#000000"])
def test_every_accent_gets_words_that_read_at_4_5_to_1(accent):
    from plugins import studio_page as S
    bg, fg = _button_colors(S.widget_snippet("https://x.example/inquiry-form.php", "Pat", button=True, accent=accent))
    assert bg == accent and _contrast(bg, fg) >= 4.5

def test_an_accent_that_is_not_a_color_is_gold():
    from plugins import studio_page as S
    for bad in ("red", "#12345", "#12345g", "#123456;background:url(x)", ""):
        assert _button_colors(S.widget_snippet("https://x.example/f.php", "Pat", button=True, accent=bad))[0] == "#b8862f"

def test_the_accent_chosen_on_the_studio_page_reaches_the_pasted_button_and_the_page(studio):
    A, c, S = studio
    page = html.unescape(c.get("/settings/studio-page", environ_base=E).get_data(as_text=True))
    assert 'name="site_accent_own" value="0" checked' in page and 'data-show-if="site_accent_own=1"' in page
    c.post("/settings/studio-page/save", data={"site_name": "Pat Lens", "shows_apart": "1", "site_accent_own": "1", "site_accent": "#1A5276"},
           environ_base=E)
    assert _sql(A, "SELECT value FROM settings WHERE key='site_accent'")[0][0] == "#1a5276"
    c.post("/settings/studio-page/widget", data={"what": "switch", "site_widget": "1"}, environ_base=E)
    c.post("/settings/studio-page/widget", data={"what": "button", "site_widget_button": "1"}, environ_base=E)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    snippet = html.unescape(page.split('id="sp-snippet"', 1)[1].split(">", 1)[1].split("</textarea>", 1)[0])
    assert _button_colors(snippet) == ("#1a5276", "#ffffff")
    assert 'name="site_accent_own" value="1" checked' in page
    c.post("/settings/studio-page/save", data={"site_name": "Pat Lens", "shows_apart": "1", "site_accent_own": "0", "site_accent": "#1a5276"},
           environ_base=E)
    assert _sql(A, "SELECT value FROM settings WHERE key='site_accent'")[0][0] == ""

def test_the_studio_page_css_takes_the_accent():
    from plugins import studio_page as S
    assert "--gold: {accent};" in S.CSS
