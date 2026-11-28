import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import _app, _q, _new, E

def _page(c, url):
    r = c.get(url, environ_base=E)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)

def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def _studio(tmp_path, monkeypatch, year="2026"):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ava", shoot_date=f"{year}-06-01")
    c.post(f"/session/{sid}/money", data={"what": "line", "label": "Family session", "amount": "600"}, environ_base=E)
    return A, c, sid

def test_money_always_shows_cents():
    import formats
    assert formats.money(600) == "$600.00" and formats.money(1250.5) == "$1,250.50" and formats.money(-125) == "-$125.00"
    assert formats.money(0) == "$0.00" and formats.money(None) == "$0.00" and formats.money(-0.001) == "$0.00"

def _seeded(tmp_path, monkeypatch):
    from test_accountant_export import seed_year
    from helpers import fresh_modules, signed_in_client
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    seed_year(A)
    con = A.connect()
    con.executescript("""
        INSERT INTO lines (session_id, label, amount, kind, fee_key) VALUES (1, 'Travel', 14, 'passthrough', 'travel');
        INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (1, '2026-05-03', 'Second drive', 0, 14, 0.70);
        INSERT INTO expenses (session_id, date, label, amount, miles, rate) VALUES (NULL, '2026-03-01', 'Lens cleaning kit', 34.5, 0, NULL);
        INSERT INTO settings (key, value) VALUES ('business_name', 'Bright Meadow Photo LLC'), ('photographer_name', 'Sam Rivera'),
            ('business_address', '12 Elm St
Springfield'), ('business_email', 'sam@example.com');
    """)
    con.commit()
    con.close()
    A.apply_settings()
    return A, c

def test_finances_headings_total_what_their_lists_show(tmp_path, monkeypatch):
    A, c = _seeded(tmp_path, monkeypatch)
    page = _page(c, "/money?year=2026")

    head = re.search(r'<h2 class="ui-sec-title">(Business expenses:[^<]*)</h2>', page).group(1)
    assert head == "Business expenses: $155.00", head
    sec = page.split('id="overhead"', 1)[1].split("</section>", 1)[0]
    assert "Lens cleaning kit" in sec and "$34.50" in sec and "Drive to the park" not in sec

    head = re.search(r'<h2 class="ui-sec-title">(Driven for sessions:[^<]*)</h2>', page).group(1)
    assert head == "Driven for sessions: 44 mi · $29.45 at your rate", head
    sec = page.split('id="driven"', 1)[1].split("</section>", 1)[0]
    assert sec.count('class="ui-money-row') == 2 and "$19.65" in sec and "$9.80" in sec and "44 mi" in sec

    year_ = page.split('id="year"', 1)[1].split("</section>", 1)[0]
    assert "Travel charged to clients" in year_ and "$14.00" in year_

def test_finances_states_figures_without_explainer_paragraphs(tmp_path, monkeypatch):
    A, c = _seeded(tmp_path, monkeypatch)
    page = _page(c, "/money?year=2026")
    for gone in ("Figures follow your settings", "check with your tax authority", "Kept apart from your sessions: not in received above. The sales tax",
                 "yours to remit", "One zip of spreadsheet files", "(in received)", "(no mileage rate set)", "(balances waived"):
        assert gone not in page, gone
    assert not re.search(r'<p class="ui-mute">[^<]{90,}</p>', page)

def test_finances_year_switcher_bolds_the_year_on_screen(tmp_path, monkeypatch):
    A, c = _seeded(tmp_path, monkeypatch)
    page = _page(c, "/money?year=2026")
    nav = page.split('class="ui-pager"', 1)[1].split("</nav>", 1)[0]
    assert re.search(r'href="/money\?year=2025"[^>]*>&lsaquo; 2025</a>', nav)
    assert '<b class="ui-pager-now" aria-current="page">2026</b>' in nav and 'href="/money?year=2026"' not in nav
    assert re.search(r'href="/money\?year=2027"[^>]*>2027 &rsaquo;</a>', nav)

def test_accountant_fold_offers_the_summary_and_the_csv_details(tmp_path, monkeypatch):
    A, c = _seeded(tmp_path, monkeypatch)
    sec = _page(c, "/money?year=2026").split('id="accountant"', 1)[1].split("</details>", 1)[0]
    assert 'action="/money/summary.pdf"' in sec and ">Year summary for your accountant</button>" in sec
    assert 'formaction="/money/accounts.zip"' in sec and ">Details (CSV)</button>" in sec

def test_year_summary_is_one_page_of_figures_with_who_and_when(tmp_path, monkeypatch):
    A, c = _seeded(tmp_path, monkeypatch)
    with A.app.test_request_context():
        v = A.year_summary(2026)
        html = A.render_template("year_summary.html", v=v, pdf=True)
    t = _text(html)
    for want in ("Bright Meadow Photo LLC", "Sam Rivera", "12 Elm St, Springfield", "sam@example.com", "Tax year 2026",
                 "Period covered January 1, 2026 to", "Generated " + v["generated"], f"App version Lumen Aureum {v['version']}"):
        assert want in t, want

    assert v["gross"] == 748.0 and v["refunds"] == 54.0 and v["net"] == 694.0
    assert "Total received $748.00" in t and "Refunds given -$54.00" in t and "Received, less refunds $694.00" in t
    assert "Lens cleaning kit $34.50" in t and "Miles driven: 44 mi" in t and "$29.45" in t
    assert "Balances waived" in t and "Sales tax collected" in t
    for advice in ("deduct", "should", "consult", "check with"):
        assert advice not in t.lower(), advice
    r = c.get("/money/summary.pdf?year=2026", environ_base=E)
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:5] == b"%PDF-"
    import weasyprint
    doc = weasyprint.HTML(string=html, base_url=ROOT).render(stylesheets=[weasyprint.CSS(string="@page { size: Letter; margin: 0.5in; }")])
    assert len(doc.pages) == 1
    assert 'filename="year-summary-2026.pdf"' in r.headers["Content-Disposition"]

def test_settings_search_finds_the_setting_itself_by_label_help_and_common_words(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        def first(term):
            r = A.settings_find(term)
            assert r, term
            return r[0]["link"]
        assert first("mileage") == "/setup/money#f-mileage_rate"
        assert first("checks").startswith("/settings?tab=profile#f-business_address") or any(
            r_["link"] == "/setup/money#pay-methods" for r_ in A.settings_find("checks"))
        assert any(r_["link"].endswith("#taxes") for r_ in A.settings_find("tax"))
        assert any(r_["link"].endswith("#f-backup_time") for r_ in A.settings_find("backup"))
        assert any(r_["link"].endswith("#f-gallery_codeword") and "/types/" in r_["link"] or "#f-gallery_codeword" in r_["link"]
                   for r_ in A.settings_find("codeword"))

        assert any(r_["link"].endswith("#f-timezone") for r_ in A.settings_find("deadline"))
        assert A.settings_find("zzzz") == []
    for page in ("/settings", "/settings?tab=prices", "/setup/money"):
        nav = _page(c, page).split('class="settabs', 1)[1].split("</nav>", 1)[0]
        assert "data-ui-find" not in nav and 'class="ui-find-index"' not in nav, page

    html = _page(c, "/search?q=mileage")
    out = html.split('id="setting-hits"', 1)[1].split("</section>", 1)[0]
    assert 'href="/setup/money#f-mileage_rate"' in out and "Mileage rate" in out
    assert "Nothing found." in _page(c, "/search?q=zzzz")

def test_every_searched_anchor_is_on_its_page(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('site_on', '1')")
    con.commit(); con.close()
    with A.app.test_request_context():
        links = {e_["link"] for e_ in A.settings_find_entries() if "#" in e_["link"] and not e_["link"].endswith("#demo")}
    import settings_registry as R
    sometimes = {f_["anchor"] for f_ in R.fields() if f_.get("optional")}
    pages = {}
    for link in sorted(links):
        url, anchor = link.split("#", 1)
        if anchor in sometimes:
            continue
        if url not in pages:
            pages[url] = c.get(url, environ_base=E, follow_redirects=True).get_data(as_text=True)
        assert f'id="{anchor}"' in pages[url], link

def test_calendar_day_cell_is_one_target_and_a_crowded_day_shows_more(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    for i in range(6):
        _new(A, c, "family", subject_name=f"Kid{i}", shoot_date="2027-03-13", name=f"Family{i}")
    page = _page(c, "/calendar?m=2027-03")
    cells = re.findall(r'<a class="mcell[^"]*" data-day="2027-03-13".*?</a>', page, re.S)
    assert len(cells) == 1
    cell = cells[0]
    assert "<a " not in cell[3:] and "<form" not in cell
    assert 'href="/calendar?m=2027-03&amp;d=2027-03-13#day-2027-03-13"' in cell or 'd=2027-03-13' in cell
    assert cell.count('class="mshoot') == 2 and re.search(r'class="mmore">\+ \d+ more<', cell)

    day = page.split('id="day-2027-03-13"', 1)[1].split("</section>", 1)[0]
    assert " hidden" in page.split('id="day-2027-03-13"', 1)[1][:80]

    assert len(re.findall(r'class="ui-row" href="/c/\d+\?s=\d+"><span class="ui-row-main"><b>[^<]+</b><small>Not penciled', day)) == 6

    page = _page(c, "/calendar?m=2027-03&d=2027-03-13")
    assert 'class="mwrap mwrap-open"' in page
    assert ' hidden' not in page.split('id="day-2027-03-13"', 1)[1][:120]
    cell = re.search(r'<a class="mcell[^"]*mcell-on" data-day="2027-03-13"\s+href="([^"]+)"', page)
    assert cell and cell.group(1) == "/calendar?m=2027-03"

    nav = page.split('class="ui-pager"', 1)[1].split("</nav>", 1)[0]
    assert "&lsaquo; Feb 2027</a>" in nav and '<b class="ui-pager-now" aria-current="page">March 2027</b>' in nav and "Apr 2027 &rsaquo;</a>" in nav

def test_a_gallery_built_before_the_credit_mark_gets_it_without_a_rebuild(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.join(ROOT, "plugins"))
    import gallery
    import refit
    monkeypatch.setattr(gallery, "OUT_ROOT", tmp_path)
    gid = "aZCZG6rfE7Wj"
    old_credit = gallery.CREDIT_HTML.split("<span>", 1)[0].split("<svg", 1)[0] + "<span>" + gallery.CREDIT_HTML.split("<span>", 1)[1]
    assert "<svg" not in old_credit
    page = gallery.viewer_page(gid, gate=True, credit=True).replace(gallery.CREDIT_HTML, old_credit)
    (tmp_path / gid).mkdir()
    (tmp_path / gid / "index.html").write_text(page)
    assert gallery.refresh_page(gid)
    now = (tmp_path / gid / "index.html").read_text()
    assert gallery.CREDIT_HTML in now and "<svg" in now and 'data-gate="1"' in now
    assert not gallery.refresh_page(gid)

    G = gallery._G() if hasattr(gallery, "_G") else gallery
    fp = refit.fingerprint(G)
    monkeypatch.setattr(gallery, "CREDIT_HTML", gallery.CREDIT_HTML.replace("Gallery by", "Gallery made by"))
    assert refit.fingerprint(gallery._G() if hasattr(gallery, "_G") else gallery) != fp

def test_the_studio_logo_is_sized_by_its_shape():
    v = open(os.path.join(ROOT, "plugins", "gallery_assets", "viewer.html")).read()
    assert "max-height: 72px" not in v
    assert "function sizeLogo()" in v and '$("logo").onload = sizeLogo' in v

def test_year_summary_is_streamed_never_saved(tmp_path, monkeypatch):

    import tempfile
    A, c = _seeded(tmp_path, monkeypatch)
    roots = [tmp_path, ROOT, tempfile.gettempdir()]

    def files():
        out = set()
        for r_ in roots:
            for d_, dirs, fs in os.walk(r_):
                dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
                out |= {os.path.join(d_, f_) for f_ in fs}
        return out
    before = files()
    r = c.get("/money/summary.pdf?year=2026", environ_base=E)
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"
    assert r.headers["Cache-Control"] == "no-store"
    new = files() - before
    assert not [f_ for f_ in new if f_.endswith(".pdf") or "summary" in os.path.basename(f_)], new
    for f_ in new:
        try:
            data = open(f_, "rb").read()
        except OSError:
            continue
        assert b"Year summary" not in data and b"Bright Meadow Photo LLC" not in data, f_

def test_history_collapses_identical_repeats_and_icons_fit(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Kim")
    for _ in range(6):
        assert c.get("/client/1/export.json", environ_base=E).status_code == 200
    with A.app.test_request_context():
        name = A.q("SELECT name FROM households WHERE id=1", one=True)["name"]

        assert not [r_ for r_ in A.client_history(1) if "xported" in r_["text"] or "everything kept" in r_["text"]]
        rows = [r_ for r_ in A.account_activity() if "everything kept about" in r_["text"]]
        assert len(rows) == 1 and rows[0]["text"] == (f"Downloaded a file of everything kept about {name} "
                                                      "(from Edit client details) · 6 times today")
        rows = A.history_collapse([dict(kind="audit", day="2026-11-01", text="Printed the QR letter", note="", who="Owner",
                                        sid=1)] * 3)
        assert len(rows) == 1 and rows[0]["text"] == "Printed the QR letter · 3 times"
        assert A.history_icon("Exported everything held about this client") == "download"
        assert A.history_icon("Printed the QR letter") == "print"
        assert A.history_icon("Sealed the RAWs and exports (encrypted; plaintext removed)") == "lock"
    assert _q(A, "SELECT COUNT(*) FROM audit WHERE action='Exported everything held about this client'")[0][0] == 6
    assert sid

def test_buttons_name_their_object(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch)
    with A.app.test_request_context():
        assert A._open_words("/prints/order/3") == "Open the print order"
        assert A._open_words("/vault") == "Open vault"
        assert all("the page" not in w_ for _, w_ in A.OPEN_WORDS)
    src = open(os.path.join(ROOT, "app.py")).read() + open(os.path.join(ROOT, "plugins", "letters.py")).read()
    for gone in ('"Open the page"', '"Open details"', 'verb="Print letter"'):
        assert gone not in src, gone
    assert 'doc_verb="Send the session sheet"' in src
    assert "'Print the QR letter'" in open(os.path.join(ROOT, "templates", "_ui_photos.html")).read()
    assert A.TODO_WORDS["Send session details"] == "Send the session sheet"
    sid = _new(A, c, "family", subject_name="Ava", shoot_date="2027-05-01")
    assert "<title>Session sheet - " in _page(c, f"/session/{sid}/details")

def test_account_activity_holds_security_events_and_history_keeps_the_work(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch)
    sid = _new(A, c, "family", subject_name="Ellis")
    assert c.get("/client/1/export.json", environ_base=E).status_code == 200
    assert c.get("/settings/export.json", environ_base=E).status_code == 200
    with A.app.test_request_context():
        name = A.q("SELECT name FROM households WHERE id=1", one=True)["name"]
        A.audit("Security: 3 failed attempts since the last sign-in (the last: owner from 10.0.0.9, Nov 18, 9:02 AM).")
        A.audit("Moved to Booked", sid=sid)
        hist = [r_["text"] for r_ in A.client_history(1)]
        acts = [r_["text"] for r_ in A.account_activity()]
    assert "Moved to Booked" in hist
    assert hist
    assert not any("xport" in t or "everything kept" in t or "failed attempt" in t for t in hist)
    assert f"Downloaded a file of everything kept about {name} (from Edit client details)" in acts
    assert "Exported the settings" in acts
    assert acts[0].startswith("3 failed attempts since the last sign-in")
    assert "Moved to Booked" not in acts
    assert _q(A, "SELECT COUNT(*) FROM audit WHERE action='Exported everything held about this client'")[0][0] == 1
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert "Account activity" in page and f"everything kept about {name}" in page
    stage = c.get("/c/1", environ_base=E).get_data(as_text=True)
    assert "everything held about this client" not in stage and "everything kept about" not in stage
