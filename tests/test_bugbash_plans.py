import html
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import E, env, household, line, pay, post, run, session, tot, one
from test_money_x_160 import booked, plan, ctx, rescheduled

def text(r):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True))))

class _Form(HTMLParser):

    def __init__(self):
        super().__init__()
        self.depth, self.done, self.fields, self.sel, self.ta = 0, False, [], None, None

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "form" and not self.done and self.depth == 0 and (a.get("method") or "").lower() == "post" and not a.get("action"):
            self.depth = 1
            return
        if not self.depth:
            return
        if tag == "input" and a.get("name") and not a.get("disabled") is not None:
            t_ = (a.get("type") or "text").lower()
            if t_ in ("checkbox", "radio") and "checked" not in a:
                return
            if t_ in ("submit", "button", "file"):
                return
            self.fields.append((a["name"], a.get("value") or ("on" if t_ in ("checkbox", "radio") else "")))
        elif tag == "select" and a.get("name"):
            self.sel = [a["name"], None, None]
        elif tag == "option" and self.sel is not None:
            if self.sel[2] is None:
                self.sel[2] = a.get("value", "")
            if "selected" in a:
                self.sel[1] = a.get("value", "")
        elif tag == "textarea" and a.get("name"):
            self.ta = [a["name"], ""]

    def handle_data(self, data):
        if self.ta is not None:
            self.ta[1] += data

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == "select" and self.sel is not None:
            self.fields.append((self.sel[0], self.sel[1] if self.sel[1] is not None else (self.sel[2] or "")))
            self.sel = None
        elif tag == "textarea" and self.ta is not None:
            self.fields.append(tuple(self.ta))
            self.ta = None
        elif tag == "form":
            self.depth, self.done = 0, True

def type_form(c, key):
    p = _Form()
    p.feed(c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True))
    return dict(p.fields)

def test_a_deposit_word_that_contains_deposit_is_not_doubled_in_the_agreement(env):

    A, c, freeze = env
    freeze("2026-11-23")
    run(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('deposit_word', 'booking deposit')")
    household(A, 1, "Lane")
    booked(A, c, 1, "2027-04-10", 1000)
    t = text(c.get("/session/1/agreement", environ_base=E))
    assert "booking deposit" in t.lower()
    assert "booking booking" not in t.lower(), re.findall(r".{40}booking booking.{40}", t, re.I)[:3]

def test_a_reschedule_fee_on_an_engagement_billed_on_the_wedding_plan_stays_on_the_plan(env):

    A, c, freeze = env
    freeze("2026-11-23")
    household(A, 1, "Lane")
    booked(A, c, 1, "2027-06-10", 3000, stype="wedding")
    booked(A, c, 2, "2027-03-10", 0, stype="engagement")
    run(A, "DELETE FROM lines WHERE session_id=2")
    run(A, "UPDATE sessions SET plan_of=1 WHERE id=2")
    assert ctx(A, A.billed_on_plan, 2)
    rescheduled(A, 2, "2026-11-23", sid=2)
    assert ctx(A, A.resched_fees_open, 2) == 1
    r = c.post("/session/2/reschedule-fee", data=dict(act="add", back="/c/1?s=2"), environ_base=E)
    assert r.status_code == 302
    t2 = tot(A, 2)
    page = text(c.get("/c/1?s=2", environ_base=E))
    assert ctx(A, A.billed_on_plan, 2), "the engagement left the plan's billing"
    assert t2["deposit_req"] == 0, f"the engagement now asks a deposit of its own: {t2['deposit_req']}"
    assert "Send the deposit invoice" not in page
    t1 = tot(A, 1)
    assert t1["quoted"] >= 3050 - 0.005, t1

def test_turning_on_a_types_plan_does_not_make_sessions_booked_earlier_overdue(env):

    A, c, freeze = env
    freeze("2026-08-01")
    household(A, 1, "Lane")
    booked(A, c, 1, "2026-12-20", 1000)
    pay(c, 1, "deposit", 100, "2026-08-01")
    freeze("2026-11-23")
    form = type_form(c, "family")
    assert "installments" in form
    form["installments"] = "4"
    assert c.post("/settings/type/family", data=form, environ_base=E).status_code == 302
    assert A.C.INSTALLMENTS["family"] == 4
    rows, ov, rem = plan(A)
    assert ov["amount"] == 0.0, f"overdue at once: {ov}, reminder: {rem and rem['words']}"
    assert rows == [], [(r_["n"], r_["due"], r_["status"]) for r_ in rows]
    fin = text(c.get("/money", environ_base=E))
    assert "Overdue" not in fin

def test_the_walkthroughs_payments_step_does_not_make_sessions_booked_earlier_overdue(env):

    A, c, freeze = env
    freeze("2026-08-01")
    household(A, 1, "Lane")
    booked(A, c, 1, "2026-12-20", 1000)
    pay(c, 1, "deposit", 100, "2026-08-01")
    freeze("2026-11-23")
    import walkthrough as W
    t = next(t_ for t_ in A.C.TYPES if t_["key"] == "family")
    n = [s_["id"] for s_ in W.steps_for(dict(t))].index("installments")
    r = c.post(f"/settings/type/family/walk/{n}", data=dict(act="save", installments="4"), environ_base=E)
    assert r.status_code == 302
    assert A.C.INSTALLMENTS["family"] == 4
    rows, ov, rem = plan(A)
    assert ov["amount"] == 0.0, f"overdue at once: {ov}, reminder: {rem and rem['words']}"
    assert rows == [], [(r_["n"], r_["due"], r_["status"]) for r_ in rows]

def test_invoice_schedule_rows_agree_with_the_balance_due_after_a_part_payment(env):

    A, c, freeze = env
    freeze("2026-11-23")
    household(A, 1, "Lane")
    booked(A, c, 1, "2027-04-10", 1000)
    post(c, "/session/1/installments", n="4", back="/c/1?s=1")
    pay(c, 1, "deposit", 100, "2026-11-23")
    pay(c, 1, "balance", 300, "2026-11-24")
    t = text(c.get("/session/1/invoice", environ_base=E))
    assert "Balance due $600.00" in t
    rows = re.findall(r"Payment (\d) of 4, due [A-Z][a-z]+ \d+, \d{4}( \(paid\))? \$([\d,]+\.\d\d)", t)
    assert len(rows) == 4, t
    unpaid = sum(float(a_.replace(",", "")) for _n, paid_, a_ in rows if not paid_)
    assert abs(unpaid - 600.0) < 0.005, f"unpaid rows add up to {unpaid:.2f}, balance due 600.00: {rows}"

def test_today_shows_a_late_installment_that_finances_calls_overdue(env):

    A, c, freeze = env
    freeze("2026-11-23")
    household(A, 1, "Lane")
    booked(A, c, 1, "2027-04-10", 1000)
    post(c, "/session/1/installments", n="4", back="/c/1?s=1")
    pay(c, 1, "deposit", 100, "2026-11-23")
    pay(c, 1, "balance", 300, "2026-11-24")
    freeze("2027-01-25")
    assert plan(A)[1]["amount"] == 150.0
    assert "Overdue $150.00" in text(c.get("/money", environ_base=E))
    today_ = text(c.get("/", environ_base=E))
    assert "$150.00" in today_ or "nstallment" in today_, today_[today_.find("Tasks"):today_.find("Tasks") + 400]

def _page(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def _owed_row(A):
    return A.q("SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.id=1",
               one=True)

def test_an_issued_invoices_due_date_is_not_overdue_before_it_comes(env):

    A, c, freeze = env
    freeze("2026-03-01")
    c.post("/setup/shoot", data={"preset_realestate": "1"}, environ_base=E)
    household(A, 1, "Acme Realty")
    run(A, "INSERT INTO people (household_id, name, role, email) VALUES (1, 'Pat Agent', 'adult', 'pat@example.com')")
    session(A, 1, 1, "shot", "2026-02-25", "realestate")
    line(c, 1, "Listing photos", 500, taxable=False)
    c.post("/session/1/invoice/issue", data={}, environ_base=E)
    run(A, "INSERT INTO invoice_sent (session_id, number, sig) VALUES (1, 'INV-2026-0001', 'x')")
    freeze("2026-03-10")
    c.post("/setup/money", data=dict(deposit="150", event_pct="50", reschedule_fee="25", delivery_days="10",
                                     event_delivery_days="20", net_days="15", pay_cash="1", bal_realestate="after"),
           environ_base=E)
    freeze("2026-03-20")
    inv = _page(c.get("/session/1/invoice", environ_base=E).get_data(as_text=True))
    assert "Due by March 31, 2026" in inv
    with A.app.test_request_context():
        ov = A.overdue(_owed_row(A))
        words = A.payment_reminder(_owed_row(A))["words"]
    fin = _page(c.get("/money", environ_base=E).get_data(as_text=True))
    assert "was due Mon Mar 16" not in fin, fin[fin.find("Owed to you"):][:160]
    assert ov["amount"] == 0.0, (ov, words)

def test_quote_terms_do_not_name_a_bigger_deposit_than_the_quote_asks_for(env):

    A, c, freeze = env
    household(A, 1, "Lopez")
    session(A, 1, 1, "quoted", "2026-12-05", "family")
    line(c, 1, "Mini add-on", 30)
    t = _page(c.get("/session/1/quote/preview", environ_base=E).get_data(as_text=True))
    assert "Deposit to hold the date $30.00" in t
    assert "A $100.00 deposit holds your date" not in t, "the terms name a $100 deposit beside a $30 price"
