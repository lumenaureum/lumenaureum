import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
E = {"REMOTE_ADDR": "127.0.0.1"}

class Node:
    __slots__ = ("tag", "attrs", "kids", "parent", "text")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.kids, self.parent, self.text = tag, dict(attrs), [], parent, []

    def get(self, k, d=None):
        v = self.attrs.get(k, d)
        return d if v is None and k in self.attrs and d is not None else v

    def has(self, k):
        return k in self.attrs

    def cls(self):
        return (self.attrs.get("class") or "").split()

    def words(self):
        out = []

        def walk(n):
            for part in n.text_and_kids():
                if isinstance(part, str):
                    out.append(part)
                elif part.tag not in ("script", "style") and "vh" not in part.cls() and not part.has("hidden"):
                    walk(part)
        walk(self)
        return " ".join(" ".join(out).split())

    def text_and_kids(self):
        return self.text

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def iter(self):
        yield self
        for k in self.kids:
            yield from k.iter()

    def __repr__(self):
        return f"<{self.tag} {self.attrs.get('id', '')} {self.words()[:50]!r}>"

class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        self.cur.text.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        self.cur.text.append(n)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text.append(data)

def parse(html):
    p = _Parser()
    p.feed(html)
    return p.root

class _Multi(dict):

    def add(self, k, v):
        if k in self:
            self[k] = (self[k] if isinstance(self[k], list) else [self[k]]) + [v]
        else:
            self[k] = v

class NotOffered(AssertionError):
    pass

class Walker:

    def __init__(self, A, c, width=1280):
        self.A, self.c, self.width = A, c, width
        self.taps = self.pages = self.dismissals = self.forced = self.opens = 0
        self.screens = 0.0
        self.at = 0
        self.url = None
        self.html = ""
        self.root = None
        self.opened = set()
        self.log = []

    def open(self, url):

        self._get(url)

    def _get(self, url, count=False):
        r = self.c.get(url, environ_base=E)
        hops = 0
        while r.status_code in (301, 302, 303) and hops < 5:
            url = urlsplit(r.location).path + (("?" + urlsplit(r.location).query) if urlsplit(r.location).query else "") + \
                (("#" + urlsplit(r.location).fragment) if urlsplit(r.location).fragment else "")
            r = self.c.get(url, environ_base=E)
            hops += 1
        assert r.status_code == 200, (url, r.status_code)
        self._show(url, r.get_data(as_text=True), keep=False)
        if count:
            self.pages += 1

    def _show(self, url, html, keep):
        self.url, self.html = url, html
        self.root = parse(html)
        if not keep:
            self.opened = set()
            self.at = 0
        frag = urlsplit(url).fragment
        if frag:
            el = self.by_id(frag)
            if el is not None:
                for a in [el] + list(el.ancestors()):
                    if a.tag == "details" or "ui-inline" in a.cls():
                        self.opened.add(self._key(a))

    def by_id(self, i):
        return next((n for n in self.root.iter() if n.attrs.get("id") == i), None)

    def text(self):
        return self.root.words()

    def sees(self, words):

        return any(words in n.words() for n in self._shown_blocks())

    def _shown_blocks(self):
        for n in self.root.iter():
            if n.tag in ("p", "li", "span", "div", "h2", "h3", "button", "a", "summary", "b", "small") and self._visible(n)[0]:
                yield n

    def _key(self, n):
        return n.attrs.get("id") or f"@{id(n)}"

    def _hider(self, n):

        for a in [n] + list(n.ancestors()):
            if a.has("hidden") or "vh" in a.cls() or a.tag in ("template", "script", "noscript"):
                if a is n and n.tag == "input":
                    continue
                return "hard"
            if a.tag == "details" and not a.has("open") and self._key(a) not in self.opened:
                summ = next((k for k in a.kids if k.tag == "summary"), None)
                if summ is not None and (n is summ or summ in list(n.ancestors())):
                    continue
                return a
            if "ui-inline" in a.cls() and self._key(a) not in self.opened:
                return a
        return None

    def _visible(self, n):
        h = self._hider(n)
        return h is None, h

    def _opener_of(self, hider):
        if hider.tag == "details":
            return next(k for k in hider.kids if k.tag == "summary")
        i = hider.attrs.get("id")
        return next((n for n in self.root.iter() if n.attrs.get("data-ui-open") == i), None)

    def _path_to(self, n, seen=None):

        seen = seen or set()
        h = self._hider(n)
        if h is None:
            return []
        if h == "hard":
            raise NotOffered(f"{n!r} is hidden and no tap shows it")
        if id(h) in seen:
            raise NotOffered(f"{n!r} loops")
        seen.add(id(h))
        op = self._opener_of(h)
        if op is None:
            raise NotOffered(f"{n!r} sits in {h!r}, which nothing on the page opens")
        return self._path_to(op, seen) + [(op, h)]

    def _clickables(self, within=None):
        root = self.root if within is None else (self.by_id(within) if isinstance(within, str) else within)
        if root is None:
            raise NotOffered(f"no part {within!r} on {self.url}")
        for n in root.iter():
            if n.tag in ("a", "button", "summary") or (n.tag == "input" and n.get("type") in ("submit",)):
                if n.has("disabled"):
                    continue
                yield n

    def _label(self, n):
        return n.words() or n.attrs.get("aria-label") or n.attrs.get("value") or ""

    def find(self, label, within=None, exact=True, nth=0):
        hits = []
        for n in self._clickables(within):
            labs = {self._label(n), self._label(n).rstrip(" ›"), n.attrs.get("aria-label") or "", n.attrs.get("title") or ""} - {""}
            if any((lab == label if exact else label in lab) for lab in labs):
                try:
                    hits.append((len(self._path_to(n)), n))
                except NotOffered:
                    continue
        if not hits:
            offered = sorted({self._label(n) for n in self._clickables(within) if self._label(n)})
            raise NotOffered(f"{label!r} is not offered on {self.url} (within {within!r}). Offered: {offered}")
        hits.sort(key=lambda h: h[0])
        return hits[min(nth, len(hits) - 1)][1]

    def offers(self, label, within=None, exact=True):
        try:
            self.find(label, within, exact)
            return True
        except NotOffered:
            return False

    def taps_to(self, label, within=None, exact=True):

        return len(self._path_to(self.find(label, within, exact))) + 1

    SCREEN = 1400

    def _pos(self, target):

        total = [0]

        def walk(n):
            for part in n.text:
                if isinstance(part, str):
                    total[0] += len(part.strip())
                    continue
                if part is target:
                    return True
                if part.tag in ("script", "style", "template") or self._hider_self(part):
                    continue
                if walk(part):
                    return True
            return False
        walk(self.root)
        return total[0]

    def _hider_self(self, n):
        if n.has("hidden") or "vh" in n.cls():
            return True
        if n.tag == "details" and not n.has("open") and self._key(n) not in self.opened:
            return False
        if "ui-inline" in n.cls() and self._key(n) not in self.opened:
            return True
        if n.parent is not None and n.parent.tag == "details" and n.tag != "summary" and not n.parent.has("open") \
                and self._key(n.parent) not in self.opened:
            return True
        return False

    def _scroll_to(self, n):
        p = self._pos(n)
        self.screens += abs(p - self.at) / self.SCREEN
        self.at = p

    def _reveal(self, n):
        for op, h in self._path_to(n):
            self._scroll_to(op)
            self.opens += 1
            self.taps += 1
            self.log.append(f"open {self._label(op)!r}")
            self.opened.add(self._key(h))

    def tap(self, label, within=None, exact=True, fields=None, dismiss=False, forced=False, nth=0, confirm=True):

        n = self.find(label, within, exact, nth)
        self._reveal(n)
        self._scroll_to(n)
        self.taps += 1
        self.log.append(f"tap {label!r}")
        if forced:
            self.forced += 1
        if n.tag == "summary":
            self.opened.add(self._key(n.parent))
            return self
        if n.tag == "a":
            href = n.attrs.get("href") or ""
            if n.has("data-ui-open"):
                self.opened.add(n.attrs["data-ui-open"])
                return self
            if href.startswith("#"):
                el = self.by_id(href[1:])
                if el is not None:
                    for a in [el] + list(el.ancestors()):
                        if a.tag == "details" or "ui-inline" in a.cls():
                            self.opened.add(self._key(a))
                return self
            if href.startswith(("mailto:", "sms:", "tel:")):
                return self
            if n.attrs.get("target") == "_blank":
                self.pages += 1
                self._get(href)
                return self
            self._get(href, count=True)
            return self
        form = next((a for a in n.ancestors() if a.tag == "form"), None)
        if n.has("form"):
            form = self.by_id(n.attrs["form"])
        if form is None:
            raise NotOffered(f"{label!r} is a button with no form on {self.url}")
        if confirm and (form.has("data-confirm") or n.has("data-confirm")):
            self.taps += 1
            self.log.append("OK")
        data = self._form_data(form, n)
        data.update(fields or {})
        if form.attrs.get("data-confirm-sets"):
            data[form.attrs["data-confirm-sets"]] = "1"
        action = form.attrs.get("action") or self.url
        if self._is_dismissal(action, data):
            self.dismissals += 1
        if dismiss and not self._is_dismissal(action, data):
            self.dismissals += 1
        self._post(action, data, inplace=form.has("data-ui-inplace") and form.attrs.get("target") != "_blank",
                   blank=form.attrs.get("target") == "_blank")
        return self

    def _is_dismissal(self, action, data):
        path = urlsplit(action).path
        if path == "/close" and data.get("outcome", "done") != "done":
            return True
        if path.endswith("/skip") and data.get("from") == "row":
            return True
        return False

    def choose(self, name, value, within=None):

        root = self.root if within is None else self.by_id(within)
        el = next((n for n in root.iter() if n.attrs.get("name") == name and n.tag in ("input", "select", "textarea")
                   and (n.tag != "input" or n.get("type") not in ("radio", "checkbox") or n.attrs.get("value") == value)), None)
        if el is None:
            raise NotOffered(f"no choice {name}={value} on {self.url}")
        self._reveal(el)
        self.taps += 1
        self.log.append(f"choose {name}={value}")
        if el.tag == "input" and el.get("type") in ("radio", "checkbox"):
            if el.get("type") == "radio":
                form = next((a for a in el.ancestors() if a.tag == "form"), None)
                for o in (form or self.root).iter():
                    if o.tag == "input" and o.attrs.get("name") == name:
                        o.attrs.pop("checked", None)
                el.attrs["checked"] = ""
            elif el.has("checked"):
                el.attrs.pop("checked")
            else:
                el.attrs["checked"] = ""
        else:
            el.attrs["value"] = value
            el.attrs["data-picked"] = value
        form = next((a for a in el.ancestors() if a.tag == "form"), None)
        if el.has("data-autosubmit") or (form is not None and form.has("data-ui-autosave")):
            self._post(form.attrs.get("action") or self.url, self._form_data(form, None), inplace=form.has("data-ui-inplace") or form.has("data-ui-autosave"))
        return self

    def toggle(self, words, within=None):

        root = self.root if within is None else self.by_id(within)
        lab = next((n for n in root.iter() if n.tag == "label" and n.words() == words), None)
        box = next((n for n in lab.iter() if n.tag == "input" and n.get("type") == "checkbox"), None) if lab else None
        if box is None:
            offered = [n.words() for n in root.iter() if n.tag == "label" and any(k.tag == "input" for k in n.iter())]
            raise NotOffered(f"no box {words!r} on {self.url}. Boxes: {offered}")
        return self.choose(box.attrs["name"], box.attrs.get("value", "on"), within)

    def type_in(self, name, value, within=None):

        root = self.root if within is None else self.by_id(within)
        el = next((n for n in root.iter() if n.attrs.get("name") == name and n.tag in ("input", "textarea", "select")), None)
        if el is None:
            raise NotOffered(f"no box {name} on {self.url}")
        self._reveal(el)
        el.attrs["value"] = value
        el.attrs["data-picked"] = value
        return self

    def _form_data(self, form, button):
        data = _Multi()
        for n in form.iter():
            nm = n.attrs.get("name")
            if not nm or n.has("disabled"):
                continue
            if n.tag == "input":
                ty = n.get("type") or "text"
                if ty in ("submit", "button", "file", "image", "reset"):
                    continue
                if ty in ("radio", "checkbox"):
                    if n.has("checked"):
                        if ty == "checkbox" and nm in data:
                            data[nm] = data[nm] if isinstance(data[nm], list) else [data[nm]]
                            data[nm].append(n.attrs.get("value", "on"))
                        else:
                            data[nm] = n.attrs.get("value", "on")
                    continue
                data.add(nm, n.attrs.get("value") or "")
            elif n.tag == "textarea":
                data[nm] = n.attrs.get("value") or "".join(t for t in n.text if isinstance(t, str))
            elif n.tag == "select":
                if n.attrs.get("data-picked") is not None:
                    data[nm] = n.attrs["data-picked"]
                else:
                    opts = [o for o in n.iter() if o.tag == "option"]
                    sel = next((o for o in opts if o.has("selected")), opts[0] if opts else None)
                    data[nm] = (sel.attrs.get("value") if sel is not None and "value" in sel.attrs else
                                (sel.words() if sel is not None else ""))
        if button is not None and button.attrs.get("name"):
            data[button.attrs["name"]] = button.attrs.get("value", "")
        return data

    def _post(self, action, data, inplace=True, blank=False):
        r = self.c.post(action, data=dict(data), environ_base=E)
        assert r.status_code in (200, 302, 303), (action, data, r.status_code, r.get_data(as_text=True)[:300])
        self.log.append(f"post {action} {sorted(data.items())[:6]}")
        if r.status_code in (302, 303) and r.location.startswith(("mailto:", "sms:", "tel:")):
            self._show(self.url, self.c.get(self.url, environ_base=E).get_data(as_text=True), keep=True)
        elif r.status_code in (302, 303):
            loc = urlsplit(r.location)
            url = loc.path + (("?" + loc.query) if loc.query else "") + (("#" + loc.fragment) if loc.fragment else "")
            g_ = self.c.get(url, environ_base=E)
            hops = 0
            while g_.status_code in (301, 302, 303) and hops < 5:
                loc = urlsplit(g_.location)
                url = loc.path + (("?" + loc.query) if loc.query else "")
                g_ = self.c.get(url, environ_base=E)
                hops += 1
            assert g_.status_code == 200, (url, g_.status_code)
            keep = inplace and urlsplit(url).path == urlsplit(self.url).path
            self._show(url, g_.get_data(as_text=True), keep=keep)
        else:
            self._show(self.url, r.get_data(as_text=True), keep=inplace)
        if not inplace or blank:
            self.pages += 1

    def upload(self, kind, files, uploader, sid):

        zone = next((n for n in self.root.iter() if n.attrs.get("data-kind") == kind
                     and ("dropzone" in n.cls() or n.has("data-drop") or n.tag in ("div", "label", "section"))), None)
        if zone is None:
            raise NotOffered(f"no drop zone for {kind} on {self.url}")
        self._reveal(zone)
        self.taps += 1
        self.log.append(f"choose {len(files)} files ({kind})")
        for name, data in files:
            uploader(self.c, sid, name, data, kind)
        self._show(self.url, self.c.get(self.url, environ_base=E).get_data(as_text=True), keep=True)
        return self

    def reload(self):

        self._show(self.url, self.c.get(self.url, environ_base=E).get_data(as_text=True), keep=True)
        return self

    def counts(self):
        return dict(taps=self.taps, pages=self.pages, dismissals=self.dismissals, forced=self.forced, opens=self.opens,
                    screens=round(self.screens, 1))

def menu_items(html_or_root, topic_id):

    root = parse(html_or_root) if isinstance(html_or_root, str) else html_or_root
    box = next((n for n in root.iter() if n.attrs.get("id") == f"more-{topic_id}"), None)
    if box is None:
        return None
    out = []
    for n in box.iter():
        if "ui-topic-item" in n.cls():
            reason = next((k.words() for k in n.iter() if "ui-topic-why" in k.cls()), "")
            lab = next((k.words() for k in n.iter() if "ui-topic-label" in k.cls()), n.words())
            out.append((lab, not n.has("aria-disabled"), reason))
    return out

__all__ = ["Walker", "NotOffered", "parse", "menu_items", "re"]
