import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_adopt_gallery_b3 import E, env, row
from test_client_page_141 import BASE, _tab, browser

GID = "g-PicksByEmail0000000000a"

def no_php_gallery(A, G):

    key = G.new_key()
    con = A.connect()
    con.execute("UPDATE sessions SET gallery_id=? WHERE id=1", (GID,))
    con.execute("""INSERT INTO galleries (session_id, key, ekey, built_at, n_files, upload_state, uploaded_at, cw_fixed,
                   sealed_gen) VALUES (1, ?, ?, ?, 3, 'up', ?, 'none', 1)""",
                (key, key, date.today().isoformat(), date.today().isoformat()))
    con.commit(); con.close()
    d = G.OUT_ROOT / GID
    d.mkdir(parents=True)
    (d / "index.html").write_text(G.viewer_page(GID, gate=False, credit=False))
    G.mark_online(GID)
    return key

def email(G, key):
    lines = ["Retouch IMG_0187.jpg: brighten the faces", "Retouch IMG_0193.jpg", "Suggested for your portfolio: IMG_0189.jpg"]
    return "Hello!\n\n" + "\n".join(lines) + f"\n\nFingerprint: {G.picks_fingerprint(lines, key)}\nThanks"

def test_the_row_offers_the_paste_and_a_paste_records_the_picks(env):
    A, c, G, host, *_ = env
    key = no_php_gallery(A, G)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Their picks come back by email: none pasted yet" in page and ">Paste their picks email</a>" in page
    assert 'action="/session/1/picks/paste" data-ui-inplace' in page
    r = c.post("/session/1/picks/paste", data={"text": email(G, key), "back": "/c/1?s=1#gallery"}, environ_base=E)
    assert r.headers["Location"].endswith("/c/1?s=1#gallery")
    s = row(A, "SELECT retouch_picks, favorites FROM sessions WHERE id=1")
    assert s[0].splitlines() == ["IMG_0187.jpg: brighten the faces", "IMG_0193.jpg"] and s[1] == "IMG_0189.jpg"
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert "Picks recorded. These match what the client picked in the gallery." in page
    assert "Paste their picks email" not in page

def test_no_paste_row_where_picks_come_back_by_themselves(env):
    A, c, G, host, *_ = env
    no_php_gallery(A, G)
    (G.OUT_ROOT / GID / "index.html").write_text(G.viewer_page(GID, gate=True, credit=False))
    assert "Paste their picks email" not in c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)

def test_a_paste_with_no_picks_is_refused_in_place(env):
    A, c, G, host, *_ = env
    no_php_gallery(A, G)
    c.post("/session/1/picks/paste", data={"text": "Thanks for the photos!", "back": "/c/1?s=1#gallery"}, environ_base=E)
    with c.session_transaction() as fs:
        assert fs.get("notice_refused") and "No picks found" in fs.get("notice")
    assert not row(A, "SELECT retouch_picks FROM sessions WHERE id=1")[0]

def test_paste_in_place_in_the_browser(browser, env):
    A, c, G, host, *_ = env
    key = no_php_gallery(A, G)
    ctx, p = _tab(browser, c, width=390)
    try:
        p.goto(BASE + "/c/1?s=1")
        p.evaluate("window.__stay = 1")
        p.get_by_role("link", name="Paste their picks email").click()
        p.locator("textarea[aria-label='Their picks email']").fill(email(G, key))
        p.get_by_role("button", name="Check and record their picks").click()
        p.wait_for_function("document.querySelector('.ui-note')")

        assert "Picks recorded" in p.locator(".ui-note").first.text_content()
        assert p.evaluate("window.__stay") == 1
        assert p.get_by_role("link", name="Paste their picks email").count() == 0
    finally:
        ctx.close()
    assert row(A, "SELECT favorites FROM sessions WHERE id=1")[0] == "IMG_0189.jpg"
