import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env
from test_walk_15 import _booked

NEW = "# Our agreement\n\nWe photograph [CLIENT] with care.\n\nPayment is due as the quote says."

def _form(doc):
    m = re.search(r'<details class="ui-fold wording-here".*?</details>', doc, re.S)
    assert m, "no Edit the wording fold"
    form = m.group(0)
    data = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    action = re.search(r'<form method="post" action="([^"]+)"', form).group(1)
    return form, action, data

def test_a_sample_agreement_offers_edit_the_wording_beside_approve(env):
    A, c = env
    _booked(A)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    draft = re.search(r'<div class="draft">.*?</details>', doc, re.S).group(0)
    assert ">Approve this wording</button>" in draft and ">Edit the wording</span>" in draft
    form, action, data = _form(doc)
    assert action == "/settings/wording/agreement/here" and data["back"] == "/session/1/agreement"
    assert "type" not in data
    assert "For every session type without its own wording" in form
    with A.app.test_request_context():
        assert A.own_wording("agreement", "family").split()[:3] == re.search(
            r"<textarea[^>]*>(.*?)</textarea>", form, re.S).group(1).split()[:3]
    assert "Saving counts as your review" in form

def test_saving_on_the_document_counts_as_the_review_and_comes_back_to_it(env):
    A, c = env
    _booked(A)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    _, action, data = _form(doc)
    r = c.post(action, data=dict(data, text=NEW), environ_base=E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/session/1/agreement")
    with A.app.test_request_context():
        assert A.doc_reviewed("agreement", "family")
        assert A.setting("agreement_text").startswith("# Our agreement")
    after = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    assert "SAMPLE wording" not in after and "We photograph" in after and "with care." in after
    assert ">Email it</a>" in after
    tip = re.search(r'title="([^"]*)">Email it</a>', after).group(1)
    assert "key facts" in tip and "attach" not in tip and "PDF" not in tip
    assert "Edited and reviewed the agreement wording on the document" in _all_audit(A)

def _all_audit(A):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit").fetchall()]
    finally:
        con.close()

def test_a_type_with_its_own_wording_is_edited_there(env):
    A, c = env
    _booked(A)
    c.post("/settings/wording/agreement?type=family", data={"text": "# Family only\n\nFamily words."}, environ_base=E)
    doc = c.get("/session/1/agreement", environ_base=E).get_data(as_text=True)
    form, action, data = _form(doc)
    assert data.get("type") == "family" and "For Family" in form
    c.post(action, data=dict(data, text="# Family only\n\nFamily words, edited."), environ_base=E)
    with A.app.test_request_context():
        assert A.doc_reviewed("agreement", "family") and not A.doc_reviewed("agreement")
        assert not A.setting("agreement_text").strip()
        assert "edited." in A.own_wording("agreement", "family")

def test_empty_wording_is_refused_and_nothing_changes(env):
    A, c = env
    _booked(A)
    _, action, data = _form(c.get("/session/1/agreement", environ_base=E).get_data(as_text=True))
    c.post(action, data=dict(data, text="   "), environ_base=E)
    with A.app.test_request_context():
        assert not A.doc_reviewed("agreement", "family") and not A.setting("agreement_text").strip()

def test_no_editor_in_the_pdf_or_on_terms(env, monkeypatch):
    A, c = env
    _booked(A)
    monkeypatch.setattr(A, "html_pdf", lambda html, name: html)
    pdf = c.get("/session/1/agreement.pdf", environ_base=E).get_data(as_text=True)
    assert "SAMPLE wording" in pdf and ">Edit the wording</span>" not in pdf and "<textarea" not in pdf
    inv = c.get("/session/1/invoice", environ_base=E).get_data(as_text=True)
    assert 'action="/settings/review/terms"' in inv and ">Edit the wording</span>" not in inv

def test_edit_save_stays_on_the_same_document_with_send_available(browser, env):
    A, c = env
    _booked(A)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.on("dialog", lambda d: d.accept())
        p.goto(BASE + "/session/1/agreement")
        p.evaluate("window.__stay = 1")
        p.locator("summary", has_text="Edit the wording").click()
        p.locator("#wording-here-text").fill(NEW)
        p.evaluate("window.scrollTo({top: 120, behavior: 'instant'})")
        y0 = p.evaluate("scrollY")
        p.get_by_role("button", name="Save this wording").click()
        p.locator(".doc-saved").wait_for()
        assert p.evaluate("window.__stay") == 1
        assert p.url == BASE + "/session/1/agreement"
        assert p.locator(".draft").count() == 0
        assert "with care." in p.locator("body > .page").inner_text()
        assert p.get_by_role("link", name="Email it").count() == 1

        assert abs(p.evaluate("scrollY") - min(y0, p.evaluate("document.documentElement.scrollHeight - innerHeight"))) < 80
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()
    with A.app.test_request_context():
        assert A.doc_reviewed("agreement", "family")
