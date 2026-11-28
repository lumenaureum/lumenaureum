import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, make_studio, new_session, one, run, sql
from journey_walker import menu_items, parse
from test_305_journeys import booked_and_ready, quoted, retouch

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def page(c, sid, **args):
    q_ = "".join(f"&{k}={v}" for k, v in args.items())
    r = c.get(f"/c/1?s={sid}{q_}", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def booked(A, c, **cols):
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), **cols)
    quoted(A, c, sid)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    return sid

def history(A, sid):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
    finally:
        con.close()

def row_of(p, words):

    for li in parse(p).iter():
        if li.tag == "li" and "ui-todo" in li.cls() and words in li.words():
            return li
    return None

def test_every_open_step_row_has_a_visible_skip(studio):
    A, c = studio
    sid = booked(A, c)
    p = page(c, sid)
    for words in ("Agreement", "Deposit invoice", "Session sheet", "Intake questions"):
        li = row_of(p, words)
        assert li is not None, words
        skips = [b for b in li.iter() if b.tag == "button" and b.words() == "Skip"]
        assert skips, words
        assert not any(a.tag == "details" for a in skips[0].ancestors()), f"{words}: Skip is under a menu"

def test_skip_is_one_tap_and_undo_brings_it_back(studio):
    A, c = studio
    sid = booked(A, c)
    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "na", "from": "row", "back": f"/c/1?s={sid}"},
           environ_base=E)
    p = page(c, sid)
    assert row_of(p, "Send the agreement") is None
    past = parse(p).iter()
    skipped = [li for li in past if "ui-past-skipped" in li.cls()]

    assert any("Agreement" in li.words() and "Skipped" in li.words() and "Undo" in li.words()
               and "The client said no" in li.words() and "Client didn't want it" not in li.words() for li in skipped)
    assert "Skipped 'Agreement signed'" in history(A, sid)

    c.post("/close", data={"sid": sid, "step": "agreement_signed", "outcome": "declined"}, environ_base=E)
    assert "Skipped 'Agreement signed': the client didn't want it" in history(A, sid)
    assert "Client didn&#39;t want it" in page(c, sid) or "Client didn't want it" in page(c, sid)

    c.post("/step/reopen", data={"sid": sid, "step": "agreement_signed"}, environ_base=E)
    assert one(A, "SELECT 1 FROM step_closes WHERE session_id=? AND step='agreement_signed'", (sid,)) is None
    assert row_of(page(c, sid), "Send the agreement") is not None
    assert "Brought back 'Agreement signed'" in history(A, sid)

def test_a_task_step_skips_and_comes_back_too(studio):
    A, c = studio
    sid = booked(A, c)
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "from": "row"}, environ_base=E)
    assert tuple(one(A, "SELECT outcome FROM tasks WHERE id=?", (tid,))) == ("na",)
    c.post("/step/reopen", data={"task": tid}, environ_base=E)
    assert tuple(one(A, "SELECT done, outcome FROM tasks WHERE id=?", (tid,))) == (None, None)

def test_undo_never_reopens_a_step_really_done(studio):
    A, c = studio
    sid = booked(A, c)
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))[0]
    c.post(f"/task/{tid}", data={"set": "1"}, environ_base=E)
    c.post("/step/reopen", data={"task": tid}, environ_base=E)
    assert one(A, "SELECT outcome FROM tasks WHERE id=?", (tid,))[0] == "done"

def test_steps_with_nothing_to_do_get_skip_not_a_forced_done(studio):

    A, c = studio
    retouch(c, False)
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-1), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    p = page(c, sid)
    for words in ("Upload original photos", "Cull", "Upload edited photos"):
        li = row_of(p, words)
        assert li is not None and any(b.tag == "button" and b.words() == "Skip" for b in li.iter()), words

def test_a_balance_is_never_skipped_away(studio):

    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="usb")
    booked_and_ready(A, c, sid)
    sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(-20), sid))
    c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
    sql(A, "UPDATE tasks SET done=?, outcome='done' WHERE session_id=? AND done IS NULL AND title != 'Send invoice'", (day(-2), sid))
    c.post(f"/session/{sid}/invoice/issue", data={"sample_ok": "1", "then": "email"}, environ_base=E)
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send invoice'", (sid,))[0]
    c.post(f"/task/{tid}", data={"set": "1"}, environ_base=E)
    li = row_of(page(c, sid), "balance")
    assert li is not None and not any(b.tag == "button" and b.words() == "Skip" for b in li.iter())

def test_steps_for_this_session_switches_steps_off_for_this_session_only(studio):
    A, c = studio
    before = {k: dict(v) for k, v in A.C.WORKFLOW.items()}
    sid = booked(A, c)
    other = booked(A, c)
    lst = run(A, lambda: A.session_step_list(sid))
    keys = {r["words"]: r["key"] for _st, rows in lst for r in rows if r["key"]}
    off = ["Get the intake answers", "Get the agreement signed", "Collect the deposit", "Send the session sheet"]
    shown = [keys[w] for w in off] + [keys["Confirm time and place"]]
    r = c.post(f"/session/{sid}/steps", data={"shown": shown, "keep": [keys["Confirm time and place"]]}, environ_base=E)
    assert r.status_code == 302
    states = {r_["words"]: r_["state"] for _st, rows in run(A, lambda: A.session_step_list(sid)) for r_ in rows}
    assert all(states[w] == "off" for w in off) and states["Confirm time and place"] == "open"
    assert one(A, "SELECT note FROM step_closes WHERE session_id=? AND step='agreement_signed'", (sid,))[0] == A.OFF_NOTE
    assert any(a.startswith("Steps for this session, off: ") for a in history(A, sid))

    states2 = {r_["words"]: r_["state"] for _st, rows in run(A, lambda: A.session_step_list(other)) for r_ in rows}
    assert all(states2[w] == "open" for w in off)
    assert {k: dict(v) for k, v in A.C.WORKFLOW.items()} == before

    c.post(f"/session/{sid}/steps", data={"shown": [keys["Get the agreement signed"]], "keep": [keys["Get the agreement signed"]]},
           environ_base=E)
    states = {r_["words"]: r_["state"] for _st, rows in run(A, lambda: A.session_step_list(sid)) for r_ in rows}
    assert states["Get the agreement signed"] == "open"
    assert any(a.startswith("Steps for this session, back on: ") for a in history(A, sid))

def test_the_steps_list_is_on_the_client_page_at_booking_and_after(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14))
    quoted(A, c, sid)
    p = page(c, sid)
    quote_box = p.split('id="quote-now"', 1)[1].split("</section>", 1)[0]
    assert "Steps for this session" in quote_box and f'href="/session/{sid}/steps"' in quote_box
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    p = page(c, sid)
    assert "Not every client needs every step." in p and f'href="/session/{sid}/steps"' in p
    steps = c.get(f"/session/{sid}/steps", environ_base=E)
    assert steps.status_code == 200 and 'name="keep"' in steps.get_data(as_text=True)

def test_a_done_step_and_the_balance_cannot_be_switched(studio):
    A, c = studio
    sid = booked(A, c)
    rows = {r["words"]: r for _st, rs in run(A, lambda: A.session_step_list(sid)) for r in rs}
    assert rows["Pick a shoot date"]["state"] == "done"
    assert rows["Collect the balance"]["state"] == "fixed" and rows["Collect the balance"]["why"]

def test_upgrade_keeps_every_step_of_a_session_in_progress(studio):

    A, c = studio
    sid = booked(A, c)
    before = one(A, "SELECT COUNT(*) FROM tasks WHERE session_id=?", (sid,))[0]
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))[0]
    sql(A, "UPDATE tasks SET done=?, outcome='na' WHERE id=?", (day(-1), tid))
    p = page(c, sid)
    c.get(f"/session/{sid}/steps", environ_base=E)
    run(A, lambda: A.sync_workflow_tasks(sid))
    assert one(A, "SELECT COUNT(*) FROM tasks WHERE session_id=?", (sid,))[0] == before
    li = next(li for li in parse(p).iter() if "ui-past-skipped" in li.cls() and "session sheet" in li.words())
    assert "Undo" in li.words()

TOPIC_ACTIONS = {
    "session": ["Edit all session details", "Change the date, time, or place", "Change how the files go", "Shoot done",
                "Mark shot", "Book without a quote", "Do not book", "Cancel the booking", "Reopen at Delivered", "Edit client details",
                "Move to another client", "Timeline", "People"],
    "tasks": ["Steps for this session", "Add a step"],
    "payments": ["Record payment", "Replace the quote", "Pay in installments", "Invoice", "Waive the balance"],
    "paperwork": ["Open the agreement", "Who signs, and add a release", "Send the session sheet"],
    "notes": ["Studio history, all clients"],
    "delivery": ["Record the handover", "Change how the files go", "The gallery"],
    "gallery": ["Build the gallery", "Put it online", "Preview the gallery", "Email the link", "Replace this link",
                "Change the closing day", "Close it now", "Put it back online", "Check what the client did"],
}
ALWAYS = ("session", "tasks", "payments", "notes")

def stages(A, c):

    out = {}
    out["inquiry"] = new_session(A, "family", "inquiry", shoot_date=day(20))
    sid = new_session(A, "family", "inquiry", shoot_date=day(20))
    quoted(A, c, sid)
    out["quoted"] = sid
    out["booked"] = booked(A, c)
    for st, d_ in (("shot", -3), ("delivered", -10), ("closed", -30)):
        sid = new_session(A, "family", "inquiry", shoot_date=day(7), delivery_method="link")
        booked_and_ready(A, c, sid)
        sql(A, "UPDATE sessions SET shoot_date=? WHERE id=?", (day(d_), sid))
        c.post(f"/session/{sid}/quick", data={"act": "shot"}, environ_base=E)
        if st != "shot":
            run(A, lambda: A.set_stage(sid, st))
        out[st] = sid
    return out

def test_every_topic_menu_lists_its_actions_and_says_why_one_is_grayed(studio):
    A, c = studio
    for st, sid in stages(A, c).items():
        root = parse(page(c, sid))
        for topic, labels in TOPIC_ACTIONS.items():
            items = menu_items(root, topic)
            if items is None:
                assert topic not in ALWAYS or (topic == "tasks" and st == "closed"), f"{st}: no More... for {topic}"
                continue
            got = {lab for lab, _on, _why in items}
            missing = [l_ for l_ in labels if l_ not in got]
            assert not missing, f"{st}, {topic}: {missing} not in its More... menu ({sorted(got)})"
            for lab, on, why in items:
                assert on or why.strip(), f"{st}, {topic}: '{lab}' is grayed with no reason"
                assert "—" not in lab + why, "no em-dashes"

        for box in (n for n in root.iter() if "ui-topic-more" in n.cls()):
            summ = next(k for k in box.kids if k.tag == "summary")
            assert summ.words() == "More…", (st, summ.words())
            for a in (n for n in box.iter() if n.attrs.get("data-ui-open")):
                target = a.attrs["data-ui-open"]
                assert any(n.attrs.get("id") == target for n in root.iter()), f"{st}: {a.words()} opens #{target}, not on the page"

def test_the_more_menu_names_a_reason_for_shoot_done_before_the_shoot(studio):
    A, c = studio
    sid = booked(A, c)
    items = {lab: (on, why) for lab, on, why in menu_items(page(c, sid), "session")}
    on, why = items["Shoot done"]
    assert not on and "From the morning of the shoot date" in why and "Mark shot" in why
    assert items["Mark shot"][0]
    on, why = items["Reopen at Delivered"]
    assert not on and why == "Only a closed session reopens"

def test_reopen_is_in_the_menu_on_a_closed_session(studio):
    A, c = studio
    sid = stages(A, c)["closed"]
    items = {lab: (on, why) for lab, on, why in menu_items(page(c, sid), "session")}
    assert items["Reopen at Delivered"][0]
    c.post(f"/session/{sid}/stage", data={"stage": "delivered"}, environ_base=E)
    assert one(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0] == "delivered"

FOLD = re.compile(r'<details class="ui-fold')

def test_no_folds_on_the_client_page_at_any_stage(studio):
    A, c = studio
    for st, sid in stages(A, c).items():
        p = page(c, sid)
        assert not FOLD.search(p), f"{st}: a fold on the client page"
        assert "Details and history" not in p

def test_no_folds_on_today_finances_and_edit_session(studio):
    A, c = studio
    sid = stages(A, c)["delivered"]
    for url in ("/", "/money", f"/session/{sid}/edit"):
        p = c.get(url, environ_base=E).get_data(as_text=True)
        assert not FOLD.search(p), url

def test_history_is_a_short_list_and_a_link_to_all_of_it(studio):
    A, c = studio
    sid = booked(A, c)
    for i in range(8):
        c.post("/client/1/note", data={"session_id": sid, "text": f"note {i}", "channel": "note"}, environ_base=E)
    p = page(c, sid)
    notes = p.split('id="notes"', 1)[1].split("</section>", 1)[0]
    assert notes.count('class="hrow') == 5
    assert re.search(r"All notes and history \(\d+\)", notes) and "/history?client=1" in notes

def test_a_part_this_stage_keeps_off_the_page_opens_as_its_own_view(studio):
    A, c = studio
    sid = booked(A, c)
    assert 'id="message"' not in page(c, sid)
    items = {lab: on for lab, on, _w in menu_items(page(c, sid), "session")}
    assert items.get("Their message")
    assert 'id="message"' in page(c, sid, show="message")

def test_files_go_changes_in_place(studio):
    A, c = studio
    sid = booked(A, c, delivery_method="usb")
    p = page(c, sid)
    assert 'id="files-go"' in p and 'data-ui-open="files-go"' in p
    c.post(f"/session/{sid}/files-go", data={"delivery_method": "link", "back": f"/c/1?s={sid}"}, environ_base=E)
    assert one(A, "SELECT delivery_method FROM sessions WHERE id=?", (sid,))[0] == "link"
    assert any("Delivery changed from USB drive to" in a for a in history(A, sid))

def test_the_topic_menu_fits_a_phone(studio):

    css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "style.css")).read()

    phone = re.search(r"@media \(max-width: 640px\) \{\s*\.ui-topic-more\[open\] > \.ui-topic-pop \{([^}]*)\}", css)
    assert phone and "position: fixed; left: 8px; right: 8px" in phone.group(1)
    rows = re.search(r"\.ui-topic-pop \.ui-topic-item \{([^}]*)\}", css)
    assert rows and "min-height: 44px" in rows.group(1)
