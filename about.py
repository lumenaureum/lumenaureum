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

SPDX_IDS = {"0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "GPL-2.0-only", "GPL-2.0-or-later",
            "GPL-3.0-only", "GPL-3.0-or-later", "HPND", "ISC", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only",
            "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT", "MIT-0", "MIT-CMU", "MPL-1.1", "MPL-2.0",
            "OFL-1.1", "PSF-2.0", "Python-2.0", "Unlicense", "Zlib"}

SPDX_OLD = {"GPL-2.0": "GPL-2.0-only", "GPL-2.0+": "GPL-2.0-or-later", "GPL-3.0": "GPL-3.0-only",
            "GPL-3.0+": "GPL-3.0-or-later", "LGPL-2.0": "LGPL-2.0-only", "LGPL-2.0+": "LGPL-2.0-or-later",
            "LGPL-2.1": "LGPL-2.1-only", "LGPL-2.1+": "LGPL-2.1-or-later", "LGPL-3.0": "LGPL-3.0-only",
            "LGPL-3.0+": "LGPL-3.0-or-later", "Apache 2.0": "Apache-2.0", "Apache License 2.0": "Apache-2.0",
            "Apache Software License": "Apache-2.0", "MIT License": "MIT", "Zlib License": "Zlib"}

CLASSIFIER_SPDX = {"Apache Software License": "Apache-2.0", "MIT License": "MIT", "ISC License (ISCL)": "ISC",
                   "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "zlib/libpng License": "Zlib",
                   "Python Software Foundation License": "PSF-2.0", "The Unlicense (Unlicense)": "Unlicense",
                   "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
                   "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
                   "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
                   "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
                   "Historical Permission Notice and Disclaimer (HPND)": "HPND"}

OVERRIDES = {
    "pyrage": "MIT",
    "sqlcipher3": "Zlib",
    "pyphen": "GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1",
    "python-dateutil": "Apache-2.0 AND BSD-3-Clause",

    "cssselect2": "BSD-3-Clause", "itsdangerous": "BSD-3-Clause", "jinja2": "BSD-3-Clause", "pydyf": "BSD-3-Clause",
    "segno": "BSD-3-Clause", "tinycss2": "BSD-3-Clause", "weasyprint": "BSD-3-Clause", "webencodings": "BSD-3-Clause",

    "paramiko": "LGPL-2.1-or-later",
}

WRONG_METADATA = {"paramiko"}
NOT_NAMED = "license not stated"

def _key(name):
    return re.sub(r"[-_.]+", "-", (name or "").lower())

def spdx(expr):

    expr = (expr or "").strip()
    if not expr or "\n" in expr or len(expr) > 120:
        return ""
    expr = SPDX_OLD.get(expr, expr)
    out = []
    for tok in re.findall(r"\(|\)|[^\s()]+", expr):
        if tok in ("(", ")", "AND", "OR", "WITH"):
            out.append(tok)
            continue
        tok = SPDX_OLD.get(tok, tok)
        if tok not in SPDX_IDS:
            return ""
        out.append(tok)
    if not out or out[0] in ("AND", "OR", "WITH") or out[-1] in ("AND", "OR", "WITH"):
        return ""
    return " ".join(out).replace("( ", "(").replace(" )", ")")

def license_of(metadata):

    m = metadata
    if _key(m.get("Name")) in WRONG_METADATA:
        return spdx(OVERRIDES[_key(m.get("Name"))])
    for got in (spdx(m.get("License-Expression")), spdx(m.get("License"))):
        if got:
            return got
    cls = [c.split(" :: ")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    ids = [CLASSIFIER_SPDX.get(c, "") for c in cls]
    if ids and all(ids) and _key(m.get("Name")) not in OVERRIDES:
        return " OR ".join(dict.fromkeys(ids))
    return spdx(OVERRIDES.get(_key(m.get("Name")), ""))

def _license(dist):
    return license_of(dist.metadata) or NOT_NAMED

def lock_names(path=None):

    try:
        text = Path(path or ROOT / "requirements.lock").read_text()
    except OSError:
        return []
    return re.findall(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.\-]*)==", text)

def license_problems(names=None):

    probs = []
    for n in names if names is not None else lock_names():
        try:
            d = md.distribution(n)
        except md.PackageNotFoundError:
            probs.append(f"{n}: not installed here, so its license cannot be read")
            continue
        if not license_of(d.metadata):
            m = d.metadata
            seen = m.get("License-Expression") or (m.get("License") or "").split("\n")[0][:60] or ", ".join(
                c.split(" :: ")[-1] for c in (m.get_all("Classifier") or []) if c.startswith("License ::")) or "nothing"
            probs.append(f"{n} {d.version}: license not named cleanly (metadata says: {seen}); read its LICENSE file "
                         "and add the SPDX id to OVERRIDES in about.py")
    return probs

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
