import html
import re

import pytest
from datetime import date, timedelta

from test_money_audit_130 import E, env, get_zip, household, line, one, pay, post, run, session, tot, year
from test_285_155 import in_req, page, tax_lines, taxes, waiting_for_yes

def text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))

def open_quote(A, c, amount=100):
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    waiting_for_yes(A, 1)
    line(c, 1, "Session", amount)

def test_switching_a_tax_to_included_double_taxes_an_open_quote(env):

    A, c, freeze = env
    freeze("2026-03-10")
    post(c, "/setup/tax", taxes_posted="1", new_tax_name="VAT", new_tax_rate="20", new_tax_default="1")
    open_quote(A, c, 100)
    assert tax_lines(A, 1) == [("VAT (20%)", 20.0, "t1")]
    post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="VAT", tax_rate_0="20", tax_how_0="in",
         tax_default_0="1")
    q_ = text(page(c, "/session/1/quote/preview"))
    total = tot(A, 1)["quoted"]
    pay(c, 1, "balance", total, "2026-03-10")
    vat = year(A, 2026)["tax_due"]

    assert abs(vat - round(total * 20 / 120, 2)) < 0.005, (total, vat)
    both = "VAT (20%) $20.00" in q_ and "Includes VAT (20%)" in q_
    assert not both, q_[q_.find("Price"):q_.find("Deposit")]

def test_a_rate_change_before_the_yes_retaxes_what_the_client_agreed_to(env):

    A, c, freeze = env
    freeze("2026-03-10")
    post(c, "/setup/tax", taxes_posted="1", new_tax_name="Sales tax", new_tax_rate="6", new_tax_default="1")
    open_quote(A, c, 500)
    post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="Sales tax", tax_rate_0="8", tax_default_0="1")
    shown, agreed = tax_lines(A, 1), tot(A, 1)["quoted"]
    pay(c, 1, "deposit", 100, "2026-03-10")
    row = get_zip(c, 2026)["sales-tax.csv"][0]
    line(c, 1, "Prints", 10)

    assert abs(float(row["taxable amount"]) * float(row["rate (%)"]) / 100 - float(row["sales tax"])) < 0.01, row

    rate = float(re.search(r"\(([0-9.]+)%\)", shown[0][0]).group(1))
    assert abs(tot(A, 1)["quoted"] - (agreed + 10 * (1 + rate / 100))) < 0.005, (shown, tax_lines(A, 1), tot(A, 1)["quoted"])

def test_a_settings_file_that_changes_the_taxes_reaches_open_quotes(env):

    import io
    import json
    A, c, freeze = env
    freeze("2026-03-10")
    post(c, "/setup/tax", taxes_posted="1", new_tax_name="VAT", new_tax_rate="20", new_tax_default="1")
    open_quote(A, c, 100)
    body = json.dumps({"lumen_aureum_settings": 1, "settings": {"taxes": json.dumps(
        [dict(key="t1", name="VAT", rate=20, included=True, default=True)])}}).encode()
    c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(body), "s.json")},
           content_type="multipart/form-data", environ_base=E)
    assert tax_lines(A, 1) == [] and tot(A, 1)["quoted"] == 100.0, (tax_lines(A, 1), tot(A, 1))

def _event(A, c, price, tax_in=True):
    day = (date.today() + timedelta(days=10)).isoformat()
    c.post("/events", data={"name": "Team day", "date": day}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    c.post("/event/1/family", data={"household_name": "Bell", "parent_name": "Pat Bell", "phone": "555-0100"},
           environ_base=E)
    c.post("/event/1/family-price", data=dict(amount=price, **({"tax_in": "1"} if tax_in else {})), environ_base=E)
    return one(A, "SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name='Bell'")[0]

@pytest.mark.xfail(strict=True, reason="no price before tax lands on it once each tax rounds on its own (decided "
                   "2026-11-23); hitting it needs a rounding line or a tax-included line, a design call")
def test_family_price_including_two_taxes_misses_the_price(env):

    A, c, freeze = env
    taxes(A, [dict(key="st", name="State tax", rate=6, included=False, default=True),
              dict(key="co", name="County tax", rate=1, included=False, default=True)])
    sid = _event(A, c, "65")
    assert tot(A, sid)["quoted"] == 65.0, (tot(A, sid)["quoted"], tax_lines(A, sid))

def test_finances_and_the_event_form_name_the_studios_own_tax(env):

    A, c, freeze = env
    freeze("2026-03-10")
    post(c, "/setup/tax", taxes_posted="1", new_tax_name="VAT", new_tax_rate="20", new_tax_default="1")
    t_ = text(page(c, "/money?year=2026"))
    assert "mileage, and VAT" in t_ and "sales tax" not in t_.lower(), t_[:300]
    c.post("/events", data={"name": "Team day", "date": "2026-04-01"}, environ_base=E)
    c.post("/event/1/billing", data={"billing": "families"}, environ_base=E)
    t_ = text(page(c, "/event/1"))
    assert "Includes VAT" in t_ and "Includes sales tax" not in t_

def test_a_print_invoice_names_the_studios_own_tax(tmp_path, monkeypatch):

    import json
    import prints_env as PE
    import test_print_orders as TP
    e = PE.make(tmp_path, monkeypatch)
    con = e.A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('taxes', ?)",
                (json.dumps([dict(key="vat", name="VAT", rate=20, included=False, default=True)]),))
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('tax_prints', '1')")
    con.commit()
    con.close()
    TP.invoiced(e)
    inv = json.loads(TP.orders(e)[0]["invoice_data"])
    assert inv["tax_cents"] > 0 and inv["tax_line"] == "VAT (20%)", inv["tax_line"]
