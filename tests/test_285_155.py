import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import (E, env, get_zip, household, line, one, pay, post, reconcile, run,
                                  session, tot, year)

TWO = [dict(key="st", name="State tax", rate=6, included=False, default=True),
       dict(key="ci", name="City tax", rate=2, included=False, default=True)]

def taxes(A, items):
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('taxes', ?)", (json.dumps(items),))
    A.apply_settings()

def in_req(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def tax_lines(A, sid):
    con = A.connect()
    try:
        return [tuple(r_) for r_ in con.execute("SELECT label, amount, tax_key FROM lines WHERE session_id=? AND kind='tax' "
                                                "ORDER BY id", (sid,))]
    finally:
        con.close()

def waiting_for_yes(A, sid):

    run(A, "INSERT INTO tasks (session_id, stage, title) VALUES (?, 'quoted', 'Client said yes')", (sid,))

def page(c, url):
    r = c.get(url, environ_base=E, follow_redirects=True)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)

def test_one_rate_from_before_reads_as_one_sales_tax(env):
    A, c, freeze = env
    run(A, "INSERT INTO settings (key, value) VALUES ('tax_mode', 'flat'), ('sales_tax_pct', '8')")
    assert in_req(A, A.taxes_list) == [dict(key="sales", name="Sales tax", rate=8.0, included=False, default=True)]
    run(A, "UPDATE settings SET value='none' WHERE key='tax_mode'")
    assert in_req(A, A.taxes_list) == []

    household(A, 1, "Lane")
    session(A, 1, 1, "booked", "2026-06-01")
    run(A, "UPDATE sessions SET tax_rate=7.5 WHERE id=1")
    items, exempt, _w = in_req(A, lambda: A.session_taxes(1))
    assert [(t_["name"], t_["rate"], t_["on"]) for t_ in items] == [("Sales tax", 7.5, True)] and not exempt

def test_two_taxes_each_a_line_each_payment_by_name_and_finances_and_export(env):
    A, c, freeze = env
    taxes(A, TWO)
    freeze("2026-03-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    line(c, 1, "Session", 500)
    assert tax_lines(A, 1) == [("State tax (6%)", 30.0, "st"), ("City tax (2%)", 10.0, "ci")]
    assert tot(A, 1)["quoted"] == 540
    pay(c, 1, "deposit", 270, "2026-03-02")
    p_ = one(A, "SELECT tax, tax_parts FROM payments")
    assert p_[0] == 20.0 and json.loads(p_[1]) == {"State tax": [15.0, 250.0], "City tax": [5.0, 250.0]}
    pay(c, 1, "balance", 270, "2026-03-03")
    ym = year(A, 2026)
    assert ym["tax_due"] == 40.0 and ym["tax_names"] == [("State tax", 30.0), ("City tax", 10.0)]
    fin = page(c, "/money?year=2026")
    assert "Taxes to remit" in fin and "State tax" in fin and "City tax" in fin
    f = get_zip(c, 2026)
    rows = f["sales-tax.csv"]
    assert sorted((r["tax"], r["rate (%)"], r["tax amount"]) for r in rows) == [("City tax", "2", "10.00"),
                                                                                ("State tax", "6", "30.00")]
    total = f["summary.csv"][-1]
    assert total["taxes"] == "40.00" and total["State tax"] == "30.00" and total["City tax"] == "10.00"

    taxes(A, [dict(TWO[0], rate=7), TWO[1]])
    line(c, 1, "Prints", 100)
    assert tax_lines(A, 1) == [("State tax (6%)", 36.0, "st"), ("City tax (2%)", 12.0, "ci")]

def test_a_tax_switched_off_for_one_session(env):
    A, c, freeze = env
    taxes(A, TWO)
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    line(c, 1, "Session", 500)
    assert "City tax 2%" in page(c, "/session/1")
    post(c, "/session/1/quote/exempt", tax_key="ci")
    assert tax_lines(A, 1) == [("State tax (6%)", 30.0, "st")]
    post(c, "/session/1/quote/exempt", tax_key="ci", tax_on="1")
    assert len(tax_lines(A, 1)) == 2
    r = c.post("/session/1/quote/exempt", data=dict(tax_key="nope"), environ_base=E)
    assert r.status_code == 400

def test_a_tax_off_by_default_applies_only_where_switched_on(env):
    A, c, freeze = env
    taxes(A, [TWO[0], dict(TWO[1], default=False)])
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    line(c, 1, "Session", 100)
    assert tax_lines(A, 1) == [("State tax (6%)", 6.0, "st")]
    post(c, "/session/1/quote/exempt", tax_key="ci", tax_on="1")
    assert tax_lines(A, 1)[-1] == ("City tax (2%)", 2.0, "ci")

def test_a_tax_included_in_the_price_has_no_line_and_is_said_under_the_total(env):
    A, c, freeze = env
    taxes(A, [dict(key="vat", name="VAT", rate=10, included=True, default=True)])
    freeze("2026-03-10")
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    line(c, 1, "Session", 110)
    assert tax_lines(A, 1) == [] and tot(A, 1)["quoted"] == 110
    assert in_req(A, lambda: A.included_taxes(1)) == [dict(name="VAT", rate=10.0, amount=10.0)]
    assert "Includes VAT (10%)" in page(c, "/session/1/quote/preview")
    pay(c, 1, "balance", 110, "2026-03-02")
    assert one(A, "SELECT tax, taxable FROM payments")[:2] == (10.0, 100.0)
    assert year(A, 2026)["tax_names"] == [("VAT", 10.0)]
    assert "VAT to remit" in page(c, "/money?year=2026")

def test_on_top_and_included_together(env):

    A, c, freeze = env
    taxes(A, [dict(key="in", name="Held", rate=10, included=True, default=True),
              dict(key="top", name="Added", rate=5, included=False, default=True)])
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    line(c, 1, "Session", 110)
    assert tax_lines(A, 1) == [("Added (5%)", 5.0, "top")]
    assert in_req(A, lambda: A.included_taxes(1)) == [dict(name="Held", rate=10.0, amount=10.0)]

def test_each_fee_says_which_taxes_apply(env):
    A, c, freeze = env
    taxes(A, TWO)
    items = in_req(A, A.current_schedule)["items"]
    form = dict(fees_posted="1")
    for i, it in enumerate(items):
        form[f"fee_label_{i}"], form[f"fee_amt_{i}"] = it["label"], str(it["amount"])
        form[f"fee_tax_{i}_st"] = "1"
        if i:
            form[f"fee_tax_{i}_ci"] = "1"
    form[f"fee_tax_1_st"] = ""
    form.pop("fee_tax_1_st")
    form.pop("fee_tax_1_ci")
    post(c, "/settings", **form)
    got = in_req(A, A.current_schedule)["items"]
    assert got[0]["taxable"] and got[0]["untaxed"] == ["ci"]
    assert not got[1]["taxable"] and "untaxed" not in got[1]
    assert got[2]["taxable"] and "untaxed" not in got[2]
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    run(A, "INSERT INTO quotes (session_id, schedule_id) VALUES (1, ?)", (in_req(A, A.current_schedule)["id"],))
    post(c, "/session/1/quote/fee", fee_key=got[0]["key"], qty="1")
    amt = one(A, "SELECT amount, untaxed FROM lines WHERE session_id=1 AND kind!='tax'")
    assert amt[1] == "ci"
    assert [(k_, a_) for _l, a_, k_ in tax_lines(A, 1)] == [("st", round(amt[0] * 0.06, 2))]

    assert "City tax on " in page(c, "/settings?tab=prices")

def test_a_client_exempt_on_every_session_not_yet_booked(env):
    A, c, freeze = env
    taxes(A, TWO)
    household(A, 1, "Lane")
    session(A, 1, 1, "quoted", "2026-05-01")
    session(A, 2, 1, "booked", "2026-04-01")
    waiting_for_yes(A, 1)
    line(c, 1, "Session", 100)
    line(c, 2, "Session", 100)
    in_req(A, lambda: A.lock_tax_rate(2))
    assert "Exemption certificate" in page(c, "/client/1")
    post(c, "/client/1", name="Lane", address="", source="", notes="", tax_posted="1", tax_exempt="1", tax_cert="EX-42")
    assert tax_lines(A, 1) == [] and len(tax_lines(A, 2)) == 2
    assert "certificate EX-42 on file" in page(c, "/session/1")

    in_req(A, lambda: A.lock_tax_rate(1))
    assert one(A, "SELECT tax_exempt FROM sessions WHERE id=1")[0] == 1
    rows = in_req(A, lambda: A.accounts_files(2026))
    assert rows

def test_a_tax_rule_sets_switches_and_rates_and_a_switch_by_hand_wins(env):
    A, c, freeze = env
    taxes(A, [TWO[0], dict(TWO[1], default=False)])
    rule = lambda sale, items: ({"ci": dict(on=True, rate=3, note="Inside the city")} if "City" in (sale["address"] or "")
                                else {"ci": False})
    A.hooks.tax_rules.append(rule)
    try:
        run(A, "INSERT INTO households (id, name, address) VALUES (1, 'Lane', '1 Main St, City')")
        session(A, 1, 1, "quoted", "2026-05-01")
        waiting_for_yes(A, 1)
        line(c, 1, "Session", 100)
        assert tax_lines(A, 1) == [("State tax (6%)", 6.0, "st"), ("City tax (3%)", 3.0, "ci")]
        assert "Inside the city" in (page(c, "/session/1"))
        post(c, "/session/1/quote/exempt", tax_key="ci")
        assert tax_lines(A, 1) == [("State tax (6%)", 6.0, "st")]
        with A.app.test_request_context():
            A.db()
            assert A.tax_pct_at("2 Main St, City") == 9 and A.tax_pct_at("Lakeside") == 6

        A.hooks.tax_rules.append(lambda sale, items: 1 / 0)
        line(c, 1, "Prints", 10)
        assert tax_lines(A, 1) == [("State tax (6%)", 6.6, "st")]
    finally:
        A.hooks.tax_rules[:] = []

def test_setup_lists_adds_and_refuses(env):
    A, c, freeze = env
    post(c, "/setup/tax", taxes_posted="1", new_tax_name="", new_tax_rate="6.25", new_tax_default="1")
    assert in_req(A, A.taxes_list) == [dict(key="t1", name="Sales tax", rate=6.25, included=False, default=True)]
    r = post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="Sales tax", tax_rate_0="6.25",
             tax_default_0="1", new_tax_name="sales TAX", new_tax_rate="1")
    assert b"Two taxes are called" in r.data
    r = post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="Sales tax", tax_rate_0="40", tax_default_0="1")
    assert b"Enter the rate for Sales tax" in r.data
    post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="GST", tax_rate_0="5", tax_how_0="in",
         new_tax_name="PST", new_tax_rate="7", new_tax_default="1")
    assert [(t_["key"], t_["name"], t_["included"]) for t_ in in_req(A, A.taxes_list)] == [("t1", "GST", True),
                                                                                         ("t2", "PST", False)]
    assert "GST" in page(c, "/setup/tax") and "PST" in page(c, "/setup/tax")
    post(c, "/setup/tax", taxes_posted="1", tax_key_0="t1", tax_name_0="GST", tax_rate_0="5", tax_del_0="1",
         tax_key_1="t2", tax_name_1="PST", tax_rate_1="7", tax_default_1="1")
    assert [t_["name"] for t_ in in_req(A, A.taxes_list)] == ["PST"]

    assert one(A, "SELECT value FROM settings WHERE key='sales_tax_pct'")[0] == "7"

def test_the_settings_file_carries_the_taxes_and_reads_an_older_one():
    from settings_io import ImportProblem, check_taxes, parse
    s_, _r, _f, _st = parse(json.dumps({"lumen_aureum_settings": 1, "settings": {"taxes": json.dumps(TWO)}}), {})
    assert json.loads(s_["taxes"])[1]["name"] == "City tax"
    s_, _r, _f, _st = parse(json.dumps({"lumen_aureum_settings": 1, "settings": {"tax_mode": "flat", "sales_tax_pct": "6"}}), {})
    assert json.loads(s_["taxes"]) == [dict(key="sales", name="Sales tax", rate=6.0, included=False, default=True)]
    for bad in ([dict(TWO[0], rate=30)], [TWO[0], dict(TWO[1], name="state TAX")], [dict(TWO[0], key="Bad key")], "x"):
        with pytest.raises(ImportProblem):
            check_taxes(json.dumps(bad) if not isinstance(bad, str) else bad)

def test_a_1_5_3_database_reads_every_figure_as_before(tmp_path, monkeypatch):

    import test_upgrade_from_153 as U
    U._place(tmp_path)
    A, c, _taken = U._open(tmp_path, monkeypatch)
    y = U.YEAR
    assert in_req(A, A.taxes_list) == [dict(key="sales", name="Sales tax", rate=6.0, included=False, default=True)]
    before_fin = U._finances(A, c, y)
    before_zip = get_zip(c, y)

    def snap():
        con = A.connect()
        try:
            return [[tuple(r_) for r_ in con.execute(sql_)] for sql_ in (
                "SELECT session_id, label, amount, kind FROM lines ORDER BY session_id, kind, label, amount", "SELECT id, tax, taxable FROM payments ORDER BY id",
                "SELECT * FROM tax_adjustments ORDER BY id", "SELECT * FROM doc_numbers ORDER BY number",
                "SELECT * FROM charges ORDER BY id")]
        finally:
            con.close()
    was = snap()

    sids = [r_[0] for r_ in U._q(A, "SELECT id FROM sessions WHERE tax_rate IS NOT NULL")]
    assert len(sids) >= 5 and U._q(A, "SELECT COUNT(*) FROM lines WHERE kind='tax' AND session_id IN (%s)" % ",".join(map(str, sids)))[0][0] >= 3

    def rebuild():
        for sid in sids:
            A._tax_line(sid)
            A.settle_tax(sid)
            A._invoice_still_sent(sid)
            A.sync_charges(sid)
    in_req(A, rebuild)
    now = snap()
    for i_, (a_, b_) in enumerate(zip(was, now)):
        assert set(a_) ^ set(b_) == set(), (i_, sorted(set(a_) - set(b_)), sorted(set(b_) - set(a_)))
    assert now == was
    assert U._finances(A, c, y) == before_fin == U.EXPECTED["finances"]

    for (sid,) in U._q(A, "SELECT id FROM sessions WHERE tax_rate IS NULL AND NOT tax_exempt"):
        base = in_req(A, lambda: A.taxable_base(sid))
        in_req(A, lambda: A._tax_line(sid))
        assert tax_lines(A, sid) == ([("Sales tax (6%)", round(base * 6 / 100, 2), "sales")] if base > 0 else [])
    assert get_zip(c, y) == before_zip
    ym = year(A, y)
    assert ym["tax_names"] == ([("Sales tax", ym["tax_due"])] if ym["tax_due"] else [])
    assert "Sales tax to remit" in page(c, f"/money?year={y}")
    reconcile(A, c, y)

