import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journey_env import E, day, make_studio, new_session, one, run
from test_305_journeys import retouch
from test_307_fixes import agreement_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIGHT = "with light retouching on up to"
PROOFS = "Before delivery I may show you proofs in a private gallery so you can choose favorites."

@pytest.fixture
def studio(tmp_path, monkeypatch):
    return make_studio(tmp_path, monkeypatch)

def test_309a_the_agreement_drops_retouching_and_proofs_with_the_session(studio):
    A, c = studio
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    a_ = agreement_text(c, sid)
    assert LIGHT in a_ and PROOFS in a_
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "note_why": "No retouching for this session"}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert LIGHT not in a_ and PROOFS not in a_ and "Every image I deliver is edited as described on my price list." in a_

    c.post("/step/reopen", data={"task": tid}, environ_base=E)
    retouch(c, False)
    a_ = agreement_text(c, sid)
    assert LIGHT not in a_ and PROOFS not in a_

def test_309b_passphrase_drafts_and_letters_never_say_the_word():
    g = open(os.path.join(ROOT, "plugins", "gallery.py")).read()
    assert "When it asks, enter the passphrase you chose with me." in g and "enter the word" not in g
    lt = open(os.path.join(ROOT, "plugins", "letters.py")).read()
    assert "{% if codeword %} When the gallery asks, enter the passphrase you chose with" in lt
    assert re.search(r"codeword=bool\(link and \"&c=1\" in link\)", lt)
    assert "enter the word" not in lt

def test_309c_a_proofs_gallery_online_never_puts_a_gallery_in_a_usb_agreement(studio):
    A, c = studio
    from journey_env import sql
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    assert "online gallery" not in agreement_text(c, sid)

    sql(A, "INSERT INTO galleries (session_id, key, built_at, upload_state, uploaded_at) VALUES (?, 'k', ?, 'up', ?)",
        (sid, day(-1), day(-1)))
    sql(A, "UPDATE sessions SET gallery_built=? WHERE id=?", (day(-1), sid))
    sql(A, "UPDATE documents SET status='signed', sent=?, signed=? WHERE session_id=? AND kind='agreement'", (day(-3), day(-2), sid))
    c.post(f"/session/{sid}/agreement/unsign", data={}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert "online gallery" not in a_ and "Your gallery link works for anyone who has it" not in a_
    c.post(f"/session/{sid}/quick", data={"act": "sign", "kind": "agreement"}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert "online gallery" not in a_ and "Your gallery link works for anyone who has it" not in a_

def test_309d_dropping_retouching_takes_its_lines_off_and_bringing_it_back_restores_them(studio):
    A, c = studio
    from test_305_journeys import quoted
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid, sent=False)
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "retouch", "qty": "2", "once": "rt"}, environ_base=E)
    before = run(A, lambda: A.totals(sid)["quoted"])
    n, amt = run(A, lambda: A.retouch_lines_total(sid))
    assert n == 1 and amt > 0
    page_ = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "retouching (" in page_ and "comes off what is still owed" in page_.replace("&#39;", "'")
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "note_why": "No retouching for this session"}, environ_base=E)
    assert run(A, lambda: A.retouch_lines_total(sid))[0] == 0
    assert abs(run(A, lambda: A.totals(sid)["quoted"]) - (before - amt)) < 0.01
    c.post("/step/reopen", data={"task": tid}, environ_base=E)
    assert run(A, lambda: A.retouch_lines_total(sid)) == (n, amt)
    assert abs(run(A, lambda: A.totals(sid)["quoted"]) - before) < 0.01

def test_309d_retouching_already_paid_shows_as_a_credit(studio):
    A, c = studio
    from test_305_journeys import quoted
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid, sent=False)
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "retouch", "qty": "2", "once": "rt"}, environ_base=E)
    c.post(f"/session/{sid}/quick", data={"act": "book", "final_shown": "1", "final": "1"}, environ_base=E)
    due = run(A, lambda: A.totals(sid)["due"])
    c.post(f"/session/{sid}/money", data={"what": "payment", "kind": "deposit", "amount": "%.2f" % due, "method": "cash",
                                         "received": day(0), "once": "all"}, environ_base=E)
    _n, amt = run(A, lambda: A.retouch_lines_total(sid))
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "note_why": "No retouching for this session"}, environ_base=E)
    assert abs(run(A, lambda: A.totals(sid)["due"]) + amt) < 0.01
    p = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "paid for retouching that is no longer included" in p

    from journey_walker import parse
    form = next(f for f in parse(p).iter() if f.tag == "form" and "Record refund" in f.words())
    fields = {i.get("name"): i.get("value") for i in form.iter() if i.tag == "input"}
    assert fields["kind"] == "refund" and abs(float(fields["amount"]) - amt) < 0.01 and "refund" in form.get("data-confirm")
    c.post(form.get("action"), data=fields, environ_base=E)
    assert one(A, "SELECT kind FROM payments WHERE session_id=? ORDER BY id DESC", (sid,))[0] == "refund"
    assert abs(run(A, lambda: A.totals(sid)["due"])) < 0.01

def test_309e_the_agreement_price_reads_the_lines_after_a_drop_and_after_bringing_it_back(studio):
    A, c = studio
    from test_305_journeys import quoted
    sid = new_session(A, "family", "inquiry", shoot_date=day(14), delivery_method="usb")
    quoted(A, c, sid, sent=False)
    c.post(f"/session/{sid}/quote/fee", data={"fee_key": "retouch", "qty": "2", "once": "rt"}, environ_base=E)
    r = c.post(f"/session/{sid}/quote/send", data={"total": "%.2f" % run(A, lambda: A.totals(sid)["quoted"])}, environ_base=E)
    vid = one(A, "SELECT id FROM quote_versions WHERE session_id=? ORDER BY id DESC", (sid,))[0]
    c.post(f"/quote/{vid}/sent", data={}, environ_base=E)
    full = run(A, lambda: A.totals(sid)["quoted"])
    a_ = agreement_text(c, sid)
    assert "Additional retouching" in a_ and run(A, lambda: A.money(full)) in a_
    tid = one(A, "SELECT id FROM tasks WHERE session_id=? AND title='Send retouched photos'", (sid,))[0]
    c.post("/close", data={"task": tid, "outcome": "na", "note_why": "No retouching for this session"}, environ_base=E)
    now_ = run(A, lambda: A.totals(sid)["quoted"])
    a_ = agreement_text(c, sid)
    assert "Additional retouching" not in a_ and run(A, lambda: A.money(now_)) in a_ and run(A, lambda: A.money(full)) not in a_
    c.post("/step/reopen", data={"task": tid}, environ_base=E)
    a_ = agreement_text(c, sid)
    assert "Additional retouching" in a_ and run(A, lambda: A.money(full)) in a_
