import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, one, run

import client_csv as CC

E = {"REMOTE_ADDR": "127.0.0.1"}

def test_there_is_no_client_list_download(env):
    A, c = env
    assert c.get("/clients/export.csv", environ_base=E).status_code == 404
    assert "clients_export" not in A.app.view_functions
    assert "clients_export" not in A.WRITING_GETS
    assert not hasattr(A, "clients_file_rows")
    assert not hasattr(CC, "write") and not hasattr(CC, "EXPORT_COLS")
    assert not any("client list" in p_ for p_ in A.SECURITY_LINES)
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert "Download the client list" not in page and "Import clients from a file" in page
    imp = c.get("/clients/import", environ_base=E).get_data(as_text=True)
    assert "Download the client list" not in imp

def test_the_security_doc_no_longer_describes_a_client_list_download():
    doc = open(os.path.join(ROOT, "docs", "security.md"), encoding="utf-8").read()
    assert "Download the client list" not in doc and "**Import clients from a file** treats the file" in doc

TITLES = {"inbox": "Studio inbox", "backup": "Studio backup", "storage": "Studio storage", "closing": "Gallery closing",
          "update": "Studio updated", "served": "Gallery page changed", "cred": "Studio sign-in changed",
          "newmaster": "Studio master key", "calendar": "Studio calendar"}

def test_every_studio_notice_keeps_its_title():
    import inboxnotice as N
    import locknotice as L
    assert N.TITLE == TITLES["inbox"] and L.TITLE == "Studio locked"
    assert {k: v[2] for k, v in N.STATUS.items()} == {k: v for k, v in TITLES.items() if k != "inbox"}

def test_no_notice_body_carries_a_count():
    import re
    from datetime import datetime
    import inboxnotice as N
    noon = datetime(2026, 11, 21, 12, 0)
    bodies = [N.text({"inquiry": 3, "picks": 2, "print": 7}), N.text({"print": 1})]
    facts = {"backup": ["failed", "late"], "storage": [True], "closing": [1, 4], "update": ["1.5.5|1.5.4|"],
             "served": [[7], [3, 7, 9]], "cred": ["2026-11-21T10:00:00"], "newmaster": ["2026-11-21T10:00:00"]}
    for kind, vals in facts.items():
        for v in vals:
            bodies.append(N.STATUS[kind][3](v, noon)[1])
    assert bodies[0] == bodies[1] == "Something new came in. Sign in to your studio to see it."
    for b in bodies:

        b = b.replace("48 hours", "").replace(f"{N.STORAGE_LOW_PCT}%", "")
        assert not re.search(r"\d", b), b
    assert len({b for b in bodies[2:] if "galleries close" in b}) == 1

def _set_ntfy(A, server, topic="studio-x7q2"):
    run(A, "INSERT INTO settings (key, value) VALUES ('ntfy_server', ?), ('ntfy_topic', ?) "
           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (server, topic))

def test_the_public_server_is_recognized():
    import locknotice as L
    for s in ("https://ntfy.sh", "https://NTFY.sh/", "https://ntfy.sh.", "https://eu.ntfy.sh"):
        assert L.public_server(s), s
    for s in ("https://ntfy.example.com", "http://192.168.1.5:8080", "https://notntfy.sh", "", None, "https://[bad"):
        assert not L.public_server(s), s

def test_settings_recommends_a_private_server_and_warns_on_the_public_one(env):
    A, c = env
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    assert "Use a private ntfy server" in page
    assert "anyone who learns the topic can read these notices" not in page
    _set_ntfy(A, "https://ntfy.sh")
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    assert "This is the public ntfy server: anyone who learns the topic can read these notices." in page
    _set_ntfy(A, "https://ntfy.example.com")
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    assert "This is the public ntfy server" not in page

def test_saving_the_public_server_says_a_private_one_is_safer(env, monkeypatch):
    A, c = env
    c.post("/security/notice", data={"act": "save", "server": "https://ntfy.sh", "topic": "studio-x7q2"}, environ_base=E)
    with c.session_transaction() as s:
        said = s.get("notice", "")
    assert said.startswith("Saved.") and "a private server of your own is safer" in said
    c.post("/security/notice", data={"act": "save", "server": "https://ntfy.example.com", "topic": "studio-x7q2"},
           environ_base=E)
    with c.session_transaction() as s:
        assert "private server" not in s.get("notice", "")

KINDS = ["Studio locked after a restart", "A new master key was asked for without the old one", "Something new in the studio inbox", "A backup failed or is late",
         "Storage running low", "A gallery closes tomorrow", "An update was installed",
         "A gallery page on your web host changed", "Your password, master key, or recovery codes changed"]

def _page(c):
    return c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)

def test_every_notice_shows_before_the_service_is_set_up_switched_off_with_the_reason(env):
    A, c = env
    import re
    page = _page(c)
    kinds = page.split('id="notice-kinds"', 1)[1].split("</section>", 1)[0]
    assert "These can be ticked once the notice service above is saved." in kinds
    for label in KINDS:
        m = re.search(r'<input type="checkbox"( checked)? disabled>' + re.escape(label), kinds)
        assert m, label
    assert "data-autosubmit" not in kinds and "<form" not in kinds
    assert re.search(r'checked disabled>Your password, master key', kinds)
    assert not re.search(r'checked disabled>Storage running low', kinds)
    _set_ntfy(A, "https://ntfy.example.com")
    kinds = _page(c).split('id="notice-kinds"', 1)[1].split("</section>", 1)[0]
    assert "These can be ticked" not in kinds and kinds.count("data-autosubmit") == len(KINDS) - 2
    assert '<input type="checkbox" checked disabled>Studio locked after a restart' in kinds
    assert '<input type="checkbox" checked disabled>A new master key was asked for' in kinds
    assert "3 new" not in kinds and "how many came in" not in kinds

def test_the_menu_and_subtitle_name_every_notice_not_only_the_locked_one(env):
    A, c = env
    import settings_registry as R
    page = _page(c)
    assert "the locked-studio notice" not in page
    reg = R.page("notifications")
    assert "inbox" in reg["sub"] and "backups" in reg["sub"] and "inbox" in reg["what"]
    assert "something new in the inbox" in page
    assert "phone notices: locked studio, inbox, backups, galleries" in c.get("/settings", environ_base=E).get_data(as_text=True)

def test_no_paragraph_repeats_the_bubble_and_the_labels_are_plain(env):
    A, c = env
    page = _page(c)
    sec = page.split('id="notice"', 1)[1].split("</section>", 1)[0]
    assert 'class="sui-tip"' not in sec and "Optional. A message to your phone" not in sec
    for plain in ("Notice service address", "Your private channel name", "Password for the service (optional)"):
        assert plain in sec, plain
    for jargon in (">ntfy server<", ">Topic<", ">Access token"):
        assert jargon not in sec, jargon

    assert "The ntfy topic" in sec and "An ntfy access token" in sec and "The address of your ntfy server" in sec
    _set_ntfy(A, "https://ntfy.example.com")
    card = _page(c).split('id="conn-ntfy"', 1)[1].split("</section>", 1)[0]
    assert "<b>Notice service</b>" in card and "<dt>Channel name</dt>" in card and "<dt>Topic</dt>" not in card

def test_the_setup_form_says_unsaved_changes_with_discard(env):
    A, c = env
    sec = _page(c).split('id="notice"', 1)[1].split("</section>", 1)[0]
    form = sec.split('id="ntfy-server"', 1)[1].split("</form>", 1)[0]
    assert 'data-ui-unsaved hidden role="status">Unsaved changes<button type="reset" class="ui-btn ui-btn-quiet">Discard changes</button>' in form

def test_unsaved_changes_shows_while_typing_and_discard_puts_it_back(env):
    from test_settings_fixes_15 import BASE, _tab
    try:
        from playwright import sync_api
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:
        import pytest
        pytest.skip(f"chromium is not available: {e}")
    A, c = env
    try:
        ctx, p = _tab(b, c, 390)
        p.goto(BASE + "/settings?tab=notifications")
        bar = p.locator("[data-ui-unsaved]")
        assert not bar.is_visible()
        p.fill("#ntfy-server", "https://ntfy.example.com")
        assert bar.is_visible() and "Unsaved changes" in bar.inner_text()
        p.click("text=Discard changes")
        p.wait_for_timeout(50)
        assert p.input_value("#ntfy-server") == "" and not bar.is_visible()
        p.fill("#ntfy-topic", "x")
        p.fill("#ntfy-topic", "")
        assert not bar.is_visible()
        ctx.close()
    finally:
        b.close()
        pw.stop()
