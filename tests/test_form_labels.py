import os
import re
import runpy
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
NOT_FIELDS = {"hidden", "submit", "button", "reset", "image"}

class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = []
        self.label_for = []
        self.labelledby = []
        self.fields = []
        self.legends = set()
        self.text = {}

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "label" and a.get("for"):
            self.label_for.append(a["for"])
        for i in a.get("aria-labelledby", "").split():
            self.labelledby.append((f"{tag} {a.get('name') or a.get('id') or ''}", i))
        if tag == "legend":
            for t, _, node in reversed(self.stack):
                if t == "fieldset":
                    self.legends.add(id(node))
                    break
        if tag in ("input", "select", "textarea"):
            self.fields.append((tag, a, [(t, at, n) for t, at, n in self.stack]))
        if tag not in VOID:
            self.stack.append((tag, a, object()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

def problems(html):
    p = _Page()
    p.feed(html)
    ids = set(p.ids)
    out = [f"duplicate id {i!r}" for i in sorted({i for i in p.ids if p.ids.count(i) > 1})]
    out += [f"label for={f!r} points at nothing" for f in p.label_for if f not in ids]
    out += [f"{w}: aria-labelledby {i!r} points at nothing" for w, i in p.labelledby if i not in ids]
    has_label = set(p.label_for)
    groups = {}
    for tag, a, anc in p.fields:
        kind = a.get("type", "text").lower() if tag == "input" else tag
        if kind in NOT_FIELDS:
            continue
        what = f"<{tag} name={a.get('name')!r} type={kind!r}>"
        named = (a.get("id") and a["id"] in has_label) or any(t == "label" for t, _, _ in anc) \
            or a.get("aria-label", "").strip() or a.get("aria-labelledby", "").strip() or a.get("title", "").strip() \
            or a.get("placeholder", "").strip()
        if not named:
            out.append(f"{what} has no accessible name")
        if kind == "radio":
            groups.setdefault((a.get("form", ""), a.get("name")), []).append((what, anc))
    for (_, name), radios in groups.items():
        for what, anc in radios:
            box = [(t, at, n) for t, at, n in anc
                   if (at.get("role") == "radiogroup" and (at.get("aria-labelledby") or at.get("aria-label")))
                   or (t == "fieldset" and (id(n) in p.legends or at.get("aria-labelledby") or at.get("aria-label")))]
            if not box:
                out.append(f"radio group {name!r} is not in a named radiogroup or fieldset")
                break
    return out

def _seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    runpy.run_path(os.path.join(ROOT, "seed_demo.py"), run_name="seed")
    return A, C, c

def _rows(A, sql):
    con = A.connect()
    try:
        return [r for r in con.execute(sql)]
    finally:
        con.close()

def _check(c, urls):

    bad, names = {}, set()
    for url in urls:
        r = c.get(url, environ_base=E)
        assert r.status_code == 200, (url, r.status_code)
        html = r.get_data(as_text=True)
        names |= set(re.findall(r'<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"', html))
        found = problems(html)
        if found:
            bad[url] = found
    return bad, names

def test_the_checker_catches_what_the_tester_heard():
    assert problems('<label class="l">Title</label><input name="title">') == ["<input name='title' type='text'> has no accessible name"]
    assert problems('<label class="l" for="t">Title</label><input id="t" name="title">') == []
    assert problems('<label>Title <input name="title"></label>') == []
    assert problems('<input name="q" aria-label="Search">') == []
    lone = '<label class="l">Kind</label><span class="seg"><label><input type="radio" name="kind" value="a">a</label></span>'
    assert problems(lone) == ["radio group 'kind' is not in a named radiogroup or fieldset"]
    grouped = ('<label class="l" id="k-l">Kind</label><span class="seg" role="radiogroup" aria-labelledby="k-l">'
               '<label><input type="radio" name="kind" value="a">a</label></span>')
    assert problems(grouped) == []
    assert problems('<fieldset><legend>Kind</legend><label><input type="radio" name="kind">a</label></fieldset>') == []
    assert problems('<span role="radiogroup" aria-labelledby="nope"><label><input type="radio" name="k">a</label></span>') \
        == ["span : aria-labelledby 'nope' points at nothing"]
    assert problems('<i id="a"></i><b id="a"></b>') == ["duplicate id 'a'"]

def test_back_office_forms_name_every_field(tmp_path, monkeypatch):
    A, C, c = _seeded(tmp_path, monkeypatch)
    sids = [r[0] for r in _rows(A, "SELECT id FROM sessions ORDER BY id")]
    stages = {}
    for sid, stage in _rows(A, "SELECT id, stage FROM sessions ORDER BY id"):
        stages.setdefault(stage, sid)
    hid_of = dict(_rows(A, "SELECT id, household_id FROM sessions"))
    hids = [r[0] for r in _rows(A, "SELECT id FROM households ORDER BY id")][:6]
    eids = [r[0] for r in _rows(A, "SELECT id FROM events ORDER BY id")][:3]
    assert len(stages) >= 5, stages
    urls = [f"/setup/{s}" for s in ("business", "storage", "shoot", "money", "tax", "documents", "galleries", "network")]
    urls += ["/settings", "/contact/new", "/inquiry", "/inquiry?hid=1"] + [f"/settings/type/{t}" for t in C.SESSION_TYPES]
    urls += [f"/inquiry?hid=1&type={t}" for t in C.SESSION_TYPES]
    urls += [f"/settings?tab={t}" for t in ("fees", "documents", "file", "system")]
    urls += ["/", "/sessions", "/events", "/vault"]
    urls += [f"/client/{h}" for h in hids] + [f"/event/{e}" for e in eids]
    for sid in sorted(set(stages.values()) | set(sids[:12])):
        urls += [f"/session/{sid}", f"/c/{hid_of[sid]}?s={sid}"]
    key = next(iter(C.SESSION_TYPES))
    for n in range(40):
        r = c.get(f"/settings/type/{key}/walk/{n}", environ_base=E)
        if r.status_code != 200:
            break
        urls.append(f"/settings/type/{key}/walk/{n}")
    assert sum("/walk/" in u for u in urls) > 3
    bad, names = _check(c, urls)
    assert not bad, "\n".join(f"{u}: {p}" for u, ps in bad.items() for p in ps)

    for n in ("business_name", "photographer_name", "business_email", "business_address", "timezone", "title",
              "shoot_date", "start_time", "duration_min", "stops", "notes", "event_id", "subject_age", "received",
              "referred_by", "contact_gender", "subject_gender", "gender", "channel", "kind", "method", "gallery_font",
              "gallery_bg", "proof_mark", "bal_family", "rights_family", "tax_mode", "delivery_method"):
        assert n in names, n
