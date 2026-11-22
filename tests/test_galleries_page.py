import os
import re
import shutil
import socket
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _q

def _env(tmp_path, monkeypatch):
    from test_fixes_141 import _served_env
    return _served_env(tmp_path, monkeypatch)

def _sql(e, sql, args=()):
    con = e.A.connect()
    con.execute(sql, args)
    con.commit()
    con.close()

def _page(e, PE, path="/galleries"):
    r = e.c.get(path, environ_base=PE.E)
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)

def _cards(html):
    return re.findall(r'<li class="ui-cover".*?</li>', html, re.S)

def _nav(html):
    return re.search(r'<nav class="sections".*?</nav>', html, re.S).group(0)

def test_galleries_is_in_the_top_bar_only_once_galleries_are_in_use(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    nav = _nav(_page(e, PE, "/"))
    assert ">Galleries</a>" in nav
    assert nav.index(">Sessions</a>") < nav.index(">Galleries</a>")
    assert 'aria-current="page">Galleries</a>' in _nav(_page(e, PE))
    more = re.search(r'<details class="ui-more ui-nav-more">.*?</details>', _page(e, PE, "/"), re.S).group(0)
    assert ">Galleries</a>" in more
    _sql(e, "DELETE FROM galleries")
    _sql(e, "DELETE FROM settings WHERE key='gallery_base_url'")
    assert ">Galleries</a>" not in _nav(_page(e, PE, "/"))

def test_a_card_says_where_the_gallery_stands_and_opens_the_client_page_at_its_session(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    _sql(e, "UPDATE galleries SET n_files=12")
    html = _page(e, PE)
    (card,) = _cards(html)
    assert 'href="/c/1?s=1#gallery"' in card
    assert "The Sample Family" in card and ">Online<" in card
    assert "Download · 12 photos" in card and "online since" in card
    assert "Not opened yet" in card and "No codeword" in card
    assert "KB here" in card and "KB on the web host" in card
    assert 'ui-cover-menu' in card and ">Show it on the client page</a>" in card and ">Close now</button>" in card
    assert f'data-gallery="{PE.GID}"' in card
    head = re.search(r'<p class="ui-status">(.*?)</p>', html).group(1)
    assert head.startswith("1 gallery · ") and " here · " in head and head.endswith(" on the web host")

def test_the_filter_row_has_phase_and_kind_and_the_three_sorts(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    html = _page(e, PE)
    bar = re.search(r'<div class="ui-filter" id="filter-galleries".*?</div>\s*<p', html, re.S).group(0)
    assert 'aria-label="Phase"' in bar and 'data-f-val="Online"' in bar
    assert 'aria-label="Kind"' in bar and 'data-f-val="Download"' in bar
    for label in ("Closing soonest", "Newest", "Largest on the web host", "Largest here"):
        assert f">{label}</option>" in bar
    assert 'aria-label="Whose turn"' not in bar
    (card,) = _cards(html)
    assert 'data-f-g-phase="Online"' in card and re.search(r'data-s-host="\d+"', card)

def test_a_page_load_asks_no_web_host_anything(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    import hostcheck

    def no_fetch(*a, **k):
        raise AssertionError("the Galleries page fetched from the web host")

    def no_net(*a, **k):
        raise AssertionError("the Galleries page opened a connection")
    monkeypatch.setattr(hostcheck, "fetch", no_fetch)
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    before = {p: p.stat().st_mtime_ns for p in e.host.rglob("*")}
    html = _page(e, PE)
    assert len(_cards(html)) == 1
    assert e.c.get("/galleries/1/cover", environ_base=PE.E).status_code == 200
    assert {p: p.stat().st_mtime_ns for p in e.host.rglob("*")} == before

def test_the_cover_comes_from_the_copy_kept_here_not_the_web_host(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    shutil.rmtree(e.host / PE.GID)
    r = e.c.get("/galleries/1/cover", environ_base=PE.E)
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"
    assert "no-store" in r.headers.get("Cache-Control", "")
    assert '<img src="/galleries/1/cover"' in _cards(_page(e, PE))[0]
    assert e.c.get("/galleries/999/cover", environ_base=PE.E).status_code == 404

def test_a_gallery_closing_soon_says_so(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    e.c.post("/session/1/gallery/closing-day", data={"day": (date.today() + timedelta(days=2)).isoformat()},
             environ_base=PE.E)
    (card,) = _cards(_page(e, PE))
    assert ">Closing<" in card and "in 2 days" in card
    assert f'data-s-closes="{(date.today() + timedelta(days=2)).isoformat()}"' in card

def test_a_closed_gallery_still_on_the_web_host_offers_its_clean_up_and_comes_back_here(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    _sql(e, "UPDATE galleries SET close_on=? WHERE session_id=1", ((date.today() - timedelta(days=2)).isoformat(),))
    (card,) = _cards(_page(e, PE))
    assert ">Closed<" in card and "Closed, still on your web host" in card
    assert 'action="/session/1/gallery/close-now"' in card and 'name="back" value="/galleries"' in card
    assert '<details class="ui-rowmore ui-cover-menu">' in card
    assert not re.search(r'class="ui-btn[^"]*"', card)
    assert 'data-f-g-look="Needs you"' in card
    r = e.c.post("/session/1/gallery/close-now", data={"back": "/galleries"}, environ_base=PE.E)
    assert r.status_code == 302 and r.headers["Location"].endswith("/galleries")
    assert _q(e.A, "SELECT upload_state FROM galleries WHERE session_id=1")[0][0] == "removed"
    (card,) = _cards(_page(e, PE))
    assert "off the web" in card and "close-now" not in card
    assert re.search(r'<button type="button" class="ui-menu-item" disabled>Close now<small[^>]*>Already off the web', card) and "on the web host" not in card

def test_a_back_address_off_this_app_is_not_followed(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    r = e.c.post("/session/1/gallery/close-now", data={"back": "https://evil.test/x"}, environ_base=PE.E)
    assert "evil.test" not in r.headers["Location"]

def test_a_codeword_gallery_says_codeword(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    _sql(e, "UPDATE galleries SET ekey='a-different-key' WHERE session_id=1")
    (card,) = _cards(_page(e, PE))
    assert "Codeword" in card and "No codeword" not in card

def test_no_galleries_says_so(tmp_path, monkeypatch):
    e, PE = _env(tmp_path, monkeypatch)
    _sql(e, "DELETE FROM galleries")
    html = _page(e, PE)
    assert not _cards(html) and "No galleries yet." in html and "0 galleries" in html
