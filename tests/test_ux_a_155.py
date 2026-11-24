import os
import re
import sys
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
CSS = open(os.path.join(ROOT, "static", "style.css")).read()
MONEY_TITLES = ("Send quote", "Client said yes", "Send invoice")

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'The Lanes')")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (1, 1, 'Dana Lane', 'adult', 0)")
    con.commit(); con.close()
    return A, c

def new_session(A, days=30, stage="booked"):
    with A.app.test_request_context():
        return A.create_session(1, "family", stage=stage, shoot_date=(A.today() + timedelta(days=days)).isoformat(),
                                subject_ids=(1,))

def add_line(c, sid, amount="300"):
    r = c.post(f"/session/{sid}/money", data={"what": "line", "label": "Family session", "amount": amount,
                                              "back": f"/session/{sid}"}, environ_base=E)
    assert r.status_code == 302

def step_labels(A, sid):
    with A.app.test_request_context():
        w = A.workflow(sid)
    return [st["label"] for g_ in w["stages"] for st in g_["steps"]]

def open_money_tasks(A, sid):
    con = A.connect()
    rows = con.execute(f"SELECT title FROM tasks WHERE session_id=? AND done IS NULL AND title IN ({','.join('?' * len(MONEY_TITLES))})",
                       (sid, *MONEY_TITLES)).fetchall()
    con.close()
    return sorted(r_[0] for r_ in rows)

@pytest.fixture
def plan(env):

    A, c = env
    head = new_session(A, 60, stage="quoted")
    add_line(c, head)
    member = new_session(A, 20, stage="quoted")
    assert open_money_tasks(A, member)
    r = c.post(f"/session/{member}/plan", data={"plan_of": str(head)}, environ_base=E)
    assert r.status_code == 302
    return A, c, head, member

def test_a_session_billed_on_its_plans_quote_has_no_quote_or_invoice_steps(plan):
    A, c, head, member = plan
    with A.app.test_request_context():
        assert A.billed_on_plan(member) and not A.billed_on_plan(head)
    labels = step_labels(A, member)
    for gone in ("Build quote", "Quote built", "Send quote", "Client said yes", "Send invoice", "Deposit received", "Paid in full"):
        assert gone not in labels, (gone, labels)
    assert "Send quote" in step_labels(A, head)
    assert open_money_tasks(A, member) == []

def test_pricing_the_session_on_its_own_brings_its_steps_back(plan):
    A, c, head, member = plan
    add_line(c, member, "150")
    with A.app.test_request_context():
        assert not A.billed_on_plan(member)
    assert "Send quote" in step_labels(A, member)
    assert open_money_tasks(A, member) == sorted(MONEY_TITLES)

def test_leaving_the_plan_brings_its_steps_back(plan):
    A, c, head, member = plan
    c.post(f"/session/{member}/plan", data={"plan_of": ""}, environ_base=E)
    assert "Send quote" in step_labels(A, member)
    assert open_money_tasks(A, member) == sorted(MONEY_TITLES)

def test_its_invoice_page_points_to_the_plans_quote_not_an_empty_invoice(plan):
    A, c, head, member = plan
    p = c.get(f"/session/{member}/invoice", environ_base=E).get_data(as_text=True)
    assert "is billed on the quote of" in p and "so it has no invoice of its own" in p
    assert f'href="/session/{head}/quote"' in p and "Open that quote" in p
    for gone in ("Issue and email it", "Paid in full", "None yet", "Balance due", "DRAFT"):
        assert gone not in p, gone
    r = c.get(f"/session/{member}/invoice.pdf", environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/session/{member}/invoice")
    p = c.get(f"/session/{member}/receipt", environ_base=E).get_data(as_text=True)
    assert "so it has no receipt of its own" in p and "Paid in full" not in p

def test_an_unpriced_sessions_invoice_says_there_is_nothing_to_invoice_yet(env):
    A, c = env
    sid = new_session(A)
    p = c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True)
    assert "Nothing to invoice yet: this session has no prices." in p
    for gone in ("Paid in full", "None yet", "Issue and email it", "Thank you"):
        assert gone not in p, gone

def test_a_no_charge_session_invoice_says_no_charge(env):
    A, c = env
    sid = new_session(A)
    add_line(c, sid, "0")
    p = c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True)
    assert "There is nothing to pay for this session" in p
    for gone in ("Paid in full", "None yet", "Balance due"):
        assert gone not in p, gone

def test_draft_invoice_bar_puts_its_status_on_its_own_line_on_a_phone(env):
    A, c = env
    sid = new_session(A)
    add_line(c, sid)
    p = c.get(f"/session/{sid}/invoice", environ_base=E).get_data(as_text=True)
    assert "Issue and email it" in p and "Draft, no number yet" in p
    assert re.search(r"@media \(max-width: 640px\).*body > \.bar > \.ui-note \{ flex: 1 1 100%; order: -1; \}", p, re.S)

def test_clients_faces_share_todays_size_and_keep_their_initials_centered(env):
    A, c = env
    p = c.get("/clients", environ_base=E).get_data(as_text=True)
    face = re.search(r'<span class="ui-roster-who"><span aria-hidden="true">(<span class="av"[^>]*>)', p).group(1)
    assert "width:36px;height:36px" in face
    macros = open(os.path.join(ROOT, "templates", "_macros.html")).read()
    assert "{% set ROW_FACE = 36 %}" in macros
    today_t = open(os.path.join(ROOT, "templates", "_today.html")).read()
    assert "avatar(line.hid, ROW_FACE)" in today_t and "avatar(sh.hid, ROW_FACE)" in today_t
    rule = re.search(r"\.ui-roster \.ui-roster-who \.av \{([^}]*)\}", CSS).group(1)
    assert "display" not in rule
    assert re.search(r"\.ui-roster td \{[^}]*vertical-align: middle", CSS)

def test_clients_import_is_a_button(env):
    A, c = env
    p = c.get("/clients", environ_base=E).get_data(as_text=True)
    assert re.search(r'<a class="ui-btn ui-btn-quiet" href="/clients/import"[^>]*>Import clients from a file</a>', p)

@pytest.fixture
def event(env):
    A, c = env
    r = c.post("/events", data={"name": "Maple picture day", "date": (A.today() + timedelta(days=10)).isoformat(),
                                    "venue": "A rather long school gymnasium name"}, environ_base=E)
    eid = int(re.search(r"/event/(\d+)", r.headers["Location"]).group(1))
    c.post(f"/event/{eid}/billing", data={"billing": "families"}, environ_base=E)
    for name, parent in (("Abara", "Nia Abara"), ("Brandt", "Lena Brandt")):
        c.post(f"/event/{eid}/family", data={"household_name": name, "children": "Kofi 7", "parent_name": parent}, environ_base=E)
    c.post(f"/event/{eid}/family-price", data={"amount": "45"}, environ_base=E)
    return A, c, eid

def test_event_highlights_wrap_and_venue_and_next_step_get_room(event):
    A, c, eid = event
    p = c.get(f"/event/{eid}", environ_base=E).get_data(as_text=True)
    assert re.search(r'<div class="hl-wide"><div class="k">Venue</div>', p)
    assert re.search(r'<div class="hl-wide"><div class="k">Next step</div>', p)
    v = re.search(r"\.highlights \.v \{([^}]*)\}", CSS).group(1)
    assert "nowrap" not in v and "ellipsis" not in v and "overflow-wrap: anywhere" in v
    assert ".highlights > div, .highlights > .hl-wide { flex: 1 1 40%; min-width: 0; }" in CSS

def test_event_family_rows_label_the_parent_say_releases_one_way_and_take_a_payment(event):
    A, c, eid = event
    p = c.get(f"/event/{eid}", environ_base=E).get_data(as_text=True)
    assert p.count('<td data-l="Parent">') == 2
    assert "All " not in re.sub(r"<[^>]+>", " ", p.split('id="families"')[1].split("</table>")[0]).replace("All families", "")
    assert re.search(r"\d of \d signed", p)
    rows = re.findall(r'<tr class="rowlink".*?</tr>', p, re.S)
    assert len(rows) == 2
    for row in rows:
        assert "$45.00 owed" in row
        assert re.search(r'<a class="ui-btn ui-btn-quiet ui-btn-small" href="#f-(pay_balance|deposit_paid)-\d+"[^>]*>Record payment</a>', row)
        assert 'name="what" value="payment"' in row and f'value="/event/{eid}#families"' in row

def test_recording_a_payment_on_a_family_row_comes_back_to_the_event(event):
    A, c, eid = event
    p = c.get(f"/event/{eid}", environ_base=E).get_data(as_text=True)
    sid = int(re.search(r'<form method="post" action="/session/(\d+)/money"', p).group(1))
    r = c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "balance", "amount": "45", "method": "cash",
                                               "back": f"/event/{eid}#families"}, environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/event/{eid}#families")
    with A.app.test_request_context():
        assert A.totals(sid)["due"] <= 0.005
