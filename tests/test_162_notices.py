import os
import re
import sys
import time

import pytest

CLONE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CLONE)
sys.path.insert(0, os.path.join(CLONE, "tests"))
from test_162_browser import BASE, E, _notice, _tab, browser, env
from test_money_docs_101 import _line, _page, _pay
from test_user_testing_fixes import _app, _new

def _toast(page):
    m = re.search(r'<div class="toast ([^"]*)"[^>]*>', page)
    return m.group(0) if m else ""

def test_162_refusal_banner_stays_in_view_on_a_phone(browser, env):

    A, c = env
    _notice(c, "Not saved: a sample refusal.", True)
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/money")
        b = p.locator("main > .toast[data-refused]")
        assert b.is_visible()
        p.evaluate("document.querySelector('main').style.minHeight = '4000px'; scrollTo(0, 1500)")
        p.wait_for_timeout(100)
        y = b.bounding_box()["y"]
        assert 0 <= y < 120, f"refusal banner is at y={y}, off screen on a phone"
    finally:
        ctx.close()

def test_162_refusal_banner_is_in_view_after_an_anchored_redirect_on_a_phone(browser, env):

    A, c = env
    _notice(c, "Not saved: a sample refusal.", True)
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/security#idle")
        p.wait_for_timeout(200)
        assert p.evaluate("scrollY") > 200, "the anchor is not deep enough in the page to test this"
        y = p.locator("main > .toast[data-refused]").bounding_box()["y"]
        assert 0 <= y < 120, f"refusal banner is at y={y}, off screen on a phone"
    finally:
        ctx.close()

def test_162_website_refusal_is_a_banner(env):

    A, c = env
    c.post("/settings?tab=profile", data={"business_website": "http://exa mple"}, environ_base=E)
    page = c.get("/settings?tab=profile", environ_base=E).get_data(as_text=True)
    assert "The website wasn&#39;t saved" in page or "The website wasn't saved" in page
    assert "data-refused" in _toast(page), _toast(page)

def test_162_idle_hours_refusal_is_a_banner(env):

    A, c = env
    c.post("/settings", data={"idle_posted": "1", "idle_signout_hours": "99", "back": "/security#idle"}, environ_base=E)
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert "wasn&#39;t saved" in page or "wasn't saved" in page
    assert "data-refused" in _toast(page), _toast(page)

def test_162_overpayment_warning_is_a_banner(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava")
    _line(c, sid, "45")
    _pay(c, sid, "50", kind="balance")
    page = _page(c, f"/session/{sid}/edit")
    assert "$5.00 more than the balance" in page
    assert "ui-said" not in _toast(page), "overpayment (a credit owed back) shown as a fading quiet line: " + _toast(page)

    assert "data-warn" in _toast(page) and "data-refused" not in _toast(page), _toast(page)

MUST_NOT_MISS = [

    ("app.py", "The signed agreement says you deliver the photos", "signed agreement contradicted"),
    ("app.py", "The Terms charge a reschedule fee for this one", "money: a fee to add or waive"),
    ("app.py", "so the discount stays, as the agreement says", "agreement + money: the removal asked for did not happen"),
    ("app.py", "more than the balance, so the session now", "money: a credit owed back"),
    ("app.py", "This is the public ntfy server, where anyone who learns the topic", "privacy: notices readable by anyone"),
    ("plugins/prints.py", "The issued invoice stays as issued", "money: the lab charged more shipping than invoiced"),
    ("plugins/prints.py", "more than the balance, so the order now", "money: a credit owed back"),
    ("plugins/gallery.py", "The web host could not be told", "a gallery coming down: the host was not told"),
    ("setup.py", "warn_ = \" \".join(w_ for w_ in (netsec.only_here_words(base)", "clients can't open galleries there"),
]

@pytest.mark.parametrize("fn,needle,why", MUST_NOT_MISS, ids=[m[2][:40] for m in MUST_NOT_MISS])
def test_162_must_not_miss_warning_is_marked_for_a_banner(fn, needle, why):

    src = open(os.path.join(CLONE, fn), encoding="utf-8").read().split("\n")
    at = next(i for i, l_ in enumerate(src) if needle in l_)
    window = "\n".join(src[at:at + 8])
    branch = window.split("return ")[0]
    assert re.search(r"notice_refused|refused\s*=\s*True", branch), f"{fn}:{at + 1} ({why}) is a fading quiet line"

def test_162_stay_signed_in_after_expiry_is_not_silent(browser, env):

    A, c = env
    import auth
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('idle_signout_hours', '1') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit()
    con.close()
    with c.session_transaction() as s:
        sid = s["sid"]
    ctx, p = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/settings")
        p.evaluate("document.querySelector('dialog[data-idle-warn]').showModal()")
        auth.LAST_SEEN[sid] = time.time() - 3600 - 30
        p.get_by_role("button", name="Stay signed in").click()
        p.wait_for_timeout(800)
        signed_out = "/login" in p.url or p.locator("main.auth").count() > 0
        still_open = p.evaluate("document.querySelector('dialog[data-idle-warn]') && document.querySelector('dialog[data-idle-warn]').open")
        said = p.locator("[role=alert]").count() > 0
        assert signed_out or still_open or said, (
            f"Stay signed in after expiry: the dialog closed with no word and the page still looks signed in "
            "(the server had already signed out)")
    finally:
        ctx.close()

def test_162_dismissing_the_banner_keeps_the_focus_in_the_page(browser, env):

    A, c = env
    _notice(c, "Not saved: a sample refusal.", True)
    ctx, p = _tab(browser, c, 1280)
    try:
        p.goto(BASE + "/money")
        p.locator("main > .toast [data-dismiss]").focus()
        p.keyboard.press("Enter")
        assert p.locator("main > .toast").count() == 0
        assert p.evaluate("document.activeElement !== document.body"), "focus fell to <body> after dismissing"
    finally:
        ctx.close()
