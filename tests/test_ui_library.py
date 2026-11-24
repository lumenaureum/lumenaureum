import pathlib
import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_client_page_141 import E, _session, env

BUTTON_CLASSES = {"ui-btn", "ui-x", "ui-menu-item"}
FOLD_CLASSES = {"ui-fold", "ui-rowmore", "ui-more"}

ONE_OFF_CLASSES = {"btn", "go", "primary", "quiet", "verb", "ghost", "pillbtn", "xbtn", "cm-act", "cm-link",
                   "closemenu", "billfold", "danger"}

NOT_YET_MIGRATED = {
    "_action.html", "_edit_clash.html", "_import_msg.html", "_macros.html", "about.html",
    "auth_2fa.html", "auth_login.html", "auth_master_unlock.html", "auth_recovery.html",
    "auth_setup_key.html", "auth_setup_owner.html", "auth_setup_verify.html", "base.html", "confirm_post.html",
    "inquiry.html", "master_key_check.html", "setup.html", "unconfirmed.html", "vault_ceremony.html",
    "walk.html",
}

TAG = re.compile(r"<(button|details|a)\b([^>]*)>", re.S | re.I)

BARE_LABELS = {"open", "go", "view"}
LABELED = re.compile(r"<(button|a)\b[^>]*>\s*([^<{]*?)\s*(?:&rsaquo;|›)?\s*</\1>", re.S | re.I)
CLASS = re.compile(r'\bclass="([^"]*)"')

def one_offs(text):

    bad = []
    text = re.sub(r"\{#.*?#\}", lambda c: "\n" * c.group(0).count("\n"), text, flags=re.S)
    for m in TAG.finditer(text):
        tag, attrs = m.group(1).lower(), m.group(2)
        cm = CLASS.search(attrs)
        classes = set(re.sub(r"\{[{%#].*?[}%#]\}", " ", cm.group(1)).split()) if cm else set()
        line = text.count("\n", 0, m.start()) + 1
        if classes & ONE_OFF_CLASSES:
            bad.append((line, m.group(0)[:120]))
        elif tag == "button" and not classes & BUTTON_CLASSES:
            bad.append((line, m.group(0)[:120]))
        elif tag == "details" and not classes & FOLD_CLASSES:
            bad.append((line, m.group(0)[:120]))
    return bad

def bare_labels(text):

    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    out = [m.group(0)[:120] for m in LABELED.finditer(text) if m.group(2).strip().lower() in BARE_LABELS]

    out += [m.group(0) for m in re.finditer(r"ui\.(?:go|button_link|action|opener)\([^)]*?,\s*'(?:Open|Go|View)'\s*[,)]", text, re.I)]
    return out

def _templates():
    return sorted(p for p in TEMPLATES.glob("*.html"))

def test_every_rebuilt_template_uses_only_the_library():
    found = {p.name: one_offs(p.read_text()) for p in _templates() if p.name not in NOT_YET_MIGRATED}
    found = {k: v for k, v in found.items() if v}
    assert not found, ("One-off button or fold markup in a template built from the library (use the macros in "
                       f"templates/_ui.html): {found}")

def test_no_button_or_link_is_a_bare_open_go_or_view():
    found = {p.name: bare_labels(p.read_text()) for p in _templates() if p.name not in NOT_YET_MIGRATED}
    found = {k: v for k, v in found.items() if v}
    assert not found, f"A button or link that names no object (rule 10): {found}"
    assert bare_labels('<a class="ui-btn" href="/x">Open</a>') and bare_labels("<button class=\"ui-btn\"> View </button>")
    assert bare_labels("{{ ui.go('/x', 'Open') }}") and not bare_labels('<a class="ui-btn" href="/x">Open quote</a>')

def test_the_client_page_never_offers_a_bare_open(env):

    A, c = env
    for stage, days in [("inquiry", None), ("quoted", 20), ("booked", 10), ("booked", 0), ("shot", -3), ("delivered", -20), ("closed", -60)]:
        con = A.connect()
        try:
            for t_ in ("sessions", "tasks", "log"):
                con.execute("DELETE FROM " + t_)
            con.commit()
        finally:
            con.close()
        _session(A, stage, days=days, start="10:00", msg="Hello")
        page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
        main = page.split('<main', 1)[1]
        assert not [m.group(0) for m in LABELED.finditer(main) if m.group(2).strip().lower() in BARE_LABELS], stage
        assert "New inquiry from the website" not in main

def test_the_header_holds_facts_only_and_needs_you_comes_first(env):

    A, c = env
    for stage, days in [("inquiry", None), ("quoted", 20), ("booked", 10), ("booked", 0), ("shot", -3), ("delivered", -20),
                        ("closed", -60), ("lost", 10)]:
        con = A.connect()
        try:
            for t_ in ("sessions", "tasks", "log"):
                con.execute("DELETE FROM " + t_)
            con.commit()
        finally:
            con.close()
        _session(A, stage, days=days, start="00:00", msg="Hello")
        page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
        head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
        assert not re.search(r"<(button|form)\b", head), (stage, head[:300])
        assert not re.search(r'class="[^"]*ui-btn', head), stage
        assert page.split("</header>", 1)[1].lstrip().startswith('<section class="ui-needs"'), stage
    page = c.get("/dev/components", environ_base=E).get_data(as_text=True)
    head = page.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
    assert not re.search(r"<(button|form)\b", head) and 'class="ui-needs"' in page

def test_every_way_to_a_client_lands_on_the_client_page(env):

    A, c = env
    _session(A, "shot", days=-3)
    con = A.connect()
    try:
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (1, 'Family session', 318, 'fee')")
        con.commit()
    finally:
        con.close()
    for path in ("/clients", "/search?q=Morgan", "/sessions", "/pipeline", "/money"):
        r = c.get(path, environ_base=E, follow_redirects=True)
        assert r.status_code == 200, (path, r.status_code)
        if r.history:
            assert r.request.path == "/c/1", (path, r.request.path)
            continue
        page = r.get_data(as_text=True).split("<main", 1)[1]
        assert re.search(r'href="/c/1(\?s=1)?[^"]*"', page), path
        assert 'href="/client/1"' not in page and 'data-href="/client/1"' not in page, path
        assert 'href="/session/1"' not in page and 'data-href="/session/1"' not in page, path
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    assert 'href="/client/1"' in page.split('id="more"', 1)[1]

def test_more_means_menus_and_the_bottom_fold_is_details_and_history(env):

    A, c = env
    _session(A, "shot", days=-3)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    bottom = re.search(r'<details[^>]*id="more"[^>]*>\s*<summary[^>]*>(.*?)</summary>', page, re.S)
    assert bottom and "Details and history" in bottom.group(1) and "More" not in re.sub(r"<[^>]+>", "", bottom.group(1))
    assert ">More</summary>" in page
    for path in ("/c/1?s=1", "/client/1"):
        html = c.get(path, environ_base=E).get_data(as_text=True)
        for summ in re.findall(r'<details[^>]*class="[^"]*ui-fold[^"]*"[^>]*>\s*<summary[^>]*>(.*?)</summary>', html, re.S):
            assert re.sub(r"<[^>]+>|\s", "", summ) != "More", (path, summ)
    with A.app.test_request_context():
        row = A.render_template_string('{% import "_ui.html" as ui with context %}{{ ui.row_more(a, "/") }}',
                                       a=dict(what="Files delivered", close={"session_id": 1, "step": "files"}))
    assert re.search(r'<details class="ui-rowmore"><summary aria-label="More for Files delivered: [^"]+"[^>]*>More</summary>', row)
    css = (pathlib.Path(A.__file__).parent / "static" / "style.css").read_text()
    assert "white-space: nowrap" in css.split(".ui-rowmore > summary {", 1)[1].split("}", 1)[0]

def test_no_map_provider_website_anywhere():

    root = TEMPLATES.parent
    files = [*root.glob("templates/*.html"), *root.glob("static/*.js"), *root.glob("plugins/*.py"), root / "app.py"]
    hits = [str(f.relative_to(root)) for f in files if re.search(r"maps\.apple\.com|maps\.google|google\.[a-z.]+/maps", f.read_text(errors="replace"))]
    assert not hits, hits

def test_the_not_yet_migrated_list_only_shrinks():
    names = {p.name for p in _templates()}
    gone = NOT_YET_MIGRATED - names
    assert not gone, f"Listed but no longer there (take them off NOT_YET_MIGRATED): {sorted(gone)}"
    clean = sorted(n for n in NOT_YET_MIGRATED if not one_offs((TEMPLATES / n).read_text()))
    assert not clean, f"Now clean (take them off NOT_YET_MIGRATED so they stay clean): {clean}"

def test_the_checker_catches_one_offs():
    assert one_offs('<button class="primary">Go</button>')
    assert one_offs('<a class="btn verb" href="/x">Open</a>')
    assert one_offs('<details class="closemenu"><summary>...</summary></details>')
    assert one_offs('<button>Plain</button>')
    assert one_offs('<a class="go" href="/x">Somewhere &rsaquo;</a>')
    assert not one_offs('<button class="ui-btn ui-btn-primary">Go</button><details class="ui-fold"></details>')
    assert not one_offs('<a class="ui-go" href="/x">Somewhere</a><a class="ui-btn ui-btn-quiet" href="/y">Open</a>')
    assert not one_offs('<button class="ui-btn {{ \'ui-btn-primary\' if x else \'ui-btn-quiet\' }}">Add</button>')

def test_the_client_page_and_the_components_page_are_built_from_the_library():
    for name in ("client_stage.html", "dev_components.html", "_ui.html", "_journey.html"):
        assert name not in NOT_YET_MIGRATED
        assert (TEMPLATES / name).is_file()

from html.parser import HTMLParser

BLOCKS = {"div", "p", "li", "tr", "section", "ul", "ol", "table", "form", "h2", "h3", "h4", "details", "label", "pre"}
VOID = {"input", "br", "img", "hr", "wbr"}

def fold_lines(page):

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out, self.cur, self.stack = [], None, []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if self.cur is None:
                if tag == "details" and "ui-fold" in (a.get("class") or "").split() and "ui-fold-more" not in (a.get("class") or ""):
                    self.cur = dict(id=a.get("id"), depth=0, lines=0, body=False, stack=[])
                return
            if tag in VOID:
                return
            st = self.cur["stack"]
            if tag == "div" and "ui-fold-body" in (a.get("class") or "") and not self.cur["body"]:
                self.cur["body"] = True
                st.append(dict(tag=tag, block=False, has=False, root=True))
                return
            if not self.cur["body"]:
                st.append(dict(tag=tag, block=False, has=False))
                return
            block = tag in BLOCKS
            if block:
                for fr in st:
                    fr["has"] = True
            if not block and len(st) == 1:
                self.cur["lines"] += 1
            st.append(dict(tag=tag, block=block, has=False))

        def handle_endtag(self, tag):
            if self.cur is None or tag in VOID:
                return
            st = self.cur["stack"]
            if not st:
                if tag == "details":
                    self.out.append((self.cur["id"], self.cur["lines"]))
                    self.cur = None
                return
            while st:
                fr = st.pop()
                if fr["block"] and not fr["has"]:
                    self.cur["lines"] += 1
                if fr["tag"] == tag:
                    break

    p = P()
    p.feed(page)
    return p.out

def _ui_env():

    from jinja2 import DictLoader, Environment
    src = (TEMPLATES.parent / "app.py").read_text()
    ns = {}
    exec(src[src.index("UI_BLOCKS = "):src.index('app.jinja_env.filters["ui_shape"]')], ns)
    env = Environment(loader=DictLoader({"_ui.html": (TEMPLATES / "_ui.html").read_text()}))
    env.filters["ui_shape"] = ns["ui_shape"]
    return env

def test_the_fold_shows_one_line_or_one_action_in_place():
    env = _ui_env()
    r = lambda body: env.from_string("{% import '_ui.html' as ui %}" + body).render()
    one_fact = r("{% call ui.fold('Light', id='l') %}{{ ui.fact('Light', 'Sunset 4:44 pm') }}{% endcall %}")
    one_action = r("{% call ui.fold('They said yes', id='y') %}<div class=\"ui-btnrow\"><form method=\"post\" action=\"/x\">"
                   "<button class=\"ui-btn\">Book it</button></form></div>{% endcall %}")
    three = r("{% call ui.fold('People', id='p') %}{{ ui.fact('A', '1') }}{{ ui.fact('B', '2') }}{{ ui.fact('C', '3') }}{% endcall %}")
    more = r("{% call ui.fold('More', id='m', always=true) %}{{ ui.fact('A', '1') }}{% endcall %}")
    assert "<details" not in one_fact and "Sunset 4:44 pm" in one_fact and "ui-sub-title" not in one_fact
    assert "<details" not in one_action and ">Book it</button>" in one_action
    assert "<details" in three and fold_lines(three) == [("p", 3)]
    assert "<details" in more

def test_the_checker_sees_a_fold_with_one_item():
    page = ('<details class="ui-fold" id="x"><summary>Light</summary><div class="ui-fold-body">'
            '<div class="ui-fact"><span>Light</span><span>4:44 pm</span></div></div></details>')
    assert fold_lines(page) == [("x", 1)]

def test_no_fold_on_the_client_page_hides_a_single_item(env):

    A, c = env
    seen = 0
    bad = {}
    for i, (stage, days) in enumerate([("inquiry", None), ("quoted", 20), ("booked", 10), ("booked", 0), ("shot", -3),
                                        ("delivered", -20), ("closed", -60)]):
        con = A.connect()
        try:
            con.execute("DELETE FROM sessions")
            con.execute("DELETE FROM tasks")
            con.execute("DELETE FROM log")
            con.commit()
        finally:
            con.close()
        _session(A, stage, days=days, start="10:00", msg="Hello")
        page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
        folds = fold_lines(page)
        seen += len(folds)
        thin = [(fid, n) for fid, n in folds if n < 2]
        if thin:
            bad[f"{stage}{days}"] = thin
    page = c.get("/dev/components", environ_base=E).get_data(as_text=True)
    thin = [(fid, n) for fid, n in fold_lines(page) if n < 2]
    if thin:
        bad["components"] = thin
    assert seen, "the client page has folds to check"
    assert not bad, f"Folds that hide a single item (rule 9): {bad}"
