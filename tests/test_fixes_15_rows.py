import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, _tab, browser
from test_stage_logic_150 import E, _acts, _in, _invoice_sent, _pay, _price, _session, _sql, env

def _rows(page):

    out = []
    for li in re.findall(r'<li class="ui-todo[^"]*"[^>]*>(.*?)</li>', page, re.S):
        m_ = re.search(r'<span class="ui-todo-what">([^<]*)', li)
        if not m_ or 'class="ui-todo-act"' not in li:
            continue
        what = m_.group(1).strip()
        act = li.split('class="ui-todo-act"', 1)[1]
        btn = re.search(r'class="ui-btn[^"]*"[^>]*>([^<]+)<', act)
        out.append((what, btn.group(1).strip() if btn else ""))
    return out

def _page(c, sid=1):
    return c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)

def _today(c):
    return c.get("/", environ_base=E).get_data(as_text=True)

def test_a_row_names_the_step_and_its_button_the_action(env):
    A, c = env
    _sql(A, "UPDATE people SET email='alex@example.com'")
    _session(A, "booked", days=10, start="10:00")
    _price(A, c, "300")
    rows = dict(_rows(_page(c)))
    assert rows.get("Agreement") == "Send the agreement", rows
    assert rows.get("Session sheet") == "Send the session sheet", rows
    assert not [w for w, b in rows.items() if w and b and w.lower() == b.lower()]
    assert not [b for b in rows.values() if b.startswith("Open ")]
    today = _today(c)
    line = today.split('id="line-s1"', 1)[1].split("</li>", 1)[0]
    what = re.search(r'class="ui-tline-what">([^<]*)<', line).group(1).strip()
    assert what in rows and ">" + rows[what] + "<" in line

def test_the_quote_button_says_send_the_quote(env):
    A, c = env
    _session(A, "inquiry", days=30)
    _price(A, c, "300")
    c.post("/session/1/quote/send", data={"total": "300.00"}, environ_base=E)
    page = _page(c)
    assert ">Send the quote<" in page and "Open quote" not in page

def test_recording_money_has_one_name_and_the_gate_is_only_a_title(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "318")
    _invoice_sent(A, c)
    rows = _rows(_page(c))
    assert ("Collect the $318.00 balance first", "Record payment ($318.00)") in rows, rows
    assert not [b for _w, b in rows if b.startswith("Collect")]
    assert not [b for _w, b in rows if re.fullmatch(r"Record \$[\d,.]+", b)]
    today = _today(c)
    assert ">Record payment ($318.00)<" in today and "Collect the $318.00 balance first" in today
    assert ">Record payment</button>" in _page(c)

def _usb_delivered(A, c):
    _session(A, "delivered", days=-20)
    _sql(A, "UPDATE sessions SET delivery_method='usb', retouch_picks='IMG_1 brighten' WHERE id=1")
    _price(A, c, "265")
    _pay(A, c, "265")
    _in(A, lambda: A.sync_workflow_tasks(1))

def test_a_usb_delivery_gets_the_delivery_row_with_another_way(env):
    A, c = env
    _usb_delivered(A, c)
    page = _page(c)
    rows = dict(_rows(page))

    assert rows.get("Get the photos to Alex") == "Choose how to deliver", rows
    ways = page.split('id="f-deliver-1"', 1)[1]
    person = ways.split('data-way="person"', 1)[1].split("</form>", 1)[0]
    assert re.findall(r'data-way="(\w+)"', ways)[0] == "person" and ">usual<" not in ways
    assert 'name="step" value="sd_shipped"' in person and re.search(r'value="usb" checked', person)
    assert "Files delivered" not in rows

def test_mark_delivered_updates_the_delivery_block_in_place(browser, env):
    A, c = env
    _usb_delivered(A, c)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        assert "Files not delivered yet" in p.locator("#delivery").inner_text()
        p.locator(".ui-todo", has_text="Get the photos to Alex").get_by_role("link", name="Choose how to deliver").click()
        p.locator("#f-deliver-1").get_by_role("button", name="Record the handover").click()
        p.wait_for_function("!document.querySelector('#delivery').innerText.includes('not delivered yet')", timeout=5000)
        assert "Files delivered (USB drive)" in p.locator("#delivery").inner_text()
        assert p.evaluate("window.__stay") == 1
    finally:
        ctx.close()

def test_picks_count_says_retouched_once_the_retouching_is_sent(env):
    A, c = env
    _usb_delivered(A, c)
    _sql(A, "UPDATE tasks SET done=date('now') WHERE session_id=1 AND title='Send retouched photos'")
    page = _page(c)
    assert "to retouch" not in page.split('id="more"', 1)[0]

def test_a_sample_agreement_has_one_way_to_edit_its_wording(env):
    A, c = env
    _session(A, "booked", days=10)
    page = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert "SAMPLE wording" in page and "Edit the wording" in page
    assert "Edit and review this agreement" not in page and ">Review this wording<" not in page

def test_today_never_says_nothing_waits_above_a_list_and_handled_picks_leave_it(env):
    A, c = env
    _usb_delivered(A, c)
    today = _today(c)
    assert "Retouch 1 photo" in today and "Sent their picks: Retouch" not in today
    c.get("/c/1?s=1", environ_base=E)
    _sql(A, "UPDATE tasks SET done=date('now') WHERE session_id=1 AND title='Send retouched photos'")
    today = _today(c)
    head = today.split("</header>", 1)[0]
    assert "Sent their picks" not in today
    assert "Nothing waits on you today." not in head

def test_an_empty_today_says_so_once_and_setting_up_has_one_heading(env):
    A, c = env
    today = _today(c)
    assert today.count("Nothing waits on you today.") == 1
    setup = today.split('id="setup"', 1)[1].split("</section>", 1)[0]
    assert "Finish setting up" not in setup and "<h3" not in setup
    if "Walk through your session types" in setup:
        bare = re.sub(r'<span class="qh".*?</span></span>', "", setup, flags=re.S)
        assert "The defaults already work" in setup and "The defaults already work" not in bare

def test_the_sessions_list_names_the_same_next_action_as_today(env):
    A, c = env
    _usb_delivered(A, c)
    today = _today(c)
    line = today.split('id="line-s1"', 1)[1].split("</li>", 1)[0]
    what = re.search(r'class="ui-tline-what">([^<]*)<', line).group(1).replace("Sent their picks: ", "").strip()
    sessions = c.get("/sessions", environ_base=E).get_data(as_text=True)
    row = sessions.split('href="/c/1?s=1"', 1)[1].split("</a>", 1)[0]
    assert what in row, (what, row)

def test_the_phone_add_button_is_named_and_leaves_room_under_the_list(browser, env):
    A, c = env
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/")
        cta = p.locator("nav.top a.cta")
        assert cta.inner_text().strip() == "+ Contact" and cta.is_visible()
        box = cta.bounding_box()
        pad = p.evaluate("parseFloat(getComputedStyle(document.querySelector('main')).paddingBottom)")
        assert pad >= box["height"] + 16
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()
