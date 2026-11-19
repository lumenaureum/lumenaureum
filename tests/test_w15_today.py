import json
import re

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

def _today(c):
    return c.get("/", environ_base=E).get_data(as_text=True)

def _badge(page):
    m = re.search(r'<span class="ui-inbox-n">(\d+)</span>', page.split("<main", 1)[0])
    return int(m.group(1)) if m else 0

def _item(page, rid):
    i = page.index(f'id="inq-{rid}"')
    return page[i:page.index("</li>", i)]

def test_the_inbox_is_one_line_per_message_opened_in_place_with_its_actions_in_order(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="Pat Visitor", message="We would love fall photos by the lake, four of us and a dog."))
    page = _today(c)
    assert ">Inbox</h2>" in page and 'id="inbox"' in page and "Inquiries and picks" not in page
    item = _item(page, ids["Pat Visitor"])
    assert '<li class="ui-mail ui-mail-new"' in page and '<details class="ui-fold ui-mail-fold">' in item
    line = item.split("<summary>", 1)[1].split("</summary>", 1)[0]
    assert "Pat Visitor" in line and ">New</span>" in line and "Website &middot; " in line and "We would love fall" in line
    assert "ui-tline" not in item and "ui-avatar" not in item and "data-ui-href" not in item
    body = item.split("</summary>", 1)[1]
    order = [body.index(w) for w in (">Reply</button>", "Add as a client</button>", ">Add to an existing client...</a>", ">Spam</button>")]
    assert order == sorted(order), order
    assert 'href="mailto:' in body and "Kind of session:" in body
    assert 'list="inbox-clients"' in body and '<datalist id="inbox-clients">' in page
    assert _badge(page) == 1

def test_reply_makes_it_read_but_keeps_it_in_the_inbox_until_filed(env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="Ana Reply"), dict(name="Ben Other"))
    assert _badge(_today(c)) == 2
    rid = ids["Ana Reply"]
    r = c.post("/settings/studio-page/inquiries/review", data={"do": "reply", "id": rid, "mail": "1", "back": "/"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:")
    page = _today(c)
    assert _badge(page) == 1 and 'id="inq-%d"' % rid in page
    assert ">New</span>" not in _item(page, rid).split("</summary>", 1)[0]
    assert ">New</span>" in _item(page, ids["Ben Other"]).split("</summary>", 1)[0]
    assert q(A, "SELECT replied FROM studio_inquiry_review WHERE id=?", (rid,))[0][0]
    c.post("/settings/studio-page/inquiries/review", data={"do": "reply", "id": rid, "back": "/"}, environ_base=E)
    assert _badge(_today(c)) == 1

def test_add_to_an_existing_client_from_the_quick_search(env):
    A, c, S, tmp, web = env
    con = A.connect()
    try:
        con.execute("INSERT INTO households (id, name) VALUES (41, 'Ellis Family')")
        con.execute("INSERT INTO people (household_id, name, role, email) VALUES (41, 'Jo Ellis', 'parent', 'old@example.org')")
        con.commit()
    finally:
        con.close()
    ids = _review(A, c, S, web, dict(name="Jo Ellis", email="new.address@example.org"), dict(name="Nobody Here"))
    page = _today(c)
    assert '<option value="Ellis Family #41">' in page
    assert "Add to Ellis Family</button>" not in _item(page, ids["Jo Ellis"])
    r = c.post("/settings/studio-page/inquiries/review", data={"do": "add_existing", "id": ids["Jo Ellis"], "client": "Ellis Family #41",
                                                             "type": "family", "back": "/"}, environ_base=E)
    assert r.status_code == 302
    assert not q(A, "SELECT 1 FROM studio_inquiry_review WHERE id=?", (ids["Jo Ellis"],))
    assert q(A, "SELECT COUNT(*) FROM sessions WHERE household_id=41 AND stage='inquiry'")[0][0] == 1
    assert q(A, "SELECT COUNT(*) FROM households WHERE name='Jo Ellis'")[0][0] == 0

    c.post("/settings/studio-page/inquiries/review", data={"do": "add_existing", "id": ids["Nobody Here"], "client": "No Such Client",
                                                         "back": "/"}, environ_base=E)
    assert q(A, "SELECT 1 FROM studio_inquiry_review WHERE id=?", (ids["Nobody Here"],))
    c.post("/settings/studio-page/inquiries/review", data={"do": "add_existing", "id": ids["Nobody Here"], "client": "ellis family",
                                                         "back": "/"}, environ_base=E)
    assert q(A, "SELECT COUNT(*) FROM sessions WHERE household_id=41 AND stage='inquiry'")[0][0] == 2

def test_the_replied_column_is_added_once_and_changes_nothing_else(env):
    A, c, S, tmp, web = env
    _review(A, c, S, web, dict(name="Kept As Is"))
    before = q(A, "SELECT id, sha256, fields, received FROM studio_inquiry_review")
    for fn in A.UNLOCK_HOOKS + A.AFTER_UNLOCK_HOOKS:
        if getattr(fn, "__name__", "") == "_review_cols":
            fn()
            fn()
    cols = [r[1] for r in q(A, "PRAGMA table_info(studio_inquiry_review)")]
    assert cols.count("replied") == 1
    assert q(A, "SELECT id, sha256, fields, received FROM studio_inquiry_review") == before

def test_inbox_opens_in_place_and_fits_a_phone(browser, env):
    A, c, S, tmp, web = env
    ids = _review(A, c, S, web, dict(name="Lee Phone", message="A long message " * 20))
    ctx, p = _tab(browser, c, 390)
    try:
        p.goto(BASE + "/")
        url = p.url
        item = p.locator(f"#inq-{ids['Lee Phone']}")
        assert not item.get_by_role("button", name="Spam").is_visible()
        item.locator("summary").click()
        assert p.url == url and item.get_by_role("button", name="Spam").is_visible()
        item.get_by_text("Add to an existing client...").click()
        assert item.locator("input[name=client]").is_visible()
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()

def test_typing_in_the_search_box_never_moves_the_page(browser, env):

    A, c, S, tmp, web = env
    for width in (390, 1280):
        ctx, p = _tab(browser, c, width)
        try:
            p.goto(BASE + "/search?q=a")
            if width < 900:
                p.locator("nav.top .ui-nav-more > summary").click()
                box = p.locator("nav.top .ui-more-pop input[name=q]")
            else:
                box = p.locator("nav.top form.search input[name=q]")
            if width < 900:
                assert float(box.evaluate("e => getComputedStyle(e).fontSize").rstrip("px")) >= 16, width
            y = p.evaluate("window.scrollY")
            box.click()
            box.press_sequentially("morgan")
            assert p.evaluate("window.scrollY") == y, width
        finally:
            ctx.close()
