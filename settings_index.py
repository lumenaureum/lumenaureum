import settings_registry as _reg

ENTRIES = _reg.entries()

TYPE_FIELDS = [
    ("Ask for a gallery passphrase", "passphrase passphrases codeword codewords gallery words password", "f-gallery_codeword"),
    ("Deposit for this type", "deposit retainer", "dep-l"),
    ("Portfolio permission discount", "portfolio discount social media", "folio-l"),
]
TYPE_WORDS = "steps workflow intake questions terms reschedule no-show late outfits wear bring weather delivery deposit indoors who signs"

def _link(url_for, ep, args, anchor):
    if ep == "settings_view" and "tab" not in args:
        args = dict(args, tab="profile")
    return url_for(ep, **args) + (f"#{anchor}" if anchor else "")

def entries(types, url_for, help_words=None):

    help_words = help_words or {}
    out = []
    for label, where, extra, ep, args, anchor in ENTRIES:
        try:
            link = _link(url_for, ep, args, anchor)
        except Exception:
            continue
        out.append(dict(text=label, sub=f"Settings, {where}", link=link,
                        words=f"{label} {where} {extra} {help_words.get(anchor, '')}".lower()))
    for key, label in types.items():
        out.append(dict(text=f"{label}: steps, intake, and terms", sub="Settings, What you shoot",
                        link=url_for("type_edit", key=key), words=f"{label} session type {TYPE_WORDS}".lower()))
        for flabel, words, anchor in TYPE_FIELDS:
            out.append(dict(text=f"{flabel}: {label}", sub="Settings, What you shoot",
                            link=_link(url_for, "type_edit", {"key": key}, anchor),
                            words=f"{flabel} {label} {words} {help_words.get(anchor, '')}".lower()))
    return out

def find(term, types, url_for, help_words=None):

    words = [w for w in term.lower().split() if w]
    if not words:
        return []
    hits = [e_ for e_ in entries(types, url_for, help_words) if all(w in e_["words"] for w in words)]

    hits.sort(key=lambda e_: (not whole_words(e_["words"], words), not whole_words(e_["text"].lower(), words)))
    return [dict(text=e_["text"], sub=e_["sub"], link=e_["link"]) for e_ in hits[:20]]

def whole_words(text_, words):

    import re
    have = set(re.findall(r"[a-z0-9.]+", text_)) | set(re.findall(r"[a-z0-9]+", text_))
    return all(w in have for w in words)

_HELP = None

def help_words():

    global _HELP
    if _HELP is None:
        import os
        import re
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        pat = re.compile(r'(?:for|id)="([^"{}]+)"[^<]*>[^<{]*(?:<[^>]*>[^<{]*)*?\{\{\s*help\("((?:[^"\\]|\\.)*)"')
        out = {}
        for name in ("settings.html", "setup.html", "type_edit.html", "auth_security.html"):
            try:
                src = open(os.path.join(root, name), encoding="utf-8").read()
            except OSError:
                continue
            for line in src.splitlines():
                for m in pat.finditer(line):
                    out.setdefault(m.group(1), m.group(2))
        _HELP = out
    return _HELP
