import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_decided_edge_cases import env, run
from test_user_testing_fixes import _app, _new, _q

E = {"REMOTE_ADDR": "127.0.0.1"}
CSS = (ROOT / "static" / "style.css").read_text()
TPL = ROOT / "templates"

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def test_the_settings_list_marks_only_the_page_shown(env):
    A, c = env
    for url, label in (("/settings?tab=notifications", "Notifications"), ("/vault", "Backups"), ("/security", "Security")):
        nav = _page(c, url).split('class="settabs', 1)[1].split("</nav>", 1)[0]
        on = re.findall(r'<a href="[^"]*" class="ui-row on"[^>]*><span class="ui-row-main"><b>([^<]+)</b>', nav)
        assert on == [label], (url, on)

    assert ".settabs a.ui-row:hover { background: none; }" in CSS
    assert re.search(r"\.settabs a\.ui-row:hover b \{[^}]*text-decoration: underline", CSS)
    top = int(re.search(r"\.settabs \{ position: sticky; top: (\d+)px", CSS).group(1))
    assert top >= 70

def test_the_settings_list_opens_at_its_group_heading():
    js = (ROOT / "static" / "ui.js").read_text()
    assert "on.closest('.sg')" in js

def test_backups_speaks_plainly_and_uses_the_shared_buttons(env):
    A, c = env
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    page = _page(c, "/vault")
    assert "Sealed sessions" not in page and "Master key set<" not in page
    assert "Sessions in the safe copy" in page or "No master key yet" in page
    src = (TPL / "vault.html").read_text()
    assert 'class="quiet"' not in src and 'class="grow"' not in src and 'class="primary"' not in src
    assert '<button class="ui-btn ui-btn-quiet">Check the newest backup opens</button>' in src
    assert 'class="ui-grow"' in src

    assert "ui.chip('Master key: set up', 'done')" in src and "master[:18] }}...{{" not in src
    import vault.web as VW
    assert VW.OBJ_WORDS["session"] == "Sessions in the safe copy"

    setup = (TPL / "setup.html").read_text()
    assert '<div class="ui-btnrow"><button class="ui-btn ui-btn-primary">Save the backup time</button></div>' in setup

def test_security_idle_time_button_says_what_it_saves(env):
    A, c = env
    page = _page(c, "/security")
    assert ">Save the idle time</button>" in page
    assert not re.search(r'id="idle".*?<button class="ui-btn ui-btn-primary">Save</button>', page, re.S)

def test_import_uses_the_shared_file_picker_with_a_plain_label(env):
    A, c = env
    page = _page(c, "/clients/import")
    assert "CSV file" not in page and "Your client list (a spreadsheet file)" in page
    assert 'class="ui-filepick" data-ui-filepick' in page and "<u>Browse</u>" in page
    inp = re.search(r'<input type="file"[^>]*>', page).group(0)
    assert 'class="vh"' in inp and 'name="file"' in inp and "required" in inp
    assert "file_pick(" in (TPL / "dev_components.html").read_text()

def test_chip_rows_show_a_scroll_cue_not_a_cut_chip():
    js = (ROOT / "static" / "filters.js").read_text()
    assert "ui-filter-more" in js and "data-more-right" in js and "cue(bar)" in js
    assert ".ui-filter-row[data-more-right] { mask-image" in CSS

    assert "padding-bottom: 2px; mask-image" not in CSS

def test_history_filters_stack_chips_in_order_and_says_entries(env):
    A, c = env
    run(A, """
        INSERT INTO sessions (id, household_id, type, stage) VALUES (5, 1, 'portrait', 'inquiry');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-20 10:00:00', 1, 5, 'Something odd', 'Owner');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-20 10:01:00', 1, 5, 'Something else odd', 'Owner');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-20 10:02:00', 1, 5, 'Recorded a $45.00 cash payment', 'Owner');
        INSERT INTO audit (at, household_id, session_id, action, who) VALUES ('2026-11-20 10:03:00', 1, 5, 'Marked the agreement signed', 'Owner');""")
    page = _page(c, "/history")
    assert re.search(r'<form method="get" action="/history" class="ui-form-grid"', page)
    assert ">Show these entries</button>" in page and "Show these lines" not in page
    assert re.search(r'data-many="entries">4 entries</span>', page)
    chips = re.findall(r'data-f-key="kind" data-f-val="([^"]+)"', page)
    assert chips and chips[-1] == "Other" and chips.index("Payments") < chips.index("Other"), chips

def test_a_payment_line_reads_as_a_sentence(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "45", "method": "cash"},
           environ_base=E)
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "20", "method": "check", "ref": "101"},
           environ_base=E)
    lines = [r[0] for r in _q(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
    assert "Recorded a $45.00 cash payment" in lines, lines
    assert "Recorded a $20.00 check deposit #101" in lines, lines

def test_an_upload_line_names_its_files_in_name_order(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    names = ["AM_01.jpg", "AM_10.jpg", "AM_03.jpg", "AM_02.jpg"]
    assert sorted(names, key=A.file_name_order) == ["AM_01.jpg", "AM_02.jpg", "AM_03.jpg", "AM_10.jpg"]
    sid = _new(A, c, "family", subject_name="Ava")
    with A.app.test_request_context():
        A.db()
        for n in ["AM_01.jpg", "AM_03.jpg", "AM_02.jpg", "AM_04.jpg"]:
            A._audit_upload_locked(sid, dict(kind="raw", name=n, size=1000))
    note = _q(A, "SELECT note FROM audit WHERE session_id=? AND action LIKE 'Uploaded%' ORDER BY id DESC", (sid,))[0][0]
    assert note == "AM_01.jpg, AM_02.jpg, AM_03.jpg and 1 more", note

def test_gallery_block_names_the_real_step_and_the_codeword_line_is_plain():
    src = (TPL / "_gallery_block.html").read_text()
    assert "Edit row" not in src and "on the Upload edited photos step" in src
    assert "None yet." not in src and "Choose it with the client, or have the app make one." in src

def test_thumbnail_delete_sits_on_the_picture_and_selected_delete_stays_in_reach():
    assert re.search(r"\.ui-thumb-del \{ position: absolute; right: 4px; top: 4px;", CSS)
    name_rule = re.search(r"\.ui-thumb-name \{[^}]*\}", CSS).group(0)
    assert "padding-right" not in name_rule
    assert re.search(r"\.ui-photos \.ui-btnrow\[data-photo-picked\] \{ position: sticky; bottom: 0;", CSS)

def test_a_quiet_job_never_shows_unless_it_fails(env):
    import jobs
    A, c = env
    jobs.start(A.app, "t-quiet", "Updating the galleries online", lambda j: "Nothing to update.", quiet=True)
    assert jobs.JOBS["t-quiet"]["state"] == "done"
    assert not [j for j in jobs.visible() if j["key"] == "t-quiet"]
    assert "Updating the galleries online" not in _page(c, "/")

    def boom(j):
        raise RuntimeError("the host did not answer")
    jobs.start(A.app, "t-quiet2", "Updating the galleries online", boom, quiet=True)
    assert [j for j in jobs.visible() if j["key"] == "t-quiet2"]

def test_nothing_online_the_gallery_update_runs_unseen(env):
    import jobs
    A, c = env
    jobs.JOBS.pop("upgrade-140", None)
    run(A, "DELETE FROM settings WHERE key='upgrade_140'")
    start = A.app.config.get("gallery_start_upgrade")
    if start is None:
        return
    with A.app.test_request_context():
        A.db()
        start()
    j = jobs.JOBS.get("upgrade-140")
    assert j and j["state"] == "done", j
    assert j["quiet"] and not [v for v in jobs.visible() if v["key"] == "upgrade-140"]

def test_a_shown_job_is_docked_in_the_page_on_a_wide_screen(env):
    import jobs
    A, c = env
    jobs.JOBS["t-shown"] = dict(key="t-shown", label="A job with work", state="running", done=1, total=4, message="",
                                started=time.time(), ended=None, link="")
    page = _page(c, "/")
    main = page.split('<main id="main"', 1)[1]
    assert main.index('<aside class="jobs"') < main.index("</main>")
    wide = re.search(r"@media \(min-width: 641px\) \{\s*\.jobs \{ position: static;", CSS)
    assert wide
