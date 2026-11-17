import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_money_audit_130 import E, env, household, line, one, pay, post, run, session

RELEASE, HOLD = "releases once nothing is owed", "stays held until paid again"

def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def blocked(A, sid=1):
    with A.app.test_request_context():
        return A.release_blocked(sid)

def stored(A, sid=1):
    return one(A, "SELECT refund_release v FROM sessions WHERE id=?", (sid,))["v"]

def refund(c, amount, after=None, sid=1):
    data = dict(what="payment", kind="refund", amount=str(amount), received="2026-07-10", method="cash")
    if after:
        data["after_refund"] = after
    return post(c, f"/session/{sid}/money", **data)

def history(A, sid=1):
    return [r["action"] for r in _rows(A, "SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]

def _rows(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def panel(c, sid=1):
    return text(c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True))

def paid_in_full(A, c, freeze, price=350):
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", price, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    pay(c, 1, "balance", price - 100, "2026-06-02")

def test_a_partial_refund_defaults_to_release(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 40)
    assert stored(A) == "release" and blocked(A) == 0

def test_a_full_refund_defaults_to_hold_until_paid_again(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350)
    assert stored(A) == "hold" and blocked(A) == 350
    pay(c, 1, "balance", 200, "2026-07-12")
    assert blocked(A) == 150
    pay(c, 1, "balance", 150, "2026-07-13")
    assert blocked(A) == 0

def test_a_full_refund_of_a_deposit_alone_defaults_to_hold(env):
    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    refund(c, 100)
    assert stored(A) == "hold" and blocked(A) == 350

def test_the_refund_form_asks_with_the_default_preselected(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    page = c.get("/session/1", environ_base=E).get_data(as_text=True)
    assert "After this refund, the gallery:" in page
    box = page.split("data-after-refund", 1)[1].split("</span></span>", 1)[0]
    assert RELEASE in box and HOLD in box
    assert re.search(r'value="release" checked', box)
    household(A, 2, "Moss")
    session(A, 2, 2, "shot", "2026-06-01")
    line(c, 2, "Session", 350, taxable=False)
    box = c.get("/session/2", environ_base=E).get_data(as_text=True).split("data-after-refund", 1)[1]
    assert re.search(r'value="hold" checked', box.split("</span></span>", 1)[0])

def test_a_partial_refund_can_hold_until_paid_again(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 40, after="hold")
    assert stored(A) == "hold" and blocked(A) == 40
    with A.app.test_request_context():
        assert A.totals(1)["due"] == 0

def test_a_full_refund_can_release_once_nothing_is_owed(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350, after="release")
    assert stored(A) == "release" and blocked(A) == 0

def test_an_unknown_choice_takes_the_default(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350, after="maybe")
    assert stored(A) == "hold"

def test_a_later_refund_asks_again(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 40)
    assert stored(A) == "release"
    refund(c, 310)
    assert stored(A) == "hold" and blocked(A) == 350

def test_the_refund_history_line_says_the_choice(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 40)
    refund(c, 10, after="hold")
    h = history(A)
    assert f"Recorded refund $40 by cash. After this refund, the gallery {RELEASE}" in h
    assert f"Recorded refund $10 by cash. After this refund, the gallery {HOLD}" in h
    pay(c, 1, "balance", 10, "2026-07-12")
    assert not any("After this refund" in a for a in history(A) if a.startswith("Recorded balance"))

def test_the_panel_shows_held_after_refund_and_releases_in_one_click(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350)
    p = panel(c)
    assert "Held after refund until paid again: $350 to pay." in p and "Release now" in p
    post(c, "/session/1/after-refund", choice="release", back="/c/1?s=1")
    assert stored(A) == "release" and blocked(A) == 0
    p = panel(c)
    assert "After the refund, the gallery releases once nothing is owed." in p and "Hold again" in p
    assert "Held after refund" not in p
    post(c, "/session/1/after-refund", choice="hold", back="/c/1?s=1")
    assert stored(A) == "hold" and blocked(A) == 350
    h = history(A)
    assert "Released the gallery held after a refund: it releases once nothing is owed" in h
    assert "Held the gallery again after a refund: it stays held until paid again" in h

def test_release_with_money_still_owed_says_so_on_the_button(env):

    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 200, "2026-05-01")
    refund(c, 50, after="hold")
    assert blocked(A) == 200
    p = panel(c)
    assert "Held after refund until paid again: $200 to pay." in p and "Release once nothing is owed" in p
    assert "Release now" not in p
    post(c, "/session/1/after-refund", choice="release", back="/c/1?s=1")
    assert blocked(A) == 150

def test_held_and_paid_again_shows_no_refund_row(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350)
    pay(c, 1, "balance", 350, "2026-07-12")
    p = panel(c)
    assert "Held after refund" not in p and "Release now" not in p

def test_the_toggle_is_refused_without_a_refund(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    post(c, "/session/1/after-refund", choice="hold", back="/c/1?s=1")
    assert stored(A) is None and blocked(A) == 0
    assert not any("after a refund" in a for a in history(A))

def test_tips_never_count_toward_release_after_a_refund(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350)
    pay(c, 1, "tip", 350, "2026-07-12")
    assert stored(A) == "hold" and blocked(A) == 350
    run(A, "UPDATE sessions SET refund_release='release' WHERE id=1")
    assert blocked(A) == 0

def test_a_tip_does_not_make_a_full_refund_partial(env):
    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    pay(c, 1, "tip", 50, "2026-05-02")
    refund(c, 100)
    assert stored(A) == "hold" and blocked(A) == 350

def test_sessions_with_no_refund_are_unchanged(env):
    A, c, freeze = env
    freeze("2026-07-15")
    household(A, 1, "Lane")
    session(A, 1, 1, "shot", "2026-06-01")
    line(c, 1, "Session", 350, taxable=False)
    pay(c, 1, "deposit", 100, "2026-05-01")
    pay(c, 1, "tip", 250, "2026-06-02")
    assert stored(A) is None and blocked(A) == 250
    p = panel(c)
    assert "after refund" not in p.lower() and "Hold again" not in p
    pay(c, 1, "balance", 250, "2026-06-03")
    assert blocked(A) == 0

def test_a_voided_refund_leaves_the_session_as_if_it_had_none(env):
    A, c, freeze = env
    paid_in_full(A, c, freeze)
    refund(c, 350)
    assert blocked(A) == 350
    pid = one(A, "SELECT id FROM payments WHERE kind='refund'")["id"]
    post(c, f"/money/payment/{pid}/delete", set="void", back="/session/1")
    assert blocked(A) == 0 and "Held after refund" not in panel(c)
    post(c, f"/money/payment/{pid}/delete", set="restore", back="/session/1")
    assert blocked(A) == 350

def test_a_refund_written_outside_the_app_gets_the_default(env):

    A, c, freeze = env
    paid_in_full(A, c, freeze)
    run(A, "INSERT INTO payments (session_id, kind, amount, received, method, tax, taxable) VALUES (1, 'refund', -350, '2026-07-10', 'cash', 0, 0)")
    assert stored(A) is None and blocked(A) == 350
