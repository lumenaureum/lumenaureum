import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_stage_logic_150 import E, _in, _invoice_sent, _price, _session, _sql, env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _page(c, sid=1):
    return c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)

def _moves(page):
    return page.split('id="stage-moves"', 1)[1].split("</div>", 1)[0]

def test_skip_to_shot_says_why_before_the_shoot_date(env):
    A, c = env
    _session(A, "booked", days=6)
    page = _page(c)
    assert '<div class="ui-btnrow" id="stage-moves" data-ui-part>' in page
    row = _moves(page)
    assert re.search(r'<button type="button" class="ui-btn ui-btn-quiet" disabled aria-describedby="skip-why">Skip to Shot</button>', row)
    assert "Not before the shoot date, " in row and "If the shoot already happened, set its real date." in row
    _sql(A, "UPDATE sessions SET shoot_date=NULL WHERE id=1")
    assert "Set the shoot date first." in _moves(_page(c))
    _sql(A, "UPDATE sessions SET shoot_date=?, date_tentative=1 WHERE id=1", (A.today().isoformat(),))
    assert "The shoot date is still tentative. Set the real date first." in _moves(_page(c))

def test_skip_to_shot_is_offered_once_the_date_has_come(env):
    A, c = env
    _session(A, "booked", days=-1)
    row = _moves(_page(c))
    assert "skip-why" not in row and "disabled" not in row
    assert ">Skip to Shot</button>" in row

def test_a_step_the_edited_photos_ticked_says_why(env):
    A, c = env
    _session(A, "shot", days=-1)
    _in(A, lambda: A.sync_workflow_tasks(1))
    assert _in(A, lambda: A.settle_by_exports(1))
    note = _sql(A, "SELECT outcome_note FROM tasks WHERE session_id=1 AND title='Cull'")[0][0]
    assert note == "Settled: the edited photos were uploaded"
    assert "Cull (done: the edited photos were uploaded)" in _page(c)

def test_the_balance_comes_before_the_ways_to_deliver(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _sql(A, "UPDATE sessions SET delivery_method='usb' WHERE id=1")
    _price(A, c, "318")
    _invoice_sent(A, c)
    _in(A, lambda: A.sync_workflow_tasks(1))
    page = _page(c)
    needs = page.split('id="needs"', 1)[1].split("</section>", 1)[0]
    row = [li for li in re.findall(r'<li class="ui-todo[^"]*"[^>]*>(.*?)</li>', needs, re.S) if "balance first" in li]
    assert row, needs
    assert "Choose how to deliver" not in row[0] and "Record payment ($318.00)" in row[0]
    assert "Choose how to deliver" not in needs

def test_the_delivery_fold_says_each_state(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _sql(A, "UPDATE sessions SET delivery_method='usb' WHERE id=1")
    fold = _page(c).split('id="delivery"', 1)[1].split("</ul>", 1)[0]
    assert "Client has not opened the gallery yet" in fold and "Client opened it" not in fold
    assert "Final photos not delivered yet (USB drive)" in fold and "Gallery not built yet" in fold
    _sql(A, "UPDATE sessions SET first_visit=? WHERE id=1", (A.today().isoformat(),))
    assert "Client opened the gallery " in _page(c).split('id="delivery"', 1)[1].split("</ul>", 1)[0]

def test_the_session_title_shows_beside_its_type(env):
    A, c = env
    _session(A, "booked", days=6)
    _sql(A, "UPDATE sessions SET title='Sample team headshots' WHERE id=1")
    head = _page(c).split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert '<span class="ui-kick-title">&nbsp;&middot; Sample team headshots</span>' in head

def test_issue_and_email_it_says_the_app_sends_nothing():
    src = open(os.path.join(ROOT, "templates", "invoice.html")).read()
    assert "Then your mail app opens a draft email for you to send. This app never sends email itself." in src

def test_the_new_contact_age_field_has_a_label(env):
    A, c = env
    page = c.get("/contact/new", environ_base=E).get_data(as_text=True)
    assert '<label for="contact-age" class="ui-label">Age (optional)</label><div class="ui-field-inline"><input type="number" id="contact-age" name="contact_age"' in page
    assert ">approximate</label>" in page

def test_the_timeline_count_is_no_badge():
    css = open(os.path.join(ROOT, "static", "style.css")).read()
    rule = css.split(".jm .jn {", 1)[1].split("}", 1)[0]
    assert "background" not in rule and "border-radius" not in rule and "var(--mute)" in rule

def test_proofs_and_the_final_delivery_are_said_once(env):
    A, c = env
    _session(A, "shot", days=-1)
    _sql(A, "UPDATE sessions SET delivery_method='usb' WHERE id=1")
    page = _page(c)
    how = re.findall(r'<p class="ui-mute" id="gal-how">([^<]*)</p>', page)
    assert how == ["First the proof gallery goes online, and the client picks their favorites from it. "
                   "After that you deliver the final photos on a USB drive."], how
    _sql(A, "UPDATE sessions SET favorites='IMG_1' WHERE id=1")
    assert 'id="gal-how"' not in _page(c)
