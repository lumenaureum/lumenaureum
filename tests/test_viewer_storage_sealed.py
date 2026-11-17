import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from helpers import LS_READ
from test_viewer_111 import GID, Host, base, browser, built, open_page, tray

NOTE = "Please soften the shadow under her chin"

def everything(page):
    return page.evaluate("Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)]))")

def nothing_readable(store, names=("img", GID, "picks:", "look:", NOTE, "soften", '"r"')):
    assert store, "the page kept nothing"
    for k, v in store.items():
        assert k.startswith("la:") and len(k) == 35, k
        for n in names:
            assert n not in k and n not in v, (n, k, v)

def test_picks_and_notes_are_sealed_and_open_again_on_reload(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    ctx, page = open_page(browser, host, g["key"])
    page.locator(".cell").nth(1).click()
    page.locator("#pr").click()
    page.locator("#note").fill(NOTE)
    page.locator("#done").click()
    page.wait_for_timeout(600)
    nothing_readable(everything(page))
    kept = json.loads(page.evaluate(LS_READ, [f"picks:/g/{GID}/", g["key"], False]))
    assert kept["picks"] == {"n:img2.jpg": {"r": True, "n": NOTE}}
    assert page.evaluate(LS_READ, [f"picks:/g/{GID}/:welcomed", g["key"], False]) == "1"
    page.reload()
    page.wait_for_selector(".cell")
    page.wait_for_function("document.querySelector('#trayn').textContent.includes('Retouch 1/3')")
    page.wait_for_timeout(300)
    nothing_readable(everything(page))

    other = G.new_key()
    assert page.evaluate(LS_READ, [f"picks:/g/{GID}/", other, False]) is None
    ctx.close()

def test_a_readable_entry_from_an_earlier_page_is_sealed_and_removed(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    mine = {"v": 2, "picks": {"n:img3.jpg": {"r": True, "n": NOTE}}, "at": "2026-11-10T10:00:00.000Z"}
    ctx, page = open_page(browser, host, g["key"], store={"": json.dumps(mine)})
    page.wait_for_function("document.querySelector('#trayn').textContent.includes('Retouch 1/3')")
    page.wait_for_timeout(600)
    nothing_readable(everything(page))
    assert json.loads(page.evaluate(LS_READ, [f"picks:/g/{GID}/", g["key"], False]))["picks"] == mine["picks"]
    ctx.close()

def test_the_colors_are_sealed_too(built, browser):
    G, g, _ = built
    host = Host(g["folder"], GID)
    ctx, page = open_page(browser, host, g["key"], init=f"localStorage.setItem('look:/g/{GID}/', "
                                                         "JSON.stringify({colors: {bg: '#101010', ink: '#fafafa'}}));")
    page.wait_for_timeout(800)
    store = everything(page)
    assert f"look:/g/{GID}/" not in store
    nothing_readable(store)
    assert json.loads(page.evaluate(LS_READ, [f"look:/g/{GID}/", g["key"], False]))["colors"]
    ctx.close()
