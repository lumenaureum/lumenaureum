import html
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_studio_inquiries import GOOD, put_inquiry
from test_studio_page import E, env, setting, switch_on, wait

def _page(c, url="/settings/studio-page"):
    r = c.get(url, environ_base=E, follow_redirects=True)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _card(page):
    return page.split('id="widget"', 1)[1].split("</section>", 1)[0]

def _on(c, sites="mystudio.com"):
    c.post("/settings/studio-page/widget", data={"what": "switch", "site_widget": "1"}, environ_base=E)
    if sites is not None:
        c.post("/settings/studio-page/widget", data={"what": "sites", "site_widget_sites": sites}, environ_base=E)

def _upload(c, S):
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)

def _snippet(page):
    return html.unescape(page.split('id="sp-snippet"', 1)[1].split(">", 1)[1].split("</textarea>", 1)[0])

def test_website_addresses_become_https_origins():
    from plugins import studio_page as S
    assert S.widget_origin("MyStudio.com") == "https://mystudio.com"
    assert S.widget_origin(" https://www.mystudio.com/contact?x=1 ") == "https://www.mystudio.com"
    assert S.widget_origin("https://mystudio.com:8443/") == "https://mystudio.com:8443"
    assert S.widget_origin("https://mystudio.com:443") == "https://mystudio.com"
    for bad, why in (("http://mystudio.com", "only https://"), ("localhost", "not a website address"),
                     ("192.168.1.10", "not a website address"), ("https://me:pw@mystudio.com", "not a website address"),
                     ("mystudio.com;script-src *", "not a website address"), ("my studio.com", "not a website address")):
        with pytest.raises(ValueError, match=why):
            S.widget_origin(bad)
    with pytest.raises(ValueError) as e:
        S.widget_origin("nope")
    assert str(e.value).startswith("nope:")
    sites, bad = S.widget_sites("a.com, b.com\nA.com https://c.com/x d.com e.com f.com http://g.com")
    assert sites == ["https://a.com", "https://b.com", "https://c.com", "https://d.com", "https://e.com"]
    assert any("only https://" in b for b in bad) and any("At most 5" in b for b in bad)

def test_the_frame_is_allowed_only_on_the_listed_sites_and_their_www_twins():
    from plugins import studio_page as S
    assert S.widget_ancestors(["https://mystudio.com", "https://www.other.com:8443"]) == [
        "https://mystudio.com", "https://www.mystudio.com", "https://www.other.com:8443", "https://other.com:8443"]
    with pytest.raises(ValueError):
        S.widget_ancestors(["https://a.com 'unsafe-inline'"])
    php = S.widget_php("<!doctype html><p>form</p>", ["https://mystudio.com"])
    head = php.split("?>", 1)[0]
    assert ("frame-ancestors https://mystudio.com https://www.mystudio.com\");" in head
            and "default-src 'none'; script-src 'self'" in head and "X-Content-Type-Options: nosniff" in head)
    assert "frame-ancestors 'none'" in S.widget_php("<p>x</p>", [])
    with pytest.raises(ValueError):
        S.widget_php("<p><?php echo 1; ?></p>", ["https://mystudio.com"])
    assert S.WIDGET_PAGE in S.SITE_TOP_NAMES

def test_the_snippet_holds_only_the_address_escaped():
    from plugins import studio_page as S
    f = S.widget_snippet("https://www.example.com/inquiry-form.php", 'Pat "Lens" <Studio>')
    assert f.startswith('<iframe src="https://www.example.com/inquiry-form.php" title="Send an inquiry to Pat &quot;Lens&quot; &lt;Studio&gt;"')
    assert "<script" not in f and "data-key" not in f
    b = S.widget_snippet("https://www.example.com/inquiry-form.php", "Pat", button=True)
    assert b.startswith('<a href="https://www.example.com/inquiry-form.php#book" target="_blank" rel="noopener noreferrer"')
    assert b.endswith(">Book a session</a>") and "<script" not in b

def test_off_to_start_with_and_findable(env):
    A, c, S, tmp, web = env
    switch_on(c)
    card = _card(_page(c))
    assert "Use the inquiry form on another website" in card
    assert 'id="sp-widget" name="site_widget" value="1" data-no-keep>' in card
    assert "sp-snippet" not in card and "sp-widget-sites" not in card
    import settings_registry
    assert any(f.get("anchor") == "widget" for g in settings_registry.GROUPS for p in g["pages"] for f in p.get("fields", []))
    _upload(c, S)
    assert setting(A, "site_state") == "up" and not (web / S.WIDGET_PAGE).exists()

def test_switch_saves_on_its_own_and_asks_for_the_address(env):
    A, c, S, tmp, web = env
    switch_on(c)
    _on(c, sites=None)
    assert setting(A, "site_widget") == "1"
    card = _card(_page(c))
    form = card.split('id="sp-widget-on"', 1)[1].split("</form>", 1)[0]
    assert form.startswith(" data-ui-autosave data-ui-autosave-reload>") and "<noscript>" in form
    assert "Add the address of your other website" in card and "sp-snippet" not in card
    r = c.post("/settings/studio-page/widget", data={"what": "sites", "site_widget_sites": "mystudio.com\nhttp://bad.com, nope"},
               environ_base=E)
    assert r.status_code == 302 and "saved=sp-widget-sites" in r.headers["Location"]
    assert setting(A, "site_widget_sites") == "https://mystudio.com"
    card = _card(_page(c, r.headers["Location"]))
    assert "Not added: http://bad.com: only https:// websites can show the form. nope: not a website address." in card
    assert 'data-copy="#sp-snippet">Copy the code</button>' in card
    assert _snippet(card) == S.widget_snippet("https://www.example.com/inquiry-form.php", "Pat Lens")
    assert "Not on your web host yet" in card
    assert "#sp-widget-sites" in _page(c).split('id="publish"', 1)[1].split("Preview the site", 1)[0]

def test_it_needs_the_form(env):
    A, c, S, tmp, web = env
    switch_on(c)
    _on(c)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('site_inquiries', '0')")
    con.commit(); con.close()
    card = _card(_page(c))
    assert "which is off: tick An inquiry form under Words" in card and "sp-snippet" not in card
    _upload(c, S)
    assert setting(A, "site_state") == "up" and not (web / S.WIDGET_PAGE).exists()

def test_upload_puts_the_contact_forms_twin_on_the_host_and_inquiries_arrive(env):
    A, c, S, tmp, web = env
    switch_on(c)
    _on(c)
    _upload(c, S)
    assert setting(A, "site_state") == "up", setting(A, "site_error")
    php = (web / S.WIDGET_PAGE).read_text()
    head, page = php.split("?>\n", 1)
    assert head.startswith("<?php\n") and "frame-ancestors https://mystudio.com https://www.mystudio.com" in head
    contact = (web / "contact" / "index.html").read_text()
    form = re.search(r'<form id="inq".*?</form>', page, re.S).group(0)
    twin = re.search(r'<form id="inq".*?</form>', contact, re.S).group(0)
    assert form == twin.replace('data-post="../inquiry.php"', 'data-post="inquiry.php"')
    assert '<script src="assets/inquiry.js?v=' in page and '<link rel="stylesheet" href="assets/site.css">' in page
    assert 'class="frame"' in page and '<meta name="robots" content="noindex">' in page
    assert not re.search(r'(src|href)="(https?:)?//', page)
    assert ".frame .alone:target" in (web / "assets" / "site.css").read_text()
    con = A.connect()
    assert con.execute("SELECT 1 FROM studio_files WHERE name=?", (S.WIDGET_PAGE,)).fetchone()
    con.close()
    assert "On your web host now" in _card(_page(c))
    assert S.WIDGET_PAGE not in (web / "sitemap.xml").read_text()

    key = re.search(r'data-key="([^"]+)"', form).group(1)
    assert key == S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(key, dict(GOOD, message="Seen on my other website")))
    assert A.app.config["studio_page_collect"]()["added"] == 1

    c.post("/settings/studio-page/widget", data={"what": "switch"}, environ_base=E)
    assert setting(A, "site_widget") == "0"
    _upload(c, S)
    assert setting(A, "site_state") == "up" and not (web / S.WIDGET_PAGE).exists()
    assert (web / "contact" / "index.html").is_file()

def test_the_button_opens_the_form_in_its_own_tab_and_no_site_frames_it(env):
    A, c, S, tmp, web = env
    switch_on(c)
    _on(c, sites=None)
    c.post("/settings/studio-page/widget", data={"what": "button", "site_widget_button": "1"}, environ_base=E)
    assert setting(A, "site_widget_button") == "1"
    card = _card(_page(c))
    assert 'id="sp-widget-button" name="site_widget_button" value="1" data-no-keep checked>' in card
    assert "sp-widget-sites" not in card and "Add the address" not in card
    assert _snippet(card) == S.widget_snippet("https://www.example.com/inquiry-form.php", "Pat Lens", button=True)
    _upload(c, S)
    php = (web / S.WIDGET_PAGE).read_text()
    assert "frame-ancestors 'none'" in php and '<h1 id="book" class="alone">Send an inquiry to Pat Lens</h1>' in php
    c.post("/settings/studio-page/widget", data={"what": "button"}, environ_base=E)
    assert setting(A, "site_widget_button") == "0"
    assert "Add the address of your other website" in _card(_page(c))

def test_the_preview_never_builds_it(env):
    A, c, S, tmp, web = env
    switch_on(c)
    _on(c)
    c.post("/settings/studio-page/preview", environ_base=E)
    assert (tmp / "site" / "preview" / "index.html").is_file()
    assert not (tmp / "site" / "preview" / S.WIDGET_PAGE).exists()
