import importlib.metadata as md
import os
import re
from pathlib import Path

from flask import render_template

ROOT = Path(__file__).resolve().parent
SOURCE = "https://github.com/lumenaureum/lumenaureum"
REQS = ("requirements.txt", "requirements-sftp.txt")

OTHER = [("SQLCipher", "BSD-3-Clause", "inside sqlcipher3"), ("OpenSSL libcrypto", "Apache-2.0", "inside sqlcipher3"),
         ("Fraunces font", "OFL-1.1", "Undercase Type"), ("Material Symbols font", "Apache-2.0", "Google"),
         ("US Census ZIP code tables", "Public domain", "US Census Bureau")]

def version():
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "dev"

def whats_new(v=None):

    v = v or version()
    try:
        text = (ROOT / "CHANGELOG.md").read_text()
    except OSError:
        return []
    out, on, item = [], False, None
    for line in text.splitlines():
        if line.startswith("## "):
            if on:
                break
            on = line[3:].split(" ", 1)[0] == v
            continue
        if not on:
            continue
        if line.startswith("### "):
            out.append((line[4:].strip(), []))
        elif line.startswith("- ") and out:
            out[-1][1].append(line[2:].strip())
        elif line.startswith("  ") and line.strip() and out and out[-1][1]:
            out[-1][1][-1] += " " + line.strip()
    return out

def _name(req):
    return re.split(r"[<>=!~;\[ ]", req.strip(), maxsplit=1)[0]

NO_LICENSE_FIELD = {"sqlcipher3": "Zlib"}

def _license(dist):
    m = dist.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        cls = [c.split(" :: ")[-1] for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = " / ".join(c.replace(" License", "") for c in cls) or (m.get("License") or "")
    lic = lic.replace("GNU Lesser General Public License v2 or later (LGPLv2+)", "LGPL-2.0+")
    lic = {"Apache Software": "Apache-2.0", "MIT License": "MIT", "BSD License": "BSD", "BSD": "BSD"}.get(lic, lic)
    lic = lic or NO_LICENSE_FIELD.get(m["Name"].lower(), "")
    return lic if len(lic) <= 40 else lic.split("\n")[0][:40]

def components():

    want, seen, out = [], set(), []
    for f in REQS:
        try:
            want += [_name(l) for l in (ROOT / f).read_text().splitlines() if l.strip() and not l.startswith(("#", "-"))]
        except OSError:
            pass
    while want:
        n = want.pop(0)
        key = n.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        try:
            d = md.distribution(n)
        except md.PackageNotFoundError:
            continue
        out.append((d.metadata["Name"], d.version, _license(d)))
        for r in d.requires or []:
            if "extra ==" not in r:
                want.append(_name(r))
    return sorted(out, key=lambda t: t[0].lower())

def register(app):
    @app.get("/about", endpoint="about")
    def about():

        from flask import session
        import auth
        signed_in = bool(session.get("uid")) and auth.SESSIONS.get(session.get("sid")) == session.get("uid")
        return render_template("about.html", version=version() if signed_in else "", build=os.environ.get("LUMENAUREUM_BUILD", "") if signed_in else "",
                               source=SOURCE, parts=components(), other=OTHER, signed_in=signed_in,
                               news=whats_new() if signed_in else [],
                               plugins=[p_ for p_ in __import__("app").PLUGINS_LOADED if not p_["built_in"]] if signed_in else [])
