import json
import re

import pytest
from test_studio_page import E, env, setting, switch_on
from test_studio_site_release import php_site, upload
from test_studio_inquiries import GOOD, put_inquiry
from test_fix15_a1 import BASE, _tab, browser

def q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

def _review(A, c, S, web, *fields):
    switch_on(c)
    upload(c, S)
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    for f in fields:
        put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, **f)))
    assert A.app.config["studio_page_collect"]()["added"] == len(fields)
    return {f_["name"]: rid for rid, f_ in ((r[0], json.loads(r[1])) for r in q(A, "SELECT id, fields FROM studio_inquiry_review"))}

def _today_list(c):
    page = c.get("/", environ_base=E).get_data(as_text=True)
    return page[page.index('id="inquiries"'):]

def test_an_other_inquiry_asks_which_kind_and_not_sure_says_so(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="Riley Other", type="other", message="A bakery's 10th anniversary party"),
                  dict(name="Sam Unsure", type=""))
    sec = _today_list(c)
    item = lambda rid: sec[sec.index(f'id="inq-{rid}"'):sec.index("</li>", sec.index(f'id="inq-{rid}"'))]
    other, unsure = item(ids["Riley Other"]), item(ids["Sam Unsure"])
    assert "Kind of session: Other" in other and 'name="type"' in other
    assert "Kind of session: Not sure yet" in unsure and 'name="type"' in unsure and "not given" not in sec
    kinds = re.findall(r'<option value="([^"]+)"', re.search(r'<select id="rv-type-%d"[^>]*>(.*?)</select>' % ids["Riley Other"],
                                                             sec, re.S).group(1))
    assert kinds and kinds[0] != "other"
    pick = kinds[-1]
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": ids["Riley Other"], "type": pick}, environ_base=E)
    (t,), = q(A, "SELECT s.type FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name='Riley Other'")
    assert t == pick
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": ids["Sam Unsure"], "type": kinds[0]}, environ_base=E)
    (summary,), = q(A, "SELECT l.summary FROM log l JOIN households h ON h.id=l.household_id WHERE h.name='Sam Unsure'")
    assert "not sure yet" in summary and "not given" not in summary

def test_the_studio_page_counts_what_waits_now(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="One"), dict(name="Two"), dict(name="Three"))
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "3 new to review" in page
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": ids["One"]}, environ_base=E)
    c.post("/settings/studio-page/inquiries/review", data={"do": "spam", "id": ids["Two"]}, environ_base=E)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "1 new to review" in page and "3 new to review" not in page
    c.post("/settings/studio-page/inquiries/review", data={"do": "spam", "id": ids["Three"]}, environ_base=E)
    page = c.get("/settings/studio-page", environ_base=E).get_data(as_text=True)
    assert "new to review" not in page

def test_the_inbox_count_never_covers_more_on_a_phone_and_follows_spam(browser, env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="One"), dict(name="Two"))
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/")
        n = p.locator("a.ui-inbox .ui-inbox-n").bounding_box()
        more = p.locator("details.ui-nav-more > summary").bounding_box()
        assert n["x"] + n["width"] <= more["x"], (n, more)
        before = p.locator("a.ui-inbox .ui-inbox-n").inner_text()
        p.locator(f"#inq-{ids['Two']} summary").click()
        p.locator(f"#inq-{ids['Two']}").get_by_role("button", name="Spam").click()
        p.wait_for_function("t => (document.querySelector('a.ui-inbox .ui-inbox-n') || {}).textContent !== t", arg=before)
        assert int(p.locator("a.ui-inbox .ui-inbox-n").inner_text()) == int(before) - 1
    finally:
        ctx.close()

def test_the_footer_contact_line_has_no_stray_dot_on_a_phone(browser, env):
    A, c, S, tmp, web = env
    switch_on(c)
    upload(c, S)
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        p = ctx.new_page()
        p.goto((web / "index.html").as_uri())
        dots = p.eval_on_selector_all("#contact span", "els => els.map(e => getComputedStyle(e, '::before').content)")
        assert len(dots) >= 2 and all(d in ("none", "normal") for d in dots), dots
        p.set_viewport_size({"width": 1280, "height": 800})
        dots = p.eval_on_selector_all("#contact span + span", "els => els.map(e => getComputedStyle(e, '::before').content)")
        assert dots and all("·" in d for d in dots), dots
    finally:
        ctx.close()
