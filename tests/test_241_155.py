import html
import os
import sys
from datetime import date, timedelta
from urllib.parse import unquote

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c

def sql(A, q_, args=()):
    con = A.connect()
    try:
        out = con.execute(q_, args).fetchall()
        con.commit()
        return [tuple(r_) for r_ in out]
    finally:
        con.close()

def _event_families(A, c):

    day = (date.today() - timedelta(days=60)).isoformat()
    c.post("/events", data={"name": "Spring Recital", "date": day, "venue": "Town Hall"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family", data={"household_name": "Bell", "parent_name": "Pat Bell", "phone": "555-0142"},
           environ_base=E)
    c.post("/event/1/family", data={"household_name": "Chen", "parent_name": "Lee Chen", "email": "lee@example.com"},
           environ_base=E)
    c.post("/event/1/family", data={"household_name": "Diaz", "parent_name": "Ana Diaz", "email": "ana@example.com",
                                    "phone": "555-0177"}, environ_base=E)
    c.post("/event/1/family-price", data={"amount": "60"}, environ_base=E)
    sid = {n: sql(A, "SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name=?", (n,))[0][0]
           for n in ("Bell", "Chen", "Diaz")}
    return sid

def _balances(c, A):
    page = html.unescape(c.get(f"/money?year={A.today().year}", environ_base=E).get_data(as_text=True))
    return page.split('id="upcoming"', 1)[1].split("</section>", 1)[0]

def _row(sec, sid):

    return next(r_ for r_ in sec.split('<div class="ui-remind-row">')[1:] if f"s={sid}&" in r_ or f"s={sid}\"" in r_)

def test_a_family_with_only_a_phone_is_reminded_by_text(env):
    A, c = env
    sid = _event_families(A, c)
    sec = _balances(c, A)
    bell = _row(sec, sid["Bell"])
    assert "Overdue $60.00" in bell
    assert f'action="/session/{sid["Bell"]}/remind"' in bell and ">Remind them by text</button>" in bell
    assert 'data-ui-mail="sms:5550142?&body=' in bell
    assert "$60.00 balance" in unquote(bell)
    assert ">Remind them by email</button>" not in bell

    chen = _row(sec, sid["Chen"])
    assert ">Remind them</button>" in chen and 'data-ui-mail="mailto:lee@example.com' in chen and "sms:" not in chen

    diaz = _row(sec, sid["Diaz"])
    assert diaz.index(">Remind them by email</button>") < diaz.index(">Remind them by text</button>")
    assert 'data-ui-mail="mailto:ana@example.com' in diaz and 'data-ui-mail="sms:5550177?&body=' in diaz

def test_the_text_reminder_opens_the_messages_app_and_is_logged_as_a_text(env):
    A, c = env
    sid = _event_families(A, c)
    r = c.post(f"/session/{sid['Bell']}/remind", data={"kind": "balance", "how": "text", "mail": "1", "back": "/money"},
               environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("sms:5550142?&body=")
    assert "$60.00 balance" in unquote(r.headers["Location"])
    assert sql(A, "SELECT channel FROM log WHERE session_id=? AND summary LIKE 'Sent a payment reminder%'",
               (sid["Bell"],)) == [("text",)]

    r = c.post(f"/session/{sid['Chen']}/remind", data={"kind": "balance", "how": "email", "mail": "1", "back": "/money"},
               environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:lee@example.com")
    assert sql(A, "SELECT channel FROM log WHERE session_id=? AND summary LIKE 'Sent a payment reminder%'",
               (sid["Chen"],)) == [("email",)]

def test_the_money_row_more_menu_offers_each_way_on_file(env):
    A, c = env
    sid = _event_families(A, c)
    with A.app.test_request_context():
        def items(n):
            s = A.q("SELECT * FROM sessions WHERE id=?", (sid[n],), one=True)
            return [(i_["verb"], i_["fields"]["how"], i_["mail"].split(":", 1)[0]) for i_ in A.remind_items(s, kind="balance")]
        assert items("Bell") == [("Remind them by text", "text", "sms")]
        assert items("Chen") == [("Remind them", "email", "mailto")]
        assert items("Diaz") == [("Remind them by email", "email", "mailto"), ("Remind them by text", "text", "sms")]
        sql(A, "UPDATE people SET phone='' WHERE household_id=(SELECT household_id FROM sessions WHERE id=?)",
            (sid["Bell"],))
        assert items("Bell") == []

def test_an_event_family_stands_by_its_event_session_on_clients_and_new_inquiry(env):

    A, c = env
    sid = _event_families(A, c)
    hid = sql(A, "SELECT household_id FROM sessions WHERE id=?", (sid["Bell"],))[0][0]
    page = html.unescape(c.get("/clients", environ_base=E).get_data(as_text=True))
    row = page[page.index(f'<tr data-ui-href="/c/{hid}"'):].split("</tr>", 1)[0]
    assert "No sessions yet" not in row and "Booked" in row
    with A.app.test_request_context():
        assert A.contact_standing()[hid] == "Booked"
    sql(A, "DELETE FROM sessions WHERE event_id=1")
    with A.app.test_request_context():
        assert hid not in A.contact_standing()
