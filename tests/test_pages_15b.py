import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

CSS = open(os.path.join(ROOT, "static", "style.css")).read()

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _bar(page):
    return page.split('<div class="bar">', 1)[1].split('<div class="page">', 1)[0]

def _studio(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava", shoot_date="2027-05-01")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Family session", "amount": "300"}, environ_base=E)
    return A, c, sid

def test_every_document_bar_has_one_primary_more_and_close_last(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    c.post(f"/session/{sid}/quote/send", data={"total": "300"}, environ_base=E)
    vid = _q(A, "SELECT MAX(id) FROM quote_versions")[0][0]
    urls = [f"/session/{sid}/invoice", f"/session/{sid}/agreement", f"/session/{sid}/details", f"/quote/{vid}",
            f"/session/{sid}/quote/preview", "/fees/sheet", "/client/1/statement?m=2026-11"]
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    urls.append(f"/session/{sid}/invoice")
    for url in urls:
        bar = _bar(_page(c, url))
        assert bar.count("ui-btn-primary") == 1, (url, bar[:400])
        assert re.findall(r"<a [^>]*>([^<]+)</a>", bar)[-1] == "Close", url
        assert not re.search(r'class="(linkish|primary|quiet)"', bar), url
        assert "@media screen" in bar.split('<div class="bar">')[0] or "@media screen" in _page(c, url), url

def test_a_draft_invoice_emails_through_issue_and_keeps_issue_under_more(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    bar = _bar(_page(c, f"/session/{sid}/invoice"))
    primary = bar.split('class="ui-more"', 1)[0]

    assert re.search(r'name="then" value="email"><button class="ui-btn ui-btn-primary"[^>]*>Issue and email it<', primary)
    assert "Issue this invoice now?" in primary
    more = bar.split('class="ui-more-pop"', 1)[1]
    assert "Issue this invoice" in more and 'value="download"' in more and 'value="print"' in more

def test_printed_documents_carry_no_bar_and_no_screen_styles(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(A, "html_pdf", lambda html, name: (seen.append(html), "pdf")[1])
    c.post(f"/session/{sid}/invoice/issue", environ_base=E)
    for url in (f"/session/{sid}/invoice.pdf", f"/session/{sid}/agreement.pdf", f"/session/{sid}/details.pdf",
                "/fees/sheet.pdf", "/client/1/statement.pdf?m=2026-11"):
        seen.clear()
        c.get(url, environ_base=E)
        assert seen, url
        html = seen[0]
        assert 'class="bar"' not in html and "ui-btn" not in html and "ui-docbar" not in html, url
        assert "@media screen" not in html, url

def test_mark_signed_follows_email_it_on_the_agreement(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    bar = _bar(_page(c, f"/session/{sid}/agreement"))
    head = bar.split('class="ui-more"', 1)[0]
    assert "Mark signed" in head
    first = re.search(r"ui-btn-primary|Mark signed", head).group(0)
    assert first == "ui-btn-primary"

def test_finances_puts_money_owed_first_and_draws_months_as_bar_rows(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("UPDATE sessions SET stage='shot', shoot_date='2026-03-01' WHERE id=?", (sid,))
    con.execute("INSERT INTO payments (session_id, kind, amount, received) VALUES (?, 'deposit', 100, '2026-03-02')", (sid,))
    con.commit()
    con.close()
    page = _page(c, "/money?year=2026")
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert not re.search(r"<(button|form)\b", head) and "ui-btn" not in head
    after = page.split("</header>", 1)[1].lstrip()
    if 'id="owed"' in page:
        assert after.startswith('<section class="ui-needs" id="owed"')
    assert 'class="ui-bars" aria-label="Received by month"' in page
    assert 'class="ui-money"' in page and "Net, after sales tax" in page
    assert ".ui-bar-amt { text-align: right; white-space: nowrap;" in CSS

def test_the_expense_form_and_remove_work_without_script(tmp_path, monkeypatch):
    A, c, sid = _studio(tmp_path, monkeypatch)
    r = c.post("/money/overhead", data={"label": "Lens cloth", "amount": "9", "date": "2026-11-02"}, environ_base=E)
    assert r.status_code == 302 and r.location.endswith("#overhead")
    page = _page(c, "/money?year=2026")
    assert 'aria-label="Remove expense Lens cloth"' in page and "Add the expense" in page
    rid = _q(A, "SELECT id FROM expenses WHERE label='Lens cloth'")[0][0]
    c.post(f"/money/expense/{rid}/delete", environ_base=E)

def test_settings_on_a_phone_is_a_grouped_list_then_a_page_with_back(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    home = _page(c, "/settings")
    nav = home.split('<nav class="settabs', 1)[1].split("</nav>", 1)[0]
    assert nav.startswith(' set-home"') and '<h1 class="set-title">Settings</h1>' in nav
    assert len(re.findall(r'<a href="[^"]+" class="ui-row', nav)) >= 15
    assert 'href="/settings?tab=profile"' in nav
    for url in ("/settings?tab=profile", "/settings?tab=prices", "/settings/type/family", "/settings/wording/agreement"):
        nav = _page(c, url).split('<nav class="settabs', 1)[1].split("</nav>", 1)[0]
        assert nav.startswith('"') and 'class="ui-go set-back" href="/settings">&lsaquo; Settings</a>' in nav, url
        assert nav.count('aria-current="page"') == 1, url
    for rule in (".settabs:not(.set-home) > .sg { display: none; }", ".settabs.set-home + .setmain { display: none; }"):
        assert rule in CSS

def test_settings_review_status_names_what_each_row_opens(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    page = _page(c, "/settings?tab=documents")
    assert ">Open &rsaquo;<" not in page and 'class="ui-row"' in page and "SAMPLE" in page

def test_a_studio_page_photo_description_gets_the_full_width(tmp_path, monkeypatch):
    import io
    from PIL import Image
    monkeypatch.setenv("CRM_SITE_ROOT", str(tmp_path / "site"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    A, c = _app(tmp_path, monkeypatch)
    c.post("/settings/studio-page/switch", data={"use": "1"}, environ_base=E)
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (120, 90, 60)).save(b, "JPEG")
    b.seek(0)
    c.post("/settings/studio-page/photos/add", data={"photos": (b, "one.jpg")}, environ_base=E, content_type="multipart/form-data")
    page = _page(c, "/settings/studio-page")
    assert 'class="ui-photo"' in page and "sp-photos" not in page
    item = page.split('<li class="ui-photo">', 1)[1].split("</li>", 1)[0]
    assert re.search(r'<input name="alt"[^>]*class="ui-grow"', item) and "Save the description" in item
    assert ".ui-photo { display: grid; grid-template-columns: 100px minmax(0, 1fr);" in CSS

def test_events_are_list_rows_and_the_event_header_holds_facts_only(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("events",))
    c.post("/events", data={"name": "Spring Recital", "date": "2026-12-05", "venue": "Hall"}, environ_base=E)
    page = _page(c, "/events")
    assert re.search(r'<a class="ui-row" href="/event/1">', page) and "Add the event" in page
    ev = _page(c, "/event/1")
    head = ev.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert not re.search(r"<(button|form)\b", head)

def test_timeline_labels_on_a_phone_never_meet_each_other_today_or_the_shoot(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", shoot_date=__import__("datetime").date.fromordinal(
        __import__("datetime").date.today().toordinal() + 40).isoformat())
    con = A.connect()
    con.execute("UPDATE sessions SET created=date('now', '-300 days'), stage='booked' WHERE id=?", (sid,))
    con.commit()
    con.close()
    page = _page(c, "/c/1")
    today = float(re.search(r'<div class="j-today" style="left:([\d.]+)%', page).group(1))
    shoot = float(re.search(r'<div class="j-shoot" style="left:([\d.]+)%', page).group(1))
    ticks = [(m.group(1) == " minor", float(m.group(2)))
             for m in re.finditer(r'<i class="tick( minor)?" style="left:([\d.]+)%">', page)]
    phone = [x for minor, x in ticks if not minor]
    assert phone and all(b - a >= 15 for a, b in zip(phone, phone[1:]))
    assert all(abs(x - today) >= 13 and abs(x - shoot) >= 13 for x in phone)
