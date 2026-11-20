import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_settings_fixes_15 import E, env

def _page(c, url):
    return c.get(url, environ_base=E).get_data(as_text=True)

def _main(html):
    return html.split('class="setmain', 1)[1] if 'class="setmain' in html else html

def test_print_finds_print_orders_before_what_prints_sample(env):
    A, c = env

    out = _page(c, "/search?q=print").split('id="setting-hits"', 1)[1].split("</section>", 1)[0]
    first = out.index("Print orders: turn on")
    assert first < out.index("Print prices") < out.index("Which documents still print SAMPLE")
    assert "/settings/plugin/prints" in out
    assert "Plugins: turn parts on or off" in _page(c, "/search?q=plugins")

def test_plugins_in_plain_words_with_the_technical_note_behind_a_question_mark(env):
    A, c = env
    page = _main(_page(c, "/settings/plugins"))
    for raw, plain in (("gallery", "Online galleries"), ("print_core", "Print order safekeeping"),
                       ("retention", "Keeping and deleting shoot files"), ("letters", "Delivery letters"),
                       ("studio_page", "Studio page")):
        assert f"<b>{raw}</b>" not in page and f"<b>{plain}</b>" in page, raw
        row = page.split(f'id="plg-{raw}"', 1)[1].split('class="card f"', 1)[0]
        assert 'class="sub"' in row, raw
        shown = row.replace(row[row.index('<span class="qh"'):row.index("</span></span>") + 14], "")
        assert ".py." not in shown and ".py." in row, raw
        assert "CRM_PLUGINS_OFF" not in shown, raw
        if raw not in A.CORE_PLUGINS:
            assert "CRM_PLUGINS_OFF" in row, raw
    intro = page.split("<h1>Plugins</h1>", 1)[1].split('<div class="card', 1)[0]
    assert "CRM_PLUGINS_OFF" not in intro.split('<span class="qh"', 1)[0]

def test_the_switch_says_it_is_working_and_where_print_orders_show_up(env):
    A, c = env
    page = _main(_page(c, "/settings/plugins"))
    row = page.split('id="plg-prints"', 1)[1].split('class="card f"', 1)[0]
    assert 'data-busy-says="Turning on..."' in row and "New print orders show on Today" not in row
    assert "data-busy-says" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                 "static", "ui.js")).read()
    r = c.post("/settings/plugins", data={"name": "prints", "turn": "on"}, environ_base=E)
    assert r.status_code == 302
    row = _main(_page(c, "/settings/plugins")).split('id="plg-prints"', 1)[1].split('class="card f"', 1)[0]
    assert "New print orders show on Today and on the session they came from." in row
    assert "No gallery offers prints yet" in row and 'href="/settings/prints"' in row
    assert "Set your print prices" in _page(c, "/")
    assert "Print orders" in _page(c, "/settings?tab=profile").split('class="settabs', 1)[1].split("</nav>", 1)[0]

def test_prices_label_their_columns_and_point_to_print_prices(env):
    A, c = env
    page = _main(_page(c, "/settings?tab=prices"))
    table = page.split('<table class="data fees">', 1)[1].split("</table>", 1)[0]
    assert "<th>Fee</th>" in table and "<th>Sales tax</th>" in table and "<th>Remove</th>" in table
    assert ">taxed<" not in table and ">drop<" not in table and ">Remove<" in table
    assert "have their own price list" in page and 'href="/settings/plugin/prints"' in page
    assert "Print orders is off" in _page(c, "/settings/plugin/prints")
    c.post("/settings/plugins", data={"name": "prints", "turn": "on"}, environ_base=E)
    assert c.get("/settings/plugin/prints", environ_base=E).headers["Location"].endswith("/settings/prints")

def test_online_galleries_says_what_it_is_for_first(env):
    A, c = env
    page = _main(_page(c, "/setup/galleries"))
    lead = page.index("Clients see, pick and download their photos in online galleries.")
    assert lead < page.index('id="f-gallery_type"')

def test_settings_have_no_search_box_of_their_own(env):

    A, c = env
    for url in ("/about", "/settings/plugins", "/settings"):
        assert 'id="ui-find-q"' not in _page(c, url)
