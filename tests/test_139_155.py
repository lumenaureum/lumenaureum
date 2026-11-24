import html
import os
import re
import sys
from datetime import date, timedelta

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

def text(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def _event(A, c, days_ago=60, price="60", tax_in=False, names=("Bell", "Chen", "Diaz")):
    day = (date.today() - timedelta(days=days_ago)).isoformat()
    c.post("/events", data={"name": "Team day", "date": day}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    for n in names:
        c.post("/event/1/family", data={"household_name": n, "parent_name": f"Pat {n}", "phone": "555-0100"},
               environ_base=E)
    c.post("/event/1/family-price", data=dict(amount=price, **({"tax_in": "1"} if tax_in else {})), environ_base=E)
    return {n: sql(A, "SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name=?", (n,))[0][0]
            for n in names}

def test_several_families_paid_at_once(env):
    A, c = env
    sid = _event(A, c)
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert 'name="pick" value="%d" form="fpay"' % sid["Bell"] in page and "Record the selected as paid in full" in page
    assert page.count(">Record payment</a>") == 3
    once = re.search(r'name="once" value="([^"]+)"', page[page.index('id="fpay"'):]).group(1)
    day = max(date.today() - timedelta(days=2), date(date.today().year, 1, 1)).isoformat()
    form = {"pick": [str(sid["Bell"]), str(sid["Diaz"])], "method": "cash", "received": day, "once": once}
    r = c.post("/event/1/payments", data=form, environ_base=E, follow_redirects=True)
    assert "Recorded 2 payments in full, $120.00 in all." in text(r.get_data(as_text=True))
    got = sql(A, "SELECT session_id, kind, amount, received, method FROM payments ORDER BY session_id")
    assert got == [(sid["Bell"], "balance", 60.0, day, "cash"), (sid["Diaz"], "balance", 60.0, day, "cash")]
    c.post("/event/1/payments", data=form, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM payments") == [(2,)]
    t = text(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert t.count("$60.00 paid in full") == 2 and "$60.00 owed" in t

def test_bulk_payments_refuse_what_they_should(env):
    A, c = env
    sid = _event(A, c)
    r = c.post("/event/1/payments", data={"method": "cash"}, environ_base=E, follow_redirects=True)
    assert "Select the families who paid first" in text(r.get_data(as_text=True))
    future = (date.today() + timedelta(days=5)).isoformat()
    c.post("/event/1/payments", data={"pick": str(sid["Bell"]), "method": "cash", "received": future}, environ_base=E)
    c.post("/event/1/payments", data={"pick": str(sid["Bell"]), "method": "gold bars"}, environ_base=E)
    sql(A, "INSERT INTO households (name) VALUES ('Elsewhere')")
    sql(A, "INSERT INTO sessions (household_id, type, stage) VALUES (4, 'event', 'booked')")
    sql(A, "INSERT INTO lines (session_id, label, amount) VALUES (4, 'Other job', 80)")
    c.post("/event/1/payments", data={"pick": "4", "method": "cash"}, environ_base=E)
    assert sql(A, "SELECT COUNT(*) FROM payments") == [(0,)]

def test_bulk_payments_never_dated_in_an_earlier_year(env, monkeypatch):

    A, c = env
    monkeypatch.setattr(A, "books_open_from", lambda: f"{date.today().year}-01-01")
    sid = _event(A, c)
    last_year = f"{date.today().year - 1}-12-30"
    r = c.post("/event/1/payments", data={"pick": str(sid["Bell"]), "method": "cash", "received": last_year},
               environ_base=E, follow_redirects=True)
    assert "is closed: the app records nothing more in a closed year. Nothing was recorded." in text(r.get_data(as_text=True))
    assert sql(A, "SELECT COUNT(*) FROM payments") == [(0,)]
    page = c.get("/event/1", environ_base=E).get_data(as_text=True)
    assert f'min="{date.today().year}-01-01"' in page[page.index('id="fpay"'):]

def test_a_price_that_includes_the_sales_tax(env):
    A, c = env
    sql(A, "INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '7.25')")
    sid = _event(A, c, days_ago=-10, price="35", tax_in=True)
    for s_ in sid.values():
        lines = dict(sql(A, "SELECT kind, amount FROM lines WHERE session_id=?", (s_,)))
        assert round(lines["custom"] + lines["tax"], 2) == 35.0 and lines["tax"] > 0
    t = text(c.get("/event/1", environ_base=E).get_data(as_text=True))
    assert t.count("$35.00 owed") == 3 and "Includes sales tax" in t

    sql(A, "UPDATE events SET family_tax_in=0")
    c.post("/event/1/family", data={"household_name": "Evans"}, environ_base=E)
    assert sql(A, "SELECT SUM(amount) FROM lines WHERE session_id=(SELECT MAX(id) FROM sessions)") == [(37.54,)]

@pytest.mark.parametrize("price", ["35", "20", "45.50", "12.99", "100"])
def test_tax_inside_comes_to_the_price_exactly(env, price):
    A, c = env
    sql(A, "INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8.875')")
    sid = _event(A, c, days_ago=-10, price=price, tax_in=True, names=("Bell",))
    assert sql(A, "SELECT ROUND(SUM(amount), 2) FROM lines WHERE session_id=?", (sid["Bell"],)) == [(float(price),)]

def test_an_event_family_with_its_share_overdue_says_so(env):
    A, c = env
    sid = _event(A, c, names=("Bell",))
    t = text(c.get(f"/c/1?s={sid['Bell']}", environ_base=E, follow_redirects=True).get_data(as_text=True))
    assert "Nothing open" not in t and "Deposit received" not in t and "Invoice ($60.00 balance" in t

    with A.app.test_request_context():
        assert [(o_["key"], o_["words"]) for o_ in A.event_family_open(1)] == [("balance_paid", "1 balance still owed")]

def test_before_the_event_a_familys_share_is_not_chased(env):
    A, c = env
    _event(A, c, days_ago=-10, names=("Bell",))
    with A.app.test_request_context():
        assert A.event_family_open(1) == []
