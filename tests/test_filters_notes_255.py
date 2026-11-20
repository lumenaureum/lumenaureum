import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import BASE, E, _tab, browser, env
from test_lists_15 import _clients_studio
from test_today_15 import _studio, _today, _x

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _visible_rows(page):

    return re.findall(r'<div class="ui-fitem" data-f [^>]*?data-f-g-show="([^"]*)"[^>]*?(\shidden)?>', page)

def test_the_update_note_is_a_dashed_box_with_its_x_in_the_corner(env):
    A, c = env
    _x(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)", ('{"version": "1.5.0", "changed": true}',))
    page = _today(c)
    note = page.split('<section class="ui-dnote" id="update-note" data-ui-part>', 1)[1].split("</section>", 1)[0]
    x_ = note.split("</form>", 1)[0]
    assert 'action="/today/update-notice-hide"' in x_ and "data-ui-inplace" in x_ and 'name="back" value="/"' in x_
    assert 'aria-label="Hide this note"' in x_ and ">Hide this note<" not in page
    assert note.index("</form>") < note.index("Updated to Lumen Aureum 1.5.0")
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    assert '<div id="update-note" data-ui-part hidden></div>' in _today(c)

def test_the_deletion_dates_list_hides_from_its_corner_and_keeps_its_other_actions(env):
    A, c = env
    A.app.config["raw_held_list"] = lambda: [dict(sid=7, name="Quinn", kind="Family", link="/c/7?s=7#raw-date",
                                                  usual="2027-01-05", usual_ok=True)]
    page = _today(c)
    note = page.split('<section class="ui-dnote" id="raw-held" data-ui-part>', 1)[1].split("</section>", 1)[0]
    x_ = note.split("</form>", 1)[0]
    assert 'action="/today/raw-held/hide"' in x_ and "data-ui-inplace" not in x_ and 'aria-label="Hide this list"' in x_
    assert "Deletion dates" in note and "Use the usual date (" in note and ">Set a date<" in note
    assert ">Hide this list<" not in page

def test_the_macro_takes_a_confirm_and_fields(env):
    A, _c = env
    with A.app.test_request_context():
        html = A.app.jinja_env.from_string(
            '{% import "_ui.html" as ui with context %}{% call ui.dismissible("/x", "/back", id="n", fields={"k": "v"}, '
            'confirm="Sure?", label="Hide this tip") %}<p>Tip.</p>{% endcall %}').render()
    assert '<section class="ui-dnote" id="n">' in html and 'data-confirm="Sure?"' in html
    assert '<input type="hidden" name="k" value="v">' in html and 'aria-label="Hide this tip" title="Hide this tip"' in html

def test_sessions_has_one_filter_row_and_the_board_is_a_plain_link(env):
    A, c = env
    _clients_studio(A)
    page = _page(c, "/sessions")
    main = page.split("<main", 1)[1]
    assert 'class="ui-views"' not in main and main.count('class="ui-filter-row"') == 1
    head = main.split("</header>", 1)[0]
    assert '<a class="ui-go " href="/pipeline">Board by stage &rsaquo;</a>' in head
    bar = main.split('id="filter-sessions"', 1)[1].split("data-f-none", 1)[0]
    row = bar.split('class="ui-filter-row"', 1)[1]
    assert re.findall(r'data-f-key="show" data-f-val="(\w+)"', row) == ["open", "upcoming", "leads", "post"]
    assert ">All <" not in row and "Board by stage" not in row

    order = [row.index(s_) for s_ in ('data-f-val="post"', 'data-f-val="mine"', 'data-f-val="week"', 'data-f-key="stage"', "data-f-clear")]
    assert order == sorted(order)

    assert re.search(r'href="/sessions\?view=open" data-f-key="show" data-f-val="open" aria-current="true">Open ', row)
    assert 'href="/sessions?view=leads" data-f-key="show" data-f-val="leads">Leads ' in row
    assert '<a class="ui-btn ui-btn-quiet ui-fclear" href="/sessions?view=all" data-f-clear>Clear filters</a>' in row

def test_sessions_has_every_row_and_hides_what_is_outside_the_view(env):
    A, c = env
    _clients_studio(A)
    rows = _visible_rows(_page(c, "/sessions"))
    assert len(rows) == 8
    shown = [show for show, hid in rows if not hid]
    assert shown and all("open" in show.split("|") for show in shown)
    assert all("open" not in show.split("|") for show, hid in rows if hid)
    page = _page(c, "/sessions")
    n_ = len(shown)
    assert f'<span data-f-count data-one="session" data-many="sessions">{n_} sessions</span>' in page
    leads = _visible_rows(_page(c, "/sessions?view=leads"))
    assert [s_ for s_, h_ in leads if not h_] == ["open|leads"]
    every = _visible_rows(_page(c, "/sessions?view=all"))
    assert len(every) == 8 and not any(h_ for _s, h_ in every)
    assert 'data-f-clear hidden>Clear filters' in _page(c, "/sessions?view=all")

def test_a_view_with_nothing_in_it_says_so_once_and_never_contradicts(env):
    A, c = env
    _x(A, "INSERT INTO households (id, name) VALUES (9, 'Nash')")
    _x(A, "INSERT INTO sessions (id, household_id, type, stage) VALUES (90, 9, 'family', 'closed')")
    page = _page(c, "/sessions?view=leads")
    assert '<p class="ui-filter-none ui-mute" data-f-none>Nothing matches these filters.</p>' in page
    assert "Nothing in this view" not in page and "0 sessions" in page
    assert '<p class="ui-filter-none ui-mute" data-f-none hidden>' in _page(c, "/sessions?view=all")

def test_search_and_view_still_work_without_script(env):
    A, c = env
    _clients_studio(A)
    page = _page(c, "/sessions?view=all&q=Ellis")
    assert all("Ellis" in r_ for r_ in re.findall(r'<a class="ui-row" href="[^"]*">(.*?)</a>', page, re.S))
    form = page.split('class="ui-filter-find"', 1)[1].split("</form>", 1)[0]
    assert '<input type="hidden" name="view" value="all">' in form and 'value="Ellis"' in form
    assert 'href="/sessions?view=leads&amp;q=Ellis"' in page
    assert '<a class="ui-btn ui-btn-quiet ui-fclear" href="/sessions?view=all" data-f-clear>' in page

def test_session_views_match_the_sql_views(env):
    A, c = env
    _clients_studio(A)
    with A.app.test_request_context():
        for k_, (_l, where) in A.VIEWS.items():
            if k_ == "all":
                continue
            sql_ = {r_["s"]["id"] for r_ in A.session_rows(where)}
            py_ = {r_["s"]["id"] for r_ in A.session_rows() if k_ in A.session_views(r_["s"])}
            assert sql_ == py_, k_

def test_sessions_chips_add_up_within_a_group_and_narrow_across(browser, env):
    A, c = env
    _clients_studio(A)
    ctx, p = _tab(browser, c, width=1280)
    try:
        p.goto(BASE + "/sessions?view=all")
        n_all = p.locator("[data-f]:visible").count()
        assert p.locator("#filter-sessions [data-f-clear]").is_hidden()
        count = lambda: p.locator("[data-f-count]").text_content()
        assert count() == f"{n_all} sessions"
        chip_n = lambda k, v: int(p.locator(f'#filter-sessions [data-f-key="{k}"][data-f-val="{v}"] [data-f-n]').inner_text())
        n_leads, n_post = chip_n("show", "leads"), chip_n("show", "post")
        p.locator('#filter-sessions [data-f-val="leads"]').click()
        assert p.url.endswith("/sessions") and p.locator("[data-f]:visible").count() == n_leads
        assert p.locator('#filter-sessions [data-f-val="leads"]').get_attribute("aria-pressed") == "true"
        p.locator('#filter-sessions [data-f-val="post"]').click()
        assert p.locator("[data-f]:visible").count() == n_leads + n_post
        assert count() == f"{n_leads + n_post} sessions"

        for b in p.locator('#filter-sessions [data-f-key="stage"]').all():
            n_ = int(b.locator("[data-f-n]").inner_text())
            b.click()
            assert p.locator("[data-f]:visible").count() == n_ or b.get_attribute("aria-pressed") == "false"
            b.click()
        p.locator('#filter-sessions [data-f-key="stage"][data-f-val="Closed"]').click()
        assert p.locator("[data-f]:visible").count() == 0
        assert p.locator("#filter-sessions [data-f-none]").is_visible() and p.locator("#session-rows").is_hidden()
        p.locator("#filter-sessions [data-f-clear]").click()
        assert p.locator("[data-f]:visible").count() == n_all and p.locator("#filter-sessions [data-f-none]").is_hidden()
        p.goto(BASE + "/sessions")
        assert p.locator("[data-f]:visible").count() == n_all
        assert p.evaluate("document.documentElement.scrollWidth") <= 1280
    finally:
        ctx.close()

def test_sessions_open_by_default_and_a_view_link_works_without_script(browser, env):
    A, c = env
    _clients_studio(A)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/sessions")
        assert p.locator('#filter-sessions [data-f-val="open"]').get_attribute("aria-pressed") == "true"
        n_open = p.locator("[data-f]:visible").count()
        assert p.locator("#filter-sessions [data-f-clear]").is_visible()
        assert p.evaluate("document.documentElement.scrollWidth") <= 390
        p.goto(BASE + "/sessions?view=leads")
        assert p.locator('#filter-sessions [data-f-val="open"]').get_attribute("aria-pressed") == "false"
        assert p.locator("[data-f]:visible").count() < n_open
    finally:
        ctx.close()
    ctx, p = _tab(browser, c, width=390, script=False)
    try:
        p.goto(BASE + "/sessions")
        assert p.locator('#filter-sessions [data-f-key="who"]').first.is_hidden()
        p.locator('#filter-sessions a[data-f-val="leads"]').click()
        p.wait_for_url("**view=leads")
        assert p.locator("[data-f]:visible").count() == 1
    finally:
        ctx.close()

def test_today_x_hides_the_update_note_in_place(browser, env):
    A, c = env
    _studio(A)
    _x(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)", ('{"version": "1.5.0", "changed": true}',))
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/")
        x_ = p.get_by_role("button", name="Hide this note")
        box, note = x_.bounding_box(), p.locator("#update-note").bounding_box()
        assert box["x"] + box["width"] > note["x"] + note["width"] - 12 and box["y"] < note["y"] + 12
        x_.click()
        p.wait_for_selector("#update-note", state="hidden")
    finally:
        ctx.close()
